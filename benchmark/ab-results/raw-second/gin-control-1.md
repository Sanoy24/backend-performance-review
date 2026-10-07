# Performance Review: gin-realworld (Conduit API, Gin + GORM + SQLite)

## Scope and context

I reviewed all non-test Go source: `hello.go`, `common/`, `users/`, `articles/`. The stack is Gin 1.10, GORM 1.25, and SQLite (mattn/go-sqlite3) through a single global `*gorm.DB`. The hot paths are the article list, feed, single article, and comment list endpoints. These sit behind every page load of a RealWorld frontend.

The code already batches some work. `BatchGetFavoriteCounts` and `BatchGetFavoriteStatus` are used for lists, and `setTags` batch-fetches existing tags. Several important problems remain:

- user-controlled limits have no upper bound
- foreign-key and lookup columns have no indexes
- one N+1 query pattern is left in the serializers
- GET requests write to the database
- the transaction and SQLite configuration can make requests stall under concurrency

Severity scale:
- **Critical**: an outage or DoS risk that is easy to trigger.
- **High**: grows with data size or traffic and will dominate latency.
- **Medium**: clear waste on common paths.
- **Low**: hygiene, or matters only at scale.

## Summary table

| # | Finding | Severity | Where |
|---|---------|----------|-------|
| 1 | Unbounded / negative `limit` and `offset` let one request load whole tables | Critical | `articles/models.go:177-264`, `266-308` |
| 2 | Missing indexes on FK and lookup columns (favorites, follows, author, comments, username, user_model_id, article_tags) | High | model structs in `articles/models.go`, `users/models.go` |
| 3 | N+1 `isFollowing` query per article and per comment in serializers | High | `users/serializers.go:16-30`, `articles/serializers.go:57-79,152-171` |
| 4 | Writes on read paths: `GetArticleUserModel` does `FirstOrCreate` on GETs, and the calls are duplicated | High | `articles/models.go:54-65` and callers |
| 5 | Read-only transactions mix `tx` and the global DB, which can stall up to the busy timeout under SQLite | High | `articles/models.go:192-263`, `280-307` |
| 6 | SQLite/pool configuration has no WAL, no busy_timeout, and no open-connection cap | Medium-High | `common/database.go:49-69` |
| 7 | Single-article endpoints issue about 9-11 queries | Medium | `articles/routers.go`, `articles/serializers.go:57-80` |
| 8 | Filtered list pagination fetches IDs unordered, then sorts only the page: extra queries and wrong pages | Medium | `articles/models.go:193-254` |
| 9 | Auth middleware runs twice on authenticated routes: two JWT parses and two user SELECTs | Medium | `hello.go:42-51`, `users/middlewares.go` |
| 10 | Comment list has no pagination and overfetches the article | Medium | `articles/routers.go:228-242`, `models.go:164-168` |
| 11 | `Save` with populated associations triggers cascading upserts (comment create, article create/update) | Medium | `articles/routers.go:181-198`, `models.go:144-148,352-356` |
| 12 | Soft-deleted favorites/follows pile up, and missing unique constraints allow duplicate rows | Low-Medium | `FavoriteModel`, `FollowModel`, `favoriteBy`, `following` |
| 13 | Feed builds author IDs in 3 round trips and preloads full users only to read IDs | Low-Medium | `articles/models.go:266-308`, `users/models.go:132-143` |
| 14 | Smaller items: tag list uncached and unbounded, per-tag INSERTs, Gin debug mode, needless queries for anonymous users | Low | various |

---

## 1. Unbounded / negative `limit` and `offset`: one request can dump whole tables (Critical)

**Where:** `FindManyArticle` (`articles/models.go:182-190`) and `GetArticleFeed` (`articles/models.go:271-278`). Both are called with raw `c.Query("limit")` and `c.Query("offset")` from `ArticleList` and `ArticleFeed`.

```go
limit_int, errLimit := strconv.Atoi(limit)
if errLimit != nil { limit_int = 20 }
```

**Problem:** The parsed value is used with no bounds check:
- `?limit=1000000` loads the entire `article_models` table.
- `?limit=-1` does the same: in GORM v1.25 a negative `Limit` emits no `LIMIT` clause at all, and a negative `Offset` is silently dropped.

