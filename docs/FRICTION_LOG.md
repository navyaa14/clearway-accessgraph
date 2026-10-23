# Friction log (genuine, with reproduction steps)

Environment: Linux container, Python 3.13, aiortc 1.15, no Ring credentials available in the build environment.

### F1 — No Ring Playground credentials in the build environment (BLOCKER for the live acceptance test)
* **What happened:** The build sandbox had no Ring developer account, tokens or device id, so a genuine Ring Playground WHEP session could not be opened during development.
* **Repro:** `curl -s localhost:8000/api/ring/devices` → `503 {"detail":"Ring is not configured: set RING_ACCESS_TOKEN …"}`.
* **Mitigation:** The adapter implements only documented endpoints and was exercised end-to-end against a local server that implements the documented WHEP contract (`tests/whep_test_server.py`, real WebRTC/VP8 frames through aiortc → pipeline). This proves protocol handling, **not** Ring compatibility. To complete the acceptance test: put Playground credentials in `.env`, restart, authorise the Ring camera, capture a reference, mark the route, press *Capture live frame & analyse*.

### F2 — Webhook v1.1 payload schema hard to obtain in full (led to F20)
* **Repro:** fetch the API reference; the page is ~120 k characters and the payload example sits deep inside "Notifications".
* **Impact:** We could confirm `X-Signature` (`sha256=` + hex HMAC-SHA256), the event `type` values and `data.attributes.sub_type` / `component_ids`, but not every field. `app/ring/webhook.py::parse_event` therefore reads documented fields with fallbacks.

### F3 — Snapshot request body not shown
* **Repro:** API reference → Image Snapshots: endpoint and `components` array are named, no example body.
* **Impact:** `RingClient.snapshot` sends `{}` or `{"components":[N]}`. Unverified.

### F4 — Ring watermark vs change detection
* **Repro:** in `tests/test_vision.py::test_ring_watermark_regions_ignored_by_change_detection`, invert the top-right strip of a frame; without the ignore mask the changing timestamp becomes an "object".
* **Fix:** fixed ignore strips for change detection only; displayed media is never cropped or covered.

### F5 — Synthetic renders initially used screen-anchored noise
* **Repro (historic):** with screen-space texture, ORB landmark matching found only 8 matches after a 3° camera yaw and reported `UNKNOWN` instead of `DISPLACED`; sensor noise also masked an obscured lens.
* **Fix:** world-anchored floor textures in `app/vision/synth.py`; noise-robust focus/texture measures (320 px, contrast-normalised); an `UNCONFIRMED` calibration state when a feature-rich reference can no longer be matched.

### F6 — Elevated vertical edges read as floor contact
* **Repro (historic):** the right vertical edge of a box became part of the "contact" line, inflating footprint width by ~13 cm.
* **Fix:** recover the vertical vanishing point from the floor homography (solve focal length from r1 ⟂ r2) and trim end segments that point at it. Recovered VP matched the renderer's true nadir to 0.0001 px.

### F7 — Frame-edge truncation produced false confidence
* **Repro (historic):** a parcel cut off by the bottom frame edge produced a tiny trimmed footprint and `NO_OBSTRUCTION_OBSERVED` with p=0.01.
* **Fix:** if the contact edge is unreliable and the silhouette touches the route at all, the result is `REVIEW_REQUIRED` regardless of p (scenario 9 test).

### F8 — FastAPI `on_event` deprecation
* Startup used `@app.on_event("startup")` (DeprecationWarning on FastAPI 0.159). **Fixed:** migrated to a lifespan handler.

## Pre-submission audit findings (fixed, with regression tests in `backend/tests/test_hardening.py`)

### F9 — Replay footage could close an incident
* **Repro (before fix):** open an incident, then upload a different `RECORDED_REPLAY` frame with `captured_at` far in the future → `NEW_EVIDENCE` → `RESOLVED`.
* **Fix:** evidence trust + chronology. Live incidents accept only server-captured Ring frames (server clock); uploads cannot attach to Ring cameras; calibration version must match; two independent clear observations to close live incidents.

### F10 — Retention purge crossed households
* **Repro (before fix):** `POST /api/households/hh_b/retention/purge?days=0` deleted household A's evidence frames.
* **Fix:** purge scoped to the authenticated household; server-wide purge only as a startup job.

### F11 — Approvals trusted a claimed `member_id`; demo tokens could reach Ring endpoints
* **Fix:** `X-Member-Token` credentials; mismatched claims refused and ledgered; Ring endpoints household-scoped and live-only; live household provisioned from server env and never listed; media/SSE via signed short-lived links (no tokens in URLs).

