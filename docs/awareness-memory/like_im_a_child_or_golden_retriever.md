# TALOS Awareness and Memory, Explained Like You Are a Child or a Golden Retriever

This is the **start here** guide for a new operator or intern. It explains what
the subsystem does, how to start it, how TALOS uses it, where the code lives,
and how to change it without accidentally teaching the robot house unsafe
tricks.

For the exhaustive reference, use
[`talos/awareness/README.md`](../../talos/awareness/README.md). For the current
implementation state, use [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

## The 30-second version

Imagine TALOS has:

- **Ears**: MQTT messages from devices.
- **A mailroom**: validates, sorts, timestamps, and stores every message.
- **A whiteboard**: the best known current state of the house.
- **A diary**: immutable events and numeric history.
- **A watchdog**: deterministic rules, alerts, and notifications.
- **A scrapbook**: validated facts and meaningful past incidents.
- **A butler's briefing card**: a small, current situation summary placed in
  front of the LLM.
- **A locked command desk**: validates and audits physical actions before
  anything is sent to a device.

The LLM can read the briefing card and use narrow tools. It is **not** the
mailroom, database, watchdog, retry loop, or safety controller.

The subsystem is a separate Python process. PostgreSQL is its source of truth.
MQTT is only the delivery truck. The main TALOS process talks to it over a
loopback HTTP API at `http://127.0.0.1:8600` by default.

## First important distinction: TALOS has two related memory systems

Do not mix these up.

| System | Simple description | Storage | Used for |
|---|---|---|---|
| Main-agent prompt memory | TALOS's conversational notebook | SQLite, normally `db/talos_memory.sqlite3` | Session summaries, explicit facts, and compact prompt memory |
| Awareness long-term memory | The house's evidence-backed scrapbook | PostgreSQL + pgvector | Validated semantic facts, preferences, episodic incidents, provenance, and search |

When the host tool `remember_memory_fact` is used, the main-agent SQLite copy
is written first. TALOS then tries to mirror that fact into awareness memory.
The tool result says whether the awareness copy synced. The SQLite copy remains
authoritative for the existing prompt-memory path.

Current state and telemetry are **not** semantic memory:

- "The fan is on now" belongs in `current_state`.
- "The temperature averaged 72 degrees yesterday" comes from telemetry.
- "Jack prefers the office at 70 degrees" can be long-term semantic memory.
- "The basement overflowed three times last week" can become episodic memory.

This separation prevents TALOS from answering an exact current question with
a fuzzy memory search result.

## What happens when a device says something

```text
Device
  │ publishes MQTT (`home/#` or legacy `status/#`)
  ▼
MQTT ingestion
  │ checks topic ownership, size, schema, source, sequence, and timestamps
  ▼
One database transaction
  ├── immutable event history
  ├── current state and state-transition history
  ├── numeric telemetry
  ├── deterministic rule evaluation
  └── durable outbox work
          ├── notification delivery
          ├── memory embedding / incident memory
          └── physical-action dispatch / timeout

PostgreSQL-backed API on 127.0.0.1:8600
  ├── compact situation snapshot ──► TALOS router ──► LLM prompt
  ├── bounded read/search tools ───► aggregate MCP server ──► LLM tools
  └── validated action requests ──► audited MQTT command lifecycle
```

Important consequences:

1. A duplicate MQTT delivery does not create a duplicate event.
2. A late event stays in history but cannot move current state backward.
3. A retained MQTT value is evidence, not automatically fresh current state.
4. Alerts and notifications still work when Ollama is unavailable.
5. Network silence never counts as a successful physical action.

## Quick start: make it work now

### Prerequisites

You need:

- Docker Desktop running.
- Python 3.12 for `.venv-awareness`.
- The main TALOS environment, `.venv-main`, if you also want to talk to TALOS.
- A repository `.env` file.
- Access to the Raspberry Pi MQTT broker for live devices, or MQTT disabled for
  a safe API-only desk setup.

Do not replace an existing `.env`; it may already contain working TALOS
secrets. Add or edit the awareness settings in it.

At minimum:

```dotenv
TALOS_AWARENESS_DB_PASSWORD=replace-with-a-real-local-password
```

Strongly recommended, and required for physical actions:

```dotenv
# Use the same value in the awareness process and main TALOS process.
# It must contain at least 16 characters.
TALOS_AWARENESS_API_TOKEN=replace-with-a-long-random-local-token
```

For an API-only start that does not ingest live device messages:

```dotenv
TALOS_AWARENESS_MQTT_ENABLED=0
```

That switch disables the ingestion connection. It is **not** a physical-action
safety lock: the action dispatcher uses the configured broker independently.
Do not request actions in this mode. For full development isolation, start the
compose test broker and point all MQTT traffic at it:

```dotenv
TALOS_AWARENESS_MQTT_ENABLED=1
TALOS_AWARENESS_MQTT_HOST=127.0.0.1
TALOS_AWARENESS_MQTT_PORT=1885
```

```bash
docker compose -f docker-compose.awareness.yml --profile test up -d --wait
```

For live operation, use the real broker settings. Defaults are
`192.168.1.160:1883` and can fall back to the legacy `MQTT_BROKER` and
`MQTT_PORT` variables.

### One-time setup

Run from the repository root:

```bash
docker compose -f docker-compose.awareness.yml up -d --wait

python3.12 -m venv .venv-awareness
.venv-awareness/bin/python -m pip install -r requirements-awareness-py312.txt

.venv-awareness/bin/python -m talos.awareness migrate
.venv-awareness/bin/python -m talos.awareness check
```

Migrations are deliberately explicit. Starting the API never applies them for
you.

### Start the three processes

Use separate terminals.

Terminal 1, awareness backend:

```bash
.venv-awareness/bin/python -m talos.awareness serve
```

Terminal 2, main TALOS app:

```bash
.venv-main/bin/python -m talos
```

Terminal 3, optional local microphone worker:

```bash
.venv-voice/bin/python -m talos.voice.worker
```

The awareness backend is usable without the voice worker. The main app is
where the router, GUI, text server, agent runtime, and MCP client live.

### Prove it is alive

In another terminal:

```bash
curl -s http://127.0.0.1:8600/health/components | python3 -m json.tool
curl -s http://127.0.0.1:8600/metrics | python3 -m json.tool
curl -s http://127.0.0.1:8600/capabilities | python3 -m json.tool
curl -s http://127.0.0.1:8600/situation | python3 -m json.tool
```

Good signs:

- The database and migration revision are healthy.
- MQTT is `connected` for live operation, or truthfully disabled for API-only
  operation.
- Freshness and outbox workers are running.
- `/situation` returns bounded text plus an audit.

The CLI health command uses these exit codes:

| Exit code | Meaning |
|---:|---|
| `0` | Healthy |
| `1` | Degraded; some capability is unavailable |
| `2` | Unavailable |
| `3` | Configuration error |

## Use it through TALOS

Once the awareness backend and main TALOS process are running, normal text or
voice requests use awareness automatically. You do not manually paste house
state into prompts.

Try questions like:

- "What is the fan's current state, and how fresh is that reading?"
- "Were there any alerts while I was away?"
- "When did the pump last run?"
- "Show the average greenhouse temperature over the last 24 hours."
- "Why do you believe that sensor reading?"
- "What do you remember about my preferred office temperature?"
- "Is the awareness system healthy?"

For an explicit fact:

- "Remember that I prefer the office at 70 degrees."

For a physical action:

- "Water plant 1."
- "Turn the fan on."

An accepted action is not the same as a completed action. TALOS returns a
durable request ID and lifecycle state. It should use `get_action_status` to
verify acknowledgement and completion. Never interpret `dispatched` or
silence as physical success.

## The tools TALOS gets

The built-in aggregate MCP server registers these awareness tools:

| Tool | Use it for |
|---|---|
| `get_current_state` | Present-tense device/entity facts, freshness, confidence, and source |
| `get_recent_events` | "When did this happen?" and recent event history |
| `get_sensor_history` | Numeric averages, minimums, maximums, and trends |
| `get_active_alerts` | Open or acknowledged incidents |
| `get_system_health` | Database, MQTT, workers, policy, and degradation |
| `get_event_provenance` | "Why do we believe this?" and source/timestamp evidence |
| `search_memory` | Validated facts, preferences, and past episodes |
| `request_device_action` | A registered, validated, audited physical action |
| `get_action_status` | Full physical-action transition history |
| `get_awareness_capabilities` | What is available, degraded, or unavailable |

The older friendly names `water_plants` and `toggle_fan` still exist, but now
route through the same safe action service.

Tool selection rule:

| Question shape | Correct source |
|---|---|
| "Is it on now?" | Current state |
| "When did it happen?" | Event history |
| "What was the average?" | Telemetry aggregates |
| "What should I pay attention to?" | Alerts / situation |
| "What do you remember about me?" | Long-term memory search |
| "Why do you think that?" | Provenance and health |

## How it plugs into TALOS

There are four integration points.

### 1. Situation injection

[`talos/router.py`](../../talos/router.py) calls
`awareness_client.snapshot_with_fallback(...)` for voice, text, and LLM event
requests. [`talos/services/awareness_client.py`](../../talos/services/awareness_client.py)
fetches `/situation`, caches it for five seconds, and gives the router a compact
briefing.

If the backend is unavailable, the router uses its legacy in-memory snapshot.
The command still runs, but with reduced awareness. Disable situation injection
explicitly with:

```dotenv
TALOS_AWARENESS_SITUATION_ENABLED=0
```

### 2. MCP tools

[`talos/mcp_servers/aggregate.py`](../../talos/mcp_servers/aggregate.py)
registers the awareness provider from
[`talos/mcp_servers/providers/awareness.py`](../../talos/mcp_servers/providers/awareness.py).
Those tools make bounded HTTP calls through `awareness_client`; they do not
give the model SQL, filesystem, or raw MQTT access.

### 3. Explicit memory facts

[`talos/agent/runtime.py`](../../talos/agent/runtime.py) handles
`remember_memory_fact`. It writes the existing SQLite prompt-memory store, then
posts a deterministic, user-evidenced copy to `/memory/deterministic`. A failed
awareness sync is reported as `awareness_memory_synced: false`; it is not
silently called successful.

### 4. Physical actions

[`talos/services/home_automation.py`](../../talos/services/home_automation.py)
routes `water_plants` and `toggle_fan` to `/actions/request`. The action service
validates registry policy, parameters, actor, current-state safety rules,
cooldown, confirmation, and idempotency before durable outbox dispatch.

## Daily operator checklist

1. Confirm Docker Desktop and `talos-awareness-db` are running.
2. Run `.venv-awareness/bin/python -m talos.awareness check`.
3. Inspect `/health/components` if the result is degraded.
4. Confirm MQTT is connected if live devices are expected.
5. Check outbox backlog and oldest pending age in `/metrics`.
6. Check active alerts and stale/offline sources.
7. Confirm the most recent backup age in `/metrics`.
8. Start the awareness process before or alongside main TALOS.

Useful read-only calls:

```bash
curl -s http://127.0.0.1:8600/alerts | python3 -m json.tool
curl -s http://127.0.0.1:8600/state/fan | python3 -m json.tool
curl -s http://127.0.0.1:8600/actions | python3 -m json.tool
```

The known seeded entities include `fan`, `quad_pump`, `plant_pot_1`,
`plant_pot_2`, and `sim_greenhouse`.

### Stop cleanly

Stop the awareness process with `Ctrl-C`, then stop the database containers:

```bash
docker compose -f docker-compose.awareness.yml stop
```

Do **not** run `docker compose ... down -v` unless you intentionally want to
delete the database volume. The `-v` is the tennis ball flying into traffic.

## Maintenance jobs

These are explicit commands, not background magic.

### Back up

```bash
.venv-awareness/bin/python -m talos.awareness backup
.venv-awareness/bin/python -m talos.awareness backup --verify
```

The second command restores into a scratch database and verifies the backup.
The local default keeps 14 days. Production scheduling is owner-managed; an
example cron entry is in [`.env.example`](../../.env.example).

### Preview and run retention

Always preview first:

```bash
.venv-awareness/bin/python -m talos.awareness retention
```

Only after checking the cutoffs, eligible counts, and protections:

```bash
.venv-awareness/bin/python -m talos.awareness retention --execute
```

Retention is batched and resumable. It protects open alert evidence and active
memory provenance. Raw measurement deletion refreshes aggregates first.

### Consolidate memory

```bash
.venv-awareness/bin/python -m talos.awareness consolidate
```

This summarizes repeated incidents with provenance links and decays old weak
model inferences. It does not decay user-confirmed memory.

### Apply a new migration

After pulling code that contains a migration:

```bash
.venv-awareness/bin/python -m talos.awareness migrate
.venv-awareness/bin/python -m talos.awareness check
```

Never make startup auto-migrate. Never fix a revision mismatch by editing the
database tables by hand.

## What each major code area does

Start with one path through the system; do not read every file at once.

| Path | Responsibility |
|---|---|
| [`talos/awareness/config.py`](../../talos/awareness/config.py) | Typed env configuration and sanitized summaries |
| [`talos/awareness/schemas/events.py`](../../talos/awareness/schemas/events.py) | Strict canonical event envelope |
| [`talos/awareness/registry/`](../../talos/awareness/registry/) | Known sources, entities, topic ownership, freshness policy |
| [`talos/awareness/ingestion/`](../../talos/awareness/ingestion/) | MQTT connection, normalization, ordering, dedupe, dead letters, transaction orchestration |
| [`talos/awareness/db/models.py`](../../talos/awareness/db/models.py) | SQLAlchemy source of the database model |
| [`talos/awareness/db/migrations/`](../../talos/awareness/db/migrations/) | Explicit Alembic schema history |
| [`talos/awareness/state/`](../../talos/awareness/state/) | Current state, authority, conflict, freshness, transitions |
| [`talos/awareness/history/`](../../talos/awareness/history/) | Bounded event and telemetry queries |
| [`talos/awareness/rules/`](../../talos/awareness/rules/) | Versioned deterministic policy and evaluation |
| [`talos/awareness/alerts/`](../../talos/awareness/alerts/) | Incident lifecycle and attention records |
| [`talos/awareness/outbox/`](../../talos/awareness/outbox/) | Crash-recoverable bounded background work |
| [`talos/awareness/notifications/`](../../talos/awareness/notifications/) | GUI and log notification adapters |
| [`talos/awareness/context/`](../../talos/awareness/context/) | Compact situation and provenance rendering |
| [`talos/awareness/memory/`](../../talos/awareness/memory/) | Validated memory, relationships, search, embeddings, consolidation |
| [`talos/awareness/actions/`](../../talos/awareness/actions/) | Registered actions and audited lifecycle |
| [`talos/awareness/retention/`](../../talos/awareness/retention/) | Dry-run and protected batched deletion |
| [`talos/awareness/api/`](../../talos/awareness/api/) | FastAPI lifecycle and narrow routes |
| [`talos/awareness/__main__.py`](../../talos/awareness/__main__.py) | `serve`, `migrate`, `check`, `retention`, `consolidate`, and `backup` CLI |

### Best first code-reading walk

For an event:

1. `schemas/events.py`
2. `ingestion/normalization.py`
3. `ingestion/pipeline.py`
4. `state/classification.py`
5. `state/manager.py`
6. `rules/engine.py`
7. `outbox/worker.py`

For a user question:

1. `talos/router.py`
2. `talos/services/awareness_client.py`
3. `api/routes/context.py` or `api/routes/reads.py`
4. `context/broker.py` or `history/queries.py`

For memory:

1. `api/routes/memory.py`
2. `memory/service.py`
3. `memory/embeddings.py`
4. `outbox/worker.py`
5. `talos/mcp_servers/providers/awareness.py`

For a physical action:

1. `actions/actions.toml`
2. `api/routes/actions.py`
3. `actions/registry.py`
4. `actions/service.py`
5. `outbox/worker.py`
6. `ingestion/pipeline.py` for acknowledgements/state evidence

## Database map without database soup

| Concern | Main tables |
|---|---|
| Identity | `locations`, `entities`, `entity_relationships`, `sources` |
| Immutable input | `events`, `dead_letter_events` |
| Present and numeric state | `current_state`, `state_transitions`, `measurements`, `source_health_history` |
| Attention | `alerts`, `alert_events`, `attention_items` |
| Reliable background work | `outbox`, `notification_deliveries` |
| Long-term memory | `memories`, `memory_embeddings`, `memory_provenance`, `memory_relationships` |
| Physical actions | `action_requests`, `action_transitions` |
| Files and versions | `artifacts`, `schema_registry` |

The database is authoritative. Do not build a parallel in-memory dictionary as
the new source of truth.

## The golden rules for developers

1. **Deterministic code owns truth and safety.** The LLM may explain or propose;
   it does not validate sensors, detect critical alerts, retry jobs, or execute
   arbitrary commands.
2. **Current state, history, telemetry, working state, and long-term memory are
   different things.** Put data in the correct bucket.
3. **Exact questions use structured retrieval.** Vector search is for semantic
   similarity, not "is the pump on?" or "what was yesterday's average?"
4. **Every input is bounded and validated.** Time windows, payloads, batches,
   result counts, context, and retries all need hard limits.
5. **Assume duplicate, late, reordered, and missing messages.** Make writes and
   work handlers idempotent.
6. **Preserve evidence.** Keep provenance, confidence, timestamps, clock
   quality, source health, validity, and relationships.
7. **Do network and model work outside the event transaction.** Commit durable
   outbox intent, then let a bounded worker do it.
8. **Failures must be visible and truthful.** Degraded is a useful answer.
9. **Physical actions use registered schemas and evidence.** No raw MQTT tool,
   no invented command, no "probably succeeded."
10. **Hardware/firmware keeps immediate interlocks.** The backend is not a
    substitute for a fuse, float switch, cutoff, or motor interlock.

The full permanent list is in
[`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md).

