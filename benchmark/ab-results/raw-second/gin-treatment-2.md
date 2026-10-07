# Performance Review: golang-gin-realworld-example-app

**Date:** 2026-10-07
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec backend-performance-review/2.0)
**Commit:** `626c372d259472148d93303f74aa9b9a1cdcef24`

---

## 1. Decision summary

### Overall assessment

This is a small, single-process Gin + GORM service backed by one embedded SQLite file. Every request path goes through that one shared resource. The code already batches some list-path queries (favorite counts and status in `ArticlesSerializer`, `IN` lookups in the list and feed paths). Four structural problems remain on the anonymous, read-heavy list endpoints:

- The serializer still runs one follow-lookup query for each article and each comment (PERF-001).
- A client can set any page size, and comments and tags are never paged (PERF-002).
- The columns those per-item queries filter on have no indexes (PERF-004).
- A request filtering by author or favoriter opens a read transaction and then tries to write on a second connection. Because SQLite allows only one writer and locks the whole file, that request can block itself (PERF-003).

All of this comes from reading the code. The repository has no runtime evidence. Review confidence is **Medium**: every path was read, but nothing was measured. The workload is unknown, so the ranking of PERF-003, PERF-004 and PERF-005 would change if we learned whether SQLite is the production datastore.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-003 (P1)** | In `FindManyArticle`, resolve the author's `ArticleUserModel` *before* `db.Begin()` (or through `tx`), or drop the transaction from this read path. | An anonymous request can trigger it, and while it waits it holds the database-wide write lock. The fix is a few lines. |
| 2 | **PERF-001 + PERF-002 (P1)** | Batch the viewer→author follow lookup into one `IN` query per response. Clamp `limit` to a maximum and reject negative values. Page the comments endpoint. | Together they set how many queries one anonymous request issues, and right now the client controls that number. |
| 3 | **PERF-004 (P1)** | Run `EXPLAIN QUERY PLAN` on the follow, favorite, comment, author and username lookups. Add indexes where it shows full scans. | Each per-item query scans a table that grows with every follow or favorite and never shrinks (soft delete). |

PERF-007 (P3, quick-win) is cheap enough to sequence alongside action 1. Without it, no fix above can be validated in production.

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is SQLite the production datastore, and how many processes open the file? | PERF-003/005 severity. If production is dev-only, everything drops to P3. | Deployment owner; there is no deploy manifest in the repo |
| Effective `busy_timeout` on the driver connection | Whether PERF-003 shows up as a lock stall (Critical) or as an immediate wrong empty result (correctness only) | `PRAGMA busy_timeout;` on a connection opened by the app's driver |
| Row counts of `follow_models`, `favorite_models`, `comment_models`, `article_models` | PERF-004 severity and whether indexes are worth their write cost | `SELECT COUNT(*)` per table on a copy of the DB |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-004 | `safe-on-production` | `sqlite3 ./data/gorm.db "EXPLAIN QUERY PLAN SELECT * FROM follow_models WHERE following_id = 1 AND followed_by_id = 2 AND deleted_at IS NULL LIMIT 1;"` | `SCAN follow_models` confirms PERF-004. `SEARCH ... USING INDEX` refutes it for this table. |
| PERF-003 / busy_timeout | `safe-on-production` | `sqlite3 ./data/gorm.db "PRAGMA journal_mode;"` (also run `PRAGMA busy_timeout;` from a Go connection opened the way `common.Init` opens it) | `wal` refutes the PERF-003 mechanism. `delete` plus a non-zero timeout confirms the stall branch. |
| PERF-001 | `not-safe-on-production` | Run the test suite with the GORM Info logger that `common.TestDBInit` already sets, and count `follow_models` SELECTs per `GET /api/articles?limit=N`: `go test ./articles/ -run TestArticleListWithFilters -v 2>&1 \| grep -c follow_models` | A count that grows with N confirms it. A constant count refutes it. |

---

## 2. Scope and method

**Reviewed:** All application Go source (`hello.go`, `common/`, `users/`, `articles/`): models, routers, serializers, validators, middleware. Also `go.mod`, the CI workflow, the test/API scripts, `AGENTS.md` and `readme.md`. Unit tests were read only for counter-evidence.
**Not reviewed:** `go.sum` beyond what the detector reported. The Postman collection (`api/Conduit.postman_collection.json`), because it is a functional test with no load scenario (`api/run-api-tests.sh` sets a 500 ms inter-request delay). The frontend/mobile instruction docs. `.env.example` was noted as present and not opened, per the no-secrets rule.

**Evidence available:** Uninstrumented. There are no metrics, traces, pprof endpoints, benchmarks, load tests, SLOs or dashboards (`grep` for pprof/prometheus/otel/Benchmark found nothing). Two things exist but are not measurement artifacts: `gin.Default()` (`hello.go:35`) installs Gin's access logger, which prints per-request latency to stdout, and `gorm.Open(..., &gorm.Config{})` (`common/database.go:57`) keeps GORM's default logger. No log output was supplied.

**Ranking method:** Structural signals only. No runtime data exists, so the ranking is inference.

**Reference depth:** Go, SQLite, REST (Gin) and the relational category were loaded at `deep` tier. The detector also flagged gRPC, but that comes from an indirect `protobuf` dependency (`go.mod`: `google.golang.org/protobuf // indirect`). No gRPC service exists, so it was not analyzed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All non-test Go source files (11) read in full; tests consulted selectively |
| **Critical paths** | 9 / 9 |
| **Shared resources** | 3 / 3 (SQLite database file + its lock, the `database/sql` pool, process CPU) |
| **Technology support** | Go: deep · SQLite: deep · REST/Gin: deep · gRPC: detected, not present |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Whether production uses SQLite, and with how many processes | No evidence exists (no Dockerfile, k8s or deploy config). `AGENTS.md` describes SQLite as "for development/testing". | Deployment owner, or the deploy manifests |
| Effective SQLite `journal_mode` and `busy_timeout` | No evidence exists. The repo sets neither; effective values come from the driver and the database file. | `PRAGMA journal_mode; PRAGMA busy_timeout;` |
| Table sizes and growth | No evidence exists | Row counts per table; growth rate |
| Request rates per endpoint | No evidence exists | Access logs (the Gin logger already writes them) or an edge/LB metric |
| Whether GORM v1.25.12 emits no LIMIT for a negative `Limit()` | Behavior lives in the dependency, not this repo | Run `GET /api/articles?limit=-1` against a test DB with the GORM Info logger |

