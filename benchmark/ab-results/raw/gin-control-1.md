# Performance Review — `gin-realworld` (Go / Gin / GORM / SQLite)

**Scope:** the whole backend — `hello.go`, `common/`, `users/`, `articles/`.
**Reviewed at:** module `github.com/gothinkster/golang-gin-realworld-example-app`, Go 1.21, Gin v1.10.0, GORM v1.25.12, `gorm.io/driver/sqlite` v1.5.7 (cgo / mattn-go-sqlite3).

---

## Executive summary

Someone has clearly already done one pass of N+1 work here — `BatchGetFavoriteCounts`, `BatchGetFavoriteStatus`, `setTags`' batch lookup, and the ID-collection rewrites in `FindManyArticle` are all real improvements. That pass fixed the *favorites* N+1 and missed the *follows* N+1, which sits one call deeper in the serializer chain and is just as hot.

Beyond that, the dominant problems are not in application logic at all — they're in the storage layer. **The schema has no index on a single foreign key.** Every `WHERE author_id = ?`, `WHERE favorite_id = ?`, `WHERE following_id = ?`, `WHERE username = ?` in this codebase is a full table scan. Combined with SQLite opened at defaults (rollback journal, no `busy_timeout`, unbounded connection pool), the service will look fine on a seeded dev database and fall over somewhere in the low thousands of rows / low tens of concurrent requests.

Rough query cost for `GET /api/articles?limit=20` as authenticated user today: **~30 SQL statements**, of which 20 are unindexed full scans of `follow_models`. After findings 1–3 that becomes **~9 statements, all index-served**.

### Priority table

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | N+1 on `isFollowing` in every profile serialization | **Critical** | M |
| 2 | No indexes on any foreign key or lookup column | **Critical** | S |
| 3 | SQLite opened at defaults — no WAL, no busy timeout, unbounded pool | **Critical** | S |
| 4 | Unbounded / unvalidated pagination; comments have none at all | **High** | S |
| 5 | Auth middleware registered twice — doubles JWT parse + user SELECT | **High** | S |
| 6 | `GetArticleUserModel` does a write (`FirstOrCreate`) on read paths, repeatedly | **High** | M |
| 7 | Soft-deleted favorites/follows accumulate forever and are re-inserted | **High** | M |
| 8 | Pointless read transaction in `FindManyArticle`, with a nested call on a *different* connection | **Medium** | S |
| 9 | Feed materializes the entire follow graph into two chained `IN (...)` lists | **Medium** | M |
| 10 | No request body limit; size validation happens after full parse | **Medium** | S |
| 11 | `Updates()` with an association-laden struct triggers cascading writes | **Medium** | S |
| 12 | No HTTP server timeouts; `gin.Default()` + debug mode in production | **Medium** | S |
| 13 | Allocation hygiene, `PrepareStmt`, unbounded `/tags` | **Low** | S |
| 14 | No profiling, slow-query logging, or metrics | **Low** | S |

---

## 1. N+1 query on `isFollowing` in every profile serialization — **Critical**

**Where:** `users/serializers.go:35` → `users/models.go:121-129`, reached from `articles/serializers.go:103` (and `:79`, `:168`).

Every article and every comment embeds an author profile, and building that profile issues its own database round trip:

```go
// users/serializers.go:24-38
func (self *ProfileSerializer) Response() ProfileResponse {
	myUserModel := self.C.MustGet("my_user_model").(UserModel)
	...
	Following: myUserModel.isFollowing(self.UserModel),   // <-- one SELECT, per profile
}
```

`ArticlesSerializer.Response()` (`articles/serializers.go:134-139`) loops over the page and calls `ResponseWithPreloaded`, which at line 103 calls `authorSerializer.Response()` → `ProfileSerializer.Response()` → `isFollowing`. The favorites N+1 was batched out; this one was not. Same story in `CommentsSerializer.Response()` (`articles/serializers.go:173-180`), which is worse because comments are unpaginated (finding 4).

**Why it's the top item:** at `limit=20` this is 20 extra queries, and because `follow_models` has no index (finding 2) each one is a full table scan. The cost is `O(page_size × total_follow_rows)` per request. On a 100k-row follow table that is 2M row examinations to render one page of articles.

It also fires for **anonymous** traffic. `AuthMiddleware(false)` stores a zero-value `UserModel`, so unauthenticated list requests still run 20 × `WHERE following_id = ? AND followed_by_id = 0` — pure waste on your highest-volume endpoint.

**Fix.** Mirror the batching pattern already used for favorites. Add to `users/models.go`:

