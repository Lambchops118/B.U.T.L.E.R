# Launcher — Phase 07 Actions

Execute **Phase 7 only: integrate missing controls into the existing action layer** so physical actions are registered, strict, authorized, confirmed when required, safe, idempotent, acknowledged, timed out, state-confirmed where possible, and audited.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_07_ACTIONS.md`, discovery/decisions, and only its action/outbox/failure/security/test references. Verify Phase 6 completion/review, documented existing action/device/firmware boundaries, owner-approved permissions/confirmation/ack policy, and Phase 7 authorization. Inspect existing action code and device protocols first. Do not load unrelated phases, full original, or large/generated files.

## Permitted changes

Implement only missing registry/validation/authorization/confirmation/safety/cooldown, durable lifecycle/outbox dispatch, command/ack/timeout/state confirmation, strict API/tool, narrow migrations, simulator/device tests, and action documentation.

## Prohibited changes

No replacement of working automation/Home Assistant ownership, arbitrary MQTT/shell/file/SQL, invented actions, confirmation bypass, silence-as-success, backend substitution for firmware/hardware interlocks, unrelated devices, Phase 8 work, placeholders, unrelated refactors, or unauthorized agent teams.

## Execution discipline

Start with `git status`, preserve unrelated changes, and inspect the real action/device contract before editing. Prefer adapters around the established action layer and define supported capability explicitly. Validate and persist actor, target, parameters, permission, confirmation, allowed state, safety, cooldown, timeout, idempotency, correlation, and acknowledgement requirements before dispatch. Keep network work outside the transaction and never retry a potentially non-idempotent physical operation without a proven device/application contract. Distinguish accepted, acknowledged, completed, failed, timed out, and unknown evidence. If hardware cannot safely be exercised, use the simulator/stub, state that boundary, and do not claim physical success. Before handoff, audit arbitrary-execution paths, confirmation bypasses, local-interlock assumptions, future hardening work, and report/test consistency.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Deliver supported action definitions, actor and correlation provenance, pre-dispatch validation, every required durable transition, command/idempotency identifiers, adapter-generated payloads, acknowledgement/negative/timeout/cancel, idempotent crash recovery, and post-ack state verification when available.

Test unsupported/unauthorized/invalid/unconfirmed/safety/cooldown paths; duplicate request/dispatch; positive/negative/missing/late ack; timeout/cancel; process/MQTT/database failures; state match/mismatch; arbitrary publishing rejection; audit; simulator and clearly identified real-hardware scope. Never claim a physical effect not observed.

Update action registry/lifecycle/topic/ack/safety/failure docs, traceability, status, decisions/questions, and handoff.

## Final response

Report files/migrations; supported actions/controls/topics/ack semantics; tests and hardware scope; failures/limitations; safety/security/deployment effects; repository state; next proposed task; explicit stop.

Stop after Phase 7. Do not begin retention/hardening or Phase 8.
