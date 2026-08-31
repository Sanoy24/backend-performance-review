# Example review — Node.js + MongoDB + Redis

> **This is a synthetic example.** The repository does not exist. It illustrates the report
> structure, the scoring rubric, and — importantly — the restraint the methodology requires.
>
> **No runtime metric in this document was measured or invented.** Where the review needs a
> number it does not have, it says so. Where an arithmetic result is derived from cited
> configuration, the derivation is shown at the point of use. That is the point of the example
> as much as the findings are.
>
> This file lives in `docs/` and is never loaded as a skill reference, so its content cannot
> leak into a real review as a few-shot pattern.

---

# Performance Review: `feed-api`

**Date:** 2026-08-31
**Mode:** Full review
**Reviewed by:** Automated performance review, `backend-performance-review` v0.2.0

---

## 1. Executive summary

### Overall assessment

`feed-api` is an Express service backed by MongoDB (via Mongoose) with Redis used both as a
response cache and as the backing store for a BullMQ job queue. The application logic is
straightforward, but the primary feed-listing path issues one query per returned post to resolve
its author, with no enforced maximum page size, and a synchronous password-hashing call blocks
the single Node.js event loop for every concurrently connected client on that process — not just
the request that triggered it.

The service is **uninstrumented for latency**: there is no APM agent, no per-route duration
metric, no event-loop-lag metric, and no committed MongoDB query profiler output. Consequently no
finding in this review reaches `Confirmed` confidence. Closing that gap is sequenced first in the
action plan, because none of the fixes proposed here can be validated without it.

### Most important findings

- **PERF-001 (P0)** — `GET /feed` resolves each post's author with a separate query inside a
  loop, and the page size is caller-controlled with no enforced maximum.
- **PERF-002 (P0, quick-win)** — `POST /auth/signup` hashes the password synchronously on the
  request handler; because Node.js runs one JavaScript thread per process, this stalls every
  other request the process is concurrently handling, not only the signup itself.
- **PERF-003 (P1)** — At the autoscaler's configured maximum, the service can request more
  MongoDB connections than the cluster's tier permits.
- **PERF-004 (P1)** — Cache invalidation uses `KEYS` against the same Redis instance that backs
  the BullMQ job queue; Redis executes commands on a single thread, so a large keyspace scan
  delays job dequeuing for the whole queue, not just the cache operation that triggered it.

### Highest-risk bottlenecks

The single Redis instance is the shared resource most likely to fail first under load. PERF-004
shows it directly: a cache-invalidation scan and the job queue's own command traffic compete for
the same single-threaded command executor. This is the same failure shape `distributed/caching.md`
and `technology/redis.md` describe generically, made concrete by this repository's specific
decision to put the cache and the queue on one instance.

Separately, PERF-002 is a *current* risk rather than a scaling one: it requires only a normal rate
of signups, not growth, to produce a stall large enough to be noticed by unrelated users of the
same worker process.

### Major unknowns

| Unknown | Would change |
|:--|:--|
| Request rate on `/feed` at peak | Whether PERF-001 is a current bottleneck or a scalability risk |
| Average and maximum `posts` document size, and typical author fan-out per page | The severity of PERF-001's per-row query cost and payload size |
| Signup rate at peak | The severity of PERF-002; the code-level defect is certain regardless |
| Actual Redis keyspace size at cache-invalidation time | Whether PERF-004's `KEYS` call is a minor pause or a multi-second stall |
| Actual pod replica count during normal operation | Whether PERF-003 is reachable today or only at maximum scale |

The user was asked these questions and did not have the data to hand. The review proceeded under
the assumptions stated in each finding's `Conditions` field, with workload-dependent confidence
capped at `Medium` where applicable.

---

## 2. Scope and method

**Reviewed:** `src/routes/`, `src/models/`, `src/services/`, `src/queues/`, `src/cache.js`,
`docker-compose.yml`, `k8s/`, `ecosystem.config.js`.

**Not reviewed:** the React front end in `web/`; the Terraform in `infra/` beyond reading the
MongoDB Atlas tier's connection limit; test fixtures. None are on a request path.

