# Performance Review: golang-gin-realworld-example-app

**Date:** 2026-10-07
**Mode:** Full review (whole repository, commit `626c372d259472148d93303f74aa9b9a1cdcef24`)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0

---

## 1. Decision summary

### Overall assessment

A single-process Gin + GORM service on an embedded SQLite file. The reading paths that every visitor hits (article list, feed, comments) have three problems that make each other worse. First, the list endpoints accept any page size, including none at all (PERF-003). Second, every returned article or comment runs its own "is the viewer following this author" query (PERF-001). Third, that query, along with most other filter queries, has no supporting index (PERF-002). Separately, the author/favorited list filters can issue a write while their own read transaction holds the SQLite file lock. Given SQLite's single-writer locking, that can stall or silently empty the response, and while the write waits it can block other readers (PERF-004). No runtime evidence exists, so everything below comes from reading the code. The service is "partially instrumented": Gin's access log and GORM's slow-query log exist, but there are no metrics, traces, or query counts. Overall review confidence: **Medium**.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-003 (P1)** | Clamp `limit` to a server-side maximum (reject negatives) in `FindManyArticle`/`GetArticleFeed`, and paginate comment lists | Cheap (quick-win); removes the client-controlled multiplier that drives PERF-001 and PERF-002 |
| 2 | **PERF-001 (P1)** | Batch the follow-status lookup: one `followed_by_id = ? AND following_id IN (...)` query per response instead of one per article/comment | Removes the per-row query; the code shows it unambiguously (High confidence) |
| 3 | **PERF-004 (P1)** | Stop calling `GetArticleUserModel` (which does `FirstOrCreate`) on a separate connection inside the open read transaction in `FindManyArticle`; drop the read transactions or do a read-only lookup | Plausible system-wide stall or wrong empty result on an anonymous GET |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is this deployed to serve real traffic on SQLite, and at what request rate? (repo docs say SQLite is "for development/testing") | Whether any finding is above P3 in practice. PERF-004 and PERF-002 severity | Owner answer; deployment manifest |
| Row counts of `follow_models`, `favorite_models`, `article_models`, `comment_models`, `tag_models` | PERF-002 confidence/severity, PERF-005 | `SELECT COUNT(*)` per table on a copy of the DB file |
| Effective busy timeout and journal mode of the deployed DB | Whether PERF-004 shows up as a stall or as a silently empty response | `PRAGMA busy_timeout; PRAGMA journal_mode;` |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-002 | `safe-on-production` | `sqlite3 data/gorm.db "EXPLAIN QUERY PLAN SELECT * FROM follow_models WHERE following_id = 1 AND followed_by_id = 2 AND deleted_at IS NULL ORDER BY id LIMIT 1;"` | A `SCAN` (or a search on `idx_follow_models_deleted_at` only) confirms the full-scan mechanism; `SEARCH ... USING INDEX` on the filter columns refutes it |
| PERF-004 | `safe-on-production` | `sqlite3 data/gorm.db "PRAGMA journal_mode; PRAGMA busy_timeout;"` | `delete` mode confirms readers can be blocked during the wait. Note that the busy timeout is set per connection by the driver, so the CLI value does not show the app's value |
| PERF-001 | `not-safe-on-production` | `GIN_MODE=debug go test ./articles/ -run TestArticleListWithFilters -v` (the test DB opens with GORM `logger.Info`, so every SQL statement is printed). Count the `follow_models` SELECTs per `/api/articles` request | Count = number of articles returned confirms N+1; a constant count refutes it |

---

## 2. Scope and method

**Reviewed:** All Go sources: `hello.go`, `common/`, `users/`, `articles/` (models, routers, serializers, validators, middlewares); `go.mod`; CI workflow; `scripts/`; `readme.md`; `AGENTS.md`. I skimmed the unit tests for coverage of the trigger paths.
**Not reviewed:** `FRONTEND_INSTRUCTIONS.md`, `MOBILE_INSTRUCTIONS.md`, `BACKEND_INSTRUCTIONS.md` (spec prose, not runtime code), the Postman collection contents, `logo.png`. `.env.example` is a secret-pattern file: I noted that it exists and did not open it.

**Evidence available:** Partially instrumented. `gin.Default()` (`hello.go:35`) installs the access logger, which logs per-request latency to stdout. GORM's default logger has a slow-SQL threshold and logs errors. There are no metrics, tracing, pprof, benchmarks, load tests, SLOs, or committed query plans. Nothing was supplied at runtime.

**Ranking method:** Structural signals only. No runtime data was available, so this ranking is inference.

**Reference depth:** SQLite, Go, and REST have deep support. The detector flagged gRPC only through an indirect `protobuf` dependency in `go.sum`/`go.mod`. No gRPC service exists in the code, so I did not apply it.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every non-test Go source file read in full. Tests skimmed only for the paths in question |
| **Critical paths** | 6 analyzed / 6 identified (article list, feed, single article, comment list, tag list, authenticated write paths) |
| **Shared resources** | 3 analyzed / 3 identified (SQLite database file and its lock, `database/sql` pool, the single Go process) |
| **Technology support** | Go: deep · SQLite: deep · Gin/REST: deep · GORM: generic, covered only by the ORM category principles |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium**. The code was fully covered, but there is no workload or runtime data |

Review confidence is not finding confidence. Several findings below are High confidence on mechanism, while their real-world importance depends on unknown traffic and data volume.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production deployment (is SQLite used beyond dev? how many processes?) | No evidence exists: no Dockerfile, manifests, or Procfile | Owner answer, or deployment config |
| Request rate and read/write mix | No evidence exists | Gin access logs aggregated per route |
| Table row counts and growth | No evidence exists: no seeds, no retention jobs | Row counts from the DB file |
| The driver's effective `busy_timeout` and `journal_mode` | No evidence in repo: the DSN is a bare path (`common/database.go:57`), so library defaults apply | `PRAGMA` reads through the app's own connection |
| GORM-specific planner/loader behavior beyond the documented API | Technology unsupported at depth (GORM has no dedicated reference) | SQL logging (`logger.Info`) in staging |

