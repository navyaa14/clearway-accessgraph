"""Monte-Carlo uncertainty envelope for the obstruction decision.

Rather than trusting one mask boundary and one calibration, we resample:
  * mask boundary: morphological erosion/dilation radius r in [-3, +3] px
  * unseen footprint extent: depth ratio (metric) or band fraction (apparent)
  * calibration: Gaussian jitter (sigma 1.5 px) of the floor correspondences
and recompute the decision each time. The fraction of samples that conclude
"obstructed" is p_obstructed. If plausible perturbations flip the conclusion,
the result is REVIEW_REQUIRED, never "clear".
"""
from __future__ import annotations

import cv2
import numpy as np
from shapely.ops import unary_union

from . import geometry as G
from .calibration import denorm, floor_homography, to_world, vertical_vanishing_point
from .footprint import apparent_footprint, column_profile, erode_dilate, metric_footprint

P_OBSTRUCTED = 0.90  # policy: >= -> POTENTIAL_OBSTRUCTION
P_CLEAR = 0.10  # policy: <= -> NO_OBSTRUCTION_OBSERVED
MIN_SEG_CONFIDENCE = 0.30
RADII = [-3, -2, -1, 0, 1, 2, 3]
CAL_JITTER_PX = 1.5
NEAR_ROUTE = {True: 20.0, False: 20.0}  # cm (metric) / px (apparent)


def decide(fp, route, clearance):
    ov = G.overlap_stats(fp, route)
    cw = None
    if clearance is not None:
        cw = G.clear_width_profile(route, [fp])
        obstructed = ov["intersection_area"] > 0 and cw["min_clear_width"] < clearance
    else:
        obstructed = ov["fraction_of_footprint"] >= G.APPARENT_OVERLAP_FRACTION
    return obstructed, ov, cw


