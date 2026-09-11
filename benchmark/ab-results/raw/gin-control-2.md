# Performance Review — `gin-realworld` (Go / Gin / GORM / SQLite)

**Scope:** the whole Go backend — `hello.go`, `common/`, `users/`, `articles/`.
**Reviewer stance:** backend engineer, looking for things that will actually hurt under load, ordered by expected impact.

## Executive summary

The codebase has clearly had one pass of N+1 remediation already — `BatchGetFavoriteCounts`, `BatchGetFavoriteStatus`, and the batched ID lookups in `FindManyArticle`/`GetArticleFeed` are evidence of that. That work is good, but it stopped one layer too early: the **author profile** on every article and comment still issues its own query, so the list endpoints are still N+1, just on a different column.

Underneath that, the bigger structural problem is the data layer itself: **not a single foreign-key column in the schema is indexed**, and the SQLite connection is configured with defaults that will produce `database is locked` errors the moment two writes overlap. Those two items dominate everything else on this list.

Findings below are ordered by what I'd fix first.

| # | Finding | Severity | Location |
|---|---------|----------|----------|
| 1 | No indexes on any foreign-key or lookup column | Critical | `articles/models.go`, `users/models.go` |
| 2 | SQLite configured for failure under concurrency (no WAL, no busy timeout, unbounded pool) | Critical | `common/database.go:49-69` |
| 3 | N+1 on `isFollowing` in every article and comment list | High | `users/serializers.go:35` |
| 4 | Single-article responses fire 3-4 extra queries, one of which is a **write** | High | `articles/serializers.go:67-90`, `articles/models.go:54-84` |
| 5 | `limit`/`offset` are unbounded and unvalidated | High | `articles/models.go:177-190, 266-278` |
| 6 | Write amplification: saving a comment rewrites the article, author, user and tags | Medium-High | `articles/routers.go:193-198`, `articles/validators.go` |
| 7 | Read-only handlers wrapped in explicit transactions, with nested out-of-transaction writes | Medium-High | `articles/models.go:192-264, 280-307` |
| 8 | `PrepareStmt` disabled; per-write implicit transactions | Medium | `common/database.go:57` |
| 9 | `setTags` inserts tags one row at a time, outside a transaction, error discarded | Medium | `articles/models.go:310-350`, `articles/validators.go:47` |
| 10 | No HTTP server timeouts; default `r.Run` | Medium | `hello.go:67` |
| 11 | Deletes leave orphan rows; no cascade | Low-Medium | `articles/models.go:358-368` |
| 12 | Auth middleware hits the DB on every single request | Low-Medium | `users/middlewares.go:30-38` |
| 13 | Misc allocation/CPU papercuts | Low | various |

---

## 1. No indexes on any foreign-key or lookup column — **Critical**

**Where:** `articles/models.go:11-52`, `users/models.go:16-42`

The only indexes declared anywhere in the schema are three `uniqueIndex` tags:

- `ArticleModel.Slug` (`articles/models.go:13`)
- `TagModel.Tag` (`articles/models.go:41`)
- `UserModel.Email` (`users/models.go:19`)

Everything else is unindexed. That includes every column the application actually filters and joins on:

| Column | Table | Queried by |
|---|---|---|
| `author_id` | `article_models` | `GetArticleFeed` (`models.go:300,302`), author listing |
| `article_id` | `comment_models` | `getComments` (`models.go:164-168`) |
| `author_id` | `comment_models` | comment author preload |
| `favorite_id` | `favorite_models` | `favoritesCount`, `BatchGetFavoriteCounts`, `isFavoriteBy` |
| `favorite_by_id` | `favorite_models` | `BatchGetFavoriteStatus`, `unFavoriteBy`, favorited listing |
| `user_model_id` | `article_user_models` | `GetArticleUserModel` (`models.go:60-62`) — called on nearly every request |
| `following_id` / `followed_by_id` | `follow_models` | `isFollowing`, `GetFollowings`, `unFollowing` |
| `username` | `user_models` | `FindOneUser` in `ProfileRetrieve`, `ProfileFollow`, `ProfileUnfollow`, and the `author=` filter in `FindManyArticle:215` |

Every one of those is a **full table scan**. The `gorm.Model` embed does give you an index on `deleted_at`, but that index is useless for these predicates — it has one distinct value for all live rows.