## Common development recipes

### Add a source or sensor

1. Define or extend the strict event schema.
2. Add the source/entity/topic ownership in `registry/bootstrap.py` or the
   approved registry path.
3. Add only the necessary adapter normalization.
4. Define deterministic state/telemetry effects.
5. Test authorized, unauthorized, malformed, duplicate, delayed, reordered,
   stale, and reboot behavior.
6. Use the simulator before any physical hardware test.

Do not accept a topic merely because it arrived on the broker.

### Add or change an alert rule

1. Edit `talos/awareness/rules/rules.toml`.
2. Keep it deterministic and typed.
3. Add tests for match, non-match, dedupe, resolution, cooldown, and critical
   behavior.
4. Confirm notifications remain useful without Ollama.

### Add a read capability

1. Add a bounded API/service query.
2. Return timestamps, freshness/confidence/source, and `truncated` when needed.
3. Add a thin method/tool in the main-agent client/provider.
4. Tell the model exactly when to use it in the tool docstring.
5. Test backend failure and limit clamping.

Do not expose SQL, arbitrary files, or an unbounded "get everything" route.

### Add a physical action

1. Register the action in `actions/actions.toml`.
2. Define strict parameters, actors, confirmation, cooldown, timeout, allowed
   state, acknowledgement source/meaning, idempotency, and rollback policy.
