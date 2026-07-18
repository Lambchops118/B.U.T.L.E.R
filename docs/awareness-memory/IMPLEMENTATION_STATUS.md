# Implementation Status

This file reports implementation state, not documentation availability.

| Field | Current value |
|---|---|
| Current phase | Phase 8 — Retention, Security, and Hardening — **complete**. All phases 0-8 implemented. |
| Phase state | Subsystem implementation complete on `memory_system_3_07152026` (owner authorized Phase 8 with local-backup defaults, 2026-07-16) |
| Last completed phase | Phase 8 (2026-07-16) |
| Current bounded task | Post-completion streamed voice role-history repair — **complete** (2026-07-18) |
| Completed items | Phases 0-7 (see git history and `talos/awareness/README.md`); Phase 8: retention service (dry-run plan, bounded resumable batched deletion, aggregate-before-delete via cagg refresh, open-alert/evidence/active-memory protections), memory consolidation (incident summaries with derived_from links, weak-inference decay, user-evidence exemption), artifact store (generated rooted paths, SHA-256, table `artifacts`, migration `3337c328523b`), local backups (pg_dump in-container + config snapshot + 14-day pruning; **restore tested live: 27/27 tables**), write-auth on all mutating endpoints (actions fail-closed; others bearer-gated when `TALOS_AWARENESS_API_TOKEN` set), `/metrics` (counters/backlog/disk/last-backup), benchmark utility (**118 ev/s, p50 7.5 ms, p95 14.8 ms, 0 drops**), broker hardening plan (`BROKER_HARDENING_PLAN.md`, owner-executed), CLI: `retention`/`consolidate`/`backup [--verify]` |
| Active work | None |
| Blocked items | Owner-executed items only: broker auth/ACL/TLS on the Pi (OQ-B plan delivered); optional firmware remediation (OQ-C); live-model retrieval scenario runs with real Qwen |
| Decisions made | ADR-001..016 in `DECISIONS.md`; OQ-013 resolved 2026-07-16 (local nightly backups, 14-day retention, no encryption, tested restore) |
| Assumptions confirmed | `DISCOVERY.md` §12/§15; open-question table in `OPEN_QUESTIONS.md` |
| Open questions | OQ-B (broker config verification + hardening execution), OQ-C (optional firmware work) — both owner-executed LAN tasks |
| Tests last run | 2026-07-18 voice role-history repair: main venv — 36 focused memory/stream/text/runtime/prompt/router/awareness tests, all pass; exact plant-watering follow-up subset rerun, 8/8 pass; `py_compile` for changed Python files passes. Earlier 2026-07-18 personality follow-up: 17 focused tests pass. 2026-07-16 awareness venv: 103 tests pass. |
| Known failures | None |
| Files recently modified | Voice role-history repair: `talos/agent/runtime.py`, `talos/memory/store.py`, `tests/test_memory_store.py`, `tests/test_run_command_stream.py`, `.env.example`, README; personality discipline: `talos/personality/monkey_butler.md`, `tests/test_prompting.py`; earlier voice continuity files remain recorded in the 2026-07-18 handoff. |
| Next permitted task | Owner review of the completed subsystem; owner-executed LAN work per `BROKER_HARDENING_PLAN.md`; production deployment (Ollama install, cron backup schedule, API token provisioning) |
| Required reading | `talos/awareness/README.md` (complete operational reference), `DISCOVERY.md`, `BROKER_HARDENING_PLAN.md`, latest session handoff |
| Explicit stop condition | Streamed voice role-history repair stopped after stored-evidence diagnosis, bounded real-role history implementation, exact regression test, focused suite, status, and handoff. No physical action or unrelated awareness work was performed. |
| Documentation follow-up (2026-07-16) | Added `like_im_a_child_or_golden_retriever.md`, a plain-language intern quick start covering immediate operation, TALOS integration, maintenance, code paths, tests, safety invariants, limitations, and troubleshooting. Linked it from this documentation index. Validation: 96 awareness unit tests and 3 main-agent home-action tests pass; CLI help, relative-link targets, and `git diff --check` pass. Runtime, schemas, migrations, decisions, and open questions are unchanged. |

Do not infer implementation progress from the presence of specification or launcher files. Session handoffs live in dated files derived from `SESSION_HANDOFF_TEMPLATE.md` (latest: `SESSION_HANDOFF_2026-07-18_VOICE_CONTEXT.md`).
