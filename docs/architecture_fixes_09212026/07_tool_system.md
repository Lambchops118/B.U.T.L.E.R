# Tool system review

2026-09-21. Detailed consultation and proposed course of action.

Implementation update: the owner subsequently authorized removal of the no-op
lights tool and preservation of tool exchanges/schema snapshots in local
streaming history. Those changes are implemented in this pass; the other
findings below remain recommendations. See [history repair](01_tool_history.md)
and [session handoff](SESSION_HANDOFF.md) for verification and limits.

The deployed inference system is local; AWS Polly is the current hosted speech
exception. OpenAI references describe public design patterns, not a proposed
switch to its API.
Companion to [Architecture Review](../ARCHITECTURE_REVIEW.md).

## Verdict

Talos has a useful MCP foundation but inconsistent capability contracts. Improve
the catalog, schemas, execution semantics, and result/history preservation before
adding a vector index. Tool discovery is a promising optimization for expansion,
especially engineering integrations; it is not a remedy for invalid contracts.

This review inspected provider registration, host tools, client translation,
dispatch/retry, output shaping, action services, memory retrieval, the InfoPanel,
and representative tests. It did not start providers or enumerate the deployed
catalog. Static inspection found 26 decorated tools across the five local
aggregate provider groups, plus three host meta-tools. Configuration and scoping
change the visible count; external providers add an unknown live set. The TV
provider is presently a no-op. A tool count also hides the many operations
inside the kitchen and host dispatchers.

## Current path

```text
Python provider functions / host JSON schemas / external MCP tools
  → MCP discovery, namespacing and schema decoration
  → host-tool merge, KiCad allowlist, kitchen keyword scoping, stable ordering
  → model tools field
  → structured calls or recovered tool-like prose
  → host dispatch or MCP client (reconnect/retry)
  → service / action backend / external provider
  → text extraction, generic summarization and truncation
  → tool result in this turn; final prose stored across turns
```

MCP is the transport and discovery protocol. It does not itself make business
semantics, permissions, retry policies, or truthful completion uniform.

## Confirmed findings

| Finding | Evidence | Consequence / recommendation |
|---|---|---|
| A published action does nothing | `talos/services/home_automation.py:turn_on_lights` only returns a sentence; the home provider registers it as a real tool | Remove it from the advertised surface or return explicit unsupported status until implemented. Do not redirect it to arbitrary plugs without a verified room/device mapping. Removed in the subsequent owner-authorized repair; regression coverage prevents its advertisement. |
| Schema styles diverge | Typed FastMCP signatures; handwritten host schemas; `kitchen_screen_control(action, arguments: str)` and `request_device_action(action, parameters: str)` carry JSON inside JSON | Expose typed operations generated from domain definitions. Natural-language descriptions cannot enforce the inner object's fields. |
| Meta-tools hide important distinctions | `mcp_admin`, `memory_admin`, and `phone` mix operations with different required fields and effects; only `action` is required in their schemas | Prefer separate typed operations under small capability groups. A coherent enum operation can remain grouped when arguments and policy truly align. |
| Errors lose machine-readable meaning | Client `_extract_text` prefers text over structured content; discovery retains input schemas but not output schemas/annotations; runtime `_tool_result_indicates_failure` guesses from strings/JSON keys | Preserve the original structured envelope through dispatch and history; render a separate concise model view. |
| Partial failure is misclassified | Smart-plug bulk calls return prose beginning `Turned ...` and append `Failed: ...`; the runtime detector returns false for that format, even for zero successes | Return per-device outcomes and an explicit partial/failed status. A transport-success boolean is insufficient. |
| Retry has no effect classification | `_async_call_with_reconnect` repeats tool calls after exceptions, including timeouts; wrappers can generate a new idempotency key if none was supplied | Bind a durable key to each intended mutation outside the model. Retry reads or proven idempotent operations; reconcile ambiguous mutation outcomes before another attempt. This is a duplicate-effect risk, not a reproduced device incident. |
| Physical actions follow different paths | Pump/fan use registered action requests; smart plugs call the device library directly; sleep/display use their own service | Reuse a common policy/result boundary with device-specific adapters. Do not require MQTT for non-MQTT devices. |
| Tool visibility is not execution authorization | Dispatcher accepts host aliases and names routed by the MCP client, not an explicit per-turn capability grant; prose recovery feeds the same executor | Enforce allowed operation and actor/session policy in the executor. Scoping a tool out of the prompt is not a permission system. |
| Recovery repairs before validation | `tool_arguments.py` can close incomplete JSON; runtime can recover tool-like prose as executable calls | A compatibility parser may help local models, but preserve the original, validate against the chosen schema, and never infer missing mutation intent from an incomplete message. Prefer native structured calls. |
| Discovery is incomplete | `mcp_admin.list_tools` returns names and parameter names, not full schemas; the streaming loop builds definitions once; kitchen visibility uses only current-command keywords | Discover and load exact schemas during a turn. Retain relevant capabilities across follow-ups such as “remove the second one.” |
| Output budgeting is generic | `_shape_tool_output` summarizes arbitrary JSON then character-truncates; this can cut structured output or omit required evidence | Give each result a bounded summary, preserved status/evidence fields, truncation indicator, and handle for detailed retrieval. |
| Cross-turn evidence is discarded | `_record_memory_turn` previously persisted request/final prose only | Local streaming now preserves complete bounded exchanges and used-schema snapshots; legacy records are not backfilled. |

