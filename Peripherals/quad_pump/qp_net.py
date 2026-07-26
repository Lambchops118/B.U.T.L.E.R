# Part of TALOS - Monkey Butler Device Operations System
#
# Quad pump firmware: Wi-Fi and MQTT supervision.
#
# Reconnection is a state machine driven from the main loop rather than a
# blocking retry loop, so relay deadlines keep being enforced while the network
# is down. A single connect attempt can still block for up to the socket
# timeout, which is why the watchdog period is set longer than it.
#
# Never wait on this module before turning a relay off.

import qp_config as config


class OutboundQueue(object):
    """Bounded outbound buffer.

    Important state and acknowledgement events survive a brief outage. When the
    bound is reached, non-critical traffic (receipts, run_stopped notes, legacy
    status) is dropped before anything critical, and the drop count is reported
    in health rather than hidden.
    """

    def __init__(self, maximum=None):
        self._maximum = maximum or config.OUTBOUND_QUEUE_MAX
        self._items = []
        self.dropped = 0

    def __len__(self):
        return len(self._items)

    def push(self, message):
        if len(self._items) >= self._maximum:
            self._evict()
        self._items.append(message)

    def extend(self, messages):
        for message in messages:
            self.push(message)

    def peek(self):
        return self._items[0] if self._items else None

    def pop(self):
        return self._items.pop(0)

    def _evict(self):
        for index, item in enumerate(self._items):
            if not item.critical:
                self._items.pop(index)
                self.dropped += 1
                return
        self._items.pop(0)
        self.dropped += 1


class NetworkSupervisor(object):
    def __init__(
        self,
        client_id,
        broker,
        on_message,
        clock,
        port=0,
        user=None,
        password=None,
        subscriptions=(),
        last_will=None,
    ):
        self._client_id = client_id
        self._broker = broker
        self._port = port
        self._user = user
        self._password = password
        self._on_message = on_message
        self._clock = clock
        self._subscriptions = tuple(subscriptions)
        self._last_will = last_will

        self._client = None
        self._wlan = None
        self._attempt = 0
        self._next_attempt_ms = None
        self._last_ping_ms = None
        self.reconnects = 0
        self.last_error = None

    # --- status ---------------------------------------------------------------

    @property
    def connected(self):
        return self._client is not None

    def wifi_connected(self):
        try:
            return bool(self._wlan is not None and self._wlan.isconnected())
        except Exception:
            return False

    def rssi(self):
        try:
            return int(self._wlan.status("rssi"))
        except Exception:
            return None

    # --- connection -----------------------------------------------------------

    def ensure_connected(self):
        """Attempt at most one connection step per call, respecting backoff.

        Returns True when an MQTT session is available.
        """
        if self._client is not None:
            return True
        now = self._clock.ticks_ms()
        if self._next_attempt_ms is not None:
            if self._clock.ticks_diff(now, self._next_attempt_ms) < 0:
                return False
        try:
            self._connect_wifi()
            self._connect_mqtt()
        except Exception as exc:
            self._client = None
            self.last_error = _bounded_error(exc)
            self._attempt += 1
            self._next_attempt_ms = now + backoff_delay_ms(self._attempt)
            return False
        self._attempt = 0
        self._next_attempt_ms = None
        self.reconnects += 1
        self.last_error = None
        self._last_ping_ms = self._clock.ticks_ms()
        return True

    def _connect_wifi(self):
        import network

        if self._wlan is None:
            self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)
        if not self._wlan.isconnected():
            from qp_secrets import WIFI_SSID, WIFI_PASSWORD

            self._wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            # Bounded wait: one attempt's worth, then back off and let the main
            # loop keep enforcing deadlines.
            deadline = self._clock.ticks_ms() + config.SOCKET_TIMEOUT_SECONDS * 1000
            import time

            while not self._wlan.isconnected():
                if self._clock.ticks_diff(self._clock.ticks_ms(), deadline) >= 0:
                    raise OSError("wifi connect timeout")
                time.sleep_ms(100) if hasattr(time, "sleep_ms") else time.sleep(0.1)

    def _connect_mqtt(self):
        client = _retain_aware_client_class()(
            self._client_id,
            self._broker,
            port=self._port,
            user=self._user,
            password=self._password,
            keepalive=config.MQTT_KEEPALIVE_SECONDS,
        )
        client.set_callback(self._on_message)
        if self._last_will is not None:
            topic, payload = self._last_will
            # Retained so the backend sees the device as offline until it
            # publishes a fresh, non-retained boot snapshot.
            client.set_last_will(topic, payload, retain=True, qos=0)
        client.connect(clean_session=True, timeout=config.SOCKET_TIMEOUT_SECONDS)
        self._client = client
        self.resubscribe()

    def resubscribe(self):
        for topic in self._subscriptions:
            self._client.subscribe(
                topic if isinstance(topic, bytes) else topic.encode("utf-8"), qos=1
            )

    def drop(self, error=None):
        if error is not None:
            self.last_error = _bounded_error(error)
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._next_attempt_ms = self._clock.ticks_ms() + backoff_delay_ms(1)

    # --- traffic ---------------------------------------------------------------

    def poll(self):
        """Service one pending inbound message, if any."""
        if self._client is None:
            return
        try:
            self._client.check_msg()
        except OSError as exc:
            self.drop(exc)
            return
        self._maybe_ping()

    def _maybe_ping(self):
        if self._last_ping_ms is None:
            return
        now = self._clock.ticks_ms()
        if self._clock.ticks_diff(now, self._last_ping_ms) < config.MQTT_PING_INTERVAL_MS:
            return
        try:
            self._client.ping()
            self._last_ping_ms = now
        except OSError as exc:
            self.drop(exc)

    def publish(self, topic, payload, qos=1, retain=False):
        if self._client is None:
            return False
        try:
            self._client.publish(
                topic if isinstance(topic, bytes) else topic.encode("utf-8"),
                payload,
                retain=retain,
                qos=qos,
            )
            return True
        except Exception as exc:
            self.drop(exc)
            return False