---

## 3. Architecture overview

One Go binary (`hello.go`) runs `gin.Default()` with three route groups (`/api/users`, `/api/articles` + `/api/tags`, `/api/profiles`, `/api/user`). It talks to a single SQLite file through GORM v2 (`gorm.io/driver/sqlite` → `mattn/go-sqlite3`, cgo). It makes no outbound HTTP calls, and there is no cache, broker or background job. Schema is managed by `AutoMigrate` at startup (`hello.go:15-22`).

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| HTTP framework | gin-gonic/gin | v1.10.0 (`go.mod`) | deep (REST) | All entry points |
| ORM | gorm.io/gorm | v1.25.12 | — (ORM behavior is outside the skill's scope) | All data access |
| Datastore | SQLite via mattn/go-sqlite3 | v1.14.22 driver (`go.mod`, indirect) | deep | Single primary store, file `DB_PATH` (default `./data/gorm.db`, `common/database.go:21-27`) |
| Runtime | Go | `go 1.21` directive; CI matrix 1.21–1.23 | deep | Single process |
| Auth | golang-jwt/jwt/v5 + bcrypt | v5.2.1 / x/crypto v0.32.0 | — | Middleware and login |

**Shared resources:** (1) the SQLite database file. It allows one writer at a time with a whole-file lock, and in the default rollback-journal mode, readers also block a writer's commit. (2) The `database/sql` pool from `common.Init`: only `SetMaxIdleConns(10)` is set (`common/database.go:65`), so there is no cap on open connections. (3) Process CPU, which bcrypt in login and registration uses.

---

## 4. Workload model

**Known**

- Default page size is 20 when `limit` is missing or unparseable. There is no maximum, and the value comes straight from the query string. Source: `articles/models.go:187-190`, `articles/models.go:275-278`, `articles/routers.go:59-61`, `:71-72`.
- `GET /api/articles`, `/api/articles/:slug`, `/api/articles/:slug/comments`, `/api/tags` and `/api/profiles/:username` are reachable anonymously. Source: `hello.go:43-46`.
- Comment lists and tag lists have no pagination. Source: `articles/models.go:164-168`, `:170-175`.
- Handler shape: 8 GET handlers vs 12 mutating handlers (counted from `articles/routers.go:13-36` and `users/routers.go:10-29`). By endpoint count the surface is write-heavy, but list/read endpoints are typically the busiest in this app class. That is an assumption, below.
- Follows, favorites, comments and articles are soft-deleted (`gorm.Model` embeds `DeletedAt`). Unfavorite and unfollow call `Delete` (`articles/models.go:140`, `users/models.go:136`), and re-favoriting creates a new row through `FirstOrCreate`, which ignores soft-deleted rows. No retention or vacuum job exists. These tables only grow.
- Connection pool: max idle 10, max open unset, conn lifetime unset. Source: `common/database.go:65`.
- No `PRAGMA` (journal_mode, busy_timeout, synchronous) is set anywhere. Source: `grep` across the repo, `common/database.go:57`.
- Deployment: single binary, port from `PORT` (`hello.go:63-66`). No container or orchestration config in the repo.
- `AGENTS.md` describes SQLite as "for development/testing".

**Assumed**

- The service is deployed on SQLite as committed, since it has no other driver. Affects PERF-003, PERF-004, PERF-005.
- List endpoints (`/api/articles`, `/feed`) are the highest-traffic paths. Affects PERF-001, PERF-002.
- Follow and favorite tables grow with the user base and are larger than tens of rows. Affects PERF-004.

**Unknown**

- Request rate and distribution per endpoint. Share of anonymous traffic.
- Table row counts and growth.
- Effective `busy_timeout` and `journal_mode`.
- Whether clients are untrusted (public internet) or internal.

**Derived**

- Queries per `GET /api/articles` (no filter, anonymous): 1 BEGIN/COUNT + 1 page SELECT + 2 preload SELECTs (Author.UserModel chain, Tags) + 1 batch favorite count + 1 `GetArticleUserModel` (no query when the viewer ID is 0) + **1 `isFollowing` per article**. So the total is a constant plus N, where N is the returned page size (default 20, client-controlled). From: `articles/models.go:255-259`, `articles/serializers.go:128-138`, `users/serializers.go:35`. Preload query counts are GORM behavior and only approximate. The per-article term is code-certain.
- Queries per `GET /api/articles/:slug/comments` = constant + **1 per comment**, and the comment count is unbounded. From: `articles/models.go:164-168`, `articles/serializers.go:173-180`, `users/serializers.go:35`.
- Authenticated routes registered after `v1.Use(users.AuthMiddleware(true))` run both auth middlewares, so they do 2 JWT parses + 2 user SELECTs per request. From: `hello.go:43`, `hello.go:48`, `users/middlewares.go:30-35`.

**Measured**

- None. No benchmark, trace, query plan or log was supplied. This is why no finding is `Confirmed`.

### Questions that would change the ranking

No one was available to answer, so these are recorded as unanswered and the review proceeded.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is SQLite the production datastore, and how many processes/replicas open the file? | severity, recommendation | If dev-only: PERF-003/004/005 drop to P3 and the advice becomes "fix before any production use". If production with multiple processes: PERF-005 rises to High. |
| 2 | highest | Is a specific symptom behind this review (slow list, `database is locked` errors, timeouts)? | severity, confidence | `database is locked` would raise PERF-003/005 confidence. A slow list endpoint would point first at PERF-001/004. |
| 3 | high | Approximate row counts of `follow_models`, `favorite_models`, `comment_models`, `article_models`? | severity, recommendation | Tens of rows: PERF-004 drops to Low/P3 and indexes are not worth their write cost. Thousands or more: it stays P1. |
| 4 | high | Are API clients untrusted (public internet), and does any gateway cap query parameters? | severity | A gateway clamp on `limit` would bound PERF-002 to Medium. |
| 5 | high | Peak request rate for `GET /api/articles` and `/feed`, and the anonymous share? | severity, confidence | Low single-digit rates per minute would drop PERF-001/002 to Medium. |
| 6 | medium | Largest comment count on one article; largest follow count for one user? | severity | Bounds the comment-list N+1 (PERF-001) and the feed's `IN` list size. |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` (± tag/author/favorited) | yes | read tx: COUNT + page + preloads; then batch favorite count/status + **1 follow query per article**. Author/favorited branch: a `FirstOrCreate` write on a second connection *inside* the open tx | **No**: `limit` is unclamped | access log only | PERF-001, -002, -003, -004 |
| 2 | `GET /api/articles/feed` | yes | `GetArticleUserModel` (FirstOrCreate) + `GetFollowings` (all follows, outside tx) + read tx (articles_user `IN`, COUNT, page, preloads) + per-article follow query | No (`limit` unclamped; following list unbounded) | access log only | PERF-001, -002, -004 |
| 3 | `GET /api/articles/:slug/comments` | yes | article + preloads; **all** comments + author preload; **1 follow query per comment** | **No**: no pagination | access log only | PERF-001, -002 |
| 4 | `GET /api/articles/:slug` | yes | article + preloads; FirstOrCreate (viewer); isFavoriteBy; favoritesCount; isFollowing | yes (single row) | access log only | constant ~5 queries; not material alone |
| 5 | `GET /api/tags` | yes | `SELECT * FROM tag_models` | **No** | access log only | PERF-002 |
| 6 | `GET /api/profiles/:username` | yes | user by `username` (no index) + isFollowing | yes | access log only | PERF-004 (username) |
| 7 | Auth: `POST /users`, `/users/login`, `GET/PUT /user` | yes | user lookup/save; bcrypt; JWT sign | yes | access log only | bcrypt CPU is by design (considered, not reported) |
| 8 | Article writes (create/update/delete/favorite/unfavorite) | yes | slug lookup + preload; FirstOrCreate; writes; tag batch | yes (per tag list) | access log only | double auth middleware (PERF-006) |
| 9 | Comment create/delete, follow/unfollow | yes | lookup + write | yes | access log only | double auth middleware (PERF-006) |

**Amplification points:** query count on paths 1–3 grows with page size or comment count (PERF-001). Page size is client-controlled (PERF-002). Each amplified query is a full table scan when no index exists (PERF-004). The three multiply.

**Paths deliberately not analyzed in depth, and why:** `/api/ping`, which is constant with no I/O. Startup `AutoMigrate`, which runs once per boot and is off the request path.

---

## 6. Layer analysis

### 6.1 Application

Handlers are synchronous Go functions on Gin's per-request goroutines. There is no unbounded goroutine spawning and no blocking-in-event-loop concern, because Go's scheduler parks goroutines on blocking cgo/syscalls. Repeated work: `ProfileSerializer.Response` issues a DB query on every call (`users/serializers.go:35`), and list serializers call it once per item (PERF-001). Auth middleware runs twice on authenticated routes (PERF-006).

### 6.2 API

The list endpoints accept `limit`/`offset` with no upper bound or sign check (PERF-002). The comments and tags endpoints return everything. JSON responses are built in memory by `c.JSON`, so response size grows with the same unbounded counts. Serialization cost is merged into PERF-002, not reported separately.

### 6.3 Data access and datastore

- **Repeated per-item queries:** PERF-001.
- **Missing indexes on filter/join columns:** PERF-004. The only declared indexes are `article_models.slug` (unique), `tag_models.tag` (unique), `users.email` (unique), and GORM's `deleted_at` index on `gorm.Model` tables.
- **Transaction misuse:** `FindManyArticle` and `GetArticleFeed` open explicit transactions for read-only work (`articles/models.go:192`, `:280`), and `FindManyArticle` calls a function that writes through the global handle, which is a different pooled connection (`articles/models.go:216`, `:238`). That is PERF-003.
- **Engine configuration:** no journal mode, busy timeout or open-connection cap (PERF-005).
- `COUNT(*)` runs on every list request (`articles/models.go:257`, `:300`). In SQLite an unfiltered COUNT scans the table or its smallest index. This is folded into PERF-004 as the same "per-request work proportional to table size" mechanism.

### 6.7 Observability

The repo is uninstrumented apart from Gin's stdout access log and GORM's default logger. There is no per-route latency histogram, no DB query count or duration metric, no pprof, and no benchmark. The tests do enable GORM's Info logger (`common/database.go:80-82`), which makes query counting in tests possible today (PERF-007).

---

## 7. Findings

### PERF-003 — Read transaction blocks its own write: `FindManyArticle` writes on a second connection while holding a SQLite read lock

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `articles/models.go:216` (`FindManyArticle`) |
| **Tags** | quick-win, needs-measurement |

**Problem**
In the `author` and `favorited` branches, `FindManyArticle` opens a transaction (`tx := db.Begin()`, line 192) and reads through it (line 215 / 237). It then calls `GetArticleUserModel`, which runs `FirstOrCreate` through the *global* handle `common.GetDB()` (`articles/models.go:59-62`), and that handle uses a different pooled connection. If the user has no `article_user_models` row yet, that call INSERTs. In SQLite's default rollback-journal mode, that INSERT cannot commit while the same goroutine's still-open transaction holds a SHARED lock. The request waits on itself.

**Performance principle**
Never wait inside a critical section for a resource that the critical section itself prevents from becoming available. More simply: do not hold a lock while acquiring a conflicting one on another handle.

**Evidence**
- `articles/models.go:192`: `tx := db.Begin()`. Lines 215 and 237: `tx.Where(...).First(&userModel)` reads inside the tx.
- `articles/models.go:216` and `:238`: `GetArticleUserModel(userModel)` is called while the tx is open.
- `articles/models.go:59-62`: `GetArticleUserModel` uses `common.GetDB()`, not `tx`, and calls `FirstOrCreate`.
- `common/database.go:57-66`: no `journal_mode` or `busy_timeout` is configured, so the engine defaults apply. No `SetMaxOpenConns`, so the pool opens a second connection rather than queuing.
- No runtime evidence. The mechanism is derived from SQLite's documented locking model (skill reference `technology/sqlite.md` §2) and was not reproduced.

**Impact**
- Position: critical path. `GET /api/articles?author=` and `?favorited=` are anonymous-reachable (`hello.go:43-44`).
- Frequency: per request, when the named user exists but has no `article_user_models` row. A failed INSERT creates no row, so every repeat of the same request hits the same condition again.
- Growth: O(1) per request, but the waiting writer holds RESERVED/PENDING locks on the *whole database file*. While it waits, new readers and writers across the process are refused or wait too.
- Blast radius: system-wide. Every endpoint uses the same file.

If `busy_timeout` is non-zero, each triggering request stalls the database for the full timeout, then the INSERT fails. The error is ignored (`articles/models.go:60-62` never checks `.Error`), `articleUserModel.ID` stays 0, and the response is an empty list. If `busy_timeout` is zero, the failure is immediate. The stall disappears, but the wrong-result behavior remains.

**Conditions**
SQLite in rollback-journal mode (no WAL is configured in the repo), a non-zero effective busy timeout, and requests filtering by a user who has never created, favorited or been loaded through an authenticated article endpoint. Workload unknown, so confidence is capped at Medium.

**Counter-evidence**
- Searched for a `PRAGMA journal_mode=WAL` or DSN parameters that would remove the reader-blocks-writer behavior. None found (`common/database.go:57`). Effect: no-effect.
- Searched the tests for coverage of this path. `TestFindManyArticle` (`articles/unit_test.go:207`) and the HTTP tests at `:424`/`:468` always create the `ArticleUserModel` first, and `?author=someauthor` (`:1116`) names a user who does not exist, so `GetArticleUserModel` returns early without a query. No test exercises the trigger. The tests passing therefore says nothing either way. Effect: no-effect.
- `AGENTS.md` says SQLite is for development/testing. If production never runs on SQLite, the blast radius disappears. Effect: lowers-confidence, already reflected in Medium.
- Effective busy timeout is unknown. It changes whether the symptom is a stall or a fast wrong result. Effect: lowers-confidence.

**Why this might not matter**
If the service only ever runs on SQLite in development, or the driver's effective busy timeout is zero, there is no stall. Users without an `article_user_models` row are also uncommon in a used deployment, because any authenticated article action creates one.

**Recommendation**
Remove the lock inversion. Resolve `userModel` and its `ArticleUserModel` **before** `db.Begin()`. Better still, drop the explicit transaction from this read-only path entirely: it only buys a consistent COUNT and page pair, and costs a held read lock (see PERF-005). And make `GetArticleUserModel` a pure lookup on read paths. The list endpoint has no reason to *create* a row for the author being filtered on. A missing row simply means "no articles", which already yields an empty result.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Look up (not create) the ArticleUserModel before or without the tx; drop the read tx | **preferred** | Removes both the write on a read path and the lock inversion. Fewest moving parts. |
| B: Pass `tx` into `GetArticleUserModel` | | Removes the inversion, but turns a read transaction into a write transaction on an anonymous GET |
| C: Enable WAL | | Readers then no longer block a writer's commit, which defuses the stall. The write on a read path and the ignored error remain. |
| D: Set a busy timeout of zero | | Converts the stall into a silent wrong result. Masks the problem. |

**Trade-offs**
Dropping the read transaction means `articlesCount` and the page can disagree by a row under a concurrent write. That is acceptable for a paginated list, but it is a semantic change. Option A also changes behavior for users with no row: they would no longer get one created as a side effect of someone else's list request.

**Validation**
- Baseline: a Go test using `common.TestDBInit()` creates a user via `test_db.Create` *without* calling `GetArticleUserModel`, then calls `FindManyArticle("", user.Username, "10", "0", "")` and records elapsed time and the returned count. `not-safe-on-production`.
- Expectation: before the fix, elapsed time is about the driver's busy timeout or the INSERT returns BUSY. After it, the test returns immediately.
- Falsifier: before the fix, the call returns immediately *and* an `article_user_models` row exists afterwards. That would mean the lock inversion does not occur with this driver and configuration, and the finding should be withdrawn.
- Guard: keep that test, asserting completion under a small bound and the correct empty result.

---

### PERF-001 — Per-item follow lookup in list serializers (N+1 on article lists and comment lists)

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `articles/serializers.go:138` (`ArticlesSerializer.Response`) |
| **Tags** | scalability-risk |

**Problem**
Every article in a list response is serialized with `ResponseWithPreloaded`, which calls `ArticleUserSerializer.Response` → `ProfileSerializer.Response`, and that runs `myUserModel.isFollowing(author)`: one SELECT on `follow_models` per article. The comments endpoint does the same once per comment. The favorite data on the same path was already batched (`articles/serializers.go:122-132`). The follow data was not.

**Performance principle**
Work per response should not scale with the number of items when the per-item fact can be fetched for the whole set in one round trip.

**Evidence**
- `articles/serializers.go:134-138`: loop over `s.Articles` → `serializer.ResponseWithPreloaded(...)`.
- `articles/serializers.go:94,103`: `authorSerializer.Response()`. `articles/serializers.go:38-39` → `users.ProfileSerializer.Response()`.
- `users/serializers.go:35`: `Following: myUserModel.isFollowing(self.UserModel)`. `users/models.go:121-129`: `isFollowing` issues `db.Where(FollowModel{...}).First(&follow)`.
- Comments: `articles/serializers.go:173-180` loops comments → `CommentSerializer.Response` → `authorSerializer.Response()` (line 168).
- Anonymous viewers are not exempt. With viewer ID 0, GORM drops the zero-valued `FollowedByID` from the struct condition (see COR-001), and the query still runs.
- No runtime evidence.

**Impact**
- Position: critical path (`GET /api/articles`, `/feed`, `/api/articles/:slug/comments`).
- Frequency: per item, on every list request.
- Growth: O(n) in page size (default 20, client-controlled, see PERF-002) or comment count (unbounded). Derivation: extra queries = n.
- Blast radius: service. All queries go through the one SQLite file and the shared pool.
- Amplification: Nx. Each of the n queries is itself a scan of `follow_models` if PERF-004 holds.

**Conditions**
Matters whenever list endpoints are a meaningful share of traffic and pages or comment threads are larger than a handful of items. At the default page size it is 20 extra round trips per list request. It is in-process SQLite, so each round trip is cheap, but with no index each one is a scan (PERF-004).

**Counter-evidence**
- Looked for batching of follow state alongside `BatchGetFavoriteStatus`. None exists. Effect: no-effect.
- Looked for memoization of `isFollowing` per author within a request. None, so repeated authors on a page are queried repeatedly. Effect: no-effect.
- SQLite is embedded, so per-query cost has no network RTT. This bounds the absolute cost per item but not the growth. Effect: bounds-impact, already reflected in High rather than Critical.

**Why this might not matter**
With in-process SQLite, an indexed point lookup is very cheap. At the default page of 20 and modest traffic, the whole N+1 may be a small slice of request time. Without a profile, we cannot claim it dominates.

**Recommendation**
Add a `BatchGetFollowingStatus(viewerID, authorUserIDs)` that runs one `SELECT following_id FROM follow_models WHERE followed_by_id = ? AND following_id IN ? AND deleted_at IS NULL`. Pass the resulting set into the serializer, mirroring the existing `BatchGetFavoriteStatus` pattern. Skip the query entirely when the viewer ID is 0, because anonymous viewers follow no one. Apply the same approach to `CommentsSerializer`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Batch follow status per response, skip for anonymous | **preferred** | Removes the work. One query regardless of n. Matches an existing pattern in the same file. |
| B: Per-request memo map keyed by author ID | | Helps only when authors repeat; still O(distinct authors) |
| C: Cache follow status | | Adds invalidation on every follow/unfollow for a problem that batching removes |

**Trade-offs**
The serializer API changes, because the profile serializer needs an injected "following" value. The `IN` list size is bounded by page size, so it will not hit SQLite's bound-variable limit for sane pages (another reason to fix PERF-002).

**Validation**
- Baseline: in tests (GORM Info logger already on in `common.TestDBInit`), count `follow_models` statements for `GET /api/articles?limit=N` at N=5 and N=20. `not-safe-on-production`.
- Expectation: before the fix, the count rises with N. After it, at most one per request, and zero for anonymous viewers.
- Falsifier: the count is already constant in N, meaning some layer batches it and the finding is wrong.
- Guard: a test asserting the follow-query count is independent of page size.
- Command: `go test ./articles/ -run TestArticleListWithFilters -v 2>&1 | grep -c "follow_models"`, which counts follow-table statements in test logs (`not-safe-on-production`; test DB only).

---

### PERF-002 — Client-controlled, unbounded result sizes on anonymous list endpoints

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `articles/models.go:187` (`FindManyArticle`) |
| **Tags** | quick-win |

**Problem**
`limit` is parsed with `strconv.Atoi` and used as-is: there is no upper bound and no sign check. `GET /api/articles/:slug/comments` and `GET /api/tags` have no pagination at all. One anonymous request can therefore ask for every article, with every per-item query from PERF-001, and build the whole JSON response in memory.

**Performance principle**
The cost of a request must be bounded by the server, not by the client or by table size.

**Evidence**
- `articles/models.go:187-190` (list) and `:275-278` (feed): `limit_int, errLimit := strconv.Atoi(limit)`, defaulting to 20 only on a parse error.
- `articles/models.go:259`, `:302`: `.Offset(offset_int).Limit(limit_int)` with the raw value.
- `articles/models.go:164-168`: `getComments` loads all comments for an article. `articles/models.go:170-175`: `getAllTags` loads all tags.
- `hello.go:43-45`: these routes are mounted under `AuthMiddleware(false)`, so they are anonymous.
- Not verified: whether GORM v1.25.12 omits `LIMIT` for a negative value. A large positive `limit` reaches the same outcome either way.
- No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in table size for rows, response bytes and memory. Multiplied by PERF-001's per-item query, it becomes O(n) queries per request.
- Blast radius: service. The single process's memory and the shared DB file.

**Conditions**
The API is reachable by clients that can choose `limit` (any public deployment), and the `article_models`/`comment_models`/`tag_models` tables hold more rows than a page. Today's table sizes are unknown.

**Counter-evidence**
- Looked for a gateway, middleware or validator clamping `limit`. None in the repo (`hello.go`, `articles/validators.go`). Effect: no-effect.
- Looked for a default `LIMIT` applied by GORM config. None. Effect: no-effect.
- Tag cardinality is bounded by distinct tag strings, which in practice grow much more slowly than articles. Effect: bounds-impact for the tags endpoint only.

**Why this might not matter**
If the deployment is internal and the only client is the official frontend, which sends small fixed limits, the unbounded case is never exercised.

**Recommendation**
Clamp `limit` to a server-side maximum (the value is a product decision; pick it from the frontend's page size), and treat values ≤0 as the default. Paginate `GET /api/articles/:slug/comments` with the same limit/offset pair. Leave tags unpaginated unless the row count proves large, but cap it defensively.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Server-side clamp plus pagination on comments | **preferred** | Bounds the work at its source; trivial change |
| B: Rate limiting at an edge | | Limits request count, not per-request cost |
| C: Response caching for list endpoints | | Does not bound the cold path; invalidation on every write |

**Trade-offs**
Paginating comments is an API change for clients that expect the full list. A clamp silently truncates oversized requests, so document it.

**Validation**
- Baseline: `GET /api/articles?limit=100000` against a test DB with more rows than the default page. Record row count and statement count. `not-safe-on-production`.
- Expectation: before the fix, all rows are returned. After it, at most the configured maximum.
- Falsifier: the current code already returns ≤ some maximum, meaning a clamp exists somewhere this review missed.
- Guard: a handler test asserting `len(articles) <= max` for a large `limit` and for `limit=-1`.

---

### PERF-004 — Filter and join columns used on every request have no indexes; per-item queries become per-item table scans

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `users/models.go:36` (`FollowModel`) — also `articles/models.go:23`, `:31`, `:45`; `users/models.go:18` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The columns these hot queries filter on have no index declared:

- `follow_models(following_id, followed_by_id)`: `isFollowing`, per item.
- `favorite_models(favorite_id, favorite_by_id)`: batch counts and status, `isFavoriteBy`, `favoritesCount`.
- `article_models.author_id`: feed and author filter.
- `comment_models.article_id`: comments.
- `article_user_models.user_model_id`: `GetArticleUserModel`, called on nearly every article request.
- `users.username`: profile and author/favorited filters.

Schema comes only from `AutoMigrate` on struct tags, which declare no such indexes. Each of these queries is therefore a full scan of a table that only grows (soft deletes).

**Performance principle**
Per-request lookups should cost in proportion to the result, not to the size of the table.

**Evidence**
- All `gorm:"..."` tags in non-test code (grep): the only indexes are `Slug uniqueIndex` (`articles/models.go:13`), `Tag uniqueIndex` (`:41`), `Email uniqueIndex` (`users/models.go:19`), plus `gorm.Model`'s `deleted_at` index.
- Hot filters: `users/models.go:124-127` (follow), `articles/models.go:98-102`, `:119` (favorites), `:291`, `:300-302` (author_id), `:166` (comments by article), `:60-62` (user_model_id), `users/models.go:83` with a username condition (`users/routers.go:34`, `articles/models.go:215`).
- Growth evidence: un-favorite and un-follow soft-delete (`articles/models.go:140`, `users/models.go:136`), and re-favoriting inserts a new row. No retention or VACUUM exists anywhere.
- Unfiltered `COUNT(*)` on `article_models` per list request (`articles/models.go:257`).
- No runtime evidence. No query plan was available, and whether GORM's SQLite migrator adds any implicit index for these FK columns was not verified in this review. Hence Medium.

**Impact**
- Position: critical path.
- Frequency: per request, and per item for the follow lookup.
- Growth: O(table) per lookup. Combined with PERF-001: O(page × follow_models rows) per list request.
- Blast radius: service (single shared DB file; scans hold SHARED locks longer, delaying writers in rollback mode).

**Conditions**
Tables beyond trivial size. With tens of rows, a scan costs about the same as an index probe, and this finding is noise. Row counts are unknown, so confidence is capped at Medium.

**Counter-evidence**
- Looked for migrations or SQL files creating indexes. There are none; schema is `AutoMigrate` only (`hello.go:15-22`). Effect: no-effect.
- `deleted_at` is indexed on every table, but it is low-selectivity (mostly NULL) and does not serve the equality filters. Effect: no-effect.
- Did not verify GORM migrator behavior for FK columns at runtime. Effect: lowers-confidence (reflected).

**Why this might not matter**
A RealWorld demo database may hold tens to hundreds of rows, where SQLite scans are effectively free and every index is pure write overhead on a single-writer engine.

**Recommendation**
First run `EXPLAIN QUERY PLAN` for each listed query on a representative copy. Then add composite indexes only where the plan shows `SCAN`. The likely candidates are `follow_models(followed_by_id, following_id)`, `favorite_models(favorite_id, favorite_by_id)`, `article_models(author_id, updated_at)`, `comment_models(article_id)`, `article_user_models(user_model_id)` (unique, which also closes a duplicate-row race), `users(username)` (unique, matching the API contract), and `article_tags(tag_model_id)`. Declare them in struct tags so `AutoMigrate` creates them. Run `ANALYZE` once afterwards, since SQLite has no background statistics.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Plan-driven composite indexes declared in model tags | **preferred** | Turns scans into probes; versioned with code |
| B: Fix PERF-001 only | | Removes the multiplier, but each remaining query still scans |
| C: Cache counts and follow state | | Invalidation on every write; hides the scans rather than removing them |

**Trade-offs**
Every index adds write cost on a single-writer engine (each follow, favorite or comment insert updates one more B-tree) and file size. A unique index on `users.username` will fail migration if duplicates already exist, so check first. Building an index on a large live file holds the write lock for the duration.

**Validation**
- Baseline: `EXPLAIN QUERY PLAN` per query. `safe-on-production` (plan only, no execution).
- Expectation: `SCAN <table>` before. `SEARCH <table> USING INDEX ...` after.
- Falsifier: the plans already show `SEARCH ... USING INDEX`. Then the finding is refuted for that table.
- Guard: a test that runs `EXPLAIN QUERY PLAN` for the follow and favorite lookups and asserts no `SCAN`.
- Commands:
  - `safe-on-production`: `sqlite3 ./data/gorm.db ".indexes follow_models"`. Lists the indexes that actually exist.
  - `safe-on-production`: `sqlite3 ./data/gorm.db "EXPLAIN QUERY PLAN SELECT * FROM follow_models WHERE following_id = 1 AND followed_by_id = 2 AND deleted_at IS NULL LIMIT 1;"`. Shows scan vs index probe for the per-item lookup.
  - `safe-on-production`: `sqlite3 ./data/gorm.db "EXPLAIN QUERY PLAN SELECT favorite_id, COUNT(*) FROM favorite_models WHERE favorite_id IN (1,2,3) AND deleted_at IS NULL GROUP BY favorite_id;"`. Same check for the batch favorite count.

---

### PERF-005 — SQLite runs with default locking configuration, an uncapped pool, and read-only transactions that lengthen lock holds

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `common/database.go:57` (`Init`) |
| **Tags** | needs-measurement |

**Problem**
The DB is opened with no `journal_mode`, no `busy_timeout` and no `SetMaxOpenConns` (only `SetMaxIdleConns(10)`). Under default rollback-journal mode, any in-progress read blocks a writer's commit, and a pending writer blocks new readers. The list and feed paths make this worse by wrapping several reads in an explicit transaction (`articles/models.go:192-262`, `:280-306`), holding the read lock across 3–6 statements instead of one at a time. An uncapped pool means concurrent requests open more connections, and on SQLite that adds contention rather than throughput.

**Performance principle**
On a resource that admits one writer at a time, minimize lock hold time and the number of concurrent contenders. Extra parallelism converts into waiting or errors.

**Evidence**
`common/database.go:57-66`. `articles/models.go:192`, `:262`, `:280`, `:306`. No `PRAGMA` anywhere in the repo. No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request under concurrent write activity.
- Growth: queueing is non-linear as write concurrency rises; the threshold is unknown.
- Blast radius: system-wide (one file).

**Conditions**
Production on SQLite with concurrent writes (favorites, comments, follows) overlapping list reads. Unknown whether this deployment exists, which caps confidence at Medium.

**Counter-evidence**
- `AGENTS.md` positions SQLite as dev/test. Effect: lowers-confidence (reflected).
- The driver may apply its own default busy timeout, which turns errors into waits. Not verified. Effect: lowers-confidence.
- Searched for a WAL or DSN setting. None found. Effect: no-effect.

**Why this might not matter**
Low write concurrency, or a dev-only deployment, never produces contention. And if production moves to a client-server database, this finding is moot.

**Recommendation**
If SQLite stays in production: enable WAL so readers do not block the writer, set an explicit `busy_timeout` chosen from a measured write duration, cap open connections with a value chosen deliberately (it buys read concurrency only), and remove the explicit transactions from read-only paths. If production needs concurrent writers at scale, the engine choice is the real decision. That needs workload data, not this review.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: WAL + explicit busy_timeout + drop read txs | **preferred** | Removes reader/writer blocking at low cost |
| B: Cap pool at 1 | | Serializes everything; simple but throttles reads |
| C: Migrate to a client-server RDBMS | | Right answer only with evidence of write concurrency needs |

**Trade-offs**
WAL adds a `-wal`/`-shm` file and needs checkpointing. A long reader can block checkpoints and grow the WAL. WAL is also unsafe on network filesystems. A busy timeout trades errors for latency.

**Validation**
- Baseline: `PRAGMA journal_mode;` and `PRAGMA busy_timeout;` (`safe-on-production`). Count `database is locked` errors in logs.
- Expectation: fewer lock errors and lower tail latency on list endpoints under concurrent writes.
- Falsifier: a concurrent load test on staging shows no lock errors or waits before the change.
- Guard: alert on `database is locked` in logs.
- Command (`safe-on-production`): `sqlite3 ./data/gorm.db "PRAGMA journal_mode;"`

---

### PERF-006 — Auth middleware runs twice on authenticated routes (duplicate JWT parse + user SELECT)

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `hello.go:48` (`main`) |
| **Tags** | quick-win |

**Problem**
`v1.Use(users.AuthMiddleware(false))` (line 43) and later `v1.Use(users.AuthMiddleware(true))` (line 48) both append to the same group. Every route registered after line 48 (`/user`, profile follow, article writes, `/feed`) runs both. That is two JWT parses, three `UpdateContextUserModel` calls, and two `SELECT ... FROM users WHERE id = ?` per request.

**Performance principle**
Do not repeat the same per-request work in successive layers.

**Evidence**
`hello.go:43`, `:48`. `users/middlewares.go:30-35` (DB fetch at :34), `:43-75`. No runtime evidence.

**Impact**
Critical path. Per request on authenticated routes. O(1): one extra primary-key lookup and one HMAC verify. Endpoint blast radius.

**Conditions**
Matters only proportionally to authenticated-route traffic. It is a constant factor.

**Counter-evidence**
Checked whether Gin dedupes middleware; it does not, because `Use` appends. Checked whether the second pass short-circuits; it does not. Effect: no-effect.

**Recommendation**
Register the authenticated routes on a separate subgroup with only `AuthMiddleware(true)`. Or have the required-auth variant reuse `my_user_model` if the optional pass already set it.

**Trade-offs**
Route-group restructuring. The test router setup must mirror it.

**Validation**
Count `users` SELECTs per `GET /api/user` in test logs. Expect 2 before, 1 after (`not-safe-on-production`, test DB). Falsifier: already 1.

---

### PERF-007 — No performance instrumentation: no metrics, traces, profiles, benchmarks or query counters

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `hello.go:35` (`main`) |
| **Tags** | quick-win |

**Problem**
The only runtime signal is Gin's stdout access log (`gin.Default()`) and GORM's default logger. There is no latency histogram per route, no query count or duration metric, no pprof, no benchmark and no load scenario. None of the findings above can be confirmed or validated in production.

**Performance principle**
A system cannot be optimized defensibly where its time cannot be measured.

**Evidence**
`hello.go:35`, `common/database.go:57`. A grep for pprof/prometheus/otel/Benchmark found nothing. `api/run-api-tests.sh` is functional with a 500 ms delay, not a load test.

**Impact**
Off the request path (async/meta). No direct latency cost. Service-wide inability to validate.

**Conditions**
Always. Its value scales with how much the findings above matter in production.

**Counter-evidence**
The access log does print per-request latency, which is a partial signal. Effect: bounds-impact (reflected in Low).

**Recommendation**
Add a request-duration histogram per route template, a GORM callback counting statements and duration per request, and, behind an internal-only listener, `net/http/pprof`. Add a `Benchmark` or test asserting per-endpoint query counts. That is the cheapest durable guard for PERF-001.

**Trade-offs**
Small per-request overhead. pprof must not be exposed publicly.

**Validation**
Metric present and populated in staging. Falsifier: N/A (it is an enabling change). `safe-on-production` once deployed.

---

### Remaining findings

All findings are presented in full above.

### Considered and not reported

| Observation | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| bcrypt at `DefaultCost` on registration, login and password update | Auth paths; process CPU | `users/models.go:63`, `:74` | Intentional security cost, constant per request, and only on auth endpoints | Login rate is high enough that CPU saturates (needs a CPU profile) |
| `GET /api/articles/feed` loads *all* followings with full user rows every request, then builds an `IN` list | Feed; SQLite | `users/models.go:143-154`, `articles/models.go:281-291` | Bounded by one user's follow count, which is unknown but typically small | Users following thousands of accounts. The `IN` list could also hit the bound-parameter limit. |
| `GetArticleUserModel` does `FirstOrCreate` (a potential write) on read paths | List/retrieve; SQLite write lock | `articles/models.go:59-62`, `articles/serializers.go:80`, `:131` | Writes at most once per user; the harmful case is PERF-003 | Evidence of frequent new users hitting reads |
| Single-article retrieve issues about 5 sequential queries | `GET /api/articles/:slug` | `articles/serializers.go:67-90` | Constant count, single row | Retrieve shows up as hot in access logs |

---

### Adjacent findings — outside performance scope

### SEC-001 — JWT signing secret hard-coded in source

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `common/utils.go:41` |

**Problem** The HMAC secret used to sign and verify every JWT is a compile-time constant in the public repository (value not reproduced here). The comment above it says it should be kept private.

**Evidence** `common/utils.go:40-41`, used at `common/utils.go:51` (sign) and `users/middlewares.go:55-60` (verify). A `#nosec G101` annotation suppresses the scanner warning.

**Impact** Anyone with the source can mint a valid token for any user ID, so this is an authentication bypass for any deployment that does not change it.

**Recommendation** Load the secret from the environment or a secret manager at startup, and fail to start if it is missing. Rotate it.

**Trade-offs** Existing tokens are invalidated on rotation, and a deploy-time secret must be provisioned.

**Validation** Run a security review of secret handling. A token signed with the old constant must be rejected.

**Would need** A dedicated security review / secret-scanning pass (e.g. gitleaks) plus an auth-focused review.

### SEC-002 — Auth tokens accepted in the query string and written to access logs

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `users/middlewares.go:21` |

**Problem** `extractToken` accepts `?access_token=`, and `gin.Default()`'s logger prints the request path including its raw query string. Tokens passed this way end up in stdout logs.

**Evidence** `users/middlewares.go:20-24`, `hello.go:35`.

**Impact** Anyone with log access can replay bearer tokens for up to their 24 h lifetime (`common/utils.go:48`).

**Recommendation** Drop query-string token support, or redact `access_token` in a custom log formatter.

**Trade-offs** Clients that rely on query tokens break.

**Validation** Request with `?access_token=` and confirm it is rejected or redacted in logs.

**Would need** A security review of authentication transport and log handling.

### COR-001 — Zero-valued struct conditions are dropped, so follow and favorite state is wrong for anonymous viewers

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `users/models.go:124` |

**Problem** `isFollowing` and `isFavoriteBy` build conditions from structs (`FollowModel{FollowingID: v.ID, FollowedByID: u.ID}`). GORM ignores zero-valued fields in struct conditions, so for an anonymous viewer (ID 0) the viewer filter disappears. The result is `following: true` if *anyone* follows the author, and `favorited: true` if anyone favorited the article.

**Evidence** `users/models.go:124-128`, `articles/models.go:79-83`. The anonymous viewer is `UserModel{}` with ID 0 (`users/middlewares.go:30-35`). The single-article path calls `isFavoriteBy(GetArticleUserModel(myUserModel))`, which returns an ID-0 model for anonymous viewers (`articles/models.go:56-57`).

**Impact** Anonymous users see incorrect `following` and `favorited` flags on profiles, single articles and every list item.

**Recommendation** Use explicit `Where("following_id = ? AND followed_by_id = ?", ...)`, or short-circuit to `false` when the viewer ID is 0. The short-circuit also removes the query, which helps PERF-001.

**Trade-offs** None material.

**Validation** Test: anonymous `GET /api/profiles/:username` for a user someone follows must return `following: false`.

**Would need** A correctness-focused test pass over GORM struct-condition usage.

---

## 8. Prioritized action plan

P1: PERF-003, PERF-002, PERF-001, PERF-004 · P2: PERF-005 · P3: PERF-007, PERF-006 (no P0 findings).

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-007 | P3 | Small | **Sequenced first despite P3**: a query-count test and a GORM statement counter are needed to validate everything below |
| 2 | PERF-003 | P1 | Small | Anonymous-triggerable lock inversion with system-wide reach; a few lines |
| 3 | PERF-002 | P1 | Small | Bounds the multiplier for PERF-001 and PERF-004 |
| 4 | PERF-001 | P1 | Medium | Removes per-item queries; extends an existing batching pattern |
| 5 | PERF-004 | P1 | Medium | Plan-driven; measure first, then index |
| 6 | PERF-005 | P2 | Small–Medium | Depends on the answer to "is SQLite production?" |
| 7 | PERF-006 | P3 | Small | Constant-factor cleanup |

**If only one thing is done:** fix PERF-003. Stop `FindManyArticle` from writing through a second connection while its own read transaction is open.

---

## 9. Validation plan

### PERF-003
- **Baseline:** Go test on the test DB. Create a user with `test_db.Create` (no ArticleUserModel), time `FindManyArticle("", username, "10", "0", "")`. `not-safe-on-production`.
- **Change:** Look up the ArticleUserModel without creating it, outside or without the transaction.
- **Measurement:** Elapsed time and whether the INSERT returned BUSY.
- **Expectation:** Before: about the busy timeout or a BUSY error. After: immediate.
- **Falsifier:** Immediate return and a created row before the fix.
- **Guard:** Keep the test with a time bound.
- **Commands:**
  - `not-safe-on-production` `go test ./articles/ -run TestFindManyArticle -v`. Runs the existing list tests against the test DB to confirm the fix does not regress them.
  - `safe-on-production` `sqlite3 ./data/gorm.db "PRAGMA journal_mode;"`. `wal` refutes the mechanism.

### PERF-001
- **Baseline:** Count `follow_models` statements per list request in test logs at two page sizes. `not-safe-on-production`.
- **Change:** Batch follow status; skip it for anonymous viewers.
- **Measurement:** Statement count.
- **Expectation:** From N per response to ≤1 (0 for anonymous).
- **Falsifier:** Count already constant.
- **Guard:** Query-count assertion test.
- **Commands:** `not-safe-on-production` `go test ./articles/ -run TestArticleListWithFilters -v 2>&1 | grep -c "follow_models"`.

### PERF-002
- **Baseline:** Rows returned for `limit=100000` and `limit=-1` on a seeded test DB. `not-safe-on-production`.
- **Change:** Clamp the limit; paginate comments.
- **Expectation:** Rows ≤ max.
- **Falsifier:** Already clamped.
- **Guard:** Handler test.

### PERF-004
- **Baseline:** `EXPLAIN QUERY PLAN` per hot query. `safe-on-production`.
- **Change:** Declared indexes where the plan shows SCAN, then `ANALYZE`.
- **Expectation:** SCAN → SEARCH USING INDEX.
- **Falsifier:** Already SEARCH.
- **Guard:** Plan-assertion test.
- **Commands:** `safe-on-production` `sqlite3 ./data/gorm.db ".indexes follow_models"`. `safe-on-production` `sqlite3 ./data/gorm.db "EXPLAIN QUERY PLAN SELECT * FROM follow_models WHERE following_id = 1 AND followed_by_id = 2 AND deleted_at IS NULL LIMIT 1;"`.

### PERF-005
- **Baseline:** `PRAGMA journal_mode;` `PRAGMA busy_timeout;` and the count of `database is locked` log lines. `safe-on-production`.
- **Expectation:** Fewer lock errors under concurrent writes after WAL and removing the read transactions.
- **Falsifier:** No lock errors under a staging concurrency test before the change. Load tests are `not-safe-on-production`.

### Instrumentation gaps to close first

The repo is uninstrumented. Before optimizing, add (1) a GORM callback that counts statements per request (also in tests), (2) a per-route latency histogram, and (3) an internal-only pprof listener. Without (1), PERF-001's fix cannot be guarded against regression.

---

## 10. Machine-readable output

Emitted alongside this report as `gin-treatment-2.json`, conforming to `schemas/review.schema.json`. The Markdown is authoritative.

## 11. Notes on this review

- Findings are classified by evidence grade. `Confirmed` requires a cited runtime artifact, and none exists here.
- No runtime metric in this report was estimated or assumed. The query counts in §4 are labelled derivations from code.
- Workload questions could not be asked of anyone. They are recorded as unanswered (§4), and workload-dependent findings are capped at Medium confidence.
