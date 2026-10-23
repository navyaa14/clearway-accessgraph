# Public demo checklist (v2.3.1)

Before exposing any instance:
- [ ] Public demo instance runs **without** `RING_*` and `CLEARWAY_LIVE_*` variables (demo households only).
- [ ] Any live instance is behind HTTPS (TLS) **and** a rate-limiting reverse proxy; live tokens kept private.
- [ ] Emulator compose override (`docker-compose.emulator.yml`) only on private/local hosts; change its fixed tokens otherwise.
- [ ] `/api/demo/households` lists only `hh_a`, `hh_b` (never `hh_live`).
- [ ] Ring page shows "Verified against real Ring: No — running against the Ring API emulator" when using the emulator.
- [ ] Manual purge refused with only a household token (403).
- [ ] `docker compose up --build` on a machine with registry access (official bases) → healthcheck green.
- [ ] Rehearse the 3-minute story: route → obstruction (65 cm vs 91 cm) → approved help → first fix 79 cm (partial, stays open) → clear 1/2 → second independent capture → resolved, labelled `EMULATOR_CAPTURE`.
- [ ] Say out loud: emulator, not Ring; synthetic evaluation only; no accessibility certification.
- [ ] If Playground credentials become available: put them in `.env`, remove the emulator override, authorise the camera, capture, and record the WHEP log — only then claim genuine Ring integration.
