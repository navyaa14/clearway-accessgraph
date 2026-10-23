"""Audit v2.3 P1: evidence ORIGIN must be compatible for automatic incident resolution.

RING and RING_API_EMULATOR are both server-captured ("LIVE" trust) but are different technologies. Emulator,
synthetic or replay evidence must never close a genuine Ring incident; an emulator incident must not be silently
upgraded to a genuine-Ring verification; an origin change (deployment migration) requires review.
Tested at verify_new_evidence() and through the real ingest → routing path.
"""
import tempfile
from pathlib import Path

from app import fixtures as FX
from app import incidents as SM
from app.config import settings
from app.db import DB
from app.service import Service


def A(cls="NO_OBSTRUCTION_OBSERVED", cal=True):
    return {"classification": cls, "primary_object": "obj1", "objects": [{"id": "obj1", "nominal": {"clear_width": {"min_clear_width": 110}}}],
            "quality": {"usable": True}, "calibration_check": {"valid": cal}, "explanation": "x"}


def INC(origin, streak=1, cal="cal-v1"):
    return {"state": "AWAITING_NEW_EVIDENCE", "namespace": "LIVE", "evidence_origin": origin, "clear_streak": streak, "last_metric": 65.0,
            "latest_captured_at": 100.0, "last_clear_session": "s1", "last_clear_at": 100.0, "calibration_version": cal}


def OBS(origin, t=200.0, session="s2", cal="cal-v1", src=None):
    return {"captured_at": t, "frame_sha256": f"h{t}{origin}", "trust": "LIVE", "chronology": "SERVER_CAPTURED",
            "evidence_origin": origin, "capture_session": session, "calibration_version": cal,
            "media_source": src or {"RING": "RING_PLAYGROUND", "RING_API_EMULATOR": "RING_API_EMULATOR"}.get(origin, origin)}


def verify(inc, ob, a=None):
    return SM.verify_new_evidence(inc, ob, a or A(), set(), close_after=2, min_clear_separation_s=60)


# ------------------------------------------------------------------ verify_new_evidence()
def test_ring_incident_with_ring_evidence_resolves():
    v = verify(INC("RING"), OBS("RING"))
    assert v.accepted and v.next_state == "RESOLVED" and v.counts_as_clear


def test_ring_incident_rejects_emulator_evidence_exact_audit_repro():
    v = verify(INC("RING"), OBS("RING_API_EMULATOR"))  # audit: previously accepted=True, next_state=RESOLVED
    assert not v.accepted and v.disposition == "ORIGIN_MISMATCH" and v.next_state is None


def test_ring_incident_rejects_synthetic_and_replay():
    for origin, src in (("SYNTHETIC", "SYNTHETIC_RENDER"), ("REPLAY", "RECORDED_REPLAY"), ("INDEPENDENT_TEST", "INDEPENDENT_TEST_VIDEO")):
        ob = OBS(origin, src=src)
        ob.update(trust="DEMO", chronology="CLIENT_ASSERTED")
        v = verify(INC("RING"), ob)
        assert not v.accepted and v.disposition in ("ORIGIN_MISMATCH", "UNTRUSTED_SOURCE")
        ob.update(trust="LIVE", chronology="SERVER_CAPTURED")  # even if mislabelled as server-captured
        v = verify(INC("RING"), ob)
        assert not v.accepted and v.disposition == "ORIGIN_MISMATCH"


def test_emulator_incident_with_emulator_evidence_follows_emulator_policy():
    v = verify(INC("RING_API_EMULATOR"), OBS("RING_API_EMULATOR"))
    assert v.accepted and v.next_state == "RESOLVED"


def test_emulator_incident_with_genuine_ring_evidence_requires_review():
    v = verify(INC("RING_API_EMULATOR"), OBS("RING"))
    assert v.accepted and v.disposition == "ORIGIN_MISMATCH" and v.next_state == "REVIEW_REQUIRED" and not v.counts_as_clear


def test_invalid_calibration_and_insufficient_separation_still_block():
    assert verify(INC("RING"), OBS("RING"), A(cal=False)).next_state == "UNABLE_TO_VERIFY"
    assert verify(INC("RING"), OBS("RING", cal="cal-v2")).next_state == "REVIEW_REQUIRED"
    v = verify(INC("RING"), OBS("RING", t=130.0))  # only 30 s after the previous clear
    assert v.next_state == "AWAITING_NEW_EVIDENCE" and not v.counts_as_clear
    v = verify(INC("RING"), OBS("RING", session="s1"))  # same capture session
    assert v.next_state == "AWAITING_NEW_EVIDENCE" and not v.counts_as_clear


