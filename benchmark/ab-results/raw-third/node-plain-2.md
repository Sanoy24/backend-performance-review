# Performance Review: node-express-realworld

**Stack:** Express 4.18, Prisma 4.16 (PostgreSQL), bcryptjs, express-jwt. One Node process, no cache layer.
**Scope:** everything under `src/` (routes, services, mappers, Prisma schema and migrations, `main.ts`), plus `Dockerfile` and `project.json`.

## Executive summary

The app is small, and per-request CPU work is light. The main risk is the data access layer. Almost every article and profile endpoint loads **whole relation lists into memory**, such as every user who favorited an article or every follower of an author, only to compute a boolean or a count. Cost therefore grows with how popular the content is, not with page size. One popular author or article makes the busiest endpoints (`GET /api/articles`, `GET /api/articles/:slug`, comments, profiles) slow and memory-hungry. The favorite and unfavorite endpoints also send those full user rows, **including password hashes and emails**, back to the client.

Other problems: the client controls page size with no upper limit, the foreign-key and sort columns have no indexes, password hashing runs in pure JavaScript and blocks the event loop, and queries that could run in parallel run one after another.

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | Unbounded `favoritedBy` / `followedBy` loading on every article, comment and profile read | **Critical** | Medium |
| 2 | Favorite/unfavorite responses contain full `favoritedBy` user rows (payload bloat plus password-hash leak) | **Critical** | Low |
| 3 | No upper bound on `limit` for article list and feed | **High** | Low |
| 4 | Missing indexes on foreign keys and on `Article.createdAt` | **High** | Low |
| 5 | Comments endpoint: no pagination, each comment loads its author's full follower list | **High** | Medium |
| 6 | `bcryptjs` (pure JS) blocks the event loop on register, login and password update | **High** | Low |
| 7 | `GET /api/tags` runs an expensive aggregate on every call and is never cached | **Medium** | Low |
| 8 | Sequential count and list queries, and other avoidable round trips | **Medium** | Low |
| 9 | `updateArticle`: 4+ sequential queries, tags cleared and re-added, no transaction | **Medium** | Low |
| 10 | No response compression; static middleware mounted after the API router | **Low** | Low |
| 11 | Connection pool and process model not configured for production | **Low** | Low |
| 12 | Offset pagination degrades on deep pages | **Low** | Medium |

---

## 1. Unbounded relation loading (`favoritedBy: true`, `followedBy: true`): Critical

**Where**
- `src/app/routes/article/article.service.ts`, in the `include` blocks of `getArticles` (L84-104), `getFeed` (L133-153), `createArticle` (L215-235), `getArticle` (L246-266), `updateArticle` (L359-379), `favoriteArticle` (L574-594) and `unfavoriteArticle` (L620-640):
  ```ts
  author: { select: { username: true, bio: true, image: true, followedBy: true } },
  favoritedBy: true,
  _count: { select: { favoritedBy: true } },
  ```
- `src/app/routes/article/article.mapper.ts` L11-12 uses `favoritedBy.some(...)` and `favoritedBy.length`.
- `src/app/routes/article/author.mapper.ts` L7-9 and `src/app/routes/profile/profile.utils.ts` L8-10 use `followedBy.some(...)`.
- `src/app/routes/profile/profile.service.ts`: `getProfile` (L10-12), `followUser` (L34-36) and `unfollowUser` (L54-56) all use `include: { followedBy: true }`.
- `getCommentsByArticle` (L447-454) and `addComment` (L501-510) use `author.followedBy: true`.

**Problem**
`favoritedBy: true` and `followedBy: true` select **every column of every related User row**: id, email, username, password hash, bio, image and demo. The code only needs two things from them:
- `favoritesCount`. This is already computed by `_count`, but the mapper ignores it and uses `favoritedBy.length`.
- Whether the *current* user is in the list. That is a single-row existence check.

Take one page of 10 articles. Prisma 4 resolves each `include` as a separate `IN (...)` query, then joins the results in JS. The page:
- reads every favoriter of each of the 10 articles,
- reads every follower of each of the 10 authors,
- moves all of those rows over the wire, parses them into JS objects, and keeps them on the heap,
- throws them away after calling `.some()` and `.length` on them.

The cost is O(total favorites + total followers), not O(page size). With an author who has 50k followers, every list page showing that author pulls 50k user rows. Single-article reads, profile reads, and follow/unfollow have the same problem. Under load this shows up as high p99 latency, GC pauses, and a DB that is busy with I/O for no useful reason.

