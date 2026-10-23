"""End-to-end scene analysis: quality -> displacement -> segmentation -> footprint
-> geometry -> uncertainty -> counterfactual -> annotated evidence."""
from __future__ import annotations

import hashlib
import os
import time

import cv2
import numpy as np

from . import counterfactual as CF
from . import geometry as G
from .calibration import check_displacement, denorm, floor_homography, to_world
from .quality import assess
from .segmentation import MODEL_VERSION, cardboard_seeded, change_detect, grabcut_from_rect
from .uncertainty import evaluate_object

PIPELINE_VERSION = "accessgraph-pipeline-2.1.0"
MAX_CHANGED_FRACTION = 0.35
MAX_RESIDUAL_ROUTE_CHANGE = 0.004  # unexplained changed pixels on the route (fraction of route area)
MAX_ROUTE_DETAIL_LOSS = 0.25  # route blocks (not covered by a detected object) that lost their texture
SEVERITY = {"POTENTIAL_OBSTRUCTION": 3, "REVIEW_REQUIRED": 2, "NO_OBSTRUCTION_OBSERVED": 1, "UNABLE_TO_VERIFY": 4}

TEAL = (180, 200, 20)
AMBER = (20, 170, 245)
RED = (60, 60, 235)
BLUE = (230, 160, 70)
WHITE = (245, 245, 245)


def frame_hash(img: np.ndarray) -> str:
    return hashlib.sha256(cv2.imencode(".png", img)[1].tobytes()).hexdigest()


def _norm_poly(p, w, h):
    if p is None or p.is_empty:
        return None
    return [[round(x / w, 5), round(y / h, 5)] for x, y in list(p.exterior.coords)[:-1]]


