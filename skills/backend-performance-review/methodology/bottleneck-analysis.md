# Bottleneck analysis

Discovery told you what exists. Workload told you what matters. Critical paths told you
where to look. This phase turns observations into ranked findings — and, just as
importantly, discards the ones that do not survive scrutiny.

---

## 1. The four questions

Every performance problem in a backend reduces to one of these. Ask them in order; they
are roughly ordered by how often they are the answer.

### Where is work repeated?

The single most productive question in backend review. Repetition is cheap to spot
statically and expensive at runtime.

- A query per item in a loop (N+1), including the ORM-lazy-loading variety where the loop
  is invisible in the code you are reading.
- The same value fetched multiple times in one request.
- Serialization and deserialization of the same object more than once.
- Validation, authorization, or parsing repeated per layer.
- Recomputation of something derivable once per request.
- A retry that repeats successful work as well as failed work.

### Where is work unbounded?

Unbounded work is the difference between a slow endpoint and an outage, because the cost
is set by data you do not control.

- Queries without `LIMIT`, endpoints without maximum page size.
- Loading a full collection to count, filter, or aggregate in application code.
- Unbounded fan-out — one call per element of a user-supplied list.
- Unbounded concurrency — spawning a task per item with no semaphore.
- Unbounded buffering — accumulating a whole response, stream, or file in memory.
- Recursive traversal with no depth cap.

### Where is work serialized that need not be?

- Independent I/O calls issued sequentially.
- A lock held across an I/O call.
- A single-threaded stage in an otherwise parallel pipeline.
- A queue with one consumer, or one partition key carrying most of the traffic.
- Synchronous work on an event loop, blocking every other task on that loop.

Be careful here: parallelism is on the cargo-cult list for good reason. Parallelizing work
that is not on the critical path, or that contends for the same downstream resource,
converts a latency problem into a saturation problem. Only recommend it when the calls are
genuinely independent and the downstream can absorb the concurrency.

### Where are resources saturated or misconfigured?

- Pool size inconsistent with worker count or with database `max_connections`.
- Container memory limit below the runtime's heap configuration.
- CPU limit that throttles a runtime configured for more parallelism than it can use.
- File descriptor limits versus expected connection count.
- Thread pool sized for a different workload shape than the one it serves.

Configuration inconsistencies are excellent findings: they are objectively checkable from
files in the repository, which makes them `High` confidence without any runtime data.

---

## 2. Distinguish the bottleneck from its symptoms

A bottleneck is where the constraint actually is. Symptoms appear elsewhere and are more
visible, which is why reviews that chase symptoms produce long, useless reports.

| Symptom | Frequent underlying constraint |
|:--|:--|
| High application latency | Downstream I/O, or queueing for a pool |
| Pool exhaustion | Slow queries holding connections, or transactions held across I/O |
| High CPU | Serialization, compression, crypto, or an algorithmic issue — profile to tell |
| Memory growth | Unbounded buffering or caching, or a retention leak |
| Queue lag | Consumer throughput below producer rate, or a poison-message retry loop |
| Timeouts firing | The dependency is slow, or the timeout budget is misallocated |
| Cache misses | Wrong key granularity, too-short TTL, or a stampede after invalidation |

Trace to the constraint before writing the finding. Three symptoms of one cause must be
merged into one finding, with the cause named — not filed as three.

**Merging is mandatory, not stylistic.** A report listing "N+1 in orders", "pool
exhaustion under load", and "slow /orders endpoint" as three findings, when they are one
problem, misrepresents both the count and the fix.

---

## 3. Discard aggressively

Most candidate observations should not become findings. Apply these tests:

**The workload test.** Under the workload model, does this cost enough to notice? A 50 μs
inefficiency on a path called twice a day is not a finding.

**The critical-path test.** Is it on a path someone waits for, or does it contend with one?
If neither, it is `Informational` at best.

**The evidence test.** Can you point at the line? If the argument requires a chain of
assumptions, it is `Low` confidence — and `Low` confidence findings usually should not be
reported as findings at all. Report them as questions.

**The alternative-explanation test.** Is there a reason the code is this way? A repeated
query may be memoized upstream. A missing index may exist in a migration you have not read.
Check before asserting. Reviews lose credibility on exactly these mistakes.

**The counterfactual test.** If this were fixed, what would measurably improve, and by
roughly how much? If you cannot answer even in relative terms, you do not understand the
finding well enough to recommend it.

**The intent test.** Would a competent engineer who knows this system agree, or would they
explain in one sentence why it is fine? Try to construct that sentence first.

---

## 4. Scoring

Score Severity and Confidence per `rubrics.md`, write down all four severity factors, and
derive Priority from the matrix. Do not choose the priority and reverse-engineer the
scores; that defeats the purpose of having a rubric.

Two rules that are easy to get wrong:

- If workload is unknown and the finding depends on workload, confidence caps at `Medium`
  regardless of how obvious the code reading is.
- Background work that saturates a shared resource is scored `position: async` with
  `blast radius: system-wide`. The matrix handles it correctly from there.

---

## 5. Recommendations that survive review

A recommendation is only complete when it addresses the principle rather than the symptom,
and when it states its own cost.

**Address the cause.** Adding a cache in front of an N+1 hides the query count and
introduces invalidation. Batching the query removes it. Prefer the removal of work over the
concealment of work.

**Prefer the cheapest intervention with meaningful impact**, in roughly this order:

1. Remove the work (fix N+1, eliminate duplicate calls, drop unused fields).
2. Bound the work (pagination, limits, timeouts, batch caps, semaphores).
3. Move the work (defer to background, precompute, stream instead of buffer).
4. Make the work cheaper (better index, better query, better data structure).
5. Cache the work — only with hit-rate reasoning, an invalidation strategy, and an
   explicit statement of acceptable staleness.
6. Add capacity — last, and only with the saturation evidence that justifies it.

**Always state trade-offs.** Every optimization costs something: memory, complexity,
consistency, operational surface, a new failure mode. A recommendation with no stated cost
has not been thought through, and reviewers notice.

**Never recommend from the cargo-cult list without the analysis.** Caching, Redis,
indexes, async, parallelism, sharding, denormalization, microservices, more servers, a
different language. Each of these is sometimes right. None is right by default, and each
carries costs that are frequently larger than the problem.

Indexes in particular are worth naming: they are not free. Each one adds write cost,
storage, and planner surface. "Add an index" is a trade, and the write-side cost belongs in
`Trade-offs`.

---

## 6. When you find nothing

This happens, and it is a legitimate outcome. Do not treat an empty findings list as a
failed review.

Deliver instead:

- A plain statement that static analysis found no material bottleneck.
- The workload model, including what remains unknown.
- The observability gaps — what cannot currently be measured.
- The two or three measurements that would change the conclusion.
- Any `scalability-risk` observations, clearly marked as not-current-problems.

A short, honest report is more valuable than a long speculative one, and far more likely to
be trusted the next time.

---

## 7. Output of this phase

A findings list, deduplicated and scored, ordered by priority, with the output budget
applied: full format for the top 10–15, a ranked table for the remainder. Plus the
discarded-candidates note — a line or two on what you considered and rejected, which
demonstrates the review had a filter.
