# Performance Review: rails-realworld-example-app (Conduit API)

**Date:** 2026-10-09
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec backend-performance-review/2.0), commit `a2ae4fff78b3337683cea988c023281dcb5551b9`

---

## 1. Decision summary

### Overall assessment

This is a small Rails 4.2 JSON API. Its handlers are thin and its point lookups are indexed. The material risks are on the article and comment **list** paths and in the **production datastore choice**. List serialisers run per-row queries for every article and comment (PERF-001), and the page size is whatever the caller asks for (PERF-002), so one request's query count is unbounded. Production is configured as a single-file SQLite database, so every write in the system shares one lock (PERF-003). Static evidence only: the repository has no metrics, traces, load tests or query plans, so nothing here is `Confirmed`. The review is **Medium confidence** overall. Every application file was read, but the workload is unknown and gem internals were not.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-002 (P1)** | Clamp `limit`/`offset` in `ArticlesController#index`/`#feed` and paginate comments | Small change, workload-independent. It removes the unbounded case that amplifies PERF-001. |
| 2 | **PERF-001 (P1)** | Batch `favorited?`/`following?` into per-page id sets, preload tags, `includes(:user)` on comments; add a query-count test (PERF-008) first | It is the largest per-request query multiplier on the hottest read paths. |
| 3 | **PERF-003 (P1)** | Confirm what production runs (question 1). If it is SQLite with concurrent writers, plan a move to a client-server RDBMS. | The answer decides whether this is irrelevant or the system's write ceiling. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Does production really run on `db/production.sqlite3`, and with how many processes/hosts? | PERF-003: withdrawn, or raised toward P0 | The production launch/deploy config, and whether `DATABASE_URL` is set |
| Puma workers × threads in production | Whether pool 5 queues requests; how much write contention PERF-003 sees | `config/puma.rb` / launch command; Puma stats |
| Row counts of `articles`, `taggings`, `comments` | PERF-004 and PERF-005 between P3 and P1; how large the comments part of PERF-001/002 gets | Row counts from a copy of the database |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | not-safe-on-production (local dev server) | `: > log/development.log && curl -s -H "Authorization: Token $JWT" "http://localhost:3000/api/articles?limit=20" > /dev/null && grep -c "SELECT" log/development.log` | If the count grows with `limit`, the N+1 is confirmed and the fix is justified |
| PERF-003 | safe-on-production | `sqlite3 db/production.sqlite3 "PRAGMA journal_mode;"` | Shows whether readers currently block on the writer |
| PERF-004 | safe-on-production | `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY articles.created_at DESC LIMIT 20 OFFSET 0;"` | A temp B-tree sort over a full scan justifies an index |

---

## 2. Scope and method

**Reviewed:** All of `app/` (9 controllers, 5 models, 15 Jbuilder views), `config/` (routes, database, environments, initializers), `db/schema.rb` and migrations, `Gemfile`/`Gemfile.lock`, `test/`, `bin/`, `README.md`.
**Not reviewed:** `config/secrets.yml` (secret file, presence noted only). Source of the third-party gems acts-as-taggable-on 3.5.0, acts_as_follower 0.2.1 and Devise 4.2.0 (not vendored), so their query behaviour is taken from their documented behaviour and flagged where a finding depends on it. Asset pipeline and HTML layout (not on the API path).

**Evidence available:** Uninstrumented. There are no metrics, tracing, APM, benchmarks, load tests, committed query plans, SLOs or dashboards. The only timing source is Rails' default per-request log line.

**Ranking method:** Structural signals only. No runtime data was available.

**Reference depth:** Ruby, Rails (REST) and SQLite all have deep references in this skill. The gem-level ORM behaviour (taggable, follower) is outside them.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every application, config, schema and test file except `config/secrets.yml`; no gem source |
| **Critical paths** | 18 / 18 (all routes under `/api`, including Devise routes) |
| **Shared resources** | 4 / 4 (SQLite database file and its writer lock, ActiveRecord pool of 5, Puma thread pool (config absent), production log file) |
| **Technology support** | Ruby: Deep · Rails/REST: Deep · SQLite: Deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not finding confidence. The code readings behind PERF-001 and PERF-002 are `High`. The review as a whole is `Medium` because workload, deployment topology and gem internals are unknown.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production database engine and process/host topology | No evidence exists | Deploy config; whether `DATABASE_URL` is set |
| Puma workers and threads | No evidence exists (no `config/puma.rb`, Procfile or Dockerfile) | Launch command; Puma stats endpoint |
| Table sizes and growth | No evidence exists (no seeds, no retention jobs) | Row counts on a copy of production |
| Exact SQL from `tag_list`, `tag_counts`, `following?` | Out of scope (gem source not in repo) | Development-log capture of the routes |
| Request rate, latency percentiles, SLO | No evidence exists | Request-duration metrics or a load-test scenario |

