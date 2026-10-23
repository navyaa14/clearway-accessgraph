"""Send a signed, Ring-SHAPED simulator event over real HTTP.

This is ClearWay's CUSTOM_EVENT_SIMULATOR. It is signed with SIMULATOR_WEBHOOK_SECRET
(not Ring's key), posted to /webhooks/simulator (not /webhooks/ring), and every
observation it produces is labelled CUSTOM_EVENT_SIMULATOR / SYNTHETIC_RENDER.

  python tools/simulate_event.py --household hh_b --scene initial
  python tools/simulate_event.py --household hh_b --scene initial --event-id dup1   # send twice to test idempotency
  python tools/simulate_event.py --household hh_b --scene moved_to_zone --offset -600  # delayed (old) event
"""
import argparse, hashlib, hmac, json, os, time, urllib.request, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000/webhooks/simulator")
ap.add_argument("--household", default="hh_b")
ap.add_argument("--scene", default="initial", help="reference|initial|partial_move|moved_to_zone|centre_block")
ap.add_argument("--sub-type", default="package_delivery")
ap.add_argument("--event-id", default=None)
ap.add_argument("--offset", type=float, default=None, help="assert a captured_at offset in seconds (negative = old event); omitted = server receive time")
ap.add_argument("--bad-signature", action="store_true")
a = ap.parse_args()
secret = os.getenv("SIMULATOR_WEBHOOK_SECRET", "local-simulator-secret-not-ring")
ts_ms = int(time.time() * 1000)
attrs = {"source": f"sim-device-{a.household}", "source_type": "devices", "timestamp": ts_ms, "sub_type": a.sub_type,
         "component_ids": ["0"], "clearway_household": a.household, "clearway_fixture": a.scene,
         **({"captured_at": time.time() + a.offset} if a.offset is not None else {})}
# Ring webhook v1.1 envelope + ClearWay-only clearway_* extension attributes (simulator key, simulator endpoint).
body = json.dumps({"meta": {"version": "1.1", "account_id": "clearway-simulator", "request_id": str(uuid.uuid4()),
                            "sender": "CUSTOM_EVENT_SIMULATOR"},
                   "data": {"id": a.event_id or f"sim-{uuid.uuid4()}", "type": "motion_detected", "attributes": attrs}}).encode()
sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
if a.bad_signature:
    sig = "sha256=" + "0" * 64
req = urllib.request.Request(a.url, data=body, headers={"Content-Type": "application/json", "X-Signature": sig})
try:
    print(urllib.request.urlopen(req).read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
