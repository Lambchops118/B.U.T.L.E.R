# Security and Privacy

The subsystem contains sensitive household, device, voice, location, conversation, phone, and preference data. Security applies in every phase and is completed/audited in Phase 8.

## Local-only boundary

- Require no cloud database, cloud vector store, cloud embeddings, or cloud inference.
- Bind internal APIs to loopback or private LAN by default; never expose PostgreSQL publicly.
- Restrict database access with host firewall and credentials.
- Do not send stored data to external services unless an existing explicitly configured integration requires that data for its intended function.
- Document all network listeners, clients, data flows, and trust boundaries.

## MQTT and source identity

- Use the existing broker's authentication and ACLs; never hard-code address or credentials.
- Validate authenticated source identity, allowlisting, schema, size, and topic ownership before accepting data.
- Support broker TLS where practical, including CA and optional client certificate/key configuration.
- Preserve retained/replayed provenance and evaluate age.
- A trusted LAN does not eliminate authentication, authorization, or reliability requirements.
- Logs and diagnostics must not contain passwords, certificates/keys, tokens, or broker secrets.

## API and action authorization

- Authenticate state-changing endpoints and authorize actions and alert acknowledgements.
- Rate-limit sensitive endpoints, validate input sizes/types, and use CSRF protections where browser interfaces require them.
- Read tools use strict schemas, time/result/tool-round bounds, and audited failures.
- Expose no arbitrary SQL, shell execution, unrestricted filesystem access, or arbitrary MQTT publishing.
- Each action is registered with allowed target/parameters/states, permission, confirmation, safety, cooldown, timeout, rollback, idempotency, and acknowledgement policy. Record the requesting actor and every transition.
- Development mode must not silently bypass safety checks; any controlled exception is explicit and documented.

## Secrets

- Real secrets remain outside version control. Provide `.env.example` or the established equivalent without usable credentials.
- Use the repository's secret mechanism and least-privilege service identities.
- Startup failures are actionable but sanitized. Structured logs redact secrets while preserving correlation IDs.
- Backups exclude live secrets; restoration documents how secrets are re-provisioned.

## Memory privacy, deletion, and retention

Apply `normal | personal | sensitive | restricted` sensitivity labels. Sensitivity policy controls acceptance, retrieval, model context, export, and retention. LLM proposals never receive unrestricted permanent-memory write access.

Support configurable retention for voice audio, transcripts, location data, phone metadata, and personal preferences. Raw voice is deleted quickly unless explicitly retained. Explicit memory deletion is authorized, audited, and implemented without erasing audit/provenance beyond what policy and privacy obligations permit. Supersession preserves historical validity; deletion is a separate explicit lifecycle state.

Artifact paths are generated/validated by deterministic code, are rooted in configured storage, and resist traversal. LLM tools cannot select arbitrary paths. Store checksums, size, type, provenance, and expiry metadata.

## Backups and audits

Secure local backups cover PostgreSQL/schema version, artifact metadata/files, and non-secret configuration. Document access controls, encryption where available, retention, failure alerts, restore procedure, and last successful backup. Test restoration where feasible and never report backup readiness solely because a command is documented.

Security verification includes unauthorized topic/source rejection, API authn/authz, action permission/confirmation, input and result limits, log redaction, safe paths, retention/deletion protection, backup handling, and a check that no unapproved cloud dependency or public listener was introduced.
