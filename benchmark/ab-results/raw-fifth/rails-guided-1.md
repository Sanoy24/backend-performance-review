# Performance Review: rails-realworld-example-app (Conduit API)

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0
**Commit:** `a2ae4fff78b3337683cea988c023281dcb5551b9`

---

## 1. Decision summary

### Overall assessment

This is a small Rails 4.2 JSON API with a clean request flow. Its performance risk comes from three places. The list endpoints make database queries per row, and clients choose the page size with no upper limit. The list query sorts the whole table on every request because no index covers it. Production is configured on a single SQLite file that allows only one writer at a time. No runtime evidence exists: the repo has no metrics, traces, or query-count tests. Every finding therefore comes from reading the code and the schema, and nothing here is `Confirmed`. Review confidence is **Medium**. I read every application file. Gem internals and the production deployment were not visible. Nobody answered the workload questions, so the ranking of PERF-001 through PERF-005 would change with real traffic and row counts (see §4).

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Load favorite, follow, and tag data for the whole page in a fixed number of queries, and cap `limit` on the server | Query count grows with a page size the client controls. This is the highest-confidence item on the busiest route |
| 2 | **PERF-002 (P1)** | Add `includes(:user)` to the comments list, add a page cap, and load follow state once per page | Adding `includes(:user)` is a one-line quick win. The list has no upper bound and makes one query per comment |
| 3 | **PERF-004 (P1)** | Confirm which database production actually uses. If it is SQLite, measure busy/locked errors, then choose between WAL mode and a client-server engine | This decides the blast radius of every other data-access finding |

PERF-007 (P3, adding instrumentation) is cheap and should be done before any of these, so that each change can be checked. Doing it first does not change its priority.

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Does production run on the SQLite file in `config/database.yml`, and on how many processes or hosts? | Whether PERF-004 is real, and whether PERF-001/002/003/006 affect the whole system or one endpoint | The production start command and environment, or `PRAGMA journal_mode` run on the production file |
| What `limit` values do clients send, and what share of list traffic is signed in? | PERF-001 stays P1 or drops toward P2 | Request logs with query strings |
| How many rows are in `articles`, `taggings`, and `comments`, and what is the most comments on one article? | The severity of PERF-002, PERF-003, and PERF-005 | The row-count command in §9 |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-004 | `safe-on-production` | `sqlite3 db/production.sqlite3 'PRAGMA journal_mode;'` | Whether readers are blocked during writes; whether production uses this file at all |
| PERF-003 | `safe-on-production` | `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY articles.created_at DESC LIMIT 20 OFFSET 0;"` | A `USE TEMP B-TREE FOR ORDER BY` line in the plan confirms the whole-table sort |
| PERF-001 | `not-safe-on-production` | `RAILS_ENV=development bin/rails runner 'n=0; ActiveSupport::Notifications.subscribe("sql.active_record"){ n+=1 }; app=ActionDispatch::Integration::Session.new(Rails.application); app.get "/api/articles?limit=20"; puts n'` | Whether query count grows with `limit` |

---

## 2. Scope and method

**Reviewed:**
- All controllers, models, and Jbuilder views
- `config/routes.rb`, `db/schema.rb`, and all migrations
- `config/database.yml`, `config/environments/*.rb`, and all initializers
- `Gemfile`, `Gemfile.lock`, `config.ru`, `README.md`, and the test directory

**Not reviewed:**
- The source of `acts-as-taggable-on` 3.5.0, `acts_as_follower` 0.2.1, and `devise` 4.2.0. They are not vendored in the repo, so claims about the queries they issue are labelled gem-dependent.
- `config/secrets.yml` was not opened (it is a secret file; only its presence is noted).
- The asset pipeline and front-end assets, which are not part of the API backend.

**Evidence available:** Uninstrumented. There are no metrics, traces, APM, benchmarks, load tests, query plans, or SLOs. The tests are empty model stubs plus fixtures. The only runtime signal would be Rails' default request log, which is at `:debug` level in production.

**Ranking method:** Structural signals only. The ranking is inference, not measurement.

**Reference depth:**
- Ruby, REST/Rails, and SQLite all have `deep` references.
- Gem-specific query behavior (taggable, follower) has no reference. Claims about it are marked as unverified.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every application, config, schema, and manifest file was read. Gem sources and secrets were not |
| **Critical paths** | 10 / 10 |
| **Shared resources** | 4 / 4 (SQLite database file, ActiveRecord pool, Puma thread pool, process log I/O) |
| **Technology support** | Ruby: deep. Rails/REST: deep. SQLite: deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not the same as finding confidence. PERF-001's code reading is High confidence. The review as a whole is Medium because the deployment, traffic, and gem internals were not visible.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| The datastore production actually uses, and the process/host count | No evidence exists: there are no deployment files | The production environment, `DATABASE_URL`, and the start command |
| Puma workers and threads compared with the pool of 5 | No evidence exists: there is no `puma.rb`, Procfile, or Dockerfile | The production Puma command, or Puma's stats |
| Request rates, latency percentiles, and row counts | No evidence exists | PERF-007 instrumentation and the row-count command in §9 |
| The exact SQL issued by the taggable and follower gems | Not examined: the gem source is not in the repo | Gem source at the locked versions, or a SQL log for one request |

---

## 3. Architecture overview

