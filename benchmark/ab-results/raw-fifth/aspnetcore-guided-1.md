# Performance Review: aspnetcore-realworld-example-app (Conduit API)

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec backend-performance-review/2.0), commit `a397d1197b22edeffa4d2563fa5f4f7f11d0b254`

---

## 1. Decision summary

### Overall assessment

This is a small, cleanly structured ASP.NET Core 10 API on embedded SQLite. Its main scaling constraint is not any single query. **Every request, reads included, holds a database transaction for its whole handler against a single-writer database file** (PERF-001), so all other per-request costs add to time spent holding one global lock. On top of that, the article list has no page-size cap (PERF-002), article reads load every favorite row multiplied by every tag (PERF-003), and no lookup column (Slug, Username, Email, CreatedAt) is indexed (PERF-004). Nothing here is measured: the repository has no metrics, traces, or load tests, and no one was available to answer workload questions. All findings come from code structure, and the ones that depend on workload are capped at Medium confidence. Review confidence: **Medium**.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Run the transaction pipeline behaviour for commands only, and enable SQLite WAL mode | It serializes every request on one lock and multiplies every other finding's cost |
| 2 | **PERF-002 (P1)** | Cap `limit` (and `offset`) in a `List.Query` validator | Anonymous callers can request the whole table today; the fix is a few lines (quick-win) |
| 3 | **PERF-003 + PERF-004 (P1)** | Project article reads (COUNT/EXISTS, no Body in lists); add unique indexes on Username/Email/Slug and an index on CreatedAt via migrations | Removes the row multiplication and per-request table scans that lengthen the lock hold; also fixes COR-001/COR-002 |

Instrumentation (PERF-005, P2, quick-win) should go in alongside action 1 so the P1 changes can be measured. See §8.

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Peak concurrent requests per instance | PERF-001 stays Critical/P1, or drops to Medium/P2 if requests rarely overlap | Answer Q1, or request-rate metrics from PERF-005 |
| Row counts; max favorites and comments per article | PERF-003/004/008 between P1/P2 and P2/P3 | `SELECT COUNT(*)` per table on a copy of the database |
| SQLite for production, or SQL Server? | PERF-001 mechanism disappears on SQL Server; PERF-006 sync `Count()` becomes a real thread-pool block | Answer Q3 |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | `not-safe-on-production` (run locally after `make run-local`) | `seq 400 \| xargs -P 16 -I{} curl -s -o /dev/null -w "%{time_total}\n" http://localhost:5000/api/articles` (compare with `-P 1`) | If p50 at 16-way concurrency matches 1-way, requests do not serialize and PERF-001 is overstated |
| PERF-004 | `safe-on-production` | `sqlite3 src/Conduit/realworld.db "EXPLAIN QUERY PLAN SELECT * FROM Persons WHERE Username = 'x' LIMIT 1;"` | SCAN confirms the missing access path; SEARCH USING INDEX refutes it |
| PERF-001 | `safe-on-production` | `sqlite3 src/Conduit/realworld.db "PRAGMA journal_mode;"` | `delete` confirms readers block writer commits; `wal` narrows PERF-001 |

---

## 2. Scope and method

**Reviewed:** All application code under `src/Conduit` (Program, ServicesExtensions, Infrastructure, Domain, every feature slice: Articles, Comments, Favorites, Followers, Profiles, Tags, Users), `build/Program.cs`, `Dockerfile`, `docker-compose.yml`, `Makefile`, both CI workflows, `Directory.Packages.props`, `Directory.Build.props`, `global.json`, and the integration-test fixture.
**Not reviewed:** The `realworld` git submodule, which is the upstream spec and is empty in this checkout. The test bodies beyond `SliceFixture.cs`, which use EF Core InMemory and say nothing about SQLite runtime behaviour. The SQL Server code path, which is unreachable because the provider is hard-coded (see "Considered and not reported").

**Evidence available:** Uninstrumented. No metrics, tracing, benchmarks, load tests, query plans, dashboards, or SLOs exist. The only runtime signal is framework console logging.

**Ranking method:** Structural signals only. No runtime data was supplied.

**Reference depth:** SQLite, .NET, REST, and Docker were all analysed at `deep` tier. SQL Server is detected (package reference) but unreachable, so it was not analysed in depth. One SQLite fact this review depends on, the lock mode Microsoft.Data.Sqlite uses for `BeginTransaction(ReadCommitted)`, is library behaviour that is not visible in the repository and is flagged as needing verification.

### Workload questions asked (unanswered)

Nobody was available to answer workload questions. These are the questions I would have asked, in priority order. The review went ahead under the assumptions in §4.

1. At peak, roughly how many requests are in flight concurrently against one instance, and is exactly one instance writing the SQLite file?
2. Roughly how many rows are in Articles, Persons, and ArticleFavorites, and what are the largest favorites and comment counts on a single article?
3. Will production stay on SQLite, or is the SQL Server provider meant to be used?
4. Is a specific slow endpoint, incident, or scaling deadline behind this review?
5. Is container stdout collected by a log pipeline, and is the log level raised by configuration outside this repository?
6. How many followers and followees do the most-followed accounts have?

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All application source and all build/deploy/CI files were read; the spec submodule and InMemory test bodies were not |
| **Critical paths** | 19 / 19 HTTP routes analysed (plus startup) |
| **Shared resources** | 4 / 4: SQLite database file and its lock; Microsoft.Data.Sqlite connections; ASP.NET Core thread pool; console logging sinks |
| **Technology support** | SQLite deep, .NET deep, REST deep, Docker deep; SQL Server deep but unreachable |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not finding confidence. The code readings below are unambiguous. What is missing is any measurement of how much they cost under real traffic.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production concurrency and instance count | No evidence exists | Workload Q1; request metrics (PERF-005) |
| Table sizes and favorites/comments distribution | No evidence exists | Row counts on a copy of `realworld.db` |
| Lock mode taken by `BeginTransaction(ReadCommitted)` in Microsoft.Data.Sqlite 10.0 | No evidence exists in the repository (library behaviour) | Library docs for 10.0, or the PERF-001 local concurrency test |
| Query plans | No evidence exists | `EXPLAIN QUERY PLAN` commands in §9 |
| SQL Server behaviour | Out of scope (unreachable code) | Re-review if the provider becomes configurable |

