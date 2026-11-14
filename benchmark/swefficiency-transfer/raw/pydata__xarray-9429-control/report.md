# Performance review: Dataset.groupby("time.month") (xarray)

Workload: groupby on a virtual coordinate `time.month` of a 10000-step daily time axis (12 groups). Only construction of the GroupBy object is timed; no reduction runs.

Call path: Dataset.groupby (core/dataset.py ~10407) -> ResolvedGrouper.__post_init__ (core/groupby.py:293) -> UniqueGrouper.factorize (groupers.py:163) -> _factorize_unique (groupers.py:179) -> EncodedGroups.__init__ (groupers.py:76) -> _codes_to_group_indices (core/groupby.py:73).

## Finding 1 (high): group indices built with a pure-Python per-element loop
File: xarray/core/groupby.py:73-80 (`_codes_to_group_indices`), called from xarray/groupers.py:94-98 (`EncodedGroups.__init__`, when `group_indices is None`, as in `_factorize_unique`).

```python
groups = tuple([] for _ in range(N))
for n, g in enumerate(codes):
    if g >= 0:
        groups[g].append(n)
```
The loop is O(len(group)) in interpreter time. Each iteration iterates a numpy int64 scalar, does a numpy-scalar comparison (`g >= 0`), a tuple index through `__index__`, and a list append. That costs several hundred ns per element, so about 3-5 ms for 10k elements. It scales linearly, so it becomes seconds for 1e6+ elements. This is the dominant cost of constructing the GroupBy, and it is paid eagerly even though the indices are only needed for iteration, `.groups`, `.sizes` or non-flox reductions. The result is also Python lists of ints, which are memory-heavy (one PyObject per index). `EncodedGroups.__init__` then runs a second generator pass (`if g`) over the N groups to drop empties.

Fix: vectorize.
- Use `np.argsort(codes, kind="stable")` plus `np.bincount(codes[codes>=0], minlength=N)` and `np.split` / cumulative offsets. Alternatively use `pd.Series(np.arange(n)).groupby(codes).indices`-style logic.
- Better, make `group_indices` lazy (a cached property computed on first use), so a groupby that is only reduced via flox or `_flox_reduce` never builds it.
- Keep results as int arrays, or slices when codes are contiguous. Keep the -1 exclusion.

## Finding 2 (medium): eager work not needed to construct the GroupBy
- groupers.py:166-171: `index.is_unique` and `is_monotonic_*` are evaluated on a 10k-element index of month values. Each check is O(n). `is_unique` builds a hash table, then `pd.factorize` (groupers.py:472) hashes the same data again. Check cheap conditions first (`is_dimension`) before the uniqueness/monotonic checks, so they are skipped when the group is not a dimension coordinate. Here `time.month` is not a dimension, so `can_squeeze` is always False and the checks are wasted.
- groupers.py:187 `self.group.copy(data=...)` and `_coordinates_from_variable` (groupers.py:197) build extra DataArray/Coordinates objects. These are small, fixed-cost overheads.
- core/groupby.py:299 `copy.deepcopy(self.grouper)` runs on every ResolvedGrouper. It is cheap for a fresh `UniqueGrouper()`, but it deep-copies any cached `_group_as_index` or bins for user-supplied groupers.

## Finding 3 (low/medium): virtual-variable and alignment overhead
- core/dataset.py:194-228 `_get_virtual_variable` wraps the time variable in a `DataArray` and computes `.dt.month` through the accessor over all 10000 points. This is vectorized and OK, but it is re-done on each `groupby("time.month")` call, with no caching.
- core/groupby.py:347 `align(obj, group, join="exact", copy=False)` against the full dataset and group goes through the full alignment machinery (index comparison across all indexes, including the 10000-element time index). The cost is small per call, but it is O(n) when the indexes are not identical objects. The group here derives from the same index, so an identity short-circuit would apply.
- The object-dtype `year_month` coordinate (10000 cftime objects) is carried along as a non-index coord. It does not affect grouping directly, but any step that touches all coords (virtual variable construction, `_construct_dataarray`) handles it as object arrays, with no vectorization.

## Priority
1. Replace the Python loop in `_codes_to_group_indices` with a vectorized implementation and/or make it lazy. This should account for most of the workload time.
2. Reorder the cheap checks in `UniqueGrouper.factorize`, so the uniqueness/monotonic scans only run for dimension coordinates.
3. Trim the minor object-construction and alignment overhead.

I did not run anything (static review only), so the relative weights are estimates from the code structure.
