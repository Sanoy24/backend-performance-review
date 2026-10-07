# Performance Review: gin-realworld (Go / Gin / GORM / SQLite)

## Scope and context

I reviewed all non-test Go source: `hello.go`, `common/`, `users/`, `articles/`. The service is the RealWorld "Conduit" API. It runs on Gin with GORM v1.25 on a single SQLite file (`./data/gorm.db`). Request handling is synchronous. Nearly all latency comes from the number and shape of SQL statements per request and from SQLite's single-writer locking. The findings below are ordered by expected production impact.

### Query budget today (authenticated `GET /api/articles?limit=20`)

| Step | Queries |
|---|---|
| `AuthMiddleware(false)` + `AuthMiddleware(true)` both run, each loading the user | 2 |
| `BEGIN` / `COMMIT` around a read-only transaction | 2 |
| `COUNT(*)` + article page | 2 |
| Preload `Author`, `Author.UserModel`, `article_tags`, `tags` | 4 |
| `BatchGetFavoriteCounts` | 1 |
| `GetArticleUserModel` (`FirstOrCreate`, which can be a write) | 1–2 |
| `BatchGetFavoriteStatus` | 1 |
| `isFollowing` **per article** (N+1) | 20 |
| **Total** | **~33–34 statements** |

With `limit=100` this grows to about 115 statements. A good implementation needs about 5–6.

---

## Summary table

| # | Finding | Severity | Effort |
|---|---|---|---|
| 1 | N+1 `isFollowing` in every list, feed and comment serializer; ~6–8 queries per single article | **Critical** | Low–Med |
| 2 | Missing indexes on every foreign-key / lookup column (incl. `users.username`) | **Critical** | Low |
| 3 | Unbounded / negative `limit`, and unpaginated comments and tags | **High** | Low |
| 4 | SQLite runs with default journal, no `busy_timeout` and no pool limits; GORM default write transactions | **High** | Low |
| 5 | Read endpoints open transactions and perform writes (`FirstOrCreate`) | **High** | Low–Med |
| 6 | Auth middleware runs twice on authenticated routes | Medium | Low |
| 7 | Filtered article lists use a 3–4-step fetch and paginate before ordering | Medium | Med |
| 8 | Saving an article or comment cascades upserts into loaded associations | Medium | Low |
| 9 | Soft-delete on favorites, follows and comments makes tables grow without bound | Medium | Med |
| 10 | Per-request `COUNT(*)` / favorite counts and uncached tag list | Low–Med | Med |
| 11 | Production config: Gin debug mode, request logger, and smaller items | Low | Low |

---

## 1. N+1 queries in serializers (`isFollowing`, `isFavoriteBy`, `favoritesCount`) — Critical