---

## 3. Architecture overview

A single ASP.NET Core 10 MVC process (`dotnet Conduit.dll`, exec-form `ENTRYPOINT`, port 8080) with controllers that dispatch to source-generated Mediator handlers. The pipeline is `ValidationPipelineBehavior` → `DBContextTransactionPipelineBehavior` → handler → EF Core 10.0.10 → SQLite file `realworld.db`. JWT bearer auth is validated locally with no remote calls. There are no outbound HTTP calls, cache, broker, or background jobs. The schema is created at startup by `EnsureCreated()`.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime | .NET / ASP.NET Core | net10.0, SDK 10.0.302 | deep | API host |
| ORM | EF Core | 10.0.10 | (data-access category) | All persistence |
| Datastore | SQLite via Microsoft.Data.Sqlite, SQLitePCLRaw 3.0.5 | engine version not pinned in repo | deep | Single embedded database file |
| Datastore (declared) | SQL Server provider | 10.0.10 | deep | Unreachable: provider hard-coded to sqlite (`Program.cs:15-24`) |
| Logging | Serilog console + default providers | Serilog 4.4.0 | — | stdout |
| Container | Docker, compose (one service, no replicas, no volume) | — | deep | Deployment |

**Shared resources:** the SQLite database file and its single-writer lock, which is the dominant shared resource; Microsoft.Data.Sqlite connections, which are in-process and give no extra write concurrency; the ASP.NET Core thread pool; and the console logging sinks.

---

## 4. Workload model

**Known**

- Provider is SQLite with file `realworld.db`, hard-coded. Compose env vars are ignored. Source: `src/Conduit/Program.cs:15-30`, `docker-compose.yml`
- Every Mediator request runs in an explicit transaction (`ReadCommitted`). Source: `ServicesExtensions.cs:26-30`, `DBContextTransactionPipelineBehavior.cs:27-31`, `ConduitContext.cs:94-105`
- No secondary indexes and no migrations. Source: `ConduitContext.cs:21-91`, `Program.cs:120-126`
- Article list default page 20 with no maximum, offset pagination, exact COUNT on every request. Source: `Features/Articles/List.cs:105-133`
- One compose service with no replica setting; no Kubernetes or IaC manifests. Source: `docker-compose.yml`
- No retention or cleanup of any table; Tag rows are never deleted. Source: `Features/Articles/Delete.cs`, `Edit.cs:128-136`
- Surface: 19 routes, 7 GET and 12 mutating. Source: `Features/*/*Controller.cs`
- No appsettings file. Serilog console sink at Verbose is attached unconditionally. Source: `ServicesExtensions.cs:110-125`, `Program.cs:105`

**Assumed**

- More than one request is in flight at a time in production. Affects PERF-001
- Articles, Persons, ArticleFavorites, and Comments grow with use. Affects PERF-002, -003, -004, -006, -008, -011
- No deploy-time configuration raises the log level. Affects PERF-010

**Unknown**

- Peak concurrency and instance count; row counts and distributions; latency at any percentile; the exact SQLite lock mode taken per transaction; whether stdout is shipped

**Derived**

- Rows returned per article by `GetAllData` = max(1, favorites) × max(1, tags), because two collection includes are joined in one statement with no split query. From: `ArticleExtensions.cs:9-14`; no `AsSplitQuery`/`UseQuerySplittingBehavior` in `src`
- Statements on an authenticated filtered feed request = 5 (person+followees, tag check, page, followed ids, COUNT), all inside one transaction. From: `List.cs:37-133`
- Statements for `POST /api/articles` with t new tags and k slug collisions = 1 + t lookups + t commits + (k+1) probes + 1. From: `Create.cs:54-101`

**Measured**

- None. No benchmark, trace, query plan, or metrics export was supplied or exists in the repository. This is why no finding is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Peak concurrent requests per instance; exactly one writer instance? | severity / confidence / recommendation | PERF-001 P1 → P2 if requests rarely overlap |
| 2 | highest | Row counts; max favorites and comments per article | severity / confidence | PERF-003/004/008 move between P1/P2 and P2/P3; PERF-003 could rise to Critical |
| 3 | high | SQLite or SQL Server in production? | severity / recommendation | PERF-001 mechanism disappears on SQL Server; PERF-006 blocking becomes real |
| 4 | high | Specific complaint behind this review? | recommendation | Review would be reorganized around it |
| 5 | medium | stdout shipped; log level raised outside the repo? | severity / confidence | PERF-010 refuted if the level is already Warning |
| 6 | medium | Follower/followee counts of heaviest accounts | severity | PERF-007 from Medium to High at thousands of follow edges |

---

## 5. Critical path analysis

