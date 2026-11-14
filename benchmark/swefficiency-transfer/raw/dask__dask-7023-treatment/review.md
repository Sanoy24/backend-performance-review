# Performance review: dask.array `Array.shape` (workload: repeated `array.shape`)

Mode: full, read-only, static. No runtime evidence supplied; nothing was run.

## Decision summary
- Assessment: one small, bounded finding. The code path is already identity-cached; no superlinear cost was found.
- Action 1 (optional): make `Array.shape` avoid per-access work (PERF-001), only if profiling shows it matters.
- Unknown: how often `.shape` is hit in real graphs and whether the per-call cost is visible against the rest of the work.
- Validation: run the supplied workload under `python -m timeit`/cProfile before and after (safe-on-production: not applicable, local only).

## Scope and observability
Workload: `array._chunks = ((20,)*50, (10,)*100)` then 1000 timed `array.shape` reads. `shape` (dask/array/core.py:1239) calls `cached_cumsum(c, initial_zero=True)[-1]` per dimension. `cached_cumsum` (dask/array/slicing.py:1285) wraps the tuple in `_HashIdWrapper` and calls an `lru_cache`d `_cumsum`. No benchmarks or telemetry in the review scope were examined.

## Findings

### PERF-001 (Low, Medium confidence, P3): `Array.shape` redoes wrapper allocation and cache lookups on every access
- Location: dask/array/core.py:1239 (`Array.shape`), dask/array/slicing.py:1255-1311
- Problem: each `.shape` read does, per dimension, a generator step, a `_HashIdWrapper` allocation, an `lru_cache` lookup that invokes Python-level `__hash__` and `__eq__`, and a tuple index; then builds a new shape tuple. The result is an immutable function of `_chunks` and is not stored.
- Principle: do not recompute a value derived from immutable state on every read.
- Evidence (static): shape property is uncached (core.py:1239-1240); `_HashIdWrapper.__hash__`/`__eq__` are Python methods (slicing.py:1261-1272). The identity cache means the O(chunks) cumsum runs only on the first access per chunk tuple, so steady-state cost is constant per call (2 dimensions in the workload). No runtime evidence.
- Impact: critical-path in graph construction code that reads `.shape` often; per-call; O(1) per access (O(number of chunks) only on a cache miss); blast radius endpoint-local.
- Conditions: matters only if `.shape` is read very many times (the workload does 1000 reads) or if many distinct chunk tuples are created, since `_cumsum` is `lru_cache()` with its default size and could evict (slicing.py:1275).
- Counter-evidence: identity-keyed caching already avoids hashing the chunk values (slicing.py:1304-1307); nothing found that makes a single call super-linear.
- Why this might not matter: per-call cost is a small constant; an optimisation saves little unless `.shape` dominates.
- Recommendation: compute the shape once per chunk assignment and store it (for example alongside `_chunks`), or compute the last element with `sum(c)` directly, which needs no cumsum tuple and no cache.
- Alternatives: enlarge the `lru_cache` (does not remove per-call work); keep as is (preferred if profiling shows negligible share).
- Trade-offs: a stored shape must be invalidated if `_chunks` is reassigned, as the workload does by writing `_chunks` directly.
- Validation: metric is time per `array.shape`; expectation is a lower constant per call; falsifier is no measurable change on the supplied workload; safe-on-production (local micro-benchmark).

## Unknowns
- Real call frequency of `.shape` and whether chunk tuples exceed the 128-entry cache default.
