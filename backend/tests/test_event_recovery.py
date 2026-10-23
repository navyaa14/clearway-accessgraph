"""Durable webhook processing (audit #2, P1): interrupted events must be recovered without depending on redelivery,
re-runs must not duplicate side effects, and partially persisted work must be completed, not lost."""
import json
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.ring import webhook as WH

from .conftest import H

DEAD_PROCESS = "boot-of-a-process-that-crashed"


@pytest.fixture()
def data_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="cw-recovery-"))
    monkeypatch.setattr(settings, "data_dir", d)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "ring_access_token", None)
    monkeypatch.setattr(settings, "ring_refresh_token", None)
    return d


def boot():
    import app.main as M
    return TestClient(M.app), M


def sim_body(event_id: str, scene: str = "initial", hh: str = "hh_b") -> bytes:
    return json.dumps({"id": event_id, "type": "motion_detected", "data": {"type": "motion_detected", "attributes": {
        "sub_type": "package_delivery", "clearway_household": hh, "clearway_fixture": scene, "render": {}}}}).encode()


def plant_event(M, event_id, status, owner=DEAD_PROCESS, lease_until=None, attempts=0, updated_at=None, scene="initial"):
    """Write an event row exactly as an earlier process would have left it."""
    body = sim_body(event_id, scene)
    now = time.time()
    M.svc().db.insert("events", {"event_key": f"simulator:{event_id}", "source": "simulator", "type": "motion_detected",
                                 "received_at": now - 600, "updated_at": updated_at or now - 600, "body_sha256": "x",
                                 "status": status, "detail": "planted", "household_id": "hh_b", "observation_id": None,
                                 "attempts": attempts, "last_error": None, "payload": body, "owner": owner,
                                 "lease_until": now + 3600 if lease_until is None else lease_until})
    return body


def wait_status(c, key, want, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        ev = {e["event_key"]: e for e in c.get("/api/households/hh_b/events", headers=H()).json()}
        if ev.get(key, {}).get("status") in want:
            return ev[key]
        time.sleep(0.2)
    raise AssertionError(f"{key} never reached {want}: {ev.get(key)}")


def n_obs(c, event_id):
    return sum(o["event_id"] == event_id for o in c.get("/api/households/hh_b/observations?limit=200", headers=H()).json())


@pytest.mark.parametrize("status", ["RECEIVED", "PROCESSING"])
def test_interrupted_event_recovered_after_restart(data_dir, status):
    c, M = boot()
    with c:
        plant_event(M, f"crash-{status}", status)  # lease still "valid", but its owner process is gone
    c2, M = boot()  # process restart on the same data directory
    with c2:
        ev = wait_status(c2, f"simulator:crash-{status}", {"COMPLETED"})
        assert ev["attempts"] == 1
        assert n_obs(c2, f"crash-{status}") == 1
        kinds = [e["kind"] for e in c2.get("/api/households/hh_b/ledger", headers=H()).json()]
        assert "EVENT_RECOVERED" in kinds
        assert c2.get("/api/households/hh_b/incidents", headers=H()).json()[0]["state"] == "ASSESSED"


def test_stuck_event_redelivery_is_processed_not_ignored(data_dir):
    c, M = boot()
    with c:
        body = plant_event(M, "stuck-1", "PROCESSING", lease_until=time.time() - 1)  # lease expired
        res = c.post("/webhooks/simulator", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)}).json()
        assert res.get("duplicate") is not True and res["retry"] is True
        wait_status(c, "simulator:stuck-1", {"COMPLETED"})
        assert n_obs(c, "stuck-1") == 1
        # once completed, further redeliveries are duplicates again
        again = c.post("/webhooks/simulator", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)}).json()
        assert again.get("duplicate") is True


def test_active_lease_in_this_process_is_not_double_claimed(data_dir):
    c, M = boot()
    with c:
        body = plant_event(M, "busy-1", "PROCESSING", owner=M.BOOT_ID, lease_until=time.time() + 300)
        res = c.post("/webhooks/simulator", content=body, headers={"X-Signature": WH.sign(settings.simulator_secret, body)}).json()
        assert res.get("duplicate") is True  # being worked on right now: do not start a second copy
        assert not M.claim_event("simulator:busy-1", include_failed=True)