3. Generate MQTT only from the registry definition.
4. Persist every request and transition.
5. Prove success from an acknowledgement or post-action state, never silence.
6. Test reject, confirm, duplicate, timeout, wrong-source ack, late ack, and
   failure behavior with the simulator.
7. Exercise real hardware only with explicit owner authorization.

### Change the database schema

1. Change `db/models.py`.
2. Add a matching Alembic migration under `db/migrations/versions/`.
3. Test a clean migration and model/schema parity.
4. Apply the migration explicitly to the development database.
5. Document deployment impact.

Never weaken the model-drift test to make it green.

## Tests an intern should know

Unit tests do not need infrastructure:

```bash
.venv-awareness/bin/python -m unittest \
  tests.test_awareness_config tests.test_awareness_event_schema \
  tests.test_awareness_health tests.test_awareness_ingestion_unit \
  tests.test_awareness_state_unit tests.test_awareness_rules_unit \
  tests.test_awareness_context_unit tests.test_awareness_actions_unit
```

Integration tests need the database and, for MQTT ingestion, the test broker:

```bash
docker compose -f docker-compose.awareness.yml --profile test up -d --wait

.venv-awareness/bin/python -m unittest \
  tests.test_awareness_migrations \
  tests.test_awareness_ingestion_integration \
  tests.test_awareness_state_integration \
  tests.test_awareness_alerts_integration \
  tests.test_awareness_context_integration \
  tests.test_awareness_memory_integration \
  tests.test_awareness_actions_integration \
  tests.test_awareness_hardening_integration
```

