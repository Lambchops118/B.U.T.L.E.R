"""Action lifecycle service (C14, Phase 7).

Validation order (all before any dispatch): registered action → strict
parameter schema → actor permission → allowed prior state → cooldown →
confirmation when required. Every transition is durable
(``action_transitions``) and the request row is the current view.

Dispatch is outbox work (durable intent precedes network) and uses a unique
``command_id`` plus idempotency key. Legacy commands are attempted at most
once because their firmware cannot dedupe; canonical envelopes may retry the
same device key. Completion is action-specific: state evidence for the legacy
Picos, an explicit execution-result ``command_ack`` for the simulator. Silence
never means success: the timeout handler marks anything unconfirmed
``timed_out`` truthfully, and late or duplicate acknowledgements cannot revive
a terminal command. Immediate electrical/mechanical interlocks remain in
firmware (INV-09).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.actions.registry import ActionDefinition, ActionRegistry
from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import ActionRequest, ActionTransition, CurrentState, OutboxItem
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.actions")

TERMINAL_STATUSES = ("rejected", "completed", "failed", "timed_out", "cancelled")
MAX_PARAMETERS_BYTES = 4096
MAX_IDEMPOTENCY_KEY_CHARS = 200


def _hash_parameters(action: str, parameters: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_confirmation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_idempotency_key(value: str) -> str:
    return value.removeprefix("caller:")


class ActionService:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: AwarenessSettings,
        registry: ActionRegistry,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._registry = registry

    @property
    def registry(self) -> ActionRegistry:
        return self._registry

    # --- request / confirm / cancel ------------------------------------------

    async def request(
        self,
        *,
        action_name: str,
        parameters: dict[str, Any],
        actor: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        caller_key = (idempotency_key or "").strip()
        key_error = None
        if idempotency_key is not None and not caller_key:
            key_error = "idempotency_key must not be blank when provided"
        elif len(caller_key) > MAX_IDEMPOTENCY_KEY_CHARS:
            key_error = (
                f"idempotency_key must be at most {MAX_IDEMPOTENCY_KEY_CHARS} characters"
            )
        durable_key = f"caller:{caller_key}" if caller_key and not key_error else f"action:{uuid4()}"

        parameter_error = None
        try:
            encoded_parameters = json.dumps(
                parameters, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            encoded_parameters = b""
            parameter_error = "parameters must be JSON-serializable"
        if len(encoded_parameters) > MAX_PARAMETERS_BYTES:
            parameter_error = (
                f"parameters exceed the {MAX_PARAMETERS_BYTES}-byte action limit"
            )

        if parameter_error:
            audit_parameters: dict[str, Any] = {
                "_omitted": parameter_error,
                "sha256": hashlib.sha256(encoded_parameters).hexdigest(),
            }
        else:
            audit_parameters = parameters

        definition = self._registry.get(action_name)
        validation_error = parameter_error or key_error
        validated = audit_parameters
        if definition is None:
            validation_error = (
                f"unsupported action {action_name!r}; supported: {self._registry.names()}"
            )
        elif validation_error is None:
            try:
                validated = definition.validate_parameters(parameters)
            except ValueError as exc:
                validation_error = str(exc)

        parameters_hash = _hash_parameters(action_name, validated)

        async with self._engine.begin() as connection:
            if caller_key and not key_error:
                existing = await self._find_by_idempotency_key(connection, durable_key)
                if existing is not None:
                    return await self._deduplicate_request(
                        connection,
                        existing,
                        action_name=action_name,
                        parameters_hash=parameters_hash,
                        actor=actor,
                        correlation_id=correlation_id,
                        now=now,
                    )

            if validation_error is not None:
                return await self._persist_rejected(
                    connection,
                    action_name=action_name,
                    target_entity_id=(definition.target_entity_id if definition else None),
                    parameters=validated,
                    parameters_hash=parameters_hash,
                    actor=actor,
                    correlation_id=correlation_id,
                    idempotency_key=durable_key,
                    reason=validation_error,
                    now=now,
                )
            assert definition is not None
            if actor not in definition.allowed_actors:
                return await self._persist_rejected(
                    connection,
                    action_name=definition.name,
                    target_entity_id=definition.target_entity_id,
                    parameters=validated,
                    parameters_hash=parameters_hash,
                    actor=actor,
                    correlation_id=correlation_id,
                    idempotency_key=durable_key,
                    reason=f"actor {actor!r} is not permitted for {action_name}",
                    now=now,
                )
            state_error = await self._check_allowed_state(connection, definition, validated)
            if state_error:
                return await self._persist_rejected(
                    connection,
                    action_name=definition.name,
                    target_entity_id=definition.target_entity_id,
                    parameters=validated,
                    parameters_hash=parameters_hash,
                    actor=actor,
                    correlation_id=correlation_id,
                    idempotency_key=durable_key,
                    reason=state_error,
                    now=now,
                )
            cooldown_error = await self._check_cooldown(connection, definition, now)
            if cooldown_error:
                return await self._persist_rejected(
                    connection,
                    action_name=definition.name,
                    target_entity_id=definition.target_entity_id,
                    parameters=validated,
                    parameters_hash=parameters_hash,
                    actor=actor,
                    correlation_id=correlation_id,
                    idempotency_key=durable_key,
                    reason=cooldown_error,
                    now=now,
                )

            request_id, token = await self._persist_request(
                connection,
                definition,
                validated,
                actor,
                correlation_id,
                durable_key,
                now,
            )
            if definition.confirmation_required:
                return {
                    "accepted": True,
                    "action_request_id": str(request_id),
                    "status": "awaiting_confirmation",
                    "idempotency_key": _public_idempotency_key(durable_key),
                    "confirmation_token": token,
                    "confirmation_expires_in_seconds": definition.confirmation_ttl_seconds,
                    "note": "confirm with the exact token to dispatch",
                }
            await self._approve_and_queue(connection, request_id, definition, validated, actor, now)
        return {
            "accepted": True,
            "action_request_id": str(request_id),
            "status": "approved",
            "idempotency_key": _public_idempotency_key(durable_key),
        }

    async def confirm(
        self, action_request_id: UUID, *, token: str, actor: str
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        ActionRequest.status,
                        ActionRequest.action_name,
                        ActionRequest.parameters,
                        ActionRequest.actor,
                        ActionRequest.confirmation_token,
                        ActionRequest.confirmation_expires_at,
                    )
                    .where(ActionRequest.action_request_id == action_request_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return {"ok": False, "reason": "unknown action request"}
            if row.status != "awaiting_confirmation":
                return {"ok": False, "reason": f"request is {row.status}, not awaiting confirmation"}
            # Confirmation binds to the exact actor and request content.
            if actor != row.actor:
                await self._audit(
                    connection,
                    action_request_id,
                    row.status,
                    actor=actor,
                    detail="confirmation rejected: actor did not match requester",
                    now=now,
                )
                return {"ok": False, "reason": "confirmation must come from the requesting actor"}
            stored_token = row.confirmation_token or ""
            candidate = _hash_confirmation_token(token) if len(stored_token) == 64 else token
            if not secrets.compare_digest(candidate, stored_token):
                await self._audit(
                    connection,
                    action_request_id,
                    row.status,
                    actor=actor,
                    detail="confirmation rejected: invalid token",
                    now=now,
                )
                return {"ok": False, "reason": "invalid confirmation token"}
            if row.confirmation_expires_at and row.confirmation_expires_at < now:
                await self._transition(
                    connection, action_request_id, row.status, "cancelled",
                    actor=actor, detail="confirmation expired", now=now,
                )
                return {"ok": False, "reason": "confirmation expired; request cancelled"}
            definition = self._registry.get(row.action_name)
            if definition is None:
                await self._transition(
                    connection,
                    action_request_id,
                    row.status,
                    "failed",
                    actor=actor,
                    detail="action no longer registered at confirmation",
                    now=now,
                    extra={"error": "action no longer registered"},
                )
                return {"ok": False, "reason": "action no longer registered"}
            state_error = await self._check_allowed_state(
                connection, definition, dict(row.parameters)
            )
            cooldown_error = await self._check_cooldown(
                connection, definition, now, exclude_request_id=action_request_id
            )
            if state_error or cooldown_error:
                reason = state_error or cooldown_error or "validation failed"
                await self._transition(
                    connection,
                    action_request_id,
                    row.status,
                    "rejected",
                    actor=actor,
                    detail=f"confirmation-time validation failed: {reason}",
                    now=now,
                    extra={"error": reason[:500]},
                )
                return {"ok": False, "reason": reason}
            await self._approve_and_queue(
                connection, action_request_id, definition, dict(row.parameters), actor, now
            )
        return {"ok": True, "status": "approved"}

    async def cancel(self, action_request_id: UUID, *, actor: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        ActionRequest.status,
                        ActionRequest.actor,
                        ActionRequest.action_name,
                    )
                    .where(ActionRequest.action_request_id == action_request_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return {"ok": False, "reason": "unknown action request"}
            definition = self._registry.get(row.action_name)
            authorized = actor == row.actor or (
                actor == "operator"
                and definition is not None
                and actor in definition.allowed_actors
            )
            if not authorized:
                await self._audit(
                    connection,
                    action_request_id,
                    row.status,
                    actor=actor,
                    detail="cancellation rejected: actor not authorized",
                    now=now,
                )
                return {"ok": False, "reason": "actor is not permitted to cancel this request"}
            if row.status in TERMINAL_STATUSES or row.status == "dispatched":
                # A dispatched physical command cannot be un-sent; only
                # pre-dispatch requests are cancellable.
                return {"ok": False, "reason": f"cannot cancel a {row.status} request"}
            await self._transition(
                connection, action_request_id, row.status, "cancelled",
                actor=actor, detail="cancelled before dispatch", now=now,
            )
        return {"ok": True, "status": "cancelled"}

    # --- observation: acks and state evidence ----------------------------------

    async def observe_event(self, connection: AsyncConnection, envelope: Any) -> None:
        """Called inside the ingestion transaction for every accepted event:
        command acknowledgements and state evidence complete pending actions."""
        payload = envelope.payload or {}
        command_id = payload.get("command_id")
        if command_id and envelope.event_type.endswith("command_ack"):
            await self._handle_ack(connection, str(command_id), payload, envelope)
            return
        if envelope.event_type == "device.pin_status.reported":
            await self._handle_state_evidence(connection, envelope)

    async def _handle_ack(
        self, connection: AsyncConnection, command_id: str, payload: dict, envelope: Any
    ) -> None:
        try:
            parsed = UUID(command_id)
        except ValueError:
            return
        row = (
            await connection.execute(
                sa.select(
                    ActionRequest.action_request_id,
                    ActionRequest.status,
                    ActionRequest.action_name,
                )
                .where(ActionRequest.command_id == parsed)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return
        now = datetime.now(timezone.utc)
        definition = self._registry.get(row.action_name)
        if definition is None or definition.ack_mode != "command_ack":
            await self._audit(
                connection,
                row.action_request_id,
                row.status,
                detail=f"unexpected command acknowledgement ignored (event {envelope.event_id})",
                now=now,
            )
            return
        if envelope.source_id != definition.ack_source_id:
            await self._audit(
                connection,
                row.action_request_id,
                row.status,
                detail=(
                    f"acknowledgement from unauthorized source {envelope.source_id!r} "
                    f"ignored (event {envelope.event_id})"
                ),
                now=now,
            )
            return
        if not isinstance(payload.get("ok"), bool):
            await self._audit(
                connection,
                row.action_request_id,
                row.status,
                detail=f"malformed acknowledgement ignored (event {envelope.event_id})",
                now=now,
            )
            return
        ok = payload["ok"]
        if row.status not in ("dispatched", "acknowledged"):
            # Late/duplicate ack: audited, but cannot revive a terminal state.
            await connection.execute(
                sa.insert(ActionTransition).values(
                    action_request_id=row.action_request_id,
                    from_status=row.status,
                    to_status=row.status,
                    occurred_at=now,
                    detail=f"late acknowledgement ignored (ok={ok}, event {envelope.event_id})",
                )
            )
            return
        if not ok:
            await self._transition(
                connection,
                row.action_request_id,
                row.status,
                "acknowledged",
                detail=f"negative acknowledgement received (event {envelope.event_id})",
                now=now,
                extra={"acknowledged_at": now},
            )
            await self._transition(
                connection,
                row.action_request_id,
                "acknowledged",
                "failed",
                detail=f"device reported action failure (event {envelope.event_id})",
                now=now,
                extra={
                    "error": str(
                        payload.get("error", "device reported failure")
                    )[:300]
                },
            )
            return
        await self._transition(
            connection, row.action_request_id, row.status, "acknowledged",
            detail=f"acknowledged by device (event {envelope.event_id})", now=now,
            extra={"acknowledged_at": now},
        )
        if definition.ack_semantics == "execution_result":
            await self._transition(
                connection,
                row.action_request_id,
                "acknowledged",
                "completed",
                detail="acknowledgement is an execution result per action definition",
                now=now,
                extra={"completed_at": now},
            )

    async def _handle_state_evidence(self, connection: AsyncConnection, envelope: Any) -> None:
        payload = envelope.payload or {}
        pin = payload.get("pin")
        if pin is None:
            return
        raw = str(payload.get("raw_value", "")).strip()
        active = (raw == "1") != bool(payload.get("value_inverted"))
        rows = (
            await connection.execute(
                sa.select(
                    ActionRequest.action_request_id,
                    ActionRequest.status,
                    ActionRequest.action_name,
                    ActionRequest.parameters,
                    ActionRequest.target_entity_id,
                )
                .where(ActionRequest.status == "dispatched")
                .with_for_update()
            )
        ).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            definition = self._registry.get(row.action_name)
            if definition is None or definition.ack_mode != "state_confirmation":
                continue
            if envelope.source_id != definition.ack_source_id:
                continue
            if envelope.entity_id != row.target_entity_id:
                continue
            expected_property = self._render_value(
                definition.confirm_property or "", row.parameters
            )
            if expected_property != f"pin_{pin}":
                continue
            expected = self._expected_value(definition, row.parameters)
            if bool(expected) != active:
                await self._transition(
                    connection,
                    row.action_request_id,
                    row.status,
                    "acknowledged",
                    detail=(
                        f"state evidence received from {envelope.source_id} "
                        f"(event {envelope.event_id})"
                    ),
                    now=now,
                    extra={"acknowledged_at": now},
                )
                await self._transition(
                    connection,
                    row.action_request_id,
                    "acknowledged",
                    "failed",
                    detail=(
                        f"state confirmation mismatch: expected {expected_property}="
                        f"{bool(expected)}, observed {active}"
                    ),
                    now=now,
                    extra={
                        "error": (
                            f"state confirmation mismatch for {expected_property}: "
                            f"expected {bool(expected)}, observed {active}"
                        )
                    },
                )
                continue
            await self._transition(
                connection,
                row.action_request_id,
                row.status,
                "acknowledged",
                detail=(
                    f"state confirmation: {expected_property} became {active} "
                    f"(event {envelope.event_id})"
                ),
                now=now,
                extra={"acknowledged_at": now},
            )
            await self._transition(
                connection,
                row.action_request_id,
                "acknowledged",
                "completed",
                detail="completed after matching state evidence",
                now=now,
                extra={"completed_at": now},
            )

    # --- outbox handlers ---------------------------------------------------------

    def dispatch_handler(self, publish):
        """Build the outbox handler for ``action_dispatch``; ``publish`` is an
        async callable (topic, payload_bytes) provided by the app wiring."""

        async def _handle(payload: dict[str, Any]) -> None:
            request_id = UUID(payload["action_request_id"])
            async with self._engine.begin() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            ActionRequest.status,
                            ActionRequest.action_name,
                            ActionRequest.parameters,
                            ActionRequest.command_id,
                            ActionRequest.idempotency_key,
                            ActionRequest.target_entity_id,
                            ActionRequest.actor,
                            ActionRequest.correlation_id,
                        )
                        .where(ActionRequest.action_request_id == request_id)
                        .with_for_update()
                    )
                ).one_or_none()
                if row is None or row.status != "approved":
                    return  # already dispatched/cancelled: idempotent no-op
                definition = self._registry.get(row.action_name)
                if definition is None:
                    await self._transition(
                        connection, request_id, row.status, "failed",
                        detail="action no longer registered", now=datetime.now(timezone.utc),
                    )
                    return
                topic = definition.render_topic(row.parameters)
                if definition.payload == "envelope":
                    body = json.dumps(
                        {
                            "command_id": str(row.command_id),
                            "idempotency_key": row.idempotency_key,
                            "action": row.action_name,
                            "target_entity_id": row.target_entity_id,
                            "parameters": row.parameters,
                            "actor": row.actor,
                            "correlation_id": row.correlation_id,
                            "timeout_seconds": definition.timeout_seconds,
                            "ack_mode": definition.ack_mode,
                            "ack_semantics": definition.ack_semantics,
                            "issued_at": datetime.now(timezone.utc).isoformat(),
                        }
                    , separators=(",", ":")).encode("utf-8")
                else:
                    body = self._render_value(definition.payload, row.parameters).encode("utf-8")
                if definition.idempotency_behavior == "at_most_once":
                    # Legacy devices cannot dedupe command IDs. Mark the
                    # attempt before network I/O so a crash cannot cause an
                    # automatic repeated physical effect.
                    await self._mark_dispatched(
                        connection, request_id, row.status, definition, topic
                    )

            if definition.idempotency_behavior == "at_most_once":
                try:
                    await publish(topic, body)
                except Exception as exc:
                    logger.warning(
                        "at-most-once action dispatch failed for %s (%s)",
                        request_id,
                        type(exc).__name__,
                        extra={"component": "actions"},
                    )
                    async with self._engine.begin() as connection:
                        status = (
                            await connection.execute(
                                sa.select(ActionRequest.status)
                                .where(ActionRequest.action_request_id == request_id)
                                .with_for_update()
                            )
                        ).scalar_one_or_none()
                        if status == "dispatched":
                            await self._transition(
                                connection,
                                request_id,
                                status,
                                "failed",
                                detail="MQTT publish failed; delivery is unknown and was not retried",
                                now=datetime.now(timezone.utc),
                                extra={
                                    "error": (
                                        "MQTT publish failed; delivery unknown; "
                                        "at-most-once policy prevented retry"
                                    )
                                },
                            )
                return

            # Device-key actions carry a stable command/idempotency key. A
            # crash or broker failure may safely retry the same envelope; the
            # device is responsible for deduplicating it.
            await publish(topic, body)
            async with self._engine.begin() as connection:
                status = (
                    await connection.execute(
                        sa.select(ActionRequest.status)
                        .where(ActionRequest.action_request_id == request_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if status == "approved":
                    await self._mark_dispatched(
                        connection, request_id, status, definition, topic
                    )

        return _handle

    async def timeout_handler(self, payload: dict[str, Any]) -> None:
        request_id = UUID(payload["action_request_id"])
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    sa.select(ActionRequest.status)
                    .where(ActionRequest.action_request_id == request_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None or row.status not in ("dispatched", "acknowledged"):
                return  # completed/failed already: idempotent no-op
            await self._transition(
                connection, request_id, row.status, "timed_out",
                detail="no completion evidence within the action timeout", now=now,
                extra={"error": "timed out awaiting acknowledgement/state confirmation"},
            )

    # --- reads ---------------------------------------------------------------

    async def get(self, action_request_id: UUID) -> dict[str, Any] | None:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.select(ActionRequest).where(
                        ActionRequest.action_request_id == action_request_id
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            transitions = (
                await connection.execute(
                    sa.select(
                        ActionTransition.from_status,
                        ActionTransition.to_status,
                        ActionTransition.occurred_at,
                        ActionTransition.actor,
                        ActionTransition.detail,
                    )
                    .where(ActionTransition.action_request_id == action_request_id)
                    .order_by(ActionTransition.id)
                    .limit(50)
                )
            ).all()
        return {
            "action_request_id": str(row.action_request_id),
            "action_name": row.action_name,
            "target_entity_id": row.target_entity_id,
            "parameters": row.parameters,
            "actor": row.actor,
            "correlation_id": row.correlation_id,
            "status": row.status,
            "command_id": str(row.command_id) if row.command_id else None,
            "idempotency_key": _public_idempotency_key(row.idempotency_key),
            "timeout_at": row.timeout_at.isoformat() if row.timeout_at else None,
            "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
            "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "error": row.error,
            "transitions": [
                {
                    "from": item.from_status,
                    "to": item.to_status,
                    "at": item.occurred_at.isoformat(),
                    "actor": item.actor,
                    "detail": item.detail,
                }
                for item in transitions
            ],
        }

    # --- internals -------------------------------------------------------------

    def _render_value(self, template: str, parameters: dict[str, Any]) -> str:
        result = template
        for key, value in parameters.items():
            result = result.replace("{" + key + "}", str(value))
        return result

    def _expected_value(self, definition: ActionDefinition, parameters: dict[str, Any]) -> Any:
        value = definition.confirm_value
        if isinstance(value, str) and "{" in value:
            rendered = self._render_value(value, parameters)
            try:
                return bool(int(rendered))
            except ValueError:
                return rendered
        return value

    async def _check_allowed_state(
        self, connection: AsyncConnection, definition: ActionDefinition, parameters: dict[str, Any]
    ) -> str | None:
        if "allowed_state" not in definition.safety_checks:
            return None
        property_name = self._render_value(definition.confirm_property, parameters)
        row = (
            await connection.execute(
                sa.select(CurrentState.value_json, CurrentState.state_status).where(
                    CurrentState.entity_id == definition.target_entity_id,
                    CurrentState.property_name == property_name,
                )
            )
        ).one_or_none()
        current = (row.value_json or {}).get("value") if row else None
        if row is None or row.state_status != "current":
            return (
                f"safety check failed: target state {property_name} is not current "
                f"(status={row.state_status if row else 'unknown'})"
            )
        if current not in definition.allowed_prior_values:
            return (
                f"safety check failed: target state {property_name}={current!r} "
                f"is not in the allowed "
                f"prior states {definition.allowed_prior_values}"
            )
        return None

    async def _check_cooldown(
        self,
        connection: AsyncConnection,
        definition: ActionDefinition,
        now: datetime,
        *,
        exclude_request_id: UUID | None = None,
    ) -> str | None:
        if definition.cooldown_seconds <= 0:
            return None
        statement = sa.select(ActionRequest.created_at).where(
            ActionRequest.action_name == definition.name,
            ActionRequest.status.notin_(("rejected", "cancelled")),
            ActionRequest.created_at
            > now - timedelta(seconds=definition.cooldown_seconds),
        )
        if exclude_request_id is not None:
            statement = statement.where(
                ActionRequest.action_request_id != exclude_request_id
            )
        recent = (await connection.execute(statement.limit(1))).first()
        if recent is not None:
            return (
                f"cooldown: {definition.name} ran within the last "
                f"{definition.cooldown_seconds:.0f}s"
            )
        return None

    async def _persist_request(
        self,
        connection: AsyncConnection,
        definition: ActionDefinition,
        parameters: dict[str, Any],
        actor: str,
        correlation_id: str | None,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[UUID, str | None]:
        token = secrets.token_hex(16) if definition.confirmation_required else None
        request_id = (
            await connection.execute(
                sa.insert(ActionRequest)
                .values(
                    action_name=definition.name,
                    target_entity_id=definition.target_entity_id,
                    parameters=parameters,
                    parameters_hash=_hash_parameters(definition.name, parameters),
                    actor=actor,
                    correlation_id=correlation_id,
                    status="requested",
                    command_id=uuid4(),
                    idempotency_key=idempotency_key,
                    confirmation_token=(
                        _hash_confirmation_token(token) if token else None
                    ),
                    confirmation_expires_at=(
                        now + timedelta(seconds=definition.confirmation_ttl_seconds)
                        if token
                        else None
                    ),
                )
                .returning(ActionRequest.action_request_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(ActionTransition).values(
                action_request_id=request_id,
                from_status=None,
                to_status="requested",
                occurred_at=now,
                actor=actor,
                detail="action request received",
            )
        )
        await self._transition(
            connection,
            request_id,
            "requested",
            "validated",
            actor=actor,
            detail="registry, schema, permission, safety, and cooldown checks passed",
            now=now,
        )
        if definition.confirmation_required:
            await self._transition(
                connection,
                request_id,
                "validated",
                "awaiting_confirmation",
                actor=actor,
                detail="exact-request confirmation required",
                now=now,
            )
        return request_id, token

    async def _persist_rejected(
        self,
        connection: AsyncConnection,
        action_name: str,
        target_entity_id: str | None,
        parameters: dict[str, Any],
        parameters_hash: str,
        actor: str,
        correlation_id: str | None,
        idempotency_key: str,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        request_id = (
            await connection.execute(
                sa.insert(ActionRequest)
                .values(
                    action_name=action_name[:100],
                    target_entity_id=target_entity_id,
                    parameters=parameters,
                    parameters_hash=parameters_hash,
                    actor=actor[:100],
                    correlation_id=correlation_id,
                    status="rejected",
                    idempotency_key=idempotency_key,
                    error=reason[:500],
                )
                .returning(ActionRequest.action_request_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(ActionTransition).values(
                action_request_id=request_id,
                from_status=None,
                to_status="rejected",
                occurred_at=now,
                actor=actor,
                detail=reason[:500],
            )
        )
        return {
            "accepted": False,
            "action_request_id": str(request_id),
            "status": "rejected",
            "idempotency_key": _public_idempotency_key(idempotency_key),
            "reason": reason,
        }

    async def _find_by_idempotency_key(
        self, connection: AsyncConnection, idempotency_key: str
    ) -> Any | None:
        return (
            await connection.execute(
                sa.select(
                    ActionRequest.action_request_id,
                    ActionRequest.action_name,
                    ActionRequest.parameters_hash,
                    ActionRequest.actor,
                    ActionRequest.correlation_id,
                    ActionRequest.status,
                    ActionRequest.error,
                    ActionRequest.idempotency_key,
                )
                .where(ActionRequest.idempotency_key == idempotency_key)
                .with_for_update()
            )
        ).one_or_none()

    async def _deduplicate_request(
        self,
        connection: AsyncConnection,
        existing: Any,
        *,
        action_name: str,
        parameters_hash: str,
        actor: str,
        correlation_id: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        exact_match = (
            existing.action_name == action_name
            and existing.parameters_hash == parameters_hash
            and existing.actor == actor
            and existing.correlation_id == correlation_id
        )
        if not exact_match:
            await self._audit(
                connection,
                existing.action_request_id,
                existing.status,
                actor=actor,
                detail=(
                    "idempotency key reuse rejected: actor/action/parameters/"
                    "correlation did not match the original request"
                ),
                now=now,
            )
            return {
                "accepted": False,
                "action_request_id": str(existing.action_request_id),
                "status": "rejected",
                "idempotency_key": _public_idempotency_key(
                    existing.idempotency_key
                ),
                "reason": "idempotency key is already bound to a different request",
            }
        await self._audit(
            connection,
            existing.action_request_id,
            existing.status,
            actor=actor,
            detail="duplicate request returned the existing action lifecycle",
            now=now,
        )
        response = {
            "accepted": existing.status != "rejected",
            "action_request_id": str(existing.action_request_id),
            "status": existing.status,
            "idempotency_key": _public_idempotency_key(existing.idempotency_key),
            "deduplicated": True,
        }
        if existing.status == "rejected":
            response["reason"] = existing.error or "request was rejected"
        if existing.status == "awaiting_confirmation":
            response["note"] = (
                "duplicate request; use the confirmation token returned by the "
                "original response"
            )
        return response

    async def _mark_dispatched(
        self,
        connection: AsyncConnection,
        request_id: UUID,
        from_status: str,
        definition: ActionDefinition,
        topic: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        timeout_at = now + timedelta(seconds=definition.timeout_seconds)
        await self._transition(
            connection,
            request_id,
            from_status,
            "dispatched",
            detail=f"command publish attempted on registered topic {topic}",
            now=now,
            extra={"dispatched_at": now, "timeout_at": timeout_at},
        )
        await connection.execute(
            pg_insert(OutboxItem)
            .values(
                work_type="action_timeout",
                aggregate_type="action_request",
                aggregate_id=str(request_id),
                payload={"action_request_id": str(request_id)},
                idempotency_key=f"action_timeout:{request_id}",
                available_at=timeout_at,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )

    async def _audit(
        self,
        connection: AsyncConnection,
        request_id: UUID,
        status: str,
        *,
        now: datetime,
        actor: str | None = None,
        detail: str,
    ) -> None:
        """Append an audit observation without changing lifecycle state."""
        await connection.execute(
            sa.insert(ActionTransition).values(
                action_request_id=request_id,
                from_status=status,
                to_status=status,
                occurred_at=now,
                actor=actor,
                detail=detail[:500],
            )
        )

    async def _approve_and_queue(
        self,
        connection: AsyncConnection,
        request_id: UUID,
        definition: ActionDefinition,
        parameters: dict[str, Any],
        actor: str,
        now: datetime,
    ) -> None:
        current = (
            await connection.execute(
                sa.select(ActionRequest.status).where(
                    ActionRequest.action_request_id == request_id
                )
            )
        ).scalar_one()
        await self._transition(
            connection, request_id, current, "approved",
            actor=actor, detail="approved for dispatch", now=now,
        )
        await connection.execute(
            pg_insert(OutboxItem)
            .values(
                work_type="action_dispatch",
                aggregate_type="action_request",
                aggregate_id=str(request_id),
                payload={"action_request_id": str(request_id)},
                idempotency_key=f"action_dispatch:{request_id}",
                available_at=now,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )

    async def _transition(
        self,
        connection: AsyncConnection,
        request_id: UUID,
        from_status: str | None,
        to_status: str,
        *,
        now: datetime,
        actor: str | None = None,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": to_status, "updated_at": now}
        if extra:
            values.update(extra)
        await connection.execute(
            sa.update(ActionRequest)
            .where(ActionRequest.action_request_id == request_id)
            .values(**values)
        )
        await connection.execute(
            sa.insert(ActionTransition).values(
                action_request_id=request_id,
                from_status=from_status,
                to_status=to_status,
                occurred_at=now,
                actor=actor,
                detail=detail,
            )
        )