**Fix**
Replace the full lists with an aggregate plus a filtered existence check scoped to the viewer:
```ts
const articleInclude = (viewerId?: number) => ({
  tagList: { select: { name: true } },
  author: {
    select: {
      username: true, bio: true, image: true,
      followedBy: viewerId ? { where: { id: viewerId }, select: { id: true } } : false,
    },
  },
  favoritedBy: viewerId ? { where: { id: viewerId }, select: { id: true } } : false,
  _count: { select: { favoritedBy: true } },
});
```
Then in the mapper:
- `favoritesCount = article._count.favoritedBy`
- `favorited = (article.favoritedBy?.length ?? 0) > 0`
- `following = (author.followedBy?.length ?? 0) > 0`

Each relation now returns at most one row. Define this include **once** and reuse it. It is copy-pasted seven times today, and that duplication is how the problem spread to every endpoint. Apply the same pattern to `getProfile`, `followUser` and `unfollowUser`, and to comment authors.

**Expected impact:** per-request DB rows and memory become bounded by page size. This is the largest single gain available.

---

## 2. Favorite/unfavorite send full user rows to the client: Critical

**Where:** `article.service.ts`, `favoriteArticle` L597-603 and `unfavoriteArticle` L643-649:
```ts
const result = { ...article, author: ..., tagList: ..., favorited: ..., favoritesCount: ... };
```

**Problem**
`...article` spreads the raw Prisma object into the response, including `favoritedBy`: the full User row of every user who favorited the article. On a popular article, a "like" click returns a response that grows with the number of likes, so it can be megabytes. Every response is also serialized with `JSON.stringify` on the event loop. The same rows include `password` (bcrypt hash) and `email` for every favoriter, so this is a serious data leak as well as a performance problem. `id` and `authorId` leak too.

**Fix:** return `articleMapper(article, id)` exactly as `getArticle` does, and use the bounded include from finding 1. Neither function then needs a hand-built result object.

---

## 3. No upper bound on page size: High

**Where:** `article.service.ts` L82-83 (`take: Number(query.limit) || 10`) and L131-132 in `getFeed`. Neither controller validates `limit` or `offset`.

**Problem**
Any client, including anonymous ones (`auth.optional`), can call `GET /api/articles?limit=1000000`. Combined with finding 1, one request loads every article together with its full body, tags, favoriters and followers. A handful of such requests can exhaust memory, block the event loop during serialization, and saturate the DB. Negative or `NaN` values also reach Prisma: `NaN || 10` is fine, but `limit=-5` produces negative `take`, which reverses the result order. That is a correctness issue as well.

**Fix:** clamp and validate in one shared helper:
```ts
const limit = Math.min(Math.max(parseInt(q.limit, 10) || 10, 1), 100);
const offset = Math.max(parseInt(q.offset, 10) || 0, 0);
```
Add a cap on `offset` too (see finding 12), or move to cursor pagination.

---

## 4. Missing indexes on foreign keys and sort columns: High

**Where:** `src/prisma/schema.prisma` and `migrations/20210924225358_initial/migration.sql`. PostgreSQL does **not** create indexes on foreign keys automatically, and the schema declares no `@@index`. The only indexes are:
- the primary keys,
- the unique indexes on `Article.slug`, `User.email`, `User.username` and `Tag.name`,
- the indexes Prisma creates for the implicit many-to-many tables.

Unindexed columns that are used on hot paths:

| Column | Used by | Effect without index |
|---|---|---|
| `Article.authorId` | List (author filter, demo filter), feed (`author.followedBy.some`), author include | Sequential scan or hash join over all articles |
| `Article.createdAt` | `ORDER BY "createdAt" DESC` on every list and feed | Full sort of the matching set on every page |
| `Comment.articleId` | `getCommentsByArticle`, cascade delete of an article | Sequential scan of `Comment` per article view; slow deletes |
| `Comment.authorId` | Comment author filter, user cascade delete | Sequential scan |
| `User.demo` | Every list and tag query (`demo = true OR id = ?`) | Sequential scan of `User` |

**Fix** (in `schema.prisma`, then `prisma migrate dev`):
```prisma
model Article {
  // ...
  @@index([authorId, createdAt(sort: Desc)])
  @@index([createdAt(sort: Desc)])
}
model Comment {
  // ...
  @@index([articleId, createdAt])
  @@index([authorId])
}
model User {
  // ...
  @@index([demo])   // or a partial index via raw SQL: CREATE INDEX ... ON "User"(id) WHERE demo
}
```
Then check the plans for the list, feed and comments queries with `EXPLAIN (ANALYZE, BUFFERS)` on a realistically sized dataset. The seed script produces only 144 articles, which hides all of this.

---

## 5. Comments endpoint: unbounded and over-fetching: High

**Where:** `article.service.ts`, `getCommentsByArticle` L416-471.

