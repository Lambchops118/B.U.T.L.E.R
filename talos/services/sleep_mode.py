"""Sleep mode: one shared night-mode flag for the whole TALOS process tree.

Sleep mode is the house going quiet for the night. Four things change while
it is on:

- the pygame info panel renders at ``DIM_LEVEL`` brightness -- dim, not off, so
  the clock is still legible from across a dark room and the panel is visibly
  asleep rather than visibly broken;
- the physical display the panel is shown on is put into standby, and lit again
  on wake (:mod:`talos.services.display_power`). Sleep mode and a dark screen
  are one action: neither can be asked for without the other;
- unsolicited speech (awareness alerts, briefings, scheduled announcements) is
  held back unless it is tagged ``critical``;
- nothing else. A command the user actually speaks is still answered out loud,
  because a butler that ignores you is indistinguishable from a crashed one.

The flag lives in a small JSON file instead of a module global because its
readers sit in different processes: the GUI, router and text server share the
main process, the MCP tool server is a subprocess of the agent runtime, and the
awareness backend is its own container. Reads come from a cache that re-stats
the file at most every ``CACHE_TTL_SECONDS``, which keeps the 60 fps render
loop off the filesystem; writes are atomic (temp file + replace) so a reader
never sees a half-written file.

Sleep ends when the user says so, or on its own when the system issues the
morning wake-up -- see :func:`is_wake_announcement` and the ``wake_display``
scheduler job.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from talos.config import REPO_ROOT, env_float, load_environment


load_environment()


def resolve_state_path(configured: str = "") -> Path:
    """Resolve one shared path regardless of each process's working directory."""
    path = Path(configured.strip()) if configured.strip() else Path("db/talos_sleep_state.json")
    return path if path.is_absolute() else REPO_ROOT / path


STATE_PATH = resolve_state_path(os.getenv("TALOS_SLEEP_STATE_PATH", ""))

# Fraction of normal brightness the panel renders at while asleep, and the
# number the panel now literally obeys: the dim moved into the CRT shader, past
# the 1/gamma pass that used to take a requested 1% back up to roughly 18% of
# awake brightness on the glass. Turn it up with TALOS_SLEEP_DIM_LEVEL (0.05 to
# 0.10 is a readable night-dark) if a true 1% is darker than you want.
DIM_LEVEL = min(max(env_float("TALOS_SLEEP_DIM_LEVEL", 0.01), 0.0), 1.0)

# How long a cached read stays good. Short enough that a spoken "butler, sleep"
# reaches the render loop within a frame or two, long enough that 60 fps costs
# two stat() calls a second.
CACHE_TTL_SECONDS = 0.5

# Severities that still speak while asleep. The awareness backend tags its own
# notifications; anything untagged is treated as routine.
CRITICAL_SEVERITIES = frozenset({"critical"})