Each loaded article also triggers:
- the Preloads (`Author`, `Author.UserModel`, `Tags` through `article_tags`), each an `IN (...)` query with one bind parameter per row
- one `isFollowing` query per article during serialization (finding 3)
- full JSON serialization of every `Body` (up to 2 KB each)

So a single anonymous GET to `/api/articles?limit=-1` costs O(N) queries and memory for N articles in the database. This is an easy DoS. With a large table it can also exceed SQLite's bound-variable limit in the `IN` preloads and return an error.

A large `offset` (`?offset=10000000`) also makes SQLite step through and discard every skipped row.

**Fix:**
- Clamp the values: `if limit_int <= 0 || limit_int > 100 { limit_int = 20 }` (or 100), and `if offset_int < 0 { offset_int = 0 }`. Do this in one shared helper used by both list functions.
- Optionally cap `offset` too, or move to keyset pagination (`WHERE (updated_at, id) < (?, ?) ORDER BY updated_at DESC, id DESC LIMIT ?`) if deep paging is a real use case.

---

## 2. Missing indexes on foreign keys and lookup columns (High)

GORM `AutoMigrate` with SQLite creates foreign-key constraints but **no indexes on FK columns**, and SQLite does not create them automatically. The only indexes defined are on `articles.slug`, `tags.tag`, `users.email`, the `deleted_at` columns from `gorm.Model`, and the composite primary key of the `article_tags` join table. Every lookup below is therefore a full table scan, and the cost grows linearly with table size.

| Table.column(s) | Used by | Frequency |
|---|---|---|
| `favorite_models(favorite_id, favorite_by_id)` | `isFavoriteBy`, `BatchGetFavoriteStatus`, `unFavoriteBy`, `favoriteBy` (FirstOrCreate) | every article view and list |
| `favorite_models(favorite_id)` | `favoritesCount`, `BatchGetFavoriteCounts` (GROUP BY) | every article view and list |
| `favorite_models(favorite_by_id)` | `?favorited=` filter and count | list filter |
| `follow_models(followed_by_id, following_id)` | `isFollowing` (N per list, finding 3), `unFollowing`, `following`, `GetFollowings` | **highest frequency query in the app** |
| `user_models(username)` | `FindOneUser({Username})` for profiles, follow/unfollow, `?author=`, `?favorited=` | every profile request |
| `article_user_models(user_model_id)` | `GetArticleUserModel` (every authenticated request that touches articles), feed | very frequent |
| `article_models(author_id)` | `?author=` filter, feed count and list, `Author` preload reverse | feed and profile pages |
| `article_models(updated_at)` | `ORDER BY updated_at DESC` in feed and filtered lists | lists |
| `comment_models(article_id)` | `getComments` | every article page |
| `article_tags(tag_model_id)` | `?tag=` filter (the composite PK is `(article_model_id, tag_model_id)`, so a lookup by tag cannot use it) | tag filter |

**Fix:** Add index tags to the models and let AutoMigrate create them. Prefer unique composite indexes where they also enforce correctness (see finding 12):

```go
// FavoriteModel
FavoriteID   uint `gorm:"uniqueIndex:idx_fav_article_user"`
FavoriteByID uint `gorm:"uniqueIndex:idx_fav_article_user;index"`
// FollowModel
FollowingID  uint `gorm:"uniqueIndex:idx_follow_pair"`
FollowedByID uint `gorm:"uniqueIndex:idx_follow_pair;index"` // order columns so followed_by_id leads if used alone
// ArticleUserModel
UserModelID uint `gorm:"uniqueIndex"`
// UserModel
Username string `gorm:"column:username;uniqueIndex"`
// ArticleModel
AuthorID uint `gorm:"index:idx_author_updated,priority:1"`
UpdatedAt  -> composite (author_id, updated_at) plus a standalone updated_at index
// CommentModel
ArticleID uint `gorm:"index"`
```

For `article_tags`, add a secondary index on `(tag_model_id, article_model_id)` through a migration (`CREATE INDEX idx_article_tags_tag ON article_tags(tag_model_id, article_model_id)`).

