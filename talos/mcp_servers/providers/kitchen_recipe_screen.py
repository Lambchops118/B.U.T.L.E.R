"""Kitchen recipe screen tools, published as one action-keyed dispatcher.

The screen exposes 28 distinct operations. Registered individually they cost
~9.6 KB of JSON schema, which is re-sent with every tool on every round for a
capability that only matters while the user is actually cooking. They are
collapsed into a single ``kitchen_screen_control`` tool whose ``action`` selects
the operation; the underlying service calls are unchanged.

The ``kitchen_screen_`` prefix is deliberate: the agent runtime scopes this
group out of the tool surface unless a request shows cooking intent, and that
check matches on the prefix.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from talos.services import kitchen_recipe_screen as screen


# action -> service callable. Every callable takes keyword arguments only, so
# the JSON ``arguments`` object maps straight through.
_ACTIONS: dict[str, Callable[..., str]] = {
    # status
    "health": lambda: screen.get_screen_health(),
    "get_state": lambda: screen.get_screen_state(),
    # header / servings / link
    "set_recipe_header": lambda title="", subtitle="": screen.set_recipe_header(
        title=title, subtitle=subtitle
    ),
    "clear_recipe_header": lambda: screen.clear_recipe_header(),
    "set_servings": lambda servings: screen.set_servings(servings),
    "reset_servings": lambda: screen.reset_servings(),
    "set_link_status": lambda link_status: screen.set_link_status(link_status),
    "read_link_status": lambda: screen.read_link_status(),
    # whole recipe
    "replace_recipe_content": lambda title="", subtitle="", servings="", ingredients=None, steps=None, notes=None: screen.replace_recipe_content(
        title=title,
        subtitle=subtitle,
        servings=servings,
        ingredients=ingredients,
        steps=steps,
        notes=notes,
    ),
    "clear_recipe_screen": lambda: screen.clear_recipe_screen(),
    # ingredients
    "read_ingredients": lambda: screen.read_ingredients(),
    "replace_ingredients": lambda ingredients: screen.replace_ingredients(ingredients),
    "remove_ingredients": lambda indices=None, matching_texts=None, clear_all=False: screen.remove_ingredients(
        indices=indices, matching_texts=matching_texts, clear_all=clear_all
    ),
    "clear_ingredients": lambda: screen.clear_ingredients(),
    # steps
    "read_steps": lambda: screen.read_steps(),
    "replace_steps": lambda steps: screen.replace_steps(steps),
    "remove_steps": lambda indices=None, matching_texts=None, clear_all=False: screen.remove_steps(
        indices=indices, matching_texts=matching_texts, clear_all=clear_all
    ),
    "clear_steps": lambda: screen.clear_steps(),
    # notes
    "read_notes": lambda: screen.read_notes(),
    "add_notes": lambda notes: screen.add_notes(notes),
    "replace_notes": lambda notes: screen.replace_notes(notes),
    "remove_notes": lambda indices=None, matching_texts=None, clear_all=False: screen.remove_notes(
        indices=indices, matching_texts=matching_texts, clear_all=clear_all
    ),
    "clear_notes": lambda: screen.clear_notes(),
    # timer
    "set_timer": lambda duration_seconds, label="Recipe timer", auto_start=False: screen.set_timer(
        duration_seconds=duration_seconds, label=label, auto_start=auto_start
    ),
    "read_timer": lambda: screen.read_timer(),
    "start_timer": lambda: screen.start_timer(),
    "stop_timer": lambda: screen.stop_timer(),
    "reset_timer": lambda: screen.reset_timer(),
}


def _error(message: str) -> str:
    return json.dumps({"success": False, "message": message}, ensure_ascii=False)


def register(server: FastMCP) -> None:
    """Register the kitchen recipe screen dispatcher on a FastMCP server."""

    @server.tool()
    def kitchen_screen_control(action: str, arguments: str = "{}") -> str:
        """Control the kitchen recipe screen. `arguments` is a JSON object string
        holding that action's parameters, e.g. '{"steps": ["Preheat oven"]}'.

        Read: health, get_state, read_ingredients, read_steps, read_notes,
        read_timer, read_link_status.

        Recipe: replace_recipe_content (title, subtitle, servings, ingredients,
        steps, notes - sets a whole recipe in one call), set_recipe_header
        (title, subtitle), clear_recipe_header, clear_recipe_screen.

        Lists: replace_ingredients (ingredients), replace_steps (steps),
        add_notes (notes), replace_notes (notes), and remove_ingredients /
        remove_steps / remove_notes (indices as 1-based ints, matching_texts, or
        clear_all), plus clear_ingredients / clear_steps / clear_notes.

        Timer: set_timer (duration_seconds, optional label and auto_start),
        start_timer, stop_timer, reset_timer.

        Other: set_servings (servings), reset_servings, set_link_status
        (link_status, e.g. LINK NOMINAL).
        """
        handler = _ACTIONS.get(str(action or "").strip().lower())
        if handler is None:
            return _error(
                f"Unknown kitchen_screen_control action '{action}'. "
                f"Valid actions: {', '.join(sorted(_ACTIONS))}."
            )

        try:
            parsed: Any = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return _error(f"arguments must be a JSON object: {exc}")
        if not isinstance(parsed, dict):
            return _error("arguments must be a JSON object")

        try:
            return handler(**parsed)
        except TypeError as exc:
            # Wrong/missing parameters for this action: report it back to the
            # model instead of raising through the MCP transport.
            return _error(f"invalid arguments for '{action}': {exc}")