---

## 3. Architecture overview

A single Rails 4.2.6 monolith served by Puma 3.4.0 (`Gemfile.lock`). It exposes a JSON REST API under `/api` (`config/routes.rb`). Authentication is a stateless JWT decoded in `ApplicationController#authenticate_user`, so no DB lookup happens until `current_user` is needed, and then it is memoised. Responses are rendered by Jbuilder partials. There are no caches, brokers, background jobs, outbound service calls, containers or infrastructure code.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime | Ruby (MRI assumed) | not pinned | Deep | App process |
| Framework | Rails | 4.2.6 | Deep | Routing, ActiveRecord, Jbuilder |
| App server | Puma | 3.4.0 | (via Ruby/REST) | Request concurrency (config absent) |
| Datastore | SQLite (sqlite3 gem) | gem 1.3.11 | Deep | All environments, including production (`config/database.yml:23-25`) |
| Tagging | acts-as-taggable-on | 3.5.0 | Category only | Tags, tag cloud |
| Following | acts_as_follower | 0.2.1 | Category only | Follows, `following?` |
| Auth | Devise + jwt | 4.2.0 / 1.5.4 | Category only | Login and registration (bcrypt stretches 11) |

**Shared resources:** the SQLite database file (one writer at a time, busy timeout 5000 ms), the ActiveRecord connection pool (`pool: 5`), Puma's thread pool (size not stated), and `log/production.log` (debug level, synchronous writes).

---

## 4. Workload model

The workload interview could not be answered (nobody was available). The questions below were asked once and are recorded as unanswered. Workload-dependent findings are capped at `Medium` confidence.

**Known**

- Pool 5, SQLite busy timeout 5000 ms, shared by all environments. Source: `config/database.yml:7-10`
- Production database is `db/production.sqlite3`. Source: `config/database.yml:23-25`
- Article list default limit 20, **no maximum**, uncapped offset. Source: `app/controllers/articles_controller.rb:13,21`
- Comments index has no limit. Source: `app/controllers/comments_controller.rb:6`
- Production log level `:debug`. Source: `config/environments/production.rb:49`
- `articles` indexed on `slug` and `user_id` only. Source: `db/schema.rb:27-28`
- No retention or archival job for any table; no seeds. Source: `db/`, `lib/tasks/`
- API shape: 8 read routes, 10 write routes. Source: `config/routes.rb` and Devise

**Assumed**

- Articles, comments and taggings grow without bound. Affects PERF-004, PERF-005, PERF-002
- Production uses the SQLite config as written. Affects PERF-003
- `GET /api/articles` and `GET /api/tags` are the most frequently called routes (public home-page reads). Affects PERF-001, PERF-004, PERF-005

**Unknown**

- Production database engine and topology; Puma workers × threads; row counts; request and write rates; whether any client sends large `limit`.

**Derived**

- A signed-in `GET /api/articles` at the default limit 20 issues 1 (current_user) + 1 (count) + 1 (articles) + 1 (users preload) + 20 × 3 per-row lookups (tag_list, favorited?, following?) = **64 statements**, 60 of them proportional to page size. From: `articles_controller.rb:5-13`, `_article.json.jbuilder:1-3`, `_profile.json.jbuilder:3`. Only `favorited?` (`user.rb:36-38`) is provable from repository code; the other two rely on gem behaviour.
- A signed-in `GET /api/articles/:slug/comments` issues 2 + 2 × C statements for C comments, and C is uncapped. From: `comments_controller.rb:6,34`, `_comment.json.jbuilder:2`, `_profile.json.jbuilder:3`.
- The rows an article-list request touches do not depend on page size: COUNT over the filtered set plus ORDER BY on unindexed `created_at`. From: `articles_controller.rb:11-13`, `schema.rb:27-28`.

**Measured**

