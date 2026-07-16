# Launcher — Phase 01 Foundation and Database

Execute **Phase 1 only: foundation and database**. Implement the owner-approved typed configuration, local database/migration foundation, in-scope foundational registries/tables, health, and structured logging described in the phase brief.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_01_FOUNDATION_DATABASE.md`, Phase 0 `DISCOVERY.md`, accepted decisions, and only that phase's named references. **Refuse to proceed** if Phase 0 is not complete and owner-reviewed, unless the owner explicitly authorized a waiver and its assumptions/risks are recorded. Inspect relevant existing code/config/tests before editing. Do not read every document, later phases, generated/dependency/cache/model/database/log files, or the complete original by default.

## Permitted changes

Change only configuration/persistence/migration/health/logging code, tests, approved local deployment/config examples, and documentation needed for Phase 1. Match repository conventions, preserve working behavior, and keep the repository runnable.

## Prohibited changes

No MQTT client/intake, state worker behavior, telemetry aggregates, rules/notifications, model memory/context/tools, actions, retention, future placeholders, unrelated refactors, cloud dependencies, hard-coded secrets, or unapproved technology substitution. Do not launch agent teams unless authorized. Do not begin Phase 2.

## Execution discipline

Start with `git status` and preserve unrelated user changes. Use targeted searches and bounded output. Make the smallest coherent repository-native implementation that satisfies the current acceptance criteria; do not create the suggested module tree ahead of need. Keep transactions short, schemas strict, timestamps timezone-aware, retries/batches bounded where present, and configuration secret-free. Inspect each changed migration and generated schema rather than assuming framework defaults preserve required semantics. If an owner-approved Phase 0 choice is missing or extension deployment would materially differ from the recorded plan, stop and report the gate instead of selecting silently. Before handoff, review the diff for future-phase leakage, run the proportional checks, and reconcile every reported result with actual command output.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Create version-controlled migrations and canonical in-scope schemas/constraints/indexes, typed sanitized config, extension/schema checks, truthful health, structured correlation-safe logging, and focused docs. Run clean database and previous-revision migration checks where applicable; config/redaction; constraints/transactions; health up/down; extension/index/schema tests; and relevant regression checks. Record exact commands, dependencies, pass/fail/skip/not-run results. Never weaken tests or claim success for unrun checks.

Update schema/deployment docs, traceability evidence, decisions/questions, `IMPLEMENTATION_STATUS.md`, and a handoff.

## Final response

Report files added/modified; migrations; tables/constraints/indexes/extensions; decisions/assumptions; tests run/passed/failed/not run; failures/limitations; security/deployment effects; repository state; next proposed task; and explicit stop.

Stop at Phase 1. Do not connect MQTT or start Phase 2 automatically.
