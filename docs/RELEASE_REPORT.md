# Release report — pre-submission hardening (v2.3.1)

Scope: the fixes requested by the external technical audit. Concept, geometry engine, visual identity and features were preserved; nothing was rebuilt.

## Verdict

**v2.3.1 go/no-go:** **GO for submission as a clearly labelled emulator demonstration; NOT verified against genuine Ring.**
Code: the fourth audit's P1 (evidence-origin mixing) is fixed and covered by regression tests at both the
function and the incident-routing level. Competition risk that code cannot remove: no genuine Ring Developer
Playground session has been run (no credentials, no network route to Ring from the build environment), and the
official rules ask for the project to function through a supported Ring API/SDK/simulator — the organisers
identify the Ring Developer Playground as that simulator. Our Ring API emulator is our own test harness, not that
simulator. Real-photo validation is also still absent. Both are stated in the README, Devpost text and video.


**v2.3.0 (third audit):** the official-webhook compatibility defect is fixed. Before the fix, a documented Ring
webhook v1.1 body (device in `data.attributes.source`, `source_type: "devices"`, no `attributes.device_id`) was
acknowledged, parsed with `device_id = None`, matched no camera, and the event was marked `COMPLETED` with no
observation — i.e. a genuine Ring motion event would have been silently dropped while reporting success. Our own
emulator had masked this because it sent the non-standard `attributes.device_id`. Both are corrected and covered by
HTTP-to-observation integration tests (below). This is still verified only against the documented format and the
emulator, **not against a genuine Ring Playground delivery**.


The live Ring code path now runs end to end at no cost against the included **Ring API emulator** (v2.2.0). What remains unproven is behaviour against **genuine** Ring: no Playground session has been run (no credentials and no network route to Ring in the build environment). Every finding from both audits that can be fixed in code is fixed and covered by regression tests. Once the live Ring capture works and is recorded, the remaining checklist items are operational (public repo, video, Devpost).

## Audit findings → status

| # | Finding | Status | Evidence |
|---|---|---|---|
| P0-1 | Replay/independent/synthetic footage could resolve incidents | **Fixed** | evidence trust + server chronology; LIVE vs DEMO incident namespaces; uploads can't attach to Ring cameras; calibration-version check; 2 independent clear frames to close live incidents — `test_replay_cannot_close_live_incident`, `test_state_machine_rejects_untrusted_evidence_for_live_incident`, `test_live_capture_time_is_server_clock`, `test_live_closure_requires_two_independent_clear_observations`, `test_calibration_change_requires_human_review`, `test_demo_incidents_still_work_with_replays`; eval scenarios 16–17 |
| P0-2 | Approvals trusted a claimed `member_id` | **Fixed** | `X-Member-Token`; mismatched claims refused + ledgered — `test_approval_impersonation_blocked`, `test_camera_authorisation_requires_authenticated_approver`, `test_override_requires_reason_and_authority`; eval scenario 12 |
| P0-3 | Demo tokens public while Ring endpoints unauthenticated; tokens in URLs | **Fixed** | live household provisioned only from server env, never listed; Ring endpoints live-household-only; signed media URLs and SSE tickets — `test_live_household_never_exposed_by_demo_endpoint`, `test_demo_households_cannot_reach_ring`, `test_live_household_ring_endpoints_pass_auth`, `test_media_urls_are_signed_and_tokenless`, `test_household_token_only_in_header`, `test_event_stream_requires_ticket`; eval scenario 19 |
| P0-4 | Retention purge deleted other households' evidence | **Fixed** | scoped purge — `test_retention_purge_is_household_scoped`; eval scenario 18 |
| P0-4 | Household deletion incomplete | **Fixed** | removes household record, member credentials, all rows, frames and reference images — `test_delete_household_removes_everything_scoped` |
| P0-4 | No upload limits | **Fixed** | size, pixel (decoder-level), min-size, container and duration caps — `test_upload_limits` |
| P0-5 | Failed webhook processing never retried | **Fixed** | RECEIVED/PROCESSING/COMPLETED/FAILED, bounded retries, re-processing on redelivery — `test_failed_event_is_retried_then_completed`, `test_permanently_failed_event_can_be_redelivered` |
| P1 | No-object ⇒ clear | **Fixed** | detection-quality gate (unexplained route change, route detail loss) — `test_unexplained_route_change_is_not_clear`; eval scenario 20 |
| P1 | Low confidence not applied to clear results | **Fixed** | `test_low_confidence_object_near_route_cannot_be_cleared` |
| P1 | No-reference fallback over-trusted | **Fixed** (found while fixing) | fallback is advisory only; it mistook terracotta tiles for a parcel — `test_no_reference_fallback_is_advisory_only`; eval scenario 21 |
| P1 | "P(obstructed)" implied a probability | **Fixed** | renamed "Perturbation agreement" in UI, API docs, evidence images, explanations |
| P1 | Privacy wording imprecise | **Fixed** | UI/README/API state that one still + one annotated still per observation are stored for the retention period |
| P1 | Reprojection error presented as accuracy | **Fixed** | relabelled "reprojection consistency" with an explicit caveat |
| P1 | Reference upload accepted a Ring label | **Fixed** | `test_reference_upload_provenance` |
| P1 | Manual closure not distinct | **Fixed** | `MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED`; demo closures labelled "verified on labelled test evidence" |
| P1 | Real-image validation | **Harness only** | `eval/run_real.py`, `tests/test_real_images.py` (skips until photos are added), format in `eval/real_images/README.md`. No real photos are bundled. |
| P1 | Real Ring evidence | **Blocked — credentials** | `tools/ring_protocol_check.py` (labelled local protocol test, not Ring) |
| — | Active verification guidance | **Added** | `verification_guidance` on review/unable results; never dispatches or declares safe — `test_guidance_never_dispatches_or_declares_safe` |
| — | Performance | Improved | vectorised clear-width cross-sections and OpenCV hulls: ~2.3 s → ~0.9 s per analysis under profiling on the same VM; same classifications |