- None. No benchmark, trace, query plan or metrics export was supplied or exists in the repository. That is why nothing in this report is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Does production actually run on `db/production.sqlite3`, and how many processes/hosts share it? | severity / confidence / recommendation | A client-server DB withdraws PERF-003. Multi-process SQLite with concurrent writes moves it toward Critical/P0. |
| 2 | high | Puma workers × threads in production? | severity / recommendation | More than 5 threads per process queues requests for a connection on top of PERF-001/002. More processes multiply SQLite writer contention (PERF-003). |
| 3 | high | Row counts for `articles`, `taggings`, `comments`; the largest comment thread? | severity / confidence | Small tables drop PERF-004/005 to Low/P3. Large ones move PERF-004 to High/P1. |
| 4 | medium | Peak request rate on `/api/articles` and `/api/tags`, and write rate? | severity / confidence | Sets frequency for PERF-001/004/005 and contention for PERF-003. |
| 5 | medium | Is there a specific slow endpoint or incident behind this review? | recommendation | Would reorganise the review around confirming that symptom. |

---

## 5. Critical path analysis

Ranked by structural signals; no runtime data was available.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | yes | count + list + users preload + ~3 per article | No (client `limit`) | default log only | PERF-001, 002, 004 |
| 2 | `GET /api/articles/feed` | yes | count + list (followed authors) + ~3 per article | No (client `limit`) | default log only | PERF-001, 002, 004 |
| 3 | `GET /api/articles/:slug/comments` | yes | article + comments + 2 per comment | No (no limit) | default log only | PERF-001, 002 |
| 4 | `GET /api/tags` | yes | aggregation over taggings | Top 20 output; input unbounded | default log only | PERF-005 |
| 5 | Writes: articles, comments, favorites, follows, user update, register | yes | 1–4 statements each, under the SQLite writer lock | yes | default log only | PERF-003 |
| 6 | `DELETE /api/articles/:slug`, `DELETE /api/users` | yes | 2 per child row, cascading | No (grows with children) | default log only | PERF-007, COR-001 |
| 7 | `GET /api/articles/:slug`, `GET /api/profiles/:username`, `GET /api/user`, login | yes | constant point lookups | yes | default log only | no material candidate |

**Amplification points:** per-row lookups (PERF-001) × client-chosen `limit` or comment count (PERF-002). Each statement is also written to the debug log (PERF-006) and, on writes, runs under the single SQLite writer lock (PERF-003).

**Paths deliberately not analyzed in depth, and why:** login and registration bcrypt cost (intentional security work, rare); asset pipeline and HTML layout (not served on the API path).

---

## 6. Layer analysis

### 6.1 Application

Handlers are thin. `authenticate_user` decodes the JWT without a DB call, and `current_user` is memoised per request (`application_controller.rb:18-38`). `underscore_params!` deep-transforms the params on every request; that is bounded by request size, so it is noise. Per-row work sits in the Jbuilder partials, not in the controllers (PERF-001).

### 6.2 API

The list endpoints have no enforced maximum page size, and comments are not paginated (PERF-002). Offset pagination is used, so cost grows with page depth. Responses are camelCased by Jbuilder (`config/initializers/jbuilder.rb`); payloads are small per row.

### 6.3 Data access and datastore

- Per-row lookups in the partials: PERF-001.
- COUNT and an unindexed ORDER BY on every list request: PERF-004. Tag-cloud aggregation: PERF-005.
- Per-row cascading deletes: PERF-007. A broken association makes article deletion fail: COR-001.
- SQLite in production, with one writer and pool 5: PERF-003. The pool vs. thread arithmetic cannot be done because the Puma thread count is absent; it is recorded as a question, not a finding.
- Missing uniqueness constraints on `favorites` and `follows`: COR-002.

### 6.4 Infrastructure

There are no container, orchestration or deployment files. The deployment model is unknown.

### 6.5 Observability

The repository is uninstrumented (PERF-008). Production logs at debug level (PERF-006). That is a cost, but it is also currently the only source of per-query evidence.

---

## 7. Findings

