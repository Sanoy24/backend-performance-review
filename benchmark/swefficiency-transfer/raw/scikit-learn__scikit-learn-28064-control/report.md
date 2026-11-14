# Performance review: _BinMapper.fit / fit_transform (HistGradientBoosting binning)

Workload: 200k x 20 float64 C-ordered X, n_bins=256, no subsampling (n_samples == subsample, so the `>` check at binning.py:199 is false). It calls fit and fit_transform with n_threads=1 and with n_threads=4. I did not run anything. Findings come from reading the code.

## 1. Threshold search in fit() is serial, and n_threads is ignored (high)
File: sklearn/ensemble/_hist_gradient_boosting/binning.py, lines 232-244 (loop) and 23-69 (`_find_binning_thresholds`).

- `fit` loops over the 20 features one at a time in pure Python and calls `_find_binning_thresholds` on each. The features are independent. `self.n_threads` is never used in `fit`. It only reaches `transform` (line 277).
- fit takes most of the time. The `n_threads=4` mapper's fit costs the same as the `n_threads=1` mapper's fit. fit_transform repeats that cost on top.
- Fix: run the per-feature search in parallel. Use `joblib.Parallel(n_jobs=n_threads, prefer="threads")`, or a ThreadPool. NumPy sort, unique and partition release the GIL, so threads work. For non-categorical features only, submit `_find_binning_thresholds(X[:, f_idx], max_bins)` and collect the results in order. Expect close to linear speedup up to the number of features. Keep categorical features in the serial path, since they are cheap.

## 2. Redundant sorting and a full-data unique per column (high)
File: binning.py lines 51-63.

- Per column, the code does `np.sort` (O(n log n)). Then `np.unique(col_data)` sorts again internally. It also allocates a mask, a boolean array and a result. Then `percentile(..., method="midpoint")` runs on the data again.
- On 200k continuous values the number of distinct values is almost always above 255, so the unique result is thrown away. It is only used to check `len(distinct_values) <= max_bins`.
- Fix: since `col_data` is already sorted, compute the distinct count in O(n) as `1 + np.count_nonzero(col_data[1:] != col_data[:-1])`. This avoids unique's extra sort and its allocations. Only build `distinct_values` (`col_data[mask]`) when the count is <= max_bins.
- Better still: since the column is already sorted, skip the generic `percentile`. It does its own partition or sort, plus interpolation machinery. Compute the midpoint quantiles directly by indexing the sorted array. `percentile(method="midpoint")` equals `(sorted[floor(i)] + sorted[ceil(i)]) / 2` with `i = q*(n-1)`. That is O(max_bins). It must give identical results to the current code.
- Optionally, cap the work at a smaller subsample. Today the default subsample is 2e5, so the sort covers the whole 200k rows.

## 3. Strided column extraction from a C-ordered array (medium)
File: binning.py line 234, `X[:, f_idx]`, and line 46 (`np.isnan(col_data)`).

- X is C-contiguous, so each column slice is strided by n_features * 8 bytes and cache-unfriendly. `np.isnan` and `np.sort` first touch it with a stride. `np.sort` copies it to a contiguous buffer, so one strided pass is paid per column. That is 20 passes over the full array, each touching a cache line per element.
- `np.isnan` plus `.any()` allocates a 200k boolean array for each column.
- Fix: do one `np.isnan(X).any(axis=0)` pass over the whole array, or process in the sorted copy. Do the isnan check after the sort, because NaNs sort to the end. Then use `np.searchsorted`, or the count from the end, instead of a mask and a boolean-indexed copy. Alternatively convert once to Fortran order (`np.asfortranarray`), which costs one transpose pass. That is cheaper than 20 strided reads, and it lets parallel threads get contiguous columns.

## 4. fit_transform runs a full fit and then a full transform, with revalidation (medium)
File: binning.py lines 195 and 269 (`check_array`), plus TransformerMixin.fit_transform.

- `fit_transform` is the inherited `fit(X).transform(X)`. The input is validated twice (`check_array` with `force_all_finite=False` scans the array for dtype and contiguity). `fit` has already made, or could have made, a float64 array.
- In transform, `np.zeros_like(X, dtype=uint8, order="F")` at line 278 allocates and zeroes 4 MB. This is minor, because the Cython code overwrites every element. `np.empty` is enough.
- Fix: override `fit_transform` so it validates once and shares the checked array. Use `np.empty` for the output.

## 5. `_map_to_bins` opens one OpenMP parallel region per column, over strided data (medium)
File: sklearn/ensemble/_hist_gradient_boosting/_binning.pyx lines 37-45 and 63-83.

- The `prange` is inside the per-column function. The code starts and joins a thread team 20 times, with an implicit barrier each time.
- Each thread reads `data[i]` with a stride of n_features * 8 bytes (C-ordered X), which wastes bandwidth. The write to `binned[:, f]` is contiguous, because the output is Fortran-ordered.
- With n_threads=1 the code still pays the `nogil` prange machinery and the memoryview slicing per column.
- Fix: parallelize once over rows, or over (feature, row-block) tiles, with a single parallel region. Process each row block across all features so a row's data stays in cache. Alternatively, parallelize over features if there are at least n_threads of them. Also hoist `binning_thresholds[feature_idx]` into a typed memoryview outside the hot loop. Add a cheap fast path for the common case where `is_categorical` is 0 and the value is non-NaN.
- Binary search is already cheap (8 steps), so this is secondary to the fit costs.

## 6. Minor
- binning.py lines 199-201: `rng.choice(n, subsample, replace=False)` plus `X.take` copies the data and, on older NumPy, builds a full permutation. It is not hit in this workload (n == subsample). For larger inputs, prefer `rng.permutation`, or sample the indices in sorted order to keep memory access sequential.
- binning.py lines 53-55: when the number of distinct values is small, a small array is built per feature. This is negligible.

## Priorities
1. Parallelize the fit threshold search across features and use n_threads (finding 1). It is the largest wall-time win for the multithreaded cases and the default for users.
2. Remove the double sort and unique, and replace percentile with direct indexing of the sorted data (finding 2). This is a serial speedup of roughly 2x on fit.
3. Do the NaN scan and column layout once (finding 3).
4. Override fit_transform and tidy the binning kernel's parallelization (findings 4 and 5).
