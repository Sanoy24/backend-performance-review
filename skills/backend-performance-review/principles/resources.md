# Resources

CPU, memory, disk, network, connections, file descriptors, and money. Every performance
problem eventually reduces to a resource that is saturated, wasted, or mis-provisioned.

This file replaces the separate CPU / memory / I/O / utilization treatments: they share one
analytical frame, and splitting them produces four files that repeat each other.

---

## 1. The frame

For any resource, ask the same five questions:

1. **What is the limit?** Often written in the repository — container limits, pool sizes,
   `ulimit` settings, instance types, quotas.
2. **What consumes it, and in proportion to what?** Per request, per item, per connection,
   per instance.
3. **What happens at the limit?** Throttling, queueing, rejection, eviction, swapping,
   or a crash. These have very different severities.
4. **Is consumption observable?** An unmonitored limit is a future incident.
5. **Is the limit consistent with the other limits?** Inconsistency between configured
   limits is a checkable, `High`-confidence finding requiring no runtime data.

Question 5 is where static review is strongest. Most repositories contain several
configured limits that contradict each other.

---

## 2. CPU

CPU-bound work is less common in backends than assumed — most services are I/O-bound. Do
not treat CPU as the problem without evidence.

Where CPU actually goes in a typical service, roughly in order of frequency:

- Serialization and deserialization (JSON in particular, at scale).
- Compression and decompression.
- TLS handshakes and, less often, bulk encryption.
- Hashing, especially deliberately expensive password hashing.
- Application-level filtering, sorting, and aggregation that a datastore could have done.
- Regular expressions, especially with catastrophic backtracking.
- Garbage collection.
- Logging and instrumentation, when verbose and synchronous.

**Only a profile tells you which.** Without one, the honest finding is "CPU attribution
unknown; here is how to obtain it". Guessing at CPU distribution is a common source of
false positives.

Two limit interactions worth checking in containerized deployments:

- A CPU quota below the parallelism the runtime configures for itself. Runtimes that size
  thread pools or GC threads from the visible core count can be badly mismatched with the
  quota, producing throttling and context-switch overhead.
- CPU throttling manifests as latency spikes, not as high CPU utilization. It is a tail
  problem that looks like nothing in an average utilization graph.

---

## 3. Memory

Memory problems take three shapes, and they need different responses.

**Growth without bound.** An unbounded cache, an accumulating buffer, a collection keyed by
something unbounded, a retained reference in a long-lived structure. The finding is the
missing bound, not the size.

**High steady-state usage.** Large per-request allocations, whole-result-set materialization,
large object graphs from an ORM, oversized worker counts each with their own heap. Fix by
streaming, by paginating, or by selecting fewer fields.

**Allocation pressure.** Not the amount retained but the rate allocated. In garbage-collected
runtimes this drives collection frequency, and collections are a whole-process tail-latency
event. A hot path allocating heavily degrades every other path in the process.

Limits to check against each other:

- Container memory limit versus the runtime's configured heap or cache sizes. If the heap
  can grow past the limit, the process is killed rather than collected — an abrupt failure
  with no warning in application logs.
- Worker count × per-worker memory versus the instance or container limit.
- In-process cache maxima versus available memory.

**Exceeding a memory limit usually kills the process.** That makes memory findings
qualitatively different from CPU findings: the failure mode is a hard stop, not a slowdown.

---

## 4. Disk I/O

Relevant even when the application does little file I/O, because the datastore does.

- Random versus sequential access differ by orders of magnitude on spinning media and still
  differ meaningfully on SSD.
- Write amplification: one logical write becomes several physical ones through journals,
  write-ahead logs, index updates, and compaction.
- `fsync` durability costs — a durable commit waits for the device.
- Filesystem-level cache is doing more work than most reviewers assume; a "fast query"
  may simply be a cached page.
- Log volume is disk I/O. Verbose synchronous logging on a hot path is a real and commonly
  missed cost.
- Ephemeral and network-attached storage often carry IOPS limits that are throttled rather
  than queued.

---

## 5. Network I/O

- **Round trips usually dominate payload size** on internal links. Reducing the number of
  calls beats reducing bytes per call, until payloads get large.
- Connection setup is expensive: TCP handshake, TLS negotiation, and often DNS. Reuse is
  the single most valuable network optimization in most services — see
  `application/connection-pools.md`.
- Bandwidth limits exist per instance and per link, and cross-zone or cross-region traffic
  is both slower and, on most clouds, billed.
- Chatty protocols and per-item remote calls turn network latency into a multiplier.

---

## 6. Connections and file descriptors

Connections are a resource with several limits stacked in series, and they are frequently
inconsistent with each other:

```
application pool size
  × workers per instance
  × instances
  ≤ datastore max connections
```

