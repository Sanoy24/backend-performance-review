# Validation

A recommendation without a validation path is a guess with formatting. This phase makes
every significant recommendation falsifiable.

Validation also protects the user from *you*: a static review produces hypotheses, and the
measurement is what turns a hypothesis into a fact.

---

## 1. Every validation needs five parts

```
Baseline:     what to measure before changing anything, and how
Measurement:  the specific metric, at the specific place
Expectation:  the direction of change, and roughly how much — stated as a prediction
Safety:       safe-on-production | not-safe-on-production
Falsifier:    what result would mean the finding was wrong
```

The **falsifier** is the part most often skipped and the part that makes the review
honest. If no observation could refute the finding, it is not an engineering claim.

The **expectation** must be a prediction, not a promise. "Query count on `/orders` should
drop from one-per-row to two total" is a prediction derived from the code. "Latency will
improve 40%" is a fabricated number unless you measured it — and per the evidence rules,
you did not.

---

## 2. Measure the baseline first

Without a baseline there is no way to distinguish a fix from a coincidence. Two rules:

- Capture the baseline under conditions comparable to the change — same data volume, same
  concurrency, same warm/cold state.
- Prefer the metric closest to the finding. For an N+1, count queries. For pool
  exhaustion, measure pool wait time. End-to-end latency is noisy and will hide a real
  improvement behind unrelated variance.

If the repository is uninstrumented, adding the measurement *is* the first recommendation,
and it should be sequenced before any optimization. Optimizing an unmeasured system is how
teams accumulate changes nobody can justify later.

---

## 3. Pick evidence appropriate to the claim

Match the tool to the question. Naming a tool without saying what it would prove is noise.

| Question | Evidence that answers it |
|:--|:--|
| How many queries does this path issue? | Query log, ORM query counter, APM span count, a test asserting query count |
| Why is this query slow? | Query plan (`EXPLAIN` family), engine-specific profiler |
| Where does CPU go? | Sampling profiler, flame graph |
| Where does memory go? | Heap profiler, allocation profiler, RSS over time |
| Is the event loop blocked? | Event-loop lag metric, blocking-call detector |
| Are we waiting on a pool? | Pool wait-time and in-use metrics |
| Where does wall-clock time go across services? | Distributed trace |
| What happens under load? | Load test with a defined scenario |
| Is the tail bad, or the median? | Percentile metrics, not averages |

**Averages hide tail problems.** If the concern is p99, an average is not evidence.

### Tool families by runtime

Recommend by capability, and never assume a tool is installed. Say what it would show.

| Runtime | Sampling profiler | Heap/allocation | Notes |
|:--|:--|:--|:--|
| Python | `py-spy` (no code change, attaches to a running process), `cProfile` | `tracemalloc`, `memray`, `scalene` (CPU+memory) | `py-spy` is usually the right first tool because it needs no restart |
| Node.js | `--cpu-prof`, `node --inspect` + DevTools, `0x` | `--heap-prof`, heap snapshots | Also check event-loop lag before profiling CPU |
| Go | `net/http/pprof`, `go tool pprof` | pprof heap and allocs profiles | Often already compiled in; check before recommending changes |
| JVM | async-profiler, JFR | JFR, heap dumps | JFR has low overhead and is usually production-acceptable |
| .NET | `dotnet-trace`, `dotnet-counters` | `dotnet-gcdump` | |
| Rust | `perf` + flamegraph, `pprof-rs` | `dhat`, heaptrack | Ensure debug symbols in the profiled build |
| Any (Linux) | `perf record` / flame graphs | — | Requires host access and appropriate permissions |

Load testing: k6, Locust, Gatling, JMeter, Artillery, `wrk`, `oha`. The tool matters less
than the scenario: a load test that does not resemble production traffic validates nothing.

---

## 4. Production safety — label every command

Classify every suggested command. This is a hard rule, not a courtesy.

**Generally safe on production**

- `EXPLAIN` without execution (plan only).
- Reading engine statistics views and slow-query logs.
- Reading existing metrics, traces, and dashboards.
- Sampling profilers with low overhead, at low sampling rates, when the team accepts it.
- Counting queries in a staging environment with production-shaped data.

**Not safe on production without explicit approval**

- `EXPLAIN ANALYZE` on any statement with side effects — it executes the statement.
- Load tests against production.
- Heap dumps (pause the process, may contain sensitive data).
- Adding indexes without a concurrent/online build path.
- Enabling verbose query logging on a busy system.
- Anything that adds sustained load to a system already suspected of saturation.

When a command is unsafe, give the safe alternative: run it on a replica, on staging with
representative data, or during a maintenance window.

The skill never runs these. It writes them down for the user to run.

---

## 5. Validate the mechanism, not just the outcome

An improvement that appears for the wrong reason will regress silently. Where possible,
confirm the mechanism changed:

- Query count actually dropped (not just latency, which could be cache warmth).
- The plan actually uses the new index (not a coincidental data change).
- Pool wait time actually fell (not just fewer concurrent requests during the test).
- The blocking call actually moved off the event loop.

Mechanism-level validation is what allows a regression test. A latency assertion is flaky;
a query-count assertion is not.

---

## 6. Guard against regression

Once validated, keep it validated. Suggest the cheapest durable guard available:

- A test asserting query count for the path — the single most effective guard against N+1
  regressions.
- A test asserting a bounded result size or a required `LIMIT`.
- A metric plus an alert threshold on the mechanism-level signal.
- A load-test scenario in CI for genuinely critical paths, run on a schedule rather than
  per-commit if it is expensive.
- A lint or architecture rule where the pattern is mechanically detectable.

Without a guard, the same finding returns in six months. Say so in the report.

---

## 7. Validation plan format

For each significant recommendation:

```
Finding:      PERF-001
Baseline:     Count queries issued by GET /orders in staging with production-shaped
              data. Method: ORM query logging, or an APM span count.
              [safe-on-production: yes, if using existing APM data]
Change:       Batch the per-row customer lookup into a single keyed query.
Measurement:  Query count for the same request, same data.
Expectation:  Query count falls from (1 + page size) to 2, independent of page size.
              Latency should improve materially where the path is query-bound; the
              size of that improvement is unknown until measured.
Falsifier:    If query count drops but latency does not change, the path was not
              query-bound and this finding's impact was overstated — reprioritize.
Guard:        Test asserting the query count for this route.
```

Note what the `Expectation` does and does not claim. The query-count prediction is derived
from code and is safe to assert. The latency claim is explicitly unquantified. That
distinction is the evidence rule applied to your own recommendations.
