# Performance Review: flexmeasures (scheduling constraint checks)

**Date:** 2026-11-22
**Mode:** Full review, scoped to the schedule-computation path named in the operators' report
**Reviewed by:** Automated performance review, `backend-performance-review` v2.3.0

---

## 1. Decision summary

### Overall assessment

Static reading supports the operators' report in one place: per-device constraint validation in `StorageScheduler._prepare` is implemented through regex column sanitising plus `DataFrame.eval`, repeated up to 14 times per device, so it carries a large fixed cost per device and grows linearly with device count. That is the most likely code behind the complaint, but nothing here is measured, so it is graded Medium confidence and its share of total schedule time is unknown. One further quadratic index construction exists, but only on the non-default Pyomo backend. Review confidence: Medium (one path read closely, no runtime data).

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P2)** | Profile one multi-device `_prepare`, then replace the expression-based validators with direct vectorised comparisons if validation is a visible share. | Matches the reported symptom; cheap to confirm before changing code. |
| 2 | **PERF-002 (P3)** | Only if the deployment sets a Pyomo solver: build `cjg` from `device_group_lookup` instead of rescanning `cg`. | Quadratic in commitments, but unreachable under the default backend. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Share of a multi-device schedule spent in validation vs. the solver | Whether PERF-001 is the cause or a minor cost | cProfile of one schedule computation |
| Deployed `FLEXMEASURES_LP_SOLVER` and commitment count | Whether PERF-002 is reachable | Deployment config |
| Typical device count and horizon | Magnitude of both findings | Operator-supplied sizes |

### Validation commands

| Finding / unknown | Safety | Command or procedure | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | not-safe-on-production | `python -m cProfile -s cumtime -m pytest flexmeasures/data/models/planning/tests/test_solver.py -k storage_constraints -x` (dev checkout) | Whether `eval` and `re.sub` dominate validator time. |
| PERF-001 / unknown 1 | safe-on-production | Procedure: time `_prepare` with and without `skip_validation=True` on a representative multi-device flex-model. | Validation share of schedule time. |

---

## 2. Scope and method

**Reviewed:** `flexmeasures/data/models/planning/storage.py` (`_prepare`, `validate_power_constraints`, `validate_storage_constraints`, `validate_constraint`, `sanitize_expression`), `linear_optimization.py`, `scheduling_problem.py`, and `highspy_optimization.py` (the parts that consume commitments).
**Not reviewed:** API layer, database access for sensor data, job queue and workers, the forecasting code, the remainder of the 4,288-line `storage.py`.

**Evidence available:** Uninstrumented for this purpose (no profile, benchmark or trace in the repository or supplied).
**Ranking method:** Structural signals only.
**Reference depth:** Python and Postgres detected at deep tier; only Python/pandas behaviour was relevant. No references beyond the universal principles were needed.

### Review completeness

| | |
|:--|:--|
| **Repository coverage** | Schedule-computation path only, as listed above. |
| **Critical paths** | 1 / 1 identified (schedule computation) |
| **Shared resources** | 0 / 0 identified on this path |
| **Technology support** | Python: deep |
| **Runtime evidence** | None |
| **Overall review confidence** | **Medium** |

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Validation share of schedule time | No evidence exists | A profile of one schedule |
| Device, horizon and commitment counts | No evidence exists | Operator-supplied sizes |
| Configured LP backend | No evidence exists | Deployment config |

---

## 3. Architecture overview

Flask application; schedules are built by `StorageScheduler` (`storage.py`), which assembles per-device constraint DataFrames and commitments in `_prepare`, then calls `device_scheduler`. By default (`FLEXMEASURES_LP_SOLVER = "highspy"`, `config_defaults.py:164`) this delegates to a direct HiGHS backend; otherwise it builds a Pyomo model.

**Shared resources:** none relevant to this path were identified.

---

## 4. Workload model

**Known**
- Validation runs per flexible device unless `skip_validation` is set — `storage.py:1642`.
- Default backend is highspy — `linear_optimization.py:213`, `config_defaults.py:164`.

**Assumed**
- `skip_validation=False` (the `compute()` default) — affects PERF-001.

**Unknown**
- Device count, horizon length, commitment count, deployed solver.

**Derived**
- Up to 14 `validate_constraint` calls per device (3 from `validate_power_constraints` + 3 + 2 + 6 from `validate_storage_constraints`), each with 2 `eval` calls — from the call sites at `storage.py:3989-4140`.

