# Session Handoff — Quad-Pump False "Offline" Announcements

Session goal:
Explain why Butler announces, roughly hourly, that the quad pump has been offline for a growing number of minutes (first announcement at 15 minutes) while the pump system is fully operational, and stop the false announcements.

Current phase:
Post-Phase-8 bounded awareness hotfix. No phase transition.

Diagnosis:
The registry holds **two** sources for the one physical pump board: `quad_pump_pico` (legacy `status/17-19` pin topics) and `quad_pump_canonical` (`home/irrigation/quad_pump/*`). On 2026-08-28 the board was reflashed with `quad_pump-2.0.0`, which publishes **only** on the canonical topics. Nothing has published `status/17-19` since, but `quad_pump_pico` stayed `enabled` with a `last_received_at` frozen at the reflash, so `FreshnessWorker._mark_offline_sources` marked it `offline` exactly `default_offline_after_seconds` (900 s = the observed 15 minutes) later and opened `source_offline:quad_pump_pico` — an incident naming the quad pump that nothing can ever resolve, because resolution requires a message on the dead topics. The pump itself keeps heartbeating every 30 s under `quad_pump_canonical`, which is why the hardware is fine while Butler is not lying about what the database says. The announcement wording ("offline for N minutes", growing) is the age rendered at speaking time — from `SituationBroker._age_text` for the HEALTH/ATTENTION lines, so it grows with the incident rather than resetting; the roughly hourly cadence matches `cooldown_seconds = 3600` on the `source_offline` attention rule in `rules/rules.toml`.

Two further defects of the same class were found and fixed:

1. `fan_pico` publishes `status/16` only when a command changes the pin. It has no heartbeat, so under the 900 s default it goes "offline" after every quiet quarter-hour. Silence carries no information for a command-driven source.
2. `IngestionPipeline._touch_source` (the duplicate-redelivery path) refreshed `last_received_at` but never restored `health_status`. A source whose messages arrive as redeliveries (retained replay, QoS-1 repeat) stayed reported as `offline` while demonstrably talking.

Changes made:
- `registry/bootstrap.py`: `quad_pump_pico` seeded and migrated to `enabled = false` (retired, not deleted — history keeps a valid foreign key and a legacy reflash only needs the flag flipped back). `fan_pico` and the retired legacy source get `offline_after_seconds = 0`. `quad_pump_canonical` gets `offline_after_seconds = 180` (six missed 30 s heartbeats) and `stale_after_seconds = 900` (three missed 300 s state snapshots), both derived from the firmware's own `qp_config` cadences — the previous 300 s default meant a snapshot arriving exactly on its own period flapped the relay/fuse rows to `stale`. Each change is a conditional `_SOURCE_MIGRATIONS` entry, so an operator's deliberate edit is left alone.
- `state/freshness.py`: new pure `offline_deadline()` — `None` falls back to the configured default, `<= 0` means the source publishes only on change and is never called offline. New `_retire_disabled_sources()` tick step returns a disabled source — and the state rows it last wrote — to `unknown` with a health-history row and a recorded `retired` state transition, and hands the hook a `source_retired` transition so the open incident is resolved. Idempotent and restart-safe like the rest of the worker.
- `api/app.py`: the freshness alert hook handles `source_retired` by resolving the open incident; `rules/engine.py`'s `apply_source_recovered` takes an optional `reason` so the resolution records "source retired in the registry" rather than claiming the device reported.
- `ingestion/pipeline.py`: a duplicate now restores `healthy`, records the health change (`duplicate_message_received`), and resolves the silence incident.
- `__main__.py`: new read-only `python -m talos.awareness sources` — every source with health, silence, effective deadline (`null` when liveness is not monitored) and any open `source_offline` incident. This is the one command that names the source behind an announcement.
- README and the plain-language guide document the retirement, the two suppression rules, and the new command.

Files modified:
`talos/awareness/registry/bootstrap.py`; `talos/awareness/state/freshness.py`; `talos/awareness/ingestion/pipeline.py`; `talos/awareness/api/app.py`; `talos/awareness/rules/engine.py`; `talos/awareness/__main__.py`; `talos/awareness/README.md`; `docs/awareness-memory/like_im_a_child_or_golden_retriever.md`; `docs/awareness-memory/IMPLEMENTATION_STATUS.md`; `tests/test_awareness_state_integration.py`.

