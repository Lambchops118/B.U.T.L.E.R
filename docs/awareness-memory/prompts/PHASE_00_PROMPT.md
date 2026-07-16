# Launcher — Phase 00 Discovery

You are executing **Phase 0 only: repository and deployment discovery**. Your exact task is to inspect the existing repository/deployment evidence, write `docs/awareness-memory/DISCOVERY.md`, update the project documentation/handoff, report, and stop. **Do not implement runtime functionality.**

## Required reading and entry check

Read root `AGENTS.md`, `docs/awareness-memory/IMPLEMENTATION_STATUS.md`, `docs/awareness-memory/phases/PHASE_00_DISCOVERY.md`, and only the references that phase lists. Confirm status permits Phase 0. Read targeted repository files before drawing conclusions. Do not read all documentation, later phase files, dependencies, caches, logs, databases, models, artifacts, or the complete original specification by default. Consult the original only for a specific ambiguity/traceability gap.

## Permitted changes

You may create/update discovery, status, decision/open-question, and handoff documentation. Use read-only repository/deployment inspection and safe bounded diagnostic commands. Record evidence and label assumptions exactly as required by the phase.

## Prohibited changes

Do not change application code, tests, dependencies, runtime configuration, deployment files, migrations, databases, broker/device state, services, or integrations. Do not create placeholder modules. Do not begin Phase 1. Do not launch agent teams unless explicitly authorized. Avoid unrelated refactoring and keep command output bounded/summarized.

## Execution discipline

Start with `git status` and preserve every pre-existing user change. Use targeted `rg`, bounded file ranges, and focused configuration inspection instead of recursively reading the repository. Cite file paths, settings names, and command evidence, but redact values that could be secrets. Separate what the repository proves, what the owner stated, and what remains an inference. If a command cannot safely run or a live topology fact cannot be verified, record the limitation and a precise confirmation method rather than changing external state or guessing. Re-check the final diff against the permitted documentation paths and the phase acceptance criteria. Keep findings concise enough for a fresh Phase 1 agent to load selectively.

## Required deliverables and validation

Deliver the complete `DISCOVERY.md`: repository/runtime/deployment maps, MQTT/LLM/tool/notification/persistence/security/backup facts, C1-C18 mapping, constraints/conflicts/risks, confidence-labeled assumptions, stack/database recommendation, Home Assistant gate, sequence, and owner decisions. Validate links/Markdown and prove only documentation changed. Runtime tests are not required; report any baseline checks exactly and do not claim an unrun test passed.

Update `IMPLEMENTATION_STATUS.md` to `awaiting owner review`, record decisions/open questions without inventing approval, and create a session handoff from `SESSION_HANDOFF_TEMPLATE.md`.

## Final response

Report: files added/modified; migrations (`none`); evidence/commands; architecture/deployment findings; assumptions; recommendations; conflicts/risks; tests/checks passed/failed/not run; limitations; security implications; owner decisions required; next permitted task (`owner review only`); explicit stop point.

Then **stop**. Do not scaffold or modify runtime architecture and do not start Phase 1 unless the owner separately reviews Phase 0 and authorizes it.
