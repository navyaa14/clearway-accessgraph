"""Ground-contact footprint estimation from an instance mask.

A bounding box or full silhouette overlapping the route on screen is NOT
evidence of physical obstruction: a tall object beside the path occludes path
pixels without standing on the path. We therefore reason about where the
object touches the floor.

* Contact line: for each image column the lowest mask pixel is taken as a
  candidate floor-contact point (valid for objects resting on the floor).
  Columns whose lowest pixel lies on the frame border are truncated and not
  trusted.
* Metric mode (floor homography available): contact points are projected to
  the floor plane (cm). The unseen far edge is placed at depth D behind the
  contact line along the local viewing direction, with D sampled between an
  aspect prior and an upper bound obtained by projecting the silhouette top
  onto the floor (any pixel projected as if on the floor overestimates
  distance for elevated surfaces, so it bounds the footprint).
* Apparent mode (no metric calibration): the footprint is the lower band of
  the silhouette, with band height sampled as a fraction of the silhouette
  height. Results are image-space only; metric clearance is NOT established.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from shapely.geometry import MultiPoint, Polygon

from .calibration import to_world


@dataclass
class Contact:
    xs: np.ndarray  # columns (px)
    bottoms: np.ndarray  # lowest mask pixel per column
    tops: np.ndarray  # highest mask pixel per column
    valid: np.ndarray  # contact not cut off by frame border
    truncated_fraction: float
    reliable: bool
    reason: str


def _vertical_edge_by_vp(cols, bottoms, vvp, max_deg: float = 6.0) -> np.ndarray:
    """Walk inward from both ends, marking contact samples whose local boundary
    direction points at the vertical vanishing point (an elevated vertical edge)."""
    n = len(cols)
    vert = np.zeros(n, bool)
    pts = np.column_stack([cols, bottoms]).astype(float)
    cosmax = np.cos(np.radians(max_deg))

    def is_vertical(i):
        a, b = pts[max(0, i - 3)], pts[min(n - 1, i + 3)]
        t = b - a
        r = vvp - pts[i]
        nt, nr = np.linalg.norm(t), np.linalg.norm(r)
        if nt < 1e-6 or nr < 1e-6:
            return False
        return abs(t @ r) / (nt * nr) > cosmax

    for order in (range(n), range(n - 1, -1, -1)):
        for i in order:
            if is_vertical(i):
                vert[i] = True
            else:
                break
    return vert


def column_profile(mask: np.ndarray, step: int = 2, vvp: np.ndarray | None = None) -> Contact:
    h, w = mask.shape
    cols = np.where(mask.any(0))[0]
    if len(cols) == 0:
        return Contact(np.array([]), np.array([]), np.array([]), np.array([], bool), 1.0, False, "empty mask")
    cols = cols[::step] if len(cols) > 4 * step else cols
    sub = mask[:, cols] > 0
    bottoms = h - 1 - np.argmax(sub[::-1, :], axis=0)
    tops = np.argmax(sub, axis=0)
    trunc = bottoms >= h - 3
    tf = float(trunc.mean())
    # Vertical-edge trim: at the silhouette ends, a column whose lowest pixel
    # rises steeply while the column itself is short lies on an elevated
    # vertical edge (not on the floor). Walk inward from both ends and drop
    # those columns from the contact set.
    heights = (bottoms - tops).astype(float)
    hmax = float(np.percentile(heights, 90)) if len(heights) else 0.0
    vert = np.zeros(len(cols), bool)
    if vvp is not None and len(cols) >= 6:
        vert = _vertical_edge_by_vp(cols, bottoms, np.asarray(vvp, float))
        if vert.mean() > 0.4:
            vert[:] = False
    elif len(cols) >= 6 and hmax > 0:
        slope = np.abs(np.gradient(bottoms.astype(float), cols.astype(float)))
        for order in (range(len(cols)), range(len(cols) - 1, -1, -1)):
            for i in order:
                if heights[i] < 0.6 * hmax and slope[i] > 1.5:
                    vert[i] = True
                else:
                    break
        if vert.mean() > 0.4:  # don't trim away most of the object
            vert[:] = False
    edge_cols = (cols <= 1) | (cols >= w - 2)
    reliable, reason = True, "contact edge fully visible"
    if tf > 0.25:
        reliable, reason = False, f"{tf * 100:.0f}% of the contact edge is cut off by the frame border"
    elif edge_cols.mean() > 0.25:
        reliable, reason = False, "object extends past the left/right frame edge"
    return Contact(cols.astype(float), bottoms.astype(float), tops.astype(float), ~trunc & ~vert, round(tf, 3), reliable, reason)


def erode_dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r == 0:
        return mask
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(r) + 1, 2 * abs(r) + 1))
    return cv2.dilate(mask, k) if r > 0 else cv2.erode(mask, k)


def apparent_footprint(c: Contact, band_frac: float) -> Polygon | None:
    ok = c.valid if c.valid.sum() >= 2 else np.ones_like(c.valid)
    if ok.sum() < 2:
        return None
    xs, b, t = c.xs[ok], c.bottoms[ok], c.tops[ok]
    height = float(b.max() - t.min()) or 1.0
    upper = np.maximum(t, b - band_frac * height)
    pts = list(zip(xs, b)) + list(zip(xs[::-1], upper[::-1]))
    poly = Polygon(pts).buffer(0)
    return poly if not poly.is_empty else None


def metric_footprint(c: Contact, H: np.ndarray, depth_ratio: float) -> tuple[Polygon | None, dict]:
    """Return footprint polygon in floor cm plus diagnostics."""
    keep = c.valid if c.valid.sum() >= 3 else np.ones_like(c.valid)
    xs, b, t = c.xs[keep], c.bottoms[keep], c.tops[keep]
    if len(xs) < 3:
        return None, {"reason": "too few contact columns"}
    contact_w = to_world(H, np.column_stack([xs, b]))
    away = to_world(H, np.column_stack([xs, b - 12.0])) - contact_w
    norms = np.linalg.norm(away, axis=1, keepdims=True)
    if not np.all(np.isfinite(norms)) or np.any(norms < 1e-9):
        return None, {"reason": "contact projects to horizon"}
    dirs = away / norms
    vdir = dirs.mean(0)
    vdir /= np.linalg.norm(vdir)
    # Depth direction: normal of the dominant front-contact edge (PCA), oriented
    # away from the camera. Objects tend to sit square to their contact edge;
    # extrapolating along the viewing ray instead skews off-axis footprints.
    centred = contact_w - contact_w.mean(0)
    _, sv, vt = np.linalg.svd(centred, full_matrices=False)
    axis = vt[0]
    ndir = np.array([-axis[1], axis[0]])
    if ndir @ vdir < 0:
        ndir = -ndir
    # fall back to the viewing direction for near-degenerate (point-like) contacts
    if sv[0] < 1e-6 or abs(ndir @ vdir) < 0.35:
        ndir = vdir
    lateral = centred @ axis
    width = float(lateral.max() - lateral.min())
    vdir = ndir
    # upper bound: silhouette top projected onto floor
    top_w = to_world(H, np.column_stack([xs, t]))
    if np.all(np.isfinite(top_w)):
        dmax = float(np.max((top_w - contact_w) @ vdir))
    else:
        dmax = np.inf
    depth = min(depth_ratio * width, dmax if dmax > 0 else np.inf)
    # front-most contact defines the near edge; far edge offset by depth
    front = contact_w
    back = contact_w + vdir * depth
    pts = np.vstack([front, back]).astype(np.float32)
    hull_pts = cv2.convexHull(pts).reshape(-1, 2).astype(float)
    if len(hull_pts) < 3:
        return None, {"reason": "degenerate footprint"}
    hull = Polygon(hull_pts)
    if hull.is_empty or hull.area <= 0:
        return None, {"reason": "degenerate footprint"}
    return hull, {"contact_width_cm": round(width, 1), "depth_cm": round(float(depth), 1),
                  "depth_upper_bound_cm": None if not np.isfinite(dmax) else round(dmax, 1)}