def evaluate_object(inst, ctx: dict, n: int = 160, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    w, h = ctx["w"], ctx["h"]
    metric = ctx.get("homography") is not None
    vvp = vertical_vanishing_point(ctx["homography"]["H"], w, h) if metric else None
    contacts = {r: column_profile(erode_dilate(inst.mask, r), vvp=vvp) for r in RADII}
    c0 = contacts[0]
    route_img = G.poly(denorm(ctx["route"], w, h))
    clearance = ctx.get("clearance_cm") if metric else None

    def route_for(H):
        return G.poly(to_world(H, np.array(route_img.exterior.coords)[:-1])) if metric else route_img

    def footprint_for(r, u, H):
        c = contacts[r]
        if metric:
            fp, _ = metric_footprint(c, H, 0.55 + 0.45 * u)
            return fp
        return apparent_footprint(c, 0.15 + 0.25 * u)

    # nominal estimate
    H0 = ctx["homography"]["H"] if metric else None
    fp0 = footprint_for(0, 0.5, H0)
    if fp0 is None or fp0.is_empty:
        return {"classification": "REVIEW_REQUIRED", "p_obstructed": None, "reasons": ["could not estimate a ground-contact footprint"],
                "contact": {"reliable": False, "truncated_fraction": c0.truncated_fraction, "reason": "no footprint"}}
    route0 = route_for(H0)
    obs0, ov0, cw0 = decide(fp0, route0, clearance)
    diag = metric_footprint(c0, H0, 0.775)[1] if metric else {}

    votes, fracs, widths, fps = [], [], [], []
    for i in range(n):
        r = int(rng.choice(RADII))
        u = float(rng.random())
        H = H0
        if metric:
            jit = denorm(ctx["floor_points_img"], w, h) + rng.normal(0, CAL_JITTER_PX, (len(ctx["floor_points_img"]), 2))
            hh = floor_homography([[x / w, y / h] for x, y in jit], ctx["floor_points_world"], w, h)
            H = hh["H"] if hh else H0
        fp = footprint_for(r, u, H)
        if fp is None or fp.is_empty:
            continue
        ob, ov, cw = decide(fp, route_for(H) if metric else route0, clearance)
        votes.append(ob)
        fracs.append(ov["fraction_of_footprint"])
        if cw:
            widths.append(cw["min_clear_width"])
        if i < 60:
            fps.append(fp if not metric else G.poly(to_world(ctx["homography"]["Hinv"], np.array(fp.exterior.coords)[:-1])))
    p = float(np.mean(votes)) if votes else None
    reasons = []
    # does the raw silhouette touch the route at all? (used when contact is unreliable)
    rmask = np.zeros_like(inst.mask)
    cv2.fillPoly(rmask, [np.round(np.array(route_img.exterior.coords)).astype(np.int32)], 255)
    silhouette_on_route = bool(((inst.mask > 0) & (rmask > 0)).any())
    if p is None:
        cls = "REVIEW_REQUIRED"
        reasons.append("uncertainty sampling produced no valid footprints")
    elif not c0.reliable and (p > P_CLEAR or silhouette_on_route):
        cls = "REVIEW_REQUIRED"
        reasons.append(f"ground-contact estimate unreliable ({c0.reason}); the object's silhouette touches the marked route")
    elif p >= P_OBSTRUCTED and inst.confidence < MIN_SEG_CONFIDENCE:
        cls = "REVIEW_REQUIRED"
        reasons.append(f"segmentation confidence {inst.confidence:.2f} below {MIN_SEG_CONFIDENCE}")
    elif p >= P_OBSTRUCTED:
        cls = "POTENTIAL_OBSTRUCTION"
    elif p <= P_CLEAR and inst.confidence < MIN_SEG_CONFIDENCE and (ov0["intersection_area"] > 0 or ov0["distance_to_route"] < NEAR_ROUTE[metric]):
        # quality gates apply to "clear" conclusions too: a poorly segmented object close to the route cannot clear it
        cls = "REVIEW_REQUIRED"
        reasons.append(f"segmentation confidence {inst.confidence:.2f} below {MIN_SEG_CONFIDENCE} for an object at or near the route")
    elif p <= P_CLEAR:
        cls = "NO_OBSTRUCTION_OBSERVED"
    else:
        cls = "REVIEW_REQUIRED"
        reasons.append(f"plausible boundary/calibration perturbations disagree (p={p:.2f} between {P_CLEAR} and {P_OBSTRUCTED})")

    fp0_img = fp0 if not metric else G.poly(to_world(ctx["homography"]["Hinv"], np.array(fp0.exterior.coords)[:-1]))
    outer = unary_union(fps).convex_hull if fps else fp0_img
    pct = lambda a, q: None if not a else round(float(np.percentile(a, q)), 4)
    return {
        "classification": cls,
        "p_obstructed": None if p is None else round(p, 3),
        "samples": len(votes),
        "reasons": reasons,
        "mode": "METRIC" if metric else "APPARENT",
        "contact": {"reliable": c0.reliable, "truncated_fraction": c0.truncated_fraction, "reason": c0.reason, **diag},
        "nominal": {
            "obstructed": bool(obs0),
            "intersection_area": round(ov0["intersection_area"], 2),
            "fraction_of_footprint": round(ov0["fraction_of_footprint"], 4),
            "fraction_of_route": round(ov0["fraction_of_route"], 4),
            "distance_to_route": round(ov0["distance_to_route"], 2),
            "intrusion_depth": round(ov0["intrusion_depth"], 2),
            "units": "cm" if metric else "px",
            "clear_width": cw0,
        },
        "envelope": {
            "fraction_p5": pct(fracs, 5), "fraction_p50": pct(fracs, 50), "fraction_p95": pct(fracs, 95),
            "clear_width_p5": pct(widths, 5), "clear_width_p95": pct(widths, 95),
        },
        "_fp_work": fp0, "_route_work": route0, "_fp_img": fp0_img, "_outer_img": outer,
    }
