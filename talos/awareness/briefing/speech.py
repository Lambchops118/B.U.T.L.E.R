"""Short deterministic speech from stored facts, separate from diagnostic text."""

import math
import re

VERSION = "briefing-speech-v1"
EVENT_SPEECH = {
    "agent.job.completed": "A background job completed earlier.",
    "agent.job.failed": "A background job failed earlier.",
    "agent.tool.failed": "A tool call failed earlier.",
    "person.interaction.started": "An earlier interaction with TALOS started.",
    "person.interaction.ended": "An earlier interaction with TALOS ended.",
}


def label(value, fallback="device"):
    value = str(value or "").replace("_", " ").strip()
    if len(value) > 60 or not re.fullmatch(r"[\w .'-]+", value):
        return fallback
    return {"owner": "your presence", "talos": "TALOS"}.get(value, value)


def short_text(value, fallback):
    text = " ".join(str(value or "").split())
    # Error objects, stack traces, source code and long logs are not speech.
    if not text or len(text) > 180 or any(x in text for x in ("{", "}", "[", "]", "Traceback", "source=", "http://", "https://")):
        return fallback
    return text.rstrip(". ") + "."


def event_text(event_type):
    return EVENT_SPEECH.get(event_type, "An activity update was recorded earlier.")


def transition_text(entity, property_name, value, status):
    if entity == "owner":
        # Transport/test metadata is useful evidence, not briefing content.
        if property_name != "present":
            return ""
        if status in {"stale", "offline"}:
            return "Your presence reading went stale earlier."
        if status in {"unknown", "conflicting"} or not isinstance(value, bool):
            return "Your presence reading was uncertain earlier."
        return "Your presence was detected again." if value is True else "Your presence was not detected earlier."
    name = label(entity)
    prop = label(property_name, "state")
    if status in {"stale", "offline", "unknown", "conflicting"}:
        return f"Earlier, the {name}'s {prop} reading was {status}."
    if isinstance(value, bool):
        value_text = "on" if value else "off"
    elif isinstance(value, (int, float)) and math.isfinite(value):
        value_text = f"{value:.3g}"
    elif isinstance(value, str) and len(value) <= 35 and re.fullmatch(r"[\w .'-]+", value):
        value_text = value.replace("_", " ")
    else:
        return f"The {name}'s {prop} changed earlier."
    return f"Earlier, the {name}'s {prop} was reported as {value_text}."


def novelty_text(entity, measurement, value, unit, mean):
    direction = "high" if value > mean else "low"
    unit = {"C": "degrees Celsius", "F": "degrees Fahrenheit", "%": "percent",
            "W": "watts", "V": "volts", "A": "amps"}.get(unit, label(unit, ""))
    return f"Earlier, the {label(entity)}'s {label(measurement, 'reading')} was unusually {direction}, at {value:.3g} {unit}."


def candidate_text(candidate):
    """Also handles already-prepared pre-fix outbox payloads safely."""
    if candidate.get("spoken_text") is not None:
        text = candidate["spoken_text"]
        if not text and candidate.get("priority") != 1:
            return ""
        fallback = "An important alert needs your attention." if candidate.get("priority") == 1 else "An update is available in the activity history."
        return short_text(text, fallback)
    category = candidate.get("category")
    # Legacy diagnostic strings are never a generic fallback to speech.
    text = str(candidate.get("text", ""))[:512]
    if category in {"agent_outcome", "interaction"}:
        match = re.search(r": ((?:agent|person\.interaction)\.[a-z_.]+) \[", text)
        return event_text(match.group(1) if match else "")
    if category == "alert":
        title = text.split("] ", 1)[-1].split(" (recorded ", 1)[0] if text.startswith("ALERT ") else ""
        return short_text(title, "An alert needs your attention.")
    if category == "reminder":
        return "You have a pending reminder. Its details are in the activity history."
    if category == "novelty":
        return "An unusual sensor reading was recorded earlier."
    if candidate.get("entity_id") == "owner":
        if candidate.get("priority") != 1 and re.match(r"CHANGE owner\.(?:modality|detail|confidence)\b", text):
            return ""
        return "Your presence changed earlier."
    return "A state change was recorded earlier."


def render_batch(candidates, *, kind, part=0):
    # One sentence may cover repeated equivalent events; don't read it repeatedly.
    sentences = list(dict.fromkeys(candidate_text(c) for c in candidates))
    sentences = [s for s in sentences if s]
    if not sentences:
        return ""
    prefix = "Welcome back. " if kind == "arrival" and part == 0 else ""
    return prefix + " ".join(sentences)
