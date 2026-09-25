# Session Log — awareness & memory

Condensed record of every bounded session on the awareness/memory subsystem.
Each entry replaces a former SESSION_HANDOFF_<date>[_TOPIC].md file, keeping
the goal, what shipped, the decisions, and anything still outstanding; the per-
session file inventories and test transcripts were dropped, since git history
carries them. Ongoing status lives in IMPLEMENTATION_STATUS.md, architectural
decisions in DECISIONS.md, and open items in OPEN_QUESTIONS.md. New sessions
append an entry here using SESSION_HANDOFF_TEMPLATE.md as the field list.

## 2026-07-16 — Awareness subsystem Phases 0–6
<a id="session-handoff-2026-07-16"></a>

Resume awareness-subsystem implementation on memory_system_3_07152026 under the
reorganized docs/awareness-memory/ specification.

**Shipped:** Port of Phase 0+1 (88f0e64) and partial Phase 2 (08b510e) from
memory_system_2_07152026; Phase 2 completion; documentation reconciliation;
Phase 3 (state authority/conflict/deadband manager, freshness worker,
measurements hypertable + 1m/1h/1d continuous aggregates, state_transitions +
source_health_history tables, bounded /state /events /telemetry reads;
migration dbfc40d327c0); Phase 4 (TOML rule policy + engine, alert dedup
lifecycle, attention cooldown/quiet hours, notification outbox + SKIP LOCKED
worker, gui/log adapters, text-server POST /notify, /alerts API; migration
7bf1cd5508d3); Phase 5 (SituationBroker + /situation /provenance /capabilities,
awareness_client + router snapshot fallback, MCP awareness provider with seven
read tools; no migration); Phase 6 (memory schema abc5ba9b578d with pgvector,
deterministic + candidate writes, supersession/conflict, episode-on-resolve,
embedding outbox handler, hybrid search, /memory API, search_memory tool,
remember_memory_fact mirroring).

**Decisions:** ADR-011..015 recorded from 2026-07-15 owner approvals; ADR-016
(2026-07-16): waive Phase 0 gate, port branch-2 work, continue phases in order.

**Limitations:** no MQTT lag metrics yet (MQTT-005 partial); dev machine off-
LAN, so the real Pi broker was never contacted — integration evidence is
against the owner-approved test broker; firmware issues remain (shared client
ID, status/16 collision, no reconnect) per ADR-014.

**Deployment:** requires Docker (timescale/timescaledb-ha:pg17 on :5433; test
Mosquitto on :1885 via --profile test) and .venv-awareness (Python 3.12).

**Security:** awareness API loopback-only; topic ownership enforced; broker
TLS/auth supported via config but Pi broker assumed anonymous (OQ-B).

## 2026-07-18 — Voice conversation continuity
<a id="session-handoff-2026-07-18"></a>

Diagnose and repair voice-chat conversation continuity and awareness-context
integration.

**Shipped:** Enabled the local conversation/prompt-memory store by default;
serialized streamed session context retrieval with turn completion; added
conversation-reset cleanup that preserves explicit facts; restored awareness
situation injection for /chat/stream; enabled memory in the local .env; added
multi-turn, reset, and awareness-injection regression tests.

**Decisions:** No new architectural decision. Existing separation was
confirmed: SQLite owns bounded conversation/session prompt context; awareness
PostgreSQL owns validated semantic/episodic long-term memory and situation
data.

**Limitations:** Conversation prompt context is a bounded compact summary (last
eight stored messages, subject to BUTLER_PROMPT_MEMORY_CHAR_LIMIT), not an
unlimited transcript. A live model/voice smoke test still requires restarting
the main and voice processes and starting awareness for situation data.

**Deployment:** Restart the main Butler process so import-time memory
configuration and code changes take effect. Start the separate awareness
process for house situation/state context; voice continuity itself degrades to
SQLite and does not depend on awareness availability.

**Security:** Conversation turns persist locally in ignored
db/talos_memory.sqlite3 by default. BUTLER_MEMORY_ENABLED=0 remains an explicit
privacy opt-out. Session reset deletes stored turns/summary but preserves
explicit durable facts.

## 2026-07-18 — Dynamic Reasoning (Thinking) Control
<a id="session-handoff-2026-07-18-dynamic-thinking"></a>

Reduce local-model (Ollama mb-core-v1, a Qwen3-14B finetune) response latency
after the cutover from hosted OpenAI Chat Completions.

**Shipped:** Added dynamic per-request reasoning control for the streamed lane.
Qwen3's /no_think and /think soft switches are appended to the outgoing user
turn based on a complexity heuristic (auto), with always/never/off overrides
via BUTLER_LLM_THINK_MODE. Only the live outgoing turn is decorated; the
original command is persisted to memory, so stored history stays clean and
continuity is unaffected.

**Decisions:** Default BUTLER_LLM_THINK_MODE=auto. auto suppresses thinking on
quick commands/chit-chat/status and enables it on analytical requests
(why/how/explain/compare/debug/plan/long or background-lane work). The
heuristic errs toward off because latency is the point. off exists for swapping
to non-Qwen models that would otherwise receive a literal /no_think token.

**Deployment:** Restart the streamed text-agent/voice process so it loads the
new code. Set BUTLER_LLM_THINK_MODE in the live .env only if overriding the auto
default.

## 2026-07-18 — Spoken Tool-Call JSON (Leaked Tool Calls)
<a id="session-handoff-2026-07-18-leaked-tool-calls"></a>

Investigate and fix the system speaking raw tool-call JSON aloud on tool
queries ("what's the temperature", "are there any calls recently").

**Decisions:** Treat a leaked tool call as a tool call (parse + execute) rather
than muting it, so the local model behaves like the structured Responses API
did. Keep markup out of both speech and history to also kill the self-
perpetuating mimicry loop.

**Limitations:** Recovery keys off content that begins with '{' or
'<tool_call'; a leak that begins with natural preamble text then emits markup
mid-stream would have the preamble spoken and the markup potentially spoken too
(not observed -- real leaks were pure markup from the first token). The false-
alarm path buffers a turn that starts with '{' until completion, a minor
latency cost for that rare case only.

**Deployment:** RESTART the streamed text-agent/voice process to load the
change. No new env required (BUTLER_RECOVER_LEAKED_TOOL_CALLS defaults on).

## 2026-07-18 — Local Ollama Inference
<a id="session-handoff-2026-07-18-local-ollama"></a>

Replace the streamed home-automation agent's hosted OpenAI Chat Completions
target with the owner's locally installed custom Ollama model while keeping STT
and inference local-first.

**Shipped:** Identified the installed model as mb-core-v1:latest; configured
the ignored live .env for Ollama on loopback; changed the streamed runtime to
use the existing backend factory; made Ollama the factory default with a
loopback endpoint; removed the streamed lane's OPENAI_API_KEY fallback; made
remote STT and legacy remote LLM failover explicit opt-ins; documented and
tested the profile. Correction follow-up: recovered the exact latest pre-
cutover live `.env` from VS Code Local History and merged the eight new
settings, restoring all prior machine-specific values without displaying them.

**Decisions:** ADR-017 records BUTLER_LLM_BACKEND=ollama,
BUTLER_LLM_BASE_URL=http://127.0.0.1:11434/v1, and BUTLER_LLM_MODEL=mb-
core-v1:latest for the default streamed lane. Remote STT and voice LLM fallback
default off to preserve the local-only boundary.

**Limitations:** The default streamed voice LLM and STT lanes are local. The
legacy non-streaming run_command path still uses hosted OpenAI Responses when
explicitly selected, and speech synthesis still uses AWS Polly, so those
separately bounded migrations are required before claiming the entire voice
stack has no network dependency. Weather and other explicitly hosted
integrations also remain network-dependent.

**Deployment:** Restart the main Butler/text-agent process and voice worker so
they load the new .env. Ollama must be running and mb-core-v1:latest must
remain installed. The ignored .env is machine-local and is not included in
source control.

**Security:** The local LLM target is loopback and no API key is used. Audio
stays local by default; remote transcription and legacy remote LLM failover
require explicit opt-in. Existing physical-action schemas, authorization,
confirmation, and audit boundaries are unchanged.

## 2026-07-18 — Personality Response Discipline
<a id="session-handoff-2026-07-18-personality"></a>

Make the active home-agent personality feel like a persistent household
presence rather than a customer-service chatbot.

**Shipped:** Confirmed butler/personality/monkey_butler.md is the active base
soul document for voice and text; added persistent-home framing, terse routine
confirmations, direct yes/no behavior, clean stopping behavior, prohibited
generic follow-up offers, explanation-length limits, and tool/device-confirmed
success language; replaced the conflicting Tactical Follow-Ups allowance; added
a prompt-assembly regression test.

