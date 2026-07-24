# TALOS LLM Turn Context Deep Dive

Date: 2026-07-24

## Executive summary

TALOS has two distinct context and memory systems:

1. The SQLite persistence system is the LLM's automatic conversational
   continuity and prompt-memory layer.
2. The PostgreSQL awareness system automatically contributes a compact
   current-situation snapshot and exposes deeper current state, history,
   telemetry, provenance, actions, and long-term memory through MCP tools.

Only SQLite persistence is automatically treated as long-term memory by the
main LLM prompt. Awareness long-term memory is not automatically injected; the
model must call `search_memory`.

The intended separation is architecturally sound, but the current integration
has several important weaknesses:

1. The awareness broker builds a snapshot under a 600-token default budget,
   but the main runtime subsequently truncates the entire rendered snapshot to
   500 characters. This can break the guarantee that critical alerts survive
   truncation.
2. The shared tool-result summarizer can replace actual awareness properties,
   events, points, alerts, search results, or health components with placeholders
   such as `"<list with 1 items>"`.
3. SQLite fact retrieval does not isolate facts by session or scope, allowing
   session-specific facts to appear in unrelated sessions.
4. `remember_memory_fact` still gives the model direct write access to the
   prompt-authoritative SQLite store. The awareness write is a best-effort
   one-way mirror, not the authoritative validation boundary.
5. `turn_on_lights` returns a success-like string without performing or
   confirming a physical action.
6. The two memory stores have no reverse synchronization or reconciliation and
   can disagree indefinitely.

## Model-call inventory

There are three relevant model-call types.

| Call | When | Context received |
|---|---|---|
| Request router | Most text requests in automatic mode | User command, source, session ID, and background-job context only |
| Responses lane | Text, legacy voice, events, and background jobs | Persona/instructions, SQLite prompt memory, optional job context, awareness snapshot, time, user command, tool schemas, and provider-side response thread |
| Streaming lane | Default voice path | Persona system message, SQLite memory, awareness snapshot, recent chat messages, time, decorated user command, and tool schemas |

## Conditional request-router call

Most automatic text requests first invoke a small model call that decides
whether the request should run in the foreground, background, or status lane.
The currently configured/default router model is `gpt-4o-mini`.

The router receives:

- Fixed routing instructions.
- The source.
- The session ID.
- Up to six persisted background-job records.
- The current user command.

It does not receive:

- The TALOS persona.
- Awareness state.
- SQLite memory.
- Current time.
- Tool definitions.
- MCP resources.

Voice skips this extra model call by default because
`TALOS_VOICE_MODEL_ROUTING=0`.

Primary code:

- `talos/agent/runtime.py::classify_request_route`
- `talos/router.py::_classify_with_context`
- `talos/router.py::_runtime_context_for_session`

## Responses API lane

`talos.agent.runtime.run_command` handles text, legacy voice, LLM-requesting
events, and background jobs.

### Initial request

The request contains:

- `model`: currently `gpt-4o-mini` through `OPENAI_VOICE_MODEL`.
- `temperature`: `0.5`.
- `max_output_tokens`: currently `1024`.
- `instructions`: assembled fresh on every request.
- `tools`: all currently exposed host and MCP tool schemas.
- `input`: the current turn's messages.
- `previous_response_id`: when a prior response exists for the same runtime
  thread.

Response threads are keyed as:

```text
foreground:<session_id>
background:<session_id>
```

Foreground and background model chains are therefore separate, even when they
share a SQLite session.

### Instructions

The instruction block is assembled from:

1. `talos/personality/monkey_butler.md`.
2. The text or voice interaction overlay.
3. The filesystem overlay.
4. The tool-usage overlay.
5. Optional KiCad, Minecraft, and phone overlays.
6. The SQLite prompt-memory block.
7. Optional job/runtime context.

The text-mode instruction block is approximately 9,933 characters before
memory and job context are added.

SQLite prompt memory is limited by `TALOS_PROMPT_MEMORY_CHAR_LIMIT`, currently
using the 1,600-character code default.

