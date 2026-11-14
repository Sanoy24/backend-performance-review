# Performance review: Array.shape (dask__dask-7023)

Workload: build a (10^4, 10^4) random array, replace `_chunks` with ((20,)*50, (10,)*100), then time `array.shape` 1000 times.

Code path: `Array.shape` (dask/array/core.py:1239-1240) calls `cached_cumsum(c, initial_zero=True)[-1]` for each dimension. `cached_cumsum` is in dask/array/slicing.py:1285-1311. It uses `_HashIdWrapper` (1255-1272) and `functools.lru_cache` `_cumsum` (1275-1282).

## 1. shape builds a full prefix-sum just to read the total (core.py:1240) - medium
- `shape` only needs `sum(c)`. It goes through `cached_cumsum`, which allocates a `_HashIdWrapper`, does an lru_cache lookup with a tuple key, and can build the whole cumulative tuple.
- The cumulative tuple is O(nblocks) per dimension (51 and 101 entries here). It is computed on the first miss for every distinct chunks tuple. It is built in Python through toolz `accumulate(add, ...)`, so it is slow per element.
- Each `.shape` call also pays Python-level overhead: a generator, a wrapper object, an lru_cache call, and a hash and eq through Python-defined `__hash__`/`__eq__` methods. That is several microseconds per call, and `.shape` is called very often inside dask (slicing, reductions, blockwise).
- Fix: compute the shape once when chunks are set and store it, for example `self._shape`, invalidated on the `_chunks` assignments at core.py:1126, 1233 and 1594. Alternatively use `tuple(sum(c) for c in self.chunks)`. For unknown (NaN) chunks `sum` gives NaN as the current code does. Use `math.fsum` or `sum` as appropriate.

## 2. Cache keyed by identity, bounded at 128 entries, and it keeps chunk tuples alive (slicing.py:1275-1282) - low to medium
- `lru_cache()` has maxsize=128. The cache key holds a strong reference to the chunks tuple, so up to 128 chunk tuples stay alive. Keeping them alive is also what makes the id-based key safe.
- In graphs that use many distinct chunk tuples (for example rechunk, or many arrays created per iteration), the cache thrashes. Every miss then recomputes an O(nblocks) accumulate, and that cost is paid on `.shape`.
- A cache hit is only valid because the old tuple is retained. Dropping the strong reference would make `id()` reuse return wrong results.
- Fix: avoid the global cache for `.shape`. Store per-array derived values (cumsums and shape) on the Array, or on the chunks object, so lifetime matches the array and nothing is shared or evicted.

## 3. Non-tuple input is converted and hashed by value (slicing.py:1309-1310) - low
- If `seq` is a list, `tuple(seq)` is built and then hashed in O(n) on every call. No identity shortcut applies. This is not hit by this workload, because new_chunks are tuples.
- Fix: normalize chunks to tuples at the point of assignment so the fast path is always used.

## Note on the workload
Because setup calls `.shape` once, the timed call is a cache hit. The measured cost is therefore the fixed per-call overhead in item 1, not the O(nblocks) accumulate. Item 1 is the main target: storing the shape, or summing directly, removes the wrapper allocation, hashing and lru_cache lookup from the hot path.
