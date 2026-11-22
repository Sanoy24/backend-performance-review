# Performance review: constraint validation in scheduling

Repository: flexmeasures-2641-control. Static review only, nothing was run, so the costs below are estimates from reading the code.

## Where the time goes

`StorageScheduler._prepare` (`flexmeasures/data/models/planning/storage.py`) loops over every device. At lines 1642-1663 each pass calls `validate_power_constraints` (line 3964) and, for devices with a stock, `validate_storage_constraints` (line 4009). Both are built on `validate_constraint` (line 4183).

Per device that is 3 power checks and up to 14 storage checks, so about 17 calls to `validate_constraint`. The work is linear in devices but with a large constant. The constant is large because each call goes through string parsing and `pandas.eval`, and copies the whole constraints DataFrame several times.

## Findings, most important first

### 1. `validate_constraint` is string-expression based and copies the frame repeatedly (high)
File: `flexmeasures/data/models/planning/storage.py`, lines 4204-4228.

- `constraints_df.astype(float).fillna(0)` runs twice per call, once for the left-hand side (lines 4217-4222) and once for the right-hand side (lines 4223-4228). Each run copies every column, including columns the expression never uses. With about 17 calls per device and 2 copies each, that is about 34 full-frame copies per device.
- Each side goes through `DataFrame.eval` on a string. This re-parses the expression and builds an expression tree every time. For small frames (one schedule horizon, a few hundred rows) this overhead is much larger than the arithmetic.
- `.round(6)` is applied to both sides on top of that.
- Fix: do the comparison with plain vectorised pandas or numpy. Convert the frame once per device with `astype(float).fillna(0)` and reuse it for all checks. Replace the string expressions with direct column arithmetic, for example `c["derivative_min"] <= c["derivative_max"]` and `c["equals"] - c["equals_prev"] <= c["derivative_max"] * factor`. Only convert expressions to strings when you build the error message for a violation.

### 2. Regex-based `sanitize_expression` runs for every column on every call (high)
File: `flexmeasures/data/models/planning/storage.py`, lines 4163-4180 and 4208-4213. The pattern builder is at lines 4145-4160.

- `sanitize_expression` is called for both the left and right expression. For every column in the frame it runs `re.search` and then `re.sub`. Each of these builds the pattern via `get_pattern_match_word`, which calls `re.escape` and concatenates strings again.
- The frame has about 15 or more columns, plus the temporary `(t)`, `(t-1)` and `factor_w_wh(t)` columns that `validate_storage_constraints` adds. That gives about 4 x 20 x 17 = 1,400 regex operations per device, all repeated identically for each device.
- The expressions are the same for every device (fixed strings, same column names). The result is therefore fully cacheable, for example with `functools.lru_cache` keyed on (expression, tuple(columns)). Better, remove the string approach as in finding 1.

### 3. Failure-path message building is a regex loop inside a loop (medium)
File: `flexmeasures/data/models/planning/storage.py`, lines 4255-4271, with `create_constraint_violations_message` at line 3705.

- For every failing time step the code loops over every column, runs `re.sub`, and does a scalar `constraints_df.loc[dt, column]` lookup. That is O(violating steps x columns) regex calls plus slow `.loc` access.
- When a device is infeasible for the whole horizon (for example `min > max` everywhere) this is hundreds of steps times 17 checks.
- The message is only used in a ValueError that aborts the schedule. The full per-step list is rarely useful. Cap it (report the first N violations per constraint plus a count) and build the text only for the involved columns, using `.at` lookups or one `to_dict`.

### 4. `validate_storage_constraints` and `validate_power_constraints` each copy and rename the frame, and `prepend_series` sorts (medium)
File: `flexmeasures/data/models/planning/storage.py`, lines 3979-3987, 4042-4050 and 4276-4288.

- Both functions do `constraints.copy()` and rename all columns, with a string `replace` and an `(t)` suffix for each. They both run on the same frame for the same device.
- `prepend_series` is called three times. Each call copies the series, inserts one element, calls `sort_index()` (an O(n log n) sort on an already sorted index) and then shifts it. The same result is obtained with `series.shift(1)` and a fill of the first value, which is O(n) and has no copy and sort.
- Fix: do the rename once, or skip it by using the real column names. Compute the lagged series with `shift(1)` and set the first element directly.

### 5. Shared-stock membership test is nested inside the per-device loop (low to medium)
File: `flexmeasures/data/models/planning/storage.py`, lines 1521-1526.

- For each device the code iterates over every stock group, and `d in devices` is a list scan. That is O(devices x total group members), which is quadratic when many devices share stocks.
- Precompute a `set` of secondary device indices (every device except the first of each group) once before the loop. Each lookup then costs O(1).
- The related code at lines 1667-1670 copies every device's `stock delta` series even when no stock groups exist. Do this copy lazily.

### 6. Validation cannot be disabled cheaply by callers, and the frame is revalidated even when nothing changed (low)
File: `flexmeasures/data/models/planning/storage.py`, line 1642 and the `skip_validation` parameter at line 148.

- The checks run on every `compute` call, including repeated scheduling for the same inputs.
- Consider a vectorised fast path as the default. Keep the detailed message generation only for the rare failing case. Do this by running one cheap boolean check per rule, and calling the message-building code only when a check fails.

## Suggested order of work

1. Rewrite `validate_constraint` and the callers to use direct pandas or numpy comparisons on a single pre-converted float frame (findings 1 and 2). This is expected to remove most of the validation time, plausibly an order of magnitude.
2. Replace `prepend_series` and the double rename (finding 4).
3. Bound the failure message (finding 3).
4. Fix the stock-group lookup (finding 5).

## Confidence and caveats

I did not profile anything. The relative ranking comes from counting copies and regex operations per device. The exact ratio of validation time to solver time is unknown. For large device counts the solver itself may dominate, but the validation cost is pure overhead and is cheap to remove. I only reviewed the validation path in `storage.py` and its direct caller. I did not review the rest of `_prepare` for other per-device costs.
