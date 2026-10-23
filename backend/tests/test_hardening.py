"""Regression tests for the pre-submission audit findings (P0 integrity/security + P1 vision gates).

Note on 'live' fixtures: no Ring credentials exist in CI, so live-household tests feed synthetic frames
through the server-side ingest path with a Ring media label. They test the evidence POLICY, not Ring itself.
"""
import time

import cv2
import numpy as np
import pytest

from app import fixtures as FX
from app import incidents as SM
from app.config import settings
from app.main import STATE

from .conftest import H

LIVE = {"X-Household-Token": "live-hh-token"}
LIVE_APPROVER = {**LIVE, "X-Member-Token": "live-approver-token"}


def live_setup():
    s = STATE["svc"]
    s.provision_live_household("hh_live", "live-hh-token", "live-approver-token", "live-helper-token", "ring-dev-1")
    s.db.update("cameras", "id", "hh_live_ring", {"authorized": 1})
    s.save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), FX.scene("reference"), "RING_PLAYGROUND")
    return s


def ring_ingest(s, img, extra=None, **kw):
    return s.ingest("hh_live", "hh_live_ring", img, "RING_PLAYGROUND", "RING_API_CAPTURE", extra=extra, **kw)


def live_incident(client):
    return client.get("/api/households/hh_live/incidents", headers=LIVE).json()[0]


def png(img):
    return cv2.imencode(".png", img)[1].tobytes()


# ------------------------------------------------------------------ P0-1 provenance
def test_replay_cannot_close_live_incident(client):
    s = live_setup()
    ring_ingest(s, FX.scene("initial"))
    inc = live_incident(client)
    assert inc["namespace"] == "LIVE" and inc["state"] == "ASSESSED"
    # API: uploads can never be attached to the Ring camera, whatever timestamp they claim
    r = client.post("/api/households/hh_live/observations/upload", headers=LIVE,
                    files={"file": ("replay.png", png(FX.scene("moved_to_zone", seed=99)), "image/png")},
                    data={"camera_id": "hh_live_ring", "source": "RECORDED_REPLAY", "captured_at": str(time.time() + 99999)})
    assert r.status_code == 403
    # Service layer refuses it too (defence in depth)
    with pytest.raises(PermissionError):
        s.ingest("hh_live", "hh_live_ring", FX.scene("moved_to_zone", seed=98), "RECORDED_REPLAY", "USER_UPLOAD", time.time() + 99999)
    assert live_incident(client)["state"] == "ASSESSED"


def test_state_machine_rejects_untrusted_evidence_for_live_incident():
    inc = {"state": "AWAITING_NEW_EVIDENCE", "namespace": "LIVE", "latest_captured_at": 100.0, "last_metric": 60.0, "clear_streak": 0}
    clear = {"classification": "NO_OBSTRUCTION_OBSERVED", "objects": [], "quality": {"usable": True}, "calibration_check": {"valid": True}}
    for obs in ({"captured_at": 1e12, "frame_sha256": "x", "trust": "DEMO", "chronology": "CLIENT_ASSERTED", "media_source": "RECORDED_REPLAY"},
                {"captured_at": 1e12, "frame_sha256": "y", "trust": "LIVE", "chronology": "CLIENT_ASSERTED", "media_source": "RING_PLAYGROUND"}):
        v = SM.verify_new_evidence(inc, obs, clear, set(), close_after=1)
        assert not v.accepted and v.disposition == "UNTRUSTED_SOURCE"


def test_live_capture_time_is_server_clock(client):
    s = live_setup()
    o = ring_ingest(s, FX.scene("initial"), captured_at=time.time() + 10 ** 6)  # fabricated future time is ignored
    assert abs(o["captured_at"] - time.time()) < 30
    assert o["chronology"] == "SERVER_CAPTURED" and o["trust"] == "LIVE"
    assert o["asserted_captured_at"] > time.time() + 1000  # retained only as an audit record


