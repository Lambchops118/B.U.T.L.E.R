# Session Handoff — Quad-Pump Relay Activation Hotfix (GPIO Map)

Session goal:
Determine why `run_pump` / `water_plants` commands were accepted and acknowledged while no pump physically activated.

Current phase:
Post-Phase-8 bounded quad-pump hardware-integration hotfix.

Bounded task completed:
Traced the full dispatch path and reproduced the host's command envelope against the firmware parser: the envelope parses cleanly, `PumpController._run_command` reaches `_begin_run`, and `RelayBank.set` drives its mapped GPIO high. The failure is the mapping itself. Extracting the netlist from `Controller_Board_mk2.kicad_pcb` shows the relay drivers on GP6/GP7/GP8/GP9 (GPIO -> base resistor -> low-side NPN -> relay coil -> output terminal): GP6->R1->Q1->K4->J2, GP7->R4->Q4->K3->J3, GP8->R3->Q3->K2->J4, GP9->R2->Q2->K1->J5. The fuse dividers reach GP0-GP3 through R5-R8. The same netlist marks GP10, GP11 and GP12 `unconnected`, so `CHANNEL_RELAY_GPIO = {1: 9, 2: 10, 3: 11, 4: 12}` drove floating pins on channels 2-4. Corrected the relay map to GP6-GP9 and the fuse map to GP0-GP3, numbering channels in output-connector order J2-J5.

Separately, commit `25e3fd2` raised the firmware `DEFAULT_RUN_SECONDS` from 8 to 30 without touching the `water_plants` registry entry, whose `timeout_seconds` was still 20. Because the legacy path publishes `status/{pin} = 0` only after the cycle finishes, every successful 30 s run would have been marked timed out. Raised it to 45 s, matching `run_pump`.

Files modified:
`Peripherals/Pump-Power-Controller/Firmware/qp_config.py` (submodule); `Peripherals/Pump-Power-Controller/Firmware/qp_hardware.py` (submodule); `talos/awareness/actions/actions.toml`; `tests/test_quad_pump_firmware.py`; `docs/awareness-memory/DECISIONS.md`; `docs/awareness-memory/OPEN_QUESTIONS.md`; `docs/awareness-memory/IMPLEMENTATION_STATUS.md`.

Files added:
`docs/awareness-memory/SESSION_HANDOFF_2026-08-28_QUAD_PUMP_RELAY_ACTIVATION.md`

Migrations added:
None.

Decisions made:
ADR-028 records the netlist-derived map: relay channels 1-4 on GP6/GP7/GP8/GP9, fuse inputs on GP0/GP1/GP2/GP3, channels numbered in output-connector order, relays active-high (Q1-Q4 are low-side NPN switches). It supersedes ADR-022 and the GPIO portion of ADR-018. ADR-029 raises the `water_plants` timeout to 45 s.

Assumptions confirmed or changed:
ADR-022's GP9-GP12 table was never verified against hardware — its own handoff records that no physical command was issued after the change. It is contradicted by the board netlist and is now superseded. The owner confirmed that pot-to-channel assignment is not a firmware concern: pumps are reassigned by moving leads between the J2-J5 output terminals, which resolves OQ-E.

Tests run:
`python -m unittest tests.test_quad_pump_firmware`

`actions.toml` re-parsed and the `water_plants` / `run_pump` timeouts read back.

Tests passed:
65/65. Two stale assertions were updated as part of this work: `test_confirmed_owner_policy_is_encoded_in_config` expected `DEFAULT_RUN_SECONDS == 8`, and `test_concurrency_limit_rejects_a_second_pump` expected a `power_limit` rejection where commit `25e3fd2` now queues the run. The latter was renamed to `test_concurrency_limit_queues_a_second_pump` and extended to assert the queued channel starts on its own at the first run's deadline.

Tests failed:
None after the updates. Both failures above were pre-existing on entry to this session.

Commands not run:
No physical command was issued. The corrected firmware has not been copied to the Pico.

Known limitations:
The correction is netlist-derived, not bench-measured. It is only in the repository until `qp_config.py` and `qp_hardware.py` are copied to the Pico root and the board restarts. Fuse sensing remains unavailable (ADR-019, OQ-D). Firmware state reports remain commanded software state, not electrical feedback.

Security implications:
None. No credentials, raw MQTT bypass, or authorization changes. All physical commands still traverse the registered action boundary and firmware safety limits.

Deployment implications:
Copy `Peripherals/Pump-Power-Controller/Firmware/qp_config.py` and `qp_hardware.py` over `/qp_config.py` and `/qp_hardware.py` on the Pico, ensure the application file is `/main.py`, and restart the board. Confirm a fresh boot heartbeat, then issue one observed command per channel and watch for a relay click on each of GP6-GP9. The submodule change also needs a submodule commit and a parent-repo pointer bump.

Unresolved questions:
OQ-D (fuse hardware) remains open. OQ-E is resolved.

Current repository state:
The `Peripherals/Pump-Power-Controller` submodule carries a local commit (`25e3fd2`) that the parent repository does not yet record, plus the uncommitted `qp_config.py` / `qp_hardware.py` edits from this session. Nothing was committed.

Next permitted task:
Owner-executed flash and physical verification of GP6-GP9, one channel at a time.

Required reading for next session:
`IMPLEMENTATION_STATUS.md`; this handoff; `Peripherals/Pump-Power-Controller/Firmware/plan.md`; ADR-028 and ADR-029.

Explicit stop point:
Stop after the mapping correction, the timeout alignment, tests, and documentation. Do not change fuse policy, the legacy cutover, broker security, or another peripheral without separate authorization.
