from __future__ import annotations

import json
import re
from typing import Any


def parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if arguments in (None, ""):
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        return _parse_tool_arguments_json(arguments)
    raise TypeError("Tool arguments must be a dict, JSON string, or None.")


def _parse_tool_arguments_json(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        repaired = _repair_json_object(arguments)
        if repaired == arguments:
            raise ValueError(f"Tool arguments must decode to a JSON object: {exc}") from exc
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError as repaired_exc:
            raise ValueError(
                "Tool arguments must decode to a JSON object. "
                f"Original parse error: {exc}. Repaired parse error: {repaired_exc}."
            ) from repaired_exc

    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must decode to a JSON object.")
    return parsed


def _repair_json_object(raw: str) -> str:
    text = str(raw or "")
    stripped = text.strip()
    if not stripped.startswith("{"):
        return text

    repaired_chars: list[str] = []
    stack: list[str] = []
    in_string = False
    escaping = False

    for char in text:
        if in_string:
            if escaping:
                repaired_chars.append(char)
                escaping = False
                continue
            if char == "\\":
                repaired_chars.append(char)
                escaping = True
                continue
            if char == '"':
                repaired_chars.append(char)
                in_string = False
                continue
            if char == "\n":
                repaired_chars.append("\\n")
                continue
            if char == "\r":
                repaired_chars.append("\\r")
                continue
            if char == "\t":
                repaired_chars.append("\\t")
                continue
            repaired_chars.append(char)
            continue

        if char == '"':
            repaired_chars.append(char)
            in_string = True
            continue
        if char == "{":
            stack.append("}")
            repaired_chars.append(char)
            continue
        if char == "[":
            stack.append("]")
            repaired_chars.append(char)
            continue
        if char in {"}", "]"}:
            if stack and stack[-1] == char:
                stack.pop()
            repaired_chars.append(char)
            continue
        repaired_chars.append(char)

    repaired = "".join(repaired_chars)
    if in_string:
        repaired += '"'
    if stack:
        repaired += "".join(reversed(stack))

    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired
