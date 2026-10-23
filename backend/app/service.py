"""Application services: observation ingestion, incident orchestration,
human-approved assistance, scene graph, metrics and event bus."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from . import fixtures as FX
from . import incidents as SM
from .config import settings
from .db import DB, canonical, new_id, sha
from .vision.pipeline import analyze

SOURCES = {"RING_PLAYGROUND", "RING_LIVE", "RING_API_EMULATOR", "CUSTOM_EVENT_SIMULATOR", "INDEPENDENT_TEST_VIDEO", "RECORDED_REPLAY", "SYNTHETIC_RENDER"}
# Provenance is recorded on separate axes so that "how it was captured" never implies "whose technology produced it".
EVIDENCE_ORIGIN = {"RING_PLAYGROUND": "RING", "RING_LIVE": "RING", "RING_API_EMULATOR": "RING_API_EMULATOR",
                   "INDEPENDENT_TEST_VIDEO": "INDEPENDENT_TEST", "RECORDED_REPLAY": "REPLAY",
                   "SYNTHETIC_RENDER": "SYNTHETIC", "CUSTOM_EVENT_SIMULATOR": "SYNTHETIC"}
VERIFICATION_BASIS = {"RING": "GENUINE_RING_CAPTURE", "RING_API_EMULATOR": "EMULATOR_CAPTURE"}  # everything else: DEMO_EVIDENCE


def capture_method_of(event_source: str, extra: dict | None) -> str:
    t = ((extra or {}).get("ring") or {}).get("transport")
    if t in ("WHEP", "SNAPSHOT"):
        return t
    if event_source == "CUSTOM_EVENT_SIMULATOR":
        return "SIMULATOR_RENDER"
    return "UPLOAD"


SOURCE_NOTES = {
    "RING_PLAYGROUND": "Genuine Ring Developer Playground media via the documented Ring API (Ring watermark retained).",
    "RING_LIVE": "Genuine Ring device media via the documented Ring API (Ring watermark retained).",
    "RING_API_EMULATOR": "Captured by ClearWay over the Ring Partner API protocol (WHEP/WebRTC) from the local Ring API emulator. NOT Ring footage; the picture is a labelled synthetic scene.",
    "INDEPENDENT_TEST_VIDEO": "Independent test footage supplied by the user. NOT Ring camera footage.",
    "RECORDED_REPLAY": "Replay of previously recorded footage. NOT a live observation.",
    "SYNTHETIC_RENDER": "Procedurally rendered test scene with exact ground truth. NOT camera footage.",
    "CUSTOM_EVENT_SIMULATOR": "Ring-shaped event produced by ClearWay's signed simulator. NOT a Ring event.",
}


class Bus:
    def __init__(self):
        self.subs: dict[str, list[asyncio.Queue]] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, hh: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subs.setdefault(hh, []).append(q)
        return q

    def unsubscribe(self, hh: str, q):
        if q in self.subs.get(hh, []):
            self.subs[hh].remove(q)

    def publish(self, hh: str, kind: str, data: dict):
        msg = {"kind": kind, "data": data, "ts": time.time()}
        for q in list(self.subs.get(hh, [])):
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(_put, q, msg)


def _put(q, msg):
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass


class Service:
    def __init__(self, db: DB, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.bus = Bus()
        (data_dir / "frames").mkdir(parents=True, exist_ok=True)
        (data_dir / "refs").mkdir(parents=True, exist_ok=True)
        kf = data_dir / ".media_key"
        if not kf.exists():
            kf.write_bytes(os.urandom(32))
            os.chmod(kf, 0o600)
        self.media_key = kf.read_bytes()

    # ---------------------------------------------------- short-lived signed URLs
    def sign(self, scope: str, ttl: int | None = None) -> tuple[int, str]:
        exp = int(time.time()) + int(ttl or settings.media_url_ttl_s)
        return exp, hmac.new(self.media_key, f"{scope}|{exp}".encode(), hashlib.sha256).hexdigest()

    def verify_sig(self, scope: str, exp: int | None, sig: str | None) -> bool:
        if not exp or not sig or int(exp) < time.time():
            return False
        good = hmac.new(self.media_key, f"{scope}|{int(exp)}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, sig)

    def signed_url(self, path: str, ttl: int | None = None) -> str:
        exp, sig = self.sign(path, ttl)
        return f"{path}?exp={exp}&sig={sig}"

    # ---------------------------------------------------------------- seeding
    def seed_demo(self):
        if self.db.one("SELECT id FROM households WHERE id='hh_a'"):
            return
        ref = FX.scene("reference")
        for hh_id, hh in FX.HOUSEHOLDS.items():
            self.db.insert("households", {"id": hh_id, "name": hh["name"], "need": hh["need"],
                                          "token_hash": sha(f"demo-{hh_id}-token"), "created_at": time.time(), "is_demo": 1})
            members = [("resident", "Resident", 1, 0), ("helper", "Neighbour (authorised helper)", 0, 1), ("guest", "Visitor (not authorised)", 0, 0)]
            for role, name, can_approve, helper in members:
                mid = f"{hh_id}_{role}"
                self.db.insert("members", {"id": mid, "household_id": hh_id, "name": name, "role": role,
                                           "can_approve": can_approve, "is_helper": helper, "contact": "local-inbox",
                                           "token_hash": sha(f"demo-{mid}-token")})
            cam_id = f"{hh_id}_synthcam"
            self.db.insert("cameras", {"id": cam_id, "household_id": hh_id, "name": "Front porch (synthetic test camera)",
                                       "kind": "synthetic", "ring_device_id": None, "authorized": 1, "authorized_at": time.time()})
            self.save_calibration(hh_id, cam_id, FX.profile_for(hh_id), ref, "SYNTHETIC_RENDER", version=FX.profile_for(hh_id)["version"])
            self.db.append_ledger(hh_id, "HOUSEHOLD_CREATED", {"name": hh["name"], "need": hh["need"], "camera": cam_id})
        # Demo households never receive a Ring camera: demo tokens are public by design.

    def provision_live_household(self, hh_id: str, household_token: str, approver_token: str, helper_token: str | None,
                                 device_id: str | None, name: str | None = None, emulator: bool = False) -> bool:
        """A non-demo household whose tokens come only from server configuration.
        It is the only kind of household that can own a Ring camera."""
        if len({household_token, approver_token, helper_token or ""} - {""}) < (3 if helper_token else 2):
            raise ValueError("live tokens must be distinct")
        name = name or ("Live household (Ring API emulator)" if emulator else "Live household (Ring)")
        cam_name = "Ring camera (API emulator — not Ring)" if emulator else "Ring camera"
        if self.db.one("SELECT id FROM households WHERE id=?", (hh_id,)):
            self.db.update("households", "id", hh_id, {"token_hash": sha(household_token), "name": name})
            if self.db.one("SELECT id FROM cameras WHERE id=?", (f"{hh_id}_ring",)):
                self.db.update("cameras", "id", f"{hh_id}_ring", {"name": cam_name, **({"ring_device_id": device_id} if device_id else {})})
            elif device_id:
                self.db.insert("cameras", {"id": f"{hh_id}_ring", "household_id": hh_id, "name": cam_name,
                                           "kind": "ring", "ring_device_id": device_id, "authorized": 0, "authorized_at": None})
            self.db.update("members", "id", f"{hh_id}_resident", {"token_hash": sha(approver_token)})
            if helper_token:
                self.db.update("members", "id", f"{hh_id}_helper", {"token_hash": sha(helper_token)})
            return False
        self.db.insert("households", {"id": hh_id, "name": name, "need": "Configured by the household (mark route and clearance in Spatial twin).",
                                      "token_hash": sha(household_token), "created_at": time.time(), "is_demo": 0})
        self.db.insert("members", {"id": f"{hh_id}_resident", "household_id": hh_id, "name": "Resident", "role": "resident",
                                   "can_approve": 1, "is_helper": 0, "contact": "local-inbox", "token_hash": sha(approver_token)})
        self.db.insert("members", {"id": f"{hh_id}_helper", "household_id": hh_id, "name": "Authorised helper", "role": "helper",
                                   "can_approve": 0, "is_helper": 1, "contact": "local-inbox",
                                   "token_hash": sha(helper_token) if helper_token else None})
        if device_id:
            self.db.insert("cameras", {"id": f"{hh_id}_ring", "household_id": hh_id, "name": cam_name,
                                       "kind": "ring", "ring_device_id": device_id, "authorized": 0, "authorized_at": None})
        self.db.append_ledger(hh_id, "HOUSEHOLD_CREATED", {"name": name, "live": True, "ring_device": bool(device_id)})
        return True

    def check_token(self, hh_id: str, token: str | None) -> bool:
        row = self.db.one("SELECT token_hash FROM households WHERE id=?", (hh_id,))
        return bool(row and token and row["token_hash"] and hmac.compare_digest(row["token_hash"], sha(token)))

    def is_demo(self, hh_id: str) -> bool:
        row = self.db.one("SELECT is_demo FROM households WHERE id=?", (hh_id,))
        return bool(row and row["is_demo"])

    def member_from_token(self, hh_id: str, token: str | None) -> dict | None:
        """Authenticated household member (identity comes from the credential, never from a request body)."""
        if not token:
            return None
        return self.db.one("SELECT * FROM members WHERE household_id=? AND token_hash=?", (hh_id, sha(token)))

    # ------------------------------------------------------------ calibration
    def save_calibration(self, hh_id, cam_id, profile: dict, reference: np.ndarray | None, ref_source: str, version: str | None = None) -> dict:
        prev = self.db.one("SELECT * FROM calibrations WHERE household_id=? AND camera_id=? AND active=1", (hh_id, cam_id))
        n = len(self.db.all("SELECT id FROM calibrations WHERE household_id=? AND camera_id=?", (hh_id, cam_id))) + 1
        version = version or f"{cam_id}-cal-v{n}"
        ref_path = prev["reference_path"] if prev else None
        if reference is not None:
            ref_path = str(self.data_dir / "refs" / f"{hh_id}__{cam_id}_{n}.png")
            cv2.imwrite(ref_path, reference)
            ref_source_final = ref_source
        else:
            ref_source_final = prev["reference_source"] if prev else None
        profile = {**profile, "camera_id": cam_id, "version": version}
        self.db.x("UPDATE calibrations SET active=0 WHERE household_id=? AND camera_id=?", (hh_id, cam_id))
        cid = new_id("cal")
        self.db.insert("calibrations", {"id": cid, "household_id": hh_id, "camera_id": cam_id, "version": version,
                                        "profile": json.dumps(profile), "reference_path": ref_path, "reference_source": ref_source_final,
                                        "created_at": time.time(), "active": 1})
        self.db.append_ledger(hh_id, "CALIBRATION_SAVED", {"camera_id": cam_id, "version": version, "profile_sha256": sha(canonical(profile)),
                                                           "reference_source": ref_source_final,
                                                           "mode": "METRIC" if profile.get("floor_points_img") else "APPARENT"})
        self.bus.publish(hh_id, "calibration", {"camera_id": cam_id, "version": version})
        return {"id": cid, "version": version}

    def active_calibration(self, hh_id, cam_id) -> tuple[dict | None, np.ndarray | None, dict | None]:
        row = self.db.one("SELECT * FROM calibrations WHERE household_id=? AND camera_id=? AND active=1", (hh_id, cam_id))
        if not row:
            return None, None, None
        ref = cv2.imread(row["reference_path"]) if row["reference_path"] and os.path.exists(row["reference_path"]) else None
        return json.loads(row["profile"]), ref, row

    # ------------------------------------------------------------ ingestion
    def metric(self, stage: str, ok: bool, latency_ms: float | None = None, detail: str = ""):
        self.db.insert("metrics", {"ts": time.time(), "stage": stage, "ok": int(ok), "latency_ms": latency_ms, "detail": detail[:300]})

    def ingest(self, hh_id: str, cam_id: str, frame: np.ndarray, media_source: str, event_source: str,
               captured_at: float | None = None, event_id: str | None = None, prompt_rect=None, extra: dict | None = None) -> dict:
        assert media_source in SOURCES, media_source
        cam = self.db.one("SELECT * FROM cameras WHERE id=? AND household_id=?", (cam_id, hh_id))
        if not cam:
            raise KeyError("camera not found for household")
        trust = SM.trust_of(media_source)
        ring_meta = (extra or {}).get("ring") or {}
        capture_session = ((extra or {}).get("capture_session") or (ring_meta.get("whep_session") or {}).get("id")
                           or f"capture-{uuid.uuid4().hex[:12]}")  # snapshot / upload: each capture is its own session
        if (cam["kind"] == "ring") != (trust == "LIVE"):
            raise PermissionError("only media captured by the server from Ring may be attached to a Ring camera, "
                                  "and Ring-labelled media may only come from a Ring camera")
        asserted = captured_at
        if trust == "LIVE":
            # Live chronology is the server's own capture clock; any caller-supplied time is ignored.
            captured_at, chronology = time.time(), "SERVER_CAPTURED"
        elif captured_at is not None:
            chronology = "CLIENT_ASSERTED"
        else:
            captured_at, chronology = time.time(), "SERVER_RECEIVED"
        profile, ref, cal_row = self.active_calibration(hh_id, cam_id)
        oid = new_id("obs")
        t0 = time.perf_counter()
        if profile is None:
            analysis = {"classification": "UNABLE_TO_VERIFY", "explanation": "No calibration (route) exists for this camera yet.",
                        "objects": [], "quality": {}, "calibration_check": {}, "limitations": ["Mark the essential route first."]}
            ann = frame.copy()
        else:
            analysis, ann = analyze(frame, profile, ref, prompt_rect=prompt_rect)
        lat = (time.perf_counter() - t0) * 1000
        self.metric("analysis", True, lat, analysis["classification"])
        analysis["provenance"] = {"media_source": media_source, "media_source_note": SOURCE_NOTES.get(media_source),
                                  "trust": trust, "chronology": chronology, "asserted_captured_at": asserted, "capture_session": capture_session,
                                  "evidence_origin": EVIDENCE_ORIGIN[media_source], "capture_method": capture_method_of(event_source, extra),
                                  "event_source": event_source, "event_id": event_id, "captured_at": captured_at,
                                  "camera_id": cam_id, "reference_source": cal_row["reference_source"] if cal_row else None, **(extra or {})}
        d = self.data_dir / "frames" / hh_id
        d.mkdir(parents=True, exist_ok=True)
        fpath, apath = d / f"{oid}.jpg", d / f"{oid}_evidence.jpg"
        cv2.imwrite(str(fpath), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        cv2.imwrite(str(apath), ann, [cv2.IMWRITE_JPEG_QUALITY, 90])
        fsha = analysis.get("frame", {}).get("sha256") or hashlib.sha256(fpath.read_bytes()).hexdigest()
        obs = {"id": oid, "household_id": hh_id, "camera_id": cam_id, "media_source": media_source, "event_source": event_source,
               "event_id": event_id, "captured_at": captured_at, "received_at": time.time(), "frame_sha256": fsha,
               "frame_path": str(fpath), "annotated_path": str(apath), "classification": analysis["classification"],
               "calibration_version": profile.get("version") if profile else None, "analysis": json.dumps(analysis, default=float),
               "incident_id": None, "disposition": None, "trust": trust, "chronology": chronology, "asserted_captured_at": asserted,
               "capture_session": capture_session, "evidence_origin": EVIDENCE_ORIGIN[media_source],
               "capture_method": capture_method_of(event_source, extra)}
        self.db.insert("observations", obs)
        self.db.append_ledger(hh_id, "OBSERVATION_ANALYZED", {
            "observation_id": oid, "frame_sha256": fsha, "evidence_sha256": hashlib.sha256(apath.read_bytes()).hexdigest(),
            "media_source": media_source, "event_source": event_source, "captured_at": captured_at,
            "evidence_origin": obs["evidence_origin"], "capture_method": obs["capture_method"], "trust": trust, "chronology": chronology, "classification": analysis["classification"], "calibration_version": obs["calibration_version"],
            "pipeline_version": analysis.get("pipeline_version"), "explanation": analysis.get("explanation")})
        self._graph_for_observation(hh_id, oid, analysis)
        self._route_to_incident(hh_id, cam_id, obs, analysis)
        out = self.observation(hh_id, oid)
        self.bus.publish(hh_id, "observation", {"id": oid, "classification": analysis["classification"]})
        return out

    def _edge(self, hh, src_t, src, rel, dst_t, dst, obs_id=None, data=None):
        self.db.insert("graph_edges", {"id": new_id("edge"), "household_id": hh, "src_type": src_t, "src_id": src, "rel": rel,
                                       "dst_type": dst_t, "dst_id": dst, "observation_id": obs_id,
                                       "data": json.dumps(data or {}, default=float), "created_at": time.time()})

    def _graph_for_observation(self, hh, oid, a):
        for o in a.get("objects", []):
            node = f"{oid}:{o['id']}"
            nom = o.get("nominal", {})
            if nom.get("intersection_area", 0) > 0:
                self._edge(hh, "object", node, "OBJECT_OVERLAPS_ROUTE", "route", a.get("calibration_version"), oid,
                           {"intersection": nom.get("intersection_area"), "units": nom.get("units"), "p_obstructed": o.get("p_obstructed")})
            if (o.get("inside_drop_zone_fraction") or 0) >= 0.8:
                self._edge(hh, "object", node, "OBJECT_INSIDE_DROP_ZONE", "drop_zone", a.get("calibration_version"), oid,
                           {"fraction": o.get("inside_drop_zone_fraction")})

    def complete_routing(self, hh, oid):
        """Finish an observation that was persisted but never routed (process died mid-ingest)."""
        o = self.db.one("SELECT * FROM observations WHERE id=? AND household_id=?", (oid, hh))
        if not o or o["disposition"] is not None:
            return
        opened = self.db.one("SELECT id FROM incidents WHERE household_id=? AND origin_observation_id=?", (hh, oid))
        if opened:  # the incident was created before the crash; just link it
            self.db.update("observations", "id", oid, {"incident_id": opened["id"], "disposition": "OPENED_INCIDENT"})
        else:
            self._route_to_incident(hh, o["camera_id"], o, json.loads(o["analysis"]))
        self.db.append_ledger(hh, "OBSERVATION_ROUTING_RECOVERED", {"observation_id": oid})

    # ------------------------------------------------------------ incidents
    def _transition(self, inc: dict, dst: str, reasons: list, obs_id: str | None = None, extra: dict | None = None):
        SM.check(inc["state"], dst)
        self.db.append_ledger(inc["household_id"], "STATE_TRANSITION",
                              {"from": inc["state"], "to": dst, "observation_id": obs_id, "reasons": reasons, **(extra or {})}, incident_id=inc["id"])
        inc["state"] = dst
        self.db.update("incidents", "id", inc["id"], {"state": dst, "updated_at": time.time()})

    def _route_to_incident(self, hh, cam, obs, analysis):
        cls = analysis["classification"]
        inc = self.db.one("SELECT * FROM incidents WHERE household_id=? AND camera_id=? AND state!='RESOLVED' ORDER BY opened_at DESC LIMIT 1", (hh, cam))
        metric, _ = SM.severity_metric(analysis)
        if inc is None:
            if cls not in ("POTENTIAL_OBSTRUCTION", "REVIEW_REQUIRED"):
                self.db.update("observations", "id", obs["id"], {"disposition": "NO_INCIDENT"})
                return
            iid = new_id("inc")
            inc = {"id": iid, "household_id": hh, "camera_id": cam, "state": "OBSERVED", "opened_at": time.time(), "updated_at": time.time(),
                   "namespace": obs["trust"], "calibration_version": obs["calibration_version"],
                   "evidence_origin": obs.get("evidence_origin"), "verification_basis": None,
                   "origin_observation_id": obs["id"], "latest_observation_id": obs["id"], "latest_captured_at": obs["captured_at"],
                   "assistance_sent_at": None, "baseline_metric": metric, "last_metric": metric, "clear_streak": 0, "resolution": None, "closed_at": None}
            self.db.insert("incidents", inc)
            self.db.append_ledger(hh, "INCIDENT_OPENED", {"observation_id": obs["id"], "classification": cls, "namespace": obs["trust"],
                                                         "calibration_version": obs["calibration_version"]}, incident_id=iid)
            self._transition(inc, "ASSESSED", [analysis.get("explanation", "")], obs["id"], {"classification": cls, "metric": metric})
            self.db.update("observations", "id", obs["id"], {"incident_id": iid, "disposition": "OPENED_INCIDENT"})
            self._edge(hh, "observation", obs["id"], "OBSERVATION_SUPPORTS_CLAIM", "incident", iid, obs["id"], {"claim": "marked route potentially obstructed"})
            self.bus.publish(hh, "incident", {"id": iid, "state": inc["state"]})
            return
        if not inc.get("evidence_origin"):  # incident created before v2.3: derive its origin from the opening observation
            o0 = self.db.one("SELECT evidence_origin, media_source FROM observations WHERE id=?", (inc["origin_observation_id"],)) or {}
            inc["evidence_origin"] = o0.get("evidence_origin") or EVIDENCE_ORIGIN.get(o0.get("media_source") or "")
            if inc["evidence_origin"]:
                self.db.update("incidents", "id", inc["id"], {"evidence_origin": inc["evidence_origin"]})
        seen = {r["frame_sha256"] for r in self.db.all("SELECT frame_sha256 FROM observations WHERE incident_id=?", (inc["id"],))}
        close_after = settings.live_close_after_clear if inc.get("namespace") == "LIVE" else settings.close_after_clear_observations
        v = SM.verify_new_evidence(inc, obs, analysis, seen, close_after,
                                   settings.live_min_clear_separation_s if inc.get("namespace") == "LIVE" else 0.0)
        self.db.update("observations", "id", obs["id"], {"incident_id": inc["id"], "disposition": v.disposition})
        if v.disposition == "ORIGIN_MISMATCH":
            self.db.append_ledger(hh, "EVIDENCE_ORIGIN_MISMATCH", {"observation_id": obs["id"], "incident_origin": inc.get("evidence_origin"),
                                                                   "observation_origin": obs.get("evidence_origin"), "accepted": v.accepted,
                                                                   "reasons": v.reasons}, incident_id=inc["id"])
        if not v.accepted:
            self.db.append_ledger(hh, "EVIDENCE_REJECTED", {"observation_id": obs["id"], "disposition": v.disposition, "reasons": v.reasons}, incident_id=inc["id"])
            self.bus.publish(hh, "incident", {"id": inc["id"], "state": inc["state"], "rejected": v.disposition})
            return
        prior = inc["state"]
        self._transition(inc, "REASSESSED", ["new distinct observation; geometry recomputed independently"], obs["id"],
                         {"classification": cls, "metric": metric, "previous_metric": inc["last_metric"]})
        nxt = v.next_state if v.next_state != "ASSESSED" else ("ASSESSED" if prior == "ASSESSED" else "AWAITING_NEW_EVIDENCE")
        if nxt == "AWAITING_NEW_EVIDENCE" and prior == "ASSESSED":
            nxt = "ASSESSED"
        self._transition(inc, nxt, v.reasons, obs["id"])
        fields = {"latest_observation_id": obs["id"], "latest_captured_at": obs["captured_at"],
                  "clear_streak": (int(inc["clear_streak"] or 0) + (1 if v.counts_as_clear else 0)) if cls == "NO_OBSTRUCTION_OBSERVED" else 0}
        if v.disposition == "ORIGIN_MISMATCH":
            fields["clear_streak"] = 0  # earlier clearance evidence is never reused across an origin change
        if v.counts_as_clear:
            fields.update({"last_clear_session": obs.get("capture_session"), "last_clear_at": obs["captured_at"]})
        if metric is not None:
            fields["last_metric"] = metric
        if nxt == "RESOLVED":
            # resolution string kept for compatibility; verification_basis says WHOSE captures verified it
            fields.update({"resolution": "VERIFIED_NO_OBSTRUCTION_OBSERVED", "closed_at": time.time(),
                           "verification_basis": VERIFICATION_BASIS.get(inc.get("evidence_origin") or obs.get("evidence_origin"), "DEMO_EVIDENCE")})
        self.db.update("incidents", "id", inc["id"], fields)
        self._edge(hh, "observation", obs["id"], "VERIFICATION_REASSESSES_INCIDENT", "incident", inc["id"], obs["id"], {"result": nxt})
        rel = "OBSERVATION_CONTRADICTS_CLAIM" if cls == "NO_OBSTRUCTION_OBSERVED" else ("OBSERVATION_SUPPORTS_CLAIM" if cls == "POTENTIAL_OBSTRUCTION" else None)
        if rel:
            self._edge(hh, "observation", obs["id"], rel, "incident", inc["id"], obs["id"], {"claim": "marked route potentially obstructed"})
        self.bus.publish(hh, "incident", {"id": inc["id"], "state": nxt})

    # ------------------------------------------------------------ assistance
    def draft_assistance(self, hh, inc_id) -> dict:
        inc = self.incident_row(hh, inc_id)
        if inc["state"] == "RESOLVED":
            raise SM.TransitionError("incident already resolved")
        helper = self.db.one("SELECT * FROM members WHERE household_id=? AND is_helper=1 ORDER BY id LIMIT 1", (hh,))
        if not helper:
            raise PermissionError("no authorised helper configured for this household")
        obs = self.observation(hh, inc["latest_observation_id"])
        a = obs["analysis"]
        cf = a.get("counterfactual") or {}
        msg = ("Your help is requested at the front entrance. ClearWay's latest camera check suggests the marked walking route "
               "may be obstructed by an object" + (f" ({a.get('explanation')})" if a.get("explanation") else "") + ". "
               + (cf.get("message", "") + " " if cf.get("resolves") else "")
               + "Please check it in person if you can. After any change, ClearWay will re-check the camera before closing this request; "
               "the request itself is not treated as proof the route is clear.")
        aid = new_id("act")
        self.db.insert("actions", {"id": aid, "incident_id": inc_id, "household_id": hh, "kind": "ASSISTANCE_REQUEST", "status": "DRAFT",
                                   "message": msg, "recipient_member_id": helper["id"], "channel": "SIMULATED_LOCAL_INBOX",
                                   "drafted_by": "clearway-agent (deterministic template)", "approved_by": None, "approved_at": None,
                                   "sent_at": None, "created_at": time.time()})
        self.db.append_ledger(hh, "ASSISTANCE_DRAFTED", {"action_id": aid, "recipient": helper["id"], "channel": "SIMULATED_LOCAL_INBOX",
                                                        "message_sha256": sha(msg), "requires_approval": True}, incident_id=inc_id)
        self.bus.publish(hh, "action", {"id": aid, "status": "DRAFT"})
        return self.db.one("SELECT * FROM actions WHERE id=?", (aid,))

    def approve(self, hh, action_id, member: dict | None) -> dict:
        """`member` is the AUTHENTICATED member (resolved from its credential), never a claimed id."""
        act = self.db.one("SELECT * FROM actions WHERE id=? AND household_id=?", (action_id, hh))
        if not act:
            raise KeyError("action not found")
        member_id = member["id"] if member else None
        if not member or member["household_id"] != hh or not member["can_approve"]:
            self.db.append_ledger(hh, "AUTHORIZATION_DENIED", {"action_id": action_id, "member_id": member_id,
                                                               "reason": "member is not authorised to approve assistance requests"}, incident_id=act["incident_id"])
            raise PermissionError("member is not authorised to approve assistance requests")
        if act["status"] != "DRAFT":
            raise SM.TransitionError(f"action is {act['status']}, not DRAFT")
        inc = self.incident_row(hh, act["incident_id"])
        self._transition(inc, "ASSISTANCE_REQUESTED", [f"approved by {member['name']}"], None, {"action_id": action_id, "approved_by": member_id})
        self.db.update("actions", "id", action_id, {"status": "APPROVED", "approved_by": member_id, "approved_at": time.time()})
        self.db.append_ledger(hh, "ASSISTANCE_APPROVED", {"action_id": action_id, "approved_by": member_id}, incident_id=act["incident_id"])
        self._edge(hh, "action", action_id, "ACTION_REQUESTED_FOR_INCIDENT", "incident", act["incident_id"], None, {"approved_by": member_id})
        return self.db.one("SELECT * FROM actions WHERE id=?", (action_id,))

    def send(self, hh, action_id) -> dict:
        act = self.db.one("SELECT * FROM actions WHERE id=? AND household_id=?", (action_id, hh))
        if not act:
            raise KeyError("action not found")
        if act["status"] != "APPROVED" or not act["approved_by"]:
            self.db.append_ledger(hh, "AUTHORIZATION_DENIED", {"action_id": action_id, "reason": f"send attempted while action is {act['status']} (approval required)"}, incident_id=act["incident_id"])
            raise PermissionError("assistance cannot be sent without recorded approval")
        recipient = self.db.one("SELECT * FROM members WHERE id=? AND household_id=? AND is_helper=1", (act["recipient_member_id"], hh))
        if not recipient:
            raise PermissionError("recipient is not an authorised helper of this household")
        now = time.time()
        mid = new_id("msg")
        self.db.insert("inbox", {"id": mid, "household_id": hh, "member_id": recipient["id"], "channel": act["channel"],
                                 "subject": "Help requested: entrance route may be obstructed", "body": act["message"],
                                 "incident_id": act["incident_id"], "action_id": action_id, "delivered_at": now, "read_at": None})
        self.db.update("actions", "id", action_id, {"status": "SENT", "sent_at": now})
        inc = self.incident_row(hh, act["incident_id"])
        self._transition(inc, "AWAITING_NEW_EVIDENCE", ["assistance message delivered; awaiting a new, independent observation"], None,
                         {"action_id": action_id, "inbox_message_id": mid})
        self.db.update("incidents", "id", inc["id"], {"assistance_sent_at": now})
        self.db.append_ledger(hh, "ASSISTANCE_SENT", {"action_id": action_id, "inbox_message_id": mid, "channel": act["channel"],
                                                     "recipient": recipient["id"]}, incident_id=act["incident_id"])
        self.bus.publish(hh, "action", {"id": action_id, "status": "SENT"})
        return self.db.one("SELECT * FROM actions WHERE id=?", (action_id,))

    def override(self, hh, inc_id, member: dict | None, reason: str) -> dict:
        member_id = member["id"] if member else None
        if not member or member["household_id"] != hh or not member["can_approve"]:
            self.db.append_ledger(hh, "AUTHORIZATION_DENIED", {"member_id": member_id, "reason": "override requires an approving household member"}, incident_id=inc_id)
            raise PermissionError("override requires an approving household member")
        if not reason or len(reason.strip()) < 4:
            raise ValueError("a reason is required for manual override")
        inc = self.incident_row(hh, inc_id)
        self._transition(inc, "RESOLVED", [f"MANUAL OVERRIDE by {member['name']}: {reason.strip()}"], None, {"override": True, "member_id": member_id})
        # A manual close is a separately labelled outcome: it is NOT camera-verified clearance.
        self.db.update("incidents", "id", inc_id, {"resolution": "MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED", "closed_at": time.time(),
                                                   "verification_basis": "MANUAL_CONFIRMATION"})
        self.bus.publish(hh, "incident", {"id": inc_id, "state": "RESOLVED"})
        return self.incident(hh, inc_id)

    # ------------------------------------------------------------ queries
    def incident_row(self, hh, inc_id) -> dict:
        inc = self.db.one("SELECT * FROM incidents WHERE id=? AND household_id=?", (inc_id, hh))
        if not inc:
            raise KeyError("incident not found")
        return inc

    def observation(self, hh, oid) -> dict | None:
        o = self.db.one("SELECT * FROM observations WHERE id=? AND household_id=?", (oid, hh))
        if not o:
            return None
        o["analysis"] = json.loads(o["analysis"])
        # short-lived signed URLs: no household credential ever appears in a media URL
        o["frame_url"] = self.signed_url(f"/api/households/{hh}/observations/{oid}/frame")
        o["evidence_url"] = self.signed_url(f"/api/households/{hh}/observations/{oid}/evidence")
        o.pop("frame_path", None)
        o.pop("annotated_path", None)
        return o

    def incident(self, hh, inc_id) -> dict:
        inc = self.incident_row(hh, inc_id)
        inc["ledger"] = [dict(r, payload=json.loads(r["payload"])) for r in self.db.all("SELECT * FROM ledger WHERE incident_id=? ORDER BY seq", (inc_id,))]
        inc["observations"] = [self.observation(hh, r["id"]) for r in self.db.all("SELECT id FROM observations WHERE incident_id=? ORDER BY captured_at", (inc_id,))]
        inc["actions"] = self.db.all("SELECT * FROM actions WHERE incident_id=? ORDER BY created_at", (inc_id,))
        inc["graph"] = self.db.all("SELECT * FROM graph_edges WHERE household_id=? AND (dst_id=? OR src_id=?) ORDER BY created_at", (hh, inc_id, inc_id))
        inc["why"] = self.why(inc)
        return inc

    def why(self, inc) -> list[dict]:
        """Human-readable decision trail for the 'Why this decision?' panel."""
        out = []
        for e in inc["ledger"]:
            p = e["payload"]
            if e["kind"] == "STATE_TRANSITION":
                out.append({"ts": e["ts"], "title": f"{p['from']} → {p['to']}", "reasons": p.get("reasons", []),
                            "observation_id": p.get("observation_id"), "metric": p.get("metric"), "previous_metric": p.get("previous_metric"),
                            "entry_hash": e["entry_hash"]})
            elif e["kind"] in ("EVIDENCE_REJECTED", "AUTHORIZATION_DENIED"):
                out.append({"ts": e["ts"], "title": e["kind"].replace("_", " ").title(), "reasons": p.get("reasons") or [p.get("reason")],
                            "observation_id": p.get("observation_id"), "entry_hash": e["entry_hash"]})
        return out

    def purge_old(self, days: int, household_id: str | None = None) -> int:
        """Retention purge. With household_id it is strictly scoped to that household
        (the per-household API); without it, it is the server-wide maintenance job run at startup."""
        cutoff = time.time() - days * 86400
        if household_id is None:
            rows = self.db.all("SELECT id, household_id, frame_path, annotated_path FROM observations WHERE received_at < ? AND frame_path != ''", (cutoff,))
        else:
            rows = self.db.all("SELECT id, household_id, frame_path, annotated_path FROM observations WHERE household_id=? AND received_at < ? AND frame_path != ''",
                               (household_id, cutoff))
        for r in rows:
            for p in (r["frame_path"], r["annotated_path"]):
                if p and os.path.exists(p):
                    os.remove(p)
            self.db.update("observations", "id", r["id"], {"frame_path": "", "annotated_path": ""})
            self.db.append_ledger(r["household_id"], "MEDIA_PURGED", {"observation_id": r["id"], "retention_days": days})
        return len(rows)

    def delete_household_data(self, hh) -> dict:
        """Delete everything belonging to one household: DB rows (incl. the household record and
        member credentials), evidence frames and calibration reference images."""
        refs = [r["reference_path"] for r in self.db.all("SELECT reference_path FROM calibrations WHERE household_id=?", (hh,)) if r["reference_path"]]
        n_obs = self.db.one("SELECT COUNT(*) n FROM observations WHERE household_id=?", (hh,))["n"]
        self.db.purge_household(hh)
        removed = 0
        for p in set(refs):
            if os.path.exists(p):
                os.remove(p)
                removed += 1
        shutil.rmtree(self.data_dir / "frames" / hh, ignore_errors=True)
        return {"deleted": True, "observations": n_obs, "reference_files": removed}
