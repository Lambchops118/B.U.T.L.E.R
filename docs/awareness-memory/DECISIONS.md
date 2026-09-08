# Architecture Decision Log

Only source-confirmed decisions are recorded as accepted. Repository-dependent selections remain pending Phase 0 and must not be inferred from defaults.

## Phase 9A implementation decisions (2026-09-06)

- **ADR-033 — Accepted within the requested assembler scope.** Reuse existing
  `notification_deliveries` as the read-side delivery watermark, identified by
  `metadata.briefing_kind`, and reuse the situation `Candidate`/temporal helpers.
  No schema migration or runtime trigger is needed for 9A. Existing notification
  code already writes `attention_items.delivery_status`; the phase brief's
  assertion that it never does is stale. Delivery producers/bookkeeping remain
  9B work. Outbox completion is not delivery evidence and completed outbox rows
  are subject to retention.
- **ADR-034 — Accepted within the requested assembler scope.** Compute novelty
  as pooled sample z-scores in SQL over complete prior hourly buckets, separated
  by entity/measurement/unit. Exclude the candidate window from the baseline;
  do not claim first-ever observations from bounded, retained history. Missing,
  constant, nonfinite, and over-bound baselines are unscored. Bounded assembly
  fails if its critical-alert result could have been truncated; it never emits
  a partial result after a query error. These choices implement INV-02/12/17.

## Phase 9B–9D implementation decisions (2026-09-06)

- **ADR-035 — Accepted under owner continuation authorization through 9D.**
  Use a dedicated, filtered outbox worker for briefings, with batch size one,
  preserving the ordinary notification/action worker's independence from
  briefing model timeouts. Use existing outbox JSON for frozen preparation and
  existing notification ledger metadata for confirmed receipts, item identity,
  and selection/query provenance; no new table is needed. A session advisory
  lock serializes briefings without a database transaction spanning network
  work. Confirmed receipt, attention marking, and critical continuation enqueue
  commit together. The delivery window cursor is the recorded assembly end,
  preventing gaps while a previous briefing was being selected or deferred.
- **ADR-036 — Accepted implementation resolution of OQ-N.** The default-three
  cap applies per delivery. More than three critical items produce durable
  capped continuation batches; model output and feedback cannot suppress them.
  Noncritical quiet-hour deferrals spend no failure attempts. Use only the
  configured existing adapter: a failed voice enqueue cannot be laundered into
  a successful speech receipt by falling back to logging. Confirmation means
  adapter acceptance, with the existing ambiguous-enqueue crash/retry window
  explicitly retained. No exactly-once speech or playback claim is made.
- **ADR-037 — Accepted within the authorized selection/feedback scope.**
  Model output ranks supplied ids and records reasons; it never provides
  delivered text. Prompt `briefing-selection-v1` is bounded and sent only to
  loopback Ollama with proxies/redirects disabled. Invalid ids/schema, timeout,
  disabled/unset/unavailable models fall back with explicit audit mode/reason.
  Feedback uses active personal semantic memories in `briefing_preferences`,
  exact structured keys, existing supersession, and explicit-user provenance.
  Dismissal is enforced before prompting and rechecked before delivery;
  critical items are exempt. There is no remote trigger endpoint. Proactive
  delivery/model ranking remain opt-in until the owner configures rollout.

## Earlier decision table

**ADR-038 — Accepted bounded repair following the owner's live-test report.**
Authenticated `/speak` enqueues `AnnouncementPayload` on an explicit announcement
message type. The router sends its supplied text through the existing speech
helper without request classification, agent/tool execution, background-job
acknowledgements, or human activity signals. The previous voice-command wrapper
was classified as background work and spoke BACKGROUND_ACK instead of content.
This narrow repair is authorized by the bug report; ordinary user-command and
voice-worker behavior remains unchanged. No additional speech endpoint or
physical-action capability is introduced. HTTP 200 remains enqueue confirmation.

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

