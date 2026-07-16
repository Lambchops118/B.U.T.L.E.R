# Definition of Done

Completing one phase never means the subsystem is complete.

## Every phase

- Entry criteria were verified and only phase scope was implemented.
- Repository conventions and working behavior were preserved; the repository remains runnable.
- Required deliverables and acceptance criteria have evidence.
- Relevant tests/checks ran and exact pass/fail/not-run results are recorded; tests were not weakened.
- Architecture/security/deployment impacts and limitations are documented.
- `IMPLEMENTATION_STATUS.md`, decisions/open questions, and session handoff are updated.
- The required final report is complete and work stops at the phase boundary.

## Subsystem implementation

The subsystem is complete only when all of these are verified:

1. It connects to the existing Raspberry Pi Mosquitto broker without deploying an unapproved second broker.
2. It ingests strict versioned events from distributed sources.
3. Events are immutable and processing is idempotent.
4. Duplicates, delays, missing ranges, reordering, and reboot sequence resets are handled.
5. Durable current state is separate from history.
6. State exposes `current`, `stale`, `unknown`, `conflicting`, `offline`, `inferred`, and `scheduled`.
7. Source health and provenance are queryable.
8. High-volume telemetry is stored/aggregated and is not embedded.
9. Safety events create deterministic alerts without Ollama.
10. Notifications are persisted, retryable, deduplicated, confirmed according to channel semantics, and auditable.
11. Critical downstream work survives crashes through the outbox.
12. Qwen receives a compact relevant situation snapshot.
13. Context budgets are enforced in code and audited.
14. Separate bounded tools retrieve exact current state, history, aggregates, health, provenance, and semantic memory.
15. Long-term memory validates evidence and supports conflict, contradiction, validity, and supersession.
16. Physical actions use a validated action service, authorization/confirmation, idempotency, timeouts, and acknowledgement.
17. Retention is configurable, previewable, aggregate-before-delete, and safe.
18. Component failure produces truthful degraded behavior.
19. No cloud database, vector store, embedding, or inference is required.
20. Existing home automation functionality continues to work.

## Testing completion

- Core unit, integration, end-to-end, failure-injection, migration, simulator, and representative capacity checks pass, or any infeasible check is explicitly justified and owner-accepted.
- Clean migrations and previous-revision upgrades are tested.
- Overflow, offline, delayed, duplicate, current-state, recurring-history, context-overflow, and Ollama-outage scenarios meet [`TEST_STRATEGY.md`](TEST_STRATEGY.md).
- Backup restoration is tested where feasible; no unrun test is reported as passed.

## Documentation and operations completion

- Architecture/topology, remote broker rationale, data flow, schema/migrations, MQTT topics, event/source/state/freshness/history/telemetry, alert/attention/notification/outbox, memory/context/tools/actions/retention, setup/service configuration, broker TLS/auth, tests/simulator, backup/restore, troubleshooting, limitations, and migration paths are current.
- Deployment/startup/shutdown, health, metrics, capacity, retention, secrets, backup, and recovery runbooks match the deployed system.
- The final report includes files/migrations/decisions/assumptions/tests/failures/limitations/security/deployment plus complete data flow, table summary, APIs/tools, MQTT topics, outbox work types, notification channels, retention defaults, restore instructions, and remaining extension points.
