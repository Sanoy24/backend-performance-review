# Performance Review: full-stack-fastapi-template (backend service)

**Date:** 2026-10-08
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v2.0.0
**Commit:** `8063fe54f17d19f01720e055103af9cad3d8f55d`

---

## 1. Decision summary

### Overall assessment

This is a small, thin CRUD backend: sync FastAPI handlers on a threadpool, a SQLModel/SQLAlchemy session per request, and one Postgres database. Static analysis found no event-loop blocking and no N+1 queries. The most important structural gap is that **`item.owner_id` has no index**. Every user's item list and its count therefore read the whole `item` table, so the cost grows with all users' data, not just the caller's (PERF-001). The second is **synchronous SMTP with no explicit timeout, sent while a pooled DB connection is still checked out** (PERF-002). No runtime evidence was supplied and nobody answered the workload questions, so every workload-dependent finding is capped at Medium confidence. Overall review confidence is **Medium**.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Run `pg_stat_user_tables` / `EXPLAIN` on the item list query. If `item` is non-trivial, add a concurrent index on `item (owner_id, created_at DESC)` | On the main page's request path. The cost grows with total table size, and the index also covers user-delete cascades |
| 2 | **PERF-002 (P1)** | Pass an explicit SMTP timeout and release the DB session before `send_email` in `recover_password` / `create_user` | Unauthenticated route. A hung mail server pins pooled connections in "idle in transaction" |
| 3 | **PERF-003 (P2, quick-win)** | Add `Query(le=…)` bounds on `limit`/`skip` for `GET /items/` and `GET /users/` | Two-line change. Turns a caller-controlled response size into a bounded one |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Row count of `item`, and the largest per-user item count | PERF-001 stays P1 (large) or drops to P3 (small); also affects PERF-003 and PERF-006 | `pg_stat_user_tables` (below) |
| Production platform, instance count, Postgres `max_connections` | Whether a pool-sizing finding is needed; confirms PERF-002's blast radius | Deployment settings plus `SHOW max_connections` |
| Whether SMTP is enabled in production, and the library's default socket timeout | PERF-002 is reachable or not; its confidence rises or it is refuted | Deployment env, plus a black-hole SMTP test in staging |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | `safe-on-production` | `psql "$DATABASE_URL" -c "SELECT relname, n_live_tup, seq_scan, idx_scan FROM pg_stat_user_tables WHERE relname IN ('item','user');"` | Large `item` with rising `seq_scan` keeps PERF-001 at P1; a small table drops it to P3 |
| PERF-001 | `safe-on-production` | `psql "$DATABASE_URL" -c "EXPLAIN SELECT * FROM item WHERE owner_id = '00000000-0000-0000-0000-000000000000' ORDER BY created_at DESC OFFSET 0 LIMIT 100;"` | A Seq Scan plan confirms the mechanism |
| PERF-002 | `safe-on-production` | `psql "$DATABASE_URL" -c "SELECT state, count(*), max(now() - state_change) FROM pg_stat_activity WHERE datname = current_database() GROUP BY state;"` | Long "idle in transaction" app connections confirm that connections are held across non-DB work |

---

## 2. Scope and method

**Reviewed:** The `backend` service. That covers `app/main.py`, `app/api/` (deps and all routes), `app/core/` (config, db, security), `app/crud.py`, `app/models.py`, `app/utils.py`, all five Alembic migrations, `backend_pre_start.py`, `initial_data.py`, `backend/Dockerfile`, `compose*.yml`, and the deploy workflows. From `frontend`, only the two routes that call the list endpoints (`items.tsx`, `admin.tsx`, `DataTable.tsx`), to establish how the API is consumed.

**Not reviewed:** Frontend UI/bundle performance and the `packages/react-email` templates (out of scope for a backend review). The FastAPI Cloud runtime configuration (not in the repository). `.env` and `frontend/.env` exist but were not opened (secret files).

**Evidence available:** Partially instrumented. Sentry tracing is wired in (`app/main.py:18-19`) but only when `SENTRY_DSN` is set. Traefik's access log is enabled (`compose.yml:16`). There are no metrics endpoint, benchmarks, load tests, committed query plans, query-count tests, SLOs, or dashboards. No runtime artifacts were supplied, so nothing here can be `Confirmed`.

**Ranking method:** Structural signals only. No runtime data exists, so every ranking below is inference.

**Reference depth:** Postgres, Python, REST, and Docker have deep references. The `node` signal on the backend service comes from the Dockerfile's frontend build stage, not from a Node backend, so Node-specific reasoning was not applied. SMTP (via the `emails` library) has no technology reference; it was analysed with the timeouts and connection-pool category principles.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Every backend application module, every migration, and all container/deploy config; frontend only at the two API call sites |
| **Critical paths** | 9 / 9 |
| **Shared resources** | 6 / 6 (pool per worker process, threadpool per worker process, Postgres primary, SMTP server, Sentry, process memory). The pool's sizes are unpinned library defaults, so its arithmetic is incomplete |
| **Technology support** | Postgres: deep; Python: deep; REST/FastAPI: deep; Docker: deep; SMTP/`emails`: category principles only |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

