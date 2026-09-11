# Performance Review: golang-gin-realworld-example-app (gin-realworld)

**Date:** 2026-09-10
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v0.6.0

---

## 1. Executive summary

### Overall assessment

This is a small, single-process Go/Gin API backed by a single embedded SQLite file through GORM. The codebase already shows evidence of one prior performance pass — several functions carry comments like "Batch fetch to avoid N+1 query" and contain real batching logic for article favorites. That pass was incomplete: the identical N+1 pattern still exists, unfixed, one function away, in the "is this article's author followed by me?" check that runs once per article in every article-list response, and it runs even for anonymous callers who cannot possibly have a follow relationship. Layered on top of that are an unbounded pagination parameter, exact `COUNT(*)` queries on every list request, several foreign-key columns with no supporting index, and a SQLite connection/timeout configuration that does not match SQLite's single-writer concurrency model. None of these are exotic; each is visible directly in the source and several interact to make each other worse.

### Most important findings

- **PERF-001 (P0)** — Every article in a list response (`GET /api/articles`, `GET /api/articles/feed`) triggers its own unbatched "am I following this author" database query, unbounded by page size and not short-circuited for anonymous requests.
- **PERF-002 (P1)** — `limit`/`offset` on the two list endpoints have no enforced maximum; a caller can request an arbitrarily large page.
- **PERF-003 (P1)** — Every branch of the article-list and feed endpoints computes an exact `COUNT(*)` for pagination metadata on every request, cost scaling with total table size.
- **PERF-004 (P1)** — Several foreign-key/lookup columns used on nearly every request (`article_user_models.user_model_id`, `articles.author_id`, `favorites.favorite_id`/`favorite_by_id`, `follows.following_id`/`followed_by_id`, `users.username`) have no index, turning routine per-request lookups into full table scans.
- **PERF-005 (P1)** — The SQLite connection pool is sized as if this were a client-server database (`SetMaxIdleConns(10)`, no `SetMaxOpenConns`), with no `busy_timeout` or WAL configuration anywhere, so concurrent writes will surface as immediate, unhandled `SQLITE_BUSY` errors rather than queuing.

### Highest-risk bottlenecks

The system's single hardest constraint to remove is architectural, not a bug: **one SQLite file is the sole datastore for the entire application**, and SQLite allows exactly one writer at a time by design. Every other finding in this report either adds unnecessary load onto that one constraint (PERF-001/002/003/004, all of which turn cheap point lookups into scans or multiply round trips against the same file) or fails to configure the application to behave well against that constraint (PERF-005, PERF-007). If this service is ever deployed with more than a handful of concurrent users, the write endpoints (create/update article, add comment, favorite, follow) are the first thing to show symptoms — as errors, not merely slowness, because SQLite's default behavior on write conflict is to fail immediately rather than wait. The read endpoints (`GET /api/articles`, `GET /api/articles/feed`) are the second-highest risk: they are almost certainly the highest-traffic routes in a RealWorld-shaped application, and PERF-001+PERF-003+PERF-004 stack on exactly those two routes.

### Major unknowns

This review is entirely static; no runtime artifact of any kind was supplied or found in the repository (see §2). The most consequential unknowns:

- **Actual concurrent write rate in any real deployment** — would change PERF-005 from a plausible risk to either a non-issue (if writes are effectively serial) or a confirmed, frequently-occurring outage (if not). Currently capped at `Medium` confidence for exactly this reason.
- **Row counts and growth rate for `articles`, `users`, `favorites`, `follows`, `article_user_models`** — would change PERF-002/003/004 from "will eventually matter" to either "already measurable" or "not yet worth prioritizing."
- **Whether this repository is ever deployed as a real, traffic-serving backend, versus used only as a teaching/starter reference** — its own documentation (`readme.md`, `BACKEND_INSTRUCTIONS.md`) frames it as a RealWorld example app with no stated production traffic target. If it is only ever run by one developer against one browser tab, most of these findings are moot; the review proceeds on the stated ask (an independent performance review as if for a production service) while flagging this honestly.

---

## 2. Scope and method

**Reviewed:** The entire repository — every non-test, non-vendor `.go` file: `hello.go`, `common/{database,utils}.go`, `users/{models,routers,serializers,middlewares,validators}.go`, `articles/{models,routers,serializers,validators}.go` — plus `go.mod`/`go.sum`, `.env.example`, `AGENTS.md`, `readme.md`, `BACKEND_INSTRUCTIONS.md`, `.github/workflows/ci.yml`, `.golangci.yml`, `api/Conduit.postman_collection.json` (skimmed for endpoint shape only), and `common/test_helpers.go`.

**Not reviewed:** Test-file assertions (`*_unit_test.go`) beyond confirming they exist and what they set up — they establish correctness expectations, not performance ones, and reading them would not have changed any finding here. `FRONTEND_INSTRUCTIONS.md`/`MOBILE_INSTRUCTIONS.md`/`logo.png`/`LICENSE` are out of backend scope. `data/.gitkeep` is a placeholder with no content.

**Evidence available:** Uninstrumented. There is no metrics endpoint, no tracing, no benchmark file, no load-test script, and no committed query plan or profile anywhere in the repository. The one partial exception: GORM's default logger (`common/database.go:57`, no explicit `Logger` configured) will print queries slower than 200ms and all errors to stdout at runtime — a real but unaggregated, unexported, easy-to-miss signal, not a substitute for instrumentation. This caps every finding below in this review at `Confirmed` — nothing here can reach that grade without a runtime artifact the user would need to supply.

**Ranking method:** Structural signals only. No runtime data (traces, latency metrics, slow-query logs, load-test results) was available, so paths are ranked by the structural signals in `methodology/critical-paths.md` (unbounded result sets, per-item query count, shared-resource contention) rather than measured duration.

**Reference depth:** Go, Gin, and SQLite all carry `deep` support in this skill's registry — dedicated technology references exist for all three and were used throughout (`technology/go.md`, `technology/rest.md`, `technology/sqlite.md`). No cache, message broker, or other datastore is present, and no container/orchestration configuration exists in the repository, so those layers are silently omitted below rather than stubbed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All application source files (100% of non-test, non-vendor `.go` files) were read in full |
| **Critical paths** | 18 / 18 HTTP routes registered in `hello.go` traced end to end (handler → data access → SQLite) |
| **Technology support** | Go: Deep · Gin/REST: Deep · SQLite: Deep · GORM query patterns: covered via `application/data-access.md` (framework-agnostic) |
| **Runtime evidence** | None supplied, none found in the repository |
| **Overall review confidence** | **Medium** |

**Review confidence is not finding confidence.** Several findings below are individually `High` confidence — they follow unambiguously from reading the code (missing index tags, absent pool/timeout configuration, a route-registration order bug). The review's own confidence is `Medium`, not `High`, because the *magnitude* of impact for the workload-dependent findings (PERF-001, PERF-002, PERF-003, PERF-005) cannot be established without knowing actual traffic and data volume, which this review does not have.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Whether SQLite write-contention errors (PERF-005) actually occur in a real deployment | No evidence exists — no logs, metrics, or incident history were supplied | Deploy behind a small load test issuing concurrent writes (e.g., `k6` or `hey` hitting `POST /api/articles/:slug/comments` from several virtual users) and count `SQLITE_BUSY`/500 responses |
| Current row counts for `articles`, `users`, `favorites`, `follows` | No evidence exists — this is a fresh/example database with no production data supplied | Query `SELECT COUNT(*)` against a real deployment's `data/gorm.db`, or ask the operator |
| Actual production request rate for `GET /api/articles` and `GET /api/articles/feed` | No evidence exists — no access logs or metrics were supplied | Add per-route request-count metrics (see §9 instrumentation gap) and observe for a representative period |
| Whether this service is deployed at all outside local/CI use | The repository's own docs frame it as a teaching reference | Ask the user directly; out of scope for a static code review to determine |

---

