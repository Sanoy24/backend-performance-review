# Example review — FastAPI + PostgreSQL + Redis

> **This is a synthetic example.** The repository does not exist. It illustrates the report
> structure, the scoring rubric, and — importantly — the restraint the methodology requires.
>
> **No runtime metric in this document was measured or invented.** Where the review needs a
> number it does not have, it says so. That is the point of the example as much as the
> findings are.
>
> This file lives in `docs/` and is never loaded as a skill reference, so its content cannot
> leak into a real review as a few-shot pattern.

---

# Performance Review: `orders-api`

**Date:** 2026-08-25
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v0.1.0

---

## 1. Executive summary

### Overall assessment

`orders-api` is a small FastAPI service backed by PostgreSQL with Redis used for caching and
as a Celery broker. The application logic is clean and readable, but two structural issues on
the primary read path mean latency and database load grow with the size of the data rather
than staying flat, and one outbound call has no bound on how long it can take.

The service is **uninstrumented for latency**: there are no traces, no endpoint timing
metrics, and no committed query plans or load tests. Consequently no finding in this review
reaches `Confirmed` confidence, and the ranking below is derived from structural signals
rather than measurement. Closing that gap is itself a P1 recommendation, because none of the
fixes proposed here can be validated without it.

### Most important findings

- **PERF-001 (P0)** — `GET /orders` issues one query per returned row and has no enforced
  maximum page size; cost scales with both traffic and data.
- **PERF-002 (P0, quick-win)** — The payment-gateway client sets a connect timeout but no read
  timeout; a hung upstream can occupy worker slots indefinitely.
- **PERF-003 (P1)** — At the autoscaler's configured maximum, the service can request more
  database connections than the instance permits.
- **PERF-004 (P1)** — Password hashing runs synchronously inside an async handler, blocking
  the event loop for the duration.

### Highest-risk bottlenecks

The connection pool is the shared resource most likely to fail first. Three separate findings
converge on it: PERF-001 holds connections for a query count proportional to page size,
PERF-003 can exhaust the database's connection limit during a scale-up, and the Celery worker
shares the same pool configuration as the API. A traffic spike that triggers scale-up is the
scenario in which all three interact.

Separately, PERF-002 is a *current* risk rather than a scaling one: it does not need growth to
cause an outage, only a slow upstream.

### Major unknowns

| Unknown | Would change |
|:--|:--|
| Request rate on `/orders` at peak | Whether PERF-001 is a current bottleneck or a scalability risk |
| Row count and growth rate of `orders` and `customers` | The severity of PERF-001; currently assumed to grow monotonically |
| Current p95/p99 latency, and whether any target exists | Whether any of this is already user-visible |
| Payment gateway's latency distribution | The correct value for the timeout recommended in PERF-002 |
| Actual replica count during normal operation | Whether PERF-003 is reachable today or only at maximum scale |

The user was asked these questions and did not have the data to hand. The review proceeded
under the assumptions stated in each finding's `Conditions` field, with workload-dependent
confidence capped at `Medium` where applicable.

---

## 2. Scope and method

**Reviewed:** `app/api/`, `app/services/`, `app/models/`, `app/workers/`, `migrations/`,
`docker-compose.yml`, `k8s/`, `gunicorn.conf.py`.

**Not reviewed:** the React front end in `web/`; the Terraform in `infra/` beyond reading the
database instance class; test fixtures. None are on a request path.

**Evidence available:** **Uninstrumented.** No OpenTelemetry, no Prometheus client, no APM
agent, no committed `EXPLAIN` output, no benchmarks, no load-test scripts. Structured logging
exists but does not record request duration. This caps confidence for the whole review.

**Ranking method:** Structural signals only. No runtime data was available to rank paths by
measured cost.

**Reference depth:** PostgreSQL and Redis are both at `deep` tier. Celery is detected as a
task queue at `generic` tier — broker-specific analysis is limited to the retry, backpressure,
and pool-sharing reasoning that applies to any queue.

---

## 3. Architecture overview

A single FastAPI service, deployed to Kubernetes, with a Celery worker deployment sharing the
same image and configuration module.

