# ClearWay AccessGraph — Beyond Detection. Prove the Way Is Clear.

### An accessibility-aware spatial intelligence system that turns camera events into personalized decisions, approved interventions, and independently verified outcomes.

**What if the smartest thing a doorbell could do wasn't detect a package—but understand whether that package had taken away someone's independence?**

For most people, a parcel on the doorstep is an inconvenience.

For someone using a wheelchair or walker, that same parcel could obstruct the only accessible route out of their home.

A notification saying **"Package detected"** doesn't answer the question that matters:

**"Can I still get through?"**

And even if someone moves the parcel, who verifies that the problem was actually fixed?

That is the problem we built **ClearWay AccessGraph** to solve.

ClearWay is a full-stack, accessibility-focused computer-vision prototype designed around Ring's event-driven camera architecture. It combines **personalized spatial reasoning, perspective-aware computer vision, counterfactual planning, uncertainty quantification, human-in-the-loop authorization, evidence provenance, and a deterministic Proof-of-Clearance protocol.**

**Our central innovation: An alert is not an outcome. An attempted fix is not proof. And an AI decision without reliable evidence is not a trustworthy decision.**

---

## Inspiration — We Wanted Cameras to Understand Consequences, Not Just Events

Smart cameras have become powerful event detectors. They can detect movement, identify packages and show what's happening at an entrance.

But the real-world consequences of those observations are highly personal.

A package that leaves enough room for one household might make another household's essential route difficult to use.

Even the same camera image can mean two completely different things.

Consider this example from our demonstration:

| | Household A | Household B |
|---|---|---|
| Configured clearance | 60 cm | 91 cm |
| Estimated available width | 65 cm | 65 cm |
| ClearWay interpretation | No obstruction observed | Potential obstruction |

**Same image. Same parcel. Same geometry. Different needs. Different decision.**

That became our founding insight:

**Accessibility cannot be inferred from object detection alone. The system must understand the environment in the context of the person using it.**

We set out to build a system that goes beyond detecting an object to evaluating its potential impact, coordinating a response, and examining evidence of the outcome.

---

## What It Does — Seven Layers of Innovation

### 01. AccessGraph — Personalized Spatial Intelligence

**From pixels to household-specific spatial reasoning.**

AccessGraph lets residents define the important parts of their entrance:

- The essential walking or mobility route.
- A preferred parcel-delivery zone.
- Four measured floor-reference points for metric calibration.
- The household's configured minimum clearance requirement.

Using OpenCV and Shapely, the engine projects estimated ground-contact geometry into a calibrated floor plane.

It then evaluates whether an obstacle's estimated ground footprint intersects the marked route and reduces its apparent clearance below the configured requirement.

This is fundamentally different from declaring an obstruction because an object's bounding box overlaps a walkway on screen.

**ClearWay reasons about where the object meets the ground—not merely where it appears in the image.**

The result is a household-specific spatial assessment, with visible geometric evidence and measurement limitations.

### 02. Perspective-Aware Ground Geometry

**A bounding box is not a footprint.**

Single-camera geometry is deceptively difficult.

A tall box can visually overlap a marked route even when its base is outside that route. An object partially outside the camera's view may have an unreliable visible contact edge.

We developed a geometry pipeline combining:

**Homography → Ground-contact estimation → Vertical-edge filtering → Footprint construction → Route intersection → Clearance estimation**

The implementation estimates a vertical vanishing point from calibrated floor geometry to help distinguish elevated object edges from likely ground-contact geometry.

It also performs camera-displacement checks using ORB feature matching and RANSAC.

If the reference geometry becomes unreliable, ClearWay can report **UNABLE TO VERIFY** instead of issuing an unsupported measurement.

This makes the computer-vision layer interpretable and explicitly aware of its limitations.

### 03. Uncertainty-Aware Decision Intelligence

**An intelligent system should know when its evidence is insufficient.**

A camera's estimate can change when the mask boundary shifts, the assumed hidden footprint depth changes, or the calibration is imperfect.

Instead of reporting a single result with false precision, ClearWay performs **160 controlled perturbation-based remeasurements**.

The system measures how often those plausible variations agree on an obstruction decision.

We call this **Perturbation Agreement**—not a calibrated probability of obstruction.

Depending on the evidence, the engine can produce:

**POTENTIAL OBSTRUCTION · NO OBSTRUCTION OBSERVED · REVIEW REQUIRED · UNABLE TO VERIFY**

If the conclusion is unstable, ClearWay refuses to turn uncertainty into a confident all-clear.

**We designed uncertainty as an explicit product behavior, not a footnote.**

### 04. Counterfactual Clearance Planner

**Don't stop at identifying the problem. Show a possible correction.**

Once an obstruction is identified, ClearWay's Counterfactual Planner evaluates alternative object positions relative to the resident's marked route and preferred delivery area.

The interface visualizes three distinct states:

**Current placement → Proposed placement → Freshly observed placement**

