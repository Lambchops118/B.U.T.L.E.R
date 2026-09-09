# Implementation Status

This file reports implementation state, not documentation availability.

## Latest bounded behavior fixes — presence, morning context, quad pump, sleep (2026-09-08)

Four owner-reported defects. Morning briefing context and the quad-pump
false-offline diagnosis were already implemented in the working tree and are
**verified** here — the live database shows `quad_pump_canonical` healthy with no
open alerts, the superseded `quad_pump_pico` (silent since 2026-08-30) reconciled
to `unknown`, and the morning weather provider returning a real observation.

Two needed completing. **Presence**: the worker-side expiry opt-out did not bind
on the read path, so every `SituationBroker`/history read still aged owner
presence at query time, and the `stale -> current` half of each expiry pair was
still spoken as "your presence was detected again" — the welcome back itself.
Reads now share `effective_state_status`, unchanged-value transitions are never
spoken (`briefing-speech-v2`), the deployed 900 s presence timer is migrated
away, and rows the old deadline left `stale` are reconciled. **Sleep mode**: the
physical display was owned by two unrelated scheduler jobs, so sleep left a lit
screen. New `talos/services/display_power.py` is driven from `sleep_mode._set`,
the single write path, so no route can separate the two. The pygame dim was
separately broken: applied on the CPU ahead of the CRT shader's 1/gamma pass, a
requested 1% reached the glass at ~18% of awake brightness. It now lives in the
shader after gamma and is verified by rendering real frames (1.1% measured
against a 1% target). ADR-044 and ADR-045.

Also repaired: `tests/test_awareness_presence_integration.py` held a corrupted
token that made the module a `SyntaxError`, so the previous session's presence
tests could not have run.

Validation: **41 focused tests passed** in `.venv-main` and **33** in
`.venv-awareness` against the live awareness Postgres; full `test_awareness*`
discovery ran 212 tests with 3 errors from `mcp` being absent in
`.venv-awareness` (those pass in `.venv-main`). All touched files compile and
`git diff --check` passes. No process restart, GUI smoke test, live display
command, or full-suite run occurred. One **pre-existing, unrelated** flake is
documented rather than fixed: an outbox test compares a database-stamped
`available_at` against a host-clock `now`, and the container clock is ~10 ms
ahead. See `SESSION_HANDOFF_2026-09-08_FOUR_BEHAVIOR_FIXES.md`; restart the
awareness backend and the main agent so the registry migrations and the
sleep/display coupling take effect, then stop at this bounded fix.

## Latest bounded launcher enhancement — persistent exact LLM I/O (2026-09-08)

The owner requested permanent capture after confirming the initial LLM I/O tab
was memory-only. Launcher-managed main agents now append every exact structured
LLM debug event to a per-run
`talos/logs/llm_io_<UTC timestamp>_<pid>.jsonl` file while continuing to send
the same event to the live color-coded tab. The directory is created lazily,
files are git-ignored because they contain sensitive prompt/tool content, and no
automatic deletion or pruning is performed. File failures remain isolated from
inference. ADR-043 supersedes ADR-042's no-file policy.

Validation: the five runtime modules and two focused test modules compile;
**34 focused tests passed**, including file-only persistence and launcher child
environment coverage. `git diff --check` passed. Testing used the available
Python 3.12.12 runtime with `.venv-main` packages because `.venv-main` still
references a missing interpreter. No live launcher restart, GUI smoke test,
model request, or full-suite run occurred. See
`SESSION_HANDOFF_2026-09-08_LLM_DEBUG_PERSISTENCE.md`; restart the launcher and
its managed main agent to begin a new transcript, then stop at this enhancement.

## Latest bounded launcher enhancement — separate logs and exact LLM I/O (2026-09-08)

The owner authorized exact prompt/response debugging in the launcher. The GUI
now has three top-level tabs: **Launcher**, **Logs**, and **LLM I/O**. A
launcher-managed main agent emits the full Chat Completions request payload and
provider response chunks/assembled completion, plus labeled warmup and legacy
Responses API traffic, through its existing private stdout pipe. The launcher
intercepts those structured records into the LLM tab instead of mixing them into
ordinary logs. No separate debug file or network endpoint is created; the GUI
retains only the latest 5,000,000 displayed characters and warns that the feed
contains sensitive conversation, memory, context, and tool data. Sent headings
and payloads are blue/cyan; received headings and payloads are green.

