# 01 — Repair the tool-calling spiral

## Problem and evidence

Local Chat Completions receives reconstructed conversation history. Previously
`MemoryStore.record_turn` retained only user and final assistant prose, even when
the answer required tool calls. Repeated confident prose taught the model a
misleading pattern. ADR-049 records historical reproduction; its prompt notice
was mitigation, not a durable record of what happened.

Tool schemas describe available operations. Tool calls and results establish
what was attempted and observed. Both are needed, but serve different purposes.
Putting a list of schemas into old prose would not repair missing evidence.

## Implemented in this bounded pass

- Persist complete assistant-call/tool-result exchanges with matching IDs and
  snapshots of the used tools' schemas in the existing SQLite message metadata.
- Write the user and assistant records atomically. No table migration or database
  reset is required; old prose-only messages remain readable and unverified.
- Reconstruct actual tool-role messages for later local streaming turns. The
  existing message/character budgets apply; oversized tool turns are omitted
  whole instead of clipping argument JSON or leaving orphan results.
- Supply current available schemas in the backend's tools field. Preserve a
  previously used tool through command-keyword scoping when still available.
  Never restore a removed/disabled capability from its archived schema.
- Preserve completed tool exchanges if a subsequent inference round fails or
  generation is interrupted. Interruption edits spoken dialogue without erasing
  the tool evidence or retaining intermediate speech as though it was heard.
- Retain a grounding reminder: past observations are not automatically current
  state. Replay does not re-execute any historical tool.

The changed path is `run_command_stream`, used by the local streaming backend.
The legacy Responses path remains unchanged and is not a claim of hosted API use.
Memory-disabled operation cannot preserve history. The stored tool results are
the bounded results delivered to the model, not an additional raw audit database.

## Next course of action

1. Restart the deployed agent to load the repair; preserve its existing database.
2. Test the same local model/template with clean and long histories, prior failed
   tool results, changed device state, and keyword-free follow-ups.
3. Compare correct tool use and factual completion against the old behavior, using
   simulated or read-only calls before physical actions.
4. Measure history budget omissions. If common useful exchanges are too large,
   design typed summaries/detail references or a measured budget adjustment.
   Do not silently increase context indefinitely or strip call/result pairing.

Acceptance: correct pairing survives restart and truncation; disabled tools stay
disabled; old prose is not fabricated into evidence; failure/cancellation does
not lose completed calls; the model rechecks stale state. Unit tests establish
the history contract, not a guarantee that every future model call will succeed.
Live accuracy and latency still require measurement on the deployed host.
