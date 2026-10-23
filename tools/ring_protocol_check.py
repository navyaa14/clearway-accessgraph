"""LOCAL PROTOCOL TEST — NOT RING.

Starts a local server that implements the *documented* Ring WHEP contract (SDP offer ->
201 + Location + SDP answer; DELETE to close), streams SYNTHETIC frames to ClearWay's real
WHEP client over WebRTC, runs the spatial pipeline on a received frame, and prints the
session log. It proves ClearWay's protocol handling only. It is not evidence of Ring
compatibility; that requires a real Ring Developer Playground session (see README).

  cd backend && python ../tools/ring_protocol_check.py
"""
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import uvicorn  # noqa: E402

from app.config import Settings  # noqa: E402
from app.fixtures import profile_for, scene  # noqa: E402
from app.ring.client import RingClient  # noqa: E402
from app.ring.whep import capture_frames  # noqa: E402
from app.vision.pipeline import analyze  # noqa: E402
from tests.whep_test_server import STATE, VALID_TOKEN, make_app  # noqa: E402

BANNER = "=" * 72 + "\nLOCAL PROTOCOL TEST — synthetic frames from a local WHEP server. NOT a Ring session.\n" + "=" * 72


def main():
    print(BANNER)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(make_app([scene("initial")]), port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.1)
    cfg = Settings()
    cfg.ring_api_base, cfg.ring_access_token = f"http://127.0.0.1:{port}", VALID_TOKEN
    frames, info = asyncio.run(capture_frames(RingClient(cfg), "dev-test-1", max_seconds=4, max_frames=3))
    for line in info.log:
        print(f"  +{line['t']:6.3f}s  {line['msg']}")
    print(f"  state={info.state} frames_received={info.frames_received} sampled={len(frames)} deleted={info.location.split('/')[-1] in STATE['deleted']}")
    if frames:
        res, _ = analyze(frames[-1], profile_for("hh_b"), scene("reference"))
        print(f"  pipeline on received frame: {res['classification']} — {res['explanation'][:110]}")
    srv.should_exit = True
    print(BANNER)


if __name__ == "__main__":
    main()