**Evidence available:** **Uninstrumented.** No APM agent, no Prometheus client, no OpenTelemetry,
no committed MongoDB `explain()` output, no benchmarks, no load-test scripts. Morgan access
logging exists but does not record downstream query timing. This caps confidence for the whole
review.

**Ranking method:** Structural signals only. No runtime data was available to rank paths by
measured cost.

**Reference depth:** Node.js, MongoDB, and Redis are all at `deep` tier. BullMQ is detected as a
task queue at `generic` tier — broker-specific analysis is limited to the retry, backpressure, and
shared-resource reasoning that applies to any queue.

---

## 3. Architecture overview

A single Express service, deployed to Kubernetes, running under `pm2` in cluster mode.

| Component | Technology | Version | Tier | Role |
|:--|:--|:--|:--|:--|
| API | Express, `pm2 -i` cluster mode | — | conceptual | HTTP entry point, 11 routes |
| Runtime | Node.js | 20 | **deep** | One event loop per cluster worker process |
| ODM | Mongoose | 7 | conceptual | Schema, query construction |
| Primary datastore | MongoDB | 6 (Atlas M10) | **deep** | Posts, users, follows |
| Cache | Redis | 7 | **deep** | Response caching |
| Queue | BullMQ, same Redis instance | — | generic | Notification fan-out on new posts |
| Orchestration | Kubernetes, HPA 2–12 replicas | — | conceptual | CPU-target autoscaling |

**Shared resources:** the MongoDB connection pool (`maxPoolSize` set once in `src/db.js`, used by
every `pm2` cluster worker in the pod); the single Redis instance (cache and BullMQ broker share
one instance and one logical database); one event loop per `pm2` cluster worker process.

`pm2 -i` cluster mode runs one Node.js process — and therefore one event loop — per CPU core
requested by the pod. This is the mechanism, not autoscaling, that determines how many
concurrently-open connections a single blocked event loop actually affects: see PERF-002.

---

## 4. Workload model

**Known** — cited

- `maxPoolSize: 20` in `src/db.js:14`; `pm2 -i max` with a pod CPU request of `2` in
  `k8s/deployment.yaml:22` (two cluster workers per pod, each opening its own pool); HPA
  `minReplicas: 2, maxReplicas: 12` (`k8s/hpa.yaml:15-17`).
- MongoDB Atlas M10 tier's documented connection limit is 1500 (`infra/atlas.tf:9`, a comment
  citing the tier's published limit alongside the cluster resource).
- `posts` is written on every post creation (`src/routes/posts.js:38`) and has no TTL index,
  archival job, or capped-collection configuration in `src/models/Post.js`.
- `GET /feed` has a default page size of 25 and no maximum (`src/routes/feed.js:12`).
- HPA scales on CPU utilization only (`k8s/hpa.yaml:24`).
- Redis is configured with `maxmemory-policy noeviction` (`k8s/redis-config.yaml:6`).

**Assumed** — stated, unverified

- `posts` grows monotonically. Basis: written per creation, no TTL index, no archival job, no
  capped collection. Affects PERF-001.
- Feed reads substantially outnumber post creations, as is typical for this application shape.
  Affects PERF-001's frequency reasoning; not verified against actual traffic.

**Unknown** — see §1.

---

## 5. Critical path analysis

Ranked by structural signals; no runtime data available.

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | `GET /feed` | yes | 1 + 1 per post | **no** | no | Unbounded page size, per-post author lookup |
| 2 | `POST /auth/signup` | yes | 1 | yes | no | Synchronous password hash on the event loop |
| 3 | `POST /posts` | yes | 1 write + 1 BullMQ enqueue | yes | no | Enqueue shares Redis with the cache |
| 4 | `DELETE /cache/posts/:authorId` (internal, called on post edit) | yes | `KEYS` scan + N deletes | **no** | no | Blocks Redis's single command thread for the scan duration |
| 5 | queue worker: `fanOutNotification` | no | 1 read + up to N writes | no | no | Notification count is follower count, uncapped |