This distinction is important.

A simulated relocation does not mean anyone has physically moved the parcel.

And a theoretically suitable placement does not mean the route is now clear.

The planner provides a geometric suggestion. The real environment must still be reobserved.

### 05. Human-in-the-Loop Assistance

**AI can recommend. People authorize.**

When a route appears obstructed, ClearWay prepares an assistance request.

An authenticated household approver must authorize it. Unauthorized approval attempts are refused and recorded.

For the current prototype, approved requests are delivered through a clearly labeled simulated local inbox, rather than a live SMS or email service.

The system never equates a message being drafted or approved with physical work having been completed.

This creates a meaningful separation between **decision support, human authorization, attempted intervention, and observed outcome**.

### 06. Proof-of-Clearance — The Core Breakthrough

**Detecting a problem is easy to demonstrate. Proving that an intervention worked is the harder engineering challenge.**

ClearWay uses a deterministic incident state machine to track the entire correction process.

In our demonstration:

**First observation — 65 cm estimated clearance.**

The household requires 91 cm. ClearWay identifies a potential obstruction and opens an incident.

**First intervention — 79 cm estimated clearance.**

The parcel has moved. Conditions have improved.

But the household's requirement is still not met.

ClearWay explicitly reports:

**PARTIALLY RESOLVED — INCIDENT REMAINS OPEN.**

**Second intervention — New evidence supports no remaining obstruction.**

ClearWay does not automatically treat a single clear-looking frame as sufficient proof.

For its live-style verification workflow, it requires two sufficiently separated observations from independent capture sessions, subject to image quality, calibration consistency, chronology and source-provenance checks.

Only then can the system record a camera-supported resolution.

**This is ClearWay's defining distinction: We verify outcomes rather than assuming them from actions.**

A camera-supported resolution is not a guarantee of physical safety or certified accessible clearance.

### 07. EvidenceGraph — Explainable, Provenance-Aware Decisions

**Every decision should answer: What evidence supported this conclusion, and where did that evidence come from?**

ClearWay's EvidenceGraph connects observations, measurements, authorization decisions, incident transitions and verification outcomes through a hash-chained, append-only application ledger.

The **"Why this decision?"** interface exposes the reasoning behind state changes.

Each observation records its capture method and evidence origin, distinguishing genuine Ring sources, the custom Ring API emulator, independent test footage, synthetic scenes and recorded replays.

A replay cannot silently become genuine Ring evidence.

An emulator capture cannot automatically close an incident initiated with genuine Ring evidence.

An origin change cannot silently upgrade the trustworthiness of earlier observations.

**Evidence provenance is part of the decision logic—not merely a label on the screen.**

---

## How We Built It — Engineering the Entire Observation-to-Outcome Loop

ClearWay is not a collection of static mockups or a hardcoded demonstration.

We built a modular full-stack system with real geometric computation, a persistent incident engine, protocol-level video handling and an interactive frontend.

**Computer Vision and Geometry**

Python, OpenCV, NumPy and Shapely power calibrated change detection, shadow suppression, object-footprint estimation, planar transformations, geometric intersections, clearance calculations, camera-displacement detection and uncertainty analysis.

**Ring-Compatible Integration**

Our server-side Ring adapter implements documented OAuth token refresh, device discovery, WHEP/WebRTC video reception using aiortc, snapshot handling, session cleanup and signed v1.1-format motion webhooks.

The pipeline is exercised end to end against a custom Ring API emulator. The genuine Ring Developer Playground connection remains unverified.

**Reliable Event Infrastructure**

FastAPI and SQLite support persistent event processing, idempotency checks, retry handling, processing recovery, incident transitions, household-scoped authorization and evidence history.

**Interactive Application**

React, TypeScript, Vite, Tailwind CSS, Konva and TanStack Query power the Spatial Twin, incident workflow, Counterfactual Planner, provenance viewer and live interface updates.

**Deployment and Testing**

Docker Compose provides the documented application and emulator setup.

Our v2.3.1 release report records:

**136 automated tests passed · 1 real-photo test skipped**

The reported testing includes Ring-format webhook handling, emulator/WebRTC integration, incident state transitions, provenance isolation, authorization, deletion and regression cases.

Our synthetic computer-vision evaluation reports 97.5% precision and no false-clear decisions in its evaluated non-borderline cases. These are synthetic results, not real-world performance claims. The 95th-percentile clearance-width error of approximately 25.9 cm highlights why conservative review decisions remain necessary.

**We focused on making the system inspectable, testable, and capable of declining an unreliable conclusion—not just making it appear confident.**

---

## Challenges We Faced — And the Engineering Decisions That Made the Project Stronger

### Challenge 1 — Building Against an External Platform Without Genuine Ring Playground Access

We could not complete a genuine Ring Developer Playground session in our build environment.

Rather than pretend that integration had been verified, we created a **custom Ring API emulator** modeled on the publicly documented contract.

