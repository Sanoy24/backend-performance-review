# Performance Review Report — template

Fill this in. **Delete every section for a layer that is not present in the system** — an
empty section is worse than no section. Do not stub, do not write "N/A: no message broker
detected".

Guidance in `[brackets]` is for the author and must not appear in the output.

---

# Performance Review: <service or repository name>

**Date:** <date>
**Mode:** Full review | Change-scoped review (<diff / branch / PR>)
**Reviewed by:** Automated performance review, `backend-performance-review` v<version>

---

## 1. Executive summary

### Overall assessment

`[Two to four sentences. What kind of shape is this system in, and what is the single most
important thing to know? If nothing material was found, say so plainly here — that is a
valid and complete result.]`

### Most important findings

`[Up to five, one line each, with ID and priority. If there are none, write "No material
bottlenecks identified by static analysis" and go straight to the unknowns.]`

- **PERF-001 (P0)** — <one line>
- **PERF-002 (P1)** — <one line>

### Highest-risk bottlenecks

`[Where this system is most likely to fail first under growth, whether or not it is failing
now. Distinguish current bottleneck from scalability risk explicitly.]`

### Major unknowns

`[Mandatory. This section may not be empty unless full runtime evidence was supplied. List
what could not be determined and what it would change.]`

- <unknown> — would change: <which findings or rankings>

---

## 2. Scope and method

**Reviewed:** `[Paths, services, layers examined.]`
**Not reviewed:** `[What was excluded, and why. Be specific — this is part of an honest review.]`

**Evidence available:** Well instrumented | Partially instrumented | Uninstrumented
`[What exists: metrics, traces, benchmarks, load tests, query plans, SLOs. This sets the
ceiling on confidence for everything below.]`

**Ranking method:** Runtime data | Structural signals only
`[If structural, say so — the reader needs to know the ranking is inference.]`

**Reference depth:** `[Note any technology carrying only conceptual or generic support, per
the registry tier. e.g. "MySQL analysis uses relational category principles only; no
engine-specific reference exists in this version."]`

---

## 3. Architecture overview

`[Short. Entry points, services, datastores, caches, brokers, deployment model. Shared
resources named explicitly — they determine blast radius throughout the report.]`

| Component | Technology | Version | Support tier | Role |
|:--|:--|:--|:--|:--|

**Shared resources:** `[connection pools, event loops, locks, primary datastore, shared cache]`

---

## 4. Workload model

**Known** `[cited — from repository files or supplied by the user]`

- <fact> — source: <file:line or "user-supplied">

**Assumed** `[stated and unverified; these drive the confidence caps]`

- <assumption> — affects: <finding IDs>

**Unknown** `[would change conclusions if answered]`

- <unknown>

---

## 5. Critical path analysis

`[Ranked. For each: what it does, what it touches, what bounds it, whether it is
instrumented.]`

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|

**Amplification points:** `[per-item costs multiplied by unbounded result sizes, fan-out]`

**Paths deliberately not analyzed in depth, and why:** `[part of an honest review]`

---

## 6. Layer analysis

`[One subsection per PRESENT layer. Delete the rest entirely. Keep each to observations that
lead to findings; do not restate the reference material.]`

### 6.1 Application
### 6.2 API
### 6.3 Data access and datastore
### 6.4 Cache
### 6.5 Distributed communication
### 6.6 Infrastructure
### 6.7 Observability

`[Observability is always assessed — its absence is itself a finding, and it determines
whether any future optimization can be validated.]`

---

## 7. Findings

`[Full format for the top 10–15 by priority. Deduplicate before capping — findings sharing a
root cause are one finding.]`

### PERF-001 — <short title>

| | |
|:--|:--|
| **Severity** | Critical / High / Medium / Low / Informational |
| **Confidence** | Confirmed / High / Medium / Low |
| **Priority** | P0 / P1 / P2 / P3 `[must match the matrix]` |
| **Category** | data-access / concurrency / serialization / io / memory / networking / infrastructure / observability / cost |
| **Location** | `path/to/file.ext:LINE` |
| **Tags** | quick-win / scalability-risk / needs-measurement |

**Problem**
`[One or two sentences.]`

**Performance principle**
`[Which universal principle this violates, stated without reference to the technology.]`

**Evidence**
`[Cite files and lines. If there is no runtime evidence, say so here explicitly — do not
leave it implied.]`

