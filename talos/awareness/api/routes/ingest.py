"""Internal (non-MQTT) ingestion endpoint.

One message, one bounded HTTP call, the *same* pipeline the broker ingress
uses. This exists for two callers:

- the main agent, reporting presence, interaction, and its own outcomes on
  the ``internal`` transport (those sources are pinned to it in the registry);
- a human debugging the subsystem, injecting a message by hand instead of
  publishing to the broker.

It is deliberately **not** a bypass. The request is turned into the identical
:class:`InboundMessage` the MQTT ingress builds and handed to
``IngestionPipeline.handle``, so registry topic ownership, transport
authorization, payload bounds, normalization, sequence assessment, state
effects, and rule evaluation all apply unchanged. A topic no registered
source owns is dead-lettered here exactly as it would be from the broker.

The value over publishing to MQTT is that the pipeline's disposition is
returned *synchronously*: the caller learns ``accepted``, ``duplicate``, or
``dead_letter:{reason}`` in the response instead of having to go read
``dead_letter_events`` afterwards.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from talos.awareness.api.auth import require_write_auth
from talos.awareness.ingestion.pipeline import InboundMessage

router = APIRouter()

# Bound the request itself before the pipeline's own payload check, so an
# oversized body is refused rather than dead-lettered (a caller error, not a
# device misbehaving).
MAX_TOPIC_CHARS = 512


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=MAX_TOPIC_CHARS)
    # A JSON object is the canonical form; a raw string is accepted so legacy
    # ``status/{pin}`` payloads ("0"/"1") can be injected verbatim.
    payload: dict[str, Any] | str = Field(default_factory=dict)
    retained: bool = False
    transport: Literal["internal", "mqtt", "manual"] = "internal"


def _encode(payload: dict[str, Any] | str) -> bytes:
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, default=str).encode("utf-8")


@router.post("/ingest", dependencies=[Depends(require_write_auth)])
async def ingest(body: IngestRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.ingest_api_enabled:
        raise HTTPException(
            status_code=503,
            detail="internal ingestion is disabled (TALOS_AWARENESS_INGEST_API_ENABLED=0)",
        )

    pipeline = getattr(request.app.state, "ingest_pipeline", None)
    if pipeline is None:
        # Truthful degradation: the pipeline is built at startup and only
        # absent if that failed. Never pretend the event was stored.
        raise HTTPException(
            status_code=503, detail="ingestion pipeline is unavailable"
        )

    encoded = _encode(body.payload)
    if len(encoded) > settings.max_event_payload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"payload is {len(encoded)} bytes; limit is "
                f"{settings.max_event_payload_bytes}"
            ),
        )

    disposition = await pipeline.handle(
        InboundMessage(
            topic=body.topic,
            payload=encoded,
            retained=body.retained,
            transport=body.transport,
        )
    )
    return {
        "topic": body.topic,
        "transport": body.transport,
        "disposition": disposition,
        "accepted": disposition == "accepted",
    }
