# Launcher — Phase 03 Current State and Telemetry

Execute **Phase 3 only: current state, source health, telemetry, aggregates, and bounded history queries**. Preserve the strict distinction between now, immutable history, and numeric telemetry.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_03_STATE_TELEMETRY.md`, discovery/decisions, and only its listed shared references. Verify Phase 2 is complete/reviewed, canonical intake/order/provenance tests pass, authority/threshold/clock/Timescale choices are confirmed, and status authorizes Phase 3. Inspect relevant state/history code first. Do not load later phases, prompts, the full original, or unrelated/generated/large files.

## Permitted changes

Implement focused state authority/freshness/conflict/source-health/transitions, deadband/hysteresis, telemetry/batching/aggregates, bounded exact queries, necessary narrow migrations, tests, and docs.

## Prohibited changes

No Phase 4 business/safety rule engine or notifications, model context/memory, vector search, actions, final retention deletion, dashboards, placeholders, unrelated refactors, agent teams without authorization, or next-phase work.

## Execution discipline

Start with `git status`, preserve unrelated user changes, and inspect only relevant state/history/migration paths with bounded output. Treat authority, freshness, clock quality, and confidence as explicit policy inputs, not convenient timestamp comparisons. Preserve every accepted event even when state is unchanged, and distinguish event receipt from observation and validity. Use database constraints plus transactional logic, keep workers resumable/idempotent, and avoid network/model calls. For telemetry, verify units, typed values, batching, indexes, aggregation correctness, and enforced query bounds against representative data. If source priority, thresholds, or extension capability is not approved, stop on that bounded decision rather than inventing it. Before handoff, inspect the diff for rule/notification/context leakage and match all claims to test evidence.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Deliver one durable active state per entity/property with all required statuses, distinct timestamps/validity, authority/confidence/provenance, delay protection, deterministic stale/offline worker, meaningful transitions, conflict, and jitter control. Store typed numeric telemetry and configured aggregates; require time/result bounds and never embed telemetry.

Test authority/newness/source priority, delayed history without state regression, every status, freshness/offline thresholds, conflict, source linkage, deadband/hysteresis, typed telemetry/batches/aggregates, range/point limits, pagination, migration upgrade/clean, and restart/idempotency. Record exact commands and pass/fail/not-run evidence.

Update state/freshness/history/telemetry/query/migration/capacity docs, traceability, status, decisions/questions, and handoff.

## Final response

Report files/migrations; authority/freshness/status/telemetry behavior; query/aggregate limits; tests; failures and clock/volume limitations; security/deployment implications; repository state; next proposed task; explicit stop.

Stop after Phase 3. Do not implement rules, alerts, notifications, or Phase 4.
