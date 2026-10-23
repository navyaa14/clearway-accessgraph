# Ring Partner API / Developer Experience — product feedback

Written from building ClearWay against the public documentation at
`developer.amazon.com/docs/ring/api-documentation.html`, `…/live-video.html` and
`…/computer-vision-guidelines.html` (read October 2026).

## What worked well

1. **WHEP is a great choice for partners.** One POST with an SDP offer, a `201` with the answer and a `Location` to DELETE is the simplest live-video contract we have integrated. It worked unchanged with a Python `aiortc` receiver, which matters for server-side CV (no browser needed).
2. **The CV guidelines page is genuinely useful.** The tiered Stage 0–3 pattern, "treat unknown sub_type as process", "keep 2–3 frames and send images rather than video to an LLM", and the watermark/TAKE notes shaped our architecture directly.
3. **Explicit session limits** (≈30 s battery / ≈60 s line-powered) let us design capture windows instead of discovering them.

## Suggestions

| # | Area | Suggestion | Why |
|---|---|---|---|
| 1 | Webhooks | Publish the full v1.1 payload as a JSON Schema plus 3–4 complete example bodies (motion with/without `component_ids`, device_offline). | The reference page is long and the payload section was hard to extract in full; we wrote a defensive parser (`data.attributes.sub_type`, several fallbacks for event id / device id). |
| 2 | Webhooks | State whether the event `id` is globally unique and stable across retries, and the retry schedule. | Needed for correct idempotency keys; we key on `source:event_id` and fall back to a body hash. |
| 3 | Snapshots | Show the exact request body for `POST /media/image/download` (is `{}` valid? are `components` integers?). | We send `{}` or `{"components":[N]}`; untested against a real device. |
| 4 | WHEP | Document codec(s) offered (H.264 profile? VP8?), whether trickle ICE / `PATCH` is supported, and the error body for "incompatible send direction". | Server-side receivers need to know which decoders to ship. |
| 5 | WHEP | Return the session limit (seconds) as a response header. | Lets clients schedule reconnection precisely. |
| 6 | Playground | A machine-readable list of Playground devices/streams (Package, Vehicle, Motion) and whether Playground can emit signed webhooks. | Hackathon teams must otherwise build simulators (as we did, clearly labelled). |
| 7 | Watermark | Publish the watermark's pixel geometry per resolution. | CV apps must mask the changing timestamp (we ignore fixed top strips) without cropping displayed media. |
| 8 | Scopes | The authorize parameters list `ava.v1:read` but an example uses `scope=ava`. Clarify the canonical value. | Avoids `invalid_scope` trial and error. |
| 9 | SDKs | Even a tiny official reference client (Python + Node) for token refresh + WHEP would cut onboarding time significantly. | "No official SDK" means every partner re-implements refresh-token rotation and session cleanup. |
| 10 | Datacenter IPs | Make the 406/WAF behaviour for cloud egress more prominent, with a self-service IP registration path. | Server-side CV almost always runs in a datacenter. |

## Addendum (v2.3.0): the cost of the missing webhook schema

Because the full v1.1 body was hard to obtain, we initially read the device from a non-existent
`attributes.device_id` field. Every event would have been acknowledged and silently dropped. A published JSON Schema
(item 1) with a complete `motion_detected` example — `meta.version/account_id/request_id`, `data.id`,
`data.attributes.source` + `source_type`, epoch-ms `timestamp`, `component_ids`, `relationships` — would have
prevented this. A **sample-event sender in the Playground** (signed with the integration's real secret) would let
teams verify parsing against genuine Ring output rather than their own reading of the docs.