Also bounded by: the process file-descriptor limit, ephemeral port range for outbound
connections, and any proxy or pooler in between.

Exceeding the datastore's connection limit is an outage, not a slowdown: new connections
are refused, and the failure appears as errors in unrelated code paths. **Check this
arithmetic in every review where the numbers are available** — it is one of the highest
value-per-effort checks in static performance review, and every input is usually in the
repository.

Note that serverless inverts the usual advice: per-invocation environments can produce a
connection per concurrent invocation, which is exactly the case pooling was meant to
prevent. See `infrastructure/resources.md`.

---

## 7. Cost as a resource

For many services the binding constraint is spend, not latency. Treat it as a first-class
axis when the evidence points there.

Cost-shaped findings:

- Per-operation-billed datastores where an N+1 multiplies the bill as directly as the
  latency.
- Cross-zone or cross-region data transfer on a hot path.
- Over-provisioned instances kept large to mask an inefficiency.
- Retries and duplicated work billed twice.
- Log and metric volume — observability is frequently a top-three line item.
- Idle capacity held for a peak that could be handled by autoscaling.

The trade-off framing matters: an optimization that halves latency and doubles cost may or
may not be correct. Say which lever the user is pulling, and let them choose.

---

## 8. Observability overhead

Instrumentation consumes the same resources as everything else in this file — CPU, memory,
network, disk, and money — and it sits disproportionately on the hot path, since that is
where there is the most to observe. This skill's own evidence-first discipline (`SKILL.md`
rule 1) depends on that instrumentation existing, which makes its cost worth analyzing on its
own terms rather than waving through as free.

- **Logging.** Synchronous, verbose logging on a hot path is disk and network I/O charged per
  request (§4 above already names this as commonly missed). Structured logging with large
  payloads (full request/response bodies, stack traces on expected errors) multiplies the
  per-line cost. Log level configuration that ships `DEBUG` to production is a checkable,
  static finding.
- **Metric cardinality.** A metric labeled or tagged with an unbounded dimension — user ID,
  request ID, raw URL path instead of a route template, a tenant identifier with no
  cap — creates one time series per distinct value. This is a memory and cost problem in the
  metrics backend, not the application, but the application is where the unbounded label is
  introduced, and it is visible in the instrumentation call site. The database analogue is
  `databases/time-series.md`'s series-cardinality section; the mechanism is the same.
- **Trace sampling.** Sampling rate is a direct trade-off between observability coverage and
  the CPU/network cost of exporting spans, and between storage cost and the ability to find
  a specific rare failure later. A rate of 100% ("always sample") is a checkable, static
  finding on a high-volume path; so is a rate low enough that the specific failure a report
  needs to investigate would statistically never be captured.
- **Span and attribute volume per request.** Deep instrumentation (a span per function call,
  large attribute payloads per span) multiplies both the CPU cost of tracing and the network
  cost of export, independent of the sampling rate applied on top of it.
- **APM and agent overhead.** A bytecode-instrumentation or sidecar-based APM agent has a
  measurable per-request CPU and memory cost, paid on every request regardless of whether
  that request is ever inspected. This is a real, checkable resource line — treat an agent's
  presence as a fact to note in the resource budget, not as free.
- **The self-monitoring feedback loop.** Metrics about metrics, logs about logging failures,
  and health checks that themselves query the primary database all consume the same shared
  capacity as the traffic they monitor — and can degrade fastest exactly when the system is
  already under load, at the moment their signal matters most.

This section is about the **cost of instrumentation**, not its adequacy — whether enough
observability exists to support a `Confirmed`-grade finding elsewhere is Phase 1's
observability inventory (`methodology/discovery.md`), a different question with the opposite
failure direction: too little instrumentation limits confidence; too much or too unbounded
instrumentation is itself a resource finding.

---

## 9. What to look for in a review

- For each resource: what is the limit, what consumes it, what happens at the limit, is it
  observable, and is it consistent with the other limits?
- Connection arithmetic — pool × workers × instances versus the datastore limit.
- Container memory limit versus configured heap and cache sizes.
- CPU quota versus runtime-configured parallelism.
- Anything unbounded: caches, buffers, result sets, queues, concurrency.
- Whether any CPU claim in the review is supported by a profile.
- Whether cost is the real constraint.
- Log volume, metric cardinality, trace sampling rate, and APM agent presence, on the hot
  path specifically.

## 10. What not to conclude

- Do not attribute CPU time without a profile.
- Do not recommend raising a limit without explaining what will then become the constraint.
- Do not treat memory usage as a problem without distinguishing growth, steady state, and
  allocation rate — the three have different fixes.
- Do not assume a resource is saturated because the code that uses it looks inefficient.
- Do not recommend stripping instrumentation to save resources without weighing the evidence
  it would cost the next review — this skill's own findings depend on it existing.