Column order matters. `isFollowing` filters on both columns. `GetFollowings` filters only on `followed_by_id`, so either lead with `followed_by_id` or add a separate index. The same applies to favorites: the GROUP BY on `favorite_id` wants `favorite_id` leading.

Because soft delete appends `deleted_at IS NULL` to every query, a partial unique index (`WHERE deleted_at IS NULL`) is the cleanest option for the unique pairs. See finding 12.

Check the result with `EXPLAIN QUERY PLAN` for each query above. Look for `SEARCH ... USING INDEX` and confirm `SCAN` is gone.

---

## 3. N+1: `isFollowing` runs once per article and once per comment (High)

**Where:**
- `users/serializers.go:16-30`: `ProfileSerializer.Response()` calls `myUserModel.isFollowing(self.UserModel)`, which is one `SELECT ... FROM follow_models` per call.
- `articles/serializers.go:84-105`: `ResponseWithPreloaded` calls `authorSerializer.Response()` for every article.
- `articles/serializers.go:152-171`: `CommentSerializer.Response()` does the same for every comment.

The favorite data was already batched (`BatchGetFavoriteCounts` / `BatchGetFavoriteStatus`), but author "following" status was not. For a default page of 20 articles, `GET /api/articles` issues roughly:

- 1 COUNT
- 1 article SELECT + 4 preload SELECTs (article_user_models, user_models, article_tags, tag_models)
- 1 favorite counts, 1 `GetArticleUserModel`, 1 favorite status
- **20 `isFollowing` SELECTs**, each a full scan of `follow_models` until finding 2 is fixed

That is about 30 queries per page, two thirds of them N+1. Comment lists have no limit (finding 10), so a popular article with 500 comments issues 500 follow lookups. The query also runs for anonymous users (`u.ID == 0`), where the answer is always false.

**Fix:**
1. Short-circuit `isFollowing` when `u.ID == 0` (anonymous). This removes the N+1 entirely for logged-out traffic, which is usually the majority.
2. Add a batch helper in `users`:
   ```go
   func BatchIsFollowing(followerID uint, targetIDs []uint) map[uint]bool // SELECT following_id FROM follow_models WHERE followed_by_id = ? AND following_id IN ? AND deleted_at IS NULL
   ```
   Call it once in `ArticlesSerializer.Response()` and `CommentsSerializer.Response()` with the distinct author user IDs. Add a `ProfileSerializer.ResponseWithFollowing(bool)` variant, mirroring the existing `ResponseWithPreloaded`, so the serializer stops querying.
3. Add a regression test that counts queries per list request (GORM callback or logger hook) and asserts a constant bound that does not depend on page size.

---

## 4. Writes on read paths: `GetArticleUserModel` uses `FirstOrCreate`, and the calls are duplicated (High)

**Where:** `articles/models.go:54-65`:

```go
db.Where(&ArticleUserModel{UserModelID: userModel.ID}).FirstOrCreate(&articleUserModel)
```

It is called from:
- `ArticleSerializer.Response()` (every single-article response)
- `ArticlesSerializer.Response()` (every list)
- `ArticleFeed`
- the `?author=` and `?favorited=` branches of `FindManyArticle`
- `ArticleUpdate`, `ArticleDelete`, `ArticleFavorite`, `ArticleUnfavorite`, `ArticleCommentDelete`
- both validators' `Bind`

**Problems:**
- **GET requests write.** The first time any authenticated user views an article, or anyone filters by `?author=X` or `?favorited=X` where X has never posted, the request runs an `INSERT` inside a GORM-default transaction. SQLite allows only one writer at a time, so read traffic now competes for the database write lock. Combined with finding 5, this can stall requests.
- **Unindexed and not unique.** `user_model_id` has no index (finding 2), so every call is a full scan of `article_user_models`. With no unique constraint, two concurrent first requests for the same user can both insert. That creates duplicate author rows, and later lookups may resolve to a different `ArticleUserModel.ID` than the one that owns the user's articles. Authorization checks (`articleModel.AuthorID != articleUserModel.ID`) and feeds can then misbehave.
- **Redundant calls.** One `ArticleFavorite` request calls it twice: once in the handler and once in the serializer. `ArticleFeed` calls it in the handler and again in the serializer.