This is a single Rails application (module `Conduit`) serving a JSON API under `/api`, with JWT auth parsed in `ApplicationController#authenticate_user`. A request goes from a controller through ActiveRecord to SQLite, and then through a Jbuilder partial. It makes no outbound service calls. There is no cache, no message broker, no background jobs, and no container or infrastructure configuration.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime / framework | Ruby, Rails | Rails 4.2.6 (Ruby version not pinned) | deep | API server |
| App server | Puma | 3.4.0 (no config in repo) | deep (ruby) | HTTP worker threads |
| Datastore | SQLite via sqlite3 gem | 1.3.11 | deep | Primary database, `db/production.sqlite3` |
| Tagging | acts-as-taggable-on | 3.5.0 | none | Tags and taggings tables |
| Follows | acts_as_follower | 0.2.1 | none | Polymorphic follows table |
| Auth | devise 4.2.0, jwt 1.5.4 | — | — | Registration and login; JWT bearer tokens |
| Serialization | jbuilder | 2.5.0 | — | JSON views with camelCase keys |

**Shared resources:**
- The single SQLite database file, which has a database-wide writer lock
- The ActiveRecord pool (`pool: 5`, `timeout: 5000`) per process
- Puma's thread pool, whose size is not stated in the repo
- The production log file, written at debug level

---

## 4. Workload model

**Known**
- Production datastore is SQLite `db/production.sqlite3`, with pool 5 and busy timeout 5000 ms. Source: `config/database.yml:7-10,23-25`.
- The app server is puma 3.4.0 with no Puma config, Procfile, or deployment manifest. Source: `Gemfile:31`, `Gemfile.lock:92`.
- The article list and feed take `limit`/`offset` from the client (default 20/0) with no maximum. Source: `app/controllers/articles_controller.rb:13,21`.
- The comments list has no limit. Source: `app/controllers/comments_controller.rb:6`.
- The route surface has 7 GET actions and 12 mutating actions. Source: `config/routes.rb`.
- No retention, archival, or scheduled jobs exist (`lib/tasks` is empty). Every table is append-only apart from user-initiated deletes.
- There is no index on `articles.created_at`, and no unique index on `favorites(user_id, article_id)` or on follows. Source: `db/schema.rb:27-62`.
- Production log level is `:debug`. Source: `config/environments/production.rb:49`.

**Assumed**
- `GET /api/articles` and `GET /api/tags` are the hottest routes, because a RealWorld home page calls both. Affects: PERF-001, PERF-003, PERF-005.
- Production uses the SQLite config as written. Affects: PERF-004, and the blast radius of PERF-001/002/003/006.
- `articles`, `comments`, and `taggings` grow without bound. Affects: PERF-002, PERF-003, PERF-005.

**Unknown**
- Whether production really uses SQLite, and how many processes and hosts run
- Puma workers and threads
- Request rates on list and write endpoints
- Row counts, and the most comments on a single article
- The `limit` values clients send, and the share of signed-in traffic
- Latency percentiles on any route

**Derived**
- Per-row queries on a signed-in article page = k × (favorited? + following? + tag_list), where k is the requested `limit`. At the default k = 20 that is 60 per-row queries. They come on top of the count query, the page query, the author preload, and the `current_user` lookup.
  - Inputs: `articles_controller.rb:13`, `_article.json.jbuilder:1,3`, `_profile.json.jbuilder:3`.
  - Only favorited? is certain from the repo (`user.rb:37`). The following? and tag_list queries depend on gem internals.
- Comments list queries = 1 + c (author) + c (following?, when signed in), where c is the article's total comment count, which has no bound. Inputs: `comments_controller.rb:6`, `_comment.json.jbuilder:2`, `_profile.json.jbuilder:3`.
- Every `GET /api/articles` reads all candidate rows twice, once for COUNT and once to sort by `created_at`. Inputs: `articles_controller.rb:11,13`, `db/schema.rb:16-28`.

**Measured**
- None. No benchmark, trace, query plan, or log was supplied or committed. This is why no finding is `Confirmed`.

### Questions that would change the ranking

Nobody was available to answer these. They are the questions I would have asked. The review went ahead with the assumptions above, and workload-dependent findings are capped at Medium confidence.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is production really on the SQLite file in `config/database.yml` (or does `DATABASE_URL` override it), and how many app processes and hosts run against it? | severity / confidence / recommendation | If production uses a client-server engine, PERF-004 is withdrawn and the system-wide blast radius of PERF-001/002/003/006 shrinks. If multiple hosts share one file, PERF-004 becomes a correctness emergency |
| 2 | high | What Puma worker and thread counts does production use? | severity / recommendation | If threads exceed 5 per process, a pool-wait finding appears and amplifies PERF-001/002. If not, there is no pool finding |
| 3 | high | What `limit` values do clients send, and what share of list traffic is signed in? | severity | If clients use 20 or less and traffic is mostly anonymous, PERF-001 drops toward Medium/P2 |
| 4 | high | How many rows are in `articles`, `taggings`, and `comments`, and what is the most comments on one article? | severity / confidence | Small tables move PERF-003 and PERF-005 to P3. If no article has more than a few tens of comments, PERF-002 moves to P2 |
| 5 | high | What is the peak request rate (order of magnitude) on list and write endpoints? | severity / confidence | If concurrent writes are rare, PERF-004 stays a scalability risk rather than a current bottleneck |
| 6 | medium | Did a specific slow endpoint or incident prompt this review? | recommendation | The review would be reorganized around confirming or refuting that symptom |

---

## 5. Critical path analysis