def _draw_poly(img, poly, color, alpha=0.0, thick=2, dashed=False):
    if poly is None or poly.is_empty:
        return
    geoms = [poly] if poly.geom_type == "Polygon" else [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
    for g in geoms:
        pts = np.round(np.array(g.exterior.coords)).astype(np.int32)
        if alpha > 0:
            ov = img.copy()
            cv2.fillPoly(ov, [pts], color)
            cv2.addWeighted(ov, alpha, img, 1 - alpha, 0, img)
        if dashed:
            for i in range(len(pts) - 1):
                a, b = pts[i].astype(float), pts[i + 1].astype(float)
                L = np.linalg.norm(b - a)
                for s in np.arange(0, L, 14):
                    e = min(s + 8, L)
                    cv2.line(img, tuple((a + (b - a) * s / L).astype(int)), tuple((a + (b - a) * e / L).astype(int)), color, thick, cv2.LINE_AA)
        else:
            cv2.polylines(img, [pts], True, color, thick, cv2.LINE_AA)


MC_SAMPLES = int(os.getenv("CLEARWAY_MC_SAMPLES", "160"))


def analyze(frame: np.ndarray, profile: dict, reference: np.ndarray | None, *, prompt_rect=None, n_samples: int | None = None) -> tuple[dict, np.ndarray]:
    n_samples = n_samples or MC_SAMPLES
    t0 = time.perf_counter()
    timings = {}
    h, w = frame.shape[:2]
    limitations: list[str] = []
    stage_log: list[str] = []
    result: dict = {
        "pipeline_version": PIPELINE_VERSION,
        "model_version": MODEL_VERSION,
        "calibration_version": profile.get("version"),
        "frame": {"sha256": frame_hash(frame), "width": w, "height": h},
        "geometry_inputs": {"route": profile.get("route"), "drop_zone": profile.get("drop_zone"),
                            "clearance_cm": profile.get("clearance_cm"), "floor_points_img": profile.get("floor_points_img")},
        "objects": [],
    }
    # Stage 1a: image quality
    q = assess(frame, reference)
    timings["quality_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    result["quality"] = q
    stage_log.append(f"quality: {'usable' if q['usable'] else 'REJECTED'}")

    # calibration validity
    t1 = time.perf_counter()
    if reference is not None:
        disp = check_displacement(reference, frame)
    else:
        disp = {"status": "UNKNOWN", "valid": None, "reason": "no calibration reference frame"}
    timings["calibration_check_ms"] = round((time.perf_counter() - t1) * 1000, 1)
    result["calibration_check"] = disp

    hom = floor_homography(profile.get("floor_points_img"), profile.get("floor_points_world"), w, h)
    result["mode"] = "METRIC" if hom else "APPARENT"
    result["homography_error_cm"] = hom["reprojection_error_cm"] if hom else None
    if not hom:
        limitations.append("No floor reference points: apparent image-space overlap only; metric clear width cannot be established.")

    def finish(cls, explanation):
        result["classification"] = cls
        result["explanation"] = explanation
        result["verification_guidance"] = guidance(cls, result)
        result["limitations"] = limitations + [
            "Camera-based visual assessment only; this is not a certified accessibility or regulatory assessment.",
            "A single camera view cannot see occluded areas or the far side of objects.",
        ]
        result["stage_log"] = stage_log
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        result["timings"] = timings
        return result

    if not q["usable"]:
        ann = frame.copy()
        _banner(ann, "UNABLE TO VERIFY: " + "; ".join(q["reasons"])[:90], AMBER)
        return finish("UNABLE_TO_VERIFY", "Image quality does not support a judgement: " + "; ".join(q["reasons"])), ann
    if disp.get("valid") is False:
        ann = frame.copy()
        _banner(ann, "UNABLE TO VERIFY: camera moved since calibration - recalibrate", AMBER)
        detail = f" (shift {disp.get('shift_px')} px, rotation {disp.get('rotation_deg')} deg)" if disp.get("shift_px") is not None else ""
        return finish("UNABLE_TO_VERIFY", f"Calibration invalid: {disp['reason']}{detail}. The marked route no longer maps to the floor reliably."), ann
    if disp.get("valid") is None and reference is not None:
        limitations.append(f"Camera stability could not be confirmed ({disp['reason']}).")

    # Stage 1b: segmentation
    t2 = time.perf_counter()
    seg_meta = {}
    if prompt_rect is not None:
        inst = grabcut_from_rect(frame, prompt_rect)
        instances = [inst] if inst else []
        stage_log.append("segmentation: user-prompted GrabCut")
    elif reference is not None:
        instances, seg_meta = change_detect(frame, reference)
        stage_log.append(f"segmentation: change detection vs reference ({len(instances)} instances)")
    else:
        instances = cardboard_seeded(frame)
        limitations.append("No clear-scene reference: parcel-colour prior used; non-parcel obstacles may be missed.")
        stage_log.append(f"segmentation: cardboard prior ({len(instances)} instances)")
    timings["segmentation_ms"] = round((time.perf_counter() - t2) * 1000, 1)
    changed_mask = seg_meta.pop("_changed_mask", None)
    result["segmentation"] = seg_meta
    if seg_meta.get("changed_fraction", 0) > MAX_CHANGED_FRACTION:
        ann = frame.copy()
        _banner(ann, "UNABLE TO VERIFY: most of the scene differs from the calibration reference", AMBER)
        return finish("UNABLE_TO_VERIFY", f"{seg_meta['changed_fraction'] * 100:.0f}% of the scene differs from the clear-scene reference "
                      "(occlusion, strong lighting change or camera change); individual objects cannot be separated reliably."), ann

    ctx = {"w": w, "h": h, "route": profile["route"], "homography": hom, "clearance_cm": profile.get("clearance_cm"),
           "floor_points_img": profile.get("floor_points_img"), "floor_points_world": profile.get("floor_points_world")}
    route_img = G.poly(denorm(profile["route"], w, h))
    zone_img = G.poly(denorm(profile["drop_zone"], w, h)) if profile.get("drop_zone") else None

    # Stage 2: geometry + uncertainty per instance
    t3 = time.perf_counter()
    evaluated = []
    for k, inst in enumerate(instances[:6]):
        ev = evaluate_object(inst, ctx, n=n_samples, seed=k)
        evaluated.append((inst, ev))
    timings["geometry_uncertainty_ms"] = round((time.perf_counter() - t3) * 1000, 1)

    ann = frame.copy()
    _draw_poly(ann, route_img, TEAL, alpha=0.22, thick=2)
    if zone_img is not None:
        _draw_poly(ann, zone_img, BLUE, alpha=0.10, thick=2, dashed=True)

    # Detection-quality gate: "nothing detected" is not the same as "route observed clear".
    route_mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(route_mask, [np.round(np.array(route_img.exterior.coords)).astype(np.int32)], 1)
    objects_mask = np.zeros((h, w), bool)
    for inst in instances:
        objects_mask |= inst.mask > 0
    route_px = max(int(route_mask.sum()), 1)
    gate: list[str] = []
    residual = None
    if changed_mask is not None:
        residual = float(((changed_mask > 0) & (route_mask > 0) & ~objects_mask).sum() / route_px)
        if residual > MAX_RESIDUAL_ROUTE_CHANGE:
            gate.append(f"{residual * 100:.1f}% of the marked route changed in ways that could not be segmented into a measurable object")
    detail_loss = None
    if reference is not None:
        detail_loss = route_detail_loss(frame, reference, route_mask, objects_mask)
        if detail_loss is not None and detail_loss > MAX_ROUTE_DETAIL_LOSS:
            _banner(ann, "UNABLE TO VERIFY: the marked route is not clearly visible", AMBER)
            result["detection_gate"] = {"route_residual_change": residual, "route_detail_loss": detail_loss}
            return finish("UNABLE_TO_VERIFY", f"{detail_loss * 100:.0f}% of the visible route surface lost detail compared with the reference "
                          "(glare, darkness, rain or something on the lens), so absence of obstacles cannot be confirmed."), ann
    if reference is None and prompt_rect is None:
        # The colour-prior fallback is advisory only: it can highlight candidates (and is fooled by
        # cardboard-coloured surfaces such as terracotta), so it never yields a definite conclusion.
        gate.append("no clear-scene reference: the colour-prior fallback cannot confirm or rule out an obstruction")
    if not instances and prompt_rect is not None:
        gate.append("the user-indicated region could not be segmented")
    result["detection_gate"] = {"route_residual_change": None if residual is None else round(residual, 5),
                                "route_detail_loss": None if detail_loss is None else round(detail_loss, 4), "reasons": gate}

    if not evaluated:
        stage_log.append("no candidate obstacles")
        if gate:
            _banner(ann, "REVIEW REQUIRED - route not adequately observed", AMBER)
            return finish("REVIEW_REQUIRED", "No object was segmented, but the route was not adequately observed: " + "; ".join(gate) + "."), ann
        _banner(ann, "NO OBSTRUCTION OBSERVED in this camera view", TEAL)
        return finish("NO_OBSTRUCTION_OBSERVED", "No obstruction observed in this camera view: no new object was segmented compared "
                      "with the calibration reference, and the visible route surface matches the reference."), ann

    primary_idx, primary_sev = 0, -1
    for k, (inst, ev) in enumerate(evaluated):
        sev = SEVERITY.get(ev["classification"], 0) * 10 + (ev.get("p_obstructed") or 0)
        if sev > primary_sev:
            primary_idx, primary_sev = k, sev
        fp_img, outer = ev.get("_fp_img"), ev.get("_outer_img")
        cs = inst.contour()
        if len(cs):
            cv2.polylines(ann, [cs.astype(np.int32)], True, AMBER, 1, cv2.LINE_AA)
        if outer is not None:
            _draw_poly(ann, outer, AMBER, thick=1, dashed=True)
        if fp_img is not None:
            obstructed = ev["classification"] == "POTENTIAL_OBSTRUCTION"
            _draw_poly(ann, fp_img, RED if obstructed else (120, 220, 120), alpha=0.35, thick=2)
            inter = fp_img.intersection(route_img)
            if not inter.is_empty:
                _draw_poly(ann, inter, RED, alpha=0.55, thick=1)
        x, y, bw, bh = inst.bbox
        p = ev.get("p_obstructed")
        label = f"#{k + 1} {ev['classification'].replace('_', ' ').lower()}" + (f"  agree={p:.2f}" if p is not None else "")
        ty = max(y - 10, int(0.1 * h) + 14)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        tx = int(min(max(x, 4), w - tw - 10))
        cv2.rectangle(ann, (tx - 4, ty - th - 6), (tx + tw + 4, ty + 5), (25, 22, 18), -1)
        cv2.putText(ann, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA)
        obj = {k2: v for k2, v in ev.items() if not k2.startswith("_")}
        obj.update({
            "id": f"obj{k + 1}", "bbox": list(inst.bbox), "area_px": inst.area, "segmentation_confidence": inst.confidence,
            "parcel_likeness": inst.parcel_likeness, "segmentation_method": inst.method,
            "mask_outline": [[round(float(a) / w, 5), round(float(b) / h, 5)] for a, b in cs[:: max(1, len(cs) // 120)]] if len(cs) else [],
            "footprint": _norm_poly(fp_img, w, h), "envelope_outer": _norm_poly(outer, w, h),
            "inside_drop_zone_fraction": round(G.inside_fraction(fp_img, zone_img), 3) if fp_img is not None else None,
        })
        result["objects"].append(obj)

    inst, ev = evaluated[primary_idx]
    result["primary_object"] = f"obj{primary_idx + 1}"
    scene_cls = max((e["classification"] for _, e in evaluated), key=lambda c: SEVERITY[c])
    if reference is None and prompt_rect is None and scene_cls != "UNABLE_TO_VERIFY":
        scene_cls = "REVIEW_REQUIRED"
        ev = {**ev, "reasons": (ev.get("reasons") or []) + gate}
    elif scene_cls == "NO_OBSTRUCTION_OBSERVED" and gate:
        scene_cls = "REVIEW_REQUIRED"
        ev = {**ev, "reasons": (ev.get("reasons") or []) + gate}

    # Counterfactual for the primary object when relevant
    if scene_cls in ("POTENTIAL_OBSTRUCTION", "REVIEW_REQUIRED") and ev.get("_fp_work") is not None:
        t4 = time.perf_counter()
        metric = hom is not None
        zone_work = None
        if zone_img is not None:
            zone_work = G.poly(to_world(hom["H"], np.array(zone_img.exterior.coords)[:-1])) if metric else zone_img
        cf = CF.plan(ev["_fp_work"], ev["_route_work"], zone_work, metric)
        if "_proposed_work" in cf:
            prop_img = CF.to_image(cf.pop("_proposed_work"), metric, hom["Hinv"] if metric else None)
            cf["proposed_footprint"] = _norm_poly(prop_img, w, h)
            cf["current_footprint"] = result["objects"][primary_idx]["footprint"]
        result["counterfactual"] = cf
        timings["counterfactual_ms"] = round((time.perf_counter() - t4) * 1000, 1)

    p = ev.get("p_obstructed")
    nom = ev.get("nominal", {})
    units = nom.get("units", "px")
    if scene_cls == "POTENTIAL_OBSTRUCTION":
        cw = nom.get("clear_width")
        detail = (f"estimated clear width {cw['min_clear_width']:.0f} cm vs required {profile.get('clearance_cm')} cm"
                  if cw else f"{nom['fraction_of_footprint'] * 100:.0f}% of its estimated ground footprint lies inside the marked route")
        expl = (f"Object {result['primary_object']} appears to obstruct the marked route: {detail} (single-camera estimate). "
                f"Perturbation agreement: {p * 100:.0f}% of {ev['samples']} perturbed re-measurements reach the same conclusion.")
        _banner(ann, "POTENTIAL OBSTRUCTION of marked route", RED)
    elif scene_cls == "REVIEW_REQUIRED":
        expl = f"Evidence is inconclusive for {result['primary_object']}: " + "; ".join(ev.get("reasons") or ["uncertain"]) + "."
        _banner(ann, "REVIEW REQUIRED - evidence inconclusive", AMBER)
    else:
        dist = nom.get("distance_to_route")
        cw = nom.get("clear_width")
        if cw and nom.get("intersection_area", 0) > 0:
            core = (f"{len(evaluated)} object(s) detected; the estimated clear width ({cw['min_clear_width']:.0f} cm, single-camera estimate) "
                    f"meets the {profile.get('clearance_cm')} cm requirement")
        else:
            core = (f"{len(evaluated)} object(s) detected, but no estimated ground footprint overlaps the marked route"
                    + (f" (nearest gap {dist:.0f} {units})" if dist else ""))
        expl = "No obstruction observed in this camera view: " + core + f"; perturbation agreement {(1 - (p or 0)) * 100:.0f}%."
        _banner(ann, "NO OBSTRUCTION OBSERVED in this camera view", TEAL)
    return finish(scene_cls, expl), ann


def route_detail_loss(frame, reference, route_mask, objects_mask) -> float | None:
    """Fraction of textured route blocks (not covered by a detected object) whose detail vanished vs the reference."""
    from .quality import texture_map
    tc = texture_map(frame)
    tr = texture_map(cv2.resize(reference, (frame.shape[1], frame.shape[0])))
    gh, gw = tc.shape

    def blocks(mask):
        m = cv2.resize(mask.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
        return m

    on_route = blocks(route_mask) > 0.6
    free = blocks(objects_mask) < 0.05
    sel = on_route & free & (tr > 3.0)
    if sel.sum() < 4:
        return None
    return float(((tc < 0.3 * tr) & sel).sum() / sel.sum())


GUIDANCE = [
    ("blurry", "Wipe the camera lens or wait for motion to stop, then request a new capture."),
    ("too dark", "Turn on the entrance light or wait for daylight before re-checking."),
    ("overexposed", "Strong glare: re-check when the sun is no longer shining into the camera."),
    ("clipped", "Exposure is extreme: re-check when lighting changes."),
    ("obscured", "Something may be covering the lens; an authorised person could check the camera."),
    ("lost detail", "The route surface is not clearly visible (glare, darkness, rain or lens dirt); re-check later or confirm in person."),
    ("Calibration invalid", "The camera appears to have moved: recalibrate the route in Spatial twin before relying on results."),
    ("differs from the clear-scene reference", "Most of the view changed (lighting or occlusion): capture again later or recalibrate."),
    ("ground-contact estimate unreliable", "The object is at the edge of the view; capture again once it is fully visible, or confirm in person."),
    ("perturbations disagree", "The measured clearance is close to your requirement; an in-person check is recommended."),
    ("could not be segmented", "Something changed on the route that could not be measured; please confirm in person."),
    ("segmentation confidence", "The object outline is uncertain; a clearer capture or an in-person check is needed."),
    ("no clear-scene reference", "Capture a clear-scene reference frame so ClearWay can tell what is new on the route."),
]


def guidance(cls: str, result: dict) -> list[str]:
    """Active verification guidance: what evidence is missing and a safe way to obtain it.
    Never dispatches anyone and never declares the route safe."""
    if cls not in ("UNABLE_TO_VERIFY", "REVIEW_REQUIRED"):
        return []
    text = " ".join([result.get("explanation", "")] + list(result.get("quality", {}).get("reasons", []))
                    + [r for o in result.get("objects", []) for r in (o.get("reasons") or [])]
                    + list((result.get("detection_gate") or {}).get("reasons", [])))
    out = [g for key, g in GUIDANCE if key in text]
    return list(dict.fromkeys(out)) or ["Request a new capture, or ask an authorised person to check the route in person."]


def _banner(img, text, color):
    h, w = img.shape[:2]
    y0 = h - 34
    ov = img.copy()
    cv2.rectangle(ov, (0, y0), (w, h), (25, 22, 18), -1)
    cv2.addWeighted(ov, 0.75, img, 0.25, 0, img)
    cv2.circle(img, (18, y0 + 17), 7, color, -1, cv2.LINE_AA)
    cv2.putText(img, text, (34, y0 + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.58, WHITE, 1, cv2.LINE_AA)