**Amplification points:** `/feed` — query count is proportional to page size, and page size is
caller-controlled and uncapped. The `KEYS`-based cache invalidation — cost is proportional to
total Redis keyspace size, not to the number of keys actually being invalidated.

**Paths deliberately not analyzed in depth:** the remaining routes are single-document reads or
writes on indexed fields (`_id`, `username`), with bounded responses. Admin routes are internal
and low-frequency.

---

## 6. Layer analysis

### 6.1 Application

Route handlers call service-layer functions directly; there is no repository abstraction, so
query shape is visible at the call site rather than hidden behind an ORM's lazy-loading default —
which makes PERF-001's per-post loop a plainly visible piece of application code, not an implicit
side effect the way an ORM's lazy relationship access can be (contrast `application/data-access.md`
§2's ORM-hydration framing with the explicit loop cited in PERF-001's evidence).

### 6.2 API

`GET /feed` accepts a caller-supplied `limit` with a default of 25 and no enforced maximum
(`src/routes/feed.js:12`). Every other list route in the service caps at 100
(`src/routes/posts.js:8`, `src/routes/comments.js:11`), so this appears to be an oversight rather
than a deliberate choice.

### 6.3 Data access and datastore

Covered by PERF-001. Positively: `Post.authorId` and `Follow.followerId` both have supporting
indexes declared in their schemas (`src/models/Post.js:22`, `src/models/Follow.js:14`), and every
query in `src/routes/` and `src/services/` is built through Mongoose's query builder rather than
string-concatenated — no injection-shaped construction found.

### 6.4 Cache

Redis caches individual post documents with a 120-second TTL (`src/cache.js:9`). There is no
hit-rate instrumentation, so the cache's effectiveness is unmeasurable.

`maxmemory-policy` is `noeviction` (`k8s/redis-config.yaml:6`). For an instance also used as the
BullMQ broker, `noeviction` is very likely deliberate — evicting queued-job state under memory
pressure would silently drop notifications rather than merely produce a cache miss. The correct
fix, if memory pressure ever becomes real, is almost certainly to separate the two uses rather
than change the eviction policy — reported here as an observation to size, not a one-line change.

### 6.5 Distributed communication

One outbound dependency: a webhook notifying a third-party analytics endpoint on post creation
(`src/services/analytics.js:11`). It has a connect timeout but no read timeout, structurally
identical to the pattern `distributed/timeouts-and-deadlines.md` names as the most commonly
missing half of a timeout configuration — noted in the remaining-findings table rather than
written up in full, since it is off the critical path (fired via `setImmediate`, not awaited by
the request handler).

### 6.6 Infrastructure

Container memory limit is 512Mi per pod (`k8s/deployment.yaml:24`) against two `pm2` cluster
workers. Node.js's V8 heap does not default its ceiling from the container's cgroup limit on
versions before the runtime reads it automatically — `technology/node.md` names this as a
checkable default worth confirming, but no `--max-old-space-size` override or version-specific
confirmation was found in `ecosystem.config.js`, so this is recorded as an unknown rather than a
finding: the exact behavior depends on the installed Node.js minor version, which was not pinned
precisely enough in `Dockerfile:2` (`node:20-slim`, a moving tag) to state confidently.

The HPA scales on CPU only. Given PERF-004's finding, this service may become Redis-command-bound
before it becomes CPU-bound, in which case requests queue while pod CPU stays low and the
autoscaler does not react — the same shape as a pool-bound service under CPU-only autoscaling
generally (`infrastructure/resources.md` §2). Folded into PERF-004 rather than filed separately,
since fixing the `KEYS` usage removes the mechanism rather than the autoscaler being the root cause.

### 6.7 Observability

No request-duration metric, no event-loop-lag metric, no Redis command-latency metric, and no
MongoDB `explain()` output committed anywhere. This is the reason nothing in this review is
`Confirmed`, and it means none of the recommendations below can currently be validated. Closing
it is sequenced first in the action plan.

---

## 7. Findings

