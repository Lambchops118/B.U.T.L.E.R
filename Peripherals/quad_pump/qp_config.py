# Part of TALOS - Monkey Butler Device Operations System
#
# Quad pump firmware: non-secret configuration.
#
# Nothing in this file is a credential. Wi-Fi and broker credentials live in a
# device-local, untracked ``qp_secrets.py`` (see ``qp_secrets_example.py``).
#
# Runs on MicroPython, so this module deliberately avoids typing/dataclasses/
# enum/__future__ and other CPython-only constructs.

FIRMWARE_VERSION = "quad_pump-2.0.0"

# --- device / awareness identity ---------------------------------------------

ENTITY_ID = "quad_pump"
DOMAIN = "irrigation"
SOURCE_ID = "quad_pump_canonical"

TOPIC_BASE = "home/" + DOMAIN + "/" + ENTITY_ID
TOPIC_COMMAND = TOPIC_BASE + "/command"
TOPIC_STATE = TOPIC_BASE + "/state"
TOPIC_EVENT = TOPIC_BASE + "/event"
TOPIC_HEALTH = TOPIC_BASE + "/health"
TOPIC_HEARTBEAT = TOPIC_BASE + "/heartbeat"

# The fan Pico also uses "pico-w-client"; a colliding MQTT client ID makes the
# broker evict one board whenever the other connects. The runtime appends this
# board's machine.unique_id() to the prefix.
MQTT_CLIENT_ID_PREFIX = "talos-quad-pump-"

EVENT_TYPE_COMMAND_ACK = ENTITY_ID + ".command_ack"
EVENT_TYPE_COMMAND_RECEIPT = ENTITY_ID + ".command_received"
EVENT_TYPE_FUSE_TRANSITION = ENTITY_ID + ".fuse_transition"
EVENT_TYPE_RUN_STOPPED = ENTITY_ID + ".run_stopped"

# --- confirmed hardware mapping (bench-confirmed 2026-07-26) ------------------
#
# The owner's original table used MicroPython GPIO numbers, not Pico physical
# header positions. This was proven after deployment with a direct script using
# Pin(9), Pin(10), Pin(11), and Pin(12): each output activated its relay when
# driven high. The fuse inputs from that same table are GP1, GP2, GP4, and GP5,
# but fuse sensing remains deliberately unavailable (see below).
#
# Logical channel numbers 1-4 are the only identifiers used off-device. GPIO
# numbers never appear in an MQTT topic, payload, or action parameter.
CHANNELS = (1, 2, 3, 4)

CHANNEL_RELAY_GPIO = {1: 9, 2: 10, 3: 11, 4: 12}
CHANNEL_FUSE_GPIO = {1: 1, 2: 2, 3: 4, 4: 5}

# Owner-confirmed: driving the GPIO high energizes the relay and runs the pump;
# driving it low is the safe state. Boot/reset leaves the pins as inputs
# (high impedance) until initialize() drives them low.
RELAY_ACTIVE_HIGH = True

# --- confirmed safety policy --------------------------------------------------

# Owner-confirmed: one pump at a time until the supply/wiring budget is
# measured, with a 30 second hard ceiling enforced locally by the firmware.
MAX_CONCURRENT_PUMPS = 1
MAX_RUN_SECONDS = 30
MIN_RUN_SECONDS = 1
# Preserves the legacy firmware's fixed 8 second watering cycle.
DEFAULT_RUN_SECONDS = 8

# Owner-confirmed policy for a *confirmed* blown fuse: refuse to start the
# channel and immediately stop it if it is already running. "unknown" is not a
# fault and never inhibits anything.
#
# NOTE (revisit): fuse sensing is NOT IMPLEMENTED on this hardware revision.
# The fuse signal is an analog divider, but GP1/GP2/GP4/GP5 have no ADC — the Pico W
# exposes only GP26-GP28. Every channel therefore reports "unknown" forever and
# this policy currently has nothing to act on. See FUSE_SENSING_AVAILABLE below
# and docs/awareness-memory/OPEN_QUESTIONS.md (OQ-D).
FUSE_FAULT_INHIBITS_START = True
FUSE_FAULT_STOPS_RUN = True

