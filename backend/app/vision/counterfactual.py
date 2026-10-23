"""Counterfactual clearance planner: 'if this object were moved to X, would the
apparent obstruction disappear?' A simulation only — never evidence that the
object was moved."""
from __future__ import annotations

import numpy as np
from shapely.geometry import Point

from . import geometry as G
from .calibration import to_world


def plan(fp, route, zone, metric: bool, margin: float | None = None) -> dict:
    margin = margin if margin is not None else (5.0 if metric else 6.0)
    units = "cm" if metric else "px"
    cx, cy = fp.centroid.x, fp.centroid.y
    best = None
    if zone is not None and not zone.is_empty:
        fits = fp.area <= zone.area * 1.02
        minx, miny, maxx, maxy = zone.bounds
        step = max((maxx - minx), (maxy - miny)) / 40.0
        for x in np.arange(minx, maxx + 1e-6, step):
            for y in np.arange(miny, maxy + 1e-6, step):
                if not zone.contains(Point(x, y)):
                    continue
                cand = G.translate(fp, x - cx, y - cy)
                if G.inside_fraction(cand, zone) < 0.92:
                    continue
                if cand.buffer(margin).intersects(route):
                    continue
                d = float(np.hypot(x - cx, y - cy))
                if best is None or d < best[0]:
                    best = (d, cand, "drop_zone")
    else:
        fits = False
    if best is None:
        # nearest position that clears the route (with margin), searched radially
        r_max = 4 * max(fp.bounds[2] - fp.bounds[0], fp.bounds[3] - fp.bounds[1])
        for rad in np.linspace(0, r_max, 40)[1:]:
            for ang in np.linspace(0, 2 * np.pi, 36, endpoint=False):
                cand = G.translate(fp, rad * np.cos(ang), rad * np.sin(ang))
                if not cand.buffer(margin).intersects(route):
                    best = (float(rad), cand, "nearest_clear")
                    break
            if best:
                break
    if best is None:
        return {"resolves": False, "reason": "no candidate placement clears the route within search radius", "units": units}
    d, cand, kind = best
    post = G.overlap_stats(cand, route)
    msg = ("Moving the object into the designated delivery area may remove its apparent overlap with the marked route."
           if kind == "drop_zone" else
           "The object does not fit the delivery area; moving it beside the route may remove the apparent overlap.")
    return {
        "resolves": post["intersection_area"] == 0,
        "target": kind,
        "fits_drop_zone": bool(fits),
        "move_distance": round(d, 1),
        "units": units,
        "safety_margin": margin,
        "message": msg,
        "caveats": [
            "Simulation on 2D geometry only; it does not show that anyone moved the object.",
            "The resident should not be asked to lift or move the object; only an authorised helper is contacted, with approval.",
        ] + ([] if metric else ["Apparent mode: translation is in image pixels and ignores perspective scaling."]),
        "_proposed_work": cand,
    }


def to_image(poly_work, metric: bool, Hinv):
    if not metric:
        return poly_work
    return G.poly(to_world(Hinv, np.array(poly_work.exterior.coords)[:-1]))