## Test results

| Check | Result |
|---|---|
| Backend test suite (`pytest -q`, 2 CPU cores) | **102 passed, 1 skipped** (real-photo test: no photos yet), 106 s |
| Audit #1 regression tests (`tests/test_hardening.py`) | 26/26 pass |
| Audit #2 regression tests (`test_ring_retries.py`, `test_event_recovery.py`, `test_privacy_deletion.py`) | 5 + 8 + 6 = 19/19 pass |
| Same suite inside the Docker image (`docker build --target test`, stand-in bases) | 102 passed, 1 skipped |
| Ring adapter tests vs local WHEP protocol server | pass (skip cleanly if `aiortc` is missing) |
| Frontend `tsc -b && vite build` | pass |
| Evaluation (`python -m eval.run_eval --n 100`) | 22/22 named scenarios pass (evaluation data unchanged); synthetic set: precision 0.975, recall 1.0, false-clear 0.0, review/unable 0.04, clear-width MAE 6.05 cm (p95 25.91 cm), p50 793 ms |
| Clean-copy run (fresh data dir, signed simulator event over real HTTP → incident) | pass |
| UI driven in headless Chromium (approve/deny, corrections, live sign-in) | pass; no credentials found in any request URL; no page errors |

Failed tests: none. Before this release, the audit's two most serious findings were reproduced against the previous code (cross-household purge deleted another household's frame; a replay with a future timestamp resolved an incident).

## Ring integration verification status

| Item | Status |
|---|---|
| Documented endpoints implemented (OAuth refresh, devices, WHEP create/DELETE, snapshot, signed webhook) | Yes |
| WHEP client over real WebRTC against a server implementing the documented contract | Yes (tests + `tools/ring_protocol_check.py`) |
| Genuine Ring Playground session: authorised device → WHEP 201 → decoded Ring frames → pipeline → DELETE → watermark shown | **Not done — no credentials** |
| Webhook v1.1 payload | Parsed defensively from documented fields; not exercised with a real Ring webhook |

## Vision limitations

Classical change detection against a clear-scene reference (no learned segmentation); class-agnostic (people on the path count as obstacles); single camera; unseen far side of objects is a bounded assumption (footprint IoU ≈ 0.56 on synthetic data); clear-width 95th-percentile error ≈ 25.91 cm on synthetic data; thresholds tuned on synthetic renders only; no real-photo validation yet.

## Security limitations

