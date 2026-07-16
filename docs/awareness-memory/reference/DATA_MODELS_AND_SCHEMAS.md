# Data Models and Schemas

These definitions are canonical across phases. Names may follow established repository conventions after Phase 0, but fields, semantic distinctions, relationships, status values, transaction boundaries, and uniqueness/idempotency properties must remain. Use strict versioned schemas, timezone-aware UTC storage, migrations, typed columns for common queries, and JSON/JSONB only for flexible content.

## Event envelope and provenance

Every inbound datum becomes an equivalent strict envelope:

```text
EventEnvelope
  event_id: UUID
  schema_version: int
  event_type: str
  entity_id: str | null
  source_id: str
  location_id: str | null
  observed_at: datetime | null
  received_at: datetime
  processed_at: datetime | null
  sequence: int | null
  source_boot_id: str | null
  correlation_id: str | null
  causation_id: str | null
  severity: debug | info | notice | warning | critical
  confidence: float
  retention_class: str | null
  expires_at: datetime | null
  payload: object
  provenance: Provenance

Provenance
  transport: str
  topic_or_endpoint: str | null
  gateway_id: str | null
  firmware_version: str | null
  software_version: str | null
  clock_quality: unknown | unsynchronized | device_local | device_synced |
                 gateway_stamped | server_received
  clock_offset_ms: float | null
  authenticated_identity: str | null
  metadata: object
```

Timestamp semantics must not be collapsed:

- `observed_at`: source-reported occurrence time.
- `received_at`: central-backend receipt time.
- `processed_at`: processing completion time.
- `valid_from` / `valid_to`: state or memory validity interval.
- `expires_at`: stale/irrelevant deadline.
- `created_at`: database-row creation time.

Preserve an original timezone in metadata when relevant. If device time is untrustworthy, order by `received_at` and retain `observed_at` as untrusted evidence. Important device events require unique `event_id`; support sequence and `source_boot_id`; detect duplicate IDs, duplicate `(source_id, source_boot_id, sequence)`, gaps, and out-of-order arrival. Delayed events remain in history even if they cannot replace state.

Validation must enforce schema version, payload-size bounds, authenticated/allowlisted source and topic ownership, and structured rejection. Malformed or unauthorized messages go to a dead-letter store without crashing intake, with metrics/logs and no secrets.

## Registry and source health

Support rooms/locations, people, plants, devices, controllers, sensors, actuators, services, automations, software agents, and notification endpoints. Relationships are typed and may have validity periods, including:

```text
sensor belongs_to device        device located_in room
plant monitored_by sensor       pump waters plant
controller controls pump        person detected_in room
service depends_on host          source reports_for entity
```

Source registry fields:

```text
source_id                 source_type               display_name
transport                 entity_id                 location_id
firmware_version          software_version          schema_version
expected_update_interval  stale_after_seconds       offline_after_seconds
clock_quality             last_observed_at          last_received_at
last_sequence             last_boot_id              health_status
enabled                   authentication_identity   metadata
created_at                updated_at
```

Health is `healthy | degraded | stale | offline | misconfigured | unauthorized | unknown`. Silence severity depends on source criticality and policy; silence is not automatically critical. Keep source-health history in addition to the current registry view.

## Current state

The durable active row contains at least:

```text
entity_id        property_name       value_json       value_type
observed_at      received_at         updated_at       valid_from
expires_at       confidence          source_id        source_event_id
state_status     authority_rank      metadata
```

State status is `current | stale | unknown | conflicting | offline | inferred | scheduled`. Enforce one active row per `(entity_id, property_name)`.

Updates compare observation/receipt time, source authority/priority, confidence, clock quality, and current validity. Preserve delayed events without allowing them to overwrite newer authoritative state. Detect conflict, update freshness deadlines, retain source-event linkage, emit meaningful transition events, and suppress numerical noise with configured deadbands and hysteresis. A deterministic freshness worker marks values stale and sources offline, updates derived state, and opens/updates/resolves a deduplicated alert according to policy.

