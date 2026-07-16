# Test Strategy

Use the repository's established test framework. Tests are added in their owning phase, kept deterministic where possible, and never weakened to force a pass. Integration dependencies remain local; unit tests use suitable stubs. Every result records the exact command, environment/dependencies, pass/fail/skip counts, failures, and checks not run.

## Test levels

### Unit

Cover strict event versions and payload limits; timezone/timestamp/clock quality; duplicate IDs and source/boot/sequence; gaps and reboot resets; state authority, freshness/offline, conflict, deadband/hysteresis; rule matching and hard-rule priority; alert deduplication/lifecycle; attention interruptibility; outbox idempotency/backoff/locks; context token budgets and routing; memory evidence/candidates/contradiction/supersession; retention protections; and action validation/permissions/confirmation/idempotency/timeouts.

### Integration

Cover MQTT intake through event transaction, state, alert, and outbox; database restart; MQTT reconnect/subscription restore; outbox crash/retry; embedding retry; notification failure; strict tool invocation with a stubbed LLM; clean migrations; and upgrade from the previous schema revision.

### End-to-end and failure injection

Run the scenario matrix below with local dependencies and the configurable simulator. Include broker/database/Ollama/embedding/notification interruption, process crash after event commit, stale worker locks, duplicate delivery, reorder/delay/gap/reboot, context overflow, load pressure, backup/restore, and safe retention.

### Performance/capacity

Phase 8 supplies a representative sensor-traffic benchmark/load utility. Measure acceptance rate, database latency, message lag, outbox latency/backlog, bounded queue behavior, telemetry batching, and tool/context limits. Demonstrate configured low-value shedding without intentional critical-event loss. Record hardware, versions, data shape, duration, and limits; do not extrapolate unsupported capacity claims.

## Requirement-to-test matrix

Status is `not_implemented` until implementation evidence is added.

