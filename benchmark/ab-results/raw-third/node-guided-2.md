# Performance Review: node-express-realworld (RealWorld API, Express + Prisma + Postgres)

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0
**Commit:** `30b68e1e881462b2f4164ea09ab4c4f5699c7b0b`

---

## 1. Decision summary

### Overall assessment

This is a small, single-process Express/Prisma API. Its request handlers are thin, but its **data-access shapes scale with total data rather than page size**. Every article response loads the full row of every favoriting user and every author follower. List and feed have no page-size cap. Comments and article listing run against unindexed foreign-key and sort columns. `/tags` re-aggregates the whole article-tag relation on every call. None of this is measured. The repository has no instrumentation, no workload data and no query plans, so every workload-dependent finding is capped at `Medium` confidence. The ranking is inferred from code structure. **Review confidence: Medium.** I read all of the code, but nothing about runtime behaviour or data volume is known. Separately, a real **security leak** turned up (SEC-001): the favorite endpoints return users' password hashes and emails.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Replace `favoritedBy: true` / `followedBy: true` with viewer-filtered `select: {id}` plus `_count`. Apply this at every include site. | It removes per-response work that grows with follower and favorite counts. The same change closes SEC-001. |
| 2 | **PERF-002 (P1)** | Clamp `limit` (and validate `offset`) in `getArticles` and `getFeed`. | Right now one anonymous request can materialise the whole Article table plus its relations. The fix is one line. |
| 3 | **PERF-003 / PERF-004 (P1)** | Add indexes on `Comment.articleId`, `Article(authorId, createdAt)` and `Article(createdAt)`, built concurrently. Check the `EXPLAIN` plans first. | These are the primary filter and sort columns of the hottest read paths. Today they scan the whole table. |

PERF-007 (instrumentation, P3) should be sequenced alongside action 1. Without it, none of the P1 changes can be validated in production.

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Row counts, plus the largest follower and favorite counts | Small tables drop PERF-001/003/004/005 from P1 to P2/P3 | `pg_stat_user_tables` and a GROUP BY on `_UserFollows`/`_UserFavorites` (§9) |
| Whether arbitrary clients can reach the API without a gateway cap | Moves PERF-002 between P1 and P2 | Deployment/gateway config |
| Peak request rate for `/articles`, `/tags`, comments | Decides whether the per-request aggregates (PERF-004/005) threaten DB CPU | Request-duration metric or access logs (PERF-007) |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| Row counts (PERF-001/003/004) | `safe-on-production` | `psql "$DATABASE_URL" -c 'SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;'` | Large tables with high `seq_scan` confirm the P1 ranking. Small ones demote it. |
| PERF-003 | `safe-on-production` | `psql "$DATABASE_URL" -c 'EXPLAIN SELECT id FROM "Comment" WHERE "articleId" = 1;'` | A Seq Scan on a large table confirms the missing index |
| PERF-005 / all | `safe-on-production` | `psql "$DATABASE_URL" -c 'SELECT query, calls, total_exec_time, mean_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;'` | Ranks the real DB cost of the list, tags and comments statements (if the extension is enabled) |

---

## 2. Scope and method

**Reviewed:**
- All application source under `src/`: routes, services, mappers, Prisma client, `schema.prisma`, all four migrations and `seed.ts`.
- `Dockerfile`, `project.json`, `package.json`, the `package-lock.json` versions, and `README.md`.
- Tests and e2e, read only to look for benchmarks or load tests. There are none.

**Not reviewed:**
- `node_modules`, which is not present in the checkout. Library internals (Prisma query engine SQL generation, bcryptjs chunking) are therefore stated from the code's usage, not verified against the library source.
- Any production deployment config. None exists in the repository.

**Evidence available:** Uninstrumented. The repository has no metrics, tracing, request or query logging, benchmarks, load tests, SLOs or dashboards. The only log line is `console.info` at startup.

**Ranking method:** Structural signals only. No runtime data was available.

**Reference depth:**
- Postgres, Node, REST and Docker have deep references.
- Prisma (the ORM) has no technology-specific reference in this skill version. Prisma analysis uses the generic data-access and relational principles only. The exact SQL Prisma 4.16 emits for nested `include`, `connectOrCreate` and relation-count `orderBy` is inferred, not observed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | All application source and all deployment and manifest files in the repo |
| **Critical paths** | 19 / 19 HTTP routes (11 article, 4 auth, 3 profile, 1 tags) |
| **Shared resources** | 3 / 3 (Node event loop, Prisma connection pool, Postgres primary) |
| **Technology support** | Postgres: Deep; Node: Deep; REST/Express: Deep; Docker: Deep; Prisma: Generic |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Row counts and follower/favorite fan-in | No evidence exists | `pg_stat_user_tables`, plus GROUP BY counts on a replica |
| Request rates and latency percentiles per route | No evidence exists | A request-duration metric or access logs |
| Prisma pool size (`connection_limit`) and instance count | No evidence exists (set only in `DATABASE_URL`) | Production env and deployment config |
| Production Node version and container CPU/memory | No evidence exists (`node:lts-alpine` is a floating tag) | Deployment manifest |
| Exact SQL emitted by Prisma for includes, `connectOrCreate` and `_count` ordering | Technology unsupported (no Prisma reference) | Prisma `log: ['query']` in staging, then capture and `EXPLAIN` the statements |

---

## 3. Architecture overview

There is one Express app (`src/main.ts`) mounted under `/api`. It has 19 routes and one `PrismaClient` per process (`src/prisma/prisma-client.ts:17`), backed by PostgreSQL. Auth uses stateless JWTs (`express-jwt`), so no session store is involved. The repository has no cache, no broker and no outbound service calls. Deployment is a single `node api` process per container (`Dockerfile:24`).

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| Runtime | Node.js | `lts-alpine` (floating) | Deep | Single process, single event loop |
| HTTP | Express | 4.18.2 (lockfile) | Deep (REST) | Routing, body parsing, static assets |
| ORM | Prisma Client | 4.16.2 (lockfile) | Generic | All data access |
| Datastore | PostgreSQL | unknown | Deep | Single primary |
| Password hashing | bcryptjs | 2.4.3 | n/a | Pure-JS bcrypt on login, register and password change |
| Container | Docker | n/a | Deep | `CMD ["node","api"]` |

