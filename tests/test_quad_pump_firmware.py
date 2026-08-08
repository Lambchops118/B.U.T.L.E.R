"""Host-side tests for the quad pump Pico W firmware logic.

The firmware modules that hold behavior (config, protocol, ledger, controller,
and the network module's pure helpers) import nothing MicroPython-specific, so
they run here against fake clock, GPIO, fuse, and persistence adapters. The
modules that do touch ``machine``/``network`` do so lazily.

No broker, no hardware, and no credentials are involved.
"""

import json
import os
import sys
import unittest

FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Peripherals",
    "Pump-Power-Controller",
    "Firmware",
)
if FIRMWARE_DIR not in sys.path:
    # The Pico filesystem is flat, so the firmware uses flat imports.
    sys.path.insert(0, FIRMWARE_DIR)

import qp_config as config  # noqa: E402
import qp_hardware as hardware  # noqa: E402
import qp_net as net  # noqa: E402
import qp_protocol as protocol  # noqa: E402
from qp_controller import FuseMonitor, Message, PumpController  # noqa: E402
from qp_ledger import PHASE_ACCEPTED, PHASE_FINAL, CommandLedger, MemoryStorage  # noqa: E402


# --- fakes --------------------------------------------------------------------


class FakeClock(object):
    def __init__(self, start=0):
        self.now = start

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, later, earlier):
        return later - earlier

    def advance(self, milliseconds):
        self.now += milliseconds


class FakePin(object):
    def __init__(self, gpio):
        self.gpio = gpio
        self.level = None
        self.history = []

    def value(self, level=None):
        if level is None:
            return self.level
        self.level = level
        self.history.append(level)
        return None


class FakePinFactory(object):
    def __init__(self):
        self.pins = {}

    def __call__(self, gpio):
        pin = FakePin(gpio)
        self.pins[gpio] = pin
        return pin


class FakeFuses(object):
    def __init__(self, states=None):
        self.states = dict(states or {})

    def read(self, channel):
        return self.states.get(channel, config.FUSE_UNKNOWN)

    def channels(self):
        return config.CHANNELS

    def snapshot(self):
        return dict((channel, self.read(channel)) for channel in config.CHANNELS)


def _sequential_uuids():
    counter = [0]

    def factory():
        counter[0] += 1
        return "00000000-0000-4000-8000-%012d" % counter[0]

    return factory


def _command(command_id, action=config.ACTION_RUN_PUMP, channel=1, **extra):
    body = {
        "command_id": command_id,
        "idempotency_key": "key-" + command_id,
        "action": action,
        "target_entity_id": "quad_pump",
        "parameters": {"channel": channel},
        "actor": "llm",
        "timeout_seconds": 20,
        "ack_mode": "command_ack",
        "ack_semantics": "execution_result",
        "issued_at": "2026-07-26T12:00:00+00:00",
    }
    if action == config.ACTION_RUN_PUMP and "duration_seconds" in extra:
        body["parameters"]["duration_seconds"] = extra.pop("duration_seconds")
    body.update(extra)
    return json.dumps(body).encode("utf-8")


UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def _build(clock=None, fuse_states=None, storage=None, ledger=None):
    clock = clock or FakeClock()
    factory = FakePinFactory()
    relays = hardware.RelayBank(pin_factory=factory)
    fuses = FakeFuses(fuse_states)
    events = protocol.EventFactory(
        boot_id="boot-test", uuid_factory=_sequential_uuids()
    )
    ledger = ledger or CommandLedger(storage or MemoryStorage(), capacity=4)
    monitor = FuseMonitor(fuses, clock, samples=3, interval_ms=100)
    controller = PumpController(relays, fuses, events, ledger, clock, fuse_monitor=monitor)
    return {
        "clock": clock,
        "pins": factory.pins,
        "relays": relays,
        "fuses": fuses,
        "events": events,
        "ledger": ledger,
        "monitor": monitor,
        "controller": controller,
    }


def _acks(messages):
    return [
        message.envelope
        for message in messages
        if message.envelope is not None
        and message.envelope.get("event_type") == config.EVENT_TYPE_COMMAND_ACK
    ]


def _states(messages):
    return [
        message.envelope
        for message in messages
        if message.topic == config.TOPIC_STATE
    ]