**Measured:** none. This is why nothing here is `Confirmed`.

### Questions that would change the ranking

| # | Value | Question | Decision dimensions | What it would change |
|:--|:--|:--|:--|:--|
| 1 | highest | What fraction of a multi-device schedule is spent in constraint validation? | severity, confidence | PERF-001 up or down |
| 2 | medium | Is a Pyomo solver configured, and how many commitments exist? | severity, confidence | PERF-002 reachable or not |

---

## 5. Critical path analysis

| # | Path | Blocking | Datastore ops | Bounded | Instrumented | Notes |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | Schedule computation: `_prepare` (per-device constraints, validation, commitments) then solve | Yes, the caller waits for the schedule | Sensor reads per device (not reviewed) | Linear in devices | No | Validation sits before the solve |

**Amplification points:** per-device validation (up to 14 validator calls each).
**Paths deliberately not analyzed in depth:** sensor-data reads inside `_prepare`, and the solver itself, because the report is scoped to constraint checking.

---

## 6. Layer analysis

### 6.1 Application

Constraint validation is written as generic string expressions (`"min(t) - max(t-1)" <= "derivative_max(t) * factor_w_wh(t)"`) evaluated by pandas. This is flexible, but each call re-parses the expression, runs two regex passes over every column name, and casts and copies the whole frame twice. The frame is vectorised over time, so the horizon is not the multiplier; the number of calls, hence the number of devices, is.

### 6.7 Observability

No timing around validation or `_prepare` was found in the files read, so the share of validation cannot be established from existing instrumentation.

---

## 7. Findings

### PERF-001 — Per-device constraint validation pays expression-parsing cost 14 times per device

| | |
|:--|:--|
| **Root cause** | `ROOT-001` |
| **Severity** | Medium |
| **Confidence** | Medium |
| **Priority** | P2 |
| **Category** | io (CPU and memory overhead of repeated work) |
| **Location** | `flexmeasures/data/models/planning/storage.py:4183` (`validate_constraint`) |
| **Tags** | needs-measurement, scalability-risk |

**Problem**
Each device's constraints are checked by up to 14 `validate_constraint` calls, each of which sanitises expressions with regex, then runs `astype(float).fillna(0).eval(...)` separately for left and right sides.

**Performance principle**
Do not pay interpretive, per-call setup costs for a handful of vectorised comparisons; hoist invariant work out of repeated operations.

**Evidence**
- `storage.py:1642-1660`: validation runs inside the per-device loop (`validate_power_constraints`, then `validate_storage_constraints`).
- `storage.py:4163-4180`: `sanitize_expression` runs `re.search` and `re.sub` for every column, for each expression.
- `storage.py:4218-4230`: the full frame is cast and filled twice per inequality, then `eval`-ed.
- `storage.py:3989-4140`: 3 + (2 optional + 3 + 6) calls per device.
- No runtime evidence exists.

**Impact**
Position: critical path (the schedule waits on `_prepare`). Frequency: per device. Growth: O(devices) in call count (derived: up to 14 calls, 28 `eval`s per device), roughly flat in horizon length. Blast radius: the schedule-computation request or job.

**Conditions**
Matters when a schedule has many devices and the solve is not so dominant that this fixed overhead disappears into it. Assumes validation is enabled. Magnitude is unknown.

**Counter-evidence**
- The checks are vectorised over time, so cost per call does not grow with horizon (bounds impact).
- Growth is linear, not superlinear; the solver may grow faster (lowers confidence, hence Medium).
- `skip_validation` already exists as a bypass.
- Searched for device-by-device nested loops in the validation path: none found.

**Why this might not matter**
Each device does a fixed, small number of pandas calls; at realistic device counts the LP solve may dwarf them, and validation also prevents an expensive infeasible solve.

**Recommendation**
Replace the expression engine with direct column arithmetic: cast and fill once per device, compare vectorised, and build the violation message strings only for failing rows. Keep the output format and the 6-decimal rounding and NaN-skip semantics.

**Alternatives**

| Option | | Why |
|:--|:--|:--|
| Cache the cast, filled frame per device; keep `eval` | | Removes repeated copies but not parsing or regex |
| Direct vectorised comparisons with lazy messages | **preferred** | Removes all three per-call overheads; behaviour pinned by existing tests |
| Skip validation for devices with all-unset constraints | | Cheap, but changes what is checked and does not help dense inputs |

**Trade-offs**
Less declarative code; NaN and rounding semantics must be preserved exactly or the set of rejected inputs changes.

