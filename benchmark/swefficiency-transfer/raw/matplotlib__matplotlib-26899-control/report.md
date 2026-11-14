# Performance review: `fig_many.savefig(format='svg', bbox_inches='tight')`

The workload saves a 30-subplot figure to SVG with `bbox_inches='tight'`. Each subplot has one 100-point line, and `set_yticks([])` hides the y ticks. I read the code only. I did not run anything, so none of the cost estimates below are measured.

## Finding 1: Figure.get_tightbbox visits every Axes child twice (high)

- Where: `lib/matplotlib/figure.py`, lines 1711-1731 in `FigureBase.get_tightbbox`. Its inputs are `FigureBase.get_default_bbox_extra_artists` (lines 1669-1678) and `_AxesBase.get_tightbbox` (`axes/_base.py`, lines 4367-4452).
- What happens:
  - `get_default_bbox_extra_artists` (figure.py, 1675-1677) extends the list with `ax.get_default_bbox_extra_artists()` for each Axes. That is each Axes' children: lines, spines, patch, text and so on.
  - The loop at 1717-1720 calls `a.get_tightbbox(renderer)` on every one of them.
  - The loop at 1722-1731 then calls `ax.get_tightbbox(renderer, ...)` for each Axes. At `_base.py` 4441-4450 that method fetches the same default artists again and calls `get_tightbbox` on each.
  - Each Axes child is therefore measured twice per `savefig`. Measuring means Artist.get_tightbbox, then get_window_extent, then transforms and path extents.
- The Axes also appears in the figure's own children list, since `get_children()` includes the axes. So `ax.get_tightbbox` runs once from the first loop and again from the second. That makes the Axes-level work, including the axis tick and label bboxes, run twice or more.
- Severity: high. The work is duplicated roughly 2x for 30 Axes, so it is probably the largest avoidable cost.
- Fix:
  - Build the figure-level list from non-Axes children only (the figure patch, figure texts, legends and so on).
  - Let the per-Axes loop cover the Axes children.
  - Alternatively, skip `Axes` instances in `FigureBase.get_default_bbox_extra_artists` when called from `get_tightbbox`, or dedupe by identity.
  - The same duplication exists for `bbox_extra_artists` supplied by the caller, so dedupe there too.

## Finding 2: Axis ticks are rebuilt and re-measured on every get_tightbbox call (medium)

- Where: `lib/matplotlib/axis.py`, lines 1255-1305 (`_update_ticks`), 1307-1314 (`_get_ticklabel_bboxes`) and 1316-1365 (`Axis.get_tightbbox`).
- What happens:
  - `_update_ticks` runs on every call. It computes tick locations and formats the labels with `format_ticks`.
  - It sets text on `tick.label1` and `tick.label2` for both major and minor ticks. Setting text invalidates the cached layout.
  - It runs a Python loop over the ticks that calls `get_transform().transform` once per tick.
  - `_get_ticklabel_bboxes` then calls `get_window_extent` for each label, which does text layout and font metrics.
- The same pass runs again in `Axis.draw` (lines 1383-1384).
- The x axes are the cost here, because the y ticks are emptied. That is 30 x-axes, each with about 5-10 ticks.
- Finding 1 doubles this work. With `savefig`, the figure gets one full draw, then `get_tightbbox`, then the real SVG draw, so the tick work happens about 4 times.
- Severity: medium.
- Fix:
  - Skip text updates when the new text equals the old text. `Text.set_text` already does this check, but `update_position` and the transform calls still run.
  - Reuse the tick state computed in the preceding draw when nothing has changed. Caching the ticks to draw and the label extents per renderer would help.
  - Vectorise the per-tick transform in `_update_ticks`. For example, transform the whole location array at once instead of looping at line 1296.

## Finding 3: Text extents are recomputed through the full draw, tight bbox and SVG draw sequence (medium)

- Where: `lib/matplotlib/backend_bases.py`, lines 2127-2144 and 2164-2175 (`print_figure`).
- What happens:
  - Tight-bbox saving does a complete `figure.draw` into a draw-disabled renderer (2139-2140) that exists only to compute layout.
  - It then calls `get_tightbbox` (2143), then `adjust_bbox` (2156), then the real SVG draw (2169).
  - The first two stages each lay out and measure every tick label, title and axis label. The SVG renderer does the same measurements again.
- Severity: medium. It is inherent to the design, but it multiplies the text-measurement cost across 30 Axes.
- Fix:
  - Cache `get_window_extent` results for unchanged text and renderer, keyed on the text, the font properties and the dpi. At minimum, do not invalidate text layout when `set_text` is given the same string.
  - Avoid a full draw when a layout engine has not changed anything.
  - Use a stripped-down pass that only updates ticks, not lines, for the pre-draw.

## Finding 4: adjust_bbox and the Axes locator override do per-Axes Python work, with `apply_aspect` repeated (low)

- Where: `lib/matplotlib/_tight_bbox.py`, lines 25-37, and `axes/_base.py`, lines 4417-4419.
- What happens:
  - `adjust_bbox` loops over all Axes in Python. For each one it calls `get_position(original=False).frozen()`, installs a lambda locator, and monkey-patches `apply_aspect`.
  - `fig.transFigure.invalidate()` then invalidates the whole transform tree.
  - `get_tightbbox` calls `apply_aspect(...)` for each Axes, and `Axes.draw` calls it again.
- This is only about 30 iterations. The cost is mostly the invalidation, which forces transform recomputation for every artist before the SVG draw.
- Severity: low.
- Fix: avoid invalidating more than necessary, and skip `apply_aspect` when the aspect is 'auto' (this is cheap to check).

## Finding 5: SVG renderer overhead for 30 Axes (low, not read in detail)

- Where: `lib/matplotlib/backends/backend_svg.py`, `print_svg` (line 1291) and `draw_path` (660).
- With `bbox_inches` the SVG draw runs on a fully re-transformed figure. Each Line2D produces a path string through `_convert_path` and XML writer calls. For only 100 points per line this is minor.
- Severity: low. The per-element XML writer overhead and per-artist `open_group` and `close_group` calls scale with the artist count (about 30 x (spines + ticks + labels)). If profiling shows it matters, batch the path-string generation.

## Priority

Fix Finding 1 first. It is simple, safe, and removes roughly half of the `get_tightbbox` cost. Then address Findings 2 and 3 to reduce the repeated tick and text measurement.