A compact situation object may derive from relevant current state, active alerts, attention items, recent meaningful transitions, likely user location, conversation state, ongoing tasks, and health. It is not a continuously regenerated natural-language world narrative or an unbounded persisted dump.

## Event history and telemetry

Normalized events are append-only and immutable. Support exact event-ID lookup, bounded/paginated time ranges, source/entity/type/severity filters, correlation/causation traversal, replay, root-cause tracing, and derived-record provenance.

Numeric measurement fields:

```text
time             received_at       entity_id          source_id
measurement_name value_double or typed value          unit
quality          confidence        source_event_id    metadata
```

Use TimescaleDB hypertables where appropriate. Configure raw retention and one-minute/hourly/daily aggregates with minimum, maximum, average, count, standard deviation, and useful threshold/anomaly counts. Raw telemetry is never embedded.

History APIs require a time range and limits. A sensor query accepts sensor/entity ID, measurement, start/end, optional aggregation and interval, and `max_points`; reject unbounded ranges or unlimited points.

## Classification and policy contracts

An accepted event may yield any combination of:

```text
DROP                         STORE_DEAD_LETTER
STORE_RAW                    STORE_HISTORY
STORE_TELEMETRY              UPDATE_CURRENT_STATE
UPDATE_SOURCE_HEALTH         CREATE_ALERT
UPDATE_ALERT                 CREATE_ATTENTION_ITEM
CREATE_MEMORY_CANDIDATE      INJECT_ON_NEXT_INTERACTION
TRIGGER_DETERMINISTIC_ACTION QUEUE_NOTIFICATION
AGGREGATE_ONLY
```

Strict, version-controlled YAML/TOML or typed policy definitions separate hard safety/operational rules, storage/downstream classification, noncritical salience, retention class, and interruptibility. Rules may use thresholds, booleans, transitions, rate of change, windows, missing data, health, correlations, cooldown/deduplication keys, escalation, acknowledgement, automatic/manual resolution, and suppression. Optional model classification is asynchronous and noncritical: it cannot block ingestion, override hard rules, directly alert, or write permanent memory without validation.

## Alerts, attention, and notification delivery

Alert fields and enum:

```text
alert_id              alert_type             severity
entity_id             location_id            title
description           opened_at              last_updated_at
resolved_at           acknowledged_at        status
deduplication_key     source_event_ids       recommended_actions
notification_policy   metadata

status: open | acknowledged | suppressed | resolved | expired
```

One persistent incident updates one alert. Track first/last seen, occurrence count, and latest supporting event.

Attention item:

```text
attention_item_id       priority               reason
entity_id               alert_id               created_at
available_after         expires_at             interruptibility
preferred_channel       cooldown_key            delivery_status
conversation_relevance  metadata

interruptibility: immediate | interrupt_when_safe | next_interaction | passive
```

Notification request/delivery records include:

```text
notification_request_id alert_id                attention_item_id
channel                 recipient_or_endpoint   attempted_at
status                  error                   retry_count
acknowledged_at         provider_message_id     metadata
```

Adapters implement a single typed send interface and define what confirmation means for their channel. Persist each attempt/result; never mark delivered without adapter confirmation. Support cooldowns, rate limits, channel fallback, critical-delivery escalation, noncritical quiet hours, suppression during calls/conversations, and acknowledgement where available. Only channels already present must initially be implemented, but the interface remains extensible.

Critical delivery flow is deterministic: rule opens/updates alert; the same database transaction creates outbox work; a worker renders fallback wording, sends, persists result, and retries. Model wording may improve an existing notification asynchronously but never delay core delivery.

## Transactional outbox

Outbox records support notification, embedding generation, memory extraction, episodic/conversation summarization, retention, action dispatch, MQTT command, and external adapter update:

```text
outbox_id        work_type          aggregate_type    aggregate_id
payload          idempotency_key    created_at        available_at
attempt_count    next_attempt_at    last_error        locked_at
locked_by        completed_at       status
```

