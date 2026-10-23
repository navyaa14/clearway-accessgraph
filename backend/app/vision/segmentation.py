"""Instance segmentation of new obstacles.

Primary method (Stage 1): calibrated background change detection against the
household's clear-scene reference frame, with illumination normalisation and
shadow suppression, followed by connected-component instance separation.
This is class-agnostic on purpose: a bin, bicycle or parcel on the route
matters equally for accessibility.

Fallbacks when no clear reference exists:
  * prompt-seeded GrabCut (user draws a box around the object)
  * cardboard-hue seeded GrabCut (parcel-like colour prior)
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MODEL_VERSION = "cw-seg-changedet-1.2"

# Normalised regions excluded from change detection. Ring places its
# mandatory watermark (logo top-left, device/app/timestamp top-right). The
# timestamp changes every frame, so it must not be read as an obstacle. We
# never crop or hide the watermark in displayed media; we only ignore it here.
WATERMARK_IGNORE = [(0.0, 0.0, 0.22, 0.075), (0.55, 0.0, 1.0, 0.075)]


@dataclass
class Instance:
    mask: np.ndarray  # uint8 0/255 full-frame
    bbox: tuple  # x, y, w, h
    area: int
    confidence: float
    parcel_likeness: float
    method: str

    def contour(self) -> np.ndarray:
        cs, _ = cv2.findContours(self.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return max(cs, key=cv2.contourArea).reshape(-1, 2) if cs else np.zeros((0, 2), int)


def _ignore_mask(h: int, w: int) -> np.ndarray:
    m = np.zeros((h, w), bool)
    for x0, y0, x1, y1 in WATERMARK_IGNORE:
        m[int(y0 * h): int(y1 * h), int(x0 * w): int(x1 * w)] = True
    return m


def cardboard_fraction(img: np.ndarray, mask: np.ndarray) -> float:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sel = mask > 0
    if not sel.any():
        return 0.0
    h, s, v = hsv[..., 0][sel], hsv[..., 1][sel], hsv[..., 2][sel]
    card = (h >= 8) & (h <= 25) & (s >= 60) & (v >= 70)
    return float(card.mean())


def change_detect(img: np.ndarray, ref: np.ndarray, min_area_frac: float = 0.0015) -> tuple[list[Instance], dict]:
    h, w = img.shape[:2]
    if ref.shape[:2] != (h, w):
        ref = cv2.resize(ref, (w, h), interpolation=cv2.INTER_AREA)
    cur = cv2.GaussianBlur(img, (5, 5), 0).astype(np.float32)
    bg = cv2.GaussianBlur(ref, (5, 5), 0).astype(np.float32)
    # global illumination gain (robust median ratio)
    cg, bgg = cur.mean(-1), bg.mean(-1)
    valid = bgg > 30
    gain = float(np.median(cg[valid] / bgg[valid])) if valid.any() else 1.0
    gain = float(np.clip(gain, 0.3, 3.0))
    bg_adj = np.clip(bg * gain, 0, 255)
    lab_c = cv2.cvtColor(cur.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_b = cv2.cvtColor(bg_adj.astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    dL = lab_c[..., 0] - lab_b[..., 0]
    dC = np.hypot(lab_c[..., 1] - lab_b[..., 1], lab_c[..., 2] - lab_b[..., 2])
    ratio = (lab_c[..., 0] + 1) / (lab_b[..., 0] + 1)
    shadow = (ratio > 0.40) & (ratio < 0.93) & (dC < 7.0)
    changed = ((np.abs(dL) > 22) | (dC > 11)) & ~shadow & ~_ignore_mask(h, w)
    m = changed.astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    # fill holes per component
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out: list[Instance] = []
    min_area = min_area_frac * h * w
    changed_frac = float((m > 0).mean())
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area:
            continue
        inst = (lab == i).astype(np.uint8) * 255
        cs, _ = cv2.findContours(inst, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        filled = np.zeros_like(inst)
        cv2.drawContours(filled, cs, -1, 255, -1)
        sel = filled > 0
        margin = float(np.mean(np.maximum(np.abs(dL[sel]) / 22.0, dC[sel] / 11.0)))
        solidity = float(sel.sum() / max(cv2.contourArea(cv2.convexHull(np.vstack(cs))), 1))
        conf = float(np.clip(0.5 * min(margin / 2.5, 1.0) + 0.5 * solidity, 0, 1))
        out.append(Instance(filled, (int(x), int(y), int(bw), int(bh)), int(sel.sum()), round(conf, 3),
                            round(cardboard_fraction(img, filled), 3), "change_detection"))
    out.sort(key=lambda i: -i.area)
    return out, {"illumination_gain": round(gain, 3), "changed_fraction": round(changed_frac, 4),
                 "shadow_fraction": round(float(shadow.mean()), 4), "_changed_mask": m}


def grabcut_from_rect(img: np.ndarray, rect: tuple) -> Instance | None:
    x, y, w, h = [int(v) for v in rect]
    H, W = img.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, W - x - 1), min(h, H - y - 1)
    if w < 8 or h < 8:
        return None
    mask = np.zeros((H, W), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, (x, y, w, h), bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    inst = (lab == i).astype(np.uint8) * 255
    bx, by, bw, bh, area = stats[i]
    fill = inst.sum() / 255 / max(w * h, 1)
    return Instance(inst, (int(bx), int(by), int(bw), int(bh)), int(area), round(float(np.clip(0.35 + 0.4 * fill, 0, 0.75)), 3),
                    round(cardboard_fraction(img, inst), 3), "prompt_grabcut")


def cardboard_seeded(img: np.ndarray, min_area_frac: float = 0.002) -> list[Instance]:
    """No-reference fallback: cardboard-hue seeds refined with GrabCut. Low confidence by design."""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(cv2.GaussianBlur(img, (5, 5), 0), cv2.COLOR_BGR2HSV)
    seed = ((hsv[..., 0] >= 8) & (hsv[..., 0] <= 25) & (hsv[..., 1] >= 70) & (hsv[..., 2] >= 80)).astype(np.uint8) * 255
    seed[_ignore_mask(H, W)] = 0
    seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < min_area_frac * H * W:
            continue
        pad = int(0.15 * max(w, h))
        inst = grabcut_from_rect(img, (x - pad, y - pad, w + 2 * pad, h + 2 * pad))
        if inst:
            inst.method = "cardboard_prior_grabcut"
            inst.confidence = round(min(inst.confidence, 0.6), 3)
            out.append(inst)
    return out
