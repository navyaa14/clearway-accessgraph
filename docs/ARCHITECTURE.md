# Architecture

## System overview

```mermaid
flowchart LR
  subgraph Ring["Ring Partner API (documented)"]
    RW[Webhook<br/>X-Signature HMAC]
    RWHEP[WHEP live video<br/>POST …/whep/sessions → 201]
    RSNAP[Snapshot<br/>POST …/media/image/download]
    ROAUTH[OAuth refresh<br/>oauth.ring.com]
  end
  subgraph Sim["Clearly-labelled test inputs"]
    SIM[Custom event simulator<br/>separate HMAC key]
    UP[Independent test video / replay upload]
  end
  subgraph Backend["FastAPI backend (all Ring calls server-side)"]
    WH[/webhooks/ring · /webhooks/simulator/<br/>verify → idempotency → Stage 0/]
    WHEP[aiortc WHEP receiver<br/>recvonly video, DELETE on close]
    PIPE[Vision pipeline<br/>quality → displacement → segmentation<br/>→ footprint → geometry → Monte-Carlo]
    CF[Counterfactual planner]
    SM[Deterministic incident<br/>state machine]
    AS[Assistance: draft → approve → send<br/>policy gates]
    LED[(SQLite: observations, incidents,<br/>scene graph, hash-chained ledger)]
    BR[Bedrock Stage 3<br/>AWS_ENABLED only]
    SSE[SSE event bus]
  end
  UI[React + TS UI<br/>Konva calibration editor]

  RW --> WH --> WHEP
  WHEP <--> RWHEP
  WHEP -. fallback .-> RSNAP
  ROAUTH --- WHEP
  SIM --> WH
  UP --> PIPE
  WHEP --> PIPE --> CF
  PIPE --> SM --> LED
  AS --> SM
  PIPE -. explanation only .-> BR
  LED --> SSE --> UI
  UI -->|household token| Backend
```

## Event flow (one observation)

```mermaid
sequenceDiagram
  participant R as Ring / Simulator
  participant W as Webhook endpoint
  participant Q as Background task
  participant V as Vision pipeline
  participant S as State machine
  participant L as Ledger
  R->>W: POST event (X-Signature: sha256=…)
  W->>W: HMAC verify (key per source)
  W->>W: idempotency on source:event_id
  W->>W: Stage 0: type + data.attributes.sub_type
  W-->>R: 200 within 5 s
  W->>Q: enqueue
  Q->>R: WHEP session (Ring) / render fixture (simulator)
  Q->>V: best sampled frame
  V->>V: quality · camera displacement · segmentation · footprint · MC uncertainty
  V->>L: OBSERVATION_ANALYZED (frame hash, evidence hash, versions)
  V->>S: classification + metric
  S->>S: duplicate? stale? quality? calibration? recompute
  S->>L: STATE_TRANSITION(s) with reasons
```

## Incident state machine

```mermaid
stateDiagram-v2
  [*] --> OBSERVED
  OBSERVED --> ASSESSED
  ASSESSED --> ASSISTANCE_REQUESTED: approved by authorised member
  ASSISTANCE_REQUESTED --> AWAITING_NEW_EVIDENCE: message delivered
  ASSESSED --> REASSESSED: new distinct observation
  AWAITING_NEW_EVIDENCE --> REASSESSED: new distinct observation
  REASSESSED --> RESOLVED: clear, quality OK, calibration valid
  REASSESSED --> PARTIALLY_RESOLVED: still obstructed, measurably better
  REASSESSED --> REVIEW_REQUIRED: inconclusive under uncertainty
  REASSESSED --> UNABLE_TO_VERIFY: bad image / camera moved
  REASSESSED --> AWAITING_NEW_EVIDENCE: no improvement
  PARTIALLY_RESOLVED --> REASSESSED
  REVIEW_REQUIRED --> REASSESSED
  UNABLE_TO_VERIFY --> REASSESSED
  PARTIALLY_RESOLVED --> ASSISTANCE_REQUESTED
  AWAITING_NEW_EVIDENCE --> RESOLVED: manual override (reason recorded)
  RESOLVED --> [*]
```

Rejected evidence (identical frame content, or captured earlier than the incident's latest evidence) is recorded as `EVIDENCE_REJECTED` and never changes state.

## Vision pipeline

| Stage | Method | Output |
|---|---|---|
| Image quality | contrast-normalised Laplacian focus (320 px), luminance, clipping, block texture loss vs reference | `usable` + reasons |
| Calibration validity | ORB landmarks + RANSAC similarity vs clear-scene reference | `STABLE` / `DISPLACED` / `UNCONFIRMED` |
| Segmentation (Stage 1) | LAB change detection vs reference, robust illumination gain, chroma-preserving shadow suppression, Ring watermark strips ignored, connected components | instance masks + confidence |
| Ground contact | lowest silhouette pixel per column; frame-border truncation flagged; elevated vertical edges trimmed using the **vertical vanishing point recovered from the floor homography** | contact polyline |
| Footprint | metric: contact projected to floor (cm), far edge at depth `D` along contact-edge normal, `D` bounded by projecting the silhouette top; apparent: lower silhouette band | polygon |
| Geometry (Shapely) | intersection, fraction, intrusion depth, gap, cross-section minimum clear width | measurements |
| Uncertainty | 160 Monte-Carlo samples: mask ±3 px, unseen depth/band, floor-point jitter σ=1.5 px | `p_obstructed`, p5–p95 envelope |
| Decision | p ≥ 0.9 obstruction · p ≤ 0.1 clear · otherwise review; unreliable contact touching the route → review | classification |
| Counterfactual | grid search of translations into the drop zone with safety margin; nearest clearing position otherwise | proposed footprint |

## Data & privacy

* Only the selected evidence frame and its annotated copy are stored; no video is persisted. Retention purge removes media but keeps hashes in the ledger.
* Per-household access tokens (hashed in DB); every route is household-scoped.
* Ring tokens are server-side only; rotated tokens encrypted with Fernet.
* Ledger: per-household SHA-256 hash chain; SQLite triggers abort UPDATE/DELETE (DELETE only via explicit household data deletion).
* No face recognition, no identity inference, no door control, no automatic sharing.