Workers claim safely with `SKIP LOCKED` or equivalent, process bounded batches, remain idempotent, apply bounded exponential backoff, release stale locks, expose backlog/oldest age, dead-letter unrecoverable work, support manual retry, and preserve sanitized error detail. Use at-least-once work execution with idempotent consumers and database uniqueness; never claim true end-to-end exactly once.

For an accepted event, write applicable immutable event, measurement, state/health changes, alert/attention changes, memory-candidate references, action request, and outbox records in one short transaction. Never perform notification, embedding, network, or LLM work inside it.

## Memory

Keep working/situational memory (seconds to hours) in state/session tables; archival numeric telemetry in time-series storage; procedural capabilities/topology/safety primarily in version-controlled configuration/docs. Long-term memory includes:

- Episodic: meaningful timestamped incidents/interactions linked to supporting events.
- Semantic: facts, names, preferences, and stable patterns with validity, contradiction, and supersession.

Memory row:

```text
memory_id              memory_type              statement
structured_content     importance               confidence
scope                  sensitivity              valid_from
valid_to               learned_at               expires_at
superseded_at          supersedes_memory_id     status
embedding_model        embedding_dimension      embedding_version
embedded_at            content_hash             created_at
updated_at             last_accessed_at         access_count
metadata

status: candidate | accepted | rejected | active | superseded | expired | deleted
sensitivity: normal | personal | sensitive | restricted
```

Use a separate provenance relation to event IDs, message IDs, conversation IDs, source IDs, user confirmation, extraction job, and model/prompt version. Keep conversations, messages, and sessions separate from memories.

Write paths:

1. Deterministic facts may be written when unambiguous and policy permits (firmware/device changes, explicit names/preferences, recorded incidents).
2. An LLM may propose strict structured candidates only. The manager validates schema and evidence, rejects unsupported claims, detects duplicates/contradictions, assigns confidence/sensitivity, merges or supersedes appropriately, records the decision, and queues embeddings.
3. Periodic consolidation may group episodes, form configured daily/weekly summaries, reduce redundancy, preserve sources, supersede outdated facts, decay weak inference, retain explicit user evidence more strongly, and re-embed changed summaries.

Never overwrite changed semantic facts in place: close validity, mark superseded, preserve the old row, and link the new one. If evidence conflicts inconclusively, keep both with a conflict relationship. Explicit user evidence outweighs weak inference.

Use configured local Ollama embeddings for selected semantic memories, episodes, selected conversation summaries, device documentation, troubleshooting summaries, and notes. Do not embed every message/sample/heartbeat, raw audio, redundant events, or binaries. Hybrid retrieval combines vector, full-text/keyword, filters, recency, importance, confidence, entity/location, and validity, returning component scores for debugging. If embeddings are unavailable, text is stored and queued; exact/full-text retrieval continues.

Create episodes for meaningful incidents, completed tasks/interactions, notable transitions, explicit preferences, novel patterns, selected conversations, or configured end-of-day summaries—not blindly every fixed interval.

## Context and read tools

A deterministic situation snapshot contains an `as_of`, qualified likely user presence/activity, active alerts, relevant current state with temporal status/age, recent changes, and health. Context priority is:

1. fixed system/safety instructions;
2. active critical alerts;
3. current user request;
4. relevant current state;
5. directly related recent events;
6. high-confidence relevant memories;
7. needed conversation history;
8. lower-confidence inference;
9. background context.

Configure separate budgets for system instructions, recent conversation, situation, memories, tool results, and reserved response. Use the actual tokenizer when feasible or a conservative estimate. Truncate lowest priority first; critical alerts survive. Audit each selected item's ID, provenance, selection reason, temporal status, tokens, priority, and truncation.

Every model-visible fact exposes temporal status, observation/receipt times, age, expiry, state status, confidence, and source. Conversational rendering may add relative time but structured timestamps remain in tool output.

Minimum strict, bounded read tools:

