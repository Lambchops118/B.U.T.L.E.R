# Phase V0 — Vision Discovery (documentation only)

## Purpose

Establish evidence-backed facts about the camera, local compute, model
throughput, room/location modeling, MQTT source integration, and the privacy
posture for household re-identification **before any runtime vision code is
written**. Produce the discovery findings, ratify the event schema, and **stop
for owner review**. This phase changes documentation only.

## Entry criteria

- The owner has assigned Phase V0.
- [`../IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) does not show V0 completed and reviewed.
- No runtime vision implementation is authorized by this phase.

## Required reading

Root `AGENTS.md`; the parent
[`../../awareness-memory/ARCHITECTURAL_INVARIANTS.md`](../../awareness-memory/ARCHITECTURAL_INVARIANTS.md);
[`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md);
[`../reference/EVENT_SCHEMA.md`](../reference/EVENT_SCHEMA.md);
existing awareness code: `schemas/events.py`, `registry/sources.py`,
`registry/bootstrap.py`, `ingestion/`, `state/`, `history/telemetry.py`,
`db/models.py`.

## In scope

- Read-only inspection of the existing awareness backend and its extension points.
- Safe, ephemeral local benchmarks (no repo runtime code, no persisted frames).
- Documentation of camera, compute, model, network, and privacy facts.
- Ratifying the `vision.*` event schema draft.
- Proposing (not creating) the locations/entities/source registry additions.

## Explicitly out of scope

No vision worker code, no dependencies added to the repo, no migrations, no
registry seed edits, no MQTT topics created, no camera capture in repo code, no
Phase V1 scaffolding. Enrollment of real biometric data is **not** done in V0.

## Discovery items

### D-V0-1 — Camera
- Interface (USB / CSI), model, native resolution, achievable FPS, field of view.
- Confirmed: **one camera, living room.** Dev uses the Mac's attached USB camera.
- Whether the camera itself has any network/cloud behavior (USB webcams: none; note for future IP cameras).

### D-V0-2 — Compute placement (the key constraint)
- USB/CSI **tethers the compute to the camera's physical location.** Document the implication: a central-GPU model only works if the box lives by the camera.
- Candidate hosts: M3 MacBook (dev), RTX 2060/5080 PC, or a dedicated edge device.
- Measure model throughput (D-V0-3) and record a recommendation; **defer the purchase decision to end of V1** when real pipeline numbers exist.

### D-V0-3 — Model throughput (evidence)
- Benchmark, on the dev Mac, per-stage forward cost for: YOLO person detection, YOLO-pose, and ArcFace re-ID (detector + embedding).
- Record device (MPS/CPU), p50/p95 ms, FPS, and an estimated combined per-frame budget.
- Restate that presence/activity need only **2–5 FPS**, so the ceiling has large headroom.
- Result location: `THROUGHPUT_SPIKE.md` (this phase's evidence artifact).

### D-V0-4 — Room / location modeling
- **Finding:** the registry currently models everything under a single `location_id = "home"` (`registry/bootstrap.py`). Rooms are not registered.
- Rooms to introduce: `living_room`, `foyer`, `kitchen`. V1 needs a **locations migration + registry seed** adding these and the `vision_living_room` source mapped to `living_room`.
- Decide whether the room is also an `entity` (for `current_state` rows) or state is location-scoped (`VOQ-3`).

### D-V0-5 — MQTT source integration
- New source: `source_id = vision_living_room`, `source_type = vision`, `allowed_topics = ["vision/living_room/#"]`, `clock_quality` per D-V0-6.
- Confirm topic-ownership + anti-spoofing behavior (`registry/sources.py`) covers the new topics with no change to existing ones (`status/*`, `home/sim/#`).
- Fold the worker's broker credentials/ACL into the existing `BROKER_HARDENING_PLAN.md` (own username, ACL limited to `vision/living_room/#`).

### D-V0-6 — Clock quality
- If the worker host runs NTP, `observed_at` is trusted and `clock_quality = device_synced` (per the memory backend, comparison time = `observed_at` for `device_synced`). Otherwise `server_received`. Record which applies to the dev Mac and to the intended deploy device.

### D-V0-7 — Privacy & identity posture
- Ratify `VIN-01`..`VIN-08`: frames never persist/leave; no pixels/embeddings in events; encrypted, consented, revocable gallery; identity evidence-class + kill switch.
- Propose retention classes: anonymous occupancy/presence (normal telemetry retention) vs. `identity_short` (shorter, evidence-protected).
- Note the enrollment flow design (explicit local CLI, N frames per named person, per-person delete) — **design only in V0, no real enrollment.**

### D-V0-8 — Model licensing / provenance
- Record the license of chosen weights (e.g. Ultralytics YOLO AGPL-3.0 vs. alternatives; InsightFace model-pack terms) so the owner can decide before V1 pins them. This is an owner decision, not an agent one.

## Required deliverables

1. `THROUGHPUT_SPIKE.md` — the benchmark evidence (device, per-stage ms/FPS, combined estimate, recommendation).
2. Ratified [`../reference/EVENT_SCHEMA.md`](../reference/EVENT_SCHEMA.md) (mark DRAFT → ratified, or record deltas).
3. Updated [`../DECISIONS.md`](../DECISIONS.md) and [`../OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md).
4. Updated [`../IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) and a dated session handoff.

## Acceptance criteria

- Every discovery item answered or explicitly marked unknown with a confirmation path.
- Throughput evidence present with device and percentiles, not guesses.
- Room/location gap named with the concrete V1 migration it implies.
- Privacy invariants ratified; identity retention + kill switch + enrollment design recorded.
- No repo runtime/config/dependency/migration file changed; no enrollment of real biometric data.

## Owner decision checklist (blocks V1)

- [ ] Approve model choices + licenses (`D-V0-8`).
- [ ] Approve deploy compute direction (share 2060 vs. dedicated edge device), or defer to end of V1.
- [ ] Approve the three-room location model + migration for V1.
- [ ] Approve identity posture: household re-ID with encrypted gallery, `identity_short` retention, kill switch.
- [ ] Approve broker ACL addition for the vision source.

## Stop condition

After the deliverables above are complete, **stop**. Do not scaffold or
implement Phase V1 until the owner reviews V0 and explicitly authorizes
continuation (per root `CLAUDE.md` and `INV-19`/`INV-20`).
