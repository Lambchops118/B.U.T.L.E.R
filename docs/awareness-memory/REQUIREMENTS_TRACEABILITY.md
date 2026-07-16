# Requirements Traceability

This ledger maps material source requirements to one canonical execution home. The unchanged [original specification](../ROBUST_HOME_AUTOMATION_MEMORY_IMPLEMENTATION_PROMPT.md) controls if a summary here is ambiguous. Component IDs C1-C18 are preserved in [`reference/COMPONENT_MAP.md`](reference/COMPONENT_MAP.md); source requirement IDs R1-R22 are preserved below.

Status values are `not_implemented`, `in_progress`, `verified`, `blocked`, or `not_applicable`. All runtime requirements start `not_implemented`; documentation restructuring is not implementation. Phase 0 discovery requirements also remain unimplemented until an authorized discovery session produces reviewed evidence.

Columns: **Canonical** is the detailed reorganized home; **Verify** is the acceptance method; **Dependencies/notes** preserves repetition or gating.

## Source requirements R1-R22

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| R1 | Ingest sensors, microcontrollers, services, conversations, and internal streams | §7 R1; C2-C3 | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Adapter/envelope integration and simulator | not_implemented; conversation storage continues in 6 |
| R2 | Store, aggregate, summarize, or discard according to policy | §7 R2; C4-C6,C15 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md), [Phase 8](phases/PHASE_08_HARDENING.md) | 1,3,6,8 | Persistence, aggregate, consolidation, retention tests | not_implemented |
| R3 | Distinguish current state from historical events | §7 R3; P1; C5-C6 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,3 | State/history separation tests | not_implemented |
| R4 | Maintain strong environmental awareness | §7 R4; C5,C12 | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 3,5 | Qualified state and situation scenarios | not_implemented |
| R5 | Track when, where, how, and source of entry | §7 R5; P7; C1,C4 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-3 | Provenance/timestamp/source queries | not_implemented |
| R6 | Track connected-system state and health | §7 R6; C4,C5,C16 | [failure](reference/FAILURE_AND_RECOVERY.md), [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 1,3,8 | Health/stale/offline tests | not_implemented |
| R7 | Detect important events independently of LLM | §7 R7; P2; C7 | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Hard-rule and Ollama-outage E2E | not_implemented |
| R8 | Proactively alert user | §7 R8; C8-C9 | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Overflow delivery/lifecycle tests | not_implemented |
| R9 | Remain fully local | §7 R9; C17 | [security](reference/SECURITY_AND_PRIVACY.md) | 0-8 | Dependency/network/data-flow audit | not_implemented |
| R10 | Avoid unnecessary LLM context | §7 R10; P3; C12 | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Relevance/budget/context-overflow tests | not_implemented |
| R11 | Push only relevant data to LLM | §7 R11; C7,C12 | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Selection/audit/unrelated-state tests | not_implemented |
| R12 | Let LLM retrieve additional information | §7 R12; C13 | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md), [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 5-6 | Strict routing and memory-search tests | not_implemented |
| R13 | Remain functional without Ollama | §7 R13; C7,C8,C16 | [failure](reference/FAILURE_AND_RECOVERY.md) | 4,6,8 | Ollama/embedding outage scenarios | not_implemented |
| R14 | Handle duplicates, delay, missing, and reorder | §7 R14; C1,C3,C5 | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2-3 | Simulator/order/idempotency tests | not_implemented |
| R15 | Persist critical downstream work reliably | §7 R15; C10 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,4,6-8 | Crash/retry/outbox tests | not_implemented |
| R16 | Support stale, conflicting, unknown, offline state | §7 R16; C5 | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3 | Status/freshness/conflict tests | not_implemented |
| R17 | Support validated semantic/episodic memory | §7 R17; C11 | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Candidate/episode/retrieval tests | not_implemented |
| R18 | Preserve memory provenance and temporal validity | §7 R18; C11 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 6 | Evidence/validity/supersession tests | not_implemented |
| R19 | Support distributed LAN deployment | §7 R19; C2,C16,C17 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0,2,8 | Topology/reconnect/security/failure tests | not_implemented |
| R20 | Integrate without unnecessary rewrites | §7 R20; P9; C18 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0-8 | Discovery mapping and regression evidence | not_implemented |
| R21 | Validate physical actions and acknowledgements | §7 R21; C14 | [Phase 7](phases/PHASE_07_ACTIONS.md) | 7 | Permission/idempotency/ack tests | not_implemented |
| R22 | Configurable retention and safe deletion | §7 R22; C15 | [Phase 8](phases/PHASE_08_HARDENING.md) | 8 | Dry-run/protection/aggregate tests | not_implemented |

## Discovery, architecture, and technology

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| DISC-001 | Inspect language/runtime/toolchain and entry points | §4.1 items 1-2 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Evidence in `DISCOVERY.md` | not_implemented |
| DISC-002 | Inspect Ollama/Qwen and tool calling | §4.1 items 3-4 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Code/config mapping | not_implemented |
| DISC-003 | Inspect STT/TTS/notification/phone/calendar/weather/voice | §4.1 item 5 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Integration map | not_implemented |
| DISC-004 | Inspect DB/ORM/migrations/storage/cache | §4.1 item 6 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Persistence map | not_implemented |
| DISC-005 | Inspect MQTT topics/auth/reconnect/schemas and other transports | §4.1 items 7-8 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Transport evidence | not_implemented |
| DISC-006 | Inspect Home Assistant overlap and stop for owner ownership decision | §4.1 item 9; §4.3 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Three-option gate documented | not_implemented; owner decision |
| DISC-007 | Inspect existing device/entity/action/event/conversation/agent models | §4.1 item 10 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Model map | not_implemented |
| DISC-008 | Inspect deployment/process/test/CI/logging/security/backup | §4.1 items 11-14 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Evidence/risk map | not_implemented |
| DISC-009 | Identify every repository conflict with source | §4.1 item 15 | [open questions](OPEN_QUESTIONS.md) | 0 | Conflict table, both readings preserved | not_implemented |
| DISC-010 | Document actual hosts, broker port/TLS/auth/ACLs, clients/endpoints/network | §4.2 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Deployment topology | not_implemented |
| DISC-011 | Document Linux/embedded clock quality and offline responsibilities | §4.2 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Evidence/unknowns labeled | not_implemented |
| DISC-012 | Produce complete `DISCOVERY.md` outputs and C1-C18 mapping | §4.4 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Deliverable checklist | not_implemented |
| DISC-013 | Label assumptions by repo, owner, or confirmation-needed | §4.4 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Label audit | not_implemented |
| DISC-014 | Decide explicitly whether defaults apply and propose DB deployment | §4.4; §5 | [decisions](DECISIONS.md) | 0 | Owner-reviewed recommendation | not_implemented |
| DISC-015 | Phase 0 performs no runtime implementation and stops for review | Opening; §4; §12 Phase 0; §20 | [Phase 0](phases/PHASE_00_DISCOVERY.md) | 0 | Git/file-scope and owner gate | not_implemented |
| ARCH-001 | Keep current/history/telemetry/memory distinct | P1 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | Cross-schema/retrieval audit | not_implemented; repeats R3 |
| ARCH-002 | LLM never owns deterministic hot/safety paths | P2 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | Outage and architecture audit | not_implemented; repeats R7/R13 |
| ARCH-003 | Context is selected and exact questions structured | P3-P4 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 5-6 | Routing/budget tests | not_implemented; repeats R10-R12 |
| ARCH-004 | Central DB authoritative; MQTT transport | P6; §6.1 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | State/restart/reconnect audit | not_implemented |
| ARCH-005 | Preserve provenance/uncertainty and truthful degradation | P7-P8 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | Schema/failure/result audit | not_implemented |
| ARCH-006 | Integrate additively; modular monolith default | P9-P10 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 0-8 | Discovery mapping/regression | not_implemented |
| OPS-001 | Existing suitable stack precedes defaults | §5 opening; §5.4 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-1 | Discovery decision | not_implemented |
| OPS-002 | Default local stack retains documented technologies where applicable | §5.1 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-1,6 | Deployment/config evidence | not_implemented |
| OPS-003 | Redis only for demonstrated transient need, never authority | §5.3 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-1 | Decision/data-ownership audit | not_implemented |
| OPS-004 | Substitute preserves transactions/history/state/outbox/queries/memory/migrations/backups/concurrency | §5.4 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-1 | Owner-approved property matrix | not_implemented |

## Events, MQTT, ingestion, and registries

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| EVT-001 | Use strict versioned envelope with every required identity/time/severity/retention/payload/provenance field | C1 schema | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-2 | Schema/unit/migration tests | not_implemented |
| EVT-002 | Preserve six clock-quality values and distinct timestamp/validity semantics in UTC | C1 timestamp rules | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-3 | Parse/order/render tests | not_implemented |
| EVT-003 | Require event ID; support sequence/boot; detect duplicate tuple/gaps/reorder | C1 identity/ordering | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Simulator and uniqueness tests | not_implemented |
| EVT-004 | Retain delayed events even when state cannot update | C1 identity; C5 update | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 2-3 | Delayed-event E2E | not_implemented |
| EVT-005 | Validate schema version, payload bounds, and topic ownership | C1 validation | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Reject/dead-letter tests | not_implemented |
| EVT-006 | Malformed/unauthorized input dead-letters without crash and emits safe logs/metrics | C1 validation; C3 | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Invalid/auth integration | not_implemented |
| MQTT-001 | Reuse existing broker; do not deploy second without proof and approval | Mission; §5.2; C2 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0,2 | Deployment diff/connect test | not_implemented |
| MQTT-002 | Preserve established topic conventions or require migration plan | C2 MQTT rules | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 0,2 | Topic inventory/config test | not_implemented |
| MQTT-003 | Apply QoS guidance plus application idempotency | C2 QoS | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2,7 | Topic/config/duplicate tests | not_implemented |
| MQTT-004 | Mark retained/replayed provenance, evaluate age, never assume authority | C2 retained | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 2-3 | Retained scenario | not_implemented |
| MQTT-005 | Bounded jittered reconnect, health, subscription restore, session/keepalive, shutdown, lag metrics | C2 connection | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Broker interruption tests | not_implemented |
| MQTT-006 | Thin non-MQTT adapters normalize all downstream data to events | C2 non-MQTT | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2+ | Adapter contract tests | not_implemented; implement only existing sources |
| MQTT-007 | Repository-controlled remote buffering is disk-bounded, pre-ID, ordered/useful, duplicate-safe with drop policy | C2 buffering | [failure](reference/FAILURE_AND_RECOVERY.md) | 2,8 | Offline queue/replay/capacity tests | not_implemented; capability-dependent |
| MQTT-008 | Do not claim buffering for clients that lack it | C2 buffering | [failure](reference/FAILURE_AND_RECOVERY.md) | 0,2 | Documentation/evidence audit | not_implemented |
| INGEST-001 | Execute deterministic ordered pipeline stages | C3 stages | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2 | Stage integration tests | not_implemented |
| INGEST-002 | Support every listed classification output | C3 classification | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 2-8 | Enum/policy coverage | not_implemented; handlers phased |
| INGEST-003 | Duplicate processing creates no duplicate event/alert/notification/action/memory | C3 idempotency | [Phase 2](phases/PHASE_02_MQTT_INGESTION.md) | 2-7 | Duplicate E2E by phase | not_implemented |
| INGEST-004 | Use DB uniqueness in addition to application checks | C3 idempotency | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-2 | Constraint/race tests | not_implemented |
| INGEST-005 | Applicable event/history/measurement/state/health/alert/attention/memory/action/outbox intent is one transaction | C3 transaction | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-7 | Rollback/crash tests | not_implemented; phased columns |
| INGEST-006 | No notification, embedding, network, or LLM call inside transaction | C3 transaction | [invariants](ARCHITECTURAL_INVARIANTS.md) | 2-8 | Code/integration timing audit | not_implemented |
| REG-001 | Support all listed entity/source types and typed validity relationships | C4 entities/relationships | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1 | Schema/repository tests | not_implemented |
| REG-002 | Preserve every required source registry field | C4 source fields | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-3 | Migration/model tests | not_implemented |
| REG-003 | Support seven source health statuses | C4 health | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,3 | Enum/transition tests | not_implemented |
| REG-004 | Source silence severity follows criticality/policy | C4 health | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3-4 | Offline rule tests | not_implemented |

## State, history, and telemetry

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| STATE-001 | DB-authoritative state preserves every required field and one active entity/property | C5 authority/model | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,3 | Constraint/model tests | not_implemented |
| STATE-002 | Support current/stale/unknown/conflicting/offline/inferred/scheduled | C5 statuses | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,3 | Enum/result tests | not_implemented |
| STATE-003 | Compare newness, authority/priority, confidence, delay and clock quality | C5 update | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3 | Authority matrix tests | not_implemented |
| STATE-004 | Preserve event linkage, conflicts, freshness, and meaningful transitions | C5 update | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3 | Conflict/provenance tests | not_implemented |
| STATE-005 | Apply deadbands and hysteresis | C5 update | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3 | Jitter/threshold tests | not_implemented |
| STATE-006 | Deterministic freshness marks stale/offline and deduplicates policy alerts | C5 freshness | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3-4 | Clock/worker/alert tests | not_implemented |
| STATE-007 | Compact situation uses relevant state/alerts/attention/transitions/location/conversation/tasks/health | C5 situation; C12 | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Situation selection tests | not_implemented |
| STATE-008 | Do not store massive regenerated prose world narrative | C5 situation | [invariants](ARCHITECTURAL_INVARIANTS.md) | 5 | Storage/context audit | not_implemented |
| HIST-001 | Events are append-only with exact/bounded filtered/correlated/replay/provenance queries | C6 event store | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 2-3 | Immutability/query tests | not_implemented |
| HIST-002 | Telemetry preserves every typed measurement field | C6 measurements | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 3 | Migration/insert/query tests | not_implemented |
| HIST-003 | Use suitable hypertables and configurable raw/minute/hour/day aggregates/statistics | C6 aggregation | [Phase 3](phases/PHASE_03_STATE_TELEMETRY.md) | 3,8 | Aggregate correctness/retention | not_implemented |
| HIST-004 | Never embed raw telemetry | C6 aggregation; C11 embeddings | [invariants](ARCHITECTURAL_INVARIANTS.md) | 3,6 | Corpus audit | not_implemented |
| HIST-005 | Reject unbounded history ranges and unlimited points | C6 query | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 3,5 | Limit/error tests | not_implemented |
| HIST-006 | Use JSONB flexibly but typed columns for common queries | C6 event store | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-3 | Schema/index audit | not_implemented |

## Rules, alerts, notifications, and outbox

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| RULE-001 | Separate hard rules, classification, noncritical salience, retention, interruptibility | C7 policy | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 4 | Policy schema/tests | not_implemented |
| RULE-002 | Salience may use all listed relevance/novelty/persistence/confidence/etc. inputs | C7 salience | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Score-policy tests | not_implemented; noncritical only |
| RULE-003 | Strict version-controlled rules support thresholds/transitions/windows/health/correlation/cooldown/dedupe/escalation/resolution/suppression | C7 rules | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Schema/matching/lifecycle tests | not_implemented |
| RULE-004 | Optional LLM classifier is async/noncritical and cannot block/override/alert/write memory | C7 optional LLM | [invariants](ARCHITECTURAL_INVARIANTS.md) | 4,6 | Architecture/outage tests | not_implemented; optional |
| ALERT-001 | Alerts preserve all fields and five statuses | C8 alerts | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,4 | Migration/lifecycle tests | not_implemented |
| ALERT-002 | Attention preserves all fields and four interruptibility values | C8 attention | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,4 | Model/policy tests | not_implemented |
| ALERT-003 | One incident updates one alert with first/last/count/latest evidence | C8 dedupe | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Repeated-overflow test | not_implemented |
| ALERT-004 | Timing examples follow severity, criticality, call/conversation context | C8 behavior | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Policy scenario tests | not_implemented |
| NOTIFY-001 | Use one extensible adapter interface; initially only existing channels | C9 interface | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Adapter contract tests | not_implemented |
| NOTIFY-002 | Critical flow creates outbox, deterministic wording, persistent attempt/result/retry before optional LLM improvement | C9 critical | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 4 | Ollama-down/crash E2E | not_implemented |
| NOTIFY-003 | Preserve every notification delivery field | C9 delivery model | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 4 | Migration/adapter tests | not_implemented |
| NOTIFY-004 | Delivered status requires channel-specific adapter confirmation | C9 delivery | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | False-positive prevention | not_implemented |
| NOTIFY-005 | Support cooldown, rate limit, fallback/escalation, quiet hours, call suppression, acknowledgement | C9 cooldown/escalation | [Phase 4](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | 4 | Policy/failure tests | not_implemented |
| OUTBOX-001 | Outbox supports every listed work type and required field | C10 outbox | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,4,6-8 | Schema/handler coverage | not_implemented |
| OUTBOX-002 | Workers safely claim bounded batches, are idempotent, back off, release stale locks | C10 workers | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 4 | Concurrency/recovery tests | not_implemented |
| OUTBOX-003 | Expose backlog/age; dead-letter unrecoverable; manual retry; sanitized errors | C10 workers | [failure](reference/FAILURE_AND_RECOVERY.md) | 4,8 | Health/error/manual tests | not_implemented |
| OUTBOX-004 | Implement at-least-once plus idempotency/uniqueness; never true exactly once | C10 exactly once; §17 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | Duplicate/crash/docs audit | not_implemented |

## Context and long-term memory

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| MEM-001 | Keep working, episodic, semantic, procedural, archival telemetry distinct | C11 memory types | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 6 | Storage/type audit | not_implemented |
| MEM-002 | Preserve all memory fields, seven statuses, and sensitivity | C11 schema; C17 privacy | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 6 | Migration/model tests | not_implemented |
| MEM-003 | Separate provenance links to events/messages/conversations/sources/confirmation/job/model/prompt | C11 provenance | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 6 | Evidence traversal tests | not_implemented |
| MEM-004 | Deterministic writes only when unambiguous and policy allows | C11 path 1 | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Explicit fact/preference tests | not_implemented |
| MEM-005 | LLM produces strict candidate only; manager validates evidence and records decision | C11 path 2 | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Unsupported/valid candidate tests | not_implemented |
| MEM-006 | Candidate flow handles duplicates, contradictions, confidence, sensitivity, merge/supersede, embedding queue | C11 path 2 | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Full candidate matrix | not_implemented |
| MEM-007 | Consolidation groups/summarizes meaningfully, preserves evidence, decays inference, favors explicit user, re-embeds | C11 path 3 | [Phase 8](phases/PHASE_08_HARDENING.md) | 8 | Consolidation/provenance tests | not_implemented |
| MEM-008 | Never overwrite changed fact; preserve validity and supersession link | C11 contradiction | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 6 | Preference-change test | not_implemented |
| MEM-009 | Inconclusive conflict retains both and explicit evidence outweighs weak inference | C11 contradiction | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Conflict/scoring tests | not_implemented |
| MEM-010 | Use configured local Ollama embeddings only for selected semantic material | C11 embeddings | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Config/corpus audit | not_implemented |
| MEM-011 | Do not embed every message/sample/heartbeat, raw audio, redundant events, binaries | C11 embeddings | [invariants](ARCHITECTURAL_INVARIANTS.md) | 6 | Corpus exclusion tests | not_implemented |
| MEM-012 | Hybrid retrieval combines listed scores/filters and returns components | C11 retrieval | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Ranking/filter/debug tests | not_implemented |
| MEM-013 | Episodes are event/interaction/transition/preference/pattern/selected-summary driven, not blind interval | C11 episode guidance | [Phase 6](phases/PHASE_06_LONG_TERM_MEMORY.md) | 6 | Trigger/noise tests | not_implemented |
| MEM-014 | Conversations/messages/sessions remain separate from memories | §10 tables; Phase 6 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,6 | Schema/write-path tests | not_implemented |
| CTX-001 | Deterministic compact situation, not raw DB/prose dump | C12 situation | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Snapshot/relevance test | not_implemented |
| CTX-002 | Enforce nine-level canonical context priority | C12 order | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 5 | Priority/truncation tests | not_implemented |
| CTX-003 | Always retain active critical alerts; select other state by relevance | C12 relevance | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Overflow/unrelated-state tests | not_implemented |
| CTX-004 | Separate budgets; actual/conservative tokenizer; low priority truncates first | C12 token budgets | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Budget/overflow tests | not_implemented |
| CTX-005 | Audit each included item ID/provenance/reason/time/tokens/priority/truncation | C12 audit | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 5 | Audit completeness test | not_implemented |
| CTX-006 | Model-visible facts expose temporal status/times/age/expiry/status/confidence/source | C12 temporal | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 5 | Tool/render tests | not_implemented |
| CTX-007 | Provide all minimum narrow read tools | C13 minimum tools | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5-6 | Tool inventory/schema tests | not_implemented; memory tool completed in 6 |
| CTX-008 | Validate inputs, enforce limits/rounds, log errors, no SQL/files | C13 principles | [security](reference/SECURITY_AND_PRIVACY.md) | 5 | Abuse/limit/error tests | not_implemented |
| CTX-009 | Route state/time/numeric/causal/preference/trust questions to correct stores | C13 examples | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5-6 | Example routing E2E | not_implemented |
| CTX-010 | Bound/summarize tool output before context | C13 output | [Phase 5](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | 5 | Large-result tests | not_implemented |

## Actions, retention, failure, security, operations, tests, and documentation

| ID | Requirement | Original section | Canonical | Phase | Verify | Status / dependencies |
|---|---|---|---|---:|---|---|
| ACTION-001 | LLM uses structured registered action request only; no arbitrary MQTT/shell/bypass/invention | C14 rules; §17 | [Phase 7](phases/PHASE_07_ACTIONS.md) | 7 | Abuse/unsupported tests | not_implemented |
| ACTION-002 | Preserve every action definition field | C14 registry | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 7 | Registry/schema tests | not_implemented |
| ACTION-003 | Preserve every lifecycle status and transition | C14 lifecycle | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 7 | Transition tests | not_implemented |
| ACTION-004 | Command includes ID/idempotency/target/params/actor/correlation/timeout/ack | C14 MQTT commands | [Phase 7](phases/PHASE_07_ACTIONS.md) | 7 | Payload/audit tests | not_implemented |
| ACTION-005 | Silence is not success; confirm resulting state where possible | C14 MQTT commands | [Phase 7](phases/PHASE_07_ACTIONS.md) | 7 | Timeout/state mismatch tests | not_implemented |
| ACTION-006 | Immediate critical shutdown remains local | C14 local safety; confirmed fact 9 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 0,7 | Firmware boundary docs/test evidence | not_implemented |
| RET-001 | Configurable retention preserves every source class/default strength | C15 classes | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 8 | Policy audit | not_implemented |
| RET-002 | Worker dry-runs, logs exact plan, batches, aggregates first, protects evidence/provenance, resumes idempotently | C15 worker | [Phase 8](phases/PHASE_08_HARDENING.md) | 8 | Retention failure/protection tests | not_implemented |
| RET-003 | Large artifacts stay outside DB with every metadata field and safe paths | C15 artifacts | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 8 | Path/checksum/deletion tests | not_implemented |
| RET-004 | LLM cannot select arbitrary artifact paths | C15 artifacts | [security](reference/SECURITY_AND_PRIVACY.md) | 8 | Traversal/tool abuse tests | not_implemented |
| FAIL-001 | Track all listed component/worker/disk/source/schema/prompt/backup health | C16 health | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 1,8 | Health contract/failure tests | not_implemented |
| FAIL-002 | Ollama outage preserves deterministic behavior and queues inference work | C16 Ollama | [failure](reference/FAILURE_AND_RECOVERY.md) | 4,6,8 | Outage E2E | not_implemented |
| FAIL-003 | DB outage is truthful; optional spool is bounded/fsynced/idempotent/documented/not permanent | C16 PostgreSQL | [failure](reference/FAILURE_AND_RECOVERY.md) | 1,8 | Restart/spool tests | not_implemented; spool optional |
| FAIL-004 | MQTT outage exposes health, retries, preserves command state, updates freshness | C16 MQTT | [failure](reference/FAILURE_AND_RECOVERY.md) | 2,3,7,8 | Broker outage tests | not_implemented |
| FAIL-005 | Embedding outage stores text, queues work, keeps exact/full-text/state/alerts | C16 embedding | [failure](reference/FAILURE_AND_RECOVERY.md) | 6,8 | Outage/retry tests | not_implemented |
| FAIL-006 | Notification failure persists/retries/escalates, stays undelivered, alert open | C16 notification | [failure](reference/FAILURE_AND_RECOVERY.md) | 4,8 | Adapter failure tests | not_implemented |
| FAIL-007 | Structured logs include canonical IDs and no secrets | C16 logging | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 1-8 | Log/redaction tests | not_implemented |
| FAIL-008 | Expose every minimum metric | C16 metrics | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 2-8 | Metrics inventory/scenarios | not_implemented |
| SEC-001 | Private binding, DB protection, MQTT auth/ACL/topic ownership/TLS support | C17 network | [security](reference/SECURITY_AND_PRIVACY.md) | 0-2,8 | Network/config/auth tests | not_implemented |
| SEC-002 | Authenticate/authorize/rate/input-bound state-changing/sensitive APIs; CSRF as relevant | C17 APIs | [security](reference/SECURITY_AND_PRIVACY.md) | 1,4,5,7,8 | Security tests | not_implemented |
| SEC-003 | No arbitrary SQL/shell/file/MQTT action access | C17 APIs; C13-C14 | [security](reference/SECURITY_AND_PRIVACY.md) | 5,7,8 | Abuse tests | not_implemented |
| SEC-004 | No hard-coded secrets; examples only; real secrets excluded/redacted | C17 secrets | [security](reference/SECURITY_AND_PRIVACY.md) | 1-8 | Repo/log scan | not_implemented |
| SEC-005 | Memory labels, explicit audited deletion, configurable sensitive-data retention | C17 privacy | [security](reference/SECURITY_AND_PRIVACY.md) | 6,8 | Privacy policy/deletion tests | not_implemented |
| SEC-006 | No stored data sent externally except intended explicit integration | C17 privacy | [security](reference/SECURITY_AND_PRIVACY.md) | 0-8 | Data-flow/network audit | not_implemented |
| SEC-007 | Local secured backup covers DB/schema/artifacts/nonsecret config and tested restoration | C17 backups | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 8 | Backup/restore evidence | not_implemented |
| OPS-005 | Typed config includes every listed database/MQTT/Ollama/budget/policy/worker/data/API key and validates startup | C18 config | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1 | Config schema/failure tests | not_implemented |
| OPS-006 | Internal APIs cover listed health/entity/state/event/alert/attention/source/memory/situation/action capabilities with bounds | C18 APIs | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1,3-7 | Route/auth/limit tests | not_implemented; adapt conventions |
| OPS-007 | Startup follows validated ordered sequence without races | C18 startup | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 1-8 | Startup failure/order tests | not_implemented |
| OPS-008 | Shutdown drains/bounds intake, releases claims, closes resources, preserves retry work | C18 shutdown | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 2-8 | Shutdown/restart tests | not_implemented |
| OPS-009 | Use migrations and every minimum table/constraint/index property | §10 | [schemas](reference/DATA_MODELS_AND_SCHEMAS.md) | 1-8 | Migration/schema/index audit | not_implemented |
| OPS-010 | Add focused modules only when their phase is implemented | §11 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 1-8 | File-scope review | not_implemented |
| OPS-011 | Async/bounded/short transactions, batching, lag metrics, safe low-value shedding | §14 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 2-8 | Load/lock/queue tests | not_implemented |
| OPS-012 | Representative benchmark; never intentionally shed critical events; no premature service fleet | §14 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 8 | Capacity report/architecture audit | not_implemented |
| TEST-001 | Unit coverage includes every source-listed validation/state/rule/context/memory/retention/action area | §13 unit | [test strategy](reference/TEST_STRATEGY.md) | 1-8 | Test matrix and results | not_implemented |
| TEST-002 | Integration covers ingest transaction, restarts/reconnects/retries/failures/tools/migrations | §13 integration | [test strategy](reference/TEST_STRATEGY.md) | 1-8 | Integration suite | not_implemented |
| TEST-003 | E2E normal telemetry meets exact expected effects | §13 normal telemetry | [test strategy](reference/TEST_STRATEGY.md) | 3-5 | Scenario evidence | not_implemented |
| TEST-004 | E2E overflow meets all nine expected effects | §13 overflow | [test strategy](reference/TEST_STRATEGY.md) | 4,6,7 | Scenario evidence | not_implemented |
| TEST-005 | E2E offline/delayed/duplicate scenarios meet exact effects | §13 scenarios | [test strategy](reference/TEST_STRATEGY.md) | 2-4 | Simulator evidence | not_implemented |
| TEST-006 | Current/history/numeric/recurring questions route and qualify exactly | §13 retrieval scenarios | [test strategy](reference/TEST_STRATEGY.md) | 5-6 | LLM/tool E2E | not_implemented |
| TEST-007 | Context overflow preserves critical and bounds growth | §13 context overflow | [test strategy](reference/TEST_STRATEGY.md) | 5 | Overflow E2E | not_implemented |
| TEST-008 | Ollama outage preserves deterministic system and queues work without fabrication | §13 Ollama | [test strategy](reference/TEST_STRATEGY.md) | 4,6,8 | Outage E2E | not_implemented |
| TEST-009 | Simulator emits every listed telemetry/failure/order/command/firmware case through existing broker config | §13 simulator | [test strategy](reference/TEST_STRATEGY.md) | 2,7 | Simulator inventory/tests | not_implemented |
| TEST-010 | Each phase tests, documents, updates status, reports and stops | §12 opening; §19 | [definition of done](reference/DEFINITION_OF_DONE.md) | 0-8 | Phase handoff audit | not_implemented |
| DOC-001 | Document architecture/topology/remote broker/data flow/schema/migrations/topics/envelope/source | §15 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-3,8 | Documentation audit | not_implemented |
| DOC-002 | Document state/freshness/conflict/history/telemetry/alerts/attention/notification/outbox | §15 | Phase 3-4 docs | 3-4,8 | Documentation audit | not_implemented |
| DOC-003 | Document memory/write/contradiction/context/tools/action/retention | §15 | Phase 5-8 docs | 5-8 | Documentation audit | not_implemented |
| DOC-004 | Document local/service setup, broker TLS/auth, tests/simulator, backup/restore/troubleshooting/limits/migrations | §15 | [operations](reference/OPERATIONS_AND_DEPLOYMENT.md) | 0-8 | Runbook audit | not_implemented |
| DOC-005 | Follow all coding quality requirements | §16 | [AGENTS](../../AGENTS.md), [definition of done](reference/DEFINITION_OF_DONE.md) | 1-8 | Review/lint/type/test evidence | not_implemented |
| DOC-006 | Enforce every non-goal/guardrail | §17 | [invariants](ARCHITECTURAL_INVARIANTS.md) | 0-8 | Architecture/security audit | not_implemented |
| DOC-007 | Verify all 22 subsystem definition-of-done items | §18 | [definition of done](reference/DEFINITION_OF_DONE.md) | 8 | Evidence checklist | not_implemented |
| DOC-008 | Every phase final report and final completion additions are complete | §19 | [definition of done](reference/DEFINITION_OF_DONE.md) | 0-8 | Report/handoff audit | not_implemented |

## Coverage maintenance

When implementation begins, update status and add concrete test/file/migration evidence without deleting the source location. If one implementation change covers several rows, link the same evidence from each. If ambiguity or conflict appears, preserve both interpretations in [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md), recommend a reading, and do not mark the row verified until the owner confirms any material choice.
