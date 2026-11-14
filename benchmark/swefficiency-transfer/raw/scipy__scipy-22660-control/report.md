# Performance review: linprog(method="highs") on a sparse LP

Workload size: n=500, d=5, 3 quantiles -> 9 extended quantiles. The LP has numcol = 9*5*2 + 9*500*2 = 9090 columns and about 4500 equality rows plus about 4590 inequality rows.

## Finding 1 (high): O(numcol^2) Python loop over pybind property accesses in the marginals extraction
File: scipy/optimize/_highspy/_highs_wrapper.py, lines 265-271.

```python
marg_bnds = np.zeros((2, numcol))
for ii in range(numcol):
    if basis.col_status[ii] == _h.HighsBasisStatus.kLower:
        marg_bnds[0, ii] = solution.col_dual[ii]
    elif basis.col_status[ii] == _h.HighsBasisStatus.kUpper:
        marg_bnds[1, ii] = solution.col_dual[ii]
```

- `basis.col_status` and `solution.col_dual` are pybind11 attributes bound to `std::vector`. Each attribute access, assuming `def_readwrite`, converts the whole vector into a new Python list (or copy). Indexing `[ii]` then reads one element of that fresh copy.
- Every iteration therefore does up to 3 full-vector copies of 9090 elements, and the loop runs 9090 times. That is roughly 10^8 element conversions and Python object creations, plus the interpreter loop overhead.
- This work is quadratic in the number of columns and does not depend on solver time. It likely dominates the runtime for this size, because HiGHS itself solves a problem of this size quickly. It gets worse quadratically as n or the number of quantiles grows.
- I did not run anything. The attribute-copy behaviour is inferred from the pybind conventions. The C++ binding source is not in the repo as a .cpp file I could check, so the exact conversion is unverified.

Fix:
- Fetch each vector once, outside the loop, and vectorize:
```python
col_status = np.asarray(basis.col_status)   # one copy
col_dual = np.asarray(solution.col_dual)
marg_bnds = np.zeros((2, numcol))
lower = col_status == int(_h.HighsBasisStatus.kLower)
upper = col_status == int(_h.HighsBasisStatus.kUpper)
marg_bnds[0, lower] = col_dual[lower]
marg_bnds[1, upper] = col_dual[upper]
```
- If the enum does not convert to numpy directly, build the status array once with `np.fromiter((int(s) for s in basis.col_status), ...)` or `[s == kLower for s in statuses]`. Either way it is a single O(numcol) pass.
- Also hoist `solution.row_value`, `solution.row_dual` and `solution.col_value`. They are accessed once each, so they are fine, but keep it that way.
- Expected effect: removes the quadratic term. This is likely the bulk of the time in this workload.

## Finding 2 (low): Repeated sparse format conversions of the constraint matrices
Files: scipy/optimize/_linprog_util.py:181-184 (`_format_A_constraints`), scipy/optimize/_linprog_highs.py:317-321.

- With sparse input, `A_ub` and `A_eq` are each copied into `coo_array` (`copy=True`, so a full copy plus a format conversion). They are then stacked with `vstack`, which converts to COO and concatenates. Then `csc_array(A)` does a COO to CSC sort and conversion. The result is 2 to 3 O(nnz) copies and a sort, with nnz about 40k.
- The cost is small compared with Finding 1. It would matter for much larger problems.
- Fix: avoid the defensive `copy=True` for sparse input where the matrix is not mutated afterwards. Stack directly in CSC (`vstack(..., format="csc")`) to skip the intermediate COO.

## Finding 3 (low): Redundant validation and dense intermediate arrays
Files: scipy/optimize/_linprog_util.py:324-328, 344, 363, 383; scipy/optimize/_linprog_highs.py:307-315, 345-348.

- Several full passes over the data check `isfinite`, and `_replace_inf` makes a boolean mask per vector. `_check_result` (`_linprog_util.py` about 1480-1500) does more O(n) passes. All of them are linear and cheap at this size, and none is worth changing before Finding 1.

## Notes on the workload script
- The workload builds the constraints with `sparse.kron`/`hstack`/`vstack` and a `bounds` list of 9090 tuples. This is user-side and linear. `linprog` then converts the bounds list through `np.array(bounds, dtype=float)` (`_linprog_util.py:431`). That conversion is slow for a long list of tuples but still linear.

## Priority
1. Fix Finding 1. It removes the O(numcol^2) loop.
2. Findings 2 and 3 only if profiling after Finding 1 shows them.