**Where**
- `users/serializers.go:23-36` `ProfileSerializer.Response()` calls `myUserModel.isFollowing(self.UserModel)` (`users/models.go:93-101`). That is one `SELECT` on `follow_models` per serialized profile.
- `articles/serializers.go:93-114` `ArticleSerializer.ResponseWithPreloaded`. The batch path (`ArticlesSerializer.Response`, lines 116-141) fixed favorites, but `authorSerializer.Response()` still runs one `isFollowing` query **per article**.
- `articles/serializers.go:161-179` `CommentsSerializer.Response`: one `isFollowing` query per comment, on an unpaginated comment list (see #3).
- `articles/serializers.go:67-90` `ArticleSerializer.Response()` (single article: retrieve, create, update, favorite, unfavorite) runs `GetArticleUserModel` (1–2 queries) + `isFavoriteBy` + `favoritesCount` + `isFollowing`. Add `FindOneArticle`'s 3–5 preload queries and every single-article endpoint costs **8–10 statements** before auth.

**Impact.** List and feed endpoints are the hottest paths in this app. Query count grows linearly with page size, and with comment count on the comments endpoint. On SQLite each statement is cheap (~20–100 µs), but they are serialized on the connection and contend with writers (#4). On any networked DB this becomes N round trips.

**Correctness side-effect worth fixing at the same time.** GORM ignores zero-value fields in struct conditions. For an anonymous viewer (`myUserModel.ID == 0`), `isFollowing` runs `WHERE following_id = ?` without the `followed_by_id` filter. It returns `true` if *anyone* follows the author. `isFavoriteBy` has the same bug when the viewer has no `ArticleUserModel`. The anonymous path also wastes a query that should never run.

**Fix**
1. Add a batch helper in `users`:
   ```go
   func FollowingSet(viewerID uint, authorIDs []uint) map[uint]bool {
       if viewerID == 0 || len(authorIDs) == 0 { return map[uint]bool{} }
       var ids []uint
       common.GetDB().Model(&FollowModel{}).
           Where("followed_by_id = ? AND following_id IN ?", viewerID, authorIDs).
           Pluck("following_id", &ids)
       ...
   }
   ```
2. Give `ProfileSerializer` a `ResponseWithFollowing(bool)` variant. In `ArticlesSerializer.Response` and `CommentsSerializer.Response`, collect the distinct author `UserModel.ID`s, call `FollowingSet` once, and pass the result down.
3. Route the single-article `ArticleSerializer.Response()` through the same batch helpers with a one-element slice, or a single query that joins the `favorited` / `favoritesCount` / `following` aggregates.
4. Short-circuit every "viewer-relative" lookup when the viewer ID is 0. Use explicit `Where("col = ?", v)` instead of struct conditions wherever zero is a meaningful value.

**Expected result.** A list of 20 drops from ~34 statements to ~13 (and to ~6 after #5–#7). The comment list drops from 1+N to a constant.

---

## 2. Missing indexes on foreign-key and lookup columns — Critical

**Where.** Model definitions in `articles/models.go:10-51` and `users/models.go:15-40`. GORM's `AutoMigrate` creates indexes only for `primaryKey`, `uniqueIndex`/`index` tags and `gorm.Model.DeletedAt`. It does **not** index foreign keys on SQLite. Columns below are filtered on in hot paths and have no index:

| Table.column(s) | Used by | Current plan |
|---|---|---|
| `user_models.username` | `ProfileRetrieve/Follow/Unfollow` (`FindOneUser{Username}`), `?author=`, `?favorited=` | full scan of users |
| `follow_models (followed_by_id, following_id)` | `isFollowing` (N times per page), `GetFollowings` (feed), `unFollowing` | full scan |
| `favorite_models (favorite_id)` and `(favorite_by_id, favorite_id)` | `BatchGetFavoriteCounts`, `BatchGetFavoriteStatus`, `favoritesCount`, `isFavoriteBy`, `?favorited=`, unfavorite | full scan |
| `article_models (author_id, updated_at)` | Feed (`author_id IN … ORDER BY updated_at DESC`), `?author=` | full scan + sort |
| `article_models (updated_at)` | Ordering in all list variants | sort of full result |
| `comment_models (article_id)` | `getComments` | full scan |
| `article_user_models (user_model_id)` | `GetArticleUserModel` (called on almost every authenticated request), feed | full scan |
| `article_tags (tag_model_id)` | `?tag=` filter (join PK is `(article_model_id, tag_model_id)`, so a tag-side lookup can't use it) | full scan of join table |

Combined with #1, the per-article `isFollowing` is an O(rows in follow_models) scan executed 20 times per page. That is the single largest scaling cliff in the codebase: latency grows with *total data size*, not just page size.

**Fix.** Add tags and let `AutoMigrate` create them (or use explicit migrations):
```go
// users
Username string `gorm:"column:username;uniqueIndex"`   // RealWorld usernames are unique
type FollowModel struct {
    gorm.Model
    Following    UserModel
    FollowingID  uint `gorm:"uniqueIndex:idx_follow_pair,priority:2;index"`
    FollowedBy   UserModel
    FollowedByID uint `gorm:"uniqueIndex:idx_follow_pair,priority:1"`
}
// articles
AuthorID     uint `gorm:"index:idx_article_author_updated,priority:1"`
// gorm.Model.UpdatedAt: add a composite idx (author_id, updated_at) via explicit migration, plus idx on updated_at
FavoriteID   uint `gorm:"index;uniqueIndex:idx_fav_pair,priority:2"`
FavoriteByID uint `gorm:"uniqueIndex:idx_fav_pair,priority:1"`
ArticleID    uint `gorm:"index"`             // CommentModel
UserModelID  uint `gorm:"uniqueIndex"`       // ArticleUserModel (also stops duplicate rows from FirstOrCreate races)
```
Add `CREATE INDEX idx_article_tags_tag ON article_tags(tag_model_id, article_model_id)`.

Unique pair indexes on favorites and follows also make `FirstOrCreate` races safe. Note that soft-deleted rows (#9) conflict with them, so do #9 together with this.

Verify with `EXPLAIN QUERY PLAN` on each statement above. Every one should show `SEARCH ... USING INDEX`, not `SCAN`.

---

## 3. Unbounded result sizes — High

**Where**
- `articles/models.go:177-185` and `266-274`: `limit` and `offset` come from the query string with no bounds check. `?limit=1000000` returns the whole table, fully preloaded (author, user, tags) and serialized, and #1 adds one extra query per row. `?limit=-1` is passed to GORM `Limit(-1)`, which GORM treats as **no limit**. Negative offsets are also accepted.
- `articles/models.go:164-168` `getComments`: loads every comment on an article with no pagination. #1 then makes it 1+N queries.
- `articles/models.go:170-175` `getAllTags`: `SELECT * FROM tag_models` with no limit, on every home-page load. It also returns orphan tags whose articles were deleted.

**Impact.** This is a trivially reachable memory, CPU and latency DoS on anonymous endpoints. One request can pin the process and hold a SQLite read lock for the whole scan, which blocks writers (#4).

**Fix.** Parse limit and offset once in a shared helper: `limit` defaults to 20, clamps to `[1, 100]`, and rejects or clamps negatives; `offset` must be `>= 0`. Paginate comments with a limit and a `created_at` or `id` cursor, or at minimum add a hard cap. For tags, return only tags in use (`JOIN article_tags`), ordered by popularity with a cap (e.g. top 20–50), and cache it (#10). Consider keyset pagination (`WHERE updated_at < ?`) if deep offsets become common, since `OFFSET n` costs O(n).

---

## 4. SQLite and GORM connection configuration — High (under concurrency)

**Where.** `common/database.go:39-58`.
```go
db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
sqlDB.SetMaxIdleConns(10)
```
**Problems**
- **Rollback-journal mode (the default).** Readers and the writer block each other. A long read (e.g. #3) blocks every write, and a write blocks reads at commit.
- **No `busy_timeout`.** Concurrent writers fail immediately with `SQLITE_BUSY: database is locked` instead of waiting a few ms. Under modest concurrency (favorites, comments, plus the hidden writes in #5) users get 422 errors.
- **No `SetMaxOpenConns`.** `database/sql` will open unbounded connections to the same file, and they all fight over the single write lock.
- **`SkipDefaultTransaction` is off.** Every `Save`/`Create`/`Update`/`Delete` is wrapped in its own `BEGIN…COMMIT`, which costs extra statements and an fsync each time.
- **`PrepareStmt` is off.** Each call re-prepares its SQL.
- `synchronous=FULL` (the default in rollback mode) costs an fsync per commit.
- `gorm.Open` errors are printed and ignored. On failure, `db.DB()` dereferences nil. This is not a performance issue, but it is fragile.

**Fix**
```go
dsn := dbPath + "?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL&_foreign_keys=on&_txlock=immediate"
db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{
    SkipDefaultTransaction: true,
    PrepareStmt:            true,
    Logger:                 logger.Default.LogMode(logger.Warn),
})
sqlDB.SetMaxOpenConns(runtime.NumCPU()*2) // readers; WAL allows concurrent readers + 1 writer
sqlDB.SetMaxIdleConns(runtime.NumCPU()*2)
sqlDB.SetConnMaxIdleTime(5 * time.Minute)
```
For heavy write load, use two pools: a 1-connection writer `*gorm.DB` with `_txlock=immediate`, and a multi-connection read-only pool. If the service is expected to scale horizontally, plan a move to Postgres. SQLite on a single file caps out at one writer and one host.

---

## 5. Transactions and writes on read-only paths — High

**Where**
- `articles/models.go:187` and `280`: `FindManyArticle` and `GetArticleFeed` call `db.Begin()` to run purely read queries. Errors from the inner queries are not checked, so the transaction gives no consistency benefit. It adds two statements and holds a connection (and a SHARED lock in rollback mode) for the whole request.
- In the same functions, `GetArticleUserModel(...)` (lines 211, 233) and `self.UserModel.GetFollowings()` (line 281) use `common.GetDB()` instead of `tx`. They therefore take a **second pool connection** while the first holds an open transaction. Under pool exhaustion that can deadlock. With SQLite it is a self-inflicted lock conflict when `FirstOrCreate` decides to INSERT.
- `articles/models.go:54-65` `GetArticleUserModel` uses **`FirstOrCreate`**, and it runs on read endpoints: list (`serializers.go:131`), single-article serializer, `?author=`, `?favorited=`, and feed. Any authenticated GET can perform an INSERT, which takes SQLite's write lock. `?author=<name>` and `?favorited=<name>` even create rows for *other* users whenever someone browses them. There is no unique index on `user_model_id` (#2), so concurrent first requests can create duplicates.

**Fix**
- Delete the `Begin`/`Commit` from both list functions. If snapshot consistency between `COUNT` and the page matters, use `db.Transaction(func(tx *gorm.DB) error {...})` and pass `tx` to *every* helper, but it is not needed here.
- Split `GetArticleUserModel` into a read-only `FindArticleUserID(userID) (uint, bool)` for read paths, and keep `FirstOrCreate` only for writes (create article, comment, favorite). Better: drop the `ArticleUserModel` indirection entirely and make `ArticleModel.AuthorID`, `CommentModel.AuthorID` and `FavoriteModel.FavoriteByID` reference `user_models.id` directly. This removes one join and one lookup from nearly every request. It requires a data migration.
- Cache the viewer's `ArticleUserModel.ID` in the gin context once per request rather than looking it up 2–3 times (it is currently looked up in the validator, the serializer and the handler).

---

## 6. Auth middleware runs twice per authenticated request — Medium

**Where.** `hello.go:43` and `hello.go:48`:
```go
v1.Use(users.AuthMiddleware(false))
...
v1.Use(users.AuthMiddleware(true))
users.UserRegister(v1.Group("/user")) // gets BOTH middlewares
```
Gin's `Use` appends to the group's handler chain, so every route registered after line 48 (`/user`, `/profiles/:u/follow`, all article write routes, `/articles/feed`) runs **both** middlewares. Each one parses and verifies the JWT and runs `SELECT * FROM user_models WHERE id = ?` (`users/middlewares.go:19-27`). That is a duplicate signature check and a duplicate DB round trip on every authenticated request. Also, `UserUpdate` (`users/routers.go:124`) reloads the user a third time, and `UsersLogin` (`users/routers.go:101`) runs `UpdateContextUserModel` to re-query a user it already loaded.

**Fix.** Use separate groups: `optional := r.Group("/api", AuthMiddleware(false))` and `required := r.Group("/api", AuthMiddleware(true))`. Alternatively, make the middleware a no-op when `my_user_id` is already set. In the login and update handlers, put the already-loaded model in the context with `c.Set` instead of re-querying.

---

## 7. Filtered article lists: multi-step fetch, pagination before ordering — Medium

**Where.** `articles/models.go:187-255` (the `tag`, `author` and `favorited` branches).

Each branch does: (1) look up the tag or user, (2) `Association(...).Offset().Limit().Find()` to fetch IDs, (3) `Association(...).Count()`, (4) re-fetch the same articles by `id IN ?` with `ORDER BY updated_at desc`, plus 4 preload queries. Problems:
- The offset and limit are applied in step 2 **without an ORDER BY**, and the ordering is only applied in step 4 to that arbitrary page. Pages are therefore not the "newest N". This is a correctness bug, and it makes stable pagination impossible. The default (no filter) branch at line 251-254 has **no `ORDER BY` at all**.
- Step 2 loads full article rows (bodies up to 2 KB each) only to extract IDs. Step 4 then loads them again.
- The `favorited` branch paginates favorites, not articles, and counts soft-deleted-aware favorites via association.

**Fix.** Express each filter as one query with a join or subquery, ordered and paginated in SQL, then preload:
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
    q = q.Where("article_models.id IN (?)", db.Table("favorite_models f").
          Select("f.favorite_id").
          Joins("JOIN article_user_models au ON au.id = f.favorite_by_id").
          Joins("JOIN user_models u ON u.id = au.user_model_id AND u.username = ?", favorited).
          Where("f.deleted_at IS NULL"))
}
q.Count(&count)
q.Order("article_models.updated_at DESC").Limit(limit).Offset(offset).
  Preload("Author.UserModel").Preload("Tags").Find(&models)
```
This cuts 4–6 statements down to 2, plus preloads, and gives correct ordering. The indexes from #2 make each one an index search.

---

## 8. Cascading association upserts on create and update — Medium

**Where**
- `articles/validators.go:46-47` loads `Author` (with nested `UserModel`) and `Tags` onto the article. `SaveOne(&articleModel)` (`articles/routers.go:46`) then makes GORM upsert **every loaded association**: `INSERT ... ON CONFLICT DO NOTHING` into `article_user_models`, `user_models`, `tag_models`, then the `article_tags` join rows.
- `articles/routers.go:121`: `articleModel.Update(articleModelValidator.articleModel)` passes a struct that includes `Author` and `Tags`. That causes the same association upserts on every edit, and it also re-runs `setTags`, which can create tag rows. (Tags are also not actually replaced; they are only added to.)
- `articles/routers.go:193-195`: a comment create sets `commentModel.Article = articleModel` (a fully preloaded article with author, user and tags) before `SaveOne`. Every new comment therefore re-upserts the article, its author, the user, every tag and every join row: roughly 6–10 extra write statements under the write lock for a one-row insert.
- `setTags` (`articles/models.go:310-350`) issues one `INSERT` per new tag in a loop, with no transaction.

**Fix.** Set foreign keys, not structs: `comment.ArticleID = article.ID`, `comment.AuthorID = authorID`, `article.AuthorID = authorID`. Use `db.Omit(clause.Associations).Create(...)` / `.Updates(map[string]any{...})` for scalar updates. Manage tags explicitly with `db.Model(&a).Association("Tags").Replace(tags)` inside one transaction. Create missing tags in one batch with `clause.OnConflict{DoNothing: true}` followed by one `SELECT ... WHERE tag IN ?`.

---

## 9. Soft-delete on high-churn relationship tables — Medium

**Where.** `FavoriteModel`, `FollowModel` and `CommentModel` embed `gorm.Model`, so `Delete` (`articles/models.go:139-142`, `users/models.go:107-110`, `articles/models.go:362-366`) only sets `deleted_at`. Every favorite/unfavorite or follow/unfollow toggle leaves a dead row behind, and the next `FirstOrCreate` inserts a new one. These tables grow without bound with user churn, and every query on them carries `AND deleted_at IS NULL`. The `deleted_at` index that `gorm.Model` adds is not usable together with the lookup columns. Deleting an article also leaves its comments, favorites and `article_tags` rows behind.

**Fix.** These are pure relationship rows, so hard-delete them (`db.Unscoped().Delete(...)`), or drop `gorm.Model` from them in favor of `{ID, CreatedAt}` plus the unique pair index from #2. If soft delete is required for audit, use partial unique indexes (`WHERE deleted_at IS NULL`) and include `deleted_at` as the last column of the composite indexes. Cascade or clean up dependents when an article is deleted.

---

## 10. Repeated aggregates and uncached global data — Low to Medium

- **`COUNT(*)` on every list request** (`articles/models.go:251-253`, 203, 213, 239, 294). This is a full index or table scan on SQLite and grows with data. Consider caching the global count briefly (a few seconds), or let clients use "has next page" (fetch `limit+1`) instead of an exact total, if the API contract allows it.
- **Favorite counts are recomputed per request** (`BatchGetFavoriteCounts`, `favoritesCount`). With the index from #2 this is acceptable. At scale, denormalize into `article_models.favorites_count` and update it in the same transaction as favorite/unfavorite (`UPDATE ... SET favorites_count = favorites_count ± 1`).
- **Tag list** (`TagList`): this is identical for every caller and changes rarely. Serve it from an in-process cache (e.g. `atomic.Value` refreshed every 30–60 s, or invalidated on article create or update) and set `Cache-Control: public, max-age=60`.

---

## 11. Production configuration and smaller items — Low

- **Gin debug mode and logger.** `.env.example` sets `GIN_MODE=debug`. `gin.Default()` (`hello.go:35`) adds the Logger middleware, which writes a synchronous line to stdout for every request. In production, set `GIN_MODE=release` and use `gin.New()` with `gin.Recovery()` plus a structured, buffered or sampled logger.
- **Over-fetching.** `FindOneComment` (`articles/models.go:157-162`) preloads `Article` (full body) and `Author.UserModel` just to compare `AuthorID` before a delete. Select only `id, author_id`. `ArticleDelete`, `ArticleFavorite`, `ArticleUnfavorite` and `ArticleUpdate` load the article with all preloads even when only `id` and `author_id` are needed before the write. Load preloads only when serializing the response.
- **`GetFollowings` loads full user rows** (`users/models.go:115-126`) via `Preload("Following")` just to collect IDs, and the feed then maps them to `ArticleUserModel` IDs in a third query. Replace this with one subquery: `author_id IN (SELECT au.id FROM article_user_models au JOIN follow_models f ON f.following_id = au.user_model_id WHERE f.followed_by_id = ? AND f.deleted_at IS NULL)`.
- **bcrypt on login and registration** (`users/models.go:47-53`) at `DefaultCost` (10) costs about 50–100 ms of CPU per call. This is intentional and correct, but the login and registration endpoints have no rate limiting, so they are an easy CPU-exhaustion vector. Add per-IP and per-account rate limiting. Separately, the `setPassword` error from bcrypt is ignored.
- **`GenToken`** is called on every `UserSerializer.Response()`, including `GET /user`. HMAC signing is cheap, so this is negligible, but the existing token could be returned instead.
- **Test DB logger.** `TestDBInit` uses `logger.Info`, which logs every SQL statement. That is fine for tests, but make sure it never ships in production code paths.

---

## Recommended order of work

1. **#2 indexes** and **#3 limit clamping**: minimal code, largest risk reduction, deploy immediately.
2. **#4 SQLite config** (WAL, `busy_timeout`, `SkipDefaultTransaction`, `PrepareStmt`, pool sizing): one-file change.
3. **#1 batch `isFollowing`** and **#6 single auth middleware**: removes N+1 and duplicate work from every request.
4. **#5** remove read transactions and read-path `FirstOrCreate`; **#8** stop association cascades.
5. **#7** rewrite filtered list queries (also fixes ordering correctness), then **#9** and **#10**.

After steps 1–3, a page of 20 articles should take ~10 statements, each an index lookup, down from ~34 mostly full-scan statements. Lock-contention errors under concurrent writes should largely disappear. Validate with `EXPLAIN QUERY PLAN`, GORM's statement logger with a count per request (add a callback that increments a per-request counter in tests), and a load test (e.g. `k6` or `hey`) mixing ~90% list/feed reads with ~10% favorite and comment writes.
