# Session Handoff — Local Debug Dashboard

Session goal: Build an expandable, barebones web debug screen for interaction I/O, system health, CPU/GPU data, and available audio metrics, with minimal impact on the running system.

Current phase: Post-Phase-8 bounded system tooling task; awareness-memory phases 0-8 remain complete.

Bounded task completed: Added a standalone local read-only debug dashboard with Interaction I/O, System Health, Live Audio, and Raw Events tabs. It polls bounded snapshots and uses existing artifacts/probes only.

Files added: `talos/debug_dashboard/__init__.py`, `talos/debug_dashboard/__main__.py`, `talos/debug_dashboard/server.py`, `talos/debug_dashboard/static/index.html`, `talos/debug_dashboard/static/styles.css`, `talos/debug_dashboard/static/app.js`, `tests/test_debug_dashboard.py`, and this handoff.

Files modified: `README.md`, `docs/awareness-memory/DECISIONS.md`, `docs/awareness-memory/OPEN_QUESTIONS.md`, and `docs/awareness-memory/IMPLEMENTATION_STATUS.md`.

Migrations added: None.

Decisions made: ADR-026 records the standalone read-only loopback service boundary. ADR-027 records that system hardware metrics are remote-only because the console runs on a different computer. The dashboard does not join the agent/audio hot paths and refuses non-loopback binding unless the operator explicitly supplies `--allow-remote`.

Assumptions confirmed or changed: Existing pipeline JSONL contains timings/configuration only; voice benchmark CSV contains the raw transcript, command, response preview, and per-turn metrics; the conversation SQLite store contains full persisted user/assistant text. Exact prompt content, tool names/arguments, raw PCM, and continuous idle audio samples are not currently available.

Tests run: `python -m unittest tests.test_debug_dashboard tests.test_pipeline_telemetry tests.test_voice_benchmarking tests.test_barge_in_observability tests.test_text_server`; targeted `py_compile`; bundled-Node `--check talos/debug_dashboard/static/app.js`; local HTTP page/snapshot test within the unit suite; `git diff --check`.

Tests passed: 19 focused dashboard/existing-telemetry tests, including 8 debug dashboard data/HTTP/remote-metrics tests; targeted Python syntax compilation; JavaScript syntax check; local HTTP page/snapshot request; diff whitespace check.

Tests failed: None.

Commands not run: Full repository test suite; awareness health tests (the main interpreter lacks its separate SQLAlchemy dependency); live full TALOS stack; browser visual/interaction QA; owner room/audio corpus; non-loopback exposure.

Known limitations: Existing audio sources update after utterance/barge-in snapshots, not per frame. Exact prompts and detailed tool calls are unavailable. Conversation messages and pipeline request IDs cannot always be correlated because current memory metadata does not store request IDs. Service health is probe-based, not launcher-process authority. Remote hardware cards remain `NOT_CONFIGURED` until a TALOS-host metrics endpoint is selected and supplied through `TALOS_DEBUG_SYSTEM_METRICS_URL`.

Security implications: The page exposes private conversation content. It binds to loopback by default, has no authentication, is read-only, sets a restrictive content security policy, and requires an explicit `--allow-remote` override for non-loopback binding. It never reads or records raw PCM.

Deployment implications: Start separately with `.venv-main\Scripts\python.exe -m talos.debug_dashboard`; default URL is `http://127.0.0.1:8787`. The launcher was intentionally not changed.

Unresolved questions: OQ-K defines the owner/privacy decisions needed before adding new content-bearing or hot-path telemetry producers. OQ-L covers selection/authentication/deployment of the remote TALOS-host metrics endpoint. OQ-I/OQ-J room-corpus gates remain unchanged.

Current repository state: Debug dashboard implementation and focused validation complete; no commit created. Pre-existing repository state was clean before this bounded task.

Next permitted task: Owner review of the page and choice of which missing feeds to instrument first. Any exact prompt/tool/audio producer is a separate bounded task.

Required reading for next session: `README.md` Local Debug Dashboard section, `talos/debug_dashboard/server.py`, `talos/debug_dashboard/static/app.js`, ADR-026, and OQ-K.

Explicit stop point: Do not wire new hot-path telemetry, capture raw room audio, add remote exposure/authentication, or modify launcher supervision without owner authorization.
