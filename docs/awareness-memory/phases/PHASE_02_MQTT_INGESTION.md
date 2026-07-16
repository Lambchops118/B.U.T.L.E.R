# Phase 02 — MQTT Ingestion and Event Integrity

## Purpose

Connect safely to the existing Raspberry Pi Mosquitto broker and implement canonical, authorized, idempotent ingestion under duplicate, delay, reorder, restart, retained-message, and malformed-input conditions.

## Entry criteria

Phase 1 is complete/reviewed; database/config/health foundations pass; broker topics/auth/TLS/session ownership are confirmed; status explicitly authorizes Phase 2.

## Required reading

Root `AGENTS.md`, status, this phase, discovery, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), event/registry/history/outbox portions of [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and Phase 2 tests in [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load later phase files, long-term memory/context/action details, or the original specification by default.

## Repository discovery required for this phase

Reconfirm actual broker endpoint, certificates/auth mechanism without exposing secrets, ACL/topic ownership, topic/schema conventions, QoS/session/retain expectations, current clients/adapters, and which remote producers support boot ID, sequence, reliable clocks, or bounded buffering.

## In scope

Existing-broker client, configured subscriptions and graceful reconnect/shutdown, thin source adapters needed for current streams, strict canonical envelope parsing/normalization, size/schema/source/topic authorization, deduplication, boot/sequence/gap/reorder/delay/clock evaluation, retained provenance/freshness classification, immutable event/dead-letter persistence, ingestion metrics/logs, and the ingestion-capable simulator.

## Explicitly out of scope

Second broker; established-topic changes without approved migration; current-state authority/freshness behavior beyond transaction interface; telemetry aggregates; rules/alerts/notifications; model calls/classification; memory; context tools; actions except simulator acknowledgement inputs; unbounded remote queues.

## Architectural invariants that apply

INV-02 through INV-07, INV-09 through INV-11, INV-14, INV-15, INV-17 through INV-19.

## Requirements implemented in this phase

R1, R5, R14, R19; EVT-001 through EVT-006; MQTT-001 through MQTT-008; INGEST-001 through INGEST-006; source identity portions of SEC-001/002; simulator portions of TEST-002.

## Dependencies on prior phases

Use Phase 1 transactions, registries, event/dead-letter tables, typed configuration, health, logging, and uniqueness constraints. Do not recreate them.

## Required deliverables

Repository-native MQTT/adapters/ingestion modules; configuration example keys; simulator modes; database changes only if Phase 1 schema requires an evidence-backed correction; unit/integration/E2E tests; topic/envelope/source-registration documentation; status/handoff.

## Detailed implementation requirements

Subscribe only to approved established topics. Implement persistent reconnect with bounded exponential backoff/jitter, connection health, subscription restoration, appropriate clean-start/session behavior, keepalive, lag/reconnect metrics, graceful shutdown, and credential-safe logs. QoS follows loss tolerance; meaningful state/safety/commands use application idempotency regardless of QoS.

The deterministic stages are receive, parse/size, authenticate/authorize, validate schema, normalize, dedupe, sequence, timestamps/clock, classification, rule interface, transactional persistence and downstream intent. Phase 2 may stop after history/dead-letter and defined interfaces for later state/rules. One accepted event is stored once. Never make network/LLM calls inside its transaction.

Mark retained/replayed origin in provenance and evaluate age; it is not authoritative current state. Generate event IDs before any repository-controlled remote queue. Any store-and-forward is bounded on disk, ordered where useful, duplicate-safe, and explicit about low-value dropping and guarantees; do not claim it for uncontrolled firmware.

Keep source-specific parsing inside thin adapters and normalization inside the ingestion boundary. Record why a message was accepted, duplicated, delayed, out of order, unauthorized, invalid, or dead-lettered using stable classifications that metrics and later investigations can query. Acknowledgement at ingestion, where a source protocol requires it, must describe only the achieved durability/processing stage; it must not imply state change, alert delivery, memory creation, or physical action that belongs to a later phase.

## Database or migration effects

Use existing migrations/constraints. Any corrective migration is narrowly justified, tested clean and upgraded, and reported. Do not add future-phase schemas.

## Integration boundaries

MQTT/adapters produce canonical events; the ingestion transaction calls repositories/interfaces. State, rules, alerts, memory, and actions remain replaceable later-phase consumers. MQTT remains transport; PostgreSQL is authority.

## Failure behavior

Malformed/unauthorized input is dead-lettered without intake crash. Broker outage updates health and retries boundedly. Database outage is truthful; no silent critical loss or unapproved spool. Delayed/reordered events remain available for history, and reboot reset is distinguished from duplicate/gap.

## Security considerations

Authenticate/allowlist source, enforce topic ownership and payload bounds, support configured TLS, redact secrets, bind no new public endpoints, and rate/bound intake. Do not weaken ACLs for tests.

## Required tests

Envelope/version/timestamp/payload and authorization tests; duplicate ID and tuple constraints; gaps/reorder/delay/reboot/clock quality; retained provenance/age; dead-letter continuation; broker reconnect/subscription restore; graceful shutdown; database rollback; simulator modes. Verify no second broker is deployed.

## Acceptance criteria

- The backend connects/reconnects to the existing broker and restores subscriptions.
- A valid event is stored once; duplicates cause no repeated effects.
- Out-of-order/delayed events remain in history; reboot resets and sequence gaps are classified.
- Unauthorized source/topic is rejected and malformed input dead-lettered without crashing.
- Retained messages are provenance-marked and freshness-evaluated.
- Metrics/logs/health reflect intake/rejection/lag/reconnect without secrets.
- The simulator targets the configured existing broker and no future phase begins.

## Documentation updates

Document broker connection, auth/TLS configuration, topic mapping, envelope/provenance, source registration, ordering/delivery guarantees, simulator, troubleshooting, and limitations.

## Implementation status updates

Record topics/sources, files/migrations, test commands/results, broker test conditions, failures, and next permitted task only after owner review.

## Required final report

Files/migrations; topics consumed; schemas/source rules; delivery guarantees; tests; failures/limitations; security/deployment impact; known buffering/clock limits; next proposed phase; stop.

## Stop condition

Stop after Phase 2 evidence, docs, status, and handoff. Do not implement current-state/telemetry behavior or Phase 3.