Static bearer tokens (no expiry/rotation UI, no rate limiting) — deploy behind TLS + a rate-limiting proxy. Demo identities are public by design. The helper inbox is a local simulated channel. The Docker image was verified on stand-in base images; the official `node`/`python` bases could not be pulled in the build sandbox.

## Deployment

* Public demo: `CLEARWAY_DEMO_MODE=true`, **no** `RING_*` / `CLEARWAY_LIVE_*` variables.
* Live: set `RING_*` and `CLEARWAY_LIVE_HOUSEHOLD_TOKEN` / `CLEARWAY_LIVE_APPROVER_TOKEN` (long random values), keep them private, run behind TLS. Optionally `CLEARWAY_DEMO_MODE=false` — the UI then shows only the live sign-in.
* `docker compose up --build` (no `.env` required for the demo) or the non-Docker steps in the README. `docker build --target test .` runs the suite inside the image.

## Reproducible 3-minute demo

`docs/DEMO_SCRIPT.md`. Segment 0:25–0:50 must show a real Ring Playground session; if that is not available, say so on screen rather than presenting test footage as Ring.

## Changed files

Backend: `app/__init__.py`, `app/config.py`, `app/db.py`, `app/incidents.py`, `app/service.py`, `app/main.py` (rewritten: lifespan, auth dependencies, signed media, SSE tickets, event reliability, live-only Ring routes, upload limits), `app/fixtures.py`, `app/ring/whep.py`, `app/vision/pipeline.py`, `app/vision/uncertainty.py`, `app/vision/segmentation.py`, `app/vision/geometry.py`, `app/vision/footprint.py`; tests: `tests/conftest.py`, `tests/test_api_flow.py`, `tests/test_ring_adapter.py`, **new** `tests/test_hardening.py`, **new** `tests/test_real_images.py`; eval: `eval/run_eval.py`, **new** `eval/run_real.py`, **new** `eval/real_images/README.md`.
Frontend: `src/api.ts`, `src/App.tsx` (live sign-in), `src/components/ui.tsx`, `src/components/EvidenceViewer.tsx`, `src/components/CalibrationEditor.tsx`, `src/pages/Incidents.tsx`, `src/pages/Ring.tsx`, `src/pages/SpatialTwin.tsx`, `src/pages/Overview.tsx`, `src/pages/Lab.tsx`.
Tools/docs: **new** `tools/ring_protocol_check.py`, `tools/simulate_event.py`, **new** `docs/SECURITY.md`, **new** `docs/RELEASE_REPORT.md`, `README.md`, `.env.example`, `docs/FRICTION_LOG.md`, `docs/DEMO_SCRIPT.md`, `docs/DEVPOST.md`.

## Submission checklist (verified items only)

| Gate | Status |
|---|---|
| Real Ring Playground capture demonstrated | ✗ needs credentials — **live path demonstrated against the Ring API emulator** (webhook → WHEP 201 → ~90 frames → pipeline → DELETE, token refresh, 503, unlink), in-process and in Docker |
| Replay and live incident closure separated | ✓ tested |
| Safe demo/live authentication split | ✓ tested |
| Cross-household deletion fixed | ✓ tested |
| Real-image vision regression test added | ◐ harness added; needs labelled photos |
| Complete test suite and frontend build pass | ✓ see Test results |
| Docker build on the actual published base images | ✗ not possible here (no registry access) — first run on your machine |
| Deployment tested without real credentials exposed | ✓ Docker image built and run (compose up, healthcheck, restart persistence, live-mode env, UI in browser, 82/83 tests in-image, no build secrets in the image) — on stand-in bases, since registries were unreachable; official bases not yet pulled |
| Public GitHub repository with visible MIT license | ✗ your step (LICENSE included) |
| Public video under 3 minutes recorded | ✗ your step |
| Devpost submission, product feedback and friction log finalised | ◐ drafts in `docs/`; finalise after the Ring run |

## Docker fix (follow-up)