# --- hardware mapping and safe boot -------------------------------------------


class HardwareMappingTest(unittest.TestCase):
    def test_confirmed_channel_map_is_explicit_and_disjoint(self):
        self.assertEqual(config.CHANNEL_RELAY_GPIO, {1: 9, 2: 10, 3: 11, 4: 12})
        self.assertEqual(config.CHANNEL_FUSE_GPIO, {1: 1, 2: 2, 3: 4, 4: 5})
        relay_pins = set(config.CHANNEL_RELAY_GPIO.values())
        fuse_pins = set(config.CHANNEL_FUSE_GPIO.values())
        self.assertEqual(len(relay_pins), 4)
        self.assertEqual(len(fuse_pins), 4)
        self.assertEqual(relay_pins & fuse_pins, set())

    def test_initialize_drives_every_relay_to_the_safe_state(self):
        harness = _build()
        harness["relays"].initialize_safe()
        for channel in config.CHANNELS:
            gpio = config.CHANNEL_RELAY_GPIO[channel]
            self.assertEqual(harness["pins"][gpio].level, 0)
            self.assertFalse(harness["relays"].is_on(channel))

    def test_each_channel_drives_only_its_own_relay(self):
        harness = _build()
        harness["relays"].initialize_safe()
        harness["relays"].set(2, True)
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[2]].level, 1)
        for channel in (1, 3, 4):
            self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[channel]].level, 0)

    def test_active_low_polarity_inverts_the_written_level(self):
        factory = FakePinFactory()
        relays = hardware.RelayBank(pin_factory=factory, active_high=False)
        relays.initialize_safe()
        relay_gpio = config.CHANNEL_RELAY_GPIO[1]
        self.assertEqual(factory.pins[relay_gpio].level, 1)
        relays.set(1, True)
        self.assertEqual(factory.pins[relay_gpio].level, 0)

    def test_fuse_bank_reports_unknown_because_sensing_is_unavailable(self):
        fuses = hardware.FuseBank()
        self.assertFalse(fuses.available)
        for channel in config.CHANNELS:
            self.assertEqual(fuses.read(channel), config.FUSE_UNKNOWN)

    def test_boot_publishes_a_complete_state_snapshot(self):
        harness = _build()
        messages = harness["controller"].initialize()
        snapshots = _states(messages)
        self.assertEqual(len(snapshots), 1)
        payload = snapshots[0]["payload"]
        for channel in config.CHANNELS:
            self.assertIs(payload["relay_%d" % channel], False)
            self.assertEqual(payload["fuse_%d" % channel], config.FUSE_UNKNOWN)


# --- command validation --------------------------------------------------------


