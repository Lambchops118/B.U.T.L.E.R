# Phase 09 — Proactive Briefing and Model-Selected Salience

## Purpose

Give the system the ability to decide **what is worth saying, unprompted**, at
moments that occur deterministically. Today every spoken output is either a
direct reply or a fixed payload rendered by a rule; the morning report
(`talos/scheduler/tasks.py:morning_report_job`) reads a hardcoded list of
fields regardless of whether anything in it matters. This phase separates the
*moment* (deterministic) from the *content* (selected), and introduces the
first sanctioned model call in a proactive output path — bounded, auditable,
and never able to invent a fact or suppress a critical one.

The owner's goal for this work is a system that feels present rather than
reactive. The architectural means to that end is not less determinism in the
plumbing; it is judgment applied at the selection layer, over a candidate set
the deterministic layer assembled and can prove.

## Entry criteria

Phases 0-8 complete. The 2026-09-06 human-context follow-on is merged and
reviewed: presence/interaction/agent-outcome signals reach the subsystem,
`POST /ingest` exists, and the situation broker honors `interruptibility` and
`conversation_relevance` (see `SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md`
and ADR-028..032). Status authorizes Phase 9.

## Required reading

Root `AGENTS.md`, `IMPLEMENTATION_STATUS.md`, this phase document,
[`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md),
ADR-028..032 in [`../DECISIONS.md`](../DECISIONS.md), the
`SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md` handoff, the "Human context"
and "Situation, context, and read tools" sections of
[`talos/awareness/README.md`](../../../talos/awareness/README.md), and
[`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load Phases 1-8 briefs, the original specification, the barge-in or
debug-dashboard handoffs, or broker hardening material. Phase 5's context
broker is read through its code, not its phase document.

## Repository discovery required for this phase

Before editing, confirm by inspection: the situation broker's candidate and
audit shape (`talos/awareness/context/broker.py`); the attention lifecycle and
`delivery_status` handling (`talos/awareness/alerts/service.py`); outbox work
types and the notification adapters (`talos/awareness/outbox/worker.py`,
`talos/awareness/notifications/`); the reminder worker as the existing example
of a due-time trigger; the continuous aggregates available for measurements;
how `morning_report_job` currently reaches the voice path via `central_queue`;
and the embedding client as the existing precedent for calling Ollama from
this backend with truthful degradation.

## The shape this phase implements

Every case the owner described reduces to one pipeline:

```
deterministic trigger
    -> deterministic candidate assembly (bounded, sourced, timestamped)
        -> model selection and phrasing (bounded, auditable, optional)
            -> deterministic delivery (existing notification egress)
                -> record what was said (so it is never said twice)
```

Three motivating cases, all the same pipeline:

| Case | Trigger | What the model decides |
|---|---|---|
| Morning briefing | cron time | which of the night's candidates are worth reporting |
| Homecoming briefing | presence transition absent/stale -> present | what happened during the day that matters |
| Novelty | an event that is statistically unusual or matches no rule | whether an unusual thing is an *interesting* thing |

## In scope

Sub-phase 9A — **Briefing assembler.** A deterministic service producing a
bounded, sourced, timestamped candidate set for a named window ("since the
last briefing of this kind"), drawing from `state_transitions`, `alerts`,
`attention_items`, `events` (including `agent.*` and `person.interaction.*`),
and measurement aggregates. No model call. No delivery.

Sub-phase 9B — **Moment triggers and delivery bookkeeping.** A due-time
trigger for scheduled briefings and a state-transition trigger for arrival,
both following the existing reminder-worker pattern; a hard cap on items per
briefing; and marking `attention_items.delivery_status` so a delivered item is
never re-reported.

Sub-phase 9C — **Model selection.** An optional, asynchronous, bounded
selection step over the 9A candidate set, invoked from the outbox worker,
choosing which candidates to speak and returning selection reasons. Includes
prompt versioning, decision provenance, and deterministic fallback.

Sub-phase 9D — **Feedback loop.** Dismissal and interest signals from the user
recorded as memories, read by 9C on subsequent briefings.

## Explicitly out of scope

Model-driven *detection* of any kind: no model call may decide that an event
occurred, that an alert exists, or that a condition is critical (INV-02). No
model call in the conversational hot path — measured p50 end-of-speech to
first audio is 1238 ms and this phase must not add to it. No unsorted/
unmodeled data handling (observations table, promotion by repetition, salience
decay) — that remains a separate owner decision. No new external integrations
(see "Worked example" below, which is illustrative only and must not be
implemented in this phase). No transcript capture. No changes to the reply
path, barge-in, or voice worker behavior.

## Architectural invariants that apply

INV-01 through INV-03, INV-06 through INV-08, INV-10, INV-12, INV-14, INV-15,
INV-17, INV-19.

Three deserve restating because this phase is the first to approach them:

- **INV-02.** Deterministic code owns detection, alerting, and retries. The
  model may only *select and phrase from* a candidate set it did not build.
- **INV-08.** Alerts and safety-related behavior continue without Ollama. A
  briefing with the model unavailable must still deliver, using deterministic
  priority order, and must say nothing untrue about why.
- **INV-12.** Context is bounded and audited; critical items survive
  truncation. A model selection that omits a critical item is invalid and must
  be overridden deterministically.

## Requirements implemented in this phase

BRIEF-001 A briefing is produced only by a deterministic trigger.
BRIEF-002 Candidates are assembled from stored records; each carries entity,
source, timestamp, category, and the query that produced it.
BRIEF-003 The candidate window is explicit and bounded; "since last briefing
of this kind" is derived from a recorded delivery, not from wall-clock guessing.
BRIEF-004 Selection never adds a fact absent from the candidate set.
BRIEF-005 Critical items are always delivered regardless of model output.
BRIEF-006 A hard item cap applies; exceeding it is a truncation, recorded.
BRIEF-007 Every delivered item is marked delivered and is not re-offered.
BRIEF-008 Model unavailability degrades to deterministic priority selection,
reported truthfully in the audit and never as a model decision.
BRIEF-009 Every selection records prompt version, model name, candidates
offered, candidates chosen, and reason.
BRIEF-010 Novelty is scored deterministically before any model involvement.
BRIEF-011 User dismissal of an item class is durable and consulted later.
BRIEF-012 A briefing with no worthwhile candidates produces silence, not filler.

## Dependencies on prior phases

Consume the typed services of Phases 3-6; do not query tables ad hoc from
trigger or prompt code. Reuse the Phase 5 candidate/audit vocabulary rather
than inventing a parallel one — the assembler's output should be recognizably
the same kind of object the situation broker already produces. Reuse the Phase
4 outbox and notification adapters for delivery; do not open a second egress.

## Required deliverables

Assembler service and its bounded queries; trigger workers; selection module
with prompt versioning and fallback; delivery bookkeeping; feedback capture;
any narrow migration proven necessary (see below); tests per sub-phase;
documentation in `talos/awareness/README.md`; ADR entries; status and handoff.

## Detailed implementation requirements

### 9A — Assembler

Produce candidates, not prose. Each candidate carries a stable id, a category
(`alert`, `transition`, `agent_outcome`, `novelty`, `interaction`, `reminder`),
a one-line rendering with explicit temporal qualification, the entity and
source it came from, a deterministic priority, and a novelty score where
applicable. This is the same discipline as `context/broker.py:Candidate` and
should share its rendering helpers.

The window is "since the last delivered briefing of this kind", resolved from
recorded deliveries. On first run, or when no prior delivery exists, use a
configured default window and say so in the audit rather than silently
scanning all history.

Bound every query by count and time range, honoring the existing
`max_query_range_days` / `max_query_points` settings. A briefing that would
exceed its bounds is truncated with the truncation recorded (INV-17).

**Novelty scoring (BRIEF-010) is deterministic and belongs here.** The
`measurements_1h` / `_1d` continuous aggregates already carry min/max/avg/
stddev per entity and measurement; unusualness is a z-score or a
first-ever-observation, computed in SQL. A model must never be asked whether a
number is unusual — it has no baseline and will confabulate one. The model's
question is the *next* one: given that this is statistically unusual, would
the owner care?

### 9B — Triggers and bookkeeping

Scheduled briefings follow the reminder worker's pattern: a deterministic
worker on a poll interval, computing due times, idempotent across restarts.
Arrival briefings trigger on the presence state transition from
`stale`/`offline`/absent to present — the transition, not the presence signal,
because presence is re-asserted on every interaction and would otherwise fire
continuously. `state_transitions` already records exactly this.

Cap items per briefing (configurable, default 3). The cap is a feature, not a
limitation: it forces genuine ranking and is the primary defense against the
system becoming background noise the owner learns to ignore.

Mark `attention_items.delivery_status = "delivered"` when an item is spoken.
This column exists and is currently never written; until it is, any briefing
will repeat itself, which destroys the impression of a system that remembers.

Quiet hours already defer non-critical delivery; briefings must respect them
rather than reimplementing the check.

### 9C — Selection

Invoke the model from the **outbox worker**, never from ingestion and never
from the reply path. The outbox already polls on a 2-second interval, already
tolerates network work outside event transactions (INV-15), and already
retries with bounds. A selection call there costs the conversational path
nothing.

The call is a *ranking over a supplied list*. The prompt contains the
candidate set and the owner's known preferences (9D); the model returns chosen
candidate ids with a one-line reason each, and optionally a phrasing. Reject
and log any returned id not present in the input — that is the guard that
makes BRIEF-004 enforceable rather than aspirational.

Deterministic overrides that the model cannot influence: critical items are
always included; the item cap is applied after selection; dismissed classes
are filtered before the prompt is built, not left to the model's discretion.

On timeout, error, or unavailable Ollama, fall back to deterministic priority
order and record `selection_mode: "deterministic_fallback"` in the audit. The
briefing still happens. Never report a fallback selection as a judgment.

Version the prompt and record the version with every selection, following the
`prompt_version` convention already used by memory candidate proposals.

### 9D — Feedback

When the owner dismisses an item or a class of item ("don't tell me about that
again"), record it as a memory through the existing memory service, scoped so
the assembler and selector can consult it. Prefer suppressing at assembly time
over asking the model to remember a preference.

This sub-phase is small and is the largest single contributor to the system
feeling like it knows the owner rather than reporting at him. It should not be
deferred indefinitely because it looks minor.

## Worked example (illustrative only — do not implement in this phase)

A financial net-worth figure shows why most new inputs need no new machinery.
It is a numeric measurement like any other:

- Register an entity `portfolio` (`entity_type = "service"`) and a source
  `finance_poller` with `allowed_topics: ["home/finance/portfolio/#"]`,
  `allowed_transports: ["internal"]`, and
  `metadata.deadbands = {"net_worth": 500.0}`.
- A poller fetches the figure and POSTs to `/ingest` on
  `home/finance/portfolio/telemetry/net_worth` with `{"value": ..., "unit":
  "USD"}`, stamping `observed_at` with the quote's as-of time rather than the
  fetch time.
- The pipeline then writes history, current state, and a measurement row; the
  aggregates provide the baseline; the deadband suppresses meaningless
  movement so `state_transitions` only records moves the owner would care
  about; `stale_after_seconds` makes a weekend-old figure read as stale rather
  than current.

Nothing about this is finance-specific: **the delta, the baseline, the noise
filtering, and the staleness are all already implemented.** The same recipe
covers unread mail counts, calendar density, power draw, or package
deliveries. An implementing agent should treat this as the template for any
future source and must not build the integration, credentials, or poller as
part of Phase 9.

## Database or migration effects

Expected: none for 9A. 9B may require a small `briefing_deliveries` table (kind,
delivered_at, window start/end, item ids, selection mode, audit) if recording
deliveries cannot be expressed with existing tables — evaluate `outbox` and
`attention_items` first and prefer them. 9C/9D should require none: selection
provenance belongs in the delivery record's audit, and preferences belong in
`memories`.

Any migration must keep `models.py` and migrations in lockstep;
`tests/test_awareness_migrations.py` fails on a non-empty autogenerate diff.

Note for the implementing agent: the 2026-09-06 follow-on required **no**
migration because the schema had already anticipated human context. Check
whether the same is true here before adding anything.

## Integration boundaries

The assembler reads typed services; the selector reads the assembler; delivery
uses the existing notification adapters and the existing text-server `/speak`
and `/notify` lanes. Do not add a second speech path, do not call the voice
worker directly, and do not route briefings through `central_queue` as
`morning_report_job` currently does unless that path is deliberately migrated
as part of 9B with its behavior preserved.

Model output never mutates physical state, never creates or resolves an alert,
and never writes to `current_state`.

## Failure behavior

Ollama unavailable: deterministic fallback selection, briefing still
delivered, audit records the mode. Assembler query failure: the briefing is
skipped and logged as a failure with evidence; never deliver a partial
briefing that implies completeness. Notification egress failure: the outbox
retries within its existing bounds; a briefing that was never spoken must not
be marked delivered. Empty candidate set: say nothing (BRIEF-012). Silence is
a valid and correct output, and filler is not.

## Security considerations

Preserve memory sensitivity boundaries when assembling candidates — a
`restricted` memory must not surface in a spoken briefing merely because it
was recent. Candidate text sent to the model stays local (INV-07); the
selection call goes to the configured local Ollama and nowhere else. Record
selection decisions without duplicating sensitive payloads. Briefing triggers
must not be remotely invocable.

## Required tests

Assembler: window derivation including first-run; bounds and truncation
recording; category coverage; deterministic novelty scoring against known
aggregates; empty-window silence. Triggers: arrival fires on transition and
not on repeated presence; scheduled briefing idempotent across restart; quiet
hours respected. Selection: a returned id absent from candidates is rejected;
critical items survive an adversarial model response that omits them; item cap
applied after selection; model timeout falls back deterministically with the
correct audit mode; prompt version recorded. Bookkeeping: a delivered item is
not re-offered in the next briefing; a failed delivery is not marked
delivered. Feedback: a dismissed class is absent from the next candidate set.

Every test must skip cleanly when its infrastructure is absent, matching the
existing suites.

## Acceptance criteria

- A briefing contains only facts traceable to stored records.
- A critical item is delivered even when the model omits it.
- With Ollama stopped, briefings still deliver and the audit says why.
- No briefing repeats an item it already delivered.
- The conversational path shows no measurable latency change (compare against
  the existing `voice_benchmarks.csv` baseline: p50 1238 ms end-of-speech to
  first audio, p95 2541 ms).
- A briefing with nothing worth saying produces silence.
- No model call exists in ingestion, alert detection, or the reply path.

## Documentation updates

Document the pipeline, the deterministic/judgment boundary and why it sits
where it does, trigger configuration, the item cap, the novelty baseline,
prompt versioning and fallback behavior, and the worked source-onboarding
example. State plainly what the model does and does not decide.

## Implementation status updates

Record sub-phases completed, files and any migration, test evidence with real
counts, latency evidence for the acceptance criterion, model and prompt
version assumptions, failures, and the review gate.

## Required final report

Files and migrations; the assembler's candidate contract; trigger behavior;
the selection guard and fallback evidence; tests run/passed/failed/not run;
latency comparison; limitations; security effects; next proposed work; stop.

## Stop condition

Stop at the end of the authorized sub-phase. Do not proceed from 9A to 9B (or
any later sub-phase) without owner authorization recorded in status. Do not
begin unsorted/unmodeled data handling. Do not implement the finance poller or
any external integration. Do not add a model call to the conversational hot
path under any justification.
