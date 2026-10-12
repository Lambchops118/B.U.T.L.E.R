"""Discord outbound messaging tools ("tell Bob on Discord that dinner's ready").

The work happens in the Discord frontend; see butler/services/discord_messages.py.
Neither tool is on the restricted allowlist by default, so Discord users who are
not privileged never see them (butler/agent/tool_access.py).
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from butler.services import discord_messages


def register(server: FastMCP) -> None:
    """Register the Discord messaging tools on a FastMCP server."""

    @server.tool()
    async def discord_send_message(
        message: str,
        recipient: str = "",
        delivery: str = "dm",
        channel: str = "",
        server: str = "",
    ) -> str:
        """Send a message to someone on Discord, as the Discord bot.

        Use when asked to tell, message, ping, or remind a person on Discord,
        or to say something in a Discord channel. The person does not need to
        have talked to the bot before; server members are searched by name.
        message: the exact text to send, written as it should appear to them.
        recipient: their name, nickname, username, or numeric Discord user id.
        Leave empty to post in a channel without pinging anyone.
        delivery: "dm" (default; a private direct message) or "channel" (a
        post in a server text channel that @mentions the recipient).
        channel: channel name as the user said it (e.g. "general",
        "#raid-night") or a numeric id. Giving a channel always posts there.
        With delivery="channel" and no channel, the channel the bot was last
        used in is chosen.
        server: optional server name, only needed when several servers have
        a channel with that name.

        Returns JSON with "status": "sent" (done), "ambiguous" (several people
        match: ask which one using "candidates"), "not_found" (unknown name;
        "known_contacts" lists who is known), "failed", "invalid", "disabled",
        "unavailable" (frontend not running), or "unknown" (it may have been
        sent: do not resend without asking). "ambiguous"/"not_found" also apply
        to the channel or server, with "candidates"/"known_channels"/
        "known_servers". Only say the message was sent when status is "sent"."""
        result = await asyncio.to_thread(
            discord_messages.send_message, message, recipient, delivery, channel, server
        )
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    async def discord_list_contacts() -> str:
        """List the Discord users the bot can message by name (people it has
        heard in text or voice, plus configured aliases). Returns JSON with
        "contacts", or a "status"/"error" pair if the frontend is unavailable."""
        result = await asyncio.to_thread(discord_messages.list_contacts)
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    async def discord_list_channels() -> str:
        """List the Discord text channels the bot can post in, with their
        server names. Returns JSON with "channels", or a "status"/"error"
        pair if the frontend is unavailable."""
        result = await asyncio.to_thread(discord_messages.list_channels)
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    async def discord_voice(action: str, channel: str = "", server: str = "") -> str:
        """Make the Discord bot join or leave a Discord voice call.

        Use for "join discord", "hop in my Discord call", "join the gaming
        voice channel", "leave discord". This only moves the bot within
        Discord; it does not route the house microphones or speakers.
        action: "join", "leave", or "status".
        channel: for join, a voice channel name or id. Leave empty to join
        whatever voice channel the owner is in right now.
        server: optional server name, to pick between same-named channels or
        to leave just one server.

        Returns JSON with "status": "joined", "already_connected",
        "waiting" (the owner is not in voice yet; the bot will join by itself
        as soon as they enter a voice channel, so tell them to hop in),
        "left", "not_connected", "ok" (status, with "connected" and
        "waiting_for_owner"), "not_found" (unknown channel/server; see
        "known_channels"/"known_servers"), "ambiguous" (see "candidates"),
        "failed", "invalid", "disabled", "unavailable" (Discord bot not
        running), or "unknown". Only claim the bot joined when status is
        "joined" or "already_connected". If the user later says they are in
        the call, call this tool again (action "status") instead of assuming."""
        result = await asyncio.to_thread(discord_messages.voice, action, channel, server)
        return json.dumps(result, ensure_ascii=False)