**Shared resources:** the Node event loop (all requests), the Prisma connection pool (all routes, size not visible), and the Postgres primary (all reads and writes).

---

## 4. Workload model

**Known**

- One Node process per container; there is no cluster or worker_threads. Source: `Dockerfile:24`, `src/main.ts`.
- Pool size is not set in code; it comes from `DATABASE_URL`. Source: `src/prisma/prisma-client.ts:17`, `README.md`.
- `limit`/`offset` are unclamped. Source: `article.service.ts:82-83`, `131-132`.
- Comments are returned unpaginated. Source: `article.service.ts:433-458`.
- None of `Comment.articleId`, `Comment.authorId`, `Article.authorId` or `Article.createdAt` has an index. Source: `migrations/20210924225358_initial/migration.sql`. The other migrations add no such index.
- No table has a retention, archival or cleanup job. Source: migrations and the `src/` tree.
- The API surface is 8 GET routes and 11 mutating routes. Source: the four controllers.
- The seed creates 12 users, 12 articles per user and 1 comment per user per article, with no follows or favorites. Source: `src/prisma/seed.ts:41-50`. This describes fixture shape, not production scale.

**Assumed**

- Article, Comment, `_UserFavorites` and `_UserFollows` grow monotonically with use. Affects PERF-001, 003, 004 and 005.
- Follower and favorite fan-in is skewed: some authors and articles are much more popular than others. Affects PERF-001.
- `/articles`, `/tags` and the comments route are the most frequently called endpoints. Affects PERF-001 through PERF-005.
- Containers may have more than one vCPU. Affects PERF-009.

**Unknown**

- Row counts and fan-in distribution
- Request rate per route, latency, SLO
- `connection_limit` and instance count
- Whether a gateway caps query parameters
- Container CPU/memory and the production Node major version

**Derived**

- Rows materialised per list request = page rows + Σ(favorites per article on the page) + Σ(followers per distinct author on the page) + tag rows. From: the include shape at `article.service.ts:84-104`, and the mappers using only `.some()` and `.length`.
- With no limit cap, one request materialises every visible article plus all of the above for each one. From: unclamped `take` at `article.service.ts:83` combined with the line above.
- `PUT /articles/:slug` makes up to 4 serial round trips outside a transaction. From: `article.service.ts:292-380`.

**Measured**

- None. No benchmark, trace, query plan or metric was supplied or exists in the repository. This is why no finding is `Confirmed`.

### Questions that would change the ranking

No one was available to answer these. I would have asked them in one message. The review proceeded under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is a specific slow endpoint, incident or scaling deadline behind this review? | severity, recommendation | The review would be reorganised around that path |
| 2 | highest | Roughly how many rows are in Article, Comment, `_UserFavorites`, `_UserFollows` and `_ArticleToTag`? What is the largest follower count for one user and the largest favorite count for one article? | severity, confidence | Small tables: PERF-001/003/004/005 drop to P2/P3. Large or skewed tables: they stay P1. |
| 3 | high | Can arbitrary clients reach the API, or only through a gateway or frontend that caps `limit`/`offset`? | severity, recommendation | With a gateway cap, PERF-002 becomes Medium/P2 |
| 4 | high | What is the peak request rate for `/articles`, `/tags` and the comments route? | severity, confidence | Low rates make PERF-004/005 budget questions rather than P1s |
| 5 | high | How many instances run, with how many vCPUs each, and what `connection_limit` is set? | severity, recommendation | Would settle PERF-009, and could reopen the pool-exhaustion candidate |
| 6 | medium | What is the peak login/registration rate? | severity | Moves PERF-006 between Low and High |

---

## 5. Critical path analysis

The paths are ranked by structural signals.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /api/articles` | yes | COUNT + findMany with 4 relation loads (tags, author + all followers, all favoritedBy) | **No**: limit/offset unclamped; relation rows scale with fan-in | no | PERF-001, 002, 004, 010 |
| 2 | `GET /api/articles/feed` | yes | Same as #1, filtered via `_UserFollows` → `Article.authorId` (unindexed) | **No** | no | PERF-001, 002, 004 |
| 3 | `GET /api/articles/:slug/comments` | yes | Article by slug + comments by `articleId` (unindexed, unpaginated) + each comment author's followers | **No** | no | PERF-003, PERF-001 |
| 4 | `GET /api/tags` | yes | Tag ranking by article count across the whole relation | Output yes (10); work no | no | PERF-005 |
| 5 | `GET /api/articles/:slug` | yes | Unique slug lookup + full favoritedBy and author followers | Relation rows no | no | PERF-001 |
| 6 | `POST/DELETE /api/articles/:slug/favorite` | yes | Update + same include | Relation rows no | no | PERF-001, SEC-001 |
| 7 | `GET /api/profiles/:username`, follow/unfollow | yes | User by username + all followers | Followers no | no | PERF-001 |
| 8 | `POST /api/users/login`, `POST /api/users`, `PUT /api/user` | yes | 1–3 indexed point ops + bcrypt on event loop | yes | no | PERF-006, PERF-011 |
| 9 | `PUT /api/articles/:slug` | yes | Up to 4 serial statements, tag rewrite | tagList bounded only by body size | no | PERF-008, COR-001 |
| 10 | `DELETE /api/articles/:slug` | yes | findFirst + delete; cascades into Comment (unindexed FK) | Cascade no | no | PERF-003 |

**Amplification points:**
- **Page size × fan-in.** Rows materialised = page size × (favorites + author followers). The page size itself is uncapped (PERF-001 × PERF-002).
- **Comments per article × each commenter's follower count** (PERF-003 × PERF-001).

**Paths deliberately not analyzed in depth, and why:**
- `GET /` and the static asset serving are constant-cost.
- `seed.ts` is offline fixture generation.
- `GET /api/user` is a single PK lookup plus a JWT sign.

---

## 6. Layer analysis

### 6.1 Application

- The mappers derive `favorited`, `following` and `favoritesCount` from fully loaded relation arrays (`article.mapper.ts:11-12`, `author.mapper.ts:8`, `profile.utils.ts:9`). The `_count.favoritedBy` the query already fetches is ignored by `articleMapper`. This drives PERF-001.
- Password hashing uses pure-JS `bcryptjs` on the event loop (PERF-006).

### 6.2 API

- There is no upper bound on `limit` or `offset` (PERF-002).
- Comments are unpaginated (PERF-003).
- List responses include the full `body` (PERF-010).
- `bodyParser.json()` uses its default size limit, which bounds request bodies.

### 6.3 Data access and datastore

- The migrations create only PKs, the unique keys (slug, email, username, tag name) and the implicit join-table `(A,B)` + `(B)` indexes. Every plain FK column on Article and Comment, and the `createdAt` sort column, is unindexed (PERF-003, PERF-004).
- List and feed issue an exact COUNT plus an offset-paginated ordered fetch, serially (PERF-004).
- `/tags` orders by a relation count (PERF-005).
- Writes use per-tag `connectOrCreate` and a full tag rewrite on update (PERF-008).
- No `$transaction` is used anywhere.

### 6.4 Infrastructure

- One process per container from a floating `node:lts-alpine` base image.
- No `--max-old-space-size`, `UV_THREADPOOL_SIZE`, CPU or memory limit, or replica count is present (PERF-009).
- `NODE_ENV` is not set in the Dockerfile; the README asks operators to set it.

### 6.5 Observability

- The service is uninstrumented (PERF-007).
- No finding can currently be confirmed, and no fix can be validated, without first adding request-duration, query and event-loop measurements.

---

## 7. Findings

### PERF-001: Article and profile responses load every favoriter's and follower's full User row to compute two booleans and a count

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:90` (`getArticles`) |
| **Tags** | scalability-risk, quick-win |

