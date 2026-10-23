"""End-to-end: ClearWay's live Ring code path against the local Ring API emulator, both as REAL HTTP servers.
Events arrive as signed webhooks from the emulator; frames are captured over WHEP/WebRTC. No direct injection.
(The emulator is not Ring; these tests prove ClearWay's handling of the documented contract.)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("aiortc", reason="needs aiortc (pip install -r requirements-dev.txt)")
import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

HH = "hh_live"
LIVE = {"X-Household-Token": "emu-live-hh"}
APPROVER = {**LIVE, "X-Member-Token": "emu-live-approver"}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(app, port):
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            return srv, th
        time.sleep(0.05)
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def stack():
    import ringemu.server as E
    import app.main as M
    mp = pytest.MonkeyPatch()
    cw_port, emu_port = free_port(), free_port()
    data = Path(tempfile.mkdtemp(prefix="cw-emu-e2e-"))
    E.S = E.State()
    mp.setattr(E, "WEBHOOK_URL", f"http://127.0.0.1:{cw_port}/webhooks/ring")
    mp.setattr(E, "WEBHOOK_SECRET", "e2e-webhook-secret")
    mp.setattr(E, "CONTROL_TOKEN", "e2e-control")
    base = f"http://127.0.0.1:{emu_port}"
    for k, v in {"data_dir": data, "demo_mode": True, "ring_api_base": base, "ring_oauth_url": base + "/oauth/token",
                 "ring_client_id": E.CLIENT_ID, "ring_client_secret": E.CLIENT_SECRET, "ring_access_token": None,
                 "ring_refresh_token": E.INITIAL_REFRESH, "ring_device_id": E.DEVICE_ID, "ring_webhook_secret": "e2e-webhook-secret",
                 "ring_emulator_control_token": "e2e-control", "ring_source_label": "RING_PLAYGROUND",  # must be overridden
                 "live_household_token": "emu-live-hh", "live_approver_token": "emu-live-approver",
                 "live_min_clear_separation_s": 3.0}.items():
        mp.setattr(settings, k, v)
    emu, emu_th = serve(E.app, emu_port)
    cw, cw_th = serve(M.app, cw_port)
    c = httpx.Client(base_url=f"http://127.0.0.1:{cw_port}", timeout=120, trust_env=False)
    yield {"c": c, "E": E, "M": M, "emu": httpx.Client(base_url=base, timeout=30, trust_env=False), "base": base, "cw_port": cw_port}
    cw.should_exit = emu.should_exit = True
    cw_th.join(10)
    emu_th.join(10)
    mp.undo()


def hh(p):
    return f"/api/households/{HH}{p}"


def wait(fn, pred, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if pred(v):
            return v
        time.sleep(0.5)
    raise AssertionError(f"timeout; last={v}")


def n_obs(c):
    return len(c.get(hh("/observations?limit=200"), headers=LIVE).json())


def fire(c, scene):
    n = n_obs(c)
    r = c.post(hh("/ring/emulator/scene"), headers=LIVE, json={"scene": scene})
    assert r.status_code == 200 and r.json()["event"]["status"] == 200, r.text  # webhook accepted by ClearWay
    wait(lambda: n_obs(c), lambda x: x > n)
    return c.get(hh("/observations?limit=1"), headers=LIVE).json()[0]


def incident(c):
    return c.get(hh("/incidents"), headers=LIVE).json()[0]


def test_label_guard_and_status(stack):
    st = stack["c"].get("/api/ring/status").json()
    assert st["emulator"] is True and st["source_label"] == "RING_API_EMULATOR" and st["verified_against_real_ring"] is False
    h = stack["c"].get(hh(""), headers=LIVE).json()
    assert "emulator" in h["name"].lower() and "emulator" in h["cameras"][0]["name"].lower()


def test_full_live_path_via_webhooks_and_whep(stack):
    c = stack["c"]
    devs = c.get(hh("/ring/devices"), headers=LIVE).json()
    assert devs[0]["id"] == stack["E"].DEVICE_ID
    assert c.post(hh(f"/cameras/{HH}_ring/authorize"), headers=APPROVER).status_code == 200
    c.post(hh("/ring/emulator/scene"), headers=LIVE, json={"scene": "reference", "fire_event": False})
    assert c.post(hh(f"/ring/{HH}_ring/reference"), headers=LIVE).status_code == 200
    from app.fixtures import profile_for
    prof = profile_for("hh_b")
    assert c.put(hh(f"/cameras/{HH}_ring/calibration"), headers=LIVE,
                 json={k: prof[k] for k in ("route", "drop_zone", "clearance_cm", "floor_points_img", "floor_points_world")}).status_code == 200

    o = fire(c, "initial")
    assert (o["media_source"], o["trust"], o["chronology"], o["event_source"]) == ("RING_API_EMULATOR", "LIVE", "SERVER_CAPTURED", "RING_WEBHOOK")
    w = o["analysis"]["provenance"]["ring"]["whep_session"]
    assert w["state"] == "COMPLETED" and w["frames_received"] > 10 and any("DELETEd" in x["msg"] for x in w["log"])
    assert o["classification"] == "POTENTIAL_OBSTRUCTION"
    inc = incident(c)
    assert inc["namespace"] == "LIVE" and inc["state"] == "ASSESSED"

    act = c.post(hh(f"/incidents/{inc['id']}/assistance/draft"), headers=LIVE).json()
    assert c.post(hh(f"/actions/{act['id']}/approve"), headers=APPROVER).status_code == 200
    assert c.post(hh(f"/actions/{act['id']}/send"), headers=LIVE).status_code == 200

    assert fire(c, "partial_move")["classification"] == "POTENTIAL_OBSTRUCTION"
    assert incident(c)["state"] == "PARTIALLY_RESOLVED"
    assert fire(c, "moved_to_zone")["classification"] == "NO_OBSTRUCTION_OBSERVED"
    assert incident(c)["state"] == "AWAITING_NEW_EVIDENCE" and incident(c)["clear_streak"] == 1
    time.sleep(3.2)  # independence: separate WHEP session, >= separation
    o = c.post(hh(f"/ring/{HH}_ring/capture"), headers=LIVE).json()
    assert o["classification"] == "NO_OBSTRUCTION_OBSERVED"
    inc = incident(c)
    assert inc["state"] == "RESOLVED" and inc["resolution"] == "VERIFIED_NO_OBSTRUCTION_OBSERVED"
    # provenance axes: closed on EMULATOR captures, never presented as genuine Ring verification
    assert inc["evidence_origin"] == "RING_API_EMULATOR" and inc["verification_basis"] == "EMULATOR_CAPTURE"
    assert o["evidence_origin"] == "RING_API_EMULATOR" and o["capture_method"] == "WHEP"
    # the emulator's webhooks are parsed as documented v1.1 and each produced a linked observation
    evs = [e for e in c.get(hh("/events"), headers=LIVE).json() if e["source"] == "ring"]
    assert len(evs) == 3 and all(e["format"] == "v1.1" and e["status"] == "COMPLETED" and e["disposition"] == "ANALYSED"
                                 and e["device_id"] == stack["E"].DEVICE_ID and e["request_id"] and e["observation_id"] for e in evs)
    assert c.get(hh("/ledger/verify"), headers=LIVE).json()["intact"]
    # every observation is labelled as emulated; nothing claims to be Ring footage
    assert {x["media_source"] for x in c.get(hh("/observations?limit=200"), headers=LIVE).json()} == {"RING_API_EMULATOR"}
    # emulator side: every session closed by ClearWay's DELETE, never left to time out
    st = stack["emu"].get("/emulator/state", headers={"X-Emulator-Token": "e2e-control"}).json()
    assert st["sessions"] and all(s["reason"] == "DELETE by client" for s in st["sessions"])
    reqs = st["recent_requests"]
    assert all(r["ua"] and r["ua"].startswith("ClearWayAccessGraph/") and r["hw"] for r in reqs if r["path"].startswith("/v1/"))


def test_emulator_webhook_is_exact_documented_v11(stack):
    """The emulator must not carry anything a genuine Ring v1.1 event would not (no attributes.device_id)."""
    import hashlib, hmac, json as J
    E = stack["E"]
    captured = {}

    class Rec:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content=None, headers=None):
            captured.update(body=content, headers=headers)
            return httpx.Response(200, text="{}")
    import ringemu.server as S
    orig = S.httpx.AsyncClient
    S.httpx.AsyncClient = Rec
    try:
        r = stack["emu"].post("/emulator/event", headers={"X-Emulator-Token": "e2e-control"}, json={"sub_type": "motion"})
        assert r.status_code == 200, r.text
    finally:
        S.httpx.AsyncClient = orig
    j = J.loads(captured["body"])
    assert set(j) == {"meta", "data"} and j["meta"]["version"] == "1.1" and j["meta"]["request_id"] and j["meta"]["account_id"]
    a = j["data"]["attributes"]
    assert "device_id" not in a and a["source"] == E.DEVICE_ID and a["source_type"] == "devices"
    assert isinstance(a["timestamp"], int) and a["timestamp"] > 1e12 and a["component_ids"] == ["0"]
    assert j["data"]["type"] == "motion_detected" and j["data"]["id"].startswith(f"{E.DEVICE_ID}_motion_")
    assert "RING_API_EMULATOR" not in captured["body"].decode()
    assert captured["headers"]["X-Signature"] == "sha256=" + hmac.new(E.WEBHOOK_SECRET.encode(), captured["body"], hashlib.sha256).hexdigest()


def test_expired_access_token_is_refreshed(stack, monkeypatch):
    E, c = stack["E"], stack["c"]
    for t in list(E.S.access_tokens):
        E.S.access_tokens[t] = time.time() - 1  # every issued token has expired
    before = sum(r["path"] == "/oauth/token" for r in E.S.requests)
    assert c.get(hh("/ring/devices"), headers=LIVE).status_code == 200
    assert sum(r["path"] == "/oauth/token" for r in E.S.requests) == before + 1


def test_device_offline_is_reported(stack):
    c = stack["c"]
    c.post(hh("/ring/emulator/online"), headers=LIVE, json={"online": False})
    try:
        r = c.post(hh(f"/ring/{HH}_ring/capture"), headers=LIVE)
        assert r.status_code == 502
        detail = r.json()["detail"]["detail"]
        assert detail["whep_error"]["status"] == 503 and "offline" in detail["whep_error"]["message"]
    finally:
        c.post(hh("/ring/emulator/online"), headers=LIVE, json={"online": True})


def test_emulator_enforces_documented_contract(stack):
    e, E = stack["emu"], stack["E"]
    tok = E.S.issue()
    url = f"/v1/devices/{E.DEVICE_ID}/media/streaming/whep/sessions"
    r = e.post(url, content=b"v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/sdp"})
    assert r.status_code == 406  # missing User-Agent / Hardware_Id
    good = {"Authorization": f"Bearer {tok}", "User-Agent": "Test/1", "Hardware_Id": "x", "Content-Type": "application/sdp"}
    assert e.post(url, content=b"v=0\r\nm=audio 9 UDP 0\r\nm=video 9 UDP 96\r\n", headers=good).status_code == 400
    assert e.post(url, content=b"v=0\r\nm=video 9 UDP 96\r\n", headers={**good, "Content-Type": "text/plain"}).status_code == 415
    assert e.get("/v1/devices", headers={**good, "Authorization": "Bearer nope"}).status_code == 401
    assert e.get("/v1/devices/other", headers=good).status_code == 404
    assert e.get("/emulator/state").status_code == 401  # control API needs its own token


def test_demo_household_cannot_drive_emulator(stack):
    r = stack["c"].post("/api/households/hh_b/ring/emulator/scene", headers={"X-Household-Token": "demo-hh_b-token"}, json={"scene": "initial"})
    assert r.status_code == 403


def test_deleting_live_household_unlinks_at_emulator(stack):
    c, E = stack["c"], stack["E"]
    r = c.delete(hh("/data"), headers=APPROVER).json()
    assert r["ring_disconnect"]["stored_tokens_removed"] in (True, False)
    assert "204" in r["ring_disconnect"]["unlink"]
    assert E.S.linked is False  # the emulator saw DELETE /v1/accounts/me/app-integrations
    assert stack["M"].STATE["ring"] is None
