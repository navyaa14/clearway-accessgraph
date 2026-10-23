# Real-photo regression set (to be populated)

No real photographs are bundled: none could be sourced with clear provenance and consent in the build environment.
Add your own (e.g. stills from your Ring Playground session or your own doorstep photos) — they are evaluated by
`python -m eval.run_real` and by `tests/test_real_images.py` (which skips while this folder has no manifest).

Recommended coverage: shadows, plants moving, a person on the path, changing sunlight, multiple objects, parcels at the
frame edge, a parcel beside (not on) the path, night/IR frames.

## manifest.json

```json
{
  "cameras": {
    "porch": {
      "reference": "porch_clear.jpg",
      "route": [[0.42,0.98],[0.58,0.98],[0.55,0.30],[0.47,0.30]],
      "drop_zone": [[0.70,0.60],[0.85,0.60],[0.85,0.75],[0.70,0.75]],
      "floor_points_img": null, "floor_points_world": null, "clearance_cm": null
    }
  },
  "cases": [
    {"image": "parcel_on_path.jpg", "camera": "porch", "label": "obstructed", "notes": "box centred on walkway"},
    {"image": "parcel_beside_path.jpg", "camera": "porch", "label": "clear"},
    {"image": "lens_wet.jpg", "camera": "porch", "label": "unverifiable"}
  ]
}
```

Labels: `obstructed`, `clear`, `unverifiable` (a human could not judge from the image). Polygons are normalised image
coordinates (you can copy them from the calibration editor's saved profile via `GET /api/households/{id}/cameras/{cam}/calibration`).
The key safety metric is **false-clear**: an `obstructed` case predicted `NO_OBSTRUCTION_OBSERVED`.