It lets our application exercise OAuth refresh, device discovery, signed v1.1-format webhook handling, WebRTC frame reception, failure paths and session cleanup through the actual backend integration code.

Emulated frames are visibly labeled `RING_API_EMULATOR`.

**Important limitation:** This is our own emulator, not Ring's official Playground. Successful emulator testing does not establish compatibility with genuine Ring services.

### Challenge 2 — Discovering That Our Own Emulator Was Masking an API Bug

During technical auditing, we found that both our webhook parser and emulator were using the wrong field to identify the source device.

Their agreement made the local test appear successful, even though the implementation was incompatible with the documented webhook shape.

We corrected both sides and introduced signed HTTP-to-observation regression tests based on the documented v1.1 event structure.

That experience reinforced an important lesson:

**A passing integration test means little if the test environment repeats the same incorrect assumption as the application.**

### Challenge 3 — Single-Camera Geometry Cannot See Everything

Perspective, shadows, vertical edges, cropped objects and camera movement can make a seemingly simple clearance measurement unreliable.

We introduced geometric consistency checks, reference-frame quality checks, vanishing-point-based edge filtering and explicit review states.

The goal wasn't to make the system produce a number in every situation.

It was to ensure that the system could explain when the available evidence was insufficient.

### Challenge 4 — Preventing False Resolution

The hardest conceptual problem wasn't opening an incident.

It was defining what would constitute defensible evidence that the problem had been addressed.

We implemented separate states for assistance, partial correction, review and resolution, then added evidence-source compatibility and independent-capture rules.

**We learned to treat incident closure as an evidence problem—not a button press.**

---

## Accomplishments We're Most Proud Of

**1. A genuinely personalized decision engine.** The same camera scene can yield different interpretations under different household clearance requirements.

**2. Ground-plane spatial reasoning.** We go beyond bounding-box overlap to estimate obstacle footprints and their relationship with a user-defined route.

**3. Explicit uncertainty handling.** The system runs 160 perturbation checks and can refuse unsupported conclusions.

**4. A counterfactual intervention planner.** ClearWay visualizes a possible correction while keeping simulated and observed outcomes distinct.

**5. A closed-loop Proof-of-Clearance protocol.** A failed correction doesn't disappear behind a successful notification. It remains unresolved until new evidence supports a different conclusion.

**6. Authorization and evidence provenance.** Approval requires an authenticated person, and synthetic, replay, emulator and genuine Ring origins cannot silently substitute for one another.

**7. An auditable architecture.** Hash-linked evidence history, explainable incident decisions, recoverable event handling and 136 documented passing automated tests support a reproducible prototype.

What makes us proudest isn't any single algorithm.

**It's that the entire system is designed to preserve the distinction between seeing an event, inferring a consequence, attempting a correction and verifying what happened afterward.**

---

## What We Learned

We started by asking whether a camera could understand an obstruction.

We ended up asking a much more important engineering question:

**What would it take for someone to trust the camera's conclusion?**

That question changed the architecture.

We learned that trustworthy computer vision requires more than detection quality. It requires calibrated context, uncertainty handling, trustworthy timestamps, compatible evidence origins, authenticated decisions and a clear distinction between planned actions and observed outcomes.

We also learned that transparency about limitations is essential.

A different frame hash does not prove a new physical event.

A newer timestamp does not automatically prove trusted evidence.

A successfully sent assistance request does not prove the obstacle moved.

And a test passed against our emulator does not prove a connection to Ring's genuine service.

**The strongest AI systems aren't the ones that always give answers. They're the ones that know what their evidence can—and cannot—justify.**

---

## What's Next — From Prototype to Real-World Accessibility Intelligence

Our immediate priorities are a genuine Ring Developer Playground acceptance test, evaluation on properly sourced real entrance photographs, and calibration under varied camera and lighting conditions.

Beyond that, we envision support for multiple camera viewpoints, improved segmentation, consent-based assistance messaging, stronger calibration tooling and more complex shared-entrance environments.

The longer-term opportunity extends beyond parcels.

The same evidence-driven pattern could support user-defined environmental accessibility checks, where systems need to observe a physical problem, coordinate an authorized response and verify the visible outcome.

**Our vision: A future in which cameras don't just notify people that something happened—they help people understand what it means and whether the problem was resolved.**

---

## Current Demonstration Status

The submitted prototype runs its Ring-compatible pipeline against **our own clearly labeled Ring API emulator**. The current vision evaluation is based on synthetic scenes. We have not established genuine Ring Playground interoperability, clinical performance, or certified physical accessibility measurements.

[Explore the source code and release evidence](https://github.com/navyaa14/clearway-accessgraph)

---

# ClearWay AccessGraph
### Beyond Detection. Beyond Alerts. Beyond Assumed Resolution.

**Observe the obstruction. Understand the consequence. Coordinate the intervention. Verify the outcome.**

**Because for someone depending on an accessible route, a notification isn't enough. What matters is whether the way is actually clear.**
