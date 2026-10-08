# Performance Review: spring-boot-realworld

Scope: everything under `src/main` (Spring Boot 2.6, MyBatis, SQLite, Netflix DGS GraphQL, JJWT).
Paths are relative to `src/main`.

## Summary

The application works at demo scale, but its data access layer will not hold up as data grows. The main problems, in order:

1. **The schema has almost no indexes.** Every join and filter used by the hot read paths scans whole tables.
2. **The article listing query is a 5-way LEFT JOIN with DISTINCT, ORDER BY, OFFSET and a separate COUNT.** It multiplies rows by tags times favorites, and it runs on every home page load.
3. **The feed and comment queries apply LIMIT to the wrong thing or leave it out entirely.** The comment query has no LIMIT at all. The feed queries apply LIMIT to joined rows rather than to articles, so they return short pages and fetch too much.
4. **The GraphQL resolvers have N+1 query patterns.** A single page of 20 articles with authors and comments can cost more than 100 queries.
5. **Every authenticated request costs an extra database lookup** in the JWT filter, and SQL DEBUG logging is turned on in the main config.

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | Missing indexes on every foreign key and filter column | Critical | Low |
| 2 | Article list query fans out, uses DISTINCT and OFFSET, and runs a duplicate COUNT | Critical | Medium |
| 3 | Comment queries have no LIMIT (cursor query is unbounded; REST has no pagination) | High | Low |
| 4 | Feed queries apply LIMIT to joined rows, have no ORDER BY, and use an unbounded IN list | High | Medium |
| 5 | GraphQL N+1: author, comments and profile re-queried per node | High | Medium |
| 6 | JWT filter hits the DB on every request; redundant follow-up reloads | Medium | Low–Medium |
| 7 | Single-article and write endpoints make many round trips | Medium | Low–Medium |
| 8 | Article create/update: per-tag queries, heavy existence checks, read-before-write | Medium | Low |
| 9 | Runtime configuration: SQL DEBUG logging, SQLite with default pool/no WAL, ineffective cache flag | Medium | Low |
| 10 | GET /tags is unbounded and uncached | Low–Medium | Low |
| 11 | Pagination limits and GraphQL query cost are uncapped or weakly capped | Low–Medium | Low |
| 12 | Minor query inefficiencies (unnecessary joins, `select *`, JWT parser rebuilds) | Low | Low |

I also found several correctness bugs in the same code paths. They are listed at the end because fixing the performance issues touches the same lines.

---

## 1. Missing indexes on nearly every join and filter column (Critical)

**Where:** `resources/db/migration/V1__create_tables.sql`

The only indexes come from the primary key and `UNIQUE` constraints: `users.id/username/email`, `articles.id/slug`, `article_favorites(article_id, user_id)`, `tags.id` and `comments.id`. Nothing else is indexed, and every query on a read path depends on the unindexed columns:

| Column(s) | Used by | Current behavior |
|---|---|---|
| `article_tags(article_id)`, `article_tags(tag_id)` | Every article read (`selectArticleData`, `selectArticle`, `selectArticleIds`) | Full scan of `article_tags` per joined article |
| `tags(name)` | Tag filter (`T.name = #{tag}`), `findTag` on article create | Full scan; also **not unique**, so concurrent creates can insert duplicate tags |
| `articles(user_id)` | Feed (`A.user_id in (...)`), author filter | Full scan |
| `articles(created_at)` | `order by A.created_at` and cursor predicates on all list queries | Full sort of the result |
| `comments(article_id, created_at)` | `findByArticleId`, `findByArticleIdWithCursor` | Full scan of `comments` on every article view |
| `follows(user_id, follow_id)` | `followedUsers`, `isUserFollowing`, `followingAuthors`, `findRelation` | Full scan. There is **no primary key**, so the check-then-insert in `MyBatisUserRepository.saveRelation` can create duplicate rows under concurrency |
| `article_favorites(user_id)` | `favoritedBy` filter (`AFU.username = ...` via `AF.user_id`), `userFavorites` | The PK leads with `article_id`, so lookups by user scan the table |

