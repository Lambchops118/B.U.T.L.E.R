"""Durable briefing work and receipts using the existing outbox/notification ledger."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from talos.awareness.db.models import AttentionItem, NotificationDelivery, OutboxItem, StateTransition
from talos.awareness.outbox.worker import OutboxDeferred


async def unavailable_ids(connection, ids: list[str]) -> set[str]:
    """Bounded exact identity lookup across all delivery history and active egress.

    The normal notification outbox owns its queued alerts/reminders. Briefings
    never compete to send those, and confirmed receipts survive outbox retention.
    """
    if not ids:
        return set()
    rows = await connection.execute(sa.text("""
        SELECT candidate FROM unnest(CAST(:ids AS text[])) AS t(candidate)
        WHERE EXISTS (
            SELECT 1 FROM notification_deliveries d WHERE d.status = 'delivered'
            AND ((d.metadata->'item_ids') ? candidate
                 OR ('alert:' || d.alert_id::text) = candidate
                 OR ('attention:' || d.attention_item_id::text) = candidate)
        ) OR EXISTS (
            SELECT 1 FROM outbox o WHERE o.work_type = 'notification' AND o.status = 'pending'
            AND (('alert:' || (o.payload->>'alert_id')) = candidate
                 OR ('attention:' || (o.payload->>'attention_item_id')) = candidate)
        ) OR EXISTS (
            SELECT 1 FROM attention_items a WHERE ('attention:' || a.attention_item_id::text) = candidate
            AND (a.delivery_status <> 'pending' OR a.expires_at <= :now OR a.available_after > :now)
        )
    """), {"ids": ids, "now": datetime.now(timezone.utc)})
    return {r.candidate for r in rows}


class BriefingStore:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings

    @asynccontextmanager
    async def serialized(self):
        # Session lock, AUTOCOMMIT: no event/DB transaction spans model or egress.
        # Dedicated briefing worker means this never serializes ordinary alerts.
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            acquired = (await connection.execute(sa.text("SELECT pg_try_advisory_lock(9009009)"))).scalar_one()
            if not acquired:
                raise OutboxDeferred(datetime.now(timezone.utc)+timedelta(seconds=2))
            try:
                yield
            finally:
                # Closing this physical connection releases the session lock even
                # on cancellation; never return a locked session to the pool.
                await asyncio.shield(connection.invalidate())

    async def enqueue_due(self, now: datetime) -> int:
        if not self.settings.briefing_enabled:
            return 0
        local = now.astimezone()
        hour, minute = map(int, self.settings.briefing_schedule_time.split(":"))
        # Use naive local -> astimezone to resolve the host's DST at the due time.
        due = local.replace(tzinfo=None, hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)
        moments = []
        if due <= now:
            moments.append((f"briefing:morning:{local.date().isoformat()}", "morning", due))
        async with self.engine.begin() as connection:
            if self.settings.briefing_arrival_enabled:
                start = now-timedelta(minutes=min(self.settings.briefing_arrival_lookback_minutes,
                                                  self.settings.max_query_range_days*1440))
                rows = (await connection.execute(sa.select(StateTransition.id, StateTransition.occurred_at).where(
                    StateTransition.entity_id == "owner", StateTransition.property_name == "present",
                    StateTransition.occurred_at >= start, StateTransition.occurred_at <= now,
                    StateTransition.to_value["value"].astext == "true",
                    StateTransition.to_status.in_(("current", "inferred")),
                    StateTransition.from_value["value"].astext.in_(("false", "absent")),
                    ~sa.exists(sa.select(OutboxItem.outbox_id).where(
                        OutboxItem.idempotency_key == sa.literal("briefing:arrival:")+sa.cast(StateTransition.id, sa.String),
                    )),
                ).order_by(StateTransition.occurred_at, StateTransition.id)
                    .limit(min(self.settings.max_query_points, 100)))).all()
                moments.extend((f"briefing:arrival:{r.id}", "arrival", r.occurred_at) for r in rows)
            count = 0
            for key, kind, moment in moments:
                count += await self._enqueue(connection, key, {"key": key, "root_key": key,
                    "kind": kind, "triggered_at": moment.isoformat(), "state": "new"}, now)
            return count

    async def _enqueue(self, connection, key, payload, available_at):
        result = await connection.execute(pg_insert(OutboxItem).values(
            work_type="briefing", aggregate_type="briefing", aggregate_id=payload["kind"],
            idempotency_key=key, payload=payload, available_at=available_at,
        ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(OutboxItem.outbox_id))
        return int(result.scalar_one_or_none() is not None)

    async def load(self, key: str) -> dict:
        async with self.engine.connect() as connection:
            payload = (await connection.execute(sa.select(OutboxItem.payload).where(
                OutboxItem.idempotency_key == key, OutboxItem.work_type == "briefing",
            ))).scalar_one()
            return dict(payload)

    async def save(self, payload: dict):
        async with self.engine.begin() as connection:
            await self._save(connection, payload)

    async def _save(self, connection, payload):
        await connection.execute(sa.update(OutboxItem).where(
            OutboxItem.idempotency_key == payload["key"], OutboxItem.work_type == "briefing",
        ).values(payload=payload))

    async def record(self, payload, batch, remaining, result, channel, *, confirmed_at=None, content=None):
        now = confirmed_at or datetime.now(timezone.utc)
        metadata = {"briefing_kind": payload["kind"], "batch_key": payload["key"],
                    "root_key": payload["root_key"], "window": payload["window"],
                    "item_ids": [c["item_id"] for c in batch],
                    "categories": {c["item_id"]: c["category"] for c in batch},
                    "selection": payload["selection"], "assembly_audit": payload["assembly_audit"],
                    "confirmation": "adapter acceptance; not proof of speech or human receipt"}
        if payload.get("morning_context") is not None:
            metadata["morning_context"] = payload["morning_context"]["audit"]
        if content is not None:
            metadata["announcement"] = {"title": content.title, "text": content.body,
                                        "playback_confirmed": False}
        async with self.engine.begin() as connection:
            await connection.execute(sa.insert(NotificationDelivery).values(
                channel=channel, attempted_at=now, acknowledged_at=now if result.confirmed else None,
                status="delivered" if result.confirmed else "failed",
                error=None if result.confirmed else "briefing adapter did not confirm",
                provider_message_id=result.provider_message_id, metadata_json=metadata,
            ))
            if not result.confirmed:
                return
            attention_ids = [UUID(c["evidence"]["attention_item_id"]) for c in batch
                             if c["evidence"].get("attention_item_id")]
            alert_ids = [UUID(c["evidence"]["alert_id"]) for c in batch if c["evidence"].get("alert_id")]
            targets = sa.select(AttentionItem.attention_item_id).where(
                AttentionItem.delivery_status == "pending",
                sa.or_(AttentionItem.attention_item_id.in_(attention_ids), AttentionItem.alert_id.in_(alert_ids)),
            ).order_by(AttentionItem.created_at).limit(self.settings.max_query_points)
            await connection.execute(sa.update(AttentionItem).where(
                AttentionItem.attention_item_id.in_(targets),
            ).values(delivery_status="delivered"))
            payload = dict(payload, state="delivered", remaining=[])
            await self._save(connection, payload)
            if remaining:
                part = payload.get("part", 0)+1
                key = f"{payload['root_key']}:part:{part}"
                await self._enqueue(connection, key, dict(payload, key=key, part=part,
                    state="prepared", remaining=remaining), now)

    async def recent(self, limit=10) -> list[dict]:
        limit = max(1, min(limit, 50, self.settings.max_query_points))
        async with self.engine.connect() as connection:
            rows = (await connection.execute(sa.select(
                NotificationDelivery.id, NotificationDelivery.attempted_at, NotificationDelivery.status,
                NotificationDelivery.channel, NotificationDelivery.metadata_json.label("audit"),
            ).where(NotificationDelivery.metadata_json["briefing_kind"].astext.is_not(None),
                    NotificationDelivery.attempted_at >= datetime.now(timezone.utc)-timedelta(days=self.settings.max_query_range_days))
                .order_by(NotificationDelivery.attempted_at.desc(), NotificationDelivery.id.desc()).limit(limit))).all()
        receipts = []
        for row in rows:
            audit = row.audit
            selection = audit.get("selection", {})
            # Full provenance stays durable in the ledger. The tool-facing view
            # returns only this capped batch, not every candidate from every run.
            receipts.append({"id": row.id, "attempted_at": row.attempted_at.isoformat(),
                "status": row.status, "channel": row.channel, "audit": {
                    k: audit[k] for k in ("briefing_kind", "batch_key", "root_key", "window", "item_ids", "categories", "confirmation", "announcement")
                    if k in audit}})
            receipts[-1]["audit"]["selection"] = {k: selection.get(k) for k in (
                "selection_mode", "prompt_version", "model", "reason", "truncated", "cap")}
            receipts[-1]["audit"]["selection"]["offered_count"] = len(selection.get("candidates_offered", []))
        return receipts