| Requirement | Phase | Level | Scenario | Expected result | Status |
|---|---:|---|---|---|---|
| EVT-001/002 | 1-2 | Unit | Envelope version, timestamps, provenance | Valid accepted; unknown/invalid rejected with distinct times/clock quality intact | not_implemented |
| EVT-003/004 | 2 | Unit/integration | Oversized, malformed, unauthorized payload/topic | Dead-lettered, logged/metriced, intake continues, no secrets | not_implemented |
| EVT-005/006 | 2 | Unit/E2E | Duplicate IDs and source/boot/sequence | One event effect; duplicate metric; no repeated alert/action/memory | not_implemented |
| EVT-003/004 | 2-3 | Unit/E2E | Delay, reorder, gap, reboot reset | History preserved; state protected; gap/restart classified correctly | not_implemented |
| MQTT-001/002 | 2 | Integration | Broker interruption/reconnect | Existing broker reused; reconnect/subscriptions/health restored | not_implemented |
| MQTT-003 | 2 | Integration | Retained state replay | Provenance marks retained/replayed; age evaluated; not blindly current | not_implemented |
| MQTT-004 | 2 | Simulator | All required input modes | Configured simulator emits heartbeat/telemetry/failure/order/command cases | not_implemented |
| STATE-001/002 | 3 | Unit/integration | Newer/weaker/delayed source updates | One authoritative active row; history separate; source linkage retained | not_implemented |
| STATE-003 | 3 | Unit/E2E | Missed heartbeats | State/source becomes stale then offline at configured thresholds | not_implemented |
| STATE-004 | 3 | Unit | Equal/conflicting sources | Conflict represented; no fabricated certainty | not_implemented |
| STATE-005 | 3 | Unit | Numeric jitter and threshold reversal | Deadband/hysteresis prevent transition noise | not_implemented |
| HIST-001/002 | 2-3 | Integration | Event filters/replay/telemetry insert | Immutable bounded history and typed telemetry query correctly | not_implemented |
| HIST-003 | 3/8 | Integration | Aggregation and retention prerequisite | Required aggregates exist and match raw data before deletion | not_implemented |
| HIST-004 | 3/5 | Unit/integration | Excess time range/points | Request rejected or safely bounded; no raw dump | not_implemented |
| RULE-001/002 | 4 | Unit | Hard rule and policy actions | Deterministic output; model path cannot override or block | not_implemented |
| ALERT-001/002 | 4 | Unit/E2E | Repeated overflow | One incident updated with occurrence/evidence; proper lifecycle | not_implemented |
| ALERT-003 | 4 | E2E | Overflow with Ollama stopped | Event/state/alert/attention/fallback notification succeed | not_implemented |
| NOTIFY-001/002 | 4 | Integration | Adapter success/failure | Confirmed status only; failures persisted/retried/escalated per policy | not_implemented |
| OUTBOX-001/002 | 4 | Failure injection | Crash after event commit before delivery | Work survives and executes idempotently after restart | not_implemented |
| OUTBOX-003 | 4 | Unit/integration | Concurrent claim/stale lock/dead letter | Bounded safe claims, recovery, backlog/error visibility | not_implemented |
| CTX-001/002 | 5 | Unit/E2E | Situation selection and overflow | Relevant state only; low priority drops first; critical alerts survive | not_implemented |
| CTX-003 | 5 | Unit | Token budgets/audit | Hard per-category/total bounds and complete selection audit | not_implemented |
| CTX-004 | 5 | E2E | "Is the pump on?" | Current-state tool, not vector search; freshness/confidence/source | not_implemented |
| CTX-005 | 5 | E2E | "When did it last run?" | Bounded event-history retrieval | not_implemented |
| CTX-006 | 5 | E2E | "Average current yesterday?" | Bounded time-series aggregate | not_implemented |
| CTX-007 | 5-6 | E2E | Recurring overflow diagnosis | Events + aggregate + episode, no raw telemetry dump, uncertainty shown | not_implemented |
| CTX-008 | 5 | Unit/integration | Invalid/unbounded tool input | Strict validation, clear error, logged failure, round/result bounds | not_implemented |
| MEM-001/002 | 6 | Unit/integration | Explicit preference and unsupported proposal | Evidence-backed preference accepted; unsupported claim rejected | not_implemented |
| MEM-003 | 6 | Integration | Overflow episode creation | Episode links supporting events and temporal validity/provenance | not_implemented |
| MEM-004 | 6 | Unit/integration | Preference change/conflicting evidence | Old record preserved/superseded or explicit conflict retained | not_implemented |
| MEM-005 | 6 | Integration | Embedding outage | Text accepted, work queued, exact/full-text works, retry succeeds | not_implemented |
| MEM-006 | 6 | Audit | Embedding corpus | No raw telemetry, heartbeat, raw audio, binaries, or indiscriminate messages | not_implemented |
| ACTION-001/002 | 7 | Unit/integration | Unsupported/unauthorized/unconfirmed action | Rejected without dispatch; transition and actor audited | verified — `test_awareness_actions_{unit,integration}` cover strict registry, durable rejections, permission, safety, confirmation, bearer fail-closed, and no wildcard/raw topic |
| ACTION-003 | 7 | Integration/E2E | Duplicate command request | Physical dispatch occurs once under idempotency policy | verified — exact caller-key duplicate returns one lifecycle/dispatch; changed content is audited/rejected; device-key retry reuses the same command ID |
| ACTION-004 | 7 | E2E | Ack success, silence, failure, timeout | Only acknowledged/result-confirmed command completes; other states truthful | verified — positive/negative/malformed/late ack policy, matching/mismatching legacy state, silence timeout, and MQTT publish failure policies exercised without physical hardware |
| RET-001/002 | 8 | Integration | Retention dry run and execution | Exact bounded plan; aggregate/evidence/provenance protections enforced | not_implemented |
| RET-003 | 8 | Unit/security | Artifact path and deletion | Safe rooted path; checksum/metadata; LLM cannot choose arbitrary path | not_implemented |
| SEC-001/002 | 2/7/8 | Security | MQTT/API/action unauthorized access | Denied, audited, rate/input bounded, no secret leakage | in_progress — Phase 7 action mutations require bearer auth, actor permission, source-bound ack, strict 4 KiB parameters and registry bounds; cross-API rate/security audit remains Phase 8 |
| SEC-003 | 8 | Security | Log/config scan | Secrets absent; no unapproved public/cloud dependency | not_implemented |
| FAIL-001 | 1-8 | Failure injection | Component matrix outages | Each component degrades and reports exactly as specified | not_implemented |
| OPS-007/008 | 1/8 | Integration | Startup/shutdown | Ordered startup, actionable failure, graceful durable shutdown | not_implemented |
| SEC-007 | 8 | Operational | Backup and restore | Documented secured backup restores verified database/artifacts | not_implemented |
| OPS-011/012 | 8 | Performance | Representative load | Bounds/latencies recorded; low-value shedding safe; critical events kept | not_implemented |
| TEST-001 | 1-8 | Migration | Clean and previous revision | Schema migrates successfully; constraints/indexes/extensions verified | not_implemented |

## Required end-to-end scenarios

### Normal telemetry

Update qualified current state and store/aggregate telemetry; create no alert and inject no automatic LLM context.

### Overflow

Device performs local shutdown where supported; the event is validated/stored; state updates; one critical alert and immediate attention item appear; deterministic notification delivers without Ollama; a memory candidate is referenced; the incident remains open until policy resolves it.

### Device offline

Missed heartbeat transitions state/source stale then offline; severity follows criticality; any assistant answer qualifies the last known value.

### Delayed and duplicate events

Delayed data remains in history and is marked without replacing newer state. Duplicate delivery has no duplicate event effects, alert, action, or notification outside policy and increments the duplicate metric.

### Retrieval

Current facts use state, last occurrence uses history, numeric history uses aggregates, recurring problems combine bounded structured history and episodic memory, and each result preserves provenance/uncertainty.

### Context overflow and Ollama outage

Low-priority context is removed before critical alerts and tool results remain bounded. With Ollama unavailable, ingestion/rules/alerts/fallback notification continue, inference work queues, and no fabricated reasoning response is returned.

## Simulator contract

The local configurable simulator targets the existing broker and emits heartbeats, temperature, moisture, pump state, overflow, offline conditions, duplicates, delays, out-of-order messages, gaps, reboot resets, command acknowledgements/failures, and firmware changes. It must not embed credentials or require another broker.