Validation: five changed Python modules and two focused test modules compile;
**33 focused tests passed** covering the backend, LLM debug routing, color-coded
rendering, and existing
launcher microphone behavior. The repository's `.venv-main` remains unusable
because its configured Python 3.12 executable is missing, so tests used the
available Python 3.12.12 runtime with `.venv-main` packages. No GUI smoke test,
live model request, process restart, or full-suite run occurred. ADR-042 records
the local, ephemeral sensitive-data policy and resolves the LLM-I/O portion of
OQ-K. See `SESSION_HANDOFF_2026-09-08_LAUNCHER_LLM_DEBUG.md`. Restart the launcher
and its managed main agent to use the new feed; stop at this bounded enhancement.

## Latest bounded repair — selectable Yeti/ReSpeaker capture (2026-09-08)

The owner authorized the repair identified by the XVF3800 diagnosis. The
launcher now has a persisted **Room microphone** dropdown and a headless
`--microphone {respeaker,yeti}` override. Both profiles select a named device
instead of trusting Windows's mutable default. ReSpeaker opens explicit 16 kHz
stereo and extracts USB channel 2, the documented auto-selected ASR beam; its
SpeechRecognition threshold retains the measured one-second room calibration.
Yeti retains its fixed threshold and the existing pinned Windows AEC contract.
ReSpeaker barge-in and experimental idle VAD fail closed because Talos renders
through BenQ and the XVF3800 hardware AEC has no validated far-end reference.

Validation: **37 distinct focused tests passed** across the two new profile
suites and the existing Windows audio, duplex, VAD, and faster-whisper suites;
the five launcher tests also passed a second time in the launcher's actual
`.venv-main`. All touched Python files compile, `git diff --check` passes, launcher help exposes both
choices, the saved launcher profile is `respeaker`, and a non-recording live
PortAudio check resolves and opens the connected two-channel MME device at
index 1 with the 16 kHz/channel 2 contract. No PCM was read; no live
transcription, process restart, firmware
write, or full-suite run occurred. ADR-041 records the capture contract and OQ-P
retains the owner-visible accuracy acceptance test. See
`SESSION_HANDOFF_2026-09-08_MICROPHONE_PROFILES.md`. Restart the voice worker (or
the launcher stack) to apply the selected profile; stop at this repair.

## Diagnostic task — ReSpeaker XVF3800 STT regression (2026-09-08)

The reported microphone-switch regression is real and is primarily an
integration/configuration mismatch, not evidence of a failed ReSpeaker. The
current Windows default capture endpoint is the XVF3800 (`...5dda5d51...`), but
`settings.env` still pins the former Yeti endpoint (`...1783577d...`). The voice
worker therefore fails its exact AEC/duplex endpoint check and falls back to
generic SpeechRecognition capture; current telemetry confirms no AEC residual
on that fallback. The fallback opens one mono MME channel at the device default
44.1 kHz and uses Yeti-era fixed energy thresholds, while the XVF3800 exposes
two semantically different channels and reserves its right channel for the
auto-selected ASR beam. Read-only USB control found normal stock routing and
processing on firmware 2.0.6: left `(8,0)`, right `(7,3)`, ASR/AGC enabled,
16-bit USB, and expected gains. No room PCM was recorded and no code,
configuration, device state, firmware, or running process was changed. See
`SESSION_HANDOFF_2026-09-08_RESPEAKER_STT_DIAGNOSIS.md`; OQ-P tracks the
owner-visible channel/threshold acceptance comparison. Stop at diagnosis until
the owner authorizes a capture-path repair and visible A/B corpus.

## Documentation task — awareness block diagram (2026-09-08)

Added `AWARENESS_BLOCK_DIAGRAM.md` with four linked views covering ingestion,
conversational retrieval and memory, proactive delivery, and physical-action
safety. Checked against current composition, ingestion, context and selection
code plus the latest status and subsystem contracts. Runtime state is unchanged;
no runtime tests or live acceptance checks were run. Session handoff:
`SESSION_HANDOFF_2026-09-08_BLOCK_DIAGRAM.md`. Stop at this explanation task.