```text
get_current_state       get_room_state           get_entity
get_active_alerts       get_attention_items      get_recent_events
query_sensor_history    search_memory            get_device_health
get_event_provenance    get_system_health        get_system_capabilities
```

Validate inputs; enforce time/result/tool-round limits; log calls/failures; return clear model-visible errors; expose no arbitrary SQL or unrestricted file access. Tool output is summarized/bounded before reentering context.

## Actions

Action registry definition:

```text
action_name          target_type             parameter_schema
permission_level     requires_confirmation   safety_checks
cooldown             timeout                 rollback_behavior
idempotency_behavior allowed_states
```

Lifecycle:

```text
requested -> validated -> awaiting_confirmation -> approved -> dispatched
          -> acknowledged -> completed | failed | timed_out | cancelled
```

Store every transition. Command records include command ID, idempotency key, target, parameters, requesting actor, correlation ID, timeout, and acknowledgement requirements. Reject unsupported actions; the LLM cannot publish arbitrary MQTT, run shell, bypass confirmation, or invent actions. Do not treat silence as success; confirm resulting state after acknowledgement where possible. Critical devices retain local firmware/hardware shutdown.

## Artifacts and retention

Artifact metadata:

```text
artifact_id             artifact_type            path
mime_type               size_bytes               sha256
created_at              source_id                related_event_id
related_conversation_id retention_class          expires_at
metadata
```

Store audio, images, firmware, diagnostic dumps, long recordings, and generated reports outside PostgreSQL with safe path handling. LLM tools never choose arbitrary paths.

All retention durations are configurable. Starting classes are: critical safety indefinite; important transitions long-term; raw high-frequency telemetry 7-30 days; one-minute aggregates 1-2 years; hourly/daily aggregates long-term or indefinite; heartbeats brief; debug logs 7-14 days; raw audio quickly deleted unless explicitly retained; transcripts configurable; semantic memory until superseded/expired/deleted; situation absent or brief; unresolved-alert evidence protected.

## Minimum tables and constraints

The database design covers:

```text
locations, entities, entity_relationships, sources, source_health_history,
events, dead_letter_events, current_state, sensor_measurements, alerts,
alert_events, attention_items, notification_requests, notification_deliveries,
memories, memory_embeddings, memory_provenance, memory_relationships,
conversations, messages, sessions, artifacts, automation_actions,
automation_action_events, outbox, retention_policies, schema_registry,
context_selection_audit, tool_call_audit
```

Use version-controlled migrations, never dynamic runtime table creation. Enforce unique event ID; unique source/boot/sequence when present; one active state per entity/property; unique active incident deduplication key where appropriate; unique outbox idempotency key. Index events by observation/receipt time, source/entity/type/severity/correlation; state by entity/location/status; telemetry by time/dimensions; memory by type/status/validity/scope and a dataset-appropriate vector index; notifications by status/retry; outbox by status/availability.

## Configuration and API contracts

Typed startup configuration includes:

```text
database_url, mqtt_host, mqtt_port, mqtt_tls, mqtt_ca_path,
mqtt_client_cert, mqtt_client_key, mqtt_username, mqtt_password,
mqtt_client_id, mqtt_topic_prefix, ollama_host, chat_model, embedding_model,
token_budgets, retention_policies, alert_thresholds, heartbeat_intervals,
stale_thresholds, offline_thresholds, notification_channels,
worker_concurrency, data_directory, logging_level, device_registry_location,
api_bind_host, api_port
```

Validate at startup and fail with actionable sanitized messages. MQTT settings also cover keepalive, clean-start/session behavior, reconnect backoff, and per-topic-class QoS; never hard-code broker IP or credentials.

Where an internal HTTP API fits repository conventions, cover health/components, entities/state, location state, bounded events, alerts/acknowledgement, attention, source health, memories/search/candidates, system situation, and action request/status. Add pagination and time-range limits, authentication/authorization for state-changing endpoints, and no arbitrary SQL/shell/filesystem access.
