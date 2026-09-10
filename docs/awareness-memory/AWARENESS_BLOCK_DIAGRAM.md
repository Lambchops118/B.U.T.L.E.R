# Awareness system block diagram

Repository implementation snapshot: 2026-09-08. These diagrams describe the checked-out code, including the announcement repairs, rather than verifying which version is running. Phase 9 is implemented but production acceptance remains pending; proactive briefing and its model ranking are opt-in.

## 1. Incoming information becomes durable awareness

```mermaid
flowchart TB
  DEV["Devices and sensors<br/>State, telemetry, events, heartbeat, acknowledgements"] --> MQTT["Existing Mosquitto broker<br/>MQTT transport, not the database"]
  HUMAN["Main agent and voice processes<br/>Presence, bounded interaction facts,<br/>job and tool outcomes"] --> HTTP["Loopback HTTP POST /ingest<br/>Internal and manual inputs"]
  subgraph BACKEND["Awareness backend — separate Python process"]
    MQTT --> IN["Shared ingestion pipeline"]
    HTTP --> IN
    REG["Source and entity registry<br/>Topic ownership, transport permissions,<br/>schemas, authority and freshness limits"] --> IN
    IN --> VALID["Authorize → bound size → normalize<br/>Validate schema → check sequence and boot ID"]
    VALID -->|rejected| DL["Dead-letter records<br/>Reason and bounded input"]
    VALID -->|accepted| TX["Database transaction<br/>Deduplicate event and record provenance"]
    TX --> EV["Immutable event history<br/>Observation, receipt and processing times"]
    TX --> STATE["Current state and transitions<br/>Order, authority, conflicts and deadbands"]
    TX --> TS["Numeric measurements<br/>TimescaleDB time series"]
    STATE --> RULE["Deterministic rules<br/>Hard rules first; no LLM"]
    EV --> RULE
    RULE --> ALERT["Alerts and evidence<br/>Open → acknowledged → resolved"]
    ALERT --> ATT["Attention items<br/>Priority, interruptibility,<br/>cooldown and delivery status"]
    ATT --> OUT["Transactional outbox<br/>Durable downstream work"]
    FRESH["Periodic freshness worker<br/>Detect stale values and offline sources"] --> STATE
    FRESH --> RULE
    TS --> AGG["Minute / hour / day aggregates<br/>Min, max, average, count, standard deviation"]
  end
  DB[("Local PostgreSQL + TimescaleDB + pgvector<br/>Authoritative persistence for the blocks above")]
  TX -.-> DB
  OUT -.-> DB
```

Accepted event storage, state/measurement effects, rule effects and associated outbox writes share a transaction. Network calls and model calls happen outside it. Late information can remain in history without rolling current state backward. Freshness qualifies whether a recorded value is still usable; a retained MQTT value alone is not proof of current state.

## 2. How the conversational agent knows what is happening

```mermaid
flowchart TB
  USER["User asks TALOS a question"] --> ROUTER["Main-agent router"]
  DATA[("Awareness records<br/>State, events, aggregates, alerts,<br/>attention, presence and delivery history")]
  DATA --> SNAP["Situation context broker<br/>Select relevant facts within a token budget<br/>Preserve critical alerts; audit inclusion"]
  SNAP --> CLIENT["HTTP awareness client<br/>Bounded timeout and short cache<br/>Legacy snapshot fallback on failure"]
  CLIENT --> ROUTER
  ROUTER --> LLM["Conversational LLM<br/>Receives selected context and tool access"]
  LLM -->|exact question| MCP["Narrow MCP tools → HTTP API<br/>Current state, event history, sensor history,<br/>alerts, provenance and system health"]
  MCP --> DATA
  MCP -->|bounded result with source and freshness| LLM
  LLM -->|similarity or remembered fact| SEARCH["Hybrid memory search<br/>Full-text + optional vector similarity<br/>Validity, sensitivity and confidence filters"]
  MEM[("Validated semantic and episodic memories<br/>Evidence, validity and supersession links")] --> SEARCH
  SEARCH --> LLM
  FACT["Explicit user facts or typed memory candidates"] --> GATE["Memory validation<br/>Evidence checks, deduplication,<br/>confidence and conflict handling"]
  GATE --> MEM
  RES["Resolved alert"] --> EP["Outbox: create incident memory"] --> GATE
  MEM --> EMB["Outbox: local Ollama embeddings<br/>Memory statements only"]
  EMB --> VEC[("pgvector embeddings")]
  VEC --> SEARCH
  LLM --> ANSWER["Reply through existing conversation / voice path"]
```

The snapshot includes alerts, attention, qualified state, meaningful transitions, source health, interaction-based owner presence and recent accepted awareness announcements. Presence is not a whole-home occupancy sensor. Exact questions such as “Is the pump on?” or “What was the average temperature?” use structured records, not vector similarity. Main-agent SQLite conversation storage remains separate; raw transcripts and telemetry do not become awareness memories automatically.

## 3. How TALOS speaks without a question

