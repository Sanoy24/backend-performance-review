# Performance Review: golang-gin-realworld-example-app (gin-realworld)

**Date:** 2026-09-10
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v0.6.0
**Commit reviewed:** `626c372d259472148d93303f74aa9b9a1cdcef24`

---

## 1. Executive summary

### Overall assessment

This is a small Go/Gin REST API (the gothinkster "RealWorld"/Conduit reference implementation) backed by a single embedded SQLite file through GORM. The code shows evidence of at least one prior optimization pass — several code comments explicitly say "batch to avoid N+1" — but that pass was incomplete: one list-rendering path still issues one database query per returned item, the schema is missing indexes on most of the columns those per-item queries filter on, and the SQLite connection is configured as if it were a client-server database with no acknowledgment that SQLite allows exactly one writer at a time. None of this is visible today because the service has no metrics, tracing, or query logging in its production code path — only Gin's default per-request access log. The two highest-priority findings are structural and config-visible, not workload-dependent guesses, and are fixable with contained, well-precedented changes.

### Most important findings

- **PERF-001 (P0)** — Article listing renders each returned article's author profile with its own, unbatched "is this author followed" query, and the page size that drives how many of these queries fire is not capped by the API.
- **PERF-002 (P0)** — The GORM/SQLite connection is configured with an unbounded connection pool, no `busy_timeout`, and no WAL mode, none of which account for SQLite's single-writer-at-a-time design; every one of the 13 write routes shares this exposure.
- **PERF-003 (P1)** — Foreign-key-shaped filter columns across the schema (`follows`, `favorites`, `articles.author_id`, `comments`, `article_user_models.user_model_id`, `users.username`) carry no index, unlike the columns that do (`slug`, `tag`, `email`).
- **PERF-004 (P2)** — Several write endpoints resolve the current user's `ArticleUserModel` twice within the same request via redundant `FirstOrCreate` calls.
- **PERF-005 (P3)** — Two read-only listing functions wrap their queries in an explicit `BEGIN`/`COMMIT` transaction that serves no purpose since nothing inside them writes.

### Highest-risk bottlenecks

**Current bottleneck (most defensible today):** PERF-001's per-article follow-status query combined with PERF-003's missing indexes on the `follows` table. Every article-listing or feed response pays one full-table-scan-shaped query per returned article, and the number of articles returned is set by a caller-supplied `limit` with no enforced ceiling — this is the one finding in this review with a genuine superlinear-in-effect shape (cost = page size × table-scan cost), reachable by any client, authenticated or anonymous.

**Highest scalability risk:** PERF-002. SQLite's single-writer design is a hard constraint, not a tuning question, and this application's configuration (no `SetMaxOpenConns`, no `busy_timeout`, no WAL) is the shape most likely to produce visible `SQLITE_BUSY` failures the moment two writes genuinely overlap — which does not require high traffic, only two users acting within the same short window. This is scored as a current risk rather than a pure future one because the misconfiguration exists today; whether it has already manifested is unknown (see below).

### Major unknowns

- Actual or expected concurrent write rate — would move PERF-002 between "current bottleneck" and "risk that has never triggered."
- Row counts for `follows`, `favorites`, `articles`, `comments` in any real deployment — would change PERF-001's and PERF-003's severity materially in either direction.
- Whether a reverse proxy/gateway sits in front of this service and already bounds request size and rate — affects how much weight the unbounded-request-body observation (see §7, Considered and not reported) deserves; it was not scored as a formal finding for exactly this reason.
- Whether this specific deployment remains the tutorial/reference app it was written as, or has been forked into a production service — this changes whether any of these findings currently matter at all.

---

## 2. Scope and method

**Reviewed:** All production Go source in the repository: `hello.go`, `doc.go`, `common/*.go`, `users/*.go`, `articles/*.go` (models, routers, serializers, validators, middleware) — excluding `*_test.go` files, which were grepped only for signals (e.g., outbound HTTP calls) and not analyzed as application logic. `go.mod`/`go.sum`, `.env.example`, `.github/workflows/ci.yml`, `.golangci.yml`, and the repository's top-level documentation were read for architecture and workload signals.

**Not reviewed:** Test files' internal logic (out of scope for a performance review of production code); `FRONTEND_INSTRUCTIONS.md`/`MOBILE_INSTRUCTIONS.md` (client-side, not backend); no `.env`, credential, or key file content was opened (only presence noted: `.env.example` exists, contains no live secrets).

**Evidence available:** Uninstrumented. There is no metrics library, tracing SDK, `/metrics` or `pprof` endpoint, query-count test, benchmark, or load-test script anywhere in the repository. The only per-request signal is Gin's default `Logger()` middleware, which writes method/path/status/latency to stdout — useful for manual inspection but not aggregated, not persisted, and not present in any form during this static review. This caps every workload-dependent finding at `Medium` confidence per the methodology and is itself a recommendation (§9).

**Ranking method:** Structural signals only. No runtime data (trace, profile, query log, `EXPLAIN` output) was supplied or found in the repository.

**Reference depth:** Go, Gin (via the comparative `technology/rest.md`), and SQLite all have dedicated (`deep`-tier) references. GORM, the ORM in use, has no dedicated technology file in this skill version; it was analyzed using the generic `application/data-access.md` guidance (ORM/query-construction patterns), which is a `conceptual`-depth substitute — noted here per the registry's degradation rule. `detect_stack.py` also flagged a `grpc` signal from an indirect `google.golang.org/protobuf` dependency; this was investigated directly (no `.proto` file, gRPC server, or client code exists anywhere in the repository) and refuted — no gRPC or distributed-call analysis was performed because there is no such call in this codebase.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All production `.go` files read in full (13 files across 4 packages); test files grepped for signals only |
| **Critical paths** | 5 / 5 identified paths analyzed (see §5) |
| **Technology support** | Go: deep · Gin/REST: deep · SQLite: deep · GORM: conceptual (no dedicated file) |
| **Runtime evidence** | None supplied or found |
| **Overall review confidence** | **Medium** |

