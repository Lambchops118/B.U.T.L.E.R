# Vision Phase Overview

Bounded, owner-gated phases mirroring the `docs/awareness-memory/` discipline.
Each phase keeps the repo runnable, runs its tests, updates status/handoff, and
**stops for owner review** (`INV-19`). Only V0 is fully specified so far
(`PHASE_V0_DISCOVERY.md`); the rest are scoped outlines to be expanded when the
owner authorizes each.

| Phase | Title | Outcome | Privacy weight |
|---|---|---|---|
| **V0** | Discovery (docs only) | Throughput evidence, ratified event schema, room/registry gap, privacy posture. **Stops for review.** | design only |
| **V1** | Capture + detection + occupancy (anonymous) | Vision worker skeleton; capture → YOLO detect → ByteTrack → debounced `vision.presence` + `vision.occupancy` over MQTT into the existing pipeline. Locations migration (`living_room`/`foyer`/`kitchen`) + source seed. **Complete and useful with zero biometric risk.** | low |
| **V2** | Pose + activity | Add YOLO-pose → coarse `vision.activity` state. | low |
| **V3** | Household re-identification | Enrollment CLI (consented, local), encrypted gallery, face+body matching, `vision.identity` evidence-class events + kill switch. **Gets its own `/security-review`.** | high |
| **V4** | Automation triggers | Rules consume vision-derived state to drive **registered** actions (e.g. occupancy → lights) through the existing confirmation/safety/ack pipeline. No new actuation path. | medium |
| **V5** | Hardening + edge deploy | Port worker to the deploy device, VLAN isolation, MQTT creds/ACL (folds into `BROKER_HARDENING_PLAN.md`), retention tuning for `identity_short`, benchmark, gallery backup coverage. | high |

## Sequencing rationale

- **V1 before identity.** Anonymous presence/occupancy is independently valuable
  and carries no biometric risk — it proves the worker, event schema, debouncing,
  and pipeline integration before any sensitive data exists.
- **Re-ID isolated in V3** so the highest-risk work (biometric templates,
  encryption, retention, kill switch) is reviewed on its own, not entangled with
  plumbing.
- **Automation (V4) never lets vision command hardware** — it only feeds state to
  the existing rules/actions layer (`VIN-04`).
- **Edge deploy last (V5)**, once V1 numbers justify the device choice.

## Deploy-device decision (informed by V0 spike)

The single-camera workload is **not throughput-bound** (see `THROUGHPUT_SPIKE.md`).
The choice is placement + privacy:

- **Dev:** M3 MacBook with the USB camera (proven).
- **Deploy candidate:** dedicated edge device (Jetson Orin Nano-class) co-located
  with the camera — best privacy isolation, frames never leave the room.
- **Alternative:** share the RTX 2060 — fine on compute, but re-tethers the USB
  camera to the PC and contends with the voice pipeline.

Finalize at end of V1.
