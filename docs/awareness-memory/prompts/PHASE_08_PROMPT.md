# Launcher — Phase 08 Retention, Security, and Hardening

Execute **Phase 8 only: retention/consolidation/artifacts, security completion, observability, failure/capacity testing, backup/restore, operations, and final subsystem verification**.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_08_HARDENING.md`, discovery/decisions/open questions, invariants, and the shared reference documents. Load prior phase briefs only for targeted gaps; use the original only for final traceability ambiguity/audit. Verify Phases 0-7 are completed/reviewed and owner decisions for retention/privacy/backup/security/capacity are settled. Otherwise stop. Inspect real implementations/config/tests before changes and keep output bounded.

## Permitted changes

Implement accepted retention/consolidation/artifact/security/health/metrics/backup/operations work, evidence-backed hardening fixes, narrow migrations, test/failure/load utilities, runbooks, final docs/status/traceability/handoff.

## Prohibited changes

No unrelated features or rewrites, cloud services, second broker, broad unpreviewed deletion, arbitrary artifact paths, invented action/device support, test weakening, false completion, future extensions, or agent teams without explicit authorization.

## Execution discipline

Start with `git status`, preserve unrelated changes, and use traceability/status plus targeted code/test searches instead of rereading all phases. Treat deletion, security configuration, backup, restore, and live failure injection as high-risk operations: use approved test environments, dry runs, bounded batches, and repository deployment policy. Do not run destructive production operations without separate authorization. Preserve unresolved alert evidence, memory provenance, explicit/pinned memory, and required aggregates while honoring explicit privacy deletion policy. Record benchmark hardware/data/duration and each injected failure so capacity/recovery claims stay bounded. If a mandatory check is unsafe or infeasible, document the concrete blocker and owner decision; never mark it passed. Before final status, reconcile every traceability/DoD row with current evidence and inspect the complete scoped diff.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Deliver configurable dry-run retention, bounded resumable aggregate-before-delete with alert/provenance/explicit-memory protection; meaningful consolidation; safe local artifacts; complete network/MQTT/API/action/secret/log/memory/path/backup controls; canonical health/metrics; failure injection; representative bounded load/shedding; secured backup and restore evidence where feasible; complete runbooks and source-required docs.

Run retention/protection/resume, consolidation/provenance, safe-path, authn/authz/rate/input/redaction, complete failure matrix, clean/upgrade migrations, simulator E2E, startup/shutdown, representative capacity, backup/restore, full regression, traceability, and all 22 DoD checks. Record commands, environment, pass/fail/skip/not-run; a documented but unexecuted restore is not a pass.

Update every affected document, requirements status/evidence, implementation status, decisions/questions, and handoff. Mark subsystem complete only when mandatory evidence exists; otherwise identify exact incomplete or owner-accepted blocked items.

## Final response

Report files/migrations/decisions/assumptions/tests/failures/limitations/security/deployment plus full data flow, table/API/tool/topic/outbox/channel summaries, retention defaults, backup/restore evidence, capacity/failure results, extension points, unmet items, repository state, and explicit stop.

Stop after Phase 8 reporting. Do not begin unrelated improvements automatically.
