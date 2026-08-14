# Vision Decision Log

Vision-specific decisions (`VDR-*`). Inherits all accepted `ADR-*` from
[`../awareness-memory/DECISIONS.md`](../awareness-memory/DECISIONS.md) unchanged.
"Accepted (owner)" entries reflect explicit owner selections in the planning
session; "Proposed" entries await ratification at the V0 review gate.

| ID | Status | Decision | Basis / consequence |
|---|---|---|---|
| VDR-001 | Accepted (owner, 2026-07-17) | Vision is a **new sensor source** on the existing awareness backend, not a new subsystem. | Reuses envelope, ingestion, registry, state, telemetry, rules, actions (`VIN-09`). |
| VDR-002 | Accepted (owner, 2026-07-17) | Scope: **presence + occupancy + activity + automation triggers**, with **household re-identification** of enrolled members. | Owner selection. Drives model set (detect + pose + ArcFace re-ID) and phase order. |
| VDR-003 | Accepted (owner, 2026-07-17) | **One camera, living room**, USB/CSI, to start. | Owner statement. USB/CSI tethers compute to the room (`D-V0-2`). |
| VDR-004 | Accepted (owner, 2026-07-17) | Rooms modeled: **`living_room`, `foyer`, `kitchen`**; camera maps to `living_room`. | Owner statement. Registry currently has only `home` → V1 needs a locations migration + source seed (`D-V0-4`). |
| VDR-005 | Accepted (owner, 2026-07-17) | **Frames never persist and never leave the worker; no pixels/embeddings in events.** | Core privacy requirement (`VIN-01`/`VIN-02`, `INV-07`). |
| VDR-006 | Accepted (owner, 2026-07-17) | Household re-ID uses a **consented, encrypted, per-person-deletable** enrollment gallery; identity events are **evidence-class, shorter retention, kill-switchable**. | `VIN-05`. Enrollment designed in V0, implemented in V3 under its own security review. |
| VDR-007 | Proposed | **Develop on the M3 Mac**; defer the deploy-device purchase to end of V1. | Spike shows the workload is not throughput-bound (`THROUGHPUT_SPIKE.md`). |
| VDR-008 | Proposed | If a dedicated device is chosen, prefer a **Jetson Orin Nano-class edge board** co-located with the camera over sharing the RTX 2060. | Best privacy isolation; avoids USB tether to the PC and voice-pipeline contention. |
| VDR-009 | Proposed | Event types **`vision.presence` (state), `vision.occupancy` (telemetry), `vision.activity` (state), `vision.identity` (state, restricted)** per [`reference/EVENT_SCHEMA.md`](reference/EVENT_SCHEMA.md). | Maps cleanly onto existing classification/storage. Ratify at V0 review. |
| VDR-010 | Proposed | Models: **YOLO11 detection + pose** (Ultralytics), **ByteTrack**, **InsightFace ArcFace** re-ID. Weights **licensing is an owner decision before V1** (`D-V0-8`). | Ultralytics AGPL-3.0; `buffalo_l` non-commercial terms — evaluate alternatives if licensing matters. |

## New decision template

```text
ID:
Date:
Status: proposed | accepted | superseded | rejected
Owner/participants:
Phase:
Context and evidence:
Decision:
Alternatives considered:
Consequences:
Invariants/requirements affected:
Supersedes / superseded by:
```