**Review confidence is not finding confidence.** The codebase is small enough (13 non-test source files) that this review is confident it saw essentially all production request-handling code; "Medium" rather than "High" reflects that the review has zero runtime evidence and therefore cannot confirm whether any of these mechanisms have actually manifested as user-visible slowness — only that the mechanisms exist.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production request rate and write concurrency | No evidence exists — no metrics, logs sample, or stated traffic figures | Access logs, a metrics export, or an operator-stated rate |
| Row counts for `follows`/`favorites`/`articles`/`comments` | No evidence exists — no seed data at scale, no production data | A row-count query (`SELECT COUNT(*)`) against the real deployed database file |
| Whether an upstream proxy bounds request size/rate | No evidence exists — no infrastructure manifest in the repository | The actual deployment topology (reverse proxy / gateway config, if any) |
| Whether `SQLITE_BUSY` errors have already occurred | No evidence exists — no logs or error-rate data available | Application logs or error-rate metrics for lock-related SQLite error strings |

---

## 3. Architecture overview

A single Go binary (`hello.go`, `package main`) serves a REST API via Gin, backed by one embedded SQLite database file accessed through GORM. There is no cache, no message broker, no second service, and no containerization or orchestration manifest in the repository (no `Dockerfile`, `docker-compose.yml`, or Kubernetes/Helm files were found).

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime | Go | `go 1.21` (go.mod); CI matrix tests 1.21–1.23 | deep | Application runtime |
| Web framework | Gin (`gin-gonic/gin`) | v1.10.0 | deep (via comparative `technology/rest.md`) | HTTP routing, middleware, JSON binding |
| ORM | GORM (`gorm.io/gorm`) | v1.25.12 | conceptual (no dedicated file; `application/data-access.md` applied) | Query construction, schema migration |
| Datastore | SQLite (`gorm.io/driver/sqlite` → `mattn/go-sqlite3`) | driver v1.5.7 / go-sqlite3 v1.14.22 | deep | System of record — single file, default path `./data/gorm.db` |
| Auth | `golang-jwt/jwt/v5` | v5.2.1 | generic (library, not a "technology" needing its own file) | Stateless session token issuance/verification |

**Shared resources:**
- The single SQLite database file (`common/database.go:21-27`) — every request across all 21 routes goes through the one `*gorm.DB` package-level variable (`common.DB`, `common/database.go:17`).
- The Go process's connection handle to that file, configured with `SetMaxIdleConns(10)` and no `SetMaxOpenConns` (`common/database.go:65`) — see PERF-002.
- `AuthMiddleware` (`users/middlewares.go:43-75`), registered globally via `v1.Use(...)` (`hello.go:43,48`), runs on nearly every request and performs one indexed primary-key lookup (`db.First(&myUserModel, my_user_id)`, `users/middlewares.go:34`) when a token is present.

---

## 4. Workload model

**Known** (cited)

- Single Go process, single `net/http` server (`gin.Default()` + `r.Run(":"+port)`), no worker/replica configuration found anywhere in the repo — `hello.go:24-70`.
- SQLite accessed through GORM; `SetMaxIdleConns(10)` is set, `SetMaxOpenConns` is never called anywhere in the repository (confirmed by repository-wide grep) — `common/database.go:65`.
- No `PRAGMA`, `journal_mode`, `busy_timeout`, or WAL configuration exists anywhere in the repository (confirmed by repository-wide grep).
- 21 registered HTTP routes: 8 read (`GET`) and 13 write (`POST`/`PUT`/`DELETE`) — `hello.go:41-60`, `users/routers.go:10-30`, `articles/routers.go:13-36` — a write-heavy-for-a-CRUD-app surface.
- No load-test script, rate-limiting middleware, autoscaling config, or container/orchestration manifest exists in the repository.
- No metrics, tracing, or query logging exists on the production DB path; `TestDBInit` (test-only) enables GORM's `logger.Info` mode, but `Init()` (production) does not — `common/database.go:49-69` vs. `72-94`.
- `ArticleList`/`ArticleFeed` accept caller-supplied `limit`/`offset` query parameters parsed with `strconv.Atoi`, defaulting to 20 only on a parse failure, with no enforced maximum in either code path — `articles/models.go:182-190`, `271-278`.
- Schema indexes (`gorm:"uniqueIndex"`) exist only on `ArticleModel.Slug`, `TagModel.Tag`, and `UserModel.Email`. No index tag appears on `UserModel.Username`, `ArticleModel.AuthorID`, `CommentModel.ArticleID`/`AuthorID`, `FavoriteModel.FavoriteID`/`FavoriteByID`, `FollowModel.FollowingID`/`FollowedByID`, or `ArticleUserModel.UserModelID` — `users/models.go:16-42`, `articles/models.go:11-52`.
- The repository is itself the "RealWorld"/Conduit reference implementation for Gin (module `github.com/gothinkster/golang-gin-realworld-example-app`; `doc.go`; `BACKEND_INSTRUCTIONS.md`: "Delete this file before publishing your app!").

**Assumed** (stated and unverified — drives the confidence caps)

- The bounded workload interview (methodology/workload.md §2) could not be answered interactively in this run. Per the methodology's own instruction for exactly this situation, the review proceeded rather than blocking, and every workload-dependent finding below is capped at `Medium` confidence or carries its assumption explicitly in `Conditions` — affects: PERF-001, PERF-003.
- This code may be deployed as, or as the base for, a real service rather than remaining a pure teaching example — this is the assumption that makes the review worth producing at all, and it underlies every finding's relevance — affects: all findings.
- Two overlapping writes (e.g., two users commenting or favoriting within the same short window) are plausible even at low absolute request rates, since `SQLITE_BUSY` contention requires overlap, not high throughput — affects: PERF-002.
- `follows`, `favorites`, `articles`, and `comments` can plausibly grow past a handful of rows in real usage; this cannot be verified from a repository with no seed/fixture data at meaningful scale — affects: PERF-001, PERF-003.

**Unknown** (would change conclusions)

- Actual or expected request rate for any endpoint.
- Row counts for `users`, `articles`, `follows`, `favorites`, `comments` in any real deployment.
- Whether this specific fork is deployed as a near-zero-traffic demo or a production service.
- Whether a reverse proxy/gateway in front of the app enforces a request body size limit.
- Current p50/p99 latency for any endpoint.
- Whether `SQLITE_BUSY` errors are being observed in practice today.

**Derived** (computed from the entries above — inputs shown)

