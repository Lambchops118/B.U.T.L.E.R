# Part of TALOS - Monkey Butler Device Operations System
#
# Quad pump firmware: hardware mapping and drivers.
#
# This is the only module that knows GPIO numbers. Everything above it speaks
# logical channels 1-4. Host tests substitute the fake drivers in
# tests/test_quad_pump_firmware.py rather than importing ``machine``.

import qp_config as config


class RelayBank(object):
    """Channel-to-relay driver.

    ``pin_factory(gpio)`` returns an object with ``.value(x)``; on MicroPython
    that is ``machine.Pin(gpio, machine.Pin.OUT)``. Injecting it keeps the
    polarity and mapping logic testable on a host.
    """

    def __init__(self, pin_factory=None, mapping=None, active_high=None):
        if mapping is None:
            mapping = config.CHANNEL_RELAY_GPIO
        if active_high is None:
            active_high = config.RELAY_ACTIVE_HIGH
        if pin_factory is None:
            pin_factory = _default_output_pin
        self._active_high = active_high
        self._mapping = dict(mapping)
        self._pins = {}
        for channel in sorted(self._mapping):
            self._pins[channel] = pin_factory(self._mapping[channel])
        self._states = {}
        for channel in self._pins:
            self._states[channel] = False

    def channels(self):
        return tuple(sorted(self._pins))

    def gpio_for(self, channel):
        return self._mapping[channel]

    def initialize_safe(self):
        """Drive every relay to the safe (off) electrical state.

        Called before anything else at boot and again after any fault. Until
        this runs the pins are high impedance, which is why the relay board
        must hold itself off with a pull-down rather than relying on firmware.
        """
        for channel in self.channels():
            self.set(channel, False)

    def set(self, channel, on):
        pin = self._pins[channel]
        level = 1 if (bool(on) == self._active_high) else 0
        pin.value(level)
        self._states[channel] = bool(on)

    def is_on(self, channel):
        return self._states.get(channel, False)

    def snapshot(self):
        result = {}
        for channel in self.channels():
            result[channel] = self.is_on(channel)
        return result


class FuseBank(object):
    """Fuse-sense reader.

    NOT IMPLEMENTED on this hardware revision, on purpose.

    The owner confirmed the fuse signal is an analog voltage from a resistor
    divider, and that it arrives on GP1/GP2/GP4/GP5. Those pins are digital-only: the
    Pico W exposes ADC on GP26-GP28 only, and only three of them. There is no
    way to measure four analog fuse voltages on this wiring, and reading the
    divider as a digital level would silently report "ok" or "blown" from a
    signal whose logic-level margins have never been verified.

    So every channel reports ``unknown``. ``unknown`` is not a fault: it never
    inhibits a run and never stops one. The firmware simply makes no claim
    about the fuses.

    REVISIT when the hardware can actually measure this. Two viable paths:
      1. Add an external I2C ADC (e.g. ADS1115) and implement ``read_raw`` here
         plus the divider conversion, averaging, and enter/exit hysteresis
         thresholds described in the plan.
      2. Confirm with measurements that fuse-good sits above V_IH (~2.0 V) and
         fuse-blown below V_IL (~0.8 V) at 3.3 V logic, record the divider
         values and margins, then implement debounced digital sensing using
         ``config.FUSE_DEBOUNCE_SAMPLES``.
    Either way, set ``config.FUSE_SENSING_AVAILABLE = True`` only once the
    truth table comes from circuit evidence rather than assumption.
    """

    def __init__(self, mapping=None, available=None):
        if mapping is None:
            mapping = config.CHANNEL_FUSE_GPIO
        if available is None:
            available = config.FUSE_SENSING_AVAILABLE
        self._mapping = dict(mapping)
        self._available = bool(available)

    def channels(self):
        return tuple(sorted(self._mapping))

    def gpio_for(self, channel):
        return self._mapping[channel]

    @property
    def available(self):
        return self._available

    def read(self, channel):
        """Return the fuse state for a channel: ok / blown / unknown / invalid."""
        if channel not in self._mapping:
            return config.FUSE_INVALID
        if not self._available:
            return config.FUSE_UNKNOWN
        # No sensing implementation exists yet; see the class docstring. Report
        # unknown rather than inventing a reading even if the flag is flipped
        # before the driver lands.
        return config.FUSE_UNKNOWN

    def snapshot(self):
        result = {}
        for channel in self.channels():
            result[channel] = self.read(channel)
        return result


def _default_output_pin(gpio):
    import machine

    return machine.Pin(gpio, machine.Pin.OUT)


def device_id():
    """Stable per-board identifier used in the MQTT client ID."""
    try:
        import machine
        import binascii

        return binascii.hexlify(machine.unique_id()).decode("utf-8")
    except Exception:
        return "unknown"


def reset_cause():
    try:
        import machine

        return int(machine.reset_cause())
    except Exception:
        return None