def test_live_closure_requires_two_independent_clear_observations(client, monkeypatch):
    monkeypatch.setattr(settings, "live_min_clear_separation_s", 4.0)  # > time for two analyses on a slow CPU
    s = live_setup()
    ring_ingest(s, FX.scene("initial"), extra={"capture_session": "whep-A"})
    ring_ingest(s, FX.scene("moved_to_zone", seed=11), extra={"capture_session": "whep-B"})
    inc = live_incident(client)
    assert inc["state"] != "RESOLVED" and inc["clear_streak"] == 1 and inc["last_clear_session"] == "whep-B"
    # a different frame from the SAME capture session is not an independent observation
    ring_ingest(s, FX.scene("moved_to_zone", seed=12), extra={"capture_session": "whep-B"})
    inc = live_incident(client)
    assert inc["state"] != "RESOLVED" and inc["clear_streak"] == 1
    # a new session too soon after the previous clear frame does not count either
    ring_ingest(s, FX.scene("moved_to_zone", seed=13), extra={"capture_session": "whep-C"})
    inc = live_incident(client)
    assert inc["state"] != "RESOLVED" and inc["clear_streak"] == 1
    detail = client.get(f"/api/households/hh_live/incidents/{inc['id']}", headers=LIVE).json()
    assert any("not independent" in r for w in detail["why"] for r in w["reasons"])
    time.sleep(4.1)
    ring_ingest(s, FX.scene("moved_to_zone", seed=14), extra={"capture_session": "whep-D"})
    inc = live_incident(client)
    assert inc["state"] == "RESOLVED" and inc["resolution"] == "VERIFIED_NO_OBSTRUCTION_OBSERVED"


def test_independence_rule_does_not_apply_to_demo_incidents():
    inc = {"state": "AWAITING_NEW_EVIDENCE", "namespace": "DEMO", "latest_captured_at": 100.0, "clear_streak": 1,
           "last_clear_session": "s1", "last_clear_at": 100.0, "last_metric": 60.0}
    obs = {"captured_at": 101.0, "frame_sha256": "z", "capture_session": "s1", "trust": "DEMO"}
    clear = {"classification": "NO_OBSTRUCTION_OBSERVED", "objects": [], "quality": {"usable": True}, "calibration_check": {"valid": True}}
    v = SM.verify_new_evidence(inc, obs, clear, set(), close_after=2, min_clear_separation_s=60)
    assert v.next_state == "RESOLVED"


def test_calibration_change_requires_human_review(client):
    s = live_setup()
    ring_ingest(s, FX.scene("initial"))
    s.save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), None, "unchanged")  # new version
    ring_ingest(s, FX.scene("moved_to_zone", seed=21))
    inc = live_incident(client)
    assert inc["state"] == "REVIEW_REQUIRED"
    detail = client.get(f"/api/households/hh_live/incidents/{inc['id']}", headers=LIVE).json()
    assert any("calibration changed" in r for w in detail["why"] for r in w["reasons"])


def test_demo_incidents_still_work_with_replays(client):
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    inc = client.get("/api/households/hh_b/incidents", headers=H()).json()[0]
    assert inc["namespace"] == "DEMO"
    r = client.post("/api/households/hh_b/observations/upload", headers=H(),
                    files={"file": ("r.png", png(FX.scene("moved_to_zone", seed=5)), "image/png")},
                    data={"camera_id": "hh_b_synthcam", "source": "RECORDED_REPLAY"})
    assert r.status_code == 200 and r.json()["trust"] == "DEMO"
    assert client.get("/api/households/hh_b/incidents", headers=H()).json()[0]["state"] == "RESOLVED"


# ------------------------------------------------------------------ P0-2 authenticated approvers
def test_approval_impersonation_blocked(client):
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    inc = client.get("/api/households/hh_b/incidents", headers=H()).json()[0]
    a = client.post(f"/api/households/hh_b/incidents/{inc['id']}/assistance/draft", headers=H()).json()
    url = f"/api/households/hh_b/actions/{a['id']}/approve"
    # claiming to be the resident while authenticated as the guest
    r = client.post(url, json={"member_id": "hh_b_resident"}, headers=H("hh_b", "hh_b_guest"))
    assert r.status_code == 403 and "does not match" in r.json()["detail"]
    # claiming to be the resident with no member credential at all
    assert client.post(url, json={"member_id": "hh_b_resident"}, headers=H()).status_code == 403
    # helper role cannot approve
    assert client.post(url, headers=H("hh_b", "hh_b_helper")).status_code == 403
    # forged token
    assert client.post(url, headers={**H(), "X-Member-Token": "demo-hh_b_resident-token-x"}).status_code == 403
    assert client.get(f"/api/households/hh_b/incidents/{inc['id']}", headers=H()).json()["state"] == "ASSESSED"
    ok = client.post(url, json={"member_id": "hh_b_resident"}, headers=H("hh_b", "hh_b_resident"))
    assert ok.status_code == 200 and ok.json()["approved_by"] == "hh_b_resident"


