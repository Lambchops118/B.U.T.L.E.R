# TALOS Awareness Subsystem

Deterministic presence, event-processing, state, history, alerting, and memory
backend for TALOS. It runs as its **own process** (like the voice worker), on
Python 3.12, backed by PostgreSQL + TimescaleDB + pgvector. The main agent
consumes it over HTTP; the LLM never becomes the event loop, database, or
alert detector.

Architecture, deployment topology, and owner decisions live in the repo-root
[`DISCOVERY.md`](../../DISCOVERY.md). The implementation follows the phased
plan in the implementation prompt (Phase 0 discovery → Phase 8 hardening).

**Status: Phase 1 (Foundation and Database) complete.**

## Setup

```bash
# 1. Database (Docker Desktop must be running).
#    timescale/timescaledb-ha:pg17 bundles TimescaleDB and pgvector.
#    Binds to 127.0.0.1:5433 (5432 is often taken by a host PostgreSQL).
docker compose -f docker-compose.awareness.yml up -d --wait

# 2. Python environment (separate venv, matching the main/voice split).
python3.12 -m venv .venv-awareness
.venv-awareness/bin/python -m pip install -r requirements-awareness-py312.txt

# 3. Configuration: set TALOS_AWARENESS_DB_PASSWORD in .env (see .env.example).

# 4. Apply migrations (never applied automatically — see policy below).
.venv-awareness/bin/python -m talos.awareness migrate

# 5. Verify, then serve.
.venv-awareness/bin/python -m talos.awareness check   # exit 0 = healthy
.venv-awareness/bin/python -m talos.awareness serve   # API on 127.0.0.1:8600
curl -s http://127.0.0.1:8600/health/components | python3 -m json.tool
```

## Migration policy

- Migrations live in `talos/awareness/db/migrations/versions/` and are applied
  **explicitly** via `python -m talos.awareness migrate` (or
  `alembic -c talos/awareness/alembic.ini upgrade head`) — never implicitly at
  process startup.
- The health endpoint and `check` command report when the database revision
  does not match the repository head, with the exact command to fix it.
- `models.py` and the migrations must stay in lockstep:
  `tests/test_awareness_migrations.py` migrates a scratch database and fails
  if an autogenerate diff between the live schema and the models is non-empty.

## Configuration (Phase 1)

All variables are read from the process environment or the repo `.env`.
Secrets never appear in logs or health output.

| Variable | Default | Purpose |
|---|---|---|
| `TALOS_AWARENESS_DB_PASSWORD` | *(required)* | Postgres password (also used by docker compose) |
| `TALOS_AWARENESS_DB_HOST` / `_DB_PORT` | `127.0.0.1` / `5433` | Postgres address |
| `TALOS_AWARENESS_DB_NAME` / `_DB_USER` | `talos_awareness` / `talos` | Database / role |
| `TALOS_AWARENESS_API_HOST` / `_API_PORT` | `127.0.0.1` / `8600` | Internal API bind |
| `TALOS_AWARENESS_LOG_LEVEL` | `INFO` | Structured JSON log level |
| `TALOS_AWARENESS_DATA_DIRECTORY` | `db/awareness` | Local artifact store root (used from Phase 8) |
| `TALOS_AWARENESS_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama (same machine per owner decision, still configurable) |
| `TALOS_AWARENESS_CHAT_MODEL` / `_EMBEDDING_MODEL` | *(unset)* | Model names (used from Phase 5/6) |
| `TALOS_AWARENESS_MQTT_HOST` / `_MQTT_PORT` | falls back to legacy `MQTT_BROKER` / `MQTT_PORT`, then `192.168.1.160:1883` | Existing Raspberry Pi Mosquitto broker (used from Phase 2) |
| `TALOS_AWARENESS_MQTT_TLS` / `_MQTT_USERNAME` / `_MQTT_PASSWORD` / `_MQTT_CLIENT_ID` / … | see `config.py` | Broker security/session options |
| `TALOS_AWARENESS_MAX_EVENT_PAYLOAD_BYTES` | `65536` | Ingestion payload bound |

## Tests

```bash
# Unit tests (no infrastructure needed):
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_config tests.test_awareness_event_schema tests.test_awareness_health

