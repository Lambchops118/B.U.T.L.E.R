# Session Handoff — Launcher logs and LLM I/O tabs

Session goal: Put ordinary launcher logs in a separate tab and add a third tab
that shows exactly what is sent to and received from the LLM for prompt-breakage
debugging.

Current phase: Post-Phase-9 bounded launcher/debug enhancement; no later phase
started.

Bounded task completed: Replaced the launcher's single-page layout with
Launcher, Logs, and LLM I/O tabs. Launcher-managed main-agent children now emit
structured LLM boundary records. Chat Completions capture includes the final
request dict after tool conversion, raw SDK response chunks, assembled text,
tool calls, finish reason, and telemetry. Warmup and Responses API traffic are
labeled separately. The GUI routes valid main-process records only to the LLM
tab and leaves malformed or non-main records visible in ordinary logs.
Sent records render in blue/cyan and received records render in green, with
distinct heading and payload tags.

Files added: `talos/llm_debug.py`, `tests/test_launcher_llm_debug.py`, and this
handoff.

Files modified: `talos/voice/backends/llm_openai_compat.py`,
`talos/agent/runtime.py`, `talos/launcher/core.py`, `talos/launcher/gui.py`,
`tests/test_llm_openai_compat.py`, `README.md`, `DECISIONS.md`,
`OPEN_QUESTIONS.md`, and `IMPLEMENTATION_STATUS.md`.

Migrations added: None.

Decisions made: ADR-042. Exact LLM capture is enabled only for a
launcher-managed main process, travels through the existing private stdout
pipe, is not separately persisted or network-served, and is capped at the
latest 5,000,000 displayed characters. Capture errors fail open for inference.

Assumptions confirmed or changed: The active conversational path uses the
OpenAI-compatible Chat Completions backend in the main agent. The same process
also retains legacy Responses API calls, including model request routing, so
both seams are instrumented. The voice and Discord frontends reach the main
agent rather than independently calling the conversational LLM.

Tests run: Python compilation for the five changed runtime modules and two
focused test modules. `unittest` for `tests.test_llm_openai_compat`,
`tests.test_launcher_llm_debug`, and `tests.test_launcher_microphone_profiles`.
Tests used Inkscape Python 3.12.12 with `.venv-main/Lib/site-packages` because
the tracked `.venv-main` interpreter target is missing.

Tests passed: Compilation passed. 33 focused unit tests passed.

Tests failed: None in the successful focused run. Before that run, the direct
`.venv-main` command failed before test execution because its configured Python
3.12 executable does not exist. A dependency probe under the fallback runtime
also reported its binary `pydantic_core` was unavailable, but the focused test
modules supply their existing dependency stubs and then passed.

Commands not run: Full test suite, GUI/live Tk smoke test, live LLM request,
launcher process restart, and live voice session.

Known limitations: The display is an SDK-boundary representation: it shows the
exact request object supplied to the SDK and every response chunk the SDK
exposes, not encrypted HTTP bytes. The oldest display text is discarded after
the five-million-character cap. Directly starting the main agent without the
launcher does not enable capture. A launcher-started headless process emits the
structured records to its console because no GUI consumes them.

Security implications: The LLM tab may contain private conversation history,
remembered facts, awareness context, tool arguments, and tool results. It is
local and ephemeral but remains shoulder-surfable and copyable. No secret
redaction is attempted because that would conflict with the requested exact
payload view; API transport headers/keys are not part of the SDK request object
and are not captured.

Deployment implications: Restart the launcher and launcher-managed main agent.
No setting, schema, database, network listener, or external service changes are
required.

Unresolved questions: OQ-K remains open only for generated-versus-audible
output and continuously sampled audio diagnostics. The broken `.venv-main`
interpreter should be repaired separately if owner-authorized.

Current repository state: Implementation, focused tests, operator docs, ADR,
status, and handoff are complete; pre-existing uncommitted microphone work and
runtime logs remain untouched.

Next permitted task: Owner review and live launcher/model acceptance only. Do
not add other sensitive feeds or begin another phase automatically.

Required reading for next session: `IMPLEMENTATION_STATUS.md`, ADR-042 in
`DECISIONS.md`, OQ-K in `OPEN_QUESTIONS.md`, this handoff, and the touched
launcher/LLM boundary modules.

Explicit stop point: Stop at the three-tab launcher and exact local LLM I/O
capture. Do not expose the feed over the network, persist a separate transcript,
or add audio/output telemetry without owner authorization.