**Problem**
Every article response does `include: { favoritedBy: true, author: { select: { followedBy: true } } }`. This loads every User row that favorited the article and every User row that follows its author, with all columns, including the password hash and email. The application then uses those arrays only for `.some(id === viewer)` and `.length`.

**Performance principle**
Fetch only what the response needs. Derive membership and counts in the datastore instead of materialising whole related sets in application memory.

**Evidence**
- `article.service.ts:95,98`: the include in `getArticles`.
- The identical include appears in `getFeed` (144, 147), `createArticle` (226, 229), `getArticle` (257, 260), `updateArticle` (370, 373), `favoriteArticle` (585, 588) and `unfavoriteArticle` (631, 634).
- Comment authors' `followedBy` is loaded at 452 and 507.
- `profile.service.ts:11, 35, 55` load `followedBy: true` for a single boolean.
- `article.mapper.ts:11-12` uses `favoritedBy` only for `.some` and `.length`. The `_count.favoritedBy` fetched at 99-103 is unused there.
- `author.mapper.ts:8` and `profile.utils.ts:9` use `followedBy` only for `.some`.
- No runtime evidence exists.

**Impact**
- Position: critical path (the public list, feed, article and profile reads).
- Frequency: per item, per request.
- Growth: O(n) in fan-in. *Derivation:* rows per list response = page rows + Σ favorites(article) + Σ followers(author). Inputs: the include shape and the unbounded `_UserFavorites`/`_UserFollows` tables, which have no retention.
- Blast radius: service. The rows are deserialised and held in the single Node heap, and serialised on the shared event loop.
- Amplified by PERF-002, because page size is uncapped.

**Conditions**
This matters once popular authors and articles accumulate followers and favorites. It assumes those tables grow with use and that fan-in is skewed. Row counts are unknown. With tens of followers per author the cost is negligible.

**Counter-evidence**
- Prisma resolves includes with batched IN queries, so the statement count does not grow per row; this is not an N+1 in statements. *Effect: bounds-impact.* That is reflected in High rather than Critical severity.
- I searched for a nested `take`, a column-limiting `select` on these relations, or response caching. None was found. *Effect: no-effect.*

**Why this might not matter**
If this is a demo-scale deployment (the seed creates 12 users and no follows), every relation list is tiny.

**Recommendation**
- Compute `favoritesCount` from `_count.favoritedBy`.
- Compute `favorited` and `following` from viewer-filtered selects: `favoritedBy: { where: { id: viewerId }, select: { id: true } }` and `followedBy: { where: { id: viewerId }, select: { id: true } }`. Skip both for anonymous viewers.
- Apply this at all ~10 include sites and in `profile.service.ts`.

This removes the work in proportion to fan-in rather than hiding it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Viewer-filtered selects + `_count` | **preferred** | Per-article cost becomes O(1) rows regardless of popularity. It also stops reading password hashes. |
| Cache rendered responses | | Hides the work. Output is per-viewer, so the hit rate is poor and invalidation is complicated. |
| Denormalise counters and follow sets | | Adds write-path complexity for something the filtered select already solves |

**Trade-offs**
Small code churn across the include sites. The per-viewer filtered subqueries are cheap because the join tables have `(A,B)` unique indexes. The API contract does not change.

**Validation**
- *Baseline:* related rows per `GET /api/articles` from Prisma query logging in staging with production-shaped data, plus fan-in sizes from §9.
- *Measurement and expectation:* related User rows per article fall from (favorites + followers) to at most 1 each. JSON output stays identical.
- *Falsifier:* the fan-in is uniformly tiny in production-shaped data. In that case demote to P3.
- *Safety:* the stats query is `safe-on-production`; the GROUP BY is `not-safe-on-production` (run it on a replica).

---

### PERF-002: List and feed endpoints accept unbounded `limit` and `offset`

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `src/app/routes/article/article.service.ts:83` (`getArticles`) |
| **Tags** | quick-win |

**Problem**
`take: Number(query.limit) || 10` and `skip: Number(query.offset) || 0` have no ceiling (`getFeed` does the same at 131-132). A single request with a large `limit` loads every visible article plus, via PERF-001, all of their related users, then serialises the result in one `res.json`. Deep offsets make Postgres produce and discard every skipped row.

**Performance principle**
Work driven by caller input must be bounded by the server.