# Integration (requires the compose database; skips cleanly when absent):
.venv-awareness/bin/python -m unittest tests.test_awareness_migrations
```

## Requirements traceability

Kept current as phases land. Components C1–C18 refer to the implementation
prompt; ✅ = implemented, 🔶 = partially implemented, ⬜ = planned (phase noted).

| ID | Requirement | Primary components | Status |
|---|---|---|---|
| R1 | Ingest streams from sensors, microcontrollers, services, conversations, internal components | C2, C3 | ⬜ Phase 2 |
| R2 | Store, aggregate, summarize, or discard data according to policy | C4, C5, C6, C15 | ⬜ Phases 3/8 |
| R3 | Distinguish current state from historical events | C5, C6 | 🔶 schemas exist (Phase 1); managers in Phase 3 |
| R4 | Maintain strong environmental awareness | C5, C12 | ⬜ Phases 3/5 |
| R5 | Track when, where, how, and from what source information entered | C1, C4 | 🔶 envelope + provenance schema (Phase 1) |
| R6 | Track health and state of connected systems | C4, C5, C16 | 🔶 self-health only (Phase 1) |
| R7 | Detect important events independently of the LLM | C7 | ⬜ Phase 4 |
| R8 | Proactively alert the user | C8, C9 | ⬜ Phase 4 |
| R9 | Remain fully local | C17 | ✅ local Postgres/Docker; no cloud services |
| R10 | Avoid unnecessary LLM context use | C12 | ⬜ Phase 5 |
| R11 | Selectively push only relevant data to the LLM | C7, C12 | ⬜ Phase 5 |
| R12 | Allow the LLM to retrieve additional information | C13 | ⬜ Phase 5 |
| R13 | Remain functional when Ollama is unavailable | C7, C8, C16 | 🔶 nothing depends on Ollama yet by design |
| R14 | Handle duplicate, delayed, missing, out-of-order messages | C1, C3, C5 | 🔶 DB uniqueness for (source, boot, sequence) (Phase 1); pipeline in Phase 2 |
| R15 | Persist critical downstream work reliably | C10 | 🔶 outbox table + constraints (Phase 1); workers in Phase 4 |
| R16 | Support stale, conflicting, unknown, offline state | C5 | 🔶 status vocabulary + CHECKs (Phase 1); manager in Phase 3 |
| R17 | Support validated semantic and episodic memory | C11 | ⬜ Phase 6 |
| R18 | Preserve provenance and temporal validity of memory | C11 | ⬜ Phase 6 |
| R19 | Support distributed deployment over the LAN | C2, C16, C17 | 🔶 all endpoints configurable (Phase 1) |
| R20 | Integrate with current code without unnecessary rewrites | Phase 0, C18 | ✅ additive package + separate process/venv |
| R21 | Validate physical actions and record acknowledgements | C14 | ⬜ Phase 7 |
| R22 | Apply configurable retention and safe deletion | C15 | 🔶 `alert_events.event_id` is `ON DELETE RESTRICT`, protecting alert evidence at the DB level; retention workers in Phase 8 |

## Phase 1 schema

Tables: `locations`, `entities`, `entity_relationships`, `sources`, `events`,
`dead_letter_events`, `current_state`, `alerts`, `alert_events`,
`attention_items`, `outbox`, `schema_registry`.

Key constraints (see `db/models.py`):

- `events`: unique `(source_id, source_boot_id, sequence)` (partial) for
  duplicate detection with reboot-safe sequence resets; indexed by received /
  observed time, source, entity, type, severity, correlation, causation.
- `current_state`: primary key `(entity_id, property_name)` — exactly one
  active row per property; status vocabulary enforced by CHECK.
- `alerts`: partial-unique `deduplication_key` while `open`/`acknowledged` —
  one live incident per key; occurrence tracking columns.
- `alert_events.event_id` → `events` with `ON DELETE RESTRICT` — unresolved
  alert evidence cannot be deleted out from under an alert.
- `outbox`: unique `idempotency_key`, `(status, available_at)` claim index.
- All timestamps are `timestamptz` (UTC); flexible payloads are JSONB.