# Notes handed to the model instead of a canned reply. The state change is
# deterministic and already applied by the time the model sees this -- the panel
# does not wait on an LLM to dim -- but the words TALOS actually says are its
# own, so a good night sounds like the butler rather than a status line.
_NOTES = {
    ("sleep", True): "You have just entered sleep mode at the user's request.",
    ("sleep", False): "The user asked for sleep mode, which was already on.",
    ("wake", True): "You have just left sleep mode at the user's request.",
    ("wake", False): "The user asked you to wake, but you were already awake.",
}
_NOTE_FRAME = (
    "[System note, not spoken by the user: {fact} {detail} This is already done "
    "-- do not call any tool for it and do not describe the mechanism. "
    "Acknowledge briefly, in your own voice.]"
)
_SLEEP_DETAIL = (
    "The screen is going dark, the info panel is dimmed, and noncritical spoken "
    "alerts are held back until morning."
)
_WAKE_DETAIL = (
    "The screen is coming back up, the panel is at full brightness, and alerts "
    "are audible again."
)
FAILURE_NOTE = (
    "[System note, not spoken by the user: the user asked to change sleep mode "
    "and it FAILED -- the state file could not be written, so nothing changed. "
    "Tell them plainly that it did not work.]"
)
# Sent when a turn is *about* sleep, the screen, or the panel but matched no
# phrase, so nothing was applied before the model ran.
#
# Without it the model saw an ordinary turn plus a history in which it had
# twice announced "I am now in sleep mode. The screen is dimmed" -- and said it
# again, over a screen that never changed. The words are the only thing the user
# can hear, so a confident sentence with no state change behind it is
# indistinguishable from the feature working, which is worse than an error.
UNVERIFIED_NOTE = (
    "[System note, not spoken by the user: this turn was NOT recognised as a "
    "sleep/wake command, so NOTHING has changed -- the panel, the screen and "
    "the alert gate are exactly as they were. Earlier turns in this "
    "conversation may show you announcing a sleep or wake change; those were "
    "separate turns and say nothing about this one. If the user is asking you "
    "to sleep, wake, dim or brighten now, you MUST call the "
    "sleep_mode_control tool and report what it returns. Never state or imply "
    "that the state changed unless a system note or a tool result says so; if "
    "you are unsure what they meant, ask.]"
)

# Vocabulary that puts a turn in the sleep/screen domain. Used only to decide
# whether an *unmatched* turn deserves the note above; it never changes state,
# so a false positive costs one honest sentence of context and nothing else.
_SLEEP_TOPIC = re.compile(
    r"\b(?:sleep|sleeping|asleep|wake|waking|awake|good\s*night|goodnight|"
    r"night\s*mode|nap|dim|dimmed|dimming|undim|bright|brighten|brighter|"
    r"brightness|lights?\s+out|go\s+dark|screen|display|panel|monitor)\b"
)

# Titles of system announcements that end sleep mode by themselves. The morning
# briefing is the wake-up message; matching on its title keeps the awareness
# backend unaware of sleep mode entirely.
_WAKE_ANNOUNCEMENT_TITLES = (
    "morning briefing",
    "morning report",
    "good morning",
)

_lock = threading.Lock()
_cache: dict | None = None
_cache_read_at = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_state() -> dict:
    return {"asleep": False, "since": None, "reason": "", "display_level": 1.0}


def _read_file() -> dict:
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        # A missing or corrupt file means "awake". Sleep mode is a convenience;
        # it must never be the reason the panel or the voice lane misbehaves.
        return _default_state()
    if not isinstance(payload, dict):
        return _default_state()
    current = _default_state()
    current["asleep"] = bool(payload.get("asleep"))
    current["since"] = payload.get("since") or None
    current["reason"] = str(payload.get("reason") or "")
    current["display_level"] = DIM_LEVEL if current["asleep"] else 1.0
    return current


