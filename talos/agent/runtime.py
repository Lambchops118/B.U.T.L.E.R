from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any

import openai

from talos.agent.prompting import DEFAULT_DOMAIN_OVERLAYS, PromptContext, build_instructions
from talos.agent.thinking import thinking_suffix
from talos.config import env_bool, env_float, env_int, load_environment, require_env
from talos.memory import MemoryStore, get_default_memory_store
from talos.mcp_client import get_local_mcp_client, shutdown_local_mcp_client
from talos.tool_arguments import parse_tool_arguments
from talos.voice.backends.base import (
    LLMCompletion,
    LLMTextDelta,
    LLMToolCall,
    chat_messages_to_tool_result,
    tool_calls_to_assistant_message,
)


load_environment()

_client: openai.OpenAI | None = None

ai_model = os.getenv("OPENAI_VOICE_MODEL", "gpt-4o-mini")
ROUTER_MODEL = os.getenv("TALOS_ROUTER_MODEL", ai_model)
MAX_TOOL_CALL_ROUNDS = max(1, env_int("TALOS_MAX_TOOL_CALL_ROUNDS", 8))
MEMORY_ENABLED = env_bool("TALOS_MEMORY_ENABLED", True)
PROMPT_MEMORY_CHAR_LIMIT = max(0, env_int("TALOS_PROMPT_MEMORY_CHAR_LIMIT", 1600))
CONVERSATION_HISTORY_MESSAGE_LIMIT = max(
    0,
    env_int("TALOS_CONVERSATION_HISTORY_MESSAGE_LIMIT", 8),
)
CONVERSATION_HISTORY_CHAR_LIMIT = max(
    0,
    env_int("TALOS_CONVERSATION_HISTORY_CHAR_LIMIT", 4000),
)
OPENAI_SERVER_ERROR_RETRIES = max(0, env_int("TALOS_OPENAI_SERVER_ERROR_RETRIES", 2))
OPENAI_SERVER_ERROR_RETRY_DELAY = max(
    0.0, env_float("TALOS_OPENAI_SERVER_ERROR_RETRY_DELAY", 1.5)
)
OPENAI_SERVER_ERROR_RETRY_MAX_DELAY = max(
    OPENAI_SERVER_ERROR_RETRY_DELAY,
    env_float("TALOS_OPENAI_SERVER_ERROR_RETRY_MAX_DELAY", 20.0),
)
OPENAI_SERVER_ERROR_RETRY_JITTER = max(
    0.0, env_float("TALOS_OPENAI_SERVER_ERROR_RETRY_JITTER", 0.25)
)
OPENAI_SERVER_ERROR_RECOVERY_ATTEMPTS = max(
    0, env_int("TALOS_OPENAI_SERVER_ERROR_RECOVERY_ATTEMPTS", 1)
)
AGENT_MAX_OUTPUT_TOKENS = max(150, env_int("TALOS_AGENT_MAX_OUTPUT_TOKENS", 400))
KICAD_TOOL_PREFIX = os.getenv("KICAD_MCP_TOOL_PREFIX", "kicad_").strip() or "kicad_"
TOOL_OUTPUT_CHAR_LIMIT = max(256, env_int("TALOS_TOOL_OUTPUT_CHAR_LIMIT", 4000))
TOOL_OUTPUT_SUMMARY_ENABLED = env_bool("TALOS_SUMMARIZE_TOOL_OUTPUTS", True)
TOOL_OUTPUT_SUMMARY_PREVIEW_ITEMS = max(
    1, env_int("TALOS_TOOL_OUTPUT_SUMMARY_PREVIEW_ITEMS", 5)
)
TOOL_OUTPUT_SUMMARY_PREVIEW_KEYS = max(
    1, env_int("TALOS_TOOL_OUTPUT_SUMMARY_PREVIEW_KEYS", 12)
)
MINECRAFT_TOOL_PREFIX = os.getenv("MINECRAFT_MCP_SEARCH_TOOL_PREFIX", "minecraft_").strip() or "minecraft_"
MINECRAFT_FILESYSTEM_TOOL_PREFIX = (
    os.getenv("MINECRAFT_MCP_FILESYSTEM_TOOL_PREFIX", "minecraft_fs_").strip() or "minecraft_fs_"
)
MINECRAFT_SERVER_DIR = os.getenv("MINECRAFT_SERVER_DIR", "").strip()

_conversation_state_lock = threading.Lock()
_conversation_locks: dict[str, threading.Lock] = {}
_last_response_ids: dict[str, str] = {}
HOST_TOOL_NAMES = {
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
    "list_mcp_server_status",
    "list_mcp_tools",
    "retry_mcp_server",
    "start_mcp_server",
    "remember_memory_fact",
    "list_memory_facts",
    "place_phone_call",
    "phone_call_status",
    "recent_phone_calls",
    "summarize_phone_call",
}

KICAD_PREFERRED_TOOL_SUFFIXES = {
    "list_tool_categories",
    "get_category_tools",
    "search_tools",
    "execute_tool",
    "create_project",
    "open_project",
    "save_project",
    "snapshot_project",
    "get_project_info",
    "set_board_size",
    "add_board_outline",
    "get_board_info",
    "check_kicad_ui",
    "launch_kicad_ui",
    "get_backend_state",
    "list_symbol_libraries",
    "list_library_symbols",
    "search_symbols",
    "get_symbol_info",
    "add_schematic_component",
    "list_schematic_components",
    "get_schematic_component",
    "edit_schematic_component",
    "delete_schematic_component",
    "move_schematic_component",
    "rotate_schematic_component",
    "annotate_schematic",
    "connect_passthrough",
    "connect_to_net",
    "add_schematic_net_label",
    "sync_schematic_to_board",
    "place_component",
    "move_component",
    "add_net",
    "route_trace",
}
KICAD_BACKEND_STATE_TOOL = f"{KICAD_TOOL_PREFIX}get_backend_state"
KICAD_UI_CHECK_TOOL = f"{KICAD_TOOL_PREFIX}check_kicad_ui"
KICAD_UI_LAUNCH_TOOL = f"{KICAD_TOOL_PREFIX}launch_kicad_ui"
KICAD_SYNC_TOOL = f"{KICAD_TOOL_PREFIX}sync_schematic_to_board"
KICAD_RELEVANT_TERMS = {
    "kicad",
    "pcb",
    "board",
    "schematic",
    "footprint",
    "trace",
    "routing",
    "route",
    "net",
    "component",
    "components",
    "place",
    "placement",
    "populate",
    "layout",
}
KICAD_BOARD_EDIT_TERMS = {
    "board",
    "footprint",
    "trace",
    "route",
    "routing",
    "net",
    "place",
    "placement",
    "populate",
    "layout",
    "move",
    "rotate",
}
MINECRAFT_RELEVANT_TERMS = {
    "minecraft",
    "forge",
    "neoforge",
    "fabric",
    "modded",
    "modpack",
    "mods",
    "kubejs",
    "datapack",
    "datapacks",
    "serverconfig",
    "defaultconfigs",
    "crash",
    "mixin",
    "modloadingexception",
}
PHONE_RELEVANT_TERMS = {
    "call",
    "phone",
    "dial",
    "ring",
    "voice",
}
MINECRAFT_PATH_HINTS = (
    "world/serverconfig/",
    "defaultconfigs/",
    "kubejs/",
    "datapacks/",
    "forge-server.toml",
)
KICAD_SCHEMATIC_TO_BOARD_TERMS = {
    "schematic",
    "sync",
    "footprint",
    "place",
    "placement",
    "populate",
    "route",
    "routing",
}