Ranked by structural signals only.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | yes | COUNT + page + author preload + about 3 per article (signed in) or about 1 (anonymous) | No: client `limit` is uncapped, and the sort covers the whole table | no | PERF-001, PERF-003 |
| 2 | `GET /api/articles/feed` | yes | Same shape, filtered by a followed-users subquery | No: client `limit` is uncapped | no | PERF-001, PERF-003 (narrowed by the `user_id` index) |
| 3 | `GET /api/articles/:slug/comments` | yes | 1 + 1 per comment + 1 per comment when signed in | No: no limit | no | PERF-002 |
| 4 | `GET /api/tags` | yes | 1 grouped aggregate over taggings | Output is limited; input is not | no | PERF-005 |
| 5 | `DELETE /api/articles/:slug` | yes | Per-row destroy of favorites and comments, with counter-cache updates | No (rare) | no | PERF-006, COR-001 |
| 6 | `POST`/`DELETE /api/articles/:slug/favorite` | yes | find_or_create or destroy_all, counter update, reload, then the article partial | yes | no | PERF-009, COR-002 |
| 7 | `POST`/`PUT /api/articles` | yes | Slug uniqueness check, insert or update, per-tag writes | Tag list is uncapped | no | PERF-010 |
| 8 | `GET /api/articles/:slug` | yes | Constant: 1 article + about 4 lookups | yes | no | No material candidate |
| 9 | `POST`/`DELETE /api/articles/:slug/comments` | yes | Article lookup + insert or delete | yes | no | No material candidate |
| 10 | Auth, profile, follow, user routes | yes | Constant point lookups on unique indexes, plus bcrypt on login | yes | no | No material candidate (bcrypt cost is intentional) |

**Amplification points:**
- `/api/articles` and `/feed`: per-article queries multiplied by a `limit` the client controls
- `/comments`: per-comment queries multiplied by the article's total comment count
- Article destroy: per-row deletes multiplied by the number of dependents

**Paths not analyzed in depth, and why:** Asset-pipeline and HTML layout routes. The app is used as a JSON API, and those routes carry no data access.

---

## 6. Layer analysis

### 6.1 Application
- The per-request filters (`underscore_params!` with deep key transform, and JWT decode) are bounded work on small payloads. No material candidate.
- `current_user` is memoized, so it costs one lookup per request.
- No blocking outbound I/O and no external calls exist.

### 6.2 API
- List endpoints take page size from the client with no server-side ceiling (PERF-001), or have no page at all (PERF-002).
- Offset pagination makes deep pages cost more (PERF-003).