### PERF-001 — List serialisers issue per-row queries (articles, feed, comments)

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/views/articles/_article.json.jbuilder:3` (also `app/views/profiles/_profile.json.jbuilder:3`, `app/views/comments/_comment.json.jbuilder:2`) |
| **Tags** | quick-win |

**Problem**
Each article in `GET /api/articles` and `/feed` costs separate queries for its tag list, the viewer's `favorited?` and the viewer's `following?` on its author. Each comment costs a query for its author (not preloaded) plus `following?`. Query count grows linearly with the rows returned.

**Performance principle**
Work that is the same for every row of a result should be issued once per result set, not once per row.

**Evidence**
- `_article.json.jbuilder:1-3` renders `tag_list` and `current_user.favorited?(article)` per article; `user.rb:37` implements `favorited?` as `favorites.find_by(...)`, one SELECT per call.
- `_profile.json.jbuilder:3` calls `current_user.following?(user)` per author (acts_as_follower runs a query).
- `articles_controller.rb:5` preloads only `:user`; tags are not preloaded and there is no `cached_tag_list` column (`db/schema.rb`).
- `comments_controller.rb:6` has no `includes(:user)`; `_comment.json.jbuilder:2` dereferences `comment.user` per row.
- Derivation: signed-in article list at default limit 20 = 64 statements, 60 of them scaling with page size. Comments = 2 + 2C.
- No runtime evidence exists; this is a code reading.

**Impact**
Position: critical path. Frequency: per item, on every list request. Growth: O(n) in rows returned. Blast radius: service (each statement holds one of 5 pooled connections and a Puma thread). The amplification factor is N, uncapped through PERF-002.

**Conditions**
Every list request, mostly signed-in users. At the default page of 20 the extra cost is fixed at about 60 statements; it is unbounded with a large `limit` or a long comment thread. Assumes the list routes are the hottest reads.

**Counter-evidence**
- `includes(:user)` already removes the author lookup on article lists (bounds impact).
- The default limit of 20 caps the per-row cost for default clients (bounds impact).
- `tag_list` and `following?` behaviour comes from non-vendored gem code; only `favorited?` is provable in-repo (lowers confidence, but `favorited?` alone keeps it High).
- Searched for memoisation, fragment caching or counter columns for these lookups: none found.

**Why this might not matter**
On in-process SQLite each lookup is an indexed point read with no network hop. Sixty of them may cost only a few milliseconds per page at current sizes. Their share of request time is unmeasured.

**Recommendation**
Before rendering, load the viewer's favorited article ids for the page in one query and the followed author ids in one query, and pass them to the partials as sets. Preload tags, or use acts-as-taggable-on's `cached_tag_list` column if 3.5 ignores preloaded taggings. Add `includes(:user)` to the comments index. This removes the work rather than hiding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Per-page id sets + preloads | **preferred** | Turns 3N lookups into a constant number of queries; responses stay exactly fresh; small, local change |
| Fragment-cache each article partial | | Viewer-dependent fields multiply cache keys per user; hides work; complex invalidation |
| Denormalise favorited/following onto the article | | Impossible: the values are relative to the viewer |

**Trade-offs**
Partials depend on locals prepared by the controller. The IN lists are bounded by page size only if PERF-002 is fixed. `cached_tag_list` adds a write on every tag change.

**Validation**
Baseline: count SQL statements in the development log for a signed-in `GET /api/articles?limit=20` and `?limit=5` (not-safe-on-production; run against a local server). Expectation: the count drops from 4 + 3N to a constant independent of N. The latency gain is unknown until measured. Falsifier: if the count is already constant in N, the finding is wrong. If the count drops but latency does not, the path was not query-bound. Guard: an integration test counting `sql.active_record` events at 2 vs 20 articles.

---

### PERF-002 — Page size is caller-controlled with no maximum; comments are unpaginated

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `app/controllers/articles_controller.rb:13` (also `:21`, `app/controllers/comments_controller.rb:6`) |
| **Tags** | quick-win |

**Problem**
`limit(params[:limit] || 20)` and `offset(params[:offset] || 0)` accept any value, and comments return the whole thread. One unauthenticated request can pull the whole articles table, and each row also pays PERF-001's per-row queries.

**Performance principle**
Every response needs an enforced upper bound. A default the caller can override is not a bound.

**Evidence**
`articles_controller.rb:13` (index) and `:21` (feed); `comments_controller.rb:6`; `articles_controller.rb:2` skips authentication on index/show. No runtime evidence.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in table size and comment count. Blast radius: service. A large request materialises every row in Ruby memory, holds a pooled connection and a Puma thread throughout, and, on SQLite in rollback-journal mode, holds a read lock that blocks writers.

**Conditions**
Any client that sends a large `limit` or deep `offset`, or an article with a long comment thread. One request is enough, so this holds at any traffic level.

**Counter-evidence**
Searched for rack-attack, a max-limit constant or params clamping: none found (`rack-cors` is declared but not configured). The default of 20 bounds default clients (bounds impact).

**Why this might not matter**
If only a first-party front end that always sends small limits uses the API, the unbounded case may never be exercised.

**Recommendation**
Clamp `limit` to a documented maximum and `offset` to a non-negative integer in one helper shared by index and feed, and paginate comments.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Server-side clamp | **preferred** | A few lines; no contract change for well-behaved clients |
| Keyset pagination on (created_at, id) | | Also fixes deep-offset cost, but changes the RealWorld offset contract; adopt only if deep paging is measured |
| Proxy rate limiting | | Bounds frequency, not size |

**Trade-offs**
Callers relying on large limits must page. Paginating comments changes that response contract.

**Validation**
`curl -s "http://localhost:3000/api/articles?limit=100000" | python -c "import json,sys; print(len(json.load(sys.stdin)['articles']))"` against a local server (not-safe-on-production). Expectation: after the clamp, the row count and statement count stay at the cap. Falsifier: the uncapped request already returns at most the cap. Guard: a controller test with `limit=10000`.

---

### PERF-003 — Production configured on single-file SQLite: one writer for the whole system

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `config/database.yml:23` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
Production points at `db/production.sqlite3`. Every write (register, article create/update/delete, comment, favorite plus its counter-cache update, follow, user update) takes the single database-wide write lock. Concurrent writers wait up to the 5000 ms busy timeout and then fail with `SQLite3::BusyException`. The file also ties the app to one host.

**Performance principle**
A single exclusive resource on the write path caps write throughput at one writer, and the queue in front of it grows sharply as utilisation approaches saturation.

**Evidence**
`config/database.yml:23-25` (production → sqlite3, `db/production.sqlite3`), `:9-10` (pool 5, timeout 5000); write paths in all mutating controllers; `app/models/favorite.rb:3` adds a counter-cache UPDATE to each favorite write. No `journal_mode`/WAL setting anywhere. No runtime evidence.

**Impact**
Position: critical path (writes). Frequency: per write request. Growth: unknown (depends on write concurrency). Blast radius: system-wide, because one lock is shared by all writers and, in rollback-journal mode, blocks readers during commit.

**Conditions**
Assumes production runs this config without a `DATABASE_URL` override, with several Puma threads or processes writing at once. At a low write rate, contention is rare.

**Counter-evidence**
No `DATABASE_URL`, pg/mysql2 gem or deploy file was found, but an environment variable outside the repo could override the config (lowers confidence → Medium). Write transactions are short single-row statements with no I/O inside (bounds impact).

**Why this might not matter**
A demo deployment with a handful of users, or a production `DATABASE_URL` pointing to a client-server database, makes this irrelevant. SQLite handles low write rates well.

**Recommendation**
Answer question 1 first. If production is SQLite and concurrent writers or multiple hosts are expected, move to a client-server RDBMS; that is the only change that adds write concurrency and horizontal scale. If SQLite stays on purpose for a single host: enable WAL, run one process, and do not raise the pool, because a larger pool adds no write throughput on SQLite.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Migrate to a client-server RDBMS | **preferred** | Removes the single-writer ceiling and the one-host limit; the schema has nothing SQLite-specific |
| Keep SQLite with WAL on one host | | Readers no longer block on the writer, but writes stay serialised |
| Raise pool or busy timeout | | Adds no write throughput; lengthens the queue; can raise BusyException rates |

**Trade-offs**
A server database adds operations work, connection-limit arithmetic, and network round trips that make PERF-001's per-row queries more expensive, so fix PERF-001 first. WAL adds a `-wal` file that needs checkpointing.

**Validation**
`sqlite3 db/production.sqlite3 "PRAGMA journal_mode;"` (safe-on-production) and `grep -c "SQLite3::BusyException" log/production.log` (safe-on-production). Expectation: under concurrent favorite/comment writes, write latency rises with concurrency and BusyException appears at the 5 s timeout. Falsifier: production is not SQLite, or a concurrent-write test at the real write rate shows flat latency and no BusyException. Guard: an alert on BusyException or on lock waits.

---

### PERF-004 — Article list counts and sorts the whole filtered set on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `app/controllers/articles_controller.rb:11` (also `:13`, `:19`, `:21`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
Each list and feed request runs `COUNT` over every matching article and `ORDER BY created_at DESC` with no `created_at` index. Work grows with the table, not the page.

**Performance principle**
A paged read should touch work proportional to the page, not the collection.

**Evidence**
`articles_controller.rb:11,19` (count), `:13,21` (order); `db/schema.rb:27-28` (indexes only on slug, user_id); no retention anywhere. No query plan exists.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in articles. Blast radius: endpoint.

**Conditions**
Assumes articles grow without bound and the list is the hottest route. Negligible at a few thousand rows. It moves to High severity once the table is large enough for the scan and sort to show in request time.

**Counter-evidence**
On the feed, the `user_id` index narrows rows to followed authors before the sort (bounds impact). No EXPLAIN output exists, so the planner could behave differently (lowers confidence). `articles_count` is part of the API contract, so the count cannot simply be dropped.

**Why this might not matter**
At demo scale, a scan and sort over hundreds of rows on in-process SQLite is negligible.

**Recommendation**
Run EXPLAIN QUERY PLAN and get row counts first. If the plan shows a full scan with a temp B-tree, add an index on `articles(created_at)` (and `(user_id, created_at)` for the feed).

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Index created_at after EXPLAIN confirms the sort | **preferred** | Turns the list into an index range read of the page size |
| Cache articles_count for unfiltered lists | | Invalidation on every create/delete; helps only one case |
| Do nothing until row counts justify it | | Valid if the table is small |

**Trade-offs**
Each index adds write and storage cost on every article insert/update. COUNT remains O(filtered rows).

**Validation**
`sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY articles.created_at DESC LIMIT 20 OFFSET 0;"` (safe-on-production). Row counts: `sqlite3 db/production.sqlite3 "SELECT (SELECT COUNT(*) FROM articles), (SELECT COUNT(*) FROM taggings), (SELECT COUNT(*) FROM comments);"` (not-safe-on-production for a large file; run on a copy). Expectation: a temp B-tree today and none after the index. Falsifier: the plan already avoids the sort, or tables are tiny, which downgrades this to Low.