**Decisions:** No ADR required. The existing active base prompt remains
authoritative; overlays and response code remain unchanged.

**Limitations:** Prompt compliance ultimately depends on the selected model. No
hard response post-processor was added because it could damage substantive
answers and was unnecessary for the requested smallest change.

**Deployment:** Restart the main Butler process to reload the base personality
content for subsequent turns.

**Security:** The existing evidence requirement was strengthened; an action may
not be described as successful without confirming tool/device evidence.

## 2026-07-18 — Time Increment & Follow-up Mimicry
<a id="session-handoff-2026-07-18-time-and-mimicry"></a>

Investigate two reported local-model misbehaviors — (1) responses ending with
"let me know what else I can help with" despite the persona forbidding it, and
(2) repeated "what time is it" turns incrementing the time by one each call
(5:55 -> 5:56 -> 5:57...) while the real clock was unchanged — and fix them.

**Decisions:** Fix the time bug structurally (ground-truth in context =
structured retrieval, per the architectural invariant) rather than only
instructing the model. For the pleasantry, owner chose history reset over a
deterministic stripper to avoid making output too rigid; the stripper remains
the fallback if it recurs.

**Limitations:** The pleasantry fix relies on history staying clean plus the
persona rule; if a pleasantry ever slips through and is persisted, mimicry
could restart. If that recurs, add the deterministic trailing-offer stripper
(strip before speaking and before persisting) that was designed and declined
this session. Time injection adds ~200 chars/turn.

**Deployment:** RESTART the streamed text-agent/voice process to load the time-
injection code. The history reset needs no restart. No new env required
(BUTLER_INJECT_CURRENT_TIME defaults on).

## 2026-07-18 — Tool Scoping & Local-Model Behavior
<a id="session-handoff-2026-07-18-tool-scoping-behavior"></a>

After the Ollama cutover, the local model (mb-core-v1, a Qwen3-14B finetune)
mis-selected tools (a "write pygame code" request triggered the kitchen recipe
screen) and ignored anti-agentic/followup controls that worked with
gpt-4o-mini. Determine whether this is an inherent model limitation and fix
what is fixable without making tool use so rigid that inferential behavior
("it's hot in here" -> lower the temperature) is lost.

**Decisions:** Preserve inference over rigidity — scope tools by intent
(structural) and steer behavior with balanced prompt guidance, rather than
forbidding tool calls that don't exact-match (owner-declined item 2).
BUTLER_SCOPE_TOOL_SURFACE defaults on; only the kitchen group is scoped today,
mechanism is extensible.

**Limitations:** Only the kitchen group is intent-scoped; other large future
groups can be added to _scope_specialized_tools. Multi-turn cooking followups
rely on kitchen-vocabulary terms; a purely pronoun followup ("add two more")
mid-recipe could drop kitchen tools for that turn. Scoping keys off the current
command only, not conversation state.

**Deployment:** Restart the streamed text-agent/voice process to load the new
code and prompt overlay. No new env required (BUTLER_SCOPE_TOOL_SURFACE defaults
on).

## 2026-07-18 — Streamed Voice Context
<a id="session-handoff-2026-07-18-voice-context"></a>

Repair the repeated streamed-voice failure to resolve immediate conversational
references such as “go with both.”

**Shipped:** Inspected the live local SQLite evidence and confirmed the exact
water-plants exchange was persisted under voice-worker. Replaced system-prompt-
only session-summary continuity in run_command_stream with bounded recent
user/assistant messages in actual Chat Completions role order. Kept durable
facts/summaries in the prompt-memory block without duplicating the active
session summary. Added message/character limits and exact regression coverage
for “Water the plants” → “Which pot?” → “Go with both.”

**Decisions:** No ADR required. Immediate conversational context is now
transported in the model API's native message roles; semantic/durable facts
remain separate.

**Limitations:** Streamed history defaults to the latest eight user/assistant
messages and 4,000 characters. Older conversation relies on summaries/facts and
is not guaranteed to preserve pronoun-level references.

**Deployment:** Restart the main Butler process to load the code. The voice
worker may also be restarted for a clean operational smoke test.

## 2026-07-20 — Proactive Presence
<a id="session-handoff-2026-07-20-proactive-presence"></a>

Let the awareness system speak up on its own — (A) route alerts to a spoken,
LLM-phrased channel instead of a silent GUI banner; (B) give the backend a
wall-clock reminder mechanism so "remind me at 7pm" is actually spoken at 7pm.

**Shipped:** Gap A (voice notification channel) + Gap B (due-time reminders).

**Decisions:** - Alerts are spoken via the existing voice_cmd router lane (the
same seam the legacy morning_report_job uses), so wording is the LLM's;
detection/rendering stay deterministic in the backend (no Ollama in the
awareness process). voice is the default preferred channel, with gui+log as
automatic fallback for resilience. - Reminders live in the awareness backend
(durable table + deterministic worker), reusing AlertService.raise_attention →
identical dedup/quiet-hours/cooldown/audit and one shared notification egress
for overflow AND reminders. - Natural-language time parsing is the LLM's job at
set_reminder time; the API requires an absolute timezone-aware future due_at
and rejects past/naive values.

**Limitations:** - "Confirmed" on the voice channel means the text server
enqueued the spoken alert, NOT that a human heard it (documented, consistent
with INV-14). Actual speech requires the main agent process + Ollama up; if
that path is down the outbox falls back to the gui banner then the log. - The
set_reminder tool trusts the LLM to compute the absolute due_at; a wrong offset
yields a wrong fire time. The API guards only against past/naive timestamps. -
No recurring reminders (one-shot only) and no "snooze"; add later if wanted.

**Deployment:** run migrations (`python -m butler.awareness migrate`) before
serving — the reminders table is new. New env vars (all optional, sensible
defaults): BUTLER_AWARENESS_NOTIFY_VOICE_ENABLED, _REMINDER_INTERVAL_SECONDS,
_REMINDER_INTERRUPTIBILITY, _REMINDER_CHANNEL.

**Security:** /speak reuses the same text-server auth as /notify (bearer +
allowed-network). Reminder write routes use require_write_auth (loopback-
trusted; bearer-gated when BUTLER_AWARENESS_API_TOKEN is set) — not fail-closed
like physical actions, since a reminder performs no physical action.

## 2026-07-24 — Pipeline Telemetry
<a id="session-handoff-2026-07-24-pipeline-telemetry"></a>

Investigate the perceived speech/latency regression, review the awareness and
full speech-to-LLM-to-output pipeline, answer the owner's focused questions,
and implement only correlated pipeline telemetry.

**Shipped:** Added telemetry identifying local versus hosted/fallback backends,
prompt-token estimates and provider counts when exposed, model-load
timing/status, and durations for each instrumented pipeline stage. Events share
a request ID across voice, text service, agent runtime, tools, LLM, TTS, and
playback. The new JSONL event stream intentionally excludes prompt, transcript,
response, and tool-argument content.

**Decisions:** - Use append-only per-process JSONL with correlated request IDs
as the durable telemetry record and SSE telemetry events for the voice/text-
process boundary. - Record prompt tokens as an explicitly labeled conservative
estimate unless the provider supplies usage. - Record exact faster-whisper load
duration. For Ollama's OpenAI-compatible stream, report an already-loaded zero
measurement when `/api/ps` confirms residency; otherwise report time-to-first-
token as a labeled cold-start upper bound because this endpoint does not expose
native model `load_duration`. - Keep telemetry best-effort so logging failure
cannot break the assistant pipeline.

**Limitations:** - Prompt-token telemetry is estimated when streaming providers
do not return usage. - Ollama native model load duration is unavailable through
the current OpenAI-compatible streaming response. The cold-load value is
therefore explicitly marked as a TTFT upper bound. - Per-process log files need
request-ID correlation when the voice and text services run separately. - Stage
timings add observability but do not themselves correct the suspected
regression.

**Deployment:** Telemetry is enabled by default through
`BUTLER_PIPELINE_TELEMETRY_ENABLED=1` and writes under `butler/logs` unless
`BUTLER_PIPELINE_TELEMETRY_DIR` overrides it. The directory must be writable. No
schema or service dependency was added.

**Security:** The new JSONL telemetry excludes conversational and tool-argument
payloads. It records operational metadata such as model/backend names, counts,
durations, request IDs, and error types. Existing legacy benchmark logs retain
their pre-existing behavior and were not broadened by this task.

## 2026-07-26 — Barge-In Redesign Phase A
<a id="session-handoff-2026-07-26-barge-in-phase-a"></a>

Implement the next bounded phase in `docs/voice/BARGE_IN_REDESIGN_PLAN.md`.

**Shipped:** Yes. Phase A is complete and the work stopped before the Phase B
AEC feasibility spike.

**Decisions:** - ADR-023: the unsafe legacy RMS-plus-ASR barge-in path fails
closed through both tracked configuration and its code default. - Synchronized
room-audio recording requires a separate explicit operator opt-in. It is local-
only, visibly announced, background-written through a non-blocking bounded
queue, capped by duration and PCM bytes, and subject to bounded retention of
recorder-owned fixture directories.

**Limitations:** - The legacy heuristic remains structurally unreliable. Phase
A contains it but does not improve its double-talk discrimination. - Aggregate
measurements are cumulative for the voice-worker process. They do not contain
transcripts/PCM and use bounded rejection labels. - Faster-whisper currently
provides no confidence in the active backend result, so ASR confidence remains
truthfully absent until supported. - Fixture WAV streams use monotonic per-
block timestamps and sample offsets for synchronization; live device-clock
accuracy has not been measured. - Graceful recorder close is wired into normal
voice-worker shutdown. Abrupt process termination may leave the newest fixture
incomplete.

**Deployment:** - Restart the voice worker to load the fail-closed code/config
default and new observability. - Normal deployment should leave both
`BUTLER_BARGE_IN=0` and `BUTLER_BARGE_IN_FIXTURE_RECORDING=0`. - An operator
fixture session needs deliberate configuration and should be handled as
sensitive room audio.

**Security:** - Raw room audio is off by default and requires
`BUTLER_BARGE_IN_FIXTURE_RECORDING=1`. - The recorder prints a visible warning,
stores only locally, writes under an ignored local data directory by default,
enforces duration/byte/session bounds, and deletes only prefix-matched
directories containing its manifest. - Privacy-safe barge-in telemetry contains
numeric counts/timings/RMS and bounded reason labels, never transcript text or
PCM. - Hosted-service behavior is unchanged.

## 2026-07-26 — Barge-in review
<a id="session-handoff-2026-07-26-barge-in-review"></a>

Investigate the unreliable barge-in voice interruption feature at both code and
architectural levels; fix it only if the design is sound, otherwise produce a
replacement plan.

**Shipped:** Traced microphone capture, render tracking, energy gate, ducking,
endpointing, faster-whisper transcription, interruption classification, local
playback cancellation, agent cancellation, conversation repair, and follow-up
redispatch. Determined that the front-end RMS-plus-ASR design cannot robustly
distinguish double-talk from echo. Documented an AEC-first redesign with phased
implementation and acceptance criteria. Added a README warning. Runtime
behavior was intentionally not patched.

**Decisions:** No owner-level architecture decision was recorded. The review
recommends WebRTC APM/AEC3 or a proven Windows endpoint AEC path, with AEC
output feeding VAD before ASR. Exact backend selection remains OQ-H pending a
deployment-host spike.

**Limitations:** - Current barge-in remains enabled by the tracked
`settings.env` and remains unreliable until the operator disables it or the
redesign is implemented. - Wake-word-required mode only contains false command
acceptance; it does not prevent false ducking or solve double-talk detection. -
The exact deployed input/output endpoints, Windows build, driver AEC support,
room response, and native WebRTC binding choice were not available from the
repository. - Current "audible prefix" bookkeeping marks a whole sentence at
its first PCM block, not the exact text heard.

**Deployment:** Safe interim posture is `BUTLER_BARGE_IN=0`. A production
redesign requires a Windows endpoint/AEC capability spike on the deployed host.
AEC failure must degrade to ordinary wake-word operation without barge-in,
never silently back to the RMS heuristic.

**Security:** False ASR text can currently enter the conversation as a user
command. Existing action authorization and safety boundaries still apply, but
this is not an acceptable authentication signal. Optional synchronized audio
fixture recording in the plan is explicitly opt-in, local, visible, and
retention-bounded because room audio is sensitive.

## 2026-07-26 — Plant Waterer Firmware and Awareness Integration
<a id="session-handoff-2026-07-26-plant-waterer-firmware"></a>

Execute Peripherals/quad_pump/plan.md: replace the legacy plant-waterer Pico W
firmware with safe, non-blocking firmware and add its bounded awareness
integration.

**Shipped:** Firmware rewrite + host tests + canonical source registration +
canonical run_pump/stop_pump actions + per-channel cooldown scope +
documentation. Rollout steps 1-3 of the plan's staged migration. Steps 4-10 are
physical and owner-executed.

**Decisions:** ADR-018 hardware/safety policy (relays GP0-3, fuse GP6-9,
active-high, 1 concurrent pump, 30 s hard max, 8 s default, fuse fault inhibits
start and stops a run, channel 1 = pot 1, channel 2 = pot 2) ADR-019 ship
without fuse monitoring; fuse state is permanently "unknown" ADR-020 staged
rollout; water_plants stays on the legacy topic for now ADR-021 at_most_once
retained; additive per-parameter cooldown scope

**Limitations:** - Fuse sensing is not implemented; every channel reports
"unknown" and no fuse interlock is active (ADR-019, OQ-D). The
debounce/hysteresis and fuse-policy code paths exist and are tested with fake
fuse readings, but the hardware cannot feed them. - Channel-to-pot ordering is
unverified (OQ-E). - Device-side persistent deduplication is implemented and
tested on the host across a simulated restart, but has not been power-loss
tested on hardware, so the action registry stays at at_most_once. - The device
clock is untrusted; issued_at is never a rejection input and the source keeps
clock_quality = server_received. - The backend's own command publications
return on its home/# subscription and dead-letter as unauthorized_topic (pre-
existing behavior, OQ-F). - qp_net contains a copy of umqtt.simple's wait_msg,
modified to expose the PUBLISH retain flag so a retained command replay cannot
water a plant. Keep it in step if simple.py is ever updated.

**Deployment:** - Copy main.py, simple.py, qp_config.py, qp_hardware.py,
qp_protocol.py, qp_ledger.py, qp_controller.py, qp_net.py, and a filled-in
qp_secrets.py to the Pico root. - Broker ACLs must allow the new client ID on
home/irrigation/quad_pump/# (see BROKER_HARDENING_PLAN.md). Owner-executed. -
The awareness backend must be restarted so seed_registry adds
quad_pump_canonical.

**Security:** - The Wi-Fi SSID and password that were committed in the old
main.py are gone from the current file but remain in Git history. **Rotate the
Wi-Fi password before or during deployment.** Deleting the file does not remove
them. - New secrets live in an untracked device-local qp_secrets.py; a redacted
qp_secrets_example.py is committed and .gitignore covers the real file. - The
device now uses a unique MQTT client ID (talos-quad-pump-<unique_id>) instead
of sharing "pico-w-client" with the fan Pico. - Published payloads carry no
credentials and bounded, non-secret error codes only (no tracebacks). A test
asserts this.

## 2026-07-26 — Plant-Waterer GPIO Mapping Hotfix
<a id="session-handoff-2026-07-26-plant-waterer-gpio-mapping"></a>

Resolve why fully acknowledged pump commands produced no physical relay click.

**Shipped:** Queried the durable action and event audit for the latest channel
3 and channel 1 requests. Both progressed requested → validated → approved →
dispatched → acknowledged → completed. The Pico reported command receipt,
`relay_N=true`, an approximately eight-second run, `relay_N=false`, and a
positive execution acknowledgement; health reported 6 accepted and 0 rejected
commands. This isolated the failure to the hardware mapping/interface. The
owner then supplied known-working MicroPython code using active-high `Pin(9)`,
`Pin(10)`, `Pin(11)`, and `Pin(12)`, proving the original mapping table
contained GPIO identifiers and the deployed GP0-GP3 interpretation was wrong.
Corrected channels 1-4 to GP9-GP12 and recorded fuse inputs GP1/GP2/GP4/GP5
while leaving fuse sensing unavailable.

**Decisions:** ADR-022 records live-board evidence: relay channels 1-4 use
GP9-GP12 and are active-high. The GPIO mapping portion of ADR-018 is
superseded. Safety/runtime/channel/legacy policies remain unchanged. Fuse
sensing remains disabled under ADR-019.

**Limitations:** The corrected mapping is only in the host repository until
`qp_config.py` is copied to the Pico root and the board restarts. Fuse sensing
is unavailable. Physical pot/zone assignment still requires one-channel-at-a-
time verification.

**Deployment:** Copy `Peripherals/quad_pump/qp_config.py` over `/qp_config.py`
on the Pico, ensure the application file is `/main.py`, and restart/power-cycle
the board. Confirm a new boot heartbeat before issuing one observed channel
command.