### Input-message order

The initial `input` list is:

1. Awareness situation or legacy state snapshot, if nonempty.
2. Optional KiCad provider/preflight context.
3. Optional Minecraft filesystem-root context.
4. Fresh authoritative local date and time.
5. Current user command.

The date/time block is retrieved locally on every initial answer-generating
turn. It is not inferred from conversation history.

### Cross-turn continuity

Responses continuity comes from two overlapping mechanisms:

1. `previous_response_id` asks the provider to preserve the prior response
   chain.
2. The SQLite session summary is included inside the prompt-memory block.

The response ID is kept only in process memory. Restarting TALOS removes that
provider-thread pointer, while the SQLite summary remains.

The runtime does not explicitly enumerate or bound the provider-side prior
response chain. Provider behavior controls how much of that chain remains in
the effective context window.

## Streaming voice lane

The default voice route is:

```text
voice worker
  -> POST /chat/stream
  -> talos.agent.runtime.run_command_stream
  -> OpenAI-compatible Chat Completions
  -> local Ollama
```

Current configuration:

- Backend: `ollama`.
- URL: `http://127.0.0.1:11434/v1`.
- Model: `mb-core-v1:latest`.
- Streaming enabled.
- Thinking mode: `never`.
- Maximum output: inherited from `TALOS_AGENT_MAX_OUTPUT_TOKENS=1024`.

### Message order

The streaming request sends:

1. System: assembled persona and runtime instructions.
2. System: awareness or fallback state context.
3. Optional KiCad preflight/status context.
4. Optional Minecraft context.
5. Up to eight recent SQLite user/assistant messages, bounded to 4,000
   characters total.
6. System: fresh authoritative date and time.
7. User: current command plus the dynamic Qwen thinking suffix.

The voice instruction block is approximately 10,714 characters before memory
is added.

With the current `TALOS_LLM_THINK_MODE=never` setting, the outgoing user
message receives:

```text
 /no_think
```

The undecorated command is what SQLite stores.

### Conversation continuity

Chat Completions has no `previous_response_id`, so continuity is supplied by
`MemoryStore.get_recent_messages`.

The runtime:

- Includes up to eight recent user/assistant messages.
- Limits each message to at most 1,200 characters.
- Limits the combined returned history to 4,000 characters.
- Removes oldest messages until the total fits.

The streaming lane omits the active-session summary from the memory block to
avoid duplicating the recent messages. It can still include user/project
summaries and relevant facts.

## Tool follow-up calls

### Responses follow-up

After one or more function calls are executed, TALOS sends:

- The same model.
- The same instructions.
- The same tool definitions.
- `function_call_output` items.
- The response ID of the tool-calling response.

The awareness snapshot is not copied into the follow-up input, but it remains
part of the referenced response chain.

### Chat Completions follow-up

The streaming lane appends:

1. An assistant message containing the structured tool calls.
2. One `role=tool` message per result.

It then resends the complete in-turn message array. The original system
instructions, awareness snapshot, recent history, time block, and user command
therefore remain present.

### Execution limits and errors

Current configuration permits 16 tool-call rounds.

Calls are executed sequentially. Errors are converted to tool-result text and
returned to the model. MCP results marked `isError` become exceptions first,
then are converted into the same model-visible error form.

Streaming also attempts to recover tool-call JSON that a local model prints as
plain text instead of returning through the structured tool-call field.

## Tool surface

The intended configured tool surface contains 56 tools.

### Host tools: 13

MCP resource and lifecycle tools:

- `list_mcp_resources`
- `list_mcp_resource_templates`
- `read_mcp_resource`
- `list_mcp_server_status`
- `list_mcp_tools`
- `retry_mcp_server`
- `start_mcp_server`

Memory tools:

- `remember_memory_fact`
- `list_memory_facts`

Phone tools:

- `place_phone_call`
- `phone_call_status`
- `recent_phone_calls`
- `summarize_phone_call`

### Built-in aggregate MCP tools: 43

The configured `talos-local` FastMCP server registers:

