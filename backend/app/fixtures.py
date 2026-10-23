"""Labelled, reproducible demo + evaluation fixtures (SYNTHETIC_RENDER source).

Households, calibration profiles and scenario frames are generated from the
procedural renderer so that ground truth is exact. All are tagged
SYNTHETIC_RENDER and never presented as Ring footage.
"""
from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon

from .vision import geometry as G
from .vision.synth import DROP_ZONE_WORLD, Box, Camera, SceneSpec, normalized, render, world_poly_to_image

CAM = Camera()
FLOOR_WORLD = [[60.0, 120.0], [260.0, 120.0], [210.0, 360.0], [100.0, 360.0]]  # tile grout + walkway joint/edge crossings

ROUTE_WORLD = {
    # walkway from the door: the route the household marks
    "walkway": [(100, 58), (210, 58), (210, 560), (100, 560)],
    # household B also marks the door landing approach (wider turning space for a walker)
    "walkway_landing": [(85, 58), (214, 58), (214, 165), (210, 175), (210, 560), (100, 560), (100, 175), (85, 165)],
}

HOUSEHOLDS = {
    "hh_a": {"name": "Household A — ambulatory resident", "route_key": "walkway", "clearance_cm": 60.0,
              "need": "Walks unaided; needs about 60 cm of clear path."},
    "hh_b": {"name": "Household B — resident uses a walker", "route_key": "walkway_landing", "clearance_cm": 91.0,
              "need": "Uses a rollator walker; needs about 91 cm of clear path including the landing turn."},
}


def _clip(cam: Camera, pts: np.ndarray) -> np.ndarray:
    """Clip an image polygon to the visible frame (what a user could actually draw)."""
    frame = Polygon([(0, 0), (cam.width, 0), (cam.width, cam.height), (0, cam.height)])
    p = G.poly(pts).intersection(frame)
    return np.array(p.exterior.coords)[:-1]


def profile_for(hh_id: str, metric: bool = True, cam: Camera = CAM) -> dict:
    hh = HOUSEHOLDS[hh_id]
    route_img = _clip(cam, world_poly_to_image(cam, ROUTE_WORLD[hh["route_key"]]))
    zone_img = _clip(cam, world_poly_to_image(cam, DROP_ZONE_WORLD))
    fp_img = world_poly_to_image(cam, FLOOR_WORLD)
    return {
        "camera_id": "synthetic-porch-cam",
        "version": f"{hh_id}-cal-v1",
        "route": normalized(cam, route_img),
        "drop_zone": normalized(cam, zone_img),
        "clearance_cm": hh["clearance_cm"] if metric else None,
        "floor_points_img": normalized(cam, fp_img) if metric else None,
        "floor_points_world": FLOOR_WORLD if metric else None,
    }


# Demo storyline boxes (world cm)
DEMO_BOXES = {
    "initial": Box(190, 230, rot_deg=8),  # on walkway edge: clear for A (60cm), not for B (91cm)
    "partial_move": Box(203, 205, rot_deg=-6),  # nudged, still narrows the path below 91cm
    "moved_to_zone": Box(251, 203, rot_deg=3),  # in designated delivery area
    "centre_block": Box(158, 260, rot_deg=12),
}


_SCENE_CACHE: dict = {}


def scene(name: str, **kw) -> np.ndarray:
    """Render a labelled fixture scene. Deterministic, so results are cached (copies are returned)."""
    key = (name, tuple(sorted((k, repr(v)) for k, v in kw.items())))
    if key not in _SCENE_CACHE:
        if len(_SCENE_CACHE) > 64:
            _SCENE_CACHE.clear()
        _SCENE_CACHE[key] = _render_scene(name, **kw)
    return _SCENE_CACHE[key].copy()


def _render_scene(name: str, **kw) -> np.ndarray:
    kw = dict(kw)
    if kw.pop("_moved", False) or kw.get("camera") == "moved":
        kw["camera"] = Camera(yaw_deg=3.0, pitch_deg=42.0)  # someone bumped the doorbell after calibration
    elif kw.get("camera") is None:
        kw.pop("camera", None)
    if name == "reference":
        return render(SceneSpec(**kw))
    if name in DEMO_BOXES:
        return render(SceneSpec(boxes=[DEMO_BOXES[name]], **kw))
    raise KeyError(name)


def ground_truth(box: Box, hh_id: str, metric: bool = True, cam: Camera = CAM) -> dict:
    """Exact decision from the true box base (world) under the same policy as the pipeline."""
    fp = Polygon(box.base_corners())
    route_w = G.poly(ROUTE_WORLD[HOUSEHOLDS[hh_id]["route_key"]])
    if metric:
        clearance = HOUSEHOLDS[hh_id]["clearance_cm"]
        ov = G.overlap_stats(fp, route_w)
        cw = G.clear_width_profile(route_w, [fp])
        obstructed = ov["intersection_area"] > 0 and cw["min_clear_width"] < clearance
        margin = cw["min_clear_width"] - clearance if ov["intersection_area"] > 0 else min(ov["distance_to_route"], 99)
        borderline = (ov["intersection_area"] > 0 and abs(cw["min_clear_width"] - clearance) < 6) or (ov["intersection_area"] == 0 and ov["distance_to_route"] < 3)
        return {"obstructed": obstructed, "borderline": bool(borderline), "clear_width_cm": cw["min_clear_width"], "margin": margin,
                "footprint_world": fp}
    fp_img = G.poly(world_poly_to_image(cam, box.base_corners()))
    route_img = G.poly(world_poly_to_image(cam, ROUTE_WORLD[HOUSEHOLDS[hh_id]["route_key"]]))
    ov = G.overlap_stats(fp_img, route_img)
    f = ov["fraction_of_footprint"]
    return {"obstructed": f >= G.APPARENT_OVERLAP_FRACTION, "borderline": 0.01 <= f <= 0.06, "fraction": f, "footprint_img": fp_img}
