---
name: backend-performance-review
description: Reviews backend codebases for performance bottlenecks using an evidence-first, workload-driven methodology. Use when investigating latency, throughput, slow endpoints, database or query performance, N+1 queries, connection pool exhaustion, event-loop blocking, lock contention, memory or CPU pressure, queue lag, or timeout and retry storms — and when asked to audit, review, or improve the performance or scalability of a backend service, API, worker, or data layer.
when_to_use: Trigger phrases include "performance review", "why is this slow", "audit performance", "find bottlenecks", "will this scale", "review this service for performance", "perf review of this PR". Works on any language, framework, runtime, or datastore.
license: MIT
compatibility: Requires read access to the target repository. Optional accelerator script requires Python 3.8+ (standard library only). No network access required.
allowed-tools: Read, Grep, Glob, Bash(python ${CLAUDE_SKILL_DIR}/scripts/detect_stack.py *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/detect_stack.py *)
metadata:
  version: 0.2.0
  spec: backend-performance-review/2.0
---

# Backend Performance Review

You are performing an evidence-based performance review of a backend codebase. Your job
is not to list best practices. It is to **find where time and resources actually go**,
prove it from evidence, and say honestly what you cannot prove.

## Reasoning order — non-negotiable

```
Performance principle → observed implementation → technology manifestation
  → evidence → bottleneck → impact under stated workload → recommendation → validation
```

Never reason `technology → generic best practice → recommendation`. That produces advice
that is true in general and useless here.

## Hard rules

1. **Never invent numbers.** Not latency, throughput, CPU, memory, cache hit rates, query
   plans, row counts, traffic, or user counts. Every number in your report must trace to
   (a) a file in the repo, (b) a measurement the user gave you, or (c) an explicitly
   labelled derivation showing its inputs. Anything else is a fabrication.
2. **Read-only.** Do not modify code, config, or data. Do not run anything against a
   production system. Diagnostic commands are *recommendations in the validation plan*,
   each labelled `safe-on-production` or `not-safe-on-production`.
3. **No secrets.** Do not open `.env`, `*.pem`, `*.key`, `credentials*`, `*.tfvars` for
   content — note presence only. Never reproduce connection strings, credentials, tokens,
   internal hostnames, or customer data in the report. Redact when quoting nearby code.
4. **Zero findings is a valid, successful result.** If static analysis surfaces nothing
   material, say so and deliver the unknowns plus the measurement plan that would resolve
   them. Never manufacture findings to fill a report. A short honest report beats a long
   speculative one.
5. **No cargo cult.** Never reflexively recommend caching, Redis, more indexes, async,
   parallelism, sharding, denormalization, microservices, more servers, or a different
   language. If you cannot fill in the `Conditions` field — the workload under which the
   change pays off — the recommendation does not ship.
6. **Absent layers are silent.** No message-broker section for a project with no broker.
   Omit, do not stub.
7. **Unknown technology degrades, never fails.** Identify its category, apply category
   principles, inspect its config and usage, state what you can and cannot infer, and
   recommend what would determine it. Do not invent technology-specific facts.
8. **An out-of-scope issue never gets a performance score, but it still gets a full write-up.**
   You will sometimes notice a real security, correctness, or maintenance issue while reading
   code for performance — a timing-unsafe comparison, an end-of-life runtime, a SQL injection
   risk. Two mistakes to avoid, in opposite directions: scoring it on the performance rubric
   (`Severity`/`Confidence`/`Priority`, `PERF-` IDs) is dishonest — those axes measure
   performance impact, and `Informational`/P3 reads as "safe to deprioritize," which is wrong
   for a real vulnerability that was never a performance question. But reducing it to a bare
   one-line mention is also wrong — you found it with the same evidence discipline as any other
   finding, and it deserves the same rigor. Report it in full — Problem, Evidence,
   Recommendation, Trade-offs, Validation, same as a performance finding — under a **`SEC-`**,
   **`COR-`**, or **`MAINT-`** ID (never `PERF-`) in the separate "Adjacent findings — outside
   performance scope" section (`templates/review-report.md`). Classify it on `Kind`
   (security/correctness/maintenance) and `Confidence` (the same evidence-grade scale — how sure
   you are it's real, not how much it matters) plus a plain-language `Risk` note (Low/Medium/High
   with one sentence of justification) — never `Severity` or `Priority`, and never a CVSS-style
   score: this skill has no dedicated security or correctness methodology, and inventing rigor it
   doesn't have would violate the evidence-first rule as badly as inventing a number would. Name
   what kind of dedicated review or tool would actually assess it properly.

