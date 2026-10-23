"""End-to-end API tests: signed events -> real pipeline -> incident workflow."""
import json
import time

from app.config import settings
from app.ring import webhook as WH

from .conftest import H


def emit(c, scene, hh="hh_b", **kw):
    r = c.post(f"/api/households/{hh}/simulator/emit", json={"scene": scene, **kw}, headers=H(hh))
    assert r.status_code == 200, r.text
    return r.json()


def incident(c, hh="hh_b"):
    lst = c.get(f"/api/households/{hh}/incidents", headers=H(hh)).json()
    return c.get(f"/api/households/{hh}/incidents/{lst[0]['id']}", headers=H(hh)).json() if lst else None


def approve_and_send(c, inc_id, approver="hh_b_resident"):
    a = c.post(f"/api/households/hh_b/incidents/{inc_id}/assistance/draft", headers=H()).json()
    assert c.post(f"/api/households/hh_b/actions/{a['id']}/approve", headers=H("hh_b", approver)).status_code == 200
    assert c.post(f"/api/households/hh_b/actions/{a['id']}/send", headers=H()).status_code == 200
    return a


def test_household_isolation(client):
    assert client.get("/api/households/hh_b").status_code == 401
    assert client.get("/api/households/hh_b", headers=H("hh_a")).status_code == 401
    assert client.get("/api/households/hh_b", headers=H("hh_b")).status_code == 200
    emit(client, "centre_block", "hh_b")
    obs_b = client.get("/api/households/hh_b/observations", headers=H()).json()[0]
    assert client.get(f"/api/households/hh_a/observations/{obs_b['id']}", headers=H("hh_a")).status_code == 404


def test_full_proof_of_clearance_flow(client):
    """Golden path from the brief: obstruction -> approval -> failed correction -> refusal -> verified clearance."""
    a = emit(client, "initial", "hh_a")
    assert a["observation"]["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert a["observation"]["disposition"] == "NO_INCIDENT"
    b = emit(client, "initial", "hh_b")
    assert b["observation"]["classification"] == "POTENTIAL_OBSTRUCTION"
    inc = incident(client)
    assert inc["state"] == "ASSESSED"
    approve_and_send(client, inc["id"])
    inc = incident(client)
    assert inc["state"] == "AWAITING_NEW_EVIDENCE"
    assert len(client.get("/api/households/hh_b/inbox", headers=H()).json()) == 1
    emit(client, "partial_move")
    inc = incident(client)
    assert inc["state"] == "PARTIALLY_RESOLVED", "partial correction must not close the incident"
    emit(client, "moved_to_zone")
    inc = incident(client)
    assert inc["state"] == "RESOLVED"
    assert inc["resolution"] == "VERIFIED_NO_OBSTRUCTION_OBSERVED"
    titles = [w["title"] for w in inc["why"]]
    assert "REASSESSED → PARTIALLY_RESOLVED" in titles and "REASSESSED → RESOLVED" in titles
    rels = {e["rel"] for e in inc["graph"]}
    assert {"OBSERVATION_SUPPORTS_CLAIM", "OBSERVATION_CONTRADICTS_CLAIM", "ACTION_REQUESTED_FOR_INCIDENT", "VERIFICATION_REASSESSES_INCIDENT"} <= rels
    assert client.get("/api/households/hh_b/ledger/verify", headers=H()).json()["intact"]


def test_identical_frame_cannot_close_incident(client):
    emit(client, "initial")
    inc = incident(client)
    approve_and_send(client, inc["id"])
    emit(client, "initial")  # same rendered content, later event
    inc = incident(client)
    assert inc["state"] != "RESOLVED"
    assert inc["observations"][-1]["disposition"] == "DUPLICATE"


# 10
def test_duplicate_event_ignored(client):
    r1 = emit(client, "centre_block", event_id="evt-dup-1")
    r2 = emit(client, "centre_block", event_id="evt-dup-1")
    assert r1["processing"] and r2.get("duplicate")
    assert len(client.get("/api/households/hh_b/observations", headers=H()).json()) == 1


# 11
def test_old_event_after_newer_observation(client):
    emit(client, "initial")
    inc = incident(client)
    approve_and_send(client, inc["id"])
    emit(client, "moved_to_zone", captured_offset_s=-3600)  # delayed: captured an hour ago
    inc = incident(client)
    assert inc["state"] == "AWAITING_NEW_EVIDENCE"
    stale = [o for o in inc["observations"] if o["disposition"] == "STALE"]
    assert len(stale) == 1 and stale[0]["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert any(e["kind"] == "EVIDENCE_REJECTED" for e in inc["ledger"])


# 12
def test_unauthorized_assistance_blocked(client):
    emit(client, "initial")
    inc = incident(client)
    a = client.post(f"/api/households/hh_b/incidents/{inc['id']}/assistance/draft", headers=H()).json()
    assert client.post(f"/api/households/hh_b/actions/{a['id']}/send", headers=H()).status_code == 403
    assert client.post(f"/api/households/hh_b/actions/{a['id']}/approve", headers=H("hh_b", "hh_b_guest")).status_code == 403
    # a resident of ANOTHER household is not a member here
    assert client.post(f"/api/households/hh_b/actions/{a['id']}/approve", headers={**H(), "X-Member-Token": "demo-hh_a_resident-token"}).status_code == 403
    assert client.get("/api/households/hh_b/inbox", headers=H()).json() == []
    kinds = [e["kind"] for e in client.get("/api/households/hh_b/ledger", headers=H()).json()]
    assert kinds.count("AUTHORIZATION_DENIED") == 3
    assert incident(client)["state"] == "ASSESSED"


def test_override_requires_reason_and_authority(client):
    emit(client, "initial")
    inc = incident(client)
    url = f"/api/households/hh_b/incidents/{inc['id']}/override"
    assert client.post(url, json={"reason": "moved it"}, headers=H("hh_b", "hh_b_helper")).status_code == 403
    assert client.post(url, json={"reason": ""}, headers=H("hh_b", "hh_b_resident")).status_code == 409
    r = client.post(url, json={"reason": "Parcel collected by me"}, headers=H("hh_b", "hh_b_resident"))
    assert r.status_code == 200 and r.json()["resolution"] == "MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED"


def test_unverifiable_recheck_keeps_incident_open(client):
    emit(client, "initial")
    inc = incident(client)
    approve_and_send(client, inc["id"])
    emit(client, "obscured")
    assert incident(client)["state"] == "UNABLE_TO_VERIFY"
    emit(client, "moved_to_zone")
    assert incident(client)["state"] == "RESOLVED"


# 15
def test_webhook_signatures(client):
    body = json.dumps({"id": "e1", "type": "motion_detected", "data": {"attributes": {"sub_type": "package_delivery", "clearway_household": "hh_b", "clearway_fixture": "reference"}}}).encode()
    assert client.post("/webhooks/simulator", content=body, headers={"X-Signature": "sha256=deadbeef"}).status_code == 401
    assert client.post("/webhooks/simulator", content=body).status_code == 401
    ok = client.post("/webhooks/simulator", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)})
    assert ok.status_code == 200 and ok.json()["processing"]
    # a simulator-signed body is NOT accepted on the Ring endpoint
    assert client.post("/webhooks/ring", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)}).status_code == 401