### PERF-001 — `GET /feed` issues one query per post, with no bound on page size

| | |
|:--|:--|
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | data-access |
| **Location** | `src/routes/feed.js:12-27`, `src/services/feedService.js:18-31` |
| **Tags** | scalability-risk |

**Problem**
The handler fetches a page of posts, then for each post calls `User.findById(post.authorId)`
inside a `for` loop to attach author details before responding. The page size is caller-controlled
with no enforced maximum.

**Performance principle**
Work repeated per item, multiplied by an unbounded item count. Query count scales with result
size, and result size scales with data.

**Evidence**
`src/services/feedService.js:18-31` — a `for (const post of posts)` loop calling
`await User.findById(post.authorId)` on each iteration, rather than collecting `authorId`s and
issuing one `User.find({ _id: { $in: authorIds } })`. `src/routes/feed.js:12` reads `limit` from
the query string with `Number(req.query.limit) || 25` and no upper clamp, unlike the other list
routes in the same directory.

*No runtime evidence:* there is no query log, no APM span count, and no query-count test in this
repository. The query count is derived from the loop structure in the code, not observed.

**Impact**
Position: critical path — a client-facing GET, almost certainly the highest-traffic read in a
feed-shaped application. Frequency: one query per returned post, per request. Growth: O(n) in
page size, and the underlying `posts` collection grows monotonically per §4. Blast radius: each
query holds a connection from the shared MongoDB pool for its duration, and that pool is shared
across every `pm2` cluster worker in the pod.

Derivation: at the default page size the request issues 26 queries; a caller passing `limit=1000`
issues 1001. Both figures follow from the code, not from measurement.

**Conditions**
Matters whenever `/feed` is called with a page size above a handful, and increasingly as `posts`
grows. Assumption: `posts` grows monotonically — no TTL index, archival job, or capped-collection
configuration exists in `src/models/Post.js`. Request rate on this endpoint is unknown, which is
why confidence is not raised further.

**Recommendation**
Two changes, addressing cause and bound separately:

1. Replace the per-post `findById` loop with a single batched lookup: collect the page's
   `authorId`s, issue one `User.find({ _id: { $in: authorIds } })`, and map results back onto
   posts in memory. This removes the repetition rather than concealing it.
2. Enforce a maximum page size on this route, consistent with the `100`-item cap used on the
   other list routes in `src/routes/`.

**Trade-offs**
The batched form transfers the same author fields as before but in one round trip instead of
`n`; if the author documents are large, this trades many small transfers for one larger one,
which is close to always a net win for round-trip-dominated internal traffic
(`principles/resources.md` §5), but is worth stating explicitly rather than assuming.

**Validation**
- *Baseline:* count MongoDB operations issued by `GET /feed?limit=25` against a database with
  production-shaped data. Method: enable Mongoose's `debug` mode in a staging environment, or add
  a temporary operation counter to the test suite. **safe-on-production: no** — use staging.
- *Expectation:* operation count falls from 26 to 2 and becomes independent of page size. This is
  derived from the code and can be asserted. The resulting latency change **cannot** be predicted
  from available evidence — it depends on how much of this endpoint's time is spent in the
  database, which is unmeasured.
- *Falsifier:* if operation count drops but endpoint latency does not change materially, the path
  was not query-bound and this finding's impact was overstated — reprioritize accordingly.
- *Guard:* a test asserting the operation count for this route. Latency assertions are flaky;
  operation-count assertions are not.

---

### PERF-002 — Signup handler hashes the password synchronously, blocking the event loop for every concurrent request on that process

| | |
|:--|:--|
| **Severity** | Critical |
| **Confidence** | High |
| **Priority** | P0 |
| **Category** | concurrency |
| **Location** | `src/routes/auth.js:22` |
| **Tags** | quick-win |

**Problem**
`POST /auth/signup` calls `bcrypt.hashSync(password, 12)` directly in the request handler. Node.js
runs one JavaScript thread per process; a synchronous, CPU-expensive call on that thread stalls
every other request the same process is concurrently handling — not only the signup request
itself — for the duration of the hash.