---

## 3. Architecture overview

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| HTTP server | Gin | v1.10.0 (`go.mod`) | deep (REST) | All entry points, `hello.go` |
| Runtime | Go | `go 1.21` (`go.mod`); CI tests 1.21–1.23 | deep | Single process |
| ORM | GORM | v1.25.12 | generic | All data access; `AutoMigrate` at boot |
| Datastore | SQLite via `gorm.io/driver/sqlite` v1.5.7 / `mattn/go-sqlite3` v1.14.22 | file `./data/gorm.db` (`DB_PATH`) | deep | Sole datastore |
| Auth | golang-jwt v5.2.1, bcrypt | — | — | JWT HS256; bcrypt at default cost |

Entry points: 19 route registrations across 4 groups (`hello.go:41-60`, `articles/routers.go:13-36`, `users/routers.go:11-26`). There are no consumers, scheduled jobs, or outbound HTTP calls.

**Shared resources:** a single SQLite database file (one writer at a time; rollback-journal mode unless configured otherwise, and no PRAGMA is set in the repo). A `database/sql` pool with `SetMaxIdleConns(10)` and no `SetMaxOpenConns` (`common/database.go:65`). One Go process. Every request does at least one DB read in `AuthMiddleware` when a token is present.

---

## 4. Workload model

**Known**

- Default page size is 20 for list and feed. It is parsed from the client `limit` with no maximum, and a negative value is accepted. Source: `articles/models.go:187-190`, `articles/models.go:275-278`.
- Comment listing has no pagination at all. Source: `articles/models.go:164-168`, `articles/routers.go:228-242`.
- Tag listing returns every row of `tag_models`. Source: `articles/models.go:170-175`.
- Read-heavy surface: 7 GET routes against 12 mutating routes registered, but the frontend-facing hot paths (home page list and tags, article page and comments) are the anonymous GETs. Source: router files.
- Indexes declared: only `article_models.slug`, `tag_models.tag`, `user_models.email` (unique) and the `deleted_at` index that `gorm.Model` adds. Source: struct tags in `articles/models.go`, `users/models.go`.
- No retention or deletion job exists for any table. Follows, favorites, comments, and tags only grow, apart from user-initiated deletes. Source: whole repo.
- DSN is a bare file path, and no PRAGMAs are set. Source: `common/database.go:57`.

**Assumed**

- The service runs as one process against one SQLite file, as the README run instructions describe. Affects: PERF-004.
- `follow_models` and `favorite_models` are the fastest-growing tables, because they are written by per-user social actions. Affects: PERF-001, PERF-002.
- The anonymous article list and tag list are the most frequently hit routes, since they back the RealWorld home page. Affects: PERF-001, PERF-003, PERF-005.

**Unknown**

- Request rate per route; p95/p99 latency; whether there is any SLO.
- Row counts for every table.
- Whether any reverse proxy caps the `limit` query parameter.
- Effective `busy_timeout`/`journal_mode` on the deployed DB.

**Derived**

- Queries per default anonymous `GET /api/articles` ≈ a fixed set plus **one `follow_models` SELECT per returned article**. With the default `limit`, that is 20 extra queries. From: `articles/models.go:189` (default 20), `articles/serializers.go:134-140` (per-article loop), `articles/serializers.go:103` → `users/serializers.go:35` → `users/models.go:121-129`. The fixed set is: BEGIN, COUNT, article page, preloads for author/user/tags, COMMIT, batched favorite counts, plus 1–2 viewer queries when logged in.
- Rows visited by those per-article queries ≈ returned articles × `follow_models` rows, if each one scans. From: the derivation above and the missing index on `following_id`/`followed_by_id` (`users/models.go:36-42`).
- `GET /api/articles/:slug/comments` issues one `follow_models` SELECT per comment, and the number of comments is unbounded. From: `articles/serializers.go:174-180`, `articles/models.go:164-168`.

**Measured**

- None. No benchmark, trace, query plan, or log export was supplied or committed.

### Questions that would change the ranking

