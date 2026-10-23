"""ClearWay AccessGraph 2.0 — FastAPI application.

Security model (see docs/SECURITY.md):
  * Household access: `X-Household-Token` header only (never a URL parameter).
  * Member actions (approve, override, authorise camera): `X-Member-Token` header;
    the member's identity comes from that credential, never from a request body.
  * Demo households (is_demo=1) publish their tokens via /api/demo/households so judges can
    try the app. They can never own a Ring camera or reach Ring endpoints.
  * The live household is provisioned only from server env tokens and is never listed.
  * Media and the event stream use short-lived HMAC-signed URLs / tickets.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import httpx
import numpy as np
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import bedrock
from . import fixtures as FX
from . import incidents as SM
from .config import settings
from .db import DB, sha
from .ring import webhook as WH
from .ring.client import RingClient, RingError
from .ring.whep import SESSIONS, capture_frames
from .service import SOURCE_NOTES, SOURCES, Service
from .tokens import TokenVault
from .vision import calibration as CAL
from .vision import quality as Q
from .vision import segmentation as SEG
from .vision import uncertainty as U
from .vision.geometry import APPARENT_OVERLAP_FRACTION
from .vision.pipeline import PIPELINE_VERSION

log = logging.getLogger("clearway")
STATE: dict = {}
LIVE_HH = "hh_live"
UPLOAD_SOURCES = ("INDEPENDENT_TEST_VIDEO", "RECORDED_REPLAY")


def svc() -> Service:
    return STATE["svc"]


@asynccontextmanager
async def lifespan(_app):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = DB(settings.data_dir / "clearway.db")
    s = Service(db, settings.data_dir)
    s.bus.loop = asyncio.get_running_loop()
    STATE.clear()
    STATE.update({"svc": s, "vault": TokenVault(db, settings), "warnings": [], "started": time.time()})
    if settings.demo_mode:
        await run_in_threadpool(s.seed_demo)
    if settings.live_household_token and settings.live_approver_token:
        s.provision_live_household(LIVE_HH, settings.live_household_token, settings.live_approver_token,
                                   settings.live_helper_token, settings.ring_device_id if settings.ring_configured else None,
                                   emulator=settings.ring_configured and not settings.ring_is_official_host)
    elif settings.ring_configured:
        STATE["warnings"].append("Ring credentials are configured but CLEARWAY_LIVE_HOUSEHOLD_TOKEN / CLEARWAY_LIVE_APPROVER_TOKEN are not; "
                                 "no household can use the Ring camera until they are set.")
    for w in STATE["warnings"]:
        log.warning(w)
    STATE["ring"] = make_ring_client()
    STATE["purged_on_start"] = s.purge_old(settings.retention_days)  # server-wide maintenance job
    # Durable event handling: reclaim work interrupted by a previous process, then keep sweeping.
    STATE["recovery_task"] = asyncio.create_task(recover_events(startup=True))
    STATE["sweeper_task"] = asyncio.create_task(event_sweeper())
    yield
    for t in ("sweeper_task", "recovery_task"):
        task = STATE.get(t)
        if task and not task.done():
            task.cancel()


app = FastAPI(title="ClearWay AccessGraph 2.0", version="2.3.0", lifespan=lifespan)


def make_ring_client() -> RingClient | None:
    if not settings.ring_configured:
        return None
    vault: TokenVault = STATE["vault"]
    if vault.is_disconnected(settings):
        STATE["warnings"].append("The Ring integration was disconnected when the live household was deleted. "
                                 "Supply new Ring credentials (re-link) to connect again.")
        return None
    stored = vault.load()
    if stored:  # rotated tokens persisted (encrypted) take precedence over env
        settings.ring_access_token = stored.get("access_token") or settings.ring_access_token
        settings.ring_refresh_token = stored.get("refresh_token") or settings.ring_refresh_token
    return RingClient(settings, token_sink=lambda t: vault.save({"access_token": t.access_token, "refresh_token": t.refresh_token}))


# ---------------------------------------------------------------- auth / isolation
def household(hh: str, x_household_token: str | None = Header(None)) -> str:
    if not svc().check_token(hh, x_household_token):
        raise HTTPException(401, "missing or invalid household token (X-Household-Token header)")
    return hh


def member(hh: str = Depends(household), x_member_token: str | None = Header(None)) -> dict | None:
    """Authenticated member of THIS household, or None. Callers decide whether None is acceptable."""
    return svc().member_from_token(hh, x_member_token)


def live_household(hh: str = Depends(household)) -> str:
    if svc().is_demo(hh):
        raise HTTPException(403, "demo households cannot access Ring devices; sign in to the live household")
    return hh


def media_access(hh: str, request: Request, exp: int | None = Query(None), sig: str | None = Query(None),
                 x_household_token: str | None = Header(None)) -> str:
    if svc().check_token(hh, x_household_token) or svc().verify_sig(request.url.path, exp, sig):
        return hh
    raise HTTPException(403, "media link expired or invalid")


def deny(hh: str, reason: str, incident_id: str | None = None, **extra):
    svc().db.append_ledger(hh, "AUTHORIZATION_DENIED", {"reason": reason, **extra}, incident_id=incident_id)
    raise HTTPException(403, reason)


def jerr(e: Exception):
    if isinstance(e, PermissionError):
        raise HTTPException(403, str(e))
    if isinstance(e, KeyError):
        raise HTTPException(404, str(e).strip("'"))
    if isinstance(e, (SM.TransitionError, ValueError)):
        raise HTTPException(409, str(e))
    raise e


# ---------------------------------------------------------------- system
@app.get("/api/health")
def health():
    return {"ok": True, "pipeline": PIPELINE_VERSION}


@app.get("/api/system")
def system():
    return {
        "pipeline_version": PIPELINE_VERSION, "model_version": SEG.MODEL_VERSION, "demo_mode": settings.demo_mode,
        "ring": {"configured": settings.ring_configured, "source_label": settings.effective_ring_source_label,
                 "live_household_provisioned": bool(svc().db.one("SELECT id FROM households WHERE is_demo=0")),
                 "webhook_secret_configured": bool(settings.ring_webhook_secret),
                 "verified_against_real_ring": False,
                 "note": "The WHEP/REST adapter has been exercised only against a local protocol test server."},
        "aws": bedrock.status(),
        "sources": {k: SOURCE_NOTES.get(k) for k in sorted(SOURCES)},
        "evidence_policy": {"live_sources": sorted(SM.TRUSTED_LIVE_SOURCES),
                            "live_close_after_clear_observations": settings.live_close_after_clear,
                            "live_min_clear_separation_s": settings.live_min_clear_separation_s,
                            "demo_close_after_clear_observations": settings.close_after_clear_observations,
                            "note": "Only server-captured Ring frames can verify a live incident; uploads, replays, synthetic renders and simulator events drive demo incidents only."},
        "policy": {"perturbation_agreement_obstructed": U.P_OBSTRUCTED, "perturbation_agreement_clear": U.P_CLEAR,
                   "apparent_overlap_fraction": APPARENT_OVERLAP_FRACTION, "mc_samples": __import__("app.vision.pipeline", fromlist=["MC_SAMPLES"]).MC_SAMPLES, "mask_radii_px": U.RADII,
                   "calibration_jitter_px": U.CAL_JITTER_PX, "max_camera_shift_px": CAL.MAX_SHIFT_PX,
                   "min_relative_sharpness": Q.MIN_SHARPNESS_REL,
                   "note": "Engineering policy defaults tuned on synthetic fixtures; not validated on real Ring footage. "
                           "Perturbation agreement is the share of perturbed re-measurements reaching a conclusion, not a calibrated probability."},
        "privacy": {"face_recognition": False, "identity_inference": False, "continuous_video_stored": False,
                    "stored_media": f"one selected still frame and one annotated copy per observation (JPEG), retained {settings.retention_days} days",
                    "retention_days": settings.retention_days, "auto_door_unlock": False, "auto_share_footage": False},
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_video_seconds": settings.max_video_seconds,
                   "max_image_pixels": settings.max_image_pixels},
        "warnings": STATE.get("warnings", []),
        "started_at": STATE.get("started"),
    }


@app.get("/api/demo/households")
def demo_households():
    """Demo-only: publishes credentials of DEMO households and their SIMULATED member identities."""
    if not settings.demo_mode:
        raise HTTPException(404, "demo mode is off")
    out = []
    for h in svc().db.all("SELECT * FROM households WHERE is_demo=1 ORDER BY id"):
        members = svc().db.all("SELECT id, name, role, can_approve, is_helper FROM members WHERE household_id=? ORDER BY id", (h["id"],))
        out.append({"id": h["id"], "name": h["name"], "need": h["need"], "token": f"demo-{h['id']}-token", "demo": True,
                    "identities": [{**m, "token": f"demo-{m['id']}-token"} for m in members]})
    return out


@app.get("/api/metrics")
def metrics():
    rows = svc().db.all("SELECT stage, COUNT(*) n, SUM(ok) ok, AVG(latency_ms) avg_ms, MAX(latency_ms) max_ms FROM metrics GROUP BY stage")
    lat = [r["latency_ms"] for r in svc().db.all("SELECT latency_ms FROM metrics WHERE stage='analysis' AND latency_ms IS NOT NULL")]
    out = {r["stage"]: {"count": r["n"], "ok": r["ok"], "failures": r["n"] - (r["ok"] or 0),
                        "avg_ms": round(r["avg_ms"] or 0, 1), "max_ms": round(r["max_ms"] or 0, 1)} for r in rows}
    if lat:
        out.setdefault("analysis", {})["p50_ms"] = round(float(np.percentile(lat, 50)), 1)
        out["analysis"]["p95_ms"] = round(float(np.percentile(lat, 95)), 1)
    return out


EVAL_RESULT = Path(__file__).resolve().parents[1] / "eval" / "results" / "latest.json"


@app.get("/api/eval/latest")
def eval_latest():
    if not EVAL_RESULT.exists():
        raise HTTPException(404, "no evaluation has been run (python -m eval.run_eval)")
    return json.loads(EVAL_RESULT.read_text())


# ---------------------------------------------------------------- household
@app.get("/api/households/{hh}")
def get_household(hh: str = Depends(household), me: dict | None = Depends(member)):
    db = svc().db
    h = db.one("SELECT id, name, need, created_at, is_demo FROM households WHERE id=?", (hh,))
    h["members"] = db.all("SELECT id, name, role, can_approve, is_helper FROM members WHERE household_id=? ORDER BY id", (hh,))
    h["me"] = {k: me[k] for k in ("id", "name", "role", "can_approve", "is_helper")} if me else None
    cams = db.all("SELECT * FROM cameras WHERE household_id=? ORDER BY id", (hh,))
    for c in cams:
        prof, ref, row = svc().active_calibration(hh, c["id"])
        c["calibration"] = None if not prof else {"version": prof["version"], "mode": "METRIC" if prof.get("floor_points_img") else "APPARENT",
                                                  "reference_source": row["reference_source"], "has_reference": ref is not None}
    h["cameras"] = cams
    h["open_incidents"] = db.all("SELECT * FROM incidents WHERE household_id=? AND state!='RESOLVED' ORDER BY opened_at DESC", (hh,))
    h["incident_count"] = db.one("SELECT COUNT(*) n FROM incidents WHERE household_id=?", (hh,))["n"]
    last = db.one("SELECT id FROM observations WHERE household_id=? ORDER BY received_at DESC LIMIT 1", (hh,))
    h["latest_observation"] = svc().observation(hh, last["id"]) if last else None
    h["unread_inbox"] = db.one("SELECT COUNT(*) n FROM inbox WHERE household_id=? AND read_at IS NULL", (hh,))["n"]
    return h


class CameraIn(BaseModel):
    name: str
    kind: str = Field("upload", pattern="^(upload)$")  # Ring cameras are provisioned by the server, never by API callers


@app.post("/api/households/{hh}/cameras")
def add_camera(body: CameraIn, hh: str = Depends(household)):
    cid = f"{hh}_cam_{uuid.uuid4().hex[:6]}"
    svc().db.insert("cameras", {"id": cid, "household_id": hh, "name": body.name, "kind": "upload",
                                "ring_device_id": None, "authorized": 1, "authorized_at": time.time()})
    svc().db.append_ledger(hh, "CAMERA_ADDED", {"camera_id": cid, "kind": "upload"})
    return {"id": cid}


@app.post("/api/households/{hh}/cameras/{cam}/authorize")
def authorize_camera(cam: str, hh: str = Depends(household), me: dict | None = Depends(member)):
    db = svc().db
    if not me or not me["can_approve"]:
        deny(hh, "camera authorisation requires an authenticated approving member", camera_id=cam, member_id=me and me["id"])
    if not db.one("SELECT id FROM cameras WHERE id=? AND household_id=?", (cam, hh)):
        raise HTTPException(404)
    db.update("cameras", "id", cam, {"authorized": 1, "authorized_at": time.time()})
    db.append_ledger(hh, "CAMERA_AUTHORIZED", {"camera_id": cam, "member_id": me["id"]})
    return {"ok": True}


def camera_row(hh, cam) -> dict:
    row = svc().db.one("SELECT * FROM cameras WHERE id=? AND household_id=?", (cam, hh))
    if not row:
        raise HTTPException(404, "camera not found for household")
    return row


# ---------------------------------------------------------------- calibration
class CalibrationIn(BaseModel):
    route: list[list[float]]
    drop_zone: list[list[float]] | None = None
    clearance_cm: float | None = None
    floor_points_img: list[list[float]] | None = None
    floor_points_world: list[list[float]] | None = None


@app.get("/api/households/{hh}/cameras/{cam}/calibration")
def get_calibration(cam: str, hh: str = Depends(household)):
    prof, ref, row = svc().active_calibration(hh, cam)
    hist = svc().db.all("SELECT version, created_at, reference_source FROM calibrations WHERE household_id=? AND camera_id=? ORDER BY created_at", (hh, cam))
    hom = None
    if prof and ref is not None:
        h = CAL.floor_homography(prof.get("floor_points_img"), prof.get("floor_points_world"), ref.shape[1], ref.shape[0])
        hom = {"reprojection_error_cm": h["reprojection_error_cm"],
               "note": "consistency with the entered points only; not a measure of real-world accuracy"} if h else None
    return {"profile": prof, "reference_source": row["reference_source"] if row else None,
            "reference_size": [ref.shape[1], ref.shape[0]] if ref is not None else None, "homography": hom, "history": hist,
            "reference_url": svc().signed_url(f"/api/households/{hh}/cameras/{cam}/reference") if ref is not None else None}


@app.put("/api/households/{hh}/cameras/{cam}/calibration")
def put_calibration(cam: str, body: CalibrationIn, hh: str = Depends(household)):
    camera_row(hh, cam)

    def valid(poly, n=3):
        return poly and len(poly) >= n and all(len(p) == 2 and 0 <= p[0] <= 1 and 0 <= p[1] <= 1 for p in poly)
    if not valid(body.route):
        raise HTTPException(422, "route must be a polygon of >=3 normalised points")
    if body.drop_zone and not valid(body.drop_zone):
        raise HTTPException(422, "invalid drop zone polygon")
    if body.floor_points_img or body.floor_points_world:
        if not (valid(body.floor_points_img, 4) and body.floor_points_world and len(body.floor_points_world) == len(body.floor_points_img)):
            raise HTTPException(422, "floor calibration needs >=4 image points with matching world (cm) coordinates")
    prof, ref, _ = svc().active_calibration(hh, cam)
    if ref is None:
        raise HTTPException(409, "capture or upload a clear-scene reference frame first")
    if body.floor_points_img:
        h = CAL.floor_homography(body.floor_points_img, body.floor_points_world, ref.shape[1], ref.shape[0])
        if h is None:
            raise HTTPException(422, "floor points are degenerate; homography could not be computed")
    return svc().save_calibration(hh, cam, body.model_dump(), None, "unchanged")


@app.get("/api/households/{hh}/cameras/{cam}/reference")
def get_reference(cam: str, hh: str = Depends(media_access)):
    _, _, row = svc().active_calibration(hh, cam)
    if not row or not row["reference_path"] or not os.path.exists(row["reference_path"]):
        raise HTTPException(404)
    return FileResponse(row["reference_path"], headers={"Cache-Control": "private, no-store"})


async def read_limited(file: UploadFile) -> bytes:
    limit = int(settings.max_upload_mb * 1024 * 1024)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"upload exceeds {settings.max_upload_mb:g} MB")
    if not data:
        raise HTTPException(422, "empty upload")
    return data


def _check_pixels(w: int, h: int):
    if w * h > settings.max_image_pixels or w < 64 or h < 64:
        raise HTTPException(422, f"unsupported resolution {w}x{h} (max {settings.max_image_pixels} pixels, min 64x64)")


def decode_media(data: bytes, filename: str) -> tuple[np.ndarray, dict]:
    try:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:  # OpenCV refuses images above OPENCV_IO_MAX_IMAGE_PIXELS (decompression-bomb guard)
        raise HTTPException(422, f"image exceeds the {settings.max_image_pixels}-pixel decoding limit")
    if img is not None:
        _check_pixels(img.shape[1], img.shape[0])
        return img, {"kind": "image"}
    suffix = Path(filename or "v.mp4").suffix.lower()
    if suffix not in (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"):
        raise HTTPException(422, "media could not be decoded as an image and is not a supported video container")
    tmp = settings.data_dir / f"upload_{uuid.uuid4().hex}{suffix}"
    tmp.write_bytes(data)
    try:
        cap = cv2.VideoCapture(str(tmp))
        if not cap.isOpened():
            raise HTTPException(422, "media could not be decoded (unsupported, corrupt or encrypted)")
        _check_pixels(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if not np.isfinite(fps) or fps <= 0 or fps > 240:
            fps = 25.0
        max_read = int(fps * settings.max_video_seconds)  # hard cap on decode work regardless of container metadata
        step = max(1, int(round(fps / 2)))  # ~2 fps sampling (Ring CV guideline)
        frames, i = [], 0
        while len(frames) < 24 and i < max_read:
            ok, f = cap.read()
            if not ok:
                break
            if i % step == 0:
                frames.append(f)
            i += 1
        cap.release()
    finally:
        tmp.unlink(missing_ok=True)
    if not frames:
        raise HTTPException(422, "media could not be decoded (unsupported, corrupt or encrypted)")
    best, q = Q.select_best_frame(frames)
    return frames[best], {"kind": "video", "sampled_frames": len(frames), "selected_index": best, "selected_sharpness": q.get("sharpness"),
                          "decode_capped_at_seconds": settings.max_video_seconds}


@app.post("/api/households/{hh}/cameras/{cam}/reference")
async def upload_reference(cam: str, file: UploadFile = File(...), source: str = Form("INDEPENDENT_TEST_VIDEO"), hh: str = Depends(household)):
    if source not in UPLOAD_SOURCES:
        raise HTTPException(422, "uploaded references must be labelled INDEPENDENT_TEST_VIDEO or RECORDED_REPLAY")
    if camera_row(hh, cam)["kind"] == "ring":
        raise HTTPException(403, "a Ring camera's reference must be captured from Ring, not uploaded")
    img, _ = decode_media(await read_limited(file), file.filename)
    prof, _, _ = svc().active_calibration(hh, cam)
    prof = prof or {"route": [], "drop_zone": None, "clearance_cm": None, "floor_points_img": None, "floor_points_world": None}
    return await run_in_threadpool(svc().save_calibration, hh, cam, prof, img, source)


# ---------------------------------------------------------------- observations
@app.get("/api/households/{hh}/observations")
def list_observations(limit: int = 30, hh: str = Depends(household)):
    rows = svc().db.all("SELECT id FROM observations WHERE household_id=? ORDER BY received_at DESC LIMIT ?", (hh, min(limit, 200)))
    return [svc().observation(hh, r["id"]) for r in rows]


@app.get("/api/households/{hh}/observations/{oid}")
def get_observation(oid: str, hh: str = Depends(household)):
    o = svc().observation(hh, oid)
    if not o:
        raise HTTPException(404)
    return o


def _obs_file(hh, oid, col):
    r = svc().db.one(f"SELECT {col} p FROM observations WHERE id=? AND household_id=?", (oid, hh))
    if not r or not r["p"] or not os.path.exists(r["p"]):
        raise HTTPException(404, "media not found or purged under retention policy")
    return FileResponse(r["p"], headers={"Cache-Control": "private, max-age=600"})


@app.get("/api/households/{hh}/observations/{oid}/frame")
def obs_frame(oid: str, hh: str = Depends(media_access)):
    return _obs_file(hh, oid, "frame_path")


@app.get("/api/households/{hh}/observations/{oid}/evidence")
def obs_evidence(oid: str, hh: str = Depends(media_access)):
    return _obs_file(hh, oid, "annotated_path")


@app.post("/api/households/{hh}/observations/upload")
async def upload_observation(file: UploadFile = File(...), camera_id: str = Form(...), source: str = Form("INDEPENDENT_TEST_VIDEO"),
                             captured_at: float | None = Form(None), prompt_rect: str | None = Form(None), hh: str = Depends(household)):
    if source not in UPLOAD_SOURCES:
        raise HTTPException(422, "uploads must be labelled INDEPENDENT_TEST_VIDEO or RECORDED_REPLAY (uploads can never claim to be Ring media)")
    if camera_row(hh, camera_id)["kind"] == "ring":
        raise HTTPException(403, "uploads cannot be attached to a live Ring camera; use an upload camera")
    img, meta = decode_media(await read_limited(file), file.filename)
    rect = None
    if prompt_rect:
        try:
            r = [float(v) for v in json.loads(prompt_rect)][:4]
            rect = [r[0] * img.shape[1], r[1] * img.shape[0], r[2] * img.shape[1], r[3] * img.shape[0]]
        except Exception:
            raise HTTPException(422, "prompt_rect must be a JSON array [x, y, w, h] in normalised coordinates")
    try:
        return await run_in_threadpool(svc().ingest, hh, camera_id, img, source, "USER_UPLOAD", captured_at, None, rect, {"upload": meta, "filename": file.filename})
    except Exception as e:
        jerr(e)


@app.post("/api/households/{hh}/observations/{oid}/explain")
async def explain(oid: str, hh: str = Depends(household)):
    r = svc().db.one("SELECT annotated_path, analysis FROM observations WHERE id=? AND household_id=?", (oid, hh))
    if not r:
        raise HTTPException(404)
    out = await run_in_threadpool(bedrock.explain, json.loads(r["analysis"]), Path(r["annotated_path"]).read_bytes())
    if out["status"] != "disabled":
        svc().metric("bedrock", out["status"] == "ok", out.get("latency_ms"), out.get("error", ""))
        svc().db.append_ledger(hh, "LLM_EXPLANATION", {"observation_id": oid, "status": out["status"], "model_id": out.get("model_id"),
                                                       "text_sha256": sha(out.get("text", "")), "note": "explanatory text only; classification unchanged"})
    return out


# ---------------------------------------------------------------- incidents & assistance
@app.get("/api/households/{hh}/incidents")
def list_incidents(hh: str = Depends(household)):
    return svc().db.all("SELECT * FROM incidents WHERE household_id=? ORDER BY opened_at DESC", (hh,))


@app.get("/api/households/{hh}/incidents/{iid}")
def get_incident(iid: str, hh: str = Depends(household)):
    try:
        return svc().incident(hh, iid)
    except Exception as e:
        jerr(e)


@app.post("/api/households/{hh}/incidents/{iid}/assistance/draft")
def draft(iid: str, hh: str = Depends(household)):
    try:
        return svc().draft_assistance(hh, iid)
    except Exception as e:
        jerr(e)


class ApproveIn(BaseModel):
    member_id: str | None = None  # optional claim; must match the authenticated member or the request is refused


@app.post("/api/households/{hh}/actions/{aid}/approve")
def approve(aid: str, body: ApproveIn | None = None, hh: str = Depends(household), me: dict | None = Depends(member)):
    if body and body.member_id and (not me or body.member_id != me["id"]):
        act = svc().db.one("SELECT incident_id FROM actions WHERE id=? AND household_id=?", (aid, hh))
        deny(hh, "claimed approver does not match the authenticated member", act and act["incident_id"],
             claimed=body.member_id, authenticated=me and me["id"], action_id=aid)
    try:
        return svc().approve(hh, aid, me)
    except Exception as e:
        jerr(e)


@app.post("/api/households/{hh}/actions/{aid}/send")
def send(aid: str, hh: str = Depends(household)):
    try:
        return svc().send(hh, aid)
    except Exception as e:
        jerr(e)


class OverrideIn(BaseModel):
    reason: str
    member_id: str | None = None


@app.post("/api/households/{hh}/incidents/{iid}/override")
def override(iid: str, body: OverrideIn, hh: str = Depends(household), me: dict | None = Depends(member)):
    if body.member_id and (not me or body.member_id != me["id"]):
        deny(hh, "claimed member does not match the authenticated member", iid, claimed=body.member_id, authenticated=me and me["id"])
    try:
        return svc().override(hh, iid, me, body.reason)
    except Exception as e:
        jerr(e)


@app.get("/api/households/{hh}/inbox")
def inbox(hh: str = Depends(household)):
    return svc().db.all("SELECT i.*, m.name member_name FROM inbox i JOIN members m ON m.id=i.member_id WHERE i.household_id=? ORDER BY delivered_at DESC", (hh,))


@app.get("/api/households/{hh}/ledger")
def ledger(limit: int = 200, hh: str = Depends(household)):
    rows = svc().db.all("SELECT * FROM ledger WHERE household_id=? ORDER BY seq DESC LIMIT ?", (hh, min(limit, 1000)))
    return [dict(r, payload=json.loads(r["payload"])) for r in rows]


@app.get("/api/households/{hh}/ledger/verify")
def ledger_verify(hh: str = Depends(household)):
    return svc().db.verify_ledger(hh)


@app.get("/api/households/{hh}/graph")
def graph(hh: str = Depends(household)):
    return svc().db.all("SELECT * FROM graph_edges WHERE household_id=? ORDER BY created_at DESC LIMIT 300", (hh,))


@app.get("/api/households/{hh}/events")
def events(hh: str = Depends(household)):
    return svc().db.all("SELECT event_key, source, type, status, disposition, request_id, device_id, format, attempts, last_error, "
                        "received_at, updated_at, lease_until, observation_id, detail FROM events "
                        "WHERE household_id=? ORDER BY received_at DESC LIMIT 100", (hh,))


@app.delete("/api/households/{hh}/data")
async def delete_data(hh: str = Depends(household), me: dict | None = Depends(member)):
    if not me or not me["can_approve"]:
        deny(hh, "deleting household data requires an authenticated approving member")
    ring_owner = (not svc().is_demo(hh)) and bool(svc().db.one("SELECT id FROM cameras WHERE household_id=? AND kind='ring'", (hh,)))
    disconnect = None
    if ring_owner:
        disconnect = await disconnect_ring()
    out = await run_in_threadpool(svc().delete_household_data, hh)
    if disconnect is not None:
        out["ring_disconnect"] = disconnect
    return out


async def disconnect_ring() -> dict:
    """Disconnect the (single) Ring integration owned by the live household being deleted:
    best-effort partner unlink at Ring, then remove stored token material and the in-process client.
    Demo households never own Ring credentials, so their deletion never reaches this."""
    vault: TokenVault = STATE["vault"]
    c: RingClient | None = STATE.get("ring")
    unlink = "not attempted (Ring client not configured)"
    if c is not None:
        try:
            unlink = f"partner unlink returned HTTP {await c.unlink()}"
        except RingError as e:
            unlink = f"partner unlink failed (HTTP {e.status}: {e.message}); local token material removed anyway"
        except Exception as e:  # network errors must not block local deletion
            unlink = f"partner unlink failed ({type(e).__name__}); local token material removed anyway"
    removed = vault.clear()
    if settings.ring_configured:
        vault.mark_disconnected(settings)
    STATE["ring"] = None
    settings.ring_access_token = settings.ring_refresh_token = None  # drop in-memory copies
    SESSIONS.clear()
    log.warning("Ring integration disconnected on live-household deletion: %s; stored tokens removed=%s", unlink, removed)
    return {"stored_tokens_removed": removed, "unlink": unlink, "client_disconnected": True,
            "note": "Environment variables are operator-controlled; remove RING_* values from the deployment to stop re-linking with them."}


@app.post("/api/households/{hh}/retention/purge")
def purge(days: int = 0, hh: str = Depends(household), me: dict | None = Depends(member)):
    """Manual, irreversible evidence purge: requires an authenticated approving member of THIS household
    (the household token alone is not enough). Strictly scoped to this household. The automatic, server-wide
    retention job (CLEARWAY_RETENTION_DAYS, run at startup) is unaffected."""
    if not me or not me["can_approve"]:
        deny(hh, "manual retention purge requires an authenticated approving member", member_id=me and me["id"], action="retention_purge")
    n = svc().purge_old(max(0, days), household_id=hh)
    svc().db.append_ledger(hh, "RETENTION_PURGE_REQUESTED", {"member_id": me["id"], "days": max(0, days), "purged": n})
    return {"purged": n, "by": me["id"]}


@app.post("/api/households/{hh}/stream-ticket")
def stream_ticket(hh: str = Depends(household)):
    exp, sig = svc().sign(f"stream:{hh}", ttl=120)
    return {"url": f"/api/households/{hh}/stream?exp={exp}&sig={sig}", "expires": exp}


@app.get("/api/households/{hh}/stream")
async def stream(hh: str, request: Request, exp: int | None = Query(None), sig: str | None = Query(None)):
    # EventSource cannot send headers, so the stream uses a 2-minute signed ticket instead of the household token
    if not svc().verify_sig(f"stream:{hh}", exp, sig):
        raise HTTPException(401, "stream ticket missing or expired")
    q = svc().bus.subscribe(hh)

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {msg['kind']}\ndata: {json.dumps(msg, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            svc().bus.unsubscribe(hh, q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- Ring (live household only)
def ring() -> RingClient:
    c = STATE.get("ring")
    if c is None:
        raise HTTPException(503, "Ring is not configured: set RING_ACCESS_TOKEN (or RING_REFRESH_TOKEN + client credentials) and RING_DEVICE_ID")
    return c


@app.get("/api/ring/status")
def ring_status():
    """Public, credential-free status only. Device and session details require the live household."""
    return {"configured": settings.ring_configured, "source_label": settings.effective_ring_source_label,
            "emulator": settings.ring_configured and not settings.ring_is_official_host,
            "live_household_provisioned": bool(svc().db.one("SELECT id FROM households WHERE is_demo=0")),
            "verified_against_real_ring": False}


@app.get("/api/households/{hh}/ring/devices")
async def ring_devices(hh: str = Depends(live_household)):
    t0 = time.perf_counter()
    try:
        devs = await ring().devices()
        svc().metric("ring_api", True, (time.perf_counter() - t0) * 1000, "devices")
        return devs
    except RingError as e:
        svc().metric("ring_api", False, (time.perf_counter() - t0) * 1000, f"{e.status} {e.message}")
        raise HTTPException(502, {"ring_status": e.status, "message": e.user_message})


def emulator_mode():
    if not settings.ring_configured or settings.ring_is_official_host:
        raise HTTPException(404, "not connected to the Ring API emulator")


async def emulator_call(method: str, path: str, json_body: dict | None = None) -> dict:
    """Forward a control request to the emulator (its /emulator/* endpoints are NOT part of Ring's API)."""
    headers = {"X-Emulator-Token": settings.ring_emulator_control_token or ""}
    try:
        async with httpx.AsyncClient(base_url=settings.ring_api_base, timeout=20.0, trust_env=False) as c:
            r = await c.request(method, path, json=json_body, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"emulator unreachable: {type(e).__name__}")
    if r.status_code >= 400:
        raise HTTPException(502, f"emulator returned {r.status_code}: {r.text[:200]}")
    return r.json()


@app.get("/api/households/{hh}/ring/emulator")
async def emulator_state(hh: str = Depends(live_household)):
    emulator_mode()
    return await emulator_call("GET", "/emulator/state")


class EmuSceneIn(BaseModel):
    scene: str
    fire_event: bool = True
    sub_type: str = "package_delivery"


@app.post("/api/households/{hh}/ring/emulator/scene")
async def emulator_scene(body: EmuSceneIn, hh: str = Depends(live_household)):
    """Change what the emulated camera sees and (by default) have the emulator send a signed motion webhook,
    which then flows through /webhooks/ring exactly like a Ring event."""
    emulator_mode()
    return await emulator_call("POST", "/emulator/scene", body.model_dump())


class EmuOnlineIn(BaseModel):
    online: bool


@app.post("/api/households/{hh}/ring/emulator/online")
async def emulator_online(body: EmuOnlineIn, hh: str = Depends(live_household)):
    emulator_mode()
    return await emulator_call("POST", "/emulator/online", body.model_dump())


@app.get("/api/households/{hh}/ring/sessions")
def ring_sessions(hh: str = Depends(household)):
    return sorted([s.public() for s in SESSIONS.values() if s.household_id == hh], key=lambda s: -s["started_at"])[:20]


async def ring_capture_frame(device_id: str, hh: str, component_id: int | None = None) -> tuple[np.ndarray | None, dict]:
    """WHEP live capture first (genuine stream), snapshot download as fallback."""
    c = ring()
    t0 = time.perf_counter()
    comp = settings.ring_component_id if component_id is None else component_id
    meta: dict = {"device_id": device_id, "component_id": comp}
    try:
        frames, info = await capture_frames(c, device_id, component_id=comp, household_id=hh,
                                            max_seconds=min(settings.ring_max_session_s, 25), sample_fps=2.0, max_frames=10)
        meta["whep_session"] = info.public()
        svc().metric("ring_whep", bool(frames), (time.perf_counter() - t0) * 1000, info.state)
        if frames:
            best, q = Q.select_best_frame(frames)
            meta.update({"transport": "WHEP", "sampled_frames": len(frames), "selected_index": best})
            return frames[best], meta
    except RingError as e:
        meta["whep_error"] = {"status": e.status, "message": e.user_message}
        svc().metric("ring_whep", False, (time.perf_counter() - t0) * 1000, f"{e.status}")
        if e.status in (401, 403, 404):
            return None, meta
    try:
        data, ctype = await c.snapshot(device_id, comp)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        meta.update({"transport": "SNAPSHOT", "content_type": ctype})
        if img is None:
            meta["snapshot_error"] = "snapshot not decodable (device may use TAKE encryption)"
        return img, meta
    except RingError as e:
        meta["snapshot_error"] = {"status": e.status, "message": e.user_message}
        return None, meta


def ring_camera(hh, cam):
    row = svc().db.one("SELECT * FROM cameras WHERE id=? AND household_id=? AND kind='ring'", (cam, hh))
    if not row:
        raise HTTPException(404, "Ring camera not found for household")
    if not row["authorized"]:
        raise HTTPException(403, "camera access has not been authorised by the household")
    return row


@app.post("/api/households/{hh}/ring/{cam}/capture")
async def ring_capture(cam: str, hh: str = Depends(live_household)):
    row = ring_camera(hh, cam)
    img, meta = await ring_capture_frame(row["ring_device_id"] or settings.ring_device_id, hh)
    if img is None:
        svc().db.append_ledger(hh, "RING_CAPTURE_FAILED", {"camera_id": cam, **{k: v for k, v in meta.items() if k != "whep_session"}})
        raise HTTPException(502, {"message": "No readable media from Ring", "detail": meta})
    try:
        return await run_in_threadpool(svc().ingest, hh, cam, img, settings.effective_ring_source_label, "RING_API_CAPTURE", None, None, None, {"ring": meta})
    except Exception as e:
        jerr(e)


@app.post("/api/households/{hh}/ring/{cam}/reference")
async def ring_reference(cam: str, hh: str = Depends(live_household)):
    row = ring_camera(hh, cam)
    img, meta = await ring_capture_frame(row["ring_device_id"] or settings.ring_device_id, hh)
    if img is None:
        raise HTTPException(502, {"message": "No readable media from Ring", "detail": meta})
    prof, _, _ = svc().active_calibration(hh, cam)
    prof = prof or {"route": [], "drop_zone": None, "clearance_cm": None, "floor_points_img": None, "floor_points_world": None}
    return await run_in_threadpool(svc().save_calibration, hh, cam, prof, img, settings.effective_ring_source_label)


# ---------------------------------------------------------------- webhooks & event reliability
BOOT_ID = uuid.uuid4().hex  # identifies this process as the owner of event leases
NON_TERMINAL = ("RECEIVED", "PROCESSING")


def record_event(source: str, evt: dict, body: bytes, process: bool, disposition: str, detail: str, hh: str | None = None) -> str:
    """Idempotent receipt with recovery. Returns 'new', 'retry' or 'duplicate'.

    Duplicate detection uses the event id (v1.1: data.id) AND the documented idempotency key meta.request_id.
    'retry' when a previous attempt FAILED, or when the event is stuck RECEIVED/PROCESSING under a lease that
    expired or belongs to a process that no longer exists (crash). The raw body is stored so the work can be
    redone without any redelivery from the sender. Events that are not processed get an explicit terminal
    status (FILTERED / REJECTED) and disposition instead of being reported as analysed."""
    key = f"{source}:{evt['event_id']}"
    db = svc().db
    now = time.time()
    if evt.get("request_id"):
        seen = db.one("SELECT event_key FROM events WHERE source=? AND request_id=? AND event_key!=?", (source, evt["request_id"], key))
        if seen:
            return "duplicate"
    if db.one("SELECT status FROM events WHERE event_key=?", (key,)) is None:
        status = "RECEIVED" if process else ("REJECTED" if disposition.startswith("REJECTED") else "FILTERED")
        db.insert("events", {"event_key": key, "source": source, "type": evt["type"], "received_at": now, "updated_at": now,
                             "body_sha256": sha(body), "status": status, "detail": detail, "disposition": disposition,
                             "request_id": evt.get("request_id"), "device_id": evt.get("device_id"), "format": evt.get("format"),
                             "household_id": hh, "observation_id": None, "attempts": 0, "last_error": None,
                             "payload": body if process else None, "owner": BOOT_ID, "lease_until": now + settings.event_lease_s})
        return "new"
    return "retry" if claim_event(key, include_failed=True) else "duplicate"


class EventDisposition(Exception):
    """Raised by an event handler for an explicit, terminal, non-error outcome (no retries)."""

    def __init__(self, status: str, detail: str, hh: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.hh = status, detail, hh


def claim_event(key: str, include_failed: bool) -> bool:
    """Atomically take ownership of a recoverable event (one claimant wins)."""
    now = time.time()
    statuses = NON_TERMINAL + (("FAILED",) if include_failed else ())
    with svc().db.lock:
        cur = svc().db.x(
            f"UPDATE events SET status='RECEIVED', owner=?, lease_until=?, updated_at=? WHERE event_key=? "
            f"AND status IN ({','.join('?' * len(statuses))}) "
            f"AND (status='FAILED' OR owner IS NULL OR owner!=? OR lease_until IS NULL OR lease_until<?)",
            (BOOT_ID, now + settings.event_lease_s, now, key, *statuses, BOOT_ID, now))
        return cur.rowcount == 1


async def run_event(key: str, fn, evt: dict, hh: str | None, backoff: tuple = (0.5, 2.0)):
    """RECEIVED -> PROCESSING -> COMPLETED | FAILED (bounded attempts per claim). FAILED events are retried by the
    sweeper after CLEARWAY_EVENT_RETRY_AFTER_S and become DEAD after CLEARWAY_EVENT_MAX_TOTAL_ATTEMPTS."""
    db = svc().db
    for attempt in range(settings.event_max_attempts):
        n = (db.one("SELECT attempts FROM events WHERE event_key=?", (key,)) or {}).get("attempts") or 0
        if n >= settings.event_max_total_attempts:
            break
        db.update("events", "event_key", key, {"status": "PROCESSING", "attempts": n + 1, "owner": BOOT_ID,
                                               "lease_until": time.time() + settings.event_lease_s, "updated_at": time.time()})
        try:
            oid = await fn(evt)
            if not oid:  # never report an event as analysed unless an observation was actually produced
                raise RuntimeError("processing returned no observation")
            db.update("events", "event_key", key, {"status": "COMPLETED", "disposition": "ANALYSED", "observation_id": oid,
                                                   "last_error": None, "lease_until": None, "updated_at": time.time()})
            return True
        except EventDisposition as d:  # explicit terminal outcome: recorded, not retried, not "COMPLETED"
            db.update("events", "event_key", key, {"status": "IGNORED", "disposition": d.status, "detail": d.detail,
                                                   "household_id": d.hh, "last_error": None, "lease_until": None, "updated_at": time.time()})
            if d.hh:
                db.append_ledger(d.hh, "EVENT_IGNORED", {"event_key": key, "disposition": d.status, "detail": d.detail})
            return False
        except Exception as e:  # record, back off, retry
            db.update("events", "event_key", key, {"status": "FAILED", "last_error": f"{type(e).__name__}: {e}"[:300],
                                                   "lease_until": None, "updated_at": time.time()})
            svc().metric("event_processing", False, None, key)
            if attempt + 1 < settings.event_max_attempts:
                await asyncio.sleep(backoff[min(attempt, len(backoff) - 1)])
    n = (db.one("SELECT attempts FROM events WHERE event_key=?", (key,)) or {}).get("attempts") or 0
    dead = n >= settings.event_max_total_attempts
    if dead:
        db.update("events", "event_key", key, {"status": "DEAD", "updated_at": time.time()})
    if hh:
        svc().db.append_ledger(hh, "EVENT_DEAD" if dead else "EVENT_PROCESSING_FAILED",
                               {"event_key": key, "attempts": n,
                                "note": "gave up after the total attempt limit" if dead else "will be retried automatically"})
    return False


def _handler_for(source: str):
    return {"ring": process_ring_event, "simulator": process_sim_event}.get(source)


async def recover_events(startup: bool = False) -> dict:
    """Reclaim and re-run work that was interrupted or failed, using the stored payload (no redelivery needed).
    At startup, in-flight events owned by any other process are reclaimed immediately (single-worker deployment);
    otherwise only expired leases and FAILED events older than the retry delay are taken."""
    db = svc().db
    now = time.time()
    rows = db.all("SELECT event_key, source, status, owner, lease_until, updated_at, attempts, household_id FROM events "
                  "WHERE status IN ('RECEIVED','PROCESSING','FAILED') AND payload IS NOT NULL")
    out = {"recovered": [], "skipped": 0}
    for r in rows:
        stale = r["owner"] != BOOT_ID if startup else (r["lease_until"] is None or r["lease_until"] < now)
        if r["status"] == "FAILED":
            ok = (r["attempts"] or 0) < settings.event_max_total_attempts and (r["updated_at"] or 0) < now - settings.event_retry_after_s
        else:
            ok = stale
        fn = _handler_for(r["source"])
        if not ok or fn is None or not claim_event(r["event_key"], include_failed=r["status"] == "FAILED"):
            out["skipped"] += 1
            continue
        body = db.one("SELECT payload FROM events WHERE event_key=?", (r["event_key"],))["payload"]
        evt = WH.parse_event(body)
        evt["recovered"] = True
        if r["household_id"]:
            db.append_ledger(r["household_id"], "EVENT_RECOVERED", {"event_key": r["event_key"], "previous_status": r["status"],
                                                                     "reason": "process restart" if startup else "lease expired / retry due"})
        await run_event(r["event_key"], fn, evt, r["household_id"])
        out["recovered"].append(r["event_key"])
    return out


async def event_sweeper():
    while True:
        await asyncio.sleep(settings.event_sweep_interval_s)
        try:
            await recover_events()
        except Exception as e:  # never let the sweeper die
            log.warning("event sweeper error: %s", e)


def existing_observation(hh: str, cam: str, event_id: str) -> dict | None:
    """Idempotency for re-run events: if the observation was already persisted before a crash, reuse it (and finish
    routing it to an incident if the crash happened before that step)."""
    o = svc().db.one("SELECT id, disposition FROM observations WHERE household_id=? AND camera_id=? AND event_id=? "
                     "ORDER BY received_at LIMIT 1", (hh, cam, event_id))
    if o and o["disposition"] is None:
        svc().complete_routing(hh, o["id"])
    return o


async def process_ring_event(evt: dict):
    device = evt.get("device_id")
    if not device:
        raise EventDisposition("REJECTED_NO_DEVICE_IDENTITY", "event has no device identity")
    known = svc().db.all("SELECT c.* FROM cameras c JOIN households h ON h.id=c.household_id "
                         "WHERE c.kind='ring' AND h.is_demo=0 AND c.ring_device_id=?", (device,))
    if not known:
        raise EventDisposition("IGNORED_UNREGISTERED_DEVICE",
                               f"device '{device}' is not registered to any ClearWay household (account {evt.get('account_id') or 'unknown'})")
    cams = [c for c in known if c["authorized"]]
    if not cams:
        raise EventDisposition("IGNORED_CAMERA_NOT_AUTHORIZED",
                               f"device '{device}' is registered but the household has not authorised camera access", known[0]["household_id"])
    svc().db.x("UPDATE events SET household_id=? WHERE event_key=?", (cams[0]["household_id"], f"ring:{evt['event_id']}"))
    done = {c["id"]: existing_observation(c["household_id"], c["id"], evt["event_id"]) for c in cams}
    todo = [c for c in cams if not done[c["id"]]]
    last = next((o["id"] for o in done.values() if o), None)
    if not todo:
        return last
    comps = [int(x) for x in (evt.get("component_ids") or []) if str(x).isdigit()]
    img, meta = await ring_capture_frame(device, todo[0]["household_id"], comps[0] if comps else None)
    if img is None:  # authorised device but no readable media: a FAILURE (retried), not a silent success
        raise RuntimeError(f"no readable media from Ring for device {device}: "
                           f"{meta.get('whep_error') or meta.get('snapshot_error') or 'unknown'}")
    for c in todo:
        o = await run_in_threadpool(svc().ingest, c["household_id"], c["id"], img, settings.effective_ring_source_label, "RING_WEBHOOK",
                                    None, evt["event_id"], None,
                                    {"ring": meta, "sub_type": evt["sub_type"], "recovered": bool(evt.get("recovered")),
                                     "ring_event": {"format": evt.get("format"), "event_id": evt["event_id"], "request_id": evt.get("request_id"),
                                                    "account_id": evt.get("account_id"), "device_id": device,
                                                    "component_ids": evt.get("component_ids"), "event_timestamp_s": evt.get("timestamp_s")}})
        last = o["id"]
    return last


async def read_webhook_body(request: Request) -> bytes:
    """Bounded read: documented payloads are tiny; refuse anything above MAX_BODY_BYTES before buffering it."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > WH.MAX_BODY_BYTES:
        raise HTTPException(413, "webhook body too large")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > WH.MAX_BODY_BYTES:
            raise HTTPException(413, "webhook body too large")
    return bytes(buf)


def parse_or_400(body: bytes) -> dict:
    try:
        return WH.parse_event(body)
    except WH.MalformedEvent as e:
        raise HTTPException(400, f"malformed event: {e}")


@app.post("/webhooks/ring")
async def ring_webhook(request: Request, bg: BackgroundTasks):
    body = await read_webhook_body(request)
    if not WH.verify(settings.ring_webhook_secret, body, request.headers.get("X-Signature")):
        svc().metric("webhook_signature", False, None, "ring")
        raise HTTPException(401, "invalid signature")
    evt = parse_or_400(body)
    process, disposition, why = WH.stage0(evt)
    state = record_event("ring", evt, body, process, disposition, why)
    if state == "duplicate":
        return {"ok": True, "duplicate": True, "event_id": evt["event_id"]}
    svc().metric("stage0", process, None, why)
    if process:
        bg.add_task(run_event, f"ring:{evt['event_id']}", process_ring_event, evt, None)  # ack within Ring's 5 s window
    # 200 even when not processed (Ring expects 2xx); the disposition is recorded and returned explicitly.
    return {"ok": True, "processing": process, "disposition": disposition, "reason": why, "event_id": evt["event_id"],
            "device_id": evt.get("device_id"), "format": evt.get("format"), "retry": state == "retry"}


async def process_sim_event(evt: dict):
    a = evt["raw"].get("data", {}).get("attributes", {})
    hh, scene_name = a.get("clearway_household"), a.get("clearway_fixture")
    if not hh or not svc().is_demo(hh):
        raise PermissionError("simulator events may only target demo households")
    prior = existing_observation(hh, f"{hh}_synthcam", evt["event_id"])
    if prior:
        return prior["id"]
    img = await run_in_threadpool(FX.scene, scene_name, **(a.get("render") or {}))
    o = await run_in_threadpool(svc().ingest, hh, f"{hh}_synthcam", img, "SYNTHETIC_RENDER", "CUSTOM_EVENT_SIMULATOR",
                                float(a["captured_at"]) if a.get("captured_at") else None, evt["event_id"], None,
                                {"simulated_event": {"type": evt["type"], "sub_type": evt["sub_type"], "fixture": scene_name}})
    return o["id"]


SCENES = {
    "reference": {}, "initial": {}, "partial_move": {}, "moved_to_zone": {}, "centre_block": {},
    "initial_lowlight": {"base": "initial", "render": {"gain": 0.14, "noise": 6}},
    "initial_blurry": {"base": "initial", "render": {"blur_sigma": 7}},
    "obscured": {"base": "initial", "render": {"obscure": 0.55}},
    "camera_moved": {"base": "initial", "render": {"camera": "moved"}},
}


async def handle_sim_webhook(body: bytes, sig: str | None, bg: BackgroundTasks | None = None) -> dict:
    if not WH.verify(settings.simulator_secret, body, sig):
        svc().metric("webhook_signature", False, None, "simulator")
        raise HTTPException(401, "invalid signature")
    evt = parse_or_400(body)
    hh = evt["raw"].get("data", {}).get("attributes", {}).get("clearway_household")
    if hh and not svc().is_demo(hh):
        raise HTTPException(403, "simulator events may only target demo households")
    if evt["type"] not in WH.RELEVANT_TYPES:
        process, disposition, why = False, "FILTERED_IRRELEVANT_TYPE", f"event type '{evt['type']}' not relevant to route analysis"
    elif not hh:
        process, disposition, why = False, "REJECTED_NO_TARGET", "simulator event without clearway_household"
    else:  # simulator events target a demo household directly; no Ring device identity is involved
        process, disposition, why = True, "PROCESS", f"process (sub_type={evt['sub_type']})"
    state = record_event("simulator", evt, body, process, disposition, why, hh)
    if state == "duplicate":
        if hh:
            svc().db.append_ledger(hh, "DUPLICATE_EVENT_IGNORED", {"event_id": evt["event_id"], "source": "CUSTOM_EVENT_SIMULATOR"})
        return {"ok": True, "duplicate": True}
    svc().metric("stage0", process, None, why)
    ok = None
    if process:
        key = f"simulator:{evt['event_id']}"
        if bg is not None:
            bg.add_task(run_event, key, process_sim_event, evt, hh)
        else:
            ok = await run_event(key, process_sim_event, evt, hh, backoff=(0.05, 0.1))
    return {"ok": True, "processing": process, "reason": why, "event_id": evt["event_id"], "retry": state == "retry",
            **({"completed": ok} if ok is not None else {})}


@app.post("/webhooks/simulator")
async def sim_webhook(request: Request, bg: BackgroundTasks):
    return await handle_sim_webhook(await read_webhook_body(request), request.headers.get("X-Signature"), bg)


class EmitIn(BaseModel):
    scene: str
    sub_type: str = "package_delivery"
    captured_offset_s: float | None = None
    event_id: str | None = None
    type: str = "motion_detected"
    render_seed: int | None = None


def build_sim_event(hh: str, body: EmitIn) -> bytes:
    spec = SCENES.get(body.scene)
    if spec is None:
        raise HTTPException(422, f"unknown scene; choose from {sorted(SCENES)}")
    render = dict(spec.get("render", {}))
    if render.get("camera") == "moved":
        render["camera"] = None
        render["_moved"] = True
    if body.render_seed is not None:
        render["seed"] = int(body.render_seed)
    # Ring v1.1 envelope (meta + data.attributes.source/source_type) plus ClearWay-only "clearway_*" extension
    # attributes that tell the simulator what to render. Signed with the SIMULATOR key and posted to
    # /webhooks/simulator, so it can never be accepted as a Ring event.
    ts_ms = int(time.time() * 1000)
    attrs = {"source": f"sim-device-{hh}", "source_type": "devices", "timestamp": ts_ms, "sub_type": body.sub_type,
             "component_ids": ["0"], "clearway_household": hh, "clearway_fixture": spec.get("base", body.scene),
             "clearway_scene": body.scene, "render": render}
    if body.captured_offset_s is not None:
        attrs["captured_at"] = time.time() + body.captured_offset_s  # explicitly asserted (CLIENT_ASSERTED) time
    payload = {"meta": {"version": "1.1", "account_id": "clearway-simulator", "request_id": str(uuid.uuid4()),
                        "sender": "CUSTOM_EVENT_SIMULATOR"},
               "data": {"id": body.event_id or f"sim-{uuid.uuid4()}", "type": body.type, "attributes": attrs}}
    return json.dumps(payload).encode()


@app.post("/api/households/{hh}/simulator/emit")
async def emit(body: EmitIn, hh: str = Depends(household)):
    """Signs a Ring-shaped event with the SIMULATOR key and delivers it through the same
    verification path as /webhooks/simulator (synchronously). Demo households only."""
    if not svc().is_demo(hh):
        raise HTTPException(403, "the simulator is only available for demo households")
    raw = build_sim_event(hh, body)
    res = await handle_sim_webhook(raw, WH.sign(settings.simulator_secret, raw))
    last = svc().db.one("SELECT id FROM observations WHERE household_id=? AND event_id=?", (hh, res.get("event_id")))
    res["observation"] = svc().observation(hh, last["id"]) if last else None
    return res


# ---------------------------------------------------------------- frontend
DIST = Path(os.getenv("CLEARWAY_FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "webhooks/")):
            raise HTTPException(404)
        f = (DIST / path).resolve()
        return FileResponse(f if path and f.is_file() and DIST.resolve() in f.parents else DIST / "index.html")