**Review confidence is not finding confidence.** The code paths are small and were read in full. The uncertainty comes from workload and deployment facts, not from unread code.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Row counts of `item` and `user` | No evidence exists | `pg_stat_user_tables` |
| Per-process pool size, instance count, `max_connections` | No evidence exists | Deployment settings; `SHOW max_connections` |
| Default socket timeout of `emails` 1.1.2's SMTP backend | No evidence exists (library source not in repo) | Read the installed library, or time a send to a black-hole host |
| Actual plans for the list and count queries | No evidence exists | `EXPLAIN` on production-shaped data |
| FastAPI Cloud workers/instances/limits | Not examined (not in repo) | FastAPI Cloud app settings |
| SMTP client internals | Technology unsupported (no `emails`/SMTP reference) | A library-level review, or a staging timeout test |

---

## 3. Architecture overview

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|
| API | FastAPI / Starlette on uvicorn | fastapi 0.141.1, starlette 1.3.1, uvicorn 0.49.0 (uv.lock) | deep | HTTP API, 4 worker processes (`backend/Dockerfile:65`) |
| Runtime | CPython | 3.14 (`backend/Dockerfile:19`) | deep | All handlers that touch the DB are sync `def` and run in the per-process threadpool |
| ORM / driver | SQLModel / SQLAlchemy / psycopg 3 | 0.0.39 / 2.0.51 / 3.3.4 | deep | Session per request via `get_db` (`deps.py:21`). Engine has default pool settings (`core/db.py:7`) |
| Datastore | PostgreSQL | 18 (`compose.yml:21`) | deep | Single primary. Tables `user`, `item` |
| Password hashing | pwdlib (Argon2 + bcrypt) | 0.3.0, argon2-cffi 25.1.0 | — | Login, signup, password change |
| Email | `emails` over SMTP | 1.1.2 | generic | Synchronous send from request handlers |
| Tracing | sentry-sdk | 2.66.1 | — | Enabled only when `SENTRY_DSN` is set |
| Proxy | Traefik | 3.6 | — | TLS and routing; no rate limiting configured |

**Shared resources:** the SQLAlchemy connection pool and the threadpool in each of the 4 worker processes; the single Postgres primary; the external SMTP server; Sentry ingestion; container memory (no limit set in compose).

---

## 4. Workload model

**Known**

- 4 uvicorn worker processes per container. Source: `backend/Dockerfile:65`
- One backend service in Compose, with no replica count. Source: `compose.yml:49`. The FastAPI Cloud instance count is not stated (`.github/workflows/deploy.yml`)
- Engine created with no pool, timeout, or pre-ping arguments. Source: `backend/app/core/db.py:7`
- No `statement_timeout`, `idle_in_transaction_session_timeout`, or `max_connections` is configured. Source: `compose.yml:20-32`
- The first-party UI always requests `skip=0, limit=100`. Source: `frontend/src/routes/_layout/items.tsx:15`, `admin.tsx:15`
- Route shape: 5 GET routes (excluding the health check) and 16 POST/PUT/PATCH/DELETE routes (excluding dev-only `/private`). Source: `backend/app/api/routes/*.py`. Which routes are busiest in production is unknown
- No retention, cleanup, or scheduled jobs. `item` is written on every `POST /items/`. Source: whole `backend/app` tree
- Signup and password recovery are unauthenticated. Source: `users.py:146`, `login.py:53`

**Assumed**

- `item` grows without bound over time. Affects PERF-001
- SMTP is enabled in at least some deployments (compose passes `SMTP_HOST` through). Affects PERF-002
- Login sees some unauthenticated traffic that nothing in-app limits. Affects PERF-004
- `SENTRY_DSN` is set in deployments that follow `deployment.md:27`. Affects PERF-005

**Unknown**

- Request rates; `item`/`user` row counts; production platform and instance count; `max_connections`; whether SMTP and Sentry are on in production; any latency target

**Derived**

- Postgres connections per container = 4 workers × (per-process pool size + overflow). The pool terms are library defaults not written in the repo, so the total is **not computed**. From: `Dockerfile:65`, `core/db.py:7`
- Statements per `GET /items/` = 1 auth PK lookup + 1 COUNT + 1 page SELECT = **3**, independent of page size. From: `deps.py:41`, `items.py:29-42`
- Memory per concurrent unknown-email login verification = `m=65536` KiB, as encoded in `DUMMY_HASH` = **64 MiB**. From: `crud.py:42`

**Measured**

- None. No benchmark, trace, query plan, or metrics export was supplied or is committed.

### Questions that would change the ranking