**Evidence**
- `article.service.ts:82-83` and `131-132`.
- `article.controller.ts:30` (`auth.optional`, so anonymous callers can reach it) and `:50-54` (feed passes `Number(req.query.limit)` through unchanged).
- A synchronous `res.json` at `:33`.
- No validation middleware or max constant exists anywhere in `src/`.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in table size, multiplied by fan-in.
- Blast radius: service. The single process's heap and event loop are shared by all requests.
- Amplification: Nx.

**Conditions**
This matters whenever any client sends a large `limit` or a deep `offset` against a non-trivial table. One request is enough, so the exposure does not depend on traffic volume, and the code fact holds at any workload. How large the damage gets depends on table size.

**Counter-evidence**
- The default is 10 when `limit` is absent. *Effect: bounds-impact* for well-behaved clients only.
- I searched for gateway or validation config. None exists in the repo. *Effect: no-effect.*

**Why this might not matter**
The only client may be a first-party frontend that always sends small limits, and a proxy outside the repo may cap query parameters.

**Recommendation**
Clamp `take` to `[1, MAX_LIMIT]` and reject negative or NaN offsets. Consider keyset pagination on `(createdAt, id)` if deep paging is a real use case.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Server-side clamp | **preferred** | A one-line, constant bound that does not depend on deployment topology |
| Keyset pagination | | Fixes deep-offset cost too, but changes the API |
| Gateway cap | | Not visible or enforceable from the service |

**Trade-offs**
Clients asking for more than the maximum get fewer rows.

**Validation**
- *Baseline:* row count and bytes for `?limit=100000` on staging (command in §9, `not-safe-on-production`).
- *Expectation:* after the clamp, at most `MAX_LIMIT` rows are returned.
- *Falsifier:* a large limit already returns a bounded number of rows.
- *Guard:* a unit test with the prisma mock asserting `take <= MAX_LIMIT`.

---

### PERF-003: Comment lookups and article-delete cascades scan the unindexed `Comment.articleId`, and comments are unpaginated

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/prisma/schema.prisma:32` (`Comment`) |
| **Tags** | scalability-risk, quick-win |

**Problem**
`Comment` has only a primary key. Loading an article's comments (`getCommentsByArticle`) filters on `articleId` with no index and no `take`. The `ON DELETE CASCADE` from Article also has to find comments by `articleId` without an index.

**Performance principle**
A lookup by a selective key should cost in proportion to the matching rows, not to the size of the table.

**Evidence**
- `migrations/20210924225358_initial/migration.sql:24-33` creates Comment with only a PK.
- Line 99 adds the FK with cascade.
- No later migration adds an index (checked all four).
- `article.service.ts:433-458` runs the include with no `take`.
- `seed.ts:50` shows a comment per user per article; production rows are appended per comment with no retention.

**Impact**
- Position: critical path (article page view).
- Frequency: per request.
- Growth: O(total comments), plus O(article's comments) in the payload.
- Blast radius: service. The load lands on the shared primary database.

**Conditions**
This matters once the Comment table is large enough that a sequential scan is materially slower than an index lookup. The row count is unknown, but the table grows with every comment and is never pruned.

**Counter-evidence**
- Postgres does not index FKs automatically, and there is no `@@index` in the schema. *Effect: no-effect.*
- On a small table the planner would scan anyway. *Effect: bounds-impact.* This is reflected in the Medium confidence.

**Why this might not matter**
With a few thousand comments the scan is fast, and the index would only add write cost.

**Recommendation**
Add `@@index([articleId])` on Comment. Hand-edit the migration to use `CREATE INDEX CONCURRENTLY` on a live database. Add pagination to the comments endpoint.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Index `Comment.articleId` | **preferred** | Lookup and cascade both become proportional to matching rows |
| Cache comment lists | | Masks the scan and adds invalidation on every comment write |
| Paginate only | | Bounds the payload, but the scan remains |

**Trade-offs**
Index maintenance on every comment insert and delete, plus storage. Prisma's default migration creates the index non-concurrently, which locks writes on a large table.

**Validation**
- *Baseline:* `EXPLAIN` of the comment lookup, plus `seq_scan`/`n_live_tup` for Comment (both `safe-on-production`, §9).
- *Expectation:* the plan changes from Seq Scan to Index or Bitmap Scan, and Comment's `seq_scan` stops rising with comment-page traffic.
- *Falsifier:* Comment is small, or an index already exists that was created outside the migrations.
- *Guard:* a lint rule that every FK in `schema.prisma` has an `@@index`.

---

### PERF-004: Every list and feed request counts and sorts the full filtered Article set without supporting indexes

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/prisma/schema.prisma:11` (`Article`) |
| **Tags** | scalability-risk |

**Problem**
`getArticles` and `getFeed` first run `prisma.article.count` over the whole filtered set. They then run `findMany` with `ORDER BY createdAt DESC` and `OFFSET`/`LIMIT`, awaited serially. `Article.createdAt` and `Article.authorId` are not indexed. Every request therefore scans the visible articles twice and sorts them. The feed and `?author=` filters reach Article through the unindexed `authorId`.

**Performance principle**
Per-request work on a growing table should be bounded by page size, not by table size. Independent queries should not be serialised.

**Evidence**
- `migrations/20210924225358_initial/migration.sql:2-13` and `:69` define Article with only the PK and a unique slug.
- `article.service.ts:71-105` runs count, then findMany.
- `:114-154` does the same in the feed.
- `:27-33` resolves the author filter.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in Article rows.
- Blast radius: system-wide, because the load lands on the single shared primary.
- Amplification: 2x (two scans per request). *Derivation:* one COUNT statement plus one ordered fetch statement over the same filter.

**Conditions**
This matters as Article grows (there is no retention) and if `/articles` is a top endpoint. Both are unverified, and with a few hundred articles the cost is small.

**Counter-evidence**
- Postgres top-N heapsort bounds sort memory, but not the scan. *Effect: bounds-impact.*
- The default `demo = true OR id = me` filter could be selective, but without an `authorId` index the join still scans Article. *Effect: no-effect.*
- No plan exists to confirm any of this. *Effect: lowers-confidence.* That is why confidence is Medium.

**Why this might not matter**
A table of a few thousand articles fits in cache, and both statements stay cheap.