### F12 — "No object detected" equalled "clear"
* **Repro:** fragmented change on the route below the instance-size threshold, or no clear-scene reference, produced `NO_OBSTRUCTION_OBSERVED`. With no reference, the colour-prior fallback even flagged terracotta tiles as a parcel.
* **Fix:** detection-quality gate (unexplained route change, route detail loss), confidence gate for clear results near the route, and the no-reference fallback is advisory only (always `REVIEW_REQUIRED`).

### F13 — Self-matching `pkill -f`
* Killing the dev server with `pkill -f "uvicorn app.main"` inside a shell command that also contained that string killed the shell itself (exit 144). Use a bracketed pattern in a separate command.

### F14 — Docker in a registry-less sandbox
* **Repro:** `docker build .` → `node:22-slim: failed to resolve source metadata … Forbidden` (Docker Hub, GHCR, ECR Public, Quay and GCR all refused by the sandbox egress policy). `dockerd` itself was installed but not started.
* **Workaround:** started `dockerd`, imported the sandbox rootfs as stand-in bases, and built the unchanged Dockerfile via `NODE_IMAGE`/`PYTHON_IMAGE` build args with the proxy CA as a build secret. Compose v5 always builds via Buildx Bake, which needs `--allow=network.host --allow=fs.read=…` for that sandbox-only override.
* **Found while testing:** the unnecessary apt step, floating base tags, unpinned transitive deps and the mandatory `.env` (all fixed; see RELEASE_REPORT).

## Second audit findings (fixed; see RELEASE_REPORT "Second audit")

### F15 — Ring retries lost the SDP content type
* **Repro:** `httpx.MockTransport` returning 401 then 201 for the WHEP POST → attempt 1 `Content-Type: application/sdp`, attempt 2 `Content-Type: None` (`kw.pop("headers")` ran inside the retry loop). Fixed; `tests/test_ring_retries.py`.

### F16 — Interrupted events stuck forever
* **Repro:** kill the process while an event is `PROCESSING` → redelivery answered "duplicate" indefinitely, and after a 200 ack Ring need not redeliver. Fixed with stored payloads, leases, startup recovery and a sweeper; `tests/test_event_recovery.py`.

### F17 — Live-household deletion left Ring tokens
* **Repro:** store tokens, delete `hh_live` → `secrets.ring_tokens` still present. Fixed (unlink, clear vault, disconnect, no silent reconnect); `tests/test_privacy_deletion.py`.

### F18 — Timing-sensitive test
* A first version of the independence test used a 1 s separation, shorter than one analysis on this CPU, so the "too soon" frame was in fact > 1 s later. The test now uses 4 s. (Docker daemon also had to be restarted between sessions: background processes are reaped.)

### F19 — No route to Ring from the build environment
* `api.amazonvision.com` and `oauth.ring.com` are refused by the sandbox egress policy, so even with credentials a Playground session could not run here. **Response:** `backend/ringemu`, a Ring API emulator built from the public docs, plus an end-to-end test suite (`tests/test_ring_emulator.py`) that runs ClearWay and the emulator as real HTTP servers. Ambiguities in the docs (webhook v1.1 body, snapshot body, WHEP codecs) are the emulator's best reading and are flagged as such.

## Third audit findings

### F20 — Our v1.1 webhook parsing was wrong, and our own emulator hid it
* **What happened:** because the full v1.1 example was hard to reach (F2), `parse_event` read the device from
  `attributes.device_id`. The documented v1.1 body carries it in `data.attributes.source` with `source_type: "devices"`
  (event id in `data.id`, idempotency key in `meta.request_id`, timestamp in epoch ms). Our emulator was written from
  the same misreading, so the end-to-end emulator tests passed while a genuine Ring event would have been dropped.
* **Repro (v2.2.0):** sign the documented body with the webhook secret, POST to `/webhooks/ring` → 200, `device_id None`,
  event `COMPLETED`, `observation_id None`, 0 observations.
* **Fix:** v1.1 parser (legacy fallback only without `meta`), emulator emits the exact documented shape, no
  `COMPLETED` without an observation, explicit dispositions, `tests/test_ring_webhook_v11.py`.
* **Lesson:** an emulator written by the same team from the same reading of the docs cannot validate that reading.
  The field locations were confirmed from the documentation's statement about `meta.request_id` and two independent
  public implementations; the payload section of the API reference itself could not be fetched in full from here.

### F21 — No licensed real photographs available
* The real-image harness is ready, but the build environment cannot download images and none with a clear licence
  or consent were supplied, so real-photo accuracy remains unmeasured.
