# ClearWay AccessGraph — Devpost project description

## In one line
A resident-defined route changes the decision; a failed correction keeps the case open; only fresh, independent
camera evidence closes it — and the app says exactly whose evidence that was.

> **Status, plainly:** the whole live Ring code path (OAuth, device discovery, WHEP/WebRTC capture, signed v1.1
> webhooks) runs end to end against **our own Ring API emulator**, clearly labelled. We have **not** yet run it
> against the genuine Ring Developer Playground (no credentials/network route in our build environment), and the
> vision engine has been evaluated on **synthetic scenes only** (p95 clear-width error 25.9 cm — borderline cases go
> to review). Emulator evidence can never close an incident opened on genuine Ring evidence.

## Inspiration
For most people a parcel on the front path is a nuisance. For someone using a walker or wheelchair it can block the only way out of the house — and the person most affected is the one least able to move it. Doorbell cameras already watch that path; they just answer the wrong question ("a package arrived") instead of the one that matters ("can *I* get out?").

## What it does
ClearWay turns a Ring camera view into a personalised spatial twin of the entrance. The household marks its essential route, a delivery area and, optionally, four floor reference points and the clear width they need (e.g. 91 cm for a walker). When a Ring event arrives, ClearWay opens a short live WHEP session on the backend, picks the best frame, and asks a geometric question: does the object's estimated *ground footprint* reduce the marked route below this household's requirement?

* The same frame can be fine for one household and an obstruction for another.
* Every judgement carries a Monte-Carlo uncertainty estimate; when plausible boundary or calibration errors could flip the answer, ClearWay says "review required" instead of guessing.
* A counterfactual planner shows where moving the object would clear the route.
* With a resident's explicit approval, ClearWay messages an authorised helper.
* It never closes an incident because help was requested: it waits for a new, distinct, quality-checked observation and recomputes the geometry. A parcel nudged a few centimetres is reported as *partially resolved*; only an independently verified clear frame resolves it.
* Every step is in a hash-chained, append-only evidence ledger with a "Why this decision?" view.

## How we built it
FastAPI + OpenCV + Shapely backend; aiortc for a server-side WHEP receiver; SQLite for the incident state machine, scene graph and ledger; React + TypeScript + Tailwind + Konva + TanStack Query UI with live server-sent events. Segmentation is calibrated change detection with shadow suppression (no opaque model weights). Ground contact comes from the silhouette's lower edge; elevated vertical edges are removed using the vertical vanishing point we recover from the floor homography. Uncertainty comes from 160 perturbations of mask, unseen depth and calibration.

## Challenges
* No Ring Playground credentials or network route to Ring in our build environment. We implemented only documented endpoints and built a **Ring API emulator** from Ring's public docs (OAuth refresh, device discovery, WHEP/WebRTC, signed webhooks, session limits, error codes) so the entire live path runs end to end at no cost. Everything it produces is labelled `RING_API_EMULATOR` and the app refuses to label emulated media as Ring footage. Pointing ClearWay at the real Playground is a configuration change.
* Single-view geometry: a box's vertical edge looks like floor contact; a parcel cut off by the frame edge looks small. Both produced false confidence until we added vanishing-point trimming and a "silhouette touches route but contact unreliable → review" rule.
* Making "unable to verify" a first-class answer (blur, darkness, obscured lens, a bumped camera).

## Accomplishments
* End-to-end proof-of-clearance workflow driven by real inference, not scripted states.
* Evidence provenance: a live Ring incident can only be verified by frames ClearWay itself captured from Ring; replays and test footage can't close it, and a manual close is labelled "not camera-verified".
* Conservative by design: on a synthetic labelled set no obstructed case was ever called clear (details and the important caveat — synthetic scenes only — in `backend/eval/results/REPORT.md`).
* Automated tests for every required scenario plus regression tests for each issue found in a pre-submission security/integrity audit.

## What we learned
Honest uncertainty is a feature — and so is honest provenance: a different image hash and a later timestamp prove neither that footage is real nor that it is recent. Users of accessibility tools need to know when *not* to trust an answer.

## What's next
Run the live Playground acceptance test; calibrate thresholds on labelled real Ring footage; learned instance segmentation as a pluggable Stage 1; multi-camera fusion; optional Bedrock explanations; real messaging channels with consent.

## How to try it
`docker compose -f docker-compose.yml -f docker-compose.emulator.yml up --build -d`, then `docker compose … exec clearway python -m ringemu.demo`, or open http://localhost:8000 (demo households need no sign-in).

## Built with
python, fastapi, opencv, shapely, aiortc, webrtc, whep, sqlite, react, typescript, tailwindcss, konva, tanstack-query, docker, ring-partner-api

### Integrity note (v2.3.0)
An external audit found that our webhook parser read the device from a non-documented field, and that our own emulator
shared the same mistake — so a genuine Ring v1.1 event would have been acknowledged and dropped. We fixed the parser,
made the emulator emit the documented v1.1 shape, and added HTTP-to-observation integration tests. We have still not
received a genuine Ring webhook or opened a genuine Playground stream; everything shown on the Ring path runs against
our clearly labelled emulator. Real-photo accuracy has not been measured (no licensed photos available).