Concretely: `GetArticleUserModel` runs a `SELECT ... WHERE user_model_id = ?` on essentially every authenticated request and scans the whole `article_user_models` table. `isFollowing` (finding 3) scans `follow_models` once *per article in the list*. `BatchGetFavoriteCounts` does a `GROUP BY favorite_id` over the full `favorite_models` table. At a few hundred rows nobody notices; at 100k favorites, an article list page becomes seconds of CPU.

**Fix.** Add index tags and re-run `AutoMigrate`:

```go
// articles/models.go
type ArticleModel struct {
    gorm.Model
    Slug        string `gorm:"uniqueIndex"`
    // ...
    AuthorID    uint   `gorm:"index"`
}

type ArticleUserModel struct {
    gorm.Model
    UserModel   users.UserModel
    UserModelID uint `gorm:"uniqueIndex"`   // one ArticleUserModel per user — unique is correct here
    // ...
}

type FavoriteModel struct {
    gorm.Model
    FavoriteID   uint `gorm:"index:idx_fav_article;uniqueIndex:idx_fav_pair,priority:1"`
    FavoriteByID uint `gorm:"index:idx_fav_by;uniqueIndex:idx_fav_pair,priority:2"`
}

type CommentModel struct {
    gorm.Model
    ArticleID uint `gorm:"index"`
    AuthorID  uint `gorm:"index"`
}
```

```go
// users/models.go
Username string `gorm:"column:username;uniqueIndex"`

type FollowModel struct {
    gorm.Model
    FollowingID  uint `gorm:"uniqueIndex:idx_follow_pair,priority:1"`
    FollowedByID uint `gorm:"index;uniqueIndex:idx_follow_pair,priority:2"`
}
```

Note the composite unique indexes on `(favorite_id, favorite_by_id)` and `(following_id, followed_by_id)`: they make the lookup a single index seek *and* they close the duplicate-row race that `FirstOrCreate` in `favoriteBy` (`models.go:128-136`) and `following` (`users/models.go:108-116`) currently leaves open. Two concurrent favorite requests today can insert two rows and inflate `favoritesCount`.

For `article_models`, also consider `(author_id, updated_at DESC)` since the feed orders by `updated_at desc` and filters on `author_id in (...)` (`models.go:302`) — a composite lets the DB satisfy the sort from the index instead of sorting the result set.

This is the single highest-leverage change in the repo: a tag-only diff plus a migration.

---

## 2. SQLite configured for failure under concurrency — **Critical**

**Where:** `common/database.go:49-69`

```go
db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
// ...
sqlDB.SetMaxIdleConns(10)
```

That is the entire configuration. Three problems, each of which will bite in production:

**a) No WAL mode.** SQLite's default journal mode is `DELETE` (rollback journal), under which a writer blocks *all* readers and readers block writers. With WAL, readers and a single writer proceed concurrently. For a read-heavy API like this, WAL is roughly free throughput.

**b) No busy timeout.** With `mattn/go-sqlite3` and no `_busy_timeout`, a connection that finds the database locked returns `SQLITE_BUSY` **immediately** rather than waiting. Combined with (c), the first two overlapping writes — two users favoriting an article at the same time — surface to the client as a 422 `{"errors":{"database":"database is locked"}}`. This is not a theoretical race; it's the default behavior.

**c) `SetMaxIdleConns(10)` with no `SetMaxOpenConns`.** `MaxOpenConns` defaults to unlimited, so Go will happily open dozens of concurrent connections to a file that supports exactly one writer. Idle connections are capped at 10 but open ones are not, so under load you get connection churn *and* lock contention.

**Fix.** Configure the DSN and pool explicitly:

```go
dsn := dbPath + "?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL&_foreign_keys=on&_txlock=immediate"
db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{
    PrepareStmt:            true,
    SkipDefaultTransaction: true,
    Logger:                 logger.Default.LogMode(logger.Warn),
})
if err != nil {
    return nil, fmt.Errorf("open database: %w", err)
}
sqlDB, err := db.DB()
// ...
sqlDB.SetMaxOpenConns(1)   // SQLite: one writer. See note below.
sqlDB.SetMaxIdleConns(1)
sqlDB.SetConnMaxLifetime(0)
```

On `SetMaxOpenConns(1)`: with a single `*sql.DB` this serializes reads too, which is a real cost. The better pattern for SQLite under a read-heavy load is **two pools** — a write pool pinned to `MaxOpenConns(1)` and a read pool with `MaxOpenConns(N)` — but that's a larger refactor of `common.GetDB()`. If you keep one pool, `SetMaxOpenConns(1)` plus WAL plus a busy timeout is still strictly better than today's unlimited-with-no-timeout, because it converts lock errors into queueing.

