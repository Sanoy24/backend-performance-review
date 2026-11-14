# Performance Review: scikit-learn `_BinMapper` (histogram gradient boosting binning)

**Mode:** Full review, scoped to the code the workload exercises. Read-only; nothing was run.

## 1. Decision summary

### Overall assessment
`_BinMapper.fit` computes quantile thresholds one feature at a time in a serial loop and ignores `n_threads`. Only `transform` is parallel. Each column is also sorted more than once. Both are static findings with no measurements. Review confidence is Medium.

### Top actions
| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | PERF-001 (P2) | Parallelise the per-feature loop in `fit` over `n_threads` | The `n_threads=4` fit in the workload currently gets no benefit from the extra threads |
| 2 | PERF-002 (P2) | Remove the redundant sort and unique passes in `_find_binning_thresholds` | Each fit and fit_transform repeats them for all 20 columns |

### Key unknowns
| Unknown | Decision it changes | How to resolve |
|:--|:--|:--|
| Share of fit time spent in sort vs unique vs percentile | Ranking of PERF-002 against PERF-001 | Profile `_BinMapper.fit` |
| Core count of the target machine | Size of the PERF-001 gain | Run the workload with the change |

### Validation commands
| Finding | Safety | Command | Decision |
|:--|:--|:--|:--|
| PERF-001 | safe-on-production | `python workload.py` | Compare the n_threads=1 and n_threads=4 fit times before and after |

## 2. Scope and method
**Reviewed:** `sklearn/ensemble/_hist_gradient_boosting/binning.py`, `_binning.pyx`, and `workload.py`.
**Not reviewed:** The rest of the library, including the callers in the gradient boosting estimators.
**Evidence available:** Uninstrumented. No runtime evidence was supplied, so all findings are static. Ranking is from structural signals only.
**Reference depth:** Generic. The code is Python/NumPy/Cython and no technology-specific reference applies.

Workload (`workload.py`): `fit` and `fit_transform` on a 200000x20 float64 array with `n_bins=256`, once with `n_threads=1` and once with `n_threads=4`. The row count equals the default `subsample` of 2e5, so subsampling is skipped (`binning.py:199`).

## PERF-001: `fit` ignores `n_threads` and loops over features serially
- **Severity / Confidence / Priority:** Medium / High / P2. **Category:** concurrency. **Location:** `binning.py:232`, `_BinMapper.fit`.
- **Problem:** The loop at line 232 calls `_find_binning_thresholds(X[:, f_idx], max_bins)` for each feature in sequence. `self.n_threads` is read only in `transform` (line 277). Both the fit and fit_transform calls in the workload do the same fit work regardless of thread count.
- **Principle:** Independent per-item work should be spread over the available workers.
- **Impact:** Offline, per batch, linear in the number of features. Scope is a single endpoint.
- **Conditions:** Several features and cores. The workload has 20 features and uses 4 threads.
- **Counter-evidence:** NumPy sort releases the GIL, so a thread pool over columns should scale. I found no existing parallel path in `fit`.
- **Why this might not matter:** Binning is one stage of a boosting fit. The gain is small with few features or cores.
- **Recommendation:** Run the per-feature computation on a thread pool sized by `n_threads` and keep the results in feature order.
- **Alternatives:** Cython/OpenMP quantiles, which need more code. The thread pool is preferred.
- **Trade-offs:** Extra transient memory per worker, and thread overhead on tiny data.
- **Validation:** Time `fit` with 1 and 4 threads and check that the thresholds are identical. `python workload.py` (safe-on-production). It is falsified if there is no speedup or the thresholds differ.

## PERF-002: Redundant sorting and copying per column
- **Severity / Confidence / Priority:** Medium / High / P2. **Category:** memory. **Location:** `binning.py:51`, `_find_binning_thresholds`.
- **Problem:** `np.sort` (line 51) copies the strided column and sorts it. `np.unique` (line 52) sorts it again. `percentile` (line 63) copies and partitions it again. An `np.isnan` scan comes first (line 46). The comment at line 49 acknowledges the repeated sorting.
- **Principle:** Do not repeat O(n log n) work on the same data.
- **Impact:** Offline, per feature, O(n log n) in rows. Scope is a single endpoint.
- **Conditions:** Many rows (up to 2e5 after subsampling) and many features, on every fit.
- **Counter-evidence:** Sorting already-sorted data is cheaper, which bounds the waste.
- **Why this might not matter:** The cost is bounded by the subsample size, and the later passes are over sorted data.
- **Recommendation:** Get the distinct values from the sorted array with a diff mask. Compute the midpoint percentiles by indexing the sorted array directly. Convert X to F-order once so column reads are contiguous.
- **Alternatives:** Leave as is. The diff-mask approach is preferred.
- **Trade-offs:** The `percentile(method="midpoint")` semantics must be reproduced exactly. The F-order copy of X costs extra memory.
- **Validation:** Time `_find_binning_thresholds` per column and compare `bin_thresholds_` before and after (safe-on-production).

## Considered, not reported
`_map_to_bins` opens one `prange` region per feature and reads from C-ordered input. I could not show that this matters without a profile. Revisit it if a profile shows `transform` dominating.

## What this review could not determine
No evidence exists on the relative cost of the sort, unique and percentile steps, or on the thread scaling. A profile of `_BinMapper.fit` on the workload data would settle both.
