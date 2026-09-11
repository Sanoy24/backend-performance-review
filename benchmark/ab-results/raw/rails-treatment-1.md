# Performance Review: rails-realworld

**Date:** 2026-09-10
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v0.6.0

---

## 1. Executive summary

### Overall assessment

This is a small, single-service Ruby on Rails 4.2.6 JSON API — the "gothinkster/rails-realworld-example-app" reference implementation of the RealWorld ("Conduit") API spec, per its own README. It uses SQLite as its only datastore, in every environment including production, with no cache, no broker, and no other services. Static review found three code-evidenced, high-priority performance findings that share a common shape — per-item work and unbounded work on the two list-serving endpoints, plus a structural mismatch between the declared connection-pool configuration and what the chosen datastore engine can actually provide — and one lower-priority observability/cost item. It also surfaced one real correctness bug (unrelated to performance) that will make every article deletion fail. The repository is uninstrumented: there is no metrics, tracing, query-count testing, or load-testing evidence anywhere in it, so every finding below is a structural inference from code, not a measurement.

### Most important findings

- **PERF-001 (P1)** — The two collection-returning views (article list/feed, comment list) fire a per-item database query for tags, "favorited" status, and "following" status instead of batching them, multiplying query count by the number of items returned.
- **PERF-002 (P1)** — `GET /api/articles` and `GET /api/articles/feed` accept a caller-supplied `limit` with no enforced maximum, and `GET /api/articles/:slug/comments` has no pagination at all — response size (and, combined with PERF-001, query count) is bounded only by table size or caller input.
- **PERF-003 (P1)** — SQLite is the declared production datastore, configured with a 5-connection pool as if it were a client-server engine, but SQLite allows exactly one writer to the file at a time by design; no WAL mode is configured, so writers also block readers.
- **PERF-004 (P3)** — `config/environments/production.rb` sets `config.log_level = :debug`, the most verbose logging level, for the production environment.

### Highest-risk bottlenecks

The structural ceiling on this system is SQLite itself (PERF-003): no in-repo tuning changes that number, because the constraint is the engine choice, not a configuration value that can be raised. It is a *current* risk the moment more than one write is in flight concurrently, not only a future one — but this review has no evidence of what current write concurrency actually is. Separately, PERF-001 and PERF-002 compound each other: an unbounded `limit` multiplies an already-linear per-item query cost, and because writes and reads share the same single SQLite connection/file, a slow or huge read request can hold a pool connection long enough to affect concurrent write requests too.

### Major unknowns

- Actual production request rate and concurrency, for any endpoint — would change every finding's severity from an assumed worst case to a measured one.
- Real deployment topology (Puma worker/thread counts, instance count): no `config/puma.rb`, `Procfile`, `Dockerfile`, or orchestration manifest exists in this repository, so the connection-pool arithmetic in `application/connection-pools.md` cannot be completed — would let PERF-003 be scored with real numbers instead of a structural argument.
- Real row counts for `articles`, `comments`, `favorites`, and `follows` in any actual deployment — would confirm or refute the "unbounded, ever-growing table" assumption behind the pagination findings.

---

## 2. Scope and method

**Reviewed:** All application code (`app/controllers`, `app/models`, `app/views/**/*.jbuilder`, `app/helpers`), all of `config/` (routes, environments, initializers, database.yml), `db/schema.rb` and every migration, `Gemfile`/`Gemfile.lock`, and `test/` (read for evidence of what is and is not covered, not executed).

**Not reviewed:** `vendor/`, the asset pipeline (`app/assets/*`, `public/`) — build-time/front-end concerns, not request-time backend performance. `log/` contents were not opened (may contain runtime data; not needed for a static review). Gem source for `devise`, `acts-as-taggable-on`, and `acts_as_follower` was not fetched or read line-by-line; their behavior is inferred from their documented public API, their declared associations as visible from the schema (e.g., absence of a caching column), and the complete absence of any preload/eager-load call naming them anywhere in this repository.

**Evidence available:** Uninstrumented. No metrics library, tracer, structured request-duration logging, benchmark, load-test script, or query-count test exists anywhere in the repository. `test/controllers/` contains only a `.keep` file — there is no controller or request test suite at all.

**Ranking method:** Structural signals only; no runtime data was available or supplied.

**Reference depth:** Ruby, Rails (via the REST-frameworks comparison file), and SQLite all have dedicated (`deep`) technology references in this skill and were analyzed with full depth. `acts-as-taggable-on` and `acts_as_follower` have no dedicated technology file in this skill's registry; their specific query behavior is analyzed at the generic ActiveRecord-association level (`application/data-access.md`, `databases/relational.md`) rather than from gem-specific documentation, which is a narrower basis than the code's own SQL would give.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All 8 controllers, all 5 models, all 12 `.jbuilder` view templates, all routes, all migrations/schema, and all config/environment files were read in full. This is a small repository (well under 1,000 lines of application code) and was read exhaustively rather than sampled. |
| **Critical paths** | 8 / 8 identified HTTP entry-point groups analyzed |
| **Technology support** | Ruby: Deep. Rails/REST: Deep. SQLite: Deep. acts-as-taggable-on: Generic. acts_as_follower: Generic. |
| **Runtime evidence** | None supplied |
| **Overall review confidence** | **Medium** |

**Review confidence is not finding confidence.** The four performance findings below are each `High`-confidence or `Medium`-confidence readings of the code itself — the code was read in full, and the mechanisms are unambiguous. The review's own confidence is capped at `Medium` because the severity of every finding depends on a production workload this review has no visibility into (no metrics, no user, no supplied numbers).

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Actual production request rate, concurrency, and read/write ratio | No evidence exists | A request-rate/latency metrics export, or a load test run against a staging copy with production-shaped data |
| Real deployment topology (Puma worker/thread counts, replica count) | No evidence exists | The actual Puma/Procfile/infrastructure configuration used wherever this app is deployed — none is committed to this repository |
| Exact SQL and query count `acts-as-taggable-on` 3.5.0 and `acts_as_follower` 0.2.1 issue for `tag_list` / `following?` | Technology unsupported (no dedicated reference for these gems in this skill) | A query log or an `assert_queries`-style test run against the actual installed gem versions |
| Actual row counts in any real deployment of this app | No evidence exists | Access to a production or production-shaped database, or its own metrics |

---

## 3. Architecture overview

A single Ruby on Rails 4.2.6 REST/JSON API service (no workspace markers, one `Gemfile`, one runtime). Authentication is JWT-based (via `devise` for password/user management plus a hand-rolled JWT issue/verify flow in `ApplicationController`). SQLite3 is the only datastore, used identically in `development`, `test`, and `production` per `config/database.yml`. There is no cache, no message broker, and no calls to any other service. The app server is Puma (from `Gemfile`), but no worker/thread configuration, `Procfile`, `Dockerfile`, or orchestration manifest is committed to this repository, so its actual deployment shape is unknown.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Web framework | Ruby on Rails | 4.2.6 | Deep | REST/JSON API |
| Runtime | Ruby (MRI assumed) | not pinned in repo | Deep | Application runtime |
| App server | Puma | 3.4.0 | Deep (via `technology/ruby.md`) | HTTP server |
| Datastore | SQLite3 | version not pinned (gem `sqlite3`, no version constraint) | Deep | Sole datastore, all environments |
| Auth | Devise + JWT (`ruby-jwt` 1.5.4) | 4.2.0 / 1.5.4 | n/a — not a perf-scored layer | Authentication |
| Tagging | acts-as-taggable-on | 3.5.0 | Generic | Article tags |
| Following | acts_as_follower | 0.2.1 | Generic | User follow graph |

**Shared resources:** The single SQLite database file (and its 5-connection pool, `config/database.yml:9`) is the only shared resource in the system. Every controller action — every read and every write — funnels through it. There is no cache and no queue to name separately.

---

## 4. Workload model

**Known** (from repository files)