def _write_file(new_state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(STATE_PATH.parent),
        prefix=".sleep_state_",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(new_state, handle)
        os.replace(handle.name, STATE_PATH)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def state(*, force_reload: bool = False) -> dict:
    """Current sleep state as ``{asleep, since, reason}``, cache-backed."""
    global _cache, _cache_read_at
    now = time.monotonic()
    with _lock:
        fresh = _cache is not None and (now - _cache_read_at) < CACHE_TTL_SECONDS
        if fresh and not force_reload:
            return dict(_cache)
        _cache = _read_file()
        _cache_read_at = now
        return dict(_cache)


def is_asleep() -> bool:
    return bool(state()["asleep"])


def _set(asleep: bool, reason: str) -> dict:
    global _cache, _cache_read_at
    new_state = {
        "asleep": asleep,
        "since": _now_iso() if asleep else None,
        "reason": str(reason or ""),
        # Sleep and display state are one atomic record. A caller can no longer
        # put TALOS to sleep while leaving the panel's intended level awake.
        "display_level": DIM_LEVEL if asleep else 1.0,
    }
    with _lock:
        try:
            _write_file(new_state)
        except OSError as exc:
            # Report the failure rather than leaving the caller believing the
            # house went quiet when it did not.
            raise RuntimeError(f"could not write sleep state: {exc}") from exc
        _cache = dict(new_state)
        _cache_read_at = time.monotonic()
    _apply_display(asleep)
    return dict(new_state)


def _apply_display(asleep: bool) -> None:
    """Bring the physical display in line with the flag just written.

    Sleep mode always means a dark screen and waking always means a lit one,
    so this is driven from the single write path rather than from each caller:
    there is no longer any way to enter sleep mode and leave the display on.
    Imported lazily and best effort -- the flag is authoritative, and a TV that
    cannot be reached must not turn a good night into a failure.
    """
    try:
        from talos.services import display_power

        display_power.apply(asleep)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[sleep-mode] display command not dispatched: {exc}")


def sleep(reason: str = "") -> dict:
    """Enter sleep mode. Idempotent."""
    return _set(True, reason)


def wake(reason: str = "") -> dict:
    """Leave sleep mode. Idempotent."""
    return _set(False, reason)


def should_speak(severity: str = "") -> bool:
    """Whether an *unsolicited* announcement of this severity may be spoken.

    Direct replies to something the user said do not go through here; they are
    always spoken.
    """
    if not is_asleep():
        return True
    return str(severity or "").strip().lower() in CRITICAL_SEVERITIES


def is_wake_announcement(title: str) -> bool:
    """Whether this system announcement is the morning wake-up message.

    The morning briefing arrives from the awareness backend titled
    "Morning briefing"; delivering it is the moment the house is expected to be
    awake again, so it clears sleep mode before it is spoken.
    """
    normalized = _normalize(title)
    return any(marker in normalized for marker in _WAKE_ANNOUNCEMENT_TITLES)


# --- spoken phrase recognition -------------------------------------------
#
# Matched before the request classifier and before the model sees the turn:
# "butler, good night" should dim the panel in milliseconds, not after a round
# trip through an LLM that might decide to hold a conversation about it.
#
# Matching is deliberately ASYMMETRIC, because the two mistakes do not cost the
# same. A false sleep dims the room's only screen when the user asked for
# something else, so the sleep patterns are anchored to the whole utterance and
# stay narrow: "set a sleep timer" and "how did you sleep" must not trigger it.
# A missed wake is far worse -- the panel is at 1% brightness with no obvious
# way back, and the user is left talking to a screen that looks broken -- while
# a false wake merely turns the lights on. So once the house is actually asleep,
# anything that plausibly reads as "undo this" counts: those patterns are only
# consulted while asleep, when there is very little else the user could mean.

_PUNCTUATION = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")
_LEADING_WAKE_WORD = re.compile(r"^(?:hey\s+)?butler\b[\s,]*")

# Conversational run-up, stripped before matching so the whole-utterance anchors
# below can stay strict without rejecting the way people actually speak. The
# bare "s" is not a typo: the recognizer drops the first word of a short
# utterance often enough that "let's sleep mode" arrives as "'s sleep mode",
# which matched nothing and fell through to the model. Only a known run-up is
# removed, and what remains must still match a complete phrase, so "set a sleep
# timer" and "how did you sleep" are unaffected.
_LEADING_FILLER = re.compile(
    r"^(?:(?:ok|okay|alright|right|so|well|yeah|yep|yes|hey|please|now|"
    r"lets|let s|its|it s|s|thats|that s|i want you to|i need you to|"
    r"can you|could you|would you|will you|time to|its time to)\b[\s,]*)+"
)

# Trailing politeness or repetition, allowed on every whole-utterance pattern.
# "go into sleep mode again" is the same request as "go into sleep mode", and
# the second time someone asks is exactly when "again" shows up.
_TAIL = r"(?:\s+(?:now|please|again|once\s+more|for\s+me))*$"

_SLEEP_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^(?:please\s+)?(?:go\s+to\s+|go\s+)?sleep(?:\s+mode)?" + _TAIL,
        r"^(?:enter|activate|start|turn\s+on|engage|go\s+into|go\s+back\s+into)"
        r"\s+(?:the\s+)?(?:sleep|night)\s+mode" + _TAIL,
        r"^(?:sleep|night)\s+mode\s+(?:on|please)" + _TAIL,
        r"^(?:can\s+you\s+|please\s+)?(?:go\s+to\s+sleep|put\s+yourself\s+to\s+sleep)" + _TAIL,
        r"^(?:its\s+|it\s+is\s+)?time\s+(?:to\s+sleep|for\s+sleep)" + _TAIL,
        r"^go\s+dark" + _TAIL,
        r"^dim\s+(?:the\s+)?(?:screen|display|panel|monitor|lights)" + _TAIL,
        r"^good\s*night(?:\s+butler)?" + _TAIL,
        r"^night\s*night" + _TAIL,
        r"^(?:i\s*am|im)\s+(?:going\s+to|off\s+to)\s+bed" + _TAIL,
        r"^(?:time\s+for\s+bed|off\s+to\s+bed|going\s+to\s+bed)" + _TAIL,
        r"^lights\s+out" + _TAIL,
    )
)

