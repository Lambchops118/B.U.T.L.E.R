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

## ADR-041 — Select microphone-specific capture contracts in the launcher

Date: 2026-09-08. Status: accepted. Owner authorized the ReSpeaker repair and a
launcher choice between ReSpeaker and Yeti. Persist one explicit profile and
inject it into the voice worker. ReSpeaker opens its named PortAudio endpoint at
16 kHz stereo, selects USB channel 2 (the documented auto-selected ASR beam), and
uses the recognizer's measured ambient threshold. It does not use the Yeti's
Windows AEC/barge-in or unaccepted idle-VAD contract because Talos renders on
BenQ and does not supply the XVF3800 hardware far-end reference. Yeti preserves
the pinned Windows communications-AEC contract and fixed threshold, with a
named-device ordinary-capture fallback if Windows's active defaults do not
match. Invalid or legacy launcher values normalize to ReSpeaker on this deployed
host. No profile claims production accuracy until the visible phrase corpus
passes, and no raw room PCM is recorded automatically.

## ADR-042 — Keep exact launcher LLM debugging local, ephemeral, and bounded

Date: 2026-09-08. Status: superseded in part by ADR-043. Owner authorized a launcher tab showing
exact prompt data sent to the LLM and data received from it for breakage
diagnosis. Capture the final provider request object after Chat Completions tool
schema conversion, every provider response chunk exposed by the SDK, the
assembled completion/tool calls, and separately labeled warmup and Responses API
traffic. Enable capture only in launcher-managed main-agent processes. Carry
records over the existing child stdout pipe, intercept them out of ordinary
logs, expose no endpoint, write no separate transcript file, and bound the GUI
to the latest 5,000,000 characters. The tab must visibly warn that prompts can
contain conversation history, memory, awareness context, tool schemas,
arguments, and results. Capture failure must never change model-call behavior.
This resolves OQ-K only for exact LLM request/response debugging; audio and other
sensitive telemetry feeds retain their existing gates.

## ADR-043 — Persist exact LLM I/O in local per-run transcripts

Date: 2026-09-08. Status: accepted. Owner explicitly requested permanent
logging after reviewing the live debug view. In addition to the existing stdout
feed, a launcher-managed main agent appends the identical structured events to
`talos/logs/llm_io_<UTC timestamp>_<pid>.jsonl`. Create the directory lazily,
use one file per process run, preserve full payloads without redaction, and do
not prune them automatically. Git-ignore the file pattern to reduce accidental
source-control disclosure. Filesystem and serialization failures remain
non-fatal to inference. The log is local but highly sensitive and requires
manual deletion when no longer wanted. This supersedes only ADR-042's
no-transcript/ephemeral policy; its GUI bound, stdout transport, no-network
endpoint, and remaining OQ-K gates continue to apply.

## ADR-044 — Presence is explicit state; silence never expires it

Date: 2026-09-08. Status: accepted. The owner reported an unrequested "welcome
back" turn after any quiet stretch. Owner presence is an explicit fact about a
person, not a sensor reading with a shelf life, so the `talos_agent` source is
exempt from state expiry (`metadata.state_freshness_detection = false`) and only
an explicit user statement may write `present = false` — exposed as the
`set_owner_presence` tool, which the model is told never to infer from silence,
elapsed time, or sleep mode.

The exemption binds on three paths, not one: the freshness worker skips those
rows, every read (`SituationBroker`, entity history) re-derives expiry through
the same opt-out instead of aging the row at query time, and the seeded
migrations clear the residue the old 15-minute deadline left behind. Speech is
gated separately: a transition whose value is unchanged and whose status merely
moved (`stale -> current`) is pipeline bookkeeping and is never spoken, so the
recorded expiry/recovery pairs stay as audit evidence without becoming a
homecoming the owner never made. The arrival briefing additionally requires a
genuine `false`/`absent -> true` value change.

## ADR-045 — Sleep mode and the physical display are one action

Date: 2026-09-08. Status: accepted. The owner reported having to ask for sleep
mode and for a dark screen separately. Sleep mode always means a dark screen and
waking always means a lit one, so the display command is issued from
`sleep_mode._set` — the single write path — rather than by each caller. No path
can enter sleep mode and leave the display on: the spoken phrase matcher, the
`sleep_mode_control` tool, the morning wake-up announcement, and both scheduler
jobs all go through it, and the 23:00 job now enters sleep mode instead of
darkening the TV behind the flag's back.

