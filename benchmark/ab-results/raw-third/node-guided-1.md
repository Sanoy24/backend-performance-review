# Performance Review: node-express-realworld (Express + Prisma + PostgreSQL RealWorld API)

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0 (spec `backend-performance-review/2.0`)
**Commit:** `30b68e1e881462b2f4164ea09ab4c4f5699c7b0b`

---

## 1. Decision summary

### Overall assessment

This is a small, single-process Express/Prisma API. Its data access has three structural problems that grow with data: every article response loads the **complete** list of users who favorited the article and the **complete** follower list of its author, only to compute a boolean and a count (PERF-001). The list endpoints accept an **unbounded client-supplied `limit`** (PERF-002), which multiplies that cost. And the schema has **no index** on `Article.authorId`, `Article.createdAt` or `Comment.articleId` (PERF-004). Nothing in the repository measures anything: no request timing, no query logging, no metrics (PERF-008). Every ranking here therefore comes from structural signals, and no finding is `Confirmed`. Overall review confidence is **Medium**: the whole application was read, but workload is unknown and no runtime evidence exists. Separately, a real **security issue** came up while tracing PERF-001 (SEC-001): the favorite and unfavorite responses serialize full `User` rows, password hashes included.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-002 (P1)** | Clamp `limit` to a server maximum and validate `offset` in `getArticles`/`getFeed` | Workload-independent: any caller can request an arbitrarily large page today. It is the multiplier on PERF-001. It is a few lines of code (`quick-win`) |
| 2 | **PERF-001 (P1)** + **SEC-001** | Replace `favoritedBy: true` / `followedBy: true` includes with a `_count` and a filtered existence check scoped to the current user | Removes per-item work that grows without bound and closes the data leak in the same edit |
| 3 | **PERF-004 (P1)** | Add a migration indexing `Comment.articleId`, `Article.authorId`, and `Article.createdAt` after confirming plans with `EXPLAIN` | The comment list, feed and article list scan or sort whole tables. Each of these tables is append-only and grows with every write |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Row counts of `Article`, `Comment`, `_UserFavorites`, `_UserFollows` and the largest per-article favorite / per-user follower counts | Whether PERF-001 and PERF-004 stay P1 or fall to P2/P3 (small tables make scans and full loads cheap) | `pg_stat_user_tables` / `SELECT count(*)` on a replica; `SELECT "A", count(*) FROM "_UserFavorites" GROUP BY 1 ORDER BY 2 DESC LIMIT 5` |
| Whether the API is publicly reachable without a gateway that caps query parameters | Whether PERF-002 is a reachable abuse path or only a latent one | Deployment/ingress configuration (not in repository) |
| Request rate on `GET /api/articles` and `GET /api/tags` (the home-page calls) | Severity of PERF-005 and PERF-006 (fixed aggregate per request) | Access logs or, once added, the request-duration metric from PERF-008 |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-004 | `safe-on-production` | `psql "$DATABASE_URL" -c 'SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup FROM pg_stat_user_tables ORDER BY seq_tup_read DESC;'` | High `seq_tup_read` on `Comment`/`Article` confirms the scans. Small `n_live_tup` demotes PERF-004 |
| PERF-002 / PERF-001 | `not-safe-on-production` (run against a local seeded DB) | `curl -s -o /dev/null -w "%{size_download} bytes %{time_total}s\n" "http://localhost:3000/api/articles?limit=100000"` | Response size and time scale with total articles rather than a fixed page size |
| PERF-001 | `safe-on-production` | `psql "$DATABASE_URL" -c 'SELECT "A" AS article_id, count(*) FROM "_UserFavorites" GROUP BY 1 ORDER BY 2 DESC LIMIT 5;'` | Shows the per-article favorite counts that PERF-001 loads in full |

---

## 2. Scope and method

**Reviewed:** All application source under `src/`: `main.ts`, all route controllers and services (article, auth, profile, tag), mappers, `prisma-client.ts`, `schema.prisma`, all four migrations, and `seed.ts`. Also `package.json`, the relevant entries in `package-lock.json`, `Dockerfile`, `project.json`, `README.md`, and the unit and e2e test scaffolding.
**Not reviewed:** `node_modules` (not present). Prisma's generated SQL was not observed, because nothing was run. The behaviour of Prisma 4.16 relation loading is reasoned from its documented behaviour (one query per relation level, not per row). No production deployment configuration exists in the repository. `.env` files are absent; none were opened.

**Evidence available:** Uninstrumented. There are no metrics, traces, request logging, query logging, benchmarks, load tests, `EXPLAIN` output, or SLOs. The only logging is a startup `console.info` (`src/main.ts:56`). This caps every workload-dependent finding at `Medium` confidence.

**Ranking method:** Structural signals only. The ranking is inference.

**Reference depth:** Node.js, PostgreSQL, REST/Express and Docker all have deep reference support. Prisma has no dedicated reference file, so ORM behaviour was analysed with the generic `application/data-access.md` principles.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All application source files and the schema/migrations were read in full |
| **Critical paths** | 19 analyzed / 19 identified (every HTTP route) |
| **Shared resources** | 3 analyzed / 3 identified (Node event loop, Prisma connection pool, single PostgreSQL primary) |
| **Technology support** | Node.js: deep; PostgreSQL: deep; Express/REST: deep; Docker: deep; Prisma: generic (no dedicated file) |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

Review confidence and finding confidence measure different things. A finding can be rated `High` while the review is only `Medium`.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Table sizes and per-article / per-user relation fan-out | No evidence exists | Row counts and distribution queries (see §9) |
| Production request rate and its distribution across routes | No evidence exists | Access logs; request-duration metric (PERF-008) |
| Prisma connection pool size actually in effect | No evidence exists: `connection_limit` would live in `DATABASE_URL`, which is not in the repo | Inspect the deployed `DATABASE_URL` parameters (do not paste them into reports) |
| Production process/instance count and CPU allotment | No evidence exists: only a Dockerfile, no compose/k8s/IaC | Deployment configuration |
| Exact SQL Prisma 4.16 emits for relation filters and `include` | Technology unsupported (no Prisma reference) | `log: ['query']` on the client in staging, or `pg_stat_statements` |

---

## 3. Architecture overview

