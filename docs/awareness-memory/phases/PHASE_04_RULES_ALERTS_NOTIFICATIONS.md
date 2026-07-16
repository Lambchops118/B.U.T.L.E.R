# Phase 04 — Rules, Alerts, Attention, and Notifications

## Purpose

Implement deterministic classification/rules, persistent incident and attention lifecycles, reliable proactive notification through existing channels, and the outbox behavior that makes critical delivery survive crashes and Ollama outages.

## Entry criteria

Phase 3 is complete/reviewed; qualified state/history/source health are stable; rule and notification policies/channels are confirmed; outbox foundation exists; status authorizes Phase 4.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), policy/alert/attention/notification/outbox schemas in [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and Phase 4 [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load memory/context/action/retention phase briefs, all prompts, or original spec by default.

## Repository discovery required for this phase

Reconfirm existing rule/automation ownership, notification interfaces/endpoints/delivery semantics, active calls/conversations/quiet hours, alert acknowledgement/resolution paths, source criticality, escalation/cooldown policy, and worker/process conventions.

## In scope

Strict version-controlled deterministic rules and classification/salience; hard-rule precedence; alert open/update/acknowledge/suppress/resolve/expire; incident deduplication/evidence/counts; attention priority/availability/expiry/interruptibility/channel/cooldown; adapters only for existing notification channels; deterministic fallback messages; delivery attempts/results; cooldown/rate/quiet/conversation suppression; retry/channel fallback/escalation; outbox claim/backoff/stale-lock/dead-letter/manual-retry behavior required for notification reliability; overflow scenario.

## Explicitly out of scope

LLM hot-path decisions, permanent memory, context injection implementation, new unsupported notification services, arbitrary automated actions, action lifecycle, retention/consolidation, or future placeholder adapters.

## Architectural invariants that apply

INV-02, INV-04 through INV-10, INV-13 through INV-15, INV-17, INV-19.

## Requirements implemented in this phase

R7, R8, R11, R13, R15; RULE-001 through RULE-004; ALERT-001 through ALERT-004; NOTIFY-001 through NOTIFY-005; OUTBOX-001 through OUTBOX-004; relevant FAIL-001/002/006.

## Dependencies on prior phases

Rules consume canonical events and qualified state/source health. Alert, attention, notification intent, and outbox work are persisted transactionally using Phase 1-3 foundations.

## Required deliverables

Focused rule/alert/attention/notification/outbox handler modules; configured policies and existing-channel adapters; narrow migrations if needed; overflow E2E and crash/outage tests; lifecycle/retry documentation; status/handoff.

## Detailed implementation requirements

Separate hard safety/operational rules, storage/downstream actions, noncritical salience, retention class, and interruptibility. Rules use strict typed or versioned config and support thresholds, transitions/rates/windows/missing data/health/correlation, dedupe/cooldown/escalation, acknowledgement/resolution/suppression. Optional model classification is asynchronous and noncritical; it cannot block, override hard rules, directly notify, or create memory.

One persistent condition maps to one active incident keyed by policy. Track first/last seen, occurrence count, and supporting events. Attention is distinct from alert state: overflow and critical sensor loss may be immediate; noncritical offline/low battery may wait/passive; calls/conversations suppress only as policy permits.

Within the event transaction, create/update alert/attention and a unique notification outbox item. A worker claims boundedly, renders deterministic wording, sends via the existing adapter, persists the attempt/confirmation/error, and retries boundedly. Crash after commit cannot lose work; duplicate execution cannot spam outside policy. Never mark delivered without adapter confirmation or let Ollama availability delay the fallback.

Fallback templates use validated fields and remain understandable without generated prose; missing optional display data must not break a critical notification. Record policy/rule version with the derived alert or supporting event so behavior is reproducible. Resolution and acknowledgement are distinct: acknowledgement does not erase an active condition, and automatic resolution requires deterministic evidence. Rate limiting and quiet-hour suppression must never silently discard a critical incident; they change delivery timing/channel only according to explicit policy and remain auditable.

## Database or migration effects

Add only missing alert-event, notification-delivery, policy, and outbox constraints/indexes needed now. Test migration upgrades. No memory/action/retention schema expansion.

## Integration boundaries

Use one typed adapter interface around existing channels. Rules may emit a later action request only through an existing deterministic validated boundary; Phase 7 owns general LLM action requests. Keep network calls outside transactions.

## Failure behavior

Ollama outage leaves deterministic path intact. Notification failure keeps alert open, records/retries, and escalates/falls back when configured. Worker/process crash recovers durable rows. Unrecoverable work is dead-lettered visibly; no infinite retry or secret-bearing error.

## Security considerations

Authorize acknowledgement/resolution endpoints; restrict recipient/endpoints to configured adapters; rate-limit and redact notification data; do not allow rule text/templates to produce arbitrary execution.

## Required tests

Rule schema/matching/hard precedence; dedupe/cooldown/escalation; alert lifecycle and occurrence evidence; attention interruptibility; deterministic wording; adapter confirmation/failure; outbox concurrent claim/backoff/stale lock/manual retry/dead letter; duplicate event; overflow with Ollama down; crash after event commit; unresolved alert persistence.

## Acceptance criteria

- Overflow stores event/state and opens one critical alert plus immediate attention.
- Fallback notification is attempted/delivered without Ollama and every result is persisted truthfully.
- Repeated signals update one incident without spam.
- Crash between event processing and send cannot lose the notification; retry is idempotent/bounded.
- Cooldown, quiet/conversation suppression, acknowledgement, resolution, fallback/escalation follow configured policy.
- No future memory/context/action phase is implemented.

## Documentation updates

Document policy schema, classification/salience boundary, alert/attention lifecycle, channel semantics, deterministic templates, retry/outbox/error/manual-retry behavior, and known notification limitations.

## Implementation status updates

Record rule/channel coverage, files/migrations, outage/crash test evidence, failures, and review gate.

## Required final report

Files/migrations; rules and incident semantics; outbox work/adapters/channels; tests; delivery evidence/failures; limitations; security/deployment changes; next proposed phase; stop.

## Stop condition

Stop after Phase 4 evidence, docs, status, and handoff. Do not implement situation/context/read tools or Phase 5.
