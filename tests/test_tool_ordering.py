from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.agent import runtime


def _tools(*names: str) -> list[dict[str, object]]:
    return [{"name": n, "type": "function"} for n in names]


# A representative surface: everyday always-on tools plus provider-gated groups
# that come and go between turns.
SURFACE = _tools(
    "set_temperature",
    "set_light_state",
    "tv_power",
    "awareness_query_state",
    "kicad_get_backend_state",
    "minecraft_search_logs",
    "phone",
)
VOLATILE_NAMES = {"kicad_get_backend_state", "minecraft_search_logs", "phone"}


class ToolOrderingTests(unittest.TestCase):
    """Ordering keeps the model server's cached prompt prefix intact: a tool
    that comes and goes must not sit ahead of one that is always published."""

    def test_volatile_groups_are_ordered_last(self):
        ordered = runtime._order_tools_by_volatility(SURFACE)
        names = [t["name"] for t in ordered]
        first_volatile = min(names.index(n) for n in VOLATILE_NAMES)
        stable_names = [n for n in names if n not in VOLATILE_NAMES]
        self.assertTrue(
            all(names.index(n) < first_volatile for n in stable_names),
            f"volatile tools should trail every stable tool: {names}",
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

    def test_dropping_a_volatile_group_leaves_the_stable_prefix_untouched(self):
        full = [t["name"] for t in runtime._order_tools_by_volatility(SURFACE)]
        without_volatile = [t for t in SURFACE if t["name"] not in VOLATILE_NAMES]
        stable = [t["name"] for t in runtime._order_tools_by_volatility(without_volatile)]
        self.assertEqual(full[: len(stable)], stable)


if __name__ == "__main__":
    unittest.main()