- 5 home-automation tools.
- 28 kitchen-screen tools.
- 10 awareness tools.

The awareness tools are:

- `get_current_state`
- `get_recent_events`
- `get_sensor_history`
- `get_active_alerts`
- `get_system_health`
- `get_event_provenance`
- `search_memory`
- `request_device_action`
- `get_action_status`
- `get_awareness_capabilities`

The current `settings.env` sets:

```text
TALOS_SCOPE_TOOL_SURFACE=0
TALOS_REDUCE_KICAD_TOOL_SURFACE=0
```

Therefore all 28 kitchen tool schemas are intended to be sent even when the
request has no cooking intent. The code supports removing them, but the active
configuration disables that behavior.

### TV tool omission

A TV-control provider exists, but the current aggregate server does not
register it. The configured `TALOS_MCP_SERVERS` value contains only the
`talos-local` aggregate, so TV tools are not part of the intended current
surface.

## MCP lifecycle and routing

`talos/mcp_client/client.py` supports:

- Local stdio MCP servers.
- Remote Streamable HTTP servers.
- Bearer tokens directly or through a named environment variable.
- TLS verification and custom CA bundles.
- Per-server tool-name prefixes.
- Per-server timeouts.
- Eager, lazy, manual-sidecar, and autostart-sidecar lifecycles.

Tool discovery works as follows:

1. Start or connect to eligible MCP servers.
2. Perform the MCP initialization handshake.
3. Call `tools/list`.
4. Apply tool hiding and prefixes.
5. Reject duplicate exposed tool names.
6. Cache the tool catalogue and routing table.
7. Translate each MCP schema into a Responses-format function definition.

Deferred providers remain hidden until they have completed startup and are
marked healthy.

When a server fails:

- The server is marked degraded or failed.
- Its cached tool/resource routes are removed.
- Ordinary turns continue without those tools.
- The model can use the host lifecycle tools to inspect or explicitly retry
  the provider.

### Filesystem restrictions

General filesystem mutation tools are hidden unless
`TALOS_FILESYSTEM_ALLOW_WRITES` is enabled.

Minecraft filesystem mutation tools are independently hidden unless
`MINECRAFT_MCP_ALLOW_WRITES` is enabled.

### Resources

MCP resources are not injected automatically. The model initially sees only
the host resource-tool schemas. Actual resource lists or contents enter context
only after it calls:

- `list_mcp_resources`
- `list_mcp_resource_templates`
- `read_mcp_resource`

## Tool argument handling

Tool arguments can be:

- A dictionary.
- A JSON object string.
- Empty/`None`, which becomes `{}`.

The parser repairs a limited set of malformed local-model output:

- Unterminated strings.
- Unclosed objects/arrays.
- Literal newlines, carriage returns, and tabs inside strings.
- Trailing commas before closing braces/brackets.

The final result must still decode to a JSON object.

## Tool-result shaping

Tool results are converted to strings before being returned to the model.

When summarization is enabled, the runtime parses JSON and builds a compact
representation. It preferentially keeps keys such as:

- `success`
- `message`
- `error`
- `path`
- `project`
- `board`
- `components`
- `warnings`

Other nested dictionaries and lists are commonly replaced by placeholders:

```text
<object with N keys>
<list with N items>
```

The summary is used whenever it is shorter than the raw result. The remaining
text is limited to 4,000 characters.

`list_mcp_tools` is the only explicit exemption because losing tool names made
that tool unusable.

### Awareness result loss

This generic summarizer is poorly matched to awareness payloads. Important
fields can be discarded:

- Current-state `properties`.
- Event lists.
- Telemetry points/buckets.
- Alert lists.
- Memory-search results.
- Health component objects.
- Action transition lists.

A representative current-state payload was transformed into:

```json
{
  "tool": "get_current_state",
  "summary": {
    "entity_id": "fan",
    "as_of": "2026-07-24T12:00:00Z",
    "properties": "<list with 1 items>"
  }
}
```

The actual current property value was no longer present in the model-visible
tool result.

