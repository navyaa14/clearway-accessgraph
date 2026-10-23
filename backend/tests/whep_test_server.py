"""TEST HARNESS ONLY: a local server that speaks the documented Ring WHEP contract
(POST SDP -> 201 + Location + SDP answer; DELETE session) and the documented REST
shapes, so the Ring adapter can be exercised without credentials.
It streams SYNTHETIC frames. It is never used by the app at runtime and its
output is never labelled as Ring footage.
"""
from __future__ import annotations

import asyncio
import fractions
import time
import uuid

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
from fastapi import FastAPI, Request, Response

VALID_TOKEN = "test-access-token"
STATE = {"sessions": {}, "deleted": [], "requests": [], "refreshes": 0, "offline": set()}


class SyntheticTrack(VideoStreamTrack):
    def __init__(self, frames: list[np.ndarray]):
        super().__init__()
        self.frames = frames
        self.i = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        img = self.frames[self.i % len(self.frames)]
        self.i += 1
        f = VideoFrame.from_ndarray(img, format="bgr24")
        f.pts, f.time_base = pts, time_base
        return f


def make_app(frames: list[np.ndarray], expire_token: bool = False) -> FastAPI:
    app = FastAPI()
    tok = {"value": VALID_TOKEN if not expire_token else "expired"}

    def authed(req: Request):
        STATE["requests"].append({"path": req.url.path, "method": req.method, "headers": dict(req.headers)})
        return req.headers.get("authorization") == f"Bearer {VALID_TOKEN}" and tok["value"] == VALID_TOKEN

    @app.post("/oauth/token")
    async def token(req: Request):
        form = await req.form()
        if form.get("grant_type") != "refresh_token":
            return Response(status_code=400)
        STATE["refreshes"] += 1
        tok["value"] = VALID_TOKEN
        return {"access_token": VALID_TOKEN, "refresh_token": "rotated-" + str(uuid.uuid4()), "expires_in": 14400, "token_type": "Bearer", "scope": "ava.v1:read"}

    @app.get("/v1/devices")
    async def devices(req: Request):
        if not authed(req):
            return Response(status_code=401, content=b'{"errors":[{"title":"Invalid Client"}]}')
        return {"data": [{"id": "dev-test-1", "type": "devices", "attributes": {"name": "Test Doorbell"}}]}

    @app.post("/v1/devices/{device_id}/media/streaming/whep/sessions")
    async def whep(device_id: str, req: Request):
        if not authed(req):
            return Response(status_code=401, content=b'{"errors":[{"title":"Invalid Client"}]}')
        if device_id in STATE["offline"]:
            return Response(status_code=503, content=b'{"errors":[{"title":"Device Unavailable"}]}')
        if device_id != "dev-test-1":
            return Response(status_code=404)
        if req.headers.get("content-type") != "application/sdp":
            return Response(status_code=415)
        offer = (await req.body()).decode()
        assert "m=audio" not in offer, "Ring rejects audio transceivers"
        pc = RTCPeerConnection()
        pc.addTrack(SyntheticTrack(frames))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
        ans = await pc.createAnswer()
        await pc.setLocalDescription(ans)
        sid = str(uuid.uuid4())
        STATE["sessions"][sid] = pc
        return Response(status_code=201, content=pc.localDescription.sdp, media_type="application/sdp",
                        headers={"Location": f"/v1/devices/{device_id}/media/streaming/whep/sessions/{sid}"})

    @app.delete("/v1/devices/{device_id}/media/streaming/whep/sessions/{sid}")
    async def close(device_id: str, sid: str, req: Request):
        if not authed(req):
            return Response(status_code=401)
        pc = STATE["sessions"].pop(sid, None)
        if pc:
            await pc.close()
        STATE["deleted"].append(sid)
        return Response(status_code=204)

    return app
