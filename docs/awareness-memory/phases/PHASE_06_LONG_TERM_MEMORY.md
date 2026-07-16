# Phase 06 — Long-Term Memory

## Purpose

Implement evidence-backed semantic and episodic memory, deterministic and validated proposal paths, contradiction/supersession, local embeddings, and bounded hybrid retrieval while keeping conversations, state, telemetry, and memory distinct.

## Entry criteria

Phase 5 is complete/reviewed; conversation/message/session and provenance sources are understood; local embedding model and pgvector/local substitute choices are approved; sensitivity/retention policy is confirmed; status authorizes Phase 6.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), memory/outbox/context schemas in [`../reference/DATA_MODELS_AND_SCHEMAS.md`](../reference/DATA_MODELS_AND_SCHEMAS.md), [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md), [`../reference/SECURITY_AND_PRIVACY.md`](../reference/SECURITY_AND_PRIVACY.md), and Phase 6 [`../reference/TEST_STRATEGY.md`](../reference/TEST_STRATEGY.md).

## Documents not normally needed

Do not load action/hardening phase details, all launcher prompts, unrelated phase briefs, or the original specification by default.

## Repository discovery required for this phase

Reconfirm conversation/message storage, user-confirmation signals, event evidence access, existing retrieval/embedding abstractions, configured Ollama embedding model/dimension/version, pgvector index fit, privacy/deletion rules, and expected corpus/volume.

## In scope

Memory/embedding/provenance/relationship schemas; semantic and episodic types; deterministic unambiguous writes; strict LLM-proposed candidates; evidence validation and recorded accept/reject; duplicate/merge; confidence/sensitivity; contradiction/conflict/supersession; local embedding outbox/retry; selected embedding corpus; metadata-filtered hybrid retrieval with component scores; context/tool integration; conversation/message/session separation; meaningful episode triggers.

## Explicitly out of scope

Treating working situation, raw telemetry, heartbeats, audio/binaries, or every transcript/message as memory; unrestricted model writes; blind fixed-window episodes; cloud embedding/vector services; broad retention/consolidation jobs (Phase 8); actions.

## Architectural invariants that apply

INV-01 through INV-08, INV-10 through INV-12, INV-14 through INV-19.

## Requirements implemented in this phase

R17-R18 and memory portion of R12; MEM-001 through MEM-014; embedding failure portions of FAIL-002; memory privacy portions of SEC-004/005.

## Dependencies on prior phases

Link memories to immutable event/message/conversation/source evidence. Use Phase 4 outbox semantics and Phase 5 bounded retrieval/context audit. Exact state/history remains separate and higher-authority for exact questions.

## Required deliverables

Focused memory candidate/manager/provenance/contradiction/embedding/retrieval modules; strict tool/proposal schemas; migrations/indexes; local embedding configuration; unit/integration/E2E/outage tests; memory policy/retrieval/privacy documentation; status/handoff.

## Detailed implementation requirements

Working/situational memory stays in state/session. Procedural capability/safety belongs primarily in versioned config/docs. Archival telemetry stays structured. Episodic records summarize meaningful incidents/interactions and link evidence. Semantic records retain validity/confidence/scope/sensitivity and historical changes.

Deterministic writes are permitted only when unambiguous and policy allows—such as explicit rename/preference, firmware/device-location change, or recorded incident. A model proposes strict structured candidates but cannot write active memory. Validate schema and referenced evidence, reject unsupported claims, detect duplicates/contradictions, assign confidence/sensitivity, merge/supersede, record the decision/model/prompt/extraction job, then enqueue embedding.

Never overwrite changed facts. Close validity, preserve and mark old memory superseded, link the replacement. If conflicting evidence is inconclusive, keep both with conflict relation. Explicit user evidence outweighs weak inference.

Embed only selected semantic/episodic/selected conversation summaries/docs/troubleshooting/notes via configured local Ollama. Store model/dimension/version/time/content hash. Hybrid retrieval combines vector, full-text, filters, recency, importance, confidence, entity/location, and validity; expose component scores and bounded provenance. Embedding outage stores accepted text, queues retry, and keeps exact/full-text working.

Episodes arise from meaningful incident, completed interaction/task, notable transition, explicit preference, novel pattern, selected conversation, or configured end-of-day summary—not the passage of every interval.

Content hashes and idempotency keys must make repeated extraction safe. Retrieval excludes rejected, expired, deleted, superseded, out-of-scope, or unauthorized records unless a privileged audit explicitly asks for history. Updating access counters must not change semantic validity. Store the raw evidence reference separately from rendered summary text so re-extraction or model-version changes remain auditable without losing the original basis.

## Database or migration effects

Add memory, embedding, provenance, relationship and conversation/message/session distinctions plus appropriate type/status/validity/scope/vector indexes. Test clean and previous-revision migrations and dimension/version changes.

## Integration boundaries

Memory consumes evidence through typed repositories and returns bounded scored results to Phase 5. Inference is asynchronous outside transactions. It cannot change current state, alert safety, or action state.

## Failure behavior

Embedding/model outage queues bounded idempotent work and reports degraded semantic capability. Invalid/unsupported candidate is rejected with audited reason. Database outage makes memory write unavailable; no false confirmation. Index/model version mismatch is explicit and recoverable.

## Security considerations

Enforce sensitivity on acceptance, retrieval, context, deletion, and retention. Audit explicit deletion. Avoid embedding restricted content when policy forbids it. No external service, indiscriminate transcript ingestion, or unbounded model exposure.

## Required tests

Explicit preference acceptance; unsupported proposal rejection; evidence/provenance validation; duplicate/merge; overflow episode linkage; changed preference supersession; inconclusive conflict; local embedding metadata; hybrid filters/scores/bounds; corpus exclusion audit; outage queue/exact search/retry; conversation separation; sensitivity/deletion authorization.

## Acceptance criteria

- Explicit evidence creates validated semantic/episodic memory with confidence, validity, sensitivity, and provenance.
- Unsupported model claims are rejected; model has no unrestricted permanent write path.
- Changed facts preserve/supersede history and inconclusive conflict remains explicit.
- No raw telemetry or indiscriminate message/audio/binary content is embedded.
- Embedding outage queues retry while exact/full-text retrieval works.
- Hybrid results are bounded, filtered, scored, and provenance-rich; exact questions still use structured retrieval.

## Documentation updates

Document memory types/write paths/evidence, statuses, sensitivity, contradiction/supersession, embedding model/version/corpus, retrieval scoring/limits, outage behavior, deletion, conversation separation, and known limitations.

## Implementation status updates

Record schema/index/model versions, files/migrations, candidate/retrieval tests, outage evidence, privacy decisions, failures, and review gate.

## Required final report

Files/migrations; memory types/write/evidence policies; embedding/retrieval configuration; tests; rejected/outage behavior; privacy/security/deployment impact; limitations; next proposed phase; stop.

## Stop condition

Stop after Phase 6 evidence, docs, status, and handoff. Do not implement actions or Phase 7.
