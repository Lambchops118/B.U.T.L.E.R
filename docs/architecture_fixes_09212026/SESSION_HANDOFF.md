# Session handoff — architecture documents and two bounded repairs

Session goal: create detailed future work documents, standardize future-agent
tool guidance, remove the fake lights capability, and repair multi-turn history.

Current phase: owner-assigned bounded follow-up; no new awareness phase.

Bounded task completed: all requested documentation and the two authorized code
changes. See [index](README.md) and [history repair](01_tool_history.md).

Files added: this folder's index, six original-finding documents, relocated
deep tool review, this handoff, and `docs/TOOL_IMPLEMENTATION_GUIDE.md`.

Files modified: root AGENTS.md; architecture overview and former tool-review
pointer; awareness status/decisions/questions; `talos/agent/runtime.py`,
`talos/memory/store.py`, home-automation service/provider; memory, streaming,
tool-scoping, home-action and runtime-recovery test modules. Existing README
changes from the preceding consultation are preserved.

Migrations added: none. Existing message metadata stores the versioned tool
history and schema snapshots; older databases/messages remain compatible.

Decisions made: ADR-055; current schemas determine available capabilities;
history is evidence and never re-executes tools. New tool guidance is linked
from AGENTS.md. Broader architecture recommendations remain proposals.

Assumptions confirmed/changed: local inference is the deployment; AWS Polly is
the hosted speech exception. Schemas alone do not fix the spiral: actual calls
and matching results must survive too. No historical evidence was fabricated.

Tests run/passed: **160 tests, zero failures/errors/skips**, Python 3.12.5 via
`.venv/bin/python`, using unittest discovery for these exact patterns:

```text
test_memory_store.py
test_run_command_stream.py
test_history_grounding.py
test_tool_scoping.py
test_home_automation_actions.py
test_barge_in_agent_integration.py
test_agent_runtime_recovery.py
test_agent_runtime_phone_tools.py
test_agent_thinking.py
test_agent_preramp.py
test_llm_openai_compat.py
test_leaked_tool_calls.py
test_tool_argument_parsing.py
test_mcp_disable_selection.py
```

Reproduce with a single unittest TestSuite, adding
`unittest.TestLoader().discover('tests', pattern=pattern)` for each listed pattern,
then running `unittest.TextTestRunner`. Tests mock inference/providers/effects;
the backend suite does not call a live hosted API. Earlier focused runs also
passed (memory 4 then 9; streaming 12 then 14), before final additional coverage.

Tests failed during development: the first combined run ran 158 tests with zero
assertion failures and one import error: the new registration test imported the
aggregate provider package, which requires unavailable optional `kasa`. Changed
the unit test to load the actual home-automation provider alone with `runpy` and
exercise registration, avoiding unrelated hardware-provider dependencies. The
final 160-test run passed. System `python3` lacked `openai`; the existing `.venv`
had the needed test dependencies, with no installation or environment changes.

Additional checks passed: syntax compilation of all nine changed Python files;
37 local Markdown link targets across 16 documents; whitespace checks on new
documents; and `git diff --check`.

Commands not run: full repository suite, live provider enumeration, Ollama/model
requests, voice/Polly playback, PostgreSQL/hardware integration, latency benchmarks,
process restart, or deployment. No live database was edited by tests.

Known limitations: history limits can omit a whole large tool turn; results stored
are the model-visible bounded tool output. Old prose remains unverified. Current
schema snapshots are not injected as repeated prose; current definitions go in
the tools field. Legacy Responses history behavior is unchanged. Unit tests do
not establish deployed model accuracy or claim all missed calls are solved.

Security implications: tool arguments/results/schema snapshots now persist in the
local conversation database and its backups. Treat that data with the same care
as conversation history. Disabled/removed tools are not revived by snapshots.
No cloud inference or new egress is introduced.

Deployment implications: restart the deployed main agent to load the repair; no
database migration/reset or configuration update is necessary.

Unresolved questions: deployed-model acceptance and history-budget adequacy;
other architecture items remain in their individual proposed work documents.

Current repository state: preceding consultation edits and pre-existing untracked
TUI/wireframe artifacts preserved. Test-created telemetry files from this pass
were removed; no unrelated files were deleted.

Next permitted task: owner review/live acceptance of these repairs or a separately
assigned bounded work package. Required reading: this folder's index, the relevant
work document, tool implementation guide, current status, and invariants.

Explicit stop point: documentation plus the two authorized repairs complete.
Do not begin other proposed architecture changes automatically.
