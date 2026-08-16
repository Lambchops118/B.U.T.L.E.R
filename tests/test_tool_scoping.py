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


class ToolOrderingTests(unittest.TestCase):
    """Ordering keeps the model server's cached prompt prefix intact: a tool
    that comes and goes must not sit ahead of one that is always published."""

    def test_volatile_groups_are_ordered_last(self):
        ordered = runtime._order_tools_by_volatility(SURFACE)
        names = [t["name"] for t in ordered]
        first_kitchen = min(names.index(n) for n in KITCHEN_NAMES)
        stable_names = [n for n in names if n not in KITCHEN_NAMES]
        self.assertTrue(
            all(names.index(n) < first_kitchen for n in stable_names),
            f"kitchen tools should trail every stable tool: {names}",
        )

    def test_phone_and_provider_gated_tools_are_volatile(self):
        surface = _tools(
            "phone",
            "kicad_get_backend_state",
            "minecraft_search_logs",
            "mcp_admin",
            "turn_on_lights",
        )
        names = [t["name"] for t in runtime._order_tools_by_volatility(surface)]
        self.assertEqual(names[:2], ["mcp_admin", "turn_on_lights"])
        self.assertEqual(
            set(names[2:]), {"phone", "kicad_get_backend_state", "minecraft_search_logs"}
        )

    def test_ordering_is_independent_of_input_order(self):
        forward = runtime._order_tools_by_volatility(SURFACE)
        reversed_input = runtime._order_tools_by_volatility(list(reversed(SURFACE)))
        self.assertEqual(
            [t["name"] for t in forward], [t["name"] for t in reversed_input]
        )

    def test_ordering_preserves_the_tool_set(self):
        ordered = runtime._order_tools_by_volatility(SURFACE)
        self.assertEqual(
            sorted(t["name"] for t in ordered), sorted(t["name"] for t in SURFACE)
        )

    def test_dropping_a_scoped_group_leaves_the_stable_prefix_untouched(self):
        full = [t["name"] for t in runtime._order_tools_by_volatility(SURFACE)]
        scoped = runtime._scope_specialized_tools(SURFACE, "turn off the lights")
        without = [t["name"] for t in runtime._order_tools_by_volatility(scoped)]
        self.assertEqual(full[: len(without)], without)


if __name__ == "__main__":
    unittest.main()