I would have asked these. Nobody was available, so they are unanswered and the review proceeds under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is this service serving real users on SQLite (one process, one file), and roughly what request rate do the list endpoints see? | severity, recommendation | If it is a dev/demo only, every PERF item drops to P3 and the advice becomes "fix before production". If production, PERF-004 and PERF-003 stay P1 and an engine change becomes a real alternative |
| 2 | high | Roughly how many rows are in `follow_models`, `favorite_models`, `article_models`, `comment_models`? | severity, confidence | Small tables (scan fits in the page cache) would bound PERF-002 to Low. Large or growing tables raise its confidence to High |
| 3 | high | Does anything in front of the app (proxy, gateway) cap or reject large/negative `limit` values? | severity | A cap would bound PERF-003 to Medium/Low and limit PERF-001's amplification |
| 4 | medium | How often are write endpoints (favorite, follow, comment, article create) hit concurrently? | severity, recommendation | High write concurrency makes PERF-004's lock wait more likely and makes WAL mode or a server DB worth considering |
| 5 | medium | Is there a specific complaint (a slow profile page, "database is locked" errors) behind this review? | recommendation | The review would be reorganised around confirming that symptom |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` (anonymous) | yes | ~7 fixed + 1 per article (follow check) | **no** (`limit` uncapped, negative = no LIMIT) | access-log latency only | Hottest public path; PERF-001/002/003; author/favorited branches PERF-004 |
| 2 | `GET /api/articles/:slug/comments` | yes | ~4 fixed + 1 per comment | **no** (no pagination) | access-log only | PERF-001/003 |
| 3 | `GET /api/articles/feed` (auth) | yes | follows lookup + IN-list + count + page + 1 per article | `limit` uncapped | access-log only | `author_id IN` unindexed (PERF-002) |
| 4 | `GET /api/tags` | yes | 1 full-table SELECT | **no** | access-log only | PERF-005 |
| 5 | `GET /api/articles/:slug` | yes | ~4 preload + 4 per-response (favorited, count, viewer, follow) | yes (single row) | access-log only | Constant work; per-query scans from PERF-002 |
| 6 | Authenticated writes (favorite, follow, comment, create) | yes | double auth lookup + 1–3 writes | yes | access-log only | PERF-006; single-writer SQLite |

**Amplification points:** per-article and per-comment `isFollowing` queries (PERF-001), multiplied by an uncapped page size (PERF-003), each potentially a table scan (PERF-002).

**Paths deliberately not analyzed in depth:** `/api/ping` (constant). Registration and login are bounded by bcrypt's deliberate CPU cost, which is a security property and not an inefficiency (see Considered and not reported). `AutoMigrate` at startup runs once.

---

## 6. Layer analysis

### 6.1 Application
The serializers do data access. `ProfileSerializer.Response` runs a query on each call, and it is called once per row by the list and comment serializers. The batch helpers `BatchGetFavoriteCounts` and `BatchGetFavoriteStatus` show the authors already fixed this pattern for favorites but not for follow status. Auth middleware is registered twice on authenticated routes (PERF-006).

### 6.2 API
Page size is a default, not a maximum (PERF-003). Comment lists and the tag list have no pagination. Gin runs in debug mode unless `GIN_MODE=release` is set (`readme.md:92`). Debug mode only adds route-registration logging at startup, so it is not material.

### 6.3 Data access and datastore
- Missing indexes on every foreign-key and filter column used by hot queries (PERF-002).
- Read paths wrapped in explicit transactions (`articles/models.go:192`, `:280`), with an out-of-transaction `FirstOrCreate` inside one of them (PERF-004).
- The pool has no `MaxOpenConns`. On SQLite, more connections add no write concurrency. See Considered and not reported.
- Offset pagination plus `COUNT(*)` on each list call. See Considered and not reported.

### 6.4 Observability
Gin's access logger records per-request latency, and GORM logs slow SQL and errors. There are no per-route latency metrics, no query-count visibility in production, no pool wait metrics, and no pprof. Every validation below has to be done with test-time SQL logging or ad-hoc measurement (PERF-007).

---

## 7. Findings

### PERF-001 — One follow-status query per article or comment in list responses (N+1)

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `users/serializers.go:35` (`ProfileSerializer.Response`) |
| **Tags** | — |

**Problem**
Each article in `GET /api/articles` and `/feed`, and each comment in `GET /api/articles/:slug/comments`, serializes its author through `ProfileSerializer.Response`. That method runs `isFollowing`, which is a separate `SELECT ... FROM follow_models`. The query count therefore grows with result size.

**Performance principle**
Do not repeat a per-item lookup that can be answered for the whole set in one round trip.

**Evidence**
- `articles/serializers.go:134-140`: the loop over `s.Articles` calls `ResponseWithPreloaded` for each article.
- `articles/serializers.go:103` → `ArticleUserSerializer.Response` (`:38-41`) → `users/serializers.go:35` `myUserModel.isFollowing(self.UserModel)`.
- `users/models.go:121-129`: `isFollowing` runs `db.Where(FollowModel{...}).First(&follow)`, one query per call.
- `articles/serializers.go:174-180`: the same chain per comment.
- Contrast with `articles/serializers.go:128-132`, where favorites were already batched ("Batch fetch favorite counts and status"). That shows the pattern is recognised but follow status was missed.
- An aggravating factor, which I did not verify in the repo: GORM's default logger reports `record not found` from `First` as an error. That means every "not following" result probably also writes a log line, with the SQL, to stdout.
- No runtime evidence.

**Impact**
- Position: critical path (anonymous home-page list, article comments).
- Frequency: per item.
- Growth: O(n) in returned rows (default 20, uncapped per PERF-003; comments unbounded).
- Blast radius: service, because all queries go to one SQLite file and one process.
- Derivation: a default list request issues 20 follow queries on top of its fixed queries (`articles/models.go:189`).

**Conditions**
This matters when list/feed/comment endpoints are a meaningful share of traffic, and when page sizes or comment counts go beyond a handful. I assume the anonymous list is the most-hit route because it backs the home page, but traffic is unknown.

**Counter-evidence**
- I searched for any memoisation of follow status per request, or a batch helper for follows: none found (`no-effect`).
- Many articles on one page share an author, but there is no deduplication by author ID: `isFollowing` is called once per article, not once per distinct author (`no-effect`).
- SQLite is embedded, so the per-query cost is an in-process call rather than a network round trip. Each query is cheaper than on a client-server DB, but each still pays statement preparation, lock acquisition, and the scan from PERF-002. This is reflected in severity staying at High rather than Critical (`bounds-impact`).

**Why this might not matter**
With an embedded DB, small tables, and the default page of 20, the 20 extra queries may cost less than a millisecond each and be invisible next to JSON encoding. If traffic is demo-level, there is no user-visible effect.

**Recommendation**
Collect the distinct author user IDs in the page and run one `SELECT following_id FROM follow_models WHERE followed_by_id = ? AND following_id IN (?)` (skip it entirely for anonymous viewers). Then pass the resulting set into a `ResponseWithPreloaded`-style author serializer. This mirrors the existing `BatchGetFavoriteStatus`. It removes the per-row query rather than hiding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: batch follow status per response (one IN query) | **preferred** | Removes the work; same pattern already used for favorites; constant query count |
| B: deduplicate by author within a page (cache in the request) | | Helps only when authors repeat; still O(distinct authors) |
| C: cache follow status across requests | | Needs invalidation on follow/unfollow; hides the query instead of removing it; no hit-rate evidence |

**Trade-offs**
The serializer API changes. The single-article path and the comment path need the same plumbing. The IN-list size is bounded by page size (after PERF-003).

**Validation**
- Baseline: run the article tests with SQL logging (the test DB uses `logger.Info`, `common/database.go:81`) and count `follow_models` SELECTs per list request. Safe-on-production: no (local/test only).
- Expectation: the count drops from (articles returned) to 1 per request, regardless of page size.
- Falsifier: if the count was already constant, or if latency is unchanged once the count drops, the path was not query-bound and the finding's impact was overstated.
- Guard: a test that asserts the number of SQL statements for `GET /api/articles?limit=N` does not depend on N, for example using a GORM callback counter.
- Commands:
  - `not-safe-on-production` `go test ./articles/ -run TestArticleListWithFilters -v`: prints every SQL statement from the test DB logger; count the `follow_models` SELECTs.

---

### PERF-003 — Client-controlled, uncapped page size on list and feed; comments unpaginated

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
`limit` is parsed straight from the query string with no maximum. A negative value, such as `?limit=-1`, makes GORM omit the `LIMIT` clause, so the whole table is returned. The comment list has no limit at all. One anonymous request can therefore load every article with its author, user, and tags, then run PERF-001's per-row query for each one.

**Performance principle**
Every request's work must have a server-enforced upper bound that does not depend on data the server does not control.

**Evidence**
- `articles/models.go:187-190`: `limit_int, errLimit := strconv.Atoi(limit)`, defaulting only when parsing fails.
- `:259`: `tx.Offset(offset_int).Limit(limit_int).Preload(...).Find(&models)`.
- The same applies in `GetArticleFeed` (`:275-278`, `:302`) and in the tag/author/favorited branches (`:199`, `:222`, `:243`).
- `articles/models.go:164-168`: comments are loaded via `Association("Comments").Find` with no limit.
- GORM's handling of negative limits (no LIMIT emitted) comes from library behavior. I have not verified it in the repo, so the validation below checks it.
- The routes are anonymous (`articles/routers.go:26-31`, registered before `AuthMiddleware(true)` in `hello.go:44`).

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in total articles (or in comments per article), plus PERF-001's per-row query on each row.
- Blast radius: system-wide. Everything is in one process with one SQLite file. The first read holds the transaction's SHARED lock while it loads, and the full result set is buffered in memory and then JSON-encoded.

**Conditions**
This matters once the article table, or one article's comment count, is large enough that a full load is noticeable. Any client, including an unauthenticated one, can trigger it. I assume no proxy caps the parameter; nothing in the repo shows one.

**Counter-evidence**
- Searched for a maximum page size, a validator on `limit`, or middleware: none found (`no-effect`).
- `Description` and `Body` are capped at 2048 by validators (`articles/validators.go:13-14`), so per-row size is bounded, but row count is not (`no-effect` on growth).

**Why this might not matter**
With a few hundred articles, a full load is still cheap, and well-behaved RealWorld clients send `limit=10` or `limit=20`. It only becomes a problem as data grows or if someone sends a large value.

**Recommendation**
Clamp `limit` to `[1, MAX]` and `offset` to `>= 0` in both `FindManyArticle` and `GetArticleFeed`, with MAX chosen by the owner (the RealWorld frontend uses small pages). Add `limit`/`offset` to the comment list, or a hard cap. This bounds the work instead of making it cheaper.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: server-side clamp on `limit`/`offset`; paginate comments | **preferred** | Removes the unbounded case with a few lines; no behavior change for normal clients |
| B: rely on a reverse-proxy rule | | Not visible in the repo; easy to lose; does not cover comments |
| C: keyset pagination | | Also fixes deep-offset cost but is a larger API change; not needed to bound the work |

**Trade-offs**
Clients asking for more than MAX get a truncated page and need to paginate. The comment API gains parameters, which is an extension to the RealWorld spec.

**Validation**
- Baseline: `curl 'http://localhost:8080/api/articles?limit=-1'` against a local copy, and compare `articlesCount` with `len(articles)`. Not safe on production, since it executes the full load.
- Expectation: after the fix, `len(articles) <= MAX` for any `limit`.
- Falsifier: if `limit=-1` already returns at most 20 rows, GORM does emit a LIMIT and the "no limit" part of this finding is wrong. The large-positive-limit case would still stand.
- Guard: a unit test that asks for `limit=-1` and `limit=100000` and asserts a capped length.
- Commands:
  - `not-safe-on-production` `curl -s 'http://localhost:8080/api/articles?limit=-1' | head -c 300`: checks whether a negative limit removes the LIMIT clause (run locally).

---

### PERF-004 — Write on a second connection inside an open SQLite read transaction (author/favorited filters)

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `articles/models.go:216` (`FindManyArticle`) |
| **Tags** | needs-measurement |

**Problem**
`FindManyArticle` opens a transaction (`db.Begin()`, `:192`) and reads `user_models` inside it (`:215`). Under SQLite's default rollback journal, that read takes a SHARED lock which is held until commit. It then calls `GetArticleUserModel` (`:216`, `:238`), which runs `FirstOrCreate` on the global DB handle, so on a different pooled connection. When the named user has no `article_user_models` row yet, that connection INSERTs. To commit, it needs an exclusive file lock, which cannot be granted while this request's own transaction holds SHARED. The INSERT therefore waits up to the driver's busy timeout and then fails. The error is ignored, `articleUserModel.ID` stays 0, and the endpoint returns an empty list.

**Performance principle**
Never hold a lock while waiting on a resource that the lock itself blocks. Keep read paths free of writes.

**Evidence**
- `articles/models.go:192` `tx := db.Begin()`; `:215` `tx.Where(users.UserModel{...}).First(&userModel)`; `:216` `GetArticleUserModel(userModel)`; `:237-238` same for `favorited`.
- `articles/models.go:59-62`: `GetArticleUserModel` uses `common.GetDB()` (not `tx`) and `FirstOrCreate`.
- `common/database.go:57`: the DSN is a bare path, so the journal mode is the default (rollback journal) and no busy timeout is set by the app.
- A row is created lazily only when `GetArticleUserModel` is called for that user (create article, comment, favorite, or a logged-in list view), so users who never did any of those have no row.
- Unit tests exercise `?author=` only for a user who already has a row (`articles/unit_test.go:424`) or does not exist at all (`:1116`, where `userModel.ID == 0` returns early, `articles/models.go:56-58`). The trigger path is untested.
- No runtime evidence.

**Impact**
- Position: critical path (anonymous profile page → `?author=` / `?favorited=`).
- Frequency: rare (once per user without a row, but repeated on every such request until a row is created elsewhere, because the failed insert never commits).
- Growth: O(1).
- Blast radius: system-wide, if the busy timeout is non-zero. While the INSERT waits for the exclusive lock it holds SQLite's PENDING lock, which blocks new readers from every request until the wait ends.
- If the effective busy timeout is zero, the result is not a stall but a fast, wrong, empty response (see COR note in Recommendation).

**Conditions**
The deployment uses SQLite in the default journal mode, and someone requests a profile/favorites list for a registered user who has never authored, commented, favorited, or browsed while logged in. That is plausible for "view another user's profile" in the RealWorld UI. Frequency is unknown.

**Counter-evidence**
- Searched for `PRAGMA journal_mode=WAL` or a DSN option: none (`no-effect`). In WAL mode the stall would not occur, because writers do not wait for readers, but no WAL setting exists.
- The size of the effect depends on a driver default that is not visible in the repo: zero means a fast failure, non-zero means a stall. I have not run it to check, which is why confidence is Medium (`lowers-confidence`).
- When the user row already exists, `FirstOrCreate` only reads, and readers do not conflict. This bounds frequency to the first-touch case (`bounds-impact`, reflected in frequency `rare` and severity High rather than Critical).

**Why this might not matter**
Most users who matter enough to be viewed have probably authored something, so their row exists. If the driver's busy timeout is zero, there is no stall, only a wrong empty page, which is a correctness problem more than a performance one.

**Recommendation**
Remove the write from the read path. Look up the `article_user_models` row read-only (`tx.Where(...).First`, no create) inside the same transaction; if it is absent, the author has no articles, so return empty without inserting. Separately, reconsider whether these read paths need an explicit transaction at all. It only buys a consistent count and page, at the cost of holding SHARED across several statements.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: read-only lookup through `tx`, no create on read paths | **preferred** | Removes both the lock inversion and the hidden write; also fixes the wrong-empty-result bug |
| B: switch to WAL mode (`_journal_mode=WAL` in DSN) | | Removes reader/writer blocking generally, but adds checkpoint management and still leaves a write on a GET path |
| C: set an explicit busy timeout | | Only chooses between a stall and an error; does not fix the inversion |

**Trade-offs**
Option A changes the semantics of `GetArticleUserModel` for read paths, so the codebase would need separate "get" and "get-or-create" helpers. Option B adds a WAL file and checkpointing, and needs filesystem support for shared memory.

**Validation**
- Baseline: in a local test, register user X (no other activity) and then time `GET /api/articles?author=X` while another goroutine issues a read. Not safe on production.
- Expectation: before the fix, the request takes about the busy timeout and returns an empty list, or fails fast and returns empty. After the fix it returns promptly with the correct empty list, and no `article_user_models` INSERT appears in the SQL log.
- Falsifier: if the request returns promptly and the INSERT succeeds, the lock-inversion reading is wrong, for example because the driver runs in WAL mode or uses a different transaction locking mode.
- Guard: a test for `?author=` on a freshly registered user, asserting status, latency under a small bound, and that a row now exists or the result is empty without error.
- Commands:
  - `safe-on-production` `sqlite3 data/gorm.db "PRAGMA journal_mode;"`: confirms whether the rollback-journal locking model applies.

---

### PERF-002 — No indexes on the foreign-key and filter columns used by hot queries

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `users/models.go:36` (`FollowModel`), also `articles/models.go:23-52` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The schema comes entirely from `AutoMigrate` over the struct definitions, which declare indexes only on `slug`, `tag`, `email` and `gorm.Model`'s `deleted_at`. Every lookup by `follow_models.(following_id, followed_by_id)`, `favorite_models.(favorite_id, favorite_by_id)`, `article_models.author_id`, `comment_models.article_id`, and `article_user_models.user_model_id` therefore has no usable index and likely scans the table. PERF-001 runs one of these scans per returned row.

**Performance principle**
A lookup on a request path should cost in proportion to its result, not to the size of the table.

**Evidence**
- `users/models.go:36-42`: `FollowModel` has no index tags.
- `articles/models.go:23-29`, `31-37`, `45-52`: `ArticleUserModel.UserModelID`, `FavoriteModel.FavoriteID/FavoriteByID`, `CommentModel.ArticleID` have no index tags. `ArticleModel.AuthorID` (`:18`) has none either.
- There is no migration directory and no raw `CREATE INDEX` anywhere (grep for "index" across `*.go`).
- Hot queries that filter on these columns:
  - `users/models.go:124` (per row, PERF-001)
  - `articles/models.go:60` (per request when logged in)
  - `:70`, `:79` (single-article view)
  - `:98-102`, `:119` (every list response)
  - `:166` (comments)
  - `:241`, `:291`, `:300-302` (favorited filter, feed)
- SQLite does not create indexes for foreign-key columns on its own.
- No runtime evidence. No query plan was supplied.

**Impact**
- Position: critical path.
- Frequency: per request and, through PERF-001, per item.
- Growth: O(n) in the size of each table, per query.
- Blast radius: service (one SQLite file).
- Derivation: rows visited for follow checks on one list request ≈ returned articles × `follow_models` rows (from PERF-001's per-row query and this missing index).

**Conditions**
This matters when `follow_models`, `favorite_models`, `article_models`, or `comment_models` grow past the point where scanning them is cheap relative to the request. Row counts are unknown, so I assume they grow without bound, since there is no retention job. Confidence is capped at Medium because the impact depends on data volume.

**Counter-evidence**
- Searched for indexes in struct tags, migrations, and raw SQL: none beyond those listed (`no-effect`).
- GORM many2many join table `article_tags` gets a composite primary key, so tag-by-article preloads are indexed. Article-by-tag lookups may not use its leading column (`bounds-impact` for the tag path only).
- With small tables, a SQLite scan is a fast in-memory B-tree walk. That is why confidence is Medium and not High (`lowers-confidence`).

**Why this might not matter**
At demo scale (hundreds of rows), the scans cost microseconds and adding indexes is pure write overhead.

**Recommendation**
Add composite indexes matching the actual predicates. Use `index:` tags so `AutoMigrate` creates them:
- `follow_models(followed_by_id, following_id)`, which also serves the batched query from PERF-001
- `favorite_models(favorite_id, favorite_by_id)` and `(favorite_by_id)`
- `article_models(author_id, updated_at)`
- `comment_models(article_id)`
- `article_user_models(user_model_id)`, ideally unique, which also prevents duplicate rows from concurrent `FirstOrCreate`

Confirm each one with `EXPLAIN QUERY PLAN` before and after.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: targeted composite indexes, verified by query plan | **preferred** | Makes each lookup proportional to its result; small, declarative change |
| B: index every FK column individually | | Simpler, but single-column indexes do not serve the two-column follow/favorite predicates as well |
| C: cache follow/favorite state | | Hides the scans; needs invalidation; no hit-rate evidence |

**Trade-offs**
Each index adds write cost on follow, favorite, comment, and article creation, which with SQLite's single writer slightly lengthens write-lock hold time. It also adds file size and a one-time build cost on existing data, and `AutoMigrate` builds it while holding the write lock at startup.

**Validation**
- Baseline: `EXPLAIN QUERY PLAN` for each hot predicate on a copy of the DB. Safe on production (plan only), but better run on a copy.
- Expectation: plans change from `SCAN` (or a search on the `deleted_at` index) to `SEARCH ... USING INDEX` on the filter columns.
- Falsifier: if plans already show `SEARCH` on the filter columns, an index exists that I did not find, and the finding is refuted.
- Guard: a test that runs `EXPLAIN QUERY PLAN` for the follow and favorite lookups and asserts no full `SCAN`.
- Commands:
  - `safe-on-production` `sqlite3 data/gorm.db "EXPLAIN QUERY PLAN SELECT * FROM follow_models WHERE following_id = 1 AND followed_by_id = 2 AND deleted_at IS NULL ORDER BY id LIMIT 1;"`: shows whether the follow check scans.
  - `safe-on-production` `sqlite3 data/gorm.db "EXPLAIN QUERY PLAN SELECT favorite_id, COUNT(*) FROM favorite_models WHERE favorite_id IN (1,2,3) AND deleted_at IS NULL GROUP BY favorite_id;"`: shows whether the batched favorite count scans.
  - `safe-on-production` `sqlite3 data/gorm.db ".indexes"`: lists the indexes that actually exist.

---

### PERF-005 — Tag list returns every tag ever created

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `articles/models.go:173` (`getAllTags`) |
| **Tags** | scalability-risk |

**Problem**
`GET /api/tags` runs `SELECT * FROM tag_models` with no limit and no filter for tags still attached to an article. The response grows with every distinct tag ever created, and tags are never deleted.

**Performance principle**
Response size and query cost should be bounded by what the client can use, not by accumulated history.

**Evidence**
- `articles/models.go:170-175`. Tags are created on demand in `setTags` (`:310-350`) and never removed.
- Article deletion (`:358-362`) soft-deletes the article only.
- No runtime evidence.

**Impact**
- Position: critical path (anonymous; the RealWorld home page loads it on every visit).
- Frequency: per request.
- Growth: O(n) in distinct tags.
- Blast radius: endpoint.

**Conditions**
This matters only once the number of distinct tags is large. Article creation lets callers send arbitrary tag lists (`articles/validators.go:15`, no max), so growth is driven by users. I assume it grows slowly unless abused.

**Counter-evidence**
- The query is one indexed-free sequential read of a narrow table, with no N+1 (`bounds-impact`, which is why Medium and not High).
- Searched for pagination or caching: none (`no-effect`).

**Why this might not matter**
Tag vocabularies are usually small, and one sequential read of a narrow table is cheap at realistic sizes.

**Recommendation**
Return the most-used N tags, for example by joining `article_tags` with GROUP BY and ORDER BY count with a LIMIT, or at least cap the result. Optionally cap tags per article in the validator.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: top-N popular tags with LIMIT | **preferred** | Bounds the work and matches the UI's "popular tags" purpose |
| B: cache the full tag list in-process with a TTL | | Removes repeated reads but keeps the unbounded payload; adds staleness |

**Trade-offs**
Rarely used tags are no longer listed. The popularity query is more complex than a plain SELECT, but it is bounded.

**Validation**
- Metric: response size and row count of `GET /api/tags` compared with `SELECT COUNT(*) FROM tag_models`. Safe on production (read-only).
- Expectation: after the fix, the response length is at most N, independent of the table size.
- Falsifier: if the tag count stays small over time, this item has no impact and should be closed.
- Commands:
  - `safe-on-production` `sqlite3 data/gorm.db "SELECT COUNT(*) FROM tag_models;"`: current tag cardinality.

---

### PERF-006 — Auth middleware runs twice on every authenticated route

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
`v1.Use(users.AuthMiddleware(false))` (`:43`) and then `v1.Use(users.AuthMiddleware(true))` (`:48`) both stay in the handler chain of groups created after line 48. Every authenticated request therefore parses and verifies the JWT twice and runs `SELECT ... FROM user_models WHERE id = ?` twice (`users/middlewares.go:34`).

**Performance principle**
Do not repeat per-request work whose result is already available.

**Evidence**
- `hello.go:43`, `:48-52`.
- `users/middlewares.go:43-75`: each run calls `UpdateContextUserModel`, which runs `db.First(&myUserModel, id)`.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1), with one extra indexed primary-key lookup and one HMAC.
- Blast radius: endpoint.

**Conditions**
Applies to every authenticated request. The cost is constant and small.

**Counter-evidence**
The extra query is a primary-key lookup, which is cheap (`bounds-impact`, hence Low).

**Recommendation**
Have the strict middleware reuse the context value set by the optional one: if `my_user_id` is already set and non-zero, skip re-parsing. Alternatively, register the authenticated routes on a group that has only `AuthMiddleware(true)`.

**Trade-offs**
Minimal. Care is needed so that the 401 behaviour for a missing token is kept.

**Validation**
- Metric: `user_models` SELECTs per authenticated request in the test SQL log. Not safe on production (test only).
- Expectation: the count drops from 2 to 1.
- Falsifier: if the SQL log already shows one lookup, Gin deduplicates the chain and this finding is wrong.

---

### PERF-007 — No query-level or per-route metrics to validate any of the above

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `hello.go:35` (`main`) |
| **Tags** | needs-measurement |

**Problem**
The only runtime signals are Gin's per-request stdout access log and GORM's default slow-SQL/error log. There is no per-route latency histogram, no query count per request, no pool or lock-wait visibility, and no pprof. None of the improvements above can be measured in a deployed environment.

**Performance principle**
A system cannot be tuned in a direction it cannot measure.

**Evidence**
- `hello.go:35` `gin.Default()`; `common/database.go:57` (production DB opened with the default GORM logger config).
- Searched for prometheus, otel, and pprof: none.

**Impact**
- Position: offline (it does not slow requests).
- Frequency: per request (the missing signal).
- Growth: O(1).
- Blast radius: service (every finding's validation depends on it).

**Conditions**
Applies whenever the service runs beyond local development.

**Counter-evidence**
Gin's access log does include latency, so per-request timing can be recovered from logs (`bounds-impact`, hence Low).

**Recommendation**
Add a GORM callback that counts queries per request and records their duration, and log both with the access log. Optionally mount `net/http/pprof` on an internal port. Sequence this alongside PERF-003, before larger changes.

**Trade-offs**
A small per-query overhead, and pprof exposure must not be public.

**Validation**
- Metric: query count per request appears in logs. Safe on production.
- Expectation: list requests show a count that scales with page size before PERF-001's fix and stays constant after it.
- Falsifier: none needed. This is enabling work.

---

### Remaining findings

All findings are presented in full above.

### Considered and not reported

| Observation | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| `database/sql` pool has no `SetMaxOpenConns` on a SQLite file | SQLite DB / pool | `common/database.go:61-66`; no other pool config | More connections give no write concurrency on SQLite and can add `SQLITE_BUSY`, but write concurrency is unknown and no write-heavy path was found | "database is locked" errors appear in logs, or write traffic is shown to be concurrent |
| `COUNT(*)` plus `OFFSET` on every list request | `GET /api/articles` default branch | `articles/models.go:257-259` | Linear in article count, but a sequential count of an integer-keyed table is the cheapest scan type. Absent any size evidence it is dominated by PERF-001/002 | GORM slow-SQL log shows the count or deep-offset queries |
| bcrypt at `DefaultCost` on register/login | `POST /api/users`, `/login` | `users/models.go:63`, `:74` | Deliberate CPU cost for password hashing (a security property); bounded per request | Login rate is high enough that CPU saturation shows in profiles |
| Feed `IN` lists sized by follow count | `GET /api/articles/feed` | `articles/models.go:281-302`, `users/models.go:143-154` | Bounded by one user's followings; same unindexed `author_id` issue already covered by PERF-002 | Users with very large follow counts exist |

### Adjacent findings: outside performance scope

### SEC-001 — JWT signing secret is a hard-coded source constant

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `common/utils.go:41` |

**Problem**
The HMAC key used to sign and verify every JWT is a string constant committed to source (value not reproduced here). It is marked `#nosec`. A second constant (`RandomPassword`, `:42`) acts as a sentinel password that bypasses re-hashing on update.