def _resource_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "list_mcp_resources",
            "description": "List read-only MCP resources exposed by connected servers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh the cached MCP resource list before returning it.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_mcp_resource_templates",
            "description": "List MCP resource templates exposed by connected servers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh the cached MCP resource template list before returning it.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_mcp_resource",
            "description": "Read a specific MCP resource URI. Use the server field when more than one server exposes the same URI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "uri": {
                        "type": "string",
                        "description": "The MCP resource URI to read.",
                    },
                    "server": {
                        "type": "string",
                        "description": "Optional server name for disambiguation when duplicate URIs exist.",
                    },
                },
                "required": ["uri"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_mcp_server_status",
            "description": "List configured MCP servers and their current health, failure, and retry state. This is read-only and does not retry degraded servers.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_mcp_tools",
            "description": "List currently available MCP tools and configured server health. Use this when asked what tools are available. This does not retry degraded servers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh the available tool list from currently healthy servers without retrying degraded servers.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "retry_mcp_server",
            "description": "Explicitly retry starting one degraded MCP server, or all degraded servers when server is omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Optional MCP server name to retry, such as kicad.",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "start_mcp_server",
            "description": (
                "Explicitly start or warm a heavyweight MCP provider (such as kicad) without restarting TALOS. "
                "By default this returns immediately while the provider warms in the background, so use "
                "list_mcp_server_status to check when it becomes ready. Its tools appear only after it is ready."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MCP server name to start, such as kicad.",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Wait for the provider to finish starting instead of warming in the background. Defaults to false.",
                    },
                },
                "required": ["server"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "remember_memory_fact",
            "description": (
                "Persist a stable user, project, environment, or session fact for future TALOS prompt context. "
                "Use this when the user explicitly asks you to remember something durable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Memory scope: user, project, global, or session. Defaults to user.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Short stable key for the fact, such as preferred_response_style.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The durable fact to remember.",
                    },
                    "salience": {
                        "type": "integer",
                        "description": "Importance from 1 to 10. Defaults to 5.",
                    },
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_memory_facts",
            "description": "List compact durable memory facts relevant to a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search text for relevant facts.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum facts to return. Defaults to 8.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "place_phone_call",
            "description": (
                "Place an outbound phone call through the configured ElevenLabs/Twilio phone agent. "
                "Use this only when the user directly asks you to make the call now. "
                "The target must be either a configured contact name or an allowlisted E.164 number. "
                "If the user wants you to report weather, KiCad status, or any other result, gather that information first, "
                "then pass the exact spoken report in message_to_deliver."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_or_number": {
                        "type": "string",
                        "description": "Configured contact name or allowlisted E.164 number to call.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Short reason for the call.",
                    },
                    "brief_context": {
                        "type": "string",
                        "description": "Optional concise context the phone agent should know before the call starts.",
                    },
                    "message_to_deliver": {
                        "type": "string",
                        "description": "The exact report or message the phone agent should deliver on the call after introducing itself as TALOS.",
                    },
                },
                "required": ["contact_or_number"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "phone_call_status",
            "description": "Read the current status, transcript, and metadata for one phone call by call id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "The TALOS phone call id, usually the ElevenLabs conversation id.",
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh from the configured phone bridge before reading the status. Defaults to true.",
                    },
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "recent_phone_calls",
            "description": "List recent phone calls known to TALOS, including inbound and outbound calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum calls to return. Defaults to 10.",
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh from the configured phone bridge before listing calls. Defaults to true.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "summarize_phone_call",
            "description": "Return a compact TALOS summary of a phone call by call id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "The TALOS phone call id, usually the ElevenLabs conversation id.",
                    },
                    "refresh": {
                        "type": "boolean",
                        "description": "Refresh from the configured phone bridge before summarizing the call. Defaults to true.",
                    },
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    ]


def _is_server_error(exc: Exception) -> bool:
    lowered = str(exc).lower()
    return "server_error" in lowered or "error code: 500" in lowered or "http 500" in lowered


def _get_openai_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = require_env("OPENAI_API_KEY")
        openai.api_key = api_key
        _client = openai.OpenAI(api_key=api_key)
    return _client


def _responses_create_with_retry(**kwargs):
    attempt = 0
    while True:
        try:
            return _get_openai_client().responses.create(**kwargs)
        except Exception as exc:
            if attempt >= OPENAI_SERVER_ERROR_RETRIES or not _is_server_error(exc):
                raise
            attempt += 1
            retry_delay = min(
                OPENAI_SERVER_ERROR_RETRY_DELAY * (2 ** (attempt - 1)),
                OPENAI_SERVER_ERROR_RETRY_MAX_DELAY,
            )
            if OPENAI_SERVER_ERROR_RETRY_JITTER > 0:
                jitter_window = retry_delay * OPENAI_SERVER_ERROR_RETRY_JITTER
                retry_delay += random.uniform(-jitter_window, jitter_window)
                retry_delay = max(0.0, retry_delay)
            print(
                f"OpenAI server error on attempt {attempt}; retrying in "
                f"{retry_delay:.1f}s..."
            )
            time.sleep(retry_delay)