```go
// BatchGetFollowingStatus returns the set of user IDs that followerID follows,
// restricted to candidateIDs.
func BatchGetFollowingStatus(candidateIDs []uint, followerID uint) map[uint]bool {
	out := make(map[uint]bool, len(candidateIDs))
	if len(candidateIDs) == 0 || followerID == 0 {
		return out
	}
	var rows []FollowModel
	common.GetDB().
		Where("followed_by_id = ? AND following_id IN ?", followerID, candidateIDs).
		Find(&rows)
	for _, r := range rows {
		out[r.FollowingID] = true
	}
	return out
}
```

Then give `ProfileSerializer` a preloaded variant (`ResponseWithFollowing(following bool)`) exactly as `ArticleSerializer` has `ResponseWithPreloaded`, thread the map down from `ArticlesSerializer.Response()` and `CommentsSerializer.Response()`, and early-return `Following: false` whenever `myUserModel.ID == 0`.

Guard the zero-user case inside `isFollowing` too, so the single-object paths (`ArticleRetrieve`, `ProfileRetrieve`) stop querying for anonymous users:

```go
func (u UserModel) isFollowing(v UserModel) bool {
	if u.ID == 0 || v.ID == 0 {
		return false
	}
	...
}
```

**Expected result:** 20 scans → 1 indexed query per list response.

---

## 2. No indexes on any foreign key or lookup column — **Critical**

**Where:** `users/models.go:16-42`, `articles/models.go:11-52`. Confirmed by grep: the only index tags in the entire repo are `UserModel.Email`, `ArticleModel.Slug`, and `TagModel.Tag`. Everything else relies on the primary key and GORM's implicit `deleted_at` index.

Columns that are queried but unindexed:

| Table | Column(s) | Query site |
|---|---|---|
| `user_models` | `username` | `FindOneUser(&UserModel{Username:…})` — `users/routers.go:34,45,62`; `articles/models.go:215,237` |
| `follow_models` | `(followed_by_id, following_id)` | `isFollowing` `users/models.go:124`; `GetFollowings` `:147`; `unFollowing` `:136` |
| `favorite_models` | `(favorite_by_id, favorite_id)` | `isFavoriteBy` `articles/models.go:79`; `unFavoriteBy` `:140`; `BatchGetFavoriteStatus` `:119` |
| `favorite_models` | `favorite_id` | `favoritesCount` `articles/models.go:70`; `BatchGetFavoriteCounts` `:100` |
| `article_models` | `author_id` | feed `articles/models.go:300,302`; author filter via association |
| `article_models` | `updated_at` | `Order("updated_at desc")` at `:210,232,252,302` |
| `article_user_models` | `user_model_id` | `GetArticleUserModel` `:60`; feed `:291` |
| `comment_models` | `article_id` | `getComments` `:166` |
| `comment_models` | `author_id` | ownership check `articles/routers.go:215` |

The `username` one deserves calling out separately: it is the lookup key for `GET /profiles/:username`, follow/unfollow, and both the `?author=` and `?favorited=` article filters, and it is a full scan of the user table every time. It should also almost certainly be `uniqueIndex` for correctness — nothing currently stops two users sharing a username.

**Fix.** Add struct tags and re-run `AutoMigrate` (SQLite creates indexes online; on a large table do it in a maintenance window):

```go
// users/models.go
Username string `gorm:"column:username;uniqueIndex"`

type FollowModel struct {
	gorm.Model
	Following    UserModel
	FollowingID  uint `gorm:"index:idx_follow_pair,priority:2;index"`
	FollowedBy   UserModel
	FollowedByID uint `gorm:"index:idx_follow_pair,priority:1"`
}
```

```go
// articles/models.go
AuthorID uint `gorm:"index"`                                   // ArticleModel
UpdatedAt — covered by: add `gorm:"index"` on a shadowing field or create
            idx_article_updated_at manually (see note below)

type FavoriteModel struct {
	gorm.Model
	Favorite     ArticleModel
	FavoriteID   uint `gorm:"index;index:idx_fav_pair,priority:2"`
	FavoriteBy   ArticleUserModel
	FavoriteByID uint `gorm:"index:idx_fav_pair,priority:1"`
}

UserModelID uint `gorm:"index"`   // ArticleUserModel — make it uniqueIndex, it is 1:1
ArticleID   uint `gorm:"index"`   // CommentModel
AuthorID    uint `gorm:"index"`   // CommentModel
```

Ordering the composite indexes with the *selective* column first (`followed_by_id`, `favorite_by_id`) matters — those match the equality predicate in the hot batch queries.

Because `UpdatedAt` comes from the embedded `gorm.Model` and can't be tagged in place, add it alongside the migration in `hello.go`:

