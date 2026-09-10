# Task queues: Celery, Sidekiq, BullMQ/Bull, RQ, Dramatiq, Hangfire, Temporal, and Asynq compared

Load after `distributed/retries-and-backpressure.md` and `application/connection-pools.md`.

This file contains **only what those files do not give you**: retry amplification,
backpressure, the general queue/consumer reasoning (throughput versus lag, partition-bound
parallelism, poison messages, batch/prefetch trade-offs, ordering constraints), and
connection-pool reasoning are covered there and are not repeated here.

**This is a deliberately comparative file, not a single-library one** (see
`docs/roadmap.md`). These libraries differ in backing store, concurrency model, and default
retry behavior in ways that directly change what a review should check. **Confirm which
library is actually deployed before applying a row from any table below.**

---

## 1. Detection signals

| Library | Ecosystem | Backing store |
|:--|:--|:--|
| **Celery** | Python | RabbitMQ or Redis (broker), configurable |
| **Sidekiq** | Ruby | Redis |
| **BullMQ** / **Bull** (BullMQ's predecessor) | Node.js | Redis |
| **RQ** / **Django-RQ** | Python | Redis |
| **Dramatiq** | Python | RabbitMQ or Redis, configurable |
| **Hangfire** | .NET | **SQL** (SQL Server, PostgreSQL, MySQL) by default — see §2 |
| **Temporal** | Multi-language SDKs (Go, Java, Python, .NET, TypeScript) | Its own server + datastore — **not the same kind of system**, see §2 |
| **Asynq** | Go | Redis |

Other signals: `celeryconfig.py`/`celery.py` with an `app = Celery(...)` declaration,
`sidekiq.yml`, a `Queue`/`Worker` instantiation from `bullmq`, `rq worker` invocations,
`dramatiq` CLI entry points, Hangfire's `GlobalConfiguration.Configuration.Use...Storage(...)`
call (names the actual backing database), Temporal's `worker.New(...)`/`@workflow.defn`
decorators.

---

## 2. What differs from the generic queue baseline, library by library

### The backing store determines the throughput ceiling and the failure mode, and Hangfire is the outlier

Most of these libraries sit on Redis (Sidekiq, BullMQ/Bull, RQ, Asynq) or a message broker
(Celery and Dramatiq, defaulting to RabbitMQ or Redis) — a datastore chosen and typically
operated specifically for queue workloads, usually isolated from the application's primary
OLTP database.

**Hangfire is different by design: its default storage is a relational database**, commonly
the same SQL Server/PostgreSQL/MySQL instance the application already uses for everything
else. This has a direct, checkable consequence the generic queue guidance does not name:
**queue polling and job-state writes compete for the same connection pool, locks, and I/O as
the application's primary business queries**, on a shared resource that was not necessarily
sized with queue load in mind. A Hangfire deployment pointed at the application's primary
database, under meaningful job volume, is a legitimate shared-resource finding in its own
right — check whether Hangfire uses a dedicated database/schema or genuinely shares capacity
with production OLTP traffic.

### Dequeue mechanism: blocking pop versus polling, and it sets a latency floor for one of these

Redis-backed libraries generally use a blocking-pop-style operation (or Redis Streams
consumer groups, for BullMQ) — the worker is notified promptly when work arrives, with no
busy-waiting. Hangfire's SQL-backed model has historically relied on **polling the storage
table at a configured interval**; where no lower-latency notification mechanism is
configured, this polling interval is a real, checkable latency floor on job pickup that the
Redis-backed libraries do not have in the same form.

### Default retry behavior varies enormously across these libraries, and the defaults are frequently not what people assume

This is the single highest-value comparison in this file, because the generic retry
guidance (`distributed/retries-and-backpressure.md` §2) assumes retry configuration is
visible; here, the *default* — what happens with no configuration at all — differs sharply
enough to be its own finding:

| Library | Default retry behavior |
|:--|:--|
| **Sidekiq** | Retries automatically, with exponential backoff, **up to 25 times over roughly 21 days** — far more persistent than most people assume until they see a job from three weeks ago fire |
| **Celery** | **No automatic retry** unless a task explicitly sets `autoretry_for`/`retry_kwargs`, or calls `self.retry()` |
| **BullMQ** | `attempts` defaults to **1 — no retry at all** unless explicitly configured higher, with backoff also opt-in |
| **RQ** | No automatic retry by default; retry behavior is opt-in via an explicit `Retry` configuration |
| **Dramatiq** | Retries by default, with a configurable maximum (finite, unlike Sidekiq's very long default window) |
| **Hangfire** | Retries automatically with a default attempt count and delay schedule, configurable per job |
| **Asynq** | Retries by default, with a configurable maximum attempts and backoff |

A code review that assumes "the queue library handles retries reasonably" without checking
which of these two very different defaults applies is checking nothing. A Sidekiq queue with
a failing job silently retrying for three weeks, or a BullMQ/RQ queue silently dropping
failures because retry was never enabled, are both real, checkable, opposite-direction
findings that follow directly from knowing the deployed library's default.

### Celery's worker concurrency model is itself a configuration choice, unlike most of the others

Celery workers select a **pool implementation** at startup: `prefork` (separate OS
processes, true parallelism, the default and highest per-worker memory cost), `eventlet` or
`gevent` (greenlet-based, high I/O-bound concurrency per process, still subject to the same
GIL-for-CPU-work constraint `technology/python.md` describes), or `solo`/`threads`. This is a
directly checkable, Celery-specific configuration decision with a real isolation and
memory-versus-concurrency trade-off — most of the other libraries here (Sidekiq, BullMQ, RQ,
Asynq) have a single, simpler concurrency model with no equivalent pool choice to check.

### Temporal is not a task queue in the sense the rest of this file means — treat it as a different category

Every other library on this page executes a fire-and-forget (or retry-and-forget) job.
**Temporal is a durable-execution/workflow orchestration engine**: a "task queue" in
Temporal's own terminology is the routing mechanism connecting workflow and activity tasks
to workers, not a simple job queue. Temporal workflows are long-running, checkpointed state
machines — a worker reconstructs a workflow's state by **replaying its event history**, and
that history's size directly affects replay cost and worker memory. The performance
questions that matter for a Temporal-based repository are correspondingly different: workflow
history growth and replay cost, activity retry policy as first-class *workflow-definition*
configuration (not worker-level defaults), and task-queue name assignment as a worker-pool
routing decision. **Applying this file's comparison table or the generic queue-depth/lag
framing to a Temporal workflow queue is the wrong mental model** — flag that distinction
explicitly rather than forcing Temporal into the same shape as the other seven libraries.

### Redis-backed durability depends entirely on Redis's own configuration — a real cross-reference, not a separate concern

Sidekiq, BullMQ/Bull, RQ, and Asynq all depend on Redis's own persistence configuration
(RDB/AOF, per `technology/redis.md`) for job durability. **A Redis instance run as a
pure cache — no persistence, or an eviction policy that can reclaim queue keys under memory
pressure — can lose queued jobs on restart or under memory pressure**, silently, with no
error visible to the producer that enqueued them. This is not a fact about the queue library
at all; it is `technology/redis.md`'s persistence guidance applying here with a sharper
consequence (lost work, not just a cache miss), and worth checking explicitly for any
Redis-backed queue in this list.

### Payload size and serialization: the same "pointer, not payload" principle, with library-specific serializer defaults

Passing large arguments directly into a job (serialized into Redis, the broker, or the SQL
table) bloats the backing store and every read/write against it — the queue-specific form of
"reference, don't inline" data-access guidance. What differs by library: Sidekiq and Celery
both default to **JSON serialization**, which constrains argument types to what JSON
represents (no arbitrary objects) and is worth checking when job code assumes richer types
survive the round trip; Celery's broker historically also supported `pickle`, which carries a
deserialization security risk if not explicitly restricted to trusted producers.

---

## 3. Diagnostics

| Library | Command / source | What it shows | Production safety |
|:--|:--|:--|:--|
| Sidekiq | Web UI (`sidekiq/web`) or `Sidekiq::Stats` | Queue depth, retry set size, dead set size, processed/failed counts | **safe-on-production** |
| Celery | `celery inspect active` / `celery inspect reserved` | Currently executing and claimed-but-not-started tasks per worker | **safe-on-production** |
| Celery | Flower (a monitoring UI) | Task history, worker status, queue depth | **safe-on-production** to run alongside the workers |
| BullMQ | `Queue.getJobCounts()` / Bull Board (UI) | Waiting/active/completed/failed/delayed counts per queue | **safe-on-production** |
| RQ | `rq info` / the RQ dashboard | Queue depth and worker status | **safe-on-production** |
| Hangfire | Built-in dashboard (`/hangfire`) | Job state counts, retry history, and — critically — recurring/queued job storage load | **safe-on-production**, but confirm dashboard access is authenticated |
| Temporal | Web UI / `tctl`/`temporal` CLI workflow list and describe | Workflow state, history size, pending activities | **safe-on-production** |
| Asynq | `asynqmon` (monitoring UI) or `asynq stats` (CLI) | Queue sizes, retry/dead counts | **safe-on-production** |

For any of the Redis-backed libraries, Redis's own diagnostics (`technology/redis.md` §3) are
equally relevant — the queue library's own dashboard shows job state, not whether Redis
itself is the bottleneck.

---

## 4. Common failure modes and their symptoms

| Symptom | Library-specific cause to check first |
|:--|:--|
| A failing job keeps firing for weeks with no one noticing | Sidekiq's default retry window (25 attempts over ~21 days) — often assumed shorter than it is |
| A failing job silently disappears with no retry at all | Celery/BullMQ/RQ's opt-in retry — nothing configured means nothing retried |
| Job pickup latency has a noticeable floor even under light load | Hangfire's polling interval, with no faster notification path configured |
| Queue processing directly slows down application database queries, or vice versa | Hangfire sharing the application's primary OLTP database and connection pool |
| CPU-bound Celery tasks don't parallelize despite raising concurrency | Wrong pool choice (`eventlet`/`gevent`/`threads` instead of `prefork`) for CPU-bound work |
| Queued jobs vanish after a Redis restart or memory-pressure event | Redis running without persistence, or with an eviction policy that can reclaim queue keys |
| A Temporal workflow's memory/CPU use grows over its lifetime | Event-history size growing with workflow duration/step count, driving up replay cost |
| Job arguments fail to serialize, or deserialize into the wrong shape | JSON-only serialization (Sidekiq/Celery default) given a non-JSON-representable argument |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Retry count / backoff (per library, see §2 table) | Resilience to transient failure versus how long a genuinely broken job keeps consuming worker capacity |
| Hangfire storage: dedicated database versus shared with the application | Operational simplicity (one less database to run) versus queue/OLTP resource contention |
| Celery pool type | True CPU parallelism (`prefork`) versus lightweight I/O-bound concurrency (`eventlet`/`gevent`) versus memory footprint |
| Redis persistence (for every Redis-backed library here) | Job durability across restarts versus the write-amplification/latency cost of AOF/RDB |
| Batch/prefetch size (library-specific naming) | Worker throughput versus redelivery risk and memory, per the generic file's framing |
| Hangfire polling interval / notification mechanism | Job-pickup latency versus polling load on the storage database |
| Temporal task-queue (routing) assignment | Worker-pool isolation and scaling granularity versus operational complexity of managing more queues |

---

## 6. Version differences worth knowing

- **Bull was succeeded by BullMQ**, a substantial rewrite (different internal data
  structures, added features like flow/dependency support); code or documentation referring
  to `bull` rather than `bullmq` may be on the older, now largely superseded library.
- **Celery's broker recommendation shifted over time**; older deployments commonly used
  RabbitMQ by default, while Redis became a common lighter-weight choice — confirm which is
  actually configured rather than assuming either.
- **Hangfire's notification mechanisms for faster-than-polling job pickup** (e.g. SQL
  Server's `SqlDependency`-based signaling) are storage-provider-specific and not uniformly
  available across all supported databases — confirm what the specific storage provider in
  use actually supports before assuming near-real-time pickup.
- **Temporal's SDKs and server evolve independently** across languages; a workflow-history
  or replay-cost claim should be checked against the specific SDK version in use.

**Confirm version-specific defaults and features against the current documentation for the
deployed library version.**

---

## 7. What this file does NOT cover

- Retry amplification, backpressure, general queue/consumer reasoning, and fan-out — see
  `distributed/retries-and-backpressure.md`.
- Connection-pool sizing for the worker processes themselves — see
  `application/connection-pools.md`.
- Redis's own performance characteristics and persistence mechanics in depth — see
  `technology/redis.md`.
- RabbitMQ's own performance characteristics, where used as Celery's or Dramatiq's broker —
  see `technology/rabbitmq.md`.
- Temporal's workflow-definition language, determinism constraints, and versioning API in
  depth — named above only as a reason to treat it as a different category.
- Each library's web dashboard authentication and access control.
- Specific numeric threshold recommendations beyond the documented defaults and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
