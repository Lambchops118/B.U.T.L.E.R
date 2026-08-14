# Vision Implementation Status

This file reports implementation state for the **vision extension**, separate
from the (complete) `docs/awareness-memory/` subsystem.

| Field | Current value |
|---|---|
| Current phase | Phase V0 — Discovery. **Scaffold + throughput spike done; awaiting owner review.** |
| Phase state | Planning/discovery scaffolding created this session. No runtime vision code exists. |
| Last completed phase | None (V0 in progress) |
| Current bounded task | V0 discovery documentation + throughput evidence. |
| Completed items | Scaffold docs (`README`, invariants `VIN-*`, `EVENT_SCHEMA` draft, `PHASE_V0_DISCOVERY`, `PHASE_OVERVIEW`, `DECISIONS`, `OPEN_QUESTIONS`, this status, handoff); **throughput spike measured on the dev Mac** (`phases/THROUGHPUT_SPIKE.md`). |
| Active work | None — stopped at the V0 review gate. |
| Blocked items | V1 blocked on owner review of V0 and the owner-decision checklist in `PHASE_V0_DISCOVERY.md`. |
| Decisions made | `VDR-001..006` accepted (owner selections); `VDR-007..010` proposed. See `DECISIONS.md`. |
| Assumptions | Single USB/CSI camera, living room; rooms = living_room/foyer/kitchen; registry currently only has `home` (confirmed in `registry/bootstrap.py`). |
| Open questions | `VOQ-1..9` in `OPEN_QUESTIONS.md` (licensing, deploy device, state modeling, clock, identity retention, enrollment UX, FoV, future cameras, body re-ID). |
| Tests last run | None (documentation + ephemeral out-of-repo benchmark only; no repo code changed). |
| Known failures | None. |
| Evidence | `phases/THROUGHPUT_SPIKE.md`: YOLO detect 136 FPS / pose 121 FPS on MPS; InsightFace re-ID on CPU (detector 8.7 FPS, embed 12.9 FPS/face); amortized full pipeline ~14 FPS vs. 2–5 FPS needed. |
| Files added this session | `docs/awareness-vision/**` (docs only). No `talos/` or migration changes. |
| Next permitted task | **Owner review of V0.** On approval, Phase V1 (capture + detection + occupancy, anonymous) including the locations migration + `vision_living_room` source seed. |
| Required reading for V1 | Root `AGENTS.md`, this status, parent + vision invariants, `PHASE_V0_DISCOVERY.md`, `reference/EVENT_SCHEMA.md`, existing `talos/awareness/{schemas,registry,ingestion,state,history}`. |
| Explicit stop condition | V0 is documentation-only. Do not scaffold or implement V1 until the owner reviews V0 and explicitly authorizes it (`CLAUDE.md`, `INV-19`/`INV-20`). |

Do not infer implementation progress from the presence of these specification
files. No vision runtime exists yet.