**Fix:**
- Split the read path from the write path. Add `FindArticleUserModel(userID) (ArticleUserModel, bool)` using plain `First` / `Take`, and use it for every read and authorization check. A user with no author row simply has no favorites or articles, so serializers can treat "not found" as ID 0.
- Create the `ArticleUserModel` exactly once: when the user registers (`UsersRegistration`), or lazily only on write endpoints (create article, favorite, comment).
- Add `uniqueIndex` on `user_model_id`. Where lazy creation remains, use `INSERT ... ON CONFLICT DO NOTHING` (`clause.OnConflict{DoNothing: true}`) and then re-select.
- Resolve it once per request and pass it down instead of calling it in both handler and serializer, for example by caching it in the gin context next to `my_user_model`.

---

## 5. Read-only transactions that mix `tx` and the global DB: SQLite lock stalls (High)

**Where:** `FindManyArticle` (`db.Begin()` at line 192) and `GetArticleFeed` (line 280).

**Problems:**
1. Both functions open a transaction for pure reads. That pins one pooled connection for the whole function and adds BEGIN and COMMIT round trips. The transaction gives no real consistency benefit, because count and page are already separate queries.
2. Inside those transactions, other helpers run on **a different connection** through `common.GetDB()`:
   - `GetArticleUserModel(userModel)` at lines 216 and 238, which can INSERT (finding 4)
   - `self.UserModel.GetFollowings()` at line 281

   In the `?author=` and `?favorited=` branches, the tx has already read `user_models`, so it holds a SQLite SHARED lock. The `FirstOrCreate` INSERT then runs on a second connection and needs an EXCLUSIVE lock to commit. The SHARED lock is held by the same goroutine, which is blocked waiting for that INSERT. The INSERT therefore waits out the busy timeout and fails with `SQLITE_BUSY`; mattn/go-sqlite3 defaults to 5 s. The error is ignored, so the result is a **multi-second stall** followed by an empty or zero author, which returns wrong results.
3. Errors from most `tx.*` calls are ignored (`First`, `Find`), so a failure inside the transaction still reaches `Commit`, and callers get partial data with `err == nil`.

**Fix:**
- Remove `Begin`/`Commit` from both functions and use `db` directly. If a consistent snapshot is truly needed, run *every* query through `tx` and never call helpers that use the global DB inside it.
- Remove the INSERT from these read paths (finding 4).
- Check and propagate errors from each query.

---

## 6. SQLite and connection pool configuration (Medium-High)

**Where:** `common/database.go:49-69`

```go
db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
sqlDB.SetMaxIdleConns(10)
```

