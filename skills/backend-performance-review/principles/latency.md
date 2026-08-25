# Latency

Latency is the time a caller waits. It is a distribution, not a number, and almost every
serious latency mistake starts with treating it as a number.

---

## 1. Percentiles, and why the average lies

| Statistic | What it tells you | What it hides |
|:--|:--|:--|
| Mean | Almost nothing useful | Everything — a bimodal distribution has a mean that occurs rarely |
| p50 | The typical experience | The entire tail |
| p95 | Where degradation becomes noticeable | The worst cases |
| p99 | The requests that trigger retries and timeouts | Rare catastrophic cases |
| p99.9 | The cases that produce incidents and complaints | — |

Report percentiles or say you have none. Never convert an average into a claim about the
tail, and never accept an average as evidence that the tail is fine.

**Percentiles do not average across dimensions.** The p99 of a fleet is not the mean of
per-instance p99s. If aggregation is being done that way in the system under review, that
is itself a finding — the operators cannot see their own tail.

---

## 2. Latency composition

For a blocking path:

```
total ≈ queueing
      + application CPU
      + Σ(serial I/O waits)
      + max(parallel I/O waits)
      + serialization/deserialization
      + network transfer
```

Practical consequences:

- Most backend paths are I/O-dominated. Optimizing application CPU on such a path yields
  little, and recommending it without a profile is a classic false positive.
- Serial I/O adds; parallel I/O takes the maximum. Converting serial independent calls to
  parallel is one of the few structurally sound latency wins — when the calls are truly
  independent and the downstream can absorb the concurrency.
- Queueing is invisible in code and dominant in incidents.

---

## 3. Queueing is where latency becomes non-linear

Service time is how long the work takes. Response time is service time plus waiting. As
demand approaches capacity, waiting grows sharply and disproportionately: a system that
looks comfortable at moderate utilization can become unstable after a modest traffic
increase.

Two consequences that matter for review:

- **A system with no headroom has no stability.** Utilization targets exist because of this
  curve, not out of caution.
- **Variability makes it worse.** Bursty arrivals and uneven service times both increase
  waiting at the same average utilization. A system fed by a batch job is more fragile than
  one fed by smooth traffic at the same rate.

Queueing occurs at every bounded resource: worker slots, connection pools, thread pools,
event loops, disk, network, downstream services. You cannot measure it statically — but you
can enumerate where it will occur, and recommend that wait time be instrumented there.
A pool with no wait-time metric is a blind spot in exactly the place incidents start.

---

## 4. Tail latency has its own causes

The tail is usually not "the same thing, but slower". It is a different mechanism.

| Tail cause | Why it only affects the tail |
|:--|:--|
| Garbage collection or runtime pauses | Periodic; affects whatever is in flight |
| Connection pool waits | Only when concurrent demand briefly exceeds the pool |
| Cache misses | The path is fast when warm, slow when cold |
| Lock contention | Only when two requests collide |
| Cold starts, JIT warmup | Only on the first requests to a new instance |
| One slow shard or replica | Only requests routed to it |
| Connection setup (DNS, TLS) | Only when a connection is not reused |
| Retry storms | Only during the window of degradation |

"Usually fast, sometimes terrible" is a tail report. Look here before looking at
algorithms.

---

## 5. Amplification

Fan-out multiplies a small cost into a large one, and multiplies a rare slowness into a
common one.

- **Per-item work in a list response.** A per-item cost multiplied by result size. Show the
  multiplication explicitly and label it a derivation.
- **Parallel fan-out.** When a request depends on several backends, overall latency tracks
  the slowest response. The more dependencies, the more often the aggregate lands in some
  dependency's tail — so a backend that is rarely slow individually can be frequently slow
  in aggregate.
- **Depth.** Nested resolution or recursive traversal multiplies invocations per level.
- **Retries.** Each retry adds its own latency and its own load.

Amplification is the main reason a system degrades faster than traffic grows. It maps
directly to the severity `growth` factor.

---

## 6. Latency budgets

A budget assigns a share of the target to each stage. It converts a vague goal into a
falsifiable per-stage claim.

```
target p99 for the endpoint
  ├─ auth check
  ├─ primary query
  ├─ external call
  └─ serialization + transfer
```

Two review uses:

- **A stage with no budget has no accountability.** If nobody has assigned one, the timeout
  on that stage is doing the job badly.
- **A timeout larger than the caller's remaining budget is useless.** It will never fire
  before the caller gives up. See `distributed/timeouts-and-deadlines.md`.

---

## 7. What to look for in a review

- Is latency measured at all, and as percentiles rather than averages?
- Which paths are blocking, and what is the composition of each?
- Are independent I/O calls issued serially?
- Where can queueing occur, and is wait time visible there?
- Are there per-item costs multiplied by an unbounded result size?
- Do stated latency targets exist, and do timeouts respect them?
- Is anything on the path unbounded in a way that makes worst-case latency undefined?

## 8. What not to conclude

- Do not assert a latency number you did not measure.
- Do not treat a fast local run or a green CI job as evidence about production latency.
- Do not recommend CPU-level optimization on an I/O-bound path without a profile.
- Do not assume parallelism is free — it shifts load onto a shared downstream.
