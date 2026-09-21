# 05 — Flexible expression, dependable execution

Status: proposed beyond the no-op lights removal and history repair.

## Problem

Repeated phrase matching and device-specific canned replies make Talos feel
scripted. However, replacing execution checks with model judgment would damage
reliability. The intended division is flexible interpretation/expression with
deterministic validation, execution, and evidence.

## Recommended course

1. Inventory hardcoded responses by purpose: ordinary dialogue, execution result,
   policy/confirmation, emergency notification, and unavailable/error fallback.
2. Remove success-like placeholders. This pass removes `turn_on_lights` from both
   the implementation and MCP registration; it does not substitute an arbitrary
   smart plug or invent a room mapping.
3. Make services return structured facts and outcomes. Let the conversational
   model phrase ordinary replies from those results in its existing response round.
4. Retain deterministic urgent messages, exact numeric constraints, confirmation
   semantics, and truthful fallback when inference is unavailable.
5. Replace device-specific interpretation patches only after the common schema,
   execution/result contract, and local-model evaluations cover their behavior.

Avoid a second inference call solely to vary “done.” An accepted command must not
be rephrased as a completed physical action. The model may infer intent within
the user's authority, but ambiguity about the target/effect can require a brief
clarification. Tool schemas and available entities should support that judgment.

Acceptance: paraphrases route correctly, speech stays natural and concise,
unavailable capabilities are admitted, and no completion claim outruns evidence.
Test both successful and failed actions, not just stylistic response variation.
