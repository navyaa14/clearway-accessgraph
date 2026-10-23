"""Frame image-quality assessment (Ring CV guideline: drop blurry / dark / blown-out frames)."""
from __future__ import annotations

import cv2
import numpy as np

# Policy defaults. These are configurable engineering thresholds, tuned on the
# synthetic fixture set (see eval/). They are NOT validated on real Ring footage.
MIN_SHARPNESS_ABS = 0.05  # contrast-normalised Laplacian variance at 320 px width
MIN_SHARPNESS_REL = 0.45  # vs. calibration reference
MIN_BRIGHTNESS = 40.0
MAX_BRIGHTNESS = 225.0
MAX_OBSCURED = 0.30
MAX_CLIPPED = 0.35


def _norm(img: np.ndarray, width: int = 640) -> np.ndarray:
    h, w = img.shape[:2]
    if w != width:
        img = cv2.resize(img, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)
    return img


def sharpness(img: np.ndarray) -> float:
    """Noise- and exposure-robust focus measure: Laplacian variance on an
    area-downsampled (320 px) frame, normalised by intensity variance."""
    g = cv2.cvtColor(_norm(img, 320), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(cv2.Laplacian(g, cv2.CV_32F).var() / (g.var() + 1e-6))


def texture_map(img: np.ndarray, block: int = 16) -> np.ndarray:
    g = cv2.cvtColor(_norm(img, 320), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.GaussianBlur(g, (0, 0), 0.8)
    m = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    h, w = m.shape
    gh, gw = h // block, w // block
    return m[: gh * block, : gw * block].reshape(gh, block, gw, block).mean(axis=(1, 3))


def assess(img: np.ndarray, ref: np.ndarray | None = None) -> dict:
    small = _norm(img)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sharp = sharpness(img)
    bright = float(gray.mean())
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat = float(hsv[..., 1].mean())
    dark = float((gray < 20).mean())
    blown = float((gray > 245).mean())
    reasons: list[str] = []
    rel_sharp = None
    obscured = 0.0
    if ref is not None:
        ref_r = cv2.resize(ref, (img.shape[1], img.shape[0]))
        rel_sharp = sharp / max(sharpness(ref_r), 1e-6)
        t_cur, t_ref = texture_map(img), texture_map(ref_r)
        textured = t_ref > 3.0
        if textured.any():
            obscured = float(((t_cur < 0.3 * t_ref) & textured).sum() / textured.sum())
        if rel_sharp < MIN_SHARPNESS_REL:
            reasons.append(f"blurry: sharpness {rel_sharp:.2f}x of calibration reference (< {MIN_SHARPNESS_REL})")
    elif sharp < MIN_SHARPNESS_ABS:
        reasons.append(f"blurry: focus measure {sharp:.3f} < {MIN_SHARPNESS_ABS}")
    if bright < MIN_BRIGHTNESS:
        reasons.append(f"too dark: mean luminance {bright:.0f} < {MIN_BRIGHTNESS:.0f}")
    if bright > MAX_BRIGHTNESS:
        reasons.append(f"overexposed: mean luminance {bright:.0f}")
    if dark + blown > MAX_CLIPPED:
        reasons.append(f"{(dark + blown) * 100:.0f}% of pixels clipped")
    if obscured > MAX_OBSCURED:
        reasons.append(f"view obscured: {obscured * 100:.0f}% of textured regions lost detail")
    grayscale_ir = sat < 6.0
    if grayscale_ir:
        reasons.append("night-vision / grayscale frame: colour cues unavailable")
    return {
        "usable": not reasons or (grayscale_ir and len(reasons) == 1),
        "sharpness": round(sharp, 4),
        "relative_sharpness": None if rel_sharp is None else round(rel_sharp, 3),
        "brightness": round(bright, 1),
        "saturation": round(sat, 1),
        "clipped_fraction": round(dark + blown, 4),
        "obscured_fraction": round(obscured, 4),
        "reasons": reasons,
    }


def select_best_frame(frames: list[np.ndarray]) -> tuple[int, dict]:
    """Pick the sharpest well-exposed frame from a sampled set."""
    best, best_score, best_q = 0, -1.0, {}
    for i, f in enumerate(frames):
        q = assess(f)
        score = q["sharpness"] * (1.0 if q["usable"] else 0.1)
        if score > best_score:
            best, best_score, best_q = i, score, q
    return best, best_q
