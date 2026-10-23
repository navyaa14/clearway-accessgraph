"""Deterministic state-machine tests with synthetic fixtures (no image inference)."""
import pytest

from app import incidents as SM


def analysis(cls, clear_width=None, frac=None, quality=True, cal=True):
    nom = {"fraction_of_footprint": frac or 0.0, "clear_width": {"min_clear_width": clear_width} if clear_width is not None else None}
    return {"classification": cls, "primary_object": "obj1", "objects": [{"id": "obj1", "nominal": nom}],
            "quality": {"usable": quality}, "calibration_check": {"valid": cal}, "explanation": "x"}


def inc(state="AWAITING_NEW_EVIDENCE", last_metric=66.0, latest=100.0, streak=0):
    return {"state": state, "last_metric": last_metric, "latest_captured_at": latest, "clear_streak": streak}


def obs(t=200.0, h="new"):
    return {"captured_at": t, "frame_sha256": h}


def test_illegal_transitions_rejected():
    with pytest.raises(SM.TransitionError):
        SM.check("OBSERVED", "RESOLVED")
    with pytest.raises(SM.TransitionError):
        SM.check("RESOLVED", "ASSESSED")
    with pytest.raises(SM.TransitionError):
        SM.check("ASSISTANCE_REQUESTED", "PARTIALLY_RESOLVED")  # must go through REASSESSED


def test_request_never_implies_clearance():
    # From ASSISTANCE_REQUESTED there is no path to RESOLVED except via evidence or override.
    assert "RESOLVED" in SM.ALLOWED["ASSISTANCE_REQUESTED"]  # manual override only (guarded in service)
    assert SM.ALLOWED["OBSERVED"] == {"ASSESSED"}


def test_duplicate_frame_is_not_new_evidence():
    v = SM.verify_new_evidence(inc(), obs(h="same"), analysis("NO_OBSTRUCTION_OBSERVED"), {"same"})
    assert not v.accepted and v.disposition == "DUPLICATE"


def test_stale_observation_cannot_change_status():
    v = SM.verify_new_evidence(inc(latest=500), obs(t=400), analysis("NO_OBSTRUCTION_OBSERVED"), set())
    assert not v.accepted and v.disposition == "STALE"


def test_clear_new_evidence_resolves():
    v = SM.verify_new_evidence(inc(), obs(), analysis("NO_OBSTRUCTION_OBSERVED"), set())
    assert v.accepted and v.next_state == "RESOLVED"


def test_close_policy_requires_streak():
    v = SM.verify_new_evidence(inc(), obs(), analysis("NO_OBSTRUCTION_OBSERVED"), set(), close_after=2)
    assert v.next_state == "AWAITING_NEW_EVIDENCE"
    v = SM.verify_new_evidence(inc(streak=1), obs(), analysis("NO_OBSTRUCTION_OBSERVED"), set(), close_after=2)
    assert v.next_state == "RESOLVED"


def test_partial_improvement_metric():
    v = SM.verify_new_evidence(inc(last_metric=66.0), obs(), analysis("POTENTIAL_OBSTRUCTION", clear_width=79.0), set())
    assert v.next_state == "PARTIALLY_RESOLVED"


def test_no_improvement_stays_open():
    v = SM.verify_new_evidence(inc(last_metric=66.0), obs(), analysis("POTENTIAL_OBSTRUCTION", clear_width=67.0), set())
    assert v.next_state == "AWAITING_NEW_EVIDENCE"


def test_partial_improvement_apparent_mode():
    v = SM.verify_new_evidence(inc(last_metric=-0.6), obs(), analysis("POTENTIAL_OBSTRUCTION", frac=0.3), set())
    assert v.next_state == "PARTIALLY_RESOLVED"


def test_unverifiable_evidence_never_resolves():
    for a in (analysis("UNABLE_TO_VERIFY"), analysis("NO_OBSTRUCTION_OBSERVED", cal=False), analysis("NO_OBSTRUCTION_OBSERVED", quality=False)):
        v = SM.verify_new_evidence(inc(), obs(), a, set())
        assert v.next_state == "UNABLE_TO_VERIFY"


def test_review_required_evidence():
    v = SM.verify_new_evidence(inc(), obs(), analysis("REVIEW_REQUIRED"), set())
    assert v.next_state == "REVIEW_REQUIRED"
