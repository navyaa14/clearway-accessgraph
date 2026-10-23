# ClearWay AccessGraph 2.0

**Ring track · Amazon Build, Ship, Shape 2026**

> Ring cameras can detect a package. ClearWay understands whether that package obstructs the route that matters to *this* household, coordinates authorised help, and checks — with new evidence — whether the problem was actually resolved.

ClearWay is an accessibility-support tool, not a surveillance product. It is a working system: a FastAPI backend with a real computer-vision and geometry pipeline, a deterministic incident state machine with a hash-chained evidence ledger, a server-side Ring WHEP/REST adapter, and a React + TypeScript interface.

> **Submission status:** the critical integrity and security checks pass (see `docs/RELEASE_REPORT.md`). The full live Ring code path runs end to end — webhooks, WHEP/WebRTC capture, token refresh — against the included **Ring API emulator** (no cost, no credentials). **A genuine Ring Developer Playground session has not been run** (no credentials and no network route to Ring where this was built); switching to it is a configuration change only.

---

## Quick start

```bash
docker compose up --build       # → http://localhost:8000  (demo mode; no .env needed)
cp .env.example .env            # optional: Ring / live household / AWS settings, then re-run the line above
docker build --target test .    # optional: runs the full test suite inside the image
```

The image uses pinned `node:22-bookworm-slim` / `python:3.13-slim-bookworm` bases, installs no OS packages, locks every Python dependency (`backend/requirements.lock`), runs as a non-root user (uid 10001), keeps data in the `/data` volume and has a healthcheck. Behind a TLS-intercepting corporate proxy, pass its CA for the build only: `docker build --secret id=extra_ca,src=proxy-ca.pem .`. If you bind-mount a host folder instead of the named volume, make it writable by uid 10001.

Without Docker (Python 3.13, Node 22):

```bash
cd frontend && npm ci && npm run build && cd ..
cd backend && pip install -r requirements-dev.txt -c requirements.lock
uvicorn app.main:app --port 8000          # serves the API and the built UI
pytest -q                                 # 110 tests: 109 pass, 1 skips until real photos are added; ~2–3 min on 2 cores
python -m eval.run_eval --n 100           # evaluation report → eval/results/REPORT.md
```

Demo mode seeds two **demo households** sharing one synthetic porch camera, with published, simulated member identities. Open the **Demo lab** and follow `docs/DEMO_SCRIPT.md`.
To send signed simulator events over real HTTP: `python tools/simulate_event.py --household hh_b --scene initial`.
Local WHEP protocol check (clearly labelled, not Ring): `cd backend && python ../tools/ring_protocol_check.py`.

### Live Ring path at no cost: the Ring API emulator

```bash
docker compose -f docker-compose.yml -f docker-compose.emulator.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.emulator.yml exec clearway python -m ringemu.demo
```

This runs ClearWay's **unchanged live Ring code path** — OAuth refresh with rotating tokens, device discovery, WHEP/WebRTC
capture and session DELETE, signed `motion_detected` webhooks, live evidence rules — against `backend/ringemu`, a local
emulator of the documented Ring Partner API contract (Bearer/406/401/404/503/415 behaviour, recvonly video only, ~30 s
session limit, partner unlink). The demo script then walks the whole story (obstruction → approved help → failed
correction → correction → independent second capture → verified) through webhooks and WHEP only. Open
http://localhost:8000, choose *Sign in to a live household…* (`hh_live`, tokens `local-live-household` /
`local-live-approver`) and use **Ring integration → Ring API emulator controls** to drive it from the browser.

**It is not Ring.** The camera picture is the labelled synthetic porch, the emulator stamps its frames "RING API EMULATOR",
and ClearWay labels all of it `RING_API_EMULATOR` — enforced in code: any API host other than `api.amazonvision.com`
forces that label regardless of `RING_SOURCE_LABEL`. Without Docker: `python -m ringemu` (port 8090) plus the
environment in `docker-compose.emulator.yml`.

### Running against the real Ring Playground (live household)