This does not happen to every small payload because a summary is used only when
it is shorter, but the behavior is content- and length-dependent rather than
semantics-aware.

## Physical-action handling

The awareness action system correctly provides:

- Registered action definitions.
- Typed parameters.
- Actor permissions.
- Safety and allowed-state checks.
- Cooldowns.
- Confirmation when configured.
- Idempotency.
- Durable transition audit.
- Dispatch and acknowledgement tracking.
- Timeouts.
- A prohibition on treating silence as success.

The generic `request_device_action`, `water_plants`, and `toggle_fan` tools
route through this action service.

### `turn_on_lights` exception

`turn_on_lights` does not use the action service. Its implementation is only:

```python
return f"Turning on lights in the {room}."
```

It performs no device action and has no acknowledgement. This creates a
success-like response with no physical evidence and conflicts with the
repository's permanent action-safety boundary.

## Awareness snapshot construction

Before an answer-generating turn, the router or text streaming server calls:

```text
awareness_client.snapshot_with_fallback(...)
```

The client:

1. Calls `GET /situation`.
2. Uses a 1.5-second default timeout.
3. Caches successful rendered text for five seconds.
4. Returns the legacy in-memory state snapshot if awareness is disabled,
   unreachable, or returns no text.

The snapshot is fetched for:

- Routed voice commands.
- Routed text commands.
- LLM-requesting events.
- Streaming `/chat/stream` requests.

For background work, the snapshot is captured when the job is submitted. It is
not refreshed when the queued job begins execution.

## Awareness situation contents

`SituationBroker` queries:

1. Active critical alerts.
2. Other open or acknowledged alerts.
3. Pending and currently available attention items.
4. Qualified current state.
5. Recent meaningful state transitions.
6. Unhealthy enabled sources.

The default configuration is:

- Situation budget: 600 estimated tokens.
- Maximum candidates per section: 20.
- Transition window: 60 minutes.

Token estimation is:

```text
ceil(character_count / 3.5)
```

Critical alerts bypass the broker's token budget and are always selected.

State lines contain:

- Entity and property.
- Value.
- Current/stale/offline/conflicting status.
- Observation time.
- Receipt time.
- Age.
- Confidence.
- Source.

The situation does not contain:

- Raw event history.
- Raw or aggregated telemetry history.
- Awareness long-term memories.
- Full conversations.
- User-location relevance.
- Current-conversation relevance.

Those require tools.

## Awareness integration truncation

The awareness API returns:

- `as_of`
- `budget_tokens`
- `used_tokens`
- `truncated`
- `item_count`
- `text`
- Complete inclusion/exclusion audit
- Limitations

The client retains only:

```text
Situation as of <timestamp>:
<text>
```

It discards:

- The `truncated` flag.
- Used and available token counts.
- Selection audit.
- Limitations.

The main runtime then calls `_format_context`, which:

1. Collapses all whitespace.
2. Truncates the rendered snapshot to 500 characters.
3. Adds an ellipsis if it cut text.

The practical automatic-awareness budget is therefore approximately 140
tokens, not 600.

The broker's guarantee that all critical alerts survive its own selection
budget does not survive this second truncation. Multiple critical alerts can be
included by the broker and subsequently removed from the model-visible tail.

## Awareness fallback behavior

If awareness cannot be reached, the client silently returns the legacy
`StateStore.snapshot()`.

The model is not told:

- That awareness is down.
- That the context is a fallback.
- Whether the fallback has any active producers.
- Whether “no recent status” means a genuinely quiet home or an unavailable
  sensing backend.

When the fallback equals `"no recent status"`, `_format_context` omits the
context message entirely.

This is non-fabricating, but it is not a fully truthful degradation signal to
the LLM.

## SQLite persistence system

The automatic prompt-memory store is:

```text
db/talos_memory.sqlite3
```

Tables:

- `sessions`
- `messages`
- `facts`
- `summaries`

The live database inspected during this investigation contained:

- 2 sessions.
- 4 messages.
- 3 facts.
- 2 summaries.