def _reduce_tool_surface(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reduced: list[dict[str, Any]] = []
    kept_kicad = 0
    skipped_kicad = 0

    for tool in tool_defs:
        name = str(tool.get("name") or "")
        if not name.startswith(KICAD_TOOL_PREFIX):
            reduced.append(tool)
            continue

        suffix = name[len(KICAD_TOOL_PREFIX) :]
        if suffix in KICAD_PREFERRED_TOOL_SUFFIXES:
            reduced.append(tool)
            kept_kicad += 1
        else:
            skipped_kicad += 1

    if skipped_kicad:
        print(
            f"Reduced KiCad tool surface: kept {kept_kicad}, skipped {skipped_kicad} "
            f"for model context."
        )
    return reduced


def _build_tool_definitions(mcp_client: Any) -> list[dict[str, Any]]:
    tool_defs = _resource_tool_definitions() + mcp_client.openai_tool_definitions()
    reduce_kicad = os.getenv("TALOS_REDUCE_KICAD_TOOL_SURFACE", "1").strip().lower()
    if reduce_kicad not in {"0", "false", "no", "off"}:
        tool_defs = _reduce_tool_surface(tool_defs)

    try:
        tool_bytes = len(json.dumps(tool_defs).encode("utf-8"))
    except Exception:
        tool_bytes = 0
    print(f"Tool definitions prepared: {len(tool_defs)} tools, {tool_bytes} bytes")
    return tool_defs


def _normalize_interaction_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized.startswith("voice"):
        return "voice"
    if normalized.startswith("text"):
        return "text"
    return "text"


def _normalize_runtime_lane(lane: str | None) -> str:
    normalized = str(lane or "").strip().lower()
    if normalized in {"background", "job", "worker"}:
        return "background"
    return "foreground"


def _response_thread_key(session_id: str, runtime_lane: str | None) -> str:
    lane = _normalize_runtime_lane(runtime_lane)
    return f"{lane}:{str(session_id or 'default').strip() or 'default'}"


def _get_conversation_lock(thread_key: str) -> threading.Lock:
    with _conversation_state_lock:
        lock = _conversation_locks.get(thread_key)
        if lock is None:
            lock = threading.Lock()
            _conversation_locks[thread_key] = lock
        return lock


def _get_last_response_id(thread_key: str) -> str | None:
    with _conversation_state_lock:
        return _last_response_ids.get(thread_key)


def _set_last_response_id(thread_key: str, response_id: str) -> None:
    with _conversation_state_lock:
        _last_response_ids[thread_key] = response_id


def _clear_response_id(thread_key: str) -> None:
    with _conversation_state_lock:
        _last_response_ids.pop(thread_key, None)


def _clear_session_response_ids(session_id: str) -> None:
    normalized_session = str(session_id or "").strip()
    with _conversation_state_lock:
        for key in list(_last_response_ids):
            key_session = key.split(":", 1)[1] if ":" in key else key
            if key_session == normalized_session:
                _last_response_ids.pop(key, None)


def _infer_interaction_mode(session_id: str) -> str:
    normalized = str(session_id or "").strip().lower()
    if normalized.startswith("voice"):
        return "voice"
    return "text"


def _domain_overlays_for_command(command: str, tool_defs: list[dict[str, Any]]) -> tuple[str, ...]:
    overlays = list(DEFAULT_DOMAIN_OVERLAYS)
    if _is_kicad_request(command, tool_defs):
        overlays.append("kicad")
    if _is_minecraft_request(command, tool_defs):
        overlays.append("minecraft")
    if _is_phone_request(command, tool_defs):
        overlays.append("phone")
    return tuple(overlays)


def _build_prompt_instructions(
    command: str,
    session_id: str,
    tool_defs: list[dict[str, Any]],
    *,
    memory_block: str | None = None,
    interaction_mode: str | None = None,
    extra_context: str | None = None,
) -> str:
    mode = _normalize_interaction_mode(interaction_mode or _infer_interaction_mode(session_id))
    context = PromptContext(
        interaction_mode=mode,
        domain_overlays=_domain_overlays_for_command(command, tool_defs),
        memory_block=memory_block,
        extra_context=extra_context,
    )
    return build_instructions(context)


def _get_memory_store() -> MemoryStore | None:
    if not MEMORY_ENABLED:
        return None
    try:
        return get_default_memory_store()
    except Exception as exc:
        print(f"TALOS memory unavailable: {_truncate_text(str(exc), 300)}")
        return None


def _get_prompt_memory(
    memory_store: MemoryStore | None,
    session_id: str,
    command: str,
    *,
    include_session_summary: bool = True,
) -> str | None:
    if memory_store is None or PROMPT_MEMORY_CHAR_LIMIT <= 0:
        return None
    try:
        return memory_store.get_prompt_memory(
            session_id,
            command,
            max_chars=PROMPT_MEMORY_CHAR_LIMIT,
            include_session_summary=include_session_summary,
        )
    except Exception as exc:
        print(f"TALOS memory retrieval failed: {_truncate_text(str(exc), 300)}")
        return None


def _get_conversation_history(
    memory_store: MemoryStore | None,
    session_id: str,
) -> list[dict[str, str]]:
    if (
        memory_store is None
        or CONVERSATION_HISTORY_MESSAGE_LIMIT <= 0
        or CONVERSATION_HISTORY_CHAR_LIMIT <= 0
    ):
        return []
    try:
        return memory_store.get_recent_messages(
            session_id,
            message_limit=CONVERSATION_HISTORY_MESSAGE_LIMIT,
            max_chars=CONVERSATION_HISTORY_CHAR_LIMIT,
        )
    except Exception as exc:
        print(f"TALOS conversation retrieval failed: {_truncate_text(str(exc), 300)}")
        return []


def _record_memory_turn(
    memory_store: MemoryStore | None,
    session_id: str,
    command: str,
    response_text: str,
    *,
    interaction_mode: str,
) -> None:
    if memory_store is None:
        return
    try:
        memory_store.record_turn(
            session_id,
            command,
            response_text,
            metadata={"interaction_mode": interaction_mode},
        )
    except Exception as exc:
        print(f"TALOS memory write failed: {_truncate_text(str(exc), 300)}")


def _format_context(snapshot: str) -> str | None:
    if not snapshot or snapshot == "no recent status":
        return None
    snapshot = " ".join(str(snapshot).split())
    if len(snapshot) > 500:
        snapshot = snapshot[:500].rsplit(" ", 1)[0] + "..."
    return f"Context (read-only): {snapshot}"


def classify_request_route(
    command: str,
    *,
    source: str,
    session_id: str,
    runtime_context: str,
) -> dict[str, str]:
    instructions = (
        "You are TALOS's request router. Decide how the next user turn should be handled. "
        "Return only compact JSON with keys mode, reason, and response. Valid modes: foreground, background, status. "
        "Use foreground for ordinary chat, definitions, explanations, and questions about TALOS behavior. "
        "Use background only when the user is asking TALOS to perform long-running work that can continue after an acknowledgement. "
        "Use status only when the user is asking about active, completed, failed, or specific background jobs. "
        "Do not route based on keywords alone; infer the user's intent from the whole request and runtime context. "
        "For background mode, response should be a short natural acknowledgement to show immediately. "
        "For foreground or status mode, response should be an empty string because the main agent will answer."
    )
    input_items = [
        {
            "role": "system",
            "content": (
                f"Source: {source}\n"
                f"Session ID: {session_id}\n"
                f"Runtime context:\n{runtime_context or 'No recent runtime context.'}"
            ),
        },
        {"role": "user", "content": command},
    ]
    response = _responses_create_with_retry(
        model=ROUTER_MODEL,
        instructions=instructions,
        input=input_items,
        temperature=0,
        max_output_tokens=120,
    )
    payload = _parse_router_payload(str(response.output_text or ""))
    mode = payload.get("mode", "foreground").strip().lower()
    if mode not in {"foreground", "background", "status"}:
        mode = "foreground"
    reason = payload.get("reason", "model route decision").strip() or "model route decision"
    response_text = payload.get("response", "").strip()
    return {
        "mode": mode,
        "reason": _truncate_text(reason, 240),
        "response": _truncate_text(response_text, 240),
    }


def _parse_router_payload(text: str) -> dict[str, str]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _tokenize_lowered(text: str) -> set[str]:
    normalized = []
    for char in text.lower():
        normalized.append(char if char.isalnum() else " ")
    return {token for token in "".join(normalized).split() if token}


def _is_kicad_request(command: str, tool_defs: list[dict[str, Any]]) -> bool:
    lowered = command.lower()
    if KICAD_TOOL_PREFIX in lowered or "kicad" in lowered:
        return True
    if not any(str(tool.get("name") or "").startswith(KICAD_TOOL_PREFIX) for tool in tool_defs):
        return False
    return bool(_tokenize_lowered(command) & KICAD_RELEVANT_TERMS)


def _has_minecraft_tools(tool_defs: list[dict[str, Any]]) -> bool:
    prefixes = (MINECRAFT_TOOL_PREFIX, MINECRAFT_FILESYSTEM_TOOL_PREFIX)
    return any(
        str(tool.get("name") or "").startswith(prefix)
        for prefix in prefixes
        for tool in tool_defs
    )


def _is_minecraft_request(command: str, tool_defs: list[dict[str, Any]]) -> bool:
    lowered = command.lower()
    if MINECRAFT_TOOL_PREFIX in lowered or MINECRAFT_FILESYSTEM_TOOL_PREFIX in lowered:
        return True
    if "minecraft" in lowered or "forge" in lowered or "modpack" in lowered or "kubejs" in lowered:
        return True
    if not _has_minecraft_tools(tool_defs):
        return False
    if bool(_tokenize_lowered(command) & MINECRAFT_RELEVANT_TERMS):
        return True
    return any(hint in lowered for hint in MINECRAFT_PATH_HINTS)


def _is_phone_request(command: str, tool_defs: list[dict[str, Any]]) -> bool:
    lowered = command.lower()
    if "place_phone_call" in lowered:
        return True
    if not any(str(tool.get("name") or "") == "place_phone_call" for tool in tool_defs):
        return False
    return bool(_tokenize_lowered(command) & PHONE_RELEVANT_TERMS)


def _format_kicad_backend_context(raw_output: str, command: str) -> str | None:
    stripped = raw_output.strip()
    if not stripped:
        return None

    try:
        payload = json.loads(stripped)
    except Exception:
        return f"KiCad backend preflight (fresh): {_truncate_text(' '.join(stripped.split()), 500)}"

    if not isinstance(payload, dict):
        return None

    status = {
        "backend": payload.get("backend"),
        "realtime": payload.get("realtime_sync", payload.get("realtime")),
        "ipc_connected": payload.get("ipc_connected", payload.get("ipcConnected")),
        "loaded_project": payload.get("loadedProject"),
        "loaded_board": payload.get("loadedBoard"),
        "project_path": payload.get("projectPath"),
        "board_path": payload.get("boardPath"),
        "dirty": payload.get("dirty"),
        "message": payload.get("message"),
    }
    compact_status = json.dumps(status, ensure_ascii=True)

    notes: list[str] = []
    tokens = _tokenize_lowered(command)

    if status["backend"] == "swig":
        notes.append(
            "KiCad is currently in SWIG mode, so board changes are file-based and not guaranteed to appear live in the UI."
        )
    if status["ipc_connected"] is False:
        notes.append(
            "For real-time board placement, KiCad must be running with IPC enabled and a board open."
        )
    if tokens & KICAD_BOARD_EDIT_TERMS and status["loaded_board"] is False:
        notes.append("No PCB board is currently loaded, so board-editing commands may fail or target only files on disk.")
    if tokens & KICAD_SCHEMATIC_TO_BOARD_TERMS:
        notes.append(
            "If the task starts from schematic components, sync the schematic to the board before placement or routing."
        )
    if KICAD_UI_CHECK_TOOL and status["backend"] == "swig":
        notes.append(
            "If the user expects visual updates, prefer checking or launching KiCad UI before claiming the placement is visible."
        )

    if notes:
        return f"KiCad backend preflight (fresh): {compact_status} Guidance: {' '.join(notes)}"
    return f"KiCad backend preflight (fresh): {compact_status}"


def _find_kicad_provider_status(mcp_client: Any) -> dict[str, Any] | None:
    """Return the KiCad provider's lifecycle status, if it is configured."""

    try:
        servers = mcp_client.list_server_status()
    except Exception:
        return None
    for server in servers:
        name = str(server.get("name") or "").lower()
        if "kicad" in name:
            return server
    return None


def _maybe_add_kicad_status_note(
    input_items: list[dict[str, Any]],
    mcp_client: Any,
    tool_defs: list[dict[str, Any]],
    command: str,
) -> None:
    """Tell the model when a KiCad request lands while KiCad is not yet ready.

    Heavyweight KiCad tools are exposed only once the provider is ready, so a
    cold/warming KiCad would otherwise look like missing tools. Surface the real
    provider phase and, for lazy/autostart providers, kick off a background
    warmup so the turn is acknowledged instead of frozen.
    """

    if not _is_kicad_request(command, tool_defs):
        return

    status = _find_kicad_provider_status(mcp_client)
    if status is None or status.get("ready") or status.get("status") == "healthy":
        return

    server_name = str(status.get("name") or "kicad")
    mode = str(status.get("mode") or "")
    phase = status.get("status")

    # Lazy/autostart providers should begin warming the moment a request needs
    # them, without blocking this turn.
    if mode in {"lazy", "sidecar_autostart"} and phase not in {"warming", "starting", "reconnecting"}:
        try:
            refreshed = mcp_client.start_server(server_name, background=True)
            for server in refreshed:
                if str(server.get("name") or "") == server_name:
                    status = server
                    break
        except Exception as exc:
            print(f"KiCad background start failed: {_truncate_text(str(exc), 200)}")

    detail = str(status.get("detail") or status.get("status") or "not ready")
    input_items.append(
        {
            "role": "system",
            "content": (
                f"KiCad provider '{server_name}' is not ready ({detail}); its KiCad tools are not "
                "exposed yet. Do not wait or hang the turn. Briefly tell the user KiCad is warming up "
                "in the background and that you will be able to run KiCad steps once it is ready. "
                "Use list_mcp_server_status to check readiness, or start_mcp_server to (re)start it. "
                "File-based schematic edits that do not require the live KiCad backend can still proceed "
                "with any available file tools."
            ),
        }
    )


def _maybe_add_kicad_preflight(
    input_items: list[dict[str, Any]],
    mcp_client: Any,
    tool_defs: list[dict[str, Any]],
    command: str,
) -> None:
    if not _is_kicad_request(command, tool_defs):
        return

    tool_names = {str(tool.get("name") or "") for tool in tool_defs}
    if KICAD_BACKEND_STATE_TOOL not in tool_names:
        # KiCad is not ready (its tools are not exposed); report the provider
        # phase instead of silently behaving as if KiCad were unavailable.
        _maybe_add_kicad_status_note(input_items, mcp_client, tool_defs, command)
        return

    try:
        raw_state = mcp_client.call_tool(KICAD_BACKEND_STATE_TOOL, {})
    except Exception as exc:
        input_items.append(
            {
                "role": "system",
                "content": f"KiCad backend preflight failed: {_truncate_text(str(exc), 300)}",
            }
        )
        return

    preflight_context = _format_kicad_backend_context(raw_state, command)
    if preflight_context:
        input_items.append({"role": "system", "content": preflight_context})


def _maybe_add_minecraft_context(
    input_items: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]],
    command: str,
) -> None:
    if not _is_minecraft_request(command, tool_defs):
        return

    if not MINECRAFT_SERVER_DIR:
        input_items.append(
            {
                "role": "system",
                "content": (
                    "Minecraft/Forge diagnostics were requested, but MINECRAFT_SERVER_DIR is not configured. "
                    "Do not assume the server filesystem is available; tell the user to configure that root first."
                ),
            }
        )
        return

    input_items.append(
        {
            "role": "system",
            "content": (
                f"Minecraft server diagnostics root: {MINECRAFT_SERVER_DIR}. Keep filesystem and search work "
                "inside that directory. Start shallow, prioritize recent logs and crash reports, avoid binaries "
                "or huge world data unless needed, and correlate failures to mod IDs, configs, datapacks, "
                "scripts, or jar filenames before proposing changes."
            ),
        }
    )


