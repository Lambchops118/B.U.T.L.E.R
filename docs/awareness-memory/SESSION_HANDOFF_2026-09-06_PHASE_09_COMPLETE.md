# Session Handoff — 2026-09-06 — Phase 9A–9D

Session goal:
  Complete the proactive briefing plan. Owner explicitly authorized continuation
  through 9D without further pauses except for critical reasons, requesting
  efficient implementation/testing. No subagents were used.

Current phase:
  Phase 9A–9D implementation complete; deployment/production acceptance pending.
  Earlier 9A handoff remains historical; its authorization pause is resolved.

Bounded task completed:
  9A: deterministic bounded candidates, six categories, temporal/source/query
  provenance, SQL novelty, first-run/delivery-derived windows. Extended to
  exclude confirmed items across kinds and apply structured preferences.
  9B: daily host-local schedule and stored arrival transitions, idempotent
  outbox keys, restart recovery within configured windows, hard per-batch cap,
  durable critical continuations, quiet-hours deferral, receipt/attention
  bookkeeping, and truthful failures. Ordinary notifications keep their own
  queued alerts/reminders; no second notification egress was created.
  9C: separate filtered outbox worker, bounded local Ollama ranking, frozen
  selection before delivery, strict supplied-id validation, critical overrides,
  source-only output text, prompt/model/selection provenance, and deterministic
  fallback. The model decides neither the moment nor detection nor severity.
  9D: explicit dismissal/interest/neutral feedback using existing memory writes,
  structured exact-key retrieval, filtering before prompt and before delivery,
  and bearer-gated API/MCP operations. Critical items cannot be dismissed.

Files added (including 9A):
  talos/awareness/context/briefing.py
  talos/awareness/history/briefing.py
  talos/awareness/briefing/__init__.py
  talos/awareness/briefing/service.py
  talos/awareness/briefing/worker.py
  talos/awareness/briefing/selection.py
  talos/awareness/briefing/feedback.py
  talos/awareness/api/routes/briefing.py
  tests/test_awareness_briefing_unit.py
  tests/test_awareness_briefing_integration.py
  tests/test_awareness_briefing_selection.py
  tests/test_awareness_briefing_delivery.py
  docs/awareness-memory/SESSION_HANDOFF_2026-09-06_PHASE_09A.md
  docs/awareness-memory/SESSION_HANDOFF_2026-09-06_PHASE_09_COMPLETE.md

Files modified:
  talos/awareness/config.py
  talos/awareness/outbox/worker.py
  talos/awareness/api/app.py
  talos/awareness/api/routes/health.py
  talos/awareness/api/routes/context.py
  talos/mcp_servers/providers/awareness.py
  tests/test_awareness_client_and_provider.py
  talos/awareness/README.md
  docs/awareness-memory/DECISIONS.md
  docs/awareness-memory/OPEN_QUESTIONS.md
  docs/awareness-memory/IMPLEMENTATION_STATUS.md
  docs/awareness-memory/README.md

Migrations added:
  None. Existing outbox, notification_deliveries, attention_items, and memories
  express preparation, receipts/provenance, status, and preferences. Receipt
  identity remains after completed-outbox retention; models are unchanged and
  the existing migration/autogenerate test passes.

Decisions made:
  ADR-033/034 (9A), ADR-035 (dedicated outbox/ledger), ADR-036 (critical batches
  and honest confirmation), ADR-037 (bounded ranking and durable preferences).
  OQ-M/N resolved. OQ-O records production acceptance still outstanding.

Assumptions confirmed or changed:
  Last-window cursor now reads window.end from a confirmed receipt, with older
  timestamp fallback. This derives the cursor from delivery evidence while
  avoiding a gap for events arriving during selection/quiet-hour delay.
  Delivery cap means per batch; critical overflow survives as continuation work.
  Model phrasing is deliberately excluded because validating arbitrary prose
  against facts cannot be guaranteed by an id guard alone.
  The existing /speak adapter still performs downstream LLM phrasing. Its
  acceptance is not evidence of audible speech; GUI is deterministic display.

Tests run:
  Focused briefing discovery during implementation: 31 tests passed.
  Adjacent alerts/config/health/client modules: 28/30 initially passed, with
  two MCP import errors from missing pywintypes initialization. Using main-site
  .pth initialization fixed the environment; the seven MCP/client tests passed.
  First expanded discovery: 194 tests, 185 passed, five failures, three errors,
  one skip. One new API test assumed every FastAPI route had .path; fixed to
  inspect OpenAPI paths. The seven other failures/errors came from provider
  import loading the real API token into the test process, while old tests
  expect unauthenticated mode. No auth checks were weakened. Preloading the
  provider and clearing that token only in the test subprocess resolves this.
  Targeted rerun of affected briefing/presence/hardening/context: 21 passed.
  Final full awareness discovery: 195 total, 194 passed, one skip, no failures.
  All 19 changed Python modules/test files compile; git diff --check passes.