No private content was displayed.

## Persistence write behavior

After every completed answer-generating turn, the runtime:

1. Records the original user command.
2. Records final assistant text when nonempty.
3. Stores interaction-mode metadata.
4. Regenerates the session summary from the last eight messages.

The “summary” is deterministic transcript compaction, not an LLM-generated
semantic summary. Each stored message is compressed to one line and at most
260 characters.

Structured tool calls and raw tool results are not persisted. A later turn sees
only:

- What the assistant ultimately said.
- The SQLite summary or recent user/assistant messages.
- Provider-side Responses history while the response ID remains available.

## SQLite prompt-memory retrieval

`MemoryStore.get_prompt_memory` retrieves, in this order:

1. User summary for `user/default`.
2. Project summary for the configured project, default `Talos`.
3. Active session summary, except in the streaming lane.
4. Up to eight facts selected by `search_facts`.

The block begins:

```text
TALOS durable memory (compact, read-only):
```

The block is truncated to the configured character limit, currently defaulting
to 1,600.

Because summaries come before facts, a large session summary can consume most
of the block and truncate relevant facts from the end.

## SQLite fact search

Fact retrieval:

1. Extracts up to eight unique alphanumeric/underscore tokens of at least three
   characters from the current command.
2. Performs case-insensitive substring matching across `key || ' ' || value`.
3. Joins token clauses with `OR`.
4. Orders matching facts by salience and update time.
5. If no match exists, falls back to the globally highest-salience/recent
   facts.

### Scope isolation issue

The query does not filter by:

- Current session.
- User.
- Project.
- Global scope.

Scopes are displayed in the resulting prompt, but they do not control
eligibility.

Consequences:

- A `session:<other-session>` fact can appear in the current session.
- Phone transcript-digest facts can be exposed to unrelated conversations.
- An irrelevant high-salience fact is automatically injected when no query
  token matches.

## Session reset behavior

Resetting a session:

- Clears its Responses API response IDs.
- Deletes the session's messages through the session foreign-key cascade.
- Deletes its session summary.
- Preserves all explicit facts, including `session:<session_id>` facts.

The reset therefore clears conversational continuity but not everything that
the name “session-scoped fact” might imply.

## Phone persistence

When a completed phone call receives a summary, the phone service:

1. Writes a `system` message containing the phone summary.
2. Refreshes the target session summary.
3. Writes a high-salience session-scoped transcript digest fact.

`get_recent_messages` filters to user/assistant roles, so the raw phone-summary
system message does not appear in streaming recent history. It can still enter:

- The non-streaming session summary.
- Unrelated prompt memory through the globally searched transcript-digest
  fact.

## Awareness long-term memory

Awareness memory is stored in PostgreSQL and models:

- Semantic facts/preferences.
- Episodic incident memories.
- Structured content.
- Importance.
- Confidence.
- Sensitivity.
- Validity intervals.
- Expiration.
- Status.
- Supersession.
- Conflicts.
- Provenance relationships.
- Embeddings.
- Access counters.

It is not part of the automatic situation snapshot.

## Awareness memory write paths

### Deterministic writes

`POST /memory/deterministic` is intended for explicit or otherwise unambiguous
facts.

It:

- Creates an active memory immediately.
- Deduplicates by content hash.
- Supersedes a same-key active memory when the value changes.
- Writes provenance records.
- Queues embedding work when an embedding model is configured.

### Candidate proposals

`POST /memory/candidates` accepts a strict `CandidateProposal` containing:

- Statement.
- Semantic/episodic type.
- Scope.
- Structured content.
- At least one evidence reference.
- Importance.
- Sensitivity.
- Proposing model.
- Prompt version.

The main runtime does not expose a candidate-proposal tool and does not run an
automatic conversation-extraction job. Normal conversations therefore do not
populate this path.

### Incident episodes

Resolved alerts can produce deterministic episodic memories linked to:

- The alert.
- Up to 50 alert evidence events.

Consolidation can create recurring-incident summaries linked to their source
episodes.