def _parse_function_arguments(arguments: Any) -> dict[str, Any]:
    return parse_tool_arguments(arguments)


def _truncate_text(value: str, limit: int = TOOL_OUTPUT_CHAR_LIMIT) -> str:
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def _summarize_jsonish_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        if isinstance(value, dict):
            return f"<object with {len(value)} keys>"
        if isinstance(value, list):
            return f"<list with {len(value)} items>"
        return value

    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        preferred_keys = (
            "success",
            "message",
            "error",
            "errorDetails",
            "name",
            "path",
            "project",
            "board",
            "component",
            "components",
            "net",
            "nets",
            "warnings",
        )
        for key in preferred_keys:
            if key not in value:
                continue
            item = value[key]
            if isinstance(item, (dict, list)):
                summary[key] = _summarize_jsonish_value(item, depth=depth + 1)
            else:
                summary[key] = item

        remaining_keys = [key for key in value.keys() if key not in summary]
        for key in remaining_keys[:TOOL_OUTPUT_SUMMARY_PREVIEW_KEYS]:
            item = value[key]
            if isinstance(item, (str, int, float, bool)) or item is None:
                summary[key] = item
            elif isinstance(item, dict):
                summary[key] = f"<object with {len(item)} keys>"
            elif isinstance(item, list):
                summary[key] = f"<list with {len(item)} items>"
            else:
                summary[key] = f"<{type(item).__name__}>"

        if len(remaining_keys) > TOOL_OUTPUT_SUMMARY_PREVIEW_KEYS:
            summary["_remaining_keys"] = len(remaining_keys) - TOOL_OUTPUT_SUMMARY_PREVIEW_KEYS
        return summary

    if isinstance(value, list):
        preview = [
            _summarize_jsonish_value(item, depth=depth + 1)
            for item in value[:TOOL_OUTPUT_SUMMARY_PREVIEW_ITEMS]
        ]
        summary = {
            "item_count": len(value),
            "preview": preview,
        }
        if len(value) > TOOL_OUTPUT_SUMMARY_PREVIEW_ITEMS:
            summary["remaining_items"] = len(value) - TOOL_OUTPUT_SUMMARY_PREVIEW_ITEMS
        return summary

    if isinstance(value, str):
        return _truncate_text(" ".join(value.split()))
    return value


