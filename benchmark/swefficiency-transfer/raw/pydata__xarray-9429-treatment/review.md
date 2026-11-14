# Performance review: Dataset.groupby("time.month") (xarray)

## Decision summary
Overall: one material, code-evident problem on the exercised path and one smaller one. Static review only; nothing was run or measured.

Actions:
1. PERF-001 (P2): stop deep-copying every coordinate when building the codes array in `UniqueGrouper._factorize_unique` (`xarray/groupers.py:187`). The workload carries a 10000-element object-dtype (cftime) coordinate `year_month` on the `time` dimension, which is deep-copied element by element on each `groupby` call.
2. PERF-002 (P2): replace the Python per-element loop in `_codes_to_group_indices` (`xarray/core/groupby.py:77`) with a vectorised argsort/split, or build it lazily.

Unknowns that change decisions: whether real users carry object-dtype coordinates on the grouped dimension (sets how often PERF-001 bites); the actual share of runtime for each item (needs a profile).

Validation command (safe-on-production, local): `python -m cProfile -s cumtime workload.py`, then compare after the change.

## Scope
Path of `ds.groupby("time.month")`: `Dataset.groupby` -> `ResolvedGrouper.__post_init__` -> `_resolve_group` -> `Dataset._construct_dataarray` / `_get_virtual_variable` -> `UniqueGrouper.factorize` -> `_factorize_unique` -> `EncodedGroups.__init__` -> `GroupBy.__init__`. Workload: 10000 daily steps, a 10000x180x360 data variable (not touched by groupby construction), plus a coordinate `year_month` of 10000 `cftime.datetime` objects. Only the `groupby` call is timed. No observability, benchmarks or workload answers beyond workload.py were available.

## PERF-001: deep copy of all coordinates when building group codes (Medium severity, High confidence, P2)
- Location: `xarray/groupers.py:187` (`UniqueGrouper._factorize_unique`); same pattern at `:215` (`_factorize_dummy`).
- Problem: `codes = self.group.copy(data=...)` uses `DataArray.copy` with default `deep=True`. Even with `data` supplied, `DataArray._copy` (`dataarray.py:1297-1313`) calls `_copy(deep=True)` on every non-index coordinate, which runs `copy.deepcopy` on the array (`variable.py:926-927`). The group `time.month` is built by `Dataset._construct_dataarray` (`dataset.py:1475-1494`), which attaches every coordinate whose dims are a subset of `{time}`, including `year_month`. Deep-copying an object-dtype ndarray deep-copies each element, so 10000 cftime objects per call.
- Principle: do not copy data that is neither mutated nor retained.
- Impact: critical path, per call, O(n) in the length of object-dtype coordinates, endpoint scope.
- Conditions: the grouped dimension carries object-dtype or large non-index coordinates (as in workload.py). Numeric coordinates make the copy a cheap memcpy.
- Counter-evidence: index coords are copied separately via `copy_indexes`; no caching avoids the copy; the codes data is freshly built, so a deep copy is not needed.
- Why this might not matter: only with object-dtype or large extra coordinates; cost is per groupby construction, not per reduction.
- Recommendation: `self.group.copy(deep=False, data=...)` here and in `_factorize_dummy`.
- Alternatives: drop unneeded coordinates before factorising (not preferred; more change).
- Trade-offs: codes shares coordinate buffers with the source; treat `encoded.codes` as read-only.
- Validation: profile before/after; `copy.deepcopy` should drop out. Falsifier: it is a negligible share of runtime. safe-on-production.

## PERF-002: Python loop to build group indices (Medium severity, Medium confidence, P2)
- Location: `xarray/core/groupby.py:73-80` (`_codes_to_group_indices`), called from `EncodedGroups.__init__` (`groupers.py:94-98`) because `_factorize_unique` passes no `group_indices`.
- Problem: a pure-Python loop over every code appends to per-group lists, executed eagerly on every groupby construction (10000 iterations in the workload).
- Principle: prefer bulk vectorised operations to per-element interpreter loops.
- Impact: critical path, per call, O(n), endpoint scope; smaller than PERF-001 for this workload.
- Conditions: long grouped dimension; cost scales with its length.
- Counter-evidence: no lazy or cached construction found.
- Why this might not matter: 10^4 iterations is bounded.
- Recommendation: stable argsort plus split on bincount offsets, or compute lazily on first use.
- Alternatives: lazy cached property (viable; preferred if flox paths never need the lists).
- Trade-offs: container types must stay compatible with downstream indexing.
- Validation: cProfile share of `_codes_to_group_indices`. Falsifier: negligible share. safe-on-production.

## Not reported
`_resolve_group`'s `align(join="exact")` and `group.copy(deep=False)` are shallow; no material cost found. Reductions are not exercised by the workload.