class CommandValidationTest(unittest.TestCase):
    def test_valid_command_is_normalized(self):
        parsed = protocol.parse_command(_command(UUID_A, channel=3, duration_seconds=5))
        self.assertEqual(parsed["action"], config.ACTION_RUN_PUMP)
        self.assertEqual(parsed["channel"], 3)
        self.assertEqual(parsed["duration_seconds"], 5)
        self.assertEqual(parsed["command_id"], UUID_A)

    def test_missing_duration_uses_the_registered_default(self):
        parsed = protocol.parse_command(_command(UUID_A))
        self.assertEqual(parsed["duration_seconds"], config.DEFAULT_RUN_SECONDS)

    def test_oversized_payload_is_rejected_before_json_parsing(self):
        oversized = b"{" + b"x" * (config.MAX_COMMAND_BYTES + 10) + b"}"
        with self.assertRaises(protocol.CommandError) as caught:
            protocol.parse_command(oversized)
        self.assertEqual(caught.exception.code, config.RESULT_INVALID_COMMAND)

    def test_schema_failures_map_to_registered_result_codes(self):
        cases = [
            (b"not json", config.RESULT_INVALID_COMMAND),
            (b"[]", config.RESULT_INVALID_COMMAND),
            (b"", config.RESULT_INVALID_COMMAND),
            (json.dumps({"action": "run_pump"}).encode(), config.RESULT_INVALID_COMMAND),
            (
                json.dumps({"command_id": "not-a-uuid", "action": "run_pump"}).encode(),
                config.RESULT_INVALID_COMMAND,
            ),
            (_command(UUID_A, action="drain_tank"), config.RESULT_UNSUPPORTED_ACTION),
            (_command(UUID_A, channel=0), config.RESULT_INVALID_CHANNEL),
            (_command(UUID_A, channel=5), config.RESULT_INVALID_CHANNEL),
            (_command(UUID_A, channel=17), config.RESULT_INVALID_CHANNEL),
            (_command(UUID_A, duration_seconds=0), config.RESULT_INVALID_DURATION),
            (
                _command(UUID_A, duration_seconds=config.MAX_RUN_SECONDS + 1),
                config.RESULT_INVALID_DURATION,
            ),
        ]
        for payload, expected in cases:
            with self.assertRaises(protocol.CommandError) as caught:
                protocol.parse_command(payload)
            self.assertEqual(caught.exception.code, expected, payload[:60])

    def test_gpio_numbers_are_not_accepted_as_channels(self):
        for gpio in (0, 6, 9, 16, 19):
            if gpio in config.CHANNELS:
                continue
            with self.assertRaises(protocol.CommandError) as caught:
                protocol.parse_command(_command(UUID_A, channel=gpio))
            self.assertEqual(caught.exception.code, config.RESULT_INVALID_CHANNEL)

    def test_booleans_do_not_satisfy_integer_parameters(self):
        body = json.loads(_command(UUID_A).decode())
        body["parameters"]["channel"] = True
        with self.assertRaises(protocol.CommandError) as caught:
            protocol.parse_command(json.dumps(body).encode())
        self.assertEqual(caught.exception.code, config.RESULT_INVALID_CHANNEL)

    def test_unregistered_parameters_are_rejected_not_ignored(self):
        body = json.loads(_command(UUID_A).decode())
        body["parameters"]["instruction"] = "also open the valve"
        with self.assertRaises(protocol.CommandError) as caught:
            protocol.parse_command(json.dumps(body).encode())
        self.assertEqual(caught.exception.code, config.RESULT_INVALID_COMMAND)

    def test_command_for_another_entity_is_rejected(self):
        body = json.loads(_command(UUID_A).decode())
        body["target_entity_id"] = "fan"
        with self.assertRaises(protocol.CommandError) as caught:
            protocol.parse_command(json.dumps(body).encode())
        self.assertEqual(caught.exception.code, config.RESULT_INVALID_COMMAND)

    def test_untrusted_issued_at_alone_never_rejects(self):
        body = json.loads(_command(UUID_A).decode())
        body["issued_at"] = "1970-01-01T00:00:00+00:00"
        self.assertEqual(
            protocol.parse_command(json.dumps(body).encode())["command_id"], UUID_A
        )

    def test_stop_pump_does_not_accept_a_duration(self):
        body = json.loads(_command(UUID_A, action=config.ACTION_STOP_PUMP).decode())
        body["parameters"]["duration_seconds"] = 5
        with self.assertRaises(protocol.CommandError) as caught:
            protocol.parse_command(json.dumps(body).encode())
        self.assertEqual(caught.exception.code, config.RESULT_INVALID_COMMAND)

    def test_generated_uuids_are_version_4_and_unique(self):
        first = protocol.new_uuid4()
        second = protocol.new_uuid4()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 36)
        self.assertEqual(first[14], "4")
        self.assertIn(first[19].lower(), "89ab")


# --- run control ----------------------------------------------------------------