## 2026-07-26 — Voice Physical-Action Dispatch
<a id="session-handoff-2026-07-26-voice-action-dispatch"></a>

Diagnose and fix repeated voice claims that a plant-waterer action had been
initiated even though no relay activated.

**Shipped:** Confirmed from the post-reboot pipeline telemetry and conversation
database that the requests “water the monstera” and “turn on the pump for pot
two” each completed with zero tool execution while the model generated and
persisted an invented initiation claim. Added deterministic routing for
unambiguous pump/pot/channel commands through the existing registered
`request_device_action` tool. Added a general streamed physical-action guard
that withholds an unsupported success claim, retries the model once with a
fresh tool requirement, and emits a deterministic truthful failure if no action
tool is called. A subsequent live retry proved deterministic tool routing was
active, but exposed a second truthfulness defect: the model paraphrased the
action API's immediate `approved` (queued) response as “activated.” Explicit
pump commands now bypass that model paraphrase and deterministically report the
request ID/status with “physical activation has not yet been confirmed”;
rejected requests surface their bounded reason and API errors cannot become
success prose.

**Decisions:** Clear imperative pump commands with an explicit channel/pot
number route deterministically to `run_pump` or `stop_pump` through the
registered action boundary. The deterministic parser does not dispatch plant-
name-only or otherwise ambiguous requests. It recognizes the deployed speech-
to-text variants `too`/`to` for channel 2 and `tree` for channel 3 only when
they follow `pump`, `pot`, or `channel`. The awareness action service remains
authoritative for authorization, validation, cooldown, MQTT dispatch,
acknowledgement, timeout, and audit.

**Limitations:** The running main/voice process must be restarted again to load
the shortened deterministic action-result wording; request UUIDs remain in the
database audit but are no longer spoken. Plant-name-only requests still require
the model or stored entity context to select a channel. The Pico is online and
publishing recurring heartbeats from a stable boot. Database evidence for the
latest channel 3 and channel 1 runs shows command receipt, `relay_N=true`, an
approximately eight-second run, `relay_N=false`, and a positive execution
acknowledgement; device health reports 6 accepted and 0 rejected commands. That
state is firmware-commanded state, not electrical relay feedback. The remaining
no-click failure therefore requires direct GP0-GP3 voltage, polarity, power,
common-ground, logic-level, and wiring bench checks. Channel-to-physical-pot
wiring remains unverified.

**Deployment:** Stop and restart Butler from the launcher. Confirm awareness is
healthy on port 8600 before testing. Leave the Pico running `main.py` without
interrupting it from Thonny. Issue one explicit command with a channel number
and observe the action record, acknowledgement, and relay.

**Security:** No raw MQTT or GPIO bypass was added. Deterministic routing calls
the existing registered action tool, preserving API authorization, schema
validation, cooldown, idempotency, acknowledgement, timeout, and audit
boundaries. Ambiguous requests do not dispatch.

## 2026-07-26 — Windows Awareness MQTT Hotfix
<a id="session-handoff-2026-07-26-windows-mqtt-hotfix"></a>

Diagnose and fix accepted plant-waterer actions that never energized a relay.

**Shipped:** Fixed the Windows awareness server event loop so aiomqtt ingestion
and action publication can connect to the configured Mosquitto broker.

**Decisions:** No architectural decision. The Windows awareness entrypoint now
explicitly gives Uvicorn an asyncio.SelectorEventLoop factory because Uvicorn
0.36+ otherwise selects ProactorEventLoop on Windows even after a global
selector policy is installed. The selector policy is also retained for non-
Uvicorn asyncio CLI commands.

**Limitations:** The already-running launcher-owned awareness process on port
8600 loaded the old code and remains MQTT-degraded until Butler is restarted.
The activated plant-waterer board still has not emitted state, health, or a
heartbeat during a 35-second direct broker subscription (OQ-G). Physical
channel mapping, relay operation, and fuse limitations remain as documented in
the plant-waterer handoff.

**Deployment:** Restart Butler/the launcher so the awareness subprocess loads
the new loop configuration. Verify /health/components reports
mqtt.state=connected before another action. Then diagnose the Pico over its
serial console and require a heartbeat before physical pump testing.

## 2026-07-27 — Barge-In Phases B-F
<a id="session-handoff-2026-07-27-barge-in-phases-b-f"></a>

Implement all remaining phases of `docs/voice/BARGE_IN_REDESIGN_PLAN.md`.

**Shipped:** Selected and proved Windows communications AEC; added pinned
endpoint checks, bounded continuous duplex capture, clean idle recognition,
stateful Silero VAD with hysteresis/pre-roll, bounded off-audio-thread ASR,
segment evidence gates, safe PCM/chunk bookkeeping, an offline fixture
acceptance runner, rollout controls, and documentation.

**Decisions:** ADR-024 selects pinned Windows communications AEC; the RMS path
is diagnostic-only and never fallback; production stays disabled pending live
acceptance.

**Limitations:** Without Polly word speech marks, an interrupted current chunk
is deliberately under-claimed as partially heard; completed earlier chunks
remain exact. Live room acceptance remains unknown.

**Deployment:** Install pinned PyWinRT packages from `requirements-voice-
py312.txt`. The full MMDevice IDs are host-specific. Endpoint mismatch disables
barge-in and preserves ordinary wake capture.

**Security:** Raw room PCM remains off by default, explicit, bounded, local-
only, and uncommitted. AEC/VAD/ASR are local. No new action authority is
introduced.

## 2026-08-09 — Local Debug Dashboard
<a id="session-handoff-2026-08-09-debug-dashboard"></a>

Build an expandable, barebones web debug screen for interaction I/O, system
health, CPU/GPU data, and available audio metrics, with minimal impact on the
running system.

**Shipped:** Added a standalone local read-only debug dashboard with
Interaction I/O, System Health, Live Audio, and Raw Events tabs. It polls
bounded snapshots and uses existing artifacts/probes only.

**Decisions:** ADR-026 records the standalone read-only loopback service
boundary. ADR-027 records that system hardware metrics are remote-only because
the console runs on a different computer. The dashboard does not join the
agent/audio hot paths and refuses non-loopback binding unless the operator
explicitly supplies `--allow-remote`.

**Limitations:** Existing audio sources update after utterance/barge-in
snapshots, not per frame. Exact prompts and detailed tool calls are
unavailable. Conversation messages and pipeline request IDs cannot always be
correlated because current memory metadata does not store request IDs. Service
health is probe-based, not launcher-process authority. Remote hardware cards
remain `NOT_CONFIGURED` until a Butler-host metrics endpoint is selected and
supplied through `BUTLER_DEBUG_SYSTEM_METRICS_URL`.

**Deployment:** Start separately with `.venv-main\Scripts\python.exe -m
butler.debug_dashboard`; default URL is `http://127.0.0.1:8787`. The launcher
was intentionally not changed.

**Security:** The page exposes private conversation content. It binds to
loopback by default, has no authentication, is read-only, sets a restrictive
content security policy, and requires an explicit `--allow-remote` override for
non-loopback binding. It never reads or records raw PCM.

## 2026-08-09 — Wake Latency and Accuracy Recovery
<a id="session-handoff-2026-08-09-wake-latency-accuracy"></a>

Implement recommendations 1, 2, and the accuracy-safe parts of 5 from the wake-
word regression analysis for commit `e33c2f1`.

**Shipped:** Restored SpeechRecognition as the production idle utterance
segmenter without restoring duplicate transcription; added independent
idle/barge-in VAD lanes; gated idle VAD behind both an enable request and
explicit corpus acceptance; increased idle pre-roll to 640 ms; preserved
utterances across playback boundaries; asynchronously preloaded faster-whisper;
and serialized local ASR through a bounded priority queue that selects fresh
idle commands before queued barge-in confirmations.

**Decisions:** ADR-025. SpeechRecognition segmentation is not a second
transcription pass. Experimental idle VAD cannot activate unless both
`BUTLER_IDLE_VAD_ENDPOINTING=1` and `BUTLER_IDLE_VAD_CORPUS_ACCEPTED=1`.

**Limitations:** Faster-whisper remains a finished-utterance batch backend.
Speculative chunk decoding was not added because it would reintroduce redundant
inference and has no accuracy evidence; true incremental decoding needs a
supported streaming backend and its own accepted corpus. The independent idle
endpoint retains the conservative 480 ms trailing-silence setting until real
command-pause evidence exists.

**Deployment:** Restart the voice worker to pick up `BUTLER_VAD_ENDPOINTING=0`.
Production uses SpeechRecognition segmentation immediately. The new idle Silero
lane remains inactive under tracked settings. Faster-whisper begins loading
asynchronously during voice-worker startup.

