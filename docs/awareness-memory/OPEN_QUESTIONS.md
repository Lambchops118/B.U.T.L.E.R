# Open Questions

These questions require Phase 0 repository/deployment evidence or owner input. Confirmed source facts—local-first operation, reuse of the existing Mosquitto broker, central database authority, and deterministic safety behavior—are not open.

| ID | Question | Why it matters | Evidence / owner decision needed | Recommended starting interpretation |
|---|---|---|---|---|
| OQ-001 | What language, runtime, dependency manager, formatter, linter, type checker, entry points, and module boundaries are established? | Determines additive integration and tooling. | Repository inspection. | Use established suitable patterns before defaults. |
| OQ-002 | What database/ORM/migration/storage/cache code exists, and what can the target host deploy? | Determines whether defaults fit. | Repo and host/deployment evidence. | PostgreSQL is required by default; validate TimescaleDB and pgvector suitability. |
| OQ-003 | Can TimescaleDB and pgvector run reliably on the central host? | Affects telemetry and semantic retrieval. | Host resources, packaging, backups, operational constraints. | Use them when feasible; any local substitute must preserve section 5.4 properties. |
| OQ-004 | Is Home Assistant present, and who owns state, events, registry, recorder, and automations? | Avoids duplicate authoritative layers. | Repository/deployment inspection plus owner approval. | Present the three source-defined ownership options; do not choose silently. |
| OQ-005 | What MQTT topic conventions, schemas, client identities, QoS, session behavior, authentication, ACLs, TLS, and broker endpoint already exist? | Controls compatible ingestion and security. | Configuration and broker/client evidence; redact secrets. | Preserve existing topics; connect to the existing broker. |
| OQ-006 | Which remote devices/gateways can buffer events, and how reliable are their clocks and boot/sequence identifiers? | Determines loss, ordering, and timestamp guarantees. | Firmware/gateway inspection and owner/device documentation. | Claim only implemented guarantees; use received time when clocks are untrusted. |
| OQ-007 | What Ollama/Qwen client, tool-calling, tokenization, prompt, STT/TTS, conversation, phone, calendar, weather, and voice paths exist? | Defines adapters and Phase 5/6 integration. | Repository inspection and live configuration where safe. | Extend existing interfaces. |
| OQ-008 | Which notification endpoints exist, and what constitutes delivery or acknowledgement for each? | Determines adapter scope and truthful status. | Repo/configuration and owner policy. | Initially implement only existing channels behind an extensible interface. |
| OQ-009 | What action registry/layer, permissions, confirmations, safety checks, transports, and acknowledgements exist? | Phase 7 must integrate rather than replace. | Repository/device inspection and owner policy. | Add only missing lifecycle controls. |
| OQ-010 | What deployment and process-management mechanism is authoritative? | Determines startup, shutdown, health, and migrations. | Docker/Compose/systemd/supervisor/Kubernetes/launch configuration. | Use the existing mechanism; Docker Compose is only a default. |
| OQ-011 | What authentication, authorization, network binding, firewall, secret management, and API exposure already exist? | Determines security integration. | Repository and deployment evidence. | Bind privately and require auth for state changes. |
| OQ-012 | What event/telemetry volume, burst rate, retention target, disk budget, and context/model budget are expected? | Sets bounded queues, batching, indexes, capacity tests, and retention. | Measurements or owner estimates. | Configure conservative bounds and never shed critical safety events. |
| OQ-013 | What backup destination, schedule, encryption, restore objective, and artifact coverage are required? | Needed before durable data is operationally complete. | Existing backup policy and owner constraints. | Local secured backups; test restore where feasible. |
| OQ-014 | Which components must operate while the central host, broker, database, notification endpoint, or Ollama is unavailable? | Defines partition and recovery claims. | Deployment topology and device behavior. | Firmware retains immediate safety; backend reports degraded state truthfully. |
| OQ-015 | Are there source requirements that conflict with repository constraints? | Adaptations must preserve intent and be approved. | Phase 0 conflict inventory. | Record both facts, recommend an adaptation, and request owner confirmation. |

## Phase 0 resolution status (2026-07-16)

Evidence lives in [`DISCOVERY.md`](DISCOVERY.md) (section references below).

| ID | Status | Resolution |
|---|---|---|
| OQ-001 | Resolved | Python-only, run-in-place, per-process venvs, stdlib-first, no linter/typechecker; CI is compile-only (§1). |
| OQ-002 | Resolved | 3 SQLite stores, no ORM/migrations; defaults adopted via Docker (§7, §11, ADR-011). |
| OQ-003 | Resolved | Yes — `timescale/timescaledb-ha:pg17` runs on the dev machine; verified live by Phase 1 health checks (§11). |
| OQ-004 | Resolved | Home Assistant absent; backend is the authoritative layer (§8). |
| OQ-005 | Resolved | Flat legacy topics, QoS 0, no TLS/auth observed client-side; broker-side config still unverified (§3-§4; remaining part → OQ-B below). |
| OQ-006 | Resolved | No buffering anywhere; Pico clocks untrusted → `server_received` clock quality (§3, §9). |
| OQ-007 | Resolved | Responses API lane + OpenAI-compatible streaming seam; no embeddings; integration points mapped (§5; addendum §15 adds `POST /phone/events`). |
| OQ-008 | Resolved | No deterministic channel exists; v1 = GUI banner `POST /notify` + log adapter (§6, ADR-015). |
| OQ-009 | Resolved | No action registry; `water_plants`/`toggle_fan` fire-and-forget MQTT; Phase 7 wraps them (§4, §10). |
| OQ-010 | Resolved | No process manager in repo; plain venv processes + Docker Compose for the DB (§11, ADR-011). |
| OQ-011 | Resolved | Text-server bearer token + localhost/Tailscale allowlist is the precedent; awareness API binds loopback (§1, §10 C17). |
| OQ-012 | Resolved | Current traffic trivial; design target 10-100 msg/s burst; payload bound 64 KiB (§9 volumes). |
| OQ-013 | Open | Backup destination/schedule/restore objective — due before Phase 8; owner input needed. |
| OQ-014 | Resolved | Firmware behaviors + Pi TV controller run independently of the central host (§3). |
| OQ-015 | Resolved | 7 conflicts inventoried with adaptations (§9.1); owner approved via ADR-011..016. |

Remaining open items:

| ID | Question | Needed by |
|---|---|---|
| OQ-013 | Backup destination, schedule, encryption, restore objective. | Phase 8 (SEC-007) |
| OQ-B | Broker-side Mosquitto config (anonymous? ACLs?) on the Pi — unverifiable off-LAN; assumed anonymous. | Phase 8 hardening plan |
| OQ-C | Owner appetite for firmware fixes (unique client IDs, status-topic prefixes, abort-during-run, reconnect, acks) — currently out of scope per ADR-014. | Phase 7 device acceptance |

## Potential source tension to confirm

No direct contradiction was found in the source. One wording tension is deliberate: PostgreSQL/TimescaleDB/pgvector and the listed Python stack are strong defaults, while existing suitable repository technology takes precedence. Phase 0 must explicitly decide whether the defaults apply; it must not treat either side as an unconditional mandate.