- Per-listing-request query count ≈ `n + 4`, where `n` = the number of articles returned: 1 main article query + 1 `BatchGetFavoriteCounts` + 1 `BatchGetFavoriteStatus` + 1 `GetArticleUserModel` (for the caller) + `n` unbatched `isFollowing` queries, one per returned article — from `articles/serializers.go:116-141` (the batched favorite calls) plus `articles/serializers.go:93-114` → `articles/serializers.go:38-41` → `users/serializers.go:24-38` → `users/models.go:121-129` (the unbatched follow-status call). Since `n` is caller-controlled with no enforced maximum (see Known, above), this count is unbounded.
- Per-article-mutation-request `ArticleUserModel` lookups ≥ 2: once during authorization/validation (e.g., `articles/routers.go:108`, or inside `Bind` at `articles/validators.go:46`), and again inside response serialization (`articles/serializers.go:80`) — both calls resolve `GetArticleUserModel` for the same authenticated user within one request (`articles/models.go:54-65`).

**Measured**

- None — no benchmark, trace, profile, or query log was supplied or found in the repository.

### Questions that would change the ranking

| # | Question | What it would change |
|:--|:--|:--|
| 1 | What is the expected concurrent-write rate (simultaneous article/comment/favorite/follow writes)? | Would move PERF-002 between "current bottleneck" (if overlaps are common) and "dormant risk" (if writes are proven never to overlap). |
| 2 | What is the maximum realistic row count for `follows`, `favorites`, `articles`, and `comments`? | Would bound PERF-001's per-article query cost and directly change PERF-003's severity in either direction. |
| 3 | Is there a reverse proxy or API gateway in front of this service enforcing request size and rate limits? | Would determine whether the unbounded-request-body observation (§7, Considered and not reported) should become a formal finding. |
| 4 | Is this deployment intended to remain the tutorial/reference app, or is it the basis for a production service? | Changes whether any finding in this report currently matters. |
| 5 | Is there a specific slow endpoint, incident, or complaint prompting this review? | Would reorganize the whole review around confirming or refuting that complaint. |

---

## 5. Critical path analysis