## Latest bounded repair — announcement recall (2026-09-07)

Owner authorized saving proactive output for later voice recall. Briefing and
ordinary awareness notification receipts now store exact rendered title/body
with playback explicitly unconfirmed. Existing source IDs remain attached.
Situation context includes up to three accepted voice announcements from 24 hours,
subject to its existing budget and alert priorities. `/briefings` and its MCP tool
return stored briefing wording, including failed status when applicable. Legacy
receipts are not backfilled with invented text. No migration or model call.
Validation: 16 focused delivery/context/alert tests passed; see
`SESSION_HANDOFF_2026-09-07_ANNOUNCEMENT_RECALL.md`. Restart awareness backend and
the awareness MCP provider/main agent to load code and tool guidance. Live voice
recall and latency remain untested. Stop at this bounded repair (ADR-040).

## Previous bounded repair — concise briefing speech (2026-09-06)

Owner reported diagnostic logs being spoken after the announcement routing fix.
Briefing candidates now retain diagnostic `text` separately from `spoken_text`.
The worker renders short factual sentences, suppresses presence metadata, and
handles legacy prepared payloads without reading their raw diagnostic strings.
Selection, critical protection, triggers and delivery bookkeeping remain intact.
ADR-039; no migrations or additional model calls. **46 focused tests passed**;
five changed Python files compile. Initial unittest discovery failed before
running tests (tests is a namespace package); explicit module loading succeeded.
Full suite and live audio/latency not run. Restart awareness backend to apply.
See `SESSION_HANDOFF_2026-09-06_BRIEFING_SPEECH.md`. Stop at this repair.

## Previous bounded repair — spoken announcements (2026-09-06)

The owner reported both injection tests speaking "I can do that. I'm working on
it now." Root cause: `/speak` wrapped output as `voice_cmd`, causing background
request classification and false human activity signals. Fixed with a typed
announcement message routed directly through the existing speech helper.
User-command routing and the voice worker are unchanged. ADR-038 records this
owner-requested repair beyond the original Phase 9 voice-path exclusion.

Validation: **18 focused tests passed**, including HTTP-to-router exact-text,
no-job/no-model/no-presence regression coverage and existing voice/phone/job
routing tests. Five touched Python files compile; whitespace checks pass.
No live process restart, audio playback, latency benchmark, or full-suite rerun.
Restart the main agent/text server before repeating the arrival test. Enqueue
still is not playback confirmation. See
`SESSION_HANDOFF_2026-09-06_ANNOUNCEMENT_FIX.md`. Stop at this bounded repair.

## Previous snapshot — Phase 9A–9D implementation complete (2026-09-06)

Owner continuation authorization: **"continue on through 9D without pausing
unless theres a critical reason to ask for input."** This authorized all
remaining sub-phases and resolved OQ-M. No further sub-phase pause was taken.