**Evidence**
`common/utils.go:41-42`; used in `common/utils.go:45-57` and `users/middlewares.go:60`.

**Impact**
Anyone with repository access can mint a valid token for any user ID, so this is High risk if deployed as-is.

**Recommendation**
Load the secret from the environment or a secret store at startup and fail closed if it is absent. Rotate it, since the current value is public.

**Trade-offs**
Configuration is needed for every environment, and existing tokens are invalidated on rotation.

**Validation**
Confirm that the binary refuses to start without the secret configured.

**Would need**
A security review. Gosec already runs in CI with `-no-fail` (`.github/workflows/ci.yml`), so its result does not gate merges.

### COR-001 — Zero-valued struct conditions report `following`/`favorited` = true for anonymous viewers

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `users/models.go:124` (`isFollowing`); `articles/models.go:79` (`isFavoriteBy`) |

**Problem**
GORM struct conditions skip zero-valued fields. For an anonymous viewer, `FollowedByID: 0` (or `FavoriteByID: 0`) is dropped, so the query becomes "does anyone follow this author / favorite this article", and the result is reported as the viewer's own state.

**Evidence**
- `users/models.go:121-129`, where it is called with the anonymous `UserModel{}` set by `users/middlewares.go:45` (and `:30-38`).
- `articles/models.go:76-84`, called with `GetArticleUserModel` returning ID 0 for anonymous users (`:56-58`), via `articles/serializers.go:80`.
- The list path guards this (`BatchGetFavoriteStatus`, `:113`), but the single-article and profile paths do not.

