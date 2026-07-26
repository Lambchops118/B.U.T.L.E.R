# Session Handoff — 2026-07-26 — Quad Pump Firmware and Awareness Integration

```text
Session goal:
  Execute Peripherals/quad_pump/plan.md: replace the legacy quad-pump Pico W
  firmware with safe, non-blocking firmware and add its bounded awareness
  integration.

Current phase:
  Post-Phase-8 bounded task (owner-assigned). No awareness phase was started.

Bounded task completed:
  Firmware rewrite + host tests + canonical source registration + canonical
  run_pump/stop_pump actions + per-channel cooldown scope + documentation.
  Rollout steps 1-3 of the plan's staged migration. Steps 4-10 are physical
  and owner-executed.

Files added:
  Peripherals/quad_pump/qp_config.py
  Peripherals/quad_pump/qp_hardware.py
  Peripherals/quad_pump/qp_protocol.py
  Peripherals/quad_pump/qp_ledger.py
  Peripherals/quad_pump/qp_controller.py
  Peripherals/quad_pump/qp_net.py
  Peripherals/quad_pump/qp_secrets_example.py
  tests/test_quad_pump_firmware.py
  docs/awareness-memory/SESSION_HANDOFF_2026-07-26_QUAD_PUMP_FIRMWARE.md

Files modified:
  Peripherals/quad_pump/main.py            (full rewrite; credentials removed)
  .gitignore                               (Peripherals/quad_pump/qp_secrets.py)
  talos/awareness/actions/actions.toml     (v2; run_pump, stop_pump)
  talos/awareness/actions/registry.py      (cooldown_scope/cooldown_parameter)
  talos/awareness/actions/service.py       (parameter-scoped cooldown)
  talos/awareness/registry/bootstrap.py    (quad_pump_canonical + migrations)
  talos/mcp_servers/providers/awareness.py (tool description)
  talos/awareness/README.md
  docs/awareness-memory/DECISIONS.md       (ADR-018..021)
  docs/awareness-memory/OPEN_QUESTIONS.md  (OQ-C update; OQ-D, OQ-E, OQ-F)
  docs/awareness-memory/IMPLEMENTATION_STATUS.md
  tests/test_awareness_actions_unit.py
  tests/test_awareness_actions_integration.py
  tests/test_awareness_ingestion_unit.py
  tests/test_awareness_state_unit.py

Migrations added:
  None. No schema change was needed. Registry seed changes reach existing
  databases through bootstrap.apply_source_migrations (conditional updates).

Decisions made:
  ADR-018 hardware/safety policy (relays GP0-3, fuse GP6-9, active-high,
          1 concurrent pump, 30 s hard max, 8 s default, fuse fault inhibits
          start and stops a run, channel 1 = pot 1, channel 2 = pot 2)
  ADR-019 ship without fuse monitoring; fuse state is permanently "unknown"
  ADR-020 staged rollout; water_plants stays on the legacy topic for now
  ADR-021 at_most_once retained; additive per-parameter cooldown scope

Assumptions confirmed or changed:
  Confirmed by owner in chat: pin numbering (physical → GPIO), relay direction
  (relays GP0-3, fuse GP6-9 — the reverse of the plan's table columns), relay
  polarity, concurrency/run-time limits, pot mapping, fuse policy, and that the
  new board is built and ready to flash.
  NOT confirmed: which physical pot each relay GPIO drives (OQ-E). The firmware
  maps channel N → GP(N-1) in index order.

Tests run:
  python -m pytest tests/test_quad_pump_firmware.py
    -> 65 passed.
  python -m pytest tests/test_awareness_actions_unit.py
      tests/test_awareness_ingestion_unit.py tests/test_awareness_state_unit.py
      tests/test_awareness_actions_integration.py
    -> 4 skipped (awareness dependencies are not installed on this machine;
       only Python 3.9 is present and there is no .venv-awareness).
  python -m compileall Peripherals/quad_pump and every changed Python file
    -> passed.
  Structural validation of actions.toml against the registry's validator rules
  via a throwaway tomli script -> passed.

Tests passed:
  65 firmware host tests. Compile checks. actions.toml structural checks.

Tests failed:
  None.

Commands not run:
  Every awareness test (unit and integration) — skipped, not run. The new
  awareness assertions are unverified by execution.
  No firmware was flashed. No bench test, no meter reading, no dummy load, no
  fuse pull, no power-loss test, no Wi-Fi/broker interruption test, no physical
  acceptance run. No MQTT broker was contacted.

Known limitations:
  - Fuse sensing is not implemented; every channel reports "unknown" and no
    fuse interlock is active (ADR-019, OQ-D). The debounce/hysteresis and
    fuse-policy code paths exist and are tested with fake fuse readings, but
    the hardware cannot feed them.
  - Channel-to-pot ordering is unverified (OQ-E).
  - Device-side persistent deduplication is implemented and tested on the host
    across a simulated restart, but has not been power-loss tested on hardware,
    so the action registry stays at at_most_once.
  - The device clock is untrusted; issued_at is never a rejection input and the
    source keeps clock_quality = server_received.
  - The backend's own command publications return on its home/# subscription
    and dead-letter as unauthorized_topic (pre-existing behavior, OQ-F).
  - qp_net contains a copy of umqtt.simple's wait_msg, modified to expose the
    PUBLISH retain flag so a retained command replay cannot water a plant. Keep
    it in step if simple.py is ever updated.

Security implications:
  - The Wi-Fi SSID and password that were committed in the old main.py are gone
    from the current file but remain in Git history. **Rotate the Wi-Fi password
    before or during deployment.** Deleting the file does not remove them.
  - New secrets live in an untracked device-local qp_secrets.py; a redacted
    qp_secrets_example.py is committed and .gitignore covers the real file.
  - The device now uses a unique MQTT client ID (talos-quad-pump-<unique_id>)
    instead of sharing "pico-w-client" with the fan Pico.
  - Published payloads carry no credentials and bounded, non-secret error
    codes only (no tracebacks). A test asserts this.

Deployment implications:
  - Copy main.py, simple.py, qp_config.py, qp_hardware.py, qp_protocol.py,
    qp_ledger.py, qp_controller.py, qp_net.py, and a filled-in qp_secrets.py to
    the Pico root.
  - Broker ACLs must allow the new client ID on home/irrigation/quad_pump/#
    (see BROKER_HARDENING_PLAN.md). Owner-executed.
  - The awareness backend must be restarted so seed_registry adds
    quad_pump_canonical.

Unresolved questions:
  OQ-B (broker config/hardening, owner-executed), OQ-D (fuse ADC path),
  OQ-E (channel-to-pot verification), OQ-F (command-topic dead letters).

Current repository state:
  Runnable. No schema change, no behavior change to existing actions. The
  legacy water_plants path is untouched and the new firmware still answers it.

Next permitted task:
  Owner-executed bench validation on the new board, in the plan's order:
  meter every MCU input, LED/dummy-load the relay outputs, confirm outputs stay
  off through boot/reset/exception/Wi-Fi loss/broker loss/watchdog reset, verify
  each channel maps to exactly one relay (OQ-E), then duplicate-delivery and
  reset-at-every-command-phase tests. Physical acceptance follows.
  Switching water_plants to the canonical command, or moving the pump actions
  to device_key idempotency, requires a separate owner-authorized task after
  those checks pass.

Required reading for next session:
  Peripherals/quad_pump/plan.md (§Testing strategy, §Physical acceptance)
  talos/awareness/README.md (§Canonical quad-pump contract)
  docs/awareness-memory/DECISIONS.md ADR-018..021
  docs/awareness-memory/OPEN_QUESTIONS.md OQ-D, OQ-E

Explicit stop point:
  Stopped after firmware, bounded awareness integration, host tests, and
  documentation. No other peripheral's firmware was touched, no broker work was
  performed, and no new awareness phase was started.
```
