# Tool implementation guide

Applies to new or modified agent-facing capabilities. Read with root AGENTS.md
and the architectural invariants. Existing inconsistencies are migration work,
not examples to copy. Do not perform unrelated migrations merely to add a tool.

## Contract and implementation

- A published tool must perform its documented operation. Unimplemented or
  unavailable features must not return a success-like sentence or be advertised
  as functioning capabilities.
- Keep business behavior in a domain service; keep the provider as a thin
  validated adapter. Reuse the existing MCP provider layout and registered action
  boundary rather than adding a parallel executor.
- Define input types, required fields, enums, ranges, units, and time semantics.
  Prefer typed nested objects over JSON encoded inside a string. Do not create a
  broad action dispatcher whose unrelated operations have undocumented schemas.
- Use one canonical operation definition to derive model-facing schemas and
  validation where feasible. Describe purpose, when to use it, side effects,
  result semantics, and material failure conditions. Preserve stable names.
- Supply known session/actor/correlation data in the runtime. Bind idempotency
  keys to the intended operation outside the model; retries reuse them, new
  intents do not. Tool visibility and retrieval never grant authorization.
- Return structured outcomes. Distinguish accepted, completed, partial, failed,
  and unknown where applicable, with operation IDs, per-target outcomes, evidence,
  freshness, and recoverable error details. Use the domain's existing canonical
  result model; do not invent a competing success/error shape for each provider.
  At a string-only compatibility boundary, serialize that object as JSON.
- Do not infer success by matching prose. Preserve structured results through
  adapters and provide a separately bounded model view when needed. Required
  status/evidence fields must survive truncation; provide a detail handle for
  large outputs rather than cutting JSON in half.

## Effects, history, and discovery

- Physical actions retain registered schemas, authorization, configured
  confirmation, idempotency, timeout, acknowledgement, and transition audit.
  Immediate electrical/mechanical protection remains in firmware/hardware.
- Reads and mutations need explicit timeout/retry semantics. After an ambiguous
  mutation timeout, reconcile outcome or use proven idempotent retry; do not
  assume the action failed just because its response was lost.
- Record real calls/results and their IDs with the used schema snapshot. Replay
  complete exchanges within bounds, never fabricated history. Historical schemas
  are evidence; the current available catalog determines executable capabilities.
- Keep current state and exact numeric/time questions on structured retrieval.
  Semantic retrieval may discover tools or documents, not establish device truth.
- Preserve useful task capabilities across follow-up utterances. Discovery must
  load exact current schemas, respect availability/permissions, and have an
  explicit no-match path. Do not introduce a vector store without measured need.
- Talos currently runs local inference with AWS Polly speech. An OpenAI-compatible
  API is a protocol choice, not evidence that hosted strict decoding, search,
  state management, or other OpenAI features exist on this backend.

## Verification expected for a changed capability

Use proportionate tests for schema/implementation agreement, valid and invalid
inputs, unavailable/error/partial outcomes, retry semantics for mutations, and
history/serialization when affected. Test adapters with mocked effects before
touching hardware. Add a local-model acceptance scenario when changing the tool
surface or selection behavior; report whether it was actually run.

The [detailed tool review](architecture_fixes_09212026/07_tool_system.md) explains
the existing debt and proposed catalog/result architecture. That larger migration
remains a separate assignment; this guide does not authorize it.
