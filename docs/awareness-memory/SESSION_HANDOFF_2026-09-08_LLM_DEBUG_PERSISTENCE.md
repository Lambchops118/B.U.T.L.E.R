# Session Handoff — Persistent LLM debug transcripts

Session goal: Determine whether exact LLM I/O was permanently logged and, when
it was not, create a persistent location under the logs folder.

Current phase: Post-Phase-9 bounded launcher/debug enhancement; no later phase
started.

Bounded task completed: Confirmed the existing feed was stdout/GUI memory only.
Added best-effort per-run JSONL persistence at
`talos/logs/llm_io_<UTC timestamp>_<pid>.jsonl`. Launcher-managed main-agent
processes enable both the existing stdout feed and the new file sink. The GUI
identifies the saved-file pattern. Exact transcript files are git-ignored.

Files added: This handoff. Runtime transcript files are created lazily on the
first model-boundary event after launcher restart.

Files modified: `talos/llm_debug.py`, `talos/launcher/core.py`,
`talos/launcher/gui.py`, `tests/test_launcher_llm_debug.py`, `.gitignore`,
`README.md`, `DECISIONS.md`, `OPEN_QUESTIONS.md`, and
`IMPLEMENTATION_STATUS.md`.

Migrations added: None.

Decisions made: ADR-043 supersedes ADR-042 only where it prohibited a
persistent transcript. Per-run files preserve unredacted events, have no
automatic retention/pruning, and fail open for inference.

Assumptions confirmed or changed: Repository search and supervisor inspection
confirmed no existing file sink consumed the `TALOS_LLM_IO` records. Existing
pipeline telemetry intentionally excludes prompts and responses and therefore
was not a substitute.

Tests run: Compilation of five runtime modules and two focused test modules;
focused unittest modules for the OpenAI-compatible backend, launcher LLM debug,
and launcher microphone profiles; `git diff --check`.

Tests passed: 34 focused tests, compilation, and whitespace checks.

Tests failed: None in the successful run. `.venv-main` remains unusable because
its configured Python executable is missing; testing used available Python
3.12.12 with its package directory.

Commands not run: Full suite, live launcher/model request, GUI smoke test, and
process restart.

Known limitations: No transcript appears until the restarted launcher-managed
main agent performs an LLM call. Files can grow for the lifetime of a process
and accumulate across runs because permanent logging has no automatic pruning.
Directly launched main agents remain opt-out unless the log-directory variable
is explicitly supplied.

Security implications: These files contain unredacted prompts, conversation
history, memory, awareness context, tool schemas, tool arguments/results, and
model output. They exclude API transport headers but must still be treated as
sensitive local data. Gitignore reduces accidental commits but is not access
control or encryption.

Deployment implications: Restart the launcher and its managed main agent. New
files will appear under `talos/logs` on the first LLM debug event.

Unresolved questions: OQ-K remains open for generated-versus-audible output and
continuous audio diagnostics. No retention period or disk quota was requested;
cleanup is manual.

Current repository state: Persistence implementation and focused validation are
complete. Pre-existing microphone changes and runtime logs remain untouched.

Next permitted task: Owner review and live verification of one generated
`llm_io_*.jsonl` file only. Do not add other sensitive feeds automatically.

Required reading for next session: `IMPLEMENTATION_STATUS.md`, ADR-043,
OQ-K, this handoff, `talos/llm_debug.py`, and launcher core/GUI integration.

Explicit stop point: Stop after local per-run LLM I/O persistence. Do not add a
network endpoint, transcript uploader, retention worker, or other debug feed.