class RunControlTest(unittest.TestCase):
    def test_run_energizes_the_mapped_relay_and_sets_a_local_deadline(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        command = protocol.parse_command(_command(UUID_A, channel=2, duration_seconds=5))
        controller.handle_command(command)
        self.assertTrue(controller.is_running(2))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[2]].level, 1)
        self.assertEqual(controller.deadline_for(2), 5000)

    def test_deadline_stops_the_pump_without_any_network_participation(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(
            protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=8))
        )

        clock.advance(7999)
        self.assertEqual(controller.tick(), [])
        self.assertTrue(controller.is_running(1))

        clock.advance(1)
        messages = controller.tick()
        self.assertFalse(controller.is_running(1))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].level, 0)

        acks = _acks(messages)
        self.assertEqual(len(acks), 1)
        self.assertIs(acks[0]["payload"]["ok"], True)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_COMPLETED)
        self.assertEqual(acks[0]["payload"]["relay_state"], "off")
        self.assertEqual(acks[0]["payload"]["command_id"], UUID_A)

    def test_success_is_acknowledged_only_after_the_relay_is_off(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        messages = controller.handle_command(
            protocol.parse_command(_command(UUID_A, duration_seconds=3))
        )
        # Nothing final while the pump is still running.
        self.assertEqual(_acks(messages), [])
        clock.advance(3000)
        self.assertEqual(len(_acks(controller.tick())), 1)

    def test_duration_is_clamped_to_the_local_hard_maximum(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        # Bypass the schema bound to prove the controller enforces its own.
        controller.handle_command(
            {
                "command_id": UUID_A,
                "action": config.ACTION_RUN_PUMP,
                "channel": 1,
                "duration_seconds": 6000,
                "correlation_id": None,
            }
        )
        self.assertEqual(
            controller.deadline_for(1), config.MAX_RUN_SECONDS * 1000
        )

    def test_stop_command_ends_an_active_run_immediately(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(
            protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=30))
        )
        clock.advance(500)
        messages = controller.handle_command(
            protocol.parse_command(
                _command(UUID_B, action=config.ACTION_STOP_PUMP, channel=1)
            )
        )
        self.assertFalse(controller.is_running(1))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].level, 0)

        # Two acknowledgements: the interrupted run fails truthfully, the stop
        # command succeeds. Neither request is left to time out.
        acks = _acks(messages)
        self.assertEqual(len(acks), 2)
        interrupted, stop = acks
        self.assertEqual(interrupted["payload"]["command_id"], UUID_A)
        self.assertIs(interrupted["payload"]["ok"], False)
        self.assertEqual(interrupted["payload"]["result"], config.RESULT_STOPPED)
        self.assertEqual(stop["payload"]["command_id"], UUID_B)
        self.assertIs(stop["payload"]["ok"], True)

    def test_stop_is_idempotent_on_an_idle_channel(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        messages = controller.handle_command(
            protocol.parse_command(
                _command(UUID_A, action=config.ACTION_STOP_PUMP, channel=4)
            )
        )
        acks = _acks(messages)
        self.assertEqual(len(acks), 1)
        self.assertIs(acks[0]["payload"]["ok"], True)
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[4]].level, 0)

    def test_concurrency_limit_rejects_a_second_pump(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(protocol.parse_command(_command(UUID_A, channel=1)))
        messages = controller.handle_command(
            protocol.parse_command(_command(UUID_B, channel=2))
        )
        self.assertFalse(controller.is_running(2))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[2]].level, 0)
        acks = _acks(messages)
        self.assertIs(acks[0]["payload"]["ok"], False)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_POWER_LIMIT)

    def test_stop_all_drives_every_relay_off(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(protocol.parse_command(_command(UUID_A, channel=3)))
        controller.stop_all("shutdown")
        self.assertEqual(controller.running_channels(), ())
        for channel in config.CHANNELS:
            self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[channel]].level, 0)


# --- fuse behavior ---------------------------------------------------------------


