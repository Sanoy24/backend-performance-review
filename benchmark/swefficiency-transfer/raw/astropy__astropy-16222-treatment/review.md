# Performance Review: astropy SkyCoord(representation) construction

## 1. Decision summary

### Overall assessment
One material finding. `SkyCoord(UnitSphericalRepresentation)` with no frame converts the input to a `SphericalRepresentation` carrying a fabricated `distance` of 1.0, then re-validates and re-copies all components, including a materialized distance array, during frame construction. The work is O(n) with several full passes over 1e6-element arrays. This is a static review; nothing was measured.

### Top three actions
1. PERF-001: in `_parse_coordinate_arg`, drop the `distance` component when the input representation is a `UnitSphericalRepresentation`, as the SkyCoord/frame branch already does.

### Key unknowns
- Share of init time spent on the extra distance array and repeated Longitude/Latitude validation versus the unavoidable lon/lat copy (needs a profile).

### Validation commands
- `python workload.py` before and after the change (not-safe-on-production; local benchmark only).

## 2. Scope and method
Full review scoped to the path the workload exercises: `SkyCoord.__init__` -> `_parse_coordinate_data` -> `_parse_coordinate_arg` (BaseRepresentation branch) -> `UnitSphericalRepresentation.represent_as` -> `SphericalRepresentation` / `BaseRepresentation.__init__` -> `BaseCoordinateFrame.__init__` / `_infer_data`. A pure library: no datastores, brokers or services, so those layers are omitted. Read-only; the workload was not run. No observability artifacts were consulted.

## 4. Workload model
Derived from `workload.py`: `SkyCoord(self.array_repr)` where `array_repr` is a 1e6-element `UnitSphericalRepresentation` in degrees, default ICRS frame, repeated 200 times. Single-threaded and CPU/memory-bound.

## 7. Findings

### PERF-001 - UnitSpherical input is inflated to Spherical with a distance array and re-validated
- Root cause: ROOT-001
- Severity: Medium; Confidence: High; Priority: P2
- Category: memory
- Location: `astropy/coordinates/sky_coordinate_parsers.py`, `_parse_coordinate_arg`, BaseRepresentation branch (`data = coords.represent_as(frame.representation_type)` then `values = [getattr(data, name) for name in repr_attr_names]`)

**Problem.** For a bare representation argument the code converts to the frame's `representation_type` (Spherical for ICRS) and takes every component including `distance`. For a `UnitSphericalRepresentation` input that fabricates `distance=1.0` and carries it through. The SkyCoord/frame branch of the same function deletes `distance` from the attribute lists when `coords.data` is UnitSpherical; the representation branch does not.

**Performance principle.** Do not materialize or reprocess data that carries no information; avoid repeated validation and copy passes over the same large array.

**Evidence.**
- `sky_coordinate_parsers.py`: the representation branch calls `represent_as(frame.representation_type)` and reads all components; the distance-removal logic exists only in the SkyCoord/frame branch above it.
- `representation/spherical.py`: `UnitSphericalRepresentation.represent_as` shortcut builds `SphericalRepresentation(lon, lat, distance=1.0, copy=COPY_IF_NEEDED)`.
- `representation/base.py`: `BaseRepresentation.__init__` broadcasts the components; when a component's shape changes and `copy` is true it does `bc_attr.copy()` (the code itself notes a "double copy"). `SkyCoord.__init__` then calls `frame_cls(copy=copy, ...)` with copy=True, so the scalar-broadcast distance becomes a full N-element array and lon/lat are copied.
- `angles/core.py`: `Latitude.__new__` runs `_validate_angles` (`np.abs(...) > limit`, `np.any`) over all N values on every construction; this happens in the parser's component construction and again in the final frame data.
- `baseframe.py:_infer_data`: downgrades to UnitSpherical only when `distance` is absent from the kwargs; the parser always supplies it.
- No runtime evidence; the magnitude is not measured.

**Impact.** Position: critical path (the workload is this call). Frequency: per call. Growth: O(n) in coordinates. Blast radius: endpoint (any caller building a SkyCoord from a representation without a frame). One extra N-element float array per call, and several redundant full-array passes.

**Conditions.** Large arrays (the workload uses 1e6) passed as a UnitSphericalRepresentation with no frame. For scalars and small arrays the overhead is a small constant per call.

**Counter-evidence.** Looked for a UnitSpherical shortcut in the representation branch and in `_infer_data`; only the SkyCoord/frame branch has one. The lon/lat copies and one validation are necessary regardless, which bounds the saving.

**Why this might not matter.** numpy passes are fast and the necessary lon/lat copy still takes part of the time, so the saving may be a fraction, not a multiple. The result type of `.data` also changes (Spherical with unit distance versus UnitSpherical), which downstream code or tests may depend on.

**Recommendation.** In the BaseRepresentation branch, mirror the SkyCoord/frame branch: if `coords` is a `UnitSphericalRepresentation`, remove `distance` from `repr_attr_names`, `units`, `frame_attr_names` and `repr_attr_classes` before building values, so the frame is built via UnitSpherical through `_infer_data`'s existing downgrade. This removes the work rather than masking it.

**Alternatives.** (a) Pass `copy=False` to the final frame creation: smaller benefit, aliasing risk. (b) Skip validation for trusted Latitude inputs: wider API change. Preferred: drop distance for UnitSpherical input.

**Trade-offs.** `.data` type changes for these SkyCoords (consistent with the SkyCoord-from-frame behaviour); tests relying on a Spherical result need checking.

**Validation.** Metric: wall time and peak memory of `SkyCoord(array_repr)` at N=1e6, and `type(sc.data)`. Expectation: lower time, one fewer N-element array. Falsifier: no measurable change. `python workload.py` before and after (not-safe-on-production; local only).

## 8. Prioritized action plan
- P2: PERF-001.

## 9. Validation plan
Run the workload before and after; profile with `python -m cProfile -s cumtime workload.py` (not-safe-on-production, local only) to split time between validation, copies and the distance array.

## 11. Notes on this review
Static only; no numbers measured. Confidence is High on the mechanism and unknown on magnitude.