**Security:** Audio remains local-first. No new upload path or action authority
was added. Room recording remains explicit and disabled by default.

## 2026-08-28 — Plant-Waterer Relay Activation Hotfix (GPIO Map)
<a id="session-handoff-2026-08-28-plant-waterer-relay-activation"></a>

Determine why `run_pump` / `water_plants` commands were accepted and
acknowledged while no pump physically activated.

**Shipped:** Traced the full dispatch path and reproduced the host's command
envelope against the firmware parser: the envelope parses cleanly,
`PumpController._run_command` reaches `_begin_run`, and `RelayBank.set` drives
its mapped GPIO high. The failure is the mapping itself. Extracting the netlist
from `Controller_Board_mk2.kicad_pcb` shows the relay drivers on
GP6/GP7/GP8/GP9 (GPIO -> base resistor -> low-side NPN -> relay coil -> output
terminal): GP6->R1->Q1->K4->J2, GP7->R4->Q4->K3->J3, GP8->R3->Q3->K2->J4,
GP9->R2->Q2->K1->J5. The fuse dividers reach GP0-GP3 through R5-R8. The same
netlist marks GP10, GP11 and GP12 `unconnected`, so `CHANNEL_RELAY_GPIO = {1:
9, 2: 10, 3: 11, 4: 12}` drove floating pins on channels 2-4. Corrected the
relay map to GP6-GP9 and the fuse map to GP0-GP3, numbering channels in output-
connector order J2-J5. Separately, commit `25e3fd2` raised the firmware
`DEFAULT_RUN_SECONDS` from 8 to 30 without touching the `water_plants` registry
entry, whose `timeout_seconds` was still 20. Because the legacy path publishes
`status/{pin} = 0` only after the cycle finishes, every successful 30 s run
would have been marked timed out. Raised it to 45 s, matching `run_pump`.

**Decisions:** ADR-028 records the netlist-derived map: relay channels 1-4 on
GP6/GP7/GP8/GP9, fuse inputs on GP0/GP1/GP2/GP3, channels numbered in output-
connector order, relays active-high (Q1-Q4 are low-side NPN switches). It
supersedes ADR-022 and the GPIO portion of ADR-018. ADR-029 raises the
`water_plants` timeout to 45 s.

**Limitations:** The correction is netlist-derived, not bench-measured; no
relay has been observed to click. Fuse sensing remains unavailable (ADR-019,
OQ-D). Firmware state reports remain commanded software state, not electrical
feedback.

**Deployment:** Done for the device. Remaining: issue one observed command per
channel and watch for a relay click on each of GP6-GP9 before trusting
unattended watering. The submodule changes (`qp_config.py`, `qp_hardware.py`,
`qp_secrets.py`) are uncommitted, and the submodule already carries commit
`25e3fd2` that the parent repository does not record, so both a submodule
commit and a parent pointer bump are still needed. Do not commit
`qp_secrets.py` further — see the security finding.

## 2026-09-01 — Plant-Waterer Network Resilience (Watchdog Reset Loop)
<a id="session-handoff-2026-09-01-plant-waterer-network-resilience"></a>

Determine why the pump controller board stopped working after several days
idle, distinguish a code fault from hardware damage, and — after owner
authorization — fix it and deploy.

**Shipped:** Diagnosed and fixed a permanent watchdog reset loop in the quad-
pump firmware's network path, deployed the fix to the Pico on COM6, and
verified it against the original failure conditions on hardware. ## Hardware
findings (no damage) The board on COM6 is healthy. USB enumerates as
`USB\VID_2E8A&PID_0005` with `ConfigManagerErrorCode 0`; MicroPython 1.28.0 on
`RPI_PICO_W`, RP2040 at 125 MHz, UID `e66598541b809938`; 200,800 B free RAM
against 4,640 B allocated (no leak or fragmentation); 757 KB of 868 KB flash
free with all eleven firmware files present and the ledger intact. The radio
joins in ~4 s at RSSI −32 dBm, DHCP issues `192.168.1.166`, and TCP to the
broker at `192.168.1.160:1883` completes in 12 ms. The watchdog was confirmed
armed and functioning: with the firmware interrupted at the REPL the board
reset at 7.8 s. ## Root cause Before anything was touched, the board reported
`machine.reset_cause() == 3` (`WDT_RESET`). Its last restart was a watchdog
timeout, not a power cycle. `NetworkSupervisor.ensure_connected()` performed
the Wi-Fi association wait **and** the MQTT connect inside a single call, while
`main.py` fed the 8 s watchdog only once per loop iteration. Both legs were
measured on the board: - Wi-Fi join from cold: ~4,000 ms (previously bounded by
`SOCKET_TIMEOUT_SECONDS = 5`) - TCP connect to an unreachable LAN host with the
firmware's own timeout: **5,003 ms** That is ~9 s between two watchdog feeds
against an 8,000 ms watchdog. The board reset *mid-connect*, before the failure
was ever recorded as a backoff, then rebooted into exactly the same conditions
and did it again — a permanent reset loop for as long as the broker was
unreachable. This matches the reported symptom precisely: the board goes idle
for days, the broker host goes down or changes address, and the pump controller
loops forever and looks dead until something power-cycles it at a moment when
the network happens to be healthy. The RP2040 hardware watchdog maxes out at
~8,388 ms, so the budget cannot be raised to fit the network. The blocking work
had to fit under it instead. Worst case was larger than 9 s: `_connect_mqtt`
also blocked on CONNACK and on three separate QoS 1 SUBACK reads, and
`publish(qos=1)` blocks on its PUBACK, with up to four publishes drained per
tick. ## Secondary defects fixed in the same path 1. **No liveness bound.**
`_maybe_ping` sent PINGREQ every 20 s and updated `_last_ping_ms`
unconditionally; nothing ever checked that a PINGRESP came back. A half-open
TCP connection left the board reporting `mqtt_connected: true` indefinitely. 2.
**The socket timeout was silently cleared.** `connect()` set a timeout, but
`check_msg()` → `wait_msg()` called `sock.setblocking(True)`, equivalent to
`settimeout(None)`. After the first poll every later read could block the main
loop forever. 3. **`poll()` caught only `OSError`** while `publish()` caught
broad `Exception`. The vendored client can raise `AssertionError`, `IndexError`
or `MemoryError` on a malformed packet; those escaped into the main loop and
stopped the relay deadline checks. 4. **No escalation.** `ensure_connected`
retried forever at the 60 s cap, never resetting the CYW43 and never resetting
the board, while the loop kept feeding the watchdog — hiding a wedged radio
rather than recovering from it. 5. **Cosmetic:** `uptime_ms` was a `ticks_diff`
against a boot reading, which goes negative after ~6.2 days (half the tick
period). ## Changes made `Firmware/qp_net.py` — the connect sequence is now a
state machine staged across loop iterations (`IDLE` → start association, non-
blocking → `WIFI` → poll `isconnected()` each tick → handshake). The MQTT
handshake is the only blocking step left and is bounded by
`SOCKET_TIMEOUT_SECONDS`, with watchdog feeds around it and between
subscriptions. Added `stale()` liveness detection driven by an `activity_hook`
the client fires for every received packet (PINGRESP included), `reset_radio()`
for the escalation ladder, broad exception handling in `poll()`, watchdog
feeding in `publish()`, wrap-safe tick arithmetic, and a `_restore_timeout()`
in the vendored-client subclass that restores the socket timeout instead of
clearing it. `Firmware/qp_config.py` — `SOCKET_TIMEOUT_SECONDS` 5 → 3; new
`WIFI_JOIN_TIMEOUT_MS = 8000`, `MQTT_INACTIVITY_TIMEOUT_MS = 90000`,
`RECONNECT_RADIO_RESET_ATTEMPTS = 5`, `RECONNECT_HARD_RESET_ATTEMPTS = 20`,
`OUTBOUND_DRAIN_PER_TICK = 4`. The measurements behind each value are recorded
in the file. `Firmware/main.py` — passes the watchdog to the supervisor;
accumulates uptime tick by tick instead of differencing against a boot reading;
reports `consecutive_failures` and `radio_resets` in health; and adds
`_should_hard_reset()` / `_hard_reset()`, which reset the board after the full
escalation ladder **only while no pump is running and none is queued**.
`Firmware/qp_controller.py` — `Clock.ticks_add()` and wrap-safe run-deadline
arithmetic. `tests/test_quad_pump_firmware.py` — `FakeClock.ticks_add`, plus a
`NetworkSupervisorTest` class of 14 cases driving the real supervisor with only
its two MicroPython-only steps stubbed.