**Problems:**
- **Rollback-journal mode, the default.** Readers block writers and writers block readers. Under concurrent API load (Gin serves each request on its own goroutine), any write (favorite, comment, finding 4's INSERTs) serializes against every in-flight read.
- **No explicit `busy_timeout`.** Lock waits rely on the driver default, and errors under contention are ignored throughout the codebase.
- **No `SetMaxOpenConns`.** The pool can open an unbounded number of SQLite connections. Each has its own page cache, and all of them contend on the same file lock, which adds contention with no throughput gain for writes.
- **`gorm.Config{}` defaults.** No `PrepareStmt` cache, so every query is re-prepared. GORM's default per-write transaction is kept even for single-statement writes.
- `db.DB()` is dereferenced even if `gorm.Open` failed (a nil-pointer panic at startup rather than a clear error). This is a robustness issue, not a performance one.

**Fix:**
```go
dsn := dbPath + "?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL&_foreign_keys=on&_txlock=immediate"
db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{PrepareStmt: true, SkipDefaultTransaction: true})
sqlDB.SetMaxOpenConns(8)  // tune; reads are concurrent in WAL
sqlDB.SetMaxIdleConns(8)
sqlDB.SetConnMaxIdleTime(5 * time.Minute)
```

WAL lets readers proceed concurrently with the single writer, which is the biggest single throughput gain available for this workload. `_txlock=immediate` avoids the SHARED-to-EXCLUSIVE upgrade deadlock pattern for write transactions. Only use `SkipDefaultTransaction` if multi-statement writes are explicitly wrapped. If production traffic is expected beyond a single node, plan the move to Postgres/MySQL, since the GORM code is otherwise portable.

---

## 7. Single-article endpoints issue about 9-11 queries (Medium)

**Where:** `GET /api/articles/:slug`, plus the favorite, unfavorite, and update responses, all through `ArticleSerializer.Response()` (`articles/serializers.go:57-80`).

Queries per request:
1. Auth middleware user SELECT, possibly twice (finding 9)
2. `FindOneArticle`: the article, plus Preloads for `article_user_models`, `user_models`, `article_tags`, and `tag_models` (5 queries)
3. `GetArticleUserModel(me)` (1, plus a possible INSERT)
4. `isFavoriteBy` (1)
5. `favoritesCount` (1)
6. `isFollowing` (1)

**Fix:**
- Use the batch helpers already written (`BatchGetFavoriteCounts` / `BatchGetFavoriteStatus` with one ID), or a single query: `SELECT COUNT(*), SUM(favorite_by_id = ?) FROM favorite_models WHERE favorite_id = ? AND deleted_at IS NULL`.
- Skip `isFavoriteBy`, `isFollowing`, and `GetArticleUserModel` when the viewer is anonymous.
- Replace `Preload("Author.UserModel")` with a `Joins("Author").Joins("Author.UserModel")`. GORM supports nested Joins for belongs-to, which collapses 3 queries into 1.
- In `ArticleFavorite` / `ArticleUnfavorite`, the favorited state after the write is already known, so pass it instead of re-querying.
- `ArticleDelete`, `ArticleCommentCreate`, `ArticleCommentList`, and `ArticleCommentDelete` call `FindOneArticle`, which preloads author and tags they never use. Add a lean `FindArticleIDBySlug` / `Select("id, author_id")` variant.

---

## 8. Filtered-list pagination: unordered ID page, then a second fetch (Medium)

**Where:** `FindManyArticle` tag, author, and favorited branches (`articles/models.go:193-254`).

```go
tx.Model(&tagModel).Offset(offset_int).Limit(limit_int).Association("ArticleModels").Find(&tempModels)
...
tx.Preload(...).Where("id IN ?", ids).Order("updated_at desc").Find(&models)
```

**Problems:**
- The first query loads **full article rows** (body included), which are thrown away except for their IDs. The data is then re-fetched in a second query. That is an extra wide query per request.
- The OFFSET/LIMIT is applied **without ORDER BY**, so the page is whatever SQLite's scan order returns. Only the 20 rows on that page are then sorted by `updated_at`. Pages are not globally ordered by recency, and rows can repeat or be skipped across pages. The default branch (line 259) also has no `ORDER BY`. This is mainly a correctness bug, but fixing it properly changes the query shape, so it is listed here.
- Each branch also runs a separate lookup (tag, user, article-user) before the real query.

**Fix:** Write each filter as one ordered, limited query that the indexes from finding 2 can serve, for example:

```go
q := db.Model(&ArticleModel{})
switch {
case tag != "":
    q = q.Joins("JOIN article_tags at ON at.article_model_id = article_models.id").
          Joins("JOIN tag_models t ON t.id = at.tag_model_id AND t.tag = ?", tag)
case author != "":
    q = q.Joins("JOIN article_user_models au ON au.id = article_models.author_id").
          Joins("JOIN user_models u ON u.id = au.user_model_id AND u.username = ?", author)
case favorited != "":
    q = q.Where("article_models.id IN (SELECT f.favorite_id FROM favorite_models f JOIN article_user_models au ON au.id = f.favorite_by_id JOIN user_models u ON u.id = au.user_model_id WHERE u.username = ? AND f.deleted_at IS NULL)", favorited)
}
q.Count(&count)
q.Preload("Author.UserModel").Preload("Tags").Order("article_models.updated_at DESC, article_models.id DESC").Offset(off).Limit(lim).Find(&models)
```

This uses two statements plus preloads, sorts globally and correctly, and has no writes and no transaction.

---

## 9. Auth middleware runs twice on authenticated routes (Medium)

**Where:** `hello.go:42-51`

```go
v1.Use(users.AuthMiddleware(false))
... anonymous groups ...
v1.Use(users.AuthMiddleware(true))
users.UserRegister(v1.Group("/user")) ...
```

Gin's `RouterGroup.Use` appends to the group's handler chain. Groups created after the second `Use` therefore inherit **both** middlewares. On every authenticated request (`/user`, follow/unfollow, create/update/delete article, favorite, comment), the following happen twice:
- the JWT is parsed and HMAC-verified
- `UpdateContextUserModel` runs a `SELECT ... FROM user_models WHERE id = ?`

`UsersLogin` and `UserUpdate` also call `UpdateContextUserModel`, which re-selects a user the handler already holds.

**Fix:**
- Register the anonymous and authenticated route groups as siblings, each with exactly one auth middleware. For example, create `authed := r.Group("/api", users.AuthMiddleware(true))` and `anon := r.Group("/api", users.AuthMiddleware(false))`.
- Alternatively, make the middleware idempotent: skip if `my_user_id` is already set, and in the `auto401` variant only check that the user ID is non-zero.
- In `UsersLogin` and `UserUpdate`, call `c.Set("my_user_model", userModel)` directly instead of re-querying.

---

## 10. Comment list has no pagination and overfetches (Medium)

**Where:** `ArticleCommentList` (`articles/routers.go:228-242`) and `getComments` (`articles/models.go:164-168`).

- It returns every comment for an article in a single response, with no limit and no ordering.
- It needs `comment_models.article_id`, which is unindexed (finding 2).
- It adds one `isFollowing` query per comment (finding 3).
- `FindOneArticle` preloads author, user, and tags only to obtain the article ID.

**Fix:**
- Add `limit`/`offset` (or cursor) parameters with a cap.
- Order by `created_at` and add an index on `(article_id, created_at)`.
- Look up only the article ID by slug, then batch the follow status.
- If the API contract requires returning all comments, at least apply a server-side hard cap.

---

## 11. `Save` with populated associations triggers cascading upserts (Medium)

**Where:**
- `ArticleCommentCreate` (`articles/routers.go:181-198`) sets `commentModel.Article = articleModel`, which is the full article with Author, UserModel, and Tags loaded, and then calls `SaveOne` (`db.Save`).
- `ArticleCreate` saves an article whose `Author` (with `UserModel`) and `Tags` are populated.
- `ArticleUpdate` calls `db.Model(model).Updates(validator.articleModel)` with `Author` and `Tags` populated.

By default GORM upserts every loaded association when it saves a parent (`INSERT ... ON CONFLICT DO NOTHING` / `DO UPDATE` for belongs-to, has-many, and many2many, recursively). A single comment insert can therefore also:
- write to `article_models`
- upsert the article's `article_user_models` and `user_models` row (including the user's password hash column)
- upsert every tag and every `article_tags` row

