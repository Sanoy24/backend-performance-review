# Performance review: np.core.records.fromarrays with 4000 fields

Workload: `fromarrays(arrays, names=names)` with 4000 length-5 arrays and 4000 names.

## 1. (High) `find_duplicate` is O(n^2) with a list copy per element
File: numpy/core/records.py, lines 76-83. It is called from `format_parser._setfieldnames` at line 194.

```python
for i in range(len(list)):
    if (list[i] in list[i + 1:]):
```
- `list[i+1:]` copies the tail of the list on every iteration. For n=4000 that is about 8M element copies, with refcount traffic.
- `in` then scans that copy linearly, with string comparisons. That is another ~8M comparisons.
- Total cost is quadratic in the number of fields. At 4000 fields this is probably the dominant cost of the whole call, in the tens of milliseconds per call.
- Fix: use a single pass with a set, which is O(n).
```python
def find_duplicate(list):
    seen = set(); dup = []
    for x in list:
        if x in seen:
            if x not in dup: dup.append(x)
        else:
            seen.add(x)
    return dup
```
  Or use `collections.Counter`. The names are strings and therefore hashable, so the output order can be kept identical. Expected speedup is large, since this step goes from quadratic to linear.

## 2. (Medium) Per-field Python loops over all fields in `fromarrays`
File: numpy/core/records.py, lines 586, 598-601, 622-626 and 631-632.
- Several separate O(n) Python passes run over the field list:
  - `asarray` per element.
  - `isinstance` and `dtype.str` per element (for the formats string).
  - A shape check per field. This does `descr[k]` (an O(1) dict lookup but with Python call overhead), `.ndim`, and a slice and tuple compare.
  - `_array[_names[i]] = arrayList[i]`, a field-by-name setitem per field. Each one does a dict lookup, builds a view and copies.
- These are linear, so they are smaller than finding 1. For 4000 fields they still cost several ms per call.
- Fixes:
  - Merge the passes.
  - Skip the redundant `isinstance` check, because `asarray` already guarantees an ndarray.
  - Skip the per-field shape check when `formats` were derived from the arrays themselves.
  - Build the structured array with a single `dtype.fields` lookup and `getfield`/`setfield` using offsets.

## 3. (Low-Medium) Dtype built via string round trip, then rebuilt
File: numpy/core/records.py, lines 146-216 and 601-602.
- Each array's dtype is turned into a string. The strings are joined into a 4000-field comma-separated format string. That string is parsed by `sb.dtype(formats)` at line 161, which creates auto-named fields `f0..f3999`.
- `_createdescr` (line 208) then builds a second dtype from names, formats, offsets and titles lists. The names dict and the fields dict get constructed twice.
- Fix: when dtypes come from the arrays, build the descr directly with `dtype({'names': names, 'formats': [a.dtype ...], 'offsets': ...})`, or `dtype(list(zip(names, dtypes)))`, in one step.
- Related: `_parseFormats` mutates a caller's list with `formats.append('')` when it is short. That is a correctness wart, not a performance issue.

## Priority
Fix 1 first. It is a trivial change and it removes the quadratic term. Findings 2 and 3 give smaller constant-factor gains.
