# Implementation Status

This file reports implementation state, not documentation availability.

| Field | Current value |
|---|---|
| Current phase | Phase 8 — Retention, Security, and Hardening — **complete**. All phases 0-8 implemented. |
| Phase state | Subsystem implementation complete on `memory_system_3_07152026` (owner authorized Phase 8 with local-backup defaults, 2026-07-16) |
| Last completed phase | Phase 8 (2026-07-16) |
| Current bounded task | Post-completion live `.env` recovery after local Ollama cutover — **complete** (2026-07-18) |
| Completed items | Phases 0-7 (see git history and `talos/awareness/README.md`); Phase 8: retention service (dry-run plan, bounded resumable batched deletion, aggregate-before-delete via cagg refresh, open-alert/evidence/active-memory protections), memory consolidation (incident summaries with derived_from links, weak-inference decay, user-evidence exemption), artifact store (generated rooted paths, SHA-256, table `artifacts`, migration `3337c328523b`), local backups (pg_dump in-container + config snapshot + 14-day pruning; **restore tested live: 27/27 tables**), write-auth on all mutating endpoints (actions fail-closed; others bearer-gated when `TALOS_AWARENESS_API_TOKEN` set), `/metrics` (counters/backlog/disk/last-backup), benchmark utility (**118 ev/s, p50 7.5 ms, p95 14.8 ms, 0 drops**), broker hardening plan (`BROKER_HARDENING_PLAN.md`, owner-executed), CLI: `retention`/`consolidate`/`backup [--verify]` |
| Active work | None |
| Blocked items | Owner-executed items only: broker auth/ACL/TLS on the Pi (OQ-B plan delivered); optional firmware remediation (OQ-C); live-model retrieval scenario runs with real Qwen |
| Decisions made | ADR-001..017 in `DECISIONS.md`; ADR-017 selects local `mb-core-v1:latest` for streamed inference with remote STT/LLM fallback opt-in; OQ-013 resolved 2026-07-16 (local nightly backups, 14-day retention, no encryption, tested restore) |
| Assumptions confirmed | `DISCOVERY.md` §12/§15; open-question table in `OPEN_QUESTIONS.md` |
| Open questions | OQ-B (broker config verification + hardening execution), OQ-C (optional firmware work) — both owner-executed LAN tasks |
| Tests last run | 2026-07-18 `.env` recovery verification: all 25 prior live keys restored, 8 Ollama/offline keys added, 0 missing prior keys, 0 duplicate keys; `.env.example` has 0 missing original assignment keys (34 original, 39 current). Local Ollama cutover: main venv — 55 focused tests pass; `py_compile` passes; live loopback and TALOS factory smoke tests pass with `mb-core-v1:latest`. Earlier 2026-07-18 voice role-history repair: 36 focused tests pass and exact subset 8/8 passes. 2026-07-16 awareness venv: 103 tests pass. |
| Known failures | None |
| Files recently modified | Local Ollama cutover: ignored `.env`, `.env.example`, README, `talos/agent/runtime.py`, `talos/voice/agent.py`, `talos/voice/backends/factory.py`, `tests/test_llm_openai_compat.py`; earlier voice role-history and personality files remain recorded in their 2026-07-18 handoffs. |
| Next permitted task | Owner live voice verification; separately authorized local TTS replacement and/or migration of the legacy non-streaming Responses lane if completely offline operation beyond the default streamed voice lane is required; owner-executed LAN work per `BROKER_HARDENING_PLAN.md`. |
| Required reading | `talos/awareness/README.md` (complete operational reference), `DISCOVERY.md`, `BROKER_HARDENING_PLAN.md`, latest session handoff |
| Explicit stop condition | Environment recovery stopped after restoring the latest pre-cutover VS Code Local History snapshot without displaying its values, merging the 8 Ollama/offline settings, verifying no missing/duplicate keys, preserving all original `.env.example` assignments, and updating the handoff. No credentials were replaced with placeholders and no unrelated runtime work was performed. |
| Documentation follow-up (2026-07-16) | Added `like_im_a_child_or_golden_retriever.md`, a plain-language intern quick start covering immediate operation, TALOS integration, maintenance, code paths, tests, safety invariants, limitations, and troubleshooting. Linked it from this documentation index. Validation: 96 awareness unit tests and 3 main-agent home-action tests pass; CLI help, relative-link targets, and `git diff --check` pass. Runtime, schemas, migrations, decisions, and open questions are unchanged. |

Do not infer implementation progress from the presence of specification or launcher files. Session handoffs live in dated files derived from `SESSION_HANDOFF_TEMPLATE.md` (latest: `SESSION_HANDOFF_2026-07-18_LOCAL_OLLAMA.md`).