| Field | Current value |
|---|---|
| Current phase | Phase 9A–9D implemented; production acceptance/review pending. |
| Completed | Assembler; idempotent daily/arrival moments; dedicated briefing outbox lane; capped delivery with durable critical-overflow continuations; confirmed receipt and attention bookkeeping; cross-kind item exclusion; optional bounded local model ranking with critical overrides/fallback; durable dismissal/interest/neutral feedback and API/MCP tools. |
| Migration | None. Outbox JSON stores frozen selections; existing notification ledger stores receipts/query-selection provenance; existing memories store feedback. Schema/autogenerate test passes unchanged. |
| Configuration | Opt-in: `TALOS_AWARENESS_BRIEFING_ENABLED=1`. Default schedule 08:00 host local, arrival enabled, cap 3, channel voice. Optional model ranking defaults off; enabling it requires configured `CHAT_MODEL` and loopback Ollama. No live settings were changed. |
| Files | 19 changed/new Python files across briefing service/worker/selection/feedback, assembler/history, config, API/health/capabilities, outbox, MCP, and tests. Full inventory in `SESSION_HANDOFF_2026-09-06_PHASE_09_COMPLETE.md`. |
| Tests | Final awareness discovery: **194 passed / 195 total; one existing broker-dependent skip; zero failures/errors**. All 35 briefing tests pass. Seven MCP/client tests pass. All 19 changed Python files compile; `git diff --check` passes. |
| Environment | Bundled Python 3.12.14 with awareness packages and main-site `.pth` initialization. Preload MCP provider then remove the inherited API token in the test subprocess only, because existing unauthenticated-mode tests require it unset. Auth tests still verify configured-token enforcement. Exact command and intermediate failures in handoff. No package installation or persistent environment changes. |
| Decisions | ADR-035: independent outbox lane and existing durable ledgers. ADR-036: critical overflow uses individually capped batches, source text only, and honest adapter receipts. ADR-037: exact structured feedback before prompt, critical protection, and local bounded ranking. OQ-N resolved at implementation level. |
| Latency | No new measured voice values. Prior baseline p50 1238 ms / p95 2541 ms remains reference only. No new hot-path model call; router/voice worker unchanged. Two new MCP tool definitions and possible local inference contention mean a live comparison remains necessary. |
| Limitations | Existing `/speak` acknowledges enqueue and performs downstream LLM phrasing. No exactly-once or playback guarantee; an ambiguous enqueue/receipt crash can duplicate transport. GUI is the existing deterministic model-independent display lane. Aggregate lag/missing or constant baselines remain explicit. Presence is interaction-based and single-owner. |
| Next proposed work | Owner rollout/acceptance: configure opt-in settings, verify scheduled/arrival delivery and user feedback on the deployed host, and compare voice latency. No future phase or external integration is started. |
| Stop | Stop at the completed Phase 9 implementation boundary. Unsorted/unmodeled data, finance/external integrations, transcript capture, and reply/voice behavior changes remain excluded. |

## Historical sub-phase snapshot — Phase 9A (2026-09-06)

| Field | Current value |
|---|---|
| Current phase | Phase 9A — deterministic briefing assembler — implemented; owner review pending. Phases 9B, 9C, and 9D are unstarted. |
| Authorization | Owner requested implementation of `PHASE_09_PROACTIVE_BRIEFING.md`. Its explicit sub-phase gate is preserved: clarification whether this authorizes all four was requested and remains unanswered; this session implements 9A only. |
| Completed work | Read-only assembler with six candidate categories, repeatable-read snapshot, versioned query provenance, delivery-derived/explicit first-run windows, count/time bounds and truncation audit, SQL pooled novelty scores, and critical-overflow/query-failure rejection. |
| Files | Added `talos/awareness/context/briefing.py`, `talos/awareness/history/briefing.py`, two briefing test modules, and `SESSION_HANDOFF_2026-09-06_PHASE_09A.md`; updated config, subsystem README, decisions, questions, and this status. |
| Migration | None. Existing `notification_deliveries` can supply the read-side watermark; no current producer writes `metadata.briefing_kind`. No triggers, API route, model call, or delivery writes were added. |
| Decisions/questions | ADR-033/034; OQ-M (authorization), OQ-N (critical overflow under the delivery cap and enqueue-vs-playback evidence). Existing notification handling already marks attention delivered, contrary to the plan's stale statement. |
| Tests | 14 new tests pass, including live PostgreSQL/TimescaleDB scratch-database coverage. Full awareness discovery: **171 passed / 174 total**, two pre-existing missing-`mcp` errors, one broker-dependent skip. Baseline: 157 passed / 160 total, same errors/skip. Existing migration/autogenerate test passes in that suite. Compilation of five touched Python files and `git diff --check` pass. |
| Test environment | `.venv-awareness/Scripts/python.exe` could not start its configured Python executable. Tests ran using bundled Python 3.12.14 with existing `.venv-awareness/Lib/site-packages` inserted into `sys.path`; exact commands in the handoff. No environment/package changes. |
| Latency | Not measured. Existing recorded baseline is p50 1238 ms / p95 2541 ms; no new comparable voice evidence. No conversational, voice, scheduler, ingestion, or outbox code was modified. |
| Limitations | Candidate generation only. Missing/constant/over-bound baselines are unscored; aggregate refresh lag can reduce novelty coverage. Source attribution can be unknown for derived records. Full cross-kind item deduplication, scheduled/arrival triggers, model guard/fallback, and feedback are 9B–9D work. |
| Next permitted task | Review 9A; explicitly authorize 9B (or all remaining sub-phases) and resolve OQ-N before delivery implementation. |
| Explicit stop | Stop at 9A. Do not begin 9B–9D without authorization, unsorted/unmodeled data, finance/external integrations, transcript capture, or conversational-path model work. |

