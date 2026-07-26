# Quad Pump Firmware and Awareness Integration Plan

## Purpose

Replace the legacy quad-pump Pico W firmware with safe, non-blocking firmware
that:

- controls four pump relays independently;
- monitors the fuse-sense input associated with each relay;
- accepts only bounded, validated commands from the TALOS awareness action
  service;
- reports relay, fuse, command, health, and liveness data through the existing
  awareness MQTT ingestion path; and
- continues to enforce immediate pump safety locally when Wi-Fi, MQTT, the
  awareness backend, its database, or the LLM is unavailable.

This document is an implementation plan, not permission to begin unrelated
awareness work. A coding agent assigned this task must follow the repository
`AGENTS.md`, preserve unrelated working-tree changes, remain within this
firmware/integration boundary, and stop after the acceptance criteria in this
document are met.

## Required reading

Read these files before changing code:

1. `AGENTS.md`
2. `docs/awareness-memory/IMPLEMENTATION_STATUS.md`
3. `docs/awareness-memory/ARCHITECTURAL_INVARIANTS.md`
4. `docs/awareness-memory/OPEN_QUESTIONS.md`, especially OQ-C
5. The MQTT ingestion and Actions sections of `talos/awareness/README.md`
6. `Peripherals/quad_pump/main.py`
7. `Peripherals/quad_pump/simple.py`
8. `talos/awareness/actions/actions.toml`
9. `talos/awareness/actions/registry.py`
10. `talos/awareness/actions/service.py`
11. `talos/awareness/ingestion/normalization.py`
12. `talos/awareness/registry/bootstrap.py`
13. Relevant awareness action, ingestion, state, and simulator tests
14. `docs/awareness-memory/BROKER_HARDENING_PLAN.md`

Do not read unrelated awareness phase documents or the complete original
specification unless one of the listed references is genuinely ambiguous.

## Existing behavior and constraints

The current `Peripherals/quad_pump/main.py`:

- is MicroPython firmware for a Raspberry Pi Pico W;
- uses GPIO 16, 17, 18, and 19 as relay outputs;
- subscribes to `quad_pump/{pin}`;
- accepts bare `0` and `1` payloads;
- blocks the MQTT callback for eight seconds while watering;
- publishes only a final bare pin value to `status/{pin}`;
- has no reconnect loop, command identifier, deduplication, heartbeat, or
  useful health reporting;
- shares its MQTT client identifier with another Pico; and
- contains committed network credentials which must not be copied into new
  tracked files or emitted in test output.

The awareness backend already provides:

- canonical ingestion on `home/#`;
- versioned event envelopes with event IDs, boot IDs, sequence numbers,
  provenance, and bounded payloads;
- durable, validated physical action requests;
- QoS 1 command publication;
- command IDs and idempotency keys;
- source-bound command acknowledgement handling;
- timeouts and truthful failure transitions; and
- legacy adapters for the old `status/{pin}` messages.

The current `water_plants` action is still a legacy definition. It permits
only pins 17 and 19, publishes bare `1`, uses action-wide cooldown, and
completes when it observes the final legacy off state. The new integration
must migrate this definition without bypassing the action service or exposing
raw MQTT publication to the LLM.

The following architectural invariants are especially important:

- deterministic firmware, not the LLM, owns local timing and immediate
  shutdown;
- MQTT is transport, not authoritative storage;
- messages may be duplicated, delayed, reordered, interrupted, or missing;
- silence is never proof that a physical action succeeded;
- action parameters and MQTT topics/payloads remain strictly registered;
- physical action retries require safe device-side idempotency; and
- failures and degraded operation must be reported truthfully.

## Hardware gate: resolve before writing GPIO or fuse logic

The owner supplied this tentative mapping:

| Logical channel | Relay output | Fuse-sense input |
|---|---:|---:|
| 1 | 9 | 1 |
| 2 | 10 | 2 |
| 3 | 11 | 4 |
| 4 | 12 | 5 |