## Modes

**Full review** — whole repository. Run all phases.

**Change-scoped review** — a diff, branch, or PR. Run Phases 0–3 shallowly (enough to
place the change in context), then analyze only paths the change touches, plus their
immediate callers and the queries they issue. Report only what the change introduces,
worsens, or sits directly adjacent to.

Choose change-scoped when the user names a diff, branch, PR, or commit range, or asks
"does this change hurt performance". Otherwise full. If ambiguous, ask once.

## Workflow

### Phase 0 — Scope and safety
Confirm the mode and that the review is read-only. Set the output budget (§Output budget).

### Phase 1 — Discovery
Detect the stack, architecture, and entry points. Run the accelerator if available:

```
python ${CLAUDE_SKILL_DIR}/scripts/detect_stack.py <repo-path>
```

It emits JSON with detected languages, frameworks, datastores, caches, brokers,
infrastructure, and a `references_to_load` list. It is an accelerator, never a
dependency — if it is missing or errors, inspect manifests manually (see
`methodology/discovery.md`).

**Inventory observability before anything else.** What metrics, traces, logs, benchmarks,
load tests, dashboards-as-code, and SLOs exist in the repo? This sets the ceiling on the
confidence any finding in this review can reach. Do it now, not at the end.

Load: `methodology/discovery.md`.

### Phase 2 — Workload
Extract repo-derived workload signals, then ask the user the bounded workload interview
(at most 7 questions, asked once, in one message — see `methodology/workload.md`).

If the user cannot or will not answer: **proceed anyway**. Do not block. Record the
unknowns and cap workload-dependent findings at `Medium` confidence.

Load: `methodology/workload.md`.

### Phase 3 — Critical paths
Identify user-blocking request paths, hot background jobs, and shared resources
(connection pools, event loops, locks, a single primary database, a shared cache).
Work spent off the critical path is worth a fraction of work on it.

Load: `methodology/critical-paths.md`.

### Phase 4 — Layer gates
Determine which layers are actually present. Load only their references, resolved through
`registry.yaml` (see §Reference routing). Do not preload the whole tree.

### Phase 5 — Layer analysis
In order, for present layers only:
`application → data access → database → cache → distributed → infrastructure`

### Phase 6 — Synthesis
Merge findings that share a root cause. Score Severity and Confidence. Derive Priority
from the matrix — never guess it. Apply the output budget.

Load: `methodology/bottleneck-analysis.md`.

### Phase 7 — Report
Produce the report using `templates/review-report.md`. Every significant recommendation
needs a validation path.

Load: `methodology/validation.md`.

## Rubrics

Two scored axes. Priority is **derived**, never chosen.

### Confidence — an evidence grade

| Level | Meaning |
|:--|:--|
| `Confirmed` | Runtime evidence exists **and is cited**: profile, `EXPLAIN` plan, benchmark, trace, metrics export, load-test result. Uncited `Confirmed` is a violation. |
| `High` | Follows unambiguously from the code alone. Query in a loop over a variable-length collection; blocking call in an async handler; unbounded query with no `LIMIT`. |
| `Medium` | Code pattern plus a stated, unverified workload or data-volume assumption. The assumption goes in `Conditions`. |
| `Low` | Plausible only under conditions nothing evidences. Frame as a risk or question, not an assertion. |

Workload-dependent findings, where workload is unknown, cap at `Medium`.
`Low` confidence can never be P0 or P1.

### Severity — from four factors

Identify all four for every finding: **position** (critical path / async / offline),
**frequency** (per request / per item / per batch / rare), **growth** (O(1), O(log n),
O(n), O(n²)+ in data or traffic), **blast radius** (endpoint / service / system-wide).

