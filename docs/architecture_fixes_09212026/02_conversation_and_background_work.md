# 02 — Fast conversation and background reasoning

Status: proposed; no model or scheduler changes in this pass.

## Problem

Conversation must remain responsive while engineering work or investigation
continues. Current foreground/background jobs are a useful foundation, but
routing, model execution, progress, and speech do not yet establish a measured
two-model experience. Two models sharing limited GPU memory can increase latency.

## Recommended course

1. Define a task contract: objective, constraints, evidence references, current
   status, context revision, cancellation, next step, and result.
2. Retain one conversation owner. Background work returns structured progress and
   findings; it does not independently speak over the foreground model.
3. Give short household actions and conversation priority over long analysis.
   Add bounded background concurrency and explicit deadlines before adding models.
4. Bind results to the originating request/context revision. If the user changes
   the target or cancels, stale work cannot execute against the old intent.
5. Revalidate authorization and world state when an action executes. Cancelling
   speech, inference, and an already-dispatched action have different outcomes.
6. Compare one model with scheduling budgets against separate fast/reasoning
   models. Measure residency, switching, GPU contention, and voice response time.

Treat a “train of thought” as resumable task state, not an endlessly generated
private monologue. Trigger work from user requests, relevant events, deadlines,
or bounded review intervals; stop when no useful work remains.

Acceptance scenario: a circuit investigation continues while the owner asks a
household question, changes a requirement, and later requests a status update.
The assistant remains responsive, does not duplicate actions, incorporates the
correction, and reports progress from real task records.

Reuse router/jobs/cancellation mechanisms and the capability executor. Avoid a
new service fleet or autonomous agent hierarchy unless measurements justify it.
