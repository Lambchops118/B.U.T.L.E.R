# Phase 01 — Foundation and Database

## Purpose

Create only the typed configuration, durable database/migration foundation, foundational registries/tables, health, and structured logging required by later phases.

## Entry criteria

Phase 0 `DISCOVERY.md` exists, its owner decisions are recorded, and status explicitly authorizes Phase 1. Otherwise refuse to proceed unless the owner explicitly waives the gate and records the assumptions/risk.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/accepted decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/OPERATIONS_AND_DEPLOYMENT.md`](../reference/OPERATIONS_AND_DEPLOYMENT.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and the Phase 1 portion of [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load later phases, memory/context/action details beyond shared table foundations, all prompts, or the original specification unless a mapped requirement is ambiguous.

## Repository discovery required for this phase

Reconfirm the selected dependency/config/migration/ORM/deployment patterns, database extension availability, schema naming, API/health integration, logging/metrics conventions, migration policy, test database, and current dirty worktree before editing.

## In scope

Typed validated configuration; selected local database deployment/connectivity; extension checks; version-controlled migrations; registries and foundational tables for locations, entities/relationships, sources/health, schema registry, immutable events, dead letters, current state, alerts/events, attention, outbox, and necessary audit foundations; health endpoint/service and structured logging.

## Explicitly out of scope

MQTT connection/intake, event pipeline behavior, state authority/freshness workers, telemetry aggregates, rules, notification workers/adapters, memory embeddings/retrieval, context/tools, actions, retention deletion, full security hardening, or empty modules for later phases.

## Architectural invariants that apply

INV-01, INV-02, INV-04 through INV-07, INV-10 through INV-11, INV-13 through INV-19.

## Requirements implemented in this phase

Foundation portions of R2-R6, R14-R18, R21-R22; REG-001 through REG-004; OPS-001 through OPS-005; EVT-001 schema storage; STATE-001 schema; ALERT-001 schema; OUTBOX-001 schema; SEC-001/003/004 foundations; TEST-001 migration coverage.

## Dependencies on prior phases

Use only owner-reviewed Phase 0 choices. If the database/extension choice remains pending, stop rather than silently selecting it.

## Required deliverables

Focused configuration and persistence modules following repository conventions; deployment additions only if approved by discovery; migrations for the in-scope minimum tables/constraints/indexes; health/logging integration; configuration examples without secrets; tests; schema/deployment documentation; status/handoff.

## Detailed implementation requirements

Configuration covers the canonical categories and fails startup with actionable sanitized messages. Use the existing suitable stack or owner-approved source default. Migrations—not runtime table creation—establish the foundation. Preserve distinct timestamp/provenance/state/status columns and relationships from the schema reference. Enforce event ID, source/boot/sequence when present, active state, active alert dedupe where appropriate, and outbox idempotency uniqueness, plus documented query indexes.

The database is authoritative and transactions/concurrency safe. Extension checks distinguish required/unavailable states; migrations follow repository policy rather than applying unexpectedly. Health reports database connectivity, extension/schema state, and truthful degraded status. Structured logs accept canonical correlation identifiers and never secrets. Keep modules focused and avoid global mutable state or circular imports.

Keep persistence layers usable without importing future services: typed schemas define contracts, repositories own bounded database operations, and startup wiring owns resource lifetime. Seed/register only sources and entities supported by reviewed discovery evidence. A table created now to satisfy the canonical minimum does not authorize speculative handlers, APIs, or background work. Document nullable fields and deferred foreign relationships intentionally so later phases can extend them through migrations without erasing required history.

## Database or migration effects

This phase owns the initial migration chain and extension verification. Record exact tables/constraints/indexes and upgrade/rollback implications. Do not add high-volume data behavior or future handlers simply because tables are reserved.

## Integration boundaries

Expose repository-native config, session/repository, health, and logging interfaces for later phases. Do not connect MQTT or call external endpoints. Preserve existing database and health behavior.

## Failure behavior

Database/config/extension failure is explicit and prevents unsafe startup as appropriate. Never claim writes succeeded during database outage. An emergency spool is not in scope unless separately, explicitly authorized.

## Security considerations

No hard-coded secrets; private database binding; least-privilege configuration; sanitized health/logs; input/schema size preparedness; state-changing health/admin behavior follows existing auth. Do not expose PostgreSQL publicly.

## Required tests

Test typed config success/failure and redaction; clean database migration; previous-revision upgrade where one exists; table/enum/constraint/index and extension checks; uniqueness/idempotency constraints; transaction rollback; database health up/down; structured identifiers/no secrets. Use local dependencies only.

## Acceptance criteria

- A clean database is created entirely from migrations and an upgrade path is tested where applicable.
- Required extensions are checked and their absence is reported actionably.
- Foundational fields, statuses, relationships, constraints, and indexes match the canonical schema.
- Health truthfully reports database/extension/schema state.
- Configuration failures are actionable and sanitized; no cloud dependency exists.
- Existing functionality and tests remain working, and no future-phase runtime is added.

## Documentation updates

Document selected stack, local setup, database/schema/migrations, configuration, health/logging, known limitations, and deviations. Update traceability evidence.

## Implementation status updates

Record migration IDs, files, test commands/results, accepted decisions, failures, and only a Phase 2 task as next permitted after owner review.

## Required final report

Files added/modified; migrations; tables/constraints/indexes/extensions; decisions/assumptions; tests passed/failed/not run; limitations; security/deployment effects; known failures; next proposed phase; explicit stop.

## Stop condition

Stop when Phase 1 acceptance evidence, documentation, status, and handoff are complete. Do not connect to MQTT or begin Phase 2.