## Awareness evidence-validation limitation

Candidate evidence types include:

- `event`
- `alert`
- `message`
- `conversation`
- `source`
- `user_confirmation`
- `extraction_job`
- `model`

The current validator checks repository existence only for event and alert
UUIDs. Other references are accepted as provided.

Any candidate containing `kind="user_confirmation"` is treated as explicit
evidence, receives higher confidence, and can supersede an old fact. The
validator does not independently prove that the referenced user confirmation
exists.

This is weaker than the documentation's statement that every evidence
reference is validated.

## Awareness memory search

`search_memory` calls `GET /memory/search`.

It:

- Excludes non-active, expired, invalid, superseded, rejected, and deleted
  memories.
- Applies sensitivity filtering.
- Optionally filters memory type and exact scope.
- Uses PostgreSQL full-text ranking.
- Optionally uses an Ollama query embedding and cosine similarity.
- Adds recency, importance, and confidence scores.
- Exposes component scores.
- Updates access counters without changing validity.

Default MCP search limits results to at most 25.

If Ollama embedding is unavailable, the query falls back to full-text search.

### Search pre-limit issue

The SQL query retrieves at most 200 active candidate rows without an SQL
relevance ordering, then calculates and sorts the combined score in Python.

Once more than 200 eligible memories exist, a relevant memory outside that
arbitrary first set cannot be returned.

### Provenance visibility

Search results include the statement, structured content, scope, sensitivity,
validity, score, and component scores. They do not include the associated
provenance rows.

The memory may be provenance-backed in the database, but the model cannot
inspect that evidence from the `search_memory` response itself.

## `remember_memory_fact` bridge

The only routine bridge between the two memory systems is:

```text
remember_memory_fact
    |
    +-- SQLite upsert
    |     prompt-authoritative
    |
    +-- POST /memory/deterministic
          best-effort awareness mirror
```

Execution order matters:

1. SQLite writes first.
2. Awareness synchronization is attempted afterward.
3. Awareness failure is caught.
4. The result still reports the SQLite write as successful and includes
   `awareness_memory_synced=false`.

The tool's model-visible description says to use it when the user explicitly
asks TALOS to remember a stable fact. The host implementation does not verify
the source user turn or require a confirmation token.

Therefore the model still has direct permanent write access to the automatic
prompt-memory store.

## Persistence versus awareness memory

| Property | SQLite persistence | Awareness long-term memory |
|---|---|---|
| Automatically injected | Yes, every answer turn | No |
| Contributes automatic current situation | No | Yes, but situation excludes memories |
| Storage | SQLite | PostgreSQL plus pgvector |
| Conversation history | Yes | No implemented ingestion path |
| Explicit facts | Yes, prompt-authoritative | Best-effort mirror |
| Incident episodes | No | Yes |
| Provenance | Only optional source session ID | Dedicated evidence rows |
| Fact changes | Overwrites `(scope,key)` in place | Supersession/conflict graph |
| Confidence | No | Yes |
| Temporal validity | No | Yes |
| Sensitivity | No | Yes |
| Retrieval | Token substring matching | Full-text plus optional vector |
| Trigger | Automatic prompt assembly | Explicit `search_memory` tool call |
| Deletion | Hard delete or whole SQLite file | Soft delete plus retention |
| Failure behavior | Turn continues without memory | Situation falls back; search returns error |

## Divergence scenarios

The two stores can disagree in several ways:

1. SQLite write succeeds while awareness is down.
2. Awareness incident episodes have no SQLite representation.
3. Awareness candidate memories have no SQLite representation.
4. Awareness supersession does not update SQLite.
5. Awareness deletion does not delete SQLite.
6. SQLite fact deletion does not delete awareness.
7. Clearing a SQLite session preserves its facts and all awareness memory.
8. Clearing awareness memory leaves SQLite facts and conversation history.

There is no:

- Reverse synchronization.
- Reconciliation worker.
- Shared content/version identifier exposed across both systems.
- Drift metric.
- Conflict resolution between SQLite and awareness.

