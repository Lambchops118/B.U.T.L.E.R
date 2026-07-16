"""Action lifecycle service (C14, Phase 7).

Validation order (all before any dispatch): registered action → strict
parameter schema → actor permission → allowed prior state → cooldown →
confirmation when required. Every transition is durable
(``action_transitions``) and the request row is the current view.

Dispatch is outbox work (durable intent precedes network); the handler
publishes the registered payload — never model content — with a unique
``command_id`` and idempotency key, then schedules timeout work. Completion
is action-specific: state evidence for the legacy Picos, an explicit
``command_ack`` for the simulator. Silence never means success: the timeout
handler marks anything unconfirmed ``timed_out`` truthfully, and late or
duplicate acknowledgements are audited but cannot revive a cancelled or
timed-out command. Immediate electrical/mechanical interlocks remain in
firmware (INV-09); the backend never retries a non-idempotent dispatch on
its own.
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


def _hash_parameters(action: str, parameters: dict[str, Any]) -> str:
    canonical = json.dumps({"action": action, "parameters": parameters}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        definition = self._registry.get(action_name)
        if definition is None:
            return {
                "accepted": False,
                "status": "rejected",
                "reason": f"unsupported action {action_name!r}; "
                f"supported: {self._registry.names()}",
            }
        try:
            validated = definition.validate_parameters(parameters)
        except ValueError as exc:
            return await self._reject_unpersisted(str(exc))
        if actor not in definition.allowed_actors:
            return await self._reject_unpersisted(
                f"actor {actor!r} is not permitted for {action_name}"
            )

        async with self._engine.begin() as connection:
            state_error = await self._check_allowed_state(connection, definition, validated)
            if state_error:
                return await self._persist_rejected(
                    connection, definition, validated, actor, correlation_id, state_error, now
                )
            cooldown_error = await self._check_cooldown(connection, definition, now)
            if cooldown_error:
                return await self._persist_rejected(
                    connection, definition, validated, actor, correlation_id, cooldown_error, now
                )

            request_id, token = await self._persist_request(
                connection, definition, validated, actor, correlation_id, now
            )
            if definition.confirmation_required:
                return {
                    "accepted": True,
                    "action_request_id": str(request_id),
                    "status": "awaiting_confirmation",
                    "confirmation_token": token,
                    "confirmation_expires_in_seconds": definition.confirmation_ttl_seconds,
                    "note": "confirm with the exact token to dispatch",
                }
            await self._approve_and_queue(connection, request_id, definition, validated, actor, now)
        return {
            "accepted": True,
            "action_request_id": str(request_id),
            "status": "approved",
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
                return {"ok": False, "reason": "confirmation must come from the requesting actor"}
            if not secrets.compare_digest(token, row.confirmation_token or ""):
                return {"ok": False, "reason": "invalid confirmation token"}
            if row.confirmation_expires_at and row.confirmation_expires_at < now:
                await self._transition(
                    connection, action_request_id, row.status, "cancelled",
                    actor=actor, detail="confirmation expired", now=now,
                )
                return {"ok": False, "reason": "confirmation expired; request cancelled"}
            definition = self._registry.get(row.action_name)
            if definition is None:
                return {"ok": False, "reason": "action no longer registered"}
            await self._approve_and_queue(
                connection, action_request_id, definition, dict(row.parameters), actor, now
            )
        return {"ok": True, "status": "approved"}

    async def cancel(self, action_request_id: UUID, *, actor: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            row = (
                await connection.execute(
                    sa.select(ActionRequest.status)
                    .where(ActionRequest.action_request_id == action_request_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                return {"ok": False, "reason": "unknown action request"}
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
                sa.select(ActionRequest.action_request_id, ActionRequest.status)
                .where(ActionRequest.command_id == parsed)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return
        now = datetime.now(timezone.utc)
        ok = bool(payload.get("ok", True))
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
                connection, row.action_request_id, row.status, "failed",
                detail=f"negative acknowledgement (event {envelope.event_id})", now=now,
                extra={"acknowledged_at": now, "error": str(payload.get("error", "device reported failure"))[:300]},
            )
            return
        await self._transition(
            connection, row.action_request_id, row.status, "acknowledged",
            detail=f"acknowledged by device (event {envelope.event_id})", now=now,
            extra={"acknowledged_at": now},
        )
        # command_ack semantics (registry): acknowledgement is execution
        # receipt for the simulator, so completion follows.
        await self._transition(
            connection, row.action_request_id, "acknowledged", "completed",
            detail="completed on acknowledgement per action definition", now=now,
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
            expected_property = self._render_value(
                definition.confirm_property or "", row.parameters
            )
            if expected_property != f"pin_{pin}":
                continue
            expected = self._expected_value(definition, row.parameters)
            if bool(expected) != active:
                continue
            await self._transition(
                connection, row.action_request_id, row.status, "completed",
                detail=(
                    f"state confirmation: {expected_property} became {active} "
                    f"(event {envelope.event_id})"
                ),
                now=now,
                extra={"acknowledged_at": now, "completed_at": now},
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
                            "parameters": row.parameters,
                            "issued_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ).encode("utf-8")
                else:
                    body = self._render_value(definition.payload, row.parameters).encode("utf-8")
                now = datetime.now(timezone.utc)
                timeout_at = now + timedelta(seconds=definition.timeout_seconds)
                # Durable intent first; the publish happens after commit via
                # the captured values (a crash between commit and publish
                # leaves a dispatched-but-unconfirmed command that the
                # timeout marks truthfully — never an untracked retry).
                await self._transition(
                    connection, request_id, row.status, "dispatched",
                    detail=f"publishing to {topic}", now=now,
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
            await publish(topic, body)

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
            "status": row.status,
            "command_id": str(row.command_id) if row.command_id else None,
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
        if not definition.allowed_prior_values or not definition.confirm_property:
            return None
        property_name = self._render_value(definition.confirm_property, parameters)
        row = (
            await connection.execute(
                sa.select(CurrentState.value_json).where(
                    CurrentState.entity_id == definition.target_entity_id,
                    CurrentState.property_name == property_name,
                )
            )
        ).one_or_none()
        current = (row.value_json or {}).get("value") if row else None
        if current not in definition.allowed_prior_values:
            return (
                f"target state {property_name}={current!r} is not in the allowed "
                f"prior states {definition.allowed_prior_values}"
            )
        return None

    async def _check_cooldown(
        self, connection: AsyncConnection, definition: ActionDefinition, now: datetime
    ) -> str | None:
        if definition.cooldown_seconds <= 0:
            return None
        recent = (
            await connection.execute(
                sa.select(ActionRequest.created_at)
                .where(
                    ActionRequest.action_name == definition.name,
                    ActionRequest.status.notin_(("rejected", "cancelled")),
                    ActionRequest.created_at
                    > now - timedelta(seconds=definition.cooldown_seconds),
                )
                .limit(1)
            )
        ).first()
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
        now: datetime,
    ) -> tuple[UUID, str | None]:
        token = secrets.token_hex(16) if definition.confirmation_required else None
        status = "awaiting_confirmation" if definition.confirmation_required else "requested"
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
                    status=status,
                    command_id=uuid4(),
                    idempotency_key=f"action:{uuid4()}",
                    confirmation_token=token,
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
                to_status=status,
                occurred_at=now,
                actor=actor,
                detail="validated request",
            )
        )
        return request_id, token

    async def _persist_rejected(
        self,
        connection: AsyncConnection,
        definition: ActionDefinition,
        parameters: dict[str, Any],
        actor: str,
        correlation_id: str | None,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
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
                    status="rejected",
                    idempotency_key=f"action:{uuid4()}",
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
            "reason": reason,
        }

    async def _reject_unpersisted(self, reason: str) -> dict[str, Any]:
        # Schema/permission failures happen before anything durable exists;
        # they are logged rather than stored (nothing physical was at stake).
        logger.info("action request rejected: %s", reason, extra={"component": "actions"})
        return {"accepted": False, "status": "rejected", "reason": reason}

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
