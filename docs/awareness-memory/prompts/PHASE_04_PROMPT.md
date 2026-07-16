# Launcher — Phase 04 Rules, Alerts, and Notifications

Execute **Phase 4 only: deterministic rules, alert/attention lifecycle, reliable notifications, and notification outbox handling**. The overflow path must work without Ollama.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_04_RULES_ALERTS_NOTIFICATIONS.md`, discovery/decisions, and only its named schemas/failure/security/tests. Verify Phase 3 completion/review, stable qualified state/history/health, confirmed policies/channels/delivery semantics, and Phase 4 authorization. Inspect existing automation/notification code first. Do not load later phase documents, the full original, or unrelated/large files.

## Permitted changes

Implement strict versioned rule/classification policy, alerts, attention, existing-channel adapters, deterministic templates, delivery audit/retries/cooldowns/fallback/escalation, notification outbox workers, narrow migrations, tests, and docs.

## Prohibited changes

No LLM hot-path safety decisions, permanent memory/context/action implementation, invented notification services, arbitrary execution, future placeholders, unrelated refactors, unauthorized agent teams, or Phase 5 work.

## Execution discipline

Start with `git status`, preserve unrelated changes, and inspect existing automation and channel semantics with targeted output. Keep rules strict/versioned and separate incident state from interruption/delivery state. Persist all intent before network work, bound claims/batches/retries, sanitize errors, and use uniqueness/idempotency to make crash recovery safe. Test the deterministic fallback with Ollama genuinely unavailable or an equivalent controlled failure, and record exactly what delivery confirmation means for each adapter. Never infer a speaker/phone/email delivery from a successful enqueue. If notification ownership, criticality, quiet hours, or escalation policy is unresolved, stop on that decision rather than inventing behavior. Before handoff, audit for duplicate notifications, future memory/action work, secret exposure, and any test/result mismatch.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Deliver hard-rule precedence, deduplicated persistent incidents with evidence/counts, distinct attention timing, atomic alert/attention/outbox intent, bounded safe outbox claim/backoff/stale-lock/dead-letter/manual retry, adapter-defined confirmation, deterministic fallback wording, and configured quiet/call/cooldown/escalation behavior.

Test rule schema/matching, alert lifecycle/dedupe, attention interruptibility, adapter success/failure truthfulness, retry/claim/lock behavior, repeated overflow, overflow with Ollama down, and crash after event commit before notification. Never report delivery without adapter confirmation or unrun tests as passing.

Update rule/alert/attention/notification/outbox/troubleshooting docs, traceability, status, decisions/questions, and handoff.

## Final response

Report files/migrations; policies/incidents/outbox work/adapters/channels; tests and crash/Ollama evidence; delivery failures/limitations; security/deployment implications; repository state; next proposed task; explicit stop.

Stop after Phase 4. Do not implement situation/context/read tools or begin Phase 5.