## 3. Architecture overview

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Language/runtime | Go | `go.mod` requires 1.21+ (deployed toolchain version not determinable from the repo) | Deep | Single compiled binary, goroutine-per-request via `net/http` |
| Web framework | Gin (`gin-gonic/gin`) | v1.10.0 | Deep | Routing, middleware, JSON binding |
| ORM | GORM (`gorm.io/gorm`) | v1.25.12 (this is GORM's own v2 module line) | Deep (framework-agnostic `application/data-access.md`) | All data access |
| Datastore | SQLite via `gorm.io/driver/sqlite` → `mattn/go-sqlite3` (cgo) | driver v1.5.7 / go-sqlite3 v1.14.22 | Deep | Sole datastore for the entire application |
| Auth | JWT (`golang-jwt/jwt/v5`) | v5.2.1 | — | Bearer-token auth, HMAC-signed, checked in `AuthMiddleware` |
| Password hashing | `golang.org/x/crypto/bcrypt`, `bcrypt.DefaultCost` | v0.32.0 | — | Used correctly; noted only in passing (§6.7) |

**Deployment model:** A single long-lived process (`go run hello.go` / a compiled binary), started via `r.Run(":"+port)` with no process manager, container, or orchestration configuration found anywhere in the repository (no `Dockerfile`, no k8s manifests, no `Procfile`). Infrastructure and Distributed-communication layers are therefore absent and are omitted from this report rather than stubbed, per the skill's layer-gating rule.

**Entry points:** 18 HTTP routes registered in `hello.go:41-56` across three router groups (`users`, `articles` anonymous+authenticated, `profiles`), plus a static `/api/ping` health check.

**Shared resources:**
- **The SQLite file itself** (`common.GetDBPath()`, default `./data/gorm.db`) — the single most important shared resource in this system. Every request of every kind ultimately serializes against this one file's single-writer lock.
- **The `*sql.DB` connection pool** wrapping that file (`common/database.go:49-69`), shared across every goroutine handling every request.
- **`common.DB`**, a package-level global `*gorm.DB`, read by every handler via `common.GetDB()`. Safe for concurrent use (GORM v2's chain methods clone rather than mutate the shared instance), so this is not itself a finding — noted here because it is where the shared pool above is reached from.

---

## 4. Workload model

**Known** (cited — from repository files)

- A single embedded SQLite file backs 100% of the application's persistent state; no other datastore, cache, or broker is declared. — source: `go.mod`, `common/database.go:22-27,49-69`
- No enforced maximum exists on the `limit`/`offset` query parameters accepted by `GET /api/articles` and `GET /api/articles/feed`. — source: `articles/models.go:182-190`, `articles/models.go:271-278`
- No `index` tag exists on `ArticleUserModel.UserModelID`, `ArticleModel.AuthorID`, `FavoriteModel.FavoriteID`/`FavoriteByID`, `CommentModel.ArticleID`/`AuthorID`, `FollowModel.FollowingID`/`FollowedByID`, or `UserModel.Username`. — source: `articles/models.go:11-52`, `users/models.go:16-42`
- No `SetMaxOpenConns`, `busy_timeout`, or `journal_mode`/WAL configuration exists anywhere in the repository. — source: repository-wide search of `common/database.go` and `.env.example`; no matches found
- No metrics, tracing, benchmark, or load-test file exists anywhere in the repository. — source: full file listing of the repository
- The repository's own documentation describes this as a RealWorld-spec teaching/reference implementation with no stated production traffic target. — source: `readme.md:9-12`, `BACKEND_INSTRUCTIONS.md:5-7`

**Assumed** (stated and unverified — these drive the confidence caps below)

- If deployed as a real backend rather than purely a local teaching reference, the service would receive requests from more than one concurrent client at a time, including concurrent write requests. — affects: PERF-005
- The `articles`, `users`, `favorites`, and `follows` tables are expected to grow over the service's lifetime rather than remain permanently tiny. — affects: PERF-002, PERF-003, PERF-004
- `GET /api/articles` and `GET /api/articles/feed` are called at meaningfully higher frequency than the write endpoints, consistent with a content-listing product shape (this is the RealWorld/Medium-clone spec's own intended usage pattern). — affects: the relative ranking of PERF-001/002/003 above the write-path findings

**Unknown** (would change conclusions if answered)

- Actual request rate at peak for any endpoint.
- Actual or expected row counts for `articles`, `users`, `favorites`, `follows`.
- Number of application instances/processes that would run concurrently in a real deployment.
- Any stated latency target or SLO.
- Whether this codebase is actually deployed as a traffic-serving service anywhere.

**Derived** (computed from the entries above — inputs shown)

- A single `GET /api/articles` request with no query parameters issues at least 1 (list query) + 1 (COUNT query, PERF-003) + up to 20 (one `isFollowing` check per returned article, PERF-001 — 20 is the hard-coded fallback page size at `articles/models.go:187-190`) = **up to 22 SQL round trips for one HTTP request**, before any preload queries for `Author.UserModel`/`Tags` are counted. If a caller instead sends `?limit=1000` (PERF-002 shows nothing rejects this), the same request issues on the order of **1,002 round trips** — this is a derivation from code structure, not a measurement.
- Every request to `GET /api/user`, `PUT /api/user`, `POST /api/profiles/:username/follow`, `DELETE /api/profiles/:username/follow`, and every `/api/articles` write route runs `AuthMiddleware` **twice** rather than once, because of the router-group registration order in `hello.go:41-52` — this is derived from tracing Gin's `Group()`/`Use()` semantics against the exact call sequence in that file, not a runtime measurement (PERF-006).

**Measured** (from a real runtime artifact, cited)

- None. No benchmark, trace, profile, or load-test artifact exists in this repository or was supplied for this review.

### Questions that would change the ranking

| # | Question | What it would change |
|:--|:--|:--|
| 1 | Does this service ever run with more than one concurrent writer (i.e., is it deployed at all, and to more than a single interactive user)? | Would move PERF-005 from `Medium` to `Confirmed` confidence if concurrent-write errors are observed, or let it be closed as a non-issue if writes are provably serial in practice. |
| 2 | What are today's row counts for `articles`, `favorites`, and `follows`? | A few hundred rows makes PERF-003/PERF-004 currently cheap in absolute terms (though still worth fixing before it isn't); tens of thousands changes their severity from "will eventually matter" to "matters now." |
| 3 | What request rate does `GET /api/articles` see at peak? | Determines how many times per second PERF-001's per-item query multiplication actually fires, which is the single biggest lever on how urgently it needs fixing. |
| 4 | Is there a reverse proxy or load balancer in front of this service in any real deployment? | Would change PERF-007's severity — an upstream proxy with its own timeouts substantially mitigates the missing server-level timeouts. |

---

## 5. Critical path analysis

Ranked by structural signals only (no runtime data available).

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` (`ArticleList`) | yes | 1 list + 1 count + N `isFollowing` (N = page size) + preloads | **No** — `limit`/`offset` unclamped | No | PERF-001, PERF-002, PERF-003, PERF-004 all stack here |
| 2 | `GET /api/articles/feed` (`ArticleFeed`) | yes | same shape as #1, plus a followings lookup | **No** | No | Same stack as #1; only reachable by authenticated users |
| 3 | `POST /api/articles/:slug/comments`, `POST/PUT/DELETE /api/articles/:slug`, favorite/unfavorite | yes | 4-10 queries per request (see PERF-008), all against the single SQLite writer | Bounded (single article) | No | PERF-005, PERF-006, PERF-008 stack here |
| 4 | `GET /api/articles/:slug/comments` (`ArticleCommentList`) | yes | 1 query, unbounded result | **No** — no `limit` param accepted at all | No | PERF-009 |
| 5 | `POST /api/users/login`, `POST /api/users` | yes | 1-2 queries + bcrypt hash/compare (CPU) | Bounded | No | bcrypt cost is appropriate; not a finding (§6.7) |
| 6 | `GET /api/user`, `GET /api/profiles/:username` | yes | 1-2 queries | Bounded | No | PERF-006 doubles the auth-check query for `/api/user` |

**Amplification points:**
- `GET /api/articles`: per-item `isFollowing` query count is proportional to result size, and result size itself has no cap (PERF-001 × PERF-002).
- Every list-endpoint query above also multiplies against PERF-004's missing indexes: an O(n) per-item cost becomes an O(n) *scan* per item instead of an O(log n) *index lookup* per item.

**Paths deliberately not analyzed in depth, and why:**
- `GET /api/ping` — trivial static handler, no data access, not worth the reader's time.
- `POST /api/users` / `POST /api/users/login` — single-row lookups plus bcrypt, already at the cheapest available shape for what they do; bcrypt's CPU cost is the point of using it, not a defect (see §6.7 and "Considered and not reported").

---

## 6. Layer analysis

### 6.1 Application

The application layer is thin — handlers in `articles/routers.go` and `users/routers.go` mostly bind input, call one or two model-layer functions, and serialize. The main application-layer findings are PERF-006 (duplicate middleware) and PERF-008 (redundant re-resolution of the caller's identity within a single request), both concrete instances of `work-and-algorithms.md §2`'s "duplicate work within a request."

The concurrency model is unremarkable and not itself a finding: Go's `net/http` (via Gin) gives one goroutine per request, so a slow request does not stall others the way it would on a single-threaded event loop. Nothing in the codebase spawns unbounded goroutines or leaves work unbounded in memory.

### 6.2 API

Both list endpoints (`GET /api/articles`, `GET /api/articles/feed`) accept caller-supplied `limit`/`offset` with no enforced maximum (PERF-002), and the comment-list endpoint accepts no pagination parameters at all, returning every comment on an article unconditionally (PERF-009). No request body size limit, no rate limiting, and no server-level timeout configuration exist anywhere (PERF-007). Middleware ordering has a genuine bug that runs the same authentication check twice on several routes (PERF-006).

### 6.3 Data access and datastore

This is where the review's highest-value findings live. `application/data-access.md`'s central question — "how many round trips does this path issue, and how does that scale with data?" — is answered badly for the two list endpoints (PERF-001, PERF-003) and answered with no bound at all for pagination (PERF-002). The schema itself is missing indexes on columns that are queried on nearly every request (PERF-004). SQLite specifically is being treated like a client-server database when it is architecturally a single-writer embedded library (PERF-005) — this is the single most consequential technology-specific finding in the review, because it is the one place a purely code-level fix (fewer queries, better indexes) cannot fully resolve the underlying constraint; the fix has to acknowledge SQLite's concurrency model directly.

Transaction scope is mostly fine — the one soft finding is that `FindManyArticle` and `GetArticleFeed` wrap pure reads in explicit `db.Begin()`/`tx.Commit()` transactions with no write inside them (PERF-010, minor).

### 6.5 Distributed communication

Absent — this service makes no calls to other services, brokers, or queues. Omitted per the layer-gating rule.

### 6.6 Infrastructure

Absent — no container, orchestration, or serverless configuration exists in the repository. Omitted per the layer-gating rule.

### 6.7 Observability

**Uninstrumented**, with one partial exception already noted in §2: GORM's default logger will print queries over 200ms and all errors to stdout, but nothing aggregates, exports, or alerts on this. There is no `/metrics` endpoint, no tracing, no per-route latency measurement, and no query-count assertion in the test suite that would catch a regression like PERF-001 if it were reintroduced after a fix. This is not scored as its own `PERF-` finding — the methodology treats an observability gap as a ceiling on confidence and a top item in the validation plan (§9), not as a scored performance defect on its own — but it is the reason nothing in this report can be graded above `High` confidence, and why "add basic per-route and per-query instrumentation" is the first line of the action plan in §8.

One item examined and *not* flagged: `setPassword` (`users/models.go:57-66`) uses `bcrypt.DefaultCost`, which is deliberately CPU-expensive — that is bcrypt's entire purpose, and this is the correct, unremarkable choice. Flagging it as a performance defect would be exactly the kind of cargo-cult reasoning this methodology is built to avoid; see "Considered and not reported" below.

---

## 7. Findings

### PERF-001 — Unbatched per-article "is this author followed?" query in every article-list response

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | data-access |
| **Location** | `users/models.go:121-129` (`isFollowing`), `articles/serializers.go:116-141` (`ArticlesSerializer.Response`), `users/serializers.go:24-38` (`ProfileSerializer.Response`) |
| **Tags** | scalability-risk |

**Problem**
Every article returned by `GET /api/articles` or `GET /api/articles/feed` triggers a separate, unbatched SQL query to check whether the requesting user follows that article's author — and, unlike the sibling "is this article favorited" check in the same code path, this one is not short-circuited for anonymous callers, so it fires even when the answer can only ever be "no."

**Performance principle**
Repeated work: one query per element of an already-fetched collection (the canonical N+1 shape), issued from inside a serializer where it is easy to miss because it does not appear in the handler.

**Evidence**
- `articles/serializers.go:116-141` (`ArticlesSerializer.Response`) loops over every article in the page and calls `serializer.ResponseWithPreloaded(...)` for each.
- `articles/serializers.go:92-114` (`ResponseWithPreloaded`) calls `authorSerializer.Response()` for every article.
- `articles/serializers.go:33-41` (`ArticleUserSerializer.Response`) delegates to `users.ProfileSerializer{...}.Response()`.
- `users/serializers.go:24-38` (`ProfileSerializer.Response`), line 35: `Following: myUserModel.isFollowing(self.UserModel)` — one call per article passed through this chain.
- `users/models.go:121-129` (`isFollowing`): `db.Where(FollowModel{FollowingID: v.ID, FollowedByID: u.ID}).First(&follow)` — a full query, executed unconditionally, with no `u.ID == 0` short-circuit.
- Contrast: `articles/models.go:112-126` (`BatchGetFavoriteStatus`) explicitly checks `if len(articleIDs) == 0 || userID == 0 { return make(map[uint]bool) }` before ever touching the database, and `articles/serializers.go:128-132` batches the equivalent favorite-status lookup into two queries total for the whole page. The follow-check received no equivalent treatment.
- No runtime evidence (query log, APM span count) was available; this is a static code-reading finding.

**Impact**
- Position: critical path (the two primary read endpoints of the application).
- Frequency: per-item — once per article in the returned page, inside a single request.
- Growth: O(n) in page size per request, and n itself is uncapped (see PERF-002) — so a single request's query count is bounded only by what the caller asks for, not by anything the server enforces. Combined with PERF-004 (no index on `follows.following_id`/`followed_by_id`), each of those n queries is itself an O(m) scan of the `follows` table (m = total follow relationships), making the true cost of one list request O(n·m) rather than O(n).
- Blast radius: service — every extra query is a round trip against the single shared SQLite connection/file that every other endpoint also depends on.
- Derivation: a default (`limit` omitted) request to `GET /api/articles` issues up to 22 total SQL round trips (1 list + 1 count + 20 follow-checks), shown in §4 "Derived."

**Conditions**
This matters in proportion to (a) how many articles a typical list page returns and how often those endpoints are called, and (b) how large the `follows` table grows. Both are unknown for this deployment (§4). Even at today's presumably small scale, the finding is `High` confidence because the mechanism — one query per row, unconditionally, with a documented easier fix sitting right next to it — is unambiguous from the code; only the magnitude of the consequence is workload-dependent.

**Counter-evidence**
Searched for a batching helper analogous to `BatchGetFavoriteStatus` covering follow status; none exists. Searched for a cache or memoization layer in front of `isFollowing`; none exists (no cache dependency anywhere in `go.mod`). Searched for an anonymous-user short-circuit inside `isFollowing` or its call chain (`ProfileSerializer.Response`, `ArticleUserSerializer.Response`); none exists — confirmed by reading all three call sites. Effect: `no-effect` — nothing found that reduces this finding's severity or confidence.

**Why this might not matter**
If this deployment's article-list pages are, in practice, small (a handful of articles) and rarely called by authenticated users who follow many other users, the absolute query count stays small and the fix is a pure hygiene improvement rather than an urgent one. The RealWorld spec's own default page size of 20 and the presence of a dedicated `/feed` endpoint (implying a following-based product shape) argue against this being negligible, but it is not `Confirmed` without traffic data.

**Recommendation**
Batch the follow-status lookup the same way `BatchGetFavoriteStatus` already batches favorite status: collect the distinct author `ArticleUserModel` IDs for the page, issue one query (`SELECT following_id FROM follows WHERE followed_by_id = ? AND following_id IN (...)`), build an in-memory set, and short-circuit the whole thing to "not following anyone" when the caller is anonymous — exactly mirroring the pattern already proven correct for favorites two functions away.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — batch the follow-status query, mirroring `BatchGetFavoriteStatus` | **preferred** | Removes the repeated work entirely rather than concealing it; the pattern, helper shape, and call site are already proven in this exact codebase for the structurally identical favorites problem |
| B — cache follow relationships (e.g., per-user in-process cache) | | Papers over the query count without removing it, adds invalidation complexity for a relationship that changes on every follow/unfollow, and this service has no cache dependency today — introducing one for this alone is disproportionate |
| C — denormalize a `following` flag onto `ArticleUserModel` | | Read-optimizes at the cost of write complexity and consistency risk on every follow/unfollow; not justified without first establishing that the batched-query approach (A) is insufficient |

**Trade-offs**
The batched version requires one extra intermediate map (bounded by page size, i.e., small) and roughly the same code shape already present for favorites — no new dependency, no new failure mode. The only real cost is the small amount of engineering time to mirror an existing pattern.

**Validation**
- Baseline: count SQL statements issued by one `GET /api/articles` request using GORM's existing query logger (`common/database.go`'s `Init()` currently uses the zero-value `&gorm.Config{}`; temporarily set `Logger: logger.Default.LogMode(logger.Info)` as `TestDBInit` already does, or add a query-count assertion in a test). — safe-on-production: no (requires a config change); safe in staging/local, yes.
- Change: replace the per-article `isFollowing` call with a single batched lookup plus an anonymous short-circuit.
- Measurement: query count for the same request, same data.
- Expectation: query count for a default (`limit` omitted) `GET /api/articles` request falls from up to 22 to a small constant number independent of page size (list + count + 1 batched favorites query + 1 batched follow query + preload queries) — a direct prediction from the code change, not a latency guess.
- Falsifier: if query count does not drop after batching, the batching was implemented incorrectly (e.g., still issuing one query per author instead of one for the page).
- Guard: add a test asserting the total query count for `GET /api/articles` with a fixed fixture (N articles, N distinct authors) stays constant as N grows — the single most effective regression guard against this exact class of bug recurring, per `methodology/validation.md §6`.

---

### PERF-002 — No enforced maximum on `limit`/`offset` for article listing and feed

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `articles/models.go:182-190` (`FindManyArticle`), `articles/models.go:271-278` (`GetArticleFeed`), `articles/models.go:164-168` (`getComments`, related) |
| **Tags** | quick-win, scalability-risk |

**Problem**
`limit` and `offset` are parsed from the query string with `strconv.Atoi` and fall back to a default of 20 only on a parse *error* — any valid integer the caller supplies, however large, is passed straight through to GORM's `Offset()`/`Limit()`. There is no upper bound.

**Performance principle**
Unbounded work: a default page size is not a bound if the caller can override it. The largest possible response this endpoint can produce is set entirely by the caller, not the server.

**Evidence**
- `articles/models.go:182-190`: `limit_int, errLimit := strconv.Atoi(limit); if errLimit != nil { limit_int = 20 }` — no `else if limit_int > someMax` clamp anywhere in the function.
- `articles/models.go:271-278`: identical parsing pattern in `GetArticleFeed`, same absence of a clamp.
- `articles/routers.go:54-68` (`ArticleList`) and `:70-86` (`ArticleFeed`) pass the raw query-string values straight through with no validation.
- Related, weaker instance of the same pattern: `articles/models.go:164-168` (`getComments`) and `articles/routers.go:228-242` (`ArticleCommentList`) accept **no** pagination parameters at all — every comment on an article is returned unconditionally on every call (tracked separately in the summary table as PERF-009, since its current urgency is lower — comment counts per article are typically far smaller than article counts, but the underlying pattern is the same).

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) where n is the caller-supplied `limit`, unbounded — in the worst case n approaches total table size.
- Blast radius: service — a single oversized request both (a) materializes and JSON-serializes a correspondingly large result set in the request goroutine's memory, and (b) multiplies PERF-001's per-item query count by the same n, turning one request into a load event against the shared SQLite connection.
- This directly amplifies PERF-001: capping `limit` here is what bounds PERF-001's worst case, even after PERF-001's own fix (batching turns N queries into 1, but the *rows scanned and joined* inside that one batched query still scale with n).

**Conditions**
Assumes the `articles` table grows beyond a trivial size and that this endpoint is reachable by callers who are not trusted to self-limit their own requests (true for any public API, including this one — both list endpoints are registered under the anonymous-access group in `hello.go:44-46`). No workload data confirms current traffic volume; the finding's existence does not depend on that, but its urgency does.

**Counter-evidence**
Searched for any request-size limiting middleware, WAF, or reverse-proxy configuration anywhere in the repository; none exists (no `Dockerfile`, no proxy config, no rate-limiting middleware registered in `hello.go`). Searched for a maximum enforced elsewhere in the call chain (e.g., in the router before calling `FindManyArticle`); none exists. Effect: `no-effect`.

**Why this might not matter**
If this service is only ever exercised through its own trusted frontend (which by convention respects the RealWorld spec's pagination UI), the unbounded path may never actually be hit by anything other than a deliberate test. That does not make it safe — it makes it a latent risk that turns into a real one the moment any other client (a script, a scraper, an adversarial user) calls the API directly, which a public REST API by definition allows.

**Recommendation**
Clamp `limit_int` to a small maximum (e.g., 100) immediately after parsing, in both `FindManyArticle` and `GetArticleFeed`, and reject or clamp negative values for both `limit` and `offset`. This is a bound, not a redesign — it removes the unbounded worst case without changing normal-case behavior at all.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — clamp `limit`/`offset` to a fixed maximum in the model layer | **preferred** | Smallest possible change, fixes the unbounded worst case immediately, no behavior change for any well-behaved caller |
| B — switch to keyset (cursor) pagination | | Avoids offset-pagination's separate cost-grows-with-depth problem (`application/api.md §1`), but is a larger API-shape change with a real trade-off (loses random page access) that is not justified until a clamp alone is shown to be insufficient |

**Trade-offs**
None of substance for option A — a hard-coded or configurable maximum costs nothing at runtime and only affects callers who were already outside intended usage.

**Validation**
- Baseline: none needed — this is a missing-bound fact, not a measured-slowness claim.
- Change: add `if limit_int > 100 { limit_int = 100 }` (and equivalent for offset/negative values) at both call sites.
- Measurement: confirm via a test that `?limit=100000` returns at most 100 rows.
- Expectation: response size for a maximal-`limit` request becomes bounded and independent of table size, by construction.
- Falsifier: if a request with `limit` above the new maximum still returns more than the maximum, the clamp was not applied correctly or was bypassed by another code path.
- Guard: a test asserting the response row count never exceeds the configured maximum regardless of the requested `limit`. — safe-on-production: yes, this is a pure code and test change with no runtime diagnostic involved.

---

### PERF-003 — Exact `COUNT(*)` computed on every branch of every article-list request

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `articles/models.go:203` (tag branch), `:219` (author branch), `:245` (favorited branch), `:257` (default branch), `:299-301` (`GetArticleFeed`) |
| **Tags** | scalability-risk |

**Problem**
Every one of the five code paths through `FindManyArticle` and `GetArticleFeed` computes an exact row count (`Count(&count64)` or `Association(...).Count()`) to populate `articlesCount` in the JSON response, on every single request, regardless of the requested page size.

**Performance principle**
Counting by materializing/traversal: an exact count of a table (or of the rows matching a filter) requires the datastore to examine every matching row, which is a full or near-full scan cost paid to produce a single integer — explicitly named in `databases/relational.md §3` as a recurring, checkable anti-pattern.

**Evidence**
- `articles/models.go:257`: default (unfiltered) branch — `tx.Model(&ArticleModel{}).Count(&count64)` runs against the whole `articles` table on every unfiltered list request, which given `hello.go`'s registration is the plain `GET /api/articles` call with no query parameters — almost certainly the single most common request shape this API serves.
- `articles/models.go:203`, `:219`, `:245`: the tag/author/favorited branches each run a separate `Association(...).Count()` query before or after fetching the filtered page.
- `articles/models.go:299-301` (`GetArticleFeed`): `tx.Model(&ArticleModel{}).Where("author_id IN ?", authorIDs).Count(&count64)` — same pattern, and this query also suffers from PERF-004's missing index on `articles.author_id`.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n), n = total rows in the counted table/association, independent of the requested page size — a request for `?limit=1` pays the same count cost as `?limit=100`.
- Blast radius: service — this query competes for the same shared SQLite connection/file as every other query in the system.

**Conditions**
Assumes the `articles` table (and the relevant tag/author/favorite associations) grow past a size where a full scan is free — true of essentially any table given enough time, and this cost curve is smooth and predictable rather than a sudden cliff, which is exactly why it is easy to under-prioritize until it is already a measured problem.

**Counter-evidence**
Searched for a cached or approximate count anywhere in the codebase (a materialized counter column, a periodic count job); none exists. Searched for whether the count is only computed conditionally (e.g., only on the first page); it is not — every branch computes it unconditionally on every call. Effect: `no-effect`.

**Why this might not matter**
For a small `articles` table (hundreds to low thousands of rows), SQLite can typically satisfy a `COUNT(*)` fast enough that this is not the dominant cost of the request relative to PERF-001's per-item queries — this finding's absolute cost today is very plausibly smaller than PERF-001's, and the two should not be conflated into one urgency. It becomes the dominant cost only as the table grows well past PERF-001's post-fix cost floor.

**Recommendation**
The product-level question ("does the UI need an exact total, or would 'has more' / an approximate count suffice?") is a real decision, not purely technical (`application/api.md §1`, `databases/relational.md §3` both flag this explicitly as partly a product call). The lowest-risk technical fix that preserves current API behavior exactly is to keep the exact count but recognize it is not free; if the RealWorld frontend does not strictly require an exact total on every page, switching to a "has more" boolean (checking for `limit+1` rows rather than counting the whole table) removes the scan entirely for the common case of paging forward.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — replace exact count with a cheap "has more" check (fetch `limit+1` rows) | **preferred** | Removes the full-table-scan cost entirely for the dominant use case (browsing forward through pages); requires a frontend/API-contract conversation since it changes `articlesCount`'s meaning |
| B — keep exact count, but only recompute it periodically/cache it | | Avoids the API contract change, but a cached count for a frequently-written table (articles are created/deleted) either goes stale quickly or requires invalidation machinery disproportionate to the problem |
| C — leave as is | | Defensible if `articlesCount` is a hard product requirement and current/projected table sizes stay small; this review cannot determine that from code alone |

**Trade-offs**
Option A changes the exact semantics of `articlesCount` in the API response (from "total matching rows" to "at least this many, possibly more" or a boolean) — a real, visible API contract change that needs sign-off, not a pure internal optimization. This is why it is not simply recommended outright as a "fix."

**Validation**
- Baseline: `EXPLAIN QUERY PLAN` on the count query against the current `data/gorm.db` — safe-on-production (plan-only, no execution of a statement with side effects).
- Change: whichever alternative is chosen.
- Measurement: query count and, if `EXPLAIN QUERY PLAN` is available before/after, rows-examined for the count query specifically.
- Expectation: if switching to a "has more" check, the count-side query cost becomes bounded by `limit+1` rather than total table size — a direct structural prediction.
- Falsifier: if the "has more" check still scans the full table (e.g., due to a missing index on the filter columns, see PERF-004), the fix did not address the actual mechanism.
- Guard: none beyond code review — this is a one-time structural decision, not a regression-prone pattern like PERF-001.

---

### PERF-004 — Missing indexes on foreign-key/lookup columns used by nearly every request

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `articles/models.go:11-52` (`ArticleModel.AuthorID`, `ArticleUserModel.UserModelID`, `FavoriteModel.FavoriteID`/`FavoriteByID`, `CommentModel.ArticleID`/`AuthorID`), `users/models.go:16-42` (`UserModel.Username`, `FollowModel.FollowingID`/`FollowedByID`) |
| **Tags** | scalability-risk |

**Problem**
Several columns that are filtered on constantly — most importantly `ArticleUserModel.UserModelID` (queried inside `GetArticleUserModel`, which is called on nearly every article-related request) and `FollowModel.FollowingID`/`FollowedByID` (queried inside `isFollowing`, the subject of PERF-001) — carry no `gorm:"index"` tag. The application relies entirely on `db.AutoMigrate()` (`hello.go:15-22`, `users/models.go:45-50`), which only creates the indexes explicitly declared in struct tags; nothing else adds them.

**Performance principle**
An unindexed predicate on a growing table forces a full scan for every lookup — the datastore is asked the same question repeatedly (`databases/universal.md §3`: "a predicate on a field with no supporting index and high selectivity" is exactly the signal an index is justified) and pays linear cost every time instead of logarithmic.

**Evidence**
- `articles/models.go:23-29` — `ArticleUserModel` struct: `UserModelID uint` has no index tag, yet `articles/models.go:54-65` (`GetArticleUserModel`) filters on exactly this column via `db.Where(&ArticleUserModel{UserModelID: userModel.ID})`, and this function is called from `ArticleCreate`'s validator, `ArticleUpdate`, `ArticleDelete`, `ArticleFavorite`, `ArticleUnfavorite`, `ArticleCommentCreate`, and once per page in `ArticlesSerializer.Response` — i.e., on the majority of article-related request paths.
- `articles/models.go:11-21` — `ArticleModel.AuthorID uint` has no index tag, but is filtered on in `GetArticleFeed` (`articles/models.go:300,302`: `Where("author_id IN ?", authorIDs)`).
- `articles/models.go:31-37` — `FavoriteModel.FavoriteID`/`FavoriteByID` have no index tags, but are filtered/grouped on in `BatchGetFavoriteCounts` (`:98-102`), `BatchGetFavoriteStatus` (`:118-119`), `isFavoriteBy` (`:79-83`) — all on the article-list and single-article response paths.
- `users/models.go:36-42` — `FollowModel.FollowingID`/`FollowedByID` have no index tags, but are the exact predicate columns in `isFollowing` (`users/models.go:124-127`), the mechanism at the center of PERF-001.
- `users/models.go:16-23` — `UserModel.Username` has no index tag (only `Email` has `uniqueIndex`), yet `ProfileRetrieve` (`users/routers.go:32-41`) and the author-filter branch of `FindManyArticle` (`articles/models.go:213-215`) both filter on it directly.
- `articles/models.go:45-52` — `CommentModel.ArticleID`/`AuthorID` have no index tags, filtered in `FindOneComment` and `DeleteCommentModel`.

**Impact**
- Position: critical path — `GetArticleUserModel` alone sits on nearly every article endpoint.
- Frequency: per request, and per-item wherever it sits inside PERF-001's loop.
- Growth: O(n) per lookup, n = row count of the affected table, versus O(log n) if indexed.
- Blast radius: system-wide — `GetArticleUserModel` and `isFollowing`/`isFavoriteBy` are shared helper functions reached from most of the request surface, not one isolated endpoint.
- This is the multiplier that turns PERF-001's already-uncapped per-item query count into a per-item *scan*, and is why PERF-001 is scored `Critical` rather than merely `High` — see the compounding note there.

**Conditions**
Assumes the affected tables (`article_user_models`, `follows`, `favorites`, `users`, `comments`) grow past the point where SQLite's own page cache can absorb a full scan cheaply — true for any of these tables given real usage over time, and, per `databases/universal.md §5`, this degradation is not smooth: it is fine until the working set outgrows available cache, then degrades sharply.

**Counter-evidence**
Searched for a manual migration file that might add these indexes outside the GORM struct tags (SQLite has no migration-versioning tool wired into this codebase, and `AutoMigrate` is the only schema-management call found anywhere); none exists. Searched for whether any of these tables might be structurally guaranteed small (e.g., capped by design); none is — all four grow with normal user activity (every article, comment, favorite, and follow adds a row). Effect: `no-effect`.

**Why this might not matter**
At small table sizes (the likely current state of a fresh/example deployment), SQLite's own page cache and the OS filesystem cache make a full scan of a few hundred or even a few thousand rows fast enough not to be noticeable — this finding's cost curve is gradual, not a cliff, and it is entirely plausible that today it costs single-digit milliseconds per call. It is flagged now because adding an index is cheap and because it directly multiplies PERF-001's severity; it would be dishonest to claim it is *currently* measured as slow.

**Recommendation**
Add `gorm:"index"` tags to `ArticleUserModel.UserModelID`, `ArticleModel.AuthorID`, `FavoriteModel.FavoriteID`, `FavoriteModel.FavoriteByID`, `CommentModel.ArticleID`, `CommentModel.AuthorID`, `FollowModel.FollowingID`, `FollowModel.FollowedByID`, and `UserModel.Username`, and let `AutoMigrate` create them (SQLite index creation is fast at any size this application is likely to reach before the next deploy). A composite index on `(FavoriteByID, FavoriteID)` and `(FollowedByID, FollowingID)` specifically would also directly serve `isFavoriteBy`/`isFollowing`'s two-column predicates.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — add the missing single/composite indexes via struct tags | **preferred** | Directly addresses the mechanism, uses the schema-management tooling already in place (`AutoMigrate`), no new dependency |
| B — do nothing until a slow-query log shows a problem | | Defensible only if the review had evidence table sizes are and will remain tiny; this review has no such evidence, and per `databases/universal.md §5` the degradation is a step function, not a warning-shot curve — by the time it's visibly slow, the fix is the same one available now |

**Trade-offs**
Every index added here costs write time on every insert/update/delete touching that column, plus storage. Given these are foreign-key columns on tables that are written far less often than they are read (a comment/favorite/follow is created once and read on every subsequent list view of the related article/profile), this trade strongly favors adding the index — but it is a real cost, not a free improvement, and is stated here per `databases/universal.md §3`'s explicit requirement.

**Validation**
- Baseline: `EXPLAIN QUERY PLAN` against `GetArticleUserModel`'s query and `isFollowing`'s query on the current database file — safe-on-production.
- Change: add the index tags listed above; let `AutoMigrate` apply them on next startup.
- Measurement: re-run the same `EXPLAIN QUERY PLAN` calls; confirm they now report an index scan (`SEARCH ... USING INDEX ...`) rather than a full table scan (`SCAN ...`).
- Expectation: plan changes from `SCAN` to `SEARCH ... USING INDEX` — a direct, checkable prediction, not a latency guess.
- Falsifier: if the plan still shows `SCAN` after the index is added, the index does not match the query's predicate shape (e.g., wrong column order in a composite index) and needs revisiting.
- Guard: none needed beyond the one-time plan check; index existence itself is now enforced by `AutoMigrate` running on every startup.

---

### PERF-005 — SQLite connection/timeout configuration mismatched to SQLite's single-writer model

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `common/database.go:49-69` (`Init`), `.env.example` (no DSN pragma parameters) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
`Init()` calls `sqlDB.SetMaxIdleConns(10)` but never calls `SetMaxOpenConns`, leaving it at Go's default of unlimited. No `busy_timeout` or `journal_mode=WAL` is configured anywhere — not in the DSN, not via a `PRAGMA` statement, not in `.env.example`. SQLite allows exactly one writer at a time by design (not a tunable setting), and its default `busy_timeout` is zero: a second connection that tries to write while another write is in progress receives `SQLITE_BUSY` immediately rather than waiting.

**Performance principle**
A bounded shared resource (here, SQLite's single-writer lock) configured as if it were unbounded or as if contention would queue rather than fail. This is the connection-pool sizing arithmetic from `application/connection-pools.md`, applied to a resource where "just add more connections" is not merely unhelpful but actively counterproductive.

**Evidence**
- `common/database.go:57`: `db, err := gorm.Open(sqlite.Open(dbPath), &gorm.Config{})` — `dbPath` (from `.env.example:6`, `DB_PATH=./data/gorm.db`) carries no query parameters of any kind; confirmed by reading `GetDBPath()` (`common/database.go:21-27`), which returns a bare filesystem path.
- `common/database.go:64-66`: `sqlDB.SetMaxIdleConns(10)` — the only pool-sizing call in the codebase; no `SetMaxOpenConns` call exists anywhere in the repository (confirmed by a repository-wide search for `SetMaxOpenConns`, `busy_timeout`, `journal_mode`, and `WAL` — no matches outside this review's own analysis).
- Per `technology/sqlite.md §2`: "A second connection attempting to write while another write is in progress receives `SQLITE_BUSY` immediately, or waits up to `busy_timeout` (default: return the error immediately, since the default timeout is zero)... A connection-pool size chosen using the same reasoning as for a client-server engine is a checkable, SQLite-specific finding" — and, further, "a larger pool can actually increase `SQLITE_BUSY` contention by increasing how often two connections attempt to write at once," which is precisely the configuration present here (an idle pool of 10 with no open-connection cap).
- No retry/backoff logic around any write call was found in `articles/models.go` or `users/models.go` — a `SQLITE_BUSY` error under this configuration propagates straight to the HTTP response as a generic `422`/`500` (e.g., `common.NewError("database", err)` in `articles/routers.go:47,122,158,174,196,222`).

**Impact**
- Position: critical path — every write endpoint (article create/update/delete, comment create/delete, favorite/unfavorite, follow/unfollow, user register/update).
- Frequency: per request, specifically per pair of requests that happen to write concurrently.
- Growth: not data-volume-driven — this is a concurrency/collision-probability phenomenon, not a scan-cost curve, so `unknown` is the honest answer for the growth axis; what is known is that the probability of collision rises with concurrent write rate, and the pool being sized for concurrency (`SetMaxIdleConns(10)`) actively increases the number of connections that can simultaneously attempt a write.
- Blast radius: system-wide — the single SQLite file backs every table in the application; a write conflict on any table surfaces as an error to whichever request loses the race, and there is no code path that retries or queues instead.

**Conditions**
This requires more than one write request to arrive close enough in time to overlap — a genuine concurrency condition, not a data-volume one. Whether that ever happens in this deployment is exactly the unknown named in §4 as the single highest-value question this review could not answer. Per the workload-unknown rule, confidence is capped at `Medium` for this reason even though the underlying configuration mismatch itself is unambiguous from the code.

**Counter-evidence**
Searched for any retry/backoff wrapper around GORM write calls; none exists. Searched for a DSN or `PRAGMA` setting anywhere in the repository that would set `busy_timeout` above its zero default; none exists. Searched for evidence this service is only ever used by a single interactive client (which would make the finding moot in practice, though still a latent risk) — the repository's own framing as a RealWorld example app is suggestive but not conclusive either way; the API itself has no such restriction built in. Effect: `lowers-confidence` — the workload uncertainty is exactly why confidence is `Medium` rather than `High`, despite the mechanism itself being unambiguous.

**Why this might not matter**
If this instance is, in every real use, run by a single developer or a single automated test suite issuing writes serially (which is exactly how the CI workflow and local development almost certainly exercise it), this failure mode may simply never trigger in practice, ever. The finding is worth fixing regardless because the fix is cheap and the current configuration provides no protection if that assumption ever stops holding — but it should not be read as "this service is currently broken."

**Recommendation**
Two changes, both small: (1) call `sqlDB.SetMaxOpenConns(1)` — since SQLite allows only one writer regardless of pool size, capping the pool at 1 open connection removes the *possibility* of two connections racing to write, converting concurrent writes from "occasionally fails outright" into "serializes automatically," which is the actual concurrency ceiling SQLite already has; and (2) set a non-zero `busy_timeout` (e.g., via the DSN, `sqlite.Open(dbPath + "?_busy_timeout=5000")`, or an explicit `PRAGMA busy_timeout` after opening) as defense in depth for the remaining case where a read transaction briefly overlaps a write. Enabling WAL mode (`journal_mode=WAL`) is a reasonable complementary change for read/write concurrency but does not by itself fix the write-write conflict this finding is about (`technology/sqlite.md §2`: "WAL mode... does **not** allow a second concurrent writer").

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — `SetMaxOpenConns(1)` + non-zero `busy_timeout` | **preferred** | Matches the pool configuration to what SQLite actually allows, converting a hard failure into a brief wait, which is a strictly better failure mode for a service with no application-level retry logic today |
| B — add application-level retry/backoff around every write call, keep the pool unbounded | | Treats the symptom at every call site individually rather than fixing the one place the mismatch is introduced; more code, more places to get it subtly wrong |
| C — migrate off SQLite to a client-server database (e.g., Postgres) | | The eventual right answer if this service is ever expected to serve real concurrent write traffic at any meaningful scale, but is a materially larger change than this review's scope justifies recommending as the first step — see `SKILL.md` Hard Rule 5 on not recommending an infrastructure change without the evidence that justifies it |

**Trade-offs**
`SetMaxOpenConns(1)` means write throughput is now explicitly serialized in the application layer rather than implicitly racing at the SQLite layer — under genuine concurrent write load, requests will queue for that one connection rather than failing, which trades "some requests error immediately" for "some requests wait" (per `application/connection-pools.md §4`, an acquisition timeout should also be set so that wait does not become unbounded). A non-zero `busy_timeout` adds up to that many milliseconds of worst-case latency to a write that collides with another.

**Validation**
- Baseline: `PRAGMA busy_timeout` and `PRAGMA journal_mode` against the running database — both safe-on-production (`technology/sqlite.md §3`).
- Change: `SetMaxOpenConns(1)` plus a configured `busy_timeout`.
- Measurement: issue two concurrent write requests (e.g., two simultaneous `POST /api/articles/:slug/comments` calls in a test or a small local load script) before and after the change, and count `SQLITE_BUSY`/5xx responses.
- Expectation: the error rate under concurrent writes should drop to zero (both requests eventually succeed, serialized) rather than one succeeding and one failing outright — a structural prediction from the mechanism, not a latency percentage.
- Falsifier: if concurrent-write errors persist unchanged after the configuration change, the mechanism was misdiagnosed (e.g., the busy condition is coming from a held read transaction rather than pool sizing) and needs re-investigation.
- Guard: a test that issues two concurrent writes against a shared test database and asserts both succeed — this test does not exist today (`common/test_helpers.go` and the `*_unit_test.go` files use a single serial test database per run) and would be a genuine new regression guard, not a duplicate of existing coverage. — not-safe-on-production for the two-concurrent-writes test itself if run against a live deployment's real database; run it against a disposable test database only.

---

### PERF-006 — Duplicate authentication middleware execution on several routes

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `hello.go:41-52` (route-group registration order), `users/middlewares.go:30-38,43-75` (`UpdateContextUserModel`, `AuthMiddleware`) |
| **Tags** | quick-win |

**Problem**
`GET/PUT /api/user`, `POST/DELETE /api/profiles/:username/follow`, and every write route under `/api/articles` (create, update, delete, favorite, unfavorite, comment create/delete) run `AuthMiddleware` **twice** per request — once as `AuthMiddleware(false)` and once as `AuthMiddleware(true)` — because both are registered on the parent `v1` group before those child groups are created, and Gin's `Group()` snapshots the parent's middleware chain at the moment it is called.

**Performance principle**
Duplicate work within a request: the same JWT is parsed and the same user is fetched from the database twice, for no additional effect, purely as a consequence of route-registration order.

**Evidence**
- `hello.go:41-52`:
  ```
  v1 := r.Group("/api")
  users.UsersRegister(v1.Group("/users"))       // no auth middleware yet
  v1.Use(users.AuthMiddleware(false))            // v1.Handlers = [false]
  articles.ArticlesAnonymousRegister(v1.Group("/articles"))   // inherits [false]
  articles.TagsAnonymousRegister(v1.Group("/tags"))           // inherits [false]
  users.ProfileRetrieveRegister(v1.Group("/profiles"))        // inherits [false]
  v1.Use(users.AuthMiddleware(true))             // v1.Handlers = [false, true]
  users.UserRegister(v1.Group("/user"))          // inherits [false, true]
  users.ProfileRegister(v1.Group("/profiles"))   // inherits [false, true]
  articles.ArticlesRegister(v1.Group("/articles")) // inherits [false, true]
  ```
- `users/middlewares.go:43-75` (`AuthMiddleware`) always calls `UpdateContextUserModel(c, 0)` first, then re-parses the token and calls `UpdateContextUserModel(c, my_user_id)` again if valid — so each of the two middleware instances independently parses the JWT and, if a token is present, independently runs `db.First(&myUserModel, my_user_id)` (`users/middlewares.go:30-38`).
- This is a direct, mechanical trace of Gin's documented `Group()`/`Use()` behavior against the exact call sequence above — not an assumption about Gin's internals.

**Impact**
- Position: critical path (all affected routes are user-facing write or profile operations).
- Frequency: per request, on the affected routes only.
- Growth: O(1) — a constant 2x overhead, not data-dependent.
- Blast radius: endpoint — scoped to the specific affected routes, though each extra query does add a small amount of pressure to the shared SQLite connection pool under load.

**Conditions**
This is a pure code-structure fact, true regardless of workload — it fires on every single request to the affected routes, with no dependency on data volume or concurrency. It is scored `Medium` rather than higher because the *cost per occurrence* is small and bounded (one extra primary-key lookup plus one extra JWT parse), not because it is uncertain.

**Counter-evidence**
Searched for whether `AuthMiddleware(false)` short-circuits before reaching `AuthMiddleware(true)` in a way that would prevent the second execution (e.g., via `c.Abort()`); it does not — `AuthMiddleware(false)` only aborts when `auto401` is true and no token is present, which is not this middleware's configuration. Effect: `no-effect`.

**Why this might not matter**
The absolute cost — one extra JWT parse and one extra primary-key-indexed row lookup — is genuinely small in isolation, and this alone would not justify urgent action. It is worth fixing because it is a trivial, unambiguous, zero-risk change with a permanent 2x reduction on the affected routes' auth overhead, not because it is currently a measured bottleneck.

**Recommendation**
Restructure route registration so `AuthMiddleware` is applied exactly once per route: either register `/api/user`, the authenticated `/api/profiles` routes, and the authenticated `/api/articles` routes directly against a group created with `AuthMiddleware(true)` alone (not layered on top of a group that already has `AuthMiddleware(false)`), or fold both modes into a single middleware function that takes the `auto401` behavior as its only variable, applied once.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — restructure registration so each route gets exactly one `AuthMiddleware` call | **preferred** | Directly removes the duplication at its source; no behavior change for any caller since both middleware instances currently produce the same end state on the second run |
| B — make `AuthMiddleware` idempotent by checking if `my_user_model` is already set in the context and skipping re-work | | Treats the symptom rather than the registration-order cause; leaves the confusing double-registration in place for the next person to trip over |

**Trade-offs**
None — this is a pure removal of duplicated work with no functional change, verifiable by the existing test suite (`users/unit_test.go`, `articles/unit_test.go`) continuing to pass unchanged.

**Validation**
- Baseline: add a temporary counter or log line inside `AuthMiddleware` and issue one request to `GET /api/user` with a valid token; observe it fire twice today.
- Change: restructure registration per the recommendation.
- Measurement: same counter/log; confirm it now fires once per request to the same route.
- Expectation: middleware execution count on affected routes drops from 2 to 1 — a direct, structural prediction.
- Falsifier: if it still fires twice after restructuring, the registration order was not actually fixed.
- Guard: none needed beyond the fix itself; this is a one-time structural correction, not an ongoing regression risk once corrected — though a lint rule flagging `Use()` calls on a group that already has routes registered under a sibling group would be a plausible mechanical guard if this pattern recurs elsewhere.
- Safety: safe-on-production (a route-registration change deployed normally; no diagnostic risk).

---

### PERF-007 — No server-level timeouts configured on the HTTP server

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `hello.go:67` (`r.Run(":" + port)`) |
| **Tags** | quick-win, needs-measurement |

**Problem**
`r.Run(":" + port)` starts Gin's default HTTP server, which is a plain `http.Server` with no `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, or `ReadHeaderTimeout` configured. A client that connects and then sends data slowly, or never finishes sending a request, can hold that connection (and the goroutine serving it) open indefinitely.

**Performance principle**
An unbounded resource: `application/api.md §5` names server timeouts explicitly as "the server's defense against slow clients holding worker slots. Their absence is a capacity risk, not just a robustness one" — the same "no timeout = unbounded worst case" pattern `critical-paths.md`'s ranking table lists as a top structural risk signal.

**Evidence**
- `hello.go:67`: `if err := r.Run(":" + port); err != nil { ... }` — Gin's `Run()` constructs a default `http.Server` internally with no timeout fields set; confirmed by reading the call site, which passes only the address string and no `*http.Server` override.
- No reverse proxy, load balancer, or other front-end configuration exists anywhere in the repository that might otherwise be terminating slow-client connections upstream of this process (no `Dockerfile`, no ingress/nginx config found anywhere).

**Impact**
- Position: critical path in the sense that this is the entry point for every single request the service handles.
- Frequency: rare — this only manifests when a client is unusually slow or deliberately adversarial, not on ordinary traffic.
- Growth: unknown — this is not a data-volume-driven cost; it is a worst-case-per-connection exposure that does not scale with anything in this codebase's control.
- Blast radius: system-wide — Go's goroutine-per-connection model means each stalled connection costs one goroutine and its stack, and enough of them (deliberate or accidental) degrade the whole server's ability to accept and serve new connections.

**Conditions**
This requires a slow or stalled client to actually occur — either organically (poor client-side networks) or adversarially (a deliberate slow-loris-style attack). Whether either happens in this deployment is unknown, which is why confidence is capped at `Medium` despite the configuration gap itself being unambiguous from the code.

**Counter-evidence**
Searched for any timeout configuration passed to Gin or to a custom `http.Server` anywhere in the repository; none exists. Searched for an upstream reverse proxy that might already provide this protection; none was found in the repository, though one could exist in a deployment environment not captured in this repo (e.g., a managed platform's own edge timeout) — this review cannot rule that out from code alone. Effect: `lowers-confidence` — the possibility of an unseen upstream mitigation is exactly why this is not scored higher.

**Why this might not matter**
Many deployment platforms (managed PaaS offerings, an ingress controller, a CDN) impose their own connection timeouts in front of an application server regardless of what the application itself configures, which would substantially mitigate this even without a code change. This review found no evidence of such a layer in the repository, but its absence from the repo does not prove its absence from a real deployment.

**Recommendation**
Construct an explicit `http.Server` with `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, and `ReadHeaderTimeout` set to values consistent with this API's expected request shape (these are simple JSON request/response cycles with no long-lived streaming, so conservative values — a few seconds for header/read timeouts, tens of seconds for idle — are appropriate), and call `server.ListenAndServe()` with Gin's router as the handler instead of `r.Run()`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — configure explicit server timeouts in `hello.go` | **preferred** | Small, self-contained change; makes the guarantee true regardless of what any future deployment environment does or does not provide upstream |
| B — rely on an upstream reverse proxy/platform timeout | | May already be true in some deployment of this code, but is not verifiable from the repository and would leave the service unprotected if ever run directly (e.g., `go run hello.go` behind nothing, which the repo's own instructions describe as the normal way to run it locally and in CI) |

**Trade-offs**
Overly aggressive timeouts could cut off legitimately slow clients (large uploads, poor mobile networks) — the values chosen should be generous enough for this API's actual payload sizes (small JSON bodies, per `articles/validators.go`'s `max=2048` field limits) while still bounding the worst case.

**Validation**
- Baseline: none needed — this is a missing-configuration fact.
- Change: add explicit `http.Server` timeouts as described.
- Measurement: a test client that opens a connection and sends data byte-by-byte with delays, confirming the server now closes the connection after the configured timeout rather than holding it indefinitely.
- Expectation: connection is closed at the configured timeout boundary — a direct, checkable prediction.
- Falsifier: if the connection remains open past the configured timeout, the `http.Server` construction did not actually apply the intended fields (e.g., still using `r.Run()` instead of the custom server).
- Guard: none beyond the configuration itself; this is not a regression-prone pattern once set. — safe-on-production: yes, this is a standard server-configuration change.

---

### PERF-008 — Redundant re-resolution of the caller's identity and favorite state within single-article endpoints

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `articles/routers.go:99-127` (`ArticleUpdate`), `:149-179` (`ArticleFavorite`/`ArticleUnfavorite`), `articles/serializers.go:67-90` (`ArticleSerializer.Response`), `articles/validators.go:35-49` (`ArticleModelValidator.Bind`) |
| **Tags** | quick-win |

**Problem**
Single-article endpoints (create, update, delete, favorite, unfavorite, comment-create) each call `GetArticleUserModel(myUserModel)` — which itself issues a `FirstOrCreate` query — more than once per request: once in the router for an authorization check, and again inside `ArticleSerializer.Response()` when computing `isFavoriteBy`. `favoritesCount()` and `isFavoriteBy()` are likewise computed synchronously via fresh queries every time a single article is serialized, even immediately after the same information was just touched elsewhere in the same request.

**Performance principle**
Duplicate work within a request: the same value (the caller's `ArticleUserModel`) is fetched more than once in a single request, per `work-and-algorithms.md §2`.

**Evidence**
- `articles/routers.go:107-108` (`ArticleUpdate`): `articleUserModel := GetArticleUserModel(myUserModel)` for the authorization check.
- `articles/serializers.go:80-81` (`ArticleSerializer.Response`): `Favorite: s.isFavoriteBy(GetArticleUserModel(myUserModel))` — a second, independent call to `GetArticleUserModel` for the same user within the same request, immediately followed by a `favoritesCount()` query.
- `articles/validators.go:46` (`ArticleModelValidator.Bind`, used by `ArticleCreate`): a third independent call site for the same pattern.
- Each `GetArticleUserModel` call is a `FirstOrCreate` (`articles/models.go:59-63`) — a read, and potentially a write, every time.

**Impact**
- Position: critical path.
- Frequency: per request, a small constant number of extra queries (2-4, depending on endpoint).
- Growth: O(1) — bounded, does not scale with data volume.
- Blast radius: endpoint.

**Conditions**
This is a pure code-structure fact, true on every request to the affected endpoints regardless of data volume or traffic. Scored `Medium` because the per-occurrence cost is small and constant, not because there is any uncertainty about whether it happens.

**Counter-evidence**
Searched for a per-request memoization of `GetArticleUserModel`'s result (e.g., stashed in the Gin context alongside `my_user_model`); none exists — each call site independently hits the database. Effect: `no-effect`.

**Why this might not matter**
Each individual endpoint only pays this cost once per request it serves (not per item in a loop, unlike PERF-001), so the absolute overhead is small and bounded. It is included because it is real, free to fix, and compounds with PERF-004's missing index on the exact column this function queries.

**Recommendation**
Resolve the caller's `ArticleUserModel` once per request (e.g., in the router, before validation/serialization) and pass it down explicitly, rather than letting each downstream layer re-derive it independently.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — resolve once in the router and pass explicitly | **preferred** | Removes the duplication at its source; no new infrastructure |
| B — memoize `GetArticleUserModel` per-request via the Gin context | | Also works, but hides the dependency behind context lookups rather than making it an explicit function argument, which is a smaller readability regression for a smaller implementation change — a reasonable second choice, not a wrong one |

**Trade-offs**
Passing the resolved value down changes several function signatures (`ArticleSerializer`, `ArticleModelValidator.Bind`) — a small, mechanical refactor with no behavioral change, verifiable by the existing test suite.

**Validation**
- Baseline: count queries for one `PUT /api/articles/:slug` request via GORM's query logger.
- Change: resolve `ArticleUserModel` once and pass it through.
- Measurement: same query count, same request.
- Expectation: query count drops by 1-2 depending on endpoint, a direct structural prediction.
- Falsifier: if query count does not change, the duplication was not actually removed (e.g., a remaining call site was missed).
- Guard: a test asserting query count for one of these endpoints, extended to cover this pattern alongside PERF-001's guard. — safe-on-production: yes, measurable via existing test infrastructure.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-009 | Medium | High | P2 | `articles/models.go:164-168`, `articles/routers.go:228-242` | `GET /articles/:slug/comments` has no pagination at all; every comment on an article is returned unconditionally on every call. Same underlying pattern as PERF-002, lower current urgency since comment counts per article are typically far smaller than article counts. |
| PERF-010 | Low | High | P3 | `articles/models.go:192,280` | `FindManyArticle` and `GetArticleFeed` wrap pure read queries in explicit `db.Begin()`/`tx.Commit()` transactions with no write inside them, adding BEGIN/COMMIT round-trip overhead with no stated consistency benefit. |
| PERF-011 | Low | High | P3 | `hello.go:35` | `gin.Default()`'s built-in access-log middleware writes synchronously to stdout on every request with no log-level control or async/buffered sink; a real cost at high request volume per `principles/resources.md §8`, though minor at this codebase's likely current scale. |

### Considered and not reported

- **bcrypt password hashing cost** (`users/models.go:57-66`, `bcrypt.DefaultCost`) — this is deliberately CPU-expensive by design, which is the entire point of using bcrypt; recommending a lower cost or flagging this as a defect would be exactly the kind of technology-uninformed pattern-matching this methodology exists to avoid. Not reported.
- **`GIN_MODE=debug` in `.env.example`** — affects startup verbosity and whether Gin's own internal warnings print, but `gin.Default()` always registers the same `Logger()`/`Recovery()` middleware regardless of mode, so this does not materially change per-request runtime cost. Too weak a mechanism to report as a performance finding on its own; folded into the observation about synchronous logging (PERF-011) instead of standing alone.
- **GORM's default logger threshold (200ms slow-query warning)** — this is actually a mild positive (some observability exists where none was guaranteed), not a defect; noted in §2/§6.7 rather than reported as a finding.

### Adjacent findings — outside performance scope

### SEC-001 — Hardcoded, source-visible JWT signing secret

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | Confirmed |
| **Risk** | High — complete authentication bypass is possible for anyone who can read this source file |
| **Location** | `common/utils.go:41-42` |

**Problem** The JWT signing secret used to authenticate every user session is a hardcoded string literal committed directly in source: `const JWTSecret = "A String Very Very Very Strong!!@##$!@#$"`, alongside `const RandomPassword = "A String Very Very Very Random!!@##$!@#4"` used as a sentinel value in the user-update validator (`users/validators.go:35,55`).

**Evidence** `common/utils.go:41-42`, both marked `// #nosec G101` — a linter-suppression comment indicating this was a known, deliberate trade-off rather than an oversight, which does not change its current risk. `common/utils.go:45-57` (`GenToken`) signs every issued token with this constant via `jwt.SigningMethodHS256`. `users/middlewares.go:60` verifies every incoming token against the same constant.

**Impact** Anyone with read access to this repository — which, for a public open-source example application, is anyone — can construct a valid signed JWT for any user ID without ever knowing that user's password, and use it to authenticate as that user against any deployment of this exact code that has not overridden the secret. This is a complete authentication bypass, not a degraded-security nuisance, hence `High` risk.

**Recommendation** Load the signing secret from an environment variable or a secrets manager at startup (the repository already has `.env`-based configuration wired up for `PORT`/`DB_PATH`/`GIN_MODE` in `common/database.go`; the same pattern extends naturally to this value), fail startup loudly if it is unset in a non-development environment, and rotate it for any deployment that has ever run with the current hardcoded value.

**Trade-offs** None of substance — this is a strictly-better configuration pattern with no functional downside; existing tokens signed with the old secret will become invalid on rotation, which is the intended effect.

**Validation** Confirm the application now reads the secret from configuration rather than the constant, and confirm a token signed with the old hardcoded value is rejected after rotation.

**Would need** A dedicated security review or secrets-management audit — this finding was noticed incidentally while reading `common/utils.go` for its role in every authenticated request's performance profile, not from a targeted security sweep, and this report makes no claim of security completeness beyond this one item.

### COR-001 — Silently discarded bcrypt error can produce an unusable account

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Low — fails closed (the affected account simply cannot log in), not exploitable as a security bypass |
| **Location** | `users/models.go:63` |

**Problem** `setPassword` discards the error from `bcrypt.GenerateFromPassword`: `passwordHash, _ := bcrypt.GenerateFromPassword(bytePassword, bcrypt.DefaultCost)`. If that call ever fails (bcrypt returns an error for inputs longer than 72 bytes in some library versions, among other cases), `u.PasswordHash` is left as an empty string, and the user record is saved anyway.

**Evidence** `users/models.go:57-66` (`setPassword`), specifically line 63's `_` discard. `users/routers.go:85` (`UsersRegistration`) calls `SaveOne(&userModelValidator.userModel)` immediately after, with no check that the password was actually set.

**Impact** An affected account would be saved with an empty password hash and would simply never be able to log in afterward (`checkPassword`, `users/models.go:71-75`, would fail to parse an empty string as a valid bcrypt hash and return a non-nil error, which `UsersLogin` already treats as invalid credentials) — a silent, hard-to-diagnose account-creation failure rather than a security hole, hence `Low` risk.

**Recommendation** Propagate the error from `setPassword` up through `Bind` and return a validation error to the caller instead of silently proceeding with an empty hash.

**Trade-offs** None — this is a strictly more correct error-handling path with no functional downside for the success case.

**Validation** A test supplying a password long enough to trigger bcrypt's length error and confirming registration now fails visibly instead of silently succeeding with an unusable account.

**Would need** A correctness-focused review or a broader pass for discarded (`_`) error values across the codebase — this single instance was noticed while reading the password-handling code for its CPU-cost profile, not from a systematic search for ignored errors.

---

## 8. Prioritized action plan

### P0 — Immediate

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P0 | Small (mirror an existing pattern in the same file) | Unbounded per-request query multiplication on the two highest-traffic endpoints, already fails to apply an optimization proven elsewhere in this exact codebase |

### P1 — High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 2 | PERF-002 | P1 | Small | Directly bounds PERF-001's worst case; a few lines |
| 3 | PERF-004 | P1 | Small | Struct-tag-only change; also directly reduces PERF-001's per-item cost |
| 4 | PERF-005 | P1 | Small | Two configuration calls; converts a hard failure mode into a queued wait |
| 5 | PERF-003 | P1 | Medium (may need a product decision on `articlesCount` semantics) | Real, growing cost on the hottest endpoints; sequenced after the cheaper fixes above |
| 6 | PERF-007 | P1 | Small | Standard server hardening; independent of the other fixes |

### P2 — Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 7 | PERF-006 | P2 | Small | Trivial, zero-risk fix — sequenced early despite P2 priority because it costs almost nothing to do alongside the P1 items (tagged `quick-win`) |
| 8 | PERF-008 | P2 | Small-Medium (touches a few function signatures) | Compounds with PERF-004; natural to do in the same pass |
| 9 | PERF-009 | P2 | Small | Same fix shape as PERF-002 |

### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 10 | PERF-010 | P3 | Small | Minor; no urgency |
| 11 | PERF-011 | P3 | Small | Minor; no urgency |

**If only one thing is done:** Fix PERF-001 (batch the follow-status check the same way favorites are already batched) — it is the single highest-impact, lowest-effort change in this report, since the pattern to copy already exists two functions away in the same file.

---

## 9. Validation plan

Per-finding validation detail is in each finding's own **Validation** subsection in §7. Summary of what to measure, in sequence:

### Instrumentation gaps to close first

This repository is uninstrumented (§2, §6.7). Before any of the above changes are shipped and trusted, add:

1. **Query-count logging or a query-count test helper** for at least `GET /api/articles`, `GET /api/articles/feed`, and one write endpoint — the cheapest, most direct way to validate PERF-001, PERF-002, PERF-006, and PERF-008, all of which make a specific, falsifiable prediction about query count. GORM's own logger (already present, just not turned on by default — `common/database.go:57`) can produce this with a one-line config change in a test or staging environment.
2. **Basic per-route request-count and latency metrics**, even a simple in-memory counter exposed on an admin-only route, to answer the workload questions in §4 that currently cap several findings at `Medium` confidence.
3. **A concurrent-write test** (two simultaneous write requests against a disposable test database) to directly validate or refute PERF-005 — this is the one finding in this report where the honest answer to "is this currently a problem" is "cannot be determined without this specific test."

None of the above requires new infrastructure — all three are achievable with what is already in `go.mod` (GORM's existing logger, Go's standard testing tools) plus a small amount of new test/ops code.

---

## 10. Machine-readable output

```json
{
  "schema_version": "1.0",
  "mode": "full",
  "reproducibility": {
    "generated_at": "2026-09-10T00:00:00Z",
    "skill_version": "0.6.0",
    "spec": "backend-performance-review/2.0",
    "registry_version": "1",
    "model": "claude-sonnet-5",
    "repository": {
      "name": "golang-gin-realworld-example-app (gin-realworld)"
    }
  },
  "workload": {
    "answered": false,
    "inputs": [
      { "question": "Roughly what request rate do the busiest endpoints see at peak?", "answer": "Unknown; no access logs, metrics, or user-supplied figures available.", "source": "repository" },
      { "question": "What is the largest table today and how fast is it growing?", "answer": "Unknown; no row-count evidence available. Repository documentation frames this as a teaching/reference app with no stated production scale.", "source": "repository" },
      { "question": "What is the read/write ratio on the primary datastore?", "answer": "Inferred coarse read-heavy shape from route count and RealWorld's content-listing product shape, not measured.", "source": "derived" },
      { "question": "Is there a latency target or SLO?", "answer": "None found in the repository.", "source": "repository" },
      { "question": "How many application instances/workers run in production?", "answer": "Unknown; no deployment configuration (Dockerfile, k8s manifest, Procfile) exists in the repository.", "source": "repository" },
      { "question": "Which operations are user-blocking vs background?", "answer": "All operations in this codebase are synchronous/user-blocking; no background job or queue exists.", "source": "repository" },
      { "question": "Is there a specific performance problem that prompted this review?", "answer": "None stated; this is a general independent review, not an incident follow-up.", "source": "user" }
    ],
    "scenarios": ["normal", "large-dataset", "high-concurrency"]
  },
  "runtime_evidence": [],
  "completeness": {
    "review_confidence": "Medium",
    "evidence_available": "uninstrumented",
    "ranking_method": "structural-signals-only",
    "repository_coverage": "All non-test, non-vendor Go source files read in full (100%)",
    "critical_paths_identified": 18,
    "critical_paths_analyzed": 18,
    "technology_support": [
      { "technology": "go", "tier": "deep" },
      { "technology": "rest (gin)", "tier": "deep" },
      { "technology": "sqlite", "tier": "deep" }
    ],
    "unknowns": [
      { "subject": "Whether concurrent SQLite write contention (PERF-005) actually occurs in a real deployment", "reason": "no-evidence-exists", "what_would_resolve_it": "A concurrent-write load test against a disposable copy of the database, counting SQLITE_BUSY/5xx responses" },
      { "subject": "Current and projected row counts for articles/users/favorites/follows", "reason": "no-evidence-exists", "what_would_resolve_it": "Query row counts against a real deployment's database file" },
      { "subject": "Production request rate for the two article-list endpoints", "reason": "no-evidence-exists", "what_would_resolve_it": "Add per-route request-count metrics and observe for a representative period" },
      { "subject": "Whether this service is deployed as a traffic-serving backend anywhere, versus used only as a local/teaching reference", "reason": "no-evidence-exists", "what_would_resolve_it": "Ask the operator directly" }
    ]
  },
  "assumptions": {
    "known": [
      "A single embedded SQLite file backs 100% of the application's persistent state (common/database.go:22-27,49-69; go.mod)",
      "No enforced maximum exists on limit/offset for GET /api/articles or GET /api/articles/feed (articles/models.go:182-190,271-278)",
      "No index tag exists on ArticleUserModel.UserModelID, ArticleModel.AuthorID, FavoriteModel.FavoriteID/FavoriteByID, CommentModel.ArticleID/AuthorID, FollowModel.FollowingID/FollowedByID, or UserModel.Username (articles/models.go:11-52; users/models.go:16-42)",
      "No SetMaxOpenConns, busy_timeout, or WAL configuration exists anywhere in the repository (common/database.go; .env.example)",
      "No metrics, tracing, benchmark, or load-test file exists anywhere in the repository",
      "The repository's own documentation frames this as a RealWorld-spec teaching/reference implementation with no stated production traffic target (readme.md:9-12; BACKEND_INSTRUCTIONS.md:5-7)"
    ],
    "unknown": [
      "Actual peak request rate for any endpoint",
      "Actual or expected row counts for articles, users, favorites, follows",
      "Number of concurrent application instances/processes in any real deployment",
      "Any stated latency target or SLO",
      "Whether this codebase is deployed as a traffic-serving service anywhere"
    ],
    "assumed": [
      "If deployed as a real backend, the service would receive concurrent requests including concurrent writes from more than one client — affects PERF-005",
      "articles/users/favorites/follows tables are expected to grow over the service's lifetime rather than remain permanently tiny — affects PERF-002, PERF-003, PERF-004",
      "GET /api/articles and GET /api/articles/feed are called at meaningfully higher frequency than the write endpoints, consistent with a content-listing product shape — affects the relative ranking of PERF-001/002/003"
    ],
    "derived": [
      "A default GET /api/articles request issues up to 22 SQL round trips (1 list + 1 count + up to 20 per-article follow-checks), from articles/models.go:187-190 (default page size 20), articles/models.go:255-260 (list+count), and users/models.go:121-129 (isFollowing per article)",
      "A GET /api/articles?limit=1000 request issues on the order of 1002 round trips, from the same code paths with no clamp applied (articles/models.go:182-190)",
      "AuthMiddleware runs twice on GET/PUT /api/user, POST/DELETE /api/profiles/:username/follow, and every /api/articles write route, derived from tracing hello.go:41-52's exact registration order against Gin's documented Group()/Use() semantics"
    ],
    "measured": []
  },
  "decision_changing_questions": [
    { "question": "Does this service ever run with more than one concurrent writer in practice?", "what_it_would_change": "Would move PERF-005 to Confirmed if errors are observed, or allow it to be closed as a non-issue if writes are provably serial", "affects": ["PERF-005"] },
    { "question": "What are today's row counts for articles, favorites, and follows?", "what_it_would_change": "Determines whether PERF-003/PERF-004 are already measurable problems or purely future risk", "affects": ["PERF-003", "PERF-004"] },
    { "question": "What request rate does GET /api/articles see at peak?", "what_it_would_change": "Determines how frequently PERF-001's per-item query multiplication fires in practice", "affects": ["PERF-001"] },
    { "question": "Is there a reverse proxy or load balancer in front of this service in any real deployment?", "what_it_would_change": "Would substantially mitigate PERF-007 if true", "affects": ["PERF-007"] }
  ],
  "root_causes": [
    { "id": "ROOT-001", "description": "The follow-status check inside article-list serialization issues one unbatched query per article and has no anonymous-user short-circuit, unlike the structurally identical favorite-status check two functions away in the same file.", "findings": ["PERF-001"] },
    { "id": "ROOT-002", "description": "Caller-supplied limit/offset pagination parameters on the article-list and feed endpoints have no enforced maximum.", "findings": ["PERF-002"] },
    { "id": "ROOT-003", "description": "Every branch of the article-list and feed endpoints computes an exact COUNT(*) for pagination metadata on every request.", "findings": ["PERF-003"] },
    { "id": "ROOT-004", "description": "Several foreign-key/lookup columns queried on nearly every request carry no supporting index, relying solely on GORM's AutoMigrate with no manually-added index tags.", "findings": ["PERF-004"] },
    { "id": "ROOT-005", "description": "The SQLite connection pool and timeout configuration is sized as if for a client-server database, not for SQLite's single-writer, zero-default-busy-timeout concurrency model.", "findings": ["PERF-005"] },
    { "id": "ROOT-006", "description": "Route-group registration order in hello.go causes AuthMiddleware to be applied twice on several routes.", "findings": ["PERF-006"] },
    { "id": "ROOT-007", "description": "The HTTP server is started with Gin's default zero-value timeouts, with no explicit ReadTimeout/WriteTimeout/IdleTimeout configured.", "findings": ["PERF-007"] },
    { "id": "ROOT-008", "description": "The caller's ArticleUserModel and per-article favorite state are independently re-resolved by more than one layer within a single request instead of being computed once and passed down.", "findings": ["PERF-008"] }
  ],
  "findings": [
    {
      "id": "PERF-001",
      "stable_id": "articleslistfollowcheckperitem",
      "root_cause_id": "ROOT-001",
      "state": "OPEN",
      "severity": "Critical",
      "confidence": "High",
      "priority": "P0",
      "category": "data-access",
      "location": { "file": "users/models.go", "line": 121, "symbol": "UserModel.isFollowing" },
      "tags": ["scalability-risk"],
      "problem": "Every article returned by GET /api/articles or GET /api/articles/feed triggers a separate, unbatched query to check whether the requesting user follows that article's author, unbounded by page size and not short-circuited for anonymous callers.",
      "performance_principle": "Repeated work: one query per element of an already-fetched collection (N+1), issued from inside a serializer where it is easy to miss because it does not appear in the handler.",
      "evidence": [
        { "statement": "ArticlesSerializer.Response loops over every article in the page and calls ResponseWithPreloaded for each.", "kind": "static", "file": "articles/serializers.go", "line": 134 },
        { "statement": "ResponseWithPreloaded calls authorSerializer.Response() for every article, which delegates to users.ProfileSerializer.Response().", "kind": "static", "file": "articles/serializers.go", "line": 103 },
        { "statement": "ProfileSerializer.Response calls myUserModel.isFollowing(self.UserModel) unconditionally, once per call.", "kind": "static", "file": "users/serializers.go", "line": 35 },
        { "statement": "isFollowing issues a full query via db.Where(FollowModel{...}).First(&follow) with no u.ID == 0 short-circuit.", "kind": "static", "file": "users/models.go", "line": 124 },
        { "statement": "BatchGetFavoriteStatus, the structurally identical check for favorites two functions away, explicitly short-circuits for anonymous users (userID == 0) and batches the whole page into one query; isFollowing received no equivalent treatment.", "kind": "static", "file": "articles/models.go", "line": 113 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-item",
        "growth": "O(n)",
        "blast_radius": "service",
        "amplification": "Nx"
      },
      "conditions": "Assumes GET /api/articles and GET /api/articles/feed are called at non-trivial frequency and that pages return more than a handful of articles; both are plausible given the RealWorld content-listing shape but neither is measured for this deployment, so confidence in the magnitude (not the mechanism) is workload-dependent.",
      "counter_evidence": [
        { "statement": "Searched for a batching helper analogous to BatchGetFavoriteStatus covering follow status; none exists.", "effect": "no-effect" },
        { "statement": "Searched for a cache or memoization layer in front of isFollowing; none exists anywhere in go.mod's dependencies.", "effect": "no-effect" },
        { "statement": "Searched for an anonymous-user short-circuit inside isFollowing or its call chain; none exists.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "If this deployment's list pages are small and rarely called by authenticated users who follow many others, the absolute query count stays small and the fix is hygiene rather than urgency; the RealWorld spec's default page size and dedicated /feed endpoint argue against this being negligible, but it is not Confirmed without traffic data.",
      "recommendation": "Batch the follow-status lookup the same way BatchGetFavoriteStatus already batches favorite status: one query with an IN clause for the page's distinct authors, plus an anonymous-user short-circuit, mirroring the proven pattern already in this file.",
      "alternatives": [
        { "option": "Batch the follow-status query, mirroring BatchGetFavoriteStatus", "preferred": true, "why": "Removes the repeated work entirely; the pattern is already proven correct in this exact codebase for the structurally identical favorites problem" },
        { "option": "Cache follow relationships in-process", "preferred": false, "why": "Papers over the query count rather than removing it, adds invalidation complexity, and introduces a cache dependency this service does not otherwise have" },
        { "option": "Denormalize a following flag onto ArticleUserModel", "preferred": false, "why": "Read-optimizes at real write-complexity and consistency cost; not justified before the batched-query approach is shown insufficient" }
      ],
      "trade_offs": "Requires one extra intermediate map bounded by page size; no new dependency, no new failure mode beyond the small added code path already proven for favorites.",
      "validation": {
        "metric": "Total SQL statement count for one GET /api/articles request",
        "expectation": "Query count for a default request falls from up to 22 to a small constant number independent of page size",
        "falsifier": "If query count does not drop after batching, the batching was implemented incorrectly",
        "safety": "not-safe-on-production",
        "guard": "A test asserting total query count for GET /api/articles stays constant as the number of returned articles grows"
      }
    },
    {
      "id": "PERF-002",
      "stable_id": "articlelistunboundedpagination",
      "root_cause_id": "ROOT-002",
      "state": "OPEN",
      "severity": "High",
      "confidence": "High",
      "priority": "P1",
      "category": "data-access",
      "location": { "file": "articles/models.go", "line": 182, "symbol": "FindManyArticle" },
      "tags": ["quick-win", "scalability-risk"],
      "problem": "limit and offset are parsed with a fallback default on parse error only; any valid integer supplied by the caller, however large, is passed straight through to GORM's Offset()/Limit() with no upper bound.",
      "performance_principle": "Unbounded work: a default page size is not a bound if the caller can override it.",
      "evidence": [
        { "statement": "limit_int/offset_int are parsed via strconv.Atoi with fallback only on error, no maximum clamp.", "kind": "static", "file": "articles/models.go", "line": 187 },
        { "statement": "GetArticleFeed has the identical parsing pattern with the same absence of a clamp.", "kind": "static", "file": "articles/models.go", "line": 275 },
        { "statement": "ArticleList and ArticleFeed pass raw query-string values through with no validation.", "kind": "static", "file": "articles/routers.go", "line": 56 },
        { "statement": "getComments/ArticleCommentList accept no pagination parameters at all, returning every comment on an article unconditionally.", "kind": "static", "file": "articles/models.go", "line": 166 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(n)",
        "blast_radius": "service",
        "amplification": "unknown"
      },
      "conditions": "Assumes the articles table grows beyond a trivial size and that this public, anonymously-reachable endpoint is exposed to callers not trusted to self-limit their requests, which is true of any public REST API including this one.",
      "counter_evidence": [
        { "statement": "Searched for request-size limiting middleware, WAF, or reverse-proxy configuration anywhere in the repository; none exists.", "effect": "no-effect" },
        { "statement": "Searched for a maximum enforced elsewhere in the call chain before reaching FindManyArticle; none exists.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "If this service is only ever exercised through its own trusted frontend, the unbounded path may never be hit by anything other than a deliberate test; this does not make it safe against any other client calling the API directly.",
      "recommendation": "Clamp limit_int to a small maximum immediately after parsing in both FindManyArticle and GetArticleFeed, and reject or clamp negative values.",
      "alternatives": [
        { "option": "Clamp limit/offset to a fixed maximum in the model layer", "preferred": true, "why": "Smallest possible change, fixes the unbounded worst case immediately with no behavior change for well-behaved callers" },
        { "option": "Switch to keyset (cursor) pagination", "preferred": false, "why": "Avoids offset-pagination's separate cost-grows-with-depth problem but is a larger API-shape change not justified until a clamp alone proves insufficient" }
      ],
      "trade_offs": "None of substance; a maximum costs nothing at runtime and only affects callers already outside intended usage.",
      "validation": {
        "metric": "Response row count for a request with an oversized limit parameter",
        "expectation": "Response size for a maximal-limit request becomes bounded and independent of table size",
        "falsifier": "If a request above the new maximum still returns more rows than the maximum, the clamp was bypassed",
        "safety": "safe-on-production",
        "guard": "A test asserting response row count never exceeds the configured maximum regardless of requested limit"
      }
    },
    {
      "id": "PERF-003",
      "stable_id": "articlelistexactcountperbranch",
      "root_cause_id": "ROOT-003",
      "state": "OPEN",
      "severity": "High",
      "confidence": "High",
      "priority": "P1",
      "category": "data-access",
      "location": { "file": "articles/models.go", "line": 257, "symbol": "FindManyArticle" },
      "tags": ["scalability-risk"],
      "problem": "Every branch of FindManyArticle and GetArticleFeed computes an exact row count for pagination metadata on every request, regardless of requested page size.",
      "performance_principle": "Counting by materializing: an exact count requires examining every matching row to produce a single integer.",
      "evidence": [
        { "statement": "Default (unfiltered) branch runs Count(&count64) against the whole articles table on every unfiltered list request.", "kind": "static", "file": "articles/models.go", "line": 257 },
        { "statement": "Tag/author/favorited branches each run a separate Association(...).Count() query.", "kind": "static", "file": "articles/models.go", "line": 219 },
        { "statement": "GetArticleFeed runs an equivalent Count query filtered by author_id IN (...), which also suffers PERF-004's missing index on articles.author_id.", "kind": "static", "file": "articles/models.go", "line": 300 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(n)",
        "blast_radius": "service",
        "amplification": "unknown"
      },
      "conditions": "Assumes the articles table and relevant associations grow past a size where a full scan is free, true of essentially any table given enough time; the degradation is smooth rather than a sudden cliff.",
      "counter_evidence": [
        { "statement": "Searched for a cached or approximate count anywhere in the codebase; none exists.", "effect": "no-effect" },
        { "statement": "Searched for whether the count is computed only conditionally (e.g., first page only); it is computed unconditionally on every branch.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "For a small articles table, SQLite can typically satisfy this count fast enough that it is not the dominant cost relative to PERF-001's per-item queries; it becomes dominant only as the table grows well past PERF-001's post-fix cost floor.",
      "recommendation": "Recognize the count is not free; where the frontend does not strictly require an exact total, replace it with a cheap has-more check (fetch limit+1 rows) rather than a full count.",
      "alternatives": [
        { "option": "Replace exact count with a has-more check (fetch limit+1 rows)", "preferred": true, "why": "Removes the full-scan cost entirely for the dominant forward-paging use case; requires an API-contract conversation since articlesCount's meaning changes" },
        { "option": "Keep exact count but cache/recompute periodically", "preferred": false, "why": "Avoids the contract change but a cache for a frequently-written table either goes stale or needs invalidation machinery disproportionate to the problem" },
        { "option": "Leave as is", "preferred": false, "why": "Defensible only if articlesCount is a hard product requirement and table sizes stay small, which this review cannot confirm" }
      ],
      "trade_offs": "Changes the exact semantics of articlesCount in the API response from a total to an at-least/boolean value, a real visible API contract change needing sign-off.",
      "validation": {
        "metric": "Rows examined for the count query, via EXPLAIN QUERY PLAN",
        "expectation": "If switching to a has-more check, the count-side cost becomes bounded by limit+1 rather than total table size",
        "falsifier": "If the has-more check still scans the full table, the fix did not address the mechanism",
        "safety": "safe-on-production",
        "guard": "None beyond the one-time structural decision"
      }
    },
    {
      "id": "PERF-004",
      "stable_id": "missingindexesonhotpathfks",
      "root_cause_id": "ROOT-004",
      "state": "OPEN",
      "severity": "High",
      "confidence": "High",
      "priority": "P1",
      "category": "data-access",
      "location": { "file": "articles/models.go", "line": 24, "symbol": "ArticleUserModel" },
      "tags": ["scalability-risk"],
      "problem": "Several columns filtered on constantly by shared helper functions (GetArticleUserModel, isFollowing, isFavoriteBy) carry no index tag, relying entirely on GORM's AutoMigrate which only creates what is explicitly declared.",
      "performance_principle": "An unindexed predicate on a growing table forces a full scan for every lookup instead of a logarithmic index traversal.",
      "evidence": [
        { "statement": "ArticleUserModel.UserModelID has no index tag; GetArticleUserModel filters on exactly this column and is called from nearly every article-related request.", "kind": "static", "file": "articles/models.go", "line": 60 },
        { "statement": "ArticleModel.AuthorID has no index tag but is filtered in GetArticleFeed's Where('author_id IN ?', ...).", "kind": "static", "file": "articles/models.go", "line": 300 },
        { "statement": "FavoriteModel.FavoriteID/FavoriteByID have no index tags but are filtered/grouped in BatchGetFavoriteCounts, BatchGetFavoriteStatus, isFavoriteBy.", "kind": "static", "file": "articles/models.go", "line": 98 },
        { "statement": "FollowModel.FollowingID/FollowedByID have no index tags and are the exact predicate columns in isFollowing, the mechanism at the center of PERF-001.", "kind": "static", "file": "users/models.go", "line": 124 },
        { "statement": "UserModel.Username has no index tag (only Email has uniqueIndex) yet is filtered in ProfileRetrieve and the author-filter branch of FindManyArticle.", "kind": "static", "file": "users/models.go", "line": 18 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(n)",
        "blast_radius": "system-wide",
        "amplification": "unknown"
      },
      "conditions": "Assumes the affected tables grow past the point where SQLite's page cache absorbs a full scan cheaply, true for any of these tables given real usage over time; degradation is a step function once the working set outgrows cache, not a smooth curve.",
      "counter_evidence": [
        { "statement": "Searched for a manual migration adding these indexes outside GORM struct tags; none exists — AutoMigrate is the only schema-management call found anywhere.", "effect": "no-effect" },
        { "statement": "Searched for whether any affected table is structurally guaranteed small; none is — all grow with normal user activity.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "At small table sizes, SQLite's own page cache and the OS filesystem cache make a full scan of a few hundred or thousand rows fast enough not to be noticeable; this cost curve is gradual, and it is plausible this currently costs single-digit milliseconds per call.",
      "recommendation": "Add gorm:\"index\" tags to the listed columns (and composite indexes on the two-column predicates used by isFavoriteBy/isFollowing) and let AutoMigrate create them.",
      "alternatives": [
        { "option": "Add the missing single/composite indexes via struct tags", "preferred": true, "why": "Directly addresses the mechanism using tooling already in place, no new dependency" },
        { "option": "Do nothing until a slow-query log shows a problem", "preferred": false, "why": "The degradation is a step function, not a warning-shot curve; by the time it is visibly slow the fix is the same one available now" }
      ],
      "trade_offs": "Every index costs write time on insert/update/delete touching that column plus storage; justified here because these tables are read far more often than written.",
      "validation": {
        "metric": "EXPLAIN QUERY PLAN output for GetArticleUserModel's and isFollowing's queries",
        "expectation": "Plan changes from SCAN to SEARCH ... USING INDEX after the index is added",
        "falsifier": "If the plan still shows SCAN after the index is added, the index does not match the query's predicate shape",
        "safety": "safe-on-production",
        "guard": "None needed beyond the one-time plan check; index existence is enforced by AutoMigrate on every startup"
      }
    },
    {
      "id": "PERF-005",
      "stable_id": "sqlitepoolmismatchedtowritemodel",
      "root_cause_id": "ROOT-005",
      "state": "OPEN",
      "severity": "Critical",
      "confidence": "Medium",
      "priority": "P1",
      "category": "concurrency",
      "location": { "file": "common/database.go", "line": 49, "symbol": "Init" },
      "tags": ["scalability-risk", "needs-measurement"],
      "problem": "SetMaxOpenConns is never called (default unlimited) while SetMaxIdleConns(10) is, and no busy_timeout or WAL journal_mode is configured anywhere, so concurrent writers to the single SQLite file will receive immediate SQLITE_BUSY errors rather than queuing.",
      "performance_principle": "A bounded shared resource (SQLite's single-writer lock) configured as if unbounded or as if contention would queue rather than fail.",
      "evidence": [
        { "statement": "gorm.Open(sqlite.Open(dbPath), &gorm.Config{}) — dbPath carries no DSN query parameters of any kind.", "kind": "static", "file": "common/database.go", "line": 57 },
        { "statement": "SetMaxIdleConns(10) is the only pool-sizing call anywhere in the repository; no SetMaxOpenConns call exists.", "kind": "static", "file": "common/database.go", "line": 65 },
        { "statement": "technology/sqlite.md: default busy_timeout is zero (immediate SQLITE_BUSY on write conflict), and a larger pool can increase SQLITE_BUSY contention rather than reduce it.", "kind": "static" },
        { "statement": "No retry/backoff logic around any write call exists in articles/models.go or users/models.go; a SQLITE_BUSY error propagates straight to a generic error response.", "kind": "static", "file": "articles/routers.go", "line": 47 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "unknown",
        "blast_radius": "system-wide",
        "amplification": "unknown"
      },
      "conditions": "Requires more than one write request to arrive close enough in time to overlap — a concurrency condition, not a data-volume one, and whether this ever occurs in this deployment is unknown, which caps confidence at Medium despite the configuration mismatch itself being unambiguous.",
      "counter_evidence": [
        { "statement": "Searched for any retry/backoff wrapper around GORM write calls; none exists.", "effect": "no-effect" },
        { "statement": "Searched for a DSN or PRAGMA setting anywhere that would raise busy_timeout above zero; none exists.", "effect": "no-effect" },
        { "statement": "Considered whether this instance is only ever run by a single interactive client, which would make the finding moot in practice; the repository's framing is suggestive but not conclusive, and the API itself has no such restriction built in.", "effect": "lowers-confidence" }
      ],
      "why_this_might_not_matter": "If every real use of this instance issues writes serially (a single developer, a single CI run), this failure mode may never trigger in practice; the fix is cheap and worth making regardless, but this should not be read as the service currently being broken.",
      "recommendation": "Call SetMaxOpenConns(1) to match SQLite's actual single-writer ceiling, and set a non-zero busy_timeout as defense in depth.",
      "alternatives": [
        { "option": "SetMaxOpenConns(1) plus non-zero busy_timeout", "preferred": true, "why": "Matches pool configuration to what SQLite actually allows, converting hard failure into a brief wait" },
        { "option": "Add application-level retry/backoff around every write call, keep pool unbounded", "preferred": false, "why": "Treats the symptom at every call site individually rather than fixing the one place the mismatch is introduced" },
        { "option": "Migrate off SQLite to a client-server database", "preferred": false, "why": "The eventual right answer at real concurrent-write scale, but a materially larger change than this review's evidence justifies recommending as a first step" }
      ],
      "trade_offs": "Write throughput becomes explicitly serialized in the application layer; under real concurrent write load, requests queue rather than error, trading immediate failure for added worst-case latency bounded by the configured busy_timeout.",
      "validation": {
        "metric": "SQLITE_BUSY / 5xx response count under two simultaneous write requests",
        "expectation": "Error rate under concurrent writes drops to zero (both requests eventually succeed, serialized) rather than one succeeding and one failing outright",
        "falsifier": "If concurrent-write errors persist unchanged after the configuration change, the mechanism was misdiagnosed",
        "safety": "not-safe-on-production",
        "guard": "A test issuing two concurrent writes against a disposable test database, asserting both succeed"
      }
    },
    {
      "id": "PERF-006",
      "stable_id": "duplicateauthmiddlewarechain",
      "root_cause_id": "ROOT-006",
      "state": "OPEN",
      "severity": "Medium",
      "confidence": "High",
      "priority": "P2",
      "category": "networking",
      "location": { "file": "hello.go", "line": 41, "symbol": "main" },
      "tags": ["quick-win"],
      "problem": "GET/PUT /api/user, POST/DELETE /api/profiles/:username/follow, and every /api/articles write route run AuthMiddleware twice per request due to route-group registration order.",
      "performance_principle": "Duplicate work within a request: the same JWT is parsed and the same user row is fetched twice for no additional effect.",
      "evidence": [
        { "statement": "v1.Use(AuthMiddleware(false)) is called, then several anonymous-group routes are registered inheriting it, then v1.Use(AuthMiddleware(true)) is called, then the authenticated-group routes are registered inheriting both.", "kind": "static", "file": "hello.go", "line": 41 },
        { "statement": "AuthMiddleware always calls UpdateContextUserModel(c, 0) first, then re-parses the token and calls it again with the resolved id if valid, each independently querying the database.", "kind": "static", "file": "users/middlewares.go", "line": 43 },
        { "statement": "UpdateContextUserModel issues db.First(&myUserModel, my_user_id) whenever a non-zero id is passed.", "kind": "static", "file": "users/middlewares.go", "line": 34 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(1)",
        "blast_radius": "endpoint",
        "amplification": "2x"
      },
      "conditions": "A pure code-structure fact, true on every request to the affected routes regardless of workload; scored Medium because the per-occurrence cost is small and bounded, not because of any uncertainty.",
      "counter_evidence": [
        { "statement": "Checked whether AuthMiddleware(false) aborts before AuthMiddleware(true) runs; it only aborts when auto401 is true, which this call is not, so both always run.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "The absolute cost is small — one extra JWT parse and one extra primary-key lookup; not currently a measured bottleneck on its own.",
      "recommendation": "Restructure route registration so AuthMiddleware is applied exactly once per route.",
      "alternatives": [
        { "option": "Restructure registration so each route gets exactly one AuthMiddleware call", "preferred": true, "why": "Directly removes the duplication at its source with no behavior change" },
        { "option": "Make AuthMiddleware idempotent by checking context state first", "preferred": false, "why": "Treats the symptom rather than the registration-order cause" }
      ],
      "trade_offs": "None; pure removal of duplicated work, verifiable by the existing test suite continuing to pass unchanged.",
      "validation": {
        "metric": "AuthMiddleware execution count per request to an affected route",
        "expectation": "Execution count drops from 2 to 1 per request",
        "falsifier": "If it still fires twice after restructuring, the registration order was not actually fixed",
        "safety": "safe-on-production",
        "guard": "None beyond the fix itself"
      }
    },
    {
      "id": "PERF-007",
      "stable_id": "httpservernodeadlinetimeouts",
      "root_cause_id": "ROOT-007",
      "state": "OPEN",
      "severity": "High",
      "confidence": "Medium",
      "priority": "P1",
      "category": "networking",
      "location": { "file": "hello.go", "line": 67, "symbol": "main" },
      "tags": ["quick-win", "needs-measurement"],
      "problem": "r.Run() starts Gin's default HTTP server with no ReadTimeout, WriteTimeout, IdleTimeout, or ReadHeaderTimeout configured, so a slow or stalled client can hold a connection and its goroutine open indefinitely.",
      "performance_principle": "An unbounded resource: absent server timeouts are the server's missing defense against slow clients holding worker slots.",
      "evidence": [
        { "statement": "r.Run(':' + port) constructs a default http.Server internally with no timeout fields set.", "kind": "static", "file": "hello.go", "line": 67 },
        { "statement": "No reverse proxy, load balancer, or other front-end configuration exists anywhere in the repository.", "kind": "static" }
      ],
      "evidence_quality": "Moderate",
      "impact": {
        "position": "critical-path",
        "frequency": "rare",
        "growth": "unknown",
        "blast_radius": "system-wide",
        "amplification": "unknown"
      },
      "conditions": "Requires a slow or stalled client to actually occur, organically or adversarially; whether this happens in this deployment is unknown, capping confidence at Medium.",
      "counter_evidence": [
        { "statement": "Searched for any timeout configuration passed to Gin or a custom http.Server anywhere in the repository; none exists.", "effect": "no-effect" },
        { "statement": "Considered whether an upstream reverse proxy might already mitigate this in some deployment; none is visible in the repository, but its absence here does not prove its absence in every real deployment.", "effect": "lowers-confidence" }
      ],
      "why_this_might_not_matter": "Many deployment platforms impose their own connection timeouts in front of an application server regardless of what the application configures; this review found no such layer in the repository but cannot rule one out in a real deployment.",
      "recommendation": "Construct an explicit http.Server with ReadTimeout/WriteTimeout/IdleTimeout/ReadHeaderTimeout set to values consistent with this API's small JSON request/response shape, and call ListenAndServe with Gin's router as the handler.",
      "alternatives": [
        { "option": "Configure explicit server timeouts in hello.go", "preferred": true, "why": "Small, self-contained change that makes the guarantee true regardless of the deployment environment" },
        { "option": "Rely on an upstream reverse proxy/platform timeout", "preferred": false, "why": "Not verifiable from the repository and leaves the service unprotected if ever run directly, which the repo's own instructions describe as the normal local/CI usage" }
      ],
      "trade_offs": "Overly aggressive timeouts could cut off legitimately slow clients; values should be generous enough for this API's small payload sizes while still bounding the worst case.",
      "validation": {
        "metric": "Whether a deliberately slow test client's connection is closed at the configured timeout boundary",
        "expectation": "Connection is closed at the configured timeout rather than held indefinitely",
        "falsifier": "If the connection remains open past the configured timeout, the server construction did not apply the intended fields",
        "safety": "safe-on-production",
        "guard": "None beyond the configuration itself"
      }
    },
    {
      "id": "PERF-008",
      "stable_id": "redundantarticleusermodelrefetch",
      "root_cause_id": "ROOT-008",
      "state": "OPEN",
      "severity": "Medium",
      "confidence": "High",
      "priority": "P2",
      "category": "data-access",
      "location": { "file": "articles/serializers.go", "line": 80, "symbol": "ArticleSerializer.Response" },
      "tags": ["quick-win"],
      "problem": "Single-article endpoints call GetArticleUserModel more than once per request across router, validator, and serializer layers, plus recompute favorite state via fresh queries each time, instead of resolving it once and passing it down.",
      "performance_principle": "Duplicate work within a request: the same value is fetched more than once in a single request.",
      "evidence": [
        { "statement": "ArticleUpdate calls GetArticleUserModel for the authorization check.", "kind": "static", "file": "articles/routers.go", "line": 108 },
        { "statement": "ArticleSerializer.Response independently calls GetArticleUserModel again within the same request, immediately followed by a favoritesCount() query.", "kind": "static", "file": "articles/serializers.go", "line": 80 },
        { "statement": "ArticleModelValidator.Bind (used by ArticleCreate) is a third independent call site for the same pattern.", "kind": "static", "file": "articles/validators.go", "line": 46 },
        { "statement": "GetArticleUserModel is a FirstOrCreate call — a read and potentially a write, every time.", "kind": "static", "file": "articles/models.go", "line": 61 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(1)",
        "blast_radius": "endpoint",
        "amplification": "unknown"
      },
      "conditions": "A pure code-structure fact true on every request to the affected endpoints regardless of data volume; scored Medium because the per-occurrence cost is small and constant.",
      "counter_evidence": [
        { "statement": "Searched for a per-request memoization of GetArticleUserModel's result (e.g., stashed in the Gin context); none exists.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "Each endpoint only pays this cost once per request (not per item in a loop), so absolute overhead is small; included because it is free to fix and compounds with PERF-004's missing index on the queried column.",
      "recommendation": "Resolve the caller's ArticleUserModel once per request and pass it down explicitly rather than letting each layer re-derive it.",
      "alternatives": [
        { "option": "Resolve once in the router and pass explicitly", "preferred": true, "why": "Removes duplication at its source with no new infrastructure" },
        { "option": "Memoize GetArticleUserModel per-request via the Gin context", "preferred": false, "why": "Also works but hides the dependency behind context lookups rather than an explicit argument" }
      ],
      "trade_offs": "Changes several function signatures (ArticleSerializer, ArticleModelValidator.Bind) — a small mechanical refactor with no behavioral change.",
      "validation": {
        "metric": "SQL query count for one PUT /api/articles/:slug request",
        "expectation": "Query count drops by 1-2 depending on endpoint",
        "falsifier": "If query count does not change, the duplication was not actually removed",
        "safety": "not-safe-on-production",
        "guard": "A test asserting query count for this endpoint, extended alongside PERF-001's guard"
      }
    }
  ],
  "considered_not_reported": [
    { "observation": "bcrypt.DefaultCost used for password hashing is CPU-expensive per request.", "why_discarded": "This is bcrypt's deliberate, correct purpose; flagging it as a performance defect would be technology-uninformed pattern-matching.", "location": "users/models.go:63" },
    { "observation": "GIN_MODE=debug in .env.example", "why_discarded": "gin.Default() registers the same middleware regardless of mode; the mode change alone does not materially affect per-request runtime cost. Folded into PERF-011 instead of standing alone.", "location": ".env.example" },
    { "observation": "GORM's default logger prints slow queries (>200ms) and errors to stdout.", "why_discarded": "This is a mild positive (partial observability exists), not a defect; noted in the report's scope and observability sections rather than reported as a finding.", "location": "common/database.go:57" }
  ],
  "adjacent_findings": [
    {
      "id": "SEC-001",
      "kind": "security",
      "confidence": "Confirmed",
      "risk": "High",
      "problem": "The JWT signing secret used to authenticate every user session is a hardcoded string literal committed directly in source.",
      "evidence": [
        { "statement": "const JWTSecret = \"A String Very Very Very Strong!!@##$!@#$\", marked with a #nosec G101 suppression comment.", "kind": "static", "file": "common/utils.go", "line": 41 },
        { "statement": "GenToken signs every issued token with this constant; AuthMiddleware verifies every incoming token against the same constant.", "kind": "static", "file": "users/middlewares.go", "line": 60 }
      ],
      "recommendation": "Load the signing secret from an environment variable or secrets manager at startup, fail loudly if unset outside development, and rotate it for any deployment that has run with the current value.",
      "location": "common/utils.go:41-42",
      "assessed_properly_by": "A dedicated security review or secrets-management audit"
    },
    {
      "id": "COR-001",
      "kind": "correctness",
      "confidence": "High",
      "risk": "Low",
      "problem": "setPassword discards the error from bcrypt.GenerateFromPassword; a failure leaves the account saved with an empty password hash instead of surfacing an error.",
      "evidence": [
        { "statement": "passwordHash, _ := bcrypt.GenerateFromPassword(bytePassword, bcrypt.DefaultCost) discards the error.", "kind": "static", "file": "users/models.go", "line": 63 },
        { "statement": "UsersRegistration saves the user model immediately after, with no check that the password was actually set.", "kind": "static", "file": "users/routers.go", "line": 85 }
      ],
      "recommendation": "Propagate the error from setPassword through Bind and return a validation error instead of silently proceeding.",
      "location": "users/models.go:57-66",
      "assessed_properly_by": "A correctness-focused review or a broader search for discarded error values across the codebase"
    }
  ]
}
```
```

---

## 11. Notes on this review

- Findings are classified by evidence grade; `Confirmed` requires a cited runtime artifact, and none exists in this repository — the highest grade reached by any finding here is `High`, reflecting that the mechanisms are unambiguous from code while their real-world magnitude is not measured.
- No runtime metric in this report was estimated or assumed. Every count that appears (the "up to 22 round trips" derivation, the "2x" middleware duplication) is explicitly labelled as a derivation showing its inputs, per Hard Rule 1.
- Recommendations state their trade-offs and their validation path throughout. Where a recommendation's benefit depends on a product decision rather than a pure engineering trade-off (PERF-003's `articlesCount` semantics), that is stated explicitly rather than presented as a free optimization.
