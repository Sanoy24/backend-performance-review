# Performance Review: spring-boot-realworld

**Date:** 2026-10-08
**Mode:** Full review (commit `ee17e31aafe733d98c4853c8b9a74d7f2f6c924a`)
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0

---

## 1. Decision summary

### Overall assessment

The service is a small, thread-per-request Spring Boot 2.6 + MyBatis app over a single SQLite file, exposing the same domain through REST and an anonymous GraphQL endpoint. The code is clean and shallow. Its performance risk is all in the data layer: list queries multiply rows before collapsing them, GraphQL resolvers issue queries per item with no batching or depth limit, and the schema has no secondary indexes. All three get worse as the data grows, and they all share one single-writer SQLite file. No runtime evidence exists (no metrics, plans, or load tests), and nobody was available to answer workload questions. So every workload-dependent finding is capped at **Medium** confidence. The ranking could move once row counts and the production datastore are known (see Key unknowns). **Review confidence: Medium.** I read all of `src/main`, but the ranking rests on code structure alone.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-002 (P1)** | Bound GraphQL: add depth/complexity limits and lower the 1000-item connection cap now; then replace per-item `author`/`article` lookups with parent data + DataLoaders | Anonymous callers can request nested lists that multiply queries; the limits are a small, fast change |
| 2 | **PERF-001 (P1)** + **PERF-003 (P1)** | Rewrite `queryArticles`/`countArticle` so they join only for the filters actually supplied, and add a V2 migration with indexes on the columns these queries filter and sort by | This is the home-page path. Its cost grows with articles × tags × favorites. The new indexes also speed up every follow, comment, and feed lookup |
| 3 | **PERF-004 (P1)** | Add `LIMIT #{page.queryLimit}` to `findByArticleIdWithCursor` (also fixes COR-001) | One-line fix that turns an unbounded read into a bounded one |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Is SQLite the production store, and how many processes share the file? | Yes + concurrent writes: PERF-005 rises to P1. No: PERF-005 is withdrawn | Operator answer; `PRAGMA journal_mode` on the deployed file |
| Row counts and the most tags, favorites, or comments on a single article | Small tables bound PERF-001/003/004 (they drop to P2). Large tables confirm them | `SELECT count(*)` per table on a copy of the database |
| Is `/graphql` used by production clients, and is it public? | If it is blocked at the edge, PERF-002 drops to P2 | Edge/proxy config; access logs |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | `safe-on-production` | `sqlite3 dev.db "EXPLAIN QUERY PLAN SELECT DISTINCT(A.id) articleId, A.created_at FROM articles A LEFT JOIN article_tags AT ON A.id = AT.article_id LEFT JOIN tags T ON T.id = AT.tag_id LEFT JOIN article_favorites AF ON AF.article_id = A.id LEFT JOIN users AU ON AU.id = A.user_id LEFT JOIN users AFU ON AFU.id = AF.user_id ORDER BY A.created_at DESC LIMIT 0, 20;"` | Temp B-trees and SCANs confirm the fan-out-and-sort mechanism |
| PERF-003 | `safe-on-production` | `sqlite3 dev.db ".indexes"` | Whether any index exists outside the migration |
| PERF-005 | `safe-on-production` | `sqlite3 dev.db "PRAGMA journal_mode;"` | Rollback-journal vs WAL decides how readers and writers block each other |

---

## 2. Scope and method

**Reviewed:** I read all of `src/main/java` (REST controllers, GraphQL data fetchers, application services, repositories, the security filter) and all of `src/main/resources` (MyBatis mapper XML, GraphQL schema, the Flyway migration, application properties), plus `build.gradle`, the CI workflow, and the README.
**Not reviewed:** I listed the test sources and searched them for query-count assertions (there are none), but did not read them line by line. No deployment manifests exist. I did not open `.env`, key, or credential files (none present).

**Evidence available:** Uninstrumented. There is no actuator, Micrometer, tracing, benchmark, load test, query plan, or SLO in the repository. The only diagnostic is MyBatis DEBUG statement logging.

**Ranking method:** Structural signals only. The ranking is inference from code, not measurement.

**Reference depth:** SQLite, JVM, GraphQL, and REST have deep support. MyBatis has no engine-specific reference. I analyzed it with generic data-access principles and read the actual SQL from the mapper XML, so no ORM-generated SQL had to be guessed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All production source and configuration; tests only scanned |
| **Critical paths** | 12 / 12 |
| **Shared resources** | 5 / 5 (SQLite file + its single-writer lock, JDBC connection pool, servlet worker threads, statement-timeout configuration, logging output) |
| **Technology support** | SQLite deep, JVM deep, GraphQL deep, REST deep, MyBatis generic |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence is not finding confidence. I read everything that matters, but without data volumes I cannot tell whether today's tables are large enough for the growth findings to hurt *now*.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Production datastore and number of processes on the SQLite file | No evidence exists | Deployment config or operator answer |
| Row counts and per-article fan-out | No evidence exists | Row counts on a database copy |
| Query plans | No evidence exists | `EXPLAIN QUERY PLAN` per mapper statement |
| Request rates, latency percentiles, pool wait time | No evidence exists | Actuator + Micrometer (PERF-008) |
| Whether sqlite-jdbc 3.36 enforces `Statement.setQueryTimeout` | Not examined | Staging test with a slow statement |
| MyBatis execution internals | Technology unsupported | Generic data-access analysis only; mapper SQL was read directly |

---

## 3. Architecture overview

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| HTTP API | Spring Boot web (servlet, thread-per-request) | 2.6.3 | deep (REST) | REST endpoints under `/articles`, `/profiles`, `/tags`, `/user(s)` |
| GraphQL API | Netflix DGS | 4.9.21 | deep | `/graphql`, anonymous (`permitAll`) |
| Data access | MyBatis spring-boot starter | 2.2.2 | generic | XML mappers, CQRS-style read services |
| Datastore | SQLite via `org.xerial:sqlite-jdbc` | 3.36.0.3 | deep | Single file `dev.db` (relative path) |
| Migrations | Flyway | Boot-managed | — | One migration, `V1__create_tables.sql` |
| Runtime | JVM | Java 11 | deep | — |

No cache, broker, outbound HTTP calls, or container/orchestration config exists. Those layers are omitted.

**Shared resources:** the SQLite database file (one writer at a time), the JDBC connection pool (framework default; size not configured), servlet worker threads (default; not configured), and the per-statement logging output.

