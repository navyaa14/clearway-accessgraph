"""Polygon geometry for route / drop-zone / footprint relationships (Shapely)."""
from __future__ import annotations

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

APPARENT_OVERLAP_FRACTION = 0.03  # >=3% of footprint inside route counts as intrusion


def poly(pts) -> Polygon:
    p = Polygon([tuple(map(float, q)) for q in pts]).buffer(0)
    if p.geom_type == "MultiPolygon":
        p = max(p.geoms, key=lambda g: g.area)
    return p


def overlap_stats(fp: Polygon, route: Polygon) -> dict:
    inter = fp.intersection(route)
    a = float(inter.area)
    depth = 0.0
    if a > 0:
        # how far the footprint penetrates into the route (max distance from route boundary)
        geoms = [inter] if inter.geom_type == "Polygon" else [g for g in getattr(inter, "geoms", []) if g.geom_type == "Polygon"]
        pts = [c for g in geoms for c in g.exterior.coords]
        if pts:
            depth = max(route.exterior.distance(Point(p)) for p in pts)
    return {
        "intersection_area": a,
        "footprint_area": float(fp.area),
        "fraction_of_footprint": a / fp.area if fp.area > 0 else 0.0,
        "fraction_of_route": a / route.area if route.area > 0 else 0.0,
        "distance_to_route": 0.0 if a > 0 else float(fp.distance(route)),
        "intrusion_depth": float(depth),
    }


def route_axis(route: Polygon) -> tuple[np.ndarray, np.ndarray, float]:
    mrr = route.minimum_rotated_rectangle
    c = np.array(mrr.exterior.coords)[:4]
    e1, e2 = c[1] - c[0], c[2] - c[1]
    long_axis = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
    length = float(np.linalg.norm(long_axis))
    return np.array(route.centroid.coords[0]), long_axis / max(length, 1e-9), length


def clear_width_profile(route: Polygon, obstacles: list[Polygon], step: float = 5.0) -> dict:
    """Minimum free passage width across the route (metric units).

    Cross-sections perpendicular to the route's long axis are clipped to the
    route; obstacle footprints are subtracted; the longest free segment of each
    section is the clear width there. Only sections that can touch an obstacle
    are evaluated (vectorised with Shapely 2), plus a sparse set for the nominal width.
    """
    centre, axis, length = route_axis(route)
    perp = np.array([-axis[1], axis[0]])
    span = 4 * max(route.bounds[2] - route.bounds[0], route.bounds[3] - route.bounds[1])
    s_all = np.arange(-length / 2, length / 2 + 1e-6, step)

    def sections(svals):
        p = centre[None, :] + axis[None, :] * svals[:, None]
        a, b = p - perp * span, p + perp * span
        lines = shapely.linestrings(np.stack([a, b], axis=1))
        return shapely.intersection(lines, route)

    nominal_sec = sections(s_all[:: max(1, len(s_all) // 12)])
    nom_len = shapely.length(nominal_sec)
    nom = float(np.median(nom_len[nom_len > 0])) if np.any(nom_len > 0) else 0.0
    blocked = unary_union(obstacles) if obstacles else None
    if blocked is None or blocked.is_empty or not blocked.intersects(route):
        return {"nominal_width": round(nom, 2), "min_clear_width": round(nom, 2), "obstructed_section": False}
    coords = np.array(blocked.convex_hull.exterior.coords) if blocked.geom_type != "Point" else np.array(blocked.coords)
    proj = (coords - centre) @ axis
    sel = s_all[(s_all >= proj.min() - step) & (s_all <= proj.max() + step)]
    worst, worst_at = np.inf, None
    if len(sel):
        secs = sections(sel)
        hits = shapely.intersects(secs, blocked)
        for s_val, sec, hit in zip(sel, secs, hits):
            if not hit or sec.is_empty:
                continue
            free = sec.difference(blocked)
            w = max((g.length for g in getattr(free, "geoms", [free])), default=0.0) if not free.is_empty else 0.0
            if w < worst:
                worst, worst_at = w, float(s_val)
    return {"nominal_width": round(nom, 2), "min_clear_width": round(float(worst if np.isfinite(worst) else nom), 2),
            "obstructed_section": worst_at is not None}


def inside_fraction(fp: Polygon, zone: Polygon | None) -> float:
    if zone is None or fp.area <= 0:
        return 0.0
    return float(fp.intersection(zone).area / fp.area)


def translate(p: Polygon, dx: float, dy: float) -> Polygon:
    return affinity.translate(p, dx, dy)