**Performance principle**
A blocking operation on a single-threaded executor stalls all concurrent work on that executor,
including work unrelated to it. This is the sharpest form the principle takes in Node.js
specifically: unlike a thread-per-request or worker-pool model where a blocked unit of concurrency
degrades only its own slice, one Node.js process has exactly one JavaScript thread, so every
in-flight request on that process — every open WebSocket, every pending route handler — pauses for
the same duration (`technology/node.md` §2, "the event loop has phases").

**Evidence**
`src/routes/auth.js:22` calls `bcrypt.hashSync(...)` inside an `async` route handler with no
offload to `bcrypt.hash`'s async form or a worker thread. `src/routes/auth.js:8` confirms the
handler is registered as a normal Express async handler running on the main thread. The cost
factor (`12`) is set in `src/config.js:19`; deliberately expensive hashing is bcrypt's intended
behavior.

*No runtime evidence:* there is no event-loop-lag metric, and no measurement of how long hashing
takes with this cost factor on this hardware.

**Impact**
Position: critical path. Frequency: once per signup attempt. Growth: O(1) per call — the concern
is the stall, not growth. Blast radius: every request concurrently in flight on the same `pm2`
cluster worker process, which under this deployment's two-workers-per-pod configuration
(`k8s/deployment.yaml:22`) is potentially half of a pod's in-flight traffic per signup.

**Conditions**
Impact scales directly with signup rate, which is unknown. At a low signup rate this is a minor,
intermittent tail-latency contributor; at a higher one it becomes a periodic, process-wide stall
that no amount of CPU-based autoscaling resolves cleanly, since the stall is a single-threaded
serialization point, not aggregate CPU exhaustion. **Confidence is not capped here** — unlike a
workload-dependent severity question, the code-level defect (a sync call on the sole JS thread)
is unambiguous regardless of signup rate; only the *frequency* of user-visible impact depends on
the unknown rate.

**Recommendation**
Use `bcrypt.hash` (the async, libuv-threadpool-backed form) instead of `bcrypt.hashSync`. This
moves the CPU-bound work off the JavaScript thread onto libuv's worker pool, which Node.js already
maintains for exactly this class of operation — see `technology/node.md` §2, "the libuv thread
pool is small, bounded, and used for more than you'd expect." Do not increase the thread pool size
reflexively; confirm what else shares it first (DNS lookups, some crypto and zlib operations, and
file I/O all use the same pool by default).

**Trade-offs**
None material for this specific change — `bcrypt.hash` and `bcrypt.hashSync` differ only in
whether the work runs on the main thread or the libuv pool; the cost factor and resulting hash are
identical. The libuv pool itself is a shared, bounded resource, so a very high concurrent signup
rate could still produce queueing there — a materially smaller and better-isolated problem than
stalling the main thread, but worth noting rather than treating the fix as removing all
concurrency risk.

**Validation**
- *Baseline:* measure event-loop lag under concurrent signup load in a test environment, and
  measure hashing duration directly with the configured cost factor. **safe-on-production: no**
  for the load portion; an event-loop-lag metric itself is safe to add.
- *Expectation:* event-loop lag during signups falls to near zero; total CPU consumed by hashing
  is unchanged — the fix relocates the work, it does not reduce it.
- *Falsifier:* if signup rate turns out to be very low in practice, this is still a correct fix
  but a lower-urgency one — reprioritize using real rate data once it exists.
- *Guard:* an event-loop-lag metric with an alert threshold, and a lint rule or code-review check
  against synchronous `bcrypt`/`crypto` calls in request handlers.

---

### PERF-003 — Connection demand can exceed the MongoDB cluster's connection limit at maximum scale

| | |
|:--|:--|
| **Severity** | High |
| **Confidence** | High |
| **Priority** | P1 |
| **Category** | infrastructure |
| **Location** | `src/db.js:14`, `k8s/deployment.yaml:22`, `k8s/hpa.yaml:15-17`, `infra/atlas.tf:9` |
| **Tags** | scalability-risk |

