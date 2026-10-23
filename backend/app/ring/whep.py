"""Ring WHEP live-video capture (server-side WebRTC receiver via aiortc).

Documented flow (developer.amazon.com/docs/ring/live-video.html):
  * one video transceiver, direction recvonly; NO audio transceiver
  * wait for ICE gathering to complete, then POST the SDP offer
    (Content-Type: application/sdp) to
    /v1/devices/{device_id}/media/streaming/whep/sessions[?component_id=N]
  * 201 Created; body = SDP answer; Location header = session URL
  * battery devices end after ~30 s, line-powered after ~60 s
  * DELETE the Location URL when finished (preserves device battery)

Frames received from the genuine stream are sampled (a few fps), quality
scored, and handed to the analysis pipeline. Media is never written to disk
as video; only the selected evidence frame is retained.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urljoin

import numpy as np

from .client import RingClient, RingError

SESSIONS: dict[str, "SessionInfo"] = {}


@dataclass
class SessionInfo:
    id: str
    device_id: str
    state: str = "CREATED"
    location: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    frames_received: int = 0
    frames_sampled: int = 0
    error: str | None = None
    resolution: tuple | None = None
    household_id: str | None = None  # sessions are only ever shown to the household that opened them
    log: list = field(default_factory=list)

    def note(self, msg: str):
        self.log.append({"t": round(time.time() - self.started_at, 3), "msg": msg})

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


async def capture_frames(client: RingClient, device_id: str, *, component_id: str | None = None, household_id: str | None = None,
                         max_seconds: float = 12.0, sample_fps: float = 2.0, max_frames: int = 12,
                         first_frame_timeout: float = 15.0) -> tuple[list[np.ndarray], SessionInfo]:
    from aiortc import RTCPeerConnection, RTCSessionDescription  # imported lazily (heavy)

    info = SessionInfo(id=str(uuid.uuid4()), device_id=device_id, household_id=household_id)
    SESSIONS[info.id] = info
    pc = RTCPeerConnection()
    frames: list[np.ndarray] = []
    track_ready: asyncio.Future = asyncio.get_event_loop().create_future()

    @pc.on("track")
    def on_track(track):
        if track.kind == "video" and not track_ready.done():
            track_ready.set_result(track)

    location = None
    try:
        pc.addTransceiver("video", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)  # aiortc completes ICE gathering here (non-trickle)
        info.state = "OFFER_SENT"
        info.note("SDP offer created (recvonly video, ICE gathering complete)")
        path = f"/v1/devices/{device_id}/media/streaming/whep/sessions"
        params = {"component_id": component_id} if component_id else None
        r = await client.request("POST", path, params=params, content=pc.localDescription.sdp.encode(),
                                 headers={"Content-Type": "application/sdp"}, retries=1)
        if r.status_code != 201:
            raise RingError(r.status_code, "WHEP session not created (expected 201)")
        location = r.headers.get("Location")
        info.location = location
        info.note(f"201 Created; session at {location}")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=r.text, type="answer"))
        info.state = "CONNECTING"
        track = await asyncio.wait_for(track_ready, timeout=first_frame_timeout)
        info.state = "STREAMING"
        deadline = time.time() + max_seconds
        interval = 1.0 / sample_fps
        next_sample = 0.0
        while time.time() < deadline and len(frames) < max_frames:
            try:
                frame = await asyncio.wait_for(track.recv(), timeout=first_frame_timeout if info.frames_received == 0 else 5.0)
            except asyncio.TimeoutError:
                if info.frames_received == 0:
                    info.error = "no decodable video received (device may use TAKE/E2EE encryption or stream unavailable)"
                    info.state = "NO_DECODABLE_MEDIA"
                else:
                    info.note("stream ended (device session limit reached)")
                break
            info.frames_received += 1
            now = time.time()
            if now >= next_sample:
                img = frame.to_ndarray(format="bgr24")
                info.resolution = (img.shape[1], img.shape[0])
                frames.append(img)
                info.frames_sampled += 1
                next_sample = now + interval
        if frames:
            info.state = "COMPLETED"
            info.note(f"received {info.frames_received} frames, sampled {info.frames_sampled}")
    except RingError as e:
        info.state, info.error = "FAILED", e.user_message
        info.note(f"Ring error {e.status}: {e.message}")
        raise
    except asyncio.TimeoutError:
        info.state, info.error = "FAILED", "WebRTC connection timed out before a video track arrived"
        info.note(info.error)
    finally:
        try:
            await pc.close()
        except Exception:
            pass
        if location:
            try:
                url = location if location.startswith("http") else urljoin(client.s.ring_api_base, location)
                await client.request("DELETE", url, retries=0)
                info.note("session DELETEd")
            except Exception as e:  # session may have already expired on the device side
                info.note(f"DELETE failed/ignored: {e}")
        info.ended_at = time.time()
    return frames, info
