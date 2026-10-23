"""Complete privacy deletion of the live household (audit #2, P1): Ring token material is removed, the integration is
disconnected (best-effort partner unlink), a restart does not silently reconnect, and demo deletions never touch it."""
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import fixtures as FX
from app.config import settings
from app.ring.client import RingClient

from .conftest import H

LIVE = {"X-Household-Token": "live-hh", "X-Member-Token": "live-appr"}


@pytest.fixture()
def env(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="cw-privacy-"))
    for k, v in {"RING_ACCESS_TOKEN": "env-access", "RING_REFRESH_TOKEN": "env-refresh", "RING_CLIENT_ID": "cid",
                 "RING_CLIENT_SECRET": "sec", "RING_DEVICE_ID": "dev-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(settings, "data_dir", d)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "ring_access_token", "env-access")
    monkeypatch.setattr(settings, "ring_refresh_token", "env-refresh")
    monkeypatch.setattr(settings, "ring_client_id", "cid")
    monkeypatch.setattr(settings, "ring_client_secret", "sec")
    monkeypatch.setattr(settings, "ring_device_id", "dev-1")
    monkeypatch.setattr(settings, "live_household_token", "live-hh")
    monkeypatch.setattr(settings, "live_approver_token", "live-appr")
    return d


def ring_calls(status=204):
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(status)
    return calls, httpx.MockTransport(handler)


def start():
    import app.main as M
    return TestClient(M.app), M


def live_with_tokens(M, transport):
    """Live household with a Ring client (mock transport) and rotated tokens stored in the encrypted vault."""
    M.STATE["vault"].save({"access_token": "rotated-access", "refresh_token": "rotated-refresh"})
    M.STATE["ring"] = RingClient(settings, transport=transport)
    s = M.svc()
    s.db.update("cameras", "id", "hh_live_ring", {"authorized": 1})
    s.save_calibration("hh_live", "hh_live_ring", FX.profile_for("hh_b"), FX.scene("reference"), "RING_PLAYGROUND")
    s.ingest("hh_live", "hh_live_ring", FX.scene("initial"), "RING_PLAYGROUND", "RING_API_CAPTURE")


def secret_names(M):
    return {r["name"] for r in M.svc().db.all("SELECT name FROM secrets")}


def test_live_deletion_removes_tokens_and_disconnects(env):
    calls, transport = ring_calls(204)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        assert "ring_tokens" in secret_names(M)
        r = c.delete("/api/households/hh_live/data", headers=LIVE)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["deleted"] and out["ring_disconnect"]["stored_tokens_removed"] is True
        assert ("DELETE", "/v1/accounts/me/app-integrations") in calls
        assert "204" in out["ring_disconnect"]["unlink"]
        assert "ring_tokens" not in secret_names(M)
        assert M.STATE["ring"] is None and settings.ring_access_token is None and settings.ring_refresh_token is None
        assert M.svc().db.one("SELECT COUNT(*) n FROM observations WHERE household_id='hh_live'")["n"] == 0


def test_restart_with_same_env_credentials_stays_disconnected(env):
    calls, transport = ring_calls(204)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        assert c.delete("/api/households/hh_live/data", headers=LIVE).status_code == 200
    settings.ring_access_token, settings.ring_refresh_token = "env-access", "env-refresh"  # env re-read on restart
    c2, M = start()
    with c2:
        assert M.STATE["ring"] is None
        assert any("disconnected" in w for w in c2.get("/api/system").json()["warnings"])
        assert c2.get("/api/households/hh_live/ring/devices", headers={"X-Household-Token": "live-hh"}).status_code == 503


def test_new_credentials_relink(env, monkeypatch):
    calls, transport = ring_calls(204)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        c.delete("/api/households/hh_live/data", headers=LIVE)
    monkeypatch.setenv("RING_ACCESS_TOKEN", "brand-new-access")
    settings.ring_access_token, settings.ring_refresh_token = "brand-new-access", "env-refresh"
    c2, M = start()
    with c2:
        assert M.STATE["ring"] is not None  # operator supplied new credentials: re-linked
        assert "ring_disconnected" not in secret_names(M)


def test_unlink_failure_still_removes_local_tokens(env):
    calls, transport = ring_calls(503)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        out = c.delete("/api/households/hh_live/data", headers=LIVE).json()
        assert "failed" in out["ring_disconnect"]["unlink"]
        assert out["ring_disconnect"]["stored_tokens_removed"] is True and "ring_tokens" not in secret_names(M)


def test_demo_deletion_never_touches_ring_credentials(env):
    calls, transport = ring_calls(204)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        r = c.delete("/api/households/hh_b/data", headers=H("hh_b", "hh_b_resident"))
        assert r.status_code == 200 and "ring_disconnect" not in r.json()
        assert "ring_tokens" in secret_names(M) and M.STATE["ring"] is not None and calls == []


def test_live_deletion_requires_approver(env):
    calls, transport = ring_calls(204)
    c, M = start()
    with c:
        live_with_tokens(M, transport)
        assert c.delete("/api/households/hh_live/data", headers={"X-Household-Token": "live-hh"}).status_code == 403
        assert "ring_tokens" in secret_names(M) and calls == []