---

## 4. Workload model

**Known**

- SQLite file datastore, no pool, `journal_mode`, or `busy_timeout` settings — source: `application.properties:1-4`, `build.gradle:43`
- Statement timeout `3000`, which MyBatis reads as seconds — source: `application.properties:13`
- REST page limit is capped at 100 and the offset is unbounded — source: `Page.java:9,18`. GraphQL page cap is 1000 — source: `CursorPageParameter.java:10`
- Comment queries have no LIMIT — source: `CommentReadService.xml:20-41`
- No secondary indexes, and `follows` has no key — source: `V1__create_tables.sql`
- `/graphql` is anonymous; there is no DataLoader and no depth/complexity limit — source: `WebSecurityConfig.java:53`, plus a search of `src/main`
- Every table is append-only with no retention; deleting an article leaves its dependent rows behind — source: `V1__create_tables.sql`, `ArticleMapper.xml:32`
- The API is read-heavy: 6 REST GET routes and 6 GraphQL query fields, against mutations that issue only a few writes each — source: `api/`, `schema.graphqls`

**Assumed**

- Tables grow without bound — affects PERF-001, PERF-003, PERF-004, PERF-012
- Production runs the SQLite file as configured, in a single JVM — affects PERF-005, PERF-006
- GraphQL is reachable by clients as configured — affects PERF-002
- Some articles collect several tags and many favorites — affects PERF-001

**Unknown**

- Production datastore and process count; row counts; request and write rates; whether GraphQL is used; current latency.

**Derived**

- An unfiltered list materializes Σ over articles of max(1, tags) × max(1, favorites) joined rows before DISTINCT/ORDER BY — from the two independent LEFT JOINs on `A.id` (`ArticleReadService.xml:32-34`)
- That join runs twice per REST list request — from `ArticleQueryService.java:102-103`
- An authenticated GraphQL `articles(first:N){…author…}` costs the list queries plus 2 × N author queries, with N ≤ 1000 — from `ProfileDatafetcher.java:37-42`, `ProfileQueryService.java:19-31`, `CursorPageParameter.java:10`
- A statement timeout of 3000 s is 50 minutes — from `application.properties:13` and MyBatis's documented unit

**Measured**

- None. No benchmark, trace, plan, or metric was supplied or exists in the repository. That is why no finding is `Confirmed`.

### Questions that would change the ranking

Nobody was available to answer these. I have recorded them as unknowns and continued under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is SQLite the production datastore, and how many processes/instances open the file? | severity, recommendation | PERF-005 rises to P1 or is withdrawn. PERF-003's blast radius changes |
| 2 | highest | Row counts per table; the most tags, favorites, or comments on one article | severity, confidence | Small counts bound PERF-001/003/004 down to P2. Large counts confirm them |
| 3 | high | Is `/graphql` used in production and publicly reachable? | severity, confidence | Internal or disabled drops PERF-002 to P2 |
| 4 | high | Peak rate for GET `/articles` and `/articles/feed`; write rate | severity | Separates a current bottleneck from a growth risk for PERF-001/005/007 |
| 5 | high | Is there a specific complaint (slow page, `SQLITE_BUSY`, incident)? | recommendation | Would re-center the review on that symptom |
| 6 | medium | Most comments on one article; most follows for one user | severity | Counts in the low tens bound PERF-004/007 |

---

## 5. Critical path analysis

Ranked by structural signals; no runtime data.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | GraphQL `articles` / `feed` / `profile{articles,favorites,feed}` with nested `author`, `comments`, `comment.article` | yes | list queries + 1–2 per article (author) + 1 per article (comments, unbounded) + 1–4 per comment (`author`, `article`) | per level ≤ 1000; nesting not limited | no | PERF-002, PERF-004 |
| 2 | GET `/articles` (home page, anonymous) | yes | id query (6-table join, DISTINCT, sort) + same join for count + article data + favorite counts (+ 2 per-viewer queries) | limit ≤ 100; offset unbounded | no | PERF-001, PERF-003 |
| 3 | GET `/articles/{slug}/comments` | yes | article by slug + all comments (table scan) + one followingAuthors | no | no | PERF-004 |
| 4 | GET `/articles/feed` | yes | all followed ids + IN-list article query + extras + IN-list count | page ≤ 100; IN list unbounded | no | PERF-007 |
| 5 | GET `/articles/{slug}` | yes | article join + 3 scalar queries when authenticated | yes | no | PERF-010 |
| 6 | GET `/profiles/{username}` | yes | user by username + follows scan | yes | no | PERF-003 |
| 7 | POST `/articles` | yes | validator (full article read) + per-tag findTag/insertTag/insertRelation + insert + re-read | tag count unbounded | no | PERF-011 |
| 8 | POST/DELETE `/articles/{slug}/favorite` | yes | find + insert/delete + full re-read | yes | no | PERF-010 |
| 9 | POST `/articles/{slug}/comments` | yes | article + insert + comment read + follows scan | yes | no | — |
| 10 | POST/DELETE `/profiles/{u}/follow` | yes | user + follows scan + insert/delete + profile read | yes | no | PERF-003 |
| 11 | GET `/tags` | yes | full tags table | no | no | PERF-013 |
| 12 | Auth filter (every request with token) + login/register | yes | 1 PK lookup; BCrypt on login | yes | no | Considered, not reported |

**Amplification points:** GraphQL list fields fan out to per-item resolvers, and nesting multiplies them (PERF-002). The article id query multiplies each article by tags × favorites (PERF-001). Each unindexed lookup costs a scan of a growing table, and the per-item resolvers multiply those scans (PERF-003 × PERF-002).

**Paths deliberately not analyzed in depth:** user update and registration, which issue a few point lookups and writes on unique-indexed columns. There are no background jobs or consumers.

---

## 6. Layer analysis

### 6.1 Application
Thread-per-request servlet model with no async code, so blocking JDBC calls are the expected model and not a finding. Per-request work is dominated by sequential datastore statements. CPU-heavy work is limited to BCrypt on login and registration, which is deliberate.

### 6.2 API
REST page size is capped at 100, but the offset is not. GraphQL connections allow up to 1000 items per level, nesting depth and aliases are unrestricted, and the endpoint is anonymous. Comment and tag endpoints are unbounded (PERF-002, PERF-004, PERF-013).