**Problems**
- Returns **all** comments for the article. There is no `take`, no pagination and no `orderBy`, so the order is also nondeterministic.
- Each comment author's `followedBy: true` loads every follower of every commenter as full User rows, including password hashes (finding 1).
- When the viewer is anonymous, `id` is `undefined` and the result is always `following: false`, yet all the follower rows are still fetched.
- It reads the article and its comments through `article.findUnique` + `include`. With no index on `Comment.articleId` (finding 4), the comments query scans the table.
- It spreads `...comment` into the result. That is fine here because the `select` is explicit, but it still carries the raw `followedBy` array until the mapper replaces it.

**Fix:** query `prisma.comment.findMany({ where: { article: { slug }, OR: queries }, orderBy: { createdAt: 'desc' }, take, skip, select: {..., author: { select: { username, bio, image, followedBy: viewerId ? { where: { id: viewerId }, select: { id: true } } : false } } } })`. Add the `Comment(articleId, createdAt)` index. `addComment` (L478-511) needs the same author-select fix.

---

## 6. `bcryptjs` blocks the event loop: High

**Where:** `src/app/routes/auth/auth.service.ts`. `bcrypt.hash(password, 10)` runs at L58 (register) and L156 (update user), and `bcrypt.compare` at L111 (login). The library is `bcryptjs` (`package.json`).

**Problem**
`bcryptjs` is a pure-JavaScript implementation. Its async API splits the work into chunks with `setImmediate`, but all of it still runs on the **main thread**. At cost 10, each hash or compare takes roughly 50-150 ms of CPU on typical server hardware, several times slower than native bcrypt. A burst of login attempts, legitimate or a credential-stuffing attack, competes directly with every other request for the single event loop. The login route has no rate limiting, so it is also an easy CPU-exhaustion vector.

**Fix**
- Switch to the native `bcrypt` package, or to `argon2`. Both run on the libuv thread pool, off the event loop. Hashes stay compatible when moving to `bcrypt`.
- If you deploy several workers, size `UV_THREADPOOL_SIZE` to match.
- Add rate limiting to `/api/users/login` and `/api/users`, for example `express-rate-limit`.
- Optional: skip `bcrypt.compare` when the user is not found but run a dummy compare, so timing stays uniform. This is a security choice, not a performance one, and it is shown only so the fix does not introduce a timing oracle.

---

## 7. `GET /api/tags` is expensive and uncached: Medium

**Where:** `src/app/routes/tag/tag.service.ts` L16-35.

**Problem**
The front-end calls this on every home-page load. The query:
- filters tags through `articles.some.author.OR[demo, id]` (Tag → `_ArticleToTag` → Article → User),
- orders by `articles._count`, which makes Prisma build a `GROUP BY` subquery counting **all** rows of `_ArticleToTag` per tag,
- then takes 10.

That is a full aggregate over the join table on every request, and it grows with total content. The result changes rarely and is identical for all anonymous users. It also counts all articles while filtering on demo or own-author articles, so the ranking and the filter disagree. That is worth noting, but it is not a perf issue.

**Fix**
- Cache the anonymous result in memory, or in Redis if there are several instances, with a TTL of 1-5 minutes. Invalidating on article create or update is optional.
- Add `Cache-Control: public, max-age=60` for anonymous responses.
- If per-user tags matter, cache per user briefly or compute the popular tags in a background job or a materialized view.

---

## 8. Avoidable sequential round trips: Medium

**Where and fix**
- `getArticles` L71-105 and `getFeed` L114-154: `count` and `findMany` run one after the other. They are independent, so run them with `Promise.all([...])` or `prisma.$transaction([count, findMany])`. That cuts one round trip from every list request. Also consider skipping the count on pages after the first, or caching it briefly. `COUNT(*)` over a filtered relation join is not cheap.
- `checkUserUniqueness` in `auth.service.ts` L9-36: two sequential `findUnique` calls. Use one `findFirst({ where: { OR: [{email}, {username}] }, select: { email: true, username: true } })`. Better still, drop the pre-check and catch Prisma `P2002` unique-constraint errors from `create`. That also closes the check-then-insert race.
- `createArticle` L180-191: a pre-check on slug uniqueness followed by `create`. Same fix: rely on the `Article_slug_key` unique index and map `P2002` to 422.
- `addComment` L478-485: reads the article id, then creates the comment. Connect by slug directly with `article: { connect: { slug } }`, which saves a round trip. It also stops a missing article from becoming an opaque 500 when `article?.id` is undefined.
- `deleteArticle` L386-413 and `deleteComment` L528-559: a read for the ownership check, then a delete. Use `deleteMany({ where: { slug, authorId: id } })` and check `count`. Fall back to a 404/403 lookup only when `count === 0`. Note that `deleteComment` filters by `author.id = userId` in the `where`, which makes the later 403 branch unreachable. Simplify it.
- `await await` at L292 and L386 is harmless, but it is a sign the code has not been profiled.

---

## 9. `updateArticle`: many sequential writes, no transaction: Medium

**Where:** `article.service.ts` L289-383.

