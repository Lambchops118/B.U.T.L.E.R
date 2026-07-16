# TALOS Awareness Subsystem

Deterministic presence, event-processing, state, history, alerting, and memory
backend for TALOS. It runs as its **own process** (like the voice worker), on
Python 3.12, backed by PostgreSQL + TimescaleDB + pgvector. The main agent
consumes it over HTTP; the LLM never becomes the event loop, database, or
alert detector.

Architecture, deployment topology, and owner decisions live in
[`docs/awareness-memory/DISCOVERY.md`](../../docs/awareness-memory/DISCOVERY.md).
The implementation follows the phase-gated plan in
[`docs/awareness-memory/`](../../docs/awareness-memory/README.md)
(Phase 0 discovery → Phase 8 hardening).

**Status: Phase 2 (MQTT Ingestion and Event Integrity) complete.**

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

## MQTT ingestion (Phase 2)

The `serve` process connects to the **existing** Raspberry Pi Mosquitto broker
(`TALOS_AWARENESS_MQTT_HOST`, default `192.168.1.160:1883`; set
`TALOS_AWARENESS_MQTT_ENABLED=0` to run API-only). No second production broker
is deployed. The connection uses a persistent reconnect loop with bounded
exponential backoff and jitter, restores subscriptions on every (re)connect,
and reports truthful state (`connecting`/`connected`/`disconnected`/`stopped`),
reconnect count, and last error at `/health/components` under `ingestion`.
Username/password and TLS (CA + optional mutual TLS) are supported via
configuration for later broker hardening; credentials never appear in logs.

### Topics consumed and source registration

Subscriptions: `home/#` (canonical scheme for new sources) and `status/#`
(legacy device state). A message is only accepted when a **registered, enabled
source owns its topic** (`sources.allowed_topics`, exact or `+`/trailing-`#`
patterns); everything else is dead-lettered as `unauthorized_topic`. The known
deployment is seeded idempotently at startup (`registry/bootstrap.py`):

| Source | Topics | Notes |
|---|---|---|
| `fan_pico` | `status/16` | Legacy pin status; `metadata.value_inverted` (fan relay is active-low) |
| `quad_pump_pico` | `status/17-19` | Legacy pin status. Firmware also publishes `status/16` — a known collision assigned to the fan (see DISCOVERY.md); fixable only in firmware, out of scope per owner decision |
| `sim_device` | `home/sim/#` | Simulator for development and tests |

Legacy `status/{pin}` payloads (`"0"`/`"1"`) are translated by a thin adapter
into canonical `device.pin_status` events; new sources must publish the
canonical JSON envelope (`event_id`, `observed_at`, `sequence`, `boot_id`,
type-specific fields — see `schemas/events.py` and `ingestion/normalization.py`).

### Delivery and ordering guarantees (and non-guarantees)

- Subscriptions are QoS 1: **at-least-once** from the broker; database
  uniqueness (`event_id` PK and partial-unique
  `(source_id, source_boot_id, sequence)`) makes ingestion idempotent, so
  redelivery stores nothing new. We do **not** claim end-to-end exactly-once.
- Sequence evaluation classifies duplicates, out-of-order late arrivals
  (stored, flagged in `provenance.metadata.arrival`, never advancing the
  counter), sequence gaps, and boot resets.
- Retained messages are marked `provenance.retained` and freshness is judged
  by `received_at` — a retained value is evidence, not current state.
- The Pico firmware publishes QoS 0 with no acks, no NTP, no reconnect; no
  buffering or delivery guarantee is claimed for those devices (their events
  carry `clock_quality="server_received"`).
- Rejections (`unauthorized_topic`, `source_disabled`, `oversized`,
  `malformed_payload`, `source_mismatch`, `unsupported_topic`,
  `internal_error`) land in `dead_letter_events` with the bounded raw payload;
  a database outage during ingest is logged truthfully and never crashes the
  intake loop.

### Simulator