A request takes this path: Express handler (`*.controller.ts`), then a service (`*.service.ts`), then the Prisma client singleton (`src/prisma/prisma-client.ts`), then PostgreSQL. There are no outbound service calls in request paths (`axios` is used only by the e2e test), no cache, no broker and no background jobs.

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| HTTP server | Express | 4.18.2 (lockfile) | deep (REST) | 19 routes under `/api` |
| Runtime | Node.js | `node:lts-alpine` (floating tag, `Dockerfile:7`) | deep | Single process, `CMD ["node","api"]` |
| ORM | Prisma Client | 4.16.2 (lockfile) | generic | All data access |
| Datastore | PostgreSQL | not determinable | deep | Single primary; schema in `src/prisma/schema.prisma` |
| Password hashing | bcryptjs (pure JS) | 2.4.3 (lockfile) | n/a | Register, login, password update |

**Shared resources:** the single Node.js event loop (one process, no cluster); one Prisma connection pool (default size, no `connection_limit` visible); one PostgreSQL primary.

---

## 4. Workload model

**Known**

- 19 HTTP routes: 9 read (GET), 10 write. The read surface dominates user-visible paths: list, feed, single article, comments, tags, profile and current user. Source: `article.controller.ts`, `auth.controller.ts`, `profile.controller.ts`, `tag.controller.ts`.
- List pagination: default `limit` 10, no maximum; `offset` unbounded. Source: `article.service.ts:82-83, 131-132`.
- Comments per article: no pagination. Source: `article.service.ts:433-458`.
- Tag list: fixed `take: 10`. Source: `tag.service.ts:34`.
- `Article`, `Comment`, `_UserFavorites` and `_UserFollows` are append-only from the application's point of view. No retention or archival code exists. Source: all services; migrations.
- No indexes on `Article.authorId`, `Article.createdAt`, `Comment.articleId`, `Comment.authorId` or `User.demo`. Source: `migrations/20210924225358_initial/migration.sql`, `20211001143221_implicit_tags/migration.sql`.
- Deployment: one Node process per container and no replica configuration. Source: `Dockerfile:24`.
- Seed shape: 12 users, 12 articles per user, one comment per user per article. All seed users are `demo: true`. Source: `seed.ts:41,46,50,22`.

**Assumed**

- `Article`, `Comment` and favorite/follow join tables grow without bound over the service's life. Affects: PERF-001, PERF-003, PERF-004, PERF-005, PERF-006.
- `GET /api/articles` and `GET /api/tags` are the highest-frequency routes, because the RealWorld front end loads both on the home page. Affects: PERF-001, PERF-004, PERF-005, PERF-006.
- The service may be reachable by untrusted clients. Affects: PERF-002.

**Unknown**

- Request rate per route; peak concurrency.
- Row counts and per-article favorite / per-user follower distributions.
- Production instance count, CPU limit, and Prisma pool size.
- Whether any latency target exists.

**Derived**