Ranked by structural signals; no runtime data. All paths are blocking HTTP requests and all run inside the request-wide transaction (PERF-001). None are instrumented.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` (home page) | yes | page query (favorites × tags join) + COUNT, + followed-ids if authenticated, + 1 per filter | **no**: caller-chosen limit/offset | no | PERF-002, -003, -004 (CreatedAt sort), -006 |
| 2 | `GET /api/articles/feed` | yes | person+all followees, page, followed ids, COUNT | **no** | no | PERF-007 (double follow load) |
| 3 | `GET /api/articles/{slug}` | yes | 1 (join) | per-article favorites × tags | no | Unindexed slug (PERF-004), PERF-003 |
| 4 | `GET /api/profiles/{username}`, follow/unfollow | yes | 2-5 incl. full follow collections | per-user follow counts | no | PERF-007 |
| 5 | `POST /api/articles` | yes | 1 + 2t + (k+1) + 1 | **no** tagList cap | no | PERF-009 |
| 6 | Comment create/delete | yes | full comment collection load | per-article comment count | no | PERF-008 |
| 7 | `GET /api/articles/{slug}/comments` | yes | 1 (join) | **no** (contract) | no | PERF-011 |
| 8 | `GET /api/tags` | yes | 1 full table | grows (orphans) | no | PERF-012 |
| 9 | Favorite add/delete, article edit/delete | yes | 3-5 | yes | no | Unindexed slug/username lookups; re-read via `GetAllData` |
| 10 | `POST /api/users`, `/users/login`, `GET/PUT /api/user` | yes | 1-3 | yes | no | Unindexed username/email (PERF-004); fast HMAC (SEC-001) |

**Amplification points:** caller-chosen page size × (favorites × tags) rows per article, with Body repeated on each row (PERF-002 × PERF-003). Every statement's duration is added to the global lock hold (PERF-001), and the current user's username is resolved by an unindexed lookup up to 2-3 times per request (PERF-004).

**Paths deliberately not analysed in depth, and why:** Swagger UI and JSON (`/swagger`), which is static or generated once and not user-blocking for API clients. Startup (`EnsureCreated`), which is a one-time schema check with no fixed sleeps or warmup.

---

## 6. Layer analysis

### 6.1 Application

Handlers are thin and await throughout, with one exception: `queryable.Count()` at `List.cs:133` is synchronous. `DBContextTransactionPipelineBehavior` wraps every request in a transaction (PERF-001). Validation runs before the transaction opens, which is good: invalid requests do not take the lock. Cancellation tokens are passed through to EF calls. No background work, no outbound calls, no regex hot spots (slug regexes are source-generated).

### 6.2 API

The article list's `limit`/`offset` are unbounded (PERF-002). The comment and tag lists return full collections by contract (PERF-011, PERF-012). The list response serializes the EF entity graph directly, so shape is controlled by `[JsonIgnore]` attributes, not by a projection. That is why Body is loaded and then nulled (part of PERF-003). No response compression or caching headers; given unknown payload sizes, no recommendation is made on those.

### 6.3 Data access and datastore

SQLite, single file, default journal mode, no pragmas. Findings: request-wide transactions (PERF-001), cartesian eager loading (PERF-003), missing lookup and sort indexes (PERF-004), full COUNT per list (PERF-006), follow-graph materialization (PERF-007), collection loads on comment writes (PERF-008), per-item round trips on article create (PERF-009). No N+1 via lazy loading: lazy-loading proxies are not enabled, and every navigation is explicitly included. Foreign-key indexes exist by EF convention, so id-based joins are covered.

### 6.4 Infrastructure

One container with no resource limits, no replicas, no health check, and no volume. The SQLite file lives in the container's writable layer (MAINT-001). The exec-form `ENTRYPOINT` runs `dotnet` as PID 1, which handles SIGTERM, so there is no signal-forwarding gap. A connection-pool arithmetic check does not apply: there is one instance and an embedded engine, and adding connections adds no write concurrency on SQLite.

### 6.5 Observability

Absent (PERF-005). Framework console logs at Information are the only timing source. They are also a per-request cost (PERF-010).

---

## 7. Findings

### PERF-001: Every request holds a transaction on the single-writer SQLite database for its whole handler

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `src/Conduit/Infrastructure/DBContextTransactionPipelineBehavior.cs:27` |
| **Tags** | needs-measurement |

**Problem**
Every Mediator request, including pure reads such as `GET /api/articles` and `GET /api/tags`, opens an explicit transaction and holds it for the whole handler. The database is one SQLite file whose write lock covers the entire database, so concurrent requests serialize on one lock for the length of their handlers, not just their writes.

**Performance principle**
Hold a shared exclusive resource only for the work that needs it. A lock wider than the protected operation turns per-request latency into system-wide queueing.

**Evidence**
- `DBContextTransactionPipelineBehavior.cs:27-31` wraps `next()` in `BeginTransaction()`/`CommitTransaction()`.
- `ServicesExtensions.cs:26-30` registers it for **all** request types, with no query exclusion.
- `ConduitContext.cs:103` calls `Database.BeginTransaction(IsolationLevel.ReadCommitted)` for every non-InMemory provider.
- `Program.cs:15-30` hard-codes SQLite. No `journal_mode`/`busy_timeout` pragma anywhere, so the default rollback-journal mode applies.
- Engine behaviour: one writer per file; in rollback-journal mode, readers holding a shared lock block a writer's commit. As I understand Microsoft.Data.Sqlite, `ReadCommitted` is promoted to Serializable and a non-deferred transaction starts with `BEGIN IMMEDIATE`, which reserves the write lock even for read-only requests. That is library behaviour, not visible in this repository, and needs verifying for 10.0.
- No runtime evidence exists.

**Impact**
Position: critical path. Frequency: every request. Growth: wait time grows with concurrent requests × handler duration (O(n) in concurrency, and handler duration grows with data through PERF-003/004). Blast radius: system-wide, because every route shares the one lock.

**Conditions**
Matters whenever more than one request is in flight. That is assumed for any deployed API, but production concurrency is unknown (Q1).

**Counter-evidence**
- Integration tests use InMemory, where `BeginTransaction` is skipped (`SliceFixture.cs:24`, `ConduitContext.cs:101`), so the tests neither expose nor refute this. *(no-effect)*
- If the provider issues a deferred `BEGIN`, read-only requests share the lock and only block writer commits. That is narrower, but still request-wide. *(lowers-confidence → Medium)*
- Searched for WAL, `busy_timeout`, a read-only bypass, context pooling: none found. *(no-effect)*
- No external calls are made inside the transaction, so hold time is bounded by database work plus trivial CPU. *(bounds-impact)*

**Why this might not matter**
A demo deployment with a handful of users rarely has overlapping requests, and on a small file each handler takes very little time, so the serialization would never be observed.

**Recommendation**
Apply the transaction behaviour to commands only (a marker interface, or registering it for command types). Rely on `SaveChangesAsync`'s implicit transaction where a handler saves once, and keep an explicit transaction only where atomicity across several saves is needed (`Articles/Create.cs`). Enable WAL at startup so readers stop blocking writer commits. This narrows the lock scope, which is the cause. Adding capacity would not help: SQLite has one writer regardless.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Transaction for commands only + WAL | **preferred** | Removes read-side lock holding; write holds shrink to actual writes; small code change |
| Deferred transactions (`deferred: true`) | | Readers share the lock, but still hold it for the whole handler, blocking commits in rollback-journal mode |
| Move to SQL Server (branch exists) | | Removes the single-writer limit; large operational change, so justify it with measurements first |
| More instances | | Does not help: same file contention, or divergent files (MAINT-001) |

**Trade-offs**
Read requests lose a consistent multi-statement snapshot: the list page and its COUNT could disagree under a concurrent insert. WAL adds `-wal`/`-shm` files and checkpoint management. Narrowing write transactions exposes the check-then-insert uniqueness races (COR-002) that whole-request serialization currently hides, so ship the unique indexes from PERF-004 with or before this change.

**Validation**
See §9, PERF-001.

---

### PERF-002: Article list page size and offset are caller-controlled with no maximum

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `src/Conduit/Features/Articles/List.cs:109` |
| **Tags** | quick-win |

**Problem**
`GET /api/articles` and `/feed` pass the caller's `limit`/`offset` straight to `Take`/`Skip`. One anonymous request can ask for the whole Articles table, with each article multiplied into favorites × tags rows (PERF-003), all buffered in memory. Deep offsets cost more as they go deeper.

**Performance principle**
Every response a caller can request needs an enforced upper bound; an overridable default is not a bound.

**Evidence**
`List.cs:108-111` (`.Skip(message.Offset ?? 0).Take(message.Limit ?? 20)` then `ToListAsync`). `ArticlesController.cs:14-22` binds both from the query string with no `[Authorize]`. There is no validator for `List.Query`.

**Impact**
Position: critical path (home page). Frequency: per request. Growth: O(n) in the requested limit, up to the full table. Blast radius: system-wide, because the request holds the global lock (PERF-001) for its whole duration.

**Conditions**
A client, buggy or hostile, sends a large limit. No workload assumption is needed for the exposure; the cost of one such request scales with Articles size (unknown).

**Counter-evidence**
- No global filter, binding constraint, or middleware clamps the parameters. *(no-effect)*
- The bundled RealWorld frontend sends small limits, so typical traffic is small. *(bounds-impact)*

**Why this might not matter**
If only the bundled frontend ever calls the API, large limits never happen.

**Recommendation**
Add a FluentValidation validator for `List.Query` with a maximum `limit` and non-negative bounded `offset`. The existing `ValidationPipelineBehavior` applies it, and it runs before the transaction opens.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Validator-enforced maximum | **preferred** | Smallest change; removes the unbounded case |
| Keyset pagination | | Fixes deep-offset cost but breaks the offset contract and random page access |
| Response caching | | Does not bound arbitrary limits; invalidation on every write |

**Trade-offs**
A contract limit to document: oversize requests get clamped or rejected.

**Validation**
See §9.

---

### PERF-003: Article reads join every favorite and every tag in one statement, and load Body only to discard it

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/Conduit/Features/Articles/ArticleExtensions.cs:9` |
| **Tags** | scalability-risk |

