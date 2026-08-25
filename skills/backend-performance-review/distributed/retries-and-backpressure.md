# Retries and backpressure

Retries improve reliability for transient failures and destroy systems during sustained ones.
Backpressure is what keeps the difference under control. Reviewing one without the other
produces incomplete findings.

---

## 1. Retries amplify load exactly when capacity is lowest

A dependency slows or fails. Callers retry. The dependency now receives more traffic than
before, while serving less. It slows further, more requests time out, more retries fire.

This is a positive feedback loop, and it is the mechanism behind a large share of
cascading outages. The amplification compounds with depth: if every layer in a four-layer
call chain retries three times, the bottom layer can see many multiples of the original
request rate. Retry configuration that looks modest at one layer is not modest in aggregate.

**Always evaluate retries in terms of the whole chain, not the single call site.** A finding
that says "this client retries 3 times" is incomplete; the finding is what that becomes at
the bottom of the stack.

---

## 2. Retry configuration to check

| Control | Absent or wrong means |
|:--|:--|
| Attempt cap | Unbounded retries; unbounded latency and load |
| Backoff | Immediate retries hit a struggling dependency at full rate |
| Exponential growth | Linear backoff barely reduces pressure |
| Jitter | Retries synchronize into waves; load arrives in spikes |
| Total time budget | Per-attempt bounds do not bound the whole operation |
| Retryable-condition filter | Retrying a permanent failure wastes capacity forever |
| Idempotency | Retried writes duplicate side effects |
| Retry budget or circuit breaker | No ceiling on retry traffic as a share of total |

**Jitter is the most commonly missing of these.** Without it, all clients that failed at the
same moment retry at the same moment, converting a smooth overload into a series of spikes
that prevents recovery.

**Retrying non-retryable failures** is close behind. A validation error, an authorization
failure, or a not-found will fail identically on every attempt; retrying them consumes
capacity and delays the inevitable error. Check what the retry predicate actually matches —
"retry on any exception" is a finding.

---

## 3. Idempotency

A timeout does not tell the caller whether the operation completed. Retrying after a timeout
therefore risks duplicating the effect.

- **Idempotent operations** can be retried safely. Reads, and writes carrying an idempotency
  key or a deterministic identifier.
- **Non-idempotent operations** cannot. Retrying a payment, an email send, or an increment
  either duplicates the effect or requires the caller to accept a long timeout instead.

If retries exist on a non-idempotent path with no idempotency key, that is a correctness
finding surfaced by performance review. Report it — the duplicate work is also load, and the
correctness consequence usually matters more than the latency one.

---

## 4. Circuit breaking and retry budgets

Both bound retry traffic, in different ways.

**Circuit breaker** — after a failure threshold, stop calling the dependency for a period,
failing fast instead. Two benefits: the dependency gets room to recover, and the caller stops
spending resources on calls that will fail. The costs are the state to manage, the tuning of
thresholds, and the half-open probing behavior, which is where most breakers are configured
badly.

**Retry budget** — cap retries as a fraction of total requests. Simpler to reason about than
a breaker, degrades more smoothly, and does not have a threshold cliff.

Neither is free, and neither should be recommended reflexively. The question to answer first
is whether the system currently has *any* ceiling on retry traffic. If it does not, that is
the finding; which mechanism to adopt is a secondary decision.

---

## 5. Backpressure

Backpressure is the signal that a producer must slow down. Its absence means work accumulates
until something fails, and the failure is usually memory or latency rather than an explicit
error.

Where it is missing, you will see:

- **Unbounded in-memory queues or channels.** Overload becomes memory growth, then a crash.
- **No admission control.** Requests are accepted at any rate, then queue internally. Callers
  time out; the server keeps processing work nobody will read.
- **Consumers fetching more than they can process** within a visibility or lease window,
  causing redelivery and duplicated work — which adds load during overload.
- **Producers writing to a queue with no depth limit** and no signal when consumers fall
  behind.

**Fast rejection is usually better than slow acceptance.** A rejected request lets a caller
retry elsewhere, degrade, or inform a user. An accepted-but-queued request consumes resources
on both sides and frequently ends in a timeout that wasted everything spent on it.

Load shedding — rejecting a fraction of work to keep the rest healthy — is a deliberate design
choice worth naming when a system has no defense at all. Prefer shedding the least valuable
work: background over interactive, retried over first-attempt.

---

## 6. Queues and consumers

Queue-based systems have their own version of the same reasoning:

- **Consumer throughput must exceed producer rate** on average, or lag grows without bound.
  Lag is the primary health metric; its absence from monitoring is a finding.
- **Partition or shard count bounds consumer parallelism** in partitioned systems. Adding
  consumers beyond the partition count adds nothing, which surprises people regularly.
- **Key skew** concentrates work on one partition; effective throughput is set by the busiest
  one.
- **Poison messages** that fail repeatedly consume capacity indefinitely without a dead-letter
  path and an attempt cap.
- **Batch and prefetch sizes** trade throughput against redelivery risk and memory. Prefetching
  more than can be processed within the lease window guarantees redelivery.
- **Ordering requirements** constrain parallelism: strict per-key ordering means one consumer
  per key at a time.

---

## 7. Fan-out

One event producing many downstream operations multiplies both load and failure surface.

- Fan-out is a growth multiplier: severity should reflect the multiplied count, not the
  triggering event.
- A fan-out with no concurrency bound is unbounded concurrency.
- A retry on a fan-out step re-runs only that step if the work is properly decomposed, or the
  whole fan-out if it is not — check which.
- Fan-out to a shared downstream converts one event into a burst on a single resource.

---

## 8. What to look for in a review

- Retry configuration at every call site: cap, backoff, exponential growth, jitter, total
  budget, retryable-condition filter.
- Aggregate amplification across the whole call chain, not per site.
- Retries on non-idempotent operations without idempotency keys.
- Any ceiling on retry traffic — breaker, budget, or nothing.
- Unbounded queues, channels, and buffers.
- Whether the system can reject work, or only accept it.
- Queue lag monitoring, partition counts versus consumer counts, key skew.
- Dead-letter handling and attempt caps.
- Fan-out concurrency bounds.

## 9. What not to conclude

- Do not recommend retries as a general reliability improvement without the controls that
  bound them.
- Do not recommend a circuit breaker without addressing threshold and half-open behavior; a
  badly tuned breaker causes its own outages.
- Do not recommend increasing consumer count beyond the partition count.
- Do not treat queueing as backpressure — an unbounded queue is the absence of backpressure,
  not its implementation.