**Validation**
- Baseline (safe-on-production): time `_prepare` with and without `skip_validation=True` on a representative multi-device flex-model at several device counts.
- Expectation: validation time per device drops; direction only.
- Falsifier: validation is a negligible share of schedule time.
- Guard: a test asserting identical violation lists before and after.
- Command (not-safe-on-production, dev checkout): `python -m cProfile -s cumtime -m pytest flexmeasures/data/models/planning/tests/test_solver.py -k storage_constraints -x` to see the `eval` and regex share.

---

### PERF-002 — Pyomo `cjg` index set rescans all commitment groups per (commitment, time) pair

| | |
|:--|:--|
| **Root cause** | `ROOT-002` |
| **Severity** | Low |
| **Confidence** | Medium |
| **Priority** | P3 |
| **Category** | io (CPU) |
| **Location** | `flexmeasures/data/models/planning/linear_optimization.py:303` (`commitment_time_device_groups_init`) |
| **Tags** | scalability-risk, needs-measurement |

**Problem**
`((c, j, g) for (c, j) in m.cj for (_, g) in m.cg if _ == c)` scans all of `cg` for every `(c, j)`, O(|cj| x |cg|).

**Performance principle**
Index by the join key instead of rescanning the whole relation per probe.

**Evidence**
`linear_optimization.py:304`; `device_group_lookup` already maps commitment to groups (`:292`); the highspy backend returns before this code (`:213`). No runtime evidence.

**Impact**
Position: critical path, only when a Pyomo solver is configured. Frequency: per model build. Growth: superlinear in commitment count. Blast radius: the schedule request.

**Conditions**
Non-default `FLEXMEASURES_LP_SOLVER` and many commitments. That commitment count grows with device count is an assumption (per-device commitments are appended, e.g. `storage.py:1127`, `1338`), not a measurement.

**Counter-evidence**
The default backend bypasses it; interchangeable sub-commitments are merged first (`scheduling_problem.py:227`); `highspy_optimization.py:569` uses the per-commitment lookup directly.

**Recommendation**
Iterate only `device_group_lookup[c]` for each `(c, j)`.

**Trade-offs**
Set ordering may differ, which could alter Pyomo variable order and tie-breaking among equal-cost solutions.

**Validation**
Time construction of `model.cjg` under a Pyomo-configured schedule (safe-on-production); falsifier: highspy in use or negligible time; guard: test that the tuple set is unchanged.

---

### Considered and not reported

- Commitment-group construction and `_validate_commitments_are_enforceable` in `scheduling_problem.py:565-775`: linear in sub-commitments with array reads, and an existing source comment records a prior optimisation of this loop. Revisit if a profile shows model preparation dominating.

---

## 8. Prioritized action plan

| Order | ID | Priority | Effort | Why here |
|:--|:--|:--|:--|:--|
| 1 | PERF-001 | P2 | Small to medium | Matches the reported symptom; measure first |
| 2 | PERF-002 | P3 | Small | Only if a Pyomo backend is deployed |

**If only one thing is done:** profile one multi-device schedule and compare with `skip_validation=True`; this decides whether PERF-001 is worth any code change.

---

## 9. Validation plan

### PERF-001
- **Baseline:** time `_prepare` with and without validation — safe-on-production.
- **Change:** direct vectorised validators.
- **Measurement:** time and call counts of the two validation functions per `_prepare`, at several device counts.
- **Expectation:** per-device validation cost falls; magnitude to be measured.
- **Falsifier:** validation is a negligible share of schedule time.
- **Guard:** test asserting identical violation lists.
- **Commands:**
  - `not-safe-on-production` `python -m cProfile -s cumtime -m pytest flexmeasures/data/models/planning/tests/test_solver.py -k storage_constraints -x` — profile validator cost in a dev checkout.

### PERF-002
- **Baseline:** time `model.cjg` construction with a Pyomo solver — safe-on-production.
- **Falsifier:** highspy in use, or construction time negligible.
- **Guard:** test that the tuple set is unchanged.

### Instrumentation gaps to close first
No timing exists around validation or `_prepare`; add a timer (or log) around them so the share can be read in staging.

---

## 10. Machine-readable output

See `review.json` in this directory (schema version 1.0, validated).

---

## 11. Notes on this review

- No runtime metric in this report was estimated. The only figures (14 calls, 28 evals per device) are derived from call sites and labelled so.
- The code was read, not run; nothing from the repository was executed.