```go
db.Exec(`CREATE INDEX IF NOT EXISTS idx_article_author_updated
         ON article_models(author_id, updated_at DESC) WHERE deleted_at IS NULL`)
```

That covering index serves both the feed's `author_id IN (…) ORDER BY updated_at DESC` and the global list's ordering.

**One more, easy to miss:** the `article_tags` join table generated by `many2many:article_tags` gets a composite primary key `(article_model_id, tag_model_id)`. The `?tag=` filter (`articles/models.go:199`) queries by `tag_model_id`, which is *not* the leftmost column — so tag filtering scans the whole join table. Add the reverse index:

```go
db.Exec(`CREATE INDEX IF NOT EXISTS idx_article_tags_tag ON article_tags(tag_model_id)`)
```

---

## 3. SQLite opened at defaults — **Critical**

**Where:** `common/database.go:49-69`.

```go
db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})
...
sqlDB.SetMaxIdleConns(10)
```

Three distinct problems:

**(a) Rollback journal, not WAL.** Default journal mode means a writer blocks *all* readers and vice versa. Given finding 6 — reads perform writes via `FirstOrCreate` — almost every request in this app is a potential writer. Read concurrency is effectively serialized.

**(b) No `busy_timeout`.** Without it, any lock contention returns `SQLITE_BUSY` **immediately** rather than waiting. Under concurrent load this surfaces as sporadic `database is locked` 422s from `common.NewError("database", err)` — a hard failure, not just slowness.

**(c) Pool is misconfigured for SQLite.** `SetMaxIdleConns(10)` is set but `SetMaxOpenConns` is not, so the pool is unbounded. Every extra connection is another contender for the same file lock; more connections make throughput *worse*, not better. `SetConnMaxLifetime` is also unset.

**Fix:**

```go
dsn := dbPath + "?_journal_mode=WAL&_busy_timeout=5000&_synchronous=NORMAL&_foreign_keys=on&_txlock=immediate"
db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{
    Logger:      logger.Default.LogMode(logger.Warn),
    PrepareStmt: true,
})
if err != nil {
    log.Fatalf("db open: %v", err)   // see note on error handling below
}
sqlDB, err := db.DB()
...
sqlDB.SetMaxOpenConns(1)     // WAL allows concurrent readers; writes must serialize anyway.
sqlDB.SetMaxIdleConns(1)     // Prefer: separate read pool (N conns) + write pool (1 conn).
sqlDB.SetConnMaxLifetime(0)
```

WAL alone typically gives the largest single throughput win available in this codebase for mixed read/write traffic.

The cleaner shape, if you want read parallelism, is two `*gorm.DB` handles over the same file: a reader pool with `SetMaxOpenConns(runtime.NumCPU())` and a writer handle pinned to `SetMaxOpenConns(1)`. That requires threading a read/write distinction through `common.GetDB()`, so treat it as a follow-up.

**Separate but related:** `Init()` at `common/database.go:57-60` prints the error and returns a `nil`-ish DB anyway. Every subsequent `common.GetDB()` call then nil-panics per request. Fail fast with `log.Fatalf`.

---

## 4. Unbounded and unvalidated pagination — **High**

**Where:** `articles/models.go:182-190` and `:271-278` (duplicated verbatim), plus `articles/routers.go:228-242`.

```go
limit_int, errLimit := strconv.Atoi(limit)
if errLimit != nil {
	limit_int = 20
}
```

Only *unparseable* input falls back to 20. A parseable but hostile value sails straight through:

- `?limit=1000000` → the server loads a million `ArticleModel` rows (each with a 2048-byte body and 2048-byte description), preloads authors and tags for all of them, then serializes the lot to JSON. That is multi-gigabyte heap in a single request. One such request from one client is enough to OOM the process.
- `?limit=-1` is worse: GORM treats a negative `Limit` as *no limit*, returning the entire table regardless of size.
- `?offset=500000` → SQLite still walks and discards 500k rows. Deep offsets degrade linearly; there's no keyset pagination.

`GET /articles/:slug/comments` (`articles/routers.go:228`) accepts no pagination parameters **at all** — `getComments()` (`articles/models.go:164-168`) returns every comment ever posted on the article, and then finding 1 issues a follow query for each one.

**Fix.** Extract one shared helper and use it in all three places:

```go
// common/pagination.go
const (
	DefaultLimit = 20
	MaxLimit     = 100
)

func ParsePagination(limitStr, offsetStr string) (limit, offset int) {
	limit = DefaultLimit
	if v, err := strconv.Atoi(limitStr); err == nil && v > 0 {
		limit = min(v, MaxLimit)
	}
	if v, err := strconv.Atoi(offsetStr); err == nil && v > 0 {
		offset = v
	}
	return limit, offset
}
```