---

### PERF-005 — Tag cloud re-aggregates all taggings per request

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `app/controllers/tags_controller.rb:3` |
| **Tags** | scalability-risk, quick-win |

**Problem**
`Article.tag_counts.most_used` computes per-tag counts by joining and grouping Article taggings on every public `GET /api/tags`, then keeps the top tags. Meanwhile `tags.taggings_count` is already a maintained counter.

**Performance principle**
A value that changes only on writes should not be recomputed from full history on every read.

**Evidence**
`tags_controller.rb:3`; `db/schema.rb:79` (`taggings_count`); `config/routes.rb:19` (public route). The exact SQL comes from gem code that is not in the repo.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in taggings. Blast radius: endpoint.

**Conditions**
Assumes taggings grow with articles and the route is called on most page loads.

**Counter-evidence**
Gem source not vendored, so the aggregation shape is unconfirmed (lowers confidence). The taggings composite index supports the join (bounds impact).

**Why this might not matter**
With few taggings the aggregate is trivial.

**Recommendation**
Confirm the SQL in the development log. If it aggregates, read the top tags from the `taggings_count` counter (`ActsAsTaggableOn::Tag.most_used`, filtered to counts > 0). Article is the only taggable model, so the counter is accurate.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Read from the maintained counter | **preferred** | Removes the aggregation entirely |
| Short-TTL cache | | Hides the work and adds staleness |