**Problem**
`GetAllData` includes `ArticleFavorites` and `ArticleTags` (two one-to-many collections) in one query. Each article therefore returns max(1, favorites) × max(1, tags) rows, each repeating every Article column including `Body`. Favorites are loaded only to compute a count and a boolean. The list then sets `Body = null` on every article it loaded.

**Performance principle**
Fetch only what the response needs, and compute aggregates in the datastore rather than by materializing the rows they summarize.

**Evidence**
- `ArticleExtensions.cs:10-14`: three `Include`s, no `AsSplitQuery`, and no `UseQuerySplittingBehavior` anywhere in `src`.
- `Domain/Article.cs:29-36`: `Favorited`/`FavoritesCount` are derived from the loaded list.
- `List.cs:114-117` nulls Body after loading it.
- Used by list, feed, details, edit, and favorite add/delete (`Favorites/Add.cs:69-71`, `Delete.cs:57-59`, `Edit.cs:140-143`, `Details.cs:28-30`).
- *Derivation:* rows per list page = Σ over the page's articles of max(1, Fᵢ) × max(1, Tᵢ), each carrying Body.

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in favorites per article (multiplied by tags), times page size. Blast radius: service-wide, and longer statements extend PERF-001's lock.

**Conditions**
Matters once popular articles accumulate favorites (at least tens, assumed) or carry several tags. The favorites distribution is unknown (Q2). The page size is uncapped (PERF-002).

**Counter-evidence**
- No global split query, projection, or count column exists. *(no-effect)*
- Tags per article are author-supplied and usually few, so growth is mostly linear in favorites. *(bounds-impact: scored High, not Critical)*
- Favorites join on the composite key `(ArticleId, PersonId)`, so the cost is rows, not lookups. *(bounds-impact)*

**Why this might not matter**
With a few favorites and tags per article, a page is tens of rows and fast.

**Recommendation**
Project to the response shape: author fields, `COUNT` of favorites, `EXISTS` for "favorited by the current user" (which also fixes COR-001), and tag ids. Exclude Body from list projections.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Projection with COUNT/EXISTS, no Body in lists | **preferred** | Removes per-favorite rows and fixes COR-001 |
| `AsSplitQuery()` | | One-line interim fix for the multiplication; still loads every favorite; adds round trips |
| Denormalized favorites count | | Hot-row writes under the single writer; consistency burden; unjustified without data |

**Trade-offs**
Needs a response DTO instead of serializing entities, which moves some code.

**Validation**
See §9.

---