**Problem:** up to five sequential round trips:
1. `findFirst`, to check ownership.
2. `findFirst` on the new slug, to check uniqueness.
3. `disconnectArticlesTags`: an `UPDATE` that deletes every row in `_ArticleToTag` for the article.
4. The main `update` with `connectOrCreate` for each tag. Prisma issues an upsert-style lookup per tag, so this costs N queries for N tags.
5. The heavy include from finding 1.

Steps 3 and 4 are not in a transaction. A failure between them leaves the article with **no tags**. And because tags are always disconnected, an update with no `tagList` silently wipes the existing tags. That is a correctness bug, and it also adds write churn on every edit.

**Fix**
- Do it as a single `update`: `tagList: { set: [], connectOrCreate: tags }`. Only touch tags when `article.tagList` is provided.
- Use `findUnique` (slug is unique) instead of `findFirst`.
- Fold the ownership check into the update with `where: { slug }` plus a pre-select, or into a `$transaction`.
- Rely on `P2002` for slug collisions instead of the pre-check.

---

## 10. HTTP layer: no compression, static files mounted after the API: Low

**Where:** `src/main.ts` L13-19.

- **No `compression` middleware.** Article lists include the full `body` and `description` of every article and compress very well, typically 70-85% smaller. Add `app.use(compression())`, or terminate gzip/brotli at a reverse proxy, which is better for CPU.
- **`express.static` is registered after `routes`.** Every image request walks the `/api` router stack first. That stack is cheap, but the order is backwards: mount static assets first, with `maxAge`/`immutable` caching headers, or serve them from a CDN or nginx.
- `bodyParser.urlencoded({ extended: true })` is unused by any route. Remove it, and set an explicit `limit` on `bodyParser.json()`. The 100 KB default is fine, but make it deliberate.
- The list payload includes the full article `body` on every list item. The current RealWorld spec omits `body` from list responses. Dropping it would greatly cut DB I/O and response size for lists.

---

## 11. Connection pool and process model: Low

**Where:** `src/prisma/prisma-client.ts`, `Dockerfile`.

- `PrismaClient` uses the default pool, `num_cpus * 2 + 1`, with no `connection_limit` or `pool_timeout` in `DATABASE_URL`. In a container with a small CPU quota, Prisma may see only 1-2 CPUs and open just 3-5 connections. Concurrent requests then queue on the pool, and you see timeouts under modest load. Set `connection_limit` explicitly based on the DB's capacity, and put PgBouncer in front if you run several instances.
- The singleton caching is limited to `NODE_ENV === 'development'`. That is fine because the module is loaded once in production.
- The app runs as a single Node process (`CMD ["node","api"]`) with no clustering. Scale horizontally with replicas or PM2 cluster mode, especially given the CPU-bound bcrypt work in finding 6.
- The build uses `bundle: false`, so the image runs `npm install` at build time. That affects cold start and image size, not request latency.

---

## 12. Offset pagination on deep pages: Low (grows with data)

**Where:** `skip` in `getArticles` and `getFeed`.

`OFFSET n` makes PostgreSQL produce and discard `n` rows. With the `createdAt` index from finding 4, the first pages are cheap, but deep pages get slower in direct proportion to the offset. Cap `offset`, for example at 10,000. If the client can change, add keyset pagination (`WHERE ("createdAt", id) < ($1, $2) ORDER BY "createdAt" DESC, id DESC`) using Prisma's `cursor`.

---

## Recommended order of work

1. **Findings 1 and 2.** Introduce one shared, viewer-scoped `articleInclude` and a profile select, and route every response through the mappers. This removes the unbounded reads and the password-hash leak.
2. **Finding 3.** Clamp `limit` and `offset`. This is a one-line safety fix.
3. **Finding 4.** Add the index migration, then verify with `EXPLAIN ANALYZE` on a seeded dataset of 100k+ articles, favorites and follows.
4. **Finding 6.** Switch to native `bcrypt` or `argon2` and rate-limit the auth routes.
5. **Finding 5.** Paginate comments.
6. **Findings 7, 8 and 9.** Cache tags, parallelize count and list, and fold pre-checks into constraint handling and transactions.
7. **Findings 10, 11 and 12.** Add compression, configure the pool, and plan for keyset pagination.

**Verification:** the seed (`src/prisma/seed.ts`) creates 12 users, 144 articles and about 1.7k comments, too few to expose any of this. Before and after each change:
- Extend the seed to a skewed dataset, for example one author with 50k followers and one article with 50k favorites.
- Load-test `GET /api/articles`, `GET /api/articles/:slug`, `GET /api/articles/:slug/comments`, `GET /api/profiles/:username` and `POST /api/users/login` with autocannon or k6.
- Record p50/p99 latency, RSS, and Prisma query logs (`log: ['query']`).