**Decisions:** ADR-030 (stage the connect inside the watchdog budget), ADR-031
(liveness bound plus radio/board reset escalation ladder), ADR-032
(`WIFI_JOIN_TIMEOUT_MS` as a measured stall detector).

**Limitations:** The escalation ladder's final rung — `machine.reset()` after
20 consecutive failures — was verified by unit test and by inspection, not by
driving a real board through 20 failures. The relay map remains netlist-derived
and no relay has been observed to click. Fuse sensing remains unavailable
(ADR-019, OQ-D).

**Deployment:** Done for the device. The `Peripherals/Pump-Power-Controller`
submodule is **uncommitted** and still carries commit `25e3fd2` that the parent
repository does not record, so both a submodule commit and a parent pointer
bump are outstanding. Do not commit `qp_secrets.py` further.

## 2026-09-06 — announcement routing repair
<a id="session-handoff-2026-09-06-announcement-fix"></a>

Fix the owner's live arrival tests speaking the background-work
acknowledgement.

**Shipped:** /speak now creates a typed announcement, not a voice command.
Router sends supplied text to its existing voice-worker helper and GUI, without
classifying, invoking an agent/tools, creating a job, or emitting human
activity signals.

**Decisions:** ADR-038. Typed system-output routing fixes classification and
protects sourced briefing text from a second model rewrite. Existing voice-
worker API is reused.

**Limitations:** Text-server HTTP 200 still confirms enqueue, not audible
playback. The existing speech helper remains best-effort. Source text may be
technical; this repair does not add greeting generation or promise verbatim
"welcome home" output.

**Deployment:** Restart main agent/text server to load the changed message type
and router. Repeat absent→present injection afterwards; previous committed
receipts remain.

**Security:** Existing /speak authorization is preserved. Announcements cannot
run tools or physical actions. No new external integration or speech-worker
endpoint.

## 2026-09-06 — concise briefing speech
<a id="session-handoff-2026-09-06-briefing-speech"></a>

Fix owner-reported logs/JSON spoken by arrival briefings.

**Shipped:** Separate diagnostic text from deterministic speech; filter
presence metadata and summarize legacy queued payloads safely.

**Decisions:** ADR-039; no model rewrite. Candidate contract adds optional
spoken_text while preserving diagnostic text and source evidence.

**Limitations:** Safe fallbacks can be generic. HTTP acceptance is not
playback. Baseline p50 1238 ms / p95 2541 ms was not remeasured. Triggers,
bounded selection, model-failure fallback and critical overflow behavior remain
unchanged.

**Deployment:** Restart awareness backend; pending old payloads use safe
rendering too. Main agent needs the previous announcement fix loaded.

## 2026-09-06 — Human context and internal ingestion
<a id="session-handoff-2026-09-06-human-context"></a>

Turn the awareness subsystem from a device backend into one that also knows
about the person it serves and about its own work, and add a way to put a
message into it by hand while debugging.

**Shipped:** Seven refactoring steps plus a manual-input endpoint, all
authorized by the owner in this session: 1. Registered `owner` (person) and
`butler` (agent) as entities, and the `talos_agent` source, in registry
bootstrap. 2. Made ingestion transport-plural: the pipeline is built at API
startup independently of MQTT and is shared by both ingress paths. 3.
Conversation reported as bounded interaction *facts* (started/ended, modality,
routing mode, duration, ok) — never utterance text. 4. Wake word and barge-in
treated as presence observations that decay through the existing freshness
worker. 5. Agent job outcomes and failed tool calls emitted as `agent.*`
events. 6. Situation broker honors `interruptibility` and scores
`conversation_relevance`; presence is its own section. 7. Wired the previously
caller-less `POST /memory/candidates` to a new `propose_memory_candidate` MCP
tool. B. `POST /ingest` — internal/manual ingestion returning the pipeline
disposition synchronously. Unsorted-data handling was explicitly out of scope
this session.

**Decisions:** ADR-050 (record presence/interaction/agent outcomes, never
transcripts) ADR-051 (POST /ingest runs the same pipeline; no bypass) ADR-052
(metadata.allowed_transports; internal sources cannot be forged over the LAN
broker) ADR-053 (interruptibility honored; relevance orders within a priority
band only, never across one) ADR-054 (sources may opt out of offline detection;
silence is only a fault for a source expected to report on a schedule)
Renumbered from this session's original ADR-028..032, which collided with the
plant-waterer ADR-028..032 added on `main` while this branch was diverged; see
ADR-050 in DECISIONS.md.

**Limitations:** - Interaction events carry `entity_ids` only when the caller
genuinely knows them; the router currently cannot attribute an utterance to an
entity, so in practice that list is usually empty and conversation relevance
contributes nothing. This is reported in the snapshot's `limitations` rather
than papered over. Populating it (e.g. from device-action tool parameters) is
the obvious next increment. - User location within the home is still not
modeled at all. - Presence is single-occupant: one `owner` entity, no
identification of who is present. - Text-modality presence proves someone is at
a keyboard, not in the room; it is recorded with its own modality so the two
are never conflated. - `propose_candidate` inserts with status "active" and
lower confidence rather than status "candidate"; the review queue implied by
the status column is still not a workflow anyone drives.

**Deployment:** No migration to apply. New registry rows are seeded
idempotently on next backend start (ON CONFLICT DO NOTHING), including on an
already-booted database. The rule policy version moved 1 -> 2 and is re-
registered in schema_registry at startup. Both new environment variables
default to enabled; no configuration change is required to adopt this, and
setting either to 0 restores the previous behavior.

**Security:** `POST /ingest` is loopback-bound and bearer-gated by the same
`require_write_auth` as other mutations, and can be disabled outright with
BUTLER_AWARENESS_INGEST_API_ENABLED=0. It grants no authority the broker path
does not already have: registry topic ownership still applies, so it cannot
write on behalf of an unregistered source. ADR-052 is a net security
*improvement*: internal sources are now unforgeable from the unauthenticated
LAN broker, which was previously possible for any `home/` topic. No new data
leaves the host. Utterance text is never transmitted or stored.

## 2026-09-06 — Phase 9A
<a id="session-handoff-2026-09-06-phase-09a"></a>

Implement the requested Phase 9 plan within its explicit sub-phase gates.

**Shipped:** Deterministic read-only candidate assembly from alerts, pending
attention, transitions, agent/interaction events, and hourly measurement
aggregates. Candidate contract extends the situation broker's Candidate
vocabulary with category, entity/source, timestamp, query identifier, evidence,
and novelty. Shared temporal helpers render one-line historical qualification.
Arbitrary event payload text and memories are not included. Window derives from
the latest confirmed notification delivery carrying metadata.briefing_kind, or
an explicitly audited configured first-run window. All storage reads share a
repeatable-read snapshot. Time/count/global bounds are enforced and audited.
Potential critical-alert truncation fails closed. No partial result is returned
on query failure; only error type/kind is logged. Novelty uses SQL pooled
sample variance from complete prior hourly buckets, with units separated and no
self-baseline. Empty results contain no filler.

**Decisions:** ADR-033: reuse situation contract and notification ledger; no
migration for 9A. ADR-034: SQL pooled novelty; bounded history is not evidence
of first-ever; no partial safety summary after overflow/query errors.

**Limitations:** 9A only: no trigger behavior, delivery cap, model selection
guard/fallback, prompt version, delivery producer, or user feedback capture
exists yet. Delivered attention and alert incidents are excluded, but
comprehensive cross-kind deduplication of every candidate remains 9B work.
Missing/constant/nonfinite/over-bound baselines are unscored. Materialized
aggregate refresh lag can reduce coverage. Aggregates combine sources per
entity/measurement/unit. No claim of first-ever observations is made. Derived
records may have unknown source attribution; it is shown explicitly. Critical
overflow fails assembly; independent existing alerting is unchanged.

**Deployment:** No migration or live setting changes required. Four optional
configuration fields control assembly only (24-hour default lookback, 100
candidates, seven-day baseline, z threshold 3); this does not turn on proactive
output.

## 2026-09-06 — Phase 9A–9D
<a id="session-handoff-2026-09-06-phase-09-complete"></a>

Complete the proactive briefing plan. Owner explicitly authorized continuation
through 9D without further pauses except for critical reasons, requesting
efficient implementation/testing. No subagents were used.