The main prompt continues trusting SQLite whenever they disagree.

## Current configuration relevant to turn construction

Observed non-secret settings:

```text
TALOS_LLM_BACKEND=ollama
TALOS_LLM_BASE_URL=http://127.0.0.1:11434/v1
TALOS_LLM_MODEL=mb-core-v1:latest
OPENAI_VOICE_MODEL=gpt-4o-mini
TALOS_LLM_THINK_MODE=never
TALOS_VOICE_STREAMING=1
TALOS_VOICE_MODEL_ROUTING=0
TALOS_MAX_TOOL_CALL_ROUNDS=16
TALOS_AGENT_MAX_OUTPUT_TOKENS=1024
TALOS_REDUCE_KICAD_TOOL_SURFACE=0
TALOS_SCOPE_TOOL_SURFACE=0
TALOS_MEMORY_ENABLED=1
TALOS_TOOL_OUTPUT_CHAR_LIMIT=4000
TALOS_SUMMARIZE_TOOL_OUTPUTS=1
```

The configured MCP server is:

```json
[
  {
    "name": "talos-local",
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "talos.mcp_server"]
  }
]
```

## Current operational state observed during investigation

This checkout was not capable of a full live inference test:

- `.env` was absent.
- No main or awareness virtualenv directory was present.
- The current shell used Python 3.9 rather than the documented main/awareness
  environments.
- The current Python environment lacked the MCP SDK.
- The current Python environment lacked SQLAlchemy.
- The awareness API at `127.0.0.1:8600` was unreachable.
- Ollama at `127.0.0.1:11434` was unreachable.
- Docker was not available on the shell path.

A live MCP discovery attempt in this shell therefore exposed zero MCP tools,
although the configured and code-defined main environment intends to expose 43
MCP tools plus 13 host tools.

The SQLite persistence database did exist and was readable.

## Verification performed

Passed:

- 31 focused prompt, runtime, streaming, tool-scoping, and SQLite-memory tests.
- 5 awareness-client cache/fallback tests.

Could not run in the current environment:

- MCP provider tests, because the MCP SDK was absent.
- Awareness context tests, because SQLAlchemy and awareness dependencies were
  absent.
- Live awareness calls.
- Live Ollama inference.

No repository files were changed during the investigation that produced this
report.

## Prioritized remediation list

### Priority 0: correctness and safety

1. Remove the post-broker 500-character awareness truncation or make the
   runtime consume the broker's already-budgeted result structurally.
2. Guarantee critical alerts survive every layer, not only broker selection.
3. Exempt awareness tools from generic lossy summarization or implement
   per-tool semantic shaping.
4. Remove or correctly implement `turn_on_lights` through the registered action
   service.

### Priority 1: memory authority and privacy

5. Decide which memory store is authoritative for durable facts.
6. Prevent direct model writes to prompt-authoritative memory without
   programmatic confirmation/policy enforcement.
7. Filter SQLite facts by permitted scopes for the active session/user/project.
8. Add reconciliation or explicit drift reporting between SQLite and awareness.
9. Ensure deletion and reset semantics clearly cover both stores.

### Priority 2: truthful degradation

10. Inject a short explicit marker when awareness is unavailable and fallback
    state is being used.
11. Preserve the situation `truncated` flag and selection limitation in
    model-visible context.
12. Refresh a background job's awareness snapshot when execution starts.

### Priority 3: context efficiency

13. Re-enable specialized tool scoping so 28 kitchen schemas are not sent on
    unrelated turns.
14. Review whether both provider-thread history and SQLite session summaries
    are needed in the Responses lane.
15. Persist compact structured tool evidence when future turns must rely on an
    action or observation.

### Priority 4: awareness-memory completeness

16. Connect the candidate-proposal path to an explicitly authorized extraction
    workflow, or document it as API-only.
17. Validate all evidence-reference types, not just alert and event IDs.
18. Order or rank awareness-memory candidates in SQL before applying the
    200-row bound.
19. Expose memory provenance through search results or a bounded memory
    provenance tool.

