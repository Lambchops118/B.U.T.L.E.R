# Part of TALOS
# Monkey Butler Device Operations System
#
# Quad pump firmware for a Raspberry Pi Pico W. Deploy to the board root
# together with simple.py, qp_config.py, qp_hardware.py, qp_protocol.py,
# qp_ledger.py, qp_controller.py, qp_net.py, and a device-local qp_secrets.py
# (see qp_secrets_example.py).
#
# Controls four pump relays independently and reports through the TALOS
# awareness MQTT ingestion path:
#
#     subscribes   home/irrigation/quad_pump/command   (QoS 1, JSON envelope)
#                  quad_pump/{17,19}                   (legacy, staged rollout)
#     publishes    home/irrigation/quad_pump/state
#                  home/irrigation/quad_pump/event     (acks, faults)
#                  home/irrigation/quad_pump/health
#                  home/irrigation/quad_pump/heartbeat
#                  status/{17,19}                      (legacy, staged rollout)
#
# Safety properties that do not depend on the network:
#   - every relay is driven off before Wi-Fi is even started;
#   - one pump at a time, 30 second hard local deadline;
#   - the run deadline is enforced from the main loop using a monotonic clock,
#     never from a sleep inside the MQTT callback;
#   - a reset during a run leaves every relay off and does not replay the run.
#
# Fuse sensing is NOT implemented on this hardware revision - see qp_hardware
# FuseBank for why and what to revisit.

import time

import qp_config as config
import qp_hardware as hardware
import qp_protocol as protocol
from qp_controller import Clock, PumpController
from qp_ledger import CommandLedger, FileStorage
from qp_net import NetworkSupervisor, OutboundQueue

MAX_INBOUND_QUEUE = 8
MAX_INBOUND_PER_TICK = 4

# --- safe hardware state comes first, before anything can fail ----------------

relays = hardware.RelayBank()
relays.initialize_safe()
fuses = hardware.FuseBank()

clock = Clock()
events = protocol.EventFactory()
ledger = CommandLedger(FileStorage())
controller = PumpController(relays, fuses, events, ledger, clock)
queue = OutboundQueue()

_inbound = []
_stats = {
    "inbound_dropped": 0,
    "rejected": 0,
    "unidentified_rejects": 0,
    "commands": 0,
}
_boot_ms = clock.ticks_ms()


def _on_message(topic, message, retained=False):
    """MQTT callback: bounded enqueue only.

    Deliberately does no validation, no flash I/O, and no relay work. The loop
    must stay responsive to stop commands, pings, and network failure while a
    pump is running, so nothing slow happens here.
    """
    if retained:
        # A retained command is a replay of the broker's last known value, not
        # a fresh instruction. Watering on reconnect because of one would be a
        # spurious physical action.
        return
    if len(_inbound) >= MAX_INBOUND_QUEUE:
        _stats["inbound_dropped"] += 1
        return
    if message is not None and len(message) > config.MAX_COMMAND_BYTES:
        _stats["inbound_dropped"] += 1
        return
    _inbound.append((topic, message))


def _decode_topic(topic):
    if isinstance(topic, bytes):
        return topic.decode("utf-8", "replace")
    return topic


def _peek_command_id(raw):
    """Best-effort command ID from an otherwise invalid payload, so a rejection
    can still be correlated with the awareness request."""
    try:
        import json

        body = json.loads(raw)
        candidate = body.get("command_id")
        if isinstance(candidate, str) and len(candidate) == 36:
            return candidate
    except Exception:
        pass
    return None


def _handle_inbound(topic, raw):
    topic = _decode_topic(topic)
    legacy = topic.startswith(config.LEGACY_COMMAND_PREFIX)
    try:
        if legacy:
            command = protocol.parse_legacy_command(topic, raw)
        elif topic == config.TOPIC_COMMAND:
            command = protocol.parse_command(raw)
        else:
            return []
    except protocol.CommandError as error:
        _stats["rejected"] += 1
        if legacy:
            # Legacy messages carry no command ID; there is nothing the action
            # service could correlate a negative acknowledgement with.
            return []
        command_id = _peek_command_id(raw)
        if command_id is None:
            _stats["unidentified_rejects"] += 1
            return []
        return controller.reject(command_id, error.code, error.message)
    except Exception:
        _stats["rejected"] += 1
        return []

    _stats["commands"] += 1
    try:
        return controller.handle_command(command)
    except Exception as exc:
        # Never leave a relay energized because of an unexpected error.
        messages = controller.stop_all("internal_error")
        messages.extend(
            controller.reject(
                command["command_id"],
                config.RESULT_INTERNAL_ERROR,
                type(exc).__name__,
                command.get("correlation_id"),
                command.get("channel"),
            )
        )
        return messages