**Also worth flagging:** `Init()` swallows every error (`common/database.go:54, 59, 63`) — it prints and continues, so a failed `gorm.Open` leaves `DB` nil and the process starts serving traffic that will panic on the first query. `Init` should return an `error` and `main` should `log.Fatal`. This is a reliability bug rather than a perf one, but it's in the same six lines.

---

## 3. N+1 on `isFollowing` in every article and comment list — **High**

**Where:** `users/serializers.go:24-38`, reached from `articles/serializers.go:69, 94, 103, 162`

`ProfileSerializer.Response()` ends with:

```go
Following: myUserModel.isFollowing(self.UserModel),
```

and `isFollowing` (`users/models.go:121-129`) is a database query.

Now trace the list path. `ArticlesSerializer.Response()` (`articles/serializers.go:116-141`) carefully batches favorites:

```go
favoriteCounts := BatchGetFavoriteCounts(articleIDs)
favoriteStatus := BatchGetFavoriteStatus(articleIDs, articleUserModel.ID)
for _, article := range s.Articles {
    serializer := ArticleSerializer{C: s.C, ArticleModel: article}
    response = append(response, serializer.ResponseWithPreloaded(favorited, count))
}
```

…and then `ResponseWithPreloaded` calls `authorSerializer.Response()` (`articles/serializers.go:103`), which calls `ProfileSerializer.Response()`, which calls `isFollowing`. **One query per article.** The favorites N+1 was fixed; the follows N+1 was left in place directly beneath it.

Same story for comments: `CommentsSerializer.Response()` (`articles/serializers.go:173-180`) loops over comments, and each `CommentSerializer.Response()` hits `isFollowing` at `articles/serializers.go:168`. An article with 100 comments issues 100 follow-lookup queries. Each of those is a full scan of `follow_models` today (finding 1), so the two defects multiply.

For a default 20-article list, the request currently costs roughly: 1 count + 1 articles + 2 preload + 1 `GetArticleUserModel` (a write, see finding 4) + 2 batch favorite queries + **20 follow queries** ≈ 27 round trips, of which 20 are avoidable.

**Fix.** Batch the follow lookup the same way favorites were batched. Add to `users/models.go`:

```go
// BatchGetFollowingStatus returns the set of user IDs that `followerID` follows,
// restricted to `userIDs`.
func BatchGetFollowingStatus(userIDs []uint, followerID uint) map[uint]bool {
    out := make(map[uint]bool, len(userIDs))
    if len(userIDs) == 0 || followerID == 0 {
        return out
    }
    var rows []FollowModel
    common.GetDB().
        Where("followed_by_id = ? AND following_id IN ?", followerID, userIDs).
        Find(&rows)
    for _, r := range rows {
        out[r.FollowingID] = true
    }
    return out
}
```

Then give `ProfileSerializer` a preloaded variant mirroring `ResponseWithPreloaded`:

```go
func (self *ProfileSerializer) ResponseWithPreloaded(following bool) ProfileResponse { ... }
```

and thread the map through `ArticlesSerializer.Response()` / `CommentsSerializer.Response()` → `ArticleUserSerializer` → `ProfileSerializer`. The author IDs are already available from the preloaded `Author.UserModel` on each article, so no extra fetch is needed to build `userIDs`.

This removes 20 queries from the article list and *N* from the comment list, and it makes the existing batching work actually pay off.

A cleaner long-term shape: stop letting serializers touch the database at all. Have handlers assemble a view-model struct (article + author + favorited + count + following) from batch queries, and make serializers pure functions over it. The current design — where rendering a response issues SQL — is what allowed this N+1 to reappear after the last one was fixed, and it will allow the next one too.

---

## 4. Single-article responses fire extra queries, one of them a write — **High**

**Where:** `articles/serializers.go:67-90`, `articles/models.go:54-84`

`ArticleSerializer.Response()` (the non-preloaded variant, used by `ArticleCreate`, `ArticleRetrieve`, `ArticleUpdate`, `ArticleFavorite`, `ArticleUnfavorite`) does:

```go
Favorite:       s.isFavoriteBy(GetArticleUserModel(myUserModel)),
FavoritesCount: s.favoritesCount(),
```

That's three queries — `GetArticleUserModel`, `isFavoriteBy`, `favoritesCount` — plus the `isFollowing` from finding 3, on top of the handler's own `FindOneArticle`. So `GET /articles/:slug` is 4 extra round trips beyond the one it needs.

