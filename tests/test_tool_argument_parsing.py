from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.tool_arguments import parse_tool_arguments


class ToolArgumentParsingTests(unittest.TestCase):
    def test_parses_valid_json_object(self) -> None:
        parsed = parse_tool_arguments('{"title":"Cookies","count":2}')
        self.assertEqual(parsed["title"], "Cookies")
        self.assertEqual(parsed["count"], 2)

    def test_repairs_truncated_json_by_closing_string_and_containers(self) -> None:
        parsed = parse_tool_arguments(
            '{"title":"Chocolate Chip Cookies","steps":["Preheat oven","Beat in the eggs one at a'
        )
        self.assertEqual(parsed["title"], "Chocolate Chip Cookies")
        self.assertEqual(parsed["steps"], ["Preheat oven", "Beat in the eggs one at a"])

    def test_repairs_literal_newlines_inside_strings(self) -> None:
        parsed = parse_tool_arguments('{"notes":["Line one\nLine two"]}')
        self.assertEqual(parsed["notes"], ["Line one\nLine two"])

    def test_rejects_non_object_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_tool_arguments('["not","an","object"]')


if __name__ == "__main__":
    unittest.main()