**Recommendation**
- Add `@@index([authorId, createdAt])` and `@@index([createdAt])` on Article, built concurrently.
- Run `count` and `findMany` concurrently (`Promise.all` or `prisma.$transaction([...])`).
- If the exact count stays expensive at scale, cap it or approximate it.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Indexes + parallel count/fetch | **preferred** | Page reads become index-ordered, the feed can seek per followed author, and one serial round trip disappears |
| Cache `articlesCount` | | Staleness, and the filter is per-viewer |
| Keyset pagination, no exact count | | Larger API change |

**Trade-offs**
Two more indexes to maintain on article writes. COUNT stays O(matching rows), at best as an index-only scan. Parallelising uses two pool connections per request.

**Validation**
- *Baseline:* `EXPLAIN` of approximations of the list and count queries (§9, `safe-on-production`), plus `seq_scan` on Article.
- *Expectation:* the plans become an Index Scan in `createdAt` order instead of Seq Scan + Sort.
- *Falsifier:* the plans already use an index, or the table is too small for the planner to choose one.

---

### PERF-005: `/api/tags` re-ranks all tags by article count on every request

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `src/app/routes/tag/tag.service.ts:16` (`getTags`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
To return 10 names, `getTags` filters tags through `articles.some(author demo OR id)` and orders by `articles._count`. That aggregates the entire `_ArticleToTag` relation, joined to Article and User, on every call.

**Performance principle**
A slowly changing result that is the same for most callers should not be recomputed from the full dataset on every request.

**Evidence**
- `tag.service.ts:16-35`.
- `_ArticleToTag` is created in `migrations/20211001143221_implicit_tags/migration.sql:18-27`; there is no retention on articles.
- `tag.controller.ts:13` is `auth.optional`.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in article-tag links.
- Blast radius: system-wide (primary database).

**Conditions**
This matters if `/tags` is called on most page loads and the relation is large. Neither is evidenced in this repository. The exact SQL Prisma emits for relation-count ordering has not been observed.

**Counter-evidence**
- `take: 10` bounds the output, not the aggregation. *Effect: no-effect.*
- The emitted query shape is unverified and might be cheaper. *Effect: lowers-confidence.* Evidence quality is Weak, and the finding is tagged `needs-measurement`.

**Why this might not matter**
With few tags and modest article counts the aggregate is cheap, and `/tags` might be rarely called.

**Recommendation**
First capture the emitted SQL and its plan (or its `pg_stat_statements` cost). If confirmed, precompute popular tags, either with a materialised view refreshed on a schedule or with a counter maintained on article writes.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Measure, then precompute | **preferred** | Removes the per-request aggregate. Measuring first because the query shape is unverified. |
| In-process TTL cache | | Per-instance staleness, and the result varies per viewer id |
| No change | | Acceptable if measurement shows it is cheap |

**Trade-offs**
Popular tags lag behind real data by the refresh interval. Precomputation adds a refresh job or extra write-path work.

**Validation**
- `pg_stat_statements` ranking (§9, `safe-on-production`).
- *Expectation:* the per-request statement reads a small fixed number of rows after the change.
- *Falsifier:* the captured plan is already index-bounded.

---

### PERF-006: Password hashing runs in pure JavaScript on the shared event loop

| | |
|:--|:--|
| **Root cause** | `ROOT-005` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `src/app/routes/auth/auth.service.ts:111` (`login`) |
| **Tags** | needs-measurement |

**Problem**
`bcryptjs` (pure JS, 2.4.3 per `package-lock.json:4792`) runs `compare` on login (`:111`) and `hash(…, 10)` on registration and password change (`:58`, `:156`). All of it runs on the single event loop that serves every request.

**Performance principle**
Keep CPU-bound work off the thread that multiplexes all request I/O.

**Evidence**
The lines above, plus `Dockerfile:24` (one process per container). No rate limiter exists on `/users/login`.

**Impact**
- Position: critical path.
- Frequency: per login or registration request.
- Growth: O(1) per request, multiplied by the login rate.
- Blast radius: service. Every concurrent request shares the loop.

**Conditions**
This matters when login or registration traffic is a meaningful share of requests or arrives in bursts, such as credential stuffing or a mass re-login. The login rate is unknown.

**Counter-evidence**
- The async API yields between chunks, so this adds CPU share and latency rather than one long freeze. *Effect: bounds-impact*, which is why this is Medium rather than High.
- I searched for rate limiting and found none. *Effect: no-effect.*

**Why this might not matter**
Logins are usually rare relative to reads.

**Recommendation**
First measure event-loop delay (PERF-007). If login CPU turns out to be material, switch to native `bcrypt` (which runs on the libuv pool and is hash-compatible) at the same cost factor, and add login rate limiting.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Native `bcrypt`, same cost | **preferred** | Compatible with existing hashes and moves the CPU off the loop |
| Lower the cost factor | | Weakens security |
| `worker_threads` pool for bcryptjs | | More code for the same effect |

**Trade-offs**
Native bcrypt needs a compiled dependency on Alpine. It also competes for the default 4-slot libuv pool.

**Validation**
- Measure `perf_hooks.monitorEventLoopDelay` p99 during a staged login burst (`not-safe-on-production`; run it in staging).
- *Expectation:* delay stays near the idle baseline after the change.
- *Falsifier:* delay does not rise during the burst even before the change.

---

### PERF-007: No request, query, event-loop or pool instrumentation

| | |
|:--|:--|
| **Root cause** | `ROOT-006` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 (sequenced first; see §8) |
| **Category** | observability |
| **Location** | `src/main.ts:13` (`app`) |
| **Tags** | quick-win |

**Problem**
The service has no per-route latency, no access log, no Prisma query logging or metrics, no event-loop delay metric and no pool metric. None of the findings above can be confirmed, and none of their fixes validated.

**Performance principle**
Measure before optimising.

**Evidence**
- `main.ts:13-16` has only cors and body parsers.
- `prisma-client.ts:17` creates `new PrismaClient()` with no `log`.
- `package.json` has no metrics or tracing dependency.

**Impact**
- Position: critical path (affects how every request is understood).
- Frequency: per request.
- Growth: O(1).
- Blast radius: service.
- It has no runtime cost of its own, hence Low severity.

**Conditions**
This applies at any workload.

**Counter-evidence**
I searched for prometheus, opentelemetry, morgan, pino, winston, APM agents, k6, artillery, benchmarks and SLO documents. None was found. *Effect: no-effect.*

**Recommendation**
- Add a per-route duration histogram or a structured access log with duration.
- Turn on Prisma query logging in staging.
- Add `perf_hooks.monitorEventLoopDelay`.

**Trade-offs**
Small per-request overhead and extra log volume. Keep query logging to staging or sample it.

**Validation**
- *Expectation:* the per-route p50/p99 and event-loop delay series exist.
- *Falsifier:* an external APM already covers the process.
- *Safety:* `safe-on-production`.

---

### PERF-008: Article update rewrites all tag links in serial, per-tag round trips

| | |
|:--|:--|
| **Root cause** | `ROOT-007` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `src/app/routes/article/article.service.ts:343` (`updateArticle`) |
| **Tags** | quick-win |

**Problem**
`updateArticle` makes up to four serial round trips:
1. `findFirst` (292)
2. An optional slug `findFirst` (320)
3. `set: []` on all tags (343)
4. An `update` with per-tag `connectOrCreate` (345)

It does this even when the tags did not change. `createArticle` (204) also runs `connectOrCreate` for every element of an uncapped `tagList`.

**Performance principle**
A logical write should touch only what changed, in as few round trips as practical.

**Evidence**
The lines cited above.

**Impact**
- Position: critical path (author write).
- Frequency: per request.
- Growth: O(tags).
- Blast radius: endpoint.

**Conditions**
Writes are author actions and probably infrequent, and tag lists are usually small. `bodyParser.json`'s default limit bounds `tagList` indirectly (*bounds-impact*).

**Counter-evidence**
The body-size limit bounds the tag count. *Effect: bounds-impact.*

**Recommendation**
Diff the tags. Apply `set` plus `connectOrCreate` in one `update` (or a `$transaction`), skip the tag rewrite when `tagList` is absent, and cap the `tagList` length.

**Trade-offs**
A little more code. A transaction holds locks for the duration of the write.

**Validation**
- *Measurement:* statements per `PUT` in a staging query log.
- *Expectation:* fewer statements, and no delete-all when tags are unchanged.
- *Safety:* `safe-on-production`.

---

### PERF-009: One Node process per container. Question: how many vCPUs are allocated?

| | |
|:--|:--|
| **Root cause** | `ROOT-008` |
| **Severity** | Medium |
| **Confidence** | Low |
| **Priority** | P3 |
| **Category** | infrastructure |
| **Location** | `Dockerfile:24` (`CMD`) |
| **Tags** | needs-measurement |

**Problem**
`CMD ["node","api"]` runs a single process, with no cluster or worker_threads. If containers are given more than one vCPU, the application's JavaScript can use only one of them.

**Performance principle**
Allocated compute should be usable by the process.

**Evidence**
`Dockerfile:24` and `src/main.ts:55`. There is no process manager.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(1). This is a capacity ceiling, not a growth problem.
- Blast radius: service.

**Conditions**
This only matters with multi-vCPU containers that are CPU-bound at peak. Neither is evidenced.

**Counter-evidence**
Running several one-vCPU replicas is an equally valid design, and the deployment config is not in the repo. *Effect: lowers-confidence* (to Low).

**Why this might not matter**
The platform probably runs one-vCPU replicas.

**Recommendation**
Confirm the vCPU allocation. Prefer one vCPU per container and scale out with replicas.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| One vCPU per container, scale replicas | **preferred** | Simple, and keeps the Prisma pool count predictable |
| cluster or a process manager in the container | | Multiplies pools inside one container |

**Trade-offs**
More processes multiply Prisma pools against Postgres `max_connections`.

**Validation**
- *Measurement:* per-core CPU at peak.
- *Falsifier:* containers have one vCPU, or CPU stays well below one core.
- *Safety:* `safe-on-production`.

---

### PERF-010: List responses carry full article bodies

| | |
|:--|:--|
| **Root cause** | `ROOT-009` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | serialization |
| **Location** | `src/app/routes/article/article.mapper.ts:7` (`articleMapper`) |
| **Tags** | needs-measurement |

**Problem**
List and feed select and serialise `body` for every item.

**Performance principle**
Do not transfer fields the caller does not use.

**Evidence**
- `article.mapper.ts:7`.
- `getArticles` and `getFeed` have no column `select`.

**Impact**
- Position: critical path.
- Frequency: per item.
- Growth: O(page size × body size).
- Blast radius: endpoint.

**Conditions**
This only matters if bodies are large, and their size is unknown. Once PERF-002 is fixed, it is bounded by page size.

**Counter-evidence**
The targeted RealWorld spec version may require `body` in lists. *Effect: bounds-impact.*

**Recommendation**
Measure body sizes. If they are large, drop `body` from list selects in line with the current RealWorld spec.

**Trade-offs**
This is an API contract change for list consumers.

**Validation**
- Response bytes before and after the change.
- Body sizing query in §9 (`not-safe-on-production`; run it on a replica).

---

### PERF-011: Registration uniqueness check makes two serial lookups

| | |
|:--|:--|
| **Root cause** | `ROOT-010` |
| **Severity** | Low |
| **Confidence** | High |
| **Priority** | P3 |
| **Category** | data-access |
| **Location** | `src/app/routes/auth/auth.service.ts:9` (`checkUserUniqueness`) |

**Problem**
The check runs two sequential unique-index point lookups (email, then username) that could be one query. The unique constraints already enforce the rule.

**Performance principle**
Avoid serial round trips for independent lookups.

**Evidence**
`auth.service.ts:10-26`.

**Impact**
- Position: critical path (registration only).
- Frequency: per request.
- Growth: O(1).
- Blast radius: endpoint.
- Amplification: 2x statements.

**Conditions**
Registration only. The cost is one extra indexed round trip.

**Counter-evidence**
Both lookups are on unique indexes. *Effect: bounds-impact.*

**Recommendation**
Use a single `findMany` with `OR`, or catch the unique-constraint error (P2002) and map it to a 422.

**Trade-offs**
Mapping P2002 means parsing the error target.

**Validation**
- *Measurement:* statements per `POST /api/users` drop from 2 lookups to 1.
- *Safety:* `safe-on-production`.

---

### Remaining findings

None. All 11 findings are given in full format above.

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-001 | High | Medium | P1 | `article.service.ts:90` | Full favoriter and follower rows loaded to compute booleans and a count |
| PERF-002 | High | High | P1 | `article.service.ts:83` | Unbounded `limit`/`offset` on list and feed |
| PERF-003 | High | Medium | P1 | `schema.prisma:32` | Unindexed `Comment.articleId`; comments unpaginated |
| PERF-004 | High | Medium | P1 | `schema.prisma:11` | COUNT + sort over the full Article set per request; no `createdAt`/`authorId` index |
| PERF-005 | High | Medium | P1 | `tag.service.ts:16` | Tag popularity aggregated per request |
| PERF-006 | Medium | Medium | P2 | `auth.service.ts:111` | Pure-JS bcrypt on the event loop |
| PERF-007 | Low | High | P3 | `main.ts:13` | No instrumentation |
| PERF-008 | Low | Medium | P3 | `article.service.ts:343` | Tag rewrite in serial per-tag round trips |
| PERF-009 | Medium | Low | P3 | `Dockerfile:24` | One process per container; vCPU allocation unknown |
| PERF-010 | Low | Medium | P3 | `article.mapper.ts:7` | Full bodies in list responses |
| PERF-011 | Low | High | P3 | `auth.service.ts:9` | Two serial uniqueness lookups |

### Considered and not reported

- **Prisma connection pool exhaustion** (shared pool, all routes).
  - *Evidence checked:* `prisma-client.ts`, `schema.prisma`, `README.md`, `Dockerfile`, and every service file for `$transaction` (there are none).
  - *Why discarded:* pool size lives only in `DATABASE_URL`, no transaction holds a connection across I/O, and there is no evidence of instance count.
  - *Revisit when:* `connection_limit` and the replica count are known, or Prisma pool-timeout errors appear.
- **Synchronous `JSON.stringify` of list responses blocking the event loop** (`article.controller.ts:33`).
  - *Why discarded:* bounded by the default page size of 10. The unbounded case is PERF-002.
  - *Revisit when:* the event-loop delay metric correlates spikes with list responses after PERF-002 is fixed.

### Adjacent findings: outside performance scope

### SEC-001: Favorite/unfavorite responses leak password hashes and emails of favoriting users

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | High |
| **Location** | `src/app/routes/article/article.service.ts:597` (and `:643`) |

**Problem**
`favoriteArticle` and `unfavoriteArticle` spread the raw Prisma result (`...article`) into the response. That result includes `favoritedBy: true`, which carries every favoriting user's full row, including `password` (the bcrypt hash) and `email`.

**Evidence**
- `article.service.ts:563-603` and `609-649`.
- `schema.prisma:44-48` (the User columns).
- Other routes go through `articleMapper`, which omits these fields. These two routes do not.

**Impact**
Any authenticated user can harvest the emails and password hashes of everyone who favorited an article by favoriting it themselves. That is a direct credential-exposure path.

**Recommendation**
Return `articleMapper(...)`, as `getArticle` does. The PERF-001 change also stops loading these columns at all.

**Trade-offs**
None material.

**Validation**
A contract test asserting that responses never contain `password` or `email` for non-self users.

**Would need**
A security review of every response shape, and an assessment of whether rotating exposed credentials is needed.

### SEC-002: JWT secret falls back to a hard-coded literal

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `src/app/routes/auth/auth.ts:16` |

**Problem**
Both verification (`auth.ts:16,21`) and signing (`token.utils.ts:4`) use `process.env.JWT_SECRET || <literal>`. The literal value is not reproduced here.

**Evidence**
The lines above. Tokens expire after 60 days (`token.utils.ts:5`).

**Impact**
If the variable is ever unset, anyone can forge valid 60-day tokens for any user id. The risk is Medium because the README instructs operators to set the variable.

**Recommendation**
Fail at startup when `JWT_SECRET` is missing.

**Trade-offs**
Local development needs the variable set.

**Validation**
Starting the app without `JWT_SECRET` exits with an error.

**Would need**
A configuration and secrets-handling security review.

### COR-001: Article update clears tags in a separate, non-transactional statement

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Low |
| **Location** | `src/app/routes/article/article.service.ts:343` |

**Problem**
`disconnectArticlesTags` and the following `update` are separate awaits. If the update fails, for example on a concurrent slug collision that hits the unique constraint after the check-then-write test at 320-331, the article is left with no tags and the client gets a 500 instead of a 422.

**Evidence**
`article.service.ts:316-357`.

**Impact**
Silent tag loss on a rare race. Low risk.

**Recommendation**
Do the tag `set` and the field update in one `update` or a `$transaction`, and map P2002 to a 422.

**Trade-offs**
None material.

**Validation**
An integration test that forces a slug collision and asserts the tags are preserved.

**Would need**
Correctness-focused integration tests against a real Postgres.

### COR-002: Commenting on a nonexistent article returns 500

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Low |
| **Location** | `src/app/routes/article/article.service.ts:478` |

**Problem**
`addComment` connects `article?.id` without a null check (`:492`). An unknown slug therefore produces a Prisma error that surfaces as a 500 instead of a 404.

**Evidence**
`article.service.ts:478-499`.

**Impact**
Wrong status code. Low risk.

**Recommendation**
Throw `HttpException(404)` when the article is not found.

**Trade-offs**
None.

**Validation**
A test asserting a 404 for an unknown slug.

**Would need**
API contract tests for error paths.

---

## 8. Prioritized action plan

### P1: High priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 0 | PERF-007 | P3 | Small | **Sequenced first on purpose, despite P3.** It is a dependency: without request and query timing, none of the P1 changes can be validated in production. |
| 1 | PERF-002 | P1 | Trivial | One-line clamp that bounds the worst case of PERF-001 and PERF-004 immediately |
| 2 | PERF-001 (+ SEC-001) | P1 | Small–medium | Removes the fan-in work and closes the credential leak in the same change |
| 3 | PERF-003 | P1 | Small (migration) | Check `EXPLAIN` and row counts first, then add the index concurrently |
| 4 | PERF-004 | P1 | Small (migration + `Promise.all`) | Same procedure as PERF-003 |
| 5 | PERF-005 | P1 | Measure first | Capture the SQL and plan, then precompute only if confirmed |

### P2: Medium priority

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 6 | PERF-006 | P2 | Small | After the event-loop delay metric exists |

### P3: Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 7 | PERF-008 | P3 | Small | Fold into the COR-001 fix |
| 8 | PERF-011 | P3 | Trivial | Opportunistic |
| 9 | PERF-010 | P3 | Small | Only if body sizes turn out to be large |
| 10 | PERF-009 | P3 | Config | Answer the vCPU question |

**If only one thing is done:** clamp `limit` (PERF-002) and replace the full `favoritedBy`/`followedBy` includes with viewer-filtered selects (PERF-001). Together they bound the worst-case per-request work and close SEC-001.

---

## 9. Validation plan

### PERF-001

- **Baseline:** fan-in sizes and table sizes. *Safety:* the stats query is `safe-on-production`; the GROUP BY is not (run it on a replica).
- **Change:** viewer-filtered relation selects plus `_count`.
- **Measurement:** User rows returned per `GET /api/articles`, from Prisma query logging in staging.
- **Expectation:** at most 1 favoriter row and 1 follower row per article, regardless of popularity. JSON output is identical.
- **Falsifier:** fan-in is uniformly tiny in production-shaped data. In that case demote to P3.
- **Guard:** a unit test on the prisma mock asserting that no include contains `favoritedBy: true` or `followedBy: true`.
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;'` (table sizes).
  - `not-safe-on-production` (run on a replica): `psql "$DATABASE_URL" -c 'SELECT "A", count(*) c FROM "_UserFollows" GROUP BY "A" ORDER BY c DESC LIMIT 5; SELECT "B", count(*) c FROM "_UserFollows" GROUP BY "B" ORDER BY c DESC LIMIT 5; SELECT "A", count(*) c FROM "_UserFavorites" GROUP BY "A" ORDER BY c DESC LIMIT 5;'` (largest follower and favorite fan-in; either column of `_UserFollows` may be the followed user, so both are shown).

### PERF-002

- **Baseline:** the row count and bytes returned for a large `limit` on staging.
- **Change:** clamp `take`.
- **Expectation:** never more than `MAX_LIMIT` rows.
- **Falsifier:** the response is already bounded.
- **Guard:** a unit test asserting `take <= MAX_LIMIT`.
- **Commands:**
  - `not-safe-on-production`: ``curl -s 'http://localhost:3000/api/articles?limit=100000' | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.parse(s).articles.length, s.length))"`` (articles and bytes in one large-limit response; run against staging).

