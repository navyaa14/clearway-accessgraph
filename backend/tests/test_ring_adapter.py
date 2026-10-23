"""Ring adapter tests against a local server implementing the documented WHEP/REST contract.
These verify protocol handling; they are NOT evidence of a genuine Ring session."""
import asyncio
import socket
import threading
import time

import pytest

pytest.importorskip("aiortc", reason="Ring WHEP tests need aiortc (pip install -r requirements-dev.txt)")
import uvicorn  # noqa: E402

from app.config import Settings
from app.fixtures import scene
from app.ring import webhook as WH
from app.ring.client import RingClient, RingError
from app.ring.whep import capture_frames
from tests.whep_test_server import STATE, VALID_TOKEN, make_app


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server():
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(make_app([scene("initial")]), port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True


def settings_for(base, token=VALID_TOKEN, refresh=None):
    s = Settings()
    s.ring_api_base = base
    s.ring_oauth_url = base + "/oauth/token"
    s.ring_access_token = token
    s.ring_refresh_token = refresh
    s.ring_client_id, s.ring_client_secret = "cid", "secret"
    return s


def test_required_headers_and_devices(server):
    c = RingClient(settings_for(server))
    devs = asyncio.run(c.devices())
    assert devs[0]["id"] == "dev-test-1"
    h = STATE["requests"][-1]["headers"]
    assert h["authorization"] == f"Bearer {VALID_TOKEN}"
    assert h["user-agent"].startswith("ClearWayAccessGraph/")
    assert "hardware_id" in h


def test_whep_session_lifecycle(server):
    c = RingClient(settings_for(server))
    frames, info = asyncio.run(capture_frames(c, "dev-test-1", max_seconds=4, max_frames=3))
    assert len(frames) == 3 and frames[0].shape == (720, 960, 3)
    assert info.state == "COMPLETED" and info.frames_received >= 3
    assert info.location and info.location.split("/")[-1] in STATE["deleted"], "session must be DELETEd"


def test_whep_frames_feed_pipeline(server):
    """First acceptance test (protocol-level): frames received over WebRTC are analysed by the real pipeline."""
    from app.fixtures import profile_for
    from app.vision.pipeline import analyze
    c = RingClient(settings_for(server))
    frames, _ = asyncio.run(capture_frames(c, "dev-test-1", max_seconds=4, max_frames=2))
    res, _ = analyze(frames[-1], profile_for("hh_b"), scene("reference"))
    # VP8 transcoding over WebRTC degrades the frame; the decision must still be correct or conservatively 'review'
    assert res["classification"] in ("POTENTIAL_OBSTRUCTION", "REVIEW_REQUIRED")


def test_token_refresh_on_401(server):
    rotated = []
    c = RingClient(settings_for(server, token="expired-token", refresh="r1"), token_sink=lambda t: rotated.append(t))
    devs = asyncio.run(c.devices())
    assert devs and rotated and rotated[0].access_token == VALID_TOKEN
    assert rotated[0].refresh_token.startswith("rotated-")


def test_unauthorized_without_refresh(server):
    c = RingClient(settings_for(server, token="bad"))
    with pytest.raises(RingError) as e:
        asyncio.run(c.devices())
    assert e.value.status == 401 and "Re-link" in e.value.user_message


# 13
def test_stream_unavailable(server):
    STATE["offline"].add("dev-test-1")
    try:
        c = RingClient(settings_for(server))
        with pytest.raises(RingError) as e:
            asyncio.run(capture_frames(c, "dev-test-1", max_seconds=2))
        assert e.value.status == 503 and "offline" in e.value.user_message
    finally:
        STATE["offline"].discard("dev-test-1")


def test_unknown_device(server):
    c = RingClient(settings_for(server))
    with pytest.raises(RingError) as e:
        asyncio.run(capture_frames(c, "nope", max_seconds=2))
    assert e.value.status == 404


def test_signature_scheme():
    body = b'{"type":"motion_detected"}'
    sig = WH.sign("k", body)
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    assert WH.verify("k", body, sig)
    assert not WH.verify("k", body + b" ", sig)
    assert not WH.verify("other", body, sig)
    assert not WH.verify(None, body, sig)


def _v11(attrs: dict, etype="motion_detected", data_id="dev1_motion_1735689600000", request_id="req-1"):
    import json
    return json.dumps({"meta": {"version": "1.1", "account_id": "acct-1", "request_id": request_id},
                       "data": {"id": data_id, "type": etype, "attributes": attrs,
                                "relationships": {"devices": {"links": {"self": "/v1/devices/dev1"}}}}}).encode()


def test_stage0_reads_documented_subtype_path():
    e = WH.parse_event(_v11({"source": "dev1", "source_type": "devices", "sub_type": "human", "timestamp": 1735689600000}))
    assert e["sub_type"] == "human" and WH.stage0(e) [:2] == (True, "PROCESS")
    e = WH.parse_event(_v11({"source": "dev1", "source_type": "devices", "timestamp": 1735689600000}))
    assert e["sub_type"] == "unknown" and WH.stage0(e)[0], "unknown subtypes must be processed"
    e = WH.parse_event(_v11({"source": "dev1", "source_type": "devices"}, etype="device_online"))
    assert WH.stage0(e)[:2] == (False, "FILTERED_IRRELEVANT_TYPE")


def test_v11_documented_payload_is_parsed():
    """Documented v1.1 shape: device in data.attributes.source (source_type 'devices'), no attributes.device_id."""
    e = WH.parse_event(_v11({"source": "dev1", "source_type": "devices", "sub_type": "motion",
                             "timestamp": 1735689600123, "component_ids": ["0"]}))
    assert e["format"] == "v1.1"
    assert e["event_id"] == "dev1_motion_1735689600000" and not e["event_id_from_body_hash"]
    assert e["device_id"] == "dev1" and e["request_id"] == "req-1" and e["account_id"] == "acct-1"
    assert e["component_ids"] == ["0"] and abs(e["timestamp_s"] - 1735689600.123) < 1e-6


def test_v11_missing_or_non_device_source_is_rejected_not_processed():
    for attrs in ({"sub_type": "motion"},                                       # no source at all
                  {"source": "user-9", "source_type": "users"},                 # app_integration-style source
                  {"source": "subscriptions", "source_type": "subscriptions"},
                  {"device_id": "dev1", "sub_type": "motion"}):                  # legacy field is NOT read for v1.1
        e = WH.parse_event(_v11(attrs))
        assert e["device_id"] is None
        assert WH.stage0(e)[:2] == (False, "REJECTED_NO_DEVICE_IDENTITY"), attrs


def test_malformed_bodies_raise():
    import pytest
    for b in (b"not json", b"[1,2]", b"\xff\xfe"):
        with pytest.raises(WH.MalformedEvent):
            WH.parse_event(b)


def test_legacy_shape_still_parsed_only_without_meta():
    e = WH.parse_event(b'{"id":"x1","type":"motion_detected","data":{"attributes":{"device_id":"dev1"}}}')
    assert e["format"] == "legacy" and e["device_id"] == "dev1" and e["event_id"] == "x1"
