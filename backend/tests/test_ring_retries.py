"""Ring request retries must preserve per-request headers and body (audit #2, P0).
Uses httpx.MockTransport to record every attempt exactly as sent; no network."""
import asyncio

import httpx
import pytest

from app.config import Settings
from app.ring import client as client_mod
from app.ring.client import RingClient, RingError

WHEP = "/v1/devices/dev-1/media/streaming/whep/sessions"
OFFER = b"v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"


def settings(token="old-token"):
    s = Settings()
    s.ring_api_base, s.ring_oauth_url = "https://ring.test", "https://ring.test/oauth/token"
    s.ring_access_token, s.ring_refresh_token = token, "refresh-1"
    s.ring_client_id, s.ring_client_secret = "cid", "secret"
    return s


def recorder(responses):
    """Returns (handler, attempts). `responses` is consumed in order for non-OAuth requests."""
    attempts, queue = [], list(responses)

    def handler(req: httpx.Request):
        if req.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "fresh-token", "refresh_token": "refresh-2", "expires_in": 14400})
        attempts.append({"ct": req.headers.get("content-type"), "auth": req.headers.get("authorization"),
                         "ua": req.headers.get("user-agent"), "hw": req.headers.get("hardware_id"), "body": req.content})
        return queue.pop(0)
    return handler, attempts


def created():
    return httpx.Response(201, text="v=0\r\nanswer", headers={"Location": f"{WHEP}/sess-1"})


def post_whep(c):
    return asyncio.run(c.request("POST", WHEP, content=OFFER, headers={"Content-Type": "application/sdp"}))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_s):
        return None
    monkeypatch.setattr(client_mod.asyncio, "sleep", fast)


def check_all(attempts, n):
    assert len(attempts) == n
    for a in attempts:
        assert a["ct"] == "application/sdp", attempts
        assert a["body"] == OFFER
        assert a["ua"].startswith("ClearWayAccessGraph/") and a["hw"]


def test_whep_after_expired_token_keeps_sdp_headers():
    handler, attempts = recorder([httpx.Response(401), created()])
    r = post_whep(RingClient(settings(), transport=httpx.MockTransport(handler)))
    assert r.status_code == 201
    check_all(attempts, 2)
    assert attempts[0]["auth"] == "Bearer old-token" and attempts[1]["auth"] == "Bearer fresh-token"


def test_whep_after_rate_limit_keeps_sdp_headers():
    handler, attempts = recorder([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(429), created()])
    r = post_whep(RingClient(settings(), transport=httpx.MockTransport(handler)))
    assert r.status_code == 201
    check_all(attempts, 3)


def test_whep_after_expired_token_then_rate_limit():
    handler, attempts = recorder([httpx.Response(401), httpx.Response(429, headers={"Retry-After": "1"}), created()])
    r = post_whep(RingClient(settings(), transport=httpx.MockTransport(handler)))
    assert r.status_code == 201
    check_all(attempts, 3)
    assert [a["auth"] for a in attempts] == ["Bearer old-token", "Bearer fresh-token", "Bearer fresh-token"]


def test_rate_limit_exhaustion_is_reported():
    handler, attempts = recorder([httpx.Response(429)] * 4)
    with pytest.raises(RingError) as e:
        post_whep(RingClient(settings(), transport=httpx.MockTransport(handler)))
    assert e.value.status == 429
    check_all(attempts, 4)


def test_caller_headers_not_mutated():
    hdrs = {"Content-Type": "application/sdp"}
    handler, _ = recorder([httpx.Response(401), created()])
    c = RingClient(settings(), transport=httpx.MockTransport(handler))
    asyncio.run(c.request("POST", WHEP, content=OFFER, headers=hdrs))
    assert hdrs == {"Content-Type": "application/sdp"}
