"""Sleep mode as a single action-keyed tool.

The spoken triggers ("butler, sleep", "butler, good night", "butler, wake up")
are recognised deterministically in the text server, which flips the flag before
the model runs so the panel never waits on generation -- the model is then told
what happened and phrases the reply itself. This tool is for the turns that
phrase list does not cover: "shut everything down for the night", "is the panel
still dimmed?", or a request buried in a longer sentence. One tool with an
``action`` argument, for the same reason the kitchen screen collapses to one:
the schema is re-sent on every turn.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from talos.services import display_power, sleep_mode


def register(server: FastMCP) -> None:
    """Register the sleep mode control tool on a FastMCP server."""

    @server.tool()
    def sleep_mode_control(action: str) -> str:
        """Control night sleep mode. This is also the screen control: sleep
        mode and a dark screen are always the same thing.

        action: "sleep" (darken the info panel, put the TV into standby, and
        hold back noncritical spoken alerts), "wake" (restore all three), or
        "status". Use this one tool for any request about the
        screen or the lights on the panel -- dim it, turn it off, brighten it,
        "I can't see", "turn the screen back on" -- there is no separate
        display command to call afterwards. Sleep mode also ends by itself at
        the morning wake-up."""
        normalized = str(action or "").strip().lower()
        try:
            if normalized == "sleep":
                state = sleep_mode.sleep(reason="requested via tool")
            elif normalized == "wake":
                state = sleep_mode.wake(reason="requested via tool")
            elif normalized == "status":
                state = sleep_mode.state(force_reload=True)
            else:
                return json.dumps(
                    {"error": f"unknown action {normalized!r}; use sleep, wake, or status"}
                )
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)})
        # The display command is dispatched in the background, so report its
        # last outcome rather than leaving "the screen did not change" to guesswork.
        return json.dumps(
            {**state, "display_command": display_power.last_result()}, ensure_ascii=False
        )
