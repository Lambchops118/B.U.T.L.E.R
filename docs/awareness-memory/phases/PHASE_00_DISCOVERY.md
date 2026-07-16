# Phase 00 — Repository and Deployment Discovery

## Purpose

Establish evidence-backed repository, integration, security, and deployment facts before any runtime architecture is selected. Produce `DISCOVERY.md` and stop for owner review. This phase changes documentation only.

## Entry criteria

- The owner has assigned Phase 0 or a bounded subset.
- [`../IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) does not show Phase 0 completed and reviewed.
- No runtime implementation is authorized by this phase.

## Required reading

Read root `AGENTS.md`, status, this document, [`../ARCHITECTURAL_INVARIANTS.md`](../ARCHITECTURAL_INVARIANTS.md), [`../OPEN_QUESTIONS.md`](../OPEN_QUESTIONS.md), [`../reference/COMPONENT_MAP.md`](../reference/COMPONENT_MAP.md), and [`../reference/OPERATIONS_AND_DEPLOYMENT.md`](../reference/OPERATIONS_AND_DEPLOYMENT.md). Consult the original only for an ambiguity or targeted traceability question.

## Documents not normally needed

Do not load later phase documents, launcher prompts, the full schema reference, or the full original specification by default. Use targeted searches and bounded output; ignore dependencies, caches, logs, databases, models, and artifacts unless directly needed to establish a listed fact.

## Repository discovery required for this phase

Inspect and cite evidence for:

- language/runtime, dependencies, formatter/linter/type checker, entry points, and module boundaries;
- Ollama/Qwen and tool/function calling; STT/TTS, notifications, phone, calendar, weather, voice/conversation paths;
- databases, ORM, migrations, storage, cache, files, and backup/recovery;
- MQTT clients, topics, schemas, identities/credentials mechanism, QoS/session/reconnect, and broker configuration without exposing secrets;
- HTTP/WebSocket/serial/gRPC/other transports and Home Assistant integration;
- device/entity/sensor/action/event/conversation/agent-state models;
- deployment/process management, tests/fixtures/simulators/CI/logging/metrics;
- authentication, authorization, secrets, bindings, firewall/network assumptions; and
- every repository fact that conflicts with or constrains the source defaults.

Determine as far as evidence permits the Ollama/subsystem host, existing Raspberry Pi broker address/port, TLS/auth/ACLs, clients/gateways, endpoints, clock synchronization and embedded clock quality, network segments/hostnames/firewalls, and components that must operate during central-host failure.

## In scope

Read-only inspection, safe diagnostic commands, documentation of evidence, repository/deployment map, risk/conflict analysis, proposed component-to-repository mapping, technology recommendation, Home Assistant ownership alternatives, and documentation/status/handoff updates.

## Explicitly out of scope

No application code, dependencies, configuration/deployment changes, migrations, schemas/tables, modules, services, workers, tests for unimplemented runtime, broker deployment, placeholder integrations, or Phase 1 scaffolding. Do not contact/change live devices merely to prove availability without separate authorization.

## Architectural invariants that apply

All invariants matter to the recommendation, especially INV-07, INV-09 through INV-11, INV-18 through INV-20.

## Requirements implemented in this phase

DISC-001 through DISC-015 and the discovery portions of R20, OPS-001, and SEC-001 in [`../REQUIREMENTS_TRACEABILITY.md`](../REQUIREMENTS_TRACEABILITY.md). This phase verifies facts; it implements no runtime requirement.

## Dependencies on prior phases

None. The authoritative source and reorganized documentation must exist.

## Required deliverables

Create `docs/awareness-memory/DISCOVERY.md` unless repository convention strongly supports another documented path. Include:

- repository map and current runtime architecture;
- actual/known deployment topology and current MQTT/LLM/tool/notification/persistence paths;
- constraints, risks, conflicts, and current security/backup posture;
- mapping of C1-C18 to existing or proposed repository locations, without creating them;
- assumptions labeled exactly `confirmed_by_repo`, `confirmed_by_owner`, or `assumed_needs_confirmation` with evidence;
- recommended implementation sequence and database deployment method;
- explicit decision recommendation on the default stack; and
- owner decision checklist.

## Detailed implementation requirements

Use file/line/config evidence and distinguish current observation from inference. Preserve secrets by naming configuration keys/mechanisms rather than values. Identify existing suitable technology before recommending defaults. If Home Assistant is present, document overlap with its event bus, state machine, recorder, registry, and automations, then recommend one source-defined ownership model: HA authority; HA as source/backend authority; or a clearly bounded hybrid. Do not select it without owner approval.

For unknown topology facts, say unknown and state how the owner can confirm them. Document expected volumes and host capability when available. Do not claim remote buffering, clock trust, delivery, backup, or notification confirmation that cannot be demonstrated.

## Database or migration effects

None. Discovery may recommend deployment/migration choices but must not create or run them.

## Integration boundaries

Map existing interfaces and ownership without changing them. Keep broker transport, database authority, Ollama, notification endpoints, firmware safety, Home Assistant, and any current action layer visibly separate.

## Failure behavior

Failed or unavailable discovery commands are reported as limitations, not filled with guesses. Do not expose secrets in diagnostics. Do not change external state to overcome a read-only inspection limitation.

## Security considerations

Redact credentials/tokens/certificates and sensitive endpoints. Record listeners, bindings, auth/ACL/TLS and secret mechanisms, but never copy live values into `DISCOVERY.md`.

## Required tests

No runtime tests are required. Validate Markdown/links, confirm only documentation changed, and record safe discovery commands and their outcomes. If existing tests are run solely to identify the baseline, label them baseline checks and report failures unchanged.

## Acceptance criteria

- `DISCOVERY.md` covers every required repository/deployment item or explicitly marks it unknown with a confirmation path.
- Assumptions carry confidence-source labels and evidence.
- Existing integrations and conflicts are named; no speculative fact is presented as confirmed.
- Home Assistant gate is addressed and left for owner approval if applicable.
- C1-C18 receive an additive repository mapping and the default technology decision is explicit.
- No runtime/config/dependency/deployment file changed and no implementation began.

## Documentation updates

Add discovery output; update open questions and proposed decisions without converting recommendations to accepted owner decisions.

## Implementation status updates

Set Phase 0 to `awaiting owner review`, record files/commands/evidence, next permitted task as owner review only, and retain the Phase 1 gate.

## Required final report

Report files added/modified; migrations (`none`); evidence sources; architecture/deployment findings; assumptions; recommended decisions; conflicts/risks; commands/checks and results; security implications; unknowns; owner decisions; and the explicit stop.

## Stop condition

After `DISCOVERY.md`, status, decisions/questions, handoff, and report are complete, **stop**. Do not scaffold or implement Phase 1 until the owner reviews Phase 0 and explicitly authorizes continuation.