def _summarize_tool_result(name: str, raw_output: str) -> str | None:
    stripped = raw_output.strip()
    if not stripped:
        return raw_output

    try:
        parsed = json.loads(stripped)
    except Exception:
        condensed = " ".join(raw_output.split())
        if len(condensed) >= len(raw_output) or len(raw_output) > TOOL_OUTPUT_CHAR_LIMIT:
            return condensed
        return None

    summary_payload = {
        "tool": name,
        "summary": _summarize_jsonish_value(parsed),
    }
    return json.dumps(summary_payload, ensure_ascii=True)


def _shape_tool_output(name: str, result: Any) -> str:
    raw_output = str(result)
    shaped_output = raw_output
    used_summary = False

    if name == "list_mcp_tools":
        if len(raw_output) > TOOL_OUTPUT_CHAR_LIMIT:
            print(
                f"Tool output preserved for {name}: raw={len(raw_output)} chars, "
                "summary=False, truncated=False"
            )
        return shaped_output

    if TOOL_OUTPUT_SUMMARY_ENABLED:
        summary_output = _summarize_tool_result(name, raw_output)
        if summary_output is not None and len(summary_output) < len(raw_output):
            shaped_output = summary_output
            used_summary = True

    truncated = False
    if len(shaped_output) > TOOL_OUTPUT_CHAR_LIMIT:
        shaped_output = _truncate_text(shaped_output)
        truncated = True

    if used_summary or truncated or len(raw_output) > TOOL_OUTPUT_CHAR_LIMIT:
        print(
            f"Tool output shaped for {name}: raw={len(raw_output)} chars, "
            f"sent={len(shaped_output)} chars, summary={used_summary}, truncated={truncated}"
        )

    return shaped_output


def _tool_result_indicates_failure(raw_result: str) -> bool:
    stripped = raw_result.strip()
    if not stripped:
        return False

    lowered = stripped.lower()
    if lowered.startswith("error calling ") or lowered.startswith("unknown function:"):
        return True
    if lowered.startswith("failed ") or lowered.startswith("failed to "):
        return True

    try:
        parsed = json.loads(stripped)
    except Exception:
        return False

    if isinstance(parsed, dict):
        success = parsed.get("success")
        if success is False:
            return True
        if success is True:
            return False
        if parsed.get("error") or parsed.get("errorDetails"):
            return True
    return False