Post-deployment bench evidence resolved the numbering convention on
2026-07-26. The table contains MicroPython GPIO identifiers, not physical Pico
header pin numbers. The owner's earlier direct script using `Pin(9)` through
`Pin(12)` activated all four relays, with `1` meaning on. The implemented map
is therefore channel 1-4 → GP9-GP12, active-high. The corresponding fuse table
entries are GP1, GP2, GP4, and GP5; they remain unusable for the analog divider
signals because those pins are not ADC inputs, so fuse sensing stays disabled.

Do not assume whether these numbers are GPIO numbers, physical header pin
numbers, ADC channel numbers, or carrier-board labels. Record the confirmed
numbering in a named mapping table in code.

Resolve and record all of the following before implementing hardware access:

1. Exact controller model and MicroPython version.
2. Pin-numbering convention.
3. Relay polarity and the electrical state that means safely off.
4. Relay state during boot/reset while pins are high impedance.
5. Whether fuse-sense signals are digital good/blown indications or analog
   voltages.
6. Divider resistor values, maximum source voltage, expected good/blown
   readings, and whether those readings are valid when the relay is off.
7. Whether the signal presented to every MCU pin remains within the
   controller's electrical input limits under normal and fault conditions.
8. Maximum number of pumps that the power supply and wiring may operate
   simultaneously.
9. Maximum safe continuous run time for one pump.
10. Whether a detected blown fuse must inhibit startup, stop an active pump,
    or only report a fault.
11. Confirmed mapping between logical channels and physical pots/zones.

On a Pico W, GP1, GP2, GP4, and GP5 are digital-only. The board exposes only
three external ADC inputs, GP26 through GP28. If the four fuse signals require
analog measurement, stop and request a hardware decision: use a suitable
external ADC, revise the carrier board, or choose another controller. Do not
silently treat an analog divider as a reliable digital fuse detector.

Recommended fail-safe defaults, subject to owner confirmation:

- all relays off at boot and reset;
- one active pump at a time until the power budget is confirmed;
- a bounded run command rather than an indefinite on command;
- immediate local stop at the maximum run deadline;
- a confirmed fuse fault inhibits or stops its channel; and
- fuse status is `unknown`, not `ok`, whenever the circuit cannot distinguish
  the two states reliably.

Record confirmed hardware and policy decisions in
`docs/awareness-memory/DECISIONS.md`. Record unresolved questions in
`docs/awareness-memory/OPEN_QUESTIONS.md`; do not guess.

## Public device model

Use stable logical channel numbers 1 through 4 at every external interface.
Physical GPIO numbers belong only in the firmware hardware mapping.

Recommended actions:

### `run_pump`

Parameters:

- `channel`: integer, allowed values 1 through 4
- `duration_seconds`: bounded integer; default behavior and maximum must come
  from the confirmed hardware policy

Behavior:

- validate the complete command before changing a relay;
- reject a channel that is faulted, busy, or prohibited by the concurrency
  limit;
- turn the requested relay on;
- set a local, monotonic shutoff deadline;
- monitor stop commands and fuse state while running;
- turn the relay off at the deadline or earlier fault/stop; and
- acknowledge success only after the relay has been driven safely off.

### `stop_pump`

Parameters:

- `channel`: integer, allowed values 1 through 4

Behavior:

- immediately drive the channel off;
- be safe and idempotent when the channel is already off; and
- publish the resulting state and final command acknowledgement.

If the owner requires latched relay control, define a separate, explicitly
registered `set_pump` action with a boolean `state` parameter. Even then, the
firmware must enforce a local hard maximum-on time. Do not turn `run_pump`
into an unbounded command.

## Canonical MQTT contract

Use the existing canonical topic shape:

```text
home/irrigation/quad_pump/command
home/irrigation/quad_pump/state
home/irrigation/quad_pump/event
home/irrigation/quad_pump/health
home/irrigation/quad_pump/heartbeat
home/irrigation/quad_pump/telemetry/fuse_1_voltage
home/irrigation/quad_pump/telemetry/fuse_2_voltage
home/irrigation/quad_pump/telemetry/fuse_3_voltage
home/irrigation/quad_pump/telemetry/fuse_4_voltage
```

Only publish voltage telemetry when the hardware actually supplies trustworthy
analog readings. Digital fuse inputs should be represented as state and
transition events, not fabricated voltages.

