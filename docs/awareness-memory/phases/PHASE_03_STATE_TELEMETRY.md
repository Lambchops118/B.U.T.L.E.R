# Phase 03 — Current State and Telemetry

## Purpose

Implement durable qualified current state, freshness/source health, conflict, meaningful transitions, numeric telemetry, aggregates, and bounded exact history queries without confusing now with the past.

## Entry criteria

Phase 2 is complete/reviewed; canonical events and ordering/provenance evidence pass; source thresholds/authority and Timescale deployment choices are confirmed; status authorizes Phase 3.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), current-state/history/telemetry schemas in [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), relevant [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md), and Phase 3 [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load Phase 4-8 details, prompts, memory/context/action schemas, or the original specification unless targeted ambiguity requires it.

## Repository discovery required for this phase

Reconfirm source priority/authority, expected intervals, stale/offline thresholds, clocks, units, numeric deadbands/hysteresis, event volumes, Timescale/alternative capabilities, query API conventions, and existing historical data/migrations.

## In scope

Current-state authority/update manager; provenance and source-event linkage; freshness deadlines and deterministic stale/offline worker; conflict/unknown/inferred/scheduled statuses; source health transitions; delayed-event protection; meaningful state transitions; deadbands/hysteresis; numeric telemetry storage/batching; one-minute/hourly/daily aggregate foundations; bounded/paginated exact event and sensor-history queries.

## Explicitly out of scope

Deterministic business/safety rules beyond freshness transition hooks, alert/notification policy implementation, natural-language situation/context, vector/memory retrieval, actions, final retention deletion, broad dashboards, or future-phase placeholders.

## Architectural invariants that apply

INV-01 through INV-06, INV-10 through INV-12, INV-14, INV-16 through INV-19.

## Requirements implemented in this phase

R2-R6, R14, R16; STATE-001 through STATE-008; HIST-001 through HIST-006; source-health parts of REG-004/005 and FAIL-007/008.

## Dependencies on prior phases

Consume Phase 2 canonical events and classifications. Use Phase 1 tables/migrations/config/health. Preserve immutable history even when state update is rejected.

## Required deliverables

Focused state/freshness/conflict and history/telemetry/aggregate/query modules; required narrow migrations; deterministic workers; unit/integration/E2E tests; state/freshness/telemetry/query documentation; status/handoff.

## Detailed implementation requirements

For each `(entity_id, property_name)`, one active durable state row records typed value, distinct times/validity, confidence, source/event, status, authority, and metadata. Compare time using clock quality, receipt time where necessary, source priority, authority, and confidence. Store every accepted delayed/out-of-order event in history but do not let an older/weaker event replace valid newer state. Represent unresolved conflicts rather than inventing certainty.

Freshness uses per-source/property deadlines to mark values stale and sources offline, updates derived entity status, emits meaningful transition events, and invokes later alert interfaces without repeatedly opening incidents. Answers/accessors never return stale/offline data as unqualified current. Numeric deadband/hysteresis suppresses jitter but not meaningful threshold transitions.

Telemetry preserves time/receipt/entity/source/name/typed value/unit/quality/confidence/source-event/metadata. Use approved hypertables/indexes/batching. Create configured minute/hour/day aggregates with min/max/avg/count/stddev and useful threshold counts. Do not embed telemetry. Exact queries require time range, pagination or point maximum, allowed aggregation/interval, and provenance/qualification.

Read results must expose an explicit `as_of` and enough age/expiry/source information for callers to distinguish current from last known. Define deterministic behavior at exact stale/offline boundaries and during worker restart. Aggregates retain bucket/timezone/unit semantics and never mix incompatible units or quality classes silently. Replay or backfill must be idempotent and must not emit false present-time transitions. Measure query and worker behavior with representative volumes; if a bound is provisional, configure and document it instead of leaving it unlimited.

## Database or migration effects

Add only evidence-backed state/history/telemetry/aggregate/index changes, with clean and previous-revision migration tests. Do not implement retention deletion yet.

## Integration boundaries

State/history services expose typed bounded repository-native reads for later rules/context. They do not produce model prose or call Ollama. Alert hooks remain interfaces until Phase 4.

## Failure behavior

Database failures are truthful and prevent durable state claims. Worker crashes resume idempotently. Clock drift, delay, silence, and conflict remain visible. Under load, bound queues/batches and shed only configured low-value telemetry—never critical events.

## Security considerations

Bound time ranges, points, pagination, payloads, and location/entity access under existing authorization. Preserve privacy/retention classifications and do not expose arbitrary SQL.

## Required tests

Authority/newness/source priority; delayed event history/state protection; every status; stale/offline deadlines and deduplicated transitions; conflict; source linkage; deadband/hysteresis; telemetry typed insert/batch; aggregates; maximum range/points; pagination; migration clean/upgrade; restart/idempotent worker.

## Acceptance criteria

- Current state is durable and separate from immutable history.
- Delayed/weaker data cannot incorrectly replace newer authoritative state.
- Stale/offline transitions occur at configured thresholds and results are qualified.
- Conflict and all required statuses are represented; provenance is retained.
- Numeric jitter does not create meaningful transition noise.
- Telemetry/aggregates are queryable without embedding; results/ranges are bounded.
- Repository remains runnable and no Phase 4 behavior is implemented.

## Documentation updates

Document current-state authority, timestamp/freshness/conflict semantics, source health, event history, telemetry schema/aggregates, query limits, migrations, and known volume/capacity limits.

## Implementation status updates

Record migrations, policies/threshold sources, tests/results, known stale/clock limits, failures, and owner-review gate.

## Required final report

Files/migrations; state/telemetry model; authority/freshness decisions; query/aggregate limits; tests; failures/limitations; security/deployment effects; next proposed phase; stop.

## Stop condition

Stop after Phase 3 acceptance evidence, docs, status, and handoff. Do not implement rules, alerts, notification workers, or Phase 4.
