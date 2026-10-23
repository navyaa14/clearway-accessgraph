"""Audit #3 P0: official Ring webhook v1.1 payloads must reach an observation.

HTTP-to-observation integration tests: a documented v1.1 body (device in data.attributes.source with
source_type "devices", NO attributes.device_id) is signed with the Ring webhook key and POSTed to
/webhooks/ring. The test asserts the parsed event id, the device match, that a capture was invoked for the
registered + authorised camera, that an observation was created and linked to the event, and the explicit
non-success dispositions (missing identity, unregistered device, unauthorised camera, capture failure).

Ring itself is not contacted: only the capture function is replaced (it would open a WHEP session).
"""
import json
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import fixtures as FX
from app.config import settings
from app.ring import webhook as WH

SECRET = "test-ring-webhook-secret"
DEVICE = "dev-v11-1"
LIVE = {"X-Household-Token": "live-hh", "X-Member-Token": "live-appr"}

# Documented v1.1 sensor-event shape (fields as published; values are test values).
FIXTURE = {
    "meta": {"version": "1.1", "account_id": "acct-test-1", "request_id": "req-0001"},
    "data": {
        "id": f"{DEVICE}_motion_1767225600000",
        "type": "motion_detected",
        "attributes": {"source": DEVICE, "source_type": "devices", "timestamp": 1767225600000,
                       "sub_type": "motion", "component_ids": ["0"]},
        "relationships": {"devices": {"links": {"self": f"/v1/devices/{DEVICE}"},
                                      "data": [{"type": "devices", "id": DEVICE}]}},
    },
}


def body_of(mut=None) -> bytes:
    j = json.loads(json.dumps(FIXTURE))
    if mut:
        mut(j)
    return json.dumps(j).encode()


def post(c, body: bytes, secret=SECRET):
    return c.post("/webhooks/ring", content=body, headers={"Content-Type": "application/json", "X-Signature": WH.sign(secret, body)})


@pytest.fixture()
def live(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="cw-v11-"))
    monkeypatch.setattr(settings, "data_dir", d)
    monkeypatch.setattr(settings, "demo_mode", True)
    # Ring "configured" so the live household owns a Ring camera; no request ever reaches Ring (capture is faked).
    for k, v in {"ring_access_token": "test-access", "ring_refresh_token": "test-refresh", "ring_client_id": "cid",
                 "ring_client_secret": "sec", "ring_api_base": "https://api.amazonvision.com"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "ring_device_id", DEVICE)
    monkeypatch.setattr(settings, "ring_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "live_household_token", "live-hh")
    monkeypatch.setattr(settings, "live_approver_token", "live-appr")
    import app.main as M
    calls = []
    state = {"fail": False}

    async def fake_capture(device_id, hh, component_id=None):
        calls.append({"device_id": device_id, "hh": hh, "component_id": component_id})
        if state["fail"]:
            return None, {"device_id": device_id, "whep_error": {"status": 503, "message": "test outage"}}
        return FX.scene("initial"), {"device_id": device_id, "transport": "WHEP",
                                     "whep_session": {"id": f"whep-{len(calls)}", "state": "closed"}}
    monkeypatch.setattr(M, "ring_capture_frame", fake_capture)
    monkeypatch.setattr(settings, "event_max_attempts", 1)
    with TestClient(M.app) as c:
        yield c, M, calls, state


def authorise(c, M):
    r = c.post("/api/households/hh_live/cameras/hh_live_ring/authorize", headers=LIVE)
    assert r.status_code == 200, r.text
    M.svc().save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), FX.scene("reference"), "RING_PLAYGROUND")


def event_row(M, eid):
    return M.svc().db.one("SELECT * FROM events WHERE event_key=?", (f"ring:{eid}",))


def test_parse_of_documented_fixture_has_no_legacy_field():
    assert "device_id" not in FIXTURE["data"]["attributes"]
    e = WH.parse_event(body_of())
    assert e["device_id"] == DEVICE and e["event_id"] == FIXTURE["data"]["id"] and e["request_id"] == "req-0001"


def test_signed_v11_event_produces_linked_observation(live):
    c, M, calls, _ = live
    authorise(c, M)
    r = post(c, body_of())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["processing"] is True and j["disposition"] == "PROCESS"
    assert j["event_id"] == FIXTURE["data"]["id"] and j["device_id"] == DEVICE and j["format"] == "v1.1"
    # capture invoked for the registered device, with the component from the event
    assert calls == [{"device_id": DEVICE, "hh": "hh_live", "component_id": 0}]
    ev = event_row(M, FIXTURE["data"]["id"])
    assert ev["status"] == "COMPLETED" and ev["disposition"] == "ANALYSED"
    assert ev["request_id"] == "req-0001" and ev["device_id"] == DEVICE and ev["household_id"] == "hh_live"
    obs = M.svc().db.one("SELECT * FROM observations WHERE id=?", (ev["observation_id"],))
    assert obs is not None and obs["household_id"] == "hh_live" and obs["camera_id"] == "hh_live_ring"
    assert obs["event_id"] == FIXTURE["data"]["id"]
    assert obs["media_source"] == "RING_PLAYGROUND" and obs["trust"] == "LIVE" and obs["chronology"] == "SERVER_CAPTURED"
    assert obs["evidence_origin"] == "RING" and obs["capture_method"] == "WHEP"
    extra = json.loads(obs["analysis"])["provenance"]
    assert extra["ring_event"]["request_id"] == "req-0001" and extra["ring_event"]["device_id"] == DEVICE
    # visible through the household's events API
    rows = c.get("/api/households/hh_live/events", headers=LIVE).json()
    assert any(x["event_key"] == f"ring:{FIXTURE['data']['id']}" and x["observation_id"] == obs["id"] for x in rows)


