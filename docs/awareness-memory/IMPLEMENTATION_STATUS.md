# Implementation Status

This file reports implementation state, not documentation availability.

| Field | Current value |
|---|---|
| Current phase | Phase 7 — Actions — **complete; awaiting owner review** |
| Phase state | Phases 0-7 complete on `memory_system_3_07152026`; stopped at the Phase 7 boundary per INV-19 |
| Last completed phase | Phase 7 (2026-07-16) |
| Current bounded task | Phase 7 evidence/documentation closure complete; no Phase 8 implementation started |
| Completed items | Phases 0-6 as previously recorded; Phase 7 adds strict versioned action definitions (`water_plants`, `toggle_fan`, `sim_command`), durable requested→validated→confirmation/approval→dispatch→acknowledgement→completion/failure lifecycle and rejection/security audit, caller-key idempotency, database-unique command IDs, hashed bound confirmations, actor/safety/cooldown checks, bearer-gated mutation API, source-bound command/state evidence, truthful timeout/mismatch/negative-ack handling, at-most-once legacy versus device-key retry policy, MQTT auth/TLS reuse, preserved legacy MCP names re-backed by the action API, simulator execution acknowledgements, and Alembic revisions `4d268f4eae02` + `e7c11f9a4b2d` |
| Active work | None — explicit Phase 7 stop boundary |
| Blocked items | Phase 8 entry: owner review plus OQ-013 backup destination/schedule/encryption/restore objective; Pi broker auth/ACL state (OQ-B) remains unverifiable off-LAN and any broker migration requires owner-approved LAN work |
| Decisions made | ADR-001..016 in `DECISIONS.md` (011-016 owner-approved 2026-07-15/16) |
| Assumptions confirmed | See `DISCOVERY.md` §12 and §15 addendum; open-question resolution table in `OPEN_QUESTIONS.md` |
| Open questions | OQ-013, OQ-B (broker config), OQ-C (optional firmware remediation) in `OPEN_QUESTIONS.md` |
| Tests last run | 2026-07-16: awareness venv — 105 tests across unit/migration/MQTT/state/alerts/context/memory/actions, all pass against local Compose Postgres + test Mosquitto; main venv — 21 tests for phone/notify endpoints, awareness client/provider, preserved home-action tools, and weather, all pass. Focused clean-migration/action suite also passed (7). Local dev DB upgraded successfully to `e7c11f9a4b2d` |
| Known failures | None. Existing `test_awareness_ingestion_integration` emits one pre-existing unawaited-coroutine `RuntimeWarning` while passing |
| Files recently modified | `talos/awareness/actions/**`, action API/app/config/models/migration/MQTT/simulator wiring, main awareness/home-automation clients/providers, focused tests, `.env.example`, `talos/awareness/README.md`, and Phase 7 status/traceability/test docs |
| Next permitted task | Owner review of Phase 7 and resolution of Phase 8 entry decisions; Phase 8 implementation is not yet permitted |
| Required reading | For review: this file, `phases/PHASE_07_ACTIONS.md`, `talos/awareness/README.md` § Actions, latest handoff. If Phase 8 is authorized: `phases/PHASE_08_HARDENING.md` and all references it names |
| Explicit stop condition | Phase 7 stopped after code, migration, tests, docs, status, handoff, and report. Do not begin Phase 8 until owner review/authorization and its entry questions are settled |

Do not infer implementation progress from the presence of specification or launcher files. When work begins, replace summary values with concrete evidence, commands, results, and file paths. Session handoffs live in dated files derived from `SESSION_HANDOFF_TEMPLATE.md` (latest: `SESSION_HANDOFF_2026-07-16.md`).