### 6.3 Data access and datastore
All SQL is hand-written in mapper XML. The CQRS read side already batches some extras (`articlesFavoriteCount`, `userFavorites`, `followingAuthors` all take id lists), which is good practice. The problems are query shape (PERF-001, COR-002), missing indexes (PERF-003), unbounded reads (PERF-004, PERF-007, PERF-013), and SQLite's single writer with an unconfigured pool and journal (PERF-005, PERF-006). Nothing runs ANALYZE, VACUUM, or retention.

### 6.7 Observability
Absent (PERF-008). The only signal is per-statement DEBUG logging, which is itself a cost (PERF-009).

---

## 7. Findings

Interaction note: PERF-001, PERF-002, PERF-003, and PERF-004 all spend time holding a pooled connection and the shared lock on one SQLite file (PERF-005). In the default rollback-journal mode, long reads delay every writer. Each one makes the others' blast radius larger, and fixing PERF-003 lowers the per-item cost that PERF-002 multiplies.

### PERF-001 — Article list query multiplies rows by tags × favorites, then sorts and repeats it for the count

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/main/resources/mapper/ArticleReadService.xml:47` (`ArticleReadService.queryArticles`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The id query behind GET `/articles` and the GraphQL article lists always LEFT JOINs `article_tags`, `tags`, `article_favorites`, and two `users` aliases, even when no filter is given. Each article therefore expands into tags × favorites rows, which are de-duplicated with DISTINCT and fully sorted before LIMIT applies. The REST path then runs the same join again to get the count.

**Performance principle**
Do not multiply rows you will immediately collapse. Join a one-to-many relation only when a predicate needs it, and do not compute an exact total over the same expanded set on every request.

**Evidence**
- `ArticleReadService.xml:27-37`: `selectArticleIds` joins five tables unconditionally. Filters are optional `<if>` blocks.
- `ArticleReadService.xml:47-62`: DISTINCT, `ORDER BY A.created_at desc`, then `limit offset, limit`.
- `ArticleReadService.xml:63-84`: `countArticle` repeats the join with `count(DISTINCT A.id)`.
- `ArticleQueryService.java:102-103`: both run on every REST list request. `ArticleReadService.xml:107` reuses the join for GraphQL lists.
- `Page.java:18`: the offset is never clamped.
- Derivation: rows = Σ over articles of max(1, tags) × max(1, favorites), from the two independent joins on `A.id`.
- **No runtime evidence exists.** No plan, row count, or timing.

**Impact**
Position: critical path (anonymous home page). Frequency: per request, twice. Growth: O(n·m), meaning articles × tags × favorites. Blast radius: system-wide, assuming SQLite in production, because a long read holds the shared lock that every writer waits behind.

**Conditions**
Matters once articles carry several tags and accumulate favorites. Assumed, but not verified, that tables grow without bound, since no retention exists. Workload is unknown, so confidence is capped at Medium.

**Counter-evidence**
- Searched for a LIMIT or subquery that restricts articles before the join: none. No effect.
- SQLite may build transient automatic indexes on the unindexed join columns. That avoids a quadratic nested loop, but it still scans and still multiplies rows. No effect.
- No row-count or seed data shows production scale. Lowers confidence (reflected in Medium).
- Filtered requests narrow the output but still join and scan. No effect.

**Why this might not matter**
On a small deployment (a few thousand articles and favorites), SQLite completes this in-process join quickly. The cliff only appears at scale.

**Recommendation**
Select from `articles` alone when no filter is given. Express the tag, author, and favoritedBy filters as `EXISTS`/`IN` subqueries instead of fan-out joins, and drop DISTINCT. Order by `created_at` with an index (PERF-003) so that `ORDER BY … LIMIT` reads only the page. Compute the total with the same filter-only form, or replace it with a has-more flag if the API contract allows.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — filter-only EXISTS subqueries + `created_at` index | **preferred** | Removes the multiplication and the sort. Work becomes proportional to the page |
| B — add indexes only | | Cheaper probes, but the multiplication, DISTINCT, and full sort remain |
| C — cache the list or the count | | Hides the work, is invalidated by every favorite and new article, and does not help filtered or deep pages |

**Trade-offs**
Three mapper statements change, and behavior under combined filters must stay identical. A `created_at` index adds write cost. Dropping the exact `articlesCount` would change the RealWorld response contract.

**Validation**
Baseline: `EXPLAIN QUERY PLAN` (safe-on-production) and the joined row count on a database copy (not-safe-on-production). Expectation: after the rewrite, no `USE TEMP B-TREE FOR DISTINCT/ORDER BY`, and rows examined proportional to offset + limit. Latency gain is unquantified. Falsifier: a production-shaped plan that already reads few rows, with a fast endpoint. Guard: a test asserting `queryArticles` does not join `article_favorites` when `favoritedBy` is null.

---

### PERF-002 — GraphQL resolvers query per item with no batching and no depth or complexity limit

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Critical |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/main/java/io/spring/graphql/ProfileDatafetcher.java:38` (`ProfileDatafetcher.getAuthor`) |
| **Tags** | needs-measurement |

**Problem**
`Article.author` and `Comment.author` each run `findByUsername` plus `isUserFollowing` per edge. `Comment.article` runs `findById` (a 4-table join) plus up to three scalar queries per comment. `Article.comments` runs an unbounded comment query per article. There is no DataLoader and no depth or complexity limit, each connection allows up to 1000 items, and `/graphql` is anonymous.

**Performance principle**
Work per response must not scale with the product of nested list sizes. Per-item lookups that share a key space should be collected into one round trip, and caller-composed queries need a cost bound.

**Evidence**
- `ProfileDatafetcher.java:37-42,44-49,58-70`; `ProfileQueryService.java:19-31` (2 queries per call when authenticated).
- `ArticleDatafetcher.java:321-340` (`getCommentArticle`, which goes to `ArticleQueryService.findById` and `fillExtraInfo`).
- `CommentDatafetcher.java:50-100` (comments per article).
- `CursorPageParameter.java:10` (`MAX_LIMIT = 1000`); `WebSecurityConfig.java:53` (`/graphql` permitAll).
- A search of `src/main` for DataLoader, Instrumentation, MaxQueryDepth, or complexity found nothing.
- Derivation: an authenticated `articles(first:1000){edges{node{author{username}}}}` costs the list queries + 2000 author queries. Nesting `comments{…author article{author}}` multiplies that by the comments per article.
- **No runtime evidence exists.**

