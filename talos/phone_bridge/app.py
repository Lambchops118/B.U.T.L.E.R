from __future__ import annotations

import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from talos.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
from talos.phone.provider import PhoneConfig
from talos.phone.store import PhoneCallStore, get_default_phone_store


def create_app(
    *,
    store: PhoneCallStore | None = None,
    api_token: str | None = None,
    webhook_token: str | None = None,
) -> Starlette:
    resolved_store = store or get_default_phone_store()
    config = PhoneConfig.from_env()
    provider = ElevenLabsTwilioProvider(config, store=resolved_store)
    resolved_api_token = api_token if api_token is not None else os.getenv("PHONE_BRIDGE_API_TOKEN", "").strip()
    resolved_webhook_token = (
        webhook_token if webhook_token is not None else os.getenv("PHONE_BRIDGE_WEBHOOK_TOKEN", "").strip()
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "status": "healthy"})

    async def list_calls(request: Request) -> JSONResponse:
        try:
            _authorize_api_request(request, resolved_api_token)
        except PermissionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)
        updated_after = (request.query_params.get("updated_after") or "").strip() or None
        limit = max(1, int(request.query_params.get("limit", "25")))
        calls = resolved_store.list_calls_updated_after(updated_after, limit=limit)
        return JSONResponse({"ok": True, "calls": [record.to_dict() for record in calls]})

    async def get_call(request: Request) -> JSONResponse:
        try:
            _authorize_api_request(request, resolved_api_token)
        except PermissionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)
        call_id = str(request.path_params.get("call_id") or "").strip()
        record = resolved_store.get_call(call_id)
        if record is None:
            return JSONResponse({"ok": False, "error": "Call not found."}, status_code=404)
        return JSONResponse({"ok": True, "call": record.to_dict()})

    async def elevenlabs_webhook(request: Request) -> JSONResponse:
        try:
            _authorize_webhook_request(request, resolved_webhook_token)
        except PermissionError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=401)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "error": "Invalid JSON payload."}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Webhook payload must be a JSON object."}, status_code=400)

        try:
            record = provider.ingest_call_event(payload)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "call": record.to_dict()})

    return Starlette(
        routes=[
            Route("/health", endpoint=health, methods=["GET"]),
            Route("/calls", endpoint=list_calls, methods=["GET"]),
            Route("/calls/{call_id:str}", endpoint=get_call, methods=["GET"]),
            Route("/webhooks/elevenlabs", endpoint=elevenlabs_webhook, methods=["POST"]),
        ]
    )


def _authorize_api_request(request: Request, api_token: str) -> None:
    if not api_token:
        return
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header == f"Bearer {api_token}":
        return
    raise PermissionError("Unauthorized")


def _authorize_webhook_request(request: Request, webhook_token: str) -> None:
    if not webhook_token:
        return
    header_token = request.headers.get("X-Webhook-Token", "").strip()
    query_token = (request.query_params.get("token") or "").strip()
    if header_token == webhook_token or query_token == webhook_token:
        return
    raise PermissionError("Unauthorized")


app = create_app()
