from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime


def _tools(*names: str) -> list[dict[str, object]]:
    return [{"name": n, "type": "function"} for n in names]


# A representative surface: a few everyday tools plus the large kitchen group.
SURFACE = _tools(
    "set_temperature",
    "set_light_state",
    "tv_power",
    "awareness_query_state",
    "kitchen_screen_replace_recipe_content",
    "kitchen_screen_set_timer",
    "kitchen_screen_add_notes",
)
KITCHEN_NAMES = {"kitchen_screen_replace_recipe_content", "kitchen_screen_set_timer", "kitchen_screen_add_notes"}


class KitchenIntentTests(unittest.TestCase):
    def test_cooking_request_keeps_kitchen_tools(self):
        for cmd in [
            "start a timer for the cookies",
            "put this recipe on the kitchen screen",
            "add flour to the list",
            "what are the next steps for dinner",
        ]:
            self.assertTrue(runtime._is_kitchen_request(cmd, SURFACE), cmd)

    def test_non_cooking_request_is_not_kitchen(self):
        for cmd in [
            "write me some code to generate a cube in pygame",
            "it's hot in here",
            "turn off the living room lights",
            "what's on tv tonight",
        ]:
            self.assertFalse(runtime._is_kitchen_request(cmd, SURFACE), cmd)


class ScopeSpecializedToolsTests(unittest.TestCase):
    def test_coding_request_drops_kitchen_tools_but_keeps_everyday(self):
        scoped = runtime._scope_specialized_tools(
            SURFACE, "write me some code to generate a cube in pygame"
        )
        names = {t["name"] for t in scoped}
        self.assertFalse(names & KITCHEN_NAMES, "kitchen tools should be dropped")
        # Everyday inference tools remain available.
        self.assertIn("set_temperature", names)
        self.assertIn("set_light_state", names)
        self.assertIn("awareness_query_state", names)

    def test_hot_in_here_keeps_home_automation(self):
        # The inference example must retain the temperature tool.
        scoped = runtime._scope_specialized_tools(SURFACE, "it's hot in here")
        names = {t["name"] for t in scoped}
        self.assertIn("set_temperature", names)
        self.assertFalse(names & KITCHEN_NAMES)

    def test_cooking_request_keeps_full_surface(self):
        scoped = runtime._scope_specialized_tools(SURFACE, "set a kitchen timer for the roast")
        self.assertEqual({t["name"] for t in scoped}, {t["name"] for t in SURFACE})

    def test_no_kitchen_tools_present_is_noop(self):
        surface = _tools("set_temperature", "tv_power")
        scoped = runtime._scope_specialized_tools(surface, "write some python")
        self.assertEqual(scoped, surface)


if __name__ == "__main__":
    unittest.main()
