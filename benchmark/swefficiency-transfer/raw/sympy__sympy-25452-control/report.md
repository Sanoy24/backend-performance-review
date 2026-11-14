# Performance review: Matrix([[a,b],[b,a+b]]).inv()  (sympy)

Path of the workload: `Matrix.inv()` -> `_inv` (matrices/inverse.py:358) with method=None -> 'GE' (line 447) -> `_inv_GE` (inverse.py:223) -> `Matrix.hstack(M, eye)` -> `_rref` (reductions.py:295) -> `_row_reduce` -> `_row_reduce_list` (reductions.py:10) -> `_find_reasonable_pivot` (determinant.py:19) + `_dotprodsimp` per cell. The workload clears the cache before every call, so everything is cold on each of 100 runs. This was reviewed statically only; the estimates below come from reading the code.

## Findings (most serious first)

### 1. Default 'GE' inverse of a symbolic matrix goes through generic Gaussian elimination with full `simplify()` and `dotprodsimp` (High)
- inverse.py:241-247 (`_inv_GE`), inverse.py:446-447 (default method), reductions.py:66-118.
- For symbolic entries, `_to_DM_ZZ_QQ` (reductions.py ~260-275) returns None. The code then falls to the generic list-based row reduction on a 2x4 augmented matrix. That path does cross-multiplication row ops, and every cell goes through `isimp`, which defaults to `_dotprodsimp` (utilities.py:30-48; the first positional arg is `deffunc`, so dotprodsimp is on by default). A final normalise-last pass divides every entry by its pivot and runs `_dotprodsimp` on it again.
- `dotprodsimp` (simplify.py:1959+) counts ops, swaps in dummies, and calls `cancel` and `count_ops`. It does this several times per cell and has high fixed overhead. This overhead dominates for a 2x2 matrix.
- Fix: for small matrices (n <= 3, as `_det` already does at determinant.py:684-695), use the closed-form adjugate/det formula in `_inv`. For n=2 that is `[[d,-b],[-c,a]]/(ad-bc)`. More generally, for symbolic input use `ADJ`, or a DomainMatrix over a polynomial or fraction field (`ZZ[a,b]` / `QQ.frac_field(a,b)`). `_det_DOM` already exists for determinants. Expect a large constant-factor win (likely 10x or more for 2x2).

### 2. Pivot search triggers `simplify()` and `.equals(0)` on plain symbols (High)
- determinant.py:56-120, called from reductions.py:67-69 with `simpfunc=_simplify` (`_rref` line 379).
- The pivot is the symbol `a`. `_iszero(a)` returns None, because `a.is_zero` is undecidable for a generic symbol. So pass 1 finds no definite non-zero. Pass 2 (line 79-88) calls full `simplify(a)` and re-tests. Pass 3 (line 101-110) calls `a.equals(0)`. Only then does it "assume non-zero" (line 119-120). `simplify` and `equals` are among the most expensive generic calls in sympy. They are made on a trivial atom, and again for subsequent pivots and for entries such as `a+b`.
- Fix: short-circuit in `_find_reasonable_pivot` when `x.is_Symbol` or `x.is_Atom` and not a number. Such a value cannot simplify, so skip the simplify and equals passes. Or provide a cheap `simpfunc` (e.g. `_dotprodsimp` or `cancel`) for the inverse path instead of `simplify`. Also skip re-simplifying elements that are already `Atom`s or already simplified.

### 3. `_dotprodsimp` applied cell by cell, including trivially simple cells (Medium)
- reductions.py:54-58 (`cross_cancel`), 91-92, 117-118.
- Every cell of every row op is passed through dotprodsimp, including the identity-half cells that are 0 or 1 and the cells of the pivot row. `cross_cancel` also processes the whole row, including columns left of the pivot that are already 0.
- Fix: skip cells where both operands are zero or numbers; start `cross_cancel` at the pivot column (`range(piv_col, cols)`, since columns before it are zero); call `isimp` only when the result is not an Atom. Bypass `isimp` when the result `.is_Atom`.

### 4. Redundant matrix copies and constructions (Low)
- inverse.py:241 (`M.as_mutable()` copy plus `hstack`/`eye` creation), reductions.py:127-131 (`list(M)` then `_new`), inverse.py:247 (`red[:, big.rows:]` slice then `M._new`), inverse.py:475 (`M._new(rv)` again). Each goes through the Matrix constructor and `_sympify` on every element.
- Fix: operate on the flat list directly. Return the slice as the inverse without the extra `_new` when it is already the right class. Each copy is small for 2x2 but adds fixed per-call overhead.

### 5. Cold-cache cost is inherent to the workload; no memoisation of simplify results (Low / informational)
- `clear_cache()` in the setup means the sympy `@cacheit` caches (used heavily in simplify, assumptions, `Add`/`Mul` construction) are cold. So the work in 1-3 is paid in full each run. There is nothing to fix in the library for this, but it explains why the effect of findings 1-2 is so large. Caching an inverse for an identical matrix is not recommended; the cheap closed-form route in finding 1 avoids the need.

## Suggested order of work
1. Add a small-n (1x1, 2x2, 3x3) closed-form branch before the method dispatch in `_inv` (inverse.py ~446), falling back to GE for singular or undecidable cases (detect with the determinant's `equals(0)`).
2. Guard `_find_reasonable_pivot` passes 2 and 3 against Symbol and Atom inputs.
3. Trim per-cell `dotprodsimp` calls in `_row_reduce_list`.
4. Remove redundant copies.
