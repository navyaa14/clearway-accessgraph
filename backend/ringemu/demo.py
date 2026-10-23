"""End-to-end run of ClearWay's LIVE Ring code path against the local Ring API emulator (NOT Ring).

    python -m ringemu.demo              # set up + full storyline
    python -m ringemu.demo --setup-only # authorise camera, capture reference over WHEP, calibrate; then stop

Uses only ClearWay's public API with the live-household credentials (as a user would), plus ClearWay's
emulator passthrough to change what the emulated camera sees. Every event reaches ClearWay as a signed
motion_detected webhook; every frame is captured by ClearWay over WHEP. Nothing is injected directly.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

BANNER = "=" * 78 + "\nRING API EMULATOR RUN — the Ring Partner API protocol is real; the camera and Ring backend are emulated.\n" + "=" * 78


class Client:
    def __init__(self, base: str, hh: str, token: str, approver: str):
        self.c = httpx.Client(base_url=base, timeout=90.0, trust_env=False)
        self.hh, self.h = hh, {"X-Household-Token": token}
        self.ha = {**self.h, "X-Member-Token": approver}

    def get(self, p, **k):
        r = self.c.get(f"/api/households/{self.hh}{p}", headers=self.h, **k)
        r.raise_for_status()
        return r.json()

    def post(self, p, json=None, approver=False):
        r = self.c.post(f"/api/households/{self.hh}{p}", headers=self.ha if approver else self.h, json=json)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {p} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def put(self, p, json):
        r = self.c.put(f"/api/households/{self.hh}{p}", headers=self.h, json=json)
        if r.status_code >= 400:
            raise RuntimeError(f"PUT {p} -> {r.status_code}: {r.text[:300]}")
        return r.json()


def step(msg):
    print(f"\n▶ {msg}", flush=True)


def wait_for(fn, pred, what, timeout=120):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if pred(last):
            return last
        time.sleep(1.0)
    raise TimeoutError(f"timed out waiting for {what}; last={last}")


def latest_obs(cl):
    obs = cl.get("/observations?limit=1")
    return obs[0] if obs else None


def show_obs(o):
    a = o["analysis"]
    ring = (a.get("provenance") or {}).get("ring") or {}
    sess = (ring.get("whep_session") or {})
    print(f"   source={o['media_source']} trust={o['trust']} chronology={o['chronology']} capture_session={o.get('capture_session')}")
    print(f"   WHEP: state={sess.get('state')} frames={sess.get('frames_received')} "
          f"log={[x['msg'][:40] for x in sess.get('log', [])]}")
    print(f"   → {o['classification']}: {a.get('explanation', '')[:150]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("CLEARWAY_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--household", default="hh_live")
    ap.add_argument("--token", default=os.getenv("CLEARWAY_LIVE_HOUSEHOLD_TOKEN"))
    ap.add_argument("--approver", default=os.getenv("CLEARWAY_LIVE_APPROVER_TOKEN"))
    ap.add_argument("--setup-only", action="store_true")
    a = ap.parse_args()
    if not a.token or not a.approver:
        sys.exit("set CLEARWAY_LIVE_HOUSEHOLD_TOKEN and CLEARWAY_LIVE_APPROVER_TOKEN (or --token/--approver)")
    print(BANNER)
    cl = Client(a.base, a.household, a.token, a.approver)
    wait_for(lambda: httpx.get(f"{a.base}/api/health", timeout=5, trust_env=False).status_code, lambda s: s == 200, "ClearWay", 90)
    st = httpx.get(f"{a.base}/api/ring/status", trust_env=False).json()
    if not st.get("emulator"):
        sys.exit(f"ClearWay is not connected to the Ring API emulator: {st}")
    cam = f"{a.household}_ring"

    step("Device discovery over the Ring API (GET /v1/devices)")
    devs = cl.get("/ring/devices")
    print("   devices:", [(d["id"], d["attributes"].get("name")) for d in devs])

    step("Household approver authorises camera access")
    cl.post(f"/cameras/{cam}/authorize", approver=True)

    step("Clear-scene reference captured over WHEP")
    cl.post("/ring/emulator/scene", {"scene": "reference", "fire_event": False})
    cl.post(f"/ring/{cam}/reference")
    from app.fixtures import FLOOR_WORLD, profile_for  # the emulated camera uses the demo porch geometry
    prof = profile_for("hh_b")
    cal = cl.put(f"/cameras/{cam}/calibration", {k: prof[k] for k in ("route", "drop_zone", "clearance_cm", "floor_points_img", "floor_points_world")})
    print(f"   calibration saved: {cal['version']} (route incl. landing, 91 cm clearance, {len(FLOOR_WORLD)} floor points)")
    if a.setup_only:
        print("\nSetup complete. Use the Ring integration page to change the emulated scene, or run without --setup-only.")
        return

    def incident():
        lst = cl.get("/incidents")
        return lst[0] if lst else None

    step("Parcel delivered on the walkway edge → emulator sends signed motion_detected webhook → ClearWay captures over WHEP")
    n0 = len(cl.get("/observations?limit=200"))
    cl.post("/ring/emulator/scene", {"scene": "initial"})
    wait_for(lambda: len(cl.get("/observations?limit=200")), lambda n: n > n0, "observation from webhook")
    show_obs(latest_obs(cl))
    inc = wait_for(incident, lambda i: i is not None, "incident")
    print(f"   incident {inc['id']} namespace={inc['namespace']} state={inc['state']}")

    step("Assistance: draft → approve (authenticated approver) → send")
    act = cl.post(f"/incidents/{inc['id']}/assistance/draft")
    cl.post(f"/actions/{act['id']}/approve", approver=True)
    cl.post(f"/actions/{act['id']}/send")
    print("   state:", incident()["state"])

    for scene, label in (("partial_move", "Correction attempt 1: parcel nudged aside"),
                         ("moved_to_zone", "Correction attempt 2: parcel moved to the delivery area")):
        step(f"{label} → webhook → WHEP capture → independent re-assessment")
        n0 = len(cl.get("/observations?limit=200"))
        cl.post("/ring/emulator/scene", {"scene": scene})
        wait_for(lambda: len(cl.get("/observations?limit=200")), lambda n: n > n0, "observation")
        show_obs(latest_obs(cl))
        print(f"   incident state: {incident()['state']} (clear streak {incident()['clear_streak']})")

    sep = float(httpx.get(f"{a.base}/api/system", trust_env=False).json()["evidence_policy"].get("live_min_clear_separation_s", 60))
    step(f"Independent confirmation: a separate WHEP session ≥ {sep:.0f} s later (policy for live incidents)")
    time.sleep(sep + 1)
    obs = cl.post(f"/ring/{cam}/capture")
    show_obs(obs)
    final = incident()
    print(f"\n✔ incident {final['id']}: {final['state']} / {final['resolution']}")
    print(f"   verification basis: {final.get('verification_basis')} (evidence origin {final.get('evidence_origin')}) — "
          "emulator captures, NOT genuine Ring evidence")
    evs = [e for e in cl.get("/events") if e["source"] == "ring"]
    print(f"   webhooks: {len(evs)} received as {sorted({e['format'] for e in evs})}; "
          f"{sum(e['status'] == 'COMPLETED' and bool(e['observation_id']) for e in evs)} produced a linked observation")
    detail = cl.get(f"/incidents/{final['id']}")
    print("   decision trail:")
    for w in detail["why"]:
        print(f"     - {w['title']}: {(w['reasons'] or [''])[0][:110]}")
    v = cl.get("/ledger/verify")
    print(f"   ledger hash chain intact: {v['intact']} ({v['entries']} entries)")
    print("\n" + BANNER)


if __name__ == "__main__":
    main()
