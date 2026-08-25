# Rubrics — expanded

`SKILL.md` carries the compact tables. This file carries the reasoning behind them and
worked scoring examples. Load it when a score is contested, when two findings feel
equally severe, or when you are about to write `Confirmed`.

---

## 1. Confidence is a claim about evidence, not about belief

Confidence answers one question: **what would someone have to accept to disbelieve this
finding?**

| Level | What a skeptic would have to reject |
|:--|:--|
| `Confirmed` | A measurement you can point at |
| `High` | The plain reading of the code |
| `Medium` | Your assumption about workload or data size |
| `Low` | A scenario you constructed |

### `Confirmed` requires a cited artifact

Acceptable artifacts, in rough order of strength:

- A profile (flame graph, `pprof` output, `py-spy` dump, JFR recording) checked into the
  repo or supplied by the user.
- A query plan (`EXPLAIN`, `EXPLAIN ANALYZE`, `.explain("executionStats")`, `PROFILE`).
- Benchmark output with the benchmark source visible.
- A load-test report with its scenario definition.
- Exported metrics or a trace covering the code path in question.
- A production incident writeup the user provides.

**A test that merely exercises the path is not evidence of its cost.** A CI job that runs
in 4 seconds tells you nothing about production latency.

If the artifact exists but is stale (a plan from a schema two migrations ago), say so and
drop to `High`.

### `High` is for code that speaks for itself

`High` is available when the behavior is a property of the code, independent of workload.
Examples that legitimately earn `High`:

- A query issued inside a loop whose iteration count comes from a prior query result.
- A synchronous filesystem, DNS, crypto, or HTTP call inside an async event-loop handler.
- A `SELECT` with no `LIMIT` against a table with an insert-only migration history.
- A lock held across an I/O call.
- A pool sized 5 in a service configured for 64 concurrent workers.

Note the pattern: each is *structurally* wrong regardless of how much traffic arrives.
Traffic changes the severity, not the truth of the finding.

### `Medium` requires the assumption to be written down

`Medium` is honest and common. It is not a weak `High`; it is a `High` with a dependency.
The dependency belongs in `Conditions`, phrased so the user can confirm or refute it in
one sentence:

> Conditions: Matters if `orders` exceeds roughly 10⁵ rows. The repo has no row-count
> evidence; the table is written on every checkout and has no retention or archival job
> (`migrations/0007_orders.sql`), so unbounded growth is the default expectation.

That is a `Medium` done properly. Compare:

> Conditions: Under high load.

That is not a condition. It is a shrug. Findings whose `Conditions` read like this should
not be reported.

### `Low` is framed as a question

If the only support is a scenario you imagined, either frame it as a question for the
user, or drop it. `Low` findings that read as assertions are the main source of noise in
automated performance reviews.

### The workload cap

If workload is unknown and the finding's impact depends on workload, the confidence
ceiling is `Medium` — no matter how confident the code reading is. This is the rule that
keeps an unanswered workload interview from silently inflating the whole report.

The cap does not apply to findings that are workload-independent: a missing timeout is a
correctness-adjacent defect at any traffic level.

---

## 2. Severity is a function, not a feeling

Score the four factors first, then read off the level. Writing the factors down prevents
the drift where everything becomes `High`.

### Position

| Value | Meaning |
|:--|:--|
| `critical-path` | A user or caller is blocked waiting for this |
| `async` | Runs outside the request, but shares resources with it |
| `offline` | Batch, nightly, or one-off; shares little |

An async path that saturates a shared resource is effectively on the critical path. A
background job that exhausts the connection pool blocks every request. Score position as
`async` but raise blast radius to `system-wide` — the matrix then does the right thing.

### Frequency

Per request > per item > per batch > rare. Multiply mentally: a 2 ms cost per item, 500
items per request, is a 1-second per-request cost. State the multiplication in `Impact`
and label it a derivation.

### Growth

The most important factor and the most often skipped. Ask: as data grows 10×, does this
cost grow 1×, 10×, or 100×?

| Growth | Typical source |
|:--|:--|
| O(1) | Fixed work, indexed point lookup, bounded cache |
| O(log n) | Index traversal, balanced tree |
| O(n) | Full scan, unbounded result set, per-row work in application code |
| O(n·m) | N+1 queries, nested loop over two collections, fan-out per item |
| O(n²)+ | Nested scans, cartesian joins, repeated re-sorting, in-memory dedupe of a scan |

Superlinear growth on the critical path is the strongest single signal of `Critical`,
because it means the system has a cliff rather than a slope.

