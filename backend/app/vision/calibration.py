"""Camera-specific spatial calibration: floor homography + displacement detection."""
from __future__ import annotations

import cv2
import numpy as np

MAX_SHIFT_PX = 8.0  # at 960 px frame width
MAX_ROT_DEG = 1.0
MIN_INLIERS = 20


def denorm(poly, w: int, h: int) -> np.ndarray:
    return np.array([[p[0] * w, p[1] * h] for p in poly], dtype=np.float64)


def floor_homography(img_pts_norm, world_pts_cm, w: int, h: int) -> dict | None:
    """Image->world (cm) homography from >=4 floor correspondences.

    Returns None if correspondences are missing/degenerate (=> apparent-overlap mode).
    """
    if not img_pts_norm or not world_pts_cm or len(img_pts_norm) < 4 or len(img_pts_norm) != len(world_pts_cm):
        return None
    src = denorm(img_pts_norm, w, h)
    dst = np.asarray(world_pts_cm, np.float64)
    if abs(cv2.contourArea(src.astype(np.float32))) < 0.002 * w * h:
        return None
    Hm, _ = cv2.findHomography(src, dst, 0)
    if Hm is None:
        return None
    proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), Hm).reshape(-1, 2)
    err = float(np.sqrt(((proj - dst) ** 2).sum(1)).mean())
    # sanity: horizon must be above the floor points
    return {"H": Hm, "Hinv": np.linalg.inv(Hm), "reprojection_error_cm": round(err, 3)}


def vertical_vanishing_point(H_img2world: np.ndarray, w: int, h: int) -> np.ndarray | None:
    """Image of the vertical direction (camera nadir), recovered from the floor
    homography assuming square pixels and a centred principal point.

    H_w2i = K [r1 r2 t]. Orthogonality of r1, r2 gives the focal length f;
    then r3 = r1 x r2 and the vanishing point is K r3. Vertical object edges
    (box corners, bin sides) all converge to this point; floor edges do not.
    """
    try:
        Hw = np.linalg.inv(H_img2world)
    except np.linalg.LinAlgError:
        return None
    cx, cy = w / 2.0, h / 2.0
    h1, h2 = Hw[:, 0], Hw[:, 1]
    a1, b1, c1 = h1[0] - cx * h1[2], h1[1] - cy * h1[2], h1[2]
    a2, b2, c2 = h2[0] - cx * h2[2], h2[1] - cy * h2[2], h2[2]
    f2 = -(a1 * a2 + b1 * b2) / (c1 * c2) if abs(c1 * c2) > 1e-12 else -1
    if not np.isfinite(f2) or f2 <= 0:
        den = c2 ** 2 - c1 ** 2
        f2 = (a1 ** 2 + b1 ** 2 - a2 ** 2 - b2 ** 2) / den if abs(den) > 1e-12 else -1
    if not np.isfinite(f2) or f2 <= 0:
        return None
    f = float(np.sqrt(f2))
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    Ki = np.linalg.inv(K)
    r1, r2 = Ki @ h1, Ki @ h2
    r1, r2 = r1 / np.linalg.norm(r1), r2 / np.linalg.norm(r2)
    v = K @ np.cross(r1, r2)
    if abs(v[2]) < 1e-9:
        return None
    return v[:2] / v[2]


def to_world(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, H).reshape(-1, 2)


def check_displacement(ref: np.ndarray, cur: np.ndarray, ignore_mask: np.ndarray | None = None) -> dict:
    """Detect camera displacement since calibration via ORB matching on stationary landmarks."""
    h, w = cur.shape[:2]
    if ref.shape[:2] != (h, w):
        ref = cv2.resize(ref, (w, h))
    g1 = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
    feat_mask = np.full((h, w), 255, np.uint8)
    feat_mask[: int(0.08 * h), :] = 0  # watermark / provenance strip
    m2 = feat_mask.copy()
    if ignore_mask is not None:
        m2[ignore_mask > 0] = 0
    orb = cv2.ORB_create(1500)
    k1, d1 = orb.detectAndCompute(g1, feat_mask)
    k2, d2 = orb.detectAndCompute(g2, m2)
    scale = w / 960.0
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return {"status": "UNKNOWN", "valid": None, "reason": "insufficient landmark features", "inliers": 0}
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    good = []
    for pair in bf.knnMatch(d1, d2, k=2):
        if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
            good.append(pair[0])
    if len(good) < MIN_INLIERS:
        if len(k1) >= 200:  # feature-rich reference whose landmarks can no longer be found
            return {"status": "UNCONFIRMED", "valid": False, "reason": f"calibration landmarks could not be re-identified ({len(good)} matches)", "inliers": len(good)}
        return {"status": "UNKNOWN", "valid": None, "reason": f"only {len(good)} landmark matches", "inliers": len(good)}
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    A, inl = cv2.estimateAffinePartial2D(p1, p2, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if A is None:
        return {"status": "UNKNOWN", "valid": None, "reason": "affine fit failed", "inliers": 0}
    n_in = int(inl.sum())
    rot = float(np.degrees(np.arctan2(A[1, 0], A[0, 0])))
    # displacement of frame centre
    c = np.array([w / 2, h / 2, 1.0])
    shift = float(np.linalg.norm(A @ c - c[:2])) / scale
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], float)
    corner_shift = float(np.max(np.linalg.norm((A @ corners.T).T - corners[:, :2], axis=1))) / scale
    if n_in < MIN_INLIERS:
        return {"status": "UNKNOWN", "valid": None, "reason": f"only {n_in} RANSAC inliers", "inliers": n_in}
    moved = shift > MAX_SHIFT_PX or abs(rot) > MAX_ROT_DEG or corner_shift > 2 * MAX_SHIFT_PX
    return {"status": "DISPLACED" if moved else "STABLE", "valid": not moved, "shift_px": round(shift, 2),
            "max_corner_shift_px": round(corner_shift, 2), "rotation_deg": round(rot, 3), "inliers": n_in,
            "reason": "camera perspective changed materially since calibration" if moved else "landmarks align with calibration"}
