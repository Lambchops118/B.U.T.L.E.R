# Session Handoff — 2026-07-17 (Vision V0 scaffold)

```text
Session goal:        Plan local computer-vision awareness; scaffold docs/awareness-vision/;
                     run a throughput spike on the dev Mac.
Current phase:       V0 — Discovery (documentation only). Stopped at the review gate.
Bounded task completed:
                     - Created docs/awareness-vision/ scaffold following awareness-memory conventions.
                     - Measured per-stage model throughput on the Mac (ephemeral, out-of-repo).
Files added:         docs/awareness-vision/README.md
                     docs/awareness-vision/ARCHITECTURAL_INVARIANTS.md   (VIN-01..09)
                     docs/awareness-vision/DECISIONS.md                  (VDR-001..010)
                     docs/awareness-vision/OPEN_QUESTIONS.md             (VOQ-1..9)
                     docs/awareness-vision/IMPLEMENTATION_STATUS.md
                     docs/awareness-vision/SESSION_HANDOFF_2026-07-17.md (this file)
                     docs/awareness-vision/reference/EVENT_SCHEMA.md     (vision.* payloads, draft v1)
                     docs/awareness-vision/phases/PHASE_V0_DISCOVERY.md
                     docs/awareness-vision/phases/PHASE_OVERVIEW.md      (V0..V5)
                     docs/awareness-vision/phases/THROUGHPUT_SPIKE.md    (evidence)
Files modified:      None outside docs/awareness-vision/.
Migrations added:    None.
Decisions made:      VDR-001..006 accepted (owner selections this session);
                     VDR-007..010 proposed, pending V0 review.
Assumptions:         1 USB/CSI camera, living room; rooms = living_room/foyer/kitchen;
                     registry currently only models "home" (registry/bootstrap.py).
Tests run:           None in-repo. Ephemeral benchmark only (scratchpad/spike.py,
                     isolated venv); results in THROUGHPUT_SPIKE.md.
Tests passed:        n/a (no repo tests touched).
Tests failed:        None.
Commands not run:    No repo dependency installs, no migrations, no registry edits,
                     no live camera capture in repo code, no real biometric enrollment.
Known limitations:   Spike used synthetic frames (not a live camera); InsightFace ran on
                     onnxruntime CPU (CoreML available but not engaged) so re-ID numbers
                     are a conservative floor. Weights caches downloaded outside the repo
                     (~/.insightface/models/buffalo_l ~280MB).
Security implications: No biometric data created or stored. Privacy invariants (VIN-01..08)
                     drafted, not yet enforced by code. Identity work is deferred to V3
                     with its own security review.
Deployment implications: Single-camera workload is not throughput-bound on any candidate
                     host; compute choice is placement + privacy driven. Deploy-device
                     purchase deferred to end of V1.
Unresolved questions: VOQ-1..9 (see OPEN_QUESTIONS.md); owner-decision checklist in
                     PHASE_V0_DISCOVERY.md.
Current repository state: Clean except new docs/awareness-vision/ files. No runtime change.
Next permitted task: Owner review of V0. On approval, Phase V1 (anonymous capture +
                     detection + occupancy) with locations migration + vision source seed.
Required reading for next session: root AGENTS.md, this handoff, IMPLEMENTATION_STATUS.md,
                     parent + vision invariants, PHASE_V0_DISCOVERY.md, reference/EVENT_SCHEMA.md,
                     existing talos/awareness/{schemas,registry,ingestion,state,history}.
Explicit stop point: V0 is documentation-only. Do not begin V1 until the owner reviews
                     V0 and explicitly authorizes it (CLAUDE.md, INV-19/INV-20).
```
