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
| ADR-011 | Accepted (owner, 2026-07-15) | Adopt the default stack with repo-fit adaptations: new `talos/awareness/` package as its own Python 3.12 process/venv (mirrors the main/voice split); PostgreSQL 17 + TimescaleDB + pgvector via Docker Compose (`timescale/timescaledb-ha:pg17`, loopback :5433); FastAPI + SQLAlchemy 2 async + Alembic + Pydantic v2 + aiomqtt; no Redis. | `DISCOVERY.md` §11. Existing repo tech evaluated first (OPS-001); no suitable DB/ORM existed. |
| ADR-012 | Accepted (owner, 2026-07-15) | LLM (Qwen via Ollama), PostgreSQL, and the awareness backend run on one machine; every cross-component link (Ollama host, DB host, broker) stays network-configurable, never localhost-assumed. | Owner decision recorded in `DISCOVERY.md` §14. |
| ADR-013 | Accepted (owner, 2026-07-15) | A test-only ephemeral Mosquitto (Docker, compose profile `test`, loopback :1885) is approved for integration tests and the simulator; production uses the existing Pi broker exclusively. | ADR-002 remains intact — this is not a second production broker. |
| ADR-014 | Accepted (owner, 2026-07-15) | Simulated hardware only for now: no firmware changes in scope; device-facing acceptance criteria run against the simulator. | Firmware risks (shared client ID, `status/16` collision, no reconnect/NTP) stay documented, not fixed. |
| ADR-015 | Accepted (owner, 2026-07-15) | Notification v1 channels: GUI banner via a new authenticated `POST /notify` on the text server, plus a structured-log adapter. TTS/speaker delivery deferred. | Phase 4 scope; deterministic and LLM-free per INV-08. |
| ADR-016 | Accepted (owner, 2026-07-16) | Waive the Phase 0 review gate; port Phase 0+1 (`88f0e64`) and partial Phase 2 (`08b510e`) from `memory_system_2_07152026` onto `memory_system_3_07152026`, and continue implementing phases in order under `docs/awareness-memory/`. | Owner selection during the 2026-07-16 session; prior work verified by the full test suite after the port. |
| ADR-017 | Accepted (owner, 2026-07-18) | The default streamed agent inference target is the local Ollama model `mb-core-v1:latest` on loopback through the existing OpenAI-compatible backend seam. Remote STT and legacy hosted LLM fallback are opt-in, not automatic. | Owner requested replacement of hosted Chat Completions for offline home automation. Live model discovery and loopback smoke tests confirmed the model and endpoint; fail-closed defaults prevent silent audio/inference egress during local outages. |

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