## Previous snapshot — human-context follow-on (historical)

| Field | Current value |
|---|---|
| Current phase | Phase 8 — Retention, Security, and Hardening — **complete**. All phases 0-8 implemented. |
| Phase state | Subsystem implementation complete on `memory_system_3_07152026` (owner authorized Phase 8 with local-backup defaults, 2026-07-16) |
| Last completed phase | Phase 8 (2026-07-16) |
| Current bounded task | Human context and internal ingestion — **implementation complete; owner review pending** (2026-09-06). Presence, bounded interaction facts, and agent job/tool outcomes now enter the subsystem through the normal pipeline; `POST /ingest` accepts internal and manual messages and returns the pipeline disposition synchronously; the situation snapshot reports presence and honors `interruptibility`/`conversation_relevance`. No migration was needed — the schema had already anticipated all of it. See `SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md`. Previous task: local debug dashboard — **implementation complete; owner review pending** (2026-08-09). A standalone loopback web page now shows bounded existing interaction I/O, pipeline telemetry, service health, remote-only hardware metrics, voice RMS, and barge-in summaries without joining the main/audio hot paths. Expanded detail rows persist across polling. Wake latency/accuracy recovery remains complete with the owner-run idle-VAD corpus pending. |
| Completed items | Phases 0-7 (see git history and `talos/awareness/README.md`); Phase 8: retention service (dry-run plan, bounded resumable batched deletion, aggregate-before-delete via cagg refresh, open-alert/evidence/active-memory protections), memory consolidation (incident summaries with derived_from links, weak-inference decay, user-evidence exemption), artifact store (generated rooted paths, SHA-256, table `artifacts`, migration `3337c328523b`), local backups (pg_dump in-container + config snapshot + 14-day pruning; **restore tested live: 27/27 tables**), write-auth on all mutating endpoints (actions fail-closed; others bearer-gated when `TALOS_AWARENESS_API_TOKEN` set), `/metrics` (counters/backlog/disk/last-backup), benchmark utility (**118 ev/s, p50 7.5 ms, p95 14.8 ms, 0 drops**), broker hardening plan (`BROKER_HARDENING_PLAN.md`, owner-executed), CLI: `retention`/`consolidate`/`backup [--verify]` |
| Active work | None — human-context boundary reached; unsorted/unmodeled-data handling deliberately deferred and unstarted. Production idle segmentation is restored; experimental idle VAD remains disabled. |
| Blocked items | Idle-VAD enablement needs an owner-visible "Butler"/pause/noise corpus (OQ-J). Barge-in production acceptance still needs the Phase F room corpus and eight-hour soak (OQ-I). Raw room PCM cannot be collected silently. |
| Decisions made | ADR-001..032 in `DECISIONS.md`; ADR-028..031 cover recording presence/interaction/agent outcomes without transcripts, `POST /ingest` running the identical pipeline rather than a bypass, transport pinning so internal sources cannot be forged over the LAN broker, and relevance ordering that can never reorder across priority bands; ADR-026 keeps the debug console standalone/read-only/bounded, and ADR-027 forbids substituting console-local hardware metrics for the remote TALOS host. |
| Assumptions confirmed | `DISCOVERY.md` §12/§15; open-question table in `OPEN_QUESTIONS.md`; quad-pump pin numbering, relay direction/polarity, concurrency and run-time limits, pot mapping, and fuse policy confirmed by the owner in chat 2026-07-26 |
| Open questions | OQ-B, OQ-C (fan only), OQ-D, OQ-E, OQ-F, OQ-I (barge-in room corpus/soak), OQ-J (idle-VAD wake/pause/noise corpus), OQ-K (policy for new sensitive debug feeds), and OQ-L (remote system-host metrics endpoint/auth). OQ-H is resolved by ADR-024 and live deployed-host evidence. |
| Tests last run | 2026-09-06: `.venv-awareness` unittest discovery over `tests/test_awareness_*.py` — **157 passed of 160**, against a baseline of 141/144 by the same command before this session, so the 16 new tests pass and nothing regressed. 2 errors are pre-existing and unrelated (`test_awareness_client_and_provider` cannot import `mcp` in that venv; it belongs to `.venv-main`), 1 skip is the test Mosquitto not running. `py_compile` passes on all touched modules, and every main-agent module changed here (`awareness_signals`, `jobs`, `router`, `agent/runtime`) imports cleanly in `.venv-main`, with `voice/agent` importing cleanly in `.venv-voice`. **Not run:** the main-venv suite — `.venv-main` lacks sqlalchemy/fastapi/mcp — so the router, jobs, runtime, voice, and MCP-provider edits are syntax-checked only and have not executed in a live agent process. No live voice session and no run against the production broker. Earlier 2026-08-09 dashboard/voice results are unchanged. |
| Known failures | Live "Butler" recall, false wakes, real command-pause behavior, and endpoint p95 for the independent idle VAD are unmeasured because they require an owner-visible room session. Incremental faster-whisper decoding is not implemented because the backend is finished-utterance/batch and speculative chunk decoding would reintroduce redundant passes without accuracy evidence. Existing Phase F barge-in live limitations remain. Additionally, interaction events carry `entity_ids` only when the caller knows them and the router currently cannot attribute an utterance to an entity, so conversation relevance usually scores zero in practice; the snapshot reports this in its `limitations` rather than implying a judgment was made. User location within the home remains unmodeled and presence is single-occupant. |
| Files recently modified | New `talos/awareness/api/routes/ingest.py`, `talos/services/awareness_signals.py`, two test modules, and the 2026-09-06 handoff. Modified: awareness registry bootstrap/sources, ingestion pipeline/service, API app, context broker, alerts service, rules engine + `rules.toml` (policy version 1 → 2), reminders worker, config, README; main-agent `router.py`, `jobs.py`, `agent/runtime.py`, `voice/agent.py`, `mcp_servers/providers/awareness.py`; `settings.env`. Earlier debug-dashboard and voice files remain as recorded. |
| Next permitted task | Owner review of the human-context work, and authorization of a Phase 9 sub-phase. **Phase 9 (Proactive Briefing and Model-Selected Salience) is now specified** in `phases/PHASE_09_PROACTIVE_BRIEFING.md` and is NOT started: it separates the deterministic *moment* from model-selected *content*, and is split into 9A (assembler), 9B (triggers and delivery bookkeeping), 9C (model selection), 9D (feedback loop), each independently authorizable. Otherwise: Then, if approved, either (a) run the main-venv suite and a live agent/voice session to verify signal emission end to end, or (b) populate interaction `entity_ids` so conversation relevance does real work rather than scoring zero. Separately, the earlier owner-visible idle wake/pause/noise corpus and Phase F barge-in corpus/soak remain outstanding before voice rollout claims. |
| Required reading | `SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md`, the README "Human context" section, `talos/services/awareness_signals.py`, `talos/awareness/api/routes/ingest.py`, `talos/awareness/context/broker.py`, and ADR-028..031. For the debug page retain the 2026-08-09 handoff; for voice follow-up retain the wake-latency handoff and barge-in plan reading list. |
| Explicit stop condition | Human-context task is complete. **Do not begin unsorted/unmodeled-data handling** (observations table, promotion by repetition, salience decay) — it was explicitly excluded and needs its own owner decision. Do not add utterance-text capture, remote exposure of `/ingest`, or escalation rules for tool failures without authorization. Prior stop conditions still hold: no prompt/tool/audio hot-path capture, no silent room recording, do not set `TALOS_IDLE_VAD_CORPUS_ACCEPTED=1` or enable unaccepted barge-in. |
| Documentation follow-up (2026-07-16) | Added `like_im_a_child_or_golden_retriever.md`, a plain-language intern quick start covering immediate operation, TALOS integration, maintenance, code paths, tests, safety invariants, limitations, and troubleshooting. Linked it from this documentation index. Validation: 96 awareness unit tests and 3 main-agent home-action tests pass; CLI help, relative-link targets, and `git diff --check` pass. Runtime, schemas, migrations, decisions, and open questions are unchanged. |

Do not infer implementation progress from the presence of specification or launcher files. Session handoffs live in dated files derived from `SESSION_HANDOFF_TEMPLATE.md` (latest: `SESSION_HANDOFF_2026-09-06_HUMAN_CONTEXT.md`).