**Trade-offs**
Relies on the gem keeping the counter correct. It stops being Article-specific if another taggable model is added.

**Validation**
`: > log/development.log && curl -s http://localhost:3000/api/tags > /dev/null && grep -E "SELECT.*tags" log/development.log` (not-safe-on-production; local). Expectation: a GROUP BY over taggings today; an ORDER BY over tags only after the change. Falsifier: the logged SQL already reads only `tags.taggings_count`.

---

### PERF-006 — Production logs every SQL statement at debug level

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `config/environments/production.rb:49` |
| **Tags** | quick-win |

**Problem**
`config.log_level = :debug` makes every request format and write each SQL statement synchronously to `log/production.log`, and the file has no rotation (`config.logger` is commented out at `:55`). PERF-001 multiplies the line count.

**Performance principle**
Verbose synchronous logging on the hot path is per-request disk I/O. Diagnostic volume should match normal-operation needs.

**Evidence**
`config/environments/production.rb:49,55`. No runtime evidence.

**Impact**
Position: critical path. Frequency: per request. Growth: O(statements per request). Blast radius: service (one log file shared by all threads).

**Conditions**
Every production request. The share of request time is unmeasured. Assumes no external rotation or shipping outside the repo.

**Counter-evidence**
No log-level override or logger replacement was found (no effect). Each write is a small buffered append (bounds impact).

**Why this might not matter**
Appending to a local file is cheap, and the SQL lines are currently the only query-level evidence anyone has.