class FuseTest(unittest.TestCase):
    def test_unknown_never_inhibits_a_run(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(protocol.parse_command(_command(UUID_A, channel=1)))
        self.assertTrue(controller.is_running(1))

    def test_debounce_requires_consecutive_agreeing_samples(self):
        clock = FakeClock()
        fuses = FakeFuses()
        monitor = FuseMonitor(fuses, clock, samples=3, interval_ms=100)

        fuses.states[1] = config.FUSE_BLOWN
        for _ in range(2):
            clock.advance(100)
            self.assertEqual(monitor.sample(), [])
        self.assertEqual(monitor.state(1), config.FUSE_UNKNOWN)

        clock.advance(100)
        transitions = monitor.sample()
        self.assertEqual(transitions, [(1, config.FUSE_UNKNOWN, config.FUSE_BLOWN)])
        self.assertEqual(monitor.state(1), config.FUSE_BLOWN)

    def test_chatter_below_the_debounce_window_publishes_nothing(self):
        clock = FakeClock()
        fuses = FakeFuses()
        monitor = FuseMonitor(fuses, clock, samples=3, interval_ms=100)
        for index in range(10):
            fuses.states[1] = (
                config.FUSE_BLOWN if index % 2 else config.FUSE_UNKNOWN
            )
            clock.advance(100)
            self.assertEqual(monitor.sample(), [])
        self.assertEqual(monitor.state(1), config.FUSE_UNKNOWN)

    def test_sampling_respects_the_bounded_interval(self):
        clock = FakeClock()
        fuses = FakeFuses({1: config.FUSE_BLOWN})
        monitor = FuseMonitor(fuses, clock, samples=1, interval_ms=100)
        clock.advance(100)
        self.assertEqual(len(monitor.sample()), 1)
        clock.advance(10)
        self.assertEqual(monitor.sample(), [])

    def test_confirmed_blown_fuse_inhibits_start(self):
        clock = FakeClock()
        harness = _build(clock=clock, fuse_states={2: config.FUSE_BLOWN})
        controller = harness["controller"]
        controller.initialize()
        for _ in range(3):
            clock.advance(100)
            controller.tick()

        messages = controller.handle_command(
            protocol.parse_command(_command(UUID_A, channel=2))
        )
        self.assertFalse(controller.is_running(2))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[2]].level, 0)
        acks = _acks(messages)
        self.assertIs(acks[0]["payload"]["ok"], False)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_FUSE_FAULT)

    def test_fuse_fault_during_a_run_stops_the_pump(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(
            protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=30))
        )
        harness["fuses"].states[1] = config.FUSE_BLOWN

        messages = []
        for _ in range(3):
            clock.advance(100)
            messages.extend(controller.tick())

        self.assertFalse(controller.is_running(1))
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].level, 0)
        acks = _acks(messages)
        self.assertEqual(acks[-1]["payload"]["result"], config.RESULT_FUSE_FAULT)
        self.assertIs(acks[-1]["payload"]["ok"], False)

    def test_state_snapshot_reports_debounced_fuse_state(self):
        clock = FakeClock()
        harness = _build(clock=clock, fuse_states={3: config.FUSE_BLOWN})
        controller = harness["controller"]
        snapshot = controller.state_message().envelope["payload"]
        self.assertEqual(snapshot["fuse_3"], config.FUSE_UNKNOWN)
        for _ in range(3):
            clock.advance(100)
            controller.tick()
        snapshot = controller.state_message().envelope["payload"]
        self.assertEqual(snapshot["fuse_3"], config.FUSE_BLOWN)


# --- idempotency ------------------------------------------------------------------


class IdempotencyTest(unittest.TestCase):
    def test_duplicate_delivery_during_a_run_has_no_extra_physical_effect(self):
        harness = _build()
        controller = harness["controller"]
        controller.initialize()
        command = protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=10))
        controller.handle_command(command)
        history = list(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].history)

        self.assertEqual(controller.handle_command(command), [])
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].history, history)

    def test_duplicate_of_a_completed_command_replays_the_recorded_outcome(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        command = protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=2))
        controller.handle_command(command)
        clock.advance(2000)
        controller.tick()
        history = list(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].history)

        messages = controller.handle_command(command)
        acks = _acks(messages)
        self.assertEqual(len(acks), 1)
        self.assertIs(acks[0]["payload"]["ok"], True)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_COMPLETED)
        self.assertEqual(acks[0]["payload"]["command_id"], UUID_A)
        self.assertEqual(harness["pins"][config.CHANNEL_RELAY_GPIO[1]].history, history)

    def test_duplicate_after_a_simulated_restart_does_not_water_again(self):
        storage = MemoryStorage()
        clock = FakeClock()
        first = _build(clock=clock, storage=storage)
        first["controller"].initialize()
        command = protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=2))
        first["controller"].handle_command(command)
        clock.advance(2000)
        first["controller"].tick()

        # Power cycle: fresh objects, same flash contents.
        second = _build(clock=FakeClock(), storage=storage)
        second["controller"].initialize()
        messages = second["controller"].handle_command(command)

        self.assertFalse(second["controller"].is_running(1))
        self.assertEqual(second["pins"][config.CHANNEL_RELAY_GPIO[1]].history, [0])
        acks = _acks(messages)
        self.assertIs(acks[0]["payload"]["ok"], True)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_COMPLETED)

    def test_reset_mid_run_is_reported_as_stopped_and_never_resumed(self):
        storage = MemoryStorage()
        clock = FakeClock()
        first = _build(clock=clock, storage=storage)
        first["controller"].initialize()
        command = protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=30))
        first["controller"].handle_command(command)
        clock.advance(1000)  # power lost here

        second = _build(clock=FakeClock(), storage=storage)
        messages = second["controller"].initialize()

        self.assertEqual(second["controller"].running_channels(), ())
        for channel in config.CHANNELS:
            self.assertEqual(second["pins"][config.CHANNEL_RELAY_GPIO[channel]].level, 0)
        acks = _acks(messages)
        self.assertEqual(len(acks), 1)
        self.assertIs(acks[0]["payload"]["ok"], False)
        self.assertEqual(acks[0]["payload"]["result"], config.RESULT_STOPPED)

        # And a redelivery of the same command must not start a new cycle.
        replay = second["controller"].handle_command(command)
        self.assertFalse(second["controller"].is_running(1))
        self.assertIs(_acks(replay)[0]["payload"]["ok"], False)

    def test_accept_is_persisted_before_the_relay_moves(self):
        storage = MemoryStorage()
        harness = _build(storage=storage)
        harness["controller"].initialize()
        harness["controller"].handle_command(
            protocol.parse_command(_command(UUID_A, channel=1))
        )
        persisted = json.loads(storage.data)["entries"]
        self.assertEqual(persisted[0]["command_id"], UUID_A)
        self.assertEqual(persisted[0]["phase"], PHASE_ACCEPTED)