Main-agent integration:

```bash
.venv-main/bin/python -m unittest \
  tests.test_text_server_notify \
  tests.test_awareness_client_and_provider \
  tests.test_home_automation_actions
```

Run the test files through `unittest` as shown. Report skips, failures, and
infrastructure that was not available; do not call a skipped database test a
passing live integration test.

Simulator suite:

```bash
docker compose -f docker-compose.awareness.yml --profile test up -d --wait

.venv-awareness/bin/python -m talos.awareness.simulator \
  --host 127.0.0.1 --port 1885 --scenario suite
```

The simulator defaults above target the test broker, not the Raspberry Pi.

## Troubleshooting: symptom to first check

| Symptom | First checks |
|---|---|
| Configuration error | Read the named `TALOS_AWARENESS_*` variables in the error; settings come from env or `.env` |
| Database unavailable | Docker Desktop, `docker compose ... ps`, password/port, then migrations |
| Migration mismatch | Run `python -m talos.awareness migrate`, then `check`; never auto-migrate in startup |
| MQTT disconnected | `/health/components` ingestion state, broker address, reachability, auth/TLS, `last_error` |
| Events missing | Dead-letter reason and ingestion counters; verify source registration and allowed topics |
| State is stale/offline | Source receipt time, freshness thresholds, device/broker connectivity |
| State is conflicting | Compare authority rank, event time, source, and contender metadata; do not pick a winner by guessing |
| Alert repeats | Check one incident's `occurrence_count` and evidence; dedupe is expected |
| GUI notification missing | Text server at `:8420`, notify token, delivery records, then log fallback |
| Memory keyword search works but semantic ranking does not | Ollama/model availability and embedding outbox backlog; full-text search should still work |
| Physical action route returns `503` | Set the same 16+ character `TALOS_AWARENESS_API_TOKEN` for backend and main TALOS, then restart both |
| Action is stuck or failed | Inspect `GET /actions/{id}` transitions, outbox health, MQTT, timeout, and acknowledgement evidence |
| Disk/backups look bad | `/metrics`, data directory, last backup age, backup logs, and a `backup --verify` run |

