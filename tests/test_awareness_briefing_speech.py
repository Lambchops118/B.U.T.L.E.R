"""Speech stays concise even for diagnostic records queued before the fix."""
import unittest

from talos.awareness.briefing.speech import candidate_text, novelty_text, render_batch, transition_text


class BriefingSpeechTest(unittest.TestCase):
    def test_legacy_job_is_not_read_as_diagnostics(self):
        candidate = {"category": "agent_outcome", "text": 'EVENT talos: agent.job.completed [info] (recorded 2026-09-06; source=talos_agent) {"log": "secret"}'}
        self.assertEqual(render_batch([candidate, candidate], kind="arrival"),
                         "Welcome back. A background job completed earlier.")

    def test_metadata_is_silent_but_critical_is_preserved(self):
        candidate = {"entity_id": "owner", "text": "CHANGE owner.detail -> {'log': 'secret'}", "priority": 6}
        self.assertEqual(render_batch([candidate], kind="arrival"), "")
        candidate.update(spoken_text="", priority=1)
        self.assertEqual(candidate_text(candidate), "An important alert needs your attention.")

    def test_raw_or_long_content_uses_bounded_fallback(self):
        for text in ('{"log":"secret"}', "x" * 1000, "Traceback: secret", "source=secret"):
            self.assertEqual(candidate_text({"spoken_text": text, "priority": 1}),
                             "An important alert needs your attention.")

    def test_sensor_summary_preserves_value_without_statistics(self):
        self.assertEqual(novelty_text("fan", "temperature", 30, "C", 20),
                         "Earlier, the fan's temperature was unusually high, at 30 degrees Celsius.")

    def test_transitions_do_not_speak_objects_or_assert_unknown_absence(self):
        self.assertEqual(transition_text("owner", "detail", {"log": "secret"}, "current"), "")
        self.assertEqual(transition_text("owner", "present", None, "unknown"),
                         "Your presence reading was uncertain earlier.")
        self.assertEqual(transition_text("owner", "present", True, "stale"), "")
        self.assertEqual(transition_text("fan", "state", {"log": "secret"}, "current"),
                         "The fan's state changed earlier.")

    def test_an_expiry_and_recovery_pair_is_never_spoken_as_a_homecoming(self):
        """The idle timer's own bookkeeping is not a fact about the person.

        A `stale -> current` transition carries the same value on both sides:
        nobody arrived, a reading was simply re-confirmed. Speaking it produced
        the "your presence was detected again" the user never triggered.
        """
        self.assertEqual(
            transition_text("owner", "present", True, "current", True, "stale"), "")
        # A real arrival still speaks: the value itself changed.
        self.assertEqual(
            transition_text("owner", "present", True, "current", False, "current"),
            "Your presence was detected again.")
        # A first-ever reading has no previous value and is still spoken.
        self.assertEqual(
            transition_text("owner", "present", True, "current", None, None),
            "Your presence was detected again.")

    def test_continuations_do_not_repeat_greeting(self):
        self.assertEqual(render_batch([{"spoken_text": "The task completed."}], kind="arrival", part=1),
                         "The task completed.")