### 6.3 Data access and datastore
- **ActiveRecord.** Lazy associations are reached from Jbuilder partials during rendering. Only `includes(:user)` is used, and only on the article lists (PERF-001, PERF-002).
- **Indexes.** No index covers the main list sort (PERF-003). The tag cloud aggregates taggings even though a counter column exists (PERF-005). Cascading deletes run row by row (PERF-006).
- **SQLite.** The database allows a single writer for the whole file. It runs in default journal mode, because no `PRAGMA` exists in the repo, with a 5000 ms busy timeout (PERF-004).
- **Pool.** The pool of 5 is consistent with SQLite's single writer for writes. Whether it matches Puma's thread count cannot be determined (question #2).

### 6.4 Observability
- Nothing is instrumented (PERF-007).
- Debug-level production logging adds per-statement I/O (PERF-008).

---

## 7. Findings

### PERF-001: Article list and feed make per-row queries, multiplied by an uncapped client page size

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/views/articles/_article.json.jbuilder:3` (with `app/controllers/articles_controller.rb:5,13,21`) |
| **Tags** | scalability-risk |

**Problem**
`GET /api/articles` and `/feed` render each article through partials that run their own queries for favorited?, the author's following?, and tag_list. The page size comes from the client with no maximum, so the number of queries per request grows linearly with a number the caller chooses.

**Performance principle**
Work per response should not grow as one round trip per returned row. Lookups for each row should be batched into a fixed number of set-based reads, and any multiplier the caller controls should be bounded.

**Evidence**
- `articles_controller.rb:5,17`: only `includes(:user)` is preloaded.
- `articles_controller.rb:13,21`: `.offset(params[:offset] || 0).limit(params[:limit] || 20)`, with no maximum.
- `_article.json.jbuilder:3`: `current_user.favorited?(article)` runs for each row.
- `user.rb:37`: `favorited?` is `favorites.find_by(article_id: article.id).present?`, which is one SELECT per call.
- `_profile.json.jbuilder:3`: `current_user.following?(user)` runs for each author. It is an acts_as_follower method and is not preloaded.
- `_article.json.jbuilder:1`: `tag_list` is rendered for each article, and taggings are not preloaded.
- Derived: about 3k per-row queries on a signed-in page of k articles, which is 60 at the default k = 20. The favorited? query is certain. The following? and tag_list queries are gem-dependent.
- **No runtime evidence:** the repo has no SQL capture, APM, or query-count test.

**Impact**
- **Position:** critical path. These GETs block the client.
- **Frequency:** per item, on every list request.
- **Growth:** O(n) in the client-chosen `limit`.
- **Blast radius:** system-wide, because each query uses the shared pool and the shared SQLite file.
- Not rated Critical: the default of 20 bounds typical requests, and the growth is linear, not superlinear.

**Conditions**
This matters on every signed-in list or feed request (the feed always requires sign-in), and on anonymous requests through tag_list. The cost scales with the `limit` the client sends, which nothing caps. The system-wide blast radius assumes production uses the configured SQLite file and its 5-connection pool.

**Counter-evidence**
- I searched `app/` for preloading of favorites, follows, or taggings, and for memoization of these lookups. Nothing was found (no-effect).
- The default limit of 20 bounds clients that use the default (bounds-impact). This is why the finding is High rather than Critical.
- The following? and tag_list queries depend on gem code that is not in the repo. The certain per-row favorited? query is enough on its own to establish the N+1 shape (no-effect).

**Why this might not matter**
SQLite runs in-process, with no network round trip. If clients always send `limit=20` and the indexed lookups stay in the page cache, the 60 extra queries may cost little absolute time.

**Recommendation**
1. Before rendering, load the current user's favorited article ids for the page in one query, and the followed author ids in one query.
2. Preload taggings with the page.
3. Have the partials read from those sets instead of querying.
4. Add a server-side maximum for `limit` and sanitize `offset`.

This removes the per-row work rather than hiding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Batch-load the favorite and follow sets, preload tags, and cap `limit` | **preferred** | Query count per page becomes constant, and the remaining linear term is bounded |
| B: Only cap `limit` | | Bounds the worst case but keeps about 3 queries per row |
| C: Cache the rendered JSON | | `favorited` and `following` differ per viewer, so the cache would be per user and need invalidation on every favorite and follow. It hides the queries rather than removing them |

**Trade-offs**
The partials gain a dependency on precomputed data. The show and favorite endpoints reuse the same partial, so they need a fallback. A cap on `limit` is a visible API change.

**Validation**
- **Baseline:** count SQL statements for `limit=5` and `limit=20` on development data.
- **Expectation:** after the fix, the count is constant regardless of `limit`. The latency effect is unknown until measured.
- **Falsifier:** if the count does not grow with `limit` today, the finding is wrong.
- **Guard:** a query-count test.
- Commands are in §9.

---

### PERF-002: Comments list has no bound and loads each author separately

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/comments_controller.rb:6` |
| **Tags** | quick-win, scalability-risk |

**Problem**
`GET /api/articles/:slug/comments` returns every comment on the article, with no limit and no preload. Each comment then loads its author separately, plus the follow state when the viewer is signed in.

**Performance principle**
A list response must have an upper bound, and related data for each row should be fetched in set-based reads.

**Evidence**
- `comments_controller.rb:6`: `@article.comments.order(created_at: :desc)`, with no limit and no `includes`.
- `_comment.json.jbuilder:2`: `comment.user` loads lazily for each comment.
- `_profile.json.jbuilder:3`: following? runs for each comment author.
- `db/schema.rb:38`: there is an index on `article_id` only, so the ORDER BY sorts all of the article's comments.
- Derived: 1 + c + c queries, where c has no bound.
- No runtime evidence.

**Impact**
- **Position:** critical path.
- **Frequency:** per item.
- **Growth:** O(n) in comments per article, which has no retention.
- **Blast radius:** system-wide, through the shared SQLite file and pool.

**Conditions**
This assumes some articles collect many comments. The comments table is append-only with no cleanup, but no row counts are available. Confidence is capped at Medium for that reason.

**Counter-evidence**
- No pagination or `includes` exists anywhere for comments (no-effect).
- Comment counts per article are unknown (lowers-confidence).

**Why this might not matter**
Most articles may have only a few comments, in which case the extra cost is a handful of indexed lookups.

**Recommendation**
- Add `includes(:user)`.
- Load the follow state for the distinct authors in one query.
- Add a page with a server-side maximum.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: `includes(:user)`, a batched follow set, and a page cap | **preferred** | Fixed query count and a bounded payload |
| B: `includes(:user)` only | | A one-line quick win that leaves the list unbounded |
| C: Cache comments per article | | Follow state is per viewer, and every comment and follow would need invalidation |

**Trade-offs**
Pagination departs from the RealWorld spec, which returns all comments, so clients would need to change.

**Validation**
- **Measure:** query count with 2 and with 20 comments.
- **Expectation:** about 3 queries in both cases after the fix.
- **Falsifier:** the count does not grow with comments today.
- **Safety:** not-safe-on-production. Run it in development.
- **Guard:** a query-count test.

---

### PERF-003: Article list sorts and counts the whole table on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `app/controllers/articles_controller.rb:11` (also `:13`, `:19`, `:21`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
Each list request does three pieces of whole-table work:
- a full `COUNT`
- `ORDER BY created_at DESC` with no supporting index
- `OFFSET` pagination

So the database scans and sorts every candidate article on every request, and deep offsets add more.

**Performance principle**
On a hot read path, the cost of each request should depend on the page returned, not on the size of the table.

**Evidence**
- `articles_controller.rb:11`: `@articles.count` runs on every request.
- `:13`: order, offset, and limit.
- `db/schema.rb:27-28`: the only indexes are on `slug` and `user_id`.
- No `EXPLAIN QUERY PLAN` output exists. The scan-and-sort is inferred from the schema.

**Impact**
- **Position:** critical path.
- **Frequency:** per request.
- **Growth:** O(n) in articles.
- **Blast radius:** system-wide, because the reads run on the shared SQLite file and, in rollback-journal mode, contend with writes.

**Conditions**
This assumes the unfiltered list is the most-called route and that `articles` grows without bound. The cost becomes material only beyond a table size the repo does not state, so confidence is Medium.

**Counter-evidence**
- No index covers `created_at` in any migration or in the schema (no-effect).
- The author and feed queries can narrow by the `user_id` index first, which bounds their cost (bounds-impact).
- Table size is unknown (lowers-confidence).

**Why this might not matter**
A demo-scale table of a few thousand rows sorts cheaply, at much lower cost than PERF-001. The count is also required by the response field `articlesCount`.

**Recommendation**
- Confirm the plan with `EXPLAIN QUERY PLAN`.
- Add an index on `articles(created_at)`, and consider `(user_id, created_at)` for the author and feed queries.
- Measure the COUNT separately. Whether to approximate or cache it is a product decision.
- Consider keyset pagination for deep pages.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Indexes, confirmed with the query plan | **preferred** | Reading the top k rows replaces sorting the whole table. Cheap and reversible |
| B: Keyset pagination | | Also fixes deep offsets, but changes the API |
| C: Cache the count | | Covers only half the cost, and the count goes stale |

**Trade-offs**
Each index adds write cost and storage. Under SQLite's single writer, writes take slightly longer.

**Validation**
- **Before:** the plan shows `SCAN articles` and `USE TEMP B-TREE FOR ORDER BY`.
- **After:** the plan uses the index, with no temp B-tree.
- **Falsifier:** the current plan already avoids the sort.
- **Safety:** `EXPLAIN QUERY PLAN` is safe-on-production.
- **Guard:** a query-plan check in CI.

---

### PERF-004: Production is configured on a single-writer embedded SQLite file

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `config/database.yml:23` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
SQLite allows one writer for the whole database file at a time. Every write endpoint (favorite, comment, article, follow, registration, user update) queues on that lock. A queued write waits up to the configured 5000 ms busy timeout and then fails. The file also cannot be shared safely across hosts.

**Performance principle**
A serialized resource shared by every request limits write throughput to one write at a time. As demand approaches that limit, waiting time grows non-linearly and spills over onto readers.

**Evidence**
- `config/database.yml:23-25`: production uses the sqlite3 adapter with `db/production.sqlite3`.
- `config/database.yml:9-10`: `pool: 5`, `timeout: 5000`.
- No `journal_mode` or WAL setting exists anywhere, so the default rollback journal applies.
- Write call sites: `favorites_controller.rb:6,12`, `comments_controller.rb:13,20`, `articles_controller.rb:30,45,57`, `follows_controller.rb:7,15`, `users_controller.rb:8`.
- No deployment files exist.

**Impact**
- **Position:** critical path.
- **Frequency:** per write request.
- **Growth:** unknown. It depends on write concurrency.
- **Blast radius:** system-wide.

**Conditions**
This assumes production uses this config and that writes arrive concurrently. At low write concurrency the lock is rarely contended. Request rates and process count are unknown, so confidence is Medium.

**Counter-evidence**
- There is no alternate adapter or `pg`/`mysql` gem, but a `DATABASE_URL` override cannot be ruled out (lowers-confidence).
- Single-row writes are short, which limits how long each one holds the lock (bounds-impact).

**Why this might not matter**
On a single host with low traffic, SQLite with short transactions handles modest write rates well, and it avoids network round trips entirely.

**Recommendation**
1. Measure busy/locked errors and confirm the deployment first.
2. If production stays on one host with low writes, enable WAL so that readers do not block on writers.
3. Otherwise, move to a client-server relational engine.
4. Do not respond by enlarging the pool. On SQLite that adds no write concurrency.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Measure, then choose WAL on SQLite or a client-server engine | **preferred** | The right fix depends on traffic that nobody has stated |
| B: A larger pool | | No write gain, and possibly more busy contention |
| C: Migrate now | | Operational cost that may not be justified at demo scale |

**Trade-offs**
WAL requires checkpoint management and allows the WAL file to grow. A client-server engine adds a network hop, an operated service, and connection limits.

**Validation**
- **Commands:** `PRAGMA journal_mode` and a grep for `database is locked` in the production log. Both are safe-on-production.
- **Expectation:** if the lock is contended, busy errors or write latency close to 5000 ms appear under concurrent writes.
- **Falsifier:** production does not use SQLite, or there are no busy waits at peak.
- **Guard:** an alert on busy/locked exceptions.

---

### PERF-005: The tag cloud aggregates all taggings on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `app/controllers/tags_controller.rb:3` |
| **Tags** | quick-win, needs-measurement |

**Problem**
`Article.tag_counts.most_used` recomputes tag counts from `taggings` on every request. The `tags` table already maintains a `taggings_count` column (`db/schema.rb:79`).

**Performance principle**
Do not recompute an aggregate over a growing table on every request when an incrementally maintained value already exists.

**Evidence**
- `tags_controller.rb:3`
- `db/schema.rb:79`
- The gem's exact SQL is not in the repo.

**Impact**
- **Position:** critical path.
- **Frequency:** per request.
- **Growth:** O(n) in taggings.
- **Blast radius:** endpoint.
- The query runs once per request, and its output is limited.

**Conditions**
This assumes the tag list loads on every home page and that taggings grow with articles.

**Counter-evidence**
- It is one statement with limited output (bounds-impact).
- The gem's SQL is unverified (lowers-confidence).

**Why this might not matter**
With a few thousand taggings, the grouped scan is fast.

**Recommendation**
Read the top tags from `tags`, ordered by `taggings_count` (`ActsAsTaggableOn::Tag.most_used`). Article is the only taggable model, so the counter matches.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: Use `tags.taggings_count` | **preferred** | Removes the aggregate |
| B: Cache with a TTL | | Still recomputes when the entry expires, and adds staleness. Unnecessary when a counter exists |

**Trade-offs**
The counter counts all taggable types, so it would diverge if another taggable model were added later. An index on `taggings_count` would add write cost.

**Validation**
- **Measure:** compare the logged SQL and plan before and after.
- **Falsifier:** the gem already reads the counter.
- **Safety:** not-safe-on-production. Run it in development.

---

### PERF-006: Article delete cascades row by row under the write lock

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `app/models/article.rb:3-4` |
| **Tags** | — |

**Problem**
`dependent: :destroy` on favorites and comments loads each dependent row and deletes it individually. Each favorite also decrements the counter cache (`favorite.rb:3`). All of this happens inside one transaction that holds SQLite's exclusive lock.

**Performance principle**
A single logical delete should not issue one statement per dependent row while holding a lock that every other writer needs.

**Evidence**
- `article.rb:3-4`
- `favorite.rb:3`
- `articles_controller.rb:57`
- Derived: about 1 + 2f + 1 + c statements, where f is the number of favorites and c the number of comments.

**Impact**
- **Position:** critical path.
- **Frequency:** rare.
- **Growth:** O(n) in dependents.
- **Blast radius:** system-wide, because the database lock is held throughout.

**Conditions**
This matters when a popular article is deleted while other writes are in flight. Today the path also fails and rolls back because of COR-001, after it has already done the per-row work.

**Counter-evidence**
- Deletes are owner-only and rare (bounds-impact).
- No callbacks other than the counter cache need to run per row (no-effect).

**Why this might not matter**
Deletes are rare, and most articles have few dependents.

**Recommendation**
- Use `dependent: :delete_all` for comments and favorites.
- Fix COR-001.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: `:delete_all` | **preferred** | Two set-based DELETEs. The only callback lost is the counter cache on a row that is being deleted anyway |
| B: Database `ON DELETE CASCADE` | | Requires turning on SQLite's foreign_keys pragma for each connection |

**Trade-offs**
Any future destroy callbacks on Comment or Favorite would silently not run.

**Validation**
- **Expectation:** a constant statement count regardless of the number of dependents.
- **Falsifier:** the count does not grow with dependents today.
- **Safety:** not-safe-on-production.

---

### PERF-007: No performance instrumentation exists

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | observability |
| **Location** | `app/controllers/application_controller.rb:1` |
| **Tags** | quick-win |

**Problem**
Nothing in the repo records per-route duration, SQL count, pool wait, or SQLite busy errors.

**Performance principle**
Optimization is only defensible when the mechanism being changed is measured.

**Evidence**
- No metrics or APM gems appear in `Gemfile` or `Gemfile.lock`.
- There are no notification subscribers.
- `test/` contains only stubs.

**Impact**
- **Position:** critical path.
- **Frequency:** per request.
- **Growth:** O(1).
- **Blast radius:** service.
- It limits every other finding to inference from the code.

**Conditions**
This applies at any traffic level.

**Counter-evidence**
Rails' default request log lines already report total, view, and ActiveRecord time per request, which gives coarse timing (bounds-impact).

**Recommendation**
- Subscribe to `process_action.action_controller` and `sql.active_record`, and log the route, duration, and SQL count per request.
- Record busy errors.
- Although P3, this is sequenced first. It is cheap, and PERF-001 through PERF-006 cannot be validated without it.

**Trade-offs**
Small per-request overhead and more log volume.

**Validation**
Every request emits a timing and query-count record. If an external APM already covers production, this finding is moot. Safe-on-production.

---

### PERF-008: Production logs every SQL statement at debug level

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | io |
| **Location** | `config/environments/production.rb:49` |
| **Tags** | quick-win |

**Problem**
`config.log_level = :debug` formats and writes every SQL statement. The per-row queries in PERF-001 and PERF-002 multiply this cost.

**Performance principle**
Diagnostic output for each operation on the hot path adds formatting and synchronous I/O in proportion to the work being done.

**Evidence**
`config/environments/production.rb:49`

**Impact**
- **Position:** critical path.
- **Frequency:** per statement.
- **Growth:** O(n) in statements.
- **Blast radius:** service.
- The cost per statement is small.

**Conditions**
The cost scales with the number of statements per request and with the speed of the log device.

**Counter-evidence**
The cost per statement is a small constant (bounds-impact).

**Recommendation**
Use `:info` in production, and take SQL counts from PERF-007's subscriber instead.

**Trade-offs**
SQL text no longer appears in the production logs.

**Validation**
- **Measure:** log bytes per request before and after.
- **Expectation:** log volume drops sharply.
- **Falsifier:** the environment overrides the log level.
- **Safety:** safe-on-production.

---

### PERF-009: Unfavorite loads rows to delete them one by one, then reloads the article

| | |
|:--|:--|
| **Root cause** | `ROOT-009` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `app/models/user.rb:31-33` |
| **Tags** | quick-win |

**Problem**
`destroy_all` runs a SELECT and then a DELETE plus a counter UPDATE for each row. It is followed by `article.reload`, an extra full read of the article.

**Performance principle**
Avoid round trips that re-fetch data the request already holds.

**Evidence**
`user.rb:31-33`

**Impact**
- **Position:** critical path.
- **Frequency:** per request.
- **Growth:** O(1).
- **Blast radius:** endpoint.

**Conditions**
There is normally one favorite row per user and article, so the cost is a few extra statements per unfavorite.

**Counter-evidence**
The reload exists to return the updated count, and it touches only one row (bounds-impact).

**Recommendation**
Leave it unless unfavorite traffic is high. If it is, decrement the count in memory instead of reloading.

**Trade-offs**
A count kept in memory can drift from the database.

**Validation**
- **Expectation:** one fewer SELECT per request.
- **Falsifier:** unfavorite traffic is negligible.
- **Safety:** not-safe-on-production.

---

### PERF-010: Uncapped client tag list is written tag by tag inside the write transaction

| | |
|:--|:--|
| **Root cause** | `ROOT-010` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `app/controllers/articles_controller.rb:68` |
| **Tags** | — |

**Problem**
`tag_list: []` is permitted with no length check. The tagging gem saves tags and taggings one tag at a time inside the write transaction, which extends how long SQLite's exclusive lock is held.

**Performance principle**
A multiplier the client controls should be bounded when it runs inside an exclusive lock.

**Evidence**
- `articles_controller.rb:68`
- `article.rb:9`: there is no validation on the tag count.
- The gem's per-tag write behavior is unverified.

**Impact**
- **Position:** critical path.
- **Frequency:** per tag.
- **Growth:** O(n) in tags.
- **Blast radius:** system-wide, because the lock is held throughout.

**Conditions**
This matters only when a client sends a very large tag list.

**Counter-evidence**
Tag lists are usually small, and article creation is infrequent (bounds-impact).

**Recommendation**
Validate a maximum length for `tag_list`.

**Trade-offs**
Requests with many tags that succeed today would be rejected.

**Validation**
- **Measure:** statement count for 1 tag versus 20 tags.
- **Falsifier:** the count stays constant.
- **Safety:** not-safe-on-production.

---

### Remaining findings

None. All 10 findings are written up in full above.

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-001 | High | High | P1 | `_article.json.jbuilder:3` | Per-row queries on article lists, with an uncapped `limit` |
| PERF-002 | High | Medium | P1 | `comments_controller.rb:6` | Comments list has no bound; author loaded separately for each comment |
| PERF-003 | High | Medium | P1 | `articles_controller.rb:11` | Whole-table COUNT and unindexed sort on every list request |
| PERF-004 | High | Medium | P1 | `database.yml:23` | Production on a single-writer SQLite file |
| PERF-005 | Medium | Medium | P2 | `tags_controller.rb:3` | Tag counts aggregated on every request |
| PERF-006 | Medium | High | P2 | `article.rb:3` | Row-by-row cascade delete under the write lock |
| PERF-007 | Low | High | P3 | `application_controller.rb:1` | No instrumentation |
| PERF-008 | Low | High | P3 | `production.rb:49` | Debug logging in production |
| PERF-009 | Low | High | P3 | `user.rb:31` | destroy_all plus reload on unfavorite |
| PERF-010 | Low | Medium | P3 | `articles_controller.rb:68` | Uncapped tag list written tag by tag |

### Considered and not reported

| Candidate | Path / resource | Evidence checked | Why not reported | Revisit when |
|:--|:--|:--|:--|:--|
| Pool of 5 may be smaller than Puma's thread count, causing checkout waits | The ActiveRecord pool per process | `config/database.yml:9`, `Gemfile`; no `puma.rb`, Procfile, or Dockerfile | Depends on a deployment fact the repo does not state. Asked as question #2 instead | Production threads per process exceed 5 |
| No response compression | Response encoding | `config/application.rb`, `production.rb`, `config.ru` | Compression is usually done at a proxy, and none is evidenced. Payloads are bounded except comments (PERF-002) | No compressing proxy, and large payloads |
| bcrypt cost of 11 on login and registration | Login and registration routes | `config/initializers/devise.rb:108` | An intentional security cost on infrequent paths | Profiles show login CPU saturating workers |

### Adjacent findings: outside performance scope

### COR-001: Article deletion fails because of a bogus `has_many :articles`

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `app/models/article.rb:15` |

**Problem**
`Article` declares `has_many :articles, dependent: :destroy`. The `articles` table has no `article_id` column (`db/schema.rb:16-25`), so destroying an article queries a column that does not exist. The query raises an error and the delete rolls back.

**Evidence**
- `app/models/article.rb:15`
- `db/schema.rb:16-25`

**Impact**
`DELETE /api/articles/:slug` cannot succeed, which breaks a core API operation. That is why the risk is High.

**Recommendation**
Remove the line, and add a request test for article deletion.

**Trade-offs**
None material.

**Validation**
A request test that deletes an article and expects a 200 response with the row gone.

**Would need**
A correctness-focused request test suite.

### COR-002: Duplicate favorites and follows are possible under concurrent requests

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Medium |
| **Location** | `app/models/user.rb:27` |

**Problem**
`find_or_create_by` checks for an existing row and then inserts. There is no unique index on `favorites(user_id, article_id)` or on the follows pair (`db/schema.rb:48-62`). Two concurrent requests can therefore both insert, creating duplicates and inflating `favorites_count`.

**Evidence**
- `app/models/user.rb:27`
- `db/schema.rb:48-49,61-62`

**Impact**
Counts become wrong, and unfavorite would delete several rows. It requires concurrent requests from the same user, so the risk is Medium.

**Recommendation**
1. Remove existing duplicates.
2. Add unique composite indexes.
3. Handle `RecordNotUnique`.

**Trade-offs**
A migration, plus a small write cost for each new index.

**Validation**
A duplicate-detection query on production data, and a concurrency test.

**Would need**
A data-integrity review.

### MAINT-001: Framework and dependencies are on an end-of-life Rails 4.2 line

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `Gemfile:5` |

**Problem**
The app is locked to Rails 4.2.6, devise 4.2.0, jwt 1.5.4, and sqlite3 1.3.11 (`Gemfile.lock`). This framework line no longer receives security fixes.

**Evidence**
- `Gemfile:5`
- `Gemfile.lock:97` (rails 4.2.6), `:76` (jwt 1.5.4), `:57` (devise 4.2.0)

**Impact**
Known framework vulnerabilities would go unpatched on a public API. That is why the risk is High.

**Recommendation**
Plan an upgrade to a supported Rails line; the README already points to a rails-5.1 branch. Run a dependency audit alongside it.

**Trade-offs**
A substantial upgrade effort.

**Validation**
A clean dependency-vulnerability scan.

**Would need**
`bundler-audit` or an equivalent dependency scan, and an upgrade plan.

---

## 8. Prioritized action plan

### P1: High priority
PERF-001, PERF-002, PERF-003, PERF-004

### P2: Medium priority
PERF-005, PERF-006

### P3: Optimization opportunity
PERF-007, PERF-008, PERF-009, PERF-010

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-007 | P3 | Small | Sequenced first despite P3: it is cheap, and it is the only way to validate everything below |
| 2 | PERF-002 | P1 | Small (`includes(:user)`) to medium (paging) | One-line quick win on an unbounded path |
| 3 | PERF-001 | P1 | Medium | Highest-confidence per-request multiplier on the hottest route |
| 4 | PERF-004 | P1 | Measure first, then a decision | Settles the blast radius of everything else. Answer question #1 first |
| 5 | PERF-003 | P1 | Small (index), after `EXPLAIN QUERY PLAN` | Cheap once the plan confirms the sort |
| 6 | PERF-005 | P2 | Small | Swap in the counter |
| 7 | PERF-006 (+ COR-001) | P2 | Small | Fix together with the correctness bug |
| 8 | PERF-008 | P3 | Trivial | Once PERF-007 provides SQL counts |
| 9 | PERF-009, PERF-010 | P3 | Trivial | Only if measurement shows they matter |

**If only one thing is done:** fix PERF-001. Batch the favorite, follow, and tag lookups for the article page, and cap `limit` on the server.

---

## 9. Validation plan

### PERF-001
- **Baseline:** count SQL statements for `GET /api/articles?limit=5` and `?limit=20`, signed in and anonymous, against development data with representative rows. Not safe-on-production.
- **Change:** batch the favorite and follow id sets, preload taggings, and cap `limit`.
- **Measurement:** SQL statements per request.
- **Expectation:** after the change, the count is the same for `limit=5` and `limit=20`. Today it is predicted to differ by about 3 × 15 when signed in (a derivation). The latency change is not quantified.
- **Falsifier:** if the count does not grow with `limit` today, or latency does not change after the count drops, the path was not query-bound.
- **Guard:** a test that subscribes to `sql.active_record` and asserts equal counts for page sizes 5 and 20.
- **Commands:**
  - `not-safe-on-production`: `RAILS_ENV=development bin/rails runner 'n=0; ActiveSupport::Notifications.subscribe("sql.active_record"){ n+=1 }; app=ActionDispatch::Integration::Session.new(Rails.application); app.get "/api/articles?limit=20"; puts n'`. Counts SQL statements for one list request.
  - `not-safe-on-production`: `grep -c 'SELECT' log/development.log`. Gives a rough SELECT count after a single request, starting from an emptied log.

### PERF-002
- **Baseline:** query count for an article with 2 comments and one with 20 (development). Not safe-on-production.
- **Change:** `includes(:user)`, a batched follow set, and a page cap.
- **Expectation:** about 3 queries in both cases.
- **Falsifier:** the count does not grow with comments today.
- **Guard:** a query-count test.

### PERF-003
- **Baseline:** the query plan for the list query.
- **Change:** an index on `created_at` (and optionally `user_id, created_at`).
- **Expectation:** the temp B-tree sort disappears from the plan.
- **Falsifier:** the plan already avoids the sort.
- **Guard:** a plan check in CI.
- **Commands:**
  - `safe-on-production`: `sqlite3 db/production.sqlite3 "EXPLAIN QUERY PLAN SELECT articles.* FROM articles ORDER BY articles.created_at DESC LIMIT 20 OFFSET 0;"`. Shows the plan without running the query.
  - `not-safe-on-production`: `sqlite3 db/production.sqlite3 "SELECT COUNT(*) FROM articles; SELECT COUNT(*) FROM taggings; SELECT MAX(c) FROM (SELECT COUNT(*) c FROM comments GROUP BY article_id);"`. Gets the row counts that decide PERF-002, PERF-003, and PERF-005. It scans every table, so run it on a copy of the file.

### PERF-004
- **Baseline:** journal mode and the count of lock errors.
- **Change:** WAL mode, or a different engine, decided after measuring.
- **Expectation:** with WAL, readers stop waiting on writers. Writers still serialize.
- **Falsifier:** production does not use SQLite, or there are no busy waits at peak.
- **Guard:** an alert on busy/locked exceptions.
- **Commands:**
  - `safe-on-production`: `sqlite3 db/production.sqlite3 'PRAGMA journal_mode;'`. Shows whether the database uses the rollback journal or WAL.
  - `safe-on-production`: `grep -c 'database is locked' log/production.log`. Counts lock-timeout failures already recorded.

### PERF-005 / PERF-006 / PERF-009 / PERF-010
- **Baseline:** the logged SQL and statement count for each route in development. Not safe-on-production.
- **Expectation:** after each change, the aggregate is replaced by a counter read (PERF-005), or the statement count becomes constant (PERF-006 and PERF-010), or drops by one SELECT (PERF-009).
- **Falsifiers:** as given in each finding.

### Instrumentation gaps to close first
PERF-007 comes first. Log the route, duration, and SQL count for every request, and count SQLite busy errors. Without these, no fix above can be told apart from coincidence.

---

## 10. Machine-readable output

The machine-readable review is at `rails-guided-1.json`, alongside this report. It conforms to `schemas/review.schema.json`, and the bundled `validate_review.py` reports it as a valid review with 10 findings. The `stable_id` values were computed with `scripts/compute_stable_id.py`. If the JSON and this report disagree, this report is authoritative.

---

## 11. Notes on this review

- Findings are graded by evidence. None is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report is estimated. The query counts (3k, 60, 1 + c + c, 1 + 2f + 1 + c) are derivations from the cited code, labelled as such, and they partly depend on gem internals I did not inspect.
- Every recommendation states its trade-offs and how to validate it.