```bash
# Scenario suite against the local test broker (never the Pi by default):
.venv-awareness/bin/python -m talos.awareness.simulator \
  --host 127.0.0.1 --port 1885 --scenario suite
# Individual scenarios: normal, overflow, duplicate, delayed, out_of_order,
# sequence_gap, reboot, malformed, unauthorized, spoofed_source, oversized,
# retained, command_ack
```

The test-only Mosquitto (owner-approved) starts with
`docker compose -f docker-compose.awareness.yml --profile test up -d`.

### Troubleshooting

- `ingestion.connection.state` stuck `disconnected`: check broker address in
  health output, broker reachability, and `last_error` (never contains
  credentials).
- Events missing: check `dead_letter_events.reason` and
  `ingestion.metrics.dead_lettered` — unauthorized topics mean the source
  registry row is missing or disabled.
- Sequence flags look wrong after a device power-cycle: a changed `boot_id`
  legitimately resets sequence comparison (`boot_reset`, not `out_of_order`).

## Tests

```bash
# Unit tests (no infrastructure needed):
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_config tests.test_awareness_event_schema \
  tests.test_awareness_health tests.test_awareness_ingestion_unit

# Integration (requires the compose database — and, for ingestion, the test
# broker profile; each skips cleanly when its infrastructure is absent):
docker compose -f docker-compose.awareness.yml --profile test up -d --wait
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_migrations tests.test_awareness_ingestion_integration
```

## Requirements traceability

Kept current as phases land. Components C1–C18 refer to the implementation
prompt; ✅ = implemented, 🔶 = partially implemented, ⬜ = planned (phase noted).

| ID | Requirement | Primary components | Status |
|---|---|---|---|
| R1 | Ingest streams from sensors, microcontrollers, services, conversations, internal components | C2, C3 | 🔶 MQTT device/simulator ingestion (Phase 2); service/conversation adapters in Phases 5/6 |
| R2 | Store, aggregate, summarize, or discard data according to policy | C4, C5, C6, C15 | ⬜ Phases 3/8 |
| R3 | Distinguish current state from historical events | C5, C6 | 🔶 schemas exist (Phase 1); managers in Phase 3 |
| R4 | Maintain strong environmental awareness | C5, C12 | ⬜ Phases 3/5 |
| R5 | Track when, where, how, and from what source information entered | C1, C4 | ✅ envelope + provenance persisted per event (Phase 2) |
| R6 | Track health and state of connected systems | C4, C5, C16 | 🔶 self-health + source liveness/last-seen (Phase 2); stale/offline workers in Phase 3 |
| R7 | Detect important events independently of the LLM | C7 | ⬜ Phase 4 |
| R8 | Proactively alert the user | C8, C9 | ⬜ Phase 4 |
| R9 | Remain fully local | C17 | ✅ local Postgres/Docker; no cloud services |
| R10 | Avoid unnecessary LLM context use | C12 | ⬜ Phase 5 |
| R11 | Selectively push only relevant data to the LLM | C7, C12 | ⬜ Phase 5 |
| R12 | Allow the LLM to retrieve additional information | C13 | ⬜ Phase 5 |
| R13 | Remain functional when Ollama is unavailable | C7, C8, C16 | 🔶 nothing depends on Ollama yet by design |
| R14 | Handle duplicate, delayed, missing, out-of-order messages | C1, C3, C5 | ✅ pipeline dedup/sequence/gap/reorder/boot-reset classification (Phase 2); state-facing effects in Phase 3 |
| R15 | Persist critical downstream work reliably | C10 | 🔶 outbox table + constraints (Phase 1); workers in Phase 4 |
| R16 | Support stale, conflicting, unknown, offline state | C5 | 🔶 status vocabulary + CHECKs (Phase 1); manager in Phase 3 |
| R17 | Support validated semantic and episodic memory | C11 | ⬜ Phase 6 |
| R18 | Preserve provenance and temporal validity of memory | C11 | ⬜ Phase 6 |
| R19 | Support distributed deployment over the LAN | C2, C16, C17 | 🔶 configurable endpoints + resilient broker client with truthful connection health (Phase 2) |
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