Add `?limit`/`?offset` to `ArticleCommentList` and to `getComments()`. Cap offset (e.g. reject beyond 10,000 with a 400) or move to keyset pagination (`WHERE updated_at < ? ORDER BY updated_at DESC LIMIT ?`) once index from finding 2 is in place.

This is also the cheapest DoS surface in the codebase — fix it alongside finding 10.

---

## 5. Auth middleware registered twice — **High**

**Where:** `hello.go:41-52`.

```go
v1 := r.Group("/api")
users.UsersRegister(v1.Group("/users"))
v1.Use(users.AuthMiddleware(false))          // appended to v1's chain
articles.ArticlesAnonymousRegister(...)
...
v1.Use(users.AuthMiddleware(true))           // appended again
users.UserRegister(v1.Group("/user"))
users.ProfileRegister(v1.Group("/profiles"))
articles.ArticlesRegister(v1.Group("/articles"))   // inherits BOTH
```

`RouterGroup.Use` mutates the group's handler slice, and groups created afterwards inherit the accumulated chain. So every authenticated route — article create/update/delete, favorite/unfavorite, comments, feed, `/user`, follow/unfollow — runs `AuthMiddleware` **twice**.

Each run does (`users/middlewares.go:44-74`):
1. `UpdateContextUserModel(c, 0)` — resets context,
2. `extractToken` + `jwt.Parse` — HMAC verify and full claim decode,
3. `UpdateContextUserModel(c, id)` — **`db.First(&myUserModel, id)`**, a real database round trip.

So two JWT verifications and **two identical user SELECTs** on every authenticated request, for zero benefit. On `POST /articles/:slug/favorite` that's 2 of roughly 10 queries — 20% waste before any other fix.

**Fix.** Don't chain `Use` on the shared group. Create explicit subgroups:

```go
v1 := r.Group("/api")
users.UsersRegister(v1.Group("/users"))

optional := v1.Group("")
optional.Use(users.AuthMiddleware(false))
articles.ArticlesAnonymousRegister(optional.Group("/articles"))
articles.TagsAnonymousRegister(optional.Group("/tags"))
users.ProfileRetrieveRegister(optional.Group("/profiles"))

required := v1.Group("")
required.Use(users.AuthMiddleware(true))
users.UserRegister(required.Group("/user"))
users.ProfileRegister(required.Group("/profiles"))
articles.ArticlesRegister(required.Group("/articles"))
```

Guard against regressions with a test asserting `len(route.HandlerFunc chain)` or, more simply, a counter in the middleware asserted once per request in an integration test.

**While you're here:** the user row is fetched by primary key on every single request. Once the above is fixed it's one query, which is acceptable — but if you later want to remove it, put the `username`/`bio`/`image` fields the serializers actually need into the JWT claims, or add a small TTL cache (`golang.org/x/sync/singleflight` + an LRU) keyed by user ID with invalidation on `UserUpdate`.

---

## 6. `GetArticleUserModel` performs a write on read paths, repeatedly — **High**

**Where:** `articles/models.go:54-65`, called from 9 sites.

```go
func GetArticleUserModel(userModel users.UserModel) ArticleUserModel {
	var articleUserModel ArticleUserModel
	if userModel.ID == 0 { return articleUserModel }
	db := common.GetDB()
	db.Where(&ArticleUserModel{UserModelID: userModel.ID}).FirstOrCreate(&articleUserModel)
	...
}
```

Two problems.