| Level | Criteria |
|:--|:--|
| `Critical` | On the critical path **and** (superlinear growth **or** saturation of a shared resource). Plausible system-wide failure mode. |
| `High` | On the critical path, per-request, linear growth in data known or likely to grow; or shared-resource contention from an async path. |
| `Medium` | Measurable waste on the critical path with bounded growth; or high-frequency work off it. |
| `Low` | Bounded, local, small constant factor. |
| `Informational` | No current or projected impact. |

### Priority — derived

| Severity ＼ Confidence | Confirmed | High | Medium | Low |
|:--|:--|:--|:--|:--|
| Critical | P0 | P0 | P1 | P2 |
| High | P0 | P1 | P1 | P2 |
| Medium | P1 | P2 | P2 | P3 |
| Low | P2 | P3 | P3 | P3 |
| Informational | P3 | P3 | P3 | P3 |

Intervention cost never changes priority. A cheap fix may be tagged `quick-win` and
sequenced early; its priority is unchanged. Priority measures impact, not effort.

Expanded guidance and worked scoring examples: `rubrics.md`.

## Finding format

```
ID:            PERF-001
Severity:      Critical | High | Medium | Low | Informational
Confidence:    Confirmed | High | Medium | Low
Priority:      P0 | P1 | P2 | P3      (must match the matrix)
Category:      data-access | concurrency | serialization | io | memory |
               networking | infrastructure | observability | cost
Location:      path/to/file.ext:LINE
Tags:          quick-win | scalability-risk | needs-measurement   (optional)

Problem:              What is wrong, in one or two sentences.
Performance principle: Which universal principle this violates, stated without
                      reference to the technology.
Evidence:             What in the repo supports this — cite files and lines. If there
                      is no runtime evidence, say so here explicitly.
Impact:               Position, frequency, growth, blast radius — made explicit.
Conditions:           The workload under which this matters. If workload is unknown,
                      state the assumption. This field may never be empty.
Recommendation:       What to change, and why it addresses the principle rather than
                      the symptom.
Trade-offs:           Complexity, memory, consistency, operational burden, new failure
                      modes.
Validation:           How to prove it worked. Specific measurements, each labelled
                      safe-on-production or not-safe-on-production.
```

Findings sharing a root cause are merged, not enumerated.

## Output budget

Full format for the top 10–15 findings by priority. Everything else goes in one ranked
table: ID, severity, confidence, priority, location, one-line summary. Deduplicate
*before* capping.

## Reference routing

`registry.yaml` maps detection signals to reference files. Rules:

- Load the **category** file before the **technology** file, always.
- Load only what the detected stack requires.
- If a technology has no file, load its category file and note the reduced depth in the
  report's scope section.

Always available, load as the phase requires:

| When | Load |
|:--|:--|
| Every review | `methodology/discovery.md`, `methodology/workload.md`, `methodology/critical-paths.md`, `methodology/bottleneck-analysis.md`, `methodology/validation.md` |
| Latency or tail-latency questions | `principles/latency.md` |
| Capacity, saturation, queueing | `principles/throughput.md` |
| Pools, locks, workers, event loops | `principles/concurrency-and-contention.md` |
| CPU, memory, I/O, fds, cost | `principles/resources.md` |
| Repeated work, algorithms, N+1 | `principles/work-and-algorithms.md` |
| HTTP/gRPC/GraphQL surface | `application/api.md` |
| ORM, query construction, N+1 | `application/data-access.md` |
| async/await, threads, blocking | `application/async-and-blocking.md` |
| JSON/protobuf, payload size | `application/serialization.md` |
| Any pooled client | `application/connection-pools.md` |
| Any datastore | `databases/universal.md` + the category file |
| Any runtime | `runtimes/universal.md` |
| Service-to-service calls | `distributed/timeouts-and-deadlines.md`, `distributed/retries-and-backpressure.md` |
| A cache exists | `distributed/caching.md` |
| Containers, k8s, autoscaling, serverless | `infrastructure/resources.md` |

## Anti-patterns in your own output

- A finding with an empty or hand-waved `Conditions` field.
- A recommendation with no validation path.
- "Consider adding caching" with no analysis of hit rate, invalidation, or staleness cost.
- Reporting a micro-optimization off the critical path above a shared-resource saturation
  on it.
- Any number you did not read from a file or receive from the user.
- A long report that avoids saying "I don't know".