- SQLite3 is configured for `development`, `test`, and `production` identically, with `pool: 5` and `timeout: 5000` (a 5-second `busy_timeout`) — source: `config/database.yml:7-10`.
- No `config/puma.rb`, `Procfile`, `Dockerfile`, `docker-compose.yml`, or Kubernetes/Terraform manifest exists anywhere in the repository — source: repository-wide file search.
- `GET /api/articles` and `GET /api/articles/feed` default to a page size of 20 and accept a caller-supplied `limit` with no clamp — source: `app/controllers/articles_controller.rb:13,21`.
- `GET /api/articles/:slug/comments` applies no `.limit`/`.offset` at all — source: `app/controllers/comments_controller.rb:6`.
- No `.includes`, `.preload`, or `.eager_load` call names `:tags` anywhere in the repository; only `:user` is eager-loaded on the two article list actions — source: repository-wide search, confirmed against `app/controllers/articles_controller.rb:5,17`.
- No retention, archival, or TTL job exists for `articles`, `comments`, `favorites`, `follows`, or `taggings` — source: no scheduler, cron, or rake task performing deletion was found in `lib/`, `config/`, or `db/`.
- The test suite's own fixture data is tiny (2-3 articles, a handful of comments/favorites/users) — source: `test/fixtures/*.yml`.
- `config/environments/production.rb:49` sets `config.log_level = :debug`.
- The project's own README describes it as "Example Rails codebase that adheres to the RealWorld API spec" — source: `README.md:3`.

**Assumed** (stated, unverified — drives confidence caps)

- Because this is a widely-forked reference/starter implementation, and its own frontend/client flow sends a JWT `Authorization` header once a user is signed in, a meaningful share of requests to `GET /api/articles` (and all requests to `GET /api/articles/feed`, which requires authentication) are authenticated — affects: PERF-001.
- `articles`, `comments`, `favorites`, and `follows` are assumed to grow monotonically over the life of any real deployment, since no retention mechanism exists — affects: PERF-002.
- Actual current write concurrency in any real deployment of this app is unknown and could plausibly be anywhere from "effectively zero" (a personal/demo instance) to "multiple concurrent users" (if forked and used as a starting point for a real service) — affects: PERF-003.

**Unknown** (would change conclusions if answered)

- Production request rate and concurrency, for any endpoint.
- Real Puma worker/thread counts and instance count wherever this app is actually run.
- Actual row counts for the growing tables in any live deployment.
- Whether any fork/deployment of this code has replaced SQLite with a client-server database (which would substantially change PERF-003, but not PERF-001/PERF-002).

**Derived** (computed from the entries above — inputs shown)

- Query amplification for an authenticated `GET /api/articles` request returning the default page of 20 articles: roughly 2 base queries (the article list, the `articles_count`) + 1 batched query for `:user` (already eager-loaded) + up to 20 queries for `tag_list` + up to 20 queries for `favorited?` + up to 20 queries for `following?` ≈ **~62 queries for one HTTP request**. Inputs: default page size 20 (`articles_controller.rb:13`), three per-item accessors identified in `app/views/articles/_article.json.jbuilder:1,3` and `app/views/profiles/_profile.json.jbuilder:3`, each confirmed to have no batched/eager-loaded alternative in use.
- Because `limit` has no enforced maximum, a caller supplying `?limit=1000` drives the same per-item multiplier to roughly **~3,000 queries for one HTTP request**, bounded only by the total number of rows in the `articles` table. Input: `articles_controller.rb:13`, `params[:limit]` used directly with no clamp against any maximum.

**Measured** (from a real runtime artifact, cited)

- None — no benchmark, trace, query log, or load-test result exists in this repository or was supplied for this review. This is why every finding below caps below `Confirmed`.

### Questions that would change the ranking

| # | Question | What it would change |
|:--|:--|:--|
| 1 | What is the actual concurrent write rate (articles/comments/favorites/follows created, users registering or logging in) against this deployment? | Directly changes PERF-003's confidence from `Medium` (workload-capped) toward `Confirmed` or `High` in either direction — it could show the single-writer constraint is never actually hit, or that it already is. |
| 2 | What are the actual/maximum `limit` values callers send to `GET /api/articles`, and typical comment counts per article? | Would confirm or refute the ~60-3,000 query amplification derived above, and could move PERF-001/PERF-002 up or down within P1. |
| 3 | What is the real Puma worker/thread configuration and instance count in production? | Would let the connection-pool arithmetic in PERF-003 be computed exactly instead of argued structurally. |
| 4 | How large are `articles`/`comments`/`favorites`/`follows` in any real deployment today? | Confirms or refutes the "unbounded growth" assumption behind PERF-002's severity. |

---

## 5. Critical path analysis

Ranked by structural signals only; no runtime data available.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | Yes | 2 base + up to 3×page-size (lazy) | No — default 20, caller can raise `limit` with no cap | No | PERF-001, PERF-002 |
| 2 | `GET /api/articles/feed` | Yes | Same shape as above + 1 (`following_users`) | No | No | PERF-001, PERF-002 |
| 3 | `GET /api/articles/:slug/comments` | Yes | 1 + up to 2×comment-count (lazy) | No — no `.limit` at all | No | PERF-001, PERF-002 |
| 4 | Writes: `POST /api/articles`, `/comments`, `/favorite`, `/follow`, `/users`, `/users/login` | Yes | 1-3 writes each | Yes (single-record) | No | PERF-003 (shared single-writer contention) |
| 5 | `GET /api/articles/:slug` | Yes | ~3, single record | Yes | No | Bounded; no material finding |
| 6 | `GET /api/profiles/:username` | Yes | 1-2 | Yes | No | Bounded; no material finding |
| 7 | `GET /api/tags` | Yes | 1 (`tag_counts`) | Yes | No | Bounded; no material finding |
| 8 | `DELETE /api/articles/:slug` | Yes | destroy + cascades | Yes | No | See COR-001 (outside performance scope) |

**Amplification points:** `GET /api/articles` and `GET /api/articles/feed` multiply a per-article cost (tags, favorited-status, following-status) by page size, and page size itself has no enforced ceiling. `GET /api/articles/:slug/comments` multiplies a per-comment cost (author lookup, following-status) by comment count, with no pagination at all.

**Paths deliberately not analyzed in depth, and why:** Devise's built-in registration/password-reset flows beyond the parts this app overrides (`SessionsController#create` and the two `.jbuilder` templates) — these are largely unmodified gem-internal code, and are low-frequency auth flows rather than content-serving paths. The asset pipeline and any JavaScript/CSS under `app/assets` — a build-time, not request-time, concern for this API-only backend.

---

## 6. Layer analysis

### 6.1 Application