```bash
# .env
RING_ACCESS_TOKEN=...            # or RING_REFRESH_TOKEN + RING_CLIENT_ID + RING_CLIENT_SECRET
RING_DEVICE_ID=...
CLEARWAY_LIVE_HOUSEHOLD_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
CLEARWAY_LIVE_APPROVER_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
```

Restart → in the UI choose *Sign in to a live household…* (ID `hh_live`, household token, approver token) → **Ring integration**: authorise the camera → **Spatial twin**: capture a clear-scene reference from Ring and mark the route → *Capture live frame & analyse*. Ring cameras exist only in this server-provisioned household; demo households can never reach Ring. Security model: `docs/SECURITY.md`.

---

## What is new

1. **Personalised spatial twin.** The household marks its essential route, a delivery zone, optional floor reference points and a minimum clear width (e.g. 91 cm for a walker). The *same frame* yields different outcomes for different households — verified by identical frame hashes in the tests.
2. **Ground-contact geometry, not bounding boxes.** Obstruction is judged from the object's estimated floor footprint. A tall object beside the path occludes path pixels but is correctly *not* an obstruction (`test_bbox_overlap_alone_is_not_obstruction`).
3. **Vertical-vanishing-point edge trimming.** From the floor homography alone we recover the focal length (orthogonality of r1, r2) and the camera's vertical vanishing point; silhouette segments that point at it are elevated vertical edges, not floor contact.
4. **Uncertainty-aware decisions.** 160 Monte-Carlo perturbations of mask boundary, unseen object depth and floor calibration. If plausible perturbations disagree, the answer is `REVIEW_REQUIRED` — never "clear". "Nothing detected" is not treated as "clear": unexplained change on the route, a route surface that lost detail, or a missing clear-scene reference all prevent a clear result.
5. **Counterfactual clearance.** Searches placements that would remove the overlap (preferring the delivery zone, with a safety margin) and visualises current → proposed → freshly observed. Explicitly a simulation.
6. **Proof-of-clearance protocol with evidence provenance.** Requesting help never closes an incident. A live Ring incident can only be verified by frames the server itself captured from Ring (server clock), under the same calibration version, with two independent clear observations (separate capture sessions, at least 60 s apart); uploads, replays, synthetic renders and simulator events can only drive *demo* incidents. Duplicate frames and delayed events are rejected and ledgered. Manual closes are labelled "not camera-verified".
7. **Explainable evidence ledger** with per-household SHA-256 hash chaining, append-only enforcement in SQLite, and a "Why this decision?" trail linking every transition to its source frame and measurements.

## Why Ring is essential

The household's camera is already pointed at the entrance. ClearWay consumes Ring motion events (Stage 0 filter on `data.attributes.sub_type`), opens a short WHEP live session on the backend to obtain a fresh frame, and re-captures on demand to verify corrections. Ring's watermark is kept on every displayed Ring frame.

## How the Ring API is called (all server-side)

| Purpose | Call |
|---|---|
| Token refresh (rotating refresh tokens persisted encrypted) | `POST https://oauth.ring.com/oauth/token` `grant_type=refresh_token` |
| Device discovery | `GET /v1/devices?include=status,capabilities` |
| Live frames | `POST /v1/devices/{id}/media/streaming/whep/sessions` (`application/sdp`, recvonly video, no audio) → `201` + `Location` + SDP answer → frames via aiortc → `DELETE <Location>` |
| Fallback still | `POST /v1/devices/{id}/media/image/download` |
| Events | `POST /webhooks/ring`, `X-Signature: sha256=<hex HMAC-SHA256>`, documented v1.1 body (device = `data.attributes.source` when `source_type` is `devices`), ≤ 64 KiB, ack < 5 s, processing in background; idempotent on `data.id` and `meta.request_id`; explicit outcome per event (`COMPLETED` → observation, or `FILTERED` / `REJECTED` / `IGNORED_*` / `FAILED`) |

Required headers `User-Agent: App/version` and `Hardware_Id: <uuid>` are always sent. 401 triggers one refresh; 429 respects `Retry-After`; 404/503/406 map to clear user messages; undecodable (TAKE-encrypted) media degrades to `UNABLE_TO_VERIFY`.