**Shipped:** 9A: deterministic bounded candidates, six categories,
temporal/source/query provenance, SQL novelty, first-run/delivery-derived
windows. Extended to exclude confirmed items across kinds and apply structured
preferences. 9B: daily host-local schedule and stored arrival transitions,
idempotent outbox keys, restart recovery within configured windows, hard per-
batch cap, durable critical continuations, quiet-hours deferral,
receipt/attention bookkeeping, and truthful failures. Ordinary notifications
keep their own queued alerts/reminders; no second notification egress was
created. 9C: separate filtered outbox worker, bounded local Ollama ranking,
frozen selection before delivery, strict supplied-id validation, critical
overrides, source-only output text, prompt/model/selection provenance, and
deterministic fallback. The model decides neither the moment nor detection nor
severity. 9D: explicit dismissal/interest/neutral feedback using existing
memory writes, structured exact-key retrieval, filtering before prompt and
before delivery, and bearer-gated API/MCP operations. Critical items cannot be
dismissed. The work landed as `butler/awareness/briefing/` (service, worker,
selection, feedback), `butler/awareness/context/briefing.py`,
`butler/awareness/history/briefing.py`, `butler/awareness/api/routes/briefing.py`,
and four briefing test modules.

**Decisions:** ADR-033/034 (9A), ADR-035 (dedicated outbox/ledger), ADR-036
(critical batches and honest confirmation), ADR-037 (bounded ranking and
durable preferences). OQ-M/N resolved. OQ-O records production acceptance still
outstanding.

**Limitations:** Proactive delivery defaults off. Enable
BUTLER_AWARENESS_BRIEFING_ENABLED=1 on restart; defaults are 08:00 host local,
arrival enabled, cap 3, voice channel. Optional ranking defaults off. It uses
configured CHAT_MODEL on loopback Ollama when enabled, otherwise explicit
deterministic fallback. Adapter acceptance is the delivery boundary. Existing
/speak phrases through the reply system; GUI avoids that model dependency. A
crash/timeout after enqueue but before receipt commit can duplicate transport
because existing adapters have no end-to-end idempotency/playback
acknowledgement. Confirmed committed receipts prevent subsequent candidate re-
offering; exactly-once speech is not claimed. Critical query overflow still
fails assembly rather than silently truncating critical context; ordinary
alerting is independent. Novelty remains unscored with
missing/constant/nonfinite/over-bound baselines; aggregate lag limits coverage.
No first-ever claim from bounded history. Presence is single-owner and
interaction-based, not proof of physical arrival. Scheduled catch-up is today
only; arrivals have a bounded catch-up window.

**Deployment:** No migration. Opt-in settings and their defaults are documented
in the subsystem README. No existing live settings were changed. Worker state
is exposed in health/metrics, receipts in GET /briefings, and feedback in POST
/briefings/feedback plus the two narrow MCP tools. Do not also enable the old
commented-out morning_report_job schedule.

**Security:** No remote trigger endpoint. Feedback and receipt API routes use
configured bearer auth. Model requests use only loopback Ollama, no
proxy/redirects, bounded prompts/timeouts/output. Model text never drives
physical state, detection, alerts, or delivered candidate wording. Feedback
reads exact structured normal/personal active memories only; restricted memory
statements and transcripts are not read. No new credentials or external
integrations.

## 2026-09-07 — announcement recall
<a id="session-handoff-2026-09-07-announcement-recall"></a>

Owner requested remembering proactive output in later voice turns.

**Shipped:** Persist exact rendered wording in existing delivery receipts;
expose recent accepted voice output in situation context and saved briefing
text through the existing history tool. No fabricated legacy backfill.

**Decisions:** ADR-040. Output is historical evidence of an attempted/accepted
announcement, not a fact about a real arrival or proof of playback.

**Limitations:** Latest three voice receipts in 24 hours enter budgeted
context; long excerpts truncate at 800 characters and are labeled. Full
briefing wording remains tool-accessible within existing history/retention
limits. Legacy receipt wording is unknown. Baseline p50 1238 ms / p95 2541 ms
was not remeasured.

**Deployment:** Restart awareness backend and MCP provider/main agent. Use a
fresh briefing to test recall. No migration or changes to trigger behavior.

**Security:** No model calls, physical actions, or external services. Quoted
announcement data is explicitly distinguished from instructions/facts.

## 2026-09-08 — awareness block diagram
<a id="session-handoff-2026-09-08-block-diagram"></a>

Explain the awareness system with a detailed block diagram.

**Shipped:** Four Mermaid diagrams and supporting explanation of current
repository behavior.

**Decisions:** No new architecture or owner decisions; DECISIONS.md unchanged
by this task.

**Limitations:** Diagrams describe checked-out implementation, not verified
running services; Mermaid rendering not visually tested.

**Deployment:** None.

## 2026-09-08 — Four reported behavior fixes
<a id="session-handoff-2026-09-08-four-behavior-fixes"></a>

Close out four owner-reported defects: an unrequested "welcome back" after a
quiet stretch, a morning briefing that said only "plant waterer offline", a
failing sleep/dim tool call, and 500-character response truncation.

**Shipped:** (1) Presence staleness is now resolved through one shared helper,
`effective_state_status` in `butler/awareness/state/freshness.py`, so the read
path (`SituationBroker._qualified_status`, `history/queries.py`) honors the same
`state_freshness_detection = false` opt-out the worker does; transitions whose
value is unchanged are suppressed for owner presence, and a source migration
clears the stale `stale_after_seconds: 900.0` still on the deployed
`talos_agent` row (`reconcile_never_expiring_state` repairs rows the old
deadline had already marked stale). Speech `VERSION` is `briefing-speech-v2`.
(2)-(4) Briefing content selection, the sleep/dim tool call, and the response
length cap were repaired in the same pass.

**Deployment:** Restart the awareness backend and the text/voice agent, and run
migrations so the source-metadata fix lands on the live row.

## 2026-09-08 — Launcher logs and LLM I/O tabs
<a id="session-handoff-2026-09-08-launcher-llm-debug"></a>

Put ordinary launcher logs in a separate tab and add a third tab that shows
exactly what is sent to and received from the LLM for prompt-breakage
debugging.

**Shipped:** Replaced the launcher's single-page layout with Launcher, Logs,
and LLM I/O tabs. Launcher-managed main-agent children now emit structured LLM
boundary records. Chat Completions capture includes the final request dict
after tool conversion, raw SDK response chunks, assembled text, tool calls,
finish reason, and telemetry. Warmup and Responses API traffic are labeled
separately. The GUI routes valid main-process records only to the LLM tab and
leaves malformed or non-main records visible in ordinary logs. Sent records
render in blue/cyan and received records render in green, with distinct heading
and payload tags.

**Decisions:** ADR-042. Exact LLM capture is enabled only for a launcher-
managed main process, travels through the existing private stdout pipe, is not
separately persisted or network-served, and is capped at the latest 5,000,000
displayed characters. Capture errors fail open for inference.

**Limitations:** The display is an SDK-boundary representation: it shows the
exact request object supplied to the SDK and every response chunk the SDK
exposes, not encrypted HTTP bytes. The oldest display text is discarded after
the five-million-character cap. Directly starting the main agent without the
launcher does not enable capture. A launcher-started headless process emits the
structured records to its console because no GUI consumes them.

**Deployment:** Restart the launcher and launcher-managed main agent. No
setting, schema, database, network listener, or external service changes are
required.

**Security:** The LLM tab may contain private conversation history, remembered
facts, awareness context, tool arguments, and tool results. It is local and
ephemeral but remains shoulder-surfable and copyable. No secret redaction is
attempted because that would conflict with the requested exact payload view;
API transport headers/keys are not part of the SDK request object and are not
captured.

## 2026-09-08 — Persistent LLM debug transcripts
<a id="session-handoff-2026-09-08-llm-debug-persistence"></a>

Determine whether exact LLM I/O was permanently logged and, when it was not,
create a persistent location under the logs folder.

**Shipped:** Confirmed the existing feed was stdout/GUI memory only. Added
best-effort per-run JSONL persistence at `butler/logs/llm_io_<UTC
timestamp>_<pid>.jsonl`. Launcher-managed main-agent processes enable both the
existing stdout feed and the new file sink. The GUI identifies the saved-file
pattern. Exact transcript files are git-ignored.

**Decisions:** ADR-043 supersedes ADR-042 only where it prohibited a persistent
transcript. Per-run files preserve unredacted events, have no automatic
retention/pruning, and fail open for inference.

**Limitations:** No transcript appears until the restarted launcher-managed
main agent performs an LLM call. Files can grow for the lifetime of a process
and accumulate across runs because permanent logging has no automatic pruning.
Directly launched main agents remain opt-out unless the log-directory variable
is explicitly supplied.

**Deployment:** Restart the launcher and its managed main agent. New files will
appear under `butler/logs` on the first LLM debug event.

**Security:** These files contain unredacted prompts, conversation history,
memory, awareness context, tool schemas, tool arguments/results, and model
output. They exclude API transport headers but must still be treated as
sensitive local data. Gitignore reduces accidental commits but is not access
control or encryption.