def _health_payload(supervisor):
    return {
        "firmware_version": config.FIRMWARE_VERSION,
        "uptime_ms": clock.ticks_diff(clock.ticks_ms(), _boot_ms),
        "reset_cause": hardware.reset_cause(),
        "wifi_connected": supervisor.wifi_connected(),
        "mqtt_connected": supervisor.connected,
        "rssi": supervisor.rssi(),
        "reconnects": supervisor.reconnects,
        "last_error": supervisor.last_error,
        "outbound_dropped": queue.dropped,
        "inbound_dropped": _stats["inbound_dropped"],
        "commands_accepted": _stats["commands"],
        "commands_rejected": _stats["rejected"],
        "fuse_sensing": "unavailable",
        "watchdog": "enabled" if _watchdog is not None else "disabled",
    }


def _start_watchdog():
    try:
        import machine

        return machine.WDT(timeout=config.WATCHDOG_TIMEOUT_MS)
    except Exception:
        return None


def _sleep_ms(milliseconds):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(milliseconds)
    else:
        time.sleep(milliseconds / 1000.0)


def _build_supervisor():
    from qp_secrets import MQTT_BROKER, MQTT_PORT, MQTT_PASSWORD, MQTT_USER

    subscriptions = [config.TOPIC_COMMAND]
    if config.LEGACY_COMPAT_ENABLED:
        for pin in sorted(config.LEGACY_PIN_TO_CHANNEL):
            subscriptions.append(config.LEGACY_COMMAND_PREFIX + str(pin))

    offline = protocol.encode(
        {
            "event_id": protocol.new_uuid4(),
            "boot_id": events.boot_id,
            "payload": {"online": False, "reason": "last_will"},
        }
    )
    return NetworkSupervisor(
        config.MQTT_CLIENT_ID_PREFIX + hardware.device_id(),
        MQTT_BROKER,
        _on_message,
        clock,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        subscriptions=subscriptions,
        last_will=(config.TOPIC_HEALTH.encode("utf-8"), offline),
    )


_watchdog = _start_watchdog()


def run():
    supervisor = _build_supervisor()

    # Boot snapshot plus truthful recovery acknowledgements for anything a
    # reset interrupted.
    queue.extend(controller.initialize())
    queue.push(_health_message(supervisor))

    was_connected = False
    last_heartbeat = clock.ticks_ms()
    last_health = clock.ticks_ms()
    last_snapshot = clock.ticks_ms()

    while True:
        if _watchdog is not None:
            _watchdog.feed()

        # 1. Local safety first: deadlines and fuse policy run every tick and
        #    never depend on the network.
        queue.extend(controller.tick())

        # 2. Network, best effort.
        connected = supervisor.ensure_connected()
        if connected and not was_connected:
            # Fresh non-retained snapshot after every (re)connect.
            queue.push(controller.state_message())
            queue.push(_health_message(supervisor))
        was_connected = connected

        if connected:
            supervisor.poll()

        # 3. Inbound work, bounded per tick.
        processed = 0
        while _inbound and processed < MAX_INBOUND_PER_TICK:
            topic, raw = _inbound.pop(0)
            queue.extend(_handle_inbound(topic, raw))
            processed += 1

        # 4. Periodic reporting.
        now = clock.ticks_ms()
        if clock.ticks_diff(now, last_heartbeat) >= config.HEARTBEAT_INTERVAL_MS:
            last_heartbeat = now
            topic, envelope = events.heartbeat(clock.ticks_diff(now, _boot_ms))
            queue.push(_message(topic, envelope, critical=False))
        if clock.ticks_diff(now, last_health) >= config.HEALTH_INTERVAL_MS:
            last_health = now
            queue.push(_health_message(supervisor))
        if clock.ticks_diff(now, last_snapshot) >= config.STATE_SNAPSHOT_INTERVAL_MS:
            last_snapshot = now
            queue.push(controller.state_message())

        # 5. Drain outbound, bounded so publishing never starves the deadline
        #    check above.
        drained = 0
        while connected and len(queue) and drained < 4:
            message = queue.peek()
            if not supervisor.publish(
                message.topic, message.payload(), qos=1, retain=message.retain
            ):
                break  # stay queued; the supervisor already dropped the session
            queue.pop()
            drained += 1

        _sleep_ms(config.TICK_SLEEP_MS)


def _message(topic, envelope, critical=True):
    from qp_controller import Message

    return Message(topic, envelope=envelope, critical=critical)


def _health_message(supervisor):
    topic, envelope = events.health(_health_payload(supervisor))
    return _message(topic, envelope, critical=False)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Last resort: an unexpected failure must not leave a pump energized.
        # The watchdog reboots the board, and boot recovery reports the
        # interrupted command truthfully instead of replaying it.
        relays.initialize_safe()
        raise