The sharper problem is `GetArticleUserModel` (`articles/models.go:54-65`):

```go
db.Where(&ArticleUserModel{UserModelID: userModel.ID}).FirstOrCreate(&articleUserModel)
```

`FirstOrCreate` is a **write path**. On a read-only `GET /articles/:slug`, on a read-only `GET /articles`, on `GET /profiles/:username` — this function can `INSERT`. GORM wraps that create in its own transaction (finding 8), which on SQLite means acquiring the write lock and an fsync. On a database configured as in finding 2, that's the operation most likely to hit `SQLITE_BUSY`.

It also runs unindexed (finding 1) and is called *repeatedly within a single request* — e.g. `ArticleUpdate` calls it at `routers.go:108`, then `Bind` calls it again at `validators.go:46`, then the serializer calls it a third time at `serializers.go:80`.

**Fix, in order of effort:**

1. Make `GetArticleUserModel` read-only on read paths. Add a `FindArticleUserModel` that only does `First` and returns a zero model on miss; use `FirstOrCreate` only where an `ArticleUserModel` genuinely must exist (article creation, comment creation, favoriting). Better still: create the `ArticleUserModel` once at user registration and never lazily again.
2. Memoize per request. Resolve the `ArticleUserModel` once in `AuthMiddleware` and stash it in the Gin context next to `my_user_model`, so the three-to-four call sites in a single request become one lookup.
3. Add the `uniqueIndex` on `user_model_id` from finding 1 — without it this scan is linear in the user count, and it also prevents `FirstOrCreate` from racing two rows into existence.

Honestly, `ArticleUserModel` is a shadow copy of `UserModel` that exists only to carry two associations. Collapsing it into `UserModel` would delete this entire class of query. That's a bigger refactor than this review calls for, but it's the root cause.

---

## 5. `limit` and `offset` are unbounded and unvalidated — **High**

**Where:** `articles/models.go:182-190` and `266-278` (duplicated verbatim)

```go
limit_int, errLimit := strconv.Atoi(limit)
if errLimit != nil {
    limit_int = 20
}
```

The default is applied only when parsing *fails*. Any parseable integer is honored:

- `GET /api/articles?limit=1000000` → the server materializes a million `ArticleModel` structs, runs `Preload("Author.UserModel")` and `Preload("Tags")` over all of them, then finding 3 issues a million follow queries, then marshals it all to JSON in memory. One request can exhaust the process.
- `limit=-1` → GORM treats a negative limit as "no limit", same outcome.
- `offset=-5` → passed straight through to SQL.

This is a trivially exploitable memory-and-CPU DoS on an unauthenticated endpoint (`ArticleList` is registered under `ArticlesAnonymousRegister`, `routers.go:27-28`).

**Fix.** Clamp centrally, and share the code between the two call sites:

```go
const (
    defaultLimit = 20
    maxLimit     = 100
)

func parsePagination(limit, offset string) (int, int) {
    l, err := strconv.Atoi(limit)
    if err != nil || l <= 0 {
        l = defaultLimit
    }
    if l > maxLimit {
        l = maxLimit
    }
    o, err := strconv.Atoi(offset)
    if err != nil || o < 0 {
        o = 0
    }
    return l, o
}
```

Separately, deep `OFFSET` pagination is O(offset) in SQLite — the engine walks and discards every skipped row. At `offset=50000` that's real work even with perfect indexes. If deep paging is a real access pattern, switch to keyset pagination (`WHERE updated_at < ? ORDER BY updated_at DESC LIMIT ?`). If it isn't, cap the offset too.

---

## 6. Write amplification on comment and article writes — **Medium-High**

**Where:** `articles/routers.go:193-198`, `articles/validators.go:35-49, 62-72`

`ArticleCommentCreate` does:

```go
commentModelValidator.commentModel.Article = articleModel
if err := SaveOne(&commentModelValidator.commentModel); err != nil {
```

and `CommentModelValidator.Bind` has already set `s.commentModel.Author = GetArticleUserModel(myUserModel)` (`validators.go:70`), where that `ArticleUserModel` carries a populated `UserModel` (`models.go:63`).

GORM auto-upserts associations on create. So inserting one comment row emits, in addition to the `INSERT` into `comment_models`:

- an upsert of the full `ArticleModel` (the `Article` field),
- an upsert of `ArticleUserModel` (the `Author` field),
- an upsert of the nested `users.UserModel`,
- and, because `articleModel` came from `FindOneArticle` with `Preload("Tags")`, upserts into `tag_models` and the `article_tags` join table.