class LedgerTest(unittest.TestCase):
    def test_capacity_is_bounded_and_evicts_oldest_first(self):
        ledger = CommandLedger(MemoryStorage(), capacity=2)
        for index in range(3):
            ledger.finalize("id-%d" % index, True, config.RESULT_COMPLETED)
        recorded = [entry["command_id"] for entry in ledger.entries()]
        self.assertEqual(recorded, ["id-1", "id-2"])

    def test_corrupt_persistence_recovers_to_an_empty_ledger(self):
        ledger = CommandLedger(MemoryStorage("{not json"), capacity=4)
        self.assertEqual(ledger.entries(), [])
        ledger.finalize(UUID_A, True, config.RESULT_COMPLETED)
        self.assertIsNotNone(ledger.outcome(UUID_A))

    def test_partial_persistence_ignores_malformed_entries(self):
        raw = json.dumps({"version": 1, "entries": [{"nope": 1}, "junk"]})
        ledger = CommandLedger(MemoryStorage(raw), capacity=4)
        self.assertEqual(ledger.entries(), [])

    def test_accepted_entries_are_not_treated_as_final_outcomes(self):
        ledger = CommandLedger(MemoryStorage(), capacity=4)
        ledger.accept(UUID_A, config.ACTION_RUN_PUMP, 1)
        self.assertIsNone(ledger.outcome(UUID_A))
        self.assertEqual(ledger.find(UUID_A)["phase"], PHASE_ACCEPTED)
        ledger.finalize(UUID_A, True, config.RESULT_COMPLETED)
        self.assertEqual(ledger.outcome(UUID_A)["phase"], PHASE_FINAL)

    def test_flash_writes_are_bounded_to_two_per_command(self):
        ledger = CommandLedger(MemoryStorage(), capacity=4)
        before = ledger.write_count
        ledger.accept(UUID_A, config.ACTION_RUN_PUMP, 1)
        ledger.finalize(UUID_A, True, config.RESULT_COMPLETED)
        self.assertEqual(ledger.write_count - before, 2)


# --- envelopes ---------------------------------------------------------------------