| Component | Technology | Version | Tier | Role |
|:--|:--|:--|:--|:--|
| API | FastAPI + Uvicorn | — | conceptual | HTTP entry point, 14 routes |
| Runtime | Python (ASGI, async handlers) | 3.12 | conceptual | Event loop per worker process |
| Primary datastore | PostgreSQL | 15 | **deep** | Orders, customers, payments |
| Cache | Redis | 7 | **deep** | Response caching, Celery broker |
| Worker | Celery | — | generic | Payment reconciliation, email |
| Orchestration | Kubernetes, HPA 2–20 replicas | — | conceptual | CPU-target autoscaling |

**Shared resources:** the PostgreSQL connection pool (API and worker use the same
`DATABASE_POOL_SIZE`); the Redis instance (cache and broker on one instance, database 0); one
event loop per Uvicorn worker.

The cache and broker sharing a single Redis instance is worth noting: broker traffic and cache
traffic contend for the same single-threaded command execution.

---

## 4. Workload model

**Known** — cited

- Pool size 10 (`app/db.py:22`), 4 Uvicorn workers (`gunicorn.conf.py:8`), HPA min 2 / max 20
  (`k8s/hpa.yaml:14-16`).
- PostgreSQL instance permits 100 connections (`infra/rds.tf:41`).
- `orders` is written on every checkout (`app/services/checkout.py:71`) and has no retention,
  archival, or partitioning in `migrations/`.
- `/orders` has a default page size of 50 and no maximum (`app/api/orders.py:31`).
- HPA scales on CPU utilization only (`k8s/hpa.yaml:22`).
- Redis is configured with `maxmemory-policy noeviction` (`k8s/redis-config.yaml:9`).

**Assumed** — stated, unverified

- `orders` grows monotonically. Basis: written per checkout, no retention job, no TTL, no
  archival. Affects PERF-001, PERF-006.
- Traffic is concentrated rather than uniform, since the HPA range is wide. Affects PERF-003.

**Unknown** — see §1.

---

## 5. Critical path analysis

