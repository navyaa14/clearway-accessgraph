# Three-minute demo script

Setup: `docker compose up --build` (or see README), open http://localhost:8000. Household B is selected by default.
Every state change below is produced live by the backend; nothing is pre-scripted.

| Time | Screen | Action / narration |
|---|---|---|
| 0:00–0:25 | Household overview | "A parcel left on the walkway is an annoyance for most people. For someone using a walker it can block the only way out of the house. Ring can tell you a package arrived; it can't tell you whether that package blocks *your* route." |
| 0:25–0:50 | Ring integration (live household) | **Real Playground if you have it.** Otherwise the no-cost path: start with `docker-compose.emulator.yml`, sign in as `hh_live`, and show the violet "Connected to the local Ring API emulator — not Ring" banner. Press *Parcel on walkway edge*: the emulator sends a signed webhook, ClearWay opens a WHEP session (log: 201 Created → ~90 frames → session DELETEd), and the result appears labelled `RING_API_EMULATOR` · "LIVE path · emulator". Say it plainly: "This is ClearWay's real Ring client running against our Ring API emulator, not Ring." |
| 0:50–1:15 | Demo lab → Spatial twin | Run *Parcel delivered on walkway edge* for Household A, then switch to Household B and run it again. Same pixels (same frame hash), different outcome: A needs 60 cm and has ~65 cm → *no obstruction observed*; B needs 91 cm → *potential obstruction*. In Spatial twin show the teal route, the hatched ground footprint (not the bounding box), the amber uncertainty envelope and the *perturbation agreement* (the share of 160 perturbed re-measurements that agree — not a probability). |
| 1:15–1:45 | Incident resolution | *Draft assistance request*. Choose the demo identity "Visitor (not authorised)" → refused and ledgered (the approver's identity comes from their credential, not the form). Approve as Resident → message lands in the **local inbox** (labelled simulated channel). Point out: "Sending help does not close anything." |
| 1:45–2:15 | Incident resolution | *Correction attempt: parcel nudged aside* (labelled synthetic test scene). Clear width rises 65 → 79 cm, but still below 91 cm → **PARTIALLY_RESOLVED**. Open *Why this decision?*: the trail shows the metric change and the reason it stayed open. |
| 2:15–2:35 | Counterfactual planner | Current → proposed (simulated move into the gravel delivery area) → *Correction attempt: parcel moved to delivery zone* → freshly observed: 110 cm clear → **RESOLVED (verified)**. |
| 2:35–3:00 | Proof ledger / Lab | *Verify hash chain* → intact. Lab: *Camera bumped after calibration* → unable to verify; *Duplicate event* → ignored. Show the evaluation card: zero false-clears on the synthetic set, with its disclaimer. Close: "ClearWay doesn't just detect what is at your doorstep. It understands the route that matters to you, helps coordinate a correction, and shows the evidence behind its latest assessment." |

Recording tips: use 1440×900; keep the source and LIVE/DEMO evidence badges visible on every frame shown. Say explicitly which clips are Ring Playground footage and which are labelled test scenes. Do not show the 0:25–0:50 segment as a Ring session unless it is one.

**Optional integrity beat (if time allows):** show that a replay upload cannot be attached to the live Ring camera, and that a live incident needs two independent server-captured clear frames (separate capture sessions, ≥ 60 s apart).

**No-cost variant (emulator):** you can run the whole story on the live path instead of the Demo lab: Ring API emulator controls → *Parcel on walkway edge* → approve help → *Parcel nudged aside* (partially resolved) → *Parcel in delivery area* (1/2 clear) → wait 20 s → *Capture live frame & analyse* (resolved). Or run `python -m ringemu.demo` and record the UI while it plays.

**Recommended structure if you have Playground access (per the audit):** (1) real Playground connection — device discovery, WHEP 201, decoded frames with the Ring watermark, session closed; (2) that Ring frame through the real pipeline, showing whatever classification it genuinely gets (even "review required"); (3) the personalised route / failed correction / recheck sequence using clearly labelled synthetic or independent footage. Never imply the Playground supplied the correction footage.

### Note for the emulator path (v2.3.0)
On the Ring page, "Webhooks as ClearWay processed them" shows each emulator webhook as `v1.1 · COMPLETED → obs_…`.
Say plainly: "This is our Ring API emulator, not Ring. The events use the documented v1.1 shape; a closure here is
labelled *verified on emulator captures — not genuine Ring evidence*." Do not describe emulator footage as Ring footage.