**(a) It's a write on GET.** `FirstOrCreate` issues a `SELECT`, and an `INSERT` when the row is absent. That means `GET /api/articles` can take a write lock. Under the default journal mode (finding 3) this blocks every reader. It is also the reason concurrent first-requests from the same new user can race and create duplicate `article_user_models` rows — there's no unique constraint on `user_model_id` (see finding 2's recommendation to make it `uniqueIndex`).

**(b) It's called several times per request.** `ArticleUpdate` (`articles/routers.go:108`) calls it for the ownership check, then `serializer.Response()` at `:126` → `ArticleSerializer.Response()` (`articles/serializers.go:80`) calls it again. Same double-call in `ArticleDelete`, `ArticleFavorite`, `ArticleUnfavorite`, `ArticleCommentDelete`. And `ArticleModelValidator.Bind` (`articles/validators.go:46`) calls it a third time on create paths.

**Fix.**

1. Add `uniqueIndex` on `ArticleUserModel.UserModelID` and change `FirstOrCreate` to a `clause.OnConflict{DoNothing: true}` upsert, so the race is resolved by the database rather than by luck.
2. Resolve it **once per request, in the auth middleware**, and stash it in the Gin context next to `my_user_model`:

```go
// users/middlewares.go — or an articles-package middleware to avoid the import cycle
c.Set("my_article_user_model", articleUserModel)
```

Then every call site becomes a context read. This removes 1–3 queries (and a possible write) from every authenticated request.

3. Long term: `ArticleUserModel` is a 1:1 shadow of `UserModel` that exists only to satisfy GORM association wiring. Collapsing it into `UserModel` would delete a whole table, an index, a join, and this entire class of bug. That's a schema migration, so scope it separately — but it's the right end state.

---

## 7. Soft-deleted favorites and follows accumulate forever — **High**

**Where:** `articles/models.go:128-142`; `users/models.go:108-138`.

`FavoriteModel` and `FollowModel` both embed `gorm.Model`, so `Delete` is a soft delete:

```go
func (article ArticleModel) unFavoriteBy(user ArticleUserModel) error {
	return common.GetDB().
		Where("favorite_id = ? AND favorite_by_id = ?", article.ID, user.ID).
		Delete(&FavoriteModel{}).Error      // UPDATE ... SET deleted_at = now()
}
```

And re-favoriting uses `FirstOrCreate` (`:131`), whose implicit `deleted_at IS NULL` scope will **not** see the tombstoned row — so it inserts a fresh one.

A user who toggles favorite 100 times leaves 100 dead rows. Identical behaviour for follow/unfollow (`users/models.go:111` vs `:136`). These tables are precisely the ones on the hot path for findings 1 and 2: `favoritesCount`, `BatchGetFavoriteCounts`, `isFollowing`, `GetFollowings`. Toggle traffic is cheap for an attacker to generate, and the table grows without bound. Query cost grows with it even after indexing, because the index itself bloats with unreachable entries.

Same class of problem, different table: `DeleteArticleModel` (`articles/models.go:358-362`) soft-deletes the article and leaves its comments, favorites, and `article_tags` join rows fully live. `favoritesCount` will keep counting favorites of deleted articles.

**Fix.**

- These are pure join/edge tables with no audit value. Drop `gorm.Model` in favour of an explicit composite primary key and use **hard** deletes:

```go
type FavoriteModel struct {
	FavoriteID   uint      `gorm:"primaryKey"`
	FavoriteByID uint      `gorm:"primaryKey;index"`
	CreatedAt    time.Time
}
```

The composite PK gives you the index from finding 2 for free *and* makes duplicate favorites structurally impossible.

- If soft delete must stay, use `Unscoped().Delete(...)` on these two paths, or make the re-create an upsert that revives the tombstone (`Unscoped().Assign(FavoriteModel{DeletedAt: gorm.DeletedAt{}}).FirstOrCreate(...)`).
- Add a cascade on article deletion (hard-delete the article's comments, favorites, and tag links in one transaction), plus a one-off cleanup migration for rows already orphaned.

---

## 8. Pointless read transaction in `FindManyArticle`, spanning two connections — **Medium**

**Where:** `articles/models.go:177-264`.

```go
tx := db.Begin()
...
articleUserModel := GetArticleUserModel(userModel)   // line 216 and 238 — uses common.GetDB(), NOT tx
...
err := tx.Commit().Error
```

Two issues:

**(a) The transaction buys nothing.** Every branch is read-only. `BEGIN`/`COMMIT` on each list request adds round trips and, on SQLite, extends how long a lock is held. Several early-return paths (`tagModel.ID == 0`, `articleUserModel.ID == 0`) fall through to `Commit` on an empty transaction; the error paths at `:200` and `:223` do roll back, but there's no `defer` guarding a panic.

**(b) The nested call escapes the transaction.** `GetArticleUserModel` at lines 216 and 238 calls `common.GetDB()` — the *global* handle — while `tx` is open. That's a second pooled connection. And per finding 6, `GetArticleUserModel` can `INSERT`. So you have connection A holding an open transaction on the database file while connection B attempts a write to it. With `SetMaxOpenConns` unbounded and no `busy_timeout`, that's a live deadlock/`SQLITE_BUSY` risk on the `?author=` and `?favorited=` list endpoints.

**Fix.** Drop the transaction entirely — or, if you want the count and the page to be consistent, use `db.Transaction(func(tx *gorm.DB) error { ... })` and pass `tx` to *every* call inside, including a `GetArticleUserModel(tx, userModel)` that takes the handle as a parameter. The latter is the better habit; threading `*gorm.DB` through the model layer instead of reaching for a package global would prevent this whole class of mistake.

**Also in this function**, the tag branch (`:193-212`) runs three queries where one would do — an association `Find` to get IDs, an association `Count`, then a second `Find` by those IDs. Worse, `Offset`/`Limit` are applied to the *first* query, which has no `ORDER BY`, and the `Order("updated_at desc")` is applied only to the *second*. So the page boundaries are chosen in arbitrary order and only sorted within the page: pagination returns wrong results, not just slow ones. Replace the whole branch with a single join:

```go
tx.Preload("Author.UserModel").Preload("Tags").
   Joins("JOIN article_tags ON article_tags.article_model_id = article_models.id").
   Joins("JOIN tag_models ON tag_models.id = article_tags.tag_model_id").
   Where("tag_models.tag = ?", tag).
   Order("article_models.updated_at desc").
   Offset(offset).Limit(limit).
   Find(&models)
```

The `?author=` branch (`:213-234`) has the identical ordering bug and the identical fix (join through `article_user_models`).

---

## 9. Feed materializes the entire follow graph into chained `IN (...)` lists — **Medium**

**Where:** `articles/models.go:266-308`, with `users/models.go:143-154`.

```go
followings := self.UserModel.GetFollowings()          // ALL follows, no limit, preloads each UserModel
...
tx.Where("user_model_id IN ?", followingUserIDs).Find(&articleUserModels)
...
tx.Where("author_id IN ?", authorIDs)...
```

`GetFollowings` loads every follow row with `Preload("Following")` — hydrating a full `UserModel` per followed user, of which only `.ID` is ever read. Those IDs then become an `IN (...)` list, whose results become a *second* `IN (...)` list.

Consequences for a user following 5,000 accounts: ~5,000 `UserModel` structs allocated and discarded per feed request; a 5,000-parameter SQL statement; then another. SQLite's `SQLITE_MAX_VARIABLE_NUMBER` is 32,766 on modern builds but **999** on older ones — on those the query simply errors out, surfacing as a 404 "Invalid param". And it's all thrown away after building the ID slice.

**Fix.** Never leave the database. One subquery replaces the whole dance:

```go
authorIDs := db.Model(&ArticleUserModel{}).
	Select("article_user_models.id").
	Joins("JOIN follow_models ON follow_models.following_id = article_user_models.user_model_id").
	Where("follow_models.followed_by_id = ? AND follow_models.deleted_at IS NULL", self.UserModelID)

db.Model(&ArticleModel{}).Where("author_id IN (?)", authorIDs).Count(&count64)
db.Preload("Author.UserModel").Preload("Tags").
	Where("author_id IN (?)", authorIDs).
	Order("updated_at desc").Offset(offset).Limit(limit).
	Find(&models)
```

With the `(author_id, updated_at)` index from finding 2 this is two indexed statements with constant memory, independent of follow count.

Separately, `GetFollowings` should drop `Preload("Following")` and `Select("following_id")` when callers only need IDs — it's also used by nothing else, so it can simply be deleted once the feed stops calling it.

---

## 10. No request body size limit; validation runs after full parse — **Medium**

**Where:** `common/utils.go:96-99`, and the `binding:"max=2048"` tags in `articles/validators.go:13-14` and `users/validators.go:14-18`.

```go
func Bind(c *gin.Context, obj interface{}) error {
	b := binding.Default(c.Request.Method, c.ContentType())
	return c.ShouldBindWith(obj, b)
}
```

`ShouldBindWith` reads and JSON-decodes the **entire** request body into Go structs before the validator ever looks at `max=2048`. A client posting a 500 MB article body gets 500 MB read into memory, decoded into a Go string (another 500 MB), and only then rejected with a 422. A handful of concurrent such requests OOMs the process. The `max=2048` tags provide no protection against this — they're a data-integrity check, not a resource control.

**Fix.** Cap the body before it's read, in `common.Bind`:

```go
const MaxBodyBytes = 1 << 20 // 1 MiB — generous for a 2 KiB article body

func Bind(c *gin.Context, obj interface{}) error {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, MaxBodyBytes)
	b := binding.Default(c.Request.Method, c.ContentType())
	return c.ShouldBindWith(obj, b)
}
```

Better still, apply `http.MaxBytesReader` as global middleware in `hello.go` so routes that don't go through `common.Bind` are covered too.

**Related CPU note:** `bcrypt.DefaultCost` (cost 10, ~60–100 ms of CPU) runs on `POST /users/login` (`users/models.go:71-75`) and registration, with no rate limiting anywhere in the codebase. ~10 concurrent login attempts saturate a core. The cost factor is correct and should not be lowered — add per-IP rate limiting on `/users/login` and `/users` instead.

---

## 11. `Updates()` with an association-laden struct triggers cascading writes — **Medium**

**Where:** `articles/routers.go:114-124` → `articles/models.go:352-356`.

```go
articleModelValidator := NewArticleModelValidatorFillWith(articleModel)
articleModelValidator.Bind(c)          // validators.go:46 sets .Author = GetArticleUserModel(...)
                                       // validators.go:47 sets .Tags via setTags()
articleModelValidator.articleModel.ID = articleModel.ID
articleModel.Update(articleModelValidator.articleModel)   // db.Model(model).Updates(data)
```

The struct passed to `Updates` carries a fully-populated `Author ArticleUserModel` (which itself carries a nested `users.UserModel`) and a `Tags []TagModel` slice. GORM's default `FullSaveAssociations=false` still **upserts** associations on save — so a single article title edit can fire additional writes against `article_user_models`, `user_models`, `tag_models`, and `article_tags`. Extra write locks on SQLite, and a latent data-integrity risk (the nested `UserModel` upsert can clobber user fields with whatever the article validator happened to hold).

**Fix.** Update only scalar columns explicitly:

```go
func (model *ArticleModel) Update(data ArticleModel) error {
	return common.GetDB().Model(model).
		Select("slug", "title", "description", "body").
		Updates(data).Error
}
```

and manage the tag associations deliberately via `db.Model(&article).Association("Tags").Replace(tags)` inside a transaction. Add `Omit(clause.Associations)` as a belt-and-braces guard.

**Also:** `setTags` (`articles/models.go:310-350`) batches the read but inserts new tags one at a time in a loop, each its own implicit transaction, each taking a write lock. Replace with a single batched upsert:

```go
db.Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "tag"}}, DoNothing: true}).
   Create(&newTags)
```
then re-select the full set. And note `articles/validators.go:47` discards `setTags`' error entirely — tag failures pass silently.

---

## 12. No HTTP server timeouts; `gin.Default()` with debug logging — **Medium**

**Where:** `hello.go:35` and `hello.go:67`.

```go
r := gin.Default()
...
r.Run(":" + port)
```

`r.Run` constructs an `http.Server` with **zero** timeouts — no `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, or `ReadHeaderTimeout`. A slowloris client (or just a flaky mobile connection) holds a goroutine, a socket, and potentially a database connection open indefinitely. With enough of them the process exhausts file descriptors and goroutine stacks. There's also no graceful shutdown, so in-flight requests are severed on deploy.

`gin.Default()` installs `Logger()` (a synchronous, unbuffered write to stdout per request) and `Recovery()`. Keep `Recovery`; the logger is a measurable per-request cost at high RPS, and in `debug` mode — the default, per `readme.md:92`, since nothing sets `GIN_MODE` — Gin additionally prints route debug output and warnings on every request path.

**Fix:**

```go
gin.SetMode(gin.ReleaseMode)   // or require GIN_MODE=release in deployment
r := gin.New()
r.Use(gin.Recovery())
// structured/sampled logging instead of gin.Logger(), or drop it behind a flag

srv := &http.Server{
	Addr:              ":" + port,
	Handler:           r,
	ReadHeaderTimeout: 5 * time.Second,
	ReadTimeout:       15 * time.Second,
	WriteTimeout:      30 * time.Second,
	IdleTimeout:       60 * time.Second,
	MaxHeaderBytes:    1 << 16,
}
go func() {
	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}()
// ... signal.NotifyContext + srv.Shutdown(ctx) for graceful drain
```

---

## 13. Allocation hygiene, prepared statements, unbounded `/tags` — **Low**

Small items, worth batching into one cleanup PR:

**Prepared statements.** GORM's `PrepareStmt` defaults to `false`, so every query is parsed and planned from scratch. With the cgo SQLite driver that's a real per-query cost. Set `PrepareStmt: true` in `gorm.Config` (see finding 3's snippet). Measure — it typically buys 10–20% on query-heavy endpoints.

**Slice preallocation.** Sizes are known up front in several hot loops but `append` starts from nil and regrows:
- `articles/serializers.go:123-126` — `articleIDs` → `make([]uint, 0, len(s.Articles))`
- `articles/serializers.go:117` — `response` → `make([]ArticleResponse, 0, len(s.Articles))`
- `articles/serializers.go:174` — same for `CommentsSerializer`
- `articles/serializers.go:25` and `:83`/`:107` — tag slices
- `articles/models.go:208, 230, 250, 287, 294` — ID slices in `FindManyArticle`/`GetArticleFeed`
- `users/models.go:146` — `followings`

**Per-article sorting.** `sort.Strings(response.Tags)` runs inside the per-article loop (`articles/serializers.go:88`, `:112`). Correct, but if tag ordering is what you want, sort once in SQL — add `Order("tag")` to the `Tags` preload: `Preload("Tags", func(db *gorm.DB) *gorm.DB { return db.Order("tag") })`.

**Serializer copies.** `ArticleSerializer{C: s.C, ArticleModel: article}` (`articles/serializers.go:135`) copies the whole `ArticleModel` by value — including the `Body` and `Description` strings' headers, the `Tags` slice header, and the nested `ArticleUserModel`. Using `&s.Articles[i]` and a pointer-embedded serializer avoids the copy. Minor at `limit=20`, less minor once finding 4 caps it at 100.

**`GET /tags` is unbounded and uncached.** `getAllTags` (`articles/models.go:170-175`) does `SELECT * FROM tag_models` with no limit. Tags only grow, and every article creation with a novel tag adds one. This endpoint is typically called on every page load of the client. Add a limit + ordering (`ORDER BY` popularity, `LIMIT 50`) and a short in-process TTL cache or an HTTP `Cache-Control: public, max-age=300` header — the data changes slowly and doesn't need to be live.

**Redundant preloads.** `FindOneArticle` (`articles/models.go:150-155`) always preloads `Author.UserModel` and `Tags`. `ArticleDelete`, `ArticleFavorite`, and `ArticleUnfavorite` only need `ID` and `AuthorID`. Add a lightweight `FindOneArticleLite` for those paths — saves 2 queries each.

---

## 14. No profiling, slow-query logging, or metrics — **Low (but do it first)**

There is no `net/http/pprof`, no request-duration histogram, no slow-query threshold, and the production logger is at default level (`common/database.go:57` passes an empty `gorm.Config{}`; only the *test* DB at `:81` enables `logger.Info`). Right now nobody can tell which of the findings above is actually hurting them in production — this review is static analysis, and static analysis guesses at ratios.

**Fix — do this before the optimization work so you can measure it:**

```go
// common/database.go
gorm.Open(sqlite.Open(dsn), &gorm.Config{
	Logger: logger.New(log.New(os.Stdout, "", log.LstdFlags), logger.Config{
		SlowThreshold: 100 * time.Millisecond,
		LogLevel:      logger.Warn,
	}),
})
```

Add `pprof` behind an internal-only listener, and a middleware exporting request count/duration/status by route. Then re-run your load profile and confirm the ordering above matches reality for your traffic mix.

---

## Suggested sequencing

**PR 1 — measurement + config (hours, near-zero risk).**
Findings 3, 12, 14, and `PrepareStmt` from 13. WAL + busy timeout + pool sizing + server timeouts + slow-query logging. This alone will likely produce the largest visible improvement, and it gives you the instrumentation to validate everything after.

**PR 2 — indexes (hours, low risk).**
Finding 2 in full, including the two raw `CREATE INDEX` statements. Verify with `EXPLAIN QUERY PLAN` on the five hot queries that you no longer see `SCAN TABLE`.

**PR 3 — safety limits (hours, low risk).**
Findings 4 and 10. Caps pagination, adds comment pagination, bounds request bodies. Closes the two easiest resource-exhaustion vectors.

**PR 4 — query-count reduction (days).**
Findings 1, 5, and 6 together: batch the follow lookups, de-duplicate the auth middleware, resolve `ArticleUserModel` once per request in context. Expect list endpoints to drop from ~30 statements to under 10.

**PR 5 — data model and query rewrites (days).**
Findings 7, 8, 9, 11. Hard-delete the join tables, remove the read transactions and the ordering bugs, replace the feed's `IN (...)` chains with subqueries, tighten `Updates`. These touch schema and semantics — land them with tests.

**Backlog.** Collapse `ArticleUserModel` into `UserModel` (finding 6, item 3). Keyset pagination for deep offsets (finding 4). Separate reader/writer connection pools (finding 3). Rate limiting on the bcrypt endpoints (finding 10). Finding 13's remaining cleanup.

---

## What's already good

Worth stating so it doesn't get regressed in the refactor:

- `BatchGetFavoriteCounts` / `BatchGetFavoriteStatus` (`articles/models.go:87-126`) are exactly the right pattern, and `ResponseWithPreloaded` is the right way to thread the results through. Finding 1 should be implemented as a copy of this design, not something new.
- `setTags` (`:310-350`) correctly batches the existence check and handles the concurrent-insert race with a fallback re-select.
- Foreign-key predicates are written as parameterized `IN ?` / `= ?` clauses throughout — no string-concatenated SQL anywhere in the repo.
- `RedirectTrailingSlash = false` with both `""` and `"/"` routes registered avoids a redirect round trip per request.
- `extractToken` (`users/middlewares.go:13-27`) is allocation-free on the header path (slicing, not `strings.Split`).
