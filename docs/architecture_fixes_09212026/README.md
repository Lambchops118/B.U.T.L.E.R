# Architecture fixes — 2026-09-21

This is the follow-up work collection for the owner's architecture consultation.
Talos is a voice-first, home-bound assistant with continuity across household
tasks and engineering work. Inference is local; AWS Polly is the current hosted
speech exception. OpenAI documentation is a design reference, not the deployment.

| Work item | Status |
|---|---|
| [01 — Tool history and the tool-calling spiral](01_tool_history.md) | Bounded local streaming repair implemented; live model acceptance pending |
| [02 — Fast conversation and background reasoning](02_conversation_and_background_work.md) | Proposed |
| [03 — Awareness responsibilities and usefulness](03_awareness.md) | Proposed |
| [04 — Native voice evaluation](04_native_voice.md) | Proposed |
| [05 — Flexible expression and deterministic execution](05_expression_and_execution.md) | Proposed |
| [06 — Behavioral evaluation and CI](06_evaluation.md) | Proposed |
| [07 — Detailed tool architecture, RAG, and InfoPanel](07_tool_system.md) | Review; no-op lights removal and history repair implemented only |

Future agents must follow the [tool implementation guide](../TOOL_IMPLEMENTATION_GUIDE.md),
root [AGENTS.md](../../AGENTS.md), and [architectural invariants](../awareness-memory/ARCHITECTURAL_INVARIANTS.md).
The [original architecture review](../ARCHITECTURE_REVIEW.md) supplies overall
context; the [session handoff](SESSION_HANDOFF.md) records this pass's changes and tests.

Recommended order: validate the history repair on the deployed model, establish
the small evaluation baseline, repair tool execution/result contracts, improve
foreground/background coordination, then evaluate attention and speech options.
Do not begin these proposed work packages without a separately assigned scope.
