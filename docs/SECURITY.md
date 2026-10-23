# Security & evidence-trust model

## Credentials

| Credential | Where it lives | How it is sent | Grants |
|---|---|---|---|
| Household token | hashed (SHA-256) in `households.token_hash` | `X-Household-Token` header only | read/write that household's data |
| Member token | hashed in `members.token_hash` | `X-Member-Token` header only | acting **as that member** (approve, override, authorise camera, delete data) |
| Ring OAuth tokens | env / Fernet-encrypted `secrets` table | server → Ring only | never reach the browser |
| Media / stream links | HMAC-SHA256 over path + expiry with a per-install key | `?exp=…&sig=…` | one file or one event stream, for 15 min / 2 min |

Household or member credentials are **never accepted in URLs**. A signature for one household's path does not open another household's media.

## Demo vs live

* **Demo households** (`is_demo=1`, `hh_a`, `hh_b`) exist only when `CLEARWAY_DEMO_MODE=true`. Their household token and *simulated member identities* are published at `/api/demo/households` so judges can try the app. They can never own a Ring camera, never reach any Ring endpoint, and the simulator can only target them.
* **The live household** (`hh_live`) is provisioned only from `CLEARWAY_LIVE_HOUSEHOLD_TOKEN` + `CLEARWAY_LIVE_APPROVER_TOKEN` (+ optional `CLEARWAY_LIVE_HELPER_TOKEN`). It is never listed anywhere. Only it can own the Ring camera (`RING_DEVICE_ID`). Ring device discovery, capture and session logs require its household token.
* Turning demo mode off leaves the UI usable: it shows a sign-in form for the live household.
* Recommended public deployment: run the public demo **without** `RING_*` and `CLEARWAY_LIVE_*` variables; run the live instance separately (or keep the live tokens private).

## Approvals

Approve, manual override, camera authorisation and household deletion require an authenticated member with `can_approve`. The member's identity is taken from `X-Member-Token`. If a request body also names a `member_id` that differs from the authenticated member, the request is refused and an `AUTHORIZATION_DENIED` ledger entry is written. Helpers can receive messages but cannot approve.

## Evidence trust (proof-of-clearance)

| Source | Trust | Chronology |
|---|---|---|
| `RING_API_EMULATOR` (captured by this server from the local emulator) | LIVE (rules apply in full; closure recorded with `verification_basis = EMULATOR_CAPTURE`) | `SERVER_CAPTURED` |
| `RING_PLAYGROUND`, `RING_LIVE` (captured by this server via the Ring API) | LIVE | `SERVER_CAPTURED` (server clock; any caller-supplied time is ignored and kept only as `asserted_captured_at`) |
| `SYNTHETIC_RENDER` / `CUSTOM_EVENT_SIMULATOR` | DEMO | `CLIENT_ASSERTED` if the event carries a time, else `SERVER_RECEIVED` |
| `INDEPENDENT_TEST_VIDEO`, `RECORDED_REPLAY` (uploads) | DEMO | same |

* An incident's namespace is the trust of the observation that opened it.
* Uploads and Ring-labelled media are restricted by camera kind: uploads can never attach to a Ring camera; Ring-labelled media can only come from a Ring camera.
* A LIVE incident accepts verification evidence only if it is LIVE **and** `SERVER_CAPTURED`; anything else is `UNTRUSTED_SOURCE` (ledgered, no state change).
* Calibration must match the version the incident opened under; otherwise the result is `REVIEW_REQUIRED`.
* LIVE closure requires `CLEARWAY_LIVE_CLOSE_AFTER_CLEAR` (default 2) consecutive **independent** clear observations: each must come from a different capture session (WHEP session) and at least `CLEARWAY_LIVE_MIN_CLEAR_SEPARATION_S` (default 60 s) after the previous counted one. Demo closure defaults to 1.
* Duplicate frame content and observations not later than the incident's latest evidence are rejected.
* Manual closes are recorded as `MANUAL_OVERRIDE_NOT_CAMERA_VERIFIED`, separate from camera-verified closure.
* Provenance is recorded on separate axes: `evidence_origin` (RING / RING_API_EMULATOR / INDEPENDENT_TEST / REPLAY / SYNTHETIC), `capture_method` (WHEP / SNAPSHOT / UPLOAD / SIMULATOR_RENDER) and, on closed incidents, `verification_basis` (GENUINE_RING_CAPTURE / EMULATOR_CAPTURE / DEMO_EVIDENCE / MANUAL_CONFIRMATION). Using the Ring protocol (WHEP) never implies Ring origin.

