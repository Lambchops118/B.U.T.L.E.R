"""Physical display power, driven as a side effect of sleep mode.

Sleep mode has always dimmed the *rendered* info panel: the pygame loop
multiplies the frame down to ``sleep_mode.DIM_LEVEL``. The panel is shown on a
TV, though, and the TV's own power state was controlled by an entirely separate
pair of scheduler jobs (``dim_display`` at 23:00, ``wake_display`` at 07:25).
Nothing connected the two, so "butler, go to sleep" left a fully lit screen
rendering a 1% frame, and the screen only went dark when it was asked for
separately.

This module is that connection. It reuses exactly the mechanisms the scheduler
jobs already used in production rather than inventing a new one:

- going dark is ``tv_control.night_sleep()`` (adb standby), which is what
  ``dim_display`` did;
- coming back is a retained-free ``tv_display/wake_status`` = ``"1"`` publish to
  the Pi, which runs ``Peripherals/mqtt_server/control_display.py`` and
  translates it into CEC power-on plus an input switch. That is what
  ``wake_display`` did.

Everything here is best effort and runs on a daemon thread. The sleep flag is
the authoritative record and must never fail to be written -- or wait several
seconds on adb -- because a TV on the other side of the house is unreachable.
Failures are recorded in :func:`last_result` so "why is the screen still on?"
has an answer that is not a guess.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from talos.config import env_bool, env_int, load_environment

load_environment()

TOPIC = "tv_display/wake_status"

# The Pi that owns the broker and the CEC cable. Same host the scheduler used.
def _broker() -> tuple[str, int]:
    import os

    host = os.getenv("TALOS_DISPLAY_MQTT_HOST", "").strip() or os.getenv(
        "TALOS_AWARENESS_MQTT_HOST", ""
    ).strip() or "192.168.1.160"
    return host, env_int("TALOS_DISPLAY_MQTT_PORT", 1883)


def is_enabled() -> bool:
    """Whether sleep/wake should drive the physical display.

    Read at call time rather than at import so a test, or a headless run with
    no TV attached, can turn it off without reloading the sleep-mode module.
    """
    return env_bool("TALOS_DISPLAY_POWER_ENABLED", True)


_lock = threading.Lock()
_last_result: dict = {"action": "", "ok": None, "at": None, "detail": ""}


def last_result() -> dict:
    """Outcome of the most recent display command, for diagnosis."""
    with _lock:
        return dict(_last_result)


def _record(action: str, ok: bool, detail: str = "") -> None:
    with _lock:
        _last_result.update(
            action=action,
            ok=ok,
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            detail=detail,
        )


def _publish(message: str) -> None:
    import paho.mqtt.client as mqtt

    host, port = _broker()
    client = mqtt.Client()
    client.connect(host, port, keepalive=60)
    try:
        client.publish(TOPIC, message)
    finally:
        client.disconnect()


def _go_dark() -> None:
    from talos.services import tv_control

    tv_control.night_sleep()


def _illuminate() -> None:
    _publish("1")


def _run(action: str, work) -> None:
    try:
        work()
    except Exception as exc:  # pragma: no cover - network/hardware dependent
        _record(action, False, f"{type(exc).__name__}: {exc}")
        print(f"[display-power] {action} failed: {exc}")
    else:
        _record(action, True)


def apply(asleep: bool, *, block: bool = False) -> None:
    """Bring the physical display in line with ``asleep``.

    Called by :mod:`talos.services.sleep_mode` on every sleep/wake write, so
    the two can no longer disagree. Re-asserting an already-correct state is
    deliberate: it is cheap, and it repairs a display that drifted (a manual
    remote press, a missed command) at the next sleep or wake.
    """
    if not is_enabled():
        _record("dim" if asleep else "illuminate", False, "disabled by configuration")
        return
    action = "dim" if asleep else "illuminate"
    work = _go_dark if asleep else _illuminate
    if block:
        _run(action, work)
        return
    # adb standby takes seconds; the spoken acknowledgement must not wait on it.
    threading.Thread(
        target=_run, args=(action, work), name=f"display-{action}", daemon=True
    ).start()
