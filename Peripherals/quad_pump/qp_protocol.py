# Part of TALOS - Monkey Butler Device Operations System
#
# Quad pump firmware: command parsing and canonical event construction.
#
# Pure logic — no GPIO, no network, no clock. Host tests import this module
# directly. Runs unmodified on MicroPython (no typing/dataclasses/__future__).

import json
import os

import qp_config as config

_HEX = "0123456789abcdef"


class CommandError(Exception):
    """A command cannot be executed. ``code`` is a registered result code."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def new_uuid4():
    """RFC 4122 version 4 UUID string. MicroPython has no ``uuid`` module."""
    try:
        raw = os.urandom(16)
    except (AttributeError, NotImplementedError):
        import random

        raw = bytes(bytearray(random.getrandbits(8) for _ in range(16)))
    values = bytearray(raw)
    values[6] = (values[6] & 0x0F) | 0x40  # version 4
    values[8] = (values[8] & 0x3F) | 0x80  # RFC 4122 variant
    digits = []
    for byte in values:
        digits.append(_HEX[(byte >> 4) & 0x0F])
        digits.append(_HEX[byte & 0x0F])
    text = "".join(digits)
    return "-".join(
        (text[0:8], text[8:12], text[12:16], text[16:20], text[20:32])
    )


def _is_int(value):
    # MicroPython, like CPython, makes bool a subclass of int; a JSON true must
    # not satisfy an integer parameter.
    return isinstance(value, int) and not isinstance(value, bool)


def parse_command(raw, now_ms=None):
    """Validate a canonical command payload.

    Returns a normalized dict on success and raises :class:`CommandError` with
    a registered result code otherwise. Nothing here touches hardware: the
    entire command is validated before any relay is allowed to move.

    ``issued_at`` is deliberately not used as a rejection input. The device
    clock is untrusted (no NTP is implemented), so the authoritative bounds
    are the awareness timeout and the local monotonic deadline.
    """
    if raw is None:
        raise CommandError(config.RESULT_INVALID_COMMAND, "empty command")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    # Length is enforced before JSON parsing so an oversized payload cannot
    # consume RAM or parser time on a device with neither to spare.
    if len(raw) > config.MAX_COMMAND_BYTES:
        raise CommandError(
            config.RESULT_INVALID_COMMAND,
            "command exceeds %d bytes" % config.MAX_COMMAND_BYTES,
        )
    if len(raw) == 0:
        raise CommandError(config.RESULT_INVALID_COMMAND, "empty command")

    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CommandError(config.RESULT_INVALID_COMMAND, "invalid JSON")
    if not isinstance(body, dict):
        raise CommandError(config.RESULT_INVALID_COMMAND, "command must be an object")

    command_id = body.get("command_id")
    if not isinstance(command_id, str) or not _looks_like_uuid(command_id):
        raise CommandError(config.RESULT_INVALID_COMMAND, "missing or invalid command_id")

    target = body.get("target_entity_id")
    if target is not None and target != config.TARGET_ENTITY_ID:
        raise CommandError(
            config.RESULT_INVALID_COMMAND,
            "command targets a different entity",
        )

    action = body.get("action")
    if not isinstance(action, str):
        raise CommandError(config.RESULT_INVALID_COMMAND, "missing action")
    if action not in config.SUPPORTED_ACTIONS:
        raise CommandError(config.RESULT_UNSUPPORTED_ACTION, "unsupported action")

    parameters = body.get("parameters", {})
    if not isinstance(parameters, dict):
        raise CommandError(config.RESULT_INVALID_COMMAND, "parameters must be an object")

    allowed = ("channel", "duration_seconds") if action == config.ACTION_RUN_PUMP else ("channel",)
    for key in parameters:
        if key not in allowed:
            # Unregistered extra instruction — including anything a model might
            # try to smuggle in — is rejected, never ignored-and-executed.
            raise CommandError(
                config.RESULT_INVALID_COMMAND, "unsupported parameter %r" % (key,)
            )

    channel = parameters.get("channel")
    if not _is_int(channel):
        raise CommandError(config.RESULT_INVALID_CHANNEL, "channel must be an integer")
    if channel not in config.CHANNELS:
        raise CommandError(config.RESULT_INVALID_CHANNEL, "channel out of range")

    duration = None
    if action == config.ACTION_RUN_PUMP:
        duration = parameters.get("duration_seconds", config.DEFAULT_RUN_SECONDS)
        if not _is_int(duration):
            raise CommandError(
                config.RESULT_INVALID_DURATION, "duration_seconds must be an integer"
            )
        if duration < config.MIN_RUN_SECONDS or duration > config.MAX_RUN_SECONDS:
            raise CommandError(
                config.RESULT_INVALID_DURATION,
                "duration_seconds must be %d-%d"
                % (config.MIN_RUN_SECONDS, config.MAX_RUN_SECONDS),
            )

    return {
        "command_id": command_id,
        "idempotency_key": _bounded_str(body.get("idempotency_key"), 200),
        "action": action,
        "channel": channel,
        "duration_seconds": duration,
        "correlation_id": _bounded_str(body.get("correlation_id"), 200),
        "actor": _bounded_str(body.get("actor"), 100),
        "source": "canonical",
    }


def parse_legacy_command(topic, raw):
    """Translate a legacy ``quad_pump/{pin}`` message into the same shape.

    Kept only for the staged rollout so flashing this firmware does not break
    the still-deployed ``water_plants`` action. Legacy messages carry no
    command ID, so a synthetic one is minted and the run is never deduplicated
    against a canonical command.
    """
    if not config.LEGACY_COMPAT_ENABLED:
        raise CommandError(config.RESULT_UNSUPPORTED_ACTION, "legacy commands disabled")
    if isinstance(topic, bytes):
        topic = topic.decode("utf-8")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if raw is None or len(raw) > 8:
        raise CommandError(config.RESULT_INVALID_COMMAND, "invalid legacy payload")

    try:
        pin = int(topic.split("/")[-1])
    except (ValueError, IndexError):
        raise CommandError(config.RESULT_INVALID_COMMAND, "legacy topic without a pin")

    channel = config.LEGACY_PIN_TO_CHANNEL.get(pin)
    if channel is None:
        # Legacy pins 16 and 18 were never mapped to a confirmed pot. Guessing
        # from list ordering could water the wrong plant.
        raise CommandError(config.RESULT_INVALID_CHANNEL, "unmapped legacy pin")

    value = raw.decode("utf-8", "replace").strip()
    if value == "1":
        action = config.ACTION_RUN_PUMP
        duration = config.DEFAULT_RUN_SECONDS
    elif value == "0":
        action = config.ACTION_STOP_PUMP
        duration = None
    else:
        raise CommandError(config.RESULT_INVALID_COMMAND, "legacy payload must be 0 or 1")

    return {
        "command_id": new_uuid4(),
        "idempotency_key": None,
        "action": action,
        "channel": channel,
        "duration_seconds": duration,
        "correlation_id": None,
        "actor": "legacy",
        "source": "legacy",
        "legacy_pin": pin,
    }


def _looks_like_uuid(value):
    if len(value) != 36:
        return False
    for index, character in enumerate(value):
        if index in (8, 13, 18, 23):
            if character != "-":
                return False
        elif character.lower() not in _HEX:
            return False
    return True


def _bounded_str(value, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return None
    return value[:limit]


class EventFactory(object):
    """Builds canonical envelopes accepted by the awareness ingestion path.

    Every message carries a unique event ID, a per-boot monotonically
    increasing sequence, and the boot ID minted at startup.
    """

    def __init__(self, boot_id=None, uuid_factory=None):
        self._uuid = uuid_factory or new_uuid4
        self.boot_id = boot_id or self._uuid()
        self._sequence = 0

    @property
    def sequence(self):
        return self._sequence

    def _envelope(self, payload, event_type=None, severity=None, correlation_id=None):
        self._sequence += 1
        envelope = {
            "event_id": self._uuid(),
            "sequence": self._sequence,
            "boot_id": self.boot_id,
            "payload": payload,
        }
        if event_type is not None:
            envelope["event_type"] = event_type
        if severity is not None:
            envelope["severity"] = severity
        if correlation_id is not None:
            envelope["correlation_id"] = correlation_id
        return envelope

    def state(self, relays, fuses):
        """Complete snapshot: every relay and every fuse, every time."""
        payload = {}
        for channel in config.CHANNELS:
            payload["relay_%d" % channel] = bool(relays.get(channel, False))
            payload["fuse_%d" % channel] = fuses.get(channel, config.FUSE_UNKNOWN)
        return (config.TOPIC_STATE, self._envelope(payload))

    def command_ack(
        self,
        command_id,
        ok,
        result,
        channel=None,
        relay_state=None,
        fuse_state=None,
        error=None,
        correlation_id=None,
    ):
        payload = {"command_id": command_id, "ok": bool(ok), "result": result}
        if channel is not None:
            payload["channel"] = channel
        if relay_state is not None:
            payload["relay_state"] = relay_state
        if fuse_state is not None:
            payload["fuse_state"] = fuse_state
        if error is not None:
            # Bounded and non-secret: no tracebacks, no credentials.
            payload["error"] = str(error)[:200]
        return (
            config.TOPIC_EVENT,
            self._envelope(
                payload,
                event_type=config.EVENT_TYPE_COMMAND_ACK,
                severity="info" if ok else "warning",
                correlation_id=correlation_id or command_id,
            ),
        )

    def command_receipt(self, command_id, action, channel, correlation_id=None):
        """Progress only. A distinct event type so it can never be mistaken
        for a final execution result by the action service."""
        return (
            config.TOPIC_EVENT,
            self._envelope(
                {"command_id": command_id, "action": action, "channel": channel},
                event_type=config.EVENT_TYPE_COMMAND_RECEIPT,
                severity="debug",
                correlation_id=correlation_id or command_id,
            ),
        )

    def fuse_transition(self, channel, previous, current):
        return (
            config.TOPIC_EVENT,
            self._envelope(
                {"channel": channel, "previous": previous, "current": current},
                event_type=config.EVENT_TYPE_FUSE_TRANSITION,
                severity="warning" if current == config.FUSE_BLOWN else "info",
            ),
        )

    def run_stopped(self, channel, reason, ran_ms):
        return (
            config.TOPIC_EVENT,
            self._envelope(
                {"channel": channel, "reason": reason, "ran_ms": ran_ms},
                event_type=config.EVENT_TYPE_RUN_STOPPED,
                severity="warning" if reason != "deadline" else "info",
            ),
        )

    def health(self, payload):
        return (config.TOPIC_HEALTH, self._envelope(payload))

    def heartbeat(self, uptime_ms):
        return (config.TOPIC_HEARTBEAT, self._envelope({"uptime_ms": uptime_ms}))


def encode(envelope):
    return json.dumps(envelope).encode("utf-8")
