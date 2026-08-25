# Infrastructure resources

Containers, orchestration, autoscaling, and serverless. The infrastructure layer is where
application assumptions meet enforced limits, and the mismatch between the two is a rich
source of high-confidence findings — because both sides are written down in the repository.

---

## 1. Limits versus what the application expects

The core check: **does the runtime's configured behavior fit inside the enforced limit?**

| Application-side | Infrastructure-side | Mismatch produces |
|:--|:--|:--|
| Configured heap or cache size | Container memory limit | Process killed, not garbage collected — abrupt, with nothing in the app log |
| Worker/thread count × per-worker memory | Container memory limit | Same, under load rather than at startup |
| Runtime parallelism (thread pools, GC threads) | CPU quota | Throttling and context-switch overhead |
| Pool size × workers × max replicas | Datastore connection limit | Refused connections at peak — an outage |
| Open connections and files | File-descriptor limit | Errors under load, unrelated to the code that fails |

Every input is usually present in the repository, which makes these `High` confidence with
no runtime data required. **Do this arithmetic in every containerized review.**

### CPU limits behave unlike memory limits

Exceeding a CPU quota causes throttling: the process is paused until the next scheduling
period. Exceeding a memory limit usually causes termination.

The practical consequences:

- CPU throttling appears as **latency spikes at p99 with unremarkable average CPU
  utilization**. It is a tail problem that dashboards showing mean utilization will not
  reveal. If throttling metrics are not collected, say so — it is a common blind spot.
- Memory overrun appears as a restart with no application-level explanation, which teams
  frequently misattribute to a crash bug.
- A CPU *limit* much lower than the *request* produces throttling under exactly the bursty
  conditions the request was sized for.

### Requests versus limits

Requests drive scheduling and placement; limits drive throttling and termination. Common
configurations and their consequences:

- **No requests set** — the scheduler cannot place the workload sensibly; noisy-neighbor
  effects follow.
- **No limits set** — one workload can starve others on the node.
- **Request equals limit** — predictable and isolated, at the cost of unused headroom.
- **Limit far above request** — good utilization, but the workload is throttled or evicted
  when the node is busy, producing latency that correlates with unrelated workloads.

---

## 2. Replicas and autoscaling

- **Size connection arithmetic against the autoscaler's maximum**, not current replica count.
  A service healthy at three replicas can exhaust a datastore's connection limit at its
  configured maximum of thirty. This is a specific, checkable, and frequently missed finding.
- **CPU-based autoscaling misses the constraints that actually bind most backends** —
  connection pools, event loops, queue depth, downstream saturation. A service that is
  pool-bound will not scale on CPU, because CPU stays low while requests queue.
- **Scale-up is not instant.** Scheduling, image pull, process start, warmup, and readiness
  all take time. A traffic spike faster than the scale-up path is served by the existing
  replicas regardless of the configured maximum.
- **Scale-down during a request** drops in-flight work unless graceful shutdown is
  implemented and the termination grace period exceeds the longest request.
- **Aggressive scale-down followed by scale-up** means constant cold starts and constant
  cache-warming, in runtimes where those matter.

---

## 3. Probes and readiness

- A readiness probe that only checks the process is alive lets traffic reach an instance that
  cannot serve — cold caches, unestablished pools, incomplete warmup. The result is a burst of
  slow or failed requests on every deploy and every scale-up.
- A liveness probe that fails under load restarts a busy instance, shifting its load to the
  remaining ones and cascading. Liveness probes with tight timeouts are a known way to convert
  a slowdown into an outage.
- Probes that perform real work — a datastore query per probe, at a short interval, across
  many replicas — are themselves load. Multiply the interval by the replica count.
- Startup work — migrations, warmup, large initialization — delays readiness and serializes
  deploys.

---

## 4. Networking topology

- **Cross-zone and cross-region hops** add latency and, on most clouds, cost. A hot path that
  crosses a region boundary is a structural latency floor that no application optimization
  will remove.
- **Service mesh sidecars** add a hop in each direction, plus CPU and memory per pod. Usually
  acceptable, occasionally significant on very high-throughput internal paths — and worth
  noting when the path in question is chatty.
- **Load-balancing algorithm** matters when request costs are uneven: round-robin sends
  expensive and cheap requests alike, and least-connections behaves better under uneven
  service times.
- **Connection pinning with long-lived connections** (HTTP/2, gRPC) can concentrate load on a
  subset of backends after a scale-up, because existing connections do not move.
- **DNS TTL and client-side caching** determine how quickly clients notice new instances.

---

## 5. Serverless

Serverless inverts several standard recommendations. Apply this section instead of, not in
addition to, the usual pooling advice.

**Cold starts.** Every new execution environment pays initialization: runtime start,
dependency loading, framework setup, connection establishment, and JIT warmup where
applicable. Drivers of cold-start cost visible in a repository: deployment package size,
dependency count, work done at module scope, and runtime choice.

**Connections do not pool the usual way.** Each concurrent execution may hold its own
connection, so connection count tracks concurrency directly with no shared pool to bound it.
A traffic spike becomes a connection spike, and the datastore's connection limit is reached
far sooner than in a long-lived deployment. The usual answers are an external pooler, a
data-access protocol designed for it, or a connection-per-environment reused across
invocations — each with its own trade-offs.

**Reuse across invocations.** State initialized outside the handler persists between
invocations on the same environment. Initializing clients inside the handler pays setup on
every invocation and is a concrete, checkable finding.

**Concurrency limits** cap throughput and, when reached, cause throttling that appears to the
caller as errors or added latency.

**Billing is by duration and memory**, so slow code costs money in direct proportion — and
memory allocation often also determines CPU allocation, which means *increasing* memory can
reduce total cost by reducing duration. This is one of the few places where the cost and
latency levers point the same way, and it is worth checking rather than assuming.

---

## 6. Storage and node-local resources

- Ephemeral container storage is typically small and its exhaustion is abrupt; temporary files
  and verbose local logging are the usual causes.
- Network-attached storage has IOPS and throughput limits that are throttled, producing
  latency that looks like a datastore problem and is not.
- Log volume is disk I/O, network I/O, and ongoing cost. Verbose logging on a hot path is a
  real finding on all three axes.

---

## 7. What to look for in a review

- Configured heap/cache sizes against container memory limits.
- Runtime parallelism against CPU quota; whether throttling is measured at all.
- Requests versus limits, and whether both are set.
- Connection arithmetic against the **maximum** replica count.
- What the autoscaler scales on, and whether that is the actual constraint.
- Readiness probe meaningfulness; liveness probe behavior under load; probe cost × replicas.
- Startup work delaying readiness.
- Cross-zone or cross-region hops on hot paths.
- For serverless: module-scope initialization, connection strategy, package size, concurrency
  limits, memory-versus-duration cost.

## 8. What not to conclude

- Do not recommend raising a limit without saying what becomes the constraint afterward.
- Do not recommend more replicas without identifying what they relieve — and check the
  connection arithmetic before suggesting it.
- Do not assume unset limits mean unlimited; the node's capacity and the scheduler still
  apply.
- Do not apply standard connection-pooling advice to serverless without addressing the
  per-invocation model.