| Problem in the previous Dockerfile/compose | Fix | Verified |
|---|---|---|
| Never built | Built and run end to end | ✓ (stand-in bases; see `tools/docker-sandbox/README.md`) |
| `apt-get install libgl1 libglib2.0-0`: unnecessary (the headless OpenCV, PyAV and aiortc wheels need only libc/libstdc++/libz; checked with `ldd` on the exact wheel), fragile across Debian releases, and needs a Debian mirror | Removed; an import check of cv2/av/aiortc/shapely/fastapi runs at build time instead | ✓ |
| Floating base tags (`python:3.13-slim` changes Debian release over time) | Pinned `node:22-bookworm-slim` / `python:3.13-slim-bookworm`, overridable via `NODE_IMAGE` / `PYTHON_IMAGE` build args | ✓ |
| Unpinned transitive dependencies (the image resolved Starlette 1.9 while local dev had 1.7) | `backend/requirements.lock` (constraints) from the image whose in-image test run passed | ✓ |
| Compose failed without a `.env` file | `env_file` marked optional | ✓ (`docker compose up` with no `.env`) |
| `useradd --create-home` assumed `/home` exists | `mkdir -p /home` first | ✓ (no warning) |
| No way to build behind a TLS-intercepting proxy | Optional `extra_ca` build secret, used only during `npm ci`/`pip install`, not stored in the image | ✓ (image config/history checked) |
| No in-image test | `--target test` stage runs pytest inside the built image; the default target skips it | ✓ 82 passed, 1 skipped |
| Healthcheck without timeout/start period; data dir not declared | `--timeout 5s --start-period 30s`; `VOLUME /data`; `--proxy-headers` for reverse proxies | ✓ healthy; data and ledger survive restart |
| `.dockerignore` sent dist, zips, screenshots and `.env*` into the build context | Tightened | ✓ |

## Second audit (v2.1 → v2.1.1)

| # | Finding | Status | Evidence |
|---|---|---|---|
| P0 | Ring request retries dropped per-request headers (`kw.pop("headers")` inside the retry loop), so WHEP retried after 401/429 without `Content-Type: application/sdp` | **Fixed** — headers captured once, re-applied on every attempt; bearer token rebuilt per attempt so a refreshed token is used | Reproduced first (attempt 2: `Content-Type=None`). `tests/test_ring_retries.py`: WHEP after 401, after 429×2, after 401 then 429, 429 exhaustion, caller dict not mutated — each attempt's headers and body asserted via `httpx.MockTransport`. 4 of the 5 fail against the old line. |
| P1 | Webhook jobs stuck in RECEIVED/PROCESSING after a crash were treated as duplicates forever | **Fixed** — raw payload stored; per-process leases (`owner` = boot id, `lease_until`); startup recovery reclaims work left by a dead process; a sweeper (every 30 s) reclaims expired leases and retries FAILED events after 120 s without needing redelivery; atomic claim so redelivery and sweeper can't double-process; total-attempt cap → `DEAD` + ledger entry; idempotent side effects (an observation already saved for the event is reused; one saved but never routed is routed on recovery) | `tests/test_event_recovery.py` (8): restart recovery from RECEIVED and from PROCESSING, redelivery of an expired-lease event, no double claim of an active lease, partial persistence not duplicated, unrouted observation routed, sweeper retry → COMPLETED and → DEAD at cap, completed/filtered never reprocessed |
| P1 | Deleting the live household left the encrypted Ring OAuth tokens | **Fixed** — best-effort partner unlink (`DELETE /v1/accounts/me/app-integrations`, the documented partner-side unlink; Ring documents no separate token-revocation endpoint), stored tokens removed, in-process client and WHEP session log dropped, and a fingerprint of the operator's env credentials recorded so a restart does not silently reconnect (new credentials = re-link). Demo deletions never touch Ring credentials. | `tests/test_privacy_deletion.py` (6): tokens removed + unlink called, restart stays disconnected, new credentials re-link, unlink failure still removes local tokens, demo deletion leaves credentials, approver required |
| P1 | Two clear frames with different hashes are not necessarily independent | **Fixed** — for live incidents a clear observation counts toward closure only if it comes from a different capture session (WHEP session id) and at least `CLEARWAY_LIVE_MIN_CLEAR_SEPARATION_S` (default 60 s) after the previous counted one | `test_live_closure_requires_two_independent_clear_observations` (same session → no; too soon → no; separate & later → RESOLVED), `test_independence_rule_does_not_apply_to_demo_incidents`; eval scenarios 17 and 22 |
| UI | Right-panel clipping at 1440 px | **Checked, then improved** — measured every element on all 7 pages at 1440/1280/1024/768/390 px: nothing extends past the viewport or its card. The narrow right column did squeeze content, so it is wider (440/480 px) and the source description now sits on its own line. | headless-browser overflow scan (0 issues), screenshots |
| UI | "Live overlay" on synthetic scenes | **Fixed** — "Spatial overlay" / "Server-rendered evidence image" | |
| UI | Clearance results not scoped to the camera view | **Fixed** — "No obstruction observed in this camera view …" in explanations, evidence banners and beside every clear/obstructed result in the UI; demo resolutions read "Verified on labelled test evidence (demo) — not a live camera verification" | browser walkthrough |
| — | Real-photo regression set | **Still open** — harness ready (`eval/run_real.py`, `tests/test_real_images.py`); no photos with clear provenance could be sourced here | |
| — | Docker on the published base images | **Still open** — no registry access here; verified on stand-in bases | |
| — | Genuine Ring Playground session | **Still open** — needs your credentials | |