**Problem**
`maxPoolSize` 20 × 2 `pm2` cluster workers per pod × 12 maximum replicas is 480 potential
connections against an Atlas M10 tier documented at 1500. This specific arithmetic does not
exceed the limit — but the queue worker process (§ below) adds its own pool on top of it from the
same configuration module, and the margin is worth stating precisely rather than assumed safe.

**Performance principle**
A bounded shared resource must accommodate the sum of all consumers at their maximum, not at
their current level.

**Evidence**
`src/db.js:14` sets `maxPoolSize: 20`. `k8s/deployment.yaml:22` requests `cpu: 2`, and
`ecosystem.config.js:4` sets `instances: 'max'`, so `pm2` starts one worker per requested core —
two per pod. `k8s/hpa.yaml:15-17` sets `minReplicas: 2, maxReplicas: 12`.
`src/queues/worker.js:6` imports the same `src/db.js` connection module for its own process.
`infra/atlas.tf:9`'s comment cites the M10 tier's documented connection limit as 1500.

Derivation, from those files: API pods alone reach 20 × 2 × 12 = 480 at maximum scale. The queue
worker deployment (`k8s/queue-deployment.yaml:11`, fixed at 3 replicas, not autoscaled) adds
20 × 3 = 60 more, for a combined maximum of 540 — under the tier's 1500 limit with headroom, but
closer than the API-only figure alone suggests, and the margin narrows further with any future
increase to `maxPoolSize`, replica count, or a second consumer added to the same configuration
module.

**Impact**
Position: affects all paths. Frequency: on scale-up. Growth: linear in replica count. Blast
radius: system-wide if ever exceeded — connection refusal surfaces as errors in code paths
unrelated to whatever caused the scale-up.

**Conditions**
Not currently exceeded per the arithmetic above, so this is reported as a scalability risk with
a quantified margin rather than a current problem. Whether the margin holds depends on future
`maxPoolSize`, replica count, or worker-count changes, none of which are currently gated by any
check against the Atlas tier's limit.