# Always active, asleep or not.
_WAKE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^(?:please\s+)?wake(?:\s+up)?" + _TAIL,
        r"^(?:exit|leave|end|cancel|stop|turn\s+off|disable)"
        r"\s+(?:the\s+)?(?:sleep|night)\s+mode" + _TAIL,
        r"^(?:sleep|night)\s+mode\s+off" + _TAIL,
        r"^good\s*morning(?:\s+butler)?" + _TAIL,
        r"^rise\s+and\s+shine" + _TAIL,
        r"^(?:i\s*am|im)\s+up" + _TAIL,
    )
)

# Consulted ONLY while asleep, as substrings rather than whole utterances. At 1%
# brightness, "wake up the display", "turn the screen back on", "undim it",
# "brighten up" and "I can't see anything" all mean the same thing, and none of
# them should have to be guessed at by the model while the user stares at a dark
# panel. Matching one of these is treated as a wake request and answered.
_WAKE_REQUEST_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        # "wake" only as an imperative aimed at TALOS. Not "I will wake you in
        # the morning" (which the model may well say, and can be heard back
        # through barge-in) and not "wake me at seven", which is a reminder.
        r"^(?:please\s+)?wake\b(?!\s+(?:me|us|him|her|them)\b)",
        r"\bwake\s+(?:up\s+)?(?:the\s+)?(?:screen|display|panel|monitor)\b",
        r"\bun\s*dim\b",
        # "brighten"/"brighter", never bare "brightness" -- a reply saying
        # "brightness restored" must not read as a request.
        r"\bbrighten\b",
        r"\bbrighter\b",
        r"\b(?:turn|bring)\s+up\s+(?:the\s+)?bright",
        r"\blights?\s+(?:back\s+)?on\b",
        r"\b(?:screen|display|panel|monitor)\s+(?:back\s+)?on\b",
        r"\bturn\s+(?:the\s+)?(?:screen|display|panel|monitor)\b",
        # Complaints only. Never a bare "dim"/"dark" ("dim the screen" is a
        # SLEEP phrase), and never a plain statement of fact -- "the panel is
        # dim until morning" is TALOS describing what it just did, so "too" is
        # required to make it a complaint rather than a description.
        r"\btoo\s+(?:dark|dim)\b",
        r"\bcan\s*t\s+see\b",
        r"\bstop\s+sleeping\b",
        r"\bnever\s*mind\b",
        r"\b(?:cancel|undo)\s+that\b",
    )
)

# Liberal wake matching is ignored for this long after falling asleep. The voice
# worker runs barge-in without requiring the wake word, so the tail of TALOS's
# own spoken acknowledgement can come back through the mic as a command -- and
# a loose pattern would then undo the sleep the user just asked for, which is
# exactly what a "dims, then pops back" bug looks like. Within the window only
# an explicit wake phrase ("wake up", "good morning") counts, so a real
# change of mind still works instantly.
LIBERAL_WAKE_GRACE_SECONDS = env_float("TALOS_SLEEP_WAKE_GRACE_SECONDS", 8.0)