## Known limits: do not promise more than the system has

- The real Picos have legacy limitations: no native command IDs, acknowledgements,
  reconnect, or buffering.
- Legacy `status/16` is ambiguous between devices; the registry assigns it to
  the fan. Fixing the collision requires firmware work.
- The pump firmware sleeps through its fixed cycle and cannot be remotely
  aborted during that sleep.
- The two deployed physical actions lack a backend-checkable pre-command safety
  sensor. Strict parameters, cooldown, status evidence, and firmware interlocks
  still apply.
- User location and conversation relevance are not available to the situation
  selector; it prioritizes alerts, attention, and freshness.
- Semantic embeddings require local Ollama and `nomic-embed-text` by default.
  An outage queues retries; it must not block deterministic features.
- Broker authentication, ACLs, and TLS require the owner to execute and verify
  [`BROKER_HARDENING_PLAN.md`](BROKER_HARDENING_PLAN.md) on the Raspberry Pi.
- No process manager is installed by the repo. The owner must arrange long-lived
  service supervision and nightly backup scheduling for production.

## Your first hour as the new intern

- [ ] Read this guide once.
- [ ] Start the database with MQTT disabled.
- [ ] Run migrations and `check`.
- [ ] Start the awareness API and inspect health, metrics, capabilities, and
      situation.
- [ ] Start main TALOS and ask one current-state question and one health
      question.
- [ ] Run the unit tests.
- [ ] Start the test broker and run the simulator suite.
- [ ] Follow one simulator event from normalization to the database to the
      situation response.
- [ ] Read one action request from registry validation through its transition
      audit without touching physical hardware.
- [ ] Preview retention. Do not execute it merely for practice.
- [ ] Run a backup verification on the development database.
- [ ] Read [`ARCHITECTURAL_INVARIANTS.md`](ARCHITECTURAL_INVARIANTS.md) before
      proposing a design change.

If you remember only one sentence, remember this:

> **The database remembers, deterministic code decides, tools retrieve, the
> LLM explains, and hardware keeps the emergency brake.**