class EnvelopeTest(unittest.TestCase):
    def test_sequence_increases_monotonically_within_a_boot(self):
        events = protocol.EventFactory(boot_id="boot-1", uuid_factory=_sequential_uuids())
        sequences = []
        for _ in range(3):
            sequences.append(events.heartbeat(0)[1]["sequence"])
        self.assertEqual(sequences, [1, 2, 3])

    def test_each_boot_gets_a_new_boot_id_and_restarts_sequences(self):
        first = protocol.EventFactory()
        second = protocol.EventFactory()
        self.assertNotEqual(first.boot_id, second.boot_id)
        self.assertEqual(second.heartbeat(0)[1]["sequence"], 1)

    def test_event_ids_are_unique_per_message(self):
        events = protocol.EventFactory()
        seen = set()
        for _ in range(5):
            seen.add(events.state({}, {})[1]["event_id"])
        self.assertEqual(len(seen), 5)

    def test_canonical_topics_match_the_registered_contract(self):
        self.assertEqual(config.TOPIC_COMMAND, "home/irrigation/quad_pump/command")
        self.assertEqual(config.TOPIC_STATE, "home/irrigation/quad_pump/state")
        self.assertEqual(config.TOPIC_EVENT, "home/irrigation/quad_pump/event")
        self.assertEqual(config.TOPIC_HEALTH, "home/irrigation/quad_pump/health")
        self.assertEqual(config.TOPIC_HEARTBEAT, "home/irrigation/quad_pump/heartbeat")

    def test_ack_event_type_and_payload_satisfy_the_action_service(self):
        events = protocol.EventFactory()
        topic, envelope = events.command_ack(UUID_A, True, config.RESULT_COMPLETED, channel=1)
        self.assertEqual(topic, config.TOPIC_EVENT)
        self.assertTrue(envelope["event_type"].endswith("command_ack"))
        self.assertEqual(envelope["payload"]["command_id"], UUID_A)
        self.assertIsInstance(envelope["payload"]["ok"], bool)

    def test_receipt_uses_a_different_event_type_than_the_final_ack(self):
        events = protocol.EventFactory()
        receipt = events.command_receipt(UUID_A, config.ACTION_RUN_PUMP, 1)[1]
        self.assertNotEqual(receipt["event_type"], config.EVENT_TYPE_COMMAND_ACK)
        self.assertFalse(receipt["event_type"].endswith("command_ack"))

    def test_error_text_is_bounded(self):
        events = protocol.EventFactory()
        envelope = events.command_ack(
            UUID_A, False, config.RESULT_INTERNAL_ERROR, error="x" * 5000
        )[1]
        self.assertLessEqual(len(envelope["payload"]["error"]), 200)

    def test_envelopes_serialize_to_compact_json(self):
        events = protocol.EventFactory()
        encoded = protocol.encode(events.state({1: True}, {1: config.FUSE_UNKNOWN})[1])
        self.assertIsInstance(encoded, bytes)
        self.assertEqual(json.loads(encoded.decode())["payload"]["relay_1"], True)

    def test_no_credentials_appear_in_any_published_payload(self):
        harness = _build()
        controller = harness["controller"]
        messages = controller.initialize()
        messages.extend(
            controller.handle_command(protocol.parse_command(_command(UUID_A)))
        )
        blob = " ".join(
            message.payload().decode("utf-8", "replace") for message in messages
        )
        for secret in ("Verizon", "artery4", "password", "PASSWORD"):
            self.assertNotIn(secret, blob)


# --- legacy compatibility ------------------------------------------------------------


class LegacyCompatibilityTest(unittest.TestCase):
    def test_confirmed_legacy_pins_map_to_confirmed_channels(self):
        self.assertEqual(config.LEGACY_PIN_TO_CHANNEL, {17: 1, 19: 2})

    def test_legacy_run_and_stop_translate_to_logical_channels(self):
        run = protocol.parse_legacy_command("quad_pump/17", b"1")
        self.assertEqual(run["action"], config.ACTION_RUN_PUMP)
        self.assertEqual(run["channel"], 1)
        self.assertEqual(run["duration_seconds"], config.DEFAULT_RUN_SECONDS)
        self.assertEqual(run["legacy_pin"], 17)

        stop = protocol.parse_legacy_command("quad_pump/19", b"0")
        self.assertEqual(stop["action"], config.ACTION_STOP_PUMP)
        self.assertEqual(stop["channel"], 2)

    def test_unmapped_legacy_pins_are_rejected_not_guessed(self):
        for pin in (16, 18, 99):
            with self.assertRaises(protocol.CommandError) as caught:
                protocol.parse_legacy_command("quad_pump/%d" % pin, b"1")
            self.assertEqual(caught.exception.code, config.RESULT_INVALID_CHANNEL)

    def test_invalid_legacy_payloads_are_rejected(self):
        for payload in (b"2", b"on", b"", b"x" * 20):
            with self.assertRaises(protocol.CommandError):
                protocol.parse_legacy_command("quad_pump/17", payload)

    def test_legacy_run_publishes_the_status_evidence_water_plants_needs(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(protocol.parse_legacy_command("quad_pump/17", b"1"))
        clock.advance(config.DEFAULT_RUN_SECONDS * 1000)
        messages = controller.tick()

        legacy = [
            message for message in messages if message.topic.startswith("status/")
        ]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0].topic, "status/17")
        self.assertEqual(legacy[0].raw, b"0")

    def test_canonical_run_publishes_no_legacy_status(self):
        clock = FakeClock()
        harness = _build(clock=clock)
        controller = harness["controller"]
        controller.initialize()
        controller.handle_command(
            protocol.parse_command(_command(UUID_A, channel=1, duration_seconds=2))
        )
        clock.advance(2000)
        messages = controller.tick()
        self.assertEqual(
            [m for m in messages if m.topic.startswith("status/")], []
        )


