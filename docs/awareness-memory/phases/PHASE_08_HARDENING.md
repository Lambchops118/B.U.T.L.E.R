# Phase 08 — Retention, Security, and Hardening

## Purpose

Complete safe retention/consolidation/artifacts, security and privacy controls, observability/capacity/failure testing, backups/restoration, operational runbooks, and final subsystem verification without weakening earlier phase boundaries.

## Entry criteria

Phases 0-7 are complete/reviewed; unresolved owner decisions affecting retention, privacy, backup, security, capacity, and operations are settled; status explicitly authorizes Phase 8.

## Required reading

Root `AGENTS.md`, status, this phase, discovery/decisions/open questions, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), and all files in [`../reference/`](../reference/). Load earlier phase briefs only for a targeted gap/audit; consult original for traceability ambiguity or architecture-wide final audit.

## Documents not normally needed

Do not load all prior phase documents or launcher prompts by default. Use status, traceability, shared references, tests, and targeted code searches.

## Repository discovery required for this phase

Reconfirm actual data volumes/growth, disk and host resources, retention/legal/privacy needs, pinned/explicit memory semantics, unresolved-alert evidence, artifact paths, auth/network/secret posture, metrics stack, process manager, backup destination/schedule/encryption/recovery objectives, and representative load profile.

## In scope

Configurable retention policies; dry-run/deletion plan; bounded resumable idempotent deletion; aggregate-before-delete; unresolved alert/memory provenance/pinned-explicit protection; meaningful memory consolidation and re-embedding; safe local artifact metadata/storage; privacy deletion audit; completion of auth/ACL/TLS/rate/input/path/log-redaction controls; health/metrics; failure injection; representative benchmark/load shedding; backup and tested restore where feasible; startup/shutdown/deployment/troubleshooting/runbooks; final traceability and definition-of-done audit.

## Explicitly out of scope

Unrelated feature expansion, new cloud services, unapproved broker/service architecture, rewriting prior phases, broad deletion without preview/protection, arbitrary artifact paths, new action/device features, or declaring success for unrun/infeasible checks.

## Architectural invariants that apply

All INV-01 through INV-20, with INV-16 and INV-19 governing retention and completion.

## Requirements implemented in this phase

R2, R9, R13, R19, R22 completion; RET-001 through RET-004; FAIL-001 through FAIL-008 completion; SEC-001 through SEC-007 completion; OPS-006 through OPS-012; TEST-003 through TEST-010; DOC-001 through DOC-008 and final DoD audit.

## Dependencies on prior phases

Operate on the real schemas/services from Phases 1-7. Fix only evidence-backed gaps required for hardening/acceptance, with changes attributed to their requirements. Do not invent a second implementation.

## Required deliverables

Retention/consolidation/artifact services and policies; remaining security/health/metrics work; backup/restore assets under approved deployment; failure/load utilities; operational runbooks and updated architecture/schema/topic/API/tool/action/retention docs; test evidence; completed traceability/status/handoff; final subsystem report.

## Detailed implementation requirements

Retention durations remain configurable from the canonical starting classes. Dry-run lists exact eligible records/artifacts and reasons. Execution batches safely, resumes/idempotently, produces required minute/hour/day aggregates before raw telemetry deletion, and protects unresolved-alert evidence and memory provenance. Explicit/pinned memory protection and privacy deletion must be reconciled explicitly. Consolidation groups meaningful repetition, preserves source links, closes outdated facts through supersession, decays weak inference, retains user evidence more strongly, and re-embeds changed content.

Artifacts remain outside PostgreSQL with generated rooted safe path, MIME/size/SHA-256/provenance/retention metadata; LLM tools cannot choose paths. Complete private binding, database protection, MQTT auth/ACL/TLS as approved, API/action authorization, rate/input/result bounds, CSRF where relevant, secret handling/redaction, sensitivity controls, deletion audit, and secure local backups.

Expose canonical component health/log IDs/metrics. Inject database, broker, Ollama, embedding, notification, worker/crash/lock, device ordering/clock/source silence, and capacity failures; verify truthful matrix behavior. Benchmark representative traffic, bounded queues/batches/transactions/results, lag/latency, and low-value shedding with zero intentional critical-event loss.

Backup database/schema and artifact metadata/files plus non-secret config. Document schedule/security/retention/alerts and execute a restore test where feasible; otherwise record concrete blocker and owner acceptance.

## Database or migration effects

Add only policies/artifact/audit/index/security corrections required by accepted design. Test clean and previous-revision migrations. Retention never substitutes direct table mutation outside policy.

## Integration boundaries

Use established deployment/process/metrics/auth/backup mechanisms. Keep the system local-first, broker remote as designed, database authoritative, and firmware safety local.

## Failure behavior

Every failure follows [`../reference/FAILURE_AND_RECOVERY.md`](../reference/FAILURE_AND_RECOVERY.md). Retention/backup/consolidation failure stops safely, remains resumable, preserves evidence, and is visible. Disk pressure sheds only configured low-value telemetry; no critical intentional loss.

## Security considerations

Perform a complete cross-phase threat/control audit: network listeners, DB/broker/API/action auth, secrets/logs, tool escape, paths/artifacts, memory sensitivity/deletion/context, external integrations, backups, dependencies, and unapproved cloud/public exposure.

## Required tests

Retention preview/execution/protections/aggregate prerequisite/resume; consolidation/provenance/supersession; safe artifact paths/checksums; authn/authz/rate/input/CSRF as relevant; secret scan/log redaction; all failure-matrix injections; migration clean/upgrade; simulator E2E suite; representative load/capacity; startup/shutdown; backup and restoration; complete DoD/traceability audit; full regression suite.

## Acceptance criteria

- Dry run reports exact deletion plan; execution is bounded/resumable and protects required evidence/provenance/memories.
- Raw telemetry deletes only after required aggregates; artifacts are safe and auditable.
- Sensitive endpoints require correct authorization, secrets are absent from logs/repo, and no unapproved cloud/public exposure exists.
- Backup/restore is documented and tested where feasible with truthful evidence.
- Failure and load tests demonstrate bounded, recoverable, truthful behavior and no intentional critical-event shedding.
- All 22 subsystem DoD items, tests, operations, docs, and traceability are evidenced or explicitly blocked/owner-accepted.

## Documentation updates

Complete all source-required architecture, topology, data flow, schemas/migrations/topics, state/history/telemetry, alert/notification/outbox, memory/context/tools/actions/retention, setup/deployment/security, simulator/tests, backup/restore, troubleshooting, limitations, and migration-path documentation.

## Implementation status updates

Mark Phase 8/subsystem complete only if all required evidence exists. Otherwise record exact incomplete/blocked items and next permitted bounded remediation. Include final test/backup/capacity/security evidence.

## Required final report

Report all phase fields plus complete data flow, table summary, APIs/tools, MQTT topics consumed/produced, outbox work types, notification channels, retention defaults, backup/restore steps/evidence, capacity/security/failure results, remaining extension points, and unfulfilled items. Do not call the subsystem complete if any mandatory item lacks evidence.

## Stop condition

Stop after the final evidence, documentation, status, handoff, traceability, and report. Do not begin unrelated improvements or extensions automatically.
