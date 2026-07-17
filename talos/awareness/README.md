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

**Status: Phase 8 (Retention, Security, and Hardening) complete — all phases implemented.**

## Setup

```bash
# 1. Database (Docker Desktop must be running).
#    timescale/timescaledb-ha:pg17 bundles TimescaleDB and pgvector.
#    Binds to 127.0.0.1:5433 (5432 is often taken by a host PostgreSQL).
docker compose -f docker-compose.awareness.yml up -d --wait

# 2. Python environment (separate venv, matching the main/voice split).
python3.12 -m venv .venv-awareness
.venv-awareness/bin/python -m pip install -r requirements-awareness-py312.txt

# 3. Configuration: set TALOS_AWARENESS_DB_PASSWORD in .env. To enable
#    physical action mutations, also set a shared TALOS_AWARENESS_API_TOKEN
#    (minimum 16 characters) for the backend and main-agent client.

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
| `TALOS_AWARENESS_API_TOKEN` | *(unset; action mutations disabled)* | Shared bearer token required by action request/confirm/cancel routes and sent by the main-agent client |
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

## Current state, freshness, and telemetry (Phase 3)

### Current state authority

`current_state` holds exactly one durable row per `(entity_id, property_name)`
(PK-enforced), written inside the ingestion transaction and always separate
from immutable history. Event kinds map to effects deterministically
(`state/classification.py`): `*.telemetry.{m}` → measurement + state property;
`*.state.reported` → one property per payload key; `device.pin_status.reported`
→ boolean `pin_{n}` (inversion respected); `*.heartbeat` → source liveness
only. The registry is the identity authority: state attaches to the registered
entity (topic-claimed, else the source's), or stays history-only.

Update semantics (`state/manager.py`): comparison time is `observed_at` for
trusted clocks (`device_synced`/`gateway_stamped`), else `received_at`; a
strictly newer equal-or-higher-authority value replaces; a delayed/out-of-order
message never moves state backwards (history keeps it); equal-time or
newer-but-weaker disagreement marks the row `conflicting` with the contender
preserved in metadata — no invented certainty. Source authority comes from
registry `metadata.authority_rank` (default 0). Numeric jitter within
`metadata.deadbands.{property}` updates the value without recording a
transition; movement beyond the deadband from the last anchor records one
(hysteresis). Meaningful changes land in `state_transitions`
(`initial|update|recovered|conflict|stale|offline`).

### Freshness and source health

A deterministic worker (`state/freshness.py`, every
`TALOS_AWARENESS_FRESHNESS_INTERVAL_SECONDS`) marks state rows `stale` and
silent sources `offline` (plus their state rows) using per-source registry
deadlines (`stale_after_seconds`/`offline_after_seconds`) or the configured
defaults, anchored on server receipt time — untrusted device clocks never
extend freshness. Every change is recorded once (`state_transitions`,
`source_health_history`); re-runs and restarts are idempotent. Sources that
never reported stay `unknown`. A first accepted message flips the source back
to `healthy` and the state to `current` (`recovered`), both with history. The
alert hook is a Phase 4 interface and currently does nothing. Reads qualify
overdue rows as `stale` even before the worker's next pass, and every read
carries `as_of`, age, source, and confidence.

### Telemetry and bounded queries

Numeric telemetry lands in the `measurements` hypertable (7-day chunks) in
the same transaction as its event; minute/hour/day continuous aggregates
(`measurements_1m/_1h/_1d`: min/max/avg/count/stddev per
entity/measurement/unit) refresh on background policies. Raw telemetry is
never embedded. Read endpoints (loopback API):

```text
GET /state/{entity_id}
GET /events?start=&end=[&entity_id=&source_id=&event_type=&severity=&limit=]
GET /telemetry/{entity_id}/{measurement}?start=&end=[&aggregation=1m|1h|1d&max_points=]
```

Every history query requires a timezone-aware range and respects
`TALOS_AWARENESS_MAX_QUERY_RANGE_DAYS` / `_MAX_QUERY_POINTS` /
`_MAX_EVENT_PAGE_SIZE`; unbounded requests get 422, never a raw dump. Results
report `truncated` when the limit cut them off. Volumes are trivial today
(see DISCOVERY.md §9); telemetry writes one row per event with no separate
batching stage — revisit against measured lag if the fleet grows.

## Rules, alerts, and notifications (Phase 4)

### Rule policy

Deterministic rules live in versioned TOML
([`rules/rules.toml`](rules/rules.toml), overridable via
`TALOS_AWARENESS_RULES_PATH`) validated by strict typed models
(`rules/policy.py`); the loaded version is registered in `schema_registry`
and stamped on every derived alert (`metadata.rule_id`/`policy_version`).
`hard` rules evaluate before `classification` rules and nothing overrides
them. Matching supports exact/prefix-glob event types, minimum severity, and
typed payload conditions (`eq/ne/gt/ge/lt/le/exists`). Templates
(`{entity_id}`, `{payload.zone}`) are plain token substitution — a missing
field renders `?` and can never break a critical notification or execute
anything. Rules run inside the ingestion transaction after state effects; the
optional async LLM classifier permitted by the spec is deliberately **not
implemented** (no model call exists anywhere in the alert path).

### Alert and attention lifecycle

One persistent condition = one active incident: the partial-unique
`deduplication_key` (while `open`/`acknowledged`) makes repeats bump
`occurrence_count`/`last_seen_at` and append `alert_events` evidence instead
of opening a duplicate. Acknowledgement (`POST /alerts/{id}/acknowledge`)
does not erase the condition — repeats keep updating the same incident;
resolution is either operator (`POST /alerts/{id}/resolve`) or deterministic
(a resolve rule with evidence, e.g. `overflow=false`). Attention items are
interruption timing, separate from incident state: priority,
interruptibility (`immediate` for overflow, `next_interaction` for
noncritical), `cooldown_key`+`cooldown_seconds` suppress interruption spam
while the incident still updates, and quiet hours
(`TALOS_AWARENESS_QUIET_HOURS`) defer only noncritical availability — a
critical alert is never deferred or silently dropped. Worker-detected source
silence opens a `source_offline` incident at policy severity (never
automatically critical) and auto-resolves when the source reports again.

### Notification delivery and the outbox

Within the event transaction the rule engine writes the alert, attention
item, and one unique notification outbox row (`notification:{attention_id}`)
— a crash after commit cannot lose the notification, and duplicate event
delivery cannot queue a second one. The outbox worker claims bounded batches
with `FOR UPDATE SKIP LOCKED`, renders **deterministic** wording from the
alert row (severity/title/description/occurrence/first-seen; no Ollama
anywhere), tries the preferred channel then the others as fallback, persists
every attempt in `notification_deliveries` (`delivered` only on adapter
confirmation), retries with bounded exponential backoff, releases stale
locks, dead-letters after `TALOS_AWARENESS_OUTBOX_MAX_ATTEMPTS`, and supports
manual retry (`POST /outbox/{id}/retry`). Semantics are at-least-once with
idempotent handlers — never exactly-once.

Channels (ADR-015, existing only):

| Channel | Transport | "Confirmed" means | Limitation |
|---|---|---|---|
| `gui` | authenticated `POST /notify` on the text server (:8420) → router `ui` lane → pygame GUI | text server accepted and enqueued the banner | not proof a human saw the screen |
| `log` | structured awareness log | log record emitted | passive; always-available fallback |

Delivery evidence per alert: `GET /alerts/{id}/deliveries`. Backlog and
oldest-pending age appear under `outbox_worker` in `/health/components`.

## Situation, context, and read tools (Phase 5)

### Situation snapshot

`GET /situation[?budget_tokens=&entity_id=]` renders a compact deterministic
snapshot under a hard token budget — never a raw dump or generated prose.
Fixed priority (CTX-002): active **critical alerts (always included, never
truncated)** → other active alerts → pending attention → qualified current
state → recent meaningful transitions (window
`TALOS_AWARENESS_SITUATION_TRANSITION_WINDOW_MINUTES`) → unhealthy sources.
Token accounting is a conservative estimate (`ceil(chars/3.5)`; no tokenizer
dependency exists in this venv — overestimating is the safe direction). The
response carries `used_tokens`, `truncated`, and a complete per-item `audit`
(id, priority, tokens, included, reason). Every line carries temporal status,
observation/receipt times, age, confidence, and source; overdue rows render
`stale` even before the freshness worker's next pass. Known limitation
(documented in the response): no user-location or conversation-relevance
signal exists in the repo yet, so relevance is alert/attention/freshness
priority only.

### Main-agent integration

The router now feeds both LLM lanes through
`talos.services.awareness_client.snapshot_with_fallback(...)`: the rendered
situation when the backend answers within
`TALOS_AWARENESS_CLIENT_TIMEOUT` (cached 5 s), else the legacy in-memory
snapshot — a backend outage degrades truthfully and never blocks commands.
Disable with `TALOS_AWARENESS_SITUATION_ENABLED=0`.

### Read tools (MCP)

`talos/mcp_servers/providers/awareness.py` registers seven narrow read tools
on the aggregate server (both LLM paths see them through the existing tool
merge): `get_current_state`, `get_recent_events`, `get_sensor_history`,
`get_active_alerts`, `get_system_health`, `get_event_provenance`,
`get_awareness_capabilities`. All are thin bounded HTTP calls to the
awareness API — no SQL/file/MQTT access, inputs clamped (≤100 events, ≤500
points, ≤31-day windows), results carry freshness/confidence/source and
`truncated` flags, and backend failure returns a clear `{"error": ...}`
string instead of fabricated data. Routing lives in the docstrings: current
facts → state, "when did X last happen" → events, numeric periods →
aggregates, trust/why → provenance/health — exact questions never route to
vector search. `search_memory` is reported `not_yet_implemented` by
`GET /capabilities` until Phase 6.

## Long-term memory (Phase 6)

### Types, write paths, and evidence

`memories` holds **semantic** (facts/preferences with validity) and
**episodic** (meaningful incidents) records; working state stays in
`current_state`/sessions, telemetry stays structured, and conversations/
messages remain in the main agent's SQLite store — they are evidence
references (`memory_provenance`), never memory rows. Two write paths:

1. **Deterministic** (`POST /memory/deterministic`) for unambiguous,
   policy-permitted facts — the main agent's `remember_memory_fact` tool now
   mirrors explicit user facts here (SQLite stays authoritative for prompt
   assembly; the response reports `awareness_memory_synced` truthfully).
2. **Candidates** (`POST /memory/candidates`): a model may only propose a
   strict typed candidate (statement, scope, structured content, evidence,
   model/prompt version). The manager validates every evidence reference
   (dangling event/alert ids are rejected), records accept/reject decisions
   on the row itself, dedupes idempotently by content hash, and assigns
   confidence (explicit user confirmation ≫ weak inference).

Changed facts are never overwritten: the old row closes validity, becomes
`superseded`, and the replacement links it (`supersedes`). Inconclusive
conflict keeps both rows active with a `conflicts_with` relation. A resolved
alert automatically queues an episodic memory linking the alert and its
evidence events. Explicit deletion is soft and audited (reason + timestamp in
metadata). Access counters never touch validity.

### Embeddings and hybrid search

Embedding is asynchronous outbox work (`work_type=embedding`) against local
Ollama (`TALOS_AWARENESS_EMBEDDING_MODEL`, default `nomic-embed-text`,
768-dim — the pgvector column dimension is fixed; changing model families
requires a migration and re-embedding). Ollama outage queues bounded retries
and **never blocks acceptance or full-text search**. Only memory statements
are embedded — raw telemetry, heartbeats, transcripts, and binaries never
enter the corpus because they never become memories. `GET /memory/search`
combines full-text (`tsvector`), vector cosine (when embeddings and a query
embedding exist; `vector_used` reports which), recency, importance, and
confidence with **component scores exposed**; results exclude rejected/
superseded/expired/deleted/out-of-sensitivity rows (`max_sensitivity`,
default `personal`). Exact current/numeric/temporal questions still route to
the structured tools, not memory search. The `search_memory` MCP tool exposes
this to both LLM lanes.

## Actions (Phase 7)

### Registry, validation, and confirmation

Every dispatchable action is registered in versioned TOML
([`actions/actions.toml`](actions/actions.toml)) with strict typed
parameters, permission/actor allowlist, confirmation, safety/allowed-state
policy, cooldown, timeout, command topic, idempotency behavior, acknowledgement
source/semantics, and rollback (`none` for all deployed actions). **MQTT
payloads are generated only from these definitions, never from model
content**, wildcard command topics are rejected, and the model has no raw-MQTT
path. Supported: `water_plants` (pot_pin 17/19), `toggle_fan` (state 0/1), and
the simulator's `sim_command` (which requires confirmation and exercises that
flow). Validation order before any dispatch: registered action → parameter
schema/bounds → actor permission → configured safety/allowed prior state →
cooldown → confirmation. Rejections are durable. Confirmation binds a
one-time returned token, actor, and exact request; only a SHA-256 digest is
stored, the token expires, and a materially different request needs a new
one. Action mutations fail closed unless `TALOS_AWARENESS_API_TOKEN` is
configured and supplied as a bearer token.

### Lifecycle, acknowledgement, and truthful failure

Every request and transition is durable (`action_requests` +
`action_transitions`, migrations `4d268f4eae02` + `e7c11f9a4b2d`): requested
→ validated → awaiting_confirmation (when required) → approved → dispatched
→ acknowledged → completed, with rejected/failed/timed_out/cancelled terminal
states. Unsupported/schema/permission/safety/cooldown rejections, failed
confirmation/cancel attempts, duplicate requests, late acks, and state
mismatches remain queryable audit transitions. A caller-supplied idempotency
key binds actor/action/normalized parameters/correlation; an exact duplicate
returns the existing lifecycle, while changed content is rejected. Command
IDs are database-unique.

Approval queues durable `action_dispatch` work before network I/O. Legacy
Picos cannot dedupe command IDs, so they use an at-most-once policy: the
attempt is recorded before publish and an uncertain/failed publish is failed
without an automatic physical retry. Canonical simulator commands carry the
same command/idempotency key on bounded retry, so broker failure leaves the
request approved until a publish succeeds. Both paths schedule
`action_timeout` after a recorded dispatch. Broker credentials/TLS settings
are shared with ingestion.

Completion is action-specific and **silence is never success**. The pump
firmware publishes `status/{pin}=0` only after its fixed eight-second cycle;
that final off evidence completes watering. Fan status is normalized for its
active-low relay. Mismatching state evidence acknowledges then fails the
action instead of being called successful. The simulator's source-bound
`sim.command_ack` explicitly means execution result; negative/malformed/
wrong-source acknowledgements cannot complete a command. Late/duplicate acks
are audited and cannot revive a terminal command. Immediate electrical and
mechanical interlocks remain in firmware (INV-09); no rollbacks are registered
because the deployed actions have no safe inverse.

API: `POST /actions/request`, `POST /actions/{id}/confirm`,
`POST /actions/{id}/cancel` (pre-dispatch only), `GET /actions[/{id}]` with
the full transition audit. MCP tools: `request_device_action`,
`get_action_status`. The existing MCP names `water_plants` and `toggle_fan`
are preserved but now call the action API; their result reports the durable
request status and never claims physical success before evidence. Physical
hardware was not exercised (ADR-014): completion/failure semantics are proven
against the simulator and the repository-confirmed legacy firmware/status
shape. Known physical limitations remain: ambiguous legacy `status/16`, no
device-side command IDs/acks/reconnect, and no backend-checkable pre-command
safety sensor for the two deployed Picos.

## Retention, backups, and operations (Phase 8)

### Retention

`python -m talos.awareness retention` prints the exact dry-run plan (per
policy: cutoff, eligible count, protections); `--execute` deletes in bounded
batches (`TALOS_AWARENESS_RETENTION_BATCH_SIZE`), each batch its own
transaction — crash-safe, resumable, idempotent. Policies (days; 0 disables):
raw measurements (30 — the 1m/1h/1d continuous aggregates are refreshed
through the cutoff BEFORE any raw deletion), heartbeat events (7; alert
evidence excluded and DB-RESTRICT-protected), dead letters (30), completed
outbox (7), resolved/expired alerts (90; open/acknowledged never eligible),
state transitions (90), source health history (90), rejected/deleted
memories (30; active and superseded memories — the supersession history —
are never deleted by retention).

### Consolidation

`python -m talos.awareness consolidate`: incident scopes with ≥3 active
episodes in the window get one summary episode linked `derived_from` to every
source (sources preserved, embedding queued, idempotent by content hash);
model-inferred memories unaccessed past the decay window lose confidence by
the configured factor (floor 0.05) — user-confirmed memories never decay.

### Artifacts

`talos/awareness/artifacts.py`: bytes under `{data_directory}/artifacts/`
with generated `{uuid}/{sanitized-name}` paths (no caller-chosen paths;
resolved paths verified inside the root), SHA-256/MIME/size/provenance in
the `artifacts` table, checksum verified on load.

### Backups (owner decision 2026-07-16: local, nightly, 14-day retention)

`python -m talos.awareness backup [--verify]` — pg_dump (custom format) runs
inside the `talos-awareness-db` container (client/server versions always
match), plus a non-secret config snapshot, plus pruning past
`TALOS_AWARENESS_BACKUP_RETENTION_DAYS`. `--verify` restores into a scratch
database using the TimescaleDB pre/post-restore protocol and compares
schema/table counts. **Restore was tested live 2026-07-16: 27/27 tables,
all events intact, no warnings.** Restore procedure for a real recovery:
create an empty DB, `CREATE EXTENSION timescaledb`,
`SELECT timescaledb_pre_restore()`, `docker exec -i talos-awareness-db
pg_restore -U talos -d <db> --no-owner < dump`, `SELECT
timescaledb_post_restore()`, then `python -m talos.awareness check`.
Schedule via cron (see `.env.example`); the repo deliberately installs no
process manager. Last-backup age is visible at `GET /metrics`.

### Security posture

- API binds loopback; **physical action** mutations are fail-closed (503
  until `TALOS_AWARENESS_API_TOKEN` is set, then bearer-authenticated).
  Non-physical mutations (memory writes/deletion, alert lifecycle, outbox
  retry) require the same bearer token when configured and are
  loopback-trusted otherwise — setting one token secures every mutation.
  The main agent sends it automatically when `TALOS_AWARENESS_API_TOKEN`
  is in its environment.
- Secrets are `SecretStr`/env-only; the settings summary and logs are
  sanitized; backups contain no secrets (config snapshot is the sanitized
  summary).
- Broker hardening (auth/ACL/TLS on the Pi) is an owner-executed plan:
  [`docs/awareness-memory/BROKER_HARDENING_PLAN.md`](../../docs/awareness-memory/BROKER_HARDENING_PLAN.md).

### Capacity (measured 2026-07-16, M3 MacBook, dockerized PG17)

`python -m talos.awareness.benchmark --events 2000`: **118 events/s**
sustained through the full pipeline (validate → dedup → persist → state/
telemetry/rules in one transaction), p50 7.5 ms, p95 14.8 ms, max 23.8 ms,
zero drops. Real deployed traffic is a few messages per day; the design
target of 10-100 msg/s bursts has >1x headroom at p95 under 15 ms. No
load-shedding is implemented because no queue ever approached its bound at
this rate — if the fleet grows, shed only configured low-value telemetry and
never critical events (documented limitation, revisit with measured lag).

### Observability

`GET /metrics`: ingestion counters, freshness/outbox worker state and
backlog/oldest-age, data-directory disk usage, last backup. Component health
(DB/extensions/migration revision, MQTT, workers, rules) stays at
`GET /health/components` with truthful degradation.

## Tests

```bash
# Unit tests (no infrastructure needed):
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_config tests.test_awareness_event_schema \
  tests.test_awareness_health tests.test_awareness_ingestion_unit \
  tests.test_awareness_state_unit tests.test_awareness_rules_unit \
  tests.test_awareness_context_unit tests.test_awareness_actions_unit

