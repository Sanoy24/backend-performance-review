# Performance Review: sympy (Matrix.inv path)

**Date:** 2026-11-14
**Mode:** Full review, scoped to the code exercised by workload.py
**Reviewed by:** `backend-performance-review` v2.2.0

## 1. Decision summary

### Overall assessment

The workload inverts a 2x2 matrix of plain symbols with default arguments and a cleared cache each iteration. That takes the default GE method into a generic expression-level Gauss-Jordan elimination. Pivot selection runs full `simplify()` and `Expr.equals(0)`, and the fast DomainMatrix path is skipped. This is one Medium-severity finding with Medium confidence (P2). There is no runtime evidence, so the claim comes from reading the code. Review confidence is Medium.

### Top three actions

| Order | Finding | Action | Why now |
|:--|:--|:--|:--|
| 1 | **PERF-001 (P2)** | Profile `Z.inv()`. If simplify and equals dominate, route symbolic matrices through a polynomial/fraction-field DomainMatrix, or add a closed-form 2x2 path. | It is the only candidate, and the profile is cheap. |

### Key unknowns

| Unknown | Decision it changes | How to resolve it |
|:--|:--|:--|
| Time split between `simplify`, `equals` and expression arithmetic | Which fix to pick | cProfile of the workload |

### Validation commands

| Finding | Safety | Command | Decision it unlocks |
|:--|:--|:--|:--|
| PERF-001 | safe-on-production | `python -m cProfile -s cumtime workload.py` | If simplify and equals are a small share, drop the finding. |

## 2. Scope and method

**Reviewed:** `sympy/matrices/inverse.py`, `reductions.py`, `determinant.py` and `utilities.py`, along the path `Matrix.inv` -> `_inv` -> `_inv_GE` -> `rref` -> `_row_reduce_list` -> `_find_reasonable_pivot`.
**Not reviewed:** `simplify()` internals, `Expr.equals`, and the assumptions system. They were not read and are only assumed to be costly.
**Evidence available:** Uninstrumented. No profiles or benchmarks were supplied.
**Ranking method:** Structural signals only.

**Overall review confidence:** Medium.

### What this review could not determine

| Unknown | Why | What would resolve it |
|:--|:--|:--|
| Share of time in simplify vs equals | No evidence exists | cProfile |

## 4. Workload model

**Known**
- `Matrix([[a, b], [b, a+b]]).inv()` with defaults, repeated 100 times, `clear_cache()` in setup (workload.py).
- The default dense method is GE (inverse.py:446).

**Assumed**
- Plain symbols have `is_zero` of None, so the simplify and equals passes in pivot search are reached. Affects PERF-001.

**Measured:** none.

## 5. Findings

### PERF-001 Symbolic `inv()` uses generic GE with full simplify/equals in pivot search

- **Severity / Confidence / Priority:** Medium / Medium / P2
- **Category:** io (CPU-bound algebra; no CPU category exists in the enum)
- **Location:** `sympy/matrices/inverse.py:223` (`_inv_GE`)
- **Root cause:** ROOT-001

**Problem.** `_inv_GE` builds `[M | I]` and calls `rref(simplify=True)` (inverse.py:241-242). `_rref` only takes the DomainMatrix route when all entries are rational (reductions.py:369, `_to_DM_ZZ_QQ`). Symbolic entries use `_row_reduce_list` with `simpfunc=simplify` (reductions.py:376-382). In `_find_reasonable_pivot` (determinant.py:56-120), `is_zero` is None for a bare Symbol. The code then calls `simplify(x)` per entry (pass 2) and `x.equals(0)` (pass 3), and finally assumes the entry is non-zero. Each elimination step (`cross_cancel`, reductions.py:54) and the normalisation step rebuild whole rows of unsimplified expressions.

**Performance principle.** Use a canonical-form representation or closed form for work that heuristic simplification and randomized equality testing would otherwise do per element.

**Evidence.** Static only; see the line references above. `workload.py` clears the cache before every call, so there is no memoization benefit across repeats. `_simplify` (utilities.py:69) is the full `simplify`.

**Impact.** Critical path of the workload, per call. Growth is unknown. For 2x2 the loop count is tiny, so the cost is per-call simplify/equals overhead.

**Conditions.** Dense symbolic matrices inverted via default `inv()`, cold cache. It does not apply to rational matrices, which take the DomainMatrix path.

**Counter-evidence.** The 2x2 loop is small and bounded (bounds impact). No profile exists, which is why confidence is Medium rather than High.

**Why this might not matter.** For one-off interactive use this is a constant overhead, and exact zero detection is why GE uses simplify.

**Recommendation.** Convert symbolic matrices to a DomainMatrix over a polynomial ring or fraction field (for example ZZ[a,b] or QQ(a,b)) and invert there. Alternatively, add a closed-form 2x2/3x3 determinant and adjugate path. Either removes `simplify` and `equals` from the default path.

**Alternatives.** (1) The fraction-field DomainMatrix, preferred because zero tests become exact and cheap at any size. (2) The closed-form small-n shortcut, which is simple but covers only tiny sizes. (3) A lighter default simplify, which may change results.

**Trade-offs.** Entries that are not polynomial or rational expressions (functions, for example) need a fallback to the generic path. The output form may differ from today's.

**Validation.**
- Metric: wall time of `Z.inv()` on a cold cache, plus the cumulative share of `simplify` and `equals`.
- Expectation: the simplify and equals share goes to about zero and total time drops.
- Falsifier: a profile showing those two are a small fraction of the call.
- Command (safe-on-production): `python -m cProfile -s cumtime workload.py` to see where the time goes.

## 6. Considered, not reported

- The flat-list copy and column slicing in `_row_reduce_list` (reductions.py:47) is linear overhead and negligible next to symbolic simplification.