Posting a comment writes to five or six tables. On SQLite, each of those is inside the default per-statement transaction, and the whole thing holds the write lock far longer than it needs to. It also means a comment write can touch the article's `updated_at` and perturb feed ordering.

The same pattern applies to `ArticleCreate` (`routers.go:46`) and to `ArticleUpdate` (`routers.go:121`), where `Updates(articleModelValidator.articleModel)` carries `Author` and `Tags` along for the ride.

**Fix.** Set the foreign key, not the association, and tell GORM to skip association handling:

```go
// ArticleCommentCreate
commentModelValidator.commentModel.ArticleID = articleModel.ID
// and in Bind:
s.commentModel.AuthorID = GetArticleUserModel(myUserModel).ID

// then
db.Omit(clause.Associations).Create(&comment)
```

For `SaveOne`, consider making the association behavior explicit rather than relying on GORM's defaults — `db.Omit(clause.Associations).Save(data)` for the cases that don't need cascading, and a separate explicit path for article↔tag linking.

---

## 7. Read-only handlers wrapped in explicit transactions, with nested out-of-transaction writes — **Medium-High**

**Where:** `articles/models.go:192-264` (`FindManyArticle`), `articles/models.go:280-307` (`GetArticleFeed`)

Both functions open an explicit transaction for what is purely a read:

```go
tx := db.Begin()
// ... several SELECTs ...
err := tx.Commit().Error
```

Three issues:

**a) The transaction buys nothing and costs lock time.** These are read paths. On SQLite, an explicit `BEGIN` holds a shared lock for the duration of every query in the block, including the query-count and preload round trips, and under the default journal mode (finding 2) that blocks writers for the whole span.

**b) There's a lock-ordering hazard.** Inside the open transaction, `FindManyArticle:216` and `:238` call `GetArticleUserModel(userModel)`, which uses `common.GetDB()` — the **global handle, not `tx`** — and performs a `FirstOrCreate`, i.e. a write, on a *different connection*, while `tx` holds a read lock on the same SQLite file. That is a textbook self-deadlock shape. With `SetMaxOpenConns(1)` (recommended in finding 2) it would deadlock outright; with the current unlimited pool and no busy timeout it will intermittently return `SQLITE_BUSY`. `GetArticleFeed:281` has the same problem via `self.UserModel.GetFollowings()`.

**c) No rollback safety.** There's no `defer func() { if r := recover(); ... tx.Rollback() }()`. A panic between `Begin` and `Commit` leaks a connection with an open transaction, permanently, until the process restarts.

**Fix.** Drop the transactions from both read functions entirely — just use `db`. If you want read consistency across the count and the page (a legitimate concern), use `db.Transaction(func(tx *gorm.DB) error { ... })` so cleanup is automatic, **and** pass `tx` down into every helper called inside it (`GetArticleUserModel`, `GetFollowings`) instead of letting them grab the global handle. That means giving those helpers a `db *gorm.DB` parameter — a small, mechanical change that also makes them testable.

**Related, in the same function:** the tag and author branches (`models.go:199-211`, `:222-233`) fetch a page of IDs via `Association(...).Find()` — which orders by the association's natural order — and then re-fetch with `Order("updated_at desc")`. The pagination window and the sort order disagree, so page 2 can repeat or skip articles. That's a correctness bug, but it's also a performance one: you're paying for two round trips to get a page you could get in one with a proper join:

```go
db.Joins("JOIN article_tags ON article_tags.article_model_id = article_models.id").
   Joins("JOIN tag_models ON tag_models.id = article_tags.tag_model_id").
   Where("tag_models.tag = ?", tag).
   Order("article_models.updated_at DESC").
   Offset(off).Limit(lim).
   Preload("Author.UserModel").Preload("Tags").
   Find(&models)
```

One query, correct ordering, correct pagination.

Also note `FindManyArticle:245` computes `count` as the number of the user's favorites, not the number of articles matching the filter — the `articlesCount` returned to the client is wrong on the `favorited=` path.

---

## 8. `PrepareStmt` disabled; implicit per-write transactions — **Medium**

**Where:** `common/database.go:57`

```go
db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
```

An empty config means:

- **`PrepareStmt: false`** — GORM builds and prepares every statement from scratch on every call. Given how many small repeated queries this app issues (`isFollowing`, `GetArticleUserModel`, `favoritesCount`, all called in loops), statement caching is a straightforward 10-30% cut in per-query overhead. Enable `PrepareStmt: true`.
- **`SkipDefaultTransaction: false`** — GORM wraps every single `Create`/`Update`/`Delete` in its own `BEGIN`/`COMMIT`. On SQLite with default `synchronous=FULL`, each commit is an fsync. `setTags` creating 5 tags is 5 fsyncs. Set `SkipDefaultTransaction: true` and manage transactions explicitly where you actually need atomicity.
- **Default logger** — `logger.Default` logs at `Warn` for the main DB, which is fine. But note `TestDBInit` (`database.go:80-83`) sets `logger.Info`, which logs *every SQL statement*. That's correct for tests; just make sure no production path ever calls `TestDBInit`, and that `GIN_MODE=release` is set in production (`.env.example` ships `GIN_MODE=debug`, which leaves Gin's debug-mode route logging and per-request warnings on).

Combined config, building on finding 2:

```go
gorm.Open(sqlite.Open(dsn), &gorm.Config{
    PrepareStmt:            true,
    SkipDefaultTransaction: true,
    Logger:                 logger.Default.LogMode(logger.Warn),
})
```

---

## 9. `setTags` — serial inserts, no transaction, error discarded — **Medium**

**Where:** `articles/models.go:310-350`, called from `articles/validators.go:47`

The batch-fetch half is good — one `WHERE tag IN ?` to find existing tags. The create half is not:

```go
for _, tag := range tags {
    if existing, ok := existingTagMap[tag]; ok { ... } else {
        newTag := TagModel{Tag: tag}
        if err := db.Create(&newTag).Error; err != nil {
            // fall back to fetching the racing insert
        }
    }
}
```

Each missing tag is a separate `INSERT`, each in its own implicit transaction (finding 8), each an fsync. An article with 8 new tags is 8 write transactions. The race-recovery path — catch the error, re-`SELECT` — is a second round trip on top, and it relies on the unique index on `tag` to fire, which at least exists here.

And at the call site:

```go
s.articleModel.setTags(s.Article.Tags)   // validators.go:47 — error dropped
```

The returned error is discarded. If tag creation fails entirely, `Bind` returns `nil` and the article is saved with no tags and no indication anything went wrong.

**Fix.** One batch upsert:

```go
func (model *ArticleModel) setTags(tags []string) error {
    if len(tags) == 0 {
        model.Tags = []TagModel{}
        return nil
    }
    db := common.GetDB()

    newTags := make([]TagModel, 0, len(tags))
    for _, t := range dedupe(tags) {
        newTags = append(newTags, TagModel{Tag: t})
    }
    // single INSERT ... ON CONFLICT DO NOTHING
    if err := db.Clauses(clause.OnConflict{DoNothing: true}).Create(&newTags).Error; err != nil {
        return err
    }
    var all []TagModel
    if err := db.Where("tag IN ?", tags).Find(&all).Error; err != nil {
        return err
    }
    model.Tags = all
    return nil
}
```

Two queries regardless of tag count, race-safe via the existing unique index, no fallback path. And propagate the error at `validators.go:47`.

Also worth noting: `tags` is unvalidated — `ArticleModelValidator.Article.Tags` (`validators.go:15`) has no `max` or `dive,max=...` binding, so a client can post 10,000 tags of 1MB each. Add `binding:"max=10,dive,min=1,max=64"`.

---

## 10. No HTTP server timeouts — **Medium**

**Where:** `hello.go:67`

```go
if err := r.Run(":" + port); err != nil {
```

`gin.Engine.Run` calls `http.ListenAndServe`, which uses a zero-value `http.Server`: **no `ReadTimeout`, no `WriteTimeout`, no `IdleTimeout`, no `ReadHeaderTimeout`**. A client that opens a connection and sends one byte per minute holds a goroutine and a file descriptor indefinitely. A few thousand such connections exhaust the process. This is the classic slowloris exposure and it's free to fix.

There's also no graceful shutdown — `defer sqlDB.Close()` at `hello.go:32` never runs, because `r.Run` blocks until fatal and then `log.Fatal` calls `os.Exit`, which skips deferred functions. In-flight requests are dropped on every deploy, and on SQLite an abrupt exit mid-write leaves a hot journal that the next start must recover.

**Fix:**