Ranked by structural signals only; no runtime data was available.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles`, `GET /api/articles/feed` | yes | 4 + n (n = returned articles) | no — `limit` uncapped | no | PERF-001; also touches the single SQLite handle under PERF-002 |
| 2 | `POST/PUT/DELETE /api/articles/*` (create, update, delete, favorite, unfavorite, comment) | yes | ~5–7 per request, includes 2 redundant `ArticleUserModel` lookups | yes (per-request work is bounded) | no | PERF-002 (write contention), PERF-004 (duplication) |
| 3 | `POST /api/users`, `POST /api/users/login`, `PUT /api/user` | yes | 1–2 + bcrypt hash/compare (CPU, bounded, expected) | yes | no | PERF-002 exposure on the write paths |
| 4 | `AuthMiddleware` (runs before nearly every request) | yes | 1 indexed PK lookup (`db.First`) when a token is present | yes | no | Highest-frequency DB touchpoint in the service; individually cheap (O(1), indexed) |
| 5 | `GET /api/tags`, `GET /api/ping` | yes | 1 unbounded `Find` (tags) / none (ping) | tags: no enforced max, but no growth mechanism found | no | Considered and discarded — see §7 |

**Amplification points:** `GET /api/articles` and `GET /api/articles/feed` — query count scales 1:1 with returned article count, and that count is caller-controlled with no enforced ceiling (PERF-001).

**Paths deliberately not analyzed in depth, and why:** `GET /api/ping` — a static JSON literal with no datastore access; nothing to analyze.

---

## 6. Layer analysis

### 6.1 Application

Request handling is thin: routers call directly into package-level model functions (`FindOneArticle`, `SaveOne`, etc.) that each grab the shared `*gorm.DB` via `common.GetDB()`. There is no service/business-logic layer distinct from the data-access functions, which makes tracing a request straightforward but also means every handler owns its own query shape with no shared enforcement point (e.g., no central place that caps `limit`).

### 6.2 API

Gin's default middleware stack (`Logger`, `Recovery`) is used with no additions — no rate limiting, no request-size limit (`http.MaxBytesReader` is never called anywhere in the repository), no server-side read/write/idle timeouts beyond `net/http`'s defaults. `RedirectTrailingSlash` is explicitly disabled (`hello.go:39`) with a comment explaining it preserves POST bodies across what would otherwise be a redirect — a correct, intentional choice, not a finding. Pagination (`limit`/`offset`) is the only bounded-response mechanism, and it is caller-overridable with no ceiling (PERF-001).

### 6.3 Data access and datastore

This is where this review's material findings concentrate. GORM is used consistently with parameterized `Where(...)` predicates throughout (no string-concatenated SQL was found — no SQL-injection-shaped pattern exists). The favorites path shows a genuine prior fix for N+1 (`BatchGetFavoriteCounts`/`BatchGetFavoriteStatus`, `articles/models.go:87-126`, wired into `ArticlesSerializer.Response()`, `articles/serializers.go:116-141`), but the same treatment was not applied to the follow-status check reached from the same serialization path (PERF-001). The schema's index coverage is inconsistent — some uniquely-constrained lookup columns are indexed, most foreign-key-shaped filter columns are not (PERF-003). The SQLite connection is configured with generic connection-pool reasoning (`SetMaxIdleConns(10)`) that does not account for SQLite's single-writer architecture (PERF-002). Two read-only listing functions wrap their queries in an unnecessary explicit transaction (PERF-005).

### 6.7 Observability

There is no metrics library, tracing SDK, `/metrics` or `pprof` endpoint, structured query log, benchmark, or load-test script anywhere in the repository. The only signal is Gin's default access log (method/path/status/latency to stdout), which is not aggregated or persisted anywhere in this codebase. This is itself the most consequential finding in the review in one sense: none of PERF-001 through PERF-005 can be validated, prioritized against real traffic, or confirmed as already-occurring without first adding some form of measurement — see §9.

*(No cache, distributed/service-to-service, or infrastructure layer exists in this repository; those sections are omitted rather than stubbed.)*

---

## 7. Findings

### PERF-001 — Unbatched per-article follow-status query in article listings, amplified by an uncapped page size

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | data-access |
| **Location** | `articles/serializers.go:35` (via `users/serializers.go:35`, reached from `articles/serializers.go:93-114`) |
| **Tags** | quick-win, scalability-risk |

**Problem**
Every article returned by `GET /api/articles` or `GET /api/articles/feed` triggers its own database query to check whether the current user follows that article's author, and the number of articles a single request can return is not capped.

**Performance principle**
Repeated, per-item remote operations inside a response-rendering loop scale query count with result size instead of issuing one operation for the whole result set (N+1) — and an uncapped result size turns that linear cost into one with no ceiling.

**Evidence**
- `articles/serializers.go:116-141` (`ArticlesSerializer.Response`) batches favorite counts and favorite status via `BatchGetFavoriteCounts`/`BatchGetFavoriteStatus` before looping over articles — a real, already-applied fix for that specific N+1.
- The same loop still calls `ResponseWithPreloaded` (`articles/serializers.go:93-114`), which calls `authorSerializer.Response()` at line 103 → `ArticleUserSerializer.Response()` (`articles/serializers.go:38-41`) → `ProfileSerializer.Response()` (`users/serializers.go:24-38`) → `myUserModel.isFollowing(self.UserModel)` at `users/serializers.go:35` → `users/models.go:121-129`, which issues `db.Where(FollowModel{...}).First(&follow)` — one query, per article, not batched.
- `articles/models.go:182-190` (`FindManyArticle`) and `articles/models.go:271-278` (`GetArticleFeed`) parse `limit` with `strconv.Atoi`, defaulting to 20 only when parsing fails — a caller supplying `?limit=5000` receives up to 5000 articles, each paying the per-article query above.
- No runtime evidence (query log, trace, or benchmark) exists; this is asserted from code structure, which is a defensible claim per methodology (an *unbounded, repeated* operation is provable from source; a *slow* one is not).

**Impact**
- Position: critical path (both are user- and client-facing GET endpoints).
- Frequency: per-item (one query per returned article).
- Growth: O(n), where n = returned article count, itself unbounded by the API.
- Amplification: Nx (n = caller-supplied page size).
- Blast radius: service — the extra round trips consume time on the single shared SQLite handle that every other request (read and write) also depends on.

**Conditions**
This matters once either (a) a listing response routinely returns more than a handful of articles, or (b) any client — including an anonymous one, since `ArticlesAnonymousRegister` requires no authentication — passes a large `limit`. Both `follows` table size (compounding via PERF-003's missing index) and typical/maximum page size are unknown; the severity grade assumes at least one of these is realistic for a service intended for real use, per the workload assumption stated in §4.

**Counter-evidence**
- Searched for memoization or caching of follow-status the way favorites were batched in the same file: none found — no-effect.
- Searched for any place `limit` is clamped (middleware, router, or elsewhere): none found; `limit_int` is set only by `strconv.Atoi` or the literal fallback `20` — no-effect.
- Checked whether the listing endpoint is reachable only by privileged/internal callers: it is not — `ArticlesAnonymousRegister` (`articles/routers.go:26-31`) requires no authenticated user — no-effect.

**Why this might not matter**
If the deployed instance never actually returns more than the ~20-article default (e.g., a small seed dataset and no client ever passing a large `limit`), the added round trips are a modest constant-factor cost rather than the unbounded worst case this grade assumes — the Critical grade specifically reflects that the ceiling is caller-controlled, not that large values have been observed in practice.

**Recommendation**
Batch the follow-status check exactly the way favorites are already batched: collect the distinct author `ArticleUserModel` IDs from the page of returned articles, issue one `Where("following_id IN ? AND followed_by_id = ?", authorIDs, currentUserID)` query against `follows`, build a lookup set, and thread a precomputed `following bool` into `ResponseWithPreloaded` the same way `favorited`/`favoritesCount` are already threaded in. Separately, enforce a hard maximum on `limit` (e.g., clamp to a fixed ceiling) independent of the parse-failure default of 20.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Batch the follow check + cap `limit` | **preferred** | Removes both the repetition and the unbounded fan-out at the source, mirroring the pattern already proven in the same file for favorites |
| B — Per-request in-memory memoization of `isFollowing` results | | Reduces duplicate calls for the same author appearing twice, but does not remove the per-author round trip or the unbounded `limit` |
| C — Cache follow-status in a shared cache (e.g., Redis) | | Introduces invalidation complexity and new infrastructure for a problem removable by batching alone — cargo-cult risk per Hard Rule 5 |

**Trade-offs**
Batching requires threading a `followingSet` parameter through `ArticleSerializer`/`ArticleUserSerializer`, a small increase in parameter-passing complexity. Capping `limit` is a caller-visible API behavior change for any client currently relying on an uncapped response and should be called out if this API has external consumers.

**Validation**
- Baseline: count SQL statements executed for `GET /api/articles?limit=20` and for a large `limit`, using GORM's `logger.Info` mode (already used in `TestDBInit`) against a seeded staging dataset. **not-safe-on-production** (verbose query logging on a live system).
- Measurement: query count for the same request, before and after.
- Expectation: query count falls from `n + 4` to `4`, independent of `n`.
- Falsifier: if query count does not drop to a constant after the change, the batched path was not actually wired into the response.
- Guard: a test asserting a fixed maximum query count for `GET /api/articles` regardless of returned article count.

---

### PERF-002 — SQLite's single-writer model is not accounted for in connection or pragma configuration

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | concurrency |
| **Location** | `common/database.go:49-69` |
| **Tags** | quick-win, scalability-risk |

**Problem**
The application opens its SQLite connection with `SetMaxIdleConns(10)` and never calls `SetMaxOpenConns`, and configures no `busy_timeout` or WAL mode — a configuration shape that assumes a client-server database where more connections mean more concurrent work. SQLite allows exactly one writer to the file at a time by design; every one of the 13 write routes in this service shares that single lock.

**Performance principle**
A bounded shared resource's capacity must be sized against how the resource actually behaves, not against a generic pooling default; sizing a pool for concurrency the underlying resource cannot provide moves the failure from "waiting" to "erroring."

**Evidence**
- `common/database.go:57` opens the database via `gorm.Open(sqlite.Open(dbPath), &gorm.Config{})` with no DSN parameters and no `PRAGMA` statements anywhere.
- `common/database.go:65` calls `sqlDB.SetMaxIdleConns(10)`; `SetMaxOpenConns` is not called anywhere in the repository (confirmed by a repository-wide search) — the Go default is unlimited open connections.
- A repository-wide search found no `busy_timeout`, `journal_mode`, `PRAGMA`, or `WAL` string anywhere in the codebase.
- Per `technology/sqlite.md`: a second connection attempting to write while another write is in progress receives `SQLITE_BUSY` immediately unless `busy_timeout` is set (default 0), and a larger pool increases how often two connections attempt to write at once rather than adding write throughput — this is documented, checkable engine behavior, not an inference about this specific deployment's traffic.

**Impact**
- Position: critical path (every write endpoint is user-blocking).
- Frequency: per-request (any write request can be the "second" writer).
- Growth: unknown — this is a saturation/configuration finding, not a data-growth one.
- Blast radius: service — a write held by one connection can cause `SQLITE_BUSY` on any other connection attempting to write to the same file, and (in the default rollback-journal mode, since WAL is not configured) can also block readers during commit.

**Conditions**
This matters the moment two write requests to any of the 13 write routes (article/comment/favorite/follow/user create-update-delete) genuinely overlap in time — which requires overlap, not high absolute throughput. Whether this has occurred in the current deployment is unknown (no logs or metrics exist to check); the severity grade reflects the configuration itself being a mismatch for the engine's documented behavior, independent of whether it has fired yet.

**Counter-evidence**
- Searched the entire repository for any `PRAGMA`/`journal_mode`/`busy_timeout` configuration: none found — no-effect.
- Checked `TestDBInit` for the same gap: also present (`SetMaxIdleConns(3)`, no `SetMaxOpenConns`, no pragmas) — no-effect, though this path is test-only and lower priority.
- Considered whether write concurrency might genuinely never occur (e.g., single-operator use consistent with this repo's origin as a tutorial reference app) — this is exactly why `Confidence` is graded `High` rather than `Confirmed`: the misconfiguration is certain from code, but whether it has caused an observed failure is not — bounds-impact.

**Why this might not matter**
If this service only ever runs as a single-operator local instance and never receives two overlapping write requests, SQLite's single-writer limitation never actually triggers a `SQLITE_BUSY` error in practice, and this finding is a scalability risk that has not yet become a current bottleneck.

**Recommendation**
Configure the SQLite connection to match its actual concurrency model: (1) set `SetMaxOpenConns` to a small explicit number (a common safe pattern is 1, given only one writer is ever useful) rather than leaving it unbounded; (2) enable WAL mode (`?_journal_mode=WAL` in the DSN, or `PRAGMA journal_mode=WAL` once at startup) so reads can proceed during a write; (3) set a non-zero `busy_timeout` (e.g., `?_busy_timeout=5000`) so a write that arrives during another write waits briefly instead of failing immediately.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Tune SQLite configuration (bounded pool + WAL + busy_timeout) | **preferred** | Cheapest, addresses the actual mechanism, keeps the existing architecture with no evidence yet that a client-server database is warranted |
| B — Migrate to a client-server relational engine (Postgres/MySQL) | | Removes the single-writer ceiling entirely, but is a materially larger change with no stated growth trigger yet — cargo-cult risk per Hard Rule 5 without evidence write concurrency actually requires it |
| C — Serialize writes through an in-process queue/mutex ahead of the DB | | Avoids relying on SQLite's own busy-retry behavior, at the cost of added complexity and a new single bottleneck (the queue itself) |

**Trade-offs**
WAL mode adds `-wal`/`-shm` files and requires periodic checkpointing (automatic by default, but a long-held read transaction can block it — see `technology/sqlite.md` §2); a non-zero `busy_timeout` converts an immediate error into added latency for the second writer, which should be reflected in any client-facing timeout budget; capping `SetMaxOpenConns` at 1 removes whatever incidental read concurrency multiple idle connections currently provide (mitigated by enabling WAL).

**Validation**
- Baseline: `PRAGMA journal_mode;` and `PRAGMA busy_timeout;` against the running database file. **safe-on-production**.
- Measurement: repeat the same pragmas after the change; additionally, in staging, fire two concurrent write requests (e.g., two simultaneous `POST /api/articles/:slug/comments`) and record whether either returns a locked-database error.
- Expectation: `journal_mode` reads `wal`, `busy_timeout` reads the configured non-zero value, and the concurrent-write staging test no longer surfaces an immediate `SQLITE_BUSY`-shaped error.
- Falsifier: if concurrent writes still fail immediately after configuring a non-zero `busy_timeout`, the DSN/pragma was not actually applied to the connection GORM uses.
- Guard: an integration test that fires several concurrent writes at a test database and asserts none fails within the configured timeout budget.

---

### PERF-003 — Foreign-key-shaped filter columns have no supporting index

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `users/models.go:16-42`, `articles/models.go:11-52` |
| **Tags** | quick-win |

**Problem**
`follows.following_id`/`followed_by_id`, `favorites.favorite_id`/`favorite_by_id`, `articles.author_id`, `comments.article_id`/`author_id`, `article_user_models.user_model_id`, and `users.username` are all used as `WHERE` predicates but carry no `gorm:"index"`/`uniqueIndex` tag, unlike `ArticleModel.Slug`, `TagModel.Tag`, and `UserModel.Email`, which do.

**Performance principle**
A predicate on an unindexed column forces the engine to examine every row of the table to find matches, rather than the ones a lookup key can reach — cost scales with table size instead of result size.

**Evidence**
- `users/models.go:16-23` (`UserModel`) — `Username` has no index tag; used in `FindOneUser(&UserModel{Username: username})` at `users/routers.go:34` (profile lookup) and `articles/models.go:215,237` (article-by-author/favorited-by-author listing).
- `users/models.go:36-42` (`FollowModel`) — `FollowingID`/`FollowedByID` have no index tags; used in `users/models.go:108-116` (`following`), `121-129` (`isFollowing`, reached per-article per PERF-001), `134-138` (`unFollowing`), `143-154` (`GetFollowings`).
- `articles/models.go:31-37` (`FavoriteModel`) — `FavoriteID`/`FavoriteByID` have no index tags; used in `articles/models.go:67-74` (`favoritesCount`), `76-84` (`isFavoriteBy`), `86-109`/`111-126` (the batched favorite lookups), `128-142` (`favoriteBy`/`unFavoriteBy`).
- `articles/models.go:11-21` (`ArticleModel`) — `AuthorID` has no index tag; used in `articles/models.go:300` (feed-by-author-ids).
- `articles/models.go:45-52` (`CommentModel`) — `ArticleID`/`AuthorID` have no index tags.
- `articles/models.go:23-29` (`ArticleUserModel`) — `UserModelID` has no index tag; used in `GetArticleUserModel` (`articles/models.go:54-65`), called on nearly every authenticated article-related request.
- No migration directory exists in this repository — `AutoMigrate`, driven purely by these struct tags, is the only schema-creation path (`hello.go:15-22`, `users/models.go:45-50`), so there is no other place an index could have been added.

**Impact**
- Position: critical path (these lookups back nearly every authenticated read and write in the `articles`/`users` packages).
- Frequency: per-request.
- Growth: O(n) — scan cost grows with the size of the affected table (`follows`, `favorites`, `articles`, `comments`, `article_user_models`, or `users`, depending on the query).
- Blast radius: service — affects profile views, article listings, comment listings, following/favoriting actions, and the per-request current-user resolution used across the `articles` package.

**Conditions**
Impact scales with each table's row count, which is unknown for any real deployment (see §4). At a handful of rows, a scan and an index-seek are indistinguishable; the severity grade assumes the workload assumption in §4 (these tables can plausibly grow past a handful of rows in real usage) holds, and is capped at `Medium` confidence for exactly this reason.

**Counter-evidence**
- Checked for a migrations directory that might add indexes outside the Go model tags: none exists — no-effect.
- Checked whether these tables might be provably small enough that a scan is cheap regardless: table sizes are unknown (no seed/fixture data at scale, no production metrics) — lowers-confidence, reflected in the `Medium` grade rather than `High`.

**Why this might not matter**
SQLite's planner performing a full scan is genuinely cheap when a table has only a few hundred rows; if `follows`, `favorites`, `articles`, and `comments` stay small in the actual deployment, these missing indexes cost nothing measurable, and adding them would be pure write-overhead for no benefit.

**Recommendation**
Add `gorm:"index"` (or a composite index where two columns are queried together, e.g., `follows(followed_by_id, following_id)`) to the columns named in Evidence, and confirm each with `EXPLAIN QUERY PLAN` before and after adding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Add the specific indexes named above | **preferred** | Directly targets the predicates actually issued; minimal write-cost increase given these tables are written far less often than they are read via the affected lookups |
| B — Do nothing until table sizes are confirmed | | Defensible only once row counts are known to stay small — currently unverifiable |
| C — Restructure the schema/ORM defaults | | Unnecessary; the gap is a missing tag on existing fields, not a modeling problem |

**Trade-offs**
Every added index costs extra write time and storage on every insert/update/delete touching that column — a real cost on `follows`, `favorites`, `articles`, `comments`, `article_user_models`, and `users`, all of which are written on ordinary user actions (creating an article, favoriting, following, commenting, registering). The expectation that this trades favorably (these paths are read far more often than written) is a reasoned expectation, not a measurement.

**Validation**
- Baseline: `EXPLAIN QUERY PLAN SELECT * FROM follows WHERE followed_by_id = ? AND following_id = ?` (and the equivalent for the other affected tables) against the current schema. **safe-on-production**.
- Measurement: repeat after the migration adding the indexes.
- Expectation: the plan changes from a full-table `SCAN` to a `SEARCH ... USING INDEX`.
- Falsifier: if the plan still shows a scan after the index is added, the predicate or the index's column order does not match the query.
- Guard: keep the `EXPLAIN QUERY PLAN` check as a one-time migration-verification step; SQLite has no automatic index-usage advisor, so periodic manual review of `PRAGMA index_list` is the available ongoing check.

---

### PERF-004 — The current user's `ArticleUserModel` is re-resolved from the database twice within a single request

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `articles/models.go:54-65` (`GetArticleUserModel`), called from `articles/routers.go:108` and `articles/serializers.go:80` within the same request |
| **Tags** | quick-win |

**Problem**
On article-mutation endpoints (create, update, delete, favorite, unfavorite, comment), the handler resolves the current user's `ArticleUserModel` once for authorization/validation and the response serializer resolves it again from scratch, each time via a `FirstOrCreate` database round trip.

**Performance principle**
The same derivable value, fetched from the same source, should be computed once per request and passed down rather than re-fetched at each point it is needed.

**Evidence**
- `articles/models.go:54-65` (`GetArticleUserModel`) always issues `db.Where(&ArticleUserModel{UserModelID: userModel.ID}).FirstOrCreate(&articleUserModel)` — no memoization exists at any layer.
- `articles/routers.go:108` (`ArticleUpdate`) calls `GetArticleUserModel(myUserModel)` for the authorship check; `articles/validators.go:46` (`ArticleModelValidator.Bind`, used by `ArticleCreate`) does the same during binding.
- `articles/serializers.go:80` (`ArticleSerializer.Response`, used by the single-article response paths — create, update, delete, favorite, unfavorite, comment-create) calls `s.isFavoriteBy(GetArticleUserModel(myUserModel))` — a second, independent resolution of the same user within the same request.

**Impact**
- Position: critical path (these are the write endpoints users wait on).
- Frequency: per-request.
- Growth: O(1) — the duplication is a fixed count per request, not data-dependent.
- Amplification: 2x (exactly two `GetArticleUserModel` calls per affected request, both for the same user).
- Blast radius: endpoint — contained to whichever article-mutation endpoint is called.

**Conditions**
This is a constant-factor cost present on every call to the affected endpoints, independent of data volume or traffic — it does not require any particular workload to matter, only that these endpoints are called at all. It ranks below PERF-001/002/003 because its cost does not grow.

**Counter-evidence**
- Checked whether `FirstOrCreate` might be served from a cache: no cache layer exists anywhere in the repository — no-effect.
- Checked whether the two call sites might resolve different users: both read `c.MustGet("my_user_model")`, i.e., the same authenticated user, within one request — no-effect.

**Why this might not matter**
`article_user_models` is bounded by user count rather than by content volume, and (absent PERF-003's fix) the lookup is a scan rather than an indexed read either way — but the table is likely to stay small relative to `articles`/`comments`, so the absolute added latency per request is a small constant. This is a real duplication but a low-magnitude one until user counts themselves are large.

**Recommendation**
Resolve the current user's `ArticleUserModel` once per request — e.g., inside `AuthMiddleware` alongside the existing `UserModel` lookup, or once at the top of each handler — and pass the resolved value into the serializer instead of calling `GetArticleUserModel` a second time from context data already available.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Resolve once per request and thread it through | **preferred** | Removes the duplicate round trip at its source, consistent with how `ResponseWithPreloaded` already avoids re-fetching favorite data |
| B — Add per-request memoization inside `GetArticleUserModel` keyed by user ID | | Smaller code change, but hides the duplication behind a cache rather than removing it |

**Trade-offs**
Threading the resolved `ArticleUserModel` through handler signatures touches several call sites (`ArticleCreate`, `ArticleUpdate`, `ArticleDelete`, `ArticleFavorite`, `ArticleUnfavorite`, `ArticleCommentCreate`) — a moderate, mechanical refactor rather than a one-line fix.

**Validation**
- Baseline: count queries against `article_user_models` for a single `POST /api/articles` request using GORM query logging in staging. **not-safe-on-production**.
- Measurement: same count, after the change.
- Expectation: count drops from 2 to 1.
- Falsifier: if the count does not drop, the duplicate call site was not actually removed.
- Guard: a test asserting the query count for article-mutation endpoints.

---

### PERF-005 — Read-only listing queries wrapped in an unnecessary explicit transaction

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `articles/models.go:192` (`FindManyArticle`), `articles/models.go:280` (`GetArticleFeed`) |
| **Tags** | quick-win |

**Problem**
`FindManyArticle` and `GetArticleFeed` open an explicit `db.Begin()`/`tx.Commit()` transaction around a sequence of queries that only ever read.

**Performance principle**
A transaction should scope exactly the work that needs its guarantees; wrapping read-only work in one adds lock-acquisition and bookkeeping overhead for no correctness benefit.

**Evidence**
- `articles/models.go:192` opens `tx := db.Begin()`; every statement between there and `articles/models.go:262` (`tx.Commit()`) is a `Find`/`First`/`Count`/`Association(...).Find` call — no `Save`, `Create`, `Update`, or `Delete` appears anywhere in the function.
- The same pattern repeats at `articles/models.go:280`–`306` in `GetArticleFeed`.

**Impact**
- Position: critical path (these back the two article-listing GET endpoints).
- Frequency: per-request.
- Growth: O(1) — fixed overhead per request, not data-dependent.
- Blast radius: endpoint — contained to the two listing functions.

**Conditions**
This is a small, constant per-request cost; it is reported because it is unambiguous and free to fix, not because it is a significant contributor to latency on its own. It would matter more once PERF-002's recommended WAL mode is in place, since a long-held read transaction can delay a WAL checkpoint (`technology/sqlite.md` §2) — an interaction worth avoiding by removing the unneeded transaction now rather than after WAL is enabled.

**Counter-evidence**
- Traced every statement inside both functions end-to-end: all are reads; no write appears at any point inside either transaction — no-effect.

**Why this might not matter**
If SQLite's overhead for a deferred, read-only transaction is negligible in this driver — plausible, since no lock upgrade is ever triggered — this may amount to a few extra function calls per request with no measurable effect, and would not be worth the risk of touching otherwise-working code ahead of PERF-001–003.

**Recommendation**
Remove the explicit `db.Begin()`/`tx.Commit()` wrapper and issue the same queries directly against `common.GetDB()`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Remove the transaction wrapper | **preferred** | No behavior change, since nothing inside ever writes; removes work that serves no purpose |
| B — Keep it for future-proofing against an eventual write being added | | Pays a real, if small, cost indefinitely for a hypothetical future change |

**Trade-offs**
None of note; if a future change adds a write inside these functions, the transaction should be reintroduced at that time.

**Validation**
- Baseline: not applicable beyond code review — this is a removal, not a behavior change.
- Measurement: confirm the returned article list and count are unchanged for the same inputs, before and after.
- Expectation: identical results; one fewer `BEGIN`/`COMMIT` pair issued per request to these two endpoints.
- Falsifier: if removing the transaction changes the returned data (e.g., under a concurrent write to the same rows during the request), that would indicate the transaction was providing read-consistency the two queries actually depend on, and it should be restored.
- Safety: **safe-on-production** (read-only queries are unaffected by removing the wrapper).
- Guard: existing article-listing tests already assert response shape and count; no new guard needed.

---

### Remaining findings

All findings identified in this review appear in full above; none exceeded the top-15 budget.

### Considered and not reported

- **Unbounded `TagList`/`getAllTags()` query** (`articles/models.go:170-175`) — no `LIMIT` is applied, but tag cardinality is bounded by the set of distinct tag strings ever submitted on article creation, not by article or user volume; no mechanism in the code generates unbounded tag growth, so this does not clear the workload test without evidence of real-world tag explosion.
- **Unbounded request body size** — no `http.MaxBytesReader` or any body-size limit is configured on any write endpoint (repository-wide search). This is real, but its actual severity depends entirely on whether an upstream reverse proxy or gateway already bounds request size — information not available in this repository (no infrastructure manifest exists to check). Per the methodology's guidance that low-confidence candidates are better framed as questions than filed as findings, this is carried instead as an open question in §4.
- **bcrypt cost factor** (`users/models.go:57-66`, `DefaultCost`) for password hashing — an expected, bounded, O(1)-per-request CPU cost and a deliberate security/performance trade-off; no evidence it is misconfigured for any stated workload.
- **`GOMAXPROCS` vs. container CPU-quota mismatch** (`technology/go.md`) — no `Dockerfile`, Kubernetes manifest, or other container/CPU-quota configuration exists anywhere in the repository, so this cannot be evaluated in either direction; recorded as an unknown, not a finding.
- **`detect_stack.py`'s `grpc` signal** — traced to an indirect `google.golang.org/protobuf` dependency (`go.mod`, `// indirect`); no `.proto` file, gRPC server, or client code exists anywhere in the repository. Investigated and refuted; no distributed-call analysis was performed because no such call exists.

### Adjacent findings — outside performance scope

#### SEC-001 — JWT signing secret is a hardcoded constant in source control

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `common/utils.go:41` |

**Problem** The JWT signing secret used to issue and verify every authentication token is a hardcoded string literal committed to source control (`const JWTSecret = "A String Very Very Very Strong!!@##$!@#$"`), rather than being loaded from an environment variable or secret manager.

**Evidence** `common/utils.go:41` declares the constant (marked `// #nosec G101`, i.e., the repository's own security linter is explicitly suppressed for this line); `common/utils.go:45-57` (`GenToken`) and `users/middlewares.go:55-61` (`AuthMiddleware`'s `jwt.Parse`) both use it directly with no override mechanism.

**Impact** Anyone with read access to this repository (public, since it is an open-source reference implementation, or otherwise) can forge a valid authentication token for any user ID without ever authenticating — a complete authentication bypass. Risk is graded High because the secret is both static and universally known to anyone who has seen the source, which is the entire population this repository is designed to be read by.

**Recommendation** Load the signing secret from an environment variable or secret manager at startup, generated uniquely per deployment, and fail startup loudly if it is unset in a production-configured environment; rotate any secret that may have been used with the hardcoded value.

**Trade-offs** Requires every deployment to provision the secret through configuration rather than relying on a value baked into the binary — a small operational step, and the correct one.

**Validation** Confirm the running service refuses to start (or falls back to a clearly-logged, non-production-safe default) when no secret is configured, and that tokens signed with the old hardcoded value are rejected after rotation.

**Would need** A dedicated security review of authentication and session management, and a secrets-scanning tool (e.g., `gitleaks`, `trufflehog`) run against the full repository history, not just the current tree.

#### COR-001 — Database initialization errors are logged and swallowed, leaving the app running against a possibly broken connection

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `common/database.go:49-69` |

**Problem** `Init()` logs (via `fmt.Println`) but does not act on errors from `gorm.Open` (line 57-60) or `db.DB()` (line 61-64), then unconditionally assigns the result to the package-level `DB` variable and returns it. If either call fails, the application continues starting up with a `nil` or non-functional database handle instead of failing fast.

**Evidence** `common/database.go:57-60`: `db, err := gorm.Open(...); if err != nil { fmt.Println(...) }` — execution continues regardless. `common/database.go:61-64`: the same pattern for `db.DB()`. `hello.go:26-27` calls `common.Init()` then immediately calls `Migrate(db)` with no error check in between.

**Impact** If the database fails to open (e.g., a bad `DB_PATH`, permissions issue, or disk problem), the failure surfaces later and more confusingly — either as a nil-pointer panic on the first query, or as GORM silently no-op'ing — rather than as a clear startup failure at the point the actual problem occurred. Graded Medium because it requires an already-unusual condition (DB open failure) to trigger, but produces a materially worse failure mode when it does.

**Recommendation** Replace the `fmt.Println` calls in `Init()` with a fatal exit (e.g., `log.Fatal`) on either error, so a broken database configuration is caught at startup rather than deferred to an arbitrary later request.

**Trade-offs** None of note — this makes an already-broken state fail faster and more clearly, with no change to the successful-startup path.

**Validation** A test or manual check that starting the service with an invalid `DB_PATH` (e.g., a directory with no write permission) causes an immediate, clear startup failure rather than continuing.

**Would need** A general correctness/error-handling review of startup and initialization code paths across the codebase, beyond this one instance.

---

## 8. Prioritized action plan

### P0 — Immediate
### P1 — High priority
### P2 — Medium priority
### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-002 | P0 | Low (config/DSN change) | Cheapest of the two P0s and reduces risk of outright write failures; sequenced first because it is both low-effort and load-bearing for PERF-005's later interaction note |
| 2 | PERF-001 | P0 | Medium (batching refactor + limit cap) | Highest-confidence current-bottleneck shape (unbounded amplification on the most-hit read endpoint); the `limit` cap alone is a same-day mitigation even before the batching refactor lands |
| 3 | PERF-003 | P1 | Low–Medium (schema migration) | Directly compounds PERF-001 and affects several other paths; straightforward to add, sequence after confirming table-size assumptions if possible |
| 4 | PERF-004 | P2 | Medium (call-site refactor) | Real but bounded constant-factor cost; lower urgency than the three above |
| 5 | PERF-005 | P3 | Trivial (delete a few lines) | Free to fix; sequenced last only because its impact is smallest, not because it is hard |

**If only one thing is done:** Cap the `limit` parameter on `GET /api/articles` and `GET /api/articles/feed` to a fixed maximum (part of PERF-001). It is a same-day change that removes the one genuinely unbounded, externally-triggerable cost in this review, ahead of the full follow-status batching refactor.

---

## 9. Validation plan

Per-finding validation appears inline in §7. Summarized production-safety labels:

| Finding | Key measurement | Safety |
|:--|:--|:--|
| PERF-001 | Query count for `GET /api/articles` before/after batching | not-safe-on-production (verbose logging) |
| PERF-002 | `PRAGMA journal_mode` / `PRAGMA busy_timeout` | safe-on-production |
| PERF-003 | `EXPLAIN QUERY PLAN` on affected predicates | safe-on-production |
| PERF-004 | Query count against `article_user_models` per request | not-safe-on-production (verbose logging) |
| PERF-005 | Response equivalence before/after removing the transaction | safe-on-production |

### Instrumentation gaps to close first

This system is uninstrumented (§2). Before any of the above changes are validated with confidence, add:
- A per-route request-duration metric (even a simple histogram exported via `expvar` or a minimal Prometheus client) — currently the only signal is an unaggregated stdout access log.
- A query-count or query-duration signal on the data-access layer (a GORM logger/callback is the lowest-effort option, since `TestDBInit` already demonstrates enabling GORM's built-in `logger.Info` mode) — this is the single most useful addition for validating PERF-001 and PERF-004 specifically, since query count is the mechanism-level signal for both.
- A way to observe `SQLITE_BUSY`/lock-related errors in production (even structured error logging distinguishing this error class) — this is what would convert PERF-002 from "a plausible mechanism" to "a confirmed occurrence."

This is sequenced ahead of the optimizations themselves per the methodology: optimizing an unmeasured system produces changes nobody can later justify or attribute correctly.

---

## 10. Machine-readable output

Conforms to `schemas/review.schema.json`, whose findings conform to `schemas/finding.schema.json`. See the accompanying file `gin-realworld-run-a.json`.

---

## 11. Notes on this review

- Findings are classified by evidence grade; no finding in this review is graded `Confirmed`, because no runtime artifact (trace, profile, query log, benchmark, or load test) was supplied or found in the repository — every grade here is `High`, `Medium`, or the discarded `Low` tier.
- No runtime metric in this report was estimated or assumed. Every number that appears (query counts, route counts, call-site counts) is either read directly from the repository or shown as a labelled derivation with its inputs visible (§4, "Derived").
- Recommendations state their trade-offs and their validation path. Where a candidate could not clear this bar (e.g., the unbounded-request-body observation), it is reported as an open question rather than a finding.