def _retain_aware_client_class():
    """Build an ``MQTTClient`` subclass that exposes the PUBLISH retain flag.

    ``umqtt.simple`` invokes ``self.cb(topic, msg)`` and discards the fixed
    header, so a subscriber cannot tell a live command from a retained replay.
    Firmware must never water a plant just because a retained command was
    redelivered on reconnect, so ``wait_msg`` is re-implemented here with the
    retain bit passed through as a third callback argument.

    This mirrors ``simple.MQTTClient.wait_msg``; keep the two in step if the
    vendored client is ever updated.
    """
    from simple import MQTTClient

    class RetainAwareMQTTClient(MQTTClient):
        def wait_msg(self):
            res = self.sock.read(1)
            self.sock.setblocking(True)
            if res is None:
                return None
            if res == b"":
                raise OSError(-1)
            if res == b"\xd0":  # PINGRESP
                size = self.sock.read(1)[0]
                assert size == 0
                return None
            op = res[0]
            if op & 0xF0 != 0x30:
                return op
            size = self._recv_len()
            topic_len = self.sock.read(2)
            topic_len = (topic_len[0] << 8) | topic_len[1]
            topic = self.sock.read(topic_len)
            size -= topic_len + 2
            pid = None
            if op & 6:
                pid = self.sock.read(2)
                pid = pid[0] << 8 | pid[1]
                size -= 2
            msg = self.sock.read(size)
            self.cb(topic, msg, bool(op & 0x01))
            if op & 6 == 2:
                import struct

                packet = bytearray(b"\x40\x02\0\0")
                struct.pack_into("!H", packet, 2, pid)
                self.sock.write(packet)
            elif op & 6 == 4:
                assert 0
            return op

    return RetainAwareMQTTClient


def backoff_delay_ms(attempt, base=None, maximum=None, jitter=None, rand=None):
    """Exponential backoff with bounded jitter, capped so a long outage still
    retries at a predictable floor."""
    base = base if base is not None else config.RECONNECT_BASE_MS
    maximum = maximum if maximum is not None else config.RECONNECT_MAX_MS
    jitter = jitter if jitter is not None else config.RECONNECT_JITTER_MS
    if attempt < 1:
        attempt = 1
    if attempt > 20:
        attempt = 20
    delay = base * (2 ** (attempt - 1))
    if delay > maximum:
        delay = maximum
    if jitter:
        if rand is None:
            import random

            rand = random.getrandbits(16)
        delay += rand % (jitter + 1)
    return delay


def _bounded_error(exc):
    # Bounded and non-secret: type name only, never a traceback or credentials.
    return type(exc).__name__[:60]