Exact final PowerShell command:

```powershell
& 'C:/Users/aljac/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -c "import sys,site,os,unittest;sys.path.insert(0,'.venv-awareness/Lib/site-packages');site.addsitedir('.venv-main/Lib/site-packages');import talos.mcp_servers.providers.awareness;os.environ.pop('TALOS_AWARENESS_API_TOKEN',None);r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('tests',pattern='test_awareness_*.py'));sys.exit(not r.wasSuccessful())"
```

Tests passed:
  All 35 briefing tests (14 assembly unit, one assembly SQL integration,
  12 selection/feedback unit, eight delivery/API integration).
  Coverage: category/provenance/bounds/window/known pooled SQL novelty; empty
  silence; arrival transition vs repeated presence; schedule restart idempotency;
  configured quiet-hours deferral without failed attempts; invalid/duplicate
  ids, extra model prose, critical omission and capped overflow; timeout and
  unreachable loopback Ollama fallback; frozen retry/receipt dedup across kinds;
  failed delivery not marked; feedback supersession/recheck; API auth/strictness
  and absence of a trigger route; separate worker claims; cancellation releasing
  the session lock. Existing adjacent tests, including migrations, pass in the
  final suite. MCP exact-registration expectation includes the two new tools
  and the previously implemented propose_memory_candidate tool.

Tests failed:
  None in final verification. Intermediate environment/test failures above were
  resolved. One existing test-Mosquitto-dependent ingestion test skips cleanly.

Commands not run:
  Live morning/arrival speech, production Ollama interruption, real playback
  acknowledgement, voice/main-agent functional session, and voice latency
  benchmark. No dependency install, production restart, or live setting edit.

Known limitations:
  Proactive delivery defaults off. Enable TALOS_AWARENESS_BRIEFING_ENABLED=1
  on restart; defaults are 08:00 host local, arrival enabled, cap 3, voice channel.
  Optional ranking defaults off. It uses configured CHAT_MODEL on loopback
  Ollama when enabled, otherwise explicit deterministic fallback.
  Adapter acceptance is the delivery boundary. Existing /speak phrases through
  the reply system; GUI avoids that model dependency. A crash/timeout after
  enqueue but before receipt commit can duplicate transport because existing
  adapters have no end-to-end idempotency/playback acknowledgement. Confirmed
  committed receipts prevent subsequent candidate re-offering; exactly-once
  speech is not claimed. Critical query overflow still fails assembly rather
  than silently truncating critical context; ordinary alerting is independent.
  Novelty remains unscored with missing/constant/nonfinite/over-bound baselines;
  aggregate lag limits coverage. No first-ever claim from bounded history.
  Presence is single-owner and interaction-based, not proof of physical arrival.
  Scheduled catch-up is today only; arrivals have a bounded catch-up window.

Latency comparison:
  Previous reference p50 1238 ms / p95 2541 ms. No new comparable measurement;
  latency acceptance remains unverified. No new model call in ingestion/replies,
  and no router/voice worker changes. Two MCP tool definitions were added;
  shared local inference resources can contend, so deployment measurement is
  still needed even with separate outbox task scheduling.

Security implications:
  No remote trigger endpoint. Feedback and receipt API routes use configured
  bearer auth. Model requests use only loopback Ollama, no proxy/redirects,
  bounded prompts/timeouts/output. Model text never drives physical state,
  detection, alerts, or delivered candidate wording. Feedback reads exact
  structured normal/personal active memories only; restricted memory statements
  and transcripts are not read. No new credentials or external integrations.

Deployment implications:
  No migration. Opt-in settings and their defaults are documented in the
  subsystem README. No existing live settings were changed. Worker state is
  exposed in health/metrics, receipts in GET /briefings, and feedback in
  POST /briefings/feedback plus the two narrow MCP tools. Do not also enable
  the old commented-out morning_report_job schedule.

Unresolved questions:
  OQ-O: production speech/arrival/feedback acceptance and voice latency evidence.
  Earlier unrelated voice/hardware/security questions remain unchanged.

Current repository state:
  Code/test/documentation edits remain uncommitted. Owner-provided Phase 9 plan,
  documentation-index edits, .claude directory, and independently changing live
  telemetry logs were preserved. No production database or permanent environment
  changes were made; integration tests used migrated scratch databases.

Next permitted task:
  Review and opt-in rollout/acceptance of completed Phase 9. No next phase is
  inferred or started. Live voice acceptance should be an owner-visible session.

Required reading for next session:
  This handoff, latest status, Phase 9 README section, ADR-035–037, OQ-O,
  briefing worker/service/selection/feedback, and existing notification adapters.

Explicit stop point:
  Stop at 9D. No unsorted/unmodeled data, finance poller/external integration,
  transcript capture, or conversational/voice behavior changes.