def test_camera_authorisation_requires_authenticated_approver(client):
    live_setup()
    STATE["svc"].db.update("cameras", "id", "hh_live_ring", {"authorized": 0})
    url = "/api/households/hh_live/cameras/hh_live_ring/authorize"
    assert client.post(url, headers=LIVE).status_code == 403
    assert client.post(url, headers={**LIVE, "X-Member-Token": "live-helper-token"}).status_code == 403
    assert client.post(url, headers=LIVE_APPROVER).status_code == 200


# ------------------------------------------------------------------ P0-3 demo / live separation
def test_live_household_never_exposed_by_demo_endpoint(client):
    live_setup()
    demo = client.get("/api/demo/households").json()
    assert {h["id"] for h in demo} == {"hh_a", "hh_b"}
    assert "live-hh-token" not in str(demo)
    assert client.get("/api/households/hh_live", headers=H()).status_code == 401  # demo token does not open live data
    assert client.get("/api/households/hh_b", headers=LIVE).status_code == 401


def test_demo_households_cannot_reach_ring(client):
    assert client.get("/api/households/hh_b/ring/devices", headers=H()).status_code == 403
    assert client.post("/api/households/hh_b/ring/hh_b_synthcam/capture", headers=H()).status_code == 403
    assert client.post("/api/households/hh_b/cameras", json={"name": "x", "kind": "ring"}, headers=H()).status_code == 422


def test_live_household_ring_endpoints_pass_auth(client):
    live_setup()
    r = client.get("/api/households/hh_live/ring/devices", headers=LIVE)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]  # authorised; Ring itself unconfigured in CI
    assert client.get("/api/households/hh_live/ring/sessions", headers=LIVE).json() == []


def test_simulator_cannot_target_live_household(client):
    live_setup()
    assert client.post("/api/households/hh_live/simulator/emit", json={"scene": "initial"}, headers=LIVE).status_code == 403


def test_media_urls_are_signed_and_tokenless(client):
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    o = client.get("/api/households/hh_b/observations", headers=H()).json()[0]
    assert "token" not in o["frame_url"] and "sig=" in o["frame_url"]
    assert client.get(o["frame_url"]).status_code == 200  # works without any credential, until it expires
    base = o["frame_url"].split("?")[0]
    assert client.get(base).status_code == 403
    assert client.get(base + "?token=demo-hh_b-token").status_code == 403  # tokens in URLs are no longer accepted
    assert client.get(o["frame_url"].replace("sig=", "sig=0")).status_code == 403
    exp, sig = STATE["svc"].sign(base, ttl=-5)
    assert client.get(f"{base}?exp={exp}&sig={sig}").status_code == 403  # expired
    # a valid signature for hh_b's path does not open hh_a's media
    other = base.replace("hh_b", "hh_a")
    assert client.get(other + "?" + o["frame_url"].split("?")[1]).status_code == 403


def test_household_token_only_in_header(client):
    assert client.get("/api/households/hh_b?token=demo-hh_b-token").status_code == 401


def test_event_stream_requires_ticket(client):
    assert client.get("/api/households/hh_b/stream").status_code == 401
    assert client.get("/api/households/hh_b/stream?token=demo-hh_b-token").status_code == 401
    t = client.post("/api/households/hh_b/stream-ticket", headers=H()).json()
    assert "sig=" in t["url"] and "token" not in t["url"]
    exp = int(t["url"].split("exp=")[1].split("&")[0])
    sig = t["url"].split("sig=")[1]
    assert STATE["svc"].verify_sig("stream:hh_b", exp, sig)
    assert not STATE["svc"].verify_sig("stream:hh_a", exp, sig)


# ------------------------------------------------------------------ P0-4 isolation
def test_retention_purge_is_household_scoped(client):
    client.post("/api/households/hh_a/simulator/emit", json={"scene": "centre_block"}, headers=H("hh_a"))
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    oa = client.get("/api/households/hh_a/observations", headers=H("hh_a")).json()[0]
    ob = client.get("/api/households/hh_b/observations", headers=H()).json()[0]
    assert client.post("/api/households/hh_b/retention/purge?days=0", headers=H("hh_b", "hh_b_resident")).json()["purged"] == 1
    assert client.get(oa["frame_url"]).status_code == 200, "household A's evidence must survive household B's purge"
    assert client.get(ob["frame_url"]).status_code == 404


