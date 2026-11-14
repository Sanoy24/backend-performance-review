# Performance Review: pandas to_datetime with format containing %z

**Mode:** Full review, scoped to the code the workload exercises
**Reviewed by:** `backend-performance-review` v2.2.0 (static, read-only)

## 1. Decision summary

### Overall assessment
The workload parses about one string per day from 1900 to 2000 with a `%z` format, and one extra string with a different offset (workload.py:6-10). Two avoidable per-element costs sit on this path. The larger one is in `_return_parsed_timezone_results`, which builds one Timestamp per element in Python. No runtime evidence exists, so confidence is Medium and the review confidence is Medium.

### Top three actions
| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Localize/convert per distinct tz in bulk instead of per-element Timestamps | Per-element Python object work scaling with input length |
| 2 | **PERF-002 (P2)** | Memoise offset-string parsing per call | Small, local change |

### Key unknowns
| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Share of time in `_return_parsed_timezone_results` vs `array_strptime` | Ordering of PERF-001 vs PERF-002 | cProfile of the workload |

### Validation commands
| Finding | Safety | Command | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | safe-on-production | `python -m cProfile -s cumtime workload.py` | Whether the tz materialisation dominates |

## 2. Scope and method
**Reviewed:** `pandas/core/tools/datetimes.py` (`_convert_listlike_datetimes`, `_array_strptime_with_fallback`, `_return_parsed_timezone_results`), `pandas/_libs/tslibs/strptime.pyx` (`array_strptime`, `parse_timezone_directive`).
**Not reviewed:** other parse paths, Timestamp/tz_localize internals, build flags.
**Evidence available:** Uninstrumented; no profile supplied. Ranking is from structural signals only.
**Review confidence:** Medium. Not run, per instructions.

## 4. Workload model
**Known:** list of daily strings 1900-2000 with `+01:00` plus one `+02:00` string (workload.py:6-7).
**Unknown:** time split between stages. **Measured:** none.

## 5. Critical path
`to_datetime(list, format=...)` -> `_convert_listlike_datetimes` -> `_to_datetime_with_format` -> `_array_strptime_with_fallback` -> `array_strptime` (per-row regex) -> `_return_parsed_timezone_results` (per-row Timestamp). The format is not ISO-only because it has day before month, so the iso8601 fast path is not used.

## 7. Findings

### PERF-001 — Per-element Timestamp construction and tz_localize for tz-aware format parsing

| | |
|:--|:--|
| **Root cause** | ROOT-001 |
| **Severity / Confidence / Priority** | High / Medium / P1 |
| **Category** | cost |
| **Location** | `pandas/core/tools/datetimes.py:316` (`_return_parsed_timezone_results`) |
| **Tags** | needs-measurement |

**Problem:** If any parsed timezone is non-None, every element goes through `np.array([Timestamp(res).tz_localize(zone) for ...])`, then Index inference over that object array. With `utc=True` a second per-element loop runs `tz_convert`/`tz_localize`.

**Performance principle:** Bulk data should be converted with batch operations over homogeneous groups.

**Evidence:** datetimes.py:316-328 (loops), :507-508 (taken whenever any tz is not None). Offsets come from `pytz.FixedOffset` (strptime.pyx:577), so distinct offsets are few while elements are many. No runtime evidence.

**Impact:** Critical path, per item, O(n), endpoint.

**Conditions:** Any large `to_datetime` with `%z`/`%Z`; the workload matches.

**Counter-evidence:** No vectorised or grouped path exists. Mixed offsets must yield an object Index, which bounds the saving.

**Why this might not matter:** With mixed offsets (the workload has one odd row) per-element objects are partly inherent. Actual share depends on a profile.

**Recommendation:** Group by distinct tzinfo and localize int64 blocks once per group; return a tz-aware DatetimeIndex directly when all offsets match; create per-element Timestamps only for the mixed-offset object result and skip the second pass.

**Alternatives:** (A, preferred) group by tz and vectorise; (B) have `array_strptime` return normalised UTC int64 plus offsets, a larger change to the Cython contract.

**Trade-offs:** More branches; must preserve return types for mixed offsets.

**Validation:** Profile `_return_parsed_timezone_results` cumulative time before and after (safe-on-production). Falsifier: it is a small share of the total. Guard: asv benchmark with a `%z` format.

### PERF-002 — Per-row groupdict and offset re-parse in array_strptime

| | |
|:--|:--|
| **Root cause** | ROOT-002 |
| **Severity / Confidence / Priority** | Medium / Medium / P2 |
| **Category** | cost |
| **Location** | `pandas/_libs/tslibs/strptime.pyx:219` (`array_strptime`) |
| **Tags** | needs-measurement |

**Problem:** Each row builds `groupdict()`, iterates keys through Python dict lookups, and calls `parse_timezone_directive` (slicing, int() conversions, `pytz.FixedOffset`) with no memo, although offset strings repeat.

**Performance principle:** Do repeated work once per distinct input.

**Evidence:** strptime.pyx:219-226, :302-303, :533-577.

**Impact:** Critical path, per item, O(n), endpoint.

**Conditions:** Large inputs with few distinct offsets; the workload has two.

**Counter-evidence:** Regex matching is inherent to the design; only the offset re-parse is avoidable.

**Why this might not matter:** Regex match and date construction likely dominate; `pytz.FixedOffset` already caches objects.

**Recommendation:** Per-call dict from raw offset string to tzinfo.

**Alternatives:** (A, preferred) per-call memo; (B) rewrite parsing without regex groupdict, larger and riskier.

**Trade-offs:** Negligible.

**Validation:** Time `array_strptime` on repeated offsets (safe-on-production); falsifier: no measurable change.

### Considered and not reported
- Regex cache lookup under lock (strptime.pyx:117-140): once per call, O(1).

## 8. Action plan
P1: PERF-001. P2: PERF-002. If only one thing is done: fix PERF-001.

## 9. Validation plan
- Baseline: `python -m cProfile -s cumtime workload.py` (safe-on-production), maintainer-run.
- Expectation: lower time in `_return_parsed_timezone_results`; magnitude not predicted.
- Falsifier: that function is a small share of total time.

## 11. Notes
No runtime metric in this report was measured or estimated. All findings are Medium confidence at most.
