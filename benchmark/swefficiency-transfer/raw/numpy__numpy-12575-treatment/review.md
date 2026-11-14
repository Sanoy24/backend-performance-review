# Performance Review: numpy.core.records.fromarrays

## Overall assessment
One material finding. `fromarrays` with thousands of fields runs a quadratic duplicate-name check. Static review only; nothing was run or measured.

## Actions
1. (P1) Rewrite `find_duplicate` in `numpy/core/records.py` as a single-pass set-based check.

## Decision-changing unknowns
- Share of total `fromarrays` time spent in `find_duplicate` at 4000 fields (a cProfile run resolves it).

## Validation commands
- safe-on-production: `python -c "import cProfile,numpy as np; a=[np.arange(5)]*4000; n=['field_%d'%i for i in range(4000)]; cProfile.run('np.core.records.fromarrays(a,names=n)',sort='cumtime')"`

## Scope
Workload: `fromarrays` with 4000 arrays of length 5 and 4000 names (workload.py). No observability or runtime evidence exists; ranking is structural only.

## PERF-001: Quadratic duplicate-name detection (High, High confidence, P1)
- Location: `numpy/core/records.py:76` (`find_duplicate`), called at line 194 from `format_parser._setfieldnames`, constructed at line 608 in `fromarrays`.
- Problem: for each name it evaluates `list[i] in list[i + 1:]`, copying a slice and linearly scanning it.
- Principle: uniqueness checks should be hash-based, not repeated linear scans with per-iteration copies.
- Evidence: code above. Derived: n=4000 gives n(n-1)/2 = 7,998,000 comparisons and slice-element copies per call when no duplicates exist. No runtime evidence.
- Impact: critical path of the call, per call, O(n^2) in field count, endpoint blast radius.
- Conditions: many fields (thousands). Negligible for a handful.
- Counter-evidence: per-field Python work elsewhere (line 631 loop) is linear but may have a large constant; string comparisons are cheap C operations. Share is unmeasured.
- Why it might not matter: real record arrays rarely have thousands of fields.
- Recommendation: single pass with `seen`/`dup` sets, preserving first-seen order of the returned list.
- Alternatives: `len(set(names)) != len(names)` precheck (equivalent, less direct); single-pass set (preferred).
- Trade-offs: O(n) extra memory; names must be hashable (strings).
- Validation: profile before/after; falsified if `find_duplicate` is a small fraction of total time at n=4000. Safe-on-production.

## Considered, not reported
- Per-field assignment loop in `fromarrays` (records.py:631): linear, no evidence it dominates; revisit after profiling.
