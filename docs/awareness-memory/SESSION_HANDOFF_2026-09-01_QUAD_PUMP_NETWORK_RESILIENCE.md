# Session Handoff — Quad-Pump Network Resilience (Watchdog Reset Loop)

Session goal:
Determine why the pump controller board stopped working after several days idle, distinguish a code fault from hardware damage, and — after owner authorization — fix it and deploy.

Current phase:
Post-Phase-8 bounded quad-pump hardware-integration hotfix.

Bounded task completed:
Diagnosed and fixed a permanent watchdog reset loop in the quad-pump firmware's network path, deployed the fix to the Pico on COM6, and verified it against the original failure conditions on hardware.

## Hardware findings (no damage)

The board on COM6 is healthy. USB enumerates as `USB\VID_2E8A&PID_0005` with `ConfigManagerErrorCode 0`; MicroPython 1.28.0 on `RPI_PICO_W`, RP2040 at 125 MHz, UID `e66598541b809938`; 200,800 B free RAM against 4,640 B allocated (no leak or fragmentation); 757 KB of 868 KB flash free with all eleven firmware files present and the ledger intact. The radio joins in ~4 s at RSSI −32 dBm, DHCP issues `192.168.1.166`, and TCP to the broker at `192.168.1.160:1883` completes in 12 ms. The watchdog was confirmed armed and functioning: with the firmware interrupted at the REPL the board reset at 7.8 s.

## Root cause

Before anything was touched, the board reported `machine.reset_cause() == 3` (`WDT_RESET`). Its last restart was a watchdog timeout, not a power cycle.

`NetworkSupervisor.ensure_connected()` performed the Wi-Fi association wait **and** the MQTT connect inside a single call, while `main.py` fed the 8 s watchdog only once per loop iteration. Both legs were measured on the board:

- Wi-Fi join from cold: ~4,000 ms (previously bounded by `SOCKET_TIMEOUT_SECONDS = 5`)
- TCP connect to an unreachable LAN host with the firmware's own timeout: **5,003 ms**

That is ~9 s between two watchdog feeds against an 8,000 ms watchdog. The board reset *mid-connect*, before the failure was ever recorded as a backoff, then rebooted into exactly the same conditions and did it again — a permanent reset loop for as long as the broker was unreachable. This matches the reported symptom precisely: the board goes idle for days, the broker host goes down or changes address, and the pump controller loops forever and looks dead until something power-cycles it at a moment when the network happens to be healthy.

The RP2040 hardware watchdog maxes out at ~8,388 ms, so the budget cannot be raised to fit the network. The blocking work had to fit under it instead.

Worst case was larger than 9 s: `_connect_mqtt` also blocked on CONNACK and on three separate QoS 1 SUBACK reads, and `publish(qos=1)` blocks on its PUBACK, with up to four publishes drained per tick.

## Secondary defects fixed in the same path

1. **No liveness bound.** `_maybe_ping` sent PINGREQ every 20 s and updated `_last_ping_ms` unconditionally; nothing ever checked that a PINGRESP came back. A half-open TCP connection left the board reporting `mqtt_connected: true` indefinitely.
2. **The socket timeout was silently cleared.** `connect()` set a timeout, but `check_msg()` → `wait_msg()` called `sock.setblocking(True)`, equivalent to `settimeout(None)`. After the first poll every later read could block the main loop forever.
3. **`poll()` caught only `OSError`** while `publish()` caught broad `Exception`. The vendored client can raise `AssertionError`, `IndexError` or `MemoryError` on a malformed packet; those escaped into the main loop and stopped the relay deadline checks.
4. **No escalation.** `ensure_connected` retried forever at the 60 s cap, never resetting the CYW43 and never resetting the board, while the loop kept feeding the watchdog — hiding a wedged radio rather than recovering from it.
5. **Cosmetic:** `uptime_ms` was a `ticks_diff` against a boot reading, which goes negative after ~6.2 days (half the tick period).

## Changes made

`Firmware/qp_net.py` — the connect sequence is now a state machine staged across loop iterations (`IDLE` → start association, non-blocking → `WIFI` → poll `isconnected()` each tick → handshake). The MQTT handshake is the only blocking step left and is bounded by `SOCKET_TIMEOUT_SECONDS`, with watchdog feeds around it and between subscriptions. Added `stale()` liveness detection driven by an `activity_hook` the client fires for every received packet (PINGRESP included), `reset_radio()` for the escalation ladder, broad exception handling in `poll()`, watchdog feeding in `publish()`, wrap-safe tick arithmetic, and a `_restore_timeout()` in the vendored-client subclass that restores the socket timeout instead of clearing it.

`Firmware/qp_config.py` — `SOCKET_TIMEOUT_SECONDS` 5 → 3; new `WIFI_JOIN_TIMEOUT_MS = 8000`, `MQTT_INACTIVITY_TIMEOUT_MS = 90000`, `RECONNECT_RADIO_RESET_ATTEMPTS = 5`, `RECONNECT_HARD_RESET_ATTEMPTS = 20`, `OUTBOUND_DRAIN_PER_TICK = 4`. The measurements behind each value are recorded in the file.

`Firmware/main.py` — passes the watchdog to the supervisor; accumulates uptime tick by tick instead of differencing against a boot reading; reports `consecutive_failures` and `radio_resets` in health; and adds `_should_hard_reset()` / `_hard_reset()`, which reset the board after the full escalation ladder **only while no pump is running and none is queued**.

`Firmware/qp_controller.py` — `Clock.ticks_add()` and wrap-safe run-deadline arithmetic.