def _fallback_response_from_tool_error(
    command: str,
    tool_events: list[dict[str, Any]],
    exc: Exception,
) -> str:
    if not tool_events:
        return (
            "The model backend failed after the tool call, and I do not have a usable KiCad result to report."
        )

    last_event = tool_events[-1]
    tool_name = last_event.get("name", "the KiCad tool")
    raw_result = " ".join(last_event.get("raw_result", "").split())
    raw_result = _truncate_text(raw_result, 500)
    tool_failed = bool(last_event.get("failed"))

    if command and _is_kicad_request(command, [{"name": tool_name}]):
        if tool_failed:
            return (
                f"The KiCad step failed in {tool_name}: {raw_result} "
                "The follow-up call to OpenAI then hit a server-side 500, so I am surfacing the real tool failure instead. "
                "Please retry with a valid KiCad symbol or path."
            )
        return (
            f"The KiCad step completed in {tool_name}: {raw_result} "
            "The follow-up call to OpenAI then hit a server-side 500 before the agent could continue. "
            "Please retry the request from the current project state."
        )

    if tool_failed:
        return (
            f"The last tool call failed in {tool_name}: {raw_result} "
            f"The follow-up model call then hit a server-side error: {_truncate_text(str(exc), 200)}"
        )
    return (
        f"The last tool call completed in {tool_name}: {raw_result} "
        f"The follow-up model call then hit a server-side error: {_truncate_text(str(exc), 200)}"
    )


def _summarize_recovery_events(tool_events: list[dict[str, Any]], limit: int = 6) -> str:
    if not tool_events:
        return "No prior tool calls were recorded."

    selected = tool_events[-limit:]
    parts: list[str] = []
    for event in selected:
        status = "failed" if event.get("failed") else "completed"
        name = str(event.get("name") or "unknown_tool")
        raw_result = " ".join(str(event.get("raw_result") or "").split())
        raw_result = _truncate_text(raw_result, 240)
        parts.append(f"{name} {status}: {raw_result}")
    return " | ".join(parts)


def _build_recovery_input_items(
    command: str,
    state_snapshot: str,
    mcp_client: Any,
    tool_defs: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    recovery_attempt: int,
) -> list[dict[str, Any]]:
    input_items: list[dict[str, Any]] = []

    context_message = _format_context(state_snapshot)
    if context_message:
        input_items.append({"role": "system", "content": context_message})

    recovery_summary = _summarize_recovery_events(tool_events)
    input_items.append(
        {
            "role": "system",
            "content": (
                "A previous OpenAI follow-up call failed with a transient server-side error "
                f"after tool execution. Recovery attempt {recovery_attempt}. "
                "Continue the same user request from the current tool state. "
                "Do not repeat already-successful project creation or placement steps unless the current backend state shows they did not persist. "
                f"Recent tool activity: {recovery_summary}"
            ),
        }
    )
    _maybe_add_kicad_preflight(input_items, mcp_client, tool_defs, command)
    input_items.append({"role": "user", "content": command})
    return input_items


def _memory_scope_for_tool(scope: str, session_id: str) -> str:
    normalized_scope = str(scope or "user").strip().lower()
    if normalized_scope == "session":
        return f"session:{session_id}"
    if normalized_scope in {"user", "project", "global"}:
        return normalized_scope
    return normalized_scope or "user"


def _invoke_host_tool(
    mcp_client: Any,
    name: str,
    arguments: Any,
    *,
    session_id: str = "default",
    runtime_lane: str = "foreground",
) -> str:
    parsed_arguments = _parse_function_arguments(arguments)
    if name == "list_mcp_resources":
        return json.dumps({"resources": mcp_client.list_resources(refresh=bool(parsed_arguments.get("refresh")))})
    if name == "list_mcp_resource_templates":
        return json.dumps(
            {
                "resourceTemplates": mcp_client.list_resource_templates(
                    refresh=bool(parsed_arguments.get("refresh"))
                )
            }
        )
    if name == "read_mcp_resource":
        uri = str(parsed_arguments.get("uri") or "").strip()
        if not uri:
            raise ValueError("read_mcp_resource requires a non-empty 'uri'.")
        server = parsed_arguments.get("server")
        server_name = str(server).strip() if server not in (None, "") else None
        return mcp_client.read_resource(uri, server=server_name)
    if name == "list_mcp_server_status":
        return json.dumps({"servers": mcp_client.list_server_status()})
    if name == "list_mcp_tools":
        return json.dumps(
            mcp_client.list_tool_inventory(refresh=bool(parsed_arguments.get("refresh")))
        )
    if name == "retry_mcp_server":
        server = parsed_arguments.get("server")
        server_name = str(server).strip() if server not in (None, "") else None
        return json.dumps({"servers": mcp_client.retry_server(server_name)})
    if name == "start_mcp_server":
        server_name = str(parsed_arguments.get("server") or "").strip()
        if not server_name:
            raise ValueError("start_mcp_server requires a non-empty 'server'.")
        wait = bool(parsed_arguments.get("wait"))
        try:
            servers = mcp_client.start_server(server_name, background=not wait)
        except KeyError:
            return json.dumps(
                {"success": False, "message": f"Unknown MCP server '{server_name}'."}
            )
        return json.dumps({"success": True, "servers": servers})
    if name == "remember_memory_fact":
        memory_store = _get_memory_store()
        if memory_store is None:
            return json.dumps({"success": False, "message": "TALOS memory is disabled or unavailable."})

        scope = _memory_scope_for_tool(str(parsed_arguments.get("scope") or "user"), session_id)
        key = str(parsed_arguments.get("key") or "").strip()
        value = str(parsed_arguments.get("value") or "").strip()
        salience = int(parsed_arguments.get("salience") or 5)
        memory_store.upsert_fact(
            scope,
            key,
            value,
            salience=salience,
            source_session_id=session_id,
        )
        # Phase 6: mirror the explicit user fact into the awareness
        # subsystem's validated long-term memory (supersession-aware,
        # provenance-backed). The SQLite store above stays authoritative for
        # prompt assembly; failures here are reported, not hidden.
        awareness_synced = False
        try:
            from talos.services import awareness_client

            awareness_client.post_json(
                "/memory/deterministic",
                {
                    "statement": f"{key}: {value}",
                    "scope": scope,
                    "structured_content": {"key": key, "value": value},
                    "evidence": [
                        {
                            "kind": "user_confirmation",
                            "reference": f"session:{session_id}",
                            "metadata": {"tool": "remember_memory_fact"},
                        }
                    ],
                    "importance": min(1.0, max(0.0, salience / 10.0)),
                },
            )
            awareness_synced = True
        except Exception:
            awareness_synced = False
        return json.dumps(
            {
                "success": True,
                "scope": scope,
                "key": key,
                "awareness_memory_synced": awareness_synced,
                "message": "Memory fact stored.",
            }
        )
    if name == "list_memory_facts":
        memory_store = _get_memory_store()
        if memory_store is None:
            return json.dumps({"success": False, "facts": []})

        query = str(parsed_arguments.get("query") or "")
        limit = int(parsed_arguments.get("limit") or 8)
        facts = memory_store.search_facts(query, limit=limit)
        return json.dumps(
            {
                "success": True,
                "facts": [
                    {
                        "scope": fact.scope,
                        "key": fact.key,
                        "value": fact.value,
                        "salience": fact.salience,
                        "updated_at": fact.updated_at,
                    }
                    for fact in facts
                ],
            }
        )
    if name == "place_phone_call":
        from talos.phone import place_phone_call

        return json.dumps(
            place_phone_call(
                str(parsed_arguments.get("contact_or_number") or ""),
                purpose=str(parsed_arguments.get("purpose") or ""),
                brief_context=str(parsed_arguments.get("brief_context") or ""),
                message_to_deliver=str(parsed_arguments.get("message_to_deliver") or ""),
                session_id=session_id,
                runtime_lane=runtime_lane,
            )
        )
    if name == "phone_call_status":
        from talos.phone import phone_call_status

        return json.dumps(
            phone_call_status(
                str(parsed_arguments.get("call_id") or ""),
                refresh=bool(parsed_arguments.get("refresh", True)),
            )
        )
    if name == "recent_phone_calls":
        from talos.phone import recent_phone_calls

        return json.dumps(
            recent_phone_calls(
                limit=int(parsed_arguments.get("limit") or 10),
                refresh=bool(parsed_arguments.get("refresh", True)),
            )
        )
    if name == "summarize_phone_call":
        from talos.phone import summarize_phone_call

        return json.dumps(
            summarize_phone_call(
                str(parsed_arguments.get("call_id") or ""),
                refresh=bool(parsed_arguments.get("refresh", True)),
            )
        )
    raise KeyError(name)


