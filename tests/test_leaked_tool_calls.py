from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime


class MarkupDetectionTests(unittest.TestCase):
    def test_natural_text_is_not_markup(self):
        for text in ["It is 71 degrees.", "No recent calls.", "Yes, sir."]:
            self.assertFalse(runtime._looks_like_tool_markup_start(text.lstrip()))

    def test_json_and_tags_are_markup(self):
        self.assertTrue(runtime._looks_like_tool_markup_start('{"name": "x"'))
        self.assertTrue(runtime._looks_like_tool_markup_start("<tool_call>"))


class ExtractLeakedToolCallsTests(unittest.TestCase):
    def test_bare_json_object(self):
        calls = runtime._extract_leaked_tool_calls(
            '{"name": "get_current_weather", "arguments": {"location": "Ellicott City, MD"}}'
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "get_current_weather")
        self.assertIn("Ellicott City", calls[0].arguments)

    def test_tool_call_tag_wrapper(self):
        calls = runtime._extract_leaked_tool_calls(
            '<tool_call>\n{"name": "list_recent_calls", "arguments": {}}\n</tool_call>'
        )
        self.assertEqual([c.name for c in calls], ["list_recent_calls"])
        self.assertEqual(calls[0].arguments, "{}")

    def test_multiple_bare_objects(self):
        calls = runtime._extract_leaked_tool_calls(
            '{"name": "a", "arguments": {}}{"name": "b", "arguments": {"x": 1}}'
        )
        self.assertEqual([c.name for c in calls], ["a", "b"])

    def test_natural_text_recovers_nothing(self):
        self.assertEqual(runtime._extract_leaked_tool_calls("It is 71 degrees."), ())
        self.assertEqual(runtime._extract_leaked_tool_calls(""), ())

    def test_json_without_name_is_ignored(self):
        self.assertEqual(runtime._extract_leaked_tool_calls('{"arguments": {}}'), ())


if __name__ == "__main__":
    unittest.main()