# Flip to True only once the fuse signal can actually be measured (external
# I2C ADC, or a divider proven to meet 3.3 V logic V_IH/V_IL margins). Until
# then the firmware must not claim a fuse is "ok".
FUSE_SENSING_AVAILABLE = False

FUSE_UNKNOWN = "unknown"
FUSE_OK = "ok"
FUSE_BLOWN = "blown"
FUSE_INVALID = "invalid"

# Consecutive agreeing samples required before a fuse state change is published.
FUSE_DEBOUNCE_SAMPLES = 5
FUSE_SAMPLE_INTERVAL_MS = 100

# --- command validation bounds ------------------------------------------------

# Rejected before the JSON parser sees the bytes.
MAX_COMMAND_BYTES = 1024
ACTION_RUN_PUMP = "run_pump"
ACTION_STOP_PUMP = "stop_pump"
SUPPORTED_ACTIONS = (ACTION_RUN_PUMP, ACTION_STOP_PUMP)
TARGET_ENTITY_ID = ENTITY_ID

# Negative result codes published in command acknowledgements.
RESULT_COMPLETED = "completed"
RESULT_INVALID_COMMAND = "invalid_command"
RESULT_UNSUPPORTED_ACTION = "unsupported_action"
RESULT_INVALID_CHANNEL = "invalid_channel"
RESULT_INVALID_DURATION = "invalid_duration"
RESULT_BUSY = "busy"
RESULT_FUSE_FAULT = "fuse_fault"
RESULT_POWER_LIMIT = "power_limit"
RESULT_STOPPED = "stopped"
RESULT_INTERNAL_ERROR = "internal_error"

# --- command ledger -----------------------------------------------------------

# Bounded in-memory + on-flash record of recent command outcomes. Sized to
# comfortably cover the awareness retry window without meaningful flash wear:
# at most two small writes per command (accept, complete).
LEDGER_CAPACITY = 16
LEDGER_PATH = "qp_ledger.json"
LEDGER_TEMP_PATH = "qp_ledger.tmp"

# --- legacy compatibility (staged rollout) ------------------------------------
#
# The awareness action registry still dispatches water_plants on the legacy
# flat topics. Keeping these alive means flashing this firmware does not break
# watering before the canonical action is validated end to end.
#
# Owner-confirmed chain: pot 1 = legacy pin 17 = logical channel 1,
# pot 2 = legacy pin 19 = logical channel 2. Legacy pins 16 and 18 have no
# owner-confirmed pot and are deliberately absent — an unmapped legacy pin is
# rejected rather than guessed from list ordering.
LEGACY_COMPAT_ENABLED = True
LEGACY_COMMAND_PREFIX = "quad_pump/"
LEGACY_STATUS_PREFIX = "status/"
LEGACY_PIN_TO_CHANNEL = {17: 1, 19: 2}

# --- loop timing / network ----------------------------------------------------

TICK_SLEEP_MS = 20
HEARTBEAT_INTERVAL_MS = 30000
HEALTH_INTERVAL_MS = 300000
STATE_SNAPSHOT_INTERVAL_MS = 300000

MQTT_KEEPALIVE_SECONDS = 60
MQTT_PING_INTERVAL_MS = 20000
RECONNECT_BASE_MS = 1000
RECONNECT_MAX_MS = 60000
RECONNECT_JITTER_MS = 500

# Bounded outbound buffer: important state/ack events survive a brief outage
# without letting a long outage exhaust RAM. Oldest non-critical entries are
# dropped first and the drop is reported in health.
OUTBOUND_QUEUE_MAX = 32

# Watchdog must be longer than any single blocking network operation. Relay
# deadlines are enforced from the main loop, never from the watchdog.
WATCHDOG_TIMEOUT_MS = 8000
SOCKET_TIMEOUT_SECONDS = 5