```go
srv := &http.Server{
    Addr:              ":" + port,
    Handler:           r,
    ReadHeaderTimeout: 5 * time.Second,
    ReadTimeout:       15 * time.Second,
    WriteTimeout:      30 * time.Second,
    IdleTimeout:       60 * time.Second,
    MaxHeaderBytes:    1 << 20,
}
go func() {
    if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
        log.Fatal(err)
    }
}()
quit := make(chan os.Signal, 1)
signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
<-quit
ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
defer cancel()
_ = srv.Shutdown(ctx)
_ = sqlDB.Close()
```

While you're here: there's no body-size limit on any endpoint. `Description` and `Body` are capped at 2048 by the validator (`validators.go:13-14`), but the validator runs *after* Gin has read the whole body into memory. Add `r.Use(func(c *gin.Context) { c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 1<<20); c.Next() })`.

---

## 11. Deletes leave orphan rows — **Low-Medium**

**Where:** `articles/models.go:358-368`, `articles/routers.go:129-147, 203-226`

`DeleteArticleModel` soft-deletes the article row and nothing else. Its comments, its `favorite_models` rows, and its `article_tags` join rows all survive. Same for user deletion (not implemented, but the pattern would repeat).

The performance consequence is that `favorite_models` and `comment_models` accumulate rows that no query will ever return but every scan must traverse. Since `BatchGetFavoriteCounts` does a `GROUP BY` over the whole favorites table (`models.go:98-102`), orphan rows directly slow down the article list forever. Combined with finding 1's missing indexes, this compounds.

Note also that `gorm.Model` gives soft deletes, so *nothing* is ever physically removed — the tables only grow. That's a deliberate choice, but it needs a companion: either a periodic hard-delete job for rows soft-deleted beyond a retention window, or partial indexes (`WHERE deleted_at IS NULL`) so the indexes from finding 1 stay small.

**Fix.** Delete dependents in a transaction:

```go
func DeleteArticle(slug string) error {
    return common.GetDB().Transaction(func(tx *gorm.DB) error {
        var a ArticleModel
        if err := tx.Where("slug = ?", slug).First(&a).Error; err != nil {
            return err
        }
        if err := tx.Where("article_id = ?", a.ID).Delete(&CommentModel{}).Error; err != nil {
            return err
        }
        if err := tx.Where("favorite_id = ?", a.ID).Delete(&FavoriteModel{}).Error; err != nil {
            return err
        }
        if err := tx.Model(&a).Association("Tags").Clear(); err != nil {
            return err
        }
        return tx.Delete(&a).Error
    })
}
```

Or enable `_foreign_keys=on` in the DSN (finding 2) and declare `constraint:OnDelete:CASCADE` on the associations so the database handles it.

---

## 12. Auth middleware queries the DB on every request — **Low-Medium**

**Where:** `users/middlewares.go:30-38`, `hello.go:43, 48`

`UpdateContextUserModel` does `db.First(&myUserModel, my_user_id)` on every authenticated request. That's one unavoidable-looking query — except that the JWT already carries the user ID, and most handlers need only the ID (authorization checks at `routers.go:109, 136, 215` compare IDs). The full user row is needed only for serialization.

More notably, `AuthMiddleware` is registered **twice** on the `v1` group (`hello.go:43` with `auto401=false`, then `hello.go:48` with `auto401=true`). Gin's `Use` appends to the group's handler chain, and because `v1.Group(...)` copies the chain at the time of the call, the `/articles` group registered at `hello.go:52` carries **both** middlewares. That means two `jwt.Parse` calls and **two** `UpdateContextUserModel` DB lookups per request on the authenticated article routes. HMAC verification is cheap, but the duplicate DB round trip is not, and the duplication is easy to miss.

**Fix.** Restructure so each route group gets exactly one auth middleware — build separate groups rather than layering `Use` on a shared parent:

```go
v1 := r.Group("/api")
users.UsersRegister(v1.Group("/users"))

optional := v1.Group("", users.AuthMiddleware(false))
articles.ArticlesAnonymousRegister(optional.Group("/articles"))
articles.TagsAnonymousRegister(optional.Group("/tags"))
users.ProfileRetrieveRegister(optional.Group("/profiles"))

required := v1.Group("", users.AuthMiddleware(true))
users.UserRegister(required.Group("/user"))
users.ProfileRegister(required.Group("/profiles"))
articles.ArticlesRegister(required.Group("/articles"))
```

If the user-row lookup shows up in profiling after that, a small TTL cache (`sync.Map` or `golang-lru`, keyed by user ID, 30-60s) keyed off the JWT is the next step — but fix the duplication first, it's half the cost.

---