| ADR-018 | Superseded in part (2026-07-26; see ADR-022) | Quad-pump hardware and safety policy for the rebuilt board: relays were initially recorded as GP0-GP3 and fuse sense as GP6-GP9; active-high, one-pump-at-a-time, 30 s hard maximum, 8 s default, and the logical/legacy channel policy were accepted. | The safety and channel policy remains valid. The GPIO interpretation was disproved by live device evidence and is superseded by ADR-022. |
| ADR-019 | Accepted (owner, 2026-07-26) | Ship the quad-pump firmware **without fuse monitoring**. Every channel reports `fuse_N = "unknown"` permanently; no fuse interlock is active and no fuse voltage telemetry is published. Revisit comments are left in `qp_hardware.FuseBank` and `qp_config`. | The fuse signal is an analog divider on GP1/GP2/GP4/GP5, which are digital-only; the Pico W exposes ADC on GP26-GP28 only, and only three of them. Reading the divider as a logic level would report `ok`/`blown` from margins that have never been measured, so the firmware makes no fuse claim at all. Tracked as OQ-D. |
| ADR-020 | Accepted (2026-07-26) | Stage the canonical quad-pump rollout: register the canonical source and `run_pump`/`stop_pump` now, but leave `water_plants` dispatching on the legacy `quad_pump/{pot_pin}` topic. The new firmware answers both surfaces and republishes `status/{pin}=0` after a legacy cycle. | Plan §"Compatibility and rollout" steps 1-3. A partial deployment must not silently disable watering or double-activate a pump. Switching `water_plants` to the canonical command is a separate owner-gated step after physical acceptance. |
| ADR-021 | Accepted (2026-07-26) | Keep the canonical pump actions at `idempotency_behavior = "at_most_once"`, and add an additive `cooldown_scope`/`cooldown_parameter` to the action registry so `run_pump` rate-limits per channel instead of action-wide. | Device-side persistent deduplication is implemented and covered by host tests but has not been power-loss tested on hardware; acks alone do not make retries safe. Action-wide cooldown remains the default so `water_plants`, `toggle_fan`, and `sim_command` behavior is unchanged; setting the global cooldown to zero was explicitly rejected. |
| ADR-022 | Accepted from live board evidence (2026-07-26) | Correct the quad-pump hardware map to **GP9-GP12** for relay channels 1-4 and **GP1/GP2/GP4/GP5** for the unavailable fuse inputs. Relays remain active-high. | The owner's known-working MicroPython script instantiated `Pin(9)`, `Pin(10)`, `Pin(11)`, and `Pin(12)` and successfully activated every relay with `value(1)`. This proves the original table used GPIO identifiers rather than physical header positions and explains why the deployed GP0-GP3 firmware acknowledged commands without any relay click. Supersedes only the GPIO mapping portion of ADR-018. |
| ADR-023 | Accepted (owner authorized Phase A, 2026-07-26) | Fail closed with the legacy room barge-in heuristic disabled by both tracked configuration and code default. Permit synchronized room-audio fixture recording only through a separate explicit operator opt-in, with a visible warning, local-only storage, a non-blocking bounded queue, per-session duration/PCM limits, and bounded owned-session retention. | The completed design review found that RMS plus unconstrained ASR cannot establish trustworthy speech and can redispatch false commands. Phase A must contain that risk while enabling privacy-conscious measurement without silently recording the room. AEC/VAD backend selection remains separately gated by OQ-H and Phase B. |
| ADR-024 | Accepted from deployed-host evidence (owner authorized Phases B-F, 2026-07-27) | Select Windows communications-mode AudioGraph AEC on the pinned Yeti capture and BenQ render MMDevice endpoints behind `DuplexAudioProcessor`. Use clean AEC capture with Silero probability VAD and evidence-gated local faster-whisper. Never silently fall back to the RMS heuristic; AEC or endpoint failure disables barge-in while ordinary wake-word capture remains available. Keep `TALOS_BARGE_IN=0` until the owner-visible Phase F corpus and soak pass. | Windows exposed active AEC/NS/AGC/deep-NS and a verified system-default render reference. The in-memory live probe measured 45.696 dB ERLE and no callback errors. Direct WebRTC bindings available to this Python/Windows deployment had materially higher maintenance risk. |
| ADR-025 | Accepted (owner, 2026-08-09) | Restore SpeechRecognition as the production idle utterance segmenter while retaining one local faster-whisper transcription, AEC, and Silero barge-in. Keep an independent idle Silero lane behind both an enable request and explicit corpus-acceptance acknowledgement; preload local STT asynchronously and serialize ASR through a bounded queue that prioritizes fresh idle commands over queued interruption confirmations. | Commit `e33c2f1` shortened usable wake-word lead-in from about 390 ms to 224 ms by treating SpeechRecognition segmentation as redundant transcription. General ASR then commonly decoded the clipped leading "Butler" as "but there." The independent idle lane uses 640 ms pre-roll and cannot be deployed from configuration intent alone. |
| ADR-026 | Accepted (owner request, 2026-08-09) | Add the initial debug console as a standalone, read-only, loopback web service. It consumes bounded existing log/database artifacts and service probes; it does not join or modify the main agent, voice, awareness, or safety loops. | The owner requested an expandable information-centric debug page while forbidding major main-system changes. Existing JSONL telemetry, voice benchmark CSV, SQLite conversation history, and health surfaces supply useful data. Exact prompts/tool arguments and continuous audio frames remain unavailable until a separate bounded, privacy-reviewed producer is authorized. |
| ADR-027 | Accepted (owner correction, 2026-08-09) | The debug-console computer is not the TALOS system host. Never label console-local CPU/GPU/memory/disk as TALOS metrics; hardware metrics are remote-only and remain explicitly not configured until a system-host endpoint is supplied. | Local sampling produced authoritative-looking data for the wrong machine. `TALOS_DEBUG_SYSTEM_METRICS_URL` is now the read-only adapter seam; exporter selection, authentication, and deployment remain separate from this page task. |
| ADR-028 | Accepted (owner request, 2026-09-06) | Record human presence, bounded interaction facts, and agent job/tool outcomes as ordinary events through the existing ingestion pipeline, with the human (`owner`) and the agent (`talos`) registered as first-class entities. Never record utterance text. | The subsystem knew a great deal about devices and nothing about the person it serves; `/situation` was a device dashboard. No migration was required — `ENTITY_TYPES` already permitted `person`/`agent` and `attention_items` already carried `conversation_relevance`/`interruptibility`, so the schema had anticipated this and only the producer side was missing. Transcripts are excluded deliberately: they would exhaust the situation token budget (INV-12) and turn an event store into a chat log (INV-01). |
| ADR-029 | Accepted (owner request, 2026-09-06) | Add `POST /ingest`, a loopback, write-authorized endpoint that runs the identical `IngestionPipeline.handle` as the MQTT ingress and returns the disposition synchronously. It serves both the main agent's internal signals and manual injection while debugging. | Ingestion had exactly one entry point (the broker), so nothing could be injected without publishing to the Pi, and a rejection was only visible afterwards in `dead_letter_events`. `InboundMessage` already carried a `transport` field, so the pipeline was transport-agnostic by construction. Rejected: a bypass path that skips registry authorization — every guarantee the pipeline provides must apply identically regardless of entry point (INV-02, INV-17). |
| ADR-030 | Accepted (owner request, 2026-09-06) | Sources may pin themselves to a transport set via `metadata.allowed_transports`; empty means unrestricted. `talos_agent` lists `["internal"]` and violations are dead-lettered as `unauthorized_transport`. | The agent's topics live under `home/`, which the broker ingress subscribes to, so without a transport check anyone on the unauthenticated LAN broker could publish fabricated presence or interaction events. Empty-means-unrestricted keeps every existing device source behaviorally unchanged (INV-10). |
| ADR-031 | Accepted (owner request, 2026-09-06) | The situation broker honors `interruptibility` (withholding `passive` items while nobody is present) and scores `conversation_relevance` to order attention items *within* their priority band only. Priority bands are never reordered, so critical alerts remain first and untruncated. | CTX-002 and INV-12 make critical-alert survival non-negotiable; relevance is a tie-break, not an override, and is covered by a regression test asserting a relevance-99 attention item still loses to a critical alert. Relevance is deterministic (entity-set intersection plus an `immediate` bonus), never model-scored, per INV-02. The per-request `limitations` string reports when no interaction named an entity, so a zero-relevance ordering is stated rather than passed off as a judgment. |
| ADR-032 | Accepted (2026-09-06) | A source may opt out of offline detection with `metadata.offline_detection = false`, and the agent's internal signal source does. | Every other source is a device that reports on a schedule, so silence is evidence of a fault. The agent source reports only when a human interacts, so silence means nobody was home. Without the opt-out, leaving the machine off for a day would make TALOS announce that TALOS is offline on the next startup — alarming and false, violating INV-14's truthfulness requirement. Implemented as an explicit `IS NULL OR != 'false'` predicate because a bare `NOT(key = 'false')` yields NULL for sources lacking the key and would have silently disabled offline detection fleet-wide; a regression test asserts a silent device still faults while the agent does not. |