## 2026-09-08 — Selectable Yeti/ReSpeaker Capture
<a id="session-handoff-2026-09-08-microphone-profiles"></a>

Repair the severe STT regression after switching from a Blue Yeti to a Seeed
Studio ReSpeaker XVF3800, and add an explicit launcher choice for either
microphone.

**Shipped:** Added shared microphone profiles, deterministic named PortAudio
capture, stereo PCM channel selection, launcher GUI and headless selection,
profile-specific endpoint/threshold behavior, regression tests, and operator
documentation. Set the tracked and current machine-local launcher selection to
ReSpeaker.

**Decisions:** ADR-041 selects microphone-specific capture contracts. The
ReSpeaker uses 16 kHz stereo USB channel 2 and calibrated SpeechRecognition
segmentation. Its barge-in and experimental idle VAD are disabled until a real
far-end/AEC topology and owner-visible corpus pass. The Yeti keeps its existing
Windows communications-AEC contract and fixed threshold.

**Limitations:** Production recall and WER are not yet measured on the new
path. The existing post-capture RMS gate remains 300. ReSpeaker barge-in and
experimental idle VAD are unavailable by design until their independent
acceptance requirements pass. If the USB friendly name changes, its
configurable name fragment must be updated.

**Deployment:** Restart the voice worker, or stop and restart the launcher
stack, to load the selected profile. Use the GUI **Room microphone** dropdown
or headless `--microphone respeaker|yeti`. No firmware upgrade is required for
this repair.

**Security:** Capture remains local. No PCM or transcript content was added to
logs or persisted by this repair. Profile selection only changes the voice
child process environment.

## 2026-09-08 — ReSpeaker XVF3800 STT Diagnosis
<a id="session-handoff-2026-09-08-respeaker-stt-diagnosis"></a>

Investigate the severe STT accuracy regression after replacing the Blue Yeti
with a Seeed Studio ReSpeaker XVF3800 USB 4-mic array, and identify whether the
cause is in Butler or the device.

**Shipped:** Traced the configured and live Windows capture paths, enumerated
the exact PortAudio devices used by the voice environment, compared fresh voice
telemetry before and after the fallback, inspected the Butler capture/STT code,
checked current official ReSpeaker documentation and firmware history, and
queried the connected XVF3800 through its official USB control protocol in
read-only mode.

**Decisions:** None. OQ-P records the owner-visible acceptance and capture
architecture choice required before a repair.

**Limitations:** Windows/PortAudio's precise stereo-to-mono mixing behavior was
not established from room audio. Whether it selects left or combines both
channels, the current path does not intentionally consume the documented right
ASR channel. The telemetry lacks rejected transcript text by design, so the
snapshot measures pipeline acceptance rather than WER against ground truth.

**Deployment:** The deployed voice worker is presently on the generic
SpeechRecognition fallback, not the pinned AEC/idle-VAD path described by the
tracked settings. Merely changing Windows's default microphone is insufficient
because Butler separately pins full MMDevice identities.
## 2026-09-13 — Pump 3 (Philodendron) registration; watering-failure diagnosis
<a id="session-handoff-2026-09-13"></a>

Owner asked why pumps 3 and 4 were unreachable and why the 2026-09-13 15:30
watering of pumps 1 and 2 did not run, then reported that a pump is now
physically connected to channel 3 watering a Philodendron.

**Shipped:** Registered pot 3 end to end — `plant_pot_3` seed entity
("Plant pot 3 (Philodendron)"), channel descriptions on `run_pump`/`stop_pump`
naming each pot and marking channel 4 as wired-but-unconnected, corrected
`request_device_action` / `get_current_state` / `water_plants` docstrings so the
legacy 2-pot tool can no longer be read as the system's limit, `philodendron`
added to the physical-action noun guard in `runtime.py`, and a canonical
channel-3 `mosquitto_pub` example in `Peripherals/debug_command.txt`. No
firmware, board, or migration change was needed: `CHANNELS`,
`CHANNEL_RELAY_GPIO`, and the action registry already carried all four
channels.

**Diagnosis (no fix applied, owner decision pending):** Three independent
defects. (1) Channels 3/4 were never blocked in code — the model inferred the
"pots 1 and 2 only" limit from the legacy `water_plants` tool, and the
deterministic router could not correct it because `water` does not match
"watering" and ASR had turned "pump" into "trump"/"comes". (2) `run_pump`
channel 2 (948a46f2) really was published at 15:30:02 and honestly timed out —
`quad_pump_canonical` had fired its last will at 15:29:47, fifteen seconds
before dispatch; `source_health_history` shows this device flapping roughly
every 15 minutes. (3) The channel-1 turn produced no action request row at all,
yet was answered "Pump 1 request is approved" — the no-tool-call guard at
`runtime.py` is gated on `_looks_like_physical_action_request`, which failed on
the mis-transcribed noun, so nothing forced a tool call or an honest denial.
Separately, `IngestionPipeline` flips any non-healthy source back to `healthy`
on `message_received` for *any* ingested event, including the broker's own
`{"online": false, "reason": "last_will"}` — which is why `sources` reports the
pump healthy while it is unreachable.

**Decisions:** Pot 3 gets no legacy pin encoding. Legacy pins 16 and 18 have no
owner-confirmed pot (`qp_config.LEGACY_PIN_TO_CHANNEL`), and the established
contract is to reject an unmapped legacy pin rather than guess it, so channel 3
is reachable only through the canonical `run_pump` path.

**Validation:** 20 action-registry tests passed in `.venv-awareness` and 15
home-automation/runtime-recovery tests in `.venv-main` (`unittest`; pytest is
not installed in either venv). All five touched Python modules compile, the
TOML parses, the registry loads and validates `{"channel": 3}`, and the noun
guard was confirmed to fire on "water the philodendron". No process restart,
GUI smoke test, live MQTT command, or full-suite run occurred.

**Limitations / outstanding:** The `plant_pot_3` row is seed-only — inserts are
`ON CONFLICT DO NOTHING` and run at startup, so the awareness backend must be
restarted before the entity exists in the live database (it is absent as of
this session). Nothing was done about the three diagnosed defects, the pump's
MQTT flapping, or the last-will health false positive; the pot-2 species is
still unrecorded.

## 2026-09-24 — SMS control channel over Twilio

**Task:** Owner asked to text the system commands (e.g. water plants, adjust
lights) through their Twilio number and get replies by text, not speech.
Calling deferred.

**Implemented:** New `butler/sms/` package. `twilio.py` holds `SmsConfig`,
stdlib Twilio signature validation, and REST send. `server.py` is a local
listener (default `127.0.0.1:8430`) meant to be exposed only via Tailscale
Funnel; it rejects bad `X-Twilio-Signature`, silently ignores non-allowlisted
senders, dedupes `MessageSid`, answers Twilio with empty TwiML at once, and
enqueues a normal `text_cmd` (session `sms:<number>`, source `sms`, SMS
formatting note as `extra_context`). Replies go out via the Messages API;
background-lane jobs get the ack and then the job result. `butler/main.py`
starts/stops it; it is disabled by default and refuses to start without public
URL, Twilio SID/token, from-number, and sender allowlist. Settings added to
`settings.env` and `.env.example`; README "SMS Control" section added. No
new agent tool, migration, or dependency; the text-agent server is unchanged
and stays private.

**Validation:** `tests/test_sms_webhook.py` (11 tests, including Twilio's
documented signature vector) plus `tests/test_text_server_phone_events.py`
(5 tests): 16 passed in `.venv-main` via `unittest`. `butler.main` imports.
No live Twilio send, Funnel exposure, restart, or full-suite run occurred.

**Limitations / outstanding:** Owner must add Twilio credentials, enable
Funnel for the node, set the Twilio messaging webhook, and restart Butler.
US A2P 10DLC (or toll-free verification) is required for outbound replies to
deliver. Sender allowlist is the authorization boundary and SMS caller ID is
spoofable. Inbound voice control is not built.

## 2026-09-25 — System overview diagram

**Task:** Owner asked for a newcomer-level SVG block diagram of the whole
system, green-CRT styled, with unfinished pieces marked or omitted.

**Implemented:** `docs/SYSTEM_OVERVIEW.svg` (self-contained; embeds an ASCII
subset of the repo's VT323 font) covering people/inputs, voice worker, main
agent, language model, outside services, MCP tools, awareness backend, home
devices, and operator tooling. Marked in progress/planned: SMS (setup pending),
barge-in, camera vision. Optional pieces drawn dotted. README links it.
Documentation only; no runtime, configuration, or test changes.

**Validation:** Rendered in headless Chromium and visually checked for overlap.
No tests run (none affected).