Use a unique MQTT client identifier, such as
`talos-quad-pump-<stable-device-id>`. The device must not reuse the fan Pico's
client identifier.

### Incoming command

The awareness action service already produces the command envelope. Firmware
must parse a bounded JSON object containing fields such as:

```json
{
  "command_id": "UUID",
  "idempotency_key": "stable key",
  "action": "run_pump",
  "target_entity_id": "quad_pump",
  "parameters": {
    "channel": 2,
    "duration_seconds": 8
  },
  "actor": "llm",
  "correlation_id": "optional request correlation",
  "timeout_seconds": 20,
  "ack_mode": "command_ack",
  "ack_semantics": "execution_result",
  "issued_at": "timezone-aware ISO-8601"
}
```

Firmware validation must:

- enforce a conservative maximum payload length before JSON parsing;
- require a valid command ID and supported action;
- reject unknown or missing parameters;
- enforce parameter types and bounds;
- require the expected target entity;
- ignore model/user text and any unregistered extra instruction;
- never execute a command merely because a retained command is replayed;
- handle duplicate QoS 1 deliveries idempotently; and
- return a negative acknowledgement for a syntactically valid command that
  cannot be executed safely.

Device clock quality is currently untrusted. Do not reject solely on
`issued_at` unless clock synchronization is explicitly implemented and
registered. The awareness timeout and the local monotonic deadline remain the
authoritative bounds.

### Command acknowledgement

Publish final acknowledgements on the canonical event topic:

```json
{
  "event_id": "unique UUID",
  "event_type": "quad_pump.command_ack",
  "sequence": 42,
  "boot_id": "new stable value for this boot",
  "correlation_id": "command UUID",
  "severity": "info",
  "payload": {
    "command_id": "command UUID",
    "ok": true,
    "result": "completed",
    "channel": 2,
    "relay_state": "off",
    "fuse_state": "ok"
  }
}
```

Requirements:

- `event_type` must end in `command_ack`;
- `payload.command_id` must equal the received command ID;
- `payload.ok` must be a JSON boolean;
- a successful `execution_result` acknowledgement is emitted only after the
  requested physical operation has reached its defined end state;
- negative acknowledgements include a bounded, non-secret error code/message;
- duplicate commands return the recorded outcome without repeating the
  physical effect; and
- receipt/start progress, if reported, uses a different event type and must
  not be mistaken for final execution success.

Suggested negative result codes include `invalid_command`, `unsupported_action`,
`invalid_channel`, `invalid_duration`, `busy`, `fuse_fault`, `power_limit`,
`stopped`, and `internal_error`.

### State, health, and heartbeat

Publish a complete state snapshot:

```json
{
  "event_id": "unique UUID",
  "sequence": 43,
  "boot_id": "this boot",
  "payload": {
    "relay_1": false,
    "fuse_1": "ok",
    "relay_2": false,
    "fuse_2": "blown",
    "relay_3": false,
    "fuse_3": "unknown",
    "relay_4": false,
    "fuse_4": "ok"
  }
}
```

Publish state:

- once after safe GPIO initialization;
- once after every MQTT reconnect;
- whenever a relay or debounced fuse state changes; and
- at a bounded periodic snapshot interval if required for freshness.

Health may include firmware version, uptime, reset cause, watchdog state,
Wi-Fi connection state, MQTT connection state, and RSSI. Heartbeats should be
small and periodic. Do not include credentials, full exception traces, or
unbounded diagnostic data.

Every canonical message needs:

- a unique event ID;
- a monotonically increasing sequence within a boot;
- a new boot ID after every reset; and
- a payload compatible with `talos/awareness/ingestion/normalization.py`.

## Firmware architecture

Keep hardware and protocol behavior deterministic and testable. Prefer small
modules only when they contain behavior needed by this task; do not create
placeholder architecture for future peripherals.

Recommended responsibilities:

1. `config.py` or equivalent:
   non-secret defaults, topic names, limits, intervals, firmware version.
2. Device-local untracked secrets file:
   Wi-Fi and optional broker credentials. Provide a redacted example.
3. Hardware mapping/driver:
   channel-to-relay and channel-to-fuse mapping, relay polarity, safe
   initialization, reads, and writes.