All of that runs inside one write transaction, which holds the SQLite write lock longer and blocks other requests.

Separately, in `ArticleUpdate`, tag associations are only appended, never replaced. That is a correctness bug, but it also means the join table grows without bound.

**Fix:**
- For comments, set `ArticleID` and `AuthorID` (IDs only) and use `db.Omit(clause.Associations).Create(&comment)`. Populate `Author` for the response from data already in memory.
- For article create, set `AuthorID` and use `Omit("Author")`. Let the tags association be written once.
- For update, use `db.Model(&article).Omit(clause.Associations).Updates(map[string]any{...})` plus `db.Model(&article).Association("Tags").Replace(tags)` when tags change.

---

## 12. Soft-delete tombstones and duplicate rows in favorites/follows (Low-Medium)

**Where:** `FavoriteModel` and `FollowModel` embed `gorm.Model`, so `Delete` is a soft delete. `favoriteBy` and `following` use `FirstOrCreate`, which filters `deleted_at IS NULL`.

**Problems:**
- Every favorite/unfavorite or follow/unfollow toggle leaves a tombstone row and inserts a fresh one. These tables grow with the number of *actions*, not the number of relationships.
- Every count and existence check must skip the tombstones, and without indexes it scans them all.
- There is no unique constraint on the pair, so concurrent double-clicks can create duplicate live rows. That inflates `favoritesCount` and also costs time.

