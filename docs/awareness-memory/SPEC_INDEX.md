# Specification Index

All links are relative to this directory. The [original specification](../ROBUST_HOME_AUTOMATION_MEMORY_IMPLEMENTATION_PROMPT.md) remains authoritative.

## Topic routing

| Topic | Canonical document |
|---|---|
| How to start and resume | [`README.md`](README.md) |
| Current work and gate | [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) |
| Architectural invariants | [`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md) |
| Requirement coverage | [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) |
| Confirmed and pending decisions | [`DECISIONS.md`](DECISIONS.md), [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md) |
| Components C1-C18 | [`reference/COMPONENT_MAP.md`](reference/COMPONENT_MAP.md) |
| Event envelope and provenance | [`reference/DATA_MODELS_AND_SCHEMAS.md`](reference/DATA_MODELS_AND_SCHEMAS.md#event-envelope-and-provenance) |
| Registries, state, history, telemetry | [`reference/DATA_MODELS_AND_SCHEMAS.md`](reference/DATA_MODELS_AND_SCHEMAS.md) |
| Alerts, attention, notification, outbox | [`reference/DATA_MODELS_AND_SCHEMAS.md`](reference/DATA_MODELS_AND_SCHEMAS.md#alerts-attention-and-notification-delivery) |
| Memory and actions | [`reference/DATA_MODELS_AND_SCHEMAS.md`](reference/DATA_MODELS_AND_SCHEMAS.md#memory) |
| Failure and recovery | [`reference/FAILURE_AND_RECOVERY.md`](reference/FAILURE_AND_RECOVERY.md) |
| Security and privacy | [`reference/SECURITY_AND_PRIVACY.md`](reference/SECURITY_AND_PRIVACY.md) |
| Test matrix and scenarios | [`reference/TEST_STRATEGY.md`](reference/TEST_STRATEGY.md) |
| Deployment, health, metrics, capacity | [`reference/OPERATIONS_AND_DEPLOYMENT.md`](reference/OPERATIONS_AND_DEPLOYMENT.md) |
| Phase and subsystem completion | [`reference/DEFINITION_OF_DONE.md`](reference/DEFINITION_OF_DONE.md) |
| MQTT ingestion | [`phases/PHASE_02_MQTT_INGESTION.md`](phases/PHASE_02_MQTT_INGESTION.md) |
| Current state and freshness | [`phases/PHASE_03_STATE_TELEMETRY.md`](phases/PHASE_03_STATE_TELEMETRY.md) |
| Rules, alerts, and notifications | [`phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md`](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) |
| Context broker and read tools | [`phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md`](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) |
| Semantic and episodic memory | [`phases/PHASE_06_LONG_TERM_MEMORY.md`](phases/PHASE_06_LONG_TERM_MEMORY.md) |
| Actions and acknowledgements | [`phases/PHASE_07_ACTIONS.md`](phases/PHASE_07_ACTIONS.md) |
| Retention and hardening | [`phases/PHASE_08_HARDENING.md`](phases/PHASE_08_HARDENING.md) |

## Minimal reading by phase

| Phase | Always read | On-demand shared references |
|---|---|---|
| 0 | `AGENTS.md`, status, [`phases/PHASE_00_DISCOVERY.md`](phases/PHASE_00_DISCOVERY.md) | component map, operations, open questions |
| 1 | `AGENTS.md`, status, [`phases/PHASE_01_FOUNDATION_DATABASE.md`](phases/PHASE_01_FOUNDATION_DATABASE.md) | invariants, schemas, operations, security, tests |
| 2 | `AGENTS.md`, status, [`phases/PHASE_02_MQTT_INGESTION.md`](phases/PHASE_02_MQTT_INGESTION.md) | invariants, schemas, failure/recovery, security, tests |
| 3 | `AGENTS.md`, status, [`phases/PHASE_03_STATE_TELEMETRY.md`](phases/PHASE_03_STATE_TELEMETRY.md) | invariants, schemas, failure/recovery, tests |
| 4 | `AGENTS.md`, status, [`phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md`](phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md) | invariants, schemas, failure/recovery, tests |
| 5 | `AGENTS.md`, status, [`phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md`](phases/PHASE_05_CONTEXT_AND_RETRIEVAL.md) | invariants, schemas, security, tests |
| 6 | `AGENTS.md`, status, [`phases/PHASE_06_LONG_TERM_MEMORY.md`](phases/PHASE_06_LONG_TERM_MEMORY.md) | invariants, schemas, failure/recovery, security, tests |
| 7 | `AGENTS.md`, status, [`phases/PHASE_07_ACTIONS.md`](phases/PHASE_07_ACTIONS.md) | invariants, schemas, failure/recovery, security, tests |
| 8 | `AGENTS.md`, status, [`phases/PHASE_08_HARDENING.md`](phases/PHASE_08_HARDENING.md) | invariants and all cross-cutting references; prior phase docs only for audit gaps |

## Original section coverage

| Source section | Canonical destination |
|---|---|
| 1 Mission; 2 Confirmed facts | [`README.md`](README.md), [`DECISIONS.md`](DECISIONS.md), invariants |
| 3 Principles | [`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md) |
| 4 Phase 0 discovery | [`phases/PHASE_00_DISCOVERY.md`](phases/PHASE_00_DISCOVERY.md) |
| 5 Technology stack; 6 topology | [`reference/OPERATIONS_AND_DEPLOYMENT.md`](reference/OPERATIONS_AND_DEPLOYMENT.md) |
| 7 R1-R22 | [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) |
| 8 Logical architecture; 9 C1-C18 | [`reference/COMPONENT_MAP.md`](reference/COMPONENT_MAP.md) and shared references |
| 10 Tables; 11 module layout | [`reference/DATA_MODELS_AND_SCHEMAS.md`](reference/DATA_MODELS_AND_SCHEMAS.md), invariants |
| 12 Phased plan | [`phases/`](phases/) |
| 13 Testing | [`reference/TEST_STRATEGY.md`](reference/TEST_STRATEGY.md) |
| 14 Performance/capacity | [`reference/OPERATIONS_AND_DEPLOYMENT.md`](reference/OPERATIONS_AND_DEPLOYMENT.md) |
| 15 Documentation; 16 quality; 17 guardrails | traceability, invariants, and phase deliverables |
| 18 Definition of done | [`reference/DEFINITION_OF_DONE.md`](reference/DEFINITION_OF_DONE.md) |
| 19 Final report | each phase's “Required final report” and definition of done |
| 20 Begin | Phase 0 launcher and mandatory owner-review gate |