**Impact**
Wrong UI state (follow/favorite buttons) for logged-out users. Medium risk because the data shown is wrong, but nothing leaks.

**Recommendation**
Short-circuit to `false` when the viewer ID is 0, or use explicit `Where("following_id = ? AND followed_by_id = ?", ...)` placeholders. The PERF-001 batched query would naturally do this.

**Trade-offs**
None material.

**Validation**
An anonymous `GET /api/profiles/:username` for a user someone follows should return `"following": false`.

**Would need**
Correctness-focused API tests for anonymous viewers.

### COR-002 — List pagination is applied before ordering, or without any ordering

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `articles/models.go:259` (`FindManyArticle`) |

**Problem**
The unfiltered list applies `Offset/Limit` with no `ORDER BY` (`:259`), so page contents are engine-order and not guaranteed stable. The tag and author branches take an unordered page of the association first (`:199`, `:222`) and only then sort that page by `updated_at` (`:210`, `:232`). The favorited branch does the same (`:241-252`). Pages are therefore not "most recent first" across pages.

**Evidence**
`articles/models.go:192-263`.

**Impact**
Clients can see duplicated or missing articles across pages, and the order does not match the RealWorld spec's newest-first expectation.

**Recommendation**
Apply `ORDER BY created_at DESC, id DESC` (or `updated_at` per spec) in the same query that applies `OFFSET/LIMIT`. Joining through `article_tags` or the author/favorite filters in one query also removes a round trip.

