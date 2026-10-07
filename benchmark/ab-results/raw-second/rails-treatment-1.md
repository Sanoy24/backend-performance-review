# Performance Review: rails-realworld (Conduit API, Rails 4.2)

**Date:** 2026-10-07
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0

---

## 1. Decision summary

### Overall assessment

This is a small, read-heavy JSON API (RealWorld "Conduit") on Rails 4.2.6, ActiveRecord, and SQLite (including in production). The most important thing to know: **the article list endpoints (`GET /api/articles`, `GET /api/articles/feed`) issue several queries per article returned, and the page size is taken from the client with no maximum**. Per-request query count therefore grows with whatever `limit` a caller sends. That cost lands on a 5-connection pool shared by up to 16 Puma threads (Puma's default), all on one SQLite file that allows only one writer at a time. The review is static only. The repo has no instrumentation and no runtime evidence was supplied, so every finding rests on code, not measurement. Workload is unknown and nobody answered the workload questions, so workload-dependent findings are capped at `Medium` confidence. **Review confidence: Medium.**

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Cap `limit` server-side. Batch-load the viewer's favorites and follows and preload tags for the page instead of looking them up per article. | Highest blast radius. Any caller can make one request issue thousands of queries. The cap is a one-line `quick-win`. |
| 2 | **PERF-003 (P1)** | Add `config/puma.rb` with thread count set equal to the AR `pool` (or size the pool to threads). Expose Puma `/stats`. | Puma 3.4's default of 16 threads against `pool: 5` means requests queue for connections and fail after the 5 s checkout timeout. |
| 3 | **PERF-005 (P1)** | Run `EXPLAIN QUERY PLAN` on the list query. If it shows a scan plus a temp B-tree sort, add an index on `articles(created_at)`. | Every unfiltered list request sorts the whole `articles` table and counts it. Cost grows with the table. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is SQLite really the production datastore, and with how many Puma processes/threads and hosts? | PERF-003/PERF-004 severity. Whether to recommend pool sizing or an engine change. | Deployment command or platform config (none is in the repo). |
| Do real clients send `limit` > 20? | PERF-001 moves between Critical (P1) and Medium (P2). | Access-log histogram of the `limit` query param. |
| Row counts of `articles`, `taggings`, and the largest comments-per-article | PERF-002, PERF-005, PERF-006 severity | `SELECT COUNT(*)` on a replica/copy of the DB file |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-005 | `safe-on-production` | `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY created_at DESC LIMIT 20 OFFSET 0;"` | A `SCAN` + `USE TEMP B-TREE FOR ORDER BY` result confirms the full sort. An index use refutes it. |
| PERF-004 | `safe-on-production` | `sqlite3 db/production.sqlite3 "PRAGMA journal_mode;"` | `delete` confirms readers block during commits. `wal` lowers PERF-004. |
| PERF-001 | `not-safe-on-production` | `RAILS_ENV=development bin/rails s` then `curl -s -H "Authorization: Token <dev-token>" "http://localhost:3000/api/articles?limit=50" >/dev/null` and count `SELECT` lines in `log/development.log` for that request | A query count proportional to `limit` confirms the N+1. A constant count refutes it. |

---

## 2. Scope and method

**Reviewed:** All application code under `app/` (8 controllers, 5 models, 13 Jbuilder views), `config/` (routes, database, environments, initializers other than secrets), `db/schema.rb` and the migrations directory listing, `Gemfile`/`Gemfile.lock`, `README.md`, `test/` fixtures.
**Not reviewed:** `config/secrets.yml` (secret file; its existence was noted but its contents were not read). Gem source for `acts-as-taggable-on` 3.5.0, `acts_as_follower` 0.2.1 and Devise 4.2.0 is not vendored. Claims about the SQL those gems emit are inferred from their public APIs and are labelled. Frontend assets (`app/assets`) are out of scope for a backend review.

**Evidence available:** Uninstrumented. There is no APM, metrics, tracing, benchmark, load test, query-plan artifact, SLO, or dashboard in the repo. The only signal is the Rails log, set to `:debug` in production (`config/environments/production.rb:49`).

**Ranking method:** Structural signals only. No runtime data exists. The ranking is inference.

**Reference depth:** Ruby, REST (Rails), and SQLite have `deep` support. The ORM-level analysis uses `application/data-access.md`. Behaviour of the tagging/follow gems is not covered by any reference and is reasoned from their documented APIs.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every Ruby and Jbuilder file under `app/`, all non-secret `config/` files, the schema, and the manifests were read in full |
| **Critical paths** | 9 / 9 (all routed endpoints grouped into 9 paths, §5) |
| **Shared resources** | 3 / 3 (AR connection pool, Puma thread pool, SQLite database file) |
| **Technology support** | Ruby: Deep. Rails/REST: Deep. SQLite: Deep. acts-as-taggable-on / acts_as_follower: Generic |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not finding confidence. The code was fully read, but nothing was measured, and the deployment shape is not in the repo.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production process/thread count and number of hosts | No evidence exists (no `config/puma.rb`, `Procfile`, Dockerfile, or platform config) | The real start command / platform settings |
| Production traffic rate, `limit` distribution, table sizes | No evidence exists | Access logs, `COUNT(*)` on a DB copy |
| Exact SQL emitted by `tag_list`, `tag_counts`, `following?` | Technology unsupported (gem internals not vendored, no reference file) | Development query log for one request (validation §9) |
| SQLite journal mode / file location in production | No evidence exists | `PRAGMA journal_mode;` and the host's volume config |

---

## 3. Architecture overview

A single Rails 4.2 monolith serving a JSON API under `/api` (`config/routes.rb:2-20`). Requests authenticate with a JWT in the `Authorization` header (`app/controllers/application_controller.rb:18-30`). Serialization uses Jbuilder partials with camelCase keys. There is no cache store, background job system, broker, or outbound HTTP call.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime | Ruby (MRI assumed) | not pinned in repo | deep | Application runtime |
| Framework | Rails / ActiveRecord / Jbuilder | 4.2.6 / 4.2.6 / 2.5.0 (`Gemfile.lock`) | deep | HTTP + ORM + JSON |
| App server | Puma | 3.4.0 (`Gemfile.lock:92`), no config file | deep (via ruby.md) | Thread-per-request |
| Datastore | SQLite via `sqlite3` gem | gem 1.3.11; engine version unknown | deep | Sole datastore, **including production** (`config/database.yml:23-25`) |
| Tagging | acts-as-taggable-on | 3.5.0 | generic | Article tags |
| Follows | acts_as_follower | 0.2.1 | generic | User follow graph |
| Auth | Devise 4.2.0 + jwt 1.5.4 + bcrypt 3.1.11 | — | generic | Login / token |

**Shared resources:** the ActiveRecord connection pool (`pool: 5`, `config/database.yml:9`), Puma's request thread pool (default, unconfigured), and the single SQLite database file (single writer, `timeout: 5000` ms busy timeout).

---

## 4. Workload model

**Known**

- Application routes outside Devise: 7 GET (articles index, feed, show, comments index, profile show, tags index, user show) and 10 mutating (user update, follow ×2, article ×3, favorite ×2, comment ×2), plus Devise login/registration. By endpoint count the surface is read-leaning. Source: `config/routes.rb:2-20`
- Article list page size defaults to 20 and is client-overridable with no maximum. Source: `app/controllers/articles_controller.rb:13,21`
- The comments list has no pagination at all. Source: `app/controllers/comments_controller.rb:6`
- AR pool is 5 and SQLite busy timeout is 5000 ms in every environment. Source: `config/database.yml:9-10`
- Production uses SQLite file `db/production.sqlite3`. Source: `config/database.yml:23-25`
- Puma 3.4.0 is the only app server declared, with no `config/puma.rb`. Puma's documented 3.x defaults are 0–16 threads in single mode. Source: `Gemfile:31`, `Gemfile.lock:92`, file listing
- There is no retention or archival job for any table, and no scheduled jobs at all. `articles`, `comments`, `favorites`, `taggings`, and `follows` grow monotonically with user activity. Source: `db/migrate/`, `lib/tasks/` (empty)
- `favorites_count` is counter-cached on `articles`. `taggings_count` exists on `tags`. Source: `app/models/favorite.rb:3`, `db/schema.rb:21,79`
- Production log level is `:debug`. Source: `config/environments/production.rb:49`

**Assumed**

- Real clients sometimes request pages larger than 20, or a hostile caller could. Affects PERF-001.
- `articles` and `taggings` grow into at least the thousands of rows over the app's life. Affects PERF-005, PERF-006.
- Production runs Puma with its defaults (no config file found). Affects PERF-003.
- The tags and global-feed endpoints are loaded on the landing page, as in the RealWorld frontend spec, so they are among the most frequent requests. Affects PERF-006, PERF-005.

**Unknown**

- Request rate per endpoint and its peak.
- Actual row counts and the largest comments-per-article.
- Whether SQLite is really what runs in production, and the number of hosts/processes.
- Current latency and any SLO.

**Derived**

- Queries per signed-in `GET /api/articles` = 3 fixed (count, page, user preload) + 3 per article (`favorited?`, `following?`, tag list). That is **3 + 3N**: 63 at the default N=20, and 3,003 at `limit=1000`. Inputs: `articles_controller.rb:5-13`, `_article.json.jbuilder:1,3`, `_profile.json.jbuilder:3`. The per-article tag-list query is an assumption about gem behaviour, so without it the count is 3 + 2N.
- Queries per anonymous `GET /api/articles` = **3 + N** (tag list only). Same inputs.
- Queries per signed-in `GET /api/articles/:slug/comments` = 2 fixed (article lookup, comment list) + 2 per comment (author lookup, `following?`) = **2 + 2C**, with C uncapped. Inputs: `comments_controller.rb:6,34`, `_comment.json.jbuilder:2`, `_profile.json.jbuilder:3`.
- Puma default max threads (16) minus AR pool (5) leaves 11 threads per process that can wait on a connection at full concurrency. Inputs: Puma 3.x default, `database.yml:9`.

**Measured**

- None. No benchmark, trace, query plan, or log was supplied. This is why no finding is `Confirmed`.

### Questions that would change the ranking

These are the questions I would have asked. Nobody was available to answer, so the review proceeded under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is SQLite the actual production datastore, and how many hosts / Puma processes / threads serve it? | severity, recommendation | If Postgres or MySQL is substituted at deploy time, PERF-004 drops out and PERF-003 becomes a pool-vs-`max_connections` question. If several hosts are used, SQLite is unworkable outright. |
| 2 | highest | Do any clients request `/api/articles` with `limit` above 20, and what is the largest seen? | severity, confidence | If never and a cap is added, PERF-001 becomes Example-B shaped (Medium, P2). If yes, PERF-001's confidence rises and it moves toward P0. |
| 3 | high | Roughly how many rows are in `articles` and `taggings`, and what is the most-commented article's comment count? | severity | Small, stable tables make PERF-005 and PERF-006 Low/P3. Tens of thousands of rows keep them P1. |
| 4 | high | What is the peak request rate on `GET /api/articles` and `GET /api/tags`, and what share is authenticated? | severity, recommendation | Low traffic pushes everything except PERF-001's unbounded case down a level. A high anonymous share shifts weight from `favorited?`/`following?` to the tag-list query. |
| 5 | medium | Is there a specific slowness or incident that prompted this review? | recommendation | The review would be re-centred on confirming or refuting it. |

---

## 5. Critical path analysis

Ranked by structural signals.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | yes | 3 + 3N (signed in), 3 + N (anon) | **no**: `limit` uncapped | no | COUNT + unindexed ORDER BY; N+1 in partial |
| 2 | `GET /api/articles/feed` | yes | 4 + 3N (adds current_user lookup) | **no**: `limit` uncapped | no | `user_id IN (followed)` subquery; same partial |
| 3 | `GET /api/tags` | yes | 1 aggregate over all article taggings | result capped at 20 by `most_used`; scan is not | no | Unauthenticated |
| 4 | `GET /api/articles/:slug/comments` | yes | 2 + 2C | **no** | no | No `includes(:user)`, no pagination |
| 5 | `GET /api/articles/:slug` | yes | ~5 (find, user, favorited?, following?, tags) | yes | no | Single row, bounded |
| 6 | `POST/DELETE /api/articles/:slug/favorite` | yes | find + find_or_create/destroy + counter update + render | yes | no | Write → SQLite writer lock |
| 7 | `POST /api/articles`, `PUT`, `DELETE` | yes | insert + tag writes (gem) | yes | no | Write → SQLite writer lock |
| 8 | `POST /api/users/login`, registration | yes | 1 lookup + bcrypt (cost 11) | yes | no | CPU-bound by design |
| 9 | `GET/PUT /api/user`, `GET /api/profiles/:u`, follow/unfollow | yes | 1–3 point lookups | yes | no | Indexed point lookups |

**Amplification points:** paths 1 and 2 each do 3 queries per item × a client-chosen N. Path 4 does 2 queries per item × an unbounded C. Debug-level logging (PERF-007) adds one synchronous log write per query on all of them.

**Paths deliberately not analyzed in depth:** none were skipped. Paths 5–9 were read fully and found bounded. Asset pipeline/frontend is out of scope.

---

## 6. Layer analysis

### 6.1 Application / API

Serialization is Jbuilder partials rendered per array element (`articles/index.json.jbuilder:2`, `comments/index.json.jbuilder:2`). The partials are not pure data mappers: `_article.json.jbuilder:3` and `_profile.json.jbuilder:3` issue database queries, which moves N+1 into the view layer where the controller cannot see it (PERF-001, PERF-002). The list endpoints take `offset`/`limit` straight from params with no clamp (PERF-001). JWT verification per request is a single HMAC check and is not material.

### 6.3 Data access and datastore

- `ArticlesController#index` preloads only `:user` (`articles_controller.rb:5`). It does not preload tags or the viewer's favorites and follows (PERF-001).
- `CommentsController#index` preloads nothing and has no limit (PERF-002).
- `articles` has no index on `created_at`, yet every list request orders by it, and every list request also runs a full `COUNT` (PERF-005).
- `TagsController#index` uses `Article.tag_counts.most_used`, a grouped aggregate over taggings, even though `tags.taggings_count` is already maintained (PERF-006).
- `favorites` has separate single-column indexes on `user_id` and `article_id` but no composite `(user_id, article_id)`. `favorited?` (`user.rb:37`) and `find_or_create_by` (`user.rb:27`) filter on both columns (folded into PERF-001; see also COR-002).
- SQLite in production with the default rollback journal assumed, a single writer, and `pool: 5` (PERF-003, PERF-004).

### 6.6 Infrastructure

No Dockerfile, Procfile, `config/puma.rb`, or platform manifest exists. Puma's defaults therefore govern concurrency (PERF-003).

### 6.7 Observability

The repo is uninstrumented: no APM, no metrics, no per-request query counter, no pool wait-time metric. The only signal is `:debug` logging, which is itself a cost (PERF-007) and not a usable metric. No optimization in this report can be validated in production without adding at least query-count and pool-wait visibility (PERF-008).

---

## 7. Findings

### PERF-001 — Article list issues 3 queries per article, and the page size is client-controlled with no cap

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/articles_controller.rb:5-13` (also `:17-21`, `app/views/articles/_article.json.jbuilder:1,3`, `app/views/profiles/_profile.json.jbuilder:3`) |
| **Tags** | quick-win, scalability-risk |

**Problem**
For each article in the page, the serializer runs `current_user.favorited?(article)` (a query), `current_user.following?(article.user)` (a query), and `article.tag_list` (tags are not preloaded). The page size is `params[:limit] || 20` with no upper bound, so one request's query count is set by the caller.

**Performance principle**
Per-item work in a list must be batched, and the size of a list a caller can request must be bounded by the server.

**Evidence**
- `articles_controller.rb:5`: `Article.all.includes(:user)` preloads only authors.
- `articles_controller.rb:13` and `:21`: `.offset(params[:offset] || 0).limit(params[:limit] || 20)`, with no clamp anywhere.
- `_article.json.jbuilder:3`: `current_user.favorited?(article)`. Then `user.rb:36-38` does `favorites.find_by(article_id: article.id)`, one query per article.
- `_profile.json.jbuilder:3`: `current_user.following?(user)`, an acts_as_follower lookup on `follows`, one per article.
- `_article.json.jbuilder:1`: `:tag_list`. With no `includes(:tags)` / `includes(:taggings)`, acts-as-taggable-on loads tags per record. This is gem behaviour, inferred and not verified in this repo.
- `index.json.jbuilder:2` renders the partial once per element.
- No runtime evidence exists.

**Impact**
- Position: critical path (`GET /api/articles`, `GET /api/articles/feed`, which the RealWorld frontend loads on the home page).
- Frequency: per item, per request.
- Growth: O(n) in the caller-chosen page size. Query count = 3 + 3N signed in (derived, §4). Critical comes from system-wide saturation, not superlinear growth. N is caller-chosen with no ceiling, so `limit=1000` means about 3,003 queries and the whole page's objects held in memory.
- Blast radius: system-wide. Each request holds one of the 5 pooled connections for the duration of all those queries, while up to 16 Puma threads compete for them (PERF-003).

**Conditions**
Matters at default N=20 under moderate traffic, as 63 queries per signed-in page view. It becomes an outage mechanism when any caller sends a large `limit`. Assumption: such requests occur or can be sent by an unauthenticated caller. The endpoint is unauthenticated (`articles_controller.rb:2`), so nothing prevents it.

**Counter-evidence**
- Searched for a `limit` clamp in the controller, a `before_action`, Rack middleware, and routes. None found (no effect).
- Searched for preloading of tags or favorites (`includes`, `preload`, `eager_load`) and for memoization of `favorited?`/`following?`. None found (no effect).
- `favorites_count` is counter-cached (`favorite.rb:3`), so the favourites *count* is not an N+1. Only `favorited?` is. This bounds the impact.
- Whether `tag_list` issues a query per row depends on gem 3.5.0 internals that are not in the repo. This lowers confidence on one of the three per-row queries.

**Why this might not matter**
If real clients only ever send the default 20 and traffic is low, 63 cheap indexed point queries on a local SQLite file may cost a few milliseconds in total. SQLite queries are in-process function calls with no network round trip, which shrinks per-query overhead compared with a client-server database.

**Recommendation**
1. Clamp `limit` server-side (for example `[[params[:limit].to_i, 1].max, MAX].min` with `MAX` chosen by the team) and clamp `offset` to a non-negative integer. This bounds the work.
2. Remove the per-row queries. Preload tags (`includes(:tags)` or the gem's tagging association). Fetch the viewer's favourited article IDs for the page in one query (`current_user.favorites.where(article_id: ids).pluck(:article_id)`) and the followed author IDs in one query against `follows`. Pass them to the partial as sets. This turns 3N queries into 3, independent of page size.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Clamp `limit` + batch-load favorites/follows/tags per page | **preferred** | Removes the work and bounds it. No new infrastructure or staleness. |
| B: Clamp `limit` only | | Cheap and stops the unbounded case, but leaves 3N queries per page. |
| C: Cache rendered article JSON | | Per-viewer fields (`favorited`, `following`) make the cache key per user, with low hit-rate and invalidation on every favourite or follow. It hides the work instead of removing it. |

**Trade-offs**
A cap changes API behaviour for clients that relied on large pages. The batched lookups add a small amount of controller code and two extra (constant) queries per page. Preloading tags loads tag rows even for clients that ignore them.

**Validation**
Baseline: query count for `GET /api/articles?limit=20` and `?limit=100` as a signed-in user, from the development log (not safe on production: needs a dev server). Expectation: before the fix the count grows by about 3 per extra article; after, it is constant across `limit` values. Falsifier: if the count is already constant across `limit`, the N+1 does not exist as described. Guard: an integration test asserting query count (via `ActiveSupport::Notifications` subscription to `sql.active_record`) is the same for 5 and 20 articles, and a test that `limit=10000` returns at most `MAX`.

---

### PERF-003 — Puma's default 16 threads share a 5-connection pool

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `config/database.yml:9` |
| **Tags** | quick-win, needs-measurement |

**Problem**
The ActiveRecord pool has 5 connections per process. There is no Puma configuration, so Puma 3.4 runs its default of up to 16 threads. Under concurrency above 5, request threads queue for a connection and raise `ActiveRecord::ConnectionTimeoutError` after the default 5 s checkout wait.

**Performance principle**
Pool size must match the number of concurrent workers that need it. Otherwise queueing for the pool becomes the dominant, invisible latency term.

**Evidence**
- `config/database.yml:9`: `pool: 5`.
- `Gemfile:31` / `Gemfile.lock:92`: `puma (3.4.0)`.
- No `config/puma.rb`, `Procfile`, or Dockerfile anywhere in the repo (full file listing), so no thread setting overrides the default.
- Puma 3.x's documented default is `threads 0, 16`.
- No runtime evidence exists.

**Impact**
- Position: critical path.
- Frequency: every request that touches the database (all of them).
- Growth: queueing grows non-linearly as concurrency approaches the pool limit.
- Blast radius: system-wide. One slow list request (PERF-001) holds a connection that every other endpoint needs.

**Conditions**
Assumes production starts Puma with defaults (no config file in the repo) and sees bursts of more than 5 concurrent DB-bound requests per process. If the deployment sets `-t 5:5` or `RAILS_MAX_THREADS`, the mismatch disappears.

**Counter-evidence**
- Searched for `config/puma.rb`, `Procfile`, `bin/` start scripts, env-var-driven pool settings (`ENV['RAILS_MAX_THREADS']`), and Dockerfiles. None found (no effect).
- Rails 4.2 does not tie pool size to thread count automatically (no effect).
- The deployment command is unknown, which lowers confidence (reflected in Medium).

**Why this might not matter**
At low concurrency (5 or fewer in-flight requests per process) the pool is never exhausted. Under MRI's GVL, CPU-heavy rendering also limits how much real parallelism 16 threads would add.

**Recommendation**
Add `config/puma.rb` with explicit `threads` and `workers`. Set `pool` to `ENV['RAILS_MAX_THREADS']`, using the same value Puma uses, so the two cannot drift. For SQLite, keep thread count modest, because extra connections add no write concurrency (PERF-004).

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Make Puma threads = AR pool, driven by one env var | **preferred** | Removes the mismatch at its source. Standard Rails practice. |
| B: Raise `pool` to 16 | | For SQLite this adds connections that contend for the single writer lock and increase `SQLITE_BUSY` (sqlite.md §2). |
| C: Lower Puma threads to 5 | | Equivalent to A without the single source of truth. |

**Trade-offs**
Fewer threads lowers peak I/O concurrency per process. More worker processes cost memory per process.

**Validation**
Baseline: Puma `/stats` (control app) or `pumactl stats` for `running`/`pool_capacity` (safe-on-production), plus any `ConnectionTimeoutError` in logs. Expectation: after the change, no connection-checkout timeouts under the same load. Falsifier: if production already passes explicit thread settings of 5 or fewer, the finding does not apply. Guard: boot-time assertion that `ActiveRecord::Base.connection_pool.size >= Puma max threads`.

---

### PERF-005 — Every article list request sorts and counts the whole `articles` table

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `db/schema.rb:16-28` (query at `app/controllers/articles_controller.rb:11-13`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
`GET /api/articles` runs `@articles.count` and then `ORDER BY created_at DESC LIMIT/OFFSET`. `articles` has indexes only on `slug` and `user_id`, so the unfiltered list (the global feed) must scan all rows to sort them. The count also scans. Large `offset` values add further linear cost.

**Performance principle**
A per-request query's cost should not grow with total table size when it returns a fixed-size page.

**Evidence**
- `db/schema.rb:27-28`: indexes on `slug` (unique) and `user_id` only. No `created_at` index.
- `articles_controller.rb:11`: `@articles_count = @articles.count`.
- `articles_controller.rb:13`: `.order(created_at: :desc).offset(...).limit(...)`.
- The query plan was not examined (no runtime evidence).

**Impact**
- Position: critical path (global feed, the default landing view in RealWorld clients).
- Frequency: per request.
- Growth: O(n) in `articles` rows for both the count and the sort. OFFSET adds O(offset).
- Blast radius: service. Holds a pool connection and, in rollback-journal mode, a shared lock that delays writers.

**Conditions**
Matters once `articles` reaches a size where a full scan is noticeable. That size is unknown (no row counts). Filtered variants (`author`, `favorited`, `tag`) are narrowed first by indexed joins and suffer less.

**Counter-evidence**
- Checked all 11 migrations and `schema.rb` for a `created_at` index. None found (no effect).
- SQLite's sorter uses a bounded top-N heap when `LIMIT` is present, so memory is bounded even though the scan is not (bounds impact: kept at High rather than Critical).
- The total count is required by the API's `articlesCount` field, so it cannot simply be removed (affects the recommendation, not the score).

**Why this might not matter**
A demo-scale or small community site may never accumulate enough articles for a scan of a local file to register. SQLite scans of a few thousand narrow rows are fast.

**Recommendation**
Confirm the plan with `EXPLAIN QUERY PLAN`. If it shows a scan with a temp B-tree for ORDER BY, add an index on `articles(created_at)`, which lets SQLite walk the index in order and stop after `offset+limit`. For the count, consider whether an exact total is required on every page. If so, it remains O(n). If not, a cheaper approximate or a cached total is an option, but only with an agreed staleness.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Index `created_at` after confirming the plan | **preferred** | Makes the page query O(offset+limit). Small write cost. |
| B: Keyset pagination (`created_at < cursor`) | | Removes the OFFSET cost too, but changes the public API contract (RealWorld spec uses offset). |
| C: Cache the global feed | | Every new article invalidates it. Hides the scan rather than removing it. |

**Trade-offs**
An index adds write cost on every article insert and some storage. The index build on an existing table takes the SQLite write lock for its duration. Run it in a quiet window.

**Validation**
Baseline: `EXPLAIN QUERY PLAN` on the list query (safe-on-production). Expectation: after the index, the plan reads `SCAN articles USING INDEX index_articles_on_created_at` with no temp B-tree. Falsifier: if the current plan already avoids a temp B-tree, or the table is small enough that timings are flat, drop to Low. Guard: a test or CI check that runs `EXPLAIN QUERY PLAN` on this query and fails on `USE TEMP B-TREE FOR ORDER BY`.

---

### PERF-006 — Tag list endpoint aggregates all article taggings on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/tags_controller.rb:3` |
| **Tags** | needs-measurement |

**Problem**
`Article.tag_counts.most_used` computes per-tag usage counts with a grouped aggregate over every `Article` tagging, on each unauthenticated request, to return 20 names. The schema already maintains `tags.taggings_count`, which would answer the same question from the small `tags` table.

**Performance principle**
Do not recompute on every read an aggregate that is already maintained incrementally on write.

**Evidence**
- `tags_controller.rb:3`: `Article.tag_counts.most_used.map(&:name)`.
- `db/schema.rb:79`: `tags.taggings_count` counter column, added by `20160712054811_add_taggings_counter_cache_to_tags...rb`.
- The SQL shape of `tag_counts` (a GROUP BY over `taggings` joined to `articles`) is inferred from acts-as-taggable-on 3.5's documented API and is not visible in this repo.
- No runtime evidence exists.

**Impact**
- Position: critical path (RealWorld clients load popular tags on the home page).
- Frequency: per request.
- Growth: O(taggings) per request, monotonically growing (no retention).
- Blast radius: service (pool connection; read lock on SQLite).

**Conditions**
Assumes `/api/tags` is requested roughly as often as the home page and that `taggings` grows into the thousands or more. Both are unverified.

**Counter-evidence**
- Searched for caching around the tags endpoint (`Rails.cache`, `expires_in`, HTTP caching headers, `fresh_when`). None found. `perform_caching = true` is set, but no cache calls exist (no effect).
- `most_used` caps the *result* at 20 rows but not the scan (bounds the payload, not the work).
- The gem's SQL is not verifiable from the repo, which lowers confidence (reflected in Medium).

**Why this might not matter**
The `taggings` table may stay small. `taggings_count` counts taggings for all taggable types, so if any non-Article model were tagged it would differ from `Article.tag_counts`. Only Article is tagged today, so the two match. That difference is a legitimate reason someone chose the scoped query.

**Recommendation**
Read the top tags from `ActsAsTaggableOn::Tag.most_used(20)` (ordered by `taggings_count`). This removes the aggregate, because only `Article` is taggable in this codebase. Confirm with the query log first. If per-type scoping is ever needed, add an index-backed query or a short-TTL cache with an explicit staleness budget.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Use the maintained `taggings_count` | **preferred** | Removes the work. Data already exists and is consistent on write. |
| B: Cache the 20 names for a short TTL | | Bounded staleness is acceptable for "popular tags", but it keeps the expensive query on every miss. Needs a cache store (none is configured). |

**Trade-offs**
Option A's counts include any future non-Article taggings. The counter can drift if taggings are written outside the gem (for example raw SQL imports).

**Validation**
Baseline: development log of `GET /api/tags` showing the aggregate SQL. Not safe on production for logging changes; `EXPLAIN QUERY PLAN` of the logged SQL is safe-on-production. Expectation: the replacement issues one indexed `SELECT ... FROM tags ORDER BY taggings_count DESC LIMIT 20`. Falsifier: if the logged `tag_counts` SQL already reads `taggings_count` without a GROUP BY over `taggings`, this finding is refuted. Guard: a query-shape test on this endpoint.

---

### PERF-002 — Comments list is unbounded and issues 2 queries per comment

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/comments_controller.rb:6` |
| **Tags** | quick-win |

**Problem**
`@article.comments.order(created_at: :desc)` returns every comment for the article with no limit and no `includes(:user)`. The partial then loads each author (`comment.user`) and, when signed in, runs `following?` per comment.

**Performance principle**
Per-item lookups in a list must be batched, and list responses must be bounded.

**Evidence**
- `comments_controller.rb:6`: no `includes`, no `limit`.
- `_comment.json.jbuilder:2`: `comment.user` → `_profile.json.jbuilder:3`: `current_user.following?(user)`.
- `comments_controller.rb:2`: `index` is public.
- No runtime evidence exists.

**Impact**
- Position: critical path (article detail page).
- Frequency: per item, per request.
- Growth: O(C). Query count 2 + 2C signed in (derived, §4), and C has no cap.
- Blast radius: endpoint, but it holds a pool connection for the duration.

**Conditions**
Matters for articles with many comments. The largest comment count is unknown. C grows only with activity on a single article, not with total table size, so this is scored High rather than Critical.

**Counter-evidence**
- Searched for pagination or a default scope on `Comment`. None found (no effect).
- `comments.article_id` is indexed (`schema.rb:38`), so the outer query is cheap. Only the per-row lookups and unbounded payload matter (bounds impact).

**Why this might not matter**
Most articles on a typical blog get a handful of comments. At C ≈ 5 this is roughly 12 point queries.

**Recommendation**
Add `.includes(:user)` and batch the viewer's follow lookups for the distinct author IDs (one query), as in PERF-001. Add a server-side cap or pagination on comments.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Preload authors + batch follow lookup + cap | **preferred** | Removes per-item work and bounds the response. |
| B: Preload authors only | | Removes half the per-item queries, but `following?` remains per comment. |

**Trade-offs**
Pagination changes the RealWorld response contract (which returns all comments). Clients may need updating.

**Validation**
Baseline: query count for this endpoint on an article seeded with 50 comments (dev log; not-safe-on-production). Expectation: the count becomes constant, independent of C. Falsifier: if the count is already constant, the finding is refuted. Guard: a query-count test.

---

### PERF-004 — Production datastore is a single SQLite file: one writer, readers blocked during commits

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `config/database.yml:23-25` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
Production points at `db/production.sqlite3`. Every write (favourite, comment, article, follow, plus the `favorites_count` counter update) serialises on the file-level write lock. No `journal_mode` is configured, so the default rollback journal also blocks readers while a commit is in progress. The 5000 ms busy timeout means contended requests can wait up to 5 s before failing. The file is host-local, so the app cannot scale past one host.

**Performance principle**
A single serialisation point shared by all writes caps write throughput regardless of how many workers are added.

**Evidence**
- `config/database.yml:8-10,23-25`: `adapter: sqlite3`, `timeout: 5000`, production file path.
- No `PRAGMA journal_mode` setting anywhere (searched `config/`, `db/`, initializers).
- `favorite.rb:3`: `counter_cache: true` turns each favourite into two writes in one transaction.
- No runtime evidence exists.

**Impact**
- Position: critical path for every write, and for reads that coincide with a commit.
- Frequency: per write request.
- Growth: contention grows with concurrent write rate. Bounded per operation.
- Blast radius: system-wide (one file for everything).

**Conditions**
Matters only if production actually runs on SQLite and sees concurrent writes, or needs more than one host. Both are unknown (question 1).

**Counter-evidence**
- Searched for a production `DATABASE_URL` override, a `pg`/`mysql2` gem, or platform config. None found: the Gemfile declares only `sqlite3` (no effect).
- This is the RealWorld *example* app, and SQLite in production may never have been intended for real deployment (lowers confidence: reflected in Medium).

**Why this might not matter**
For a single-host, low-write blog workload, SQLite with WAL is entirely adequate, and its in-process reads are faster than a networked database.

**Recommendation**
First establish what production runs (question 1). If SQLite stays: enable WAL so reads stop blocking on commits, and keep Puma thread count at the pool size (PERF-003). If more than one host or sustained concurrent writes are required, move to a client-server engine. Make that move only on that evidence, not by default.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Confirm deployment, then enable WAL on SQLite | **preferred** | Cheapest change that removes reader/writer blocking. No new infrastructure. |
| B: Migrate to Postgres/MySQL | | Removes the single-writer and single-host limits, but at real operational cost. Justified only by multi-host or write-concurrency evidence. |

**Trade-offs**
WAL adds a `-wal`/`-shm` file and checkpoint management. A long read transaction can let the WAL grow. Migrating engines is a substantial operational change.

**Validation**
`PRAGMA journal_mode;` (safe-on-production). Count `SQLite3::BusyException` / `database is locked` in production logs (safe-on-production). Expectation: after WAL, no reader waits on commits. Falsifier: production already runs another engine, or WAL is already on.

---

### PERF-007 — Production logs at `:debug`, writing every SQL statement synchronously

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `config/environments/production.rb:49` |
| **Tags** | quick-win |

**Problem**
`config.log_level = :debug` in production makes ActiveRecord format and write a log line for every query. Combined with PERF-001's 3 + 3N queries per page, logging I/O and string formatting scale with page size on the request thread.

**Performance principle**
Instrumentation overhead must be proportionate. Per-operation logging on the hot path multiplies with that path's amplification.

**Evidence**
`config/environments/production.rb:49`. The Rails 4.2 AR log subscriber logs SQL at debug level.

**Impact**
- Position: critical path.
- Frequency: per query.
- Growth: O(queries per request).
- Blast radius: service (log I/O on the shared disk, and log volume).

**Conditions**
Holds at any traffic. The magnitude scales with query count per request and request rate.

**Counter-evidence**
Searched for a log-level env override (`RAILS_LOG_LEVEL`) or a custom logger. None found (no effect).

**Why this might not matter**
Buffered file logging is cheap per line. If PERF-001 is fixed, the query count per request falls and so does this cost.

**Recommendation**
Set production to `:info`. Use sampled or on-demand debug logging for query investigation.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: `:info` in production | **preferred** | Removes per-query log writes. One line. |
| B: Keep `:debug` as the only query visibility | | It is the only query signal today (§6.7), but it is a costly and unstructured substitute for real instrumentation (PERF-008). |

**Trade-offs**
Removes the only current per-query visibility in production. Pair it with PERF-008.

**Validation**
Compare log bytes per request before and after (safe-on-production). Expectation: SQL lines disappear from production logs. Falsifier: production already overrides the level at deploy time.

---

### PERF-008 — No request timing, query count, or pool wait instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `Gemfile` |
| **Tags** | needs-measurement |

**Problem**
No APM, metrics, or query counting exists, so none of PERF-001 to PERF-007 can be confirmed or validated in production.

**Performance principle**
A system must be measured before it is optimised.

**Evidence**
`Gemfile` / `Gemfile.lock`: no APM, metrics, or profiler gems. No `ActiveSupport::Notifications` subscribers in `config/initializers/`.

**Impact**
- Position: critical path (enabling).
- Frequency: n/a (per request).
- Growth: O(1).
- Blast radius: service.

**Conditions**
Applies at any workload. It is the precondition for validating every other finding.

**Counter-evidence**
Searched initializers, the Gemfile, and `lib/` for instrumentation. None found (no effect).

**Recommendation**
Add per-request duration plus SQL query count (an `sql.active_record` subscriber, or an APM agent) and Puma thread/backlog stats. Sequence this before or alongside the PERF-001 fix.

**Trade-offs**
Small per-request overhead and a new dependency or agent.

**Validation**
After the change, per-endpoint query count and p95 duration are visible. Falsifier: n/a (presence check).

---

### Remaining findings

All findings are given in full above.

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-001 | Critical | Medium | P1 | `app/controllers/articles_controller.rb:5-13` | Article list N+1 (3/article) with uncapped `limit` |
| PERF-003 | High | Medium | P1 | `config/database.yml:9` | Puma default 16 threads vs pool 5 |
| PERF-005 | High | Medium | P1 | `db/schema.rb:16-28` | Unindexed `ORDER BY created_at` + full COUNT per list |
| PERF-006 | High | Medium | P1 | `app/controllers/tags_controller.rb:3` | Tag counts aggregated per request despite counter cache |
| PERF-002 | High | Medium | P1 | `app/controllers/comments_controller.rb:6` | Comments unbounded, 2 queries/comment |
| PERF-004 | Medium | Medium | P2 | `config/database.yml:23-25` | SQLite single writer in production, no WAL |
| PERF-007 | Medium | High | P2 | `config/environments/production.rb:49` | `:debug` logging of every query in production |
| PERF-008 | Low | High | P3 | `Gemfile` | No timing/query-count/pool instrumentation |

### Considered and not reported

| Observation | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| Jbuilder per-element partial rendering CPU cost (`json.array! ... partial:`) | `GET /api/articles`, comments | `index.json.jbuilder:2`, `comments/index.json.jbuilder:2`, jbuilder 2.5.0 | Probably dominated by PERF-001's I/O. No profile to show CPU is a meaningful share (critical-paths §3). | A `stackprof` profile of the list endpoint shows template rendering as a top frame after PERF-001 is fixed |
| bcrypt cost 11 on login/registration | `POST /api/users/login` | `config/initializers/devise.rb:108` | Intentional security cost. Bounded per login. Low frequency. | Login rate becomes a large share of traffic, or Puma threads saturate on login bursts |
| `dependent: :destroy` cascades on article/user delete instantiate each child | `DELETE /api/articles/:slug` | `article.rb:3-4`, `user.rb:7-9` | Rare operation. Bounded by per-article children. The path is currently broken anyway (COR-001). | Bulk deletes or very large comment threads appear |
| Feed `where(user: following_users)` subquery | `GET /api/articles/feed` | `articles_controller.rb:17`, `schema.rb:28,62` | Uses `user_id` index and `fk_follows`. Bounded by followed authors' articles. Shares PERF-001/PERF-005 costs, which are already reported. | A user follows thousands of authors |
| Per-request JWT decode | All authenticated requests | `application_controller.rb:18-30` | A single HMAC verification. Constant and negligible next to DB work. | A profile shows auth as a top frame |

### Adjacent findings — outside performance scope

### COR-001 — `Article has_many :articles, dependent: :destroy` breaks article deletion

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `app/models/article.rb:15` |

**Problem** `Article` declares `has_many :articles, dependent: :destroy`. On destroy, Rails loads `articles WHERE article_id = ?`, but `articles` has no `article_id` column.
**Evidence** `app/models/article.rb:15`. `db/schema.rb:16-25` has no `article_id`. `articles_controller.rb:57` calls `@article.destroy`.
**Impact** `DELETE /api/articles/:slug` would raise a SQL error and roll back, so authors likely cannot delete articles. Medium risk: one endpoint is broken, with no data loss.
**Recommendation** Remove the association.
**Trade-offs** None.
**Validation** A request or model test that destroys an article with comments and favourites.
**Would need** A correctness-focused test suite covering destroy paths.

### COR-002 — Favourite creation is not protected by a unique index

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Low |
| **Location** | `app/models/user.rb:27` |

**Problem** `favorites.find_or_create_by(article: article)` is check-then-insert with no unique `(user_id, article_id)` index. Concurrent double-clicks can create duplicates and inflate `favorites_count`.
**Evidence** `user.rb:27`. `db/schema.rb:48-49` has only single-column non-unique indexes.
**Impact** Low risk: cosmetic count inflation. SQLite's single writer narrows the race window but does not close it across separate transactions.
**Recommendation** Add a unique composite index on `favorites(user_id, article_id)` and rescue `RecordNotUnique`. This also serves the `favorited?` lookup (PERF-001).
**Trade-offs** Index write cost. Existing duplicates must be cleaned before the index can be built.
**Validation** A concurrency test issuing two simultaneous favourites.
**Would need** A data-integrity review.

### MAINT-001 — Framework and auth dependencies are long past end of life

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `Gemfile.lock` |

**Problem** Rails 4.2.6, Devise 4.2.0, jwt 1.5.4, and Puma 3.4.0 are pinned. The Rails 4.2 line no longer receives security fixes.
**Evidence** `Gemfile:5`, `Gemfile.lock:29,57,76,92,97`.
**Impact** High risk: an internet-facing auth/API stack without upstream security patches.
**Recommendation** Plan an upgrade path (the README itself points to a Rails 5.1 branch).
**Trade-offs** Significant migration effort (strong params, `update_attributes`, and similar changes).
**Validation** A dependency audit (e.g. `bundle audit`) after the upgrade.
**Would need** A dependency/security review.

---

## 8. Prioritized action plan

### P1 — High priority (with one P3 sequenced first)

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-008 | P3 | small | **Sequenced early despite P3:** it is cheap and makes every P1 validatable |
| 2 | PERF-001 | P1 | small (cap) / medium (batching) | Highest blast radius, and attacker-reachable |
| 3 | PERF-003 | P1 | small | Config-only. Interacts with PERF-001. |
| 4 | PERF-002 | P1 | small | Same pattern as PERF-001; reuse the batching helper |
| 5 | PERF-005 | P1 | small | Measure the plan first, then index |
| 6 | PERF-006 | P1 | small | Confirm SQL in the log first |

### P2 — Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 7 | PERF-007 | P2 | trivial | `quick-win`, but pair it with PERF-008 so query visibility is not lost |
| 8 | PERF-004 | P2 | depends on answer to Q1 | Decide after confirming the production deployment |

**If only one thing is done:** clamp `limit` on `GET /api/articles` and `/feed` (PERF-001). It is one line, and it removes the only path where a single request's cost is set by the caller.

---

## 9. Validation plan

### PERF-001
- **Baseline:** signed-in `GET /api/articles?limit=20` and `?limit=100` against a development server with seeded data. Count SQL lines per request in `log/development.log`. Not safe on production.
- **Change:** clamp `limit`. Preload tags. Batch favourites/follows for the page.
- **Measurement:** SQL statements per request.
- **Expectation:** before, the count grows by about 3 per added article (derived: 3 + 3N). After, a constant independent of `limit`. Latency change is unquantified until measured.
- **Falsifier:** if the count does not grow with `limit` today, the N+1 is not real.
- **Guard:** query-count integration test, and a `limit` clamp test.
- **Commands:**
  - `not-safe-on-production` `RAILS_ENV=development bin/rails s` then `curl -s "http://localhost:3000/api/articles?limit=50" >/dev/null`. Purpose: generate the per-request SQL log to count queries (anonymous, which exercises the tag-list query).

### PERF-003
- **Baseline:** Puma thread stats and occurrences of `ConnectionTimeoutError` in production logs. Safe on production.
- **Change:** `config/puma.rb` with `threads n, n` and `pool: n` from one env var.
- **Expectation:** no checkout timeouts at the same concurrency.
- **Falsifier:** production already runs 5 or fewer threads per process.
- **Guard:** boot-time pool ≥ threads assertion.

### PERF-005
- **Commands:**
  - `safe-on-production` `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY created_at DESC LIMIT 20 OFFSET 0;"`. Purpose: shows whether the list query sorts with a temp B-tree.
- **Expectation:** after indexing, the plan uses the `created_at` index with no temp B-tree.
- **Falsifier:** the plan already avoids the sort.

### PERF-006
- **Baseline:** development log of `GET /api/tags`. Then run `EXPLAIN QUERY PLAN` on the logged SQL (safe-on-production).
- **Expectation:** the replacement is a single `ORDER BY taggings_count LIMIT 20` on `tags`.
- **Falsifier:** the current SQL already avoids a GROUP BY over `taggings`.

### PERF-002
- **Baseline:** query count on an article with 50 seeded comments (dev, not-safe-on-production).
- **Expectation:** constant query count after preloading and batching.

### PERF-004
- **Commands:**
  - `safe-on-production` `sqlite3 db/production.sqlite3 "PRAGMA journal_mode;"`. Purpose: confirms rollback-journal versus WAL.
- **Falsifier:** another engine is used in production, or WAL is already enabled.

### PERF-007
- **Baseline / expectation:** SQL lines per request in production logs fall to zero after switching to `:info` (safe-on-production).

### Instrumentation gaps to close first

1. Per-request SQL query count and duration (PERF-008). This is the mechanism-level metric for PERF-001 and PERF-002.
2. Puma thread utilisation and AR pool wait time (PERF-003).
3. Row counts for `articles`, `taggings`, and `comments` (resolves question 3).

---

## 10. Machine-readable output

Emitted alongside this report as `rails-treatment-1.json`, conforming to `schemas/review.schema.json`.

## 11. Notes on this review

- Findings are classified by evidence grade. `Confirmed` requires a cited runtime artifact, and none exists.
- No runtime metric in this report was estimated. Query counts are labelled derivations from code. Puma's 16-thread default is a documented framework default, not a measurement.
- Recommendations state their trade-offs and their validation path.