def test_partial_persistence_is_not_duplicated(data_dir):
    """Crash after the observation was saved but before the event was marked COMPLETED."""
    c, M = boot()
    with c:
        c.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "partial-1"}, headers=H())
        oid = c.get("/api/households/hh_b/observations", headers=H()).json()[0]["id"]
        M.svc().db.update("events", "event_key", "simulator:partial-1", {"status": "PROCESSING", "owner": DEAD_PROCESS})
    c2, M = boot()
    with c2:
        ev = wait_status(c2, "simulator:partial-1", {"COMPLETED"})
        assert ev and n_obs(c2, "partial-1") == 1
        assert M.svc().db.one("SELECT observation_id FROM events WHERE event_key='simulator:partial-1'")["observation_id"] == oid


def test_unrouted_observation_is_routed_on_recovery(data_dir):
    """Crash after the observation row was written but before it was routed to an incident."""
    c, M = boot()
    with c:
        c.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "unrouted-1"}, headers=H())
        db = M.svc().db
        oid = db.one("SELECT id FROM observations WHERE event_id='unrouted-1'")["id"]
        inc = db.one("SELECT id FROM incidents WHERE origin_observation_id=?", (oid,))["id"]
        # roll back to the state just before routing: no incident, observation without disposition
        db.x("INSERT INTO purge_guard(allow) VALUES(1)")
        db.x("DELETE FROM ledger WHERE incident_id=?", (inc,))
        db.x("DELETE FROM purge_guard")
        db.x("DELETE FROM graph_edges WHERE dst_id=?", (inc,))
        db.x("DELETE FROM incidents WHERE id=?", (inc,))
        db.update("observations", "id", oid, {"incident_id": None, "disposition": None})
        db.update("events", "event_key", "simulator:unrouted-1", {"status": "PROCESSING", "owner": DEAD_PROCESS})
    c2, M = boot()
    with c2:
        wait_status(c2, "simulator:unrouted-1", {"COMPLETED"})
        o = c2.get(f"/api/households/hh_b/observations/{oid}", headers=H()).json()
        assert o["disposition"] == "OPENED_INCIDENT" and o["incident_id"]
        assert n_obs(c2, "unrouted-1") == 1
        assert any(e["kind"] == "OBSERVATION_ROUTING_RECOVERED" for e in c2.get("/api/households/hh_b/ledger", headers=H()).json())


def test_failed_event_retried_without_redelivery_then_dead(data_dir, monkeypatch):
    import app.main as M
    monkeypatch.setattr(settings, "event_retry_after_s", 0)
    monkeypatch.setattr(settings, "event_max_total_attempts", 4)
    real = M.FX.scene
    c, M = boot()
    with c:
        plant_event(M, "flaky-sweep", "FAILED", owner=M.BOOT_ID, lease_until=0, attempts=3, updated_at=time.time() - 10)
        out = c.portal.call(M.recover_events)  # what the background sweeper does every 30 s
        assert "simulator:flaky-sweep" in out["recovered"]
        assert wait_status(c, "simulator:flaky-sweep", {"COMPLETED"})["attempts"] == 4
        # an event that keeps failing stops at the total cap and is marked DEAD (no infinite retries)
        monkeypatch.setattr(M.FX, "scene", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("renderer down")))
        plant_event(M, "hopeless", "FAILED", owner=M.BOOT_ID, lease_until=0, attempts=3, updated_at=time.time() - 10)
        c.portal.call(M.recover_events)
        ev = wait_status(c, "simulator:hopeless", {"DEAD"})
        assert ev["attempts"] == 4 and "renderer down" in ev["last_error"]
        c.portal.call(M.recover_events)
        assert wait_status(c, "simulator:hopeless", {"DEAD"})["attempts"] == 4  # not retried again
        assert any(e["kind"] == "EVENT_DEAD" for e in c.get("/api/households/hh_b/ledger", headers=H()).json())
        monkeypatch.setattr(M.FX, "scene", real)


def test_completed_and_filtered_events_never_reprocessed(data_dir):
    c, M = boot()
    with c:
        c.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "done-1"}, headers=H())
        c.post("/api/households/hh_b/simulator/emit", json={"scene": "initial", "event_id": "noise-1", "type": "device_online"}, headers=H())
    c2, M = boot()
    with c2:
        out = c2.portal.call(M.recover_events, True)
        assert out["recovered"] == []
        assert n_obs(c2, "done-1") == 1 and n_obs(c2, "noise-1") == 0