Note on the audit's own test run: it could not reproduce the full suite (no `aiortc`, time limit). Inside the Docker image all dependencies are pinned and the whole suite runs with `docker build --target test .` (~2 min on 2 cores); without Docker, `pip install -r requirements-dev.txt -c requirements.lock` provides `aiortc`. Ring adapter tests skip cleanly (rather than error) when `aiortc` is missing.

Changed files (second audit): `backend/app/ring/client.py`, `backend/app/main.py` (event leases/recovery/sweeper, idempotent processing, Ring disconnect on deletion), `backend/app/service.py` (capture sessions, independence bookkeeping, routing recovery), `backend/app/incidents.py` (independence rule), `backend/app/tokens.py` (clear, disconnect marker), `backend/app/db.py`, `backend/app/config.py`, `backend/app/vision/pipeline.py` (camera-view wording); tests: **new** `test_ring_retries.py`, `test_event_recovery.py`, `test_privacy_deletion.py`, updated `test_hardening.py`; `backend/eval/run_eval.py` (scenarios 17, 22); frontend: `components/ui.tsx` (ViewScope, SourceLine), `components/EvidenceViewer.tsx`, `pages/SpatialTwin.tsx`, `pages/Overview.tsx`, `pages/Incidents.tsx`, `pages/Lab.tsx`; docs: this report, `SECURITY.md`, `README.md`, `FRICTION_LOG.md`, `DEMO_SCRIPT.md`, `.env.example`.

## Fourth audit (v2.3.0 → v2.3.1)

| Finding | Status | Evidence |
|---|---|---|
| P0: verify genuine Ring Playground integration | **Not possible here — unresolved** | No Playground credentials and no network route to `api.amazonvision.com` / `oauth.ring.com` from the build environment. No integration evidence was fabricated. The emulator demonstration is retained and labelled. **Competition-eligibility risk remains.** |
| P1: RING and RING_API_EMULATOR evidence could mix in a LIVE incident (audit repro: RING incident + emulator frame → `accepted=True, RESOLVED`) | **Fixed** | `incidents.verify_new_evidence` compares the incident's `evidence_origin` with the observation's. Genuine-Ring incident + any other origin → rejected (`ORIGIN_MISMATCH`, no state change, ledgered `EVIDENCE_ORIGIN_MISMATCH`). Other LIVE incident + different origin (e.g. emulator → genuine Ring after a deployment switch) → `REVIEW_REQUIRED`, clear streak reset, provenance never upgraded. Emulator-only incidents still verify under the same two-independent-capture policy and close as `EMULATOR_CAPTURE`. Pre-v2.3 incidents get their origin derived from the opening observation. `tests/test_origin_isolation.py` (10 tests: 6 on `verify_new_evidence()`, 4 through the real ingest → routing path) |
| P1: real-photo validation | **Not done** | No properly licensed or consented entrance photographs were available, and the build sandbox cannot download images. Harness unchanged and ready; nothing invented. |
| Vision limitation to state plainly | Documented | Synthetic set: footprint IoU 0.563, clear-width MAE 6.05 cm, **p95 clear-width error 25.91 cm** — large near accessibility thresholds, which is why borderline cases must produce review rather than a safety claim. Synthetic only. |
| Repo hygiene: `.pytest_cache` in the zip | **Fixed** | packaging excludes caches; tests run with `-p no:cacheprovider` |
| Deployment hardening checklist | Re-verified by existing tests | server-side credentials, demo ≠ live, approver-only purge, OAuth removal on deletion, emulator labelling, webhook recovery, WHEP retry headers, bounded uploads; emulator control token only forwarded for the live household in emulator mode. TLS + rate-limiting reverse proxy required for any public live deployment (not provided by the app). See `docs/PUBLIC_DEMO_CHECKLIST.md`. |