# Integration (requires the compose database — and, for ingestion, the test
# broker profile; each skips cleanly when its infrastructure is absent):
docker compose -f docker-compose.awareness.yml --profile test up -d --wait
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_migrations tests.test_awareness_ingestion_integration \
  tests.test_awareness_state_integration tests.test_awareness_alerts_integration \
  tests.test_awareness_context_integration tests.test_awareness_memory_integration \
  tests.test_awareness_actions_integration tests.test_awareness_hardening_integration \
  tests.test_awareness_actions_integration

# Main-venv integration (text-server /notify, awareness client + MCP tools):
.venv-main/bin/python -m unittest tests.test_text_server_notify \
  tests.test_awareness_client_and_provider tests.test_home_automation_actions
```

## Requirements traceability

Kept current as phases land. Components C1–C18 refer to the implementation
prompt; ✅ = implemented, 🔶 = partially implemented, ⬜ = planned (phase noted).

| ID | Requirement | Primary components | Status |
|---|---|---|---|
| R1 | Ingest streams from sensors, microcontrollers, services, conversations, internal components | C2, C3 | 🔶 MQTT device/simulator ingestion (Phase 2); service/conversation adapters in Phases 5/6 |
| R2 | Store, aggregate, summarize, or discard data according to policy | C4, C5, C6, C15 | ✅ store/aggregate/summarize/discard per policy: aggregates + consolidation + retention (Phases 3/6/8) |
| R3 | Distinguish current state from historical events | C5, C6 | ✅ durable current_state separate from immutable events/measurements (Phase 3) |
| R4 | Maintain strong environmental awareness | C5, C12 | ✅ qualified state + deterministic situation snapshot with budget/audit (Phase 5) |
| R5 | Track when, where, how, and from what source information entered | C1, C4 | ✅ envelope + provenance persisted per event (Phase 2) |
| R6 | Track health and state of connected systems | C4, C5, C16 | ✅ source health lifecycle + history + stale/offline workers (Phase 3) |
| R7 | Detect important events independently of the LLM | C7 | ✅ deterministic TOML rules inside the ingestion transaction; no LLM in the path (Phase 4) |
| R8 | Proactively alert the user | C8, C9 | ✅ alert→attention→outbox→adapter delivery with persisted attempts (Phase 4) |
| R9 | Remain fully local | C17 | ✅ fully local: Docker PG + local Ollama seam + local backups; no cloud services (Phase 8 audit) |
| R10 | Avoid unnecessary LLM context use | C12 | ✅ hard token budget, priority truncation, audit (Phase 5) |
| R11 | Selectively push only relevant data to the LLM | C7, C12 | ✅ relevance-selected situation; critical alerts always survive (Phase 5) |
| R12 | Allow the LLM to retrieve additional information | C13 | ✅ read tools + search_memory (Phases 5-6) |
| R13 | Remain functional when Ollama is unavailable | C7, C8, C16 | ✅ deterministic paths Ollama-free; embedding outage queues + full-text continues (Phases 4+6) |
| R14 | Handle duplicate, delayed, missing, out-of-order messages | C1, C3, C5 | ✅ pipeline dedup/sequence/gap/reorder/boot-reset classification (Phase 2); state-facing effects in Phase 3 |
| R15 | Persist critical downstream work reliably | C10 | ✅ transactional outbox + claiming worker with backoff/dead-letter/manual retry (Phase 4) |
| R16 | Support stale, conflicting, unknown, offline state | C5 | ✅ stale/unknown/conflicting/offline statuses with deterministic transitions (Phase 3) |
| R17 | Support validated semantic and episodic memory | C11 | ✅ validated semantic/episodic memory with candidate path (Phase 6) |
| R18 | Preserve provenance and temporal validity of memory | C11 | ✅ provenance links, validity, supersession chains (Phase 6) |
| R19 | Support distributed deployment over the LAN | C2, C16, C17 | 🔶 configurable endpoints + resilient broker client with truthful connection health (Phase 2) |
| R20 | Integrate with current code without unnecessary rewrites | Phase 0, C18 | ✅ additive package + separate process/venv |
| R21 | Validate physical actions and record acknowledgements | C14 | ✅ registered/validated/confirmed/acknowledged actions with full transition audit (Phase 7) |
| R22 | Apply configurable retention and safe deletion | C15 | ✅ configurable dry-run retention, aggregate-before-delete, evidence protection (Phase 8) |

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
