# Phase 05 — Situation, Context, and Read Tools

## Purpose

Integrate a deterministic compact situation model and context broker with the existing Ollama/Qwen client, plus narrow bounded tools that route exact state, event, telemetry, health, and provenance questions correctly.

## Entry criteria

Phase 4 is complete/reviewed; state/history/telemetry/alerts/attention/health reads are stable; existing LLM/tool integration and tokenizer/context limits are documented; status authorizes Phase 5.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), context/read-tool and audit contracts in [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and Phase 5 [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load Phase 6-8 briefs, all prompts, detailed failure modes unrelated to read/context behavior, or the original specification by default.

## Repository discovery required for this phase

Reconfirm Qwen/Ollama client and prompt assembly, tool/function schema and round handling, tokenizer/model context length, conversation/session model, existing system/safety prompts, health/read APIs, and authorization/audit conventions.

## In scope

Compact deterministic situation snapshot; relevance selection and fixed priority; separate hard token budgets; actual tokenizer or conservative estimate; truncation and context-selection audit; temporal rendering; strict read tools for current/room/entity state, active alerts/attention, recent events, sensor history/aggregates, device/system health, event provenance, and capabilities; bounded tool results/rounds/errors; integration with existing LLM client.

## Explicitly out of scope

Long-term memory schema/search (`search_memory` may remain unavailable or use an explicit existing provider until Phase 6), embeddings, memory writes, model-driven hot-path decisions, arbitrary SQL/files, action tools, new LLM client rewrite, or context filled merely because budget remains.

## Architectural invariants that apply

INV-01 through INV-03, INV-06 through INV-08, INV-10 through INV-12, INV-14, INV-17, INV-19.

## Requirements implemented in this phase

R4, R10-R12; CTX-001 through CTX-010; exact retrieval portions of HIST-004/005, STATE-007, SEC-006, and TEST retrieval scenarios.

## Dependencies on prior phases

Use qualified services from Phases 3-4; do not query tables ad hoc from prompt code. Preserve existing system/personality/tool behavior and add context through established boundaries.

## Required deliverables

Situation/context/budget/audit modules; strict read schemas/router/tools; existing-client integration; any narrow audit migration; tests for routing, limits, temporal truthfulness, overflow; context/tool documentation; status/handoff.

## Detailed implementation requirements

Generate a structured snapshot from relevant state, critical alerts, attention, recent transitions, likely user location, conversation/task state, and system health—never a raw dump or massive prose narrative. Always include active critical alerts. Other data must be earned by request, location/entity/task/conversation/attention/health relevance.

Apply canonical priority order and separate budgets for fixed instructions, conversation, situation, retrieved memories (reserved for Phase 6), tool results, and response. Use actual tokenization when feasible. Drop lower priority first and never critical alerts. Audit selected item ID/provenance/reason/temporal status/tokens/priority/truncation.

Every model-visible fact includes temporal status, observation/receipt time, age/expiry, state status, confidence, and source. Human relative time supplements—not replaces—structured timestamps.

Strict tools validate inputs, auth, time range, points, pagination, output tokens, and tool rounds; log calls/failures and return clear errors. Route current facts to state, last occurrence to events, numeric periods to aggregates, trust to health/provenance. Never default exact/numeric/temporal questions to vector search or return thousands of raw rows.

Tool schemas and result envelopes must remain stable/versioned enough for the existing client to validate them. Include query bounds and truncation indicators in results so the model cannot mistake a partial result for a complete history. Capability reporting distinguishes unavailable, not-yet-implemented, unauthorized, and temporarily degraded tools. Keep fixed system/safety instructions outside relevance pruning, and ensure context audit content is useful for debugging without duplicating sensitive payloads or entire tool results.

## Database or migration effects

Only context-selection and tool-call audit additions proven necessary. No memory/vector/action migrations.

## Integration boundaries

Context broker consumes typed read services and feeds the established LLM client. Tools expose narrow capabilities, no arbitrary SQL/file/shell/MQTT. Model output cannot mutate physical state in this phase.

## Failure behavior

Stale/offline/unknown sources render explicitly. A read dependency error becomes a model-visible bounded error and health/audit event, not fabricated data. Ollama outage returns truthful unavailability while deterministic backend behavior continues.

## Security considerations

Apply existing auth/authorization and sensitivity/location boundaries; log selection without leaking secret content; constrain inputs/results/rounds; preserve system/safety instruction priority; no unrestricted access.

## Required tests

Relevance and unrelated-state exclusion; token accounting/truncation/critical preservation; complete audit; temporal rendering; pump current-state route; last-run event route; average-current aggregate route; health/provenance route; stale truthfulness; invalid/unbounded input; bounded outputs/rounds; stubbed LLM integration; context-overflow E2E.

## Acceptance criteria

- Exact example questions route to the correct structured source, not vector search.
- Results include freshness, confidence, source/provenance, and enforced bounds.
- Context stays under hard budget and unrelated state is absent; critical alerts survive truncation.
- Selection/tool audits explain inclusion, limits, and failures.
- Existing Ollama client remains functional and outage is truthful.
- No long-term memory or action implementation begins.

## Documentation updates

Document situation shape, relevance/priority, budget configuration/tokenizer, audit, temporal rendering, each tool schema/routing/limit/error, existing LLM integration, and limitations.

## Implementation status updates

Record tools/budgets, files/migrations, test evidence, model/tokenizer assumptions, failures, and review gate.

## Required final report

Files/migrations; situation/context selection; tools/routes/limits; tests and token evidence; failures/limitations; security/model integration effects; next proposed phase; stop.

## Stop condition

Stop after Phase 5 evidence, docs, status, and handoff. Do not implement long-term memory or Phase 6.