### Results (v2.3.1, recorded exactly)

| # | Check (command) | Result |
|---|---|---|
| 1 | Full Python suite (`cd backend && python -m pytest -q -rs -p no:cacheprovider`) | **136 passed, 1 skipped** — skip: `tests/test_real_images.py` (no real-photo set). In this environment aiortc is installed, so the WebRTC/emulator tests ran and passed (the auditor's environment skipped 2 of them because aiortc was unavailable there). |
| 2 | Official-format Ring webhook tests (`tests/test_ring_webhook_v11.py`) | 10/10 pass (part of #1) |
| 3 | Ring retry + recovery (`test_ring_retries.py`, `test_event_recovery.py`) | 5 + 8 pass (part of #1) |
| 4 | Ring emulator / WHEP end-to-end (`tests/test_ring_emulator.py`, real HTTP + WebRTC) | 8/8 pass (part of #1) |
| 5 | Frontend `tsc --noEmit` | see "Packaging verification" below |
| 6 | Vite production build | see "Packaging verification" below |
| 7 | Docker build with official base images | **Not verified** — registries unreachable from the build sandbox; last in-image run (v2.3.0) used stand-in bases. v2.3.1 changes are Python/doc only; not re-run in Docker. |
| 8 | Household security + deletion (`test_hardening.py`, `test_privacy_deletion.py`) | 28 + 6 pass (part of #1) |
| 9 | Incident-provenance regression (`tests/test_origin_isolation.py`) | **10/10 pass** (part of #1) |
| 10 | Real-image validation | **Not run** — no fixtures |
| 11 | Public deployment smoke test | **Not run** — no public deployment from this environment |
| 12 | 3-minute demo rehearsal | Recorded pitch video (2:55) built from a real end-to-end emulator run; not a genuine Ring session |

## Third audit (v2.2.0 → v2.3.0)

### Reproduction of the P0 before the fix
Signed documented v1.1 body → `POST /webhooks/ring` → **HTTP 200**, parsed `device_id: None`, event status
**`COMPLETED`**, `observation_id: None`, **0 observations**. (Reproduced against v2.2.0 before any change.)

### Findings → status

| Finding | Status | Evidence |
|---|---|---|
| P0: v1.1 payloads not parsed (device in `data.attributes.source` / `source_type "devices"`, event id `data.id`, `meta.request_id`, `meta.account_id`, epoch-ms `timestamp`, `component_ids`) | **Fixed** | `app/ring/webhook.py::parse_event` reads v1.1 when a `meta` block is present; the legacy fallback (`attributes.device_id`) is used **only** when there is no `meta` block, so it can never mask a v1.1 defect — `test_v11_documented_payload_is_parsed`, `test_legacy_device_id_field_is_not_trusted_for_v11` |
| P0: relevant event reported `COMPLETED` without an observation | **Fixed** | `run_event` refuses `COMPLETED` unless an observation id was produced; explicit terminal statuses instead — `test_signed_v11_event_produces_linked_observation` |
| P0: missing identity / unregistered device / failed capture indistinguishable | **Fixed** | `REJECTED` / `REJECTED_NO_DEVICE_IDENTITY`; `IGNORED` / `IGNORED_UNREGISTERED_DEVICE`; `IGNORED` / `IGNORED_CAMERA_NOT_AUTHORIZED` (ledgered for the household); capture failure of an authorised device → `FAILED` (retried, then `DEAD`) — `test_missing_device_identity_is_rejected_not_completed`, `test_unregistered_device_is_explicitly_ignored`, `test_registered_but_unauthorised_camera_is_explicitly_ignored`, `test_capture_failure_of_authorised_device_is_failed_not_completed` |
| P0: idempotency | **Fixed** | dedupe on `data.id` **and** `meta.request_id` (new `events.request_id`) — `test_replay_is_idempotent_by_data_id_and_by_request_id` |
| P0: HTTP-to-observation integration test (not only a `parse_event` unit test) | **Added** | `tests/test_ring_webhook_v11.py` (10 tests): signed documented fixture → `/webhooks/ring` → capture invoked with the event's device and component → observation created and linked to the event, visible via `/events`. Only the capture function is replaced (it would open a WHEP session to Ring) |
| P0: emulator emitted a non-standard payload | **Fixed** | emits exact v1.1: `meta{version,account_id,request_id}`, `data{id,type,attributes{source,source_type,timestamp(ms),sub_type,component_ids},relationships}`; no `attributes.device_id`, no emulator marker inside the body — `test_emulator_webhook_is_exact_documented_v11`; the full emulator flow now asserts every webhook was parsed as `v1.1` and produced a linked observation |
| Webhook body bounds | **Fixed** | 64 KiB cap on `/webhooks/ring` and `/webhooks/simulator` (Content-Length check + streamed cap) → 413; non-JSON / non-object → 400 — `test_bad_signature_malformed_and_oversized_bodies` |
| P1: manual retention purge needed only the household token | **Fixed** | requires an authenticated approving member (`X-Member-Token`); refusal is ledgered (`AUTHORIZATION_DENIED`), success writes `RETENTION_PURGE_REQUESTED` + one `MEDIA_PURGED` per item; automatic startup retention unchanged — `test_manual_purge_requires_approving_member`, `test_automatic_retention_job_still_runs_without_member`, `test_retention_purge_is_household_scoped`; eval scenario 18 now uses an approver |
| P1: evidence origin conflated with capture method | **Fixed** | observations: `evidence_origin` (`RING` / `RING_API_EMULATOR` / `INDEPENDENT_TEST` / `REPLAY` / `SYNTHETIC`) and `capture_method` (`WHEP` / `SNAPSHOT` / `UPLOAD` / `SIMULATOR_RENDER`); incidents: `evidence_origin` and `verification_basis` (`GENUINE_RING_CAPTURE` / `EMULATOR_CAPTURE` / `DEMO_EVIDENCE` / `MANUAL_CONFIRMATION`). `resolution` strings unchanged for compatibility. UI: "Verified on Ring API emulator captures — not genuine Ring evidence" |
| P1: real-image validation | **Not done** | No appropriately licensed or consented real photographs were available in the build environment (the sandbox cannot download images), so `backend/eval/real_images/` is still empty and `test_real_images.py` still skips. No accuracy on real photos is claimed. |
| Simulator / `tools/simulate_event.py` | Updated | v1.1 envelope + ClearWay-only `clearway_*` attributes; still signed with the simulator key and accepted only on `/webhooks/simulator` |

### Results (v2.3.0, recorded exactly)

| Check | Result |
|---|---|
| Reproduction of the P0 on v2.2.0 | defect confirmed (see above) |
| `tests/test_ring_webhook_v11.py` | **10/10 pass** |
| `tests/test_ring_adapter.py` (incl. new v1.1 / malformed / legacy-only-without-meta parser tests) | **13/13 pass** |
| `tests/test_ring_emulator.py` (real HTTP servers, emulator emits exact v1.1) | **8/8 pass** |
| Purge tests in `tests/test_hardening.py` | 3/3 pass (28 tests in the file, all pass) |
| Full backend suite, local | **126 passed, 1 skipped** (the skip is the real-photo test: no photos), verified from a clean extraction of the zip |
| Full backend suite inside the Docker image (`--target test`, stand-in bases) | **126 passed, 1 skipped** |
| `eval/run_eval.py` | **22/22 scenarios pass**; random set: precision 0.975, recall 1.0, false-clear rate 0.0, unresolved 0.04 (synthetic renders only; thresholds unchanged) |
| `python -m ringemu.demo` in Docker (two containers) and from the host against it | RESOLVED / VERIFIED_NO_OBSTRUCTION_OBSERVED, `verification_basis = EMULATOR_CAPTURE`, all webhooks `v1.1` → `COMPLETED` with a linked observation (3 per run), ledger intact |
| Frontend `tsc --noEmit` + `vite build` | pass |
| Browser check (headless Chromium, live household, emulator mode) | Ring page lists webhooks as ClearWay processed them (`v1.1 · COMPLETED → obs_…`); incident shows "Verified on Ring API emulator captures — not genuine Ring evidence" |
| Genuine Ring Playground | **Not run** — no credentials and no network route to Ring from the build environment |
| Real-photo validation | **Not run** — no licensed/consented images |

Release status: the official-webhook compatibility defect is fixed and its integration tests pass. The release is
final **for what can be verified here**; the two items above remain open and are stated as such everywhere.

Changed (v2.3.0): `backend/app/ring/webhook.py` (rewritten parser, `MalformedEvent`, body cap, dispositions),
`backend/app/main.py` (bounded reads, dispositions, `EventDisposition`, no COMPLETED without observation,
request_id dedupe, component from event, approver-only purge, v1.1 simulator), `backend/app/service.py`
(provenance axes), `backend/app/db.py` (migrations), `backend/ringemu/server.py` (exact v1.1), `backend/ringemu/demo.py`,
`tools/simulate_event.py`, `eval/run_eval.py`; tests `test_ring_webhook_v11.py` (new), `test_ring_adapter.py`,
`test_ring_emulator.py`, `test_hardening.py`; frontend `api.ts` (`resolutionText`), `pages/Incidents.tsx`,
`pages/Ring.tsx`; docs.

## Ring API emulator (v2.2.0)

| Check | Result |
|---|---|
| `python -m ringemu.demo` against ClearWay (local processes) | Full story via webhooks + WHEP only: 65 cm → ASSESSED (LIVE) → approved help → 79 cm PARTIALLY_RESOLVED → clear 1/2 → separate WHEP session ≥ 20 s later → RESOLVED / VERIFIED; ledger intact; every observation `RING_API_EMULATOR`, trust LIVE, `SERVER_CAPTURED` |
| Same, in Docker (`docker-compose.yml` + `docker-compose.emulator.yml`, two containers, WebRTC across the compose network) | Identical outcome; both containers healthy |
| `tests/test_ring_emulator.py` (real HTTP servers) | 7/7: label guard, full flow, expired-token refresh, device offline → 503 surfaced, emulator enforces 406/400 (audio)/415/401/404 and its control token, demo households refused, live deletion unlinks at the emulator |
| Label guard | `RING_SOURCE_LABEL=RING_PLAYGROUND` with a non-Ring host still yields `RING_API_EMULATOR` |
| Browser | Ring page shows the "not Ring" banner, emulator controls, webhooks sent and WHEP sessions; Spatial twin shows `RING_API_EMULATOR` + "LIVE path · emulator" |
| Full suite | **109 passed, 1 skipped** locally and inside the image |

Fidelity limits: the emulator follows the public documentation; where the docs are silent (exact webhook v1.1 body, snapshot request body, WHEP codecs/ICE details, rate-limit behaviour) it is a best reading, not a guarantee of how Ring behaves. Organisers have said custom simulators are acceptable alongside substantial genuine Ring technology; whether an emulator of the API itself satisfies the Ring-track requirement is the organisers' call, so a real Playground run remains the stronger submission.

Changed/added (v2.2.0): **new** `backend/ringemu/` (`server.py`, `demo.py`, `__main__.py`), **new** `docker-compose.emulator.yml`, **new** `tests/test_ring_emulator.py`; `backend/app/config.py` (label guard), `service.py` (RING_API_EMULATOR source, emulator-aware naming), `incidents.py`, `main.py` (emulator status/control passthrough, system info); frontend `pages/Ring.tsx` (emulator banner + controls), `components/ui.tsx`, `pages/SpatialTwin.tsx`, `pages/Incidents.tsx`; docs.

### Packaging verification (v2.3.1)
From a clean extraction of the release zip: `npm ci` ✓ · `npx tsc --noEmit` ✓ · `npm run build` ✓ (Vite) ·
`python -m pytest -q -rs -p no:cacheprovider` → **136 passed, 1 skipped** (real-photo test; no fixtures).
The zip contains no `.pytest_cache`, `__pycache__`, `node_modules`, `dist`, data directory or `.env`.
