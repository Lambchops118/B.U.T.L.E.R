# Failure and Recovery

All failure handling is bounded, observable, recoverable where possible, and truthful. Do not imply a component or operation works without evidence. Immediate physical interlocks remain local to firmware/hardware.

## Failure matrix

| Failure | Required behavior | Recovery and verification | Never claim / do |
|---|---|---|---|
| Ollama/chat unavailable | Continue ingestion, state, deterministic rules/shutdown paths, alerts, fallback notifications; store appropriate requests/conversations; queue inference-dependent work. | Health exposes outage; retry summaries/embeddings with bounded backoff; verify deterministic overflow path without Ollama. | Do not fabricate a reasoning response or delay critical notification for wording. |
| Embedding model unavailable | Store accepted memory text and provenance; queue embedding; preserve exact/full-text search and all state/alert behavior. | Show backlog/age and retry idempotently when healthy. | Do not report semantic retrieval fully available or discard accepted memory. |
| PostgreSQL unavailable | Report persistence unavailable; avoid claims of successful writes; fail safe for actions requiring durable state; expose health. Avoid silent critical-event loss. | Reconnect with bounds; replay any explicitly implemented emergency spool idempotently; test restart. | Do not buffer without limit or treat a spool as a second permanent database. |
| MQTT unavailable | Expose connection state; reconnect with bounded exponential backoff/jitter; preserve outbound command state; allow freshness/offline transitions as expected messages cease. | Restore subscriptions/session policy; measure reconnect and lag; reconcile acknowledgements. | Do not mark commands completed without acknowledgement. |
| Notification endpoint failure | Persist each attempt/error; keep alert open; retry by policy; use channel fallback/escalate critical delivery failure where available. | Adapter-specific confirmation, bounded retry, manual retry/dead-letter, delivery audit. | Do not mark delivered without channel-confirmed semantics. |
| Process crash | Atomic event transaction preserves event/state/alert/outbox intent; short transactions contain no network/LLM work. | Workers safely reclaim stale locks; idempotent handlers resume; test crash between commit and notification. | Do not rely on in-memory pending work. |
| Outbox handler failure | Retain sanitized error, increment attempts, schedule bounded backoff, expose backlog/oldest age; dead-letter unrecoverable work. | `SKIP LOCKED` or equivalent claims, bounded batches, stale-lock release, manual retry. | Do not promise exactly once or retry forever without limits. |
| Device/source restart | Distinguish legitimate sequence reset with `source_boot_id`; update registry/health; preserve event history. | Simulator reboot scenario and uniqueness checks. | Do not label every reset duplicate or assume sequence monotonic across boots. |
| Duplicate delivery | Database uniqueness plus idempotent consumers prevent repeated event effects, alert spam, notification outside policy, action, and memory candidate. | Increment duplicate metrics; duplicate-event end-to-end test. | Do not depend only on application prechecks or MQTT QoS. |
| Delayed/reordered delivery | Evaluate clock quality and lag; retain in history; prevent older/weaker data from replacing newer authoritative state; mark temporal qualification. | Delayed/out-of-order simulator and state test; keep root-cause provenance. | Do not discard solely because state cannot be updated or assume order. |
| Missing sequence | Detect and record gaps without inventing absent events. | Metrics/health and recovery policy; simulator gap. | Do not silently synthesize measurements. |
| Clock drift/untrusted time | Preserve clock quality/offset and original observation time as evidence; use receipt time for ordering when necessary. | Compare source health/sync and render uncertainty. | Do not conflate observed, received, processed, valid, and expiry times. |
| Source silence | Deterministic worker transitions values/source through stale/offline thresholds; alert severity follows configured criticality. | Heartbeat/offline test; last-known answers include age/status/source. | Do not call stale data current or make every silence critical. |
| Disk/capacity pressure | Report disk and queues; batch telemetry; apply configured bounded low-value load shedding. | Metrics/alerts and capacity test; retention dry run. | Never intentionally shed critical safety events or use unbounded queues. |

## Emergency spool constraints

An emergency spool is optional and may be built only in the phase that explicitly authorizes it after repository discovery. If implemented, it uses bounded disk, append-only local files or an embedded durable queue, fsync for important records, documented ordering/loss guarantees, and idempotent replay. Low-value dropping policy is explicit. It is not a substitute for PostgreSQL.

Remote store-and-forward is similarly capability-dependent: event IDs are created before queueing, disk and retries are bounded, ordering is preserved where useful, delivery is duplicate-safe, and low-value telemetry has an explicit dropping policy. Do not claim this guarantee for devices/firmware that lack it.

## Startup and shutdown recovery

Startup validates configuration, logging, database/extensions/schema policy, registries, broker/subscriptions, workers, API, and health without task races. Shutdown stops new work, drains or bounds intake, finishes/releases claims, closes pools and MQTT, flushes logs, and leaves retryable outbox rows durable. See [`OPERATIONS_AND_DEPLOYMENT.md`](OPERATIONS_AND_DEPLOYMENT.md).
