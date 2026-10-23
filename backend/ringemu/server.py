"""ClearWay Ring API EMULATOR — a local, no-cost stand-in for the Ring Partner API / Developer Playground.

It implements the *documented* contract (developer.amazon.com/docs/ring/api-documentation.html, live-video.html):
  POST /oauth/token                                   refresh_token grant; rotating refresh tokens; access-token expiry
  GET  /v1/users/me
  GET  /v1/devices[?include=status,capabilities]      JSON:API
  GET  /v1/devices/{id} | /status | /capabilities
  POST /v1/devices/{id}/media/streaming/whep/sessions  application/sdp offer -> 201 + Location + SDP answer (WebRTC video)
  DELETE <Location>                                   closes the session
  POST /v1/devices/{id}/media/image/download          JPEG snapshot
  DELETE /v1/accounts/me/app-integrations             partner-side unlink
  Webhooks: signed POSTs (X-Signature: sha256=<hex HMAC-SHA256>) of motion_detected events to ClearWay
Behaviour mirrored from the docs: Bearer auth (401), missing User-Agent / Hardware_Id -> 406, audio m-line rejected,
battery-device session limit (~30 s), JSON:API error bodies, 404 / 503 (device offline).

It is NOT Ring. Everything it produces is stamped "RING API EMULATOR" and ClearWay labels its media RING_API_EMULATOR.
The camera picture is the labelled synthetic porch scene (fixtures), switchable through /emulator/* control endpoints.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field

import cv2
import httpx
import numpy as np
from fastapi import FastAPI, Header, HTTPException, Request, Response

DEVICE_ID = os.getenv("RINGEMU_DEVICE_ID", "emu-front-door-1")
ACCOUNT_ID = "emu-account-1"
CLIENT_ID = os.getenv("RINGEMU_CLIENT_ID", "clearway-emulator-client")
CLIENT_SECRET = os.getenv("RINGEMU_CLIENT_SECRET", "clearway-emulator-secret")
INITIAL_REFRESH = os.getenv("RINGEMU_REFRESH_TOKEN", "emu-refresh-initial")
TOKEN_TTL_S = float(os.getenv("RINGEMU_ACCESS_TOKEN_TTL_S", "3600"))
SESSION_LIMIT_S = float(os.getenv("RINGEMU_SESSION_LIMIT_S", "30"))  # battery devices: ~30 s per docs
WEBHOOK_URL = os.getenv("RINGEMU_WEBHOOK_URL", "http://127.0.0.1:8000/webhooks/ring")
WEBHOOK_SECRET = os.getenv("RINGEMU_WEBHOOK_SECRET", os.getenv("RING_WEBHOOK_SECRET", "clearway-emulator-webhook-secret"))
CONTROL_TOKEN = os.getenv("RINGEMU_CONTROL_TOKEN", "clearway-emulator-control")
FPS = float(os.getenv("RINGEMU_FPS", "10"))


def jsonapi_error(status: int, title: str, detail: str = "") -> Response:
    body = {"errors": [{"status": str(status), "title": title, **({"detail": detail} if detail else {})}]}
    return Response(status_code=status, content=json.dumps(body), media_type="application/vnd.api+json")


@dataclass
class State:
    access_tokens: dict = field(default_factory=dict)  # token -> expiry
    refresh_token: str = INITIAL_REFRESH
    scene: str = "reference"
    online: bool = True
    linked: bool = True
    sessions: dict = field(default_factory=dict)  # sid -> {pc, started, closed}
    events_sent: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    frame_cache: dict = field(default_factory=dict)

    def issue(self) -> str:
        t = "emu-at-" + secrets.token_urlsafe(18)
        self.access_tokens[t] = time.time() + TOKEN_TTL_S
        return t


S = State()
SCENES = ["reference", "initial", "partial_move", "moved_to_zone", "centre_block"]


def base_frame(scene: str) -> np.ndarray:
    if scene not in S.frame_cache:
        from app.fixtures import scene as render_scene  # labelled synthetic porch (same camera geometry as the demo)
        S.frame_cache[scene] = render_scene(scene).astype(np.int16)
    return S.frame_cache[scene]


def camera_frame(rng: np.random.Generator | None = None) -> np.ndarray:
    """Current 'camera' picture: synthetic scene + sensor noise + an emulator watermark in the regions Ring uses
    (logo top-left; device id, app name and timestamp top-right). Never imitates Ring's logo."""
    rng = rng or np.random.default_rng()
    img = np.clip(base_frame(S.scene) + rng.normal(0, 2.0, base_frame(S.scene).shape), 0, 255).astype(np.uint8)
    h, w = img.shape[:2]
    cv2.rectangle(img, (6, 6), (int(0.21 * w), int(0.07 * h)), (40, 40, 40), -1)
    cv2.putText(img, "RING API EMULATOR", (12, int(0.05 * h)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    stamp = f"{DEVICE_ID} | ClearWay | {time.strftime('%Y-%m-%d %H:%M:%S')}"
    (tw, _), _ = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
    x0 = max(int(0.56 * w), w - tw - 16)
    cv2.rectangle(img, (x0 - 6, 6), (w - 6, int(0.07 * h)), (40, 40, 40), -1)
    cv2.putText(img, stamp, (x0, int(0.05 * h)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def make_app() -> FastAPI:
    app = FastAPI(title="ClearWay Ring API emulator (NOT Ring)")

    def log(req: Request):
        S.requests.append({"t": time.time(), "method": req.method, "path": req.url.path,
                           "ua": req.headers.get("user-agent"), "hw": req.headers.get("hardware_id")})
        S.requests[:] = S.requests[-200:]

    def require(req: Request) -> Response | None:
        log(req)
        if not req.headers.get("user-agent") or not req.headers.get("hardware_id"):
            return jsonapi_error(406, "Not Acceptable", "missing required User-Agent or Hardware_Id header")
        auth = req.headers.get("authorization", "")
        tok = auth[7:] if auth.startswith("Bearer ") else None
        if not tok or tok not in S.access_tokens:
            return jsonapi_error(401, "Invalid Client")
        if S.access_tokens[tok] < time.time():
            return jsonapi_error(401, "Invalid Client", "access token expired")
        if not S.linked:
            return jsonapi_error(401, "Invalid Client", "app integration was unlinked")
        return None

    def device_or_error(device_id: str) -> Response | None:
        if device_id != DEVICE_ID:
            return jsonapi_error(404, "Not Found")
        if not S.online:
            return jsonapi_error(503, "Device Unavailable")
        return None

    @app.post("/oauth/token")
    async def token(req: Request):
        log(req)
        form = await req.form()
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            return Response(status_code=401, content=b'{"error":"invalid_client"}', media_type="application/json")
        if form.get("grant_type") != "refresh_token" or form.get("refresh_token") != S.refresh_token or not S.linked:
            return Response(status_code=400, content=b'{"error":"invalid_grant"}', media_type="application/json")
        S.refresh_token = "emu-rt-" + secrets.token_urlsafe(18)  # each refresh invalidates the previous refresh token
        return {"access_token": S.issue(), "refresh_token": S.refresh_token, "scope": "ava.v1:read",
                "expires_in": int(TOKEN_TTL_S), "token_type": "Bearer"}

    def device_doc(include: str) -> dict:
        rel = {}
        inc = set((include or "").split(","))
        attrs = {"name": "Front door (emulated)", "kind": "doorbell", "power_source": "battery"}
        doc = {"id": DEVICE_ID, "type": "devices", "attributes": attrs}
        if "status" in inc:
            rel["status"] = {"online": S.online}
        if "capabilities" in inc:
            rel["capabilities"] = {"video": {"live": True, "whep": True, "snapshot": True}, "audio": {"live": False}}
        if rel:
            doc["attributes"] = {**attrs, **rel}
        return doc

    @app.get("/v1/users/me")
    async def me(req: Request):
        if (e := require(req)):
            return e
        return {"data": {"id": ACCOUNT_ID, "type": "users", "attributes": {"note": "emulated account"}}}

    @app.get("/v1/devices")
    async def devices(req: Request, include: str = ""):
        if (e := require(req)):
            return e
        return {"data": [device_doc(include)]}

    @app.get("/v1/devices/{device_id}")
    async def device(device_id: str, req: Request, include: str = ""):
        if (e := require(req)) or (e := (jsonapi_error(404, "Not Found") if device_id != DEVICE_ID else None)):
            return e
        return {"data": device_doc(include)}

    @app.get("/v1/devices/{device_id}/status")
    async def status(device_id: str, req: Request):
        if (e := require(req)) or (e := (jsonapi_error(404, "Not Found") if device_id != DEVICE_ID else None)):
            return e
        return {"data": {"id": device_id, "type": "status", "attributes": {"online": S.online}}}

    @app.get("/v1/devices/{device_id}/capabilities")
    async def capabilities(device_id: str, req: Request):
        if (e := require(req)) or (e := (jsonapi_error(404, "Not Found") if device_id != DEVICE_ID else None)):
            return e
        return {"data": {"id": device_id, "type": "capabilities", "attributes": {"video_live": True, "snapshot": True}}}

    @app.post("/v1/devices/{device_id}/media/image/download")
    async def snapshot(device_id: str, req: Request):
        if (e := require(req)) or (e := device_or_error(device_id)):
            return e
        ok, jpg = cv2.imencode(".jpg", camera_frame(), [cv2.IMWRITE_JPEG_QUALITY, 90])
        return Response(content=jpg.tobytes(), media_type="image/jpeg")

    @app.post("/v1/devices/{device_id}/media/streaming/whep/sessions")
    async def whep(device_id: str, req: Request):
        if (e := require(req)) or (e := device_or_error(device_id)):
            return e
        if req.headers.get("content-type", "").split(";")[0].strip() != "application/sdp":
            return jsonapi_error(415, "Unsupported Media Type", "WHEP offers must be application/sdp")
        offer = (await req.body()).decode(errors="replace")
        if "m=audio" in offer:
            return jsonapi_error(400, "Bad Request", "Incompatible send direction: audio is not supported (video only)")
        if "m=video" not in offer:
            return jsonapi_error(400, "Bad Request", "SDP offer must contain one recvonly video m-line")
        from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
        from av import VideoFrame

        class Camera(VideoStreamTrack):
            def __init__(self):
                super().__init__()
                self.rng = np.random.default_rng()

            async def recv(self):
                pts, tb = await self.next_timestamp()
                f = VideoFrame.from_ndarray(camera_frame(self.rng), format="bgr24")
                f.pts, f.time_base = pts, tb
                return f

        pc = RTCPeerConnection()
        pc.addTrack(Camera())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        sid = str(uuid.uuid4())
        S.sessions[sid] = {"pc": pc, "started": time.time(), "closed": None, "reason": None, "scene": S.scene}

        async def limit():  # documented: battery devices end the session automatically (~30 s)
            await asyncio.sleep(SESSION_LIMIT_S)
            s = S.sessions.get(sid)
            if s and not s["closed"]:
                await s["pc"].close()
                s.update(closed=time.time(), reason="session time limit")
        asyncio.get_running_loop().create_task(limit())
        return Response(status_code=201, content=pc.localDescription.sdp, media_type="application/sdp",
                        headers={"Location": f"/v1/devices/{device_id}/media/streaming/whep/sessions/{sid}"})

    @app.delete("/v1/devices/{device_id}/media/streaming/whep/sessions/{sid}")
    async def close(device_id: str, sid: str, req: Request):
        if (e := require(req)):
            return e
        s = S.sessions.get(sid)
        if not s:
            return jsonapi_error(404, "Not Found")
        if not s["closed"]:
            await s["pc"].close()
            s.update(closed=time.time(), reason="DELETE by client")
        return Response(status_code=204)

    @app.delete("/v1/accounts/me/app-integrations")
    async def unlink(req: Request):
        if (e := require(req)):
            return e
        S.linked = False
        S.access_tokens.clear()
        return Response(status_code=204)

    # ------------------------------------------------------------------ emulator control (not part of Ring's API)
    def control(token: str | None):
        if not hmac.compare_digest(token or "", CONTROL_TOKEN):
            raise HTTPException(401, "emulator control token required (X-Emulator-Token)")

    @app.get("/emulator/state")
    async def state(x_emulator_token: str | None = Header(None)):
        control(x_emulator_token)
        return {"emulator": True, "not_ring": True, "device_id": DEVICE_ID, "scene": S.scene, "scenes": SCENES,
                "online": S.online, "linked": S.linked, "session_limit_s": SESSION_LIMIT_S,
                "sessions": [{"id": k, "started": v["started"], "closed": v["closed"], "reason": v["reason"], "scene": v["scene"]}
                             for k, v in list(S.sessions.items())[-20:]],
                "events_sent": S.events_sent[-20:], "recent_requests": S.requests[-30:]}

    @app.post("/emulator/scene")
    async def set_scene(req: Request, x_emulator_token: str | None = Header(None)):
        control(x_emulator_token)
        body = await req.json()
        scene = body.get("scene")
        if scene not in SCENES:
            raise HTTPException(422, f"scene must be one of {SCENES}")
        S.scene = scene
        out = {"scene": S.scene}
        if body.get("fire_event", True):
            out["event"] = await fire_event(body.get("sub_type", "package_delivery"))
        return out

    @app.post("/emulator/online")
    async def set_online(req: Request, x_emulator_token: str | None = Header(None)):
        control(x_emulator_token)
        S.online = bool((await req.json()).get("online", True))
        return {"online": S.online}

    @app.post("/emulator/relink")
    async def relink(x_emulator_token: str | None = Header(None)):
        control(x_emulator_token)
        S.linked, S.refresh_token = True, INITIAL_REFRESH
        return {"linked": True, "refresh_token_reset": True}

    @app.post("/emulator/event")
    async def event(req: Request, x_emulator_token: str | None = Header(None)):
        control(x_emulator_token)
        body = await req.json() if (await req.body()) else {}
        return await fire_event(body.get("sub_type", "package_delivery"), body.get("event_id"), body.get("request_id"))

    async def fire_event(sub_type: str, event_id: str | None = None, request_id: str | None = None) -> dict:
        """Emits the documented Ring webhook v1.1 shape exactly: the device is data.attributes.source with
        source_type "devices"; data.id is the event id; meta.request_id is the idempotency key; timestamp is
        epoch milliseconds. Deliberately NO attributes.device_id and NO emulator marker inside the payload:
        the receiver must not need anything a genuine Ring event would not carry. (Media provenance is
        established by ClearWay from the API host it captured from, not from the webhook body.)"""
        ts_ms = int(time.time() * 1000)
        payload = {"meta": {"version": "1.1", "account_id": ACCOUNT_ID, "request_id": request_id or str(uuid.uuid4())},
                   "data": {"id": event_id or f"{DEVICE_ID}_{sub_type}_{ts_ms}", "type": "motion_detected",
                            "attributes": {"source": DEVICE_ID, "source_type": "devices", "timestamp": ts_ms,
                                           "sub_type": sub_type, "component_ids": ["0"]},
                            "relationships": {"devices": {"links": {"self": f"/v1/devices/{DEVICE_ID}"},
                                                          "data": [{"type": "devices", "id": DEVICE_ID}]}}}}
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        rec = {"event_id": payload["data"]["id"], "request_id": payload["meta"]["request_id"], "sub_type": sub_type, "scene": S.scene, "t": time.time()}
        try:
            async with httpx.AsyncClient(timeout=6.0, trust_env=False) as c:
                r = await c.post(WEBHOOK_URL, content=body, headers={"Content-Type": "application/json", "X-Signature": sig})
            rec.update(status=r.status_code, response=r.text[:300])
        except Exception as e:  # ClearWay down: record, like a real sender would
            rec.update(status=None, error=f"{type(e).__name__}: {e}"[:200])
        S.events_sent.append(rec)
        return rec

    return app


app = make_app()