Files added:
`tests/test_awareness_registry_unit.py`; this handoff.

Migrations added:
None. The registry changes ride the existing conditional `_SOURCE_MIGRATIONS` path applied at every `seed_registry`, so restarting the awareness backend applies them; no schema change was needed.

Decisions made:
No new ADR. The retirement follows the existing supersession decision recorded in the `quad_pump_pico` display-name migration; the deadline values are read from firmware constants rather than chosen.

Tests run:
`python -m unittest discover -s tests -p "test_awareness_*unit*.py"` (97 tests) and `python -m unittest discover -s tests -p "test_awareness_*integration*.py"`.

Tests passed:
97/97 unit tests pass, including the 9 new registry/deadline tests.

Tests failed:
None. **The 15 integration tests all skipped**: this session ran in a container with no Docker and no awareness Postgres, so the new `test_source_liveness_expectations` case (retirement clears the incident, the command-driven fan never goes offline, a duplicate restores health) has **not been executed**. It must be run against the awareness database before this is considered verified:
`docker compose -f docker-compose.awareness.yml up -d --wait` then
`python -m unittest tests.test_awareness_state_integration`.
The quad-pump firmware suite was not run either — the `Peripherals/Pump-Power-Controller` submodule is not checked out in this working copy — and no firmware file was touched.

Deployment performed:
None. Nothing was deployed to the Pico or the TALOS host.

Verification the owner should run on the live host, in order:
1. `.venv-awareness/bin/python -m talos.awareness sources` — before restarting. Expect `quad_pump_pico` with `health_status: offline`, a `last_received_at` frozen at the 2026-08-28 reflash and an `open_offline_alert`, alongside a healthy `quad_pump_canonical` with a recent `last_received_at`. That is the diagnosis, confirmed or refuted in one command.
2. Restart the awareness backend so `seed_registry` applies the migrations.
3. Re-run `sources`: `quad_pump_pico` should read `enabled: false`, `health_status: unknown`, `open_offline_alert: null`; `quad_pump_canonical` should read `offline_after_seconds: 180`.
4. `curl -s http://127.0.0.1:8600/alerts` — no open `source_offline` incident for the pump.

If step 1 instead shows `quad_pump_canonical` offline with a stale `last_received_at`, the diagnosis above is wrong and the board's canonical traffic is not reaching the backend (broker ACL, subscription, or ingress). In that case the tightened 180 s deadline will make the announcements *more* frequent, not fewer, and the dead-letter table (`SELECT reason, count(*) FROM dead_letters GROUP BY 1`) is the next place to look.

Known limitations:
The root cause is inferred from the repository and the 2026-08-28 handoff, not from live database rows — this session had no access to the deployed host. The hourly announcement cadence is consistent with the `source_offline` attention cooldown and with the situation brief being re-read, but which of those two paths actually spoke was not observed. Retiring `quad_pump_pico` means legacy `status/17-19` messages, if any board ever publishes them again, are dead-lettered as `source_disabled` until the flag is flipped back.

Security implications:
None. No credentials, authorization, or transport changes. The new CLI command is read-only and prints no secrets.

Deployment implications:
Restarting the awareness backend is sufficient; no migration, no firmware change, no broker change.

Unresolved questions:
None opened. The pre-existing `qp_secrets.py` credential disclosure from the 2026-08-28 session remains outstanding and untouched here.

Next permitted task:
Owner runs the four verification steps above and the integration suite against the awareness Postgres. Then the still-outstanding items from the previous handoff: owner-observed relay verification on GP6-GP9 and rotation of the disclosed Wi-Fi/broker credentials.

Required reading for next session:
`IMPLEMENTATION_STATUS.md`; this handoff; `SESSION_HANDOFF_2026-08-28_QUAD_PUMP_RELAY_ACTIVATION.md`; `talos/awareness/state/freshness.py`; `talos/awareness/registry/bootstrap.py`.

Explicit stop point:
Stop after the registry retirement, the freshness suppression rules, the duplicate-liveness fix, the diagnostic command, tests, and documentation. Do not change alert policy thresholds, the notification channels, or firmware without separate authorization.