Controllers are thin and mostly free of obvious application-level waste. `ApplicationController#authenticate_user` decodes the JWT and stores only the user id (`@current_user_id`) without touching the database, deferring the actual `User` lookup to `current_user`, which is called on demand — this is a deliberate, effective avoidance of an unnecessary query on every request (confirmed by the README's own description of this design), and is noted here as a positive, not a finding. The one per-request cost worth naming and discarding is `ApplicationController#underscore_params!`, which walks the entire `params` hash on every request (`deep_transform_keys!`) — for the small JSON payloads this API accepts (article/comment bodies, user profile fields), this is not material and is not reported as a finding (see §7 "Considered and not reported").

### 6.3 Data access and datastore

This is where all four performance findings live. `app/models/article.rb` and `app/models/user.rb` declare associations correctly for `:user`, `:favorites`, and `:comments`, and `articles_controller.rb` does eager-load `:user` on both list actions — but three other per-item accessors invoked from the JSON serializers (`tag_list`, `favorited?`, `following?`) are never batched (PERF-001). The two collection endpoints also lack an enforced bound on how much they return (PERF-002). Underneath all of this, the datastore itself is SQLite, configured with a connection-pool abstraction that does not change SQLite's fundamental single-writer behavior (PERF-003) — this is a category-level mismatch per `databases/universal.md` §1, not a tuning question.

### 6.7 Observability

Uninstrumented. No metrics client, tracer, structured request-duration logging, or query-count test exists anywhere in the repository, and `test/controllers/` is empty apart from a `.keep` placeholder. This sets the ceiling on every finding's confidence in this report (none can exceed `High`, most workload-dependent claims cap at `Medium`), and it means none of the recommendations below can currently be validated in this codebase without first adding the instrumentation named in §9. Separately, `config/environments/production.rb:49` sets `config.log_level = :debug` — the most verbose level, logging full SQL and parameters synchronously on every request in production (PERF-004).

---

## 7. Findings

### PERF-001 — Per-item queries for tags, favorited-status, and following-status in list serializers

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/views/articles/_article.json.jbuilder:1` |
| **Tags** | scalability-risk, quick-win |

**Problem**
The article and comment JSON serializers call three per-item accessors — `article.tag_list`, `current_user.favorited?(article)`, and `current_user.following?(author)` — that each issue their own database query, once per item in a list, instead of being batched across the whole collection.

**Performance principle**
Repeated work: the same class of remote operation (a lookup keyed by one item) is issued once per item in a collection instead of once for the whole collection, so cost scales with result size rather than with a fixed number of round trips.

**Evidence**
- `app/views/articles/_article.json.jbuilder:1` — `json.(article, ..., :tag_list)`: `tag_list` is provided by `acts_as_taggable` (`app/models/article.rb:9`), a virtual accessor backed by a join through the `taggings`/`tags` tables (`db/schema.rb`); there is no `cached_tag_list`-style column on `articles` and no eager-load of `:tags` anywhere in the repository.
- `app/views/articles/_article.json.jbuilder:3` — `current_user.favorited?(article)`, calling `app/models/user.rb:36-38`, which explicitly issues `favorites.find_by(article_id: article.id)` — one query per call, visible directly in source.
- `app/views/profiles/_profile.json.jbuilder:3` — `current_user.following?(user)`, provided by `acts_as_follower` (`app/models/user.rb:11`), invoked once per author rendered — this partial is reused for every article's `author` field (`_article.json.jbuilder:2`) and every comment's `author` field (`app/views/comments/_comment.json.jbuilder:2`).
- `app/controllers/articles_controller.rb:5,17` — only `.includes(:user)` is applied; no `.includes(:tags)` exists anywhere in the repository (confirmed by a repository-wide search for `includes(`, `preload(`, `eager_load(`).
- `app/controllers/comments_controller.rb:6` — `@article.comments.order(created_at: :desc)` has no `.includes(:user)` at all, so `comment.user` (`_comment.json.jbuilder:2`) is also a per-comment query.
- No runtime evidence (query log, APM span count) exists for this path; this finding is derived entirely from static reading of the code and the documented association design of the gems involved.

**Impact**
- Position: critical-path (all three affected paths — article index, article feed, comment index — are synchronous, user-facing reads).
- Frequency: per-item, multiplied by the number of articles or comments returned in one request.
- Growth: O(n) in the number of items returned, with a constant multiplier of up to 3 (tags, favorited, following) for articles and up to 2 (author, following) for comments.
- Blast radius: endpoint directly; under concurrent load it also consumes a disproportionate share of the shared 5-connection SQLite pool (see PERF-003), which can extend to service-wide latency.
- Derivation (shown in §4): an authenticated `GET /api/articles` request returning the default 20 articles issues roughly 62 queries; combined with PERF-002's unbounded `limit`, a larger caller-supplied page multiplies this further with no ceiling.

**Conditions**
This matters whenever a list endpoint (`/articles`, `/articles/feed`, `/articles/:slug/comments`) is called with more than a handful of items and, for the `favorited`/`following` accessors specifically, with an `Authorization` header present (which `/feed` always requires, and which any signed-in client of this API — including its own reference frontend — sends on `/articles` too). Workload (actual page sizes and request rate in production) is unknown, but the mechanism itself does not depend on that assumption — it is visible directly in code regardless of traffic level.

**Counter-evidence**
- Searched the codebase for `.includes`, `.preload`, or `.eager_load` naming `:tags`; found none — only `:user` is eager-loaded on the two article list actions (`articles_controller.rb:5,17`). Effect: no-effect (confirms the finding).
- Checked whether `favorited?`/`following?` are gated so they never fire for anonymous requests: true for both (`signed_in? ? ... : false` in `_article.json.jbuilder:3` and `_profile.json.jbuilder:3`), but `tag_list` has no such gate and fires unconditionally regardless of authentication state. Effect: no-effect on the scored (authenticated worst-case) severity, but worth noting the anonymous case is smaller (1x multiplier, not 3x).
- Checked whether the three accessors might already be memoized per request across repeated calls to the same underlying value: they are not — `favorited?` and `following?` are each called with a *different* argument (a different article, a different author) per iteration, so no single-request memoization would help without restructuring.

**Why this might not matter**
If this deployment (or any given fork of it) serves only a small number of users and articles — plausible for a reference/demo instance, and consistent with the tiny fixture data in `test/fixtures/*.yml` — the absolute number of extra queries stays in the tens, and SQLite's in-process, no-network-round-trip query cost may make this imperceptible in practice. A maintainer of a low-traffic instance could reasonably leave this unfixed until the code is used as the basis for a real production service.

**Recommendation**
Batch each of the three per-item lookups once per request instead of once per item: `.includes(:tags)` (or `.preload(:tags)`) on the article query; one query fetching the current user's favorited `article_id`s into a `Set` and checking membership in the serializer; one query fetching the current user's followed user ids (or using whatever bulk accessor `acts_as_follower` exposes) into a `Set` and checking membership. This removes the repeated work rather than concealing it behind a cache.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Batch-load tags/favorited-set/following-set once per collection render | **preferred** | Removes the repeated work entirely; each becomes one query regardless of page size, with no invalidation surface to maintain |
| B — Cache each per-item check (e.g., `Rails.cache` keyed by user+article) | | Masks the query count without reducing it below what a single batched query already achieves; adds staleness/invalidation cost for a value this cheap to compute correctly in one shot |

**Trade-offs**
Passing precomputed sets into the serializers requires wiring the extra context through every call site that renders `_article`/`_profile` (index, feed, show, favorite create/destroy, follow create/destroy) — a missed call site would silently reintroduce the N+1 for that one path. No meaningful memory or consistency cost at this data volume.

**Validation**
- Baseline: not measured (uninstrumented repository).
- Metric: SQL query count issued per `GET /api/articles` request, for a fixed page of N articles, as an authenticated request.
- Expectation: query count should drop from roughly `(2 + 3N)` to a small constant independent of N (on the order of 5-6 total: 2 base + 1 batched tag preload + 1 batched favorited-set query + 1 batched following-set query).
- Falsifier: if the query count after the change still scales with N, the batching was incomplete (e.g., only one of the three accessors was fixed), and the finding should be treated as unresolved rather than partially resolved.
- Safety: not-safe-on-production as a first-time measurement (enabling verbose query logging on a live system is a "not safe without approval" action per `methodology/validation.md`); measure in staging/test with production-shaped fixture data instead, which is safe.
- Guard: a request/controller test asserting the query count for `GET /api/articles` stays constant as the number of returned articles in the test fixture increases.

---

### PERF-002 — No enforced maximum page size on article endpoints, and no pagination at all on comments

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/articles_controller.rb:13` |
| **Tags** | scalability-risk, quick-win |

**Problem**
`GET /api/articles` and `GET /api/articles/feed` apply a default page size of 20 but accept an unbounded caller-supplied `limit`, and `GET /api/articles/:slug/comments` applies no pagination at all — the amount of data returned (and, combined with PERF-001, the number of queries issued) is bounded only by caller input or table size.

**Performance principle**
Unbounded work: a default is not a bound. If the caller can control how much work is done with no ceiling, the endpoint's cost is set by the caller (or by however large the underlying table grows), not by the service.

**Evidence**
- `app/controllers/articles_controller.rb:13` — `@articles = @articles.order(created_at: :desc).offset(params[:offset] || 0).limit(params[:limit] || 20)`: `params[:limit]` is passed directly into `.limit` with no clamp against any maximum.
- `app/controllers/articles_controller.rb:21` — the same unbounded pattern in `feed`.
- `app/controllers/comments_controller.rb:6` — `@comments = @article.comments.order(created_at: :desc)` has no `.limit`/`.offset` at all; every comment on an article is returned on every request.
- `db/schema.rb` — no retention, archival, or size cap exists for `articles`, `comments`, `favorites`, or `follows`; `lib/`, `config/`, and `db/` were searched for any scheduled job or rake task that prunes these tables, and none was found.
- No runtime evidence exists confirming an actual observed large `limit` or large comment count in practice; this is a structural, code-derived finding.

**Impact**
- Position: critical-path.
- Frequency: per-request, but the amount of "per-request" work is caller- or table-size-controlled rather than fixed.
- Growth: O(n) where n is either caller-supplied (`articles`) or the full row count for the resource (`comments`) — n itself has no ceiling.
- Blast radius: endpoint under ordinary use; potentially service-wide, because a request holding a database connection for an unbounded query occupies one of only 5 total pool slots (`config/database.yml:9`) for a correspondingly long time — per `application/connection-pools.md` §2, a slow query directly reduces the pool's effective capacity for every other concurrent request.
- Combined with PERF-001: a caller supplying `?limit=1000` to `/api/articles` drives the per-item multiplier from ~60 queries (default page) to roughly 3,000 queries for a single HTTP request (derivation in §4).

**Conditions**
This matters once either (a) the affected tables grow large enough that an unbounded `/comments` response or a large `limit` becomes expensive in its own right, or (b) any caller — malicious or simply a misconfigured client — sends a large `limit` value. Neither the actual growth trajectory nor actual caller behavior is known for this deployment; both are assumed based on the absence of any retention mechanism and the absence of any clamp in the code.

**Counter-evidence**
- Checked whether any reverse proxy, gateway, or Rack middleware in this repository enforces a response-size or parameter limit upstream of the controller: none was found in `config/`, `Gemfile`, or `app/`.
- Checked whether `articles_count`/comment counts are themselves bounded in a way that would cap practical page sizes: they are not — `@articles_count = @articles.count` (line 11) counts the full filtered set, independent of the page `limit` applied afterward.
- Checked whether SQLite's own row-count limits would bound this in practice: SQLite has no such application-level limit; the only ceiling is actual table size.

**Why this might not matter**
If this deployment's tables never grow beyond what the test fixtures suggest (a handful of rows), and no client or attacker ever supplies an unusually large `limit`, this finding has no observable current effect — it is a latent risk rather than an active one until either the data or the caller behavior changes.

**Recommendation**
Clamp `limit` to an enforced maximum (e.g., `[params[:limit].to_i, 100].min`, with the existing default retained as the floor) on both article endpoints, and add an explicit `.limit`/`.offset` with a similar enforced maximum to `comments#index`. This bounds the work at the source rather than relying on caller good behavior.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Enforce a maximum `limit` server-side on all three endpoints | **preferred** | Small, local change; removes the unbounded-cost property directly at its source |
| B — Switch to keyset (cursor) pagination | | Solves deep-offset cost as tables grow, but does not by itself cap page *size* — would need to be combined with option A anyway, and loses random page access, which the RealWorld API spec's `offset`/`limit` contract currently relies on |

**Trade-offs**
An enforced maximum changes existing client behavior for any caller currently relying on a large `limit` to fetch everything in one request — those callers would need to paginate. No other meaningful cost.

**Validation**
- Baseline: not measured.
- Metric: response row count and query count for `GET /api/articles?limit=<value>` at several `limit` values, and for `GET /api/articles/:slug/comments` against an article fixture with many comments.
- Expectation: after the fix, response size and query count should plateau at the enforced maximum regardless of the requested `limit` or the underlying comment count.
- Falsifier: if a `limit` value above the enforced maximum still returns more than that maximum, the clamp was not applied correctly.
- Safety: safe-on-production (reading response sizes for existing traffic; the fix itself is a code change validated in staging/test).
- Guard: a request test asserting the response size for each of the three endpoints never exceeds the enforced maximum regardless of caller input or fixture size.

---

### PERF-003 — SQLite configured as the production datastore with a pool sized for a client-server engine

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `config/database.yml:7` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
`config/database.yml` configures SQLite3 for the `production` environment with `pool: 5` — a setting whose entire purpose, for a client-server engine, is to let that many operations run against the datastore concurrently. SQLite allows exactly one writer to the database file at a time by design, and no WAL mode is configured, so in the default rollback-journal mode, a write also blocks concurrent readers. The pool size does not deliver the concurrency it implies.

**Performance principle**
A shared, serializing resource (here, SQLite's single-writer whole-file lock) caps effective concurrency regardless of how much of everything else — connections, threads, application instances — is provisioned around it.

**Evidence**
- `config/database.yml:7-10,23-25` — `adapter: sqlite3`, `pool: 5`, applied identically to `production`.
- `Gemfile` — `gem 'sqlite3'` with no version pin; no `pg`, `mysql2`, or other client-server driver is present anywhere in `Gemfile`/`Gemfile.lock`.
- No `PRAGMA journal_mode` (WAL) is set anywhere in the repository (no initializer, no migration, no `database.yml` `pragmas:` key) — SQLite's default rollback-journal mode is therefore in effect, per `technology/sqlite.md` §2: in this mode, writers block readers, not only other writers.
- `timeout: 5000` (`database.yml:10`) sets a 5-second busy-timeout, which converts an immediate `SQLITE_BUSY` failure into up to a 5-second wait under contention rather than eliminating the contention itself.
- No `Procfile`/`config/puma.rb` exists to state actual worker/thread counts, so the full pool-vs-worker-vs-datastore arithmetic from `application/connection-pools.md` cannot be completed with real numbers from this repository.

**Impact**
- Position: critical-path (every write endpoint — article/comment/favorite/follow creation, user registration, login's `trackable` update — is a synchronous, user-facing write).
- Frequency: per-request, for every write.
- Growth: unknown in the data/traffic sense used elsewhere in this report — the relevant variable here is concurrent write *rate*, not data volume. As concurrent write attempts approach the single-writer's capacity, wait time grows non-linearly rather than in proportion to load (the general queueing behavior described in `principles/latency.md` §3), which is a materially different and more dangerous curve than a linear one.
- Blast radius: system-wide — in the default rollback-journal mode, a write blocks not just other writers but concurrent readers too, so this affects every endpoint, not only write endpoints.

**Conditions**
This requires more than one write attempt against the database at genuinely the same moment to manifest at all; it does not depend on data volume. Actual production write concurrency for this deployment is unknown — no metrics, no stated user count, and no load test exist. If this deployment truly serves one user at a time (plausible for a personal demo instance), the mechanism exists but is never actually exercised.

**Counter-evidence**
- Checked whether an external connection pooler or a networked SQLite fork (`libsql`/Turso) is in use, which would change this analysis materially: no evidence of either was found in `Gemfile`, `Gemfile.lock`, or `config/`.
- Checked whether the database file might be configured to live on a shared/networked filesystem (a separate, more severe SQLite risk — silent corruption rather than contention): no such configuration was found; `database: db/production.sqlite3` is a plain relative path with no indication of a network mount.
- Checked whether `busy_timeout` mitigates this to the point of being a non-issue: it raises the failure threshold from instantaneous to 5 seconds under contention, which reduces but does not remove the effect — a request waiting up to 5 seconds for a database lock is itself a severe latency event on a user-facing path.

**Why this might not matter**
If this specific deployment (or any given fork of it) never runs with more than one concurrent write in flight — plausible for a personal or demonstration instance, and this repository gives no evidence either way — this finding describes a real constraint that simply never gets tested. The `busy_timeout` also means that even under contention, the visible failure mode is added latency up to 5 seconds rather than an immediate, confusing error, which somewhat softens the practical impact for very light, intermittent write concurrency.

**Recommendation**
For any deployment expecting more than trivial concurrent writes, replace SQLite with a client-server relational engine (e.g., PostgreSQL or MySQL) in `production`, keeping SQLite for `development`/`test` if desired. If SQLite must remain (e.g., this stays a single-user reference/demo deployment), enable WAL mode (`PRAGMA journal_mode=WAL`) to at least stop writes from blocking concurrent reads, and reduce the connection pool to reflect the true single-writer ceiling rather than a number implying five-way concurrency.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — Move to a client-server relational engine for production | **preferred** | Removes the structural ceiling entirely rather than working within it; this is the standard, well-understood path for any service expecting real concurrent users |
| B — Enable WAL mode and right-size the pool, keep SQLite | | A smaller, lower-effort mitigation that helps concurrent reads during writes and stops the pool size from over-promising, but does not add any write concurrency — appropriate only if this is deliberately staying a single-writer-scale deployment |

**Trade-offs**
Option A (changing engines) is a real migration effort — schema, deployment, and operational surface all change, and is not justified without evidence that concurrent writes actually occur. Option B (WAL + smaller pool) is nearly free but does not remove the underlying ceiling — it should be paired with monitoring so the team knows if/when they've outgrown it.

**Validation**
- Baseline: not measured.
- Metric: `SQLITE_BUSY` occurrence count and write-request latency (specifically the tail, p99) under concurrent write load, and `PRAGMA journal_mode` (currently unset/default) as a config check.
- Expectation: if this constraint is real for the actual deployment, a load test issuing concurrent writes should show write latency rising sharply (not linearly) as concurrency increases, consistent with queueing near a serialization point rather than a shared-nothing resource.
- Falsifier: if write latency under concurrent load stays flat as concurrency increases, either write concurrency in the real deployment is lower than assumed, or WAL/some other mitigation is already effectively in place — this finding's assumed severity would be overstated.
- Safety: `PRAGMA journal_mode` is safe-on-production to read; a concurrent-write load test is not-safe-on-production and should run against a staging copy with production-shaped data.
- Guard: an alert or dashboard on `SQLITE_BUSY`/lock-wait occurrences if this deployment stays on SQLite, so the team has warning before this becomes a user-visible incident rather than after.

---

### PERF-004 — Production logging configured at debug level

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `config/environments/production.rb:49` |
| **Tags** | quick-win |

**Problem**
`config.log_level = :debug` is set for the `production` environment — the most verbose Rails log level, which logs full SQL statements, parameters, and view-rendering detail on every request.

**Performance principle**
Instrumentation is not free: synchronous, verbose logging on every request is disk and (depending on log shipping) network I/O charged per request, proportional to traffic rather than to anything the request actually needed to accomplish.

**Evidence**
- `config/environments/production.rb:49` — `config.log_level = :debug`, unconditional for the environment.
- Rails' default production logger writes synchronously; no async logging shim or alternative logger is configured anywhere in this repository.

**Impact**
- Position: critical-path (logging happens inline during request handling).
- Frequency: per-request.
- Growth: O(1) additional cost per request (a fixed, small overhead), scaling linearly with request volume rather than with data size.
- Blast radius: service-wide — every request pays this cost, and log volume/storage is a shared concern across the whole deployment.

**Conditions**
This is a small, fixed per-request cost that only becomes noticeable at meaningful request volume, which this review has no visibility into. It is included because it is a one-line, unambiguous, checkable misconfiguration exactly matching a named example in this methodology's own resource-cost guidance, not because its current magnitude is known to be large.

**Counter-evidence**
- Checked whether the log destination is `/dev/null` or otherwise discarded in a way that would make the level irrelevant: no such configuration was found; the default file/STDOUT logger is in effect.
- Checked whether any log-filtering/sampling exists that would reduce debug-level volume: `config/initializers/filter_parameter_logging.rb` redacts specific parameter values (e.g., passwords) but does not reduce log *volume* or level.

**Recommendation**
Set `config.log_level` to `:info` (or `:warn`) for the `production` environment, matching the common Rails default, and reserve `:debug` for `development`/`test` where it already effectively applies by omission.

**Trade-offs**
Reduces the detail available for debugging a production issue after the fact (full SQL and params will no longer be logged by default) — if that detail is valuable, it is better obtained through a purpose-built APM/query-logging tool with sampling than through blanket debug-level logging.

**Validation**
- Baseline: not measured.
- Metric: log line volume/bytes per request, before and after the change, in a staging environment under representative traffic.
- Expectation: log volume per request should drop measurably (fewer lines, no full SQL/param dumps) with no change in application behavior.
- Falsifier: if error visibility genuinely degrades in a way the team relies on, the level should be raised again or replaced with targeted logging rather than left at `:debug`.
- Safety: safe-on-production (a one-line config change; validating the *before* state by reading existing logs is also safe-on-production).
- Guard: none needed beyond code review of future changes to this file — this is a single static config value.

---

### Remaining findings

All material findings identified in this review received full write-ups above; there are no additional lower-priority `PERF-` findings to list in summary form.

### Considered and not reported

- **`ApplicationController#underscore_params!`** (`app/controllers/application_controller.rb:44`) walks the entire `params` hash on every request. Discarded: this API's payloads (article/comment bodies, user profile fields) are small, and there is no evidence of large nested payloads anywhere in the routes or serializers.
- **`Article.all.includes(:user)` followed by a separate `.count`** (`articles_controller.rb:5,11`) issues an extra round trip for pagination metadata. Discarded as its own finding: the count is filtered by the same predicates as the page query and served from an indexed column path; it is real but bounded work, and any material cost here is already captured by PERF-002's broader "unbounded/ungrowing table" framing rather than warranting a separate entry.
- **`favorited_by`/`tagged_with` scopes** (`app/models/article.rb:7`, `acts_as_taggable`) generate joins/subqueries. Discarded: the schema shows supporting indexes on `favorites` (`article_id`, `user_id`) and `taggings` (`taggable_id`/`taggable_type`/`context`, and a unique compound index), and no missing-index pattern was found for the predicates these scopes actually use.
- **Devise `stretches = 11`** (`config/initializers/devise.rb:108`) is bcrypt's standard cost factor and the library's own recommended production default; not flagged.
- **Puma/worker/thread sizing** — cannot be assessed as its own finding because no worker/thread configuration exists anywhere in this repository to check; folded into PERF-003's unknowns instead of treated as a separate speculative finding.

### Adjacent findings — outside performance scope

### COR-001 — Self-referential `has_many :articles` with no matching database column

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | High — this will raise a SQL error on every execution of a commonly-used, user-facing action (deleting one's own article), not merely in an edge case. |
| **Location** | `app/models/article.rb:15` |

**Problem** `Article` declares `has_many :articles, dependent: :destroy` (line 15). By Rails' default association-naming convention, this expects an `article_id` foreign-key column on the `articles` table itself (a self-referential association). No such column exists — `db/schema.rb`'s `articles` table has `title`, `slug`, `body`, `description`, `favorites_count`, `user_id`, and timestamps only. Because this association carries `dependent: :destroy`, it is not merely unused dead code: it is invoked automatically whenever `Article#destroy` runs, which happens in `ArticlesController#destroy` (`app/controllers/articles_controller.rb:53-63`) — the endpoint an authenticated author uses to delete their own article.

**Evidence**
- `app/models/article.rb:15` — `has_many :articles, dependent: :destroy`.
- `db/schema.rb` (articles table definition) — no `article_id` column exists.
- `app/controllers/articles_controller.rb:53-63` — `ArticlesController#destroy` calls `@article.destroy`, which triggers all `dependent: :destroy` associations, including this one.
- `test/models/article_test.rb` — contains only a commented-out placeholder test; no test exercises `Article#destroy` or this association.

**Impact** Every call to `DELETE /api/articles/:slug` by an article's owner will trigger this association's dependent-destroy lookup, which queries for `articles.article_id = ?` — a column that does not exist — and will raise a SQL error (`SQLite3::SQLException: no such column: articles.article_id` or equivalent) instead of completing the deletion. This is a full-stop feature failure, not a degraded-performance issue, which is why it is reported here rather than scored on the performance rubric.

**Recommendation** Remove the erroneous `has_many :articles, dependent: :destroy` line entirely — it duplicates no legitimate relationship visible anywhere else in this model or schema, and appears to be a copy/paste artifact from the adjacent `has_many :favorites, dependent: :destroy` / `has_many :comments, dependent: :destroy` lines above it.

**Trade-offs** None — this line does not appear to serve any working purpose today, since it cannot execute successfully.

**Validation** Add a test that creates an article and calls `.destroy` on it, asserting no exception is raised and the record is removed. This would have caught the issue immediately.

**Would need** A correctness-focused test suite covering the destroy path for every model with `dependent: :destroy` associations (this review is not a substitute for one) — this is exactly the kind of defect a minimal request/model test suite (currently absent, per §6.7) would catch on the first run.

---

## 8. Prioritized action plan

### P0 — Immediate
None.

### P1 — High priority
| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-002 | P1 | Low | Cheapest of the three P1s to fix (a clamp plus a `.limit` call); also reduces the worst-case amplification of PERF-001 and the worst-case connection-hold time feeding PERF-003 |
| 2 | PERF-001 | P1 | Medium | Removes the dominant per-request query cost on the two most-used read endpoints |
| 3 | PERF-003 | P1 | High (if migrating engines) / Low (if only enabling WAL) | Structural ceiling on the whole system; sequenced after the two cheaper fixes above because it requires a workload decision (§4 Q1, Q3) the team should make deliberately rather than under review pressure |

### P2 — Medium priority
None.

### P3 — Optimization opportunity
| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 4 | PERF-004 | P3 | Very low | One-line config change; sequenced last only because its impact is small, not because it is hard |

**If only one thing is done:** Fix PERF-002 (enforce a maximum page size on all three list endpoints). It is the cheapest change here, it directly bounds the worst case of PERF-001, and it reduces how long any single request can hold one of the system's only 5 database connections — which is also the input that makes PERF-003 worse under load.

---

## 9. Validation plan

Per-finding validation detail is in each finding's own **Validation** block in §7. Summary:

### PERF-001
Measure query count for `GET /api/articles` in staging with production-shaped fixture data; expect it to drop from `~(2+3N)` to a small constant after batching; guard with a query-count test.

### PERF-002
Measure response size/row count for various `limit` values and comment counts; expect a plateau at the enforced maximum after the fix; guard with a response-size test.

### PERF-003
Measure `SQLITE_BUSY` occurrences and write-latency tail under a concurrent-write load test in staging; expect non-linear latency growth near the single-writer's capacity if the constraint is real for the actual deployment; guard with a lock-contention alert if SQLite is retained.

### PERF-004
Measure log volume per request before/after lowering the level in staging; expect a measurable drop with no behavior change.

### Instrumentation gaps to close first

This repository has no metrics, tracing, or query-count testing at all. Before any of the above optimizations are trusted to have worked, add: (1) a query-count assertion capability for controller/request tests (the single highest-value addition, since it directly validates PERF-001 and guards against its return), and (2) basic per-request duration logging or an APM integration, since right now there is no way to observe production latency at all, let alone its distribution.

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
      "name": "rails-realworld",
      "commit": "a2ae4fff78b3337683cea988c023281dcb5551b9"
    }
  },
  "workload": {
    "answered": false,
    "inputs": [],
    "scenarios": ["normal", "peak", "large-dataset", "high-concurrency"]
  },
  "runtime_evidence": [],
  "completeness": {
    "review_confidence": "Medium",
    "evidence_available": "uninstrumented",
    "ranking_method": "structural-signals-only",
    "critical_paths_identified": 8,
    "critical_paths_analyzed": 8,
    "technology_support": [
      { "technology": "ruby", "tier": "deep" },
      { "technology": "rest (rails)", "tier": "deep" },
      { "technology": "sqlite", "tier": "deep" },
      { "technology": "acts-as-taggable-on", "tier": "generic" },
      { "technology": "acts_as_follower", "tier": "generic" }
    ],
    "unknowns": [
      { "subject": "Production request rate and concurrency", "reason": "no-evidence-exists", "what_would_resolve_it": "A metrics export or a load test against a staging copy with production-shaped data" },
      { "subject": "Real Puma worker/thread/instance configuration", "reason": "no-evidence-exists", "what_would_resolve_it": "The actual deployment configuration used wherever this app runs; none is committed to this repository" },
      { "subject": "Exact SQL issued by acts-as-taggable-on 3.5.0 and acts_as_follower 0.2.1", "reason": "technology-unsupported", "what_would_resolve_it": "A query log or assert_queries-style test against the installed gem versions" },
      { "subject": "Real row counts for growing tables in any live deployment", "reason": "no-evidence-exists", "what_would_resolve_it": "Access to a production or production-shaped database" }
    ]
  },
  "assumptions": {
    "known": [
      "SQLite3 is configured identically for development/test/production with pool: 5, timeout: 5000 (config/database.yml:7-10)",
      "No Puma config, Procfile, Dockerfile, or orchestration manifest exists in this repository",
      "articles#index and #feed default limit=20 with no clamp (articles_controller.rb:13,21)",
      "comments#index has no .limit/.offset at all (comments_controller.rb:6)",
      "No .includes/.preload/.eager_load names :tags anywhere in the repository",
      "No retention/archival/TTL job exists for articles, comments, favorites, follows, or taggings",
      "config/environments/production.rb:49 sets config.log_level = :debug",
      "This repository is the README-described reference implementation of the RealWorld API spec"
    ],
    "unknown": [
      "Production request rate and concurrency for any endpoint",
      "Real Puma worker/thread and instance counts",
      "Real row counts for growing tables in any live deployment",
      "Whether any fork/deployment has replaced SQLite with a client-server database"
    ],
    "assumed": [
      "A meaningful share of /articles requests, and all /feed requests, are authenticated, triggering the favorited?/following? per-item queries",
      "articles/comments/favorites/follows grow monotonically over the life of a real deployment",
      "Actual current write concurrency is unknown and could range from effectively zero to multiple concurrent users"
    ],
    "derived": [
      "Authenticated GET /api/articles at default page size ~62 queries, from: default limit 20 (articles_controller.rb:13) x 3 per-item accessors (tag_list, favorited?, following?) + 2 base queries",
      "GET /api/articles?limit=1000 ~3,000 queries, from: same per-item multiplier applied to an unbounded caller-supplied limit (articles_controller.rb:13)"
    ],
    "measured": []
  },
  "decision_changing_questions": [
    { "question": "What is the actual concurrent write rate against this deployment?", "what_it_would_change": "PERF-003's confidence and whether it is a live or purely latent risk", "affects": ["PERF-003"] },
    { "question": "What limit values and comment counts occur in practice?", "what_it_would_change": "Whether PERF-001/PERF-002's derived query counts are realistic or overstated", "affects": ["PERF-001", "PERF-002"] },
    { "question": "What is the real Puma worker/thread/instance configuration?", "what_it_would_change": "Would let the connection-pool arithmetic behind PERF-003 be computed exactly", "affects": ["PERF-003"] },
    { "question": "How large are the growing tables in any real deployment today?", "what_it_would_change": "Confirms or refutes the unbounded-growth assumption behind PERF-002", "affects": ["PERF-002"] }
  ],
  "root_causes": [
    { "id": "ROOT-001", "description": "Per-item ORM/lazy-loading calls (acts-as-taggable-on's tag_list, User#favorited?, acts_as_follower's following?) inside JSON serialization partials are not batched, so each item in a list response issues its own query per accessor.", "findings": ["PERF-001"] },
    { "id": "ROOT-002", "description": "List-returning endpoints (articles#index, articles#feed, comments#index) either apply no enforced maximum page size or no pagination at all, so response size and query cost are set by caller input or table size rather than a fixed bound.", "findings": ["PERF-002"] },
    { "id": "ROOT-003", "description": "SQLite is configured as the production datastore behind a connection-pool abstraction designed for client-server engines; SQLite's single-writer whole-file lock (default rollback-journal mode, no WAL configured) means the pool size does not deliver the concurrency it implies.", "findings": ["PERF-003"] },
    { "id": "ROOT-004", "description": "Production logging is configured at Ruby/Rails' most verbose level, a synchronous per-request cost charged regardless of traffic volume.", "findings": ["PERF-004"] }
  ],
  "findings": [
    {
      "id": "PERF-001",
      "stable_id": "n1serializerarticletagfavoritefollow",
      "root_cause_id": "ROOT-001",
      "state": "OPEN",
      "severity": "High",
      "confidence": "High",
      "priority": "P1",
      "category": "data-access",
      "location": { "file": "app/views/articles/_article.json.jbuilder", "line": 1, "symbol": "articles/_article partial" },
      "tags": ["scalability-risk", "quick-win"],
      "problem": "The article and comment JSON serializers call three per-item accessors (tag_list, favorited?, following?) that each issue their own database query per item in a list, instead of being batched across the collection.",
      "performance_principle": "Repeated work: the same class of remote operation, keyed by item, is issued once per item instead of once for the whole collection, so cost scales with result size rather than with a fixed number of round trips.",
      "evidence": [
        { "statement": "json.(article, ..., :tag_list) calls acts_as_taggable's tag_list, a virtual accessor with no cached column and no eager-load anywhere in the repo.", "kind": "static", "file": "app/views/articles/_article.json.jbuilder", "line": 1 },
        { "statement": "current_user.favorited?(article) explicitly issues favorites.find_by(article_id: article.id) per call.", "kind": "static", "file": "app/models/user.rb", "line": 37 },
        { "statement": "current_user.following?(user) from acts_as_follower is invoked once per author via the reused profile partial, for every article and every comment rendered.", "kind": "static", "file": "app/views/profiles/_profile.json.jbuilder", "line": 3 },
        { "statement": "Only .includes(:user) is applied on the article list actions; no .includes/.preload/.eager_load names :tags anywhere in the repository.", "kind": "static", "file": "app/controllers/articles_controller.rb", "line": 5 },
        { "statement": "comments#index applies no .includes(:user) at all, so comment.user is also a per-comment query.", "kind": "static", "file": "app/controllers/comments_controller.rb", "line": 6 }
      ],
      "evidence_quality": "Moderate",
      "impact": {
        "position": "critical-path",
        "frequency": "per-item",
        "growth": "O(n)",
        "blast_radius": "endpoint",
        "amplification": "3x"
      },
      "conditions": "Matters whenever a list endpoint returns more than a handful of items with an Authorization header present (required on /feed, common on /articles for signed-in clients). Actual production page sizes and request rate are unknown; the mechanism itself does not depend on that assumption.",
      "counter_evidence": [
        { "statement": "Searched for .includes/.preload/.eager_load naming :tags anywhere in the repo; found none.", "effect": "no-effect" },
        { "statement": "favorited?/following? are gated by signed_in? and do not fire for anonymous requests, but tag_list has no such gate.", "effect": "no-effect" },
        { "statement": "Checked for per-request memoization across the three accessors; none exists, and each call uses a different argument so memoization would not help without restructuring.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "If this deployment serves only a small number of users and articles, consistent with the tiny test fixtures, the absolute number of extra queries stays in the tens and SQLite's in-process query cost may make this imperceptible in practice.",
      "recommendation": "Batch each per-item lookup once per request: .includes(:tags) on the article query, one query for the current user's favorited article ids into a Set, and one query for the current user's followed user ids into a Set, checked by membership in the serializer.",
      "alternatives": [
        { "option": "Batch-load tags/favorited-set/following-set once per collection render", "preferred": true, "why": "Removes the repeated work entirely; becomes one query regardless of page size with no invalidation surface" },
        { "option": "Cache each per-item check in Rails.cache", "preferred": false }
      ],
      "trade_offs": "Requires wiring the precomputed sets through every call site that renders the article/profile partials; a missed call site would silently reintroduce the N+1 for that path. No meaningful memory or consistency cost at this data volume.",
      "validation": {
        "metric": "SQL query count issued by GET /api/articles for a fixed page of N articles, authenticated",
        "expectation": "Query count should drop from roughly (2 + 3N) to a small constant independent of N",
        "falsifier": "If query count after the change still scales with N, the batching was incomplete",
        "safety": "not-safe-on-production",
        "guard": "A request/controller test asserting query count for GET /api/articles stays constant as fixture article count increases"
      }
    },
    {
      "id": "PERF-002",
      "stable_id": "unboundedpaginationarticlescommentsindex",
      "root_cause_id": "ROOT-002",
      "state": "OPEN",
      "severity": "High",
      "confidence": "High",
      "priority": "P1",
      "category": "data-access",
      "location": { "file": "app/controllers/articles_controller.rb", "line": 13, "symbol": "ArticlesController#index" },
      "tags": ["scalability-risk", "quick-win"],
      "problem": "articles#index and #feed accept a caller-supplied limit with no enforced maximum, and comments#index applies no pagination at all.",
      "performance_principle": "Unbounded work: a default is not a bound. If the caller (or table size) controls how much work is done with no ceiling, the endpoint's cost is set externally, not by the service.",
      "evidence": [
        { "statement": "@articles.order(created_at: :desc).offset(params[:offset] || 0).limit(params[:limit] || 20) passes params[:limit] directly into .limit with no clamp.", "kind": "static", "file": "app/controllers/articles_controller.rb", "line": 13 },
        { "statement": "Same unbounded pattern in feed.", "kind": "static", "file": "app/controllers/articles_controller.rb", "line": 21 },
        { "statement": "@article.comments.order(created_at: :desc) has no .limit/.offset at all.", "kind": "static", "file": "app/controllers/comments_controller.rb", "line": 6 },
        { "statement": "No retention, archival, or size cap exists for articles, comments, favorites, or follows anywhere in db/, config/, or lib/.", "kind": "static", "file": "db/schema.rb" }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(n)",
        "blast_radius": "service",
        "amplification": "unknown"
      },
      "conditions": "Matters once affected tables grow large enough to make an unbounded response expensive, or once any caller sends a large limit value. Neither actual growth trajectory nor actual caller behavior is known for this deployment.",
      "counter_evidence": [
        { "statement": "Checked for any reverse proxy, gateway, or Rack middleware enforcing a response-size or parameter limit upstream; none found.", "effect": "no-effect" },
        { "statement": "articles_count itself counts the full filtered set, independent of the page limit, so it does not bound practical page sizes either.", "effect": "no-effect" }
      ],
      "why_this_might_not_matter": "If tables never grow beyond fixture-scale and no caller ever sends an unusually large limit, this has no observable current effect and is a latent rather than active risk.",
      "recommendation": "Clamp limit to an enforced maximum on both article endpoints, and add an explicit bounded .limit/.offset to comments#index.",
      "alternatives": [
        { "option": "Enforce a maximum limit server-side on all three endpoints", "preferred": true, "why": "Small, local change that removes the unbounded-cost property directly at its source" },
        { "option": "Switch to keyset (cursor) pagination", "preferred": false }
      ],
      "trade_offs": "Changes behavior for any caller relying on a large limit to fetch everything in one request; they would need to paginate instead.",
      "validation": {
        "metric": "Response row count and query count for GET /api/articles at several limit values, and for comments#index against a high-comment-count fixture",
        "expectation": "Response size and query count should plateau at the enforced maximum regardless of requested limit or comment count",
        "falsifier": "If a limit above the enforced maximum still returns more rows than that maximum, the clamp was not applied correctly",
        "safety": "safe-on-production",
        "guard": "A request test asserting response size never exceeds the enforced maximum regardless of input"
      }
    },
    {
      "id": "PERF-003",
      "stable_id": "sqlitesinglewriterproductionpool",
      "root_cause_id": "ROOT-003",
      "state": "OPEN",
      "severity": "Critical",
      "confidence": "Medium",
      "priority": "P1",
      "category": "concurrency",
      "location": { "file": "config/database.yml", "line": 7 },
      "tags": ["scalability-risk", "needs-measurement"],
      "problem": "SQLite is configured for production with a 5-connection pool, but SQLite allows exactly one writer to the database file at a time; no WAL mode is configured, so writes also block concurrent reads.",
      "performance_principle": "A shared, serializing resource caps effective concurrency regardless of how much connection/thread/instance capacity is provisioned around it.",
      "evidence": [
        { "statement": "adapter: sqlite3, pool: 5 applied identically to the production environment.", "kind": "static", "file": "config/database.yml", "line": 7 },
        { "statement": "No PRAGMA journal_mode (WAL) is set anywhere in the repository; SQLite's default rollback-journal mode is in effect.", "kind": "static", "file": "config/database.yml" },
        { "statement": "timeout: 5000 sets a 5-second busy_timeout, converting immediate SQLITE_BUSY failures into up to a 5-second wait under contention rather than removing the contention.", "kind": "static", "file": "config/database.yml", "line": 10 },
        { "statement": "No Procfile or config/puma.rb exists to state actual worker/thread counts.", "kind": "static", "file": "Gemfile" }
      ],
      "evidence_quality": "Moderate",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "unknown",
        "blast_radius": "system-wide",
        "amplification": "unknown"
      },
      "conditions": "Requires more than one write attempt against the database at genuinely the same moment; does not depend on data volume. Actual production write concurrency is unknown.",
      "counter_evidence": [
        { "statement": "Checked for an external connection pooler or a networked SQLite fork (libsql/Turso); none found in Gemfile, Gemfile.lock, or config/.", "effect": "no-effect" },
        { "statement": "Checked whether the database file might live on a networked filesystem (a separate, more severe risk); found no such configuration.", "effect": "no-effect" },
        { "statement": "busy_timeout raises the failure threshold to 5 seconds under contention, reducing but not removing the effect.", "effect": "bounds-impact" }
      ],
      "why_this_might_not_matter": "If this deployment never runs more than one concurrent write, this describes a real constraint that is never actually exercised; the 5-second busy_timeout also softens the failure mode to added latency rather than an immediate error for light, intermittent contention.",
      "recommendation": "For any deployment expecting more than trivial concurrent writes, replace SQLite with a client-server relational engine in production. If SQLite must remain, enable WAL mode and right-size the pool to reflect the true single-writer ceiling.",
      "alternatives": [
        { "option": "Move to a client-server relational engine for production", "preferred": true, "why": "Removes the structural ceiling entirely rather than working within it" },
        { "option": "Enable WAL mode and right-size the pool, keep SQLite", "preferred": false }
      ],
      "trade_offs": "Changing engines is a real migration effort not justified without evidence of actual concurrent writes; WAL + smaller pool is nearly free but does not add write concurrency.",
      "validation": {
        "metric": "SQLITE_BUSY occurrence count and write-request p99 latency under concurrent write load; PRAGMA journal_mode as a config check",
        "expectation": "Write latency should grow non-linearly, not linearly, as concurrency approaches the single-writer's capacity, if the constraint is real for the actual deployment",
        "falsifier": "If write latency stays flat as concurrency increases, actual write concurrency is lower than assumed or a mitigation is already effectively in place",
        "safety": "not-safe-on-production",
        "guard": "An alert or dashboard on SQLITE_BUSY/lock-wait occurrences if SQLite is retained"
      }
    },
    {
      "id": "PERF-004",
      "stable_id": "productiondebugloglevel",
      "root_cause_id": "ROOT-004",
      "state": "OPEN",
      "severity": "Low",
      "confidence": "High",
      "priority": "P3",
      "category": "observability",
      "location": { "file": "config/environments/production.rb", "line": 49 },
      "tags": ["quick-win"],
      "problem": "config.log_level = :debug is set for the production environment, the most verbose Rails log level.",
      "performance_principle": "Instrumentation is not free: synchronous, verbose logging on every request is I/O charged per request, proportional to traffic rather than to what the request needed.",
      "evidence": [
        { "statement": "config.log_level = :debug, unconditional for the production environment.", "kind": "static", "file": "config/environments/production.rb", "line": 49 }
      ],
      "evidence_quality": "Strong",
      "impact": {
        "position": "critical-path",
        "frequency": "per-request",
        "growth": "O(1)",
        "blast_radius": "service",
        "amplification": "unknown"
      },
      "conditions": "A small, fixed per-request cost that only becomes noticeable at meaningful request volume, which this review has no visibility into.",
      "counter_evidence": [
        { "statement": "Checked whether the log destination is discarded in a way that would make the level irrelevant; no such configuration found.", "effect": "no-effect" },
        { "statement": "filter_parameter_logging.rb redacts specific values but does not reduce log volume or level.", "effect": "no-effect" }
      ],
      "recommendation": "Set config.log_level to :info or :warn for production, matching the common Rails default.",
      "trade_offs": "Reduces post-hoc debugging detail (full SQL/params); better obtained through a purpose-built, sampled APM/query-logging tool if needed.",
      "validation": {
        "metric": "Log line volume/bytes per request, before and after, in staging under representative traffic",
        "expectation": "Measurable drop in log volume per request with no behavior change",
        "falsifier": "If error visibility genuinely degrades in a way the team relies on, the level should be raised again or replaced with targeted logging",
        "safety": "safe-on-production",
        "guard": "None beyond code review of future changes to this file"
      }
    }
  ],
  "considered_not_reported": [
    { "observation": "ApplicationController#underscore_params! walks the entire params hash on every request.", "why_discarded": "Payloads are small for this API; no evidence of large nested payloads anywhere in the routes or serializers.", "location": "app/controllers/application_controller.rb:44" },
    { "observation": "Article.all.includes(:user) is followed by a separate .count query for pagination metadata.", "why_discarded": "Filtered by the same predicates as the page query, served via an indexed column path; bounded work already captured by PERF-002's framing.", "location": "app/controllers/articles_controller.rb:5,11" },
    { "observation": "favorited_by/tagged_with scopes generate joins/subqueries.", "why_discarded": "Schema shows supporting indexes for the predicates these scopes actually use; no missing-index pattern found.", "location": "app/models/article.rb:6-7" },
    { "observation": "Devise stretches = 11 (bcrypt cost factor).", "why_discarded": "Standard library-recommended production default.", "location": "config/initializers/devise.rb:108" },
    { "observation": "Puma/worker/thread sizing.", "why_discarded": "No worker/thread configuration exists anywhere in this repository to check; folded into PERF-003's unknowns instead of a separate speculative finding." }
  ],
  "adjacent_findings": [
    {
      "id": "COR-001",
      "kind": "correctness",
      "confidence": "High",
      "risk": "High",
      "problem": "Article declares has_many :articles, dependent: :destroy (a self-referential association expecting an article_id column) but the articles table has no such column, so Article#destroy (used by DELETE /api/articles/:slug) will raise a SQL error on every call.",
      "evidence": [
        { "statement": "has_many :articles, dependent: :destroy", "kind": "static", "file": "app/models/article.rb", "line": 15 },
        { "statement": "articles table schema has no article_id column", "kind": "static", "file": "db/schema.rb" },
        { "statement": "ArticlesController#destroy calls @article.destroy, triggering all dependent: :destroy associations", "kind": "static", "file": "app/controllers/articles_controller.rb", "line": 57 }
      ],
      "recommendation": "Remove the erroneous has_many :articles, dependent: :destroy line; it appears to be a copy/paste artifact from the adjacent favorites/comments association lines.",
      "location": "app/models/article.rb:15",
      "assessed_properly_by": "A correctness-focused test suite exercising the destroy path for every model with dependent: :destroy associations; a general code review would also catch this."
    }
  ]
}
```

---

## 11. Notes on this review

- Findings are classified by evidence grade; `Confirmed` requires a cited runtime artifact. No finding in this review reached `Confirmed` because the repository is uninstrumented and no runtime artifact (query log, trace, load test) was supplied.
- No runtime metric in this report was estimated or assumed. The two query-count figures in §4 ("Derived") are explicit arithmetic derivations from code-visible constants (default page size, number of per-item accessors), with their inputs shown at the point of use, per Hard Rule 1 — they are not measurements.
- Recommendations state their trade-offs and their validation path. Where a claim could not be established from code or a supplied number (e.g., exact query counts the installed gem versions issue, real deployment concurrency), it is reported as an unknown or a structural argument rather than as a measured fact.