**Recommendation**
Set the level to `:info` (this keeps the per-request "Completed in X ms (ActiveRecord: Y ms)" line) and add rotation. Replace SQL-in-logs with PERF-008's instrumentation.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| `:info` + rotation | **preferred** | One line; keeps request timing |
| Keep `:debug`, ship stdout asynchronously | | Moves disk growth off the host, but formatting and writing still happen on the request thread |

**Trade-offs**
Production loses SQL lines, the only crude query evidence today.

**Validation**
`ls -l log/production.log` (safe-on-production) for growth. Expectation: log lines per request fall to a few request lines. Falsifier: no change in request time and disk is not a constraint, so the change is hygiene only.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-007 | Low | High | P3 | `app/models/article.rb:3` | `dependent: :destroy` deletes favorites and comments one row at a time (plus a counter decrement per favorite), cascading through users. Rare, but holds the SQLite write lock for the whole cascade. Use `delete_all` after fixing COR-001. |
| PERF-008 | Low | High | P3 | `Gemfile` | No metrics, tracing, query-count tests or N+1 detector. Sequence it **first**: add a query-count test for the list routes before fixing PERF-001. |

### Considered and not reported

| Candidate | Path / resource | Evidence checked | Why not reported | Revisit when |
|:--|:--|:--|:--|:--|
| Pool 5 smaller than Puma's thread count | AR pool / Puma | `config/database.yml:9`, `Gemfile.lock` (puma 3.4.0), no `config/puma.rb`/Procfile | The thread count is not stated anywhere, so it was turned into question 2 instead of a finding | Production threads per worker exceed 5 |
| bcrypt stretches 11 on login/register | Login, registration | `config/initializers/devise.rb`, `sessions_controller.rb` | Intentional security cost on rare paths; JWT requests do not pay it | A profile shows bcrypt dominating worker CPU |
| Feed author filter via `following_users` | `GET /api/articles/feed` | `articles_controller.rb:17`, `user.rb` | Expected to be a subquery, not an in-memory list | The development log shows followed users loaded in full first |

### Adjacent findings — outside performance scope

### COR-001 — Bogus `has_many :articles` on Article breaks article (and user) deletion

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `app/models/article.rb:15` |

**Problem** `Article` declares `has_many :articles, dependent: :destroy`, but `articles` has no `article_id` column, so destroying an article queries a column that does not exist and raises.
**Evidence** `app/models/article.rb:15`; `db/schema.rb:16-25` (no `article_id`).
**Impact** `DELETE /api/articles/:slug` cannot succeed, and user deletion cascades into the same error. A core API feature is broken, hence Medium.
**Recommendation** Remove the line and add a request test for article deletion.
**Trade-offs** None.
**Validation** A request test deleting an owned article returns 200 and the row is gone.
**Would need** A correctness-focused request/integration test suite covering every route.

### COR-002 — No uniqueness constraint on favorites/follows

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Low |
| **Location** | `db/schema.rb:48` |

**Problem** `favorite` uses `find_or_create_by` (`user.rb:27`) without a unique `(user_id, article_id)` index, so concurrent double-submits can create duplicates and double-increment `favorites_count`. The same applies to follows.
**Evidence** `db/schema.rb:48-49, 61-62`; `app/models/user.rb:27`.
**Impact** Wrong counts and duplicate rows under a race. It is visible to users but not dangerous, hence Low.
**Recommendation** Add unique composite indexes and rescue `RecordNotUnique`.
**Trade-offs** One more index per write.
**Validation** A concurrent double-favorite test yields one row.
**Would need** A data-integrity review with concurrency tests.

### MAINT-001 — End-of-life framework and 2016-era dependencies

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `Gemfile:5` |

**Problem** Rails 4.2.6, jwt 1.5.4, devise 4.2.0 and sqlite3 1.3.11 are pinned (`Gemfile`, `Gemfile.lock`) and are long past upstream support.
**Evidence** `Gemfile:5`; `Gemfile.lock:57,76,97,144`.
**Impact** These versions no longer get security fixes, and the app handles authentication and passwords, hence High.
**Recommendation** Plan an upgrade (the README points to a rails-5.1 branch) and run a dependency audit.
**Trade-offs** Upgrade effort and API changes across major Rails versions.
**Validation** A dependency audit reports no known advisories after the upgrade.
**Would need** A dependency vulnerability audit (for example bundler-audit) and a security review of the JWT handling.

---

## 8. Prioritized action plan