## 13. Allocation and CPU papercuts — **Low**

These are minor next to the above, but they're cheap to fix and they're in hot paths.

**a) Slices grown without capacity hints.** `articles/serializers.go:123-126` builds `articleIDs` with `append` into a nil slice; same at `models.go:207-209, 228-230, 249-251, 286-288, 293-296`, and `users/models.go:150-152`. Each is a known length. Use `make([]uint, 0, len(s.Articles))`. At 20 items this is noise; at a raised `limit` (finding 5) it's several reallocations and copies per request.

**b) `sort.Strings` on every article.** `articles/serializers.go:88, 112` sorts the tag list per article on every serialization. Tags are small so this is cheap, but it's also unnecessary — sort once when tags are written (`setTags`), or add `Order("tag")` to the `Preload`, and the read path becomes free.

**c) Timestamp formatting.** `CreatedAt.UTC().Format("2006-01-02T15:04:05.999Z")` appears six times across `articles/serializers.go`. `time.Format` allocates a string each call; at 20 articles × 2 timestamps × (article + author) that's a lot of small garbage. Not worth restructuring for, but if this endpoint ever gets hot, `AppendFormat` into a reused buffer is the move. Worth noting the layout string is also *wrong* — a literal `Z` with no offset means the output claims UTC regardless; it happens to be correct only because `.UTC()` is called first. Use `time.RFC3339Nano` (the commented-out line at `serializers.go:77` had it right).

**d) `RandString` calls `crypto/rand` once per character.** `common/utils.go:19-29` makes one syscall-backed `rand.Int` per rune. It's only used in tests today, so impact is nil — but if it ever moves into request handling (slug disambiguation, token generation), read one buffer of bytes and index into `letters` with rejection sampling.

**e) `common.GetDB()` is called inside loops and per-helper.** It's just a global read so it's nearly free, but the pattern means every helper reaches for global state, which is what makes finding 7's transaction hazard possible. Passing `*gorm.DB` explicitly fixes a correctness problem and a testability problem at the same time.

**f) `Description` and `Body` are `size:2048` in the schema** (`articles/models.go:15-16`) but `Body` is the article's full text. If that limit is ever raised, note that both columns are selected on every list query — `SELECT *` via GORM — so the article list transfers every article's full body even though a list view typically doesn't render it. Adding `.Select("id, slug, title, description, created_at, updated_at, author_id")` to the list query would cut the payload substantially at higher body sizes.

---

## Recommended order of work

**Week 1 — do these first, they're small and they dominate:**

1. Add the index tags (finding 1). Pure struct-tag diff plus migration. Biggest win per line changed.
2. Fix the SQLite DSN and pool config (finding 2) — WAL, busy timeout, `PrepareStmt`, `SkipDefaultTransaction`, bounded pool. One function.
3. Clamp `limit`/`offset` (finding 5). Ten lines, closes an unauthenticated DoS.
4. Add HTTP server timeouts and graceful shutdown (finding 10).
5. De-duplicate the auth middleware registration (finding 12) — halves auth cost on authenticated article routes.

**Week 2 — the query-shape work:**

6. Batch `isFollowing` (finding 3). This is the remaining N+1 and it's the one that makes list endpoints scale with page size.
7. Make `GetArticleUserModel` read-only and memoize it per request (finding 4).
8. Remove the read-path transactions and the nested out-of-transaction writes (finding 7); replace the tag/author association pagination with a proper join.
9. Batch `setTags` and propagate its error (finding 9).

**Later:**

10. Fix write amplification on comment/article saves (finding 6).
11. Cascade deletes (finding 11).
12. The papercuts (finding 13).

**Structural recommendation, beyond the individual fixes:** move database access out of the serializer layer. Every one of the N+1 findings here — the one that was fixed, and the one in finding 3 that wasn't — exists because `Response()` methods issue SQL. Serializers should be pure functions from an assembled view-model to JSON, with all fetching done in the handler or a service layer. Until that boundary exists, each N+1 fix is a patch and the next one will be reintroduced the same way.

**What I'd measure.** There are no benchmarks or load tests in the repo, and the GORM logger is at default. Before and after this work, I'd want: `logger.Info` behind a flag in a staging environment to count queries per request (the article list should go from ~27 to ~6); `go test -bench` on `ArticlesSerializer.Response()` with a seeded 10k-row database; and a `hey`/`vegeta` run against `GET /api/articles` at concurrency 50 to confirm the SQLite lock errors from finding 2 are actually gone rather than merely rarer.
