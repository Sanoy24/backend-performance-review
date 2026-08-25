# Connection pools

Pools exist because connection setup is expensive and connection count is limited. They are
also one of the most reliable sources of high-confidence findings in static review, because
every input to the analysis is usually a number written in the repository.

Applies to datastore pools, HTTP client pools, cache client pools, and broker connections.

---

## 1. The arithmetic

Two calculations. Do both whenever the numbers are available.

**Does the pool meet demand?**

```
concurrency required ≈ arrival rate × time a connection is held
```

If the pool is smaller than this, requests queue for a connection. The symptom is rising
latency with an *idle-looking datastore* — which is why this failure is so often
misdiagnosed as a slow database.

**Does the total exceed the datastore's limit?**

```
pool size × workers per instance × instances  ≤  datastore max connections
```

Exceeding the limit is an outage, not a slowdown: new connections are refused and errors
surface in unrelated code paths. Leave headroom for admin connections, migrations,
monitoring agents, and rolling deploys — during a rolling deploy, old and new instances
briefly coexist and the connection count roughly doubles.

**Do this arithmetic in every review where the inputs exist.** It is the highest
value-per-effort check available, it requires no runtime data, and it earns `High`
confidence because it is a property of the configuration.

Where to find the inputs:

| Input | Where |
|:--|:--|
| Pool size | ORM/driver config, connection-string parameters, framework settings |
| Workers per instance | Entrypoint script, `Procfile`, container `CMD`, process-manager config |
| Instances | Deployment manifest replica count, ASG size, compose scale |
| Datastore max connections | Infrastructure-as-code, managed-service instance class, engine config |

If any input is unavailable, say which one and note that the check is incomplete. Do not
substitute a guess.

---

## 2. Hold time is the lever

Pool pressure is driven as much by how long connections are held as by how many exist.

Findings that increase hold time:

- **A connection acquired for the whole request** rather than for the query. Time spent on
  serialization, external calls, or business logic is time a connection is held for nothing.
- **A transaction spanning an external call.** The worst case: hold time becomes a third
  party's latency.
- **A slow query.** Doubling query time halves effective pool capacity at the same arrival
  rate. This is why a small datastore regression can cause an application-wide outage.
- **A connection leak.** Not returned on an error path, or held by an object that outlives
  the request. Symptom: pool exhaustion that worsens over time and resets on restart.
- **Idle-in-transaction connections** — a transaction opened and neither committed nor
  rolled back promptly.

Reducing hold time is frequently a better fix than enlarging the pool, because enlarging the
pool moves contention into the datastore.

---

## 3. Sizing is not "bigger is better"

An oversized pool moves the constraint from the application to the datastore, where more
concurrent queries mean more contention for CPU, memory, locks, and I/O. Past a point,
throughput falls while connection count rises.

Signals of an oversized pool: pool size far exceeding worker count (only useful if a single
worker holds multiple connections concurrently); total connections near the datastore's
limit; a pool much larger than the datastore's effective parallelism.

Signals of an undersized pool: pool much smaller than concurrent workers with no
justification; latency rising under load with datastore utilization low; wait-time metrics
present and non-zero.

The honest recommendation is usually a range plus a measurement, not a number. **Recommend
that acquisition wait time be instrumented** — without it, nobody can tell which of the two
problems they have, and any specific number you supply is a guess.

---

## 4. Pool configuration worth checking

| Setting | Failure mode when wrong |
|:--|:--|
| Acquisition timeout | Absent means requests wait indefinitely for a connection — an unbounded worst case that turns saturation into a hang |
| Maximum lifetime | Absent means connections outlive infrastructure changes; failovers and proxy restarts surface as errors |
| Idle timeout | Too aggressive means constant reconnection; too lax means idle connections consume the datastore's limit |
| Validation / health check | Absent means broken connections are handed out; per-acquisition checks add a round trip to every operation |
| Minimum / warm size | Zero means the first requests after idle pay full setup cost — a cold-start tail |
| Queue bound | Unbounded acquisition queues turn overload into memory growth and long waits instead of fast failure |

An absent acquisition timeout is the most consequential of these, and it is visible in
config. It converts "slow" into "hung", and it is why a saturated pool can take down a
service that would otherwise have shed load.

---

## 5. HTTP client pools

The same reasoning applies, and is more often neglected than datastore pooling.

- **A client constructed per request** creates a new connection each time, paying DNS, TCP,
  and TLS setup on every call, and can exhaust ephemeral ports under load. This is a
  frequent, high-value, easily-verified finding.
- Per-host connection limits are usually low by default and silently serialize calls to a
  busy dependency.
- Keep-alive must be enabled on both ends; a server closing connections defeats client
  reuse.
- DNS caching interacts with pooling: cached entries pointing at a rotated address, or no
  caching at all adding a lookup per connection.

---

## 6. Pools across deployment models

**Multi-process.** Each process has its own pool. Total connections multiply by process
count; per-process pool size is what the datastore sees multiplied.

**Serverless.** The usual advice inverts. Each concurrent invocation may hold its own
connection, so concurrency directly becomes connection count, and there is no shared pool to
bound it. An external pooler, or a protocol designed for it, is usually required. See
`infrastructure/resources.md`.

**Autoscaled.** Peak connection count is set by *maximum* replicas, not current ones. Size
the arithmetic against the autoscaler's upper bound; a service that is fine at three
replicas can exhaust the datastore at its configured maximum of thirty.

**With an external pooler in front.** The pooling mode matters: some modes multiplex many
clients onto few server connections but restrict session-level features such as prepared
statements or session variables. Check that the application's usage is compatible.

---

## 7. What to look for in a review

- Both pieces of arithmetic, with every input cited.
- Autoscaler maximum, not current replica count.
- Connection hold time relative to actual query time.
- Transactions or connections held across external calls.
- Acquisition timeout present and bounded.
- Connection lifetime and idle settings.
- HTTP clients constructed per request.
- Whether wait time is observable at all.

## 8. What not to conclude

- Do not recommend a specific pool size without the arithmetic and its inputs.
- Do not recommend enlarging a pool before checking hold time.
- Do not assume defaults are sensible; find the configured value or state that you could not.
- Do not treat pool exhaustion as the root cause — it is usually a symptom of hold time,
  leak, or a slow dependency.