Existing strengths worth retaining: provider modules separated from services,
duplicate MCP name detection, lifecycle/health handling, stable schema ordering,
bounded awareness reads, strict action definitions, durable action audit, and
tests for several of these contracts. Uniformity should extend these strengths.

## What a professional contract would contain

A single authoritative capability catalog should describe each operation's
stable ID/version, purpose, examples, input/output schema, domain, availability,
effect class, authorization, timeout, cancellation, retry/idempotency policy,
and result interpretation. Many fields are runtime metadata and need not consume
model context. Generate MCP exposure, local dispatch validation, documentation,
and retrieval entries from that same source rather than maintain parallel lists.

For action-registry capabilities, derive the model-visible parameters from the
existing definitions. The model should choose the target and requested effect;
the runtime supplies session identity, actor, correlation IDs, deadlines, and
idempotency keys. Separate independent user intents even when their text matches.

A tool description should answer: when to use it, required inputs, what it
returns, what completion means, and any material side effects. Use enums,
bounded numbers, units, explicit time semantics, and typed nested objects.
Prefer `set_device_power(..., power="on")` to ambiguous toggles. Do not expose
an unbounded universal `execute(name, arbitrary_json)` as the normal interface.

OpenAI's public guidance supports clear contracts, reducing model-supplied
arguments already known by the application, and keeping the initial tool set
small. Strict schema generation is useful where supported, but the current
Ollama-compatible backend must be tested independently; an OpenAI-shaped API
does not establish equivalent decoding guarantees. Server-side validation is
still necessary. [Function calling guidance](https://developers.openai.com/api/docs/guides/function-calling).

Proposed shared result envelope: `status`, `data`, `error`, `operation_id`,
`evidence`, `observed_at`, and `truncated`/detail reference where applicable.
Use a small common status vocabulary with domain-specific details. For example,
an accepted command has an action ID but may have no completion evidence yet;
a device read has an observation timestamp and freshness. Partial bulk success
has per-target outcomes. Transport error, application rejection, and unknown
execution outcome are distinct.

MCP already supports structured content, output schemas, and behavioral
annotations. Preserve these at the adapter boundary, while treating provider
annotations as hints rather than authorization. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

## Tool retrieval and RAG

Do not store the tools themselves only in a vector database. Keep executable
definitions in the catalog and optionally index their descriptions, aliases,
examples, domain, and schema version. Retrieval returns catalog IDs; the runtime
loads the exact current schemas and checks availability/permissions.

Recommended initial approach:

1. Keep common home interaction and discovery capabilities immediately visible.
2. Add relevant domain tools from the active task and recent entities, preserving
   the working set through conversational follow-ups.
3. Search the remaining catalog on demand using exact names, keywords, domain
   filters, and optionally embeddings. Start with lexical search; measure what
   embeddings improve before introducing them as a dependency.
4. When retrieval is uncertain, widen discovery or clarify instead of silently
   choosing the nearest wrong action. Always have a browse/list fallback.
5. Load schemas into the real tools field for the next model round. Do not merely
   paste retrieved descriptions into prose and expect reliable invocation.

OpenAI documents deferred tool loading, including client-executed search. That
is a useful reference pattern, not a drop-in feature guaranteed for Talos's
local Chat Completions model. Talos would implement selection, schema loading,
and working-set persistence in its own orchestration. [Tool search guide](https://developers.openai.com/api/docs/guides/tools-tool-search).

Retrieval adds a new failure mode: the correct tool might never be offered. It
also adds latency if the model must request discovery before every common
action. Compare a stable full set, curated domain sets, lexical discovery, and
hybrid retrieval on the same multi-turn corpus. Measure discovery recall,
wrong-tool selection, valid arguments, verified task completion, prompt cost,
and first useful audio at median/p95. Preserve stable prefixes where supported;
fewer prompt tokens alone do not prove lower latency.

## Where else RAG belongs

| Information | Recommended access |
|---|---|
| Engineering manuals, datasheets, project notes, design decisions | Hybrid text/semantic retrieval, with source passages, revision and project filters; verify exact part IDs/specifications against the source |
| Past incidents and relevant personal/project memories | Existing awareness hybrid search, with provenance, validity and supersession |
| Repository code | Start with exact symbol/path/text search; add semantic search for conceptual discovery, then read current source |
| Current device state, latest reading, action outcome | Structured authoritative query with freshness; not similarity search |
| Exact dates, numeric trends, reminders and schedules | Bounded relational/time-series queries and deterministic time handling |
| Immediate conversational references | Structured session/task state; semantic memory is supplementary |

Talos already implements full-text plus optional vector memory retrieval in
`talos/awareness/memory/service.py`. Whether embeddings are enabled on the live
host was not checked. Explicit facts currently go to SQLite for prompt assembly
and are mirrored best-effort into awareness; the response records sync success.
Resolve that ownership/synchronization contract before adding another memory
store. Retrieved documents remain evidence, not instructions or permissions.
Index current and historical documentation with explicit status/revision rather
than giving obsolete handoffs equal authority.

## InfoPanel as a capability

Yes: expose deliberate presentation operations, not renderer internals. The
current panel consumes `VOICE_CMD` and `STATUS` queue messages and has fixed
layouts; inspected status indicators are assigned placeholder values every
frame. The kitchen screen already has a separate control tool, while sleep mode
already controls panel darkness and TV power. These are different surfaces.

Proposed tools (not currently implemented):

- `present_content(surface_id, content, lifetime)` for bounded cards, lists,
  approved artifact references, or query-backed charts; return a presentation ID.
- `update_presentation(presentation_id, patch)` and
  `dismiss_presentation(presentation_id)`.
- `get_presentation_state(surface_id)` for actual acknowledged content, IDs,
  revision and visibility, enabling “leave that up” and “show the previous one.”

Expose surface discovery/capabilities so the model knows which display supports
which content. Validate content schemas and resource references; do not give the
model arbitrary Python, shader execution, or unrestricted remote-content loading
to draw a card. Keep hardware power/quiet-mode semantics explicit and separate
from content operations, honoring the existing sleep/display coupling.

Automatic status updates, critical banners, clocks, and animation should remain
event-driven. The model need not call a tool every frame or decide whether a
status indicator is healthy. A presentation service should arbitrate priority,
expiry, replacement, and acknowledgements; report queued versus rendered honestly.

Example: “Show the plant's moisture over the last week” performs bounded sensor
retrieval and presents a chart from those results. Speech can stay brief while
the evidence stays visible. “Keep that up while we discuss the circuit” uses the
presentation ID and task context rather than rediscovering what “that” means.

## Alternatives and recommended order

Keep MCP and build consistent adapters/contracts around it. Replacing MCP with
direct function calls does not fix schema or evidence problems; direct calls
may still be appropriate inside the same process. Domain specialists can help
long engineering jobs later, but add coordination and should share the same
executor. A bounded workflow engine is appropriate for repeated multi-step
procedures with checkpoints. Sandboxed programmatic composition may eventually
help batch read/analysis work; unrestricted code execution is not a substitute
for household action tools.

Priority sequence: remove false capabilities; standardize result/error and
retry semantics; derive typed operations from the catalog and preserve history;
then compare discovery strategies; then add deliberate InfoPanel presentation.
No step depends on a vector database or a hosted model.

Acceptance cases should include unsupported lights, valid/invalid pump targets,
timeout after an action was accepted, partial plug failure, malformed nested
arguments, a kitchen follow-up without keywords, unavailable providers, a
long-history state query, and a presentation that fails to render. Use simulated
effects for failure injection. Evaluate recall separately from tool execution.

## Historical consultation handoff

- Goal/current phase: bounded tool-system consultation; no implementation phase.
- Completed: source inspection, static local inventory, execution/schema critique,
  retrieval and InfoPanel recommendations; this document serves as handoff.
- Added: this review. Modified: architecture-review cross-link and awareness
  status/decision/question records. Runtime/configuration/migrations: unchanged.
- Confirmed decision: broader alternatives may be explored; implementation remains
  outside this assignment. All technical designs above are proposals.
- Diagnostic validation: four isolated assertions against the existing failure
  detector passed, confirming partial-failure prose and empty output are not
  detected as failures while JSON rejection/error are. No app imports, network,
  providers, device effects or model calls were involved. This confirms a defect;
  it is not a passing end-to-end reliability test.
- Documentation validation passed: `git diff --check`, all nine local Markdown
  link targets across the five documents touched this pass, and new-review
  trailing-whitespace check. No diagnostic assertions or documentation checks failed.
- Not run: existing unit/integration suites, live catalog discovery, model/tool
  benchmarks, hardware/audio tests, or deployment changes.
- Limitations: external provider schemas and actual configured tool exposure were
  not enumerated live; no performance improvement is claimed.
- Security/deployment: no behavior changed; current authority/safety boundaries
  remain. Pre-existing untracked TUI/wireframe work is untouched.
- Open decisions: initial tool working set, authoritative catalog ownership,
  memory authority, allowed presentation formats/surfaces, and acceptance targets.
- Next permitted task: owner discussion or an explicitly bounded implementation
  assignment. Required reading: this review, current status, invariants, and the
  relevant provider/service contracts.
- Stop: consultation complete; no tool migration or next phase starts automatically.