# ------------------------------------------------------------------ real routing path
def svc():
    d = Path(tempfile.mkdtemp(prefix="cw-origin-"))
    s = Service(DB(d / "clearway.db"), d)
    s.provision_live_household("hh_live", "live-hh", "live-appr", None, "dev-1")
    s.db.update("cameras", "id", "hh_live_ring", {"authorized": 1})
    s.save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), FX.scene("reference"), "RING_PLAYGROUND")
    return s


def open_incident(s, source):
    s.ingest("hh_live", "hh_live_ring", FX.scene("initial"), source, "RING_API_CAPTURE", extra={"capture_session": "open"})
    return s.db.one("SELECT * FROM incidents WHERE household_id='hh_live'")


def clear(s, source, session, seed):
    s.ingest("hh_live", "hh_live_ring", FX.scene("moved_to_zone", seed=seed), source, "RING_API_CAPTURE", extra={"capture_session": session})
    return s.db.one("SELECT * FROM incidents WHERE household_id='hh_live'")


def test_routing_genuine_incident_cannot_be_closed_by_emulator(monkeypatch):
    monkeypatch.setattr(settings, "live_min_clear_separation_s", 0.0)
    s = svc()
    inc = open_incident(s, "RING_PLAYGROUND")
    assert inc["evidence_origin"] == "RING"
    inc = clear(s, "RING_PLAYGROUND", "c1", 61)
    assert inc["state"] != "RESOLVED" and inc["clear_streak"] == 1
    inc = clear(s, "RING_API_EMULATOR", "c2", 62)  # the audit's migration scenario
    assert inc["state"] != "RESOLVED" and inc["clear_streak"] == 1
    kinds = [r["kind"] for r in s.db.all("SELECT kind FROM ledger WHERE household_id='hh_live'")]
    assert "EVIDENCE_ORIGIN_MISMATCH" in kinds and "EVIDENCE_REJECTED" in kinds
    inc = clear(s, "RING_PLAYGROUND", "c3", 63)  # a genuine, independent capture still closes it
    assert inc["state"] == "RESOLVED" and inc["verification_basis"] == "GENUINE_RING_CAPTURE"


def test_routing_emulator_incident_not_upgraded_by_switch_to_genuine_ring(monkeypatch):
    monkeypatch.setattr(settings, "live_min_clear_separation_s", 0.0)
    s = svc()
    open_incident(s, "RING_API_EMULATOR")
    inc = clear(s, "RING_API_EMULATOR", "c1", 61)
    assert inc["clear_streak"] == 1
    inc = clear(s, "RING_PLAYGROUND", "c2", 62)  # deployment switched to genuine Ring mid-incident
    assert inc["state"] == "REVIEW_REQUIRED" and inc["clear_streak"] == 0 and inc["verification_basis"] is None
    assert inc["evidence_origin"] == "RING_API_EMULATOR"  # provenance is never upgraded


def test_routing_emulator_only_incident_still_verifies_and_is_labelled(monkeypatch):
    monkeypatch.setattr(settings, "live_min_clear_separation_s", 0.0)
    s = svc()
    open_incident(s, "RING_API_EMULATOR")
    clear(s, "RING_API_EMULATOR", "c1", 61)
    inc = clear(s, "RING_API_EMULATOR", "c2", 62)
    assert inc["state"] == "RESOLVED" and inc["verification_basis"] == "EMULATOR_CAPTURE"


def test_pre_v23_incident_without_origin_is_derived_not_assumed(monkeypatch):
    monkeypatch.setattr(settings, "live_min_clear_separation_s", 0.0)
    s = svc()
    inc = open_incident(s, "RING_PLAYGROUND")
    s.db.update("incidents", "id", inc["id"], {"evidence_origin": None})  # legacy row
    inc = clear(s, "RING_API_EMULATOR", "c1", 61)
    assert inc["evidence_origin"] == "RING" and inc["clear_streak"] == 0 and inc["state"] != "RESOLVED"