**Impact**
Position: critical path. Frequency: per item. Growth: O(n·m) across nesting levels. Blast radius: system-wide, because one query occupies a connection and the SQLite file for its whole duration. Amplification is N×, chosen by the caller.

**Conditions**
Matters if production clients, or any anonymous caller, use the GraphQL list fields with `author`, `comments`, or `article`. Whether GraphQL carries production traffic is unknown; I assumed it is reachable as configured.

**Counter-evidence**
- No request-scoped batching or memoization exists. No effect.
- The 1000 cap bounds each level but not the product across levels. Bounds impact (already reflected; without it, growth would be unbounded per level).
- Author lookups by username use the UNIQUE index. The `isUserFollowing` part scans `follows` (PERF-003). No effect; it is the count of queries per item that drives this finding.
- Production GraphQL use is not evidenced. Lowers confidence (Medium).

**Why this might not matter**
If the frontend uses only REST and `/graphql` is blocked at the edge, this never runs. With page sizes of 20 and shallow queries, the cost is tens of in-process lookups.

**Recommendation**
Use the author data already selected into `ArticleData`/`CommentData` (id, username, bio, image) instead of re-querying by username. Add DGS data loaders keyed by author id (following flags via the existing `followingAuthors` IN query) and by article id for `Comment.article`. Add max-depth and complexity instrumentation, and lower the connection cap toward the REST cap.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — reuse parent data + DataLoaders + depth/complexity limits | **preferred** | Removes most per-item queries and collapses the rest. The limits bound caller-chosen fan-out |
| B — limits only | | Bounds abuse, but leaves 2 queries per edge on normal queries |
| C — in-process profile cache | | `following` depends on the viewer, so the hit rate is low where it matters, and every follow or unfollow must invalidate it |

**Trade-offs**
DataLoaders add request-scoped state and dispatch latency. Limits can reject legitimate deep queries and need tuning against real client queries.

**Validation**
Metric: SQL statements logged for one `articles(first:20){…author…}` request in staging (not-safe-on-production). Expectation: a constant count, independent of `first`. Falsifier: the count is already constant. Guard: a `DgsQueryExecutor` test asserting the mapper call count does not grow with the number of seeded articles.

---

### PERF-003 — No secondary indexes: follow, comment, tag, and feed lookups scan whole tables

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/main/resources/db/migration/V1__create_tables.sql:27` (`V1__create_tables`) |
| **Tags** | scalability-risk |

**Problem**
The only migration creates no secondary indexes. `follows` has no key at all. `article_tags`, `comments.article_id`, `articles.user_id`, `articles.created_at`, `tags.name`, and `article_favorites.user_id` are all unindexed, yet they are the filters, join keys, or sort keys of nearly every read.

**Performance principle**
A per-request lookup should cost in proportion to the rows it returns, not the size of the table it searches.

**Evidence**
`V1__create_tables.sql:12,17` (`articles.user_id`, `created_at`), `:27-30` (`follows`), `:34` (`tags.name`), `:37-40` (`article_tags`), `:45` (`comments.article_id`). `UserRelationshipQueryService.xml:4-17` (follow lookups on every authenticated article or profile view and per GraphQL author). No other migration and no `CREATE INDEX` exist anywhere. **No runtime evidence exists.**

**Impact**
Position: critical path. Frequency: per request, and per item under PERF-002. Growth: O(n) in the size of each table. Blast radius: service-wide.

**Conditions**
Matters once `follows`, `comments`, `article_tags`, and `articles` exceed what a full scan reads cheaply. I assumed they grow without bound. Row counts are unknown, so confidence is capped at Medium.

**Counter-evidence**
- UNIQUE on username, email, and slug, and the composite primary key on `article_favorites`, create implicit indexes, so those point lookups are covered. Bounds impact (this is why severity is High rather than Critical).
- SQLite automatic indexes still scan the table once per statement. No effect.
- No row counts exist. Lowers confidence.

**Why this might not matter**
SQLite scans tens of thousands of narrow, cached rows quickly. At small scale the indexes would only add write cost.

**Recommendation**
Add a V2 migration:
- `follows(user_id, follow_id)` as a primary key or unique index, which also prevents duplicate follows
- `article_tags(article_id, tag_id)` and `article_tags(tag_id)`
- `comments(article_id, created_at)`
- `articles(user_id, created_at)` and `articles(created_at)`
- unique `tags(name)`
- `article_favorites(user_id)`

Then run `ANALYZE`, because SQLite collects no planner statistics on its own.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — targeted indexes per mapper predicate + ANALYZE | **preferred** | Each index is justified by a named query |
| B — index everything | | Pays write cost for indexes nothing uses |
| C — cache follows or comments | | Data is per viewer and must be invalidated on every write; it hides the scan instead of removing it |

**Trade-offs**
Every index adds insert and delete cost and file size. In SQLite, the single writer holds the lock slightly longer per write. Building indexes on a large existing file holds the write lock for the whole build.

**Validation**
`EXPLAIN QUERY PLAN` for `isUserFollowing`, `followingAuthors`, comments `findByArticleId`, and the feed queries (safe-on-production). Expectation: `SCAN` changes to `SEARCH … USING INDEX`. Falsifier: the plans already show `SEARCH`, which would mean indexes were created out of band. Guard: a test that runs `EXPLAIN QUERY PLAN` on each mapper statement and fails on a `SCAN` of `follows`, `comments`, or `article_tags`.

---

### PERF-004 — Comment reads are unbounded on both REST and GraphQL

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/main/resources/mapper/CommentReadService.xml:24` (`CommentReadService.findByArticleIdWithCursor`) |
| **Tags** | quick-win |

**Problem**
REST `findByArticleId` has no LIMIT. The GraphQL cursor query receives a page parameter but never uses `page.queryLimit`. So every comment after the cursor is fetched, `followingAuthors` runs over all of them, and then a single element is removed (`CommentQueryService.java:78`).

**Performance principle**
A read endpoint's cost needs an enforced upper bound that does not depend on data volume.

**Evidence**
`CommentReadService.xml:20-23,24-41`; `CommentQueryService.java:56-84`; `CommentsApi.java` (GET returns the full list). `comments.article_id` is unindexed (PERF-003). **No runtime evidence exists.**

**Impact**
Position: critical path. Frequency: per request, and per article inside GraphQL article lists. Growth: O(n) in comments per article, plus a table scan. Blast radius: endpoint.