Ranked by structural signals; no runtime data available.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /orders` | yes | 1 + 1 per row | **no** | no | Unbounded page size, per-row lookup |
| 2 | `POST /checkout` | yes | 3 in one transaction | yes | no | Transaction spans an external call |
| 3 | `POST /auth/login` | yes | 1 | yes | no | Synchronous password hash on the event loop |
| 4 | `GET /dashboard` | yes | 4, incl. an exact count | no | no | `COUNT(*)` over `orders` |
| 5 | worker: `reconcile_payments` | no | 1 per payment | no | no | Shares the API's pool configuration |

**Amplification points:** `/orders` — query count is proportional to page size, and page size
is caller-controlled and uncapped.

**Paths deliberately not analyzed in depth:** the 9 remaining routes are single-row reads or
writes on indexed primary keys, with bounded responses. Admin routes are internal and
low-frequency.

---

## 6. Layer analysis

### 6.1 Application

Handlers are thin and delegate to a service layer. One structural issue: `app/services/`
functions accept and return ORM model instances rather than plain data, which means lazy
relationship loading can be triggered anywhere downstream — including inside response
serialization, where it is easiest to miss. PERF-001 is an instance of this.

### 6.2 API

`GET /orders` accepts a caller-supplied `limit` with a default of 50 and no enforced maximum
(`app/api/orders.py:31`). A default is not a bound. Every other list route in the service caps
at 100, so this appears to be an oversight rather than a deliberate choice.

### 6.3 Data access and datastore

Covered by PERF-001 and PERF-006. Positively: all foreign keys have supporting indexes
(`migrations/0003_indexes.sql`), and query construction is parameterized throughout.

`POST /checkout` opens a transaction, calls the payment gateway, then commits
(`app/services/checkout.py:44-83`). Lock duration on the affected rows is therefore bounded by
the gateway's response time rather than by the database work — see PERF-007.

### 6.4 Cache

Redis caches customer lookups with a 300-second TTL (`app/cache.py:18`). There is no hit-rate
instrumentation, so the cache's effectiveness is unmeasurable — see PERF-005.

`maxmemory-policy` is `noeviction` (`k8s/redis-config.yaml:9`). For an instance used as a
cache, this converts memory pressure into write errors rather than eviction. Given the same
instance is also the Celery broker, `noeviction` may well be deliberate — evicting broker
state would lose tasks. The correct fix is likely to separate the two uses rather than change
the policy, which is why this is reported as an observation rather than a one-line change.

### 6.5 Distributed communication

Two outbound dependencies: the payment gateway and an email provider. Neither has a retry
policy. The gateway client sets `connect=2.0` but no read timeout — PERF-002.

### 6.6 Infrastructure

Container memory limit is 512Mi (`k8s/deployment.yaml:38`) against 4 Uvicorn workers. No
finding is raised: Python does not preallocate a heap, so there is no configured-heap-versus-
limit contradiction to check, and per-worker memory is unmeasured. Flagged as unknown rather
than as a finding.

The HPA scales on CPU only. This service is likely to become pool-bound before it becomes
CPU-bound, in which case requests queue while CPU stays low and the autoscaler does not react.
See PERF-008.

### 6.7 Observability

No request duration metric, no tracing, no query logging, no `pg_stat_statements` in the
database configuration, no load-test scripts. This is the reason nothing in this review is
`Confirmed`, and it means none of the recommendations below can currently be validated.
Closing it is sequenced first in the action plan.

---

## 7. Findings

### PERF-001 — `GET /orders` issues one query per row, with no bound on row count

| | |
|:--|:--|
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | data-access |
| **Location** | `app/api/orders.py:31`, `app/services/orders.py:58`, `app/models/order.py:24` |
| **Tags** | scalability-risk |

**Problem**
The handler fetches a page of orders, then accesses `order.customer` while serializing each
one. The relationship is lazy-loaded, so this issues one additional query per row. The page
size is caller-controlled with no enforced maximum.

**Performance principle**
Work repeated per item, multiplied by an unbounded item count. Query count scales with result
size, and result size scales with data.

**Evidence**
`app/models/order.py:24` declares `customer` with the default loading strategy — no
`selectinload` or `joinedload` appears on this path. `app/services/orders.py:58` returns model
instances, and `app/api/schemas.py:41` accesses `order.customer.name` during serialization.
`app/api/orders.py:31` reads `limit` from the query string with `default=50` and no `le=`
constraint, unlike the other list routes in the same module.

*No runtime evidence:* there is no query log, no APM span count, and no query-count test in
this repository. The query count is derived from the loading strategy declared in the model,
not observed.

**Impact**
Position: critical path — a client-facing GET. Frequency: one query per returned row, per
request. Growth: O(n·m) — query count grows with page size, and the underlying table grows
monotonically. Blast radius: system-wide — each query occupies a pooled connection, and the
pool is shared with the Celery worker.

Derivation: at the default page size the request issues 51 queries; a caller passing
`limit=1000` issues 1001. Both figures follow from the code, not from measurement.

**Conditions**
Matters whenever `/orders` is called with a page size above a handful, and increasingly as
`orders` and `customers` grow. Assumption: `orders` grows monotonically — it is written per
checkout (`app/services/checkout.py:71`) and `migrations/` contains no retention, archival, or
partitioning. Request rate on this endpoint is unknown, which is why confidence is not raised
further.

**Recommendation**
Two changes, addressing cause and bound separately:

1. Load the relationship in one additional query for the whole page (`selectinload`), or select
   the required customer fields in the original query. This removes the repetition rather than
   hiding it.
2. Enforce a maximum page size on this route, consistent with the `le=100` used elsewhere in
   the module.

**Trade-offs**
Eager loading transfers customer rows the caller may not need for every order, and if a batch
join were used instead of a separate query it would risk row multiplication where an order has
multiple one-to-many relations. Selecting only the needed fields avoids both, at the cost of
returning plain data rather than model instances from the service layer — which is a change
the service-layer issue in §6.1 arguably calls for anyway.

**Validation**
- *Baseline:* count queries issued by `GET /orders?limit=50` against a database with
  production-shaped data. Method: enable SQLAlchemy engine logging in a staging environment,
  or add a temporary query counter to the test suite. **safe-on-production: no** — use staging.
- *Expectation:* query count falls from 51 to 2 and becomes independent of page size. This is
  derived from the code and can be asserted. The resulting latency change **cannot** be
  predicted from available evidence — it depends on how much of this endpoint's time is spent
  in the database, which is unmeasured.
- *Falsifier:* if query count drops but endpoint latency does not change materially, the path
  was not query-bound and this finding's impact was overstated — reprioritize accordingly.
- *Guard:* a test asserting the query count for this route. Latency assertions are flaky;
  query-count assertions are not.

---

### PERF-002 — Payment gateway client has no read timeout

| | |
|:--|:--|
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | networking |
| **Location** | `app/clients/payments.py:19` |
| **Tags** | quick-win |

**Problem**
The HTTP client sets a connect timeout of 2 seconds but no read timeout. A gateway that
accepts the connection and then stops responding will hold the request open indefinitely.

**Performance principle**
An operation with no bound on its duration has an undefined worst case, and consumes a finite
resource for the whole of it.

**Evidence**
`app/clients/payments.py:19` constructs the client with `timeout=httpx.Timeout(connect=2.0)`,
leaving read, write, and pool timeouts unset. No retry or circuit-breaker wrapper exists on
this path. `app/services/checkout.py:44-83` calls it inside a database transaction.

*No runtime evidence:* the gateway's latency distribution is not measured anywhere in this
repository.

**Impact**
Position: critical path — `POST /checkout` blocks on it. Frequency: once per checkout. Growth:
O(1) — this is not a scaling issue. Blast radius: system-wide — each hung request holds a
worker slot, a pooled database connection, *and* an open transaction with row locks, until the
process is restarted.

**Conditions**
This does not require growth or load to cause an incident; it requires only a slow or hung
upstream. It is therefore a current risk, not a scalability risk. Confidence is not capped by
unknown workload, because the finding does not depend on workload.

**Recommendation**
Set an explicit read timeout, and a total timeout covering the whole request. The value should
be derived from the gateway's observed latency distribution — which is currently unmeasured,
so the first step is to measure it. Where a provisional value is needed before that data
exists, it should be treated as a bound to be corrected, not as a tuned setting.

Because this call sits inside a transaction, the timeout also bounds lock duration — see
PERF-007, which shares a root cause and should be fixed together.

**Trade-offs**
A read timeout that is too short will fail slow-but-successful payments, which for this
operation is worse than a slow response. This argues for measuring first and for making the
operation idempotent before adding any retry.

**Validation**
- *Baseline:* instrument the gateway call's duration and record its distribution over a
  representative period. **safe-on-production: yes** — this is instrumentation, not load.
- *Measurement:* after the change, confirm requests to a deliberately unresponsive endpoint
  fail at the configured bound rather than hanging. Test with a local stub.
  **safe-on-production: no** — use a test environment.
- *Falsifier:* if the gateway's high-percentile latency turns out to exceed the chosen bound
  under normal conditions, the bound is wrong, not the gateway.
- *Guard:* a test asserting the client is constructed with read and total timeouts set.

---

### PERF-003 — Connection demand can exceed the database's limit at maximum scale

| | |
|:--|:--|
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | infrastructure |
| **Location** | `app/db.py:22`, `gunicorn.conf.py:8`, `k8s/hpa.yaml:14-16`, `infra/rds.tf:41` |
| **Tags** | scalability-risk |

**Problem**
Pool size 10 × 4 workers × 20 maximum replicas is 800 potential connections against a database
configured for 100. The Celery worker deployment adds to this from the same configuration.

**Performance principle**
A bounded shared resource must accommodate the sum of all consumers at their maximum, not at
their current level.

**Evidence**
`app/db.py:22` sets `pool_size=10`. `gunicorn.conf.py:8` sets `workers=4`. `k8s/hpa.yaml:14-16`
sets `minReplicas: 2, maxReplicas: 20`. `infra/rds.tf:41` selects an instance class whose
`max_connections` is 100. `app/workers/celery_app.py:11` imports the same engine configuration.

Derivation, from those four files: 10 × 4 × 20 = 800, against a limit of 100. Exceeded from
approximately 3 replicas onward, before counting the worker deployment, migrations, or
monitoring connections.

**Impact**
Position: affects all paths. Frequency: on scale-up. Growth: linear in replica count. Blast
radius: system-wide — connection refusal surfaces as errors in code paths unrelated to
whatever caused the scale-up.

**Conditions**
Reachable at approximately 3 replicas, which is above the configured minimum of 2. Whether
this is a current problem depends on actual replica counts during peak, which is unknown. It
is certainly reachable within the configured autoscaling range.

**Recommendation**
Reconcile the arithmetic. Three levers, in order of preference: reduce pool size to fit the
maximum replica count; introduce a connection pooler between the application and the database;
or raise the database's connection limit — which is the least preferred, because PostgreSQL
backs each connection with a process and does not scale gracefully to high connection counts.

Give the Celery worker its own pool configuration rather than sharing the API's, so the two
can be sized for their different concurrency profiles.

**Trade-offs**
A smaller pool means requests may wait for a connection during bursts — which is preferable to
connection refusal, but only if acquisition wait time is bounded and observable, and currently
it is neither. A pooler in transaction mode restricts session-scoped features; the codebase
would need checking for prepared-statement usage before adopting one.

**Validation**
- *Baseline:* record connections currently in use per instance, and total connections at the
  database. `SELECT count(*) FROM pg_stat_activity;` **safe-on-production: yes**.
- *Measurement:* recompute the arithmetic after the change; confirm the maximum is below the
  limit with headroom for migrations, monitoring, and rolling deploys, during which old and
  new pods briefly coexist.
- *Falsifier:* if the service never scales beyond 2 replicas in practice, this is a latent
  configuration risk rather than an operational one — still worth fixing, but not at P1.
- *Guard:* an alert on database connection count as a fraction of the limit.

---

### PERF-004 — Password hashing blocks the event loop

| | |
|:--|:--|
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | concurrency |
| **Location** | `app/api/auth.py:47` |
| **Tags** | needs-measurement |

**Problem**
`POST /auth/login` is an `async def` handler that calls a synchronous password-hashing
function directly. Password hashing is deliberately CPU-expensive, and calling it on the event
loop blocks every other request being served by that worker for its duration.

**Performance principle**
A blocking operation on a single-threaded executor stalls all concurrent work on that
executor, including work unrelated to it.

**Evidence**
`app/api/auth.py:47` calls `bcrypt.checkpw(...)` inside an `async def` handler with no
executor offload. `app/api/auth.py:12` confirms the handler is async. The work factor is set
in `app/config.py:29`; deliberately expensive hashing is the intended behavior of the library.

*No runtime evidence:* there is no event-loop lag metric, and no measurement of how long
hashing takes with this configuration on this hardware.

**Impact**
Position: critical path. Frequency: once per login attempt. Growth: O(1) per call — the
concern is the stall, not growth. Blast radius: all requests concurrently handled by the same
worker process; with 4 workers, roughly a quarter of in-flight requests per login.

**Conditions**
Impact scales directly with login rate, which is unknown. At a low login rate this is a minor
tail-latency contributor; at a high one it becomes a throughput ceiling that no amount of
autoscaling on CPU will resolve cleanly. **Confidence is capped at `Medium` because the impact
depends on a workload figure that is not available** — the code reading itself is unambiguous.

**Recommendation**
Offload the hashing to a thread or process executor, and bound that executor. Offloading to an
unbounded pool would replace a stall with resource exhaustion.

**Trade-offs**
Offloading adds a small per-call overhead and one more bounded resource to size and monitor.
It does not reduce the CPU cost — it relocates it so that it does not block unrelated requests.

**Validation**
- *Baseline:* measure event-loop lag under concurrent login load in a test environment, and
  measure the hashing duration directly with the configured work factor.
  **safe-on-production: no** for the load portion; the lag metric itself is safe to add.
- *Expectation:* event-loop lag during logins falls substantially; total CPU consumed is
  unchanged. Do not expect login latency itself to improve — it should not.
- *Falsifier:* if login rate turns out to be very low, this is a `Low` severity item and
  should be reprioritized rather than fixed urgently.
- *Guard:* an event-loop lag metric with an alert threshold.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-005 | Medium | Medium | P2 | `app/cache.py:18` | Customer cache has no hit-rate instrumentation; its effectiveness is unmeasurable and its 300s TTL is unexplained |
| PERF-006 | Medium | Medium | P2 | `app/api/dashboard.py:22` | Exact `COUNT(*)` over `orders` on a dashboard route; cost grows with the table |
| PERF-007 | Medium | High | P2 | `app/services/checkout.py:44-83` | Transaction spans the payment-gateway call; lock duration is bounded by a third party. Shares a root cause with PERF-002 and should be fixed with it |
| PERF-008 | Medium | Medium | P2 | `k8s/hpa.yaml:22` | HPA scales on CPU only; the service is more likely to become pool-bound, in which case requests queue while CPU stays low |
| PERF-009 | Low | High | P3 | `k8s/redis-config.yaml:9` | Cache and Celery broker share one Redis instance with `noeviction`; cache pressure becomes write errors. Likely deliberate given the shared broker — separating the two uses is the cleaner fix |
| PERF-010 | Informational | High | P3 | `app/services/` | Service layer returns ORM instances, allowing lazy loads to trigger during serialization. Root cause enabling PERF-001; worth addressing structurally |

### Considered and not reported

- **Swapping the JSON serializer for a faster library.** Not recommended. This path makes
  multiple database round trips, and no profile exists to show serialization is a meaningful
  share of request time. Payload sizes are unknown. Recommending a dependency change on this
  basis would be speculation.
- **Caching `/orders` responses in Redis.** Not recommended. It would conceal PERF-001 rather
  than remove it, and the responses are per-user and likely to have a poor hit rate. Fix the
  query count first; revisit only if the endpoint remains slow afterward, with hit-rate data.
- **Adding an index on `orders.status`.** Not recommended. The column has three values, so the
  predicate is unselective and an index is unlikely to be chosen. It would add write cost to a
  table written on every checkout for no expected read benefit.
- **Converting the Celery worker to async.** Not recommended. The worker is I/O-bound but off
  the critical path; the constraint it contributes to is the shared pool, which PERF-003
  addresses directly and at far lower cost.

---

## 8. Prioritized action plan

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | — | — | Small | Add request-duration and event-loop-lag metrics. Nothing below can be validated without them |
| 2 | PERF-002 | P0 | Small | `quick-win`; removes an unbounded worst case with no growth required to trigger it |
| 3 | PERF-007 | P2 | Small | Same code path as PERF-002; fix together |
| 4 | PERF-001 | P0 | Medium | Largest structural improvement on the primary read path |
| 5 | PERF-003 | P1 | Small | Configuration change; prevents a scale-up outage |
| 6 | PERF-004 | P1 | Small | Bounded executor offload |
| 7 | PERF-005/006/008 | P2 | Varies | After measurement exists, these can be sized properly |

Note the sequencing: PERF-007 is a P2 but is done third because it shares a code path with a
P0. PERF-002 is a P0 done before PERF-001 because it is smaller and removes an unbounded
failure mode. **Priority measures impact; sequencing may account for effort.** Neither
finding's priority changed.

**If only one thing is done:** add request-duration instrumentation. Every other item on this
list is currently unverifiable, and that is the more expensive problem.

---

## 9. Validation plan

### Instrumentation gaps to close first

The service is uninstrumented for latency. Before any optimization:

1. Request duration per route, as percentiles — not averages. Averages will hide exactly the
   tail behavior PERF-002 and PERF-004 produce.
2. Event-loop lag per worker.
3. Connection pool in-use count and acquisition wait time. The wait-time metric is what
   distinguishes an undersized pool from a slow query, and PERF-003's fix cannot be sized
   without it.
4. `pg_stat_statements` enabled on the database. **safe-on-production: yes.** This alone would
   likely confirm or refute PERF-001 and PERF-006 within minutes, and would raise several
   findings in this review from `High` to `Confirmed`.

Per-finding validation is given inline in §7.

---

## 10. Notes on this review

- No runtime metric in this report was measured or estimated. The two arithmetic results —
  51 queries per default page, and 800 potential connections — are derivations from cited
  configuration files, labelled as such at the point of use.
- No finding reached `Confirmed` confidence, because the repository contains no runtime
  evidence. Four findings would likely reach it with `pg_stat_statements` alone.
- Four plausible optimizations were considered and deliberately not recommended; they are
  listed with reasons in §7 so the reader can disagree with the reasoning rather than wonder
  whether they were missed.
