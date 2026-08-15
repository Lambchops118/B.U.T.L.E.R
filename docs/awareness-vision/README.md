# Awareness — Vision Extension

This directory specifies **local computer-vision awareness**: giving the home
automation system sight so it can know **who** is in a room, **how many**, and
**what they are doing** — without any video ever leaving the local network.

Vision is **not a new subsystem**. It is a new *sensor source* that feeds the
existing awareness backend (`talos/awareness/`, spec in
[`../awareness-memory/`](../awareness-memory/)). A local **vision edge worker**
watches a camera, runs detection/tracking/pose/re-identification locally, and
publishes small, strict semantic events over MQTT into the pipeline that is
already built (ingestion → state → telemetry → rules/alerts → actions).

> **Frames never persist and never leave the worker.** Only derived, structured
> events (counts, presence, coarse activity, and — for enrolled household
> members — an identity label with a confidence score) cross the wire. No
> pixels, no crops, no raw embeddings appear in any event or database row.

## Why this fits the existing architecture

| Concern | Already handled by the awareness backend |
|---|---|
| Probabilistic input | `EventEnvelope.confidence` + `provenance` are first-class |
| Room occupancy (numeric) | `sensor_measurements` hypertable + 1m/1h/1d caggs |
| Presence / activity / identity (semantic) | `events` + `current_state` |
| No cloud | **INV-07** already mandates local-first inference/storage |
| Vision can't actuate directly | **INV-02 / INV-09 / INV-13** — a rule reads vision state and dispatches a *registered* action with confirmation |
| Source can't spoof topics | Source registry topic-ownership + anti-spoofing (`registry/sources.py`) |

## Authority and organization

The parent [`../awareness-memory/ARCHITECTURAL_INVARIANTS.md`](../awareness-memory/ARCHITECTURAL_INVARIANTS.md)
governs this work unchanged. This directory adds only what is vision-specific:

- [`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md) — vision-only invariants (`VIN-*`), layered on top of the parent `INV-*`.
- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) — authoritative phase/session state.
- [`DECISIONS.md`](DECISIONS.md) — vision decision log (`VDR-*`).
- [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md) — unresolved owner questions (`VOQ-*`).
- [`reference/EVENT_SCHEMA.md`](reference/EVENT_SCHEMA.md) — versioned `vision.*` event/payload schemas.
- [`phases/`](phases/) — bounded, gated implementation specs. **V0 is discovery-only and stops for owner review.**

## Starting or resuming work

1. The owner selects one phase and updates `IMPLEMENTATION_STATUS.md`.
2. The agent reads root `AGENTS.md`, this status, the parent invariants, the selected phase, and only its required references.
3. The agent verifies entry criteria, inspects existing awareness code before editing, implements only in-scope work, runs required tests, and reports truthfully.
4. The agent updates status, decisions/questions, and a dated handoff, submits the phase report, and **stops** for owner review before the next phase.

Phase 0 (`PHASE_V0_DISCOVERY.md`) performs discovery only and must not scaffold
runtime code.
