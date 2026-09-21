# 03 — Give awareness clear responsibilities

Status: proposed; no awareness runtime changes in this pass.

## Problem

The awareness subsystem combines qualified world state, history, alerts,
memories, reminders, attention, briefing selection, and delivery. Much of its
internal separation is sound. Its broad name creates the expectation of general
initiative, which a situation snapshot or scheduled briefing does not provide.

The inspected configuration enables briefings, but optional model ranking
defaults off. Interaction-based presence is not continuous occupancy sensing.
Entity-based relevance cannot contribute when interaction events omit entity IDs.
Enqueued speech is not confirmed playback, and playback is not proof of hearing.

## Recommended course

1. Preserve deterministic ingestion, freshness, exact queries, alerts, durable
   outboxes, and physical-action safeguards.
2. Define separate world-state and attention contracts. Attention candidates need
   evidence, importance, relevance, expiry, interruptibility, cooldown, and a
   recorded selection/suppression reason.
3. Supply credible presence transitions and active-task/entity focus. Do not infer
   departure from silence merely to make arrival briefings trigger.
4. Make the complete path observable: observation → candidate → selection or
   suppression → queued delivery → playback outcome where observable.
5. Permit bounded reasoning to propose evidence-backed insights. Deterministic
   policy retains alert priority, action permissions, and interruption controls.
6. Evaluate with the owner across real daily scenarios: useful information,
   unwanted interruptions, missed important events, repetition, and recall.

Acceptance: silence has an explainable reason; critical alerts still work with
inference down; facts retain source/freshness; the system distinguishes absent
data from negative observations; unsolicited output is useful rather than merely
frequent. Improve logical ownership before moving modules or databases.