**Recommendation**
Add an explicit, tested assertion (even a simple script run in CI, mirroring
`scripts/check_repo_invariants.py`'s own "arithmetic invariant" pattern in this project) that
`maxPoolSize × workers-per-pod × maxReplicas` (summed across every deployment sharing the
connection module) stays under the configured Atlas tier's limit with a stated safety margin.
This converts a fact that currently has to be manually re-derived on every configuration change
into a checked one.

**Trade-offs**
A stricter automated check adds a small amount of CI complexity and requires keeping the Atlas
tier's limit in sync in one place if the tier is ever changed.

**Validation**
- *Baseline:* `db.serverStatus().connections` against the Atlas cluster during normal operation.
  **safe-on-production: yes** — a standard read-only diagnostic command.
- *Measurement:* recompute the arithmetic whenever `maxPoolSize`, replica counts, or worker counts
  change; confirm the maximum stays below the limit with headroom for Atlas's own overhead
  connections and any monitoring agent.
- *Falsifier:* if the service never scales beyond a few replicas in practice, this is a latent
  configuration risk rather than an operational one — still worth the automated check, but not at
  elevated urgency.
- *Guard:* the CI arithmetic check described above.

---

### PERF-004 — Cache invalidation uses `KEYS` against the Redis instance that also backs the job queue

| | |
|:--|:--|
| **Severity** | High |
| **Confidence** | Medium |
| **Priority** | P1 |
| **Category** | io |
| **Location** | `src/cache.js:24-31` |
| **Tags** | needs-measurement |

**Problem**
When a post is edited, `invalidateAuthorCache` calls `redis.keys('post:*')`, filters the results
in application code, and deletes the matches. Redis executes commands on a single thread; `KEYS`
is documented as an operation whose cost is proportional to the total size of the keyspace being
scanned, not to the number of matches — and this instance is also the BullMQ broker.

**Performance principle**
On a single-threaded command executor, one slow command delays every other command queued behind
it, regardless of whether those commands belong to a logically unrelated feature
(`technology/redis.md` §2, "command execution is effectively single-threaded"; the general
version of this appears in `principles/concurrency-and-contention.md`'s treatment of a
shared-executor bottleneck).

**Evidence**
`src/cache.js:24-31` — the `keys()` call and the subsequent `Array.prototype.filter`. No `SCAN`
based incremental iteration is used anywhere in `src/cache.js`. `docker-compose.yml:18` and
`k8s/redis-config.yaml:1` confirm the cache client and the BullMQ client
(`src/queues/connection.js:4`) both point at the same Redis host and logical database.

*No runtime evidence:* the actual size of the Redis keyspace at invalidation time is not known —
this determines whether the `KEYS` call is a sub-millisecond operation or a multi-second stall,
and it is not measured anywhere in this repository.

**Impact**
Position: off the critical path for the request that triggers it (post edit), but on the
*shared* critical path for every job the queue is concurrently trying to dequeue, since both
share the same single-threaded command execution. Frequency: once per post edit. Growth: O(keys)
in total Redis keyspace size, which grows with cache usage generally, not just with this
operation's own targets. Blast radius: the queue and the cache together — a busy invalidation
delays notification delivery for reasons that have nothing to do with the notification system
itself, which is the specific "shared resource, unrelated blast radius" shape this finding exists
to name.

**Conditions**
Severity depends on the actual Redis keyspace size at invalidation time, which is unknown —
**confidence is capped at `Medium` for this reason**, even though the mechanism (a blocking `KEYS`
call on a shared single-threaded executor) is unambiguous from the code alone. At a small keyspace
this is negligible; the point at which it becomes noticeable cannot be stated without measurement.

**Recommendation**
Replace `KEYS` with `SCAN`'s cursor-based iteration, which trades one blocking call for several
small non-blocking ones — the standard fix for this exact pattern
(`technology/redis.md` §4 names `KEYS` in production as a common failure mode by name). Separately,
consider whether the cache and the BullMQ broker should share an instance at all now that a
concrete contention mechanism has been identified between them — noted as a follow-on question,
not folded into this recommendation, since separating them is an infrastructure decision with its
own cost or an operational decision, not just a code change.

**Trade-offs**
`SCAN` gives up `KEYS`'s single-call simplicity and point-in-time-consistent view of the keyspace
for a series of calls that may see keys added or removed mid-iteration — acceptable here, since
cache invalidation does not require strict consistency of the scan itself. Separating cache and
queue onto different instances (the follow-on question above) would add one more piece of
infrastructure to operate and would need its own capacity planning.

**Validation**
- *Baseline:* measure `redis-cli --bigkeys` or `DBSIZE` against the production instance to
  establish current keyspace size. **safe-on-production: yes** — read-only diagnostics.
- *Measurement:* after switching to `SCAN`, measure BullMQ job-processing latency during a post
  edit versus during quiescent periods; the gap this finding predicts should narrow.
  **safe-on-production: no** for a deliberate load test; passive observation during normal
  operation is safe.
- *Falsifier:* if `DBSIZE` shows the keyspace is small (low thousands of keys), the `KEYS` call's
  actual cost is likely negligible regardless of the theoretical mechanism, and this finding
  should be downgraded.
- *Guard:* a Redis slow-log threshold alert, since none currently exists.

---

### Remaining findings

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|
| PERF-005 | Medium | Medium | P2 | `src/cache.js:9` | Post cache has no hit-rate instrumentation; effectiveness is unmeasurable and the 120s TTL is unexplained |
| PERF-006 | Medium | High | P2 | `src/services/analytics.js:11` | Outbound webhook client has a connect timeout but no read timeout; off the critical path today (fire-and-forget via `setImmediate`), so scored lower than PERF-002 despite the same root shape |
| PERF-007 | Medium | Medium | P2 | `k8s/hpa.yaml:24` | HPA scales on CPU only; per §6.6 the service may become Redis-command-bound before CPU-bound |
| PERF-008 | Low | High | P3 | `k8s/redis-config.yaml:6` | Cache and BullMQ broker share one Redis instance with `noeviction`; likely deliberate given the shared queue — separating the two uses is the cleaner long-term fix, flagged as a follow-on to PERF-004 |
| PERF-009 | Informational | Medium | P3 | `src/queues/fanOut.js:14` | Notification fan-out writes one document per follower with no batching and no concurrency limit; not scored higher because follower counts are unknown and the work is already off the request-serving critical path |

### Considered and not reported

- **Switching Mongoose to the native MongoDB driver for performance.** Not recommended. No
  profile exists showing ODM overhead is a meaningful share of request time, and PERF-001's
  per-post query loop dwarfs any plausible driver-level overhead. Fix the query count first;
  revisit only with profiling evidence.
- **Sharding the MongoDB cluster.** Not recommended. Nothing in this repository indicates write
  throughput or data volume anywhere near a level where sharding's operational complexity would
  pay for itself; the M10 tier's connection headroom (PERF-003) is itself evidence capacity is
  not currently the constraint.
- **Increasing `pm2` cluster worker count to compensate for PERF-002.** Not recommended. Every
  additional worker is still a single-threaded process that stalls the same way under the same
  synchronous call; adding workers increases the number of processes that can stall concurrently
  and increases memory footprint, without addressing the mechanism. Fix the blocking call itself.
- **Adding a Redis Cluster for horizontal scaling.** Not recommended. PERF-004's finding is about
  command-execution contention from a specific access pattern (`KEYS`), not about the instance
  running out of capacity; clustering would multiply operational complexity while leaving the
  actual `KEYS` usage — the root cause — unchanged on whichever node held the affected keys.

---

## 8. Prioritized action plan

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | — | — | Small | Add request-duration and event-loop-lag metrics. Nothing below can be validated without them |
| 2 | PERF-002 | P0 | Small | `quick-win`; removes a process-wide stall with no growth required to trigger it |
| 3 | PERF-001 | P0 | Medium | Largest structural improvement on the primary read path |
| 4 | PERF-004 | P1 | Small | `SCAN` migration is a contained, low-risk change that removes a shared-resource contention mechanism |
| 5 | PERF-003 | P1 | Small | Automated arithmetic check; prevents the margin from silently narrowing on a future change |
| 6 | PERF-005/006/007 | P2 | Varies | After measurement exists, these can be sized properly |

**If only one thing is done:** add request-duration and event-loop-lag instrumentation. Every
other item on this list is currently unverifiable, and that is the more expensive problem.

---

## 9. Validation plan

### Instrumentation gaps to close first

The service is uninstrumented for latency. Before any optimization:

1. Request duration per route, as percentiles — not averages. Averages will hide exactly the
   tail behavior PERF-002 produces.
2. Event-loop lag per `pm2` worker process.
3. Redis command latency and slow-log threshold, per the standard diagnostic
   `technology/redis.md` §3 names — this alone would likely confirm or refute PERF-004's severity
   within minutes.
4. MongoDB connection-pool in-use count, and `db.serverStatus().connections` sampled during
   normal operation and during scale-up events.

Per-finding validation is given inline in §7.

---

## 10. Notes on this review

- No runtime metric in this report was measured or estimated. The two arithmetic results — 26
  queries per default feed page, and a 540-connection maximum against a 1500-connection limit —
  are derivations from cited configuration files, labelled as such at the point of use.
- No finding reached `Confirmed` confidence, because the repository contains no runtime evidence.
  PERF-001 and PERF-004 would likely reach it quickly with query logging and a Redis slow-log
  threshold respectively — both cheap, safe-on-production changes named in §9.
- Four plausible optimizations were considered and deliberately not recommended; they are listed
  with reasons in §7 so the reader can disagree with the reasoning rather than wonder whether they
  were missed.
- PERF-002's `Category` is `concurrency`, not `data-access` — noted because it is the one finding
  in this review whose severity comes entirely from Node.js's single-JavaScript-thread execution
  model rather than from a datastore or network interaction, and is worth reading alongside
  `technology/node.md` for readers coming from a thread-per-request or multi-process background,
  where the same code pattern would be materially less severe.