## ADR-039 — Separate briefing speech from diagnostic evidence

Date: 2026-09-06. Status: accepted. Scope: owner-reported Phase 9 speech defect.
The announcement routing repair exposed diagnostic candidate strings as audio.
Keep those strings for audit and selection, but add bounded deterministic
`spoken_text` from stored facts. Suppress owner transport metadata, preserve
critical notices, deduplicate equivalent sentences, and render legacy prepared
payloads through safe category fallbacks. Arrival batches may say welcome back.
No model rewriting or conversational-path inference is introduced. Generic
fallbacks omit detail when stored content looks like logs; history retains it.
Restart awareness backend; enqueue confirmation remains distinct from playback.

## ADR-040 — Recall proactive announcements from delivery receipts

Date: 2026-09-07. Status: accepted. Owner authorized announcement recall.
Save exact rendered wording in existing briefing and awareness notification
receipt metadata, alongside existing source references. Do not convert output
into authoritative facts or validated long-term memory. Accepted voice receipts
enter bounded situation context (latest three within 24 hours), below alert
priority. Failed and nonvoice attempts stay out. Briefing retrieval returns exact
wording when available; historical receipts without it remain unchanged.
Playback is explicitly unconfirmed. Quotes are historical data, not instructions;
manual arrival evidence requires checking the source events. No schema migration,
LLM call, speech restart, or physical action. Existing context budgeting and
receipt retention still apply; this covers awareness announcements, not unrelated
direct speech callers outside the awareness delivery ledger.

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