**Conditions**
Matters for articles with many comments. The comment count per article is unknown, and I assumed some articles are popular. Confidence is capped at Medium.

**Counter-evidence**
- No caller-side truncation exists. No effect.
- The RealWorld REST spec returns all comments, so the REST behavior is a contract choice rather than an oversight. Lowers confidence.

**Why this might not matter**
Most blog articles get few comments, and the REST spec expects the full list.

**Recommendation**
Add `LIMIT #{page.queryLimit}` to `findByArticleIdWithCursor`; the service already expects limit + 1 rows. Run `followingAuthors` only over the trimmed page. For REST, either add an optional limit with a server-side maximum, or keep the full list and index `comments(article_id, created_at)`.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — LIMIT in SQL for GraphQL; bound or index the REST path | **preferred** | Uses the pagination model the code already has; one-line change |
| B — truncate in Java | | Still reads and maps every row |

**Trade-offs**
A server-side maximum on REST comments changes the observable API.

**Validation**
Fetch `comments(first:5)` on an article with more than 6 comments in staging (not-safe-on-production). Expectation: at most 6 rows fetched and 5 returned; today all but one are returned. Falsifier: 5 are already returned. Guard: a `CommentQueryService` test asserting the page size. Command: `./gradlew test --tests 'io.spring.application.comment.CommentQueryServiceTest'` (safe-on-production; local).

---