### Status of the live Ring acceptance test — honest disclosure

No Ring Playground credentials were available in the environment where this was built, so **a genuine Ring Playground session has not yet been executed**. The WHEP client was verified end-to-end over real WebRTC against a local server implementing the documented contract (`backend/tests/whep_test_server.py`): offer → 201/Location/answer → VP8 frames decoded → analysed by the pipeline → session DELETEd; 401-refresh, 404 and 503 paths are tested. To run the real acceptance test, set `RING_ACCESS_TOKEN` (or refresh token + client credentials) and `RING_DEVICE_ID`, restart, then in **Ring integration**: authorise the camera → capture a clear-scene reference in **Spatial twin** → mark the route → *Capture live frame & analyse*. See `docs/FRICTION_LOG.md` F1.

## What is simulated (and how it is labelled)

Every observation carries a media source and an event source, shown as a badge everywhere it is displayed:

| Label | Meaning |
|---|---|
| `RING_PLAYGROUND` / `RING_LIVE` | Genuine Ring media via the documented API (only possible when the API host is Ring's) |
| `RING_API_EMULATOR` | Captured by ClearWay over WHEP from the local Ring API emulator; follows the live evidence rules; **not Ring footage** |
| `CUSTOM_EVENT_SIMULATOR` | Ring-*shaped* event signed with a **different** key, posted to a **different** endpoint (`/webhooks/simulator`); rejected by `/webhooks/ring` |
| `SYNTHETIC_RENDER` | Procedurally rendered porch scene with exact ground truth (stamped "SYNTHETIC TEST RENDER") |
| `INDEPENDENT_TEST_VIDEO` / `RECORDED_REPLAY` | User uploads; the upload API refuses any Ring label |

The helper "inbox" is a built-in local channel labelled `SIMULATED_LOCAL_INBOX`; delivery is real within the app, but no SMS/email is sent.

## How geometry is computed

* **Metric mode** (≥4 floor points with measured cm coordinates): OpenCV homography image→floor. Contact points (lowest silhouette pixel per column, minus frame-truncated and vertical-edge columns) are projected to the floor; the unseen far edge is placed at depth `D` along the contact-edge normal, `D` sampled in [0.55, 1.0] × contact width and bounded by the projection of the silhouette top. Route, footprint and zone are Shapely polygons in cm. Minimum clear width = shortest longest-free-segment over cross-sections perpendicular to the route axis. Obstruction ⇔ footprint intersects route **and** clear width < household requirement.
* **Apparent mode** (no floor points): footprint = lower band of the silhouette in image space; obstruction ⇔ ≥3 % of the footprint inside the route. The UI and every result state that metric clearance cannot be established.
* Camera displacement: ORB landmarks + RANSAC vs the calibration reference; shift > 8 px, rotation > 1°, or a feature-rich reference that can no longer be matched → `UNABLE_TO_VERIFY`.

## How uncertainty affects decisions

**Perturbation agreement** (`p_obstructed` in the API) = the share of 160 perturbed re-measurements that conclude "obstructed". It is *not* a calibrated probability that the route is obstructed. `≥ 0.90` → `POTENTIAL_OBSTRUCTION`; `≤ 0.10` → `NO_OBSTRUCTION_OBSERVED`; in between → `REVIEW_REQUIRED`. Unreliable ground contact touching the route, low segmentation confidence, bad image quality, >35 % scene change, or invalid calibration override to review / unable-to-verify. These thresholds are engineering policy tuned on synthetic data, exposed at `/api/system`, not validated facts.

## How fresh observations are verified

`app/incidents.py::verify_new_evidence` — for live incidents rejects anything that is not a server-captured Ring frame (`UNTRUSTED_SOURCE`); rejects identical frame content (`DUPLICATE`) and observations not later than the incident's latest evidence (`STALE`); maps unusable quality / invalid calibration to `UNABLE_TO_VERIFY`; inconclusive geometry to `REVIEW_REQUIRED`; a measurable improvement (≥5 cm clear width, or ≥25 % less overlap) that is still obstructed to `PARTIALLY_RESOLVED`; and only an independently recomputed clear result to `RESOLVED` (2 consecutive independent clear observations for live incidents, 1 for demo; configurable). A calibration change since the incident opened yields `REVIEW_REQUIRED`. Manual override requires an authenticated approving member and a recorded reason, and is recorded as `MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED`. When a result is inconclusive, ClearWay gives **verification guidance** (what evidence is missing and a safe way to get it) — it never dispatches anyone or declares the route safe.

## Measured results

See `backend/eval/results/REPORT.md` (regenerate with `python -m eval.run_eval`). **All numbers are on synthetic renders with exact ground truth — one porch, one box, CPU — and say nothing about real Ring footage.**

| Configuration | n | Precision | Recall | False-clear rate | False-alarm rate | Review/unable | Clear-width MAE | Clear-width p95 error | p50 latency |
|---|---|---|---|---|---|---|---|---|---|
| All | 300 | 0.975 | 1.0 | 0.0 | 0.0345 | 0.04 | 6.05 cm | 25.91 cm | 793 ms |
| hh_a/METRIC | 100 | 0.9118 | 1.0 | 0.0 | 0.0526 | 0.06 | 5.8 cm | 23.2 cm | 834 ms |
| hh_b/METRIC | 100 | 0.9808 | 1.0 | 0.0 | 0.0294 | 0.06 | 6.31 cm | 24.35 cm | 873 ms |
| hh_b/APPARENT | 100 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | n/a cm | n/a cm | 670 ms |

100 random scenes × 3 configurations = 300 analyses of **100 rendered scenes of one synthetic porch** — not 300 independent real-world examples. 28 borderline cases are excluded from precision/recall and shown separately in the report. Footprint IoU vs the true base is only 0.563 (the unseen far side is estimated), and the 95th-percentile clear-width error is 25.91 cm — large relative to a walker threshold, which is why near-threshold cases go to review. The 4 failure cases are conservative false alarms; there were **no false-clears**. Named scenarios (the 15 required plus 6 audit regressions): **21/21 pass**. Latency: CPU, 160 Monte-Carlo samples, 960×720.

These figures demonstrate that the reasoning behaves as designed on controlled inputs; they are **not** an accuracy claim for real Ring footage.

## Which AWS features genuinely run

**None by default.** `AWS_ENABLED=false` → Bedrock is reported as disabled in the UI and `/api/system`, and the deterministic pipeline is fully functional. With `AWS_ENABLED=true`, boto3 installed (`requirements-aws.txt`) and model access, *Explain* calls Bedrock `converse` (default `us.amazon.nova-2-lite-v1:0`; verify the id in your console) with the annotated frame and measurements, records latency/failures, and stores the text separately — it can never change a classification or state. This path has **not** been executed against AWS in development. No AWS Builder qualification is claimed.

## Privacy, security, safety

Explicit per-camera authorisation by an authenticated approving member · approver identity from `X-Member-Token`, never from a request body · credentials in headers only; media and the event stream use short-lived HMAC-signed links · demo and live households strictly separated · Ring tokens server-side, rotated tokens Fernet-encrypted · HMAC-verified webhooks with per-source keys · durable, idempotent event processing (stored payloads, leases, crash recovery on restart, automatic retries, DEAD after a cap) · household-scoped retention purge and complete household deletion (deleting the live household also removes stored Ring tokens and disconnects Ring) · upload size/resolution/duration limits · no face recognition or identity inference · **no continuous video; ClearWay does store one selected still frame and one annotated copy per observation (JPEG) for the retention period (default 14 days)** · no automatic sharing, door unlocking or third-party dispatch. Details: `docs/SECURITY.md`.

## Known limitations

* Genuine Ring Playground session not yet exercised (no credentials or network route to Ring here). The live path is proven end to end against the included Ring API emulator, which follows Ring's public documentation; details the docs leave open (exact webhook v1.1 body, snapshot request body, WHEP codecs) are the emulator's best reading of them and may differ from real Ring.
* Calibration "reprojection error" only measures consistency with the points entered; it is not physical accuracy. Clear-width estimates come from a single camera; on the synthetic set the 95th-percentile error is 25.91 cm, which matters near a walker/wheelchair threshold — hence the review band.
* No real-photo regression set is included (none could be sourced with clear provenance here). `backend/eval/real_images/README.md` defines the format; the harness runs automatically once labelled photos are added.
* Tokens are static bearer secrets without expiry or rate limiting; put a TLS reverse proxy with rate limits in front of any public deployment.
* Segmentation is classical change detection against a clear-scene reference: it needs a reference frame, is sensitive to large lighting changes (handled by refusing to judge), and is class-agnostic (a person standing on the path is treated as an obstacle). The no-reference fallback (cardboard-hue + GrabCut) is advisory only — it is fooled by cardboard-coloured surfaces such as terracotta tiles — so it can never produce a definite result.
* Footprint depth of the unseen far side is an assumption with bounds; IoU vs true footprint is moderate (see report). The near/route-side edge, which drives the decision, is far more accurate.
* Evaluation uses synthetic renders only; real-world performance is unknown. Thresholds are not calibrated on labelled real data.
* Single camera, single storey; no multi-camera fusion; no slopes/steps modelling.
* Bedrock path implemented but unexecuted; Strands orchestration not implemented (the agent is a deterministic template + policy gates).
* Docker: the Dockerfile and compose setup were built and run end to end (UI build, locked dependency install from PyPI, non-root runtime, healthcheck, volume persistence across restarts, `docker compose up`, the full test suite inside the image, UI driven in a browser). The build sandbox could not pull from any container registry, so this was done on **stand-in base images** (same Python 3.13 / Node 22, from an Ubuntu 24.04 rootfs) instead of the official Debian-based images. The first build on a normal machine is the first one on the official bases. See `tools/docker-sandbox/README.md`.
* Webhook v1.1 payload: parsed from the documented field locations (`data.attributes.source` / `source_type`, `data.id`, `meta.request_id`, epoch-ms `timestamp`) and covered by HTTP-to-observation tests — but never yet against a genuine Ring delivery. v2.2.0 and earlier mis-parsed v1.1 (see `docs/FRICTION_LOG.md` F20). Snapshot request body unverified.
* Evidence-origin isolation (v2.3.1): genuine-Ring incidents can only be closed by genuine Ring captures; an origin change mid-incident forces review.
* Vision accuracy is measured on synthetic scenes only (clear-width MAE 6.05 cm, **p95 error 25.91 cm**); treat borderline results as review.
* No real-photo validation: no licensed or consented photographs were available; `eval/real_images/` is empty.
* The Ring API emulator is ClearWay's own stand-in written from Ring's public docs — it is not Ring technology and cannot prove compatibility with Ring; closures on it are labelled `EMULATOR_CAPTURE`.

## Repository map

```
backend/app/vision/   synth.py · quality.py · segmentation.py · calibration.py · footprint.py · geometry.py · uncertainty.py · counterfactual.py · pipeline.py
backend/app/ring/     client.py (REST + OAuth) · whep.py (aiortc WHEP) · webhook.py (HMAC, Stage 0)
backend/app/          main.py (API) · service.py · incidents.py (state machine) · db.py (ledger) · tokens.py · bedrock.py · fixtures.py
backend/tests/        110 tests incl. the emulator end-to-end suite, audit regressions (test_hardening, test_ring_retries, test_event_recovery, test_privacy_deletion) and a local WHEP protocol server
backend/eval/         run_eval.py, results/
frontend/src/         React + TS + Tailwind + Konva + TanStack Query
docs/                 ARCHITECTURE · SECURITY · RELEASE_REPORT · DEMO_SCRIPT · DEVPOST · RING_API_FEEDBACK · FRICTION_LOG
backend/ringemu/      Ring API emulator (server.py) and the end-to-end live-path demo (demo.py) — not Ring
tools/                simulate_event.py · ring_protocol_check.py (local, labelled, not Ring)
```

License: MIT (see `LICENSE`, `THIRD_PARTY.md`).