4. Channel controller:
   per-channel state, monotonic deadlines, concurrency checks, stop/fault
   handling.
5. Fuse monitor:
   bounded sampling, debounce, optional averaging/hysteresis, and transition
   detection.
6. Protocol codec:
   bounded JSON command parsing, schema validation, canonical event creation.
7. Command ledger:
   command ID deduplication and bounded outcome retention.
8. MQTT supervisor:
   Wi-Fi/MQTT reconnect with bounded backoff, resubscription, keepalive,
   last-will, and outbound publishing.
9. Main loop:
   short, non-blocking ticks for MQTT, deadlines, fuse monitoring, health,
   heartbeat, watchdog feeding, and bounded outbound work.

Do not sleep for the watering duration inside the MQTT callback. Use
`time.ticks_ms()`/`time.ticks_diff()` or their runtime-appropriate monotonic
equivalents so counter wraparound is handled correctly.

The MQTT callback should only validate/enqueue bounded work. It must remain
responsive to stop commands, duplicate delivery, pings, and network failure
while a pump is active.

## Command idempotency

The awareness action registry currently uses `at_most_once` because the
legacy Pico cannot deduplicate commands. The new canonical path should
eventually use `device_key`, but only after device-side deduplication is
proven safe across resets.

Implementation requirements:

- keep a bounded in-memory ledger of recent command IDs and outcomes;
- persist enough accepted/completed command information to prevent a reboot
  during the awareness retry window from repeating a watering action;
- use an atomic or recoverable persistence format;
- bound flash writes and document the wear tradeoff;
- replay the prior acknowledgement for a completed duplicate;
- resume safe shutdown, not an unbounded run, after a reset; and
- test power loss before acceptance, during operation, after relay-off, and
  before acknowledgement publication.

Until persistent deduplication passes those tests, leave the action definition
as `at_most_once`. Adding command acknowledgements alone does not make retries
safe.

## Fuse monitoring

Model fuse state as an explicit enum:

- `unknown`
- `ok`
- `blown`
- optionally `invalid` for out-of-range or contradictory readings

For digital inputs:

- configure the confirmed pull mode;
- define the good/blown truth table from circuit evidence;
- sample repeatedly over a bounded debounce window; and
- publish only the debounced state.

For analog inputs:

- use only confirmed ADC-capable pins or an approved external ADC;
- convert raw counts using the confirmed reference and divider ratio;
- use averaging or a median filter;
- apply separate enter/exit thresholds to avoid chatter;
- keep raw value, calculated voltage, threshold version, and quality
  available for diagnostics; and
- publish a fuse transition event immediately.

If the circuit can assess a fuse only while its relay is energized, publish
`unknown` while off. Do not report `ok` from an electrically unobservable
state.

## Network and credential behavior

Implement:

- unique client identity;
- MQTT keepalive;
- bounded reconnect with exponential backoff and jitter;
- resubscription after reconnect;
- QoS 1 command subscription;
- QoS 1 command acknowledgements and important state/fault events where the
  MicroPython client can support them safely;
- a retained last-will health/offline indication;
- a fresh non-retained boot/reconnect snapshot; and
- bounded outbound buffering for important state/ack events.

Local relay deadlines and fuse shutdown must continue while disconnected.
Never wait for network I/O before turning a relay off.

Do not commit real credentials. Add ignore rules narrowly if a new
device-local secrets filename is introduced. Existing committed credentials
must be rotated during deployment; deleting them from the latest file does
not remove them from Git history.

## Awareness backend changes

### Source registry

Register `home/irrigation/quad_pump/#` to the physical pump source and entity.
The existing bootstrap uses conflict-do-nothing behavior, so editing the seed
alone will not update an existing database row. Implement an explicit,
tested registry migration/update path that preserves intentional operator
configuration.

Keep clock quality as `server_received` unless the firmware implements and
proves clock synchronization. Keep source ownership strict so another MQTT
client cannot claim the pump source merely by including its source ID in a
payload.

### Action registry

Version the registry and migrate the legacy `water_plants` action to:

- logical channel parameters rather than GPIO numbers;
- the canonical command topic;
- envelope payloads;
- `ack_mode = "command_ack"`;
- `ack_semantics = "execution_result"`;
- the physical pump source as `ack_source_id`; and
- `idempotency_behavior = "device_key"` only after persistent firmware
  deduplication is accepted.

Decide whether to:

- preserve `water_plants` as the public name and add a separate operator-level
  `stop_pump`, or
- add internal `run_pump`/`stop_pump` actions while keeping the existing MCP
  `water_plants` tool as a compatibility wrapper.

Do not expose arbitrary duration, raw pin number, MQTT topic, or payload
content to model output. Duration must have a strict registry bound.

The current cooldown check is action-wide. If independent channel cooldowns
are required, implement and test cooldown scope by target/channel rather than
silently setting the global cooldown to zero. Firmware safety limits remain
mandatory either way.

### Entities and mappings

Confirm the logical mapping of channels to plant pots/zones. Add entities for
channels 3 and 4 only if they represent distinct known targets; do not invent
plant names or locations.

Preserve the existing user-facing `water_plants` behavior for currently
configured pots while migrating its implementation to logical channel IDs.

### Rules and alerts

If fuse faults should create awareness alerts, add a deterministic,
source-bound rule for a transition to `blown`. It should deduplicate one live
incident per channel and use the canonical event as evidence. Do not route
raw fuse samples or heartbeat traffic through the LLM.

## Compatibility and rollout

Use a staged migration so a partial deployment cannot silently disable
watering or cause duplicate activation.

1. Confirm hardware and protocol decisions and record them.
2. Add/update awareness source registration for canonical pump topics while
   leaving legacy action dispatch unchanged.
3. Implement firmware unit tests and a host-side protocol/device simulator.
4. Flash firmware that can temporarily accept both:
   - canonical commands; and
   - explicitly mapped legacy `quad_pump/{old_pin}` commands.
5. During the transition, publish canonical state and only the minimum legacy
   status needed by the existing action completion path.
6. Validate canonical ingestion, state, fuse data, heartbeat, reconnect, and
   command acknowledgements without switching production action dispatch.
7. Switch the action registry and compatibility wrapper to the canonical
   command.
8. Exercise all configured channels end to end through the awareness action
   API.
9. Update broker credentials and ACLs for the canonical topics.
10. Remove legacy command/status support only after the physical acceptance
    run passes and the owner approves retirement.

The old topics use pin numbers, while the new firmware uses logical channels
and different GPIOs. The legacy-pin-to-channel mapping must be explicit and
owner-confirmed. Never infer it from list ordering.

## Testing strategy

### Host-side firmware tests

Structure deterministic firmware logic so it can be tested with fake clock,
GPIO, fuse, persistence, and MQTT adapters. Cover:

- safe boot initialization;
- each relay polarity and channel mapping;
- all command schema failures;
- duration bounds;
- per-channel start, deadline, and stop;
- concurrency/power-limit policy;
- fuse debounce and hysteresis;
- fuse fault before and during a run;
- state/event/heartbeat envelope generation;
- boot ID and sequence behavior;
- duplicate commands in memory;
- duplicate commands after simulated restart;
- persistence corruption and recovery;
- queue bounds and overflow policy;
- MQTT disconnect/reconnect/resubscribe; and
- watchdog-safe main-loop behavior.

### Awareness unit and integration tests

Update or add focused tests for:

- canonical topic authorization and normalization;
- source spoof rejection;
- state classification for relay and fuse properties;
- all four allowed logical channels;
- rejection of GPIO numbers and out-of-range durations;
- exact registered command topic/payload generation;
- source-bound positive and negative command acknowledgements;
- wrong, malformed, late, and duplicate acknowledgements;
- timeout when final evidence is absent;
- persistent-idempotency retry semantics;
- compatibility behavior for existing `water_plants` callers;
- cooldown scope; and
- deterministic fuse-fault alerting, if added.

Use the repository's test-only Mosquitto, never the production Pi broker by
default.

### Bench tests without pumps

Before attaching pumps:

- verify all MCU input voltages with a meter;
- use LEDs or a current-limited dummy load for relay outputs;
- verify all outputs remain off during boot, reset, firmware exception, Wi-Fi
  loss, broker loss, and watchdog reset;
