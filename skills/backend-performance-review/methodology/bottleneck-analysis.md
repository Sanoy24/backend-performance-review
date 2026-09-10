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

Give the merged finding a root-cause id and name the cause in it. Recording the merge is
what makes it auditable — "three symptoms, one problem" is a much stronger statement than
three separate entries, and a reader can check it.

### Issues that are mild alone and severe together

The opposite error is splitting problems that *interact*. Independently moderate issues
compound when they share a resource:

```
per-request extra queries        moderate on its own
+ a connection pool sized small   moderate on its own
+ traffic arriving in bursts      moderate on its own
= pool exhaustion under burst     severe
```

Where two findings feed the same constrained resource, say so explicitly and score the
combination on its real blast radius rather than scoring each in isolation. This is not
license to inflate: the interaction has to be mechanical and stateable — *this* consumes
*that* resource, which *that other thing* also consumes — not merely "both are about the
database."

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

## 4. Search for counter-evidence

§3 discards weak candidates. This step attacks the ones that survived.

The asymmetry is the problem. Having formed a hypothesis, you will naturally find more of
what supports it — a second query in the same file reads as confirmation, and the reading
that would have refuted it never gets attempted. The discipline is to go looking for the
refutation *on purpose*, before scoring, and to record what you found.

**What to look for, by what you think you have found:**

| Hypothesis | The counter-evidence that would deflate it |
|:--|:--|
| Query in a loop | A hard cap on the collection size; the ORM batching this into one round trip; the result memoized upstream; the loop running over a fixed configuration list, not user data |
| Unbounded query | A `LIMIT` applied by the caller or by a framework default; a table whose row count is structurally small; pagination in the layer above |
| Blocking call in async code | The call being made once at startup, not per request; the runtime already offloading it to a thread pool |
| Missing index | An index created in a migration you have not read; a table small enough for a scan to be correct; a query that never filters on that column |
| Pool exhaustion risk | A pool sized against a documented limit; short-lived connections; a concurrency cap upstream |
| Serialization cost | Payloads bounded by a schema; compression handled at the proxy; the endpoint being internal |

**Also check, for any finding:** is this path actually reachable? Is it admin-only, or behind
a feature flag that is off? Is it dead code? Is it a test fixture or a seed script? A finding
on an unreachable path is a false positive no matter how correct the mechanism is.

**What you find must change something.** Record the effect, then apply it:

| What you found | Effect |
|:--|:--|
| The mechanism cannot occur as described | `refutes` — this never becomes a finding. It goes in "Considered and not reported" |
| The mechanism holds but is bounded | `bounds-impact` — reduce **Severity**; the growth or blast radius you assumed was wrong |
| The mechanism holds but the reading is less certain | `lowers-confidence` — reduce **Confidence**; the code is more ambiguous than you first read it |
| Looked, found nothing | `no-effect` — scores stand, and the record shows the finding was tested rather than merely asserted |

Severity and Confidence move for different reasons, and the distinction survives here:
counter-evidence that *bounds* a problem makes it smaller, while counter-evidence that
*obscures* it makes you less sure. Do not collapse the two.

**Recording it is not optional.** A finding with no counter-evidence section is
indistinguishable from one where nobody looked, and the reader has no way to tell which they
are holding. "Searched, found nothing" is a complete and respectable answer. Silence is not.

If new evidence arrives later — the user answers a workload question, a trace appears —
revisit the findings it bears on and revise. Confidence is a running assessment, not a
verdict issued once at the moment of discovery.

---

## 5. Separate what is wrong from what should change

Establish the problem completely before considering the fix.

Reversing that order corrupts the analysis, and it does so invisibly. An agent that has
decided the answer is a cache will find the evidence that justifies a cache: repeated reads
look hotter, the cost of the underlying query looks higher, and the counter-evidence search
above gets quietly less rigorous because the conclusion is already comfortable. The finding
ends up shaped by the recommendation instead of the other way round.

Two practical consequences:

- Do not name a technology while establishing the mechanism. "Query count grows with result
  size" is a finding. "This needs Redis" is a recommendation wearing a finding's clothes.
- When the fix seems obvious immediately, treat that as a signal to slow down rather than
  speed up. The obvious fix is usually the one that masks the work — a cache in front of a
  query nobody needed to run.

---

## 6. Scoring

Score Severity and Confidence per `rubrics.md`, write down all four severity factors, and
derive Priority from the matrix. Do not choose the priority and reverse-engineer the
scores; that defeats the purpose of having a rubric.

Two rules that are easy to get wrong:

- If workload is unknown and the finding depends on workload, confidence caps at `Medium`
  regardless of how obvious the code reading is.
- Background work that saturates a shared resource is scored `position: async` with
  `blast radius: system-wide`. The matrix handles it correctly from there.

---

## 7. Recommendations that survive review

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

## 8. When you find nothing

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

## 9. Output of this phase

A findings list, deduplicated and scored, ordered by priority, with the output budget
applied: full format for the top 10–15, a ranked table for the remainder. Plus the
discarded-candidates note — a line or two on what you considered and rejected, which
demonstrates the review had a filter.