The panel dim is separate from the TV and stays that way: it is a real
brightness change on the pygame window, applied in the CRT fragment shader after
its 1/gamma pass. Applying it on the CPU before that pass was self-defeating --
a requested 1% arrived on the glass at roughly 18% of awake brightness, and the
8-bit multiply crushed the mid-tones first. `DIM_LEVEL` is now obeyed literally
and `TALOS_SLEEP_DIM_LEVEL` tunes it. The plain-pygame fallback path keeps the
BLEND_MULT fill, which is correct there because it has no gamma pass.

`talos.services.display_power` reuses the two mechanisms already proven in
production — adb standby to go dark, an MQTT `tv_display/wake_status` = `"1"`
publish to come back — and runs them on a daemon thread. The sleep flag stays
authoritative: a display that cannot be reached records a failure in
`last_result()` (surfaced by the tool's `status` action) and never turns a good
night into an error, and `TALOS_DISPLAY_POWER_ENABLED=0` disables the coupling
for a headless host.

## ADR-046 — Accept Windows AEC barge-in on the ReSpeaker XVF3800

Date: 2026-09-08. Status: accepted; resolves the barge-in half of ADR-041's
fail-closed decision and OQ-P's far-end question for this microphone. Owner
requested the probe and authorized the change on its result.

ADR-041 disabled barge-in for the ReSpeaker because its far-end reference to the
BenQ render path was unvalidated, and the microphone-profile work made
`respeaker` the default. That combination silently removed barge-in: the
`windows_aec` flag gates it in both the launcher (`_microphone_env` forces
`TALOS_BARGE_IN=0`) and the voice worker (`run_voice_recognition` clears
`_barge_in_runtime_ready`), so `TALOS_BARGE_IN=1` had no effect.

The bounded live probe answered the open question directly. Windows reports
acoustic echo cancellation, noise suppression, AGC and deep noise suppression
active on the XVF3800 capture endpoint, and the far-end reference resolves as
`system_default_verified` — the pinned render endpoint *is* the current system
default, which is exactly the binding the driver requires when it exposes no
explicit reference control. At amplitude 0.06 the probe measured **43.668 dB
ERLE** (far-end RMS 278.863 -> 1.829), peak normalized correlation 0.546 -> 0.050,
and 0 callback errors — comparable to the Yeti baseline that originally
qualified this contract (45.696 dB at amplitude 0.03).

`windows_aec` is therefore true for both deployed profiles. The suppression path
in the launcher and the worker is unchanged and remains the fail-closed default
for any profile added later, which must produce its own probe evidence first.
The generic `TALOS_AUDIO_CAPTURE_ENDPOINT_ID` fallback, which still held the
Yeti identity after the ReSpeaker became the default capture device, is brought
back in step; the per-profile pins remain the values the worker actually reads.

## ADR-047 — A sleep/wake claim requires evidence, never history

Date: 2026-09-08. Status: accepted. The owner reported that the first "butler,
sleep" dimmed the screen but later ones did not, and theorized the model was
copying earlier turns instead of acting. The captured LLM I/O confirms it
exactly: of six sleep/wake turns, two matched no phrase, so nothing was applied
and the model was handed no context -- and, with two of its own "I am now in
sleep mode. The screen is dimmed" replies sitting in history, it said it again
over a screen that never changed.

The deterministic layer is widened but that is not the fix. Whole-utterance
anchors now tolerate a known conversational run-up (the recognizer drops the
leading word often enough that "let's sleep mode" arrives as "'s sleep mode")
and trailing politeness or repetition ("again" appears precisely when someone
asks a second time). Lookalikes stay excluded: what remains after the strip must
still match a complete phrase, so "set a sleep timer" and "wake me at seven"
change nothing.

The fix is that an unmatched turn is no longer silent. Any turn in the
sleep/screen/panel vocabulary that matched no phrase now carries
``UNVERIFIED_NOTE``: nothing changed, earlier announcements in this conversation
say nothing about this turn, ``sleep_mode_control`` is the only way to act, and
a state change must never be stated without a system note or tool result behind
it. Detection is deliberately broad because a false positive costs one true
sentence of context while a false negative costs a fabricated confirmation --
and a confident sentence with no state change behind it is indistinguishable,
to someone listening, from the feature working.

## ADR-048 — The wake word is stripped as a pattern, not a slice

Date: 2026-09-08. Status: accepted; supersedes the prefix handling assumed by
ADR-047, whose note remains the backstop.

The owner said "butler, sleep mode" every time, so the "'s sleep mode" seen in
the transcripts had to come from the pipeline. It did: faster-whisper writes
"butler's sleep mode" (short comma pause, following word starts with an s), and
the wake-word removal sliced off exactly `len("butler")` then stripped
" ,.:;!?-" -- a set with no apostrophe in it. The command handed on was
"'s sleep mode". Removal is now a compiled pattern covering the possessive
(straight and curly apostrophe) and the punctuation run.

Replaying the captured request against the deployed model settles the "is the
small model just not capable" question with measurements rather than opinion.
Qwen3 14.8B Q4_K_M at temperature 0.2 called `sleep_mode_control` 6/6 for every
well-formed phrasing -- with the full 26-tool list, the full system prompt,
conversation history present, and `/no_think` set. Only the garbled turn failed,
and only with history present: "'s sleep mode." scored 6/6 with no history and
0/6 with it. The model is not incapable and thinking mode is not the variable;
a degraded input is, because history then offers a nearby precedent to imitate.
Fixing the transcript at the source removes the input that triggers it, the
widened matcher means such a turn is applied deterministically anyway, and
ADR-047's note covers whatever still reaches the model.

## ADR-049 — The transcript is not evidence, and the broker owns the context budget

Date: 2026-09-08. Status: accepted. The owner reported a long-standing suspicion
that the assistant answers as though it had called tools when it had not. It is
real, it is systemic, and it is two separate defects -- neither of them a limit
of the model.

**Stored history hides tool use.** ``_record_memory_turn`` persists only
``(command, response_text)``, so the tool calls that produced past answers are
dropped. After a few exchanges the replayed conversation is an unbroken run of
"question, then a confident prose answer, no tools", and the model follows the
pattern it can see over the instruction it was given. Replaying a captured
production request against the deployed model (Qwen3 14.8B Q4_K_M, temp 0.2,
full 26-tool list): "is the plant watering system online?" called a tool 5/5
with six or fewer prior messages and **0/8 with eight**, answering "online and
operational" having checked nothing. Restoring one real tool call to that same
history returned it to **8/8** -- the pattern is the variable, not capability.

The fix is a system notice placed after history and immediately before the user
turn -- the same position, and the same reasoning, as the authoritative time
block -- stating that the transcript records what was said, not what was
verified, and that a state question must be answered from a tool result. That
took the failing request to 8/8 while leaving conversational turns ("tell me a
fact", "summarize our conversation") at 0/8, so it buys grounding without
buying tool spam. Annotating past answers as unverified was also tried and did
nothing (0/8). ``TALOS_INJECT_HISTORY_GROUNDING=0`` disables it. Persisting tool
calls into history is the durable fix and remains open.

**The context budget was being overridden downstream.** The awareness broker
ranks candidates and fits them to ``situation_budget_tokens`` (600, ~2.4k
characters), guaranteeing that critical alerts survive its selection.
``_format_context`` then cut the result to 500 characters, discarding 1559 of
2059 characters on the deployed box -- every STATE, health and transition line,
which is to say every fact about the house, leaving three verbose announcement
receipts. A critical alert the broker had deliberately protected could be
discarded the same way. This is why a device-state question reached the model
with no device state in context. The limit is now a backstop above the broker's
budget (``TALOS_CONTEXT_SNAPSHOT_CHAR_LIMIT``, 4000), so the component that
reasons about priority is the one that decides what survives. Recorded as a
known defect in ``talos/todo/llm-turn-context-deep-dive.md``; this closes it.

The 500 was an artifact, not a constraint. It entered on 2026-02-22 in
``InfoPanel/voice_agent.py`` (commit d345ce5, "sending the command to openai as
to not waste tokens on everything heard") as a cap on
``StateStore.snapshot()`` -- an in-memory dict rendered as ``key:value(ttl)``
pairs, typically well under 100 characters, on a metered hosted API. It was a
guard against an unbounded dict, and generous at the time. The June 2026
restructuring (d1a4a50) carried it across verbatim, the awareness broker was
later built behind it with its own 600-token budget, and the two were never
reconciled.

Nothing about the local deployment required it. Measured on the box: the model
occupies 10.29 GB of the 5080's 16 GB, ``num_ctx`` is 16384, and logged prompts
run 5.0k-6.0k tokens -- a 36% peak with ~10.4k tokens spare. The KV cache is
allocated from ``num_ctx``, not from tokens actually used, so filling more of an
already-allocated window costs no additional VRAM at all. The real cost is
prefill: restoring the full snapshot measured **+649 prompt tokens and +9 ms**
to first token.

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