### PERF-003

- **Baseline:** the plan and scan counters.
- **Change:** an index on `Comment.articleId`, created concurrently.
- **Expectation:** Seq Scan becomes Index or Bitmap Scan.
- **Falsifier:** the table is tiny, or an index already exists.
- **Guard:** an FK-index lint rule.
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'EXPLAIN SELECT id FROM "Comment" WHERE "articleId" = 1;'` (plan only, no execution).
  - `safe-on-production`: `psql "$DATABASE_URL" -c "SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup FROM pg_stat_user_tables WHERE relname IN ('Comment','Article');"` (scan counters and sizes).

### PERF-004

- **Baseline:** plans for the list and count queries.
- **Change:** indexes on `(authorId, createdAt)` and `(createdAt)`, plus a parallel count and fetch.
- **Expectation:** an Index Scan in `createdAt` order instead of Seq Scan + Sort, and one fewer serial round trip.
- **Falsifier:** the plans are unchanged or already indexed.
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'EXPLAIN SELECT a.id FROM "Article" a JOIN "User" u ON u.id = a."authorId" WHERE u.demo = true ORDER BY a."createdAt" DESC LIMIT 10 OFFSET 0;'` (approximates the default list query).
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'EXPLAIN SELECT count(*) FROM "Article" a JOIN "User" u ON u.id = a."authorId" WHERE u.demo = true;'` (the per-request count).

### PERF-005

- **Baseline:** DB time for the `/tags` statement.
- **Change:** precompute, but only if the measurement confirms the cost.
- **Expectation:** the per-request statement reads a small fixed number of rows.
- **Falsifier:** the plan is already index-bounded.
- **Commands:**
  - `safe-on-production`: `psql "$DATABASE_URL" -c 'SELECT query, calls, total_exec_time, mean_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;'` (requires the extension).

### PERF-006

- **Baseline:** `perf_hooks.monitorEventLoopDelay` p99 during a staged login burst in staging. *Safety:* `not-safe-on-production`. No repository command exists for this; it needs the PERF-007 metric.
- **Expectation:** delay stays near idle after moving to native bcrypt.
- **Falsifier:** delay does not rise during the burst.

### PERF-010

- **Commands:**
  - `not-safe-on-production` (full scan; run on a replica): `psql "$DATABASE_URL" -c 'SELECT avg(length(body)), max(length(body)) FROM "Article";'`

### Instrumentation gaps to close first

1. A per-route request-duration histogram or a structured access log with duration.
2. Prisma query logging (`log: ['query']`) in staging, so statements per request can be counted for PERF-001, 004 and 008.
3. `perf_hooks.monitorEventLoopDelay` exported as a metric (PERF-006, PERF-002).
4. `pg_stat_statements` enabled on the database, if it is not already.

---

## 10. Machine-readable output

Emitted alongside this report at `out/node-guided-2.json`. It conforms to `schemas/review.schema.json` and was validated against it with zero errors. Each finding's priority was checked against the severity/confidence matrix. `stable_id` values were computed with `scripts/compute_stable_id.py`.

---

## 11. Notes on this review

- Findings are classified by evidence grade. No finding is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated or assumed. The row-count expressions in §4 and in PERF-001, PERF-004 and PERF-011 are labelled derivations from code.
- Recommendations state their trade-offs and their validation path.
- I would have asked the workload questions in §4 once. With no one available to answer, the review proceeded under the stated assumptions, and workload-dependent findings were capped at `Medium` confidence. **The ranking would change with data volume:** small tables would move PERF-001, 003, 004 and 005 from P1 to P2/P3, and a gateway cap would move PERF-002 to P2.