def _collect_tool_outputs(
    response: Any,
    mcp_client: Any,
    *,
    session_id: str = "default",
    runtime_lane: str = "foreground",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tool_outputs = []
    tool_events: list[dict[str, Any]] = []
    for item in response.output:
        if item.type != "function_call":
            continue

        print(f"FUNCTION CALL DETECTED: {item.name} with args {item.arguments}")
        try:
            if item.name in HOST_TOOL_NAMES:
                result = _invoke_host_tool(
                    mcp_client,
                    item.name,
                    item.arguments,
                    session_id=session_id,
                    runtime_lane=runtime_lane,
                )
            else:
                result = mcp_client.call_tool(item.name, item.arguments)
        except KeyError:
            result = f"Unknown function: {item.name}"
        except Exception as exc:
            result = f"Error calling {item.name}: {exc}"

        shaped_output = _shape_tool_output(item.name, result)
        raw_result = str(result)
        tool_outputs.append(
            {
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": shaped_output,
            }
        )
        tool_events.append(
            {
                "name": item.name,
                "raw_result": raw_result,
                "output": shaped_output,
                "failed": _tool_result_indicates_failure(raw_result),
            }
        )
        print(f"Function '{item.name}' executed with result: {_truncate_text(raw_result, 600)}")
    return tool_outputs, tool_events


def run_command(
    command: str,
    state_snapshot: str = "no recent status",
    *,
    session_id: str = "default",
    interaction_mode: str | None = None,
    runtime_lane: str = "foreground",
    extra_context: str | None = None,
    benchmark: Any = None,
) -> str:
    if benchmark:
        benchmark.set_command(command)

    mcp_client = get_local_mcp_client()
    tool_defs = _build_tool_definitions(mcp_client)
    mode = _normalize_interaction_mode(interaction_mode or _infer_interaction_mode(session_id))
    memory_store = _get_memory_store()
    if memory_store is not None:
        try:
            memory_store.record_session(session_id, metadata={"interaction_mode": mode})
        except Exception as exc:
            print(f"TALOS memory session write failed: {_truncate_text(str(exc), 300)}")
            memory_store = None
    memory_block = _get_prompt_memory(memory_store, session_id, command)
    instructions = _build_prompt_instructions(
        command,
        session_id,
        tool_defs,
        memory_block=memory_block,
        interaction_mode=mode,
        extra_context=extra_context,
    )

    input_items: list[dict[str, Any]] = []
    context_message = _format_context(state_snapshot)
    if context_message:
        input_items.append({"role": "system", "content": context_message})
    _maybe_add_kicad_preflight(input_items, mcp_client, tool_defs, command)
    _maybe_add_minecraft_context(input_items, tool_defs, command)
    input_items.append({"role": "user", "content": command})

    try:
        thread_key = _response_thread_key(session_id, runtime_lane)
        with _get_conversation_lock(thread_key):
            request_kwargs: dict[str, Any] = {
                "model": ai_model,
                "instructions": instructions,
                "tools": tool_defs,
                "input": input_items,
                "temperature": 0.5,
                "max_output_tokens": AGENT_MAX_OUTPUT_TOKENS,
            }
            previous_response_id = _get_last_response_id(thread_key)
            if previous_response_id:
                request_kwargs["previous_response_id"] = previous_response_id

            if benchmark:
                benchmark.mark_stage("llm_send")
            response = _responses_create_with_retry(**request_kwargs)
            if benchmark:
                benchmark.mark_stage("llm_first_done")

            response_text = ""
            rounds = 0
            tool_events: list[dict[str, Any]] = []
            recovery_attempts = 0
            while True:
                tool_outputs, recent_events = _collect_tool_outputs(
                    response,
                    mcp_client,
                    session_id=session_id,
                    runtime_lane=runtime_lane,
                )
                if recent_events:
                    tool_events.extend(recent_events)
                if not tool_outputs:
                    response_text = (response.output_text or "").strip()
                    _set_last_response_id(thread_key, response.id)
                    break

                if rounds >= MAX_TOOL_CALL_ROUNDS:
                    response_text = "I reached the tool-call limit before finishing that request."
                    _set_last_response_id(thread_key, response.id)
                    break

                if benchmark:
                    benchmark.mark_stage("llm_followup_send")
                try:
                    response = _responses_create_with_retry(
                        model=ai_model,
                        instructions=instructions,
                        tools=tool_defs,
                        input=tool_outputs,
                        previous_response_id=response.id,
                        temperature=0.5,
                        max_output_tokens=AGENT_MAX_OUTPUT_TOKENS,
                    )
                except Exception as exc:
                    if not _is_server_error(exc):
                        raise
                    if recovery_attempts < OPENAI_SERVER_ERROR_RECOVERY_ATTEMPTS and tool_events:
                        recovery_attempts += 1
                        print(
                            "OpenAI follow-up failed after tool execution; "
                            f"attempting recovery {recovery_attempts}/"
                            f"{OPENAI_SERVER_ERROR_RECOVERY_ATTEMPTS}."
                        )
                        recovery_input_items = _build_recovery_input_items(
                            command,
                            state_snapshot,
                            mcp_client,
                            tool_defs,
                            tool_events,
                            recovery_attempts,
                        )
                        response = _responses_create_with_retry(
                            model=ai_model,
                            instructions=instructions,
                            tools=tool_defs,
                            input=recovery_input_items,
                            temperature=0.5,
                            max_output_tokens=AGENT_MAX_OUTPUT_TOKENS,
                        )
                        continue

                    response_text = _fallback_response_from_tool_error(command, tool_events, exc)
                    _clear_response_id(thread_key)
                    break
                rounds += 1

            if benchmark:
                benchmark.mark_stage("llm_done")
    except Exception as exc:
        if benchmark:
            benchmark.add_error(f"Agent runtime error: {exc}")
            benchmark.emit_summary_once("agent_runtime_error")
        raise

    response_text = response_text.replace("Monkey Butler:", "").strip()
    _record_memory_turn(
        memory_store,
        session_id,
        command,
        response_text,
        interaction_mode=mode,
    )
    if benchmark:
        benchmark.set_response_text(response_text)
    return response_text


def _get_stream_backend():
    """Build the configured streaming backend through the shared factory.

    The factory owns provider selection and local endpoint defaults. In the
    Ollama profile this never reads ``OPENAI_API_KEY`` or falls back to a hosted
    model when local configuration is incomplete.
    """
    from talos.voice.backends.factory import get_llm_backend

    return get_llm_backend()


def _execute_chat_tool_calls(
    tool_calls: tuple[LLMToolCall, ...],
    mcp_client: Any,
    *,
    session_id: str,
    runtime_lane: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute streamed tool calls and return (tool result messages, events).

    Reuses the exact host/MCP dispatch as the non-streaming path so tools behave
    identically regardless of which LLM API produced the call.
    """
    messages: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for call in tool_calls:
        print(f"FUNCTION CALL DETECTED (stream): {call.name} with args {call.arguments}")
        try:
            if call.name in HOST_TOOL_NAMES:
                result = _invoke_host_tool(
                    mcp_client,
                    call.name,
                    call.arguments,
                    session_id=session_id,
                    runtime_lane=runtime_lane,
                )
            else:
                result = mcp_client.call_tool(call.name, call.arguments)
        except KeyError:
            result = f"Unknown function: {call.name}"
        except Exception as exc:  # noqa: BLE001 - surfaced back to the model
            result = f"Error calling {call.name}: {exc}"

        shaped_output = _shape_tool_output(call.name, result)
        messages.append(chat_messages_to_tool_result(call, shaped_output))
        events.append(
            {
                "name": call.name,
                "raw_result": str(result),
                "output": shaped_output,
                "failed": _tool_result_indicates_failure(str(result)),
            }
        )
    return messages, events


def run_command_stream(
    command: str,
    state_snapshot: str = "no recent status",
    *,
    session_id: str = "default",
    interaction_mode: str | None = None,
    runtime_lane: str = "foreground",
    extra_context: str | None = None,
):
    """Streaming variant of :func:`run_command`.

    Yields assistant text fragments as they are generated so the caller can begin
    speaking on the first sentence. Tool-call rounds are executed transparently;
    only the final assistant turn produces spoken text (tool-calling turns emit no
    content). Reuses the same prompt, tool, and memory machinery as
    :func:`run_command`.

    Chat Completions does not provide the Responses API's
    ``previous_response_id`` threading. Cross-turn continuity therefore comes
    from bounded, persisted user/assistant messages in the conversation store.
    The per-session lock is acquired before those messages are read so a queued
    voice turn observes the turn that completed immediately before it. Within
    a single request the tool loop keeps full message history.
    """
    thread_key = _response_thread_key(session_id, runtime_lane)

    with _get_conversation_lock(thread_key):
        mcp_client = get_local_mcp_client()
        tool_defs = _build_tool_definitions(mcp_client)
        mode = _normalize_interaction_mode(
            interaction_mode or _infer_interaction_mode(session_id)
        )
        memory_store = _get_memory_store()
        if memory_store is not None:
            try:
                memory_store.record_session(
                    session_id,
                    metadata={"interaction_mode": mode},
                )
            except Exception as exc:
                print(f"TALOS memory session write failed: {_truncate_text(str(exc), 300)}")
                memory_store = None
        memory_block = _get_prompt_memory(
            memory_store,
            session_id,
            command,
            include_session_summary=False,
        )
        conversation_history = _get_conversation_history(memory_store, session_id)
        instructions = _build_prompt_instructions(
            command,
            session_id,
            tool_defs,
            memory_block=memory_block,
            interaction_mode=mode,
            extra_context=extra_context,
        )

        messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
        context_message = _format_context(state_snapshot)
        if context_message:
            messages.append({"role": "system", "content": context_message})
        _maybe_add_kicad_preflight(messages, mcp_client, tool_defs, command)
        _maybe_add_minecraft_context(messages, tool_defs, command)
        messages.extend(conversation_history)
        # Dynamic reasoning control: suppress the Qwen3 <think> block on quick
        # commands (the dominant local-model latency cost) and keep it for
        # analytical requests. Only the outgoing turn is decorated; the original
        # command is what gets persisted below, so stored history stays clean.
        user_content = command + thinking_suffix(command, runtime_lane=runtime_lane)
        messages.append({"role": "user", "content": user_content})

        backend = _get_stream_backend()
        full_text_parts: list[str] = []
        rounds = 0
        while True:
            turn_text_parts: list[str] = []
            completion: LLMCompletion | None = None
            for event in backend.stream(messages, tools=tool_defs):
                if isinstance(event, LLMTextDelta):
                    if event.text:
                        turn_text_parts.append(event.text)
                        full_text_parts.append(event.text)
                        yield event.text
                elif isinstance(event, LLMCompletion):
                    completion = event
            if completion is None:
                break

            if not completion.wants_tools:
                break

            if rounds >= MAX_TOOL_CALL_ROUNDS:
                limit_note = " I reached the tool-call limit before finishing that request."
                full_text_parts.append(limit_note)
                yield limit_note
                break

            messages.append(
                tool_calls_to_assistant_message("".join(turn_text_parts), completion.tool_calls)
            )
            tool_messages, _events = _execute_chat_tool_calls(
                completion.tool_calls,
                mcp_client,
                session_id=session_id,
                runtime_lane=runtime_lane,
            )
            messages.extend(tool_messages)
            rounds += 1

        response_text = "".join(full_text_parts).replace("Monkey Butler:", "").strip()
        _record_memory_turn(
            memory_store,
            session_id,
            command,
            response_text,
            interaction_mode=mode,
        )


def reset_session(session_id: str) -> None:
    _clear_session_response_ids(session_id)
    memory_store = _get_memory_store()
    if memory_store is None:
        return
    try:
        memory_store.clear_session(session_id)
    except Exception as exc:
        print(f"TALOS memory session reset failed: {_truncate_text(str(exc), 300)}")


def shutdown() -> None:
    shutdown_local_mcp_client()
