"""Sleep mode: phrase recognition, shared state, and the paths it gates.

Covers the three behaviors sleep mode promises -- a dimmed panel, noncritical
speech held back, and an automatic wake at the morning message -- plus the
phrases that must NOT trigger it, which is where a night-mode switch does its
real damage if it is loose.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.text.server import TextAgentHTTPServer, TextServerConfig


def _fresh_sleep_mode(test: unittest.TestCase):
    """The sleep_mode module, redirected to a throwaway state file.

    Patched rather than reloaded: the module object is shared with the text
    server and the pygame panel, and a reload would leave the real one pointing
    at a temp path (and possibly asleep) for every test that runs afterwards.
    """
    import talos.services.sleep_mode as module

    state_path = Path(tempfile.mkdtemp(prefix="talos_sleep_test_")) / "sleep.json"
    test.enterContext(patch.object(module, "STATE_PATH", state_path))
    _reset_cache(module)
    test.addCleanup(_reset_cache, module)
    return module


def _reset_cache(module) -> None:
    module._cache = None
    module._cache_read_at = 0.0


class PhraseRecognitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep_mode = _fresh_sleep_mode(self)

    def test_sleep_phrases_match(self) -> None:
        for phrase in (
            "sleep",
            "go to sleep",
            "Butler, good night.",
            "goodnight",
            "good night",
            "night night",
            "sleep mode on",
            "activate sleep mode",
            "lights out",
            "I am going to bed",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.sleep_mode.match_phrase(phrase), "sleep")

    def test_wake_phrases_match(self) -> None:
        for phrase in (
            "wake up",
            "wake",
            "good morning",
            "Butler, good morning",
            "sleep mode off",
            "turn off sleep mode",
            "rise and shine",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(self.sleep_mode.match_phrase(phrase), "wake")

    def test_wake_vocabulary_is_liberal_once_asleep(self) -> None:
        # A missed wake strands the user at 1% brightness, so while asleep
        # anything that reads as "undo this" gets the screen back.
        for phrase in (
            "wake up the display",
            "turn the screen back on",
            "undim the panel",
            "brighten up",
            "the screen is too dark",
            "I can't see anything",
            "lights back on",
            "never mind",
        ):
            with self.subTest(phrase=phrase):
                with patch.object(self.sleep_mode, "LIBERAL_WAKE_GRACE_SECONDS", 0.0):
                    self.assertEqual(self.sleep_mode.match_phrase(phrase, asleep=True), "wake")
                # Awake, these are ordinary commands again.
                self.assertIsNone(self.sleep_mode.match_phrase(phrase, asleep=False))

    def test_model_phrasing_cannot_undo_the_sleep_it_just_announced(self) -> None:
        # The reply is the model's own wording now, and it is spoken with the
        # mic live while barge-in does not require the wake word -- so TALOS can
        # hear itself. A good night is very likely to mention waking or the
        # morning, and none of those may bounce back as a wake command.
        plausible_replies = (
            "Good night, sir. I will wake you in the morning.",
            "Sleeping now, sir. The panel is dim until morning.",
            "Of course. Waking at seven, as usual.",
            "Rest well, sir. Everything is dark and quiet.",
            "Awake again, sir. Brightness restored.",
        )
        for reply in plausible_replies:
            for grace in (self.sleep_mode.LIBERAL_WAKE_GRACE_SECONDS, 0.0):
                # Tested with the grace period BOTH armed and expired. Only
                # checking the armed case hides the real hole: an echo arriving
                # late, or the user quoting the phrase later, must not wake it
                # either.
                with self.subTest(reply=reply, grace=grace):
                    with patch.object(
                        self.sleep_mode, "LIBERAL_WAKE_GRACE_SECONDS", grace
                    ):
                        self.sleep_mode.sleep(reason="test")
                        self.sleep_mode.apply_phrase(reply, source="voice")
                        self.assertTrue(self.sleep_mode.is_asleep())

    def test_wake_me_at_seven_is_a_reminder_not_a_wake(self) -> None:
        self.sleep_mode.sleep(reason="test")
        with patch.object(self.sleep_mode, "LIBERAL_WAKE_GRACE_SECONDS", 0.0):
            for phrase in ("wake me at seven", "wake me up at seven", "wake us in an hour"):
                with self.subTest(phrase=phrase):
                    self.assertIsNone(self.sleep_mode.match_phrase(phrase, asleep=True))

    def test_ordinary_commands_do_not_wake_the_panel(self) -> None:
        # Night mode means a 3am question gets an answer without lighting the
        # room back up.
        self.sleep_mode.sleep(reason="test")
        self.assertIsNone(self.sleep_mode.apply_phrase("what is the weather this morning"))
        self.assertTrue(self.sleep_mode.is_asleep())

    def test_grace_period_ignores_loose_wakes_but_not_explicit_ones(self) -> None:
        self.sleep_mode.sleep(reason="test")
        # Loose match, moments after being told to sleep: almost certainly echo.
        self.assertIsNone(self.sleep_mode.match_phrase("it is too dark"))
        self.assertTrue(self.sleep_mode.is_asleep())
        # An explicit wake always works, grace period or not.
        self.assertIsNotNone(self.sleep_mode.apply_phrase("wake up"))
        self.assertFalse(self.sleep_mode.is_asleep())

    def test_loose_wake_works_once_the_grace_period_passes(self) -> None:
        self.sleep_mode.sleep(reason="test")
        with patch.object(self.sleep_mode, "LIBERAL_WAKE_GRACE_SECONDS", 0.0):
            self.assertEqual(self.sleep_mode.match_phrase("it is too dark"), "wake")

    def test_ordinary_commands_are_left_alone(self) -> None:
        # These mention sleeping, waking or the night without asking for night
        # mode. Matching any of them while awake would hijack a real request --
        # which is why the liberal wake vocabulary is gated on being asleep.
        for phrase in (
            "set a sleep timer for ten minutes",
            "how did you sleep",
            "send mom a goodnight message",
            "what time does the sun rise in the morning",
            "turn off the lights",
            "did the pump run last night",
            "wake me up at seven",
            "what is the weather this morning",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(self.sleep_mode.match_phrase(phrase, asleep=False))


class StateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sleep_mode = _fresh_sleep_mode(self)

    def test_missing_state_file_reads_as_awake(self) -> None:
        self.assertFalse(self.sleep_mode.is_asleep())

    def test_sleep_and_wake_round_trip(self) -> None:
        # apply_phrase returns a note for the model, never the spoken words.
        note = self.sleep_mode.apply_phrase("good night")
        self.assertIn("entered sleep mode", note)
        self.assertTrue(self.sleep_mode.is_asleep())
        self.assertIn("already on", self.sleep_mode.apply_phrase("sleep"))
        self.assertIn("left sleep mode", self.sleep_mode.apply_phrase("wake up"))
        self.assertFalse(self.sleep_mode.is_asleep())
        self.assertIn("already awake", self.sleep_mode.apply_phrase("wake up"))

    def test_note_tells_the_model_to_speak_for_itself(self) -> None:
        note = self.sleep_mode.apply_phrase("good night")
        # The note must read as instruction to the model, not as spoken words.
        self.assertTrue(note.startswith("[System note"))
        self.assertIn("your own voice", note)
        self.assertIn("do not call any tool", note)

    def test_state_survives_a_cold_read(self) -> None:
        # The MCP tool server and the pygame panel read this flag from other
        # processes, so it has to round-trip through the file rather than living
        # in a module global. Dropping the cache is what a cold reader sees.
        self.sleep_mode.sleep(reason="test")
        self.assertEqual(json.loads(self.sleep_mode.STATE_PATH.read_text())["asleep"], True)
        _reset_cache(self.sleep_mode)
        self.assertTrue(self.sleep_mode.is_asleep())

    def test_only_critical_speaks_while_asleep(self) -> None:
        self.assertTrue(self.sleep_mode.should_speak("notice"))
        self.sleep_mode.sleep(reason="test")
        self.assertFalse(self.sleep_mode.should_speak("notice"))
        self.assertFalse(self.sleep_mode.should_speak(""))
        self.assertTrue(self.sleep_mode.should_speak("critical"))

    def test_morning_briefing_is_the_wake_announcement(self) -> None:
        self.assertTrue(self.sleep_mode.is_wake_announcement("Morning briefing"))
        self.assertFalse(self.sleep_mode.is_wake_announcement("Arrival briefing"))
        self.assertFalse(self.sleep_mode.is_wake_announcement("[CRITICAL] Pump offline"))


def _make_config() -> TextServerConfig:
    return TextServerConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        api_token="",
        request_timeout=5,
        terminal_request_timeout=0,
        allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),),
        phone_push_token="",
    )


class _RunningServer:
    def __init__(self) -> None:
        self.central_queue: queue.Queue = queue.Queue()
        self.server = TextAgentHTTPServer(("127.0.0.1", 0), self.central_queue, _make_config())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _post(url: str, body: dict, timeout: float = 5) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class TextServerSleepTest(unittest.TestCase):
    """The text server is the one door every command and announcement passes."""

    def setUp(self) -> None:
        # The text server holds a reference to this same module object, so
        # redirecting its state file is enough -- no patching of the handler.
        self.sleep_mode = _fresh_sleep_mode(self)
        self.running = _RunningServer()
        self.addCleanup(self.running.close)

    def test_sleep_applies_before_the_model_and_the_model_still_answers(self) -> None:
        # The panel must not wait on generation, so the flag flips during the
        # request; the turn then continues to the agent, which supplies the
        # actual words. No router is running here, hence the 504.
        status, _ = _post(self.running.url("/chat"), {"message": "good night"}, timeout=20)
        self.assertEqual(status, 504)
        self.assertTrue(self.sleep_mode.is_asleep())

        message = self.running.central_queue.get_nowait()
        self.assertEqual(message.type, "text_cmd")
        # Command reaches the model unaltered, with the note carried alongside.
        self.assertEqual(message.payload.command, "good night")
        self.assertIn("entered sleep mode", message.payload.extra_context)
        # An acknowledgement is a reply, not a background job.
        self.assertEqual(message.payload.requested_mode, "foreground")

    def test_ordinary_command_still_reaches_the_router(self) -> None:
        # No router is running, so the request times out -- which is exactly the
        # proof that the command was handed off rather than intercepted.
        # Wait past the server's own 5 s request timeout so we read its 504
        # rather than timing out on the client side.
        status, payload = _post(
            self.running.url("/chat"), {"message": "turn on the fan"}, timeout=20
        )
        self.assertEqual(status, 504)
        self.assertFalse(payload["ok"])
        message = self.running.central_queue.get_nowait()
        self.assertEqual(message.type, "text_cmd")
        self.assertEqual(message.payload.command, "turn on the fan")

    def test_noncritical_speech_is_suppressed_while_asleep(self) -> None:
        self.sleep_mode.sleep(reason="test")
        status, payload = _post(
            self.running.url("/speak"),
            {"title": "Soil moisture low", "body": "Pot two is dry.", "severity": "notice"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["suppressed"])
        self.assertTrue(self.running.central_queue.empty())

    def test_critical_speech_still_speaks_while_asleep(self) -> None:
        self.sleep_mode.sleep(reason="test")
        status, payload = _post(
            self.running.url("/speak"),
            {"title": "Water on the floor", "body": "The pump is stuck open.", "severity": "critical"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        message = self.running.central_queue.get_nowait()
        self.assertEqual(message.type, "announcement")
        self.assertTrue(self.sleep_mode.is_asleep())

    def test_morning_briefing_wakes_the_house_and_is_spoken(self) -> None:
        self.sleep_mode.sleep(reason="test")
        status, payload = _post(
            self.running.url("/speak"),
            {"title": "Morning briefing", "body": "Fifty-two degrees and clear.", "severity": "notice"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(self.sleep_mode.is_asleep())
        message = self.running.central_queue.get_nowait()
        self.assertEqual(message.type, "announcement")


class RouterAnnouncementTest(unittest.TestCase):
    def test_severity_is_read_from_the_title_tag(self) -> None:
        from talos.router import _announcement_severity

        self.assertEqual(_announcement_severity("[CRITICAL] Pump down"), "critical")
        self.assertEqual(_announcement_severity("[NOTICE] Morning briefing"), "notice")
        self.assertEqual(_announcement_severity("Untagged title"), "notice")


if __name__ == "__main__":
    unittest.main()