**Fix:**
- Make these join tables hard-delete. Use `db.Unscoped().Delete`, or drop `gorm.Model` in favor of `ID` and `CreatedAt`.
- Add a unique index on `(favorite_id, favorite_by_id)` and `(following_id, followed_by_id)`, and create rows with `ON CONFLICT DO NOTHING`.
- If soft delete must stay, use a partial unique index `WHERE deleted_at IS NULL`, and periodically purge old tombstones.
- For high-read scale, consider a denormalized `favorites_count` column on `article_models`, maintained in the same transaction as favorite and unfavorite.

---

## 13. Feed resolves author IDs in 3 round trips (Low-Medium)

**Where:** `GetArticleFeed` (`articles/models.go:266-308`) and `GetFollowings` (`users/models.go:132-143`).

The current flow:
1. `GetFollowings` selects follow rows and then `Preload("Following")`, which loads full `UserModel` rows (including password hashes) only so their IDs can be read.
2. It selects `article_user_models WHERE user_model_id IN (...)`.
3. It runs COUNT and a SELECT with `author_id IN (...)`.

For a user who follows thousands of accounts, the IN lists become very large, and SQLite has a bind-variable limit.

**Fix:** Use one subquery that the database can optimize with the indexes from finding 2:

```sql
WHERE article_models.author_id IN (
  SELECT au.id FROM article_user_models au
  JOIN follow_models f ON f.following_id = au.user_model_id
  WHERE f.followed_by_id = ? AND f.deleted_at IS NULL)
ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
```

Also apply the limit clamp from finding 1, and drop the read transaction (finding 5).

---

## 14. Smaller items (Low)

- **Tag list** (`getAllTags`, `articles/models.go:170-175`) returns every tag ever created, including orphaned ones, on every home page load, uncached. Return only tags in use (or the top N by usage), and cache the result in memory with a short TTL (for example 30-60 s), invalidated on article create.
- **`setTags`** (`articles/models.go:310-350`) inserts new tags one at a time and runs during `Bind`, before the article save can fail, which leaves orphan tags behind. Batch-insert the missing tags with `ON CONFLICT DO NOTHING` and re-select once, inside the same transaction as the article save. Also de-duplicate the input slice, because duplicate tags in the request currently cause failed inserts and retries.
- **Anonymous viewers**: `isFollowing`, `isFavoriteBy`, and `BatchGetFavoriteStatus` already return early for ID 0 in one place. Apply the same guard everywhere (`isFollowing`, `isFavoriteBy`) to save one query per entity for logged-out traffic.
- **Gin debug mode**: `gin.Default()` without `GIN_MODE=release` runs in debug mode, with verbose route logging and a console logger on every request. Set release mode in production and use a structured, buffered logger.
- **bcrypt `DefaultCost`** on register and login is expected CPU work of roughly 50-100 ms per call, but there is no rate limiting, so login floods can saturate CPU. Add rate limiting on `/users/login` and `/users`. Keep the cost as it is; it exists for security reasons.
- **Response sizes**: list responses include the full `body` of each article. The RealWorld spec allows this, but if the API can evolve, omit `body` in list views. That alone shrinks list payloads and DB reads considerably.

---

## Recommended order of work

1. **Clamp `limit`/`offset`** (finding 1). This takes minutes and closes the DoS.
2. **Add the indexes** (finding 2), and verify each hot query with `EXPLAIN QUERY PLAN`.
3. **Enable WAL, busy_timeout, and pool caps** (finding 6), and **remove the read transactions** (finding 5). Together these remove the lock stalls.
4. **Batch `isFollowing` and short-circuit anonymous viewers** (finding 3), and **stop writing on GETs** (finding 4). After this, list requests drop from about 30 queries to about 8, with a constant query count per request.
5. Rewrite the filtered pagination (finding 8), fix the double middleware (finding 9), and cut single-article queries (finding 7).
6. Clean up association saves, tombstones, the feed query, and the remaining small items (findings 10-14).

To keep these gains, add a test helper that counts SQL statements per request through a GORM callback. Assert fixed upper bounds for the list, feed, article, and comments endpoints, so any new N+1 query fails CI.