`tests/test_quad_pump_firmware.py` — `FakeClock.ticks_add`, plus a `NetworkSupervisorTest` class of 14 cases driving the real supervisor with only its two MicroPython-only steps stubbed.

Files modified:
`Peripherals/Pump-Power-Controller/Firmware/qp_net.py`, `qp_config.py`, `main.py`, `qp_controller.py` (submodule); `tests/test_quad_pump_firmware.py`; `docs/awareness-memory/DECISIONS.md`; `docs/awareness-memory/IMPLEMENTATION_STATUS.md`.

Files added:
`docs/awareness-memory/SESSION_HANDOFF_2026-09-01_QUAD_PUMP_NETWORK_RESILIENCE.md`

Migrations added:
None.

Decisions made:
ADR-030 (stage the connect inside the watchdog budget), ADR-031 (liveness bound plus radio/board reset escalation ladder), ADR-032 (`WIFI_JOIN_TIMEOUT_MS` as a measured stall detector).

Assumptions confirmed or changed:
The comment in `qp_net.py` claiming "the watchdog period is set longer than [the socket timeout]" was false for the combined path and is now replaced by an explicit per-call budget. Measurement also changed the Wi-Fi timeout: over repeated cold starts, bring-up took ~970 ms and a successful association ~4.1 s, but one association stalled and never completed within 40 s, recovering only when `connect()` was re-issued. A stalled association is therefore a retry condition, not something to wait out.

Tests run:
`python -m unittest tests.test_quad_pump_firmware`; `python -m py_compile` on the four changed firmware modules.

Tests passed:
79/79 (65 pre-existing plus 14 new). No existing test required changing.

Tests failed:
None.

Deployment performed:
`main.py`, `qp_config.py`, `qp_controller.py` and `qp_net.py` copied to the Pico on COM6 with `mpremote` 1.28.0. Because the armed watchdog would have reset the board mid-copy, `main.py` was renamed aside and the board reset so it booted to a REPL with no watchdog; files were copied, verified, and the boot path restored. On-device SHA-256 of all four matches the repository byte-for-byte. `qp_ledger.py` and `qp_protocol.py` still differ from the repository by line endings only (CRLF in git, LF on device), unchanged from the previous session and confirmed by hashing the LF-normalized repository copies.

On-hardware verification of the fix:
The original failure conditions were reproduced against the fixed firmware — a forced cold Wi-Fi join plus a deliberately unreachable broker (`192.168.1.199`), driven exactly as `main.py` drives it with one watchdog feed per iteration and the supervisor's own feeds disabled. Result: **survived 45 s with no reset**, worst single `ensure_connected()` call **3,218 ms** against the 8,000 ms budget, 5 failed attempts correctly recorded with backoff, and the escalation ladder firing one radio reset at the fifth failure. The same supervisor logic then connected to the real broker in 4,513 ms.

Live after deployment: `wifi_connected: true`, `mqtt_connected: true`, `rssi: -32`, `reconnects: 1`, `consecutive_failures: 0`, `radio_resets: 0`, `last_error: null`, watchdog enabled, all four relays off, heartbeats on schedule, time-to-online 4,147 ms (down from 19,473 ms before ADR-032).

Commands not run:
No pump was activated. Physical relay verification on GP6-GP9 remains outstanding and still needs the owner present, since it moves water.

Observation (not acted on):
The retained `{"online": false, "reason": "last_will"}` on `home/irrigation/quad_pump/health` persists while the board is online, because fresh health snapshots are published non-retained. Any new subscriber therefore sees "offline" as the retained value on that topic. This is pre-existing and arguably as designed; changing it was outside this task.

Security finding (still not acted on):
`Firmware/qp_secrets.py` remains **tracked in git** with the live Wi-Fi SSID and password on a GitHub remote, despite its own header stating the file is git-ignored. Rotate the credentials, then `git rm --cached` and add to `.gitignore`. Deleting it from the current tree does not remove it from history.

Known limitations:
The escalation ladder's final rung — `machine.reset()` after 20 consecutive failures — was verified by unit test and by inspection, not by driving a real board through 20 failures. The relay map remains netlist-derived and no relay has been observed to click. Fuse sensing remains unavailable (ADR-019, OQ-D).

Security implications:
None. No credentials, raw MQTT bypass, or authorization changes. All physical commands still traverse the registered action boundary and firmware safety limits, and the new board reset is explicitly gated on no run being in flight.

Deployment implications:
Done for the device. The `Peripherals/Pump-Power-Controller` submodule is **uncommitted** and still carries commit `25e3fd2` that the parent repository does not record, so both a submodule commit and a parent pointer bump are outstanding. Do not commit `qp_secrets.py` further.

Unresolved questions:
OQ-D (fuse hardware) remains open.

Current repository state:
Submodule working tree has the four modified firmware files uncommitted, plus the pre-existing unrecorded commit `25e3fd2`. Parent repository has `tests/test_quad_pump_firmware.py`, `DECISIONS.md`, `IMPLEMENTATION_STATUS.md` modified and this handoff added. Nothing was committed.

Next permitted task:
Commit the submodule and bump the parent pointer. Then owner-observed physical verification of GP6-GP9, one channel at a time, and credential rotation for the disclosed `qp_secrets.py` values.

Required reading for next session:
`IMPLEMENTATION_STATUS.md`; this handoff; ADR-030, ADR-031 and ADR-032; `Peripherals/Pump-Power-Controller/Firmware/qp_net.py`.

Explicit stop point:
Stop after the network-resilience fix, its tests, the verified deployment, and this documentation. Do not change fuse policy, the legacy cutover, broker security, or another peripheral without separate authorization.
