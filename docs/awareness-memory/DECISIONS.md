# Architecture Decision Log

Only source-confirmed decisions are recorded as accepted. Repository-dependent selections remain pending Phase 0 and must not be inferred from defaults.

| ID | Status | Decision | Basis / consequence |
|---|---|---|---|
| ADR-001 | Accepted | Operate local-first; require no cloud database, vector store, embeddings, or inference. | Original sections 1-3, C17. Existing explicitly configured external integrations may handle only their intended data. |
| ADR-002 | Accepted | Reuse the existing Raspberry Pi Mosquitto broker unless discovery proves another broker necessary and the owner approves it. | Original sections 1, 5.2, C2. Do not deploy a second broker by default. |
| ADR-003 | Accepted | The central local database, not MQTT retained messages, is authoritative. | P6. MQTT is transport; retained values require provenance and freshness checks. |
| ADR-004 | Accepted | Current state, immutable event history, time-series telemetry, working state, and long-term memory are distinct models. | P1, C5, C6, C11. Exact state/history queries do not default to semantic search. |
| ADR-005 | Accepted | Deterministic code owns ingestion, state, safety rules, alerts, fallback notifications, retries, retention, and action validation. | P2, P8. Ollama outages must not disable core safety-related behavior. |
| ADR-006 | Accepted | Important work uses transactions, a durable outbox, at-least-once execution, idempotent consumers, and uniqueness constraints. | P5, C3, C10. Do not claim end-to-end exactly once. |
| ADR-007 | Accepted | Physical interlocks remain in firmware/hardware; backend actions are validated and acknowledged. | Confirmed facts, C14. Silence is not success. |
| ADR-008 | Accepted | Implementation is phase-gated; every phase ends with tests, docs, status/handoff, report, and a stop. | Original sections 4, 12, 19. Phase 0 owner review is mandatory unless explicitly waived. |
| ADR-009 | Accepted default | Integrate additively and prefer a modular monolith unless discovery shows an established suitable architecture. | P9-P10. This is a default, not authorization to rewrite existing systems. |
| ADR-010 | Accepted default | Existing suitable repository technology takes precedence; otherwise use the documented Python/PostgreSQL/TimescaleDB/pgvector/FastAPI/SQLAlchemy/Alembic/Pydantic/Docker defaults where applicable. | Original section 5. Substitution must preserve required properties and be documented. |

## New decision template

```text
ID:
Date:
Status: proposed | accepted | superseded | rejected
Owner/participants:
Phase:
Context and evidence:
Decision:
Alternatives considered:
Consequences:
Requirements affected:
Supersedes / superseded by:
```