def test_manual_purge_requires_approving_member(client):
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    ob = client.get("/api/households/hh_b/observations", headers=H()).json()[0]
    # household token alone: refused and ledgered, nothing deleted
    r = client.post("/api/households/hh_b/retention/purge?days=0", headers=H())
    assert r.status_code == 403
    # a non-approving member (helper): refused
    assert client.post("/api/households/hh_b/retention/purge?days=0", headers=H("hh_b", "hh_b_helper")).status_code == 403
    # another household's approver cannot purge this household
    assert client.post("/api/households/hh_b/retention/purge?days=0", headers=H("hh_b", "hh_a_resident")).status_code in (401, 403)
    assert client.get(ob["frame_url"]).status_code == 200
    led = client.get("/api/households/hh_b/ledger", headers=H()).json()
    rows = led["entries"] if isinstance(led, dict) else led
    kinds = [e["kind"] for e in rows]
    assert "AUTHORIZATION_DENIED" in kinds and "MEDIA_PURGED" not in kinds
    # approver: allowed; ledger records the request and each purged item
    r = client.post("/api/households/hh_b/retention/purge?days=0", headers=H("hh_b", "hh_b_resident"))
    assert r.status_code == 200 and r.json()["purged"] == 1 and r.json()["by"] == "hh_b_resident"
    assert client.get(ob["frame_url"]).status_code == 404
    led = client.get("/api/households/hh_b/ledger", headers=H()).json()
    rows = led["entries"] if isinstance(led, dict) else led
    kinds = [e["kind"] for e in rows]
    assert kinds.count("MEDIA_PURGED") == 1 and "RETENTION_PURGE_REQUESTED" in kinds
    assert client.get("/api/households/hh_b/ledger/verify", headers=H()).json()["intact"]


def test_automatic_retention_job_still_runs_without_member():
    """The server-wide retention job (startup maintenance) is not an API call and needs no member."""
    import tempfile
    from pathlib import Path
    from app.config import settings
    from app.db import DB
    from app.service import Service
    import app.fixtures as FX
    d = Path(tempfile.mkdtemp(prefix="cw-ret-"))
    s = Service(DB(d / "clearway.db"), d)
    s.seed_demo()
    s.ingest("hh_b", "hh_b_synthcam", FX.scene("initial"), "SYNTHETIC_RENDER", "UPLOAD")
    s.ingest("hh_a", "hh_a_synthcam", FX.scene("initial"), "SYNTHETIC_RENDER", "UPLOAD")
    assert s.purge_old(0) == 2  # all households, as the startup job does


def test_delete_household_removes_everything_scoped(client):
    import os
    s = STATE["svc"]
    client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial"}, headers=H())
    client.post("/api/households/hh_a/simulator/emit", json={"scene": "initial"}, headers=H("hh_a"))
    refs_b = [r["reference_path"] for r in s.db.all("SELECT reference_path FROM calibrations WHERE household_id='hh_b'")]
    refs_a = [r["reference_path"] for r in s.db.all("SELECT reference_path FROM calibrations WHERE household_id='hh_a'")]
    assert set(refs_a).isdisjoint(refs_b)
    out = client.delete("/api/households/hh_b/data", headers=H("hh_b", "hh_b_resident")).json()
    assert out["deleted"] and out["reference_files"] >= 1
    assert not any(os.path.exists(p) for p in refs_b)
    assert all(os.path.exists(p) for p in refs_a)
    for t in ("households", "members", "cameras", "calibrations", "observations", "incidents", "ledger", "events", "graph_edges"):
        col = "id" if t == "households" else "household_id"
        assert s.db.one(f"SELECT COUNT(*) n FROM {t} WHERE {col}='hh_b'")["n"] == 0, t
    assert not (settings.data_dir / "frames" / "hh_b").exists()
    assert client.get("/api/households/hh_a/observations", headers=H("hh_a")).status_code == 200


def test_upload_limits(client, monkeypatch):
    url, data = "/api/households/hh_b/observations/upload", {"camera_id": "hh_b_synthcam", "source": "INDEPENDENT_TEST_VIDEO"}
    monkeypatch.setattr(settings, "max_upload_mb", 0.01)
    r = client.post(url, headers=H(), files={"file": ("x.png", png(FX.scene("initial")), "image/png")}, data=data)
    assert r.status_code == 413
    monkeypatch.setattr(settings, "max_upload_mb", 25)
    tiny = np.zeros((32, 32, 3), np.uint8)
    assert client.post(url, headers=H(), files={"file": ("t.png", png(tiny), "image/png")}, data=data).status_code == 422
    big = np.zeros((4200, 4200, 3), np.uint8)  # 17.6 MP > 16.7 MP decoder limit
    assert client.post(url, headers=H(), files={"file": ("b.png", png(big), "image/png")}, data=data).status_code == 422
    assert client.post(url, headers=H(), files={"file": ("x.exe", b"MZ" + b"\0" * 100, "application/octet-stream")}, data=data).status_code == 422