- verify each logical channel maps to exactly one relay and one fuse input;
- pull each fuse or simulate both sense states;
- validate noise/debounce behavior; and
- test duplicate QoS delivery and device reset at every command phase.

### Physical acceptance

With owner authorization and safe supervision:

1. Run each pump independently.
2. Verify actual run duration and immediate stop.
3. Verify the configured simultaneous-pump limit.
4. Remove each fuse and confirm detection, reporting, and local safety action.
5. Interrupt Wi-Fi and restart the broker during a run; the local deadline
   must still stop the pump.
6. Reset the Pico during a run; every relay must become/remain safely off and
   the same command must not be replayed as a new watering cycle.
7. Run through `POST /actions/request` or the preserved awareness tool and
   verify durable lifecycle transitions:
   `requested -> validated -> approved -> dispatched -> acknowledged ->
   completed`.
8. Verify negative device results become `failed`, missing results become
   `timed_out`, and silence is never success.
9. Verify current state, event history, source health, and any fuse alert
   evidence in awareness.

## Acceptance criteria

Implementation is complete only when all applicable statements are true:

- Hardware numbering, polarity, fuse semantics, voltage limits, power limit,
  maximum run time, and pot mapping are confirmed and documented.
- All relays initialize and fail safely off.
- Each logical channel controls only its mapped relay.
- The main loop remains responsive while any pump is running.
- A local deadline stops every run without network/backend participation.
- Stop commands work during an active run.
- Malformed, unsupported, oversized, duplicate, and unsafe commands cannot
  produce an unintended physical effect.
- Device-side idempotency has the durability required by the selected action
  retry policy.
- Fuse states are truthful, debounced, and locally safety-enforced according
  to the confirmed policy.
- The device uses a unique MQTT identity and reconnects/resubscribes
  automatically.
- Canonical state, health, heartbeat, fault, and acknowledgement messages are
  accepted by awareness without dead letters.
- Awareness action completion requires a valid source-bound execution result.
- Existing supported `water_plants` callers continue to work through the
  action service.
- All focused tests and physical acceptance checks are reported exactly as
  run; unrun hardware checks are not described as passing.
- Real credentials are absent from new tracked files and deployment output.
- Relevant operational documentation, decisions, open questions,
  `IMPLEMENTATION_STATUS.md`, and the required session handoff are updated.

## Expected implementation touch points

The exact file split should follow repository conventions and remain as small
as practical. Likely touch points include:

- `Peripherals/quad_pump/main.py`
- a small number of new quad-pump firmware modules/config examples
- a narrow ignore entry for device-local secrets, if needed
- `talos/awareness/actions/actions.toml`
- `talos/awareness/actions/registry.py` and/or action service only where the
  existing registry semantics cannot express the confirmed contract
- `talos/awareness/registry/bootstrap.py` plus a safe existing-row migration
- relevant awareness rules, if fuse alerting is approved
- focused firmware and awareness tests
- `talos/awareness/README.md`
- `docs/awareness-memory/DECISIONS.md`
- `docs/awareness-memory/OPEN_QUESTIONS.md`
- `docs/awareness-memory/IMPLEMENTATION_STATUS.md`
- a session handoff based on
  `docs/awareness-memory/SESSION_HANDOFF_TEMPLATE.md`

Do not refactor the fan firmware, generic awareness architecture, unrelated
home-automation tools, speech/LLM pipeline, or other peripherals as part of
this task.

## Required final report and stop condition

The implementing agent's final report must state:

1. Confirmed hardware mapping and safety decisions.
2. Firmware behavior and MQTT contract implemented.
3. Awareness registry/action/rule changes.
4. Compatibility and deployment steps performed or still pending.
5. Every test and bench/physical check run, with exact pass/fail/not-run
   status.
6. Credential rotation or broker work still requiring owner execution.
7. Known limitations and unresolved questions.

Stop after the quad-pump firmware, its bounded awareness integration,
documentation, and relevant verification are complete. Do not start firmware
work for another peripheral, broker-wide hardening beyond the approved
quad-pump deployment step, or any new awareness phase without separate owner
authorization.