```mermaid
flowchart TB
  ALERT["Rule raises alert / attention"] --> NORMAL["General outbox worker<br/>Notification lane"]
  REM["Durable reminders + due-time worker"] --> ALERT
  MOMENT["Briefing moment worker — opt-in<br/>Scheduled time or arrival transition"] --> BOUT["Dedicated briefing outbox worker"]
  DB[("Stored alerts, attention, transitions,<br/>agent / interaction events and aggregates")] --> BUILD["Bounded briefing assembler<br/>Sourced candidates and statistical novelty<br/>Query window and selection provenance"]
  BOUT --> BUILD
  BUILD --> FILTER["Exclude already-delivered items<br/>Apply explicit dismissal / interest feedback"]
  FB[("Feedback stored as memories")] --> FILTER
  FILTER --> SELECT["Select candidate IDs<br/>Deterministic ordering by default<br/>Optional bounded local Ollama ranking"]
  SELECT --> GUARD["Code enforces valid IDs and critical protection<br/>Item cap + durable critical overflow batches<br/>Fallback if ranking fails"]
  GUARD --> WORDS["Deterministic spoken wording<br/>Diagnostic text kept separately"]
  NORMAL --> WORDS
  WORDS --> ADAPT["Notification adapters<br/>Preferred channel, then fallback"]
  ADAPT --> VOICE["Text server /speak<br/>Typed router announcement<br/>Voice worker → Polly TTS → audio"]
  ADAPT --> GUI["Text server /notify → GUI banner"]
  ADAPT --> LOG["Structured log fallback"]
  ADAPT --> RECEIPT[("Delivery ledger<br/>Attempts, status, rendered title / text,<br/>source references; playback unconfirmed")]
  RECEIPT --> RECALL["Recent accepted voice announcements<br/>Available to situation context and briefing history"]
  RECEIPT --> DEDUP["Delivery and attention bookkeeping<br/>Feeds later briefing exclusions"]
  DEDUP --> FILTER
  USER["User dismisses or expresses interest"] --> FB
```

The clock and arrival detection run in deterministic code. The optional model chooses from existing candidates; it does not invent facts, suppress critical items, or rewrite delivered speech. A delivery receipt proves adapter acceptance, not that audio played or a person heard it. Transport can still duplicate after an ambiguous crash; end-to-end exactly-once delivery is not claimed. Core awareness is local-first; the existing Polly speech integration is an external TTS dependency.

## 4. Physical actions and the safety boundary

```mermaid
flowchart TB
  REQ["User / agent requests a named action"] --> API["Authenticated action API<br/>Registered action only"]
  DEF["Versioned action definitions<br/>Schema, actor permissions, safety policy,<br/>confirmation, cooldown and timeout"] --> CHECK
  API --> CHECK["Deterministic validation<br/>Parameters → authorization → allowed state<br/>Cooldown → confirmation when configured"]
  CHECK -->|invalid| REJECT["Durable rejection with reason"]
  CHECK -->|approved| RECORD[("Action request and transition audit<br/>Idempotency key and command ID")]
  RECORD --> OUT["Transactional outbox<br/>Action dispatch and timeout work"]
  OUT --> MQTT["Registered MQTT command<br/>Payload generated from action definition"]
  MQTT --> DEVICE["Device firmware / hardware<br/>Immediate interlocks and local deadlines"]
  DEVICE -->|acknowledgement / reported state| IN["Normal ingestion and source validation"]
  IN --> COMPLETE["Action lifecycle evaluation<br/>Action-specific acknowledgement / state evidence"]
  OUT -->|deadline expires| TIMEOUT["Timed out / failed<br/>Silence never means success"]
  COMPLETE --> RECORD
  TIMEOUT --> RECORD
```

Physical retry policy is action-specific. Current pump paths use at-most-once dispatch where safe device-side deduplication has not been accepted. Firmware/hardware, not the backend or LLM, owns immediate physical safety. Current quad-pump fuse sensing is unimplemented and reported as unknown.

## Operations surrounding all four flows

Health endpoints, metrics and audit records expose disconnected sources, stale data, queue backlog and failures. Bounded outbox retries handle eligible work; failed work can be dead-lettered. Retention protects active evidence, refreshes required aggregates before deleting raw data, and runs in resumable batches. Memory consolidation, rooted artifacts and local backup/restore tooling support long-term operation.

## Source map and verification

- `talos/awareness/ingestion/pipeline.py`: shared ingestion and transaction effects.
- `talos/awareness/api/app.py`: composition and separate freshness, reminder, general-outbox and briefing workers.
- `talos/awareness/context/broker.py`: bounded conversational situation context and announcement recall.
- `talos/awareness/context/briefing.py` and `talos/awareness/briefing/selection.py`: candidate assembly and constrained model selection.
- `talos/awareness/README.md`: persistence, memory, delivery, actions and operational contracts.
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`: latest implementation and deployment limitations; newer repairs supersede historical snapshots.
- `docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`: permanent boundaries.

Documentation-only task. No runtime changes, service restarts, device commands or live deployment verification. No runtime tests run. Stop at the requested explanation/diagram boundary; no new phase is authorized.
