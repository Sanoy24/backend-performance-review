# Performance Review: scipy (linprog HiGHS path)

**Mode:** Full review (scoped to the code workload.py exercises) | **Review confidence:** Medium

## 1. Decision summary

**Overall assessment.** Sparse LP construction is vectorized and linear. One material issue was found: `_highs_wrapper` builds the bound marginals with a Python loop over every column, indexing native HiGHS result objects per element. For the workload (9 extended quantiles, 9090 columns) this is a large per-solve Python cost, and quadratic if the binding copies vectors per access. Not runtime-verified.

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P1)** | Fetch `col_status`/`col_dual` once, vectorize with numpy masks | Per-column loop on every highs solve; cheap fix |

**Key unknown:** whether the pybind property copies the vector on each access, and the share of time versus `highs.run()`. Resolve with `cProfile` of the workload.

**Validation command** (safe-on-production): `python -m cProfile -s cumtime workload.py` (run outside the repo tree).

## 2. Scope and method

Reviewed: `_linprog.py`, `_linprog_util.py` (`_clean_inputs`), `_linprog_highs.py`, `_highspy/_highs_wrapper.py`, `sparse/_construct.py` (`kron`, `_block`). Not reviewed: HiGHS C++ core and binding source. Evidence: uninstrumented; ranking is structural inference. No runtime measurements.

## 4. Workload model

Known: n=500, d=5, 3 quantiles, coherence_buffer=3 (workload.py). Derived: num_quantiles=(3-1)*4+1=9; numcol = 9*5*2 + 9*500*2 = 9090. Measured: none.

## 8. Findings

### PERF-001 — Per-column Python loop over native result vectors (High / Medium / P1)
- **Location:** `scipy/optimize/_highspy/_highs_wrapper.py:266` (`_highs_wrapper`)
- **Problem:** `for ii in range(numcol)` reads `basis.col_status[ii]` and `solution.col_dual[ii]` from native objects each iteration. If each access builds a full Python list from the C++ vector, the loop is O(numcol^2); otherwise it is a 9090-iteration interpreted loop.
- **Principle:** do not cross a language boundary per element; fetch in bulk and vectorize.
- **Evidence:** static, lines 266-272; the result is built unconditionally (also consumed at `_linprog_highs.py:373`). No runtime evidence.
- **Impact:** critical path, per column, O(n^2)+ if copy-per-access, endpoint blast radius.
- **Conditions:** every `linprog(method='highs*')`; grows with column count.
- **Counter-evidence:** binding source not inspected (copy semantics assumed); the HiGHS solve may dominate.
- **Why it might not matter:** cheap views or a solver-dominated runtime make it a small share; small LPs unaffected.
- **Recommendation:** hoist both vectors once into numpy arrays and compute `marg_bnds` with boolean masks for kLower/kUpper.
- **Alternatives:** hoist only (removes quadratic term, keeps loop); vectorize (preferred).
- **Trade-offs:** two arrays of length numcol; preserve zero for other statuses.
- **Validation:** profile `_highs_wrapper` before/after (safe-on-production). Falsifier: loop negligible versus `highs.run()`.

## Considered, not reported
- `bounds` list-of-tuples conversion in `_clean_inputs`: linear C-level, small.
- Sparse kron/hstack/vstack construction: vectorized, linear in nnz.