P1: PERF-001, PERF-002, PERF-003 · P2: PERF-004, PERF-005, PERF-006 · P3: PERF-007, PERF-008. No P0.

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-008 | P3 | small | Sequenced first despite P3: a query-count test is how PERF-001/002 are proven and guarded |
| 2 | PERF-002 | P1 | small | Removes the unbounded case; workload-independent |
| 3 | PERF-001 | P1 | small–medium | Largest per-request query multiplier |
| 4 | PERF-003 | P1 | answer question 1 first; migration is large | Decides the system's write ceiling |
| 5 | PERF-006 | P2 | one line | Cheap; do it once PERF-008 gives a replacement signal |
| 6 | PERF-004 | P2 | small, after EXPLAIN | Only if row counts justify it |
| 7 | PERF-005 | P2 | small | Confirm the SQL first |
| 8 | PERF-007 | P3 | small, after COR-001 | Rare path |

**If only one thing is done:** clamp the list page size (PERF-002). It is a few lines, holds at any traffic level, and caps the cost of every per-row query in PERF-001.

---

## 9. Validation plan

### PERF-001
- **Baseline:** statement count for a signed-in `GET /api/articles?limit=20` and `?limit=5` from the development log — local only.
- **Change:** per-page id sets for favorites and follows; preload tags; `includes(:user)` on comments.
- **Measurement:** statements per request.
- **Expectation:** 4 + 3N → constant. The latency gain is unquantified.
- **Falsifier:** the count is already constant in N; or the count drops but latency does not move.
- **Guard:** an integration test asserting an equal statement count for 2 and 20 articles.
- **Commands:**
  - `not-safe-on-production` `: > log/development.log && curl -s -H "Authorization: Token $JWT" "http://localhost:3000/api/articles?limit=20" > /dev/null && grep -c "SELECT" log/development.log` — count statements for one request

### PERF-002
- **Baseline / measurement:** rows returned for `limit=100000`.
- **Expectation:** capped after the change.
- **Falsifier:** already capped.
- **Guard:** a controller test.
- **Commands:**
  - `not-safe-on-production` `curl -s "http://localhost:3000/api/articles?limit=100000" | python -c "import json,sys; print(len(json.load(sys.stdin)['articles']))"` — show that the limit is honoured uncapped

### PERF-003
- **Baseline:** journal mode; BusyException count in the production log.
- **Expectation:** under concurrent writes, latency rises and BusyException appears.
- **Falsifier:** not SQLite in production, or flat write latency at the real write rate.
- **Guard:** an alert on BusyException / lock waits.
- **Commands:**
  - `safe-on-production` `sqlite3 db/production.sqlite3 "PRAGMA journal_mode;"` — rollback journal vs WAL
  - `safe-on-production` `grep -c "SQLite3::BusyException" log/production.log` — writer-lock timeouts already seen

### PERF-004
- **Commands:**
  - `safe-on-production` `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY articles.created_at DESC LIMIT 20 OFFSET 0;"` — full scan + temp B-tree?
  - `not-safe-on-production` `sqlite3 db/production.sqlite3 "SELECT (SELECT COUNT(*) FROM articles), (SELECT COUNT(*) FROM taggings), (SELECT COUNT(*) FROM comments);"` — row counts; run on a copy
- **Expectation:** no temp B-tree after the index. **Falsifier:** already index-ordered, or tables tiny.

### PERF-005
- **Commands:**
  - `not-safe-on-production` `: > log/development.log && curl -s http://localhost:3000/api/tags > /dev/null && grep -E "SELECT.*tags" log/development.log` — capture the tag-cloud SQL locally
- **Expectation:** GROUP BY over taggings → ORDER BY over tags. **Falsifier:** already reads the counter.

### PERF-006
- **Commands:**
  - `safe-on-production` `ls -l log/production.log` — unrotated log size
- **Expectation:** log lines per request fall to request-level lines only.

### Instrumentation gaps to close first

1. A query-count integration test for `GET /api/articles`, `/feed` and `/comments` (subscribe to `sql.active_record`).
2. A development-only N+1 detector.
3. Per-route latency percentiles exported from production. The default Rails log line gives one sample per request but no percentiles.

---

## 10. Machine-readable output

The machine-readable form of this review is in `rails-guided-2.json`, next to this file. It conforms to the skill's `schemas/review.schema.json`, and `scripts/validate_review.py` reports it as a valid review (8 findings). If the two disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade; none is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated. The statement counts in §4 are labelled derivations, with their inputs shown.
- Recommendations state their trade-offs and validation paths. Commands are labelled for production safety. Commands aimed at `localhost` assume a local development server with seeded data.
