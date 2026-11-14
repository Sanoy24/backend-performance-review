# Performance review: SkyCoord(UnitSphericalRepresentation) with 1e6 elements, no frame

Workload: `SkyCoord(array_repr)` where array_repr is a 1e6-element UnitSphericalRepresentation; default frame is ICRS (representation_type = Spherical).

## Finding 1 (high): unit-spherical input is promoted to a full SphericalRepresentation with a fabricated distance
- `astropy/coordinates/sky_coordinate_parsers.py` lines 442-458 (`_parse_coordinate_arg`, BaseRepresentation branch), especially line 457 `data = coords.represent_as(frame.representation_type)` and 458 `values = [getattr(data, name) ...]` over all of lon, lat, distance.
- `astropy/coordinates/representation/spherical.py` lines 131-137 (`UnitSphericalRepresentation.represent_as` -> `SphericalRepresentation(lon, lat, distance=1.0)`).
- The frame branch (lines 401-410) deletes "distance" from the attribute lists when `coords.data` is UnitSpherical. The plain-representation branch has no equivalent. So a UnitSpherical input becomes a Spherical with distance=1.0. The distance is then validated and broadcast to a 1e6 array. Components are built at lines 554-559 as `Longitude`, `Latitude` and `Distance` objects, each constructed via Quantity validation. `Distance` also checks for negative values, which is an O(N) pass. Finally the ICRS frame is created holding a SphericalRepresentation with a dimensionful-looking 1e6 distance array.
- Cost: several extra O(N) passes and allocations, including an 8 MB distance array, a Distance construction with a min/negativity check, and a more expensive frame/representation init. For N=1e6 this is a large share of the runtime. The result is also semantically worse, because the coordinate now carries a spurious distance and no longer behaves as unit-spherical. Downstream transforms then take the slower dimensional path.
- Fix: in the BaseRepresentation branch, mirror the frame branch. If `isinstance(coords, UnitSphericalRepresentation)` (or the data has no distance), do not call `represent_as(Spherical)`. Take `lon` and `lat` directly, and drop "distance" from `frame_attr_names`, `repr_attr_names`, `repr_attr_classes` and `units`, as lines 402-410 do. The frame will then be built from a UnitSpherical, which is what `ICRS(ra=, dec=)` does. Alternatively, call `coords.represent_as(UnitSphericalRepresentation)` when no distance is present.

## Finding 2 (medium): redundant validation and copies of already-validated Quantity data
- `sky_coordinate_parsers.py` lines 554-559, and `sky_coordinate.py` lines 366-368 (`frame_cls(copy=copy, **frame_kwargs)` with `copy=True` default).
- The components are already `Longitude`/`Latitude` instances. They are re-wrapped via `repr_attr_class(value, unit=unit, copy=COPY_IF_NEEDED)` and then re-wrapped again in frame/representation init. With the default `copy=True` the final frame init copies the data, so each 1e6 array is copied again, and the wrap-angle and latitude range checks (`np.any` over the data) are repeated.
- Fix: skip re-wrapping when the value is already the right class with a compatible unit. Consider passing `copy=False` internally, since the parsed components are fresh objects owned by the parser.

## Finding 3 (low): list/dict churn per call
- `sky_coordinate_parsers.py` lines 381-383, and 202-231 (`_parse_coordinate_data`), plus `frame_cls(**frame_kwargs)` at `sky_coordinate.py` line 352, which builds a throwaway data-less frame just for parsing.
- These are O(1) per call. They only matter for scalar SkyCoord creation, not for this 1e6 workload.

## Priority
Fix Finding 1 first. It removes the largest chunk of O(N) work and is a pure speedup, since it avoids creating the spurious distance. Then look at Finding 2.