- Seed data produces 144 articles and 1,728 comments. From: 12 users × 12 articles (`seed.ts:41,46`), and 144 articles × 12 commenters (`seed.ts:50`). This is the only data volume the repository states, and it is a development fixture, not production.
- Rows loaded per article list request ≈ page size + Σ(favorites of each article on the page) + Σ(followers of each article's author) + tags. From: the `include` shape at `article.service.ts:84-104`. Page size is client-controlled (PERF-002), so this sum has no upper bound.

**Measured**

- None. No benchmark, trace, profile, query plan or metric was available. This is why no finding is `Confirmed`.

### Questions that would change the ranking

The interview could not be run: nobody was available to answer it. These are the questions that would have been asked, in one message. The review proceeded on the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is there a specific symptom (slow endpoint, incident, cost) that prompted this review? | severity, recommendation | The review would be reorganized around confirming or refuting it |
| 2 | highest | Roughly how many favorites does the most-favorited article have, and how many followers does the most-followed author have? | severity, confidence | Tens: PERF-001 drops to Medium/P2. Thousands or more: it moves toward Critical |
| 3 | high | Approximate row counts of `Article` and `Comment`, and their growth rate? | severity, confidence | Small tables (low thousands) demote PERF-004 and PERF-005 to P3. Large ones confirm P1/P2 |
| 4 | high | Is the API exposed to untrusted clients, and does any gateway cap query parameters? | severity, recommendation | A gateway cap would bound PERF-002 to Medium |
| 5 | high | Peak request rate on `GET /api/articles` and `GET /api/tags`? | severity | Very low rates demote PERF-005 and PERF-006 to Low/P3 |
| 6 | medium | How many instances or processes run in production, with what CPU, and is `connection_limit` set in `DATABASE_URL`? | severity, recommendation | Decides whether PERF-009 is real, and whether pool sizing becomes a finding |
| 7 | medium | Login and registration rate at peak? | severity | High login bursts would raise PERF-007; rare logins make it Low |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | yes | 1 `COUNT` + 1 list + one query per included relation level (tags, author, author.followedBy, favoritedBy, `_count`) | **no**: `limit`/`offset` uncapped; relation loads unbounded | no | Most frequent read (assumed); PERF-001/002/004/005 |
| 2 | `GET /api/articles/feed` | yes | same shape as #1, filter on author's followers | **no** | no | PERF-001/002/004/005 |
| 3 | `GET /api/articles/:slug/comments` | yes | article by unique slug + comments by `articleId` (unindexed) + author + author.followedBy | **no**: all comments | no | PERF-003/004/001 |
| 4 | `GET /api/tags` | yes | one aggregate over `_ArticleToTag` × `Article` × `User` | result capped at 10, but scan is not | no | PERF-006 |
| 5 | `GET /api/articles/:slug` | yes | unique lookup + relation loads | per-article relation loads unbounded | no | PERF-001 |
| 6 | `GET /api/profiles/:username` | yes | unique lookup + full `followedBy` | follower list unbounded | no | PERF-001 |
| 7 | `POST /api/articles/:slug/favorite`, `DELETE …/favorite` | yes | update + full relation reloads | unbounded relation loads | no | PERF-001, SEC-001 |
| 8 | `POST /api/users/login`, `POST /api/users`, `PUT /api/user` | yes | 1–3 unique lookups + bcrypt on main thread | yes | no | PERF-007 |
| 9 | `PUT /api/articles/:slug` | yes | 4 sequential round trips + per-tag upserts | tag count uncapped | no | PERF-010, COR-001 |
| 10 | Remaining writes (create/delete article, comment add/delete, follow/unfollow, `GET /user`) | yes | 1–3 point operations each, plus relation reloads on follow/create | mostly | no | Covered by PERF-001/010 where applicable |

**Amplification points:** On `GET /api/articles` and `/feed`, rows read and serialized ≈ page size × (1 + favorites per article + followers per author). Page size is client-controlled, and both relation sizes grow with usage.

**Paths deliberately not analyzed in depth:** `GET /` (static JSON) and static asset serving (`express.static`). Both are trivial and do no datastore work.

---

## 6. Layer analysis

### 6.1 Application

Handlers are thin `async` wrappers that `await` service calls. There is no synchronous filesystem or network I/O in request paths. The one CPU-heavy operation is bcryptjs hashing (PERF-007). Mapping code does linear `.some()` scans over relation arrays (`article.mapper.ts:11`, `author.mapper.ts:8`, `profile.utils.ts:9`). That CPU cost is small per item, but it exists only because whole relations were loaded (PERF-001).

### 6.2 API

Pagination uses offset/limit with no server maximum (PERF-002). The comments endpoint has no pagination at all (PERF-003). Every list response carries an exact total count (PERF-005). JSON body parsing uses `body-parser` defaults. The 100 kb default limit bounds request bodies, which was checked: there is no explicit override in `src/main.ts:14-15`.

### 6.3 Data access and datastore

All access goes through a single Prisma client (`prisma-client.ts:17`). In production, module caching makes it one instance per process, so there is no client-per-request problem. Prisma 4 loads `include` relations with one batched query per relation level, so there is **no classic N+1 query count** here. The cost is the *volume* of related rows (PERF-001), plus missing indexes on the foreign-key and sort columns those queries filter and order by (PERF-004). Implicit many-to-many join tables (`_UserFavorites`, `_UserFollows`, `_ArticleToTag`) are indexed on both columns, which was checked: `migration.sql:78-87`, `implicit_tags/migration.sql:24-27`.

### 6.6 Infrastructure

One Dockerfile, a floating `node:lts-alpine` base, and a single `node api` process with no cluster/worker setting, `--max-old-space-size`, or `UV_THREADPOOL_SIZE` (PERF-009). There are no orchestration files, so replica count is unknown.

### 6.7 Observability

There is none (PERF-008): no request logging, latency metrics, Prisma query logging, event-loop lag monitoring, or pool metrics. None of the optimizations below can be validated in production until a minimum of instrumentation exists.

---

## 7. Findings

### PERF-001 — Every article/profile response loads full favoriter and follower lists to compute a boolean and a count

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:84` (`getArticles`) |
| **Tags** | scalability-risk |

**Problem**
Article queries `include` `favoritedBy: true` (every `User` row, all columns, that favorited the article) and `author.followedBy: true` (every `User` row that follows the author). The code then uses these lists only for `favorited` (is the current user in the list?), `favoritesCount` (list length) and `following` (is the current user in the list?). The data loaded grows with each article's popularity and each author's audience, not with the response.

**Performance principle**
Fetch only what the response needs. Membership tests and counts should be answered by the datastore as O(1)/indexed lookups, not by transferring whole collections into the application.

**Evidence**
- Include shape with full relation loads: `article.service.ts:90-98` (`getArticles`), `139-147` (`getFeed`), `221-229` (`createArticle`), `252-260` (`getArticle`), `365-373` (`updateArticle`), `580-588` / `626-634` (favorite/unfavorite). Comment authors: `article.service.ts:447-453`, `503-508`.
- Consumers use only membership and length: `article.mapper.ts:11-12`, `author.mapper.ts:7-9`, `article.service.ts:466, 522`.
- `_count.favoritedBy` is requested (`article.service.ts:99-103`) and then **ignored** by `articleMapper`, which uses `favoritedBy.length` instead (`article.mapper.ts:12`). The count is computed twice.
- Profile paths: `profile.service.ts:10-12, 34-36, 54-56` load the full `followedBy` list for the same membership test (`profile.utils.ts:8-10`).
- No runtime evidence exists.

**Impact**
- Position: critical path, on every article read, the comment list and profile reads.
- Frequency: per request, per item on list pages.
- Growth: O(n) in favorites and followers per item, multiplied by page size. Derivation: rows ≈ page × (1 + favorites/article + followers/author).
- Blast radius: service. Rows are materialized and mapped on the single event loop and transferred over the shared pool.

**Conditions**
This matters once popular articles have favorite counts, or popular authors have follower counts, that are large relative to page size: roughly hundreds or more per item. The repository has no row-count evidence. Both join tables are append-only with no cap, so unbounded growth is the default expectation. Workload is unknown, so confidence is capped at Medium.

**Counter-evidence**
- Looked for N+1 query shape: Prisma batches each relation level into one query, so query *count* is constant per request. This bounds the query-count growth the finding might otherwise claim. Severity is High, not Critical. (bounds-impact)
- Looked for an upstream cap on favorites/followers: none found. (no-effect)
- Join tables are indexed on both columns (`migration.sql:78-87`), so each relation query is an index lookup. The cost is row volume, not scanning. (no-effect on the mechanism)

**Why this might not matter**
In a demo-scale deployment like the seed (12 users, so at most 12 favoriters/followers per item), the extra rows are trivial. The per-request cost stays negligible until relations reach sizes the repository gives no evidence of.

**Recommendation**
Ask the database the questions the response asks. Use `_count: { select: { favoritedBy: true } }` for the count (already present), and pass that into the mapper. For `favorited` / `following`, include the relation **filtered to the current user**, for example `favoritedBy: { where: { id: currentUserId }, select: { id: true } }` and `followedBy: { where: { id: currentUserId }, select: { id: true } }`. Skip it entirely for anonymous requests. Apply the same change to profile queries. This removes the work instead of hiding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: filtered relation include + `_count` | **preferred** | Bounded at ≤1 row per relation per item; no schema change; keeps one query per relation level |
| B: denormalized `favoritesCount` column maintained on favorite/unfavorite | | Faster count, but adds write-path consistency burden; unnecessary while `_count` is index-backed |
| C: cache the article payloads | | Masks the work; per-user `favorited`/`following` makes cache keys user-specific; adds invalidation on every favorite/follow |

**Trade-offs**
The mapper signatures change. Anonymous and authenticated requests take slightly different query shapes. `_count` adds a grouped count per page, which the code already pays for today.

**Validation**
See §9 PERF-001.

---

### PERF-002 — List endpoints accept an unbounded client-supplied `limit` (and unbounded `offset`)

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.controller.ts:30` (`GET /articles`) → `article.service.ts:82-83` |
| **Tags** | quick-win |

**Problem**
`take: Number(query.limit) || 10` and `skip: Number(query.offset) || 0` pass client input straight to the query with no maximum. A single request can ask for every article, with all PERF-001 relation rows, in one response. `/articles/feed` does the same (`article.service.ts:131-132`).

**Performance principle**
Bound all work set by input you do not control. The server, not the client, must own the maximum cost of a request.

**Evidence**
- `article.service.ts:82-83` (`getArticles`) and `131-132` (`getFeed`); controller passes `req.query` / `Number(req.query.limit)` directly (`article.controller.ts:32, 50-54`).
- No validation middleware, gateway config or rate limiter in the repository (`src/main.ts:13-16`).
- No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in total articles, because the page size becomes the table size. Each item also carries the PERF-001 relation rows.
- Blast radius: service. The whole result is materialized, mapped and `JSON.stringify`'d synchronously on the single event loop, stalling every concurrent request, and it holds a pooled connection for the duration.
- Deep `offset` also makes PostgreSQL produce and discard the skipped rows (cost ∝ offset).

**Conditions**
This requires one caller sending a large `limit` or `offset`, and is independent of traffic volume. Severity grows with table size, which is assumed to grow. If a gateway in front of the service caps query parameters, this is bounded (question 4).

**Counter-evidence**
- Looked for a framework or ORM default maximum: Prisma has none. Express does not validate query parameters. (no-effect)
- Looked for a gateway or reverse-proxy config in the repo: none present. Its absence is not proof that none exists in production. (no-effect: confidence remains High because the code path itself is unambiguous)

**Why this might not matter**
If the deployment is private or behind a gateway that strips or caps `limit`, and the table is small, the worst case is a slow but harmless response.

**Recommendation**
Clamp `limit` to a server maximum (for example `Math.min(Math.max(limit,1), MAX)`, where MAX is a product decision) and reject a negative or non-numeric `offset`. Apply the same clamp in `getFeed`. If deep paging becomes common, consider keyset pagination on `(createdAt, id)` (this depends on PERF-004's index).

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: server-side clamp in the service | **preferred** | Removes the unbounded case at its source; trivial; no API change for well-behaved clients |
| B: gateway/proxy parameter cap | | Works only where that proxy exists; not visible or testable in this repo |
| C: keyset pagination | | Fixes deep-offset cost too, but changes the API contract; do later if deep paging is observed |

**Trade-offs**
Clients that relied on very large pages get truncated results. Choosing MAX is a product decision.

**Validation**
See §9 PERF-002.

---

### PERF-004 — Foreign-key and sort columns on `Article` and `Comment` are unindexed

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/prisma/schema.prisma:11` (`model Article`), also `model Comment` (`schema.prisma:26`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
The migrations create no index on `Comment.articleId`, `Comment.authorId`, `Article.authorId` or `Article.createdAt`. Prisma does not create foreign-key indexes for PostgreSQL. Each hot read path filters or sorts on one of these columns:
- the comments list filters `Comment` by `articleId`;
- the feed filters `Article` by `authorId IN (followed)`;
- every list sorts by `createdAt DESC` with `LIMIT`.

**Performance principle**
A predicate or sort over a growing table needs an access path proportional to the result, not to the table.

**Evidence**
- `migrations/20210924225358_initial/migration.sql:2-13, 24-33, 68-87`: only primary keys, unique slug/email/username, and join-table indexes. No later migration adds any (`20211001143221`, `20211105153605`, `20211221184529`).
- Query predicates: `article.service.ts:433-458` (comments by article), `114-130` (feed by author's followers), `79-81` and `128-130` (`orderBy createdAt desc`).
- Foreign-key cascades on `Comment.articleId` and `Article.authorId` (`migration.sql:90-102`) also need these columns indexed to avoid scans on delete.
- No `EXPLAIN` output or runtime evidence.

**Impact**
- Position: critical path (article list, feed, comments).
- Frequency: per request.
- Growth: O(n) in total `Comment` rows per comments request, and O(n) in matching `Article` rows to sort before `LIMIT`. Derivation: without an index on `createdAt`, the "top 10 by date" query must examine every row that matches the filter.
- Blast radius: system-wide. Sequential scans on the single primary consume shared buffers and I/O used by every other query.

**Conditions**
This matters once `Comment` and `Article` hold more rows than fit cheaply in a scan, roughly tens of thousands or more. Both tables are append-only with no retention, so they grow with every write. The current sizes are unknown, so confidence is capped at Medium.

**Counter-evidence**
- Searched all four migrations for `CREATE INDEX` on these columns: none. (no-effect)
- At seed scale (144 articles, 1,728 comments: derived in §4), PostgreSQL would correctly choose a sequential scan, and the cost is negligible. This is why confidence is Medium and the finding is tagged `scalability-risk`. (lowers-confidence)

**Why this might not matter**
On small tables, sequential scans are optimal and indexes add write cost for nothing. If production data is demo-sized, this finding is P3.

**Recommendation**
Add a migration with `@@index([articleId])` on `Comment`, and `@@index([authorId])` and `@@index([createdAt])` on `Article`. A composite `([authorId, createdAt])` would serve the feed's filter and sort together. Choose after looking at `EXPLAIN` for the actual queries. Build with `CREATE INDEX CONCURRENTLY` on an existing large table. Prisma migrate does not do this by default, so edit the generated SQL.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: targeted single/composite indexes chosen from `EXPLAIN` | **preferred** | Directly gives the queries an access path; small, well-understood cost |
| B: index every foreign key blindly | | Covers cascades too, but `Comment.authorId` has no hot read path today; pays write cost without a confirmed reader |
| C: cache list/comment responses | | Hides scans; per-user visibility filters (`demo OR id = me`) fragment the cache |

**Trade-offs**
Every index adds insert cost and storage on `Article`/`Comment`. A non-concurrent build on a large table blocks writes.

**Validation**
See §9 PERF-004.

---

### PERF-003 — Comment list for an article is returned in full, with no pagination

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:433` (`getCommentsByArticle`) |

**Problem**
`GET /articles/:slug/comments` returns every visible comment for the article, each with its author's full follower list (PERF-001). Neither the service nor the controller has a `take`.

**Performance principle**
Bound list responses on the server.

**Evidence**
`article.service.ts:433-458` (no `take`/`skip`); controller `article.controller.ts:149-160` takes no paging parameters. No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in comments per article, plus followers per commenter.
- Blast radius: endpoint, extending to the event loop for very large articles.

**Conditions**
This matters for articles with many comments: a popular article could attract hundreds or more. The comment distribution is unknown, so confidence is Medium. The RealWorld API spec defines this endpoint without pagination, so changing it is a contract decision.

**Counter-evidence**
- The spec-defined response shape (no pagination) explains the design. This lowers the case for a code change, not the mechanism. (no-effect on scores)
- Comments are filtered to `author.demo OR author.id = me` (`article.service.ts:417-431`). This bounds visible comments for most viewers to demo users' comments plus their own, which partially bounds result size. (bounds-impact: Medium rather than High)

**Why this might not matter**
Real articles in this app may rarely exceed a few dozen comments, and the visibility filter further limits the rows returned.

**Recommendation**
Add an optional server-capped `limit` (default kept at the current behaviour up to a cap) ordered by `createdAt`. Fix the per-comment follower load as part of PERF-001.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: server cap with optional `limit`/`offset` | **preferred** | Bounds the worst case while staying compatible with spec clients for typical articles |
| B: leave as-is and rely on PERF-001 fix | | Removes the per-comment amplification but leaves comment count unbounded |

**Trade-offs**
The response may be truncated for spec clients that expect every comment. Clients need to be told about pagination.

**Validation**
Count rows returned for the article with the most comments (`SELECT "articleId", count(*) FROM "Comment" GROUP BY 1 ORDER BY 2 DESC LIMIT 5`, safe-on-production). After the fix, the response row count must not exceed the cap. Falsifier: if no article exceeds the cap, the fix has no current effect.

---

### PERF-005 — Every list request runs an exact `COUNT` over the full filtered set, serially before the page query

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:114` (`getFeed`); same pattern at `getArticles` `:71` |
| **Tags** | needs-measurement |

**Problem**
`prisma.article.count` with the same relation filter runs before `findMany` on every list and feed request. The two independent queries are awaited one after the other.

**Performance principle**
Avoid whole-set aggregation for per-request metadata, and do not serialize independent I/O.

**Evidence**
`article.service.ts:71-75` then `77-105`; `114-120` then `122-154`. The relation filter (`author.demo OR author.id`, or `author.followedBy some`) joins `User` or `_UserFollows`. `User.demo` is unindexed (migrations). No runtime evidence.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in matching articles, because the count traverses every match regardless of page size.
- Blast radius: service (primary DB CPU/I/O).
- Latency adds the count's round trip in series with the list query.

**Conditions**
This matters once matching articles number in the many thousands and list traffic is frequent. Both are unknown, so confidence is Medium.

**Counter-evidence**
- The RealWorld spec requires `articlesCount`. Removing it is a contract change, which limits the recommendation rather than the mechanism. (no-effect)
- At seed scale the count is trivial. (lowers-confidence: already reflected)

**Why this might not matter**
Counts over a few thousand rows are cheap, and the serial round trip is a small constant on a local database.

**Recommendation**
First issue the count and the page query concurrently (`Promise.all` or `prisma.$transaction([...])`). This removes the serial wait at no semantic cost. If measurements show the count itself is expensive, consider a "has more" flag or a bounded/approximate count. That is a product decision.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: run count and page concurrently | **preferred** | Removes one serial round trip; no contract change |
| B: approximate or capped count | | Removes the whole-set scan but changes API semantics |
| C: cache counts | | Per-user visibility filter makes counts user-specific; invalidation on every article write |

**Trade-offs**
Concurrent queries hold two pool connections per request instead of one at a time, which matters if the pool is small (unknown). `$transaction` gives a consistent snapshot at the cost of holding a transaction.

**Validation**
With Prisma query logging in staging, confirm both queries overlap in time after the change. Use `pg_stat_statements` mean time of the count query to decide on option B (safe-on-production). Falsifier: if the count's mean time is negligible relative to the list query, B is not needed.

---

### PERF-006 — `GET /tags` aggregates over every article–tag pair on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | data-access |
| **Location** | `src/app/routes/tag/tag.service.ts:16` (`getTags`) |
| **Tags** | needs-measurement |

**Problem**
The top-10 tags are computed per request by ordering all tags by `_count` of articles, restricted to articles whose author is `demo` or the caller. That requires grouping across `_ArticleToTag`, `Article` and `User` before taking 10.

**Performance principle**
Do not recompute a whole-table aggregate on every request when the result changes slowly.

**Evidence**
`tag.service.ts:16-35`. The `take: 10` bounds output, not work. No runtime evidence.

**Impact**
- Position: critical path (home page, assumed).
- Frequency: per request.
- Growth: O(n) in article–tag pairs.
- Blast radius: service (primary DB).

**Conditions**
This matters when `_ArticleToTag` is large and `/tags` is called on every page view (assumed from the RealWorld front end). Both are unknown.

**Counter-evidence**
The join table has indexes on `(A,B)` and `B` (`implicit_tags/migration.sql:24-27`), so the joins are index-supported. Only the grouping is whole-set. (bounds-impact: Medium rather than High)

**Why this might not matter**
At modest tag/article counts this is a fast aggregate. It changes only when articles are created or edited.

**Recommendation**
Measure first. If the query is significant in `pg_stat_statements`, compute popular tags off the request path. A periodically refreshed materialized view or table is the right shape. The per-user `OR id = me` term complicates this: the shared result is computable for demo authors, and only the caller's own articles need a small per-request merge. Caching here has a clear staleness tolerance: popular tags can lag by minutes.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: measure; then precompute demo-author popularity periodically | **preferred** | Removes repeated whole-set work, with an explicit staleness budget |
| B: in-process cache with short TTL | | Simpler, but per-process and per-user variants; still stampedes on expiry |
| C: no change | | Correct if the query is cheap at real data volume |

**Trade-offs**
Precomputation adds a refresh job and staleness. The per-user term needs separate handling.

**Validation**
Use `pg_stat_statements` for this query's `calls` and `mean_exec_time` (safe-on-production). After the change, the per-request query should be a point read. Falsifier: if the mean time is already negligible, take no action.

---

### PERF-007 — Password hashing runs in pure JavaScript on the single event loop

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `src/app/routes/auth/auth.service.ts:111` (`login`); also `:58` (`createUser`), `:156` (`updateUser`) |
| **Tags** | needs-measurement |

**Problem**
`bcryptjs` is a pure-JavaScript implementation. Its async API splits the work into chunks but still executes all of it on the main thread, competing with every other request in the one Node process (cost factor 10, `auth.service.ts:58,156`).

**Performance principle**
CPU-bound work must not share the thread that services all I/O. Offload it, or bound its concurrency.

**Evidence**
`package.json:19` (`bcryptjs`), `package-lock.json` 2.4.3; calls at `auth.service.ts:58, 111, 156`. Single process: `Dockerfile:24`. No profile or event-loop lag data.

**Impact**
- Position: critical path (login and register), with side effects on all concurrent requests.
- Frequency: per login/register/password change.
- Growth: O(1) per call, linear in login rate.
- Blast radius: service (event loop).

**Conditions**
This matters when login or registration bursts coincide with read traffic on the same process. The login rate is unknown, so confidence is Medium.

**Counter-evidence**
- The async API yields between chunks, so the loop is not blocked for the whole hash, only saturated by it. (bounds-impact: Medium, not High)
- The hashing is necessary work. The issue is where it runs, not that it runs. (no-effect)

**Why this might not matter**
If logins are infrequent relative to reads, the main-thread CPU share is negligible.

**Recommendation**
Measure event-loop delay (`perf_hooks.monitorEventLoopDelay`) during a login burst in staging. If lag is material, switch to a native implementation that runs on the libuv thread pool (for example `bcrypt` or `argon2`). Existing `$2a$` hashes remain verifiable by native bcrypt.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: measure, then native bcrypt/argon2 off the main thread | **preferred** | Moves CPU off the event loop without changing the hash format (bcrypt) |
| B: `worker_threads` pool running bcryptjs | | Works without native builds; more code to own |
| C: lower cost factor | | Weakens password security; rejected |

**Trade-offs**
Native modules need build tooling in the Alpine image. Libuv pool work contends with other pool users (default size 4, `UV_THREADPOOL_SIZE` unset).

**Validation**
`perf_hooks.monitorEventLoopDelay()` p99 during a scripted login loop in staging (not-safe-on-production as a load generator). Expectation: lag during logins falls after the change. Falsifier: lag is already negligible before the change.

---

### PERF-008 — No request, query or event-loop instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | observability |
| **Location** | `src/main.ts:13` (app middleware setup) |
| **Tags** | quick-win |

**Problem**
Nothing measures request duration, per-route rates, query time, pool wait, or event-loop lag. The `PrismaClient` is constructed without `log` options (`prisma-client.ts:17`).

**Performance principle**
A system that is not measured cannot have its bottlenecks confirmed or its fixes validated.

**Evidence**
`src/main.ts:13-16`: only `cors` and `body-parser`. There are no logging or metrics dependencies in `package.json:15-27`, and no APM, OpenTelemetry or Prometheus anywhere in `src/` (searched).

**Impact**
- Position: all critical paths.
- Frequency: per request.
- Growth: O(1).
- Blast radius: service. It is the reason every finding in this review is unconfirmed.

**Conditions**
This applies at any traffic level. It matters as soon as anyone needs to decide which of PERF-001 to PERF-007 to act on first.

**Counter-evidence**
Searched for logging and metrics libraries, `/metrics` routes, and Prisma `log` / `$on('query')`: none found. Instrumentation could exist outside the repo (for example a platform load balancer's logs). That is unknown. (no-effect)

**Why this might not matter**
A hosting platform may already provide request latency at the edge. Even so, it would not show query or event-loop data.

**Recommendation**
Add, in this order:
1. A request-duration log or histogram per route and status.
2. `monitorEventLoopDelay` exported periodically.
3. Prisma query logging or metrics in staging.
4. `pg_stat_statements` on the database.

These are the measurements that confirm or refute PERF-001 to PERF-007.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| A: minimal structured timing + event-loop delay + `pg_stat_statements` | **preferred** | Cheap; answers the open questions in §4 directly |
| B: full OpenTelemetry tracing | | Richer, but more setup than needed to rank these findings |

**Trade-offs**
There is small per-request overhead, and logs can carry data volume and cost. Verbose query logging should stay off busy production systems.

**Validation**
After deployment, the route-latency and event-loop-delay series exist and populate under normal traffic. Falsifier: n/a (presence check).

---

### PERF-009 — Single Node process; no parallelism, heap, or pool settings visible

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | infrastructure |
| **Location** | `Dockerfile:24` (`CMD`) |
| **Tags** | needs-measurement |

**Problem**
The container runs one `node api` process with no cluster, worker count, `--max-old-space-size`, `UV_THREADPOOL_SIZE` or Prisma `connection_limit`. The base image tag `node:lts-alpine` floats.

**Performance principle**
Capacity configuration should be explicit and match the resources the deployment provides.

**Evidence**
`Dockerfile:7, 24`; `prisma-client.ts:17`. There are no orchestration files.

**Impact**
- Position: all paths.
- Frequency: per request.
- Growth: O(1).
- Blast radius: service.

**Conditions**
This matters only if production gives a container multiple cores and runs one container. That is unknown.

**Counter-evidence**
Horizontal scaling by running many single-process containers is a valid design, and it is not visible here. (lowers-confidence)

**Recommendation**
Record the intended process and instance count. Pin the Node major version. Set `connection_limit` explicitly so that instances × pool size stays within PostgreSQL `max_connections`.

**Trade-offs**
More processes multiply DB connections and memory.

**Validation**
Compare container CPU allotment with process count in the deployment config. Falsifier: the deployment already runs N containers at one core each.

---

### PERF-010 — `updateArticle` makes four sequential round trips and resets tags outside a transaction

| | |
|:--|:--|
| **Root cause** | `ROOT-009` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:289` (`updateArticle`) |

**Problem**
An update issues four sequential statements:
1. `findFirst` by slug;
2. optional `findFirst` for the new slug;
3. `disconnectArticlesTags` (an `update` clearing all tags);
4. the final `update` with `connectOrCreate` per tag, followed by the relation reloads from PERF-001.

`createArticle` similarly does a pre-check and a `connectOrCreate` per tag. The tag list is uncapped (`article.service.ts:164, 335-341`).

**Performance principle**
Collapse dependent writes into one round trip or transaction, and bound per-item write work.

**Evidence**
`article.service.ts:292-304, 320-327, 343, 345-380`; `276-287`; `180-187, 203-208`.

**Impact**
- Position: critical path (write).
- Frequency: per request.
- Growth: O(n) in tags supplied by the client.
- Blast radius: endpoint.

**Conditions**
Writes are assumed far less frequent than reads. The issue becomes material only with large tag lists.

**Counter-evidence**
`findFirst` on `slug` hits the unique index, so each step is cheap. (bounds-impact: Low)

**Recommendation**
Use `tagList: { set: [], connectOrCreate: [...] }` in the single update, or wrap the steps in `prisma.$transaction`, and cap the number of tags. Rely on the unique constraint (catch `P2002`) instead of pre-checking the slug.

**Trade-offs**
Error handling moves from pre-checks to constraint-violation mapping.

**Validation**
Query log in staging: statement count per update falls from four plus one per tag to one transaction. Safe on staging.

---

### Remaining findings

All findings are in full format above. Ranked summary:

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-001 | High | Medium | P1 | `article.service.ts:84` | Full favoriter/follower lists loaded per article/profile |
| PERF-002 | High | High | P1 | `article.controller.ts:30` / `article.service.ts:82` | Unbounded client `limit`/`offset` |
| PERF-004 | High | Medium | P1 | `schema.prisma:11` | Unindexed `Article.authorId`, `Article.createdAt`, `Comment.articleId` |
| PERF-003 | Medium | Medium | P2 | `article.service.ts:433` | Comments returned without pagination |
| PERF-005 | Medium | Medium | P2 | `article.service.ts:114` | Exact count per list request, serial with page query |
| PERF-006 | Medium | Medium | P2 | `tag.service.ts:16` | Whole-set tag popularity aggregate per request |
| PERF-007 | Medium | Medium | P2 | `auth.service.ts:111` | Pure-JS bcrypt on the event loop |
| PERF-008 | Medium | High | P2 | `src/main.ts:13` | No instrumentation |
| PERF-009 | Low | Medium | P3 | `Dockerfile:24` | Single process; capacity config implicit |
| PERF-010 | Low | High | P3 | `article.service.ts:289` | Sequential update round trips; uncapped tag list |

### Considered and not reported

- **Prisma client instantiated per request / per module.** Checked `prisma-client.ts:17-21` and all imports. Production uses one module-cached instance. The seed script creates its own client, but it is offline. Discarded: refuted. Revisit if new code calls `new PrismaClient()` inside handlers.
- **Classic N+1 via `include`.** Checked all `include` uses. Prisma 4 batches each relation level into one query, so query count does not scale with rows. Discarded as N+1. The real cost (row volume) is PERF-001. Revisit if query logging shows per-row queries.
- **Large JSON request bodies blocking the loop.** Checked `bodyParser.json()` (`main.ts:14`). The default 100 kb limit applies. Discarded: bounded. Revisit if the limit is raised.

### Adjacent findings — outside performance scope

### SEC-001 — Favorite/unfavorite responses serialize full `User` rows, including password hashes and emails

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `src/app/routes/article/article.service.ts:597-605` (`favoriteArticle`), `643-651` (`unfavoriteArticle`) |

**Problem** The result object spreads `...article`, which still contains `favoritedBy`: every `User` row with all columns, because `favoritedBy: true` at `:588` / `:634`. It also contains `authorId` and `id`. That object is returned as JSON (`article.controller.ts:215-216, 235-236`).

**Evidence** `article.service.ts:563-605`: only `_count` is destructured out, and `author` and `tagList` are overwritten, but `favoritedBy` is not. `schema.prisma:43-49`: `User` has `email` and `password`. By contrast, `articleMapper` (`article.mapper.ts:3-14`) whitelists fields and does not leak.

**Impact** Any authenticated user can retrieve the bcrypt hashes and email addresses of everyone who favorited an article, by favoriting it. That is credential and PII exposure.

**Recommendation** Return `articleMapper(...)` from both functions, as the other paths do. Also apply the PERF-001 change, so full `User` rows are never selected.

**Trade-offs** None material. The response shape converges on the documented one.

**Validation** Call `POST /api/articles/:slug/favorite` against a local seeded DB and assert that the response has no `favoritedBy`, `password` or `email` keys. Add this as a unit test.

**Would need** A dedicated security review of all response serialization paths.

### SEC-002 — JWT signing secret falls back to a hard-coded default

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/app/routes/auth/auth.ts:16,21`, `src/app/routes/auth/token.utils.ts:4` |

**Problem** If `JWT_SECRET` is unset, tokens are signed and verified with a hard-coded literal (value not reproduced here).

**Evidence** `process.env.JWT_SECRET || '<literal>'` in three places.

**Impact** A deployment that forgets the variable accepts forged tokens for any user id. The Risk is Medium because the README marks the variable as required.

**Recommendation** Fail at startup when `JWT_SECRET` is missing.

**Trade-offs** None.

**Validation** Starting without `JWT_SECRET` exits with an error.

**Would need** A security review of authentication configuration.

### COR-001 — `updateArticle` clears tags before the update, outside a transaction

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Low |
| **Location** | `src/app/routes/article/article.service.ts:343-380` |

**Problem** `disconnectArticlesTags` commits before the main `update`. If the update fails, for example on a slug unique-constraint race, the article is left with no tags. Separately, `addComment` with an unknown slug passes `id: undefined` to `connect` (`:487-493`), which surfaces as a 500 instead of a 404.

**Evidence** Cited lines. There is no `$transaction` anywhere in the codebase.

**Impact** Tag data is lost on failed updates. The comment error is mapped incorrectly. Both are Low risk, being data-integrity edge cases.

**Recommendation** Fold the tag reset into the single update (`set` + `connectOrCreate`) or a transaction. Return 404 when the article is missing.

**Trade-offs** None material.

**Validation** Unit tests that simulate update failure and an unknown slug.

**Would need** A correctness-focused test pass on write paths.

---

## 8. Prioritized action plan

### P1 — High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-002 | P1 | Small | Workload-independent, removes the multiplier on PERF-001; quick-win |
| 2 | PERF-001 (+ SEC-001) | P1 | Small–medium | Same edit fixes a data leak; removes unbounded per-item work |
| 3 | PERF-004 | P1 | Small (migration) + `EXPLAIN` | Confirm with plans and row counts first; concurrent build if tables are large |

### P2 — Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 0 (do first, alongside #1) | PERF-008 | P2 | Small | Sequenced before everything despite P2: without it none of the P1 fixes can be validated in production |
| 4 | PERF-005 | P2 | Small | `Promise.all` is trivial; count redesign only if measured |
| 5 | PERF-003 | P2 | Small | Contract decision needed |
| 6 | PERF-006 | P2 | Medium | Measure first |
| 7 | PERF-007 | P2 | Medium | Measure event-loop lag first |

### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 8 | PERF-010 (+ COR-001) | P3 | Small | Correctness benefit justifies doing it with PERF-001 edits |
| 9 | PERF-009 | P3 | Small | Document deployment intent; set pool size explicitly |

**If only one thing is done:** clamp `limit` in `getArticles`/`getFeed` (PERF-002). Then, in the same pull request, replace the full `favoritedBy`/`followedBy` includes with `_count` plus a current-user-filtered include (PERF-001), which also closes SEC-001.

---

## 9. Validation plan

### PERF-001

- **Baseline:** In staging with production-shaped data, enable Prisma query logging and record rows returned by the `_UserFavorites`/`User` and `_UserFollows`/`User` relation queries for one `GET /api/articles` page. Not safe on production (verbose logging).
- **Change:** `_count` plus a relation include filtered to the current user.
- **Measurement:** Relation rows per request; response size.
- **Expectation:** Relation rows fall from Σ(favorites + followers) on the page to at most one per relation per article. Response size becomes independent of popularity. Latency change is unquantified until measured.
- **Falsifier:** If relation rows are already ≤ page size (low popularity), the finding's impact was overstated. Re-rank to P3.
- **Guard:** A unit test asserting that the `include` passed to `prisma.article.findMany` uses a filtered `favoritedBy`/`followedBy` (the existing `prisma-mock.ts` supports call inspection).
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'SELECT "A" AS article_id, count(*) FROM "_UserFavorites" GROUP BY 1 ORDER BY 2 DESC LIMIT 5;'`. Purpose: size the largest favorite lists.
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'SELECT "B" AS followed_user, count(*) FROM "_UserFollows" GROUP BY 1 ORDER BY 2 DESC LIMIT 5;'`. Purpose: size the largest follower lists. The column direction depends on Prisma's A/B assignment; run with both `"A"` and `"B"`.

### PERF-002

- **Baseline:** Against a local seeded DB, measure response bytes and time for `limit=10` and `limit=100000`. Not safe on production.
- **Change:** A server-side clamp.
- **Measurement:** Returned `articles.length` and response bytes for an oversized `limit`.
- **Expectation:** `articles.length ≤ MAX` regardless of the requested `limit`.
- **Falsifier:** If the oversized request already returns ≤ MAX (some upstream cap), the finding is refuted.
- **Guard:** A unit test calling `getArticles({limit: '1000000'})` and asserting that the `take` passed to Prisma is ≤ MAX.
- **Commands:**
  - `not-safe-on-production`: `curl -s -o /dev/null -w "%{size_download} bytes %{time_total}s\n" "http://localhost:3000/api/articles?limit=100000"`. Purpose: show the response scales with table size, not page size.

### PERF-004

- **Baseline:** Get plans for the comments, feed and list queries on a replica or staging copy.
- **Change:** An index migration (concurrent build).
- **Measurement:** Plan node type and buffers read; `seq_scan` versus `idx_scan` deltas in `pg_stat_user_tables`.
- **Expectation:** `Seq Scan on "Comment"` becomes an index scan on `articleId`. The list sort becomes an index-ordered scan with early `LIMIT` termination.
- **Falsifier:** If the planner still chooses sequential scans after `ANALYZE` (tables too small), the indexes are unnecessary. Drop them and demote to P3.
- **Guard:** A migration test or CI check that the indexes exist. Watch `idx_scan` on the new indexes.
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup FROM pg_stat_user_tables ORDER BY seq_tup_read DESC;'`. Purpose: see whether `Comment`/`Article` are scanned and how large they are.
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'EXPLAIN SELECT * FROM "Comment" WHERE "articleId" = 1;'`. Purpose: the plan without executing.
  - `not-safe-on-production` (run on a replica): `psql "$DATABASE_URL" -c 'EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM "Article" ORDER BY "createdAt" DESC LIMIT 10;'`. Purpose: actual rows examined for the sort.

### PERF-003, PERF-005, PERF-006, PERF-007, PERF-010

See the Validation paragraph in each finding.

### Instrumentation gaps to close first

PERF-008 comes before any optimization. Add per-route request duration, `monitorEventLoopDelay`, and enable `pg_stat_statements`. Without these, the P1 fixes can be shown to work only in staging, not in production.

---

## 10. Machine-readable output

Emitted alongside this report at `node-guided-1.json` (conforms to `schemas/review.schema.json`). If the two disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade. `Confirmed` requires a cited runtime artifact, and none exists here.
- No runtime metric in this report was estimated or assumed. The only numbers are read from files (versions, seed counts, default `limit` 10, tag `take` 10, bcrypt cost 10), plus the seed-volume derivation labelled in §4.
- Recommendations state their trade-offs and validation path.
