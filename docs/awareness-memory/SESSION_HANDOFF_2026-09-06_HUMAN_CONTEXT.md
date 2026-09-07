# Session Handoff — 2026-09-06 — Human context and internal ingestion

```text
Session goal:
  Turn the awareness subsystem from a device backend into one that also knows
  about the person it serves and about its own work, and add a way to put a
  message into it by hand while debugging.

Current phase:
  Post-Phase-8 follow-on, owner-requested bounded task (not a new numbered
  phase). Phases 0-8 remain complete and unchanged.

Bounded task completed:
  Seven refactoring steps plus a manual-input endpoint, all authorized by the
  owner in this session:
   1. Registered `owner` (person) and `talos` (agent) as entities, and the
      `talos_agent` source, in registry bootstrap.
   2. Made ingestion transport-plural: the pipeline is built at API startup
      independently of MQTT and is shared by both ingress paths.
   3. Conversation reported as bounded interaction *facts* (started/ended,
      modality, routing mode, duration, ok) — never utterance text.
   4. Wake word and barge-in treated as presence observations that decay
      through the existing freshness worker.
   5. Agent job outcomes and failed tool calls emitted as `agent.*` events.
   6. Situation broker honors `interruptibility` and scores
      `conversation_relevance`; presence is its own section.
   7. Wired the previously caller-less `POST /memory/candidates` to a new
      `propose_memory_candidate` MCP tool.
   B. `POST /ingest` — internal/manual ingestion returning the pipeline
      disposition synchronously.
  Unsorted-data handling was explicitly out of scope this session.

Files added:
  talos/awareness/api/routes/ingest.py
  talos/services/awareness_signals.py
  tests/test_awareness_presence_integration.py
  tests/test_awareness_signals_unit.py
  docs/awareness-memory/SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md

Files modified:
  talos/awareness/registry/bootstrap.py   (entities + talos_agent source)
  talos/awareness/registry/sources.py     (allowed_transports / permits_transport)
  talos/awareness/state/freshness.py      (metadata.offline_detection opt-out)
  talos/awareness/ingestion/pipeline.py   (transport check; public .sources)
  talos/awareness/ingestion/service.py    (accept shared pipeline; seed_on_start)
  talos/awareness/api/app.py              (shared pipeline, seeding, /ingest router)
  talos/awareness/context/broker.py       (presence, relevance, interruptibility)
  talos/awareness/alerts/service.py       (conversation_relevance parameter)
  talos/awareness/rules/engine.py         (populate conversation_relevance)
  talos/awareness/reminders/worker.py     (reminder relevance)
  talos/awareness/rules/rules.toml        (agent-job-failed rule; version 1 -> 2)
  talos/awareness/config.py               (ingest_api_enabled + summary)
  talos/awareness/README.md               (Human context section, config, sources)
  talos/router.py                         (presence + interaction, voice and text)
  talos/voice/agent.py                    (wake word / barge-in presence)
  talos/jobs.py                           (job completed/failed events)
  talos/agent/runtime.py                  (failed tool call events)
  talos/mcp_servers/providers/awareness.py (propose_memory_candidate tool)
  settings.env                            (two new documented variables)
  docs/awareness-memory/DECISIONS.md      (ADR-028..031)
  docs/awareness-memory/IMPLEMENTATION_STATUS.md

Migrations added:
  NONE — and this is the substantive finding of the session. The schema had
  already anticipated all of this: `ENTITY_TYPES` permitted `person` and
  `agent`, and `attention_items` already carried `conversation_relevance`,
  `interruptibility`, `preferred_channel`, and `cooldown_key`. Only the
  producer side was missing. `tests/test_awareness_migrations.py` (the
  models/migrations lockstep check) passes unchanged, confirming no schema
  drift was introduced.

Decisions made:
  ADR-028 (record presence/interaction/agent outcomes, never transcripts)
  ADR-029 (POST /ingest runs the same pipeline; no bypass)
  ADR-030 (metadata.allowed_transports; internal sources cannot be forged
           over the LAN broker)
  ADR-031 (interruptibility honored; relevance orders within a priority band
           only, never across one)
  ADR-032 (sources may opt out of offline detection; silence is only a
           fault for a source expected to report on a schedule)

Assumptions confirmed or changed:
  Confirmed: `InboundMessage` was already transport-agnostic (`transport`
  field present, only ever "mqtt"), so no pipeline restructuring was needed.
  Confirmed: `IngestionPipeline._resolve_entity` degrades safely for
  unregistered entities, so seeding `owner`/`talos` is the only prerequisite
  for human state.
  Changed: the situation broker's static `limitations` string, which claimed
  no conversation-relevance signal exists, is now computed per request and
  reports which signals were actually available.

Tests run:
  .venv-awareness/Scripts/python.exe -m unittest discover -s tests -p "test_awareness_*.py"

Tests passed:
  157 of 160. Baseline before this work was 141 of 144 by the same command,
  so the 16 new tests pass and nothing regressed. New coverage: /ingest
  dispositions (accepted / unauthorized_topic / unauthorized_transport /
  413 oversized), device sources still unrestricted, presence becoming
  durable state on the person entity, interaction staying history-only,
  the agent-job-failed rule raising a deferred non-notifying item, failed
  tool calls raising nothing, presence/relevance/interruptibility in
  /situation, critical alerts surviving a relevance-99 competitor, and the
  signal emitter's bounded queue, rate limiting, drop counting, and
  never-raises behavior, and offline detection still faulting a silent
  device while exempting the agent source.

Tests failed:
  2 errors, both PRE-EXISTING and unrelated to this work:
  test_awareness_client_and_provider (2 tests) fails with
  "ModuleNotFoundError: No module named 'mcp'" because .venv-awareness does
  not have the MCP package; that suite is intended for .venv-main. It failed
  identically before any change in this session.
  1 skip: tests.test_awareness_ingestion_integration needs the test Mosquitto
  (docker compose --profile test), which was not running.

Commands not run:
  The main-venv suite (tests.test_text_server_notify,
  tests.test_awareness_client_and_provider, tests.test_home_automation_actions)
  was NOT run: .venv-main has no pytest/unittest-visible sqlalchemy, fastapi,
  or mcp installed, so it cannot execute.
  Partially closed since: every touched module was import-checked in the venv
  that actually runs it. talos.services.awareness_signals, talos.jobs,
  talos.router, and talos.agent.runtime all import cleanly in .venv-main, and
  talos.voice.agent imports cleanly in .venv-voice. So these are no longer
  merely syntax-checked; they load. What remains unverified is their runtime
  behavior inside a live agent process.
  No live voice session was run, so wake-word presence emission is unverified
  against real audio.
  The awareness backend was not started against the production Pi broker.

Known limitations:
  - Interaction events carry `entity_ids` only when the caller genuinely knows
    them; the router currently cannot attribute an utterance to an entity, so
    in practice that list is usually empty and conversation relevance
    contributes nothing. This is reported in the snapshot's `limitations`
    rather than papered over. Populating it (e.g. from device-action tool
    parameters) is the obvious next increment.
  - User location within the home is still not modeled at all.
  - Presence is single-occupant: one `owner` entity, no identification of who
    is present.
  - Text-modality presence proves someone is at a keyboard, not in the room;
    it is recorded with its own modality so the two are never conflated.
  - `propose_candidate` inserts with status "active" and lower confidence
    rather than status "candidate"; the review queue implied by the status
    column is still not a workflow anyone drives.

Security implications:
  `POST /ingest` is loopback-bound and bearer-gated by the same
  `require_write_auth` as other mutations, and can be disabled outright with
  TALOS_AWARENESS_INGEST_API_ENABLED=0. It grants no authority the broker path
  does not already have: registry topic ownership still applies, so it cannot
  write on behalf of an unregistered source.
  ADR-030 is a net security *improvement*: internal sources are now
  unforgeable from the unauthenticated LAN broker, which was previously
  possible for any `home/` topic.
  No new data leaves the host. Utterance text is never transmitted or stored.

Deployment implications:
  No migration to apply. New registry rows are seeded idempotently on next
  backend start (ON CONFLICT DO NOTHING), including on an already-booted
  database. The rule policy version moved 1 -> 2 and is re-registered in
  schema_registry at startup. Both new environment variables default to
  enabled; no configuration change is required to adopt this, and setting
  either to 0 restores the previous behavior.

Unresolved questions:
  - Should interaction events attribute entities from tool parameters, and is
    that worth the coupling? (Blocks conversation relevance being useful in
    practice.)
  - Should `agent.tool.failed` ever escalate after N failures in a window, or
    stay history-only as it is now?
  - Is a single `owner` person entity sufficient, or should presence be
    multi-occupant before it is built on further?
  - The unsorted/unmodeled-data question (observations table, promotion by
    repetition, salience decay) was deliberately deferred and remains open.

Current repository state:
  Runnable. Branch awareness_debug_09062026. All touched files compile; the
  awareness suite is green apart from the two pre-existing mcp import errors.
  Nothing was committed — the working tree holds the changes.

Next permitted task:
  Owner review. Then, if approved, either (a) run the main-venv suite and a
  live agent/voice session to verify emission end to end, or (b) populate
  interaction `entity_ids` so conversation relevance does real work.

Required reading for next session:
  talos/awareness/README.md § "Human context: presence, interaction, and agent
  outcomes"; talos/services/awareness_signals.py; talos/awareness/api/routes/
  ingest.py; talos/awareness/context/broker.py; ADR-028..031 in DECISIONS.md.

Explicit stop point:
  Stop here. Do not begin unsorted-data handling (the observations table,
  promotion by repetition, or salience decay) — it was explicitly excluded
  from this task and needs its own owner decision. Do not add utterance-text
  capture, remote exposure of /ingest, or escalation rules for tool failures
  without owner authorization.
```
