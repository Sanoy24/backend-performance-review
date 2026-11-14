# Performance review: matplotlib savefig (svg, bbox_inches='tight') with many subplots

## 1. Decision summary

Overall: one material finding (static analysis only; nothing was run or measured). Figure.get_tightbbox measures every Axes twice, which is wasted work on a many-subplot figure saved with `bbox_inches='tight'`.

Actions:
1. Make Figure.get_tightbbox measure each Axes once (PERF-001).

Decision-changing unknowns:
1. What share of savefig time is spent in get_tightbbox compared with the SVG draw and serialization. This sets the size of the gain.
2. Whether the renderer text layout cache makes the second pass cheap.

Validation command (safe-on-production): `python -m cProfile -s cumtime workload.py`

## 2. Scope and method

Full review limited to the code the workload exercises. Files read: `lib/matplotlib/backend_bases.py` (print_figure), `lib/matplotlib/figure.py` (get_default_bbox_extra_artists, get_tightbbox), `lib/matplotlib/axes/_base.py` (Axes.get_tightbbox, get_default_bbox_extra_artists), `lib/matplotlib/axis.py` (Axis.get_tightbbox). The SVG backend draw was not read. No observability, benchmarks or runtime evidence exist or were supplied. Review confidence: Medium.

## 4. Workload model

workload.py builds a figure of 10 x 3 Axes, each with one 100-point line, then times `savefig(BytesIO, format='svg', bbox_inches='tight')`, repeated 25 times. Workload is repository-derived; no user answers were requested.

## 5. Critical path

savefig -> print_figure -> full draw with output disabled -> Figure.get_tightbbox -> adjust_bbox -> SVG draw and serialization.

## 7. Findings

### PERF-001: Axes tight bboxes computed twice in Figure.get_tightbbox
- Root cause: ROOT-001
- Severity: Medium; Confidence: High; Priority: P2; Category: io; Tags: quick-win, needs-measurement
- Location: `lib/matplotlib/figure.py:1669` (get_default_bbox_extra_artists) and `:1712`, `:1717` (get_tightbbox)

**Problem.** The figure-level default extra-artist list is built from `self.get_children()`, which includes every Axes. get_tightbbox calls `a.get_tightbbox(renderer)` for each, then loops over `self.axes` and calls `ax.get_tightbbox` again.

**Performance principle.** Do not recompute the same expensive value twice in one operation.

**Evidence.** figure.py lines 1673, 1712 and 1717. Axes.get_tightbbox (`axes/_base.py:4367`) runs apply_aspect, measures all tick label and axis label text through Axis.get_tightbbox, updates titles, and iterates the Axes children. No bbox-level caching was found. No runtime evidence exists.

**Impact.** Critical path of savefig with bbox_inches='tight'; per call; linear in Axes count; amplification 2x on the Axes pass.

**Conditions.** Any tight-bbox save or Figure.get_tightbbox call; most visible with many subplots as in the workload (30 Axes). The size of the saving is unmeasured.

**Counter-evidence.** Axes.get_default_bbox_extra_artists excludes Axis objects, so tick work is not repeated through the child list; the duplication is at the Axes level. The renderer text layout cache may make the second pass cheaper. Both bound the magnitude, not the existence of the duplicate call.

**Why this might not matter.** The SVG draw and serialization may dominate; a cached second pass could save little.

**Recommendation.** Exclude Axes from the figure-level default extra-artist pass (the explicit `self.axes` loop already handles them), or memoize per-Axes results within one call.

**Alternatives.** Preferred: exclude Axes from the figure-level list (minimal change). Alternative: per-call memoization (more general, more state).

**Trade-offs.** Must preserve behaviour for user-supplied `bbox_extra_artists` containing Axes and for subfigures; the union bbox must stay identical.

**Validation.** Metric: Axes.get_tightbbox calls per savefig and workload wall time. Expectation: calls fall from about 2 to 1 per Axes. Falsifier: a profile shows get_tightbbox is a small share of the time. Command: `python -m cProfile -s cumtime workload.py` (safe-on-production). Guard: a unit test counting calls for an N-subplot figure.

## Considered, not reported
- The extra full draw in print_figure before get_tightbbox (`backend_bases.py:2128`): required for correct final positions; no evidence it is avoidable.

## 8. Action plan
1. PERF-001: dedupe the Axes pass, then profile before and after.

## 9. Validation plan
Profile the workload with cProfile (safe-on-production); compare Axes.get_tightbbox call counts and total time before and after the change.