**Impact**
`[All four severity factors made explicit: position, frequency, growth, blast radius. Show
any arithmetic and label it a derivation.]`

**Conditions**
`[The workload under which this matters. If workload is unknown, state the assumption. This
field may not be empty, and "under high load" is not a condition.]`

**Recommendation**
`[What to change, and why it addresses the cause rather than the symptom.]`

**Trade-offs**
`[Complexity, memory, consistency, operational burden, new failure modes. Every optimization
costs something.]`

**Validation**
`[Baseline, measurement, expectation, safety label, falsifier. Predictions derived from code
may be asserted; latency improvements may not be quantified without measurement.]`

---

### Remaining findings

`[Everything beyond the top 10–15, ranked.]`

| ID | Sev | Conf | Pri | Location | Summary |
|:--|:--|:--|:--|:--|:--|

### Considered and not reported

`[One or two lines on candidates that were evaluated and discarded, with the reason. This
demonstrates the review had a filter, and it pre-empts "why didn't you mention X".]`

### Adjacent findings — outside performance scope

`[Real security, correctness, or maintenance issues noticed while reading code for performance.
Full write-up, same rigor as a performance finding — this section exists because a one-line
mention would under-serve something you found with real evidence. But never score it on the
performance axes: Severity/Confidence/Priority/a PERF- ID all measure performance impact, and
"Informational/P3" reads as "safe to deprioritize," which is wrong for a real vulnerability.
Equally, never invent a CVSS-style score or claim comprehensive coverage — this skill has no
dedicated security or correctness methodology, and manufacturing that rigor is the same kind of
dishonesty as inventing a number. Omit this section entirely if nothing qualifies — do not
write "none noticed" as filler.]`

### SEC-001 / COR-001 / MAINT-001 — <short title>

| | |
|:--|:--|
| **Kind** | Security / Correctness / Maintenance |
| **Confidence** | Confirmed / High / Medium / Low `[same evidence-grade meaning as a performance finding — how sure you are it's real]` |
| **Risk** | Low / Medium / High `[a plain-language judgment call, not a security-specific rating scale — justify it in one sentence in Impact below]` |
| **Location** | `path/to/file.ext:LINE` |

**Problem** `[What is wrong, in one or two sentences.]`

**Evidence** `[Cite files and lines, same standard as a performance finding.]`

**Impact** `[Plain language, one or two sentences: why this Risk level. Not position/frequency/
growth/blast-radius — those are the performance-specific factors and don't apply here.]`

**Recommendation** `[What to change.]`

**Trade-offs** `[What the change costs, if anything.]`

**Validation** `[What would confirm the fix. May simply be: run the review this needs.]`

**Would need** `[The dedicated review or tool that would actually assess this properly —
e.g. a security review, a dependency-update pass, a correctness-focused test suite. This
section is not a substitute for that review; it is a pointer toward it.]`

---

## 8. Prioritized action plan

`[Sequenced. Priority measures impact; sequencing may account for effort. Where a lower
priority item is sequenced early because it is cheap, say so explicitly.]`

### P0 — Immediate
### P1 — High priority
### P2 — Medium priority
### P3 — Optimization opportunity

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|

**If only one thing is done:** `[Name it. This is often the most-read line in the report.]`

---

## 9. Validation plan

`[Per significant recommendation. Every command labelled for production safety.]`

### PERF-001

- **Baseline:** `[what to measure first, and how]` — `[safe-on-production: yes/no]`
- **Change:** `[what is being changed]`
- **Measurement:** `[the specific metric, at the specific place]`
- **Expectation:** `[direction and, where derivable from code, magnitude. Do not fabricate
  latency percentages.]`
- **Falsifier:** `[what result would mean the finding was wrong]`
- **Guard:** `[test, metric, or alert that prevents regression]`

### Instrumentation gaps to close first

`[If the system is uninstrumented, this comes before any optimization — optimizing an
unmeasured system produces changes nobody can justify later.]`

---

## 10. Notes on this review

- Findings are classified by evidence grade; `Confirmed` requires a cited runtime artifact.
- No runtime metric in this report was estimated or assumed. `[If any number is a derivation,
  it is labelled as such at the point of use.]`
- Recommendations state their trade-offs and their validation path. Where neither could be
  established, the item is reported as an observation rather than a recommendation.
