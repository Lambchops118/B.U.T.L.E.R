# Operations and Deployment

Repository- and host-specific choices are pending Phase 0. This document preserves required operational properties and the source defaults without claiming discovery has occurred.

## Default topology and ownership

The central AI host normally runs the modular awareness/memory backend, authoritative PostgreSQL, TimescaleDB, pgvector, local artifact store, and Ollama/Qwen plus local embeddings. Distributed devices, gateways, automation hosts, and services communicate over the LAN through adapters and the existing Raspberry Pi Mosquitto broker. Existing TTS/speaker/desktop/phone/email endpoints are notification adapters.

- Mosquitto transports messages; it is not permanent history or authoritative state.
- The central backend validates, classifies, and processes.
- PostgreSQL owns authoritative records; TimescaleDB handles high-volume numeric telemetry; pgvector indexes selected semantic material.
- Ollama performs local chat inference and embeddings.
- Devices retain immediate electrical/mechanical safety.

Do not deploy a second broker unless Phase 0 proves it necessary and the owner approves. If Home Assistant exists, Phase 0 must propose Home Assistant authority, backend authority with HA as source, or a defined hybrid; the owner chooses before implementation.

## Technology defaults and substitutions

Use established suitable repository equivalents. Otherwise the source defaults are Python 3.11+ (for a Python repository), PostgreSQL, TimescaleDB, pgvector, FastAPI, SQLAlchemy 2.x/current ORM, Alembic/current migration tool, Pydantic v2/equivalent strict schemas, asyncio-compatible clients, configured Ollama embeddings, local filesystem artifacts, and Docker Compose when consistent with deployment.

Optional Redis requires demonstrated caching/lock/transient-coordination need and is never the sole state authority. A database/extension substitute requires owner-visible rationale and must preserve transactions, immutable events, durable state, outbox, indexed temporal queries, semantic retrieval, migrations, backups, and concurrency safety.

## Phase 0 deployment inventory

Record central/Ollama/subsystem host, broker address/port without secrets, TLS/auth/ACL method, clients, endpoint channels, Linux clock sync, embedded clock reliability, segments/firewalls/hostnames, offline responsibilities, deployment/process manager, storage capacity, backups, and expected volumes. Label each assumption `confirmed_by_repo`, `confirmed_by_owner`, or `assumed_needs_confirmation`.

## Startup and shutdown

Startup order, adapted to established policy:

1. validate typed configuration and initialize structured logging;
2. check database and required extensions/schema version;
3. apply migrations only under repository policy;
4. register known sources/entities;
5. connect MQTT and restore subscriptions;
6. start outbox, freshness, and (when implemented) retention workers;
7. start the API and expose component health;
8. avoid races among tasks.

Shutdown stops accepting API work, stops MQTT intake or drains bounded queues, finishes/releases worker claims, closes pools and MQTT cleanly, flushes logs, and preserves retryable work.

## Configuration categories

Typed configuration covers database; MQTT endpoint/TLS/identity/session/QoS/topic prefix; Ollama chat/embedding models; context budgets; retention; rule thresholds; heartbeat/stale/offline windows; notification channels; worker concurrency; data/artifact location; logging; registry source; API binding/port. Never hard-code broker address or credentials. Fail fast with actionable sanitized errors.

## Health, logs, and metrics

Health reports database/extensions, MQTT and broker address, Ollama chat/embedding, notification adapters, disk, workers, outbox backlog/age, failures, stale/offline sources, schema/prompt/tool versions, and last successful backup.

Structured logs use relevant event, correlation, causation, source, entity, alert, action, conversation, outbox, and notification identifiers without secrets.

Metrics include received/accepted/invalid/unauthorized/duplicate/delayed/out-of-order/gapped events; MQTT reconnect/lag; stale/offline sources; alerts by severity; notification failures; outbox backlog/age; tool and database latency; embedding backlog; context tokens; accepted/rejected memory candidates; worker failures; disk usage. Use the repository metrics stack or minimal internal endpoint/counters.

## Capacity and load shedding

Phase 0 documents representative and peak volume. Ingestion never blocks on inference/embedding. Use async network/database I/O where appropriate, bounded queues and batches, short transactions, no network calls while locks/transactions are held, bounded APIs/tools, and measured message/outbox lag. Gracefully shed only configured low-value telemetry; never intentionally shed critical safety events. Phase 8 adds a representative benchmark/load utility and records capacity results; do not introduce a service fleet as premature optimization.

## Backups, restoration, retention, and artifacts

Back up the database and schema/version plus artifact metadata/files and non-secret configuration. Define schedule, secured location, retention, monitoring, recovery objective, and tested restore steps. Retention is dry-runnable and aggregate-before-delete; deletions are bounded, resumable, idempotent, logged, and protect unresolved alerts and memory provenance. Large files live in the configured local artifact store with safe paths and checksums.

## Simulator and troubleshooting

The configured simulator targets the existing broker and can produce heartbeat, temperature, moisture, pump state, overflow, offline, duplicate, delayed, out-of-order, sequence gap, reboot, acknowledgement, command failure, and firmware change. Runbooks cover component health, broker connectivity/auth/TLS, lag/backlogs, stale sources, database/extensions/migrations, Ollama/embedding outages, notification failure, retention preview, backup/restore, and truthful known limitations.
