# Implementation Status

This file reports implementation state, not documentation availability.

| Field | Current value |
|---|---|
| Current phase | Phase 6 — Long-Term Memory — **complete** |
| Phase state | Phases 0-6 complete on `memory_system_3_07152026`; sequential continuation authorized for the 2026-07-16 session (ADR-016) |
| Last completed phase | Phase 6 (2026-07-16) |
| Current bounded task | Phase 7 — Actions (next; authorized for this session) |
| Completed items | Phase 0 `DISCOVERY.md` (owner-reviewed; gate waived); Phase 1 foundation (`talos/awareness/` process, typed config, Timescale/pgvector compose DB, Alembic rev `a526ee7fcfdb` with 12 tables, `/health`, structured logging); Phase 2 ingestion (aiomqtt reconnect/restore, canonical validation, topic ownership, dedup/sequence/boot classification, dead-letter, legacy `status/#` adapter, registry seed, simulator, test-profile Mosquitto); Phase 3 state/telemetry (Alembic rev `dbfc40d327c0`: measurements hypertable + 1m/1h/1d continuous aggregates + state_transitions + source_health_history; state authority/conflict/deadband manager, freshness worker, bounded `/state` `/events` `/telemetry` reads; verified live: broker → ingest → qualified API reads); Phase 4 rules/alerts/notifications (Alembic rev `7bf1cd5508d3`: notification_deliveries; versioned TOML rule policy with hard-rule precedence, alert dedup lifecycle + evidence, attention cooldown + quiet hours, transactional notification outbox, SKIP LOCKED worker with backoff/stale-lock/dead-letter/manual retry, gui `POST /notify` + log adapters with fallback, `/alerts` API; verified live: simulator overflow → critical alert → confirmed log delivery); Phase 5 context/retrieval (SituationBroker with hard budget/priority/audit, `/situation` `/provenance` `/capabilities` endpoints, main-agent `awareness_client` feeding both LLM lanes via the router snapshot seam with truthful fallback, MCP provider with seven bounded read tools registered on the aggregate server; no migration needed; verified live: budget-truncated situation with critical alert first); Phase 6 long-term memory (Alembic rev `abc5ba9b578d`: memories + memory_embeddings (pgvector hnsw, 768-dim) + memory_provenance + memory_relationships; deterministic + validated candidate write paths, supersession/conflict semantics, episodic memory on alert resolution via outbox, Ollama embedding handler with queue-on-outage, hybrid search with component scores, `/memory/*` API, `search_memory` MCP tool, `remember_memory_fact` mirrored to awareness; verified live: write + full-text search with truthful vector degradation) |
| Active work | Phase 7 per `phases/PHASE_07_ACTIONS.md` |
| Blocked items | None. OQ-013 (backup policy) due before Phase 8; broker-side ACL verification requires LAN access |
| Decisions made | ADR-001..016 in `DECISIONS.md` (011-016 owner-approved 2026-07-15/16) |
| Assumptions confirmed | See `DISCOVERY.md` §12 and §15 addendum; open-question resolution table in `OPEN_QUESTIONS.md` |
| Open questions | OQ-013, OQ-B (broker config), OQ-C (firmware scope) in `OPEN_QUESTIONS.md` |
| Tests last run | 2026-07-16: awareness venv — 99 tests (adds `tests.test_awareness_memory_integration`), all pass (integration requires `docker compose -f docker-compose.awareness.yml --profile test up -d --wait`); main venv — 14 tests (`tests.test_text_server_{phone_events,notify}`, `tests.test_awareness_client_and_provider`), all pass |
| Known failures | None |
| Files recently modified | `talos/awareness/**`, `tests/test_awareness_*`, `docker-compose.awareness.yml`, `docker/mosquitto-test.conf`, `.env.example`, `requirements-awareness-py312.txt`, `docs/awareness-memory/**` |
| Next permitted task | Phase 7 — Actions (`phases/PHASE_07_ACTIONS.md`) |
| Required reading | Root `AGENTS.md`, this file, `DISCOVERY.md`, `phases/PHASE_07_ACTIONS.md`, and its listed references |
| Explicit stop condition | Stop at each phase boundary with tests/status/handoff/report; the owner authorized sequential continuation within the 2026-07-16 session, and later sessions require fresh authorization per phase |

Do not infer implementation progress from the presence of specification or launcher files. When work begins, replace summary values with concrete evidence, commands, results, and file paths. Session handoffs live in dated files derived from `SESSION_HANDOFF_TEMPLATE.md` (latest: `SESSION_HANDOFF_2026-07-16.md`).