### PERF-004: No index on Slug, Username, Email, or CreatedAt, which every request filters or sorts by

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/Conduit/Infrastructure/ConduitContext.cs:21` |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The model declares no secondary indexes. Every `/articles/{slug}` route looks up by `Slug`. Every authenticated handler resolves the current user by `Username`, often 2-3 times per request (feed, ProfileReader, follow add/delete). Login and registration look up by `Email`. The list sorts by `CreatedAt` and then pages. None of these predicates has a supporting index, so their cost is proportional to table size.

**Performance principle**
A point lookup or ordered top-N read on a growing table needs an access path proportional to the result, not the table.

**Evidence**
`ConduitContext.cs:21-91` contains keys and relationships only. A repo-wide search finds no `HasIndex`/`[Index]`/`IsUnique`. `Program.cs:124` uses `EnsureCreated`, with no migrations or raw `CREATE INDEX`. Lookups: `Articles/Details.cs:30`, `ProfileReader.cs:26,39`, `Articles/List.cs:40,106`, `Users/Login.cs:49`, `Users/Create.cs:50,59`. No query plan was available: this is "no supporting index", not an observed scan.

**Impact**
Position: critical path. Frequency: per request (several per request for username). Growth: O(n) in Persons/Articles. Blast radius: system-wide through PERF-001's lock.

**Conditions**
Matters as Persons and Articles grow. There is no retention, so growth is expected, but counts are unknown (Q2). At a few hundred rows a scan is fine.

**Counter-evidence**
- No migrations, raw SQL indexes, or unique constraints exist. *(no-effect)*
- EF creates FK indexes and composite keys lead with `ArticleId`/`ObserverId`, so id-based joins are covered. Only these non-key columns are not. *(bounds-impact)*
- Table sizes are unknown. *(lowers-confidence → Medium)*

**Why this might not matter**
At demo scale SQLite scans are fast, and indexes would add write cost for no visible gain.

**Recommendation**
Unique indexes on `Person.Username`, `Person.Email`, and `Article.Slug` (which also closes COR-002), and an index on `Article.(CreatedAt, ArticleId)` for the list order. Adopt migrations first (MAINT-002), because `EnsureCreated` will not alter an existing file. Run `ANALYZE` afterwards, since SQLite has no background statistics.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Unique + sort indexes via migrations | **preferred** | Seeks instead of scans; enforces uniqueness |
| Resolve current user once per request | | Cuts repeated lookups; complementary, not a substitute |
| In-memory person cache | | Invalidation burden for a lookup an index makes cheap |

**Trade-offs**
Extra write work per insert/update under the single writer; larger file. Unique index creation fails if duplicates already exist, so check the data first.

**Validation**
See §9.

---

### PERF-005: No performance telemetry exists

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | observability |
| **Location** | `src/Conduit/Program.cs:103` |
| **Tags** | quick-win |

**Problem**
There are no request-duration metrics, traces, DB command or lock-wait metrics, or exported runtime counters. No P1 finding can be confirmed or validated in a deployed environment.

**Performance principle**
What is not measured cannot be tuned; measure the critical path before optimizing it.

**Evidence**
`Conduit.csproj` has no OpenTelemetry/metrics/tracing packages. `Program.cs:107-118` has no diagnostics endpoint. There are no load tests, benchmarks, dashboards, SLOs, or alerts in the repository; CI runs functional suites only (`.github/workflows/*.yml`).

**Impact**
Critical path, per request, O(1), system-wide: it limits every future optimization decision.

**Conditions**
Any traffic level; this is a repository property.

**Counter-evidence**
Hosting logs "Request finished ... in N ms" at Information to the console, a crude unaggregated timing source *(bounds-impact)*. `dotnet-counters` works without code changes *(bounds-impact)*.

**Why this might not matter**
A demo app with no production users has no one to read the telemetry.

**Recommendation**
Add OpenTelemetry metrics and traces for ASP.NET Core and EF Core, plus a histogram of transaction-begin wait in the pipeline behaviour (the direct PERF-001 signal). Sequence this before the P1 changes.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| OpenTelemetry + lock-wait histogram | **preferred** | Covers request, query, and lock signals the P1 findings need |
| `dotnet-counters` + console timing | | No code, but ad hoc; cannot back alerts or guards |

**Trade-offs**
Small per-request overhead, a collector to operate, route-label cardinality.

**Validation**
See §9.

---

### PERF-006: Exact synchronous COUNT over the full filtered set on every list request

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/Conduit/Features/Articles/List.cs:133` |
| **Tags** | scalability-risk |

**Problem**
`ArticlesCount = queryable.Count()` runs a second statement that traverses every matching article on every list request, through the synchronous API inside an async handler.

**Performance principle**
A per-request total should not cost a full traversal, and synchronous I/O should not run on an async request thread.

**Evidence**
`List.cs:133`. The unfiltered home-page list counts every article.

**Impact**
Critical path, per request, O(n) in matching articles, service blast radius.

**Conditions**
Articles growth (unknown, Q2). The blocking aspect matters only on a provider with real async I/O (Q3).

**Counter-evidence**
Microsoft.Data.Sqlite documents its async methods as executing synchronously, so on the configured provider `Count()` blocks no more than `CountAsync()` would *(bounds-impact)*. `COUNT(*)` on SQLite reads the narrowest B-tree *(bounds-impact)*. `articlesCount` is required by the contract *(no-effect)*.

**Why this might not matter**
On SQLite at moderate size this is cheap, and the sync/async difference is nil on this provider.

**Recommendation**
Use `CountAsync(cancellationToken)` now. If Articles grows, make the count index-supported, or cap it (offset + limit + 1) if the frontend contract allows.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| `CountAsync` now; bounded or index-supported count later | **preferred** | Free now; defers the contract question until data justifies it |
| Cached total | | Invalidation on every create and delete |

**Trade-offs**
A capped count changes the meaning of `articlesCount`.

**Validation**
See §9.

---

### PERF-007: Follow-graph membership answered by loading the whole follow lists

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/Conduit/Features/Profiles/ProfileReader.cs:36` |

**Problem**
To decide "does the current user follow X", `ProfileReader` loads the current user with **both** follow collections (`Following` is never used) as tracked entities. The feed loads all followees, sends their ids back as a parameter list, then queries the followed ids again a few lines later.

**Performance principle**
Answer a membership question with an existence query, and do not fetch the same set twice per request.

**Evidence**
`ProfileReader.cs:36-49` (used by `GET /profiles/{username}` and follow/unfollow via `Followers/Add.cs:72`, `Delete.cs:64`). Feed: `List.cs:37-50` and again `List.cs:123-126`.

**Impact**
Critical path, per request, O(n) in the viewer's follow edges, endpoint blast radius.

**Conditions**
Accounts with hundreds or more follow edges (assumed; Q6).

**Counter-evidence**
Loads are index range scans (`ObserverId` key, `TargetId` FK index) *(bounds-impact)*. Typical accounts have few follows *(bounds-impact)*.

**Why this might not matter**
Small follow graphs make this a few rows, invisible next to PERF-004.

**Recommendation**
Use `FollowedPeople.AnyAsync(f => f.ObserverId == me && f.TargetId == them)`. Express the feed filter as a server-side subquery and reuse it for `author.following`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| `AnyAsync` + server-side feed subquery | **preferred** | Constant work per profile view; one fewer feed statement |
| Drop `Include(Following)` and add `AsNoTracking` | | Halves the waste; still loads all followees |

**Trade-offs**
Negligible.

**Validation**
See §9.

---

### PERF-008: Comment create and delete load the article's whole comment collection

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/Conduit/Features/Comments/Create.cs:36` |
| **Tags** | quick-win |

**Problem**
Adding a comment loads every comment on the article (tracked) in order to append one. Deleting one loads every comment plus its author in order to find one by id. Both do work proportional to comment count, inside the request transaction.

**Performance principle**
A single-row write should touch a constant number of rows.

**Evidence**
`Comments/Create.cs:36-38,59`; `Comments/Delete.cs:27-35`. No comment retention or per-article limit.

**Impact**
Critical path, per request, O(n) in comments per article. Blast radius system-wide only through PERF-001; otherwise endpoint.

**Conditions**
Articles with many comments (unknown, Q2).

**Counter-evidence**
FK index on `Comments.ArticleId` limits the read to one article's comments *(bounds-impact)*. Read/write ratio is unknown, and writes are probably rarer *(lowers-confidence)*.

**Why this might not matter**
Most articles have few comments, and comment writes are rare.

**Recommendation**
Create: resolve the article id by slug and insert with `ArticleId`. Delete: query the one comment by `(ArticleId, CommentId)` with its `AuthorId`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Key-based single-row read/insert | **preferred** | Constant work; local change |
| `ExecuteDeleteAsync` with author predicate | | Fewer round trips but loses the 404 vs 403 distinction |

**Trade-offs**
None material.

**Validation**
See §9.

---

### PERF-009: Article create does one round trip (and one commit) per tag and probes slugs one at a time

| | |
|:--|:--|
| **Root cause** | `ROOT-009` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/Conduit/Features/Articles/Create.cs:59` |
| **Tags** | quick-win |

**Problem**
Each tag is a separate `FindAsync`, and each new tag a separate `SaveChangesAsync`. The unique slug is found by probing `slug`, `slug-1`, `slug-2`... one unindexed query at a time (also on title edit, `Edit.cs:95`). Slugs are truncated to 45 characters (`Slug.cs:20`), which makes collisions likelier.

**Performance principle**
Batch per-item work into set-based statements; resolve uniqueness in one query.

**Evidence**
`Create.cs:59-70` and `75-82`; `Edit.cs:95-106`; no `tagList` length rule in `ArticleDataValidator` (`Create.cs:28-36`). *Derivation:* statements = 1 + t + new-tag commits + (k+1) + 1.

**Impact**
Critical path, per item, O(n) in tags and collisions. Blast radius system-wide through PERF-001.

**Conditions**
Long tag lists or many title collisions, neither bounded. Assumed rare in typical use.

**Counter-evidence**
`FindAsync` checks the change tracker first, so repeated identical tags don't re-query *(bounds-impact)*. No `tagList` cap was found *(no-effect)*.

**Why this might not matter**
A few tags and rare collisions mean a few extra in-process SQLite calls per create.

**Recommendation**
Fetch existing tags in one `Contains` query, add the missing ones, save once. Resolve the slug suffix from one query over the stem, backed by PERF-004's unique index. Cap `tagList` in the validator.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Set-based tags, single save, one-query slug, tagList cap | **preferred** | Constant round trips |
| Random or id suffix on every slug | | No probing, but a visible slug-format change |

**Trade-offs**
The `tagList` cap is a contract limit to document.

**Validation**
See §9.

---

### PERF-010: Every request and every SQL statement is logged to two console sinks

| | |
|:--|:--|
| **Root cause** | `ROOT-010` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io |
| **Location** | `src/Conduit/ServicesExtensions.cs:113` |
| **Tags** | quick-win |

**Problem**
With no appsettings or level filter, the framework default (Information) applies. Hosting and MVC request messages and every EF Core "Executed DbCommand" message (with SQL text) go to the console twice: once through the default console provider, and once through a Serilog themed console sink marked "just for local debug" that is attached unconditionally.

**Performance principle**
Fixed per-request work should be proportional to its value; statement-level logging in production is I/O and contention paid on every request.

**Evidence**
`ServicesExtensions.cs:110-125`; `Program.cs:105`; no `appsettings*.json` in the repository. *Derivation:* lines per request = framework messages + one per SQL statement (5 on a filtered feed), × 2 sinks.

**Impact**
Critical path, per request, O(1) per statement, service blast radius (console stream shared by all requests).

**Conditions**
Assumes nothing outside the repository raises the level (Q5). Cost scales with request rate.

**Counter-evidence**
The Microsoft console provider writes from a background queue; only the Serilog sink writes on the request thread *(bounds-impact)*. Environment-variable overrides could exist at deploy time *(lowers-confidence)*.

**Why this might not matter**
At low rates a few console lines per request are cheap, and useful for a demo.

**Recommendation**
Add `appsettings.json` with `Microsoft.AspNetCore` and `Microsoft.EntityFrameworkCore` at Warning, and attach the Serilog console sink only in Development.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Level filters + Development-only sink | **preferred** | Configuration-only; removes volume and duplication |
| Async Serilog sink | | Off the request thread, but keeps the volume and ingestion cost |

**Trade-offs**
Less production log detail, and SQL text no longer appears in logs (also a data-exposure gain).

**Validation**
See §9.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-011 | Low | Medium | P3 | `src/Conduit/Features/Comments/List.cs:22` | Comment list returns every comment with its author, unbounded. Mandated by the RealWorld contract, index-supported; revisit if any article's comment count grows large (optional paging or a projection) |
| PERF-012 | Low | Medium | P3 | `src/Conduit/Features/Tags/List.cs:22` | `GET /api/tags` returns the whole Tags table, which only grows: orphaned tags are never deleted on article edit or delete. Return in-use tags or delete orphans; do not cache without a measurement |

PERF-011 and PERF-012 carry full Problem / Evidence / Conditions / Counter-evidence / Recommendation / Trade-offs / Validation entries in the JSON (§10).

### Considered and not reported

| Candidate | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| SQL Server branch: sync `Count()` and sync transaction Begin/Commit would block thread-pool threads | Every request | `Program.cs:15-45`; compose env vars never read | Unreachable: provider hard-coded to sqlite | Provider becomes configurable or SQL Server is deployed |
| `AddDbContext` (not pooled) | Every request | `Program.cs:26`, `ServicesExtensions.cs` | Small constant cost; no evidence it is material | A CPU profile shows context construction as a visible share |
| Password hashing on the request path | Login / register / user edit | `PasswordHasher.cs`, `Login.cs` | One HMAC is trivially cheap; this is a security issue instead (SEC-001) | SEC-001 is fixed with a slow KDF. Then login CPU, and its placement inside the PERF-001 transaction, matter |

### Adjacent findings: outside performance scope

### SEC-001: Passwords stored with a single fast, keyed HMAC and compared in non-constant time

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `src/Conduit/Infrastructure/Security/PasswordHasher.cs:11` |

**Problem** Password hashes are one HMAC-SHA512 over password+salt, keyed with a constant in source. That is a fast hash, not a password KDF. Verification uses `SequenceEqual` (`Login.cs:61`).
**Evidence** `PasswordHasher.cs:11-21`; `Features/Users/Login.cs:56-64`.
**Impact** A leaked database is cheap to brute-force offline, because the per-guess cost is one HMAC.
**Recommendation** Use ASP.NET Core Identity's `PasswordHasher<T>` or another slow, salted KDF (PBKDF2 with high iterations, Argon2, bcrypt). Compare with `CryptographicOperations.FixedTimeEquals`. Rehash on next login.
**Trade-offs** A deliberately slow KDF adds CPU to every login and registration. If the PERF-001 transaction still wraps commands, keep the hash computation outside it.
**Validation** Unit test that stored hashes use the new format; a security review.
**Would need** A dedicated security review of authentication and credential storage.

### SEC-002: JWT signing key hard-coded in source

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `src/Conduit/ServicesExtensions.cs:48` |

**Problem** The symmetric signing key (value not reproduced here), issuer, and audience are string literals.
**Evidence** `ServicesExtensions.cs:48-53`.
**Impact** Anyone with repository access can mint valid tokens for any user on any deployment built from this code.
**Recommendation** Load the key from configuration or a secret store, fail fast if it is missing, and rotate the key on any existing deployment.
**Trade-offs** One more secret to manage per environment.
**Validation** Startup fails without a configured key; the token issued in one environment is rejected by another.
**Would need** A security review and secret scanning across repository history.

### COR-001: `favorited` reflects whether anyone favorited, not the current user

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/Conduit/Domain/Article.cs:29` |

**Problem** `Favorited => ArticleFavorites.Count != 0` never references the current user.
**Evidence** `Domain/Article.cs:29`.
**Impact** Every viewer sees "favorited" on any article with at least one favorite. This is a visible API contract error.
**Recommendation** Compute it as an `EXISTS` for the current user in the query (PERF-003's projection does this).
**Trade-offs** None.
**Validation** Two-user spec test checking the flag from the non-favoriting user's view.
**Would need** A correctness-focused API test pass.

### COR-002: Username, Email, and Slug uniqueness is check-then-insert with no unique constraint

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Medium |
| **Location** | `src/Conduit/Features/Users/Create.cs:48` |

**Problem** Concurrent registrations or creates can both pass the `AnyAsync` check. A duplicate Email then makes Login's `SingleOrDefaultAsync` (`Login.cs:50`) throw, which is a 500 for that user.
**Evidence** `Users/Create.cs:48-64`; `Users/Edit.cs:141-167`; `Articles/Create.cs:75-82`; no unique index (`ConduitContext.cs:21-91`).
**Impact** Medium. On SQLite the race is currently hidden by PERF-001's request-wide serialization, and fixing PERF-001 alone would expose it.
**Recommendation** Add unique indexes (PERF-004) and map constraint violations to the existing 409/422 responses.
**Trade-offs** Existing duplicates block index creation; check the data first.
**Validation** Parallel registration test with the same email.
**Would need** A concurrency-focused correctness test.

### MAINT-001: Database configuration hard-coded; SQLite file ephemeral inside the container

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/Conduit/Program.cs:21` |

**Problem** Provider and connection string are constants. The env vars compose passes are never read. The database file sits in the container's writable layer with no volume.
**Evidence** `Program.cs:12-24`; `docker-compose.yml`.
**Impact** Data is lost when the container is recreated, and replicas would each get their own divergent database.
**Recommendation** Read provider and connection string from configuration. Mount a local (not network) volume for the file. Document that the SQLite deployment is single-instance.
**Trade-offs** Volume management.
**Validation** Data survives `docker compose down && up`.
**Would need** A deployment and configuration review.

### MAINT-002: Schema managed by `EnsureCreated()` with no migrations

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/Conduit/Program.cs:124` |

**Problem** `EnsureCreated` never alters an existing database, so schema changes, including PERF-004's indexes, cannot reach a deployed file.
**Evidence** `Program.cs:120-126`; no migrations in the repository.
**Impact** Every schema-level performance fix is blocked until this changes.
**Recommendation** Create a baseline migration matching the current model, then apply migrations as a deploy step.
**Trade-offs** Migration tooling and a baseline step for existing databases.
**Validation** `dotnet ef migrations list` shows the baseline; an index migration applies to an existing file.
**Would need** A schema-management and release-process review.

---

## 8. Prioritized action plan

There are no P0 findings. P1: PERF-001, -002, -003, -004. P2: PERF-005 to PERF-010. P3: PERF-011, PERF-012.

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-005 | P2 | Small | **Sequenced early despite P2:** cheap, and without it none of the P1 changes can be measured in a deployed environment |
| 2 | PERF-002 | P1 | Small | Quick-win; removes the unbounded request that makes every other read finding worse |
| 3 | MAINT-002 → PERF-004 (+COR-002) | P1 | Medium | Migrations are a prerequisite for indexes; unique indexes must land before or with PERF-001 so narrowing the lock does not expose duplicate-row races |
| 4 | PERF-001 | P1 | Medium | Biggest structural constraint; commands-only transactions + WAL |
| 5 | PERF-003 (+COR-001) | P1 | Medium | Projection removes row multiplication and fixes the favorited flag |
| 6 | PERF-010 | P2 | Small | Configuration-only quick-win |
| 7 | PERF-006, PERF-007, PERF-008, PERF-009 | P2 | Small each | Local query rewrites |
| 8 | PERF-011, PERF-012 | P3 | Small | Only if the measurements show growth |

Security items SEC-001 and SEC-002 are outside this ranking and should be scheduled by a security review, not by performance priority.

**If only one thing is done:** PERF-001. Stop holding a transaction (and with it the SQLite lock) for the whole of every read request, and enable WAL.

---

## 9. Validation plan

All commands assume a local instance started with `make run-local` (listens on `http://localhost:5000`, database at `src/Conduit/realworld.db` per the `Makefile`) seeded with representative data. None of them should be pointed at production unless labelled `safe-on-production`.

### PERF-001

- **Baseline:** latency distribution of `GET /api/articles` at 1 and 16 concurrent clients, local. *(not-safe-on-production: generates load)*
- **Change:** transaction behaviour for commands only; WAL enabled at startup.
- **Measurement:** p50/p95 at each concurrency level; once PERF-005 exists, the transaction-begin wait histogram.
- **Expectation:** before the change, latency grows roughly with concurrency (serialization); after it, concurrent reads stay near the single-client latency until CPU or disk saturates. No magnitude is claimed.
- **Falsifier:** if 16-way latency already matches 1-way latency before the change, requests do not serialize and PERF-001 is overstated.
- **Guard:** CI concurrency smoke test against a SQLite-backed instance.
- **Commands:**
  - `safe-on-production` `sqlite3 src/Conduit/realworld.db "PRAGMA journal_mode;"`: confirm the journal mode (expected `delete`)
  - `not-safe-on-production` `seq 400 | xargs -P 16 -I{} curl -s -o /dev/null -w "%{time_total}\n" http://localhost:5000/api/articles | sort -n | awk '{a[NR]=$1} END {print "p50",a[int(NR*0.5)],"p95",a[int(NR*0.95)]}'`: concurrency comparison (repeat with `-P 1`)

### PERF-002

- **Baseline / Measurement:** response size of `GET /api/articles?limit=100000`.
- **Expectation:** after the change, at most the configured maximum number of articles.
- **Falsifier:** a large limit already returns at most the default page.
- **Guard:** integration test for the cap.
- **Commands:** `not-safe-on-production` `curl -s "http://localhost:5000/api/articles?limit=100000" | wc -c`

### PERF-003

- **Measurement:** rows and SQL shape of the list query, from EF's Information-level command log.
- **Expectation:** one row per article instead of Σ favorites × tags.
- **Falsifier:** the logged SQL already uses split queries or returns one row per article.
- **Guard:** SQLite-backed test seeding many favorites and asserting command and row count.
- **Commands:** `not-safe-on-production` `make run-local 2>&1 | tee run.log` (start and capture); `safe-on-production` `grep -c "Executed DbCommand" run.log`

### PERF-004

- **Measurement:** `EXPLAIN QUERY PLAN` for each lookup and for the list order.
- **Expectation:** `SCAN` becomes `SEARCH ... USING INDEX`; the list order no longer needs `USE TEMP B-TREE FOR ORDER BY`.
- **Falsifier:** plans already show index use.
- **Guard:** model test asserting the indexes exist.
- **Commands:**
  - `safe-on-production` `sqlite3 src/Conduit/realworld.db ".indexes"`
  - `safe-on-production` `sqlite3 src/Conduit/realworld.db "EXPLAIN QUERY PLAN SELECT * FROM Persons WHERE Username = 'x' LIMIT 1;"`
  - `safe-on-production` `sqlite3 src/Conduit/realworld.db "EXPLAIN QUERY PLAN SELECT * FROM Articles ORDER BY CreatedAt DESC, ArticleId DESC LIMIT 20;"`

### PERF-005

- **Expectation:** per-route p50/p95/p99 and DB command duration become available.
- **Falsifier:** an external APM agent already instruments the deployed process.
- **Commands:** `safe-on-production` `dotnet-counters monitor --process-id <pid> --counters System.Runtime,Microsoft.AspNetCore.Hosting` (interim view)

### PERF-006 to PERF-010

| Finding | Measurement | Expectation | Falsifier | Command (safety) |
|:--|:--|:--|:--|:--|
| PERF-006 | COUNT statement duration vs Articles size | Flat once index-supported or capped | Already flat as data grows | `grep "SELECT COUNT(\*)" run.log` (safe) |
| PERF-007 | Rows/statements for a profile view by a heavily-following user | Constant; feed loses one statement | No collection join in logged SQL | `grep -A3 "FollowedPeople" run.log` (safe) |
| PERF-008 | Rows read by comment create on a many-comment article | Constant | No Comments join in logged SQL | `grep -B1 -A6 'FROM "Comments"' run.log` (safe) |
| PERF-009 | Statements for article create with t tags, k collisions | Constant (about 4) | Already constant | `grep -c "Executed DbCommand" run.log` (safe) |
| PERF-010 | Log lines per request in container stdout | Near zero on success paths | No `Executed DbCommand` lines in deployed stdout | `docker compose logs conduit \| grep -c "Executed DbCommand"` (safe) |

### Instrumentation gaps to close first

The repository is uninstrumented. Add PERF-005's request-duration and DB-command metrics and the transaction-begin wait histogram **before** changing PERF-001/003/004, so each change has a before and after measurement.

---

## 10. Machine-readable output

Emitted alongside this report at `out/aspnetcore-guided-1.json`, conforming to the skill's `schemas/review.schema.json`. `python scripts/validate_review.py` reports it as a valid review (12 findings). Stable IDs were computed with `scripts/compute_stable_id.py`. The JSON contains the same findings, root causes, workload inputs (including the six asked-but-unanswered questions), considered-not-reported entries, adjacent findings, and validation commands as this report. If the two ever disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade. None is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated or assumed. The row-multiplication and statement-count figures are derivations from code, labelled as such with their inputs.
- One technology-specific claim (the SQLite lock mode Microsoft.Data.Sqlite takes for `ReadCommitted` transactions) is not verifiable from the repository. It is flagged where used and lowers PERF-001's confidence.
- No secret values are reproduced; the hard-coded signing key is referenced by location only.