def test_reference_upload_provenance(client):
    f = {"file": ("r.png", png(FX.scene("reference")), "image/png")}
    r = client.post("/api/households/hh_b/cameras/hh_b_synthcam/reference", headers=H(), files=f, data={"source": "RING_PLAYGROUND"})
    assert r.status_code == 422
    live_setup()
    r = client.post("/api/households/hh_live/cameras/hh_live_ring/reference", headers=LIVE, files=f, data={"source": "INDEPENDENT_TEST_VIDEO"})
    assert r.status_code == 403


# ------------------------------------------------------------------ P0-5 event reliability
def test_failed_event_is_retried_then_completed(client, monkeypatch):
    import app.main as M
    real, calls = M.FX.scene, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient renderer failure")
        return real(*a, **k)
    monkeypatch.setattr(M.FX, "scene", flaky)
    r = client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "flaky-1"}, headers=H()).json()
    assert r["completed"] is True and r["observation"]
    ev = client.get("/api/households/hh_b/events", headers=H()).json()[0]
    assert ev["status"] == "COMPLETED" and ev["attempts"] == 2


def test_permanently_failed_event_can_be_redelivered(client, monkeypatch):
    import app.main as M
    real = M.FX.scene
    monkeypatch.setattr(M.FX, "scene", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("renderer down")))
    r = client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "dead-1"}, headers=H()).json()
    assert r["completed"] is False
    ev = client.get("/api/households/hh_b/events", headers=H()).json()[0]
    assert ev["status"] == "FAILED" and ev["attempts"] == settings.event_max_attempts and "renderer down" in ev["last_error"]
    assert any(e["kind"] == "EVENT_PROCESSING_FAILED" for e in client.get("/api/households/hh_b/ledger", headers=H()).json())
    monkeypatch.setattr(M.FX, "scene", real)
    again = client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "dead-1"}, headers=H()).json()
    assert again["retry"] is True and again["completed"] is True
    third = client.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "dead-1"}, headers=H()).json()
    assert third.get("duplicate") is True  # completed events stay idempotent


# ------------------------------------------------------------------ P1 vision integrity
def test_unexplained_route_change_is_not_clear(ref):
    from app.vision.pipeline import analyze
    img = FX.scene("reference").copy()
    for y in range(260, 600, 40):  # chalk-like marks: real change on the route, too small/fragmented to be an object
        cv2.line(img, (430, y), (470, y), (240, 240, 240), 2)
    res, _ = analyze(img, FX.profile_for("hh_b"), ref)
    assert res["classification"] == "REVIEW_REQUIRED"
    assert res["detection_gate"]["route_residual_change"] > 0
    assert res["verification_guidance"]


def test_no_reference_fallback_is_advisory_only():
    from app.vision.pipeline import analyze
    for name in ("reference", "centre_block"):
        res, _ = analyze(FX.scene(name), FX.profile_for("hh_b"), None)
        assert res["classification"] == "REVIEW_REQUIRED", name


def test_low_confidence_object_near_route_cannot_be_cleared(ref):
    from app.vision.segmentation import change_detect
    from app.vision.uncertainty import evaluate_object
    from app.vision.calibration import floor_homography
    img = FX.scene("initial")
    inst = change_detect(img, ref)[0][0]
    prof = FX.profile_for("hh_a")  # 60 cm need: this object leaves ~65 cm, i.e. 'clear' at normal confidence
    hom = floor_homography(prof["floor_points_img"], prof["floor_points_world"], 960, 720)
    ctx = {"w": 960, "h": 720, "route": prof["route"], "homography": hom, "clearance_cm": 60.0,
           "floor_points_img": prof["floor_points_img"], "floor_points_world": prof["floor_points_world"]}
    assert evaluate_object(inst, ctx, n=40)["classification"] == "NO_OBSTRUCTION_OBSERVED"
    inst.confidence = 0.1
    ev = evaluate_object(inst, ctx, n=40)
    assert ev["classification"] == "REVIEW_REQUIRED" and any("segmentation confidence" in r for r in ev["reasons"])


def test_guidance_never_dispatches_or_declares_safe(ref):
    from app.vision.pipeline import analyze
    res, _ = analyze(FX.scene("initial", blur_sigma=7), FX.profile_for("hh_b"), ref)
    g = " ".join(res["verification_guidance"]).lower()
    assert res["classification"] == "UNABLE_TO_VERIFY" and "lens" in g
    assert "safe" not in g and "dispatch" not in g