Nobody was available to answer these. The review proceeded under the assumptions above.

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | Is there a specific performance problem (slow endpoint, incident, cost) behind this review? | severity / recommendation | Would reorganise the review around that symptom. All current ranks are structural |
| 2 | highest | Roughly how many rows are in `item`, and what is the largest per-user item count? | severity / confidence | Small: PERF-001 → Low/P3. Large and growing: PERF-001 → High confidence, stays P1. Raises PERF-003/006 |
| 3 | high | Where does production run, how many instances, and what is `max_connections`? | severity / recommendation | Completes the pool arithmetic. May add a pool-sizing finding or rule one out; confirms PERF-002 blast radius |
| 4 | high | Is SMTP enabled in production, and has recovery or user creation ever hung? | severity / confidence | SMTP off: PERF-002 is unreachable. A past hang raises its confidence |
| 5 | medium | Peak login rate, and is there an upstream rate limit? | severity / recommendation | With an upstream limit or a low rate, PERF-004 → P3 |
| 6 | low | Is `SENTRY_DSN` set in production, and at what volume? | severity / recommendation | Unset: PERF-005 is unreachable. High volume makes it a cost decision |

---

## 5. Critical path analysis

Ranked by structural signals.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /items/` (non-superuser) | yes | PK lookup + COUNT + page SELECT, both filtered on unindexed `owner_id` | Rows returned: no (caller `limit`). Rows examined: whole table | Sentry only if DSN set | PERF-001, PERF-003 |
| 2 | `POST /password-recovery/{email}` | yes | SELECT by email (indexed), then SMTP while the connection is still checked out | No SMTP timeout set | same | PERF-002; unauthenticated |
| 3 | `POST /login/access-token` | yes | SELECT by email, then Argon2 verify while the connection is still checked out | CPU/memory per call is fixed; concurrency is unbounded | same | PERF-004 |
| 4 | `POST /users/` (admin create) | yes | SELECT, INSERT, commit, refresh SELECT, then SMTP | No SMTP timeout set | same | PERF-002 |
| 5 | `GET /users/` (superuser) | yes | COUNT(*) full table + ORDER BY created_at (no index) | Rows returned: no | same | PERF-003. Low frequency |
| 6 | `DELETE /users/me` | yes | ORM loads and deletes each owned item | O(user's items) | same | PERF-006 |
| 7 | `DELETE /users/{id}` (admin) | yes | Bulk DELETE on unindexed `owner_id`, plus FK cascade | Set-based | same | Covered by PERF-001's index |
| 8 | `POST/PUT/DELETE /items/{id}` | yes | PK get + write | yes | same | No candidate |
| 9 | Any authenticated route | yes | One PK lookup in `get_current_user` | yes | same | Considered, not reported |

**Amplification points:** per-row `ItemPublic.model_validate` in `GET /items/`, multiplied by a caller-chosen `limit` (PERF-003). Each COUNT and page query examines rows in proportion to the whole `item` table, not the caller's share (PERF-001).

**Paths deliberately not analyzed in depth:** `/private/users/` (mounted only when `FASTAPI_ENV=development`, `api/main.py:13`); `/utils/health-check/` (async, no I/O); `/password-recovery-html-content/{email}` (superuser-only, no SMTP); startup scripts (`backend_pre_start.py`, `initial_data.py`), which run once per deploy.

---

## 6. Layer analysis

### 6.1 Application

All DB-touching handlers are sync `def`, so FastAPI runs them in the per-process threadpool and the event loop is never blocked by DB, SMTP, or hashing work (checked across all route modules). The application-level costs are therefore capacity costs, not event-loop stalls. The two heavy synchronous operations in request paths are SMTP delivery (PERF-002) and Argon2 verification (PERF-004). Both happen while the request's session already holds a pooled connection.

### 6.2 API

The list endpoints have defaults but no maximum for `limit` and `skip` (PERF-003). Response models carry no relationship fields, so serialization cannot trigger lazy loads. The bundled UI fetches 100 rows and paginates client-side, which also hides rows beyond 100 (COR-001, adjacent).

### 6.3 Data access and datastore

- The schema has only primary keys and `ix_user_email`. The FK column `item.owner_id` is unindexed, and so is `created_at` on both tables (PERF-001).
- Every list request pays an exact `COUNT` in addition to the page query. With an index on the owner, the per-user count becomes an index range scan. The superuser count stays a full traversal; it is low-frequency and was not promoted separately.
- The session is request-scoped. After the first SELECT, a connection stays checked out until the session closes at the end of the request, so any non-DB work after the first query holds it (PERF-002, PERF-004).
- The engine has no pool settings, so pool size, overflow, and acquisition timeout are library defaults. Postgres has no `statement_timeout` or `idle_in_transaction_session_timeout`. Pool arithmetic could not be completed without instance count and `max_connections`, so it is recorded as question 3 rather than a finding.
- `DELETE /users/me` relies on ORM cascade rather than the DB's `ON DELETE CASCADE` (PERF-006).

### 6.4 Infrastructure

Docker Compose deploys one backend container with `--workers 4` and no CPU or memory limits. Each worker has its own pool and threadpool, so connections and memory multiply by 4. The FastAPI Cloud path (`deploy.yml`) does not describe its runtime, which is an unknown, not a finding.

### 6.5 Observability

Sentry tracing is opt-in via `SENTRY_DSN`. When on, it samples at full rate (PERF-005). Nothing exposes pool checkout/wait time, query counts, or per-route latency outside Sentry. No test asserts query counts, and the repository holds no query plans or load tests. This is why no finding can reach `Confirmed`, and why §9 lists instrumentation gaps first.

---

## 7. Findings

### PERF-001 — `item.owner_id` has no index; every user's item list scans the whole table

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | data-access |
| **Location** | `backend/app/models.py:97` (`Item.owner_id`); queries at `backend/app/api/routes/items.py:29-42` |
| **Tags** | scalability-risk, quick-win |

**Problem**
`GET /items/` for every non-superuser runs `SELECT count(*) … WHERE owner_id = ?` and `SELECT … WHERE owner_id = ? ORDER BY created_at DESC OFFSET ? LIMIT ?`. Neither the filter nor the sort has a supporting index, so both statements must read the entire `item` table, which holds every user's rows.

**Performance principle**
Per-request work should scale with the data the request returns, not with the total size of a shared table.

**Evidence**
- `backend/app/models.py:97-99`: `owner_id` is declared with `foreign_key="user.id"` and `ondelete="CASCADE"`, without `index=True`.
- `backend/app/alembic/versions/e2412789c190_initialize_models.py:33`: `ix_user_email` is the only secondary index ever created. The later migrations (`9c0a54914c78`, `d98dd8ec85a3`, `1a31ce608336`, `fe56fa70289e`) add no index on `item`.
- `backend/app/alembic/versions/1a31ce608336_add_cascade_delete_relationships.py:26`: the FK is recreated with `ON DELETE CASCADE`. The referencing column remains unindexed, so user deletes also look up `item.owner_id` without an index (`users.py:228`).
- No runtime evidence: no plan, slow-query log, or row count exists in the repository. The claim is "this predicate and sort have no supporting index", which is provable. It is not "this query does a sequential scan", which needs a plan.

**Impact**
- Position: critical path (the items page every user lands on).
- Frequency: per request, two statements.
- Growth: O(n) in **total** `item` rows, not in the caller's rows.
- Blast radius: system-wide. Each scan holds a pooled connection and consumes I/O and CPU on the single shared Postgres primary.

**Conditions**
Matters once `item` is large enough that a full scan is no longer cheap. No row counts are in the repository. `item` is written on every `POST /items/` and has no retention job, so unbounded growth is the default expectation. At small sizes a scan is the right plan, and this finding costs nothing.

**Counter-evidence**
- Searched every migration and model for any index on `owner_id` or `created_at`, including composite ones. Found none (no effect).
- The UI's `limit=100` bounds rows returned, not rows examined (no effect).
- Table size is unknown, and a small table makes this moot. This lowers confidence to Medium.

**Why this might not matter**
Many deployments of this template will have a few thousand items. Postgres scans that cheaply, and the index would then add only write cost.

**Recommendation**
Add a new Alembic migration that creates an index on `item (owner_id, created_at DESC)`, built `CONCURRENTLY` on a live database. This one index serves the per-owner filter. It supplies the `ORDER BY` so `OFFSET/LIMIT` reads only the requested page. It turns the per-owner `COUNT` into an index range scan, and it covers the FK lookup used by `delete_user` and by `ON DELETE CASCADE`. Decide separately on an `item (created_at DESC)` index for the superuser listing, based on superuser traffic.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Composite `(owner_id, created_at DESC)` | **preferred** | Matches the predicate and the sort. One index fixes the list, the count, and the delete/cascade paths |
| `owner_id` only | | Fixes the filter and the FK, but still sorts all of the owner's rows on every page |
| Replace exact count with `has_more` | | Removes one statement but changes the API contract; the page query still scans |
| Cache list or count | | Hides the scan behind invalidation on every item write |

**Trade-offs**
One more B-tree to maintain on every insert, delete, and relevant update of `item`. Storage grows with the row count. `CREATE INDEX CONCURRENTLY` cannot run inside a transaction, which affects how the Alembic migration is written.

**Validation**
See §9, PERF-001.

---

### PERF-002 — Synchronous SMTP with no explicit timeout, sent while holding a pooled DB connection

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | networking |
| **Location** | `backend/app/utils.py:55` (`send_email`); callers `login.py:67`, `users.py:73`, `utils.py` route `:21` |
| **Tags** | quick-win |

**Problem**
Password recovery (unauthenticated), admin user creation, and the test-email route send mail synchronously inside the request. `send_email` passes no timeout to the SMTP client. In password recovery and user creation, the request's session has already run a SELECT, so a pooled Postgres connection stays checked out in an open transaction for the whole SMTP exchange.

**Performance principle**
A shared, bounded resource must not be held across a call to a dependency whose latency you do not control, and every outbound call needs a bounded worst case.

**Evidence**
- `backend/app/utils.py:46-55`: `smtp_options` contains `host`, `port`, `tls`/`ssl`, `user`, `password`. There is no timeout. `message.send(...)` is called inline.
- `backend/app/api/routes/login.py:58-71`: `get_user_by_email` (a SELECT, which starts a transaction) and then `send_email`. The session closes only when `get_db` exits (`deps.py:21-23`).
- `backend/app/api/routes/users.py:68-77`: `crud.create_user` commits, then `refresh` runs a SELECT (a new transaction), and then `send_email`.
- `backend/app/core/db.py:7`: no pool acquisition timeout is configured. `compose.yml:20-32`: no `idle_in_transaction_session_timeout`.
- No runtime evidence of SMTP latency or a hang exists.

**Impact**
- Position: critical path.
- Frequency: per request on the email routes (a small share of traffic).
- Growth: O(1).
- Blast radius: system-wide within a worker process. Each stuck request holds one threadpool slot and one pooled connection, and once enough are stuck, every route in that process waits for a connection.

**Conditions**
Matters when SMTP is enabled (`SMTP_HOST` and `EMAILS_FROM_EMAIL` set) and the mail server is slow or goes silent after connecting. `POST /password-recovery/{email}` needs no authentication, and signup is open, so any caller can generate the concurrency needed to exhaust a process's pool.

**Counter-evidence**
- Handlers are sync `def`, so the event loop is not blocked. This bounds the impact to threadpool and pool capacity.
- `emails` 1.1.2 may apply its own default socket timeout. Its source is not in the repo, so this could not be confirmed. This lowers confidence to Medium.
- Email routes are a small share of traffic, and test-email is superuser-only. This bounds the impact.
- Searched for `BackgroundTasks`, a queue, or a mail worker. Found none (no effect).

**Why this might not matter**
If production has no SMTP configured, the code never runs. If the library's default timeout is short and the provider is reliable, each send holds a connection only briefly.

**Recommendation**
(1) Pass an explicit, short SMTP timeout. Verify the option name `emails` 1.1.2's SMTP backend accepts. (2) Release the DB transaction before sending. In `recover_password`, read `user.email` and then close the session or end the transaction before `send_email`. In `create_user`, send after the session's work is complete, without the post-commit refresh holding a connection. (3) Optionally move the send to a FastAPI `BackgroundTask` so the response does not wait on SMTP. As a database-side backstop, set `idle_in_transaction_session_timeout` for the application role.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Explicit timeout + release session before send | **preferred** | Local, small change that removes both the unbounded wait and the connection hold |
| `BackgroundTasks` | | Takes SMTP off the response, but uses the same threadpool and still needs a timeout. A complement |
| External job queue for email | | Fully decouples mail, but adds a broker and a worker the template does not otherwise need |
| Bigger pool | | Moves the exhaustion point and adds idle-in-transaction connections to Postgres |

**Trade-offs**
A short timeout turns slow mail into an error. Password recovery must still return the same response whether or not the email exists, so log the failure rather than surface it. Releasing the session early forbids lazy ORM access afterwards. Background sending loses synchronous error feedback for admins.

**Validation**
See §9, PERF-002.

---

### PERF-003 — List endpoints accept unbounded `limit` and `skip`

| | |
|:--|:--|
| **Root cause** | `ROOT-003` |
| **Severity** | Medium |
| **Confidence** | High |
| **Priority** | P2 |
| **Category** | networking |
| **Location** | `backend/app/api/routes/items.py:15` (`read_items`); also `backend/app/api/routes/users.py:37` (`read_users`) |
| **Tags** | quick-win |

**Problem**
`limit` and `skip` are plain `int` parameters with defaults and no maximum. One request can load, re-validate (`ItemPublic.model_validate` per row, `items.py:44`), and serialize every matching row. A deep `skip` makes Postgres produce and discard the skipped rows.

**Performance principle**
Every read endpoint needs an enforced ceiling on response size; a default the caller can override is not a bound.

**Evidence**
`items.py:15` (`skip: int = 0, limit: int = 100`), `items.py:44` (per-row validation), `users.py:37` (same signature, superuser-only), `users.py:146` (open signup means any caller can obtain a token). Derived: rows per response ≤ `limit`, and `limit` is unbounded.

**Impact**
- Position: critical path.
- Frequency: per request.
- Growth: O(n) in matching rows when `limit` is large.
- Blast radius: endpoint (worker memory and the connection for that request).

**Conditions**
Only for callers other than the bundled UI that pass a large `limit` or deep `skip`, and only up to the rows that exist. A non-superuser is capped by their own items; a superuser by the whole table.

**Counter-evidence**
The UI always sends `limit=100` (`items.tsx:15`), which bounds the impact. Non-superusers see only their own rows (`items.py:37`), which also bounds it. Searched for a global page-size cap and found none (no effect). Severity is Medium rather than High because of these bounds.

**Why this might not matter**
If only the bundled frontend calls the API, no request ever exceeds 100 rows.

**Recommendation**
Declare `limit: int = Query(100, ge=1, le=<max>)` and `skip: int = Query(0, ge=0)` on both list endpoints, with the max matching what the UI needs. If deep paging becomes real, consider keyset pagination on `(created_at, id)`, which uses the PERF-001 index.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| `Query` bounds | **preferred** | Two lines; documented in OpenAPI; returns 422 for oversize requests |
| Keyset pagination | | Removes deep-offset cost but changes the contract and loses random page access |
| Silent clamp in handler | | Bounds the cost but hides the contract |

**Trade-offs**
Clients asking for more than the cap get 422 and must paginate. Regenerate the frontend client.

**Validation**
See §9, PERF-003.

---

### PERF-004 — Unbounded concurrent Argon2 verification on unauthenticated login, with a connection checked out

| | |
|:--|:--|
| **Root cause** | `ROOT-004` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | concurrency |
| **Location** | `backend/app/crud.py:45` (`authenticate`) |
| **Tags** | needs-measurement |

**Problem**
Every `POST /login/access-token`, including attempts for unknown emails, performs a deliberately expensive Argon2 verification. Nothing limits how many run at once per process. Each runs after the user SELECT, so it also holds a pooled DB connection.

**Performance principle**
Deliberately expensive work on an unauthenticated path needs a concurrency bound, and shared resources should not be held during it.

**Evidence**
- `crud.py:45-60`: SELECT, then `verify_password`, or verification against `DUMMY_HASH` when the user is missing.
- `crud.py:42`: `DUMMY_HASH` encodes `m=65536, t=3, p=4`. Derived: 65536 KiB = **64 MiB** per concurrent unknown-email verification.
- `core/security.py:11-16`: Argon2 and bcrypt with library-default parameters.
- No rate limiting in the app or in the Traefik flags (`compose.yml:8-18`, `compose.deploy.yml:13-32`).
- Concurrency is bounded only by the threadpool (a library default, not set here) × 4 workers (`Dockerfile:65`).

**Impact**
- Position: critical path.
- Frequency: per login.
- Growth: O(1) per call.
- Blast radius: service (CPU, memory, and pool capacity shared with every route in the process).

**Conditions**
Matters under bursts of login attempts, such as credential stuffing, that nothing in-repo limits. At ordinary interactive login rates it is negligible. The login rate is unknown.

**Counter-evidence**
The dummy verification is an intentional anti-enumeration control and must stay; this bounds what can change. Handlers are sync, so the event loop is unaffected (bounds the impact). Whether an upstream platform rate-limits login is unknown, which lowers confidence.

**Why this might not matter**
Login is low-frequency for most apps, a platform or WAF may already rate-limit it, and the hashing cost is by design.

**Recommendation**
Do not weaken the hash. Measure a login burst in staging first. If contention appears, bound concurrent verifications per process (a semaphore with its own timeout) and rate-limit `/login/access-token` at the proxy. Separately, end the DB transaction before verifying so the slow step does not hold a connection.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Measure, then semaphore + proxy rate limit | **preferred** | Keeps the security property and caps per-process CPU and memory |
| Lower Argon2 parameters | | That is a security trade, not a performance fix |
| More instances | | Capacity that abusive traffic consumes equally |

**Trade-offs**
A semaphore queues logins under burst. Proxy limits can affect users behind shared NAT. Releasing the session complicates the rehash-on-login write.

**Validation**
See §9, PERF-004.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-005 | Low | Medium | P3 | `backend/app/main.py:19` (`sentry_sdk.init`) | `enable_tracing=True` with no `traces_sample_rate` means full sampling in sentry-sdk 2.x when a DSN is set. This is per-request span overhead and billed volume, including the health check that compose calls every 10s. Set an explicit rate or a sampler. Trade-off: fewer traces for rare slow requests. Falsifier: Sentry already ingests far fewer transactions than requests. |
| PERF-006 | Low | Medium | P3 | `backend/app/api/routes/users.py:141` (`delete_user_me`) | `session.delete(current_user)` with `cascade_delete=True` and no passive deletes makes the ORM load and delete each owned item, although the FK already has `ON DELETE CASCADE` and the admin path already issues a bulk `DELETE`. Fix: `passive_deletes=True`, or a bulk delete first. Validate by counting statements for a user with N items. |

### Considered and not reported

- **Event-loop blocking.** Path: all routes and the per-process event loop. Checked all route modules. Refuted: every DB/SMTP/hash path is sync `def` (threadpool), and the only `async def` route does no I/O. Revisit if any DB-touching handler becomes `async def` while still using the sync `Session`.
- **N+1 through `Item.owner` / `User.items` during serialization.** Path: `GET /items/`, `GET /users/`. Checked `models.py`, `items.py:44`, `users.py:50`. Refuted: the public models have no relationship fields. Revisit if a response model adds `owner` or `items`.
- **Pool size × 4 workers × instances vs `max_connections`.** Path: the pool in each worker process and the Postgres primary. Checked `core/db.py:7`, `Dockerfile:65`, compose, and deploy workflows. Not a finding: pool terms are unpinned defaults, and instance count and `max_connections` are absent, so it is workload question 3. Revisit when those are known or pool wait time is observed.
- **Per-request PK lookup in `get_current_user`.** Path: every authenticated route. Bounded single-row PK lookup, required for the `is_active` check. Revisit if profiling shows auth lookups as a meaningful share of DB time.

### Adjacent findings — outside performance scope

### SEC-001 — New-account email contains the plaintext password

| | |
|:--|:--|
| **Kind** | Security |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `backend/app/api/routes/users.py:69-77` |

**Problem** When a superuser creates a user and email is enabled, the user's initial password is sent in clear text in the email body.

**Evidence** `users.py:71` passes `password=user_in.password` to `generate_new_account_email`. `app/email-templates/new_account.html` renders `{{ password }}`.

**Impact** Medium risk: the credential then sits in mail servers and inboxes outside the application's control, and the template only asks the user to change it.

**Recommendation** Send a set-password link using the existing reset-token flow instead of the password.

**Trade-offs** One more step for new users; the reset-token expiry must suit onboarding.

**Validation** Create a user in the compose stack and inspect the email in Mailpit; it should contain a link and no password.

**Would need** A security review of account provisioning and credential handling.

### COR-001 — UI shows at most 100 items or users

| | |
|:--|:--|
| **Kind** | Correctness |
| **Confidence** | High |
| **Risk** | Medium |
| **Location** | `frontend/src/routes/_layout/items.tsx:15`, `frontend/src/routes/_layout/admin.tsx:15` |

**Problem** The UI fetches `skip=0, limit=100` once and paginates locally (`DataTable.tsx:125`). Rows past 100 are invisible in the UI, even though the API returns the total `count`.

**Evidence** The three file references above.

**Impact** Medium risk: users with more than 100 items silently lose access to the rest in the UI, with no error.

**Recommendation** Drive the table with server-side pagination using `skip`/`limit` and the API's `count`. This also lets the UI ask for smaller pages.

**Trade-offs** One request per page change instead of one per load.

**Validation** Seed 150 items for a user and confirm all of them can be reached in the UI.

**Would need** A frontend correctness test suite with more than 100 seeded rows.

---

## 8. Prioritized action plan

No P0 findings. Two P1, two P2, two P3.

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 0 | (instrumentation) | — | Small | Run the §9 baseline queries and add pool and SQL visibility first, so the P1 changes can be validated |
| 1 | PERF-001 | P1 | Small (one migration, concurrent build) | Hot path; cost grows with total data; also covers delete paths |
| 2 | PERF-002 | P1 | Small | Unauthenticated route can pin pooled connections on a degraded dependency |
| 3 | PERF-003 | P2 | Very small | Quick-win, sequenced early because it costs two lines; priority unchanged |
| 4 | PERF-004 | P2 | Medium (measure first) | Needs a staging burst test before any change |
| 5 | PERF-005 | P3 | Very small | Config-only; decide once Sentry volume is known |
| 6 | PERF-006 | P3 | Very small | Rare path; set-based delete for consistency with the admin path |

**If only one thing is done:** run `pg_stat_user_tables` and `EXPLAIN` for the item list query. If `item` is non-trivial, add the `item (owner_id, created_at DESC)` index (PERF-001).

---

## 9. Validation plan

### PERF-001

- **Baseline:** row counts and scan counters for `item`; the plan for the per-owner page and count queries. Safe on production (`EXPLAIN` without `ANALYZE`, stats views).
- **Change:** concurrent composite index `item (owner_id, created_at DESC)`.
- **Measurement:** plan node type and estimated rows for both statements; `seq_scan` vs `idx_scan` on `item` over a day of traffic.
- **Expectation:** the plan moves from Seq Scan on `item` to an index scan. Rows examined for a page fall to roughly `OFFSET+LIMIT`, and for the count to the owner's item count. Latency change is not quantified until measured.
- **Falsifier:** if the current plan is already cheap on production-shaped data, or `seq_scan` on `item` does not grow with list traffic, the table is too small for this to matter; drop to P3.
- **Guard:** a test that runs `EXPLAIN` on the list query against seeded data and fails on `Seq Scan on item`, or a lint that every FK column has an index.
- **Commands:**
  - `safe-on-production` `psql "$DATABASE_URL" -c "SELECT relname, n_live_tup, seq_scan, idx_scan FROM pg_stat_user_tables WHERE relname IN ('item','user');"`. Purpose: row counts and scan counters.
  - `safe-on-production` `psql "$DATABASE_URL" -c "EXPLAIN SELECT * FROM item WHERE owner_id = '00000000-0000-0000-0000-000000000000' ORDER BY created_at DESC OFFSET 0 LIMIT 100;"`. Purpose: plan only, no execution.
  - `not-safe-on-production` `psql "$DATABASE_URL" -c "EXPLAIN ANALYZE SELECT count(*) FROM item WHERE owner_id = '00000000-0000-0000-0000-000000000000';"`. Purpose: actual rows examined. Run on a replica or staging copy.

### PERF-002

- **Baseline:** in staging or the local compose stack, point SMTP at a non-routable host and time `send_email`; watch `pg_stat_activity` during a password-recovery request. The `pg_stat_activity` read is safe on production; the black-hole test is not.
- **Change:** explicit SMTP timeout; session released before sending.
- **Measurement:** request wall time against the black-hole host; app connections in state `idle in transaction` during the send.
- **Expectation:** after the change, the request ends within the configured timeout, and no app connection is idle in transaction while mail is being sent.
- **Falsifier:** if the request already fails fast and no idle-in-transaction connection appears during the send, the library and session already bound this; drop the finding.
- **Guard:** a test with `SMTP_HOST` set to a non-routable address that asserts recovery completes within the timeout.
- **Commands:**
  - `safe-on-production` `psql "$DATABASE_URL" -c "SELECT state, count(*), max(now() - state_change) FROM pg_stat_activity WHERE datname = current_database() GROUP BY state;"`. Purpose: idle-in-transaction count and age.
  - `not-safe-on-production` `time docker compose exec -e SMTP_HOST=10.255.255.1 -e EMAILS_FROM_EMAIL=probe@example.com backend python -c "from app.utils import send_email; send_email(email_to='probe@example.com', subject='t', html_content='t')"`. Purpose: shows whether the library bounds the wait. Local or staging only.

### PERF-003

- **Baseline:** `GET /items/?limit=100000` in staging returns all matching rows.
- **Change:** `Query(ge=…, le=…)` bounds.
- **Measurement:** status code and row count for oversize requests.
- **Expectation:** 422 above the cap; accepted responses never exceed the cap.
- **Falsifier:** an existing proxy or global rule already rejects large `limit` values.
- **Guard:** a pytest asserting 422 for `limit` above the cap.
- **Commands:**
  - `not-safe-on-production` `cd backend && uv run pytest tests/api/routes/test_items.py -q`. Purpose: run the item route tests, including the new bound test, against the test database.

### PERF-004

- **Baseline:** a controlled burst of failed logins in staging; record login p95/p99, process RSS, latency of an unrelated route, and pool checkouts. Not safe on production.
- **Change:** a per-process concurrency bound on verification, a proxy rate limit, and the session released before verify, applied only if the baseline shows contention.
- **Measurement:** the same metrics under the same burst.
- **Expectation:** RSS growth and the latency impact on unrelated routes are capped; login latency under burst rises by design.
- **Falsifier:** no measurable effect on unrelated routes or memory at the highest plausible login rate; drop to P3.
- **Guard:** alerts on login rate and process RSS.
- **Commands:**
  - `safe-on-production` `py-spy top --pid <backend-worker-pid>`. Purpose: live CPU attribution during a burst, no restart needed.

### PERF-005

- **Baseline / Measurement:** Sentry transactions ingested per minute compared with request count from the Traefik access log. Safe on production.
- **Expectation:** ingested transactions fall in proportion to the chosen rate, and health-check transactions disappear if filtered.
- **Falsifier:** ingestion is already well below request volume.

### PERF-006

- **Measurement:** SQL statements issued by `DELETE /users/me` for a user with N items (engine echo in a test). Not safe on production.
- **Expectation:** after the change, a constant statement count independent of N.
- **Falsifier:** the log already shows no item SELECT and a single DELETE.

### Instrumentation gaps to close first

1. **SQL visibility in tests:** a query-count fixture (an SQLAlchemy `before_cursor_execute` listener) so PERF-001/003/006 regressions fail CI.
2. **Pool visibility:** log or export pool checkout counts and wait time per worker. Without these, nobody can tell whether question 3's arithmetic is a real constraint.
3. **Per-route latency:** Traefik's access log already records request durations. Retain and aggregate it per route, or rely on Sentry at a deliberate sample rate (PERF-005).
4. **Postgres:** consider enabling `pg_stat_statements` and slow-query logging with a threshold, on staging first.

---

## 10. Machine-readable output

The machine-readable form of this review is emitted alongside this report at `out/fastapi-guided-1.json`. It conforms to the bundled `schemas/review.schema.json` and passed `scripts/validate_review.py` ("is a valid review"). Each finding's `stable_id` was computed with `scripts/compute_stable_id.py`:

| ID | stable_id | file / symbol / category |
|:--|:--|:--|
| PERF-001 | `b76b21f34aae4bcb` | `backend/app/models.py` / `Item.owner_id` / data-access |
| PERF-002 | `fab1da75f12f7c1c` | `backend/app/utils.py` / `send_email` / networking |
| PERF-003 | `2b8ae423d47b6b5d` | `backend/app/api/routes/items.py` / `read_items` / networking |
| PERF-004 | `5468d8a3b49432ee` | `backend/app/crud.py` / `authenticate` / concurrency |
| PERF-005 | `b254cb33ab77d64e` | `backend/app/main.py` / `sentry_sdk.init` / observability |
| PERF-006 | `64f6bb8cd18761a0` | `backend/app/api/routes/users.py` / `delete_user_me` / data-access |

If this Markdown and the JSON disagree, this Markdown is authoritative.

---

## 11. Notes on this review

- Findings are classified by evidence grade. None is `Confirmed`, because no runtime artifact exists.
- No runtime metric in this report was estimated or assumed. The only computed numbers are labelled derivations: 3 statements per item list request, and 64 MiB per dummy-hash verification. The pool-connection total was deliberately left uncomputed because its inputs are not in the repository.
- Nobody was available to answer the workload interview. The six questions in §4 were recorded as unanswered, and workload-dependent findings are capped at Medium confidence. If row counts or deployment facts become available, PERF-001, PERF-002, and PERF-004 are the findings most likely to move.
- Recommendations state their trade-offs and their validation path.