def test_replay_is_idempotent_by_data_id_and_by_request_id(live):
    c, M, calls, _ = live
    authorise(c, M)
    assert post(c, body_of()).json()["processing"] is True
    again = post(c, body_of()).json()
    assert again["duplicate"] is True
    # same documented idempotency key, different event id (e.g. a re-serialised redelivery)
    redelivery = post(c, body_of(lambda j: j["data"].__setitem__("id", "other-id"))).json()
    assert redelivery["duplicate"] is True
    assert len(calls) == 1
    assert M.svc().db.one("SELECT COUNT(*) n FROM observations WHERE household_id='hh_live'")["n"] == 1


def test_missing_device_identity_is_rejected_not_completed(live):
    c, M, calls, _ = live
    authorise(c, M)

    def strip(j):
        j["data"]["attributes"].pop("source")
        j["meta"]["request_id"] = "req-nosrc"
        j["data"]["id"] = "no-source-1"
    j = post(c, body_of(strip)).json()
    assert j["processing"] is False and j["disposition"] == "REJECTED_NO_DEVICE_IDENTITY"
    ev = event_row(M, "no-source-1")
    assert ev["status"] == "REJECTED" and ev["status"] != "COMPLETED" and ev["observation_id"] is None
    assert calls == []


def test_legacy_device_id_field_is_not_trusted_for_v11(live):
    c, M, calls, _ = live
    authorise(c, M)

    def legacy(j):
        a = j["data"]["attributes"]
        a.pop("source"), a.pop("source_type")
        a["device_id"] = DEVICE
        j["meta"]["request_id"] = "req-legacy"
        j["data"]["id"] = "legacy-1"
    j = post(c, body_of(legacy)).json()
    assert j["disposition"] == "REJECTED_NO_DEVICE_IDENTITY" and calls == []


def test_unregistered_device_is_explicitly_ignored(live):
    c, M, calls, _ = live
    authorise(c, M)

    def other(j):
        j["data"]["attributes"]["source"] = "someone-elses-doorbell"
        j["meta"]["request_id"] = "req-unreg"
        j["data"]["id"] = "unreg-1"
    j = post(c, body_of(other)).json()
    assert j["processing"] is True  # identity present -> accepted for processing ...
    ev = event_row(M, "unreg-1")
    assert ev["status"] == "IGNORED" and ev["disposition"] == "IGNORED_UNREGISTERED_DEVICE"  # ... but not "analysed"
    assert ev["observation_id"] is None and calls == []


def test_registered_but_unauthorised_camera_is_explicitly_ignored(live):
    c, M, calls, _ = live
    j = post(c, body_of()).json()  # camera not authorised yet
    ev = event_row(M, FIXTURE["data"]["id"])
    assert ev["status"] == "IGNORED" and ev["disposition"] == "IGNORED_CAMERA_NOT_AUTHORIZED" and calls == []
    led = M.svc().db.all("SELECT kind FROM ledger WHERE household_id='hh_live' AND kind='EVENT_IGNORED'")
    assert led, "ignored events for a known household are ledgered"


def test_capture_failure_of_authorised_device_is_failed_not_completed(live):
    c, M, calls, state = live
    authorise(c, M)
    state["fail"] = True
    post(c, body_of())
    ev = event_row(M, FIXTURE["data"]["id"])
    assert ev["status"] == "FAILED" and ev["observation_id"] is None and "no readable media" in (ev["last_error"] or "")
    assert len(calls) == 1
    # a later successful attempt (e.g. the sweeper retry) completes it
    state["fail"] = False
    ok = c.portal.call(M.run_event, f"ring:{FIXTURE['data']['id']}", M.process_ring_event, WH.parse_event(body_of()), None, (0.0,))
    assert ok is True and event_row(M, FIXTURE["data"]["id"])["status"] == "COMPLETED"


def test_bad_signature_malformed_and_oversized_bodies(live):
    c, M, _, _ = live
    assert post(c, body_of(), secret="wrong").status_code == 401
    bad = b"{not json"
    assert post(c, bad).status_code == 400
    big = json.dumps({"meta": {"version": "1.1"}, "pad": "x" * (WH.MAX_BODY_BYTES + 10)}).encode()
    assert post(c, big).status_code == 413


def test_irrelevant_v11_type_is_filtered(live):
    c, M, calls, _ = live

    def online(j):
        j["data"]["type"] = "device_online"
        j["data"]["id"] = "online-1"
        j["meta"]["request_id"] = "req-online"
    j = post(c, body_of(online)).json()
    assert j["disposition"] == "FILTERED_IRRELEVANT_TYPE" and event_row(M, "online-1")["status"] == "FILTERED" and calls == []
