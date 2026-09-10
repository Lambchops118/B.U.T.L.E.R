"""Structured JSON logging for the awareness backend.

Every record becomes one JSON line on stdout. Correlation identifiers are
attached via ``logger.info("...", extra={"event_id": ..., "source_id": ...})``
and only whitelisted context fields are emitted, so secrets never leak through
free-form extras.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

# C16: identifiers that structured log records may carry.
CONTEXT_FIELDS = (
    "component",
    "event_id",
    "correlation_id",
    "causation_id",
    "source_id",
    "entity_id",
    "alert_id",
    "action_id",
    "conversation_id",
    "outbox_id",
    "notification_request_id",
)

_HANDLER_MARKER = "_talos_awareness_handler"


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = record.__dict__.get(field)
            if value is not None:
                payload[field] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    root.handlers = [
        handler for handler in root.handlers if not getattr(handler, _HANDLER_MARKER, False)
    ]
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