## Data isolation & retention

* Every query is household-scoped. The manual per-household retention purge (`POST /retention/purge`) is irreversible, so it requires an authenticated **approving member** (`X-Member-Token`); the household token alone is refused and the refusal ledgered. It only touches that household. The automatic server-wide purge (`CLEARWAY_RETENTION_DAYS`) runs only as a startup maintenance job and needs no member.
* `DELETE /api/households/{id}/data` (approver required) removes all rows for that household (including the household record and member credentials), its evidence frames and its calibration reference images. Ledger hash chains are per household, so deleting one never breaks another.
* Deleting the **live** household also disconnects Ring: best-effort partner unlink (`DELETE /v1/accounts/me/app-integrations`), removal of the encrypted stored OAuth tokens, removal of the in-process client and WHEP session log, and a fingerprint of the operator's env credentials so a restart does not reconnect with them (supplying new credentials re-links). The response reports what happened, including an unlink failure. Environment variables themselves are operator-controlled; remove `RING_*` from the deployment as well. Demo deletions never touch Ring credentials.

## Event reliability

Webhook events move `RECEIVED → PROCESSING → COMPLETED | FAILED → … | DEAD`, or end in an explicit non-success status:
`FILTERED` (irrelevant type), `REJECTED` (no device identity / malformed target), `IGNORED` (`IGNORED_UNREGISTERED_DEVICE`, `IGNORED_CAMERA_NOT_AUTHORIZED`).
`COMPLETED` is only ever recorded together with the id of the observation the event produced.

* Ring webhook bodies are parsed as documented v1.1 (`data.attributes.source` with `source_type "devices"`, `data.id`, `meta.request_id`). Bodies over 64 KiB → 413; non-JSON → 400; bad signature → 401. Duplicates are detected by `data.id` and by `meta.request_id`.

* The raw (signature-verified) payload is stored, so work can be redone without redelivery.
* Each in-flight event carries a lease (`owner` = process boot id, `lease_until`, default 300 s). On startup, events left in flight by a previous process are reclaimed immediately; a sweeper every 30 s reclaims expired leases and retries `FAILED` events after 120 s. Claims are atomic, so a redelivery and the sweeper never process the same event twice.
* Each claim makes up to `CLEARWAY_EVENT_MAX_ATTEMPTS` (3) attempts with backoff; after `CLEARWAY_EVENT_MAX_TOTAL_ATTEMPTS` (9) the event becomes `DEAD` and an `EVENT_DEAD` ledger entry is written.
* Side effects are idempotent: if the observation for an event/camera already exists (crash after saving it), it is reused; if it was saved but never routed to an incident, routing is completed (`OBSERVATION_ROUTING_RECOVERED`).
* `COMPLETED` and `FILTERED` events stay idempotent on redelivery. `GET /api/households/{id}/events` shows status, attempts, lease and last error.
* Designed for a single worker process (SQLite + in-process event bus), which is how the Docker image runs.

## Upload limits

25 MB per upload, ≤ 16.7 MP images (OpenCV decoder limit set before import), ≥ 64×64, known video containers only, decoding capped at 60 s of video regardless of container metadata. All configurable (`CLEARWAY_MAX_*`).

## Known security limitations

* Tokens are static bearer secrets (no expiry, no rotation UI, no rate limiting). Use HTTPS and a reverse proxy with rate limits for any public deployment.
* The local helper inbox is a simulated channel; there is no external messaging, so no delivery authentication beyond the app.
* Demo identities are, by design, public. Never put real data in demo households.

## Ring API emulator

* Media from any Ring API host other than `api.amazonvision.com` is always labelled `RING_API_EMULATOR` (`Settings.effective_ring_source_label`), so emulated footage can never be presented as Ring footage, whatever `RING_SOURCE_LABEL` says.
* The emulator's `/emulator/*` control endpoints (not part of Ring's API) require `X-Emulator-Token`; ClearWay only forwards them for the authenticated live household, and only in emulator mode (`404` otherwise).
* The compose override ships fixed local demo tokens; change them if the port is reachable by anyone else.