**Impact:** Article list, single article, comments, feed and profile endpoints all slow down roughly linearly with table size. `comments` and `article_tags` are scanned on almost every page view.

**Fix:** Add a new Flyway migration (do not edit V1), for example `V2__add_indexes.sql`:

```sql
create index idx_article_tags_article on article_tags(article_id);
create unique index uq_article_tags on article_tags(tag_id, article_id);   -- also serves tag -> articles
create unique index uq_tags_name on tags(name);
create index idx_articles_user_created on articles(user_id, created_at);
create index idx_articles_created on articles(created_at);
create index idx_comments_article_created on comments(article_id, created_at);
create unique index uq_follows on follows(user_id, follow_id);
create index idx_follows_follow on follows(follow_id);
create index idx_article_favorites_user on article_favorites(user_id, article_id);
```

Deduplicate any existing rows in `tags`, `follows` and `article_tags` before you add the unique indexes. With the unique constraints in place, `saveRelation` and the favorite `save()` can become `INSERT OR IGNORE` (`ON CONFLICT DO NOTHING`), which removes one query per call (see #7).

---

## 2. Article listing query: row fan-out, DISTINCT, OFFSET, and a duplicate COUNT (Critical)

**Where:** `resources/mapper/ArticleReadService.xml`: `selectArticleIds`, `queryArticles`, `countArticle`, `findArticlesWithCursor`. Called from `ArticleQueryService.findRecentArticles` / `findRecentArticlesWithCursor`, which serve `GET /articles` and the GraphQL `articles`, `Profile.articles` and `Profile.favorites` fields.

```sql
select DISTINCT(A.id) articleId, A.created_at
from articles A
left join article_tags AT on A.id = AT.article_id
left join tags T on T.id = AT.tag_id
left join article_favorites AF on AF.article_id = A.id
left join users AU on AU.id = A.user_id
left join users AFU on AFU.id = AF.user_id
<where> ... </where>
order by A.created_at desc
limit #{page.offset}, #{page.limit}
```

Problems:

- **The joins multiply rows.** All five joins run even when no filter is set, which is the default home page request. An article with 5 tags and 1,000 favorites produces 5,000 intermediate rows. The `DISTINCT` then has to deduplicate the whole joined set before it can sort and apply the limit. Popular articles make the home page slow.
- **The LEFT JOINs serve no purpose.** The `WHERE` predicates on `T.name`, `AU.username` and `AFU.username` turn them into inner joins anyway. The optimizer also cannot drive the query from the filter.
- **`countArticle` repeats the same join** with `count(DISTINCT A.id)`. It runs **unconditionally on every list request**, so the cost of each page roughly doubles.
- **OFFSET pagination** makes deep pages O(offset): SQLite still has to build and discard every skipped row.

**Fix:** Build the query from `articles` alone and add each filter only when it is present, as an `EXISTS` or `IN` subquery:

```xml
<sql id="articleFilter">
  <where>
    <if test="tag != null">
      A.id in (select AT.article_id from article_tags AT
               join tags T on T.id = AT.tag_id where T.name = #{tag})
    </if>
    <if test="author != null">
      AND A.user_id = (select id from users where username = #{author})
    </if>
    <if test="favoritedBy != null">
      AND A.id in (select AF.article_id from article_favorites AF
                   join users U on U.id = AF.user_id where U.username = #{favoritedBy})
    </if>
  </where>
</sql>

<select id="queryArticles" resultType="string">
  select A.id from articles A
  <include refid="articleFilter"/>
  order by A.created_at desc
  limit #{page.limit} offset #{page.offset}
</select>

<select id="countArticle" resultType="int">
  select count(*) from articles A <include refid="articleFilter"/>
</select>
```

- `DISTINCT` is no longer needed, and the no-filter case becomes an index-ordered scan of `articles(created_at)` that stops after `limit` rows.
- Apply the same rewrite to `findArticlesWithCursor` (the GraphQL path).
- With the indexes from #1, every filter becomes an index seek.
- For the count: cache it briefly, or skip it when the client does not need `articlesCount`. Longer term, move the REST API to keyset/cursor pagination (the GraphQL path already has the cursor mechanism).

**Expected gain:** home page list cost drops from O(articles × tags × favorites) plus a sort, to O(limit) on an index.

---

## 3. Comment queries have no LIMIT (High)

**Where:**
- `resources/mapper/CommentReadService.xml`, `findByArticleIdWithCursor`. **No `LIMIT` clause.** It loads every comment newer or older than the cursor.
- `CommentQueryService.findByArticleIdWithCursor`. It then checks `comments.size() > page.getLimit()` and calls `comments.remove(page.getLimit())`, which removes only **one** element. The caller therefore gets back *all* comments, not one page, and the method runs `followingAuthors` with an IN list of every comment author.
- `CommentReadService.xml` `findByArticleId` / `CommentsApi.getComments` (REST `GET /articles/{slug}/comments`). No pagination at all, and no `ORDER BY`.

**Impact:** an article with 10,000 comments makes every view load 10,000 rows plus a user join, map them to objects, serialize them and send them over the wire. Combined with the missing `comments(article_id)` index, every view also scans the full comments table. This is a memory and latency risk, and a simple DoS vector.

**Fix:**
- Add `limit #{page.queryLimit}` to `findByArticleIdWithCursor`, matching the article cursor queries. Then trim the result in Java with `subList(0, limit)` rather than `remove(index)`.
- Paginate the REST endpoint with a `limit` (default 20, max 100) and an `offset` or cursor. If the RealWorld spec forces "return all", at least add `order by C.created_at desc` and a hard cap.
- Make sure the `comments(article_id, created_at)` index from #1 exists so the query is a single index range scan.

---

## 4. Feed queries: LIMIT on joined rows, no ORDER BY, unbounded IN list (High)

**Where:** `ArticleReadService.xml`: `findArticlesOfAuthors`, `findArticlesOfAuthorsWithCursor`, `countFeedSize`. `ArticleQueryService.findUserFeed` / `findUserFeedWithCursor`.

Problems:

1. **LIMIT is applied to joined rows, not articles.** These queries use `selectArticleData`, which joins `article_tags` and `tags`, so each article appears once per tag. `limit 0, 20` returns 20 *rows*. With 4 tags per article, that is 5 articles, and the last one may have a partial tag list. The page is short, `hasExtra` is computed on articles that MyBatis has already collapsed, and clients make more requests to fill a page.
2. **`findArticlesOfAuthors` has no `ORDER BY`.** Results are in arbitrary order, so offset paging is non-deterministic: duplicates and gaps.
3. **The `ORDER BY` in `findArticlesOfAuthorsWithCursor` sits inside `<where>`.** It works, but it is fragile.
4. **Two round trips plus an unbounded IN list.** `followedUsers(userId)` loads the full list of followed IDs into Java, and then each followed ID becomes a bind variable in `A.user_id in (...)`. A user who follows thousands of accounts produces a huge statement that cannot be cached. That runs twice (data and `countFeedSize`), it approaches SQLite's bind-variable limit, and it would exceed the limits on other databases.

**Fix:** Do the ID selection as one paginated query against `articles` with a join or subquery on `follows`, then hydrate the selected IDs. This is the same two-step pattern `findRecentArticles` already uses with `findArticles`:

```sql
-- step 1: ids only, index-driven (follows(user_id, follow_id) + articles(user_id, created_at))
select A.id from articles A
where A.user_id in (select follow_id from follows where user_id = #{userId})
  [and A.created_at < #{cursor}]
order by A.created_at desc
limit #{page.queryLimit}

-- step 2: existing findArticles(articleIds) to hydrate with tags/author
```

This removes the `followedUsers` round trip and the large IN list, and makes the page size correct. `countFeedSize` uses the same subquery. Consider dropping it from the REST feed or caching it.

---

## 5. GraphQL N+1 resolvers (High)

**Where:** `graphql/ProfileDatafetcher.java`, `graphql/CommentDatafetcher.java`, `graphql/ArticleDatafetcher.java`.

- **`Article.author`** (`ProfileDatafetcher.getAuthor`) calls `profileQueryService.findByUsername` once per article. That is one `users` query plus one `follows` query each. The `ArticleData` in local context **already holds** `profileData`, including `following`, which `ArticleQueryService.setIsFollowingAuthor` filled in with one batch query. A 20-article page that selects `author` therefore issues 40 extra queries for data already in memory.
- **`Comment.author`** (`getCommentAuthor`) has the same pattern. `CommentData.profileData` already holds the author, and `CommentQueryService` already batch-computes `following`. That is 2 extra queries per comment.
- **`Article.comments`** (`CommentDatafetcher.articleComments`) runs one comment query per article in a list. Because of #3, each of those queries is unbounded.
- **`Comment.article`** (`ArticleDatafetcher.getCommentArticle`) calls `articleQueryService.findById` once per comment. That is 4 queries each (article + isFavorite + count + isFollowing), and they repeat for the same article.
- **`Profile.feed` / `Profile.articles` / `Profile.favorites`** each call `userRepository.findByUsername` or re-resolve the profile, after the parent `Profile` was already loaded by username.

**Worst case:** `articles(first: 20) { author {...} comments(first: 20) { author {...} } }` can issue 1 + 1 + 3 + 40 + 20 × (comment query + follow batch) + 400 × 2 ≈ **880 queries**, and the comment queries are unbounded.

**Fix:**
- In `getAuthor` and `getCommentAuthor`, build `Profile` directly from `map.get(slug).getProfileData()` / `map.get(id).getProfileData()`. This needs no database access and is a small change.
- Use DGS `@DgsDataLoader` (a `MappedBatchLoader`) for `Article.comments` and `Comment.article`. Batch them by article ID into one `where article_id in (...)` query, using a window function or per-article limit for paging.
- Add query depth and complexity limits (`MaxQueryDepthInstrumentation`, `MaxQueryComplexityInstrumentation`) so a single request cannot fan out without bound.

---

## 6. JWT filter loads the user on every request (Medium)

**Where:** `api/security/JwtTokenFilter.java`. Every request carrying a token calls `userRepository.findById(id)`. Endpoints then often reload the same user. For example, `CurrentUserApi.currentUser` calls `userQueryService.findById` again (`select *`), and so does `MeDatafetcher.getMe`.

**Impact:** at least 1 extra query on every authenticated request, which is the majority of traffic for a logged-in client, and 2 on `/user`.

**Fix:**
- Put the claims needed for authorization (user ID, plus username if useful) in the JWT, and build a lightweight principal from the token without touching the database. Load the full `User` only in endpoints that need it.
- Alternatively, add a small Caffeine cache in front of `findById` (TTL 30–60s, evicted on `UserService.updateUser`).
- In `CurrentUserApi.currentUser`, build `UserData` from the already-loaded principal instead of re-querying.

---

## 7. Single-article and write endpoints make many round trips (Medium)

**Where:** `ArticleQueryService.findById/findBySlug` → `fillExtraInfo(String, User, ArticleData)`. Also `ArticleFavoriteApi`, `ArticleApi.updateArticle`, `CommentsApi`, `ProfileApi.follow/unfollow`, `MyBatisArticleFavoriteRepository.save` and `MyBatisUserRepository.saveRelation`.

- A single article read costs 4 queries: the article+tags+author join, `isUserFavorite`, `articleFavoriteCount` and `isUserFollowing`. These could be one query with scalar subqueries:
  ```sql
  select ..., (select count(*) from article_favorites where article_id = A.id) favoritesCount,
         exists(select 1 from article_favorites where article_id = A.id and user_id = #{uid}) favorited,
         exists(select 1 from follows where user_id = #{uid} and follow_id = A.user_id) following
  ```
- `POST /articles/{slug}/favorite` makes about 8 round trips:
  - `findBySlug` (the Article aggregate with a tag join)
  - `find` and then `insert` (check-then-insert)
  - `findBySlug` again (ArticleData join)
  - 3 enrichment queries

  With a unique key, the insert can be `INSERT OR IGNORE`, and the article can be loaded once. A lighter `select id from articles where slug = ?` is enough for the write.
- `follow` follows the same pattern: `findByUsername` (`select *`), `findRelation`, `saveRelation`, then `profileQueryService.findByUsername`, which queries the user and the follow relation again. That is 5 queries where 2 would do. `unfollow` is similar.
- `createComment`: `findBySlug` (aggregate with tags), insert, then `findById` with a user join and a follow check. Most of the response data is already in memory: the comment and the current user. The only thing that needs a query is `following`, and a user always sees themselves as not-followed.

**Fix:** Collapse enrichment into the main query, use idempotent upserts and drop the read-before-write, and build responses from in-memory data where possible. Each change is small; together they cut query counts on these endpoints by about 50–70%.

---

## 8. Article create/update path (Medium)

**Where:** `MyBatisArticleRepository.save/createNew`, `DuplicatedArticleValidator`, `NewArticleParam`.

- **Read-before-write on every save.** `save()` calls `articleMapper.findById` to decide between insert and update. That is a full article+tags join, run on every create and every update, even though the caller always knows which one it is doing. Provide separate `create()` and `update()` methods, or check `select 1 from articles where id = ?`.
- **Per-tag queries.** Each tag costs `findTag` + `insertTag` (if new) + `insertArticleTagRelation`, so an article with 10 tags issues up to 30 statements. Instead, do one `insert or ignore into tags (id, name) values ...` (multi-row), one `select id, name from tags where name in (...)` and one multi-row `insert into article_tags`. This needs the unique index on `tags(name)` from #1. It also fixes duplicate tags created by concurrent posts.
- **Heavy duplicate check.** `DuplicatedArticleValidator` calls `articleQueryService.findBySlug(slug, null)`, which runs the full `selectArticleData` 4-table join just to test for existence. Replace it with `select exists(select 1 from articles where slug = ?)`. The unique `slug` constraint already exists, so you could also drop the pre-check and map the constraint violation to a 422.
- `POST /articles` then calls `articleQueryService.findById` (4 more queries) to build the response. The response could be built from the in-memory `Article` plus the current user (favorited=false, count=0, following=false).
- `ArticleApi.updateArticle` loads the Article aggregate, `save()` reloads it, and then `findBySlug` loads it again with enrichment: 3 loads of the same row.

---

## 9. Runtime configuration (Medium)

**Where:** `resources/application.properties`, `build.gradle`.

- **SQL DEBUG logging is on in the main profile:**
  ```
  logging.level.io.spring.infrastructure.mybatis.readservice.ArticleReadService=DEBUG
  logging.level.io.spring.infrastructure.mybatis.mapper=DEBUG
  ```
  Every statement, its parameters, and (for mappers) row counts are formatted and written synchronously. Given the query volumes above, this adds measurable latency and I/O to every request, and it logs user emails and password hashes from `UserMapper`. Move these settings to a dev profile.
- **SQLite as the production datastore.** `jdbc:sqlite:dev.db` runs with the default HikariCP pool of 10 connections, no WAL journal mode and no busy timeout. SQLite allows a single writer, and in the default rollback-journal mode readers block during writes, so concurrent requests get `SQLITE_BUSY` errors or serialize. If SQLite stays:
  - set `journal_mode=WAL`, `synchronous=NORMAL` and `busy_timeout=5000` (for example `jdbc:sqlite:dev.db?journal_mode=WAL&busy_timeout=5000`, or Hikari `connection-init-sql`)
  - size the pool deliberately, since writes serialize anyway

  For real multi-user load, use PostgreSQL or MySQL. The SQL is mostly portable apart from `limit a, b`, which you should switch to `limit b offset a`.
- **`mybatis.configuration.cache-enabled=true` has no effect.** No mapper declares `<cache/>`, so nothing is cached. If you enable caching, do it deliberately for read-mostly data such as tags (see #10), not for article reads that depend on the current user.
- **`default-statement-timeout=3000`** is reasonable as a guard. Keep it.

---

## 10. GET /tags is unbounded and uncached (Low–Medium)

**Where:** `TagReadService.xml` (`select name from tags`), `TagsQueryService`, `TagsApi`, `TagDatafetcher`.

The RealWorld UI calls this endpoint on every home page load. It returns every tag ever created, including tags whose articles were deleted (`articles` delete does not clean up `article_tags` or `tags`). It is a full table scan with no ordering or limit, and it runs on every call.

**Fix:**
- Return popular tags with a limit:
  ```sql
  select T.name from tags T join article_tags AT on AT.tag_id = T.id
  group by T.id order by count(*) desc limit 20
  ```
- Cache the result (`@Cacheable` with a Caffeine TTL of 1–5 minutes, or evict on article create).
- Add `Cache-Control: max-age=60` to the HTTP response.

---

## 11. Pagination and GraphQL query cost are poorly bounded (Low–Medium)

- `CursorPageParameter.MAX_LIMIT = 1000` (GraphQL). Combined with nested `comments` and `author` resolvers (#5), one request can touch tens of thousands of rows. Lower it to 100, matching `Page.MAX_LIMIT`.
- REST offset paging accepts any `offset`, so a large offset forces a large scan (#2).
- GraphQL has no depth or complexity limits (#5).
- The comment cursor query has no LIMIT (#3).

---

## 12. Minor inefficiencies (Low)

- `UserMapper.findByUsername` and `UserReadService.findByUsername/findById` use `select *`. List the columns explicitly so schema additions do not inflate every auth and profile lookup.
- `ArticleFavoritesReadService.xml`: `articlesFavoriteCount` and `userFavorites` join `articles` unnecessarily. Query `article_favorites` directly:
  - `select article_id id, count(*) favoriteCount from article_favorites where article_id in (...) group by article_id`
  - `select article_id from article_favorites where user_id = ? and article_id in (...)`

  Then default missing counts to 0 in Java.
- `DefaultJwtService.getSubFromToken` builds a new `JwtParser` on every request. Build it once in the constructor (it is thread-safe) and reuse it. Catching a broad `Exception` for invalid tokens is fine.
- `isUserFavorite` / `isUserFollowing` use `count(1)`. Prefer `select exists(select 1 ...)`, which can stop at the first match. With the indexes from #1 the difference is small.
- BCrypt (default strength 10) on login and registration costs about 50–100 ms of CPU per call. That is appropriate for security, but login should be rate-limited so it cannot be used to exhaust CPU.

---

## Correctness issues found along the way

These are not performance findings, but they sit on the same code paths and should be fixed together with them:

- **The cursor field does not match the query field.** `ArticleData.getCursor()` returns `updatedAt`, but the cursor queries filter and sort on `A.created_at`. After any article is edited, cursor pages skip or repeat items.
- **Anonymous single-article reads return `favoritesCount = 0`.** `ArticleQueryService.findById/findBySlug` call `fillExtraInfo` only when `user != null`, so anonymous readers always see 0. The query rewrite in #7 fixes this as a side effect.
- **Comment cursor paging is broken.** `CommentQueryService.findByArticleIdWithCursor` removes only one element instead of truncating to the limit (#3).
- **Feed pages are short or have truncated tag lists** because LIMIT applies to joined rows (#4).
- **Deleting an article leaves orphaned rows** in `article_tags`, `article_favorites` and `comments`. The tables slowly grow with data that no read path needs. Add cascading deletes, or delete these rows in the same transaction.

## Suggested order of work

1. Add the V2 index migration (#1) and move DEBUG logging out of the main profile (#9). These are low effort and benefit every endpoint.
2. Add a LIMIT to the comment queries and paginate REST comments (#3).
3. Rewrite the list, count and feed queries to select IDs first with EXISTS/IN filters (#2, #4).
4. Make the GraphQL author resolvers use in-memory data, and add DataLoaders and depth/complexity limits (#5, #11).
5. Make the JWT filter stateless, and collapse queries on the single-article and write paths (#6, #7, #8).
6. Cache tags and clean up the minor items (#10, #12).

After step 3, measure with a seeded dataset of around 100k articles, 1M favorites and 500k comments. Run `EXPLAIN QUERY PLAN` on each rewritten query to confirm it uses the indexes.