### Blast radius

`endpoint` → `service` → `system-wide`. `system-wide` means it touches something shared:
a connection pool, an event loop, a global lock, the primary database, a shared cache, a
single-partition queue key.

Blast radius is what separates a slow endpoint from an outage.

### Reading off the level

- **`Critical`** — `critical-path` AND (`O(n·m)`+ growth OR `system-wide` saturation).
- **`High`** — `critical-path` + per-request + `O(n)` growth in data likely to grow; or
  `async` + `system-wide`.
- **`Medium`** — real waste on the critical path but bounded; or high-frequency off it.
- **`Low`** — bounded, local, small constant.
- **`Informational`** — no current or projected impact; recorded for context.

---

## 3. Worked examples

### Example A — N+1 in a list endpoint

Loop over `orders` from a prior query; one `customer` lookup per order. No pagination
cap in the handler.

- Position: `critical-path` — a GET the client waits on.
- Frequency: per item, per request.
- Growth: `O(n·m)` — queries scale with result-set size, which scales with data.
- Blast radius: `system-wide` — each query occupies a pooled connection; a large result
  set can hold many, starving other requests.

→ **Severity `Critical`.** Code is unambiguous → **Confidence `High`.** → **P0.**

Note the severity came from growth × blast radius, not from "N+1 is bad".

### Example B — the same N+1, with a hard page cap of 20

Same code, but the handler enforces `limit=20` maximum.

- Growth becomes bounded: at most 21 queries, regardless of data size.
- Blast radius drops to `endpoint`.

→ **Severity `Medium`** (waste on the critical path, bounded), Confidence `High`, → **P2**,
tagged `quick-win` if a join or batched load is a small change.

The identical code pattern scored three priority levels apart. That difference is the
methodology working.

### Example C — a JSON serializer choice

A handler uses the standard library JSON encoder rather than a faster third-party one.

- Position: `critical-path`. Frequency: per request. Growth: `O(n)` in payload size.
  Blast radius: `endpoint`. Payload sizes unknown; no profile in the repo.

→ Severity `Low` (bounded, local, constant factor) unless payloads are large.
Confidence capped at `Medium` — impact depends on unmeasured payload size. → **P3.**

And per the no-cargo-cult rule, this should probably be reported as an
`Informational` observation with a measurement suggestion, not a recommendation. If you
cannot say what fraction of request time is spent encoding, you cannot say the swap is
worth its dependency.

### Example D — missing timeout on an outbound HTTP call

No timeout configured on a client used in a request handler.

- Position: `critical-path`. Frequency: per request. Growth: O(1) — but the failure mode
  is not about growth. Blast radius: `system-wide` — a hung upstream ties up a worker
  slot per in-flight request until the process runs out.

→ **Severity `Critical`** via shared-resource saturation. Confidence `High` — the absence
of a timeout is visible in the code and independent of workload, so the workload cap does
not apply. → **P0**, very likely `quick-win`.

### Example E — nothing found

A small service: every query is a point lookup on a primary key, all handlers are thin,
pools are sized consistently with the worker count, timeouts are set everywhere.

→ **No findings.** Report the unknowns (no profiling data, no load test, unknown traffic),
state that static analysis found no material bottleneck, and give the three measurements
that would change that conclusion.

This is a successful review. Do not go looking for something to say.

---

## 4. Priority derivation and the quick-win tag

Priority comes from the matrix in `SKILL.md`. It measures **impact adjusted for how sure
you are**, and nothing else.

Effort belongs in the action plan's sequencing, not in the priority. A P2 that takes ten
minutes may be scheduled before a P1 that takes two weeks — say that explicitly in the
plan, and tag the finding `quick-win`. Do not promote it to P1.

Tags:

| Tag | Meaning |
|:--|:--|
| `quick-win` | Small, low-risk change relative to its impact |
| `scalability-risk` | Not a current bottleneck; becomes one at plausible future scale |
| `needs-measurement` | The finding is real but the impact cannot be sized without data |

`scalability-risk` is how you keep the current-bottleneck / future-risk distinction
visible without inflating severity. A `scalability-risk` finding is scored on today's
conditions and flagged for tomorrow's.

---

## 5. Self-check before publishing a finding

- Can I point at the line that proves this?
- Have I written all four severity factors, or did I guess the level?
- Does `Conditions` say something a user could confirm or refute?
- Does the priority match the matrix?
- If the user asked "what would you measure to check you're right", do I have an answer?
- Would I still report this if I were being scored on false positives?

If any answer is no, fix the finding or drop it.
