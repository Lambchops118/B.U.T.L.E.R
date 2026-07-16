# Launcher — Phase 02 MQTT Ingestion

Execute **Phase 2 only: MQTT ingestion and distributed event integrity**. Connect to the existing configured Raspberry Pi Mosquitto broker and implement strict, authorized, idempotent canonical ingestion plus the required simulator behavior.

## Required reading and entry check

Read root `AGENTS.md`, status, `docs/awareness-memory/phases/PHASE_02_MQTT_INGESTION.md`, discovery/accepted decisions, and only its listed schema/failure/security/test references. Verify Phase 1 completion/review, passing foundation evidence, confirmed broker/topic/auth/TLS/session facts, and explicit Phase 2 authorization; otherwise stop. Inspect existing MQTT and persistence code first. Avoid loading unrelated phases, full original spec, dependencies/caches/logs/databases/models/artifacts, or unbounded output.

## Permitted changes

Implement focused MQTT/adapters/ingestion/normalization/dedupe/sequence/dead-letter/simulator work, narrow evidence-backed migrations, tests, configuration examples without secrets, and Phase 2 documentation. Preserve established topics and existing functionality.

## Prohibited changes

No second broker; no topic migration without approval; no unbounded queue; no LLM hot path; no Phase 3 state/freshness/telemetry, rules/notifications, memory/context/actions, or future placeholders. Do not weaken ACLs/tests, hard-code endpoints/credentials, perform unrelated refactors, launch teams without authorization, or begin Phase 3.

## Execution discipline

Start with `git status`, preserve unrelated changes, and use targeted searches/bounded logs. Work through the deterministic intake stages and verify each observable rejection/order class rather than relying on broker QoS. Keep network work outside database transactions and all queues, payloads, retries, and batches bounded. Generate no claim about offline buffering, clock trust, authentication, or delivery that exceeds verified producer/broker capability. Use safe local test identities/configuration and never print credentials or certificate contents. If live broker access is unavailable, complete safe unit/local integration work, label the missing evidence, and do not claim reconnect or ACL acceptance. Before handoff, review the diff for Phase 3+ behavior and reconcile metrics, database effects, and test reports with evidence.

At checkpoints, compare the diff with the phase requirement IDs and acceptance criteria. Keep a compact record of commands and decisions so status and handoff are complete for a fresh session. When blocked, exhaust safe read-only evidence, identify the exact missing input or permission, and do not broaden scope.

## Deliverables and tests

Deliver reconnect/session/subscription/health behavior, strict envelope and provenance, size/source/topic/schema checks, duplicate/boot/sequence/gap/reorder/delay/clock handling, retained freshness classification, immutable event/dead-letter flow, metrics/logs, and configured simulator cases. Test valid once, duplicates, unauthorized/malformed, retained, delay/reorder/gap/reboot, reconnect/restore/shutdown, rollback, and simulator. Prove no second broker was deployed. Report exact commands and truthful results; do not claim unavailable live-broker checks passed.

Update topic/envelope/source/delivery/simulator/troubleshooting docs, traceability, status, decisions/questions, and handoff.

## Final response

Report files/migrations; topics/sources and delivery guarantees; tests run/passed/failed/not run; broker/simulator conditions; buffering/clock limitations; security/deployment effects; repository state; next proposed task; explicit stop.

Stop after Phase 2. Do not implement current-state/telemetry or begin Phase 3.
