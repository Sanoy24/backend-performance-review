# Throughput

Throughput is completed work per unit time. Latency and throughput are related but
distinct, and optimizing one can damage the other — batching improves throughput and
worsens per-item latency; adding concurrency improves throughput up to the point where
contention makes both worse.

---

## 1. Throughput is set by the narrowest stage

A pipeline's throughput equals the throughput of its slowest stage. Every other stage's
capacity is invisible until that stage is fixed.

Practical consequences for review:

- **Optimizing a non-bottleneck stage produces no measurable improvement.** This is the
  most common way performance work is wasted, and it is why bottleneck identification
  precedes recommendation in this methodology.
- **Fixing the bottleneck moves it.** Say so in the report: after this change, the next
  constraint is expected to be X. It sets accurate expectations and prevents the "we fixed
  it and nothing improved" conversation.

Candidate narrowest stages: worker slots, connection pool size, datastore CPU or I/O, a
single-threaded stage, a lock, a queue partition, an external rate limit, disk, network.

---

## 2. Concurrency, capacity, and hold time

Three quantities interlock:

```
concurrency required ≈ arrival rate × time each unit occupies the resource
```

This is the most useful arithmetic in backend capacity work, and it needs no measurement of
the system to apply — only two of the three quantities.

Uses in review:

- **Sizing a pool.** If a request holds a connection for the duration of a query, the pool
  must cover arrival rate × hold time, or requests queue.
- **Spotting an inconsistency.** A pool of 5 behind 64 workers is a claim that at most 5
  requests will need the datastore concurrently. Usually that claim is wrong, and it is
  checkable from files in the repository.
- **Understanding why a small latency increase causes a large capacity drop.** If hold time
  doubles, the same arrival rate needs twice the concurrency. A dependency slowing slightly
  can exhaust a pool that appeared to have adequate headroom.

That last point is worth stating explicitly in reports: **slow dependencies consume
capacity, not just time.**

---

## 3. Utilization and headroom

High utilization is not efficiency; it is fragility. As utilization rises toward capacity,
queueing grows disproportionately (see `latency.md`), so a system running near saturation
has no room to absorb a burst, a retry wave, or a slow dependency.

Review implications:

- A service with no headroom target and no autoscaling signal is operating on hope.
- Autoscaling on CPU alone misses saturation of pools, event loops, and downstream
  dependencies — the resources that actually bind most backends.
- A resource at full utilization is a bottleneck; a resource that never rises above a few
  percent is not worth optimizing regardless of how inefficient its code looks.

---

## 4. Batching

Batching amortizes per-operation overhead — round trips, syscalls, transaction setup,
per-message framing — across many items. It is one of the few reliably large wins.

It trades latency for throughput, and its costs must be stated:

- Per-item latency rises by up to the batch window.
- A failure affects the whole batch unless partial failure is handled.
- Memory grows with batch size.
- Very large batches produce long lock or transaction durations that hurt everything else.

Batching is most valuable where per-operation overhead dominates per-item work — typical of
remote calls, rare in local computation.

---

## 5. Where throughput is actually lost

| Pattern | Effect |
|:--|:--|
| Per-item round trips instead of batched operations | Overhead dominates; throughput scales with round-trip time |
| A serialized stage inside a parallel pipeline | Caps the whole pipeline |
| Skewed partitioning | One partition saturates while others idle; effective capacity is set by the hottest key |
| Lock held across I/O | Serializes every contender for the duration of a network call |
| Undersized pool | Requests queue for connections while the datastore sits idle |
| Oversized pool | Contention moves into the datastore, which now thrashes |
| Retries without backpressure | Load amplifies exactly when capacity is lowest |
| Single consumer on a growing queue | Lag grows without bound |

Skew deserves emphasis: a partitioned system's throughput is governed by its hottest
partition, not its average. Even distribution is an assumption to check, never a property
to assume.

---

## 6. Scaling behavior

Adding capacity helps only where the constraint is capacity.

- **Scales horizontally** — stateless request handling against an unsaturated datastore.
- **Does not scale by adding instances** — a single-writer datastore, a global lock, a
  single-partition queue key, an external rate limit, a shared cache node.

More instances against a saturated shared datastore makes things worse: more connections,
more contention, more queueing at the constraint. "Add servers" without identifying the
constraint is on the cargo-cult list for exactly this reason.

---

## 7. What to look for in a review

- What is the narrowest stage on each important path?
- Are per-item remote operations used where a batched form is available?
- Is any partitioning scheme vulnerable to skew?
- Are pool sizes consistent with worker counts and with the datastore's connection limit?
- Is any lock held across I/O?
- Do queues have enough consumers, and is lag monitored?
- Does the scaling story identify a constraint, or does it assume adding instances works?

## 8. What not to conclude

- Do not claim a throughput number you did not measure.
- Do not recommend batching without stating the latency and failure-mode costs.
- Do not recommend more instances without identifying the constraint they relieve.
- Do not treat a stage as the bottleneck because it is the most visible or easiest to read.