**Trade-offs**
The ordering needs index support (see PERF-002's `article_models(author_id, updated_at)`).

**Validation**
Create more than `limit` articles with known timestamps, page through them, and assert global order and no duplicates.

**Would need**
A correctness test suite for pagination.

---

## 8. Prioritized action plan

### P1: High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-003 | P1 | small | Quick-win; bounds the multiplier for PERF-001/002 |
| 2 | PERF-001 | P1 | medium | Removes the per-row query; a pattern for it already exists in the code |
| 3 | PERF-004 | P1 | small–medium | Removes a lock inversion with system-wide stall potential; also fixes wrong results |
| 4 | PERF-002 | P1 | small | Add indexes after the plans are measured. Sequenced after PERF-001 so the batched follow query's index can be designed once |

### P2: Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 5 | PERF-005 | P2 | small | Bounded payload for the home-page tag list |

### P3: Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 0 (sequenced first) | PERF-007 | P3 | small | Sequenced early, despite P3, because it is what makes every other change measurable |
| 6 | PERF-006 | P3 | small | Quick-win; constant per-request saving |

**If only one thing is done:** clamp `limit` and paginate comments (PERF-003). That puts a ceiling on how much work any single anonymous request can force.

---

## 9. Validation plan

### PERF-001
- **Baseline:** count `follow_models` SELECTs per `GET /api/articles?limit=N` in the test SQL log. Not safe on production.
- **Change:** one batched follow-status query per response.
- **Measurement:** SQL statements per request at N = 5 and N = 20.
- **Expectation:** follow queries go from N to 1. The latency improvement is unquantified until measured.
- **Falsifier:** the count is already constant, or latency is unchanged once the count drops.
- **Guard:** a query-count assertion test.
- **Commands:** `not-safe-on-production` `go test ./articles/ -run TestArticleListWithFilters -v`: SQL is printed by the test DB logger.

### PERF-003
- **Baseline:** `len(articles)` for `limit=-1` and for a large limit, on a local copy. Not safe on production.
- **Change:** clamp `limit`/`offset`; paginate comments.
- **Measurement:** returned row count.
- **Expectation:** the count is at most MAX for any input.
- **Falsifier:** `limit=-1` already returns at most 20 rows (GORM emitted a LIMIT).
- **Guard:** a unit test with hostile `limit` values.
- **Commands:** `not-safe-on-production` `curl -s 'http://localhost:8080/api/articles?limit=-1' | head -c 300`.

### PERF-004
- **Baseline:** time `GET /api/articles?author=<fresh user>` locally, with a concurrent reader. Not safe on production.
- **Change:** read-only lookup inside the transaction; no create on read paths.
- **Measurement:** request latency; whether an INSERT into `article_user_models` appears; latency of concurrent readers.
- **Expectation:** no INSERT, no lock wait, and correct results.
- **Falsifier:** the pre-fix request is fast and the INSERT succeeds, which would mean WAL or another locking mode is in effect.
- **Guard:** a regression test on a freshly registered author.
- **Commands:** `safe-on-production` `sqlite3 data/gorm.db "PRAGMA journal_mode;"`.

### PERF-002
- **Baseline:** `EXPLAIN QUERY PLAN` on each hot predicate. Safe on production, as a plan only.
- **Change:** composite indexes via `index:` tags.
- **Measurement:** plan shape (`SCAN` compared with `SEARCH USING INDEX`).
- **Expectation:** `SEARCH` on the filter columns.
- **Falsifier:** the plans already use such indexes.
- **Guard:** a plan-assertion test.
- **Commands:** see PERF-002. The `sqlite3 ... EXPLAIN QUERY PLAN` and `.indexes` commands are all safe on production.

### PERF-005
- **Baseline:** `SELECT COUNT(*) FROM tag_models` (safe on production).
- **Expectation:** the response is at most N after the fix.
- **Falsifier:** the tag count stays small.

### PERF-006
- **Baseline:** `user_models` lookups per authenticated request in the test SQL log (not safe on production).
- **Expectation:** 2 lookups become 1.
- **Falsifier:** only one lookup is observed before the fix.

### Instrumentation gaps to close first
1. Per-request query count and total DB time (GORM callback), logged with Gin's latency (PERF-007).
2. Run Gin in release mode with structured access logs, so per-route latency percentiles can be computed.
3. Optional: `net/http/pprof` on an internal-only listener.

---

## 10. Machine-readable output

Emitted alongside this report as `gin-treatment-1.json`, which conforms to `schemas/review.schema.json`. If the two disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade. None is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated. The only numbers come from repository files (default page size 20, field size limits), and the per-request query counts are derivations labelled as such.
- Library behaviors not visible in the repo are flagged at the point of use, each with a command or test that checks it: GORM's negative-limit handling, GORM's record-not-found logging, the go-sqlite3 default busy timeout, and SQLite's PENDING-lock semantics.