# --- outbound queue and reconnect ------------------------------------------------------


class OutboundQueueTest(unittest.TestCase):
    def test_queue_is_bounded(self):
        queue = net.OutboundQueue(maximum=3)
        for index in range(10):
            queue.push(Message("t/%d" % index, envelope={}, critical=True))
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue.dropped, 7)

    def test_non_critical_traffic_is_dropped_before_critical_traffic(self):
        queue = net.OutboundQueue(maximum=2)
        queue.push(Message("ack", envelope={}, critical=True))
        queue.push(Message("receipt", envelope={}, critical=False))
        queue.push(Message("ack2", envelope={}, critical=True))
        self.assertEqual([item.topic for item in (queue.pop(), queue.pop())], ["ack", "ack2"])
        self.assertEqual(queue.dropped, 1)

    def test_fifo_order_is_preserved(self):
        queue = net.OutboundQueue(maximum=5)
        queue.extend([Message("a"), Message("b")])
        self.assertEqual(queue.pop().topic, "a")
        self.assertEqual(queue.pop().topic, "b")


class BackoffTest(unittest.TestCase):
    def test_backoff_grows_exponentially_and_is_capped(self):
        delays = [net.backoff_delay_ms(n, rand=0) for n in range(1, 9)]
        self.assertEqual(delays[0], config.RECONNECT_BASE_MS)
        for earlier, later in zip(delays, delays[1:]):
            self.assertGreaterEqual(later, earlier)
        self.assertEqual(delays[-1], config.RECONNECT_MAX_MS)

    def test_jitter_stays_within_the_configured_bound(self):
        base = net.backoff_delay_ms(1, rand=0)
        for value in range(0, 5000, 137):
            delay = net.backoff_delay_ms(1, rand=value)
            self.assertGreaterEqual(delay, base)
            self.assertLessEqual(delay, base + config.RECONNECT_JITTER_MS)

    def test_attempt_zero_and_huge_attempts_stay_bounded(self):
        self.assertEqual(net.backoff_delay_ms(0, rand=0), config.RECONNECT_BASE_MS)
        self.assertEqual(net.backoff_delay_ms(9999, rand=0), config.RECONNECT_MAX_MS)


class SafetyPolicyTest(unittest.TestCase):
    def test_confirmed_owner_policy_is_encoded_in_config(self):
        self.assertEqual(config.MAX_CONCURRENT_PUMPS, 1)
        self.assertEqual(config.MAX_RUN_SECONDS, 30)
        self.assertEqual(config.DEFAULT_RUN_SECONDS, 8)
        self.assertTrue(config.RELAY_ACTIVE_HIGH)
        self.assertTrue(config.FUSE_FAULT_INHIBITS_START)
        self.assertTrue(config.FUSE_FAULT_STOPS_RUN)
        self.assertFalse(config.FUSE_SENSING_AVAILABLE)

    def test_client_id_does_not_collide_with_the_fan_pico(self):
        self.assertNotEqual(config.MQTT_CLIENT_ID_PREFIX, "pico-w-client")
        self.assertTrue(config.MQTT_CLIENT_ID_PREFIX.startswith("talos-quad-pump-"))


if __name__ == "__main__":
    unittest.main()