def test_stage0_filters_irrelevant_events(client):
    r = emit(client, "initial", type="device_online")
    assert r["processing"] is False
    assert client.get("/api/households/hh_b/observations", headers=H()).json() == []


def test_ledger_is_append_only(client):
    emit(client, "initial")
    db = __import__("app.main", fromlist=["STATE"]).STATE["svc"].db
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.DatabaseError):
        db.x("UPDATE ledger SET kind='X'")
    with pytest.raises(sqlite3.DatabaseError):
        db.x("DELETE FROM ledger")
    assert client.get("/api/households/hh_b/ledger/verify", headers=H()).json()["intact"]


def test_calibration_validation_and_versioning(client):
    cam = "hh_b_synthcam"
    url = f"/api/households/hh_b/cameras/{cam}/calibration"
    cur = client.get(url, headers=H()).json()["profile"]
    assert client.put(url, json={"route": [[0.1, 0.1], [0.2, 0.2]]}, headers=H()).status_code == 422
    assert client.put(url, json={**cur, "floor_points_world": [[0, 0]]}, headers=H()).status_code == 422
    r = client.put(url, json={k: cur[k] for k in ("route", "drop_zone", "clearance_cm", "floor_points_img", "floor_points_world")}, headers=H())
    assert r.status_code == 200 and r.json()["version"].endswith("v2")
    assert len(client.get(url, headers=H()).json()["history"]) == 2


def test_upload_cannot_claim_ring_source(client):
    import cv2
    from app.fixtures import scene
    ok, png = cv2.imencode(".png", scene("initial"))
    r = client.post("/api/households/hh_b/observations/upload", headers=H(),
                    files={"file": ("x.png", png.tobytes(), "image/png")}, data={"camera_id": "hh_b_synthcam", "source": "RING_PLAYGROUND"})
    assert r.status_code == 422
    r = client.post("/api/households/hh_b/observations/upload", headers=H(),
                    files={"file": ("x.png", png.tobytes(), "image/png")}, data={"camera_id": "hh_b_synthcam", "source": "INDEPENDENT_TEST_VIDEO"})
    assert r.status_code == 200 and r.json()["media_source"] == "INDEPENDENT_TEST_VIDEO"


def test_video_upload_samples_frames(client, tmp_path):
    import cv2
    from app.fixtures import scene
    p = tmp_path / "clip.mp4"
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 10, (960, 720))
    img = scene("centre_block")
    for _ in range(25):
        vw.write(img)
    vw.release()
    r = client.post("/api/households/hh_b/observations/upload", headers=H(),
                    files={"file": ("clip.mp4", p.read_bytes(), "video/mp4")}, data={"camera_id": "hh_b_synthcam", "source": "RECORDED_REPLAY"})
    assert r.status_code == 200, r.text
    meta = r.json()["analysis"]["provenance"]["upload"]
    assert meta["kind"] == "video" and meta["sampled_frames"] >= 2
    assert r.json()["classification"] == "POTENTIAL_OBSTRUCTION"


def test_bedrock_disabled_is_explicit(client):
    emit(client, "initial")
    oid = client.get("/api/households/hh_b/observations", headers=H()).json()[0]["id"]
    r = client.post(f"/api/households/hh_b/observations/{oid}/explain", headers=H()).json()
    assert r["status"] == "disabled"


def test_ring_endpoints_protected(client):
    assert client.get("/api/households/hh_b/ring/devices").status_code == 401
    r = client.get("/api/households/hh_b/ring/devices", headers=H())
    assert r.status_code == 403 and "demo households" in r.json()["detail"]
    st = client.get("/api/ring/status").json()
    assert st["configured"] is False and "device_id" not in st


def test_delete_household_data(client):
    emit(client, "initial")
    assert client.delete("/api/households/hh_b/data", headers=H()).status_code == 403  # needs an approving member
    assert client.delete("/api/households/hh_b/data", headers=H("hh_b", "hh_b_resident")).status_code == 200
    assert client.get("/api/households/hh_b/observations", headers=H()).status_code == 401  # household record + token removed
    assert client.get("/api/households/hh_a/ledger/verify", headers=H("hh_a")).json()["intact"]