def _in_wake_grace_period() -> bool:
    """Whether sleep started too recently to trust a loose wake match.

    Measured from the stored ``since`` timestamp so it holds across processes:
    the text server decides this, but the flag was written by whichever
    component the user reached.
    """
    if LIBERAL_WAKE_GRACE_SECONDS <= 0:
        return False
    since = state().get("since")
    if not since:
        return False
    try:
        started = datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    # A clock jump backwards should not pin the grace period open forever.
    return 0 <= elapsed < LIBERAL_WAKE_GRACE_SECONDS


def _normalize(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = _LEADING_WAKE_WORD.sub("", lowered)
    lowered = _PUNCTUATION.sub(" ", lowered)
    lowered = _WHITESPACE.sub(" ", lowered).strip()
    return _LEADING_FILLER.sub("", lowered).strip()


def match_phrase(command: str, *, asleep: bool | None = None) -> str | None:
    """Return ``"sleep"``, ``"wake"``, or ``None`` for a spoken/typed command.

    ``asleep`` selects which wake vocabulary applies; it defaults to the current
    state. While asleep the liberal set is used, so almost any request to
    reverse the dimming gets out of sleep mode without the model's help.

    The wake word is normally stripped upstream, but a leading "butler" is
    tolerated so the same matcher works on raw transcripts.
    """
    normalized = _normalize(command)
    if not normalized:
        return None
    if any(pattern.match(normalized) for pattern in _WAKE_PATTERNS):
        return "wake"
    if asleep is None:
        asleep = is_asleep()
    if asleep and not _in_wake_grace_period():
        # Checked before the sleep patterns: while asleep, "go to sleep" is
        # already satisfied, so an utterance matching both is far likelier to be
        # someone trying to get the screen back.
        if any(pattern.search(normalized) for pattern in _WAKE_REQUEST_PATTERNS):
            return "wake"
    if any(pattern.match(normalized) for pattern in _SLEEP_PATTERNS):
        return "sleep"
    return None


def is_sleep_topic(command: str) -> bool:
    """Whether this turn is about sleep mode, the screen, or the panel.

    Deliberately broad. It gates a note that only tells the model the truth
    (nothing changed, use the tool), so over-matching is cheap and
    under-matching is what caused the bug.
    """
    return bool(_SLEEP_TOPIC.search(_normalize(command)))


def apply_phrase(command: str, *, source: str = "voice") -> str | None:
    """Apply a recognised sleep/wake phrase; return a note for the model.

    The state change happens here, deterministically and before the model runs,
    so the panel dims the moment the words are recognised. The reply itself is
    left to the model: this returns the note that tells it what just happened,
    and the turn then proceeds down the normal agent path.

    ``None`` means the command was not a sleep phrase at all. Ordinary commands
    do not wake the panel: asking the time at 3am gets a spoken answer and
    leaves the screen dark, which is the point of a night mode.
    """
    currently_asleep = is_asleep()
    intent = match_phrase(command, asleep=currently_asleep)
    if intent is None:
        # Nothing was applied. If the turn is nevertheless about sleep or the
        # screen, say so explicitly rather than staying silent -- silence is
        # what let the model narrate a change that never happened.
        return UNVERIFIED_NOTE if is_sleep_topic(command) else None

    changed = (intent == "sleep") != currently_asleep
    if changed:
        # Let a write failure propagate: the caller turns it into a note that
        # tells the user it did not work, rather than a confident good night
        # over a panel that never dimmed.
        (sleep if intent == "sleep" else wake)(reason=f"requested via {source}")

    return _NOTE_FRAME.format(
        fact=_NOTES[(intent, changed)],
        detail=_SLEEP_DETAIL if intent == "sleep" else _WAKE_DETAIL,
    )
