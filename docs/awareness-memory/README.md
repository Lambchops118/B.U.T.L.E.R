# Awareness and Memory Implementation Guide

This documentation divides the robust distributed presence, awareness, event-processing, state, history, alerting, and memory subsystem into bounded implementation sessions. It is designed for coding agents that must preserve the architecture without loading a 2,800-line prompt on every turn.

New operator or intern? Start with
[`like_im_a_child_or_golden_retriever.md`](like_im_a_child_or_golden_retriever.md)
for the plain-language mental model, copy/paste startup path, TALOS integration
map, maintenance checklist, code-reading route, and troubleshooting guide.

> **Do not load everything.** A normal session should load only root `AGENTS.md`, `IMPLEMENTATION_STATUS.md`, the current phase document, and the few shared references named by that phase.

## Authority and organization

The [original specification](../ROBUST_HOME_AUTOMATION_MEMORY_IMPLEMENTATION_PROMPT.md) is authoritative and remains unchanged. These files canonicalize repeated material and make it executable phase by phase:

- [`SPEC_INDEX.md`](SPEC_INDEX.md) routes topics and phase reading.
- [`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md) contains permanent system constraints.
- [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) maps source requirements to canonical homes and verification.
- [`reference/`](reference/) contains reusable schemas, failure, security, testing, operations, component, and completion material.
- [`phases/`](phases/) contains nine bounded implementation specifications.
- [`prompts/`](prompts/) contains concise launcher prompts, not duplicate specifications.
- [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md), [`DECISIONS.md`](DECISIONS.md), [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md), and [`SESSION_HANDOFF_TEMPLATE.md`](SESSION_HANDOFF_TEMPLATE.md) support fresh-session handoff.

## Starting or resuming work

1. A human selects one phase or smaller bounded task and updates `IMPLEMENTATION_STATUS.md`.
2. The agent reads `AGENTS.md`, status, the selected phase, and only its required references.
3. The agent verifies entry criteria and inspects relevant repository code before editing.
4. The agent implements only in-scope work, runs required tests, and records truthful results.
5. The agent updates status, decisions/questions, and a handoff based on the template.
6. The agent submits the phase report and stops. The owner reviews before authorizing the next phase.

For Phase 0, use [`prompts/PHASE_00_PROMPT.md`](prompts/PHASE_00_PROMPT.md). Phase 0 performs discovery only, produces `DISCOVERY.md`, and stops for mandatory owner review. A fresh agent resumes from status and the most recent handoff, not by rereading all prior phase documents.

## When to consult the original

Consult the complete source only for ambiguity, apparent omission, traceability or architecture-wide audit, or an explicit owner request. When a source conflict is found, preserve both readings, record the issue, recommend an interpretation, and wait for owner confirmation where the choice affects implementation.
