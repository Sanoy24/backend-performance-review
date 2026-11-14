# Perf review: pd.to_datetime(list_of_36k_strings, format='%Y-%d-%m %H:%M:%S%z')

Workload: ~36,500 strings, all "+01:00" except one "+02:00" (so mixed offsets -> object Index result).

## 1. Per-element Timestamp construction + tz_localize in Python (HIGH, dominant)
pandas/core/tools/datetimes.py:316-318 (`_return_parsed_timezone_results`), reached from line 507-508.
`[Timestamp(res).tz_localize(zone) for res, zone in zip(result, timezones)]` builds one Timestamp per row and localizes each separately, going through the generic tz machinery (pytz FixedOffset) in the Python interpreter. Each iteration costs several microseconds and allocates 2 Timestamps. It is then wrapped in np.array(...) (extra pass, object array creation).
Fix: group rows by distinct tz object (few unique offsets). For each unique tz, vectorize: DatetimeIndex(result[mask]).tz_localize(tz) (or compute UTC ints directly by subtracting the offset in ns), then convert to UTC i8 once. Where all offsets are equal, return a tz-aware DatetimeIndex directly without a Python loop. Alternatively do the offset arithmetic inside array_strptime (return UTC i8 plus the tz/offset), as later pandas versions do.

## 2. Second Python loop for utc=True (MEDIUM; not hit by this workload but same function)
datetimes.py:319-328. Another per-element list comprehension calling tz_convert("utc") / tz_localize on each Timestamp. Fix: with unique-tz grouping, the whole result is just UTC i8 values; no second pass needed.

## 3. Redundant `any(tz is not None for tz in timezones)` scan (LOW)
datetimes.py:507. A Python generator over an object array of 36k entries. Fix: have array_strptime return a flag (found_tz) or check with a cheap vectorized test (e.g. `timezones` all-None known from the cython loop).

## 4. Repeated parse_timezone_directive / pytz.FixedOffset per row (MEDIUM)
pandas/_libs/tslibs/strptime.pyx:303 and 533-577. For every row the "%z" string is re-parsed (slicing, several int() calls, string padding, `pytz.FixedOffset(total_minutes)`), though only 2 distinct values exist. pytz.FixedOffset does have an internal cache, but the string parsing around it is repeated each row. Fix: memoize per distinct z string in a local dict inside array_strptime (`tz_cache[z]`), also return identical tz objects so downstream grouping by identity is O(1).

## 5. Other per-row overhead in array_strptime loop (LOW/MEDIUM)
strptime.pyx:219-310: `found.groupdict()` creates a dict per row, then iterates keys with a long if/elif chain doing dict lookups by key (`found_dict["Y"]` etc.) and `int()` on strings; lines 349-350 and 365 construct `datetime.date` objects (2-3 per row) to compute julian/weekday that is never used for output. Fix: skip julian/weekday computation unless needed (only validate date via dts/check), use `found.group`/`match.lastgroup` or positional groups, and avoid groupdict.

## 6. Mixed-offset result is an object Index (informational)
datetimes.py:330. The final `Index(tz_results)` of Timestamps infers over 36k objects (another pass). Unavoidable semantically for mixed offsets, but with fix #1 the common single-offset case would return a fast DatetimeIndex.

Priority: 1 (largest by far) > 4 > 5 > 2 > 3 > 6.
