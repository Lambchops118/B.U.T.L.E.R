# Phase 07 — Actions

## Purpose

Integrate with the existing automation action layer and ensure every physical command is registered, strictly validated, authorized, confirmed where required, idempotent, acknowledged, timed out truthfully, state-confirmed where possible, and fully audited.

## Entry criteria

Phase 6 is complete/reviewed; the existing action/automation/device command layer and firmware safety boundaries are documented; permission/confirmation/ack policies are owner-approved; status authorizes Phase 7.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), action/outbox schemas in [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and Phase 7 [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load long-term-memory implementation details, full hardening phase, unrelated prompts, or original specification by default.

## Repository discovery required for this phase

Reconfirm existing action registry/tools, actors/permissions, user confirmation flow, allowed device states, MQTT command/ack topics and device semantics, timeout/retry/idempotency support, rollback, state-confirmation signals, and firmware/hardware interlocks.

## In scope

Only missing controls around the existing layer: action definitions and strict parameter schemas; validated request tool/API; actor/permission and confirmation; configured safety/allowed-state/cooldown; durable request/transitions; command/correlation/idempotency IDs; outbox dispatch; MQTT or existing transport; acknowledgement/failure/timeout/cancel; post-ack state confirmation where possible; audit and bounded status reads.

## Explicitly out of scope

Replacing working automation/Home Assistant ownership, arbitrary MQTT/shell/file access, unsupported invented actions, bypassing confirmations, treating silence as success, moving local interlocks to backend/model, automatic broad rollback, new unrelated devices, Phase 8 retention/security work.

## Architectural invariants that apply

INV-02, INV-04 through INV-10, INV-13 through INV-15, INV-17, INV-19.

## Requirements implemented in this phase

R21; ACTION-001 through ACTION-006; action portions of OUTBOX-001/004 and SEC-002/003; FAIL-004 command behavior.

## Dependencies on prior phases

Use existing authenticated tool/API boundary, state reads, provenance, database transaction/outbox, MQTT connection, health, logging, and metrics. Integrate with—not duplicate—the current action service.

## Required deliverables

Registry definitions for supported existing actions; validation/authorization/confirmation service; durable request/transition/outbox dispatch/ack logic; strict tool/API; narrow migrations; simulator/device tests; action safety/lifecycle documentation; status/handoff.

## Detailed implementation requirements

Each action defines name/target/parameters, permission, confirmation, safety checks, cooldown, timeout, rollback behavior, idempotency, and allowed states. A request identifies actor and correlation. Unsupported target/action/parameters/state, insufficient permission, missing confirmation, failed safety check, or cooldown violation is rejected before dispatch.

Persist every transition among requested, validated, awaiting confirmation, approved, dispatched, acknowledged, completed, failed, timed out, and cancelled. A dispatch has unique command ID and idempotency key. Database/outbox intent precedes network work. Consumer/device/application idempotency prevents duplicate physical effects. Acknowledgement requirements are action-specific; silence never means success. After acknowledgement, compare resulting state when the device provides evidence and preserve distinction between acknowledged and completed.

Immediate overflow/electrical/mechanical shutdown remains firmware/hardware behavior. Backend timeout or database/MQTT loss fails safely and reports unknown/failed state; it must not issue an untracked retry that could repeat a non-idempotent action.

Confirmation binds to the exact actor, action, target, normalized parameters, and expiry; a materially changed request requires new validation and confirmation. Late or duplicate acknowledgements remain auditable but cannot incorrectly revive a cancelled/timed-out command. Define which device acknowledgement means receipt versus execution and what state evidence means completion. Rollback is attempted only when registered, safe, and idempotent; its own transitions and failures are recorded rather than hiding the original failure.

## Database or migration effects

Add only action registry/request/transition constraints/indexes and outbox linkage needed for current supported actions. Test clean/upgrade migration and uniqueness. Do not add retention/consolidation schemas.

## Integration boundaries

The LLM calls a narrow structured action request tool; deterministic service owns validation/authorization/dispatch. MQTT payloads are generated from registered definitions, never arbitrary model content. Existing Home Assistant/action ownership remains as approved.

## Failure behavior

MQTT outage preserves approved durable request and reports undispatched/degraded status; database outage blocks actions requiring durable state; missing ack times out; negative ack fails; process crash resumes idempotently; state mismatch after ack remains incomplete/failed per policy.

## Security considerations

Authenticate actors, authorize target/action, protect confirmation tokens, rate-limit, audit every transition, redact secrets, restrict MQTT topics/parameters, and prevent shell/file/SQL escape. Document high-risk actions and local interlocks.

## Required tests

Registry/schema; unsupported action/target/parameter; actor permission; confirmation; safety/allowed state/cooldown; duplicate request/dispatch; positive/negative/missing/late ack; timeout/cancel; process/MQTT/database failure; state confirmation/mismatch; arbitrary MQTT rejection; transition/provenance audit; simulator command ack/failure.

## Acceptance criteria

- Model cannot publish arbitrary MQTT or request unsupported actions.
- Authorization, required confirmation, safety, allowed state, cooldown, and schema validation precede dispatch.
- Duplicate request cannot repeat physical action under the defined device policy.
- No command completes without required acknowledgement; timeout/failure/state mismatch are truthful.
- Every transition/actor/ID is durable and queryable; resulting state is checked where possible.
- Local safety remains in firmware/hardware and no Phase 8 work begins.

## Documentation updates

Document supported registry/actions, permissions/confirmations/safety, command/ack topics and payload ownership, lifecycle/idempotency/timeout/state confirmation, failure recovery, local interlocks, and limitations.

## Implementation status updates

Record action coverage, migrations, real/simulated test scope, transition evidence, safety/security decisions, failures, and review gate.

## Required final report

Files/migrations; supported actions and controls; topics/ack semantics; tests and whether physical hardware was exercised; failures/limitations; security/deployment/safety effects; next proposed phase; stop.

## Stop condition

Stop after Phase 7 evidence, docs, status, and handoff. Do not begin retention, consolidation, final hardening, or Phase 8.