### PERF-005 — Single-writer SQLite behind an unconfigured multi-connection pool and journal

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `src/main/resources/application.properties:1` (`spring.datasource.url`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
All traffic goes to one SQLite file through a pool left at framework defaults. No `journal_mode`, `busy_timeout`, or pool size is set. SQLite admits one writer at a time, and in the default rollback-journal mode, readers also block a committing writer. So the long scans from PERF-001, PERF-003, and PERF-004 lengthen every write's wait, and concurrent writes surface as `SQLITE_BUSY` errors instead of queueing.

**Performance principle**
Concurrency admitted upstream must match what the shared resource can serve. Extra connections to a single-writer resource add contention, not throughput.

**Evidence**
`application.properties:1-4` (no pool or PRAGMA settings); `build.gradle:43`; a search for `PRAGMA`, `journal_mode`, `busy_timeout`, or `hikari` found nothing; `MyBatisArticleRepository.java:20` (a multi-statement `@Transactional` write). **No runtime evidence exists.**

**Impact**
Position: critical path (writes, and reads that wait behind them). Frequency: per request. Growth: O(1), but it saturates. Blast radius: system-wide.

**Conditions**
Matters only if this SQLite configuration runs in production with concurrent writes. It gets worse as read scans lengthen. I assumed SQLite in a single JVM. The README calls SQLite a local-testing convenience.

**Counter-evidence**
- The README says the database "can be changed easily" for another one. Lowers confidence.
- Writes are short and few per request. Bounds impact (severity is Medium, not Critical).

**Why this might not matter**
If production points at a client-server database, or writes are rare, the single writer never becomes the constraint.

**Recommendation**
Decide explicitly. If SQLite stays, enable WAL, set a non-zero `busy_timeout`, size the pool deliberately (it helps reads under WAL, not writes), and keep write transactions short. If production uses another engine, move the SQLite URL into a dev profile.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — measure `journal_mode` and `SQLITE_BUSY` counts, then WAL + `busy_timeout` | **preferred** | Cheap, reversible, and aimed at the actual mechanism |
| B — move to a client-server engine | | Removes the ceiling, but it is a migration; justified only by write-concurrency evidence |
| C — enlarge the pool | | More connections fighting over the same writer lock |

**Trade-offs**
WAL needs checkpointing and is unsafe on network filesystems. `busy_timeout` turns errors into latency.

**Validation**
Run `PRAGMA journal_mode;` (safe-on-production) and `./gradlew dependencies --configuration runtimeClasspath` to confirm the pool implementation (safe-on-production). Run a concurrent-write test in staging and count `SQLITE_BUSY` (not-safe-on-production). Falsifier: production is not SQLite, or there is no `SQLITE_BUSY` and no read stalls under concurrent writes.

---

### PERF-006 — Statement timeout of 3000 is read as seconds (50 minutes)

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `src/main/resources/application.properties:13` (`mybatis.configuration.default-statement-timeout`) |
| **Tags** | quick-win |

**Problem**
MyBatis reads `defaultStatementTimeout` in seconds. A runaway statement, such as a deep list query or a nested GraphQL query, can therefore hold a connection and the SQLite lock for up to 50 minutes, not the 3 seconds the value suggests.

**Performance principle**
Every operation on a shared resource needs a timeout shorter than the latency budget of the callers waiting behind it.

**Evidence**
`application.properties:13`. Derivation: 3000 s ÷ 60 = 50 minutes. No request-level timeouts are configured elsewhere.

**Impact**
Position: critical path. Frequency: per request. Growth: O(1). Blast radius: system-wide (connection hold and lock hold).

**Conditions**
Matters whenever a statement runs long, which is likely as data grows (PERF-001, PERF-003, PERF-002). It is a configuration defect at any traffic level.

**Counter-evidence**
- I did not verify that sqlite-jdbc 3.36 enforces `setQueryTimeout`. If it does not, there is effectively no timeout at all, and the conclusion is unchanged. Lowers confidence (Medium).
- No other timeout layer exists. No effect.

**Why this might not matter**
If every statement finishes in milliseconds at current scale, the timeout never fires.

**Recommendation**
Set a seconds value below the client latency budget, and confirm in staging that a slow query is actually cancelled.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — correct the unit to a budget-derived value; verify the driver enforces it | **preferred** | One-line bound on worst-case hold time |
| B — rely on client or proxy timeouts | | The server statement keeps running and keeps holding the lock |

**Trade-offs**
Too low a value turns legitimate slow queries into errors.

**Validation**
Run a deliberately slow statement in staging (not-safe-on-production). Expectation: it is cancelled near the configured bound. Falsifier: it is already cancelled near 3 s. Guard: a startup assertion on the configured maximum.

---

### PERF-007 — Feed ships the full followed-user list as an IN list and re-counts it on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/main/java/io/spring/application/ArticleQueryService.java:114` (`ArticleQueryService.findUserFeed`) |
| **Tags** | scalability-risk |

**Problem**
`followedUsers` loads every followed id with no limit. Those ids are then bound into `A.user_id IN (...)` for the article query, and again for `countFeedSize` on every REST feed request. `articles.user_id` is unindexed, so both statements scan.

**Performance principle**
Push set membership into the datastore instead of shipping an unbounded key list through the application, and do not recompute an exact total for every page.

**Evidence**
`UserRelationshipQueryService.xml:15-17`; `ArticleReadService.xml:93-106`; `ArticleQueryService.java:113-123`. **No runtime evidence exists.**

**Impact**
Position: critical path. Frequency: per request. Growth: O(n) in follows, and in articles because of the scan. Blast radius: endpoint.

**Conditions**
Matters for users who follow many authors. The bound-parameter count also grows, and SQLite has a compile-time limit on bound parameters. Follow counts are unknown.

**Counter-evidence**
- The feed is authenticated-only. Bounds impact.
- There is no cap on follows per user. No effect.

**Why this might not matter**
Typical users follow tens of authors.

**Recommendation**
Use `WHERE user_id IN (SELECT follow_id FROM follows WHERE user_id = ?)` with the PERF-003 indexes, add the missing ORDER BY (COR-002), and drop or cache the exact count if the contract allows.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A — SQL subquery + indexes | **preferred** | One statement, no unbounded parameter list |
| B — chunk the IN list | | Multiplies statements and breaks global ordering |

**Trade-offs**
Depends on the PERF-003 indexes to pay off.

**Validation**
`EXPLAIN QUERY PLAN` of the feed query (safe-on-production). Expectation: `SEARCH` via the user_id index, and one fewer statement per request. Falsifier: the feed is already fast with a `SEARCH` plan for a heavy follower.

---

### PERF-008 — No runtime instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 (sequenced first — see §8) |
| **Category** | observability |
| **Location** | `build.gradle:33` (`dependencies`) |
| **Tags** | quick-win |

**Problem**
There are no metrics, traces, actuator endpoints, pool metrics, benchmarks, or load tests, so no finding here can be confirmed and no fix can be validated with runtime data.
**Performance principle:** Measurement precedes optimization.
**Evidence:** `build.gradle:33-56` has no actuator, Micrometer, or tracing dependency. A repository search for actuator, micrometer, prometheus, opentelemetry, k6, jmh, or gatling found nothing.
**Impact:** Off the request path, constant, service-wide.
**Conditions:** Any workload.
**Counter-evidence:** I looked for request-duration logging as a substitute; only MyBatis statement logging exists, which records neither timings nor pool wait. No effect.
**Recommendation:** Add `spring-boot-starter-actuator` + Micrometer. Expose HTTP server request timers and the pool's active, pending, and acquire-time metrics. Add a query-count helper for tests.
**Trade-offs:** Actuator endpoints need access control, and metrics need a backend to store them.
**Validation:** `http.server.requests` and pool metrics appear (safe-on-production). Falsifier: metrics already flow from an agent configured outside the repository.

---

### PERF-009 — Per-statement DEBUG logging in the default configuration

| | |
|:--|:--|
| **Root cause** | `ROOT-009` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | io |
| **Location** | `src/main/resources/application.properties:20` |
| **Tags** | quick-win |

**Problem:** DEBUG is set for the mapper package and `ArticleReadService` in the default properties file, so every statement and its parameters are formatted and written to the log. Under PERF-002, that output is multiplied per item.
**Performance principle:** Diagnostic output should be opt-in per environment.
**Evidence:** `application.properties:19-20`.
**Impact:** Critical path, per statement, O(n) in statements, service-wide.
**Conditions:** Visible at request rates where synchronous log I/O matters. The rate is unknown.
**Counter-evidence:** Only two logger scopes are at DEBUG. Bounds impact.
**Recommendation:** Move these levels to a dev profile. This also fixes SEC-002.
**Trade-offs:** Loses ad hoc SQL visibility in production; PERF-008's metrics replace it.
**Validation:** Zero MyBatis lines per request at INFO (safe-on-production). Falsifier: an external logback config already overrides these levels.

---

### PERF-010 — Single-article reads use four statements; write endpoints re-read; existence check loads the full entity

| | |
|:--|:--|
| **Root cause** | `ROOT-010` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `src/main/java/io/spring/application/ArticleQueryService.java:175` (`ArticleQueryService.fillExtraInfo`) |

**Problem:** An authenticated article read runs the article join plus `isUserFavorite`, `articleFavoriteCount`, and `isUserFollowing`. Favorite, unfavorite, create-article, and create-comment endpoints re-read through the same path. The slug validator does a full 4-table article read just to test whether the slug exists (`DuplicatedArticleValidator.java:16`).
**Performance principle:** Fetch what the response needs in as few round trips as practical, and test existence without loading the entity.
**Evidence:** `ArticleQueryService.java:175-183`, `DuplicatedArticleValidator.java:16`, `ArticleFavoriteApi.java`.
**Impact:** Critical path, per request, O(1) (4× statements), endpoint-scoped.
**Conditions:** A bounded constant; it matters only at high request rates.
**Counter-evidence:** SQLite is in-process, so there are no network round trips. Bounds impact.
**Recommendation:** Fold the count and the viewer flags into the article query, and use `SELECT 1 … LIMIT 1` for the validator.
**Trade-offs:** A viewer-dependent query is harder to share between the anonymous and authenticated paths.
**Validation:** Statements per authenticated GET `/articles/{slug}` drop from 4 to 1 (staging, not-safe-on-production). Falsifier: the count is already 1.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-011 | Low | High | P3 | `src/main/java/io/spring/infrastructure/repository/MyBatisArticleRepository.java:30` | Article create runs findTag (table scan) / insertTag / insertRelation per tag inside the write transaction. `tagList` has no size bound. Fix: cap tags, batch the lookup and inserts |
| PERF-012 | Low | High | P3 | `src/main/resources/mapper/ArticleMapper.xml:32` | Delete removes only the `articles` row. Orphaned tags, comments, and favorites stay in tables that reads scan. Fix: delete dependents in the same transaction, then clean up existing orphans |
| PERF-013 | Low | Medium | P3 | `src/main/resources/mapper/TagReadService.xml:4` | `/tags` returns the whole tags table. Growth is bounded by distinct names (tags are reused). Fix: return the top-N popular tags |

### Considered and not reported

| Candidate | Path / resource | Evidence checked | Why discarded | Revisit when |
|:--|:--|:--|:--|:--|
| `mybatis.configuration.cache-enabled=true` might mask repeated reads | All MyBatis reads | All mapper XML (no `<cache/>`), `application.properties:12` | No second-level cache is active | A `<cache/>` element is added; it would serve stale per-viewer data |
| JwtTokenFilter registered twice could double the user lookup | Every authenticated request | `WebSecurityConfig.java:25-28,64`, `JwtTokenFilter.java:19,32` | `OncePerRequestFilter` plus the existing-auth check mean one PK lookup at most | The filter stops extending `OncePerRequestFilter` |
| BCrypt CPU cost on login and registration | Login/register | `UserMutation.java:59`, `UserService.java:39` | Deliberate security cost on low-frequency endpoints | Login CPU becomes measurable, or credential-stuffing load appears |
| REST offset pagination degrades with depth | GET `/articles` | `Page.java:18`, `ArticleReadService.xml:61` | Merged into PERF-001 (same query, dominated by the fan-out and sort) | Deep-offset requests are still slow after PERF-001 is fixed |

### Adjacent findings — outside performance scope

### SEC-001 — JWT signing secret committed in the default configuration

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `src/main/resources/application.properties:9` |

**Problem:** The HS512 signing secret is a literal in the default properties file (value not reproduced here).
**Evidence:** `application.properties:9`.
**Impact:** High risk, because anyone with repository access can mint valid tokens for any user on a deployment that uses this file unchanged.
**Recommendation:** Load the secret from the environment or a secret store, rotate it, and fail startup if it is absent.
**Trade-offs:** Adds secret provisioning to deployment.
**Validation:** The application refuses to start without an externally supplied secret, and old tokens are rejected after rotation.
**Would need:** A security review and a repository secret scan.

### SEC-002 — DEBUG mapper logging writes emails and password hashes to logs

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/main/resources/application.properties:20` |

**Problem:** At DEBUG, MyBatis logs bound parameters, which include `#{user.email}` and `#{user.password}` (the hash) in `UserMapper` insert and update.
**Evidence:** `application.properties:20`; `UserMapper.xml:5-12,18-26`.
**Impact:** Medium risk, because log access becomes access to personal data and password hashes.
**Recommendation:** Remove DEBUG from the default profile (PERF-009), and treat existing logs as sensitive.
**Trade-offs:** None material.
**Validation:** No parameter lines in production logs.
**Would need:** A privacy and logging review.

### COR-001 — GraphQL comment pagination returns all comments but one

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/main/resources/mapper/CommentReadService.xml:24` |

**Problem:** The query has no LIMIT, and `comments.remove(page.getLimit())` removes a single element. So `comments(first:N)` returns nearly everything, and `hasNextPage` is wrong.
**Evidence:** `CommentReadService.xml:24-41`; `CommentQueryService.java:76-79`.
**Impact:** Medium risk, because clients get wrong page contents and page info.
**Recommendation:** Add `LIMIT #{page.queryLimit}` (shared with PERF-004).
**Trade-offs:** None.
**Validation:** A test that asserts page size and `hasNextPage`.
**Would need:** A correctness-focused pagination test suite.

### COR-002 — Feed LIMIT applies to joined article×tag rows; the REST feed has no ORDER BY

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/main/resources/mapper/ArticleReadService.xml:93` |

**Problem:** `findArticlesOfAuthors` and `findArticlesOfAuthorsWithCursor` LIMIT the article × tag join rows, so pages can hold fewer articles than requested and an article's tags can be cut off at the page boundary. The REST feed query has no ORDER BY, so page order is undefined.
**Evidence:** `ArticleReadService.xml:93-100,134-155`.
**Impact:** Medium risk, because feed pages can be short, unordered, or carry truncated tag lists.
**Recommendation:** Select ordered, limited article ids first, then load the data for those ids, as the non-feed path already does.
**Trade-offs:** One extra statement per feed page.
**Validation:** A test seeding multi-tag articles and asserting page size and order.
**Would need:** Correctness tests for feed pagination.

### COR-003 — Shared static `Calendar` in DateTimeHandler

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | Medium |
| **Risk** | Low |
| **Location** | `src/main/java/io/spring/infrastructure/mybatis/DateTimeHandler.java:18` |

**Problem:** One `java.util.Calendar`, which is not thread-safe, is shared by all threads and passed to the JDBC timestamp calls.
**Evidence:** `DateTimeHandler.java:18,24,29,35,41`.
**Impact:** Low risk. If the driver mutates the Calendar, concurrent requests could read or write wrong timestamps.
**Recommendation:** Create a Calendar per call.
**Trade-offs:** A trivial allocation per call.
**Validation:** A parallel timestamp round-trip test.
**Would need:** A concurrency test, or review of how sqlite-jdbc uses the Calendar argument.

### MAINT-001 — End-of-support framework line

| | |
|:--|:--|
| **Kind** | Maintenance |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `build.gradle:2` |

**Problem:** Spring Boot 2.6.3 and Java 11, with DGS 4.9.21 and sqlite-jdbc 3.36.0.3. The Spring Boot 2.x line no longer receives open-source support.
**Evidence:** `build.gradle:2,10-11,40,43`.
**Impact:** Medium risk, because fixes and security patches stop arriving.
**Recommendation:** Plan an upgrade to a supported Spring Boot 3.x line (Java 17, jakarta namespace).
**Trade-offs:** A non-trivial migration.
**Validation:** The build and tests pass on the new line.
**Would need:** A dependency-update pass with a vulnerability scanner.

---

## 8. Prioritized action plan

### P1 — High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-008 | P3 | small | **Sequenced first despite P3:** without request and pool metrics, none of the P1 fixes can be validated |
| 2 | PERF-002 | P1 | small (limits) → medium (DataLoaders) | Anonymous, caller-controlled fan-out. Ship the depth/complexity limits immediately |
| 3 | PERF-004 | P1 | tiny | One-line LIMIT; also fixes COR-001 |
| 4 | PERF-003 | P1 | small | One migration; it lowers the per-item cost behind PERF-001, PERF-002, and PERF-007 |
| 5 | PERF-001 | P1 | medium | Query rewrite on the home-page path; uses PERF-003's `created_at` index |

### P2 — Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 6 | PERF-006 | P2 | tiny | One-line fix that bounds worst-case lock hold; do it together with item 3 |
| 7 | PERF-005 | P2 | small | Answer the production-datastore question first, then WAL + `busy_timeout` |
| 8 | PERF-007 | P2 | small | Feed rewrite, together with COR-002 |

### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 9 | PERF-009 | P3 | tiny | Also closes SEC-002 |
| 10 | PERF-010 | P3 | small | After the indexes |
| 11 | PERF-011, PERF-012, PERF-013 | P3 | small | Hygiene and growth |

**If only one thing is done:** add GraphQL depth/complexity limits and the comment LIMIT (PERF-002 and PERF-004). They are the cheapest changes that remove caller-controlled, unbounded work from a shared single-writer database.

---

## 9. Validation plan

### PERF-001
- **Baseline:** `EXPLAIN QUERY PLAN` of the list query — safe-on-production. Joined row count on a database copy — not-safe-on-production.
- **Change:** Filter-only EXISTS subqueries plus an `articles(created_at)` index.
- **Measurement:** Plan operators, rows examined, and GET `/articles?limit=20` time on a production-shaped copy.
- **Expectation:** No temp B-tree for DISTINCT/ORDER BY, and rows examined ∝ offset + limit. Latency gain unquantified.
- **Falsifier:** The current plan already reads few rows and the endpoint is fast.
- **Guard:** A test asserting no `article_favorites` join when `favoritedBy` is null.
- **Commands:**
  - `safe-on-production` `sqlite3 dev.db "EXPLAIN QUERY PLAN SELECT DISTINCT(A.id) articleId, A.created_at FROM articles A LEFT JOIN article_tags AT ON A.id = AT.article_id LEFT JOIN tags T ON T.id = AT.tag_id LEFT JOIN article_favorites AF ON AF.article_id = A.id LEFT JOIN users AU ON AU.id = A.user_id LEFT JOIN users AFU ON AFU.id = AF.user_id ORDER BY A.created_at DESC LIMIT 0, 20;"` — show the plan without executing
  - `not-safe-on-production` `sqlite3 copy-of-prod.db "SELECT count(*) FROM articles A LEFT JOIN article_tags AT ON A.id = AT.article_id LEFT JOIN article_favorites AF ON AF.article_id = A.id;"` — measure the fan-out on a copy

### PERF-002
- **Baseline:** Statements logged per GraphQL request (DEBUG logging is already on) — staging, not-safe-on-production.
- **Change:** Reuse parent author data, add DataLoaders, add depth/complexity limits.
- **Measurement:** Statement count for `articles(first:20){…author…}` at `first` = 5, 20, and 100.
- **Expectation:** Constant in `first`.
- **Falsifier:** Already constant.
- **Guard:** A `DgsQueryExecutor` query-count test.
- **Commands:**
  - `not-safe-on-production` `curl -s -X POST http://localhost:8080/graphql -H 'Content-Type: application/json' -d '{"query":"{ articles(first: 20) { edges { node { slug author { username following } } } } }"}'` — run locally or in staging and count logged statements

### PERF-003
- **Baseline:** Current plans and indexes — safe-on-production.
- **Change:** V2 index migration + `ANALYZE`.
- **Measurement:** `SCAN` vs `SEARCH` per mapper statement.
- **Expectation:** `SEARCH … USING INDEX` on follows, comments, article_tags, and articles(user_id).
- **Falsifier:** Plans already show `SEARCH`.
- **Guard:** An `EXPLAIN QUERY PLAN` test over the mapper statements.
- **Commands:**
  - `safe-on-production` `sqlite3 dev.db ".indexes"` — list existing indexes
  - `safe-on-production` `sqlite3 dev.db "EXPLAIN QUERY PLAN SELECT count(1) FROM follows WHERE user_id = 'x' AND follow_id = 'y';"` — confirm the follows scan

### PERF-004
- **Baseline:** `comments(first:5)` response size on an article with more than 6 comments — staging.
- **Change:** `LIMIT #{page.queryLimit}`.
- **Expectation:** 5 returned, at most 6 fetched.
- **Falsifier:** Already 5.
- **Guard:** A page-size assertion in `CommentQueryServiceTest`.
- **Commands:**
  - `safe-on-production` `./gradlew test --tests 'io.spring.application.comment.CommentQueryServiceTest'` — local test run after adding the assertion

### PERF-005
- **Baseline:** `journal_mode` on the deployed file; `SQLITE_BUSY` count under a concurrent-write test in staging.
- **Change:** WAL + `busy_timeout` + a deliberate pool size (if SQLite is retained).
- **Expectation:** Fewer `SQLITE_BUSY` errors, and reads no longer stall during writes.
- **Falsifier:** Production is not SQLite, or the staging test shows no contention.
- **Commands:**
  - `safe-on-production` `sqlite3 dev.db "PRAGMA journal_mode;"` — current journal mode
  - `safe-on-production` `./gradlew dependencies --configuration runtimeClasspath` — confirm the pool implementation

### PERF-006
- **Baseline / Measurement:** Whether a slow statement in staging is cancelled, and when (not-safe-on-production).
- **Expectation:** Cancelled near the corrected bound.
- **Falsifier:** Already cancelled near 3 s.
- **Guard:** A startup check on the configured maximum.

### PERF-007
- **Commands:**
  - `safe-on-production` `sqlite3 dev.db "EXPLAIN QUERY PLAN SELECT count(1) FROM articles A WHERE A.user_id IN ('a','b','c');"` — show the feed scan
- **Expectation:** `SEARCH` after the indexes and subquery rewrite, and one fewer statement per request. **Falsifier:** Already `SEARCH` and fast.

### Instrumentation gaps to close first

The repository is uninstrumented. Before the P1 changes, add actuator/Micrometer HTTP timers and connection-pool active, pending, and acquire-time metrics (PERF-008). Also add a test helper that counts MyBatis statements per request. Without these, the improvements above can only be shown through plans and statement counts, not through latency.

---

## 10. Machine-readable output

Emitted alongside this report at `out/spring-guided-1.json`, conforming to `schemas/review.schema.json`. `stable_id` values were computed with `scripts/compute_stable_id.py`. If the JSON and this report disagree, this report is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade. `Confirmed` requires a cited runtime artifact, and none exists, so no finding is `Confirmed`.
- No runtime metric in this report was estimated or assumed. The numbers that appear are read from files (100, 1000, 3000, versions) or labeled derivations (50 minutes; row-multiplication and per-edge query formulas).
- Recommendations state their trade-offs and validation paths. Nobody was available for the workload interview, so the questions are recorded in §4, and workload-dependent findings are capped at `Medium` confidence.
