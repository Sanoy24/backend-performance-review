# Critical paths

Optimization effort should be proportional to time spent on the path a user waits for.
This phase decides where the rest of the review looks.

A critical path is any sequence of work that a caller is blocked on. Everything else is
secondary — with one important exception, covered below.

---

## 1. Find the paths

Start from entry points found in discovery and work inward.

| Entry point kind | How to find it |
|:--|:--|
| HTTP routes | Route registration, decorators, controller annotations, router tables |
| gRPC methods | Service implementations against the generated interface |
| GraphQL | Resolvers, especially field resolvers on list types |
| Message consumers | Subscriber registration, consumer group setup |
| Scheduled jobs | Cron expressions, scheduler definitions |
| Startup/readiness | Init code, migrations at boot, readiness probes |

For each, trace: **handler → business logic → data access → datastore**, plus every
external call and every piece of shared state touched along the way.

### What to record per path

```
Path:            GET /orders
Blocking:        yes — client waits
Datastore ops:   1 query (list) + 1 query per row (customer lookup)
External calls:  none
Shared state:    connection pool
Bounded:         no — no maximum page size found
Instrumented:    no timing metric or span on this route
```

Focus depth on the paths most likely to matter: those that fan out, those that are
unbounded, those that touch shared resources, and any path the user named in the workload
interview.

---

## 2. Rank the paths

Without runtime data, rank by structural signals rather than guessing at durations:

| Signal | Why it ranks a path higher |
|:--|:--|
| Unbounded result set | Cost grows with data, without a ceiling |
| Query count proportional to result size | N+1 shape; the classic superlinear cliff |
| Fan-out to multiple services | Latency is the max of the parallel calls, or the sum of serial ones |
| Serial dependent calls | Latency adds directly; each is a tail-latency contributor |
| Holds a shared resource | Local slowness becomes global |
| Writes in a transaction spanning I/O | Lock duration scales with the slowest call inside it |
| No timeout | Unbounded worst case |
| Missing pagination | Same as unbounded, but usually easier to fix |

If runtime data *does* exist — traces, endpoint latency metrics, a slow-query log — use it
and stop guessing. The ranking it gives you is evidence; the structural ranking is
inference.

State your ranking method in the report. "Ranked by structural signals; no runtime data
available" is a meaningful disclosure.

---

## 3. Latency composition

For a blocking path, the user-visible latency is roughly:

```
total ≈ queueing + application CPU + Σ(serial I/O waits) + max(parallel I/O waits)
        + serialization + network transfer
```

Most backend paths are dominated by the I/O terms. This has a practical consequence worth
holding onto: **micro-optimizing application CPU on an I/O-dominated path is almost always
the wrong target**, and recommending it is a common failure of automated review.

Before recommending a CPU-level optimization, establish that CPU is actually a meaningful
share. Without a profile you usually cannot — so either say so, or recommend the profile
instead of the optimization.

### Where queueing comes from

Queueing delay is invisible in code and dominant in incidents. It appears when demand
approaches capacity in any of: worker slots, connection pool, event loop, thread pool,
database CPU, disk, or downstream service. As utilization rises toward saturation, waiting
time grows sharply and non-linearly — the practical consequence is that a system running
comfortably at moderate utilization can become unstable with a modest traffic increase.

You cannot measure queueing statically. You *can* find the places it will occur: every
bounded pool with no visibility into wait time is a candidate. Recommending a wait-time
metric on a pool is often more valuable than any code change.

---

## 4. Latency amplification

One slow unit multiplied by fan-out. Amplification is where "a few milliseconds" becomes
an outage.

- **Per-item work in a list response.** 2 ms × 500 items = 1 s. Show this arithmetic in
  `Impact` and label it as a derivation.
- **Per-request work in a batch job.** A job processing 100k items pays every per-item cost
  100k times.
- **Fan-out across services.** With parallel calls, overall latency tracks the slowest one,
  so a rarely-slow dependency shows up much more often at the aggregate tail than it does
  on its own. The more dependencies, the more pronounced this becomes.
- **Retries.** A retry multiplies load on an already-degraded dependency; see
  `distributed/retries-and-backpressure.md`.
- **GraphQL nesting.** Depth multiplies resolver invocations, and each level may issue its
  own queries.

Amplification is a growth-factor argument, so it feeds directly into the severity `growth`
factor.

---

## 5. Tail latency deserves separate treatment

p50 tells you about the typical request. p99 and p99.9 tell you about the ones that break
things — retries, timeouts, user-visible stalls, cascading pressure upstream.

Common tail-only causes that median latency hides completely:

- GC pauses or runtime stop-the-world events
- Connection pool waits under transient burst
- Cache misses on an otherwise warm path
- Lock contention windows
- Cold starts (serverless, new container, JIT warmup)
- A single slow shard, partition, or replica
- DNS or TLS handshake on a connection that should have been reused
- Retry storms briefly saturating a dependency

When the user reports "it's usually fast but sometimes terrible", read that as a tail
problem and look at this list before looking at algorithms.

---

## 6. The exception: background work on shared resources

Off-critical-path work is usually low priority. It stops being low priority when it
contends for something the critical path needs.

Examples worth treating as critical-path findings:

- A nightly job that exhausts the connection pool during its run.
- A batch process that saturates database I/O, slowing every concurrent query.
- A consumer that blocks the event loop the HTTP server shares.
- An analytics query on the primary rather than a replica.
- A migration or backfill running without throttling.

Score these with `position: async` but `blast radius: system-wide`. The severity table
then produces the right answer without special-casing.

---

## 7. Output of this phase

```
Ranked critical paths (method: structural signals | runtime data)
  1. GET /orders          — unbounded result set, per-row queries, pool contention
  2. POST /checkout       — 3 serial external calls, one with no timeout
  3. consumer: order-events — unbounded batch, shares the pool with the API

Amplification points
  - /orders: query count proportional to page size, page size uncapped

Shared resources at risk
  - connection pool (API + consumer + nightly job)

Paths deliberately not analyzed in depth, and why
  - admin routes: internal-only, low frequency, not user-blocking
```

The last section matters. Saying what you did not examine, and why, is part of an honest
review.
