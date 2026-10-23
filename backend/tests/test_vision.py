"""Image-based tests: real inference on labelled synthetic renders (exact ground truth)."""
import numpy as np
import pytest

from app.fixtures import DEMO_BOXES, ground_truth, profile_for, scene
from app.vision import geometry as G
from app.vision.pipeline import analyze
from app.vision.quality import assess
from app.vision.synth import Box, Camera, SceneSpec, render


def run(img, ref, hh="hh_b", metric=True):
    res, ann = analyze(img, profile_for(hh, metric=metric), ref)
    assert ann.shape == img.shape
    return res


def primary(res):
    return next(o for o in res["objects"] if o["id"] == res["primary_object"])


# 1
def test_parcel_outside_route_is_not_obstruction(ref):
    res = run(render(SceneSpec(boxes=[Box(45, 230)])), ref)
    assert res["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert res["objects"], "the parcel itself must still be detected"


# 2
def test_parcel_overlapping_route(ref):
    res = run(scene("centre_block"), ref)
    assert res["classification"] == "POTENTIAL_OBSTRUCTION"
    o = primary(res)
    gt = ground_truth(DEMO_BOXES["centre_block"], "hh_b")
    assert abs(o["nominal"]["clear_width"]["min_clear_width"] - gt["clear_width_cm"]) < 8
    assert o["p_obstructed"] >= 0.9


# 3
def test_same_frame_different_household_needs(ref):
    img = scene("initial")
    a, b = run(img, ref, "hh_a"), run(img, ref, "hh_b")
    assert a["frame"]["sha256"] == b["frame"]["sha256"]
    assert a["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert b["classification"] == "POTENTIAL_OBSTRUCTION"


def test_bbox_overlap_alone_is_not_obstruction(ref):
    """Tall object beside the route occludes route pixels but its footprint is off-route."""
    img = render(SceneSpec(boxes=[Box(236, 330, w=40, d=40, h=70)]))
    res = run(img, ref, "hh_b", metric=False)
    o = primary(res)
    x, y, w, h = o["bbox"]
    route = G.poly([[p[0] * 960, p[1] * 720] for p in res["geometry_inputs"]["route"]])
    bbox = G.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
    assert bbox.intersects(route), "test premise: the bounding box overlaps the route on screen"
    assert res["classification"] == "NO_OBSTRUCTION_OBSERVED"


# 4/5 geometry
def test_partial_move_reduces_but_does_not_clear(ref):
    r1, r2 = run(scene("initial"), ref), run(scene("partial_move"), ref)
    c1 = primary(r1)["nominal"]["clear_width"]["min_clear_width"]
    c2 = primary(r2)["nominal"]["clear_width"]["min_clear_width"]
    assert c2 > c1 + 5
    assert r2["classification"] == "POTENTIAL_OBSTRUCTION"


def test_moved_into_drop_zone(ref):
    res = run(scene("moved_to_zone"), ref)
    assert res["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert primary(res)["inside_drop_zone_fraction"] >= 0.8


# 6
def test_obscured_view_unable_to_verify(ref):
    res = run(scene("initial", obscure=0.55), ref)
    assert res["classification"] == "UNABLE_TO_VERIFY"
    assert res["quality"]["obscured_fraction"] > 0.3


# 7
@pytest.mark.parametrize("kw", [{"gain": 0.14, "noise": 6}, {"blur_sigma": 7}])
def test_low_light_or_blurry_unable_to_verify(ref, kw):
    res = run(scene("initial", **kw), ref)
    assert res["classification"] == "UNABLE_TO_VERIFY"
    assert res["quality"]["reasons"]


def test_mild_lighting_change_still_analyzed(ref):
    res = run(scene("centre_block", gain=1.12), ref)
    assert res["classification"] == "POTENTIAL_OBSTRUCTION"


# 8
def test_camera_displacement_invalidates_calibration(ref):
    res = run(scene("initial", camera="moved"), ref)
    assert res["calibration_check"]["status"] == "DISPLACED"
    assert res["classification"] == "UNABLE_TO_VERIFY"


def test_stable_camera_passes_displacement_check(ref):
    res = run(scene("initial"), ref)
    assert res["calibration_check"]["status"] == "STABLE"


# 9
def test_unreliable_ground_contact_requires_review(ref):
    res = run(render(SceneSpec(boxes=[Box(150, 62, h=40)])), ref)
    o = primary(res)
    assert not o["contact"]["reliable"]
    assert res["classification"] == "REVIEW_REQUIRED"


# 14
def test_uncertainty_straddling_threshold_requires_review(ref):
    box = Box(213.5, 300)  # true clear width 91.0 cm == household B requirement
    assert abs(ground_truth(box, "hh_b")["clear_width_cm"] - 91.0) < 0.6
    res = run(render(SceneSpec(boxes=[box])), ref)
    assert res["classification"] == "REVIEW_REQUIRED"
    assert 0.1 < primary(res)["p_obstructed"] < 0.9


def test_apparent_mode_reports_limitation(ref):
    res = run(scene("centre_block"), ref, metric=False)
    assert res["mode"] == "APPARENT"
    assert res["classification"] == "POTENTIAL_OBSTRUCTION"
    assert any("metric clear width cannot be established" in l for l in res["limitations"])
    assert primary(res)["nominal"]["units"] == "px"


def test_no_objects_in_clear_scene(ref):
    res = run(scene("reference"), ref)
    assert res["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert res["objects"] == []


def test_counterfactual_proposes_drop_zone(ref):
    res = run(scene("initial"), ref)
    cf = res["counterfactual"]
    assert cf["resolves"] and cf["target"] == "drop_zone"
    assert cf["proposed_footprint"] and cf["current_footprint"]
    assert any("does not show that anyone moved" in c for c in cf["caveats"])


def test_explainability_fields_present(ref):
    res = run(scene("initial"), ref)
    for k in ("frame", "calibration_version", "model_version", "quality", "calibration_check", "geometry_inputs", "limitations", "explanation", "timings"):
        assert k in res
    o = primary(res)
    for k in ("footprint", "mask_outline", "envelope_outer", "nominal", "envelope", "p_obstructed", "contact"):
        assert k in o


def test_quality_metrics_monotone(ref):
    sharp = assess(scene("reference"), ref)["relative_sharpness"]
    blurry = assess(scene("reference", blur_sigma=5), ref)["relative_sharpness"]
    assert sharp > 0.9 > 0.3 > blurry


def test_ring_watermark_regions_ignored_by_change_detection(ref):
    """A changing timestamp in the watermark strip must not become an 'obstacle'."""
    img = scene("reference").copy()
    img[5:45, 650:950] = 255 - img[5:45, 650:950]
    res = run(img, ref)
    assert res["objects"] == []
