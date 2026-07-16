# Repository Agent Instructions

These instructions apply to every coding-agent session in this repository. For the awareness and memory subsystem, start with [`docs/awareness-memory/IMPLEMENTATION_STATUS.md`](docs/awareness-memory/IMPLEMENTATION_STATUS.md).

## Scope and phase control

- Work only on the explicitly assigned phase or bounded task.
- Read the current phase document and only the shared references it names.
- Follow [`docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`](docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md).
- Inspect relevant existing code, tests, configuration, and deployment files before changing them.
- Preserve repository conventions and working behavior. Integrate additively and avoid unrelated refactoring.
- Do not create placeholder modules, migrations, integrations, or services for future phases.
- Keep the repository runnable at the end of the task.
- Never begin the next phase automatically. Stop at the current phase boundary and wait for owner authorization.
- Phase 0 is discovery and documentation only. It requires owner review before runtime implementation unless the owner explicitly waives that gate.
- Consult the [original specification](docs/ROBUST_HOME_AUTOMATION_MEMORY_IMPLEMENTATION_PROMPT.md) only when the reorganized documents are incomplete or ambiguous, for a traceability audit, or when the owner asks.

## Working method

- Run relevant tests and checks in proportion to the change.
- Report exactly which tests ran, passed, failed, or were not run; never imply unrun checks passed.
- Do not weaken tests or bypass safety checks to obtain a passing result.
- Update `IMPLEMENTATION_STATUS.md` and record the session handoff using [`SESSION_HANDOFF_TEMPLATE.md`](docs/awareness-memory/SESSION_HANDOFF_TEMPLATE.md).
- Record confirmed decisions in `DECISIONS.md` and unresolved owner/repository questions in `OPEN_QUESTIONS.md`.
- End with the phase document's required final report and explicit stop condition.

## Context efficiency

- Use targeted searches; do not recursively read the whole repository.
- Bound command output and summarize verbose logs.
- Do not print entire large files when a heading, range, or search result will do.
- Do not load generated files, dependencies, caches, model files, logs, databases, or large artifacts unless directly needed.
- Do not read unrelated phase documents or repeatedly reread the complete original specification.
- Do not launch subagents or agent teams unless explicitly authorized.

## Permanent safety boundaries

- The LLM is not the event loop, authoritative database, safety controller, alert detector, retry mechanism, or arbitrary command executor.
- Exact current, numeric, and temporal questions use bounded structured retrieval, not vector search.
- Physical actions require registered schemas, authorization, confirmation when configured, idempotency, timeouts, acknowledgements, and transition audit.
- Immediate electrical and mechanical interlocks remain in firmware or hardware.
- Keep the subsystem local-first and report failures and degraded capability truthfully.
