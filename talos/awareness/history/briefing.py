"""Typed, bounded stored-record reads for briefing assembly (Phase 9A).

All reads share the assembler's repeatable-read snapshot. No trigger, prompt,
network work, or write belongs here. Delivery metadata is read from the
existing notification ledger, never inferred from an outbox completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from talos.awareness.db.models import (
    Alert, AttentionItem, Event, NotificationDelivery, Reminder, StateTransition,
)


@dataclass(frozen=True)
class BriefingRecords:
    rows: list[Any]
    truncated: bool
    query: str


class BriefingHistory:
    """Storage service: stable query identifiers are included in every result."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def last_delivery(self, kind: str, end: datetime) -> datetime | None:
        return (await self.connection.execute(
            sa.select(sa.func.coalesce(
                sa.cast(NotificationDelivery.metadata_json["window"]["end"].astext, sa.DateTime(timezone=True)),
                NotificationDelivery.acknowledged_at, NotificationDelivery.attempted_at,
            ))
            .where(
                NotificationDelivery.status == "delivered",
                NotificationDelivery.metadata_json["briefing_kind"].astext == kind,
                NotificationDelivery.attempted_at < end,
            )
            .order_by(NotificationDelivery.attempted_at.desc()).limit(1)
        )).scalar_one_or_none()

    async def unavailable(self, ids: list[str]) -> set[str]:
        from talos.awareness.briefing.service import unavailable_ids
        return await unavailable_ids(self.connection, ids)

    async def preferences(self, candidates, now):
        from talos.awareness.briefing.feedback import preferences
        return await preferences(self.connection, candidates, now)

    async def _read(self, statement, limit: int, query: str) -> BriefingRecords:
        rows = (await self.connection.execute(statement.limit(limit + 1))).all()
        return BriefingRecords(list(rows[:limit]), len(rows) > limit, query)

    async def alerts(self, start: datetime, end: datetime, limit: int) -> BriefingRecords:
        # An alert and its attention item describe one incident. Delivered
        # incidents are not offered again, including delivery via normal alerts.
        delivered = sa.exists(sa.select(NotificationDelivery.id).where(
            NotificationDelivery.alert_id == Alert.alert_id,
            NotificationDelivery.status == "delivered",
        ))
        statement = sa.select(Alert.__table__).where(
            Alert.last_updated_at >= start, Alert.last_updated_at < end,
            Alert.status.in_(("open", "acknowledged")), ~delivered,
        ).order_by(
            sa.case((Alert.severity == "critical", 0), else_=1),
            Alert.last_updated_at.desc(), Alert.alert_id,
        )
        return await self._read(statement, limit, "briefing.alerts.v1")

    async def attention(self, start: datetime, end: datetime, limit: int) -> BriefingRecords:
        statement = sa.select(
            AttentionItem.__table__, Reminder.reminder_id, Reminder.due_at,
        ).outerjoin(Reminder, Reminder.attention_item_id == AttentionItem.attention_item_id).where(
            AttentionItem.created_at >= start, AttentionItem.created_at < end,
            AttentionItem.delivery_status == "pending",
            AttentionItem.alert_id.is_(None),
            sa.or_(AttentionItem.available_after.is_(None), AttentionItem.available_after <= end),
            sa.or_(AttentionItem.expires_at.is_(None), AttentionItem.expires_at > end),
        ).order_by(AttentionItem.priority, AttentionItem.created_at, AttentionItem.attention_item_id)
        return await self._read(statement, limit, "briefing.attention.v1")

    async def transitions(self, start: datetime, end: datetime, limit: int) -> BriefingRecords:
        statement = sa.select(
            StateTransition.__table__, Event.source_id, Event.observed_at,
            Event.received_at, Event.confidence,
        ).outerjoin(Event, Event.event_id == StateTransition.source_event_id).where(
            StateTransition.occurred_at >= start, StateTransition.occurred_at < end,
        ).order_by(StateTransition.occurred_at.desc(), StateTransition.id)
        return await self._read(statement, limit, "briefing.transitions.v1")

    async def events(self, start: datetime, end: datetime, limit: int) -> BriefingRecords:
        statement = sa.select(Event.__table__).where(
            Event.received_at >= start, Event.received_at < end,
            sa.or_(Event.event_type.startswith("agent."),
                   Event.event_type.startswith("person.interaction.")),
        ).order_by(sa.case((Event.severity == "critical", 0), else_=1), Event.received_at.desc(), Event.event_id)
        return await self._read(statement, limit, "briefing.events.v1")

    async def novelty(
        self, start: datetime, end: datetime, baseline_start: datetime, limit: int,
        baseline_limit: int,
    ) -> BriefingRecords:
        # Pool sample variance, rather than averaging per-bucket stddevs.
        # Use only complete hours BEFORE the candidate window: no self-baseline.
        # Units are separate series; aggregate provenance is entity-level because
        # the existing view intentionally combines sources. Missing/constant
        # baselines yield NULL, never an invented z-score or "first-ever" claim.
        statement = sa.text("""
            WITH recent AS (
                SELECT * FROM measurements
                WHERE time >= :start AND time < :end
                ORDER BY time DESC, entity_id, measurement_name, source_id
                LIMIT :row_limit
            )
            SELECT r.*, b.n AS baseline_samples, b.buckets AS baseline_buckets,
                   b.mean AS baseline_mean,
                   CASE WHEN b.n > 1 AND b.buckets <= :baseline_limit
                        THEN sqrt(greatest(0, (b.squares - b.n*b.mean*b.mean)/(b.n-1)))
                   END AS baseline_stddev,
                   CASE WHEN b.n > 1 AND b.buckets <= :baseline_limit
                        THEN abs(r.value_double-b.mean) /
                             nullif(sqrt(greatest(0, (b.squares-b.n*b.mean*b.mean)/(b.n-1))), 0)
                   END AS novelty_score
            FROM recent r
            LEFT JOIN LATERAL (
                SELECT sum(sample_count) AS n, count(*) AS buckets,
                       sum(sample_count*value_avg)/nullif(sum(sample_count),0) AS mean,
                       sum((sample_count-1)*coalesce(value_stddev,0)*coalesce(value_stddev,0)
                           + sample_count*value_avg*value_avg) AS squares
                FROM measurements_1h
                WHERE entity_id = r.entity_id AND measurement_name = r.measurement_name
                  AND unit IS NOT DISTINCT FROM r.unit
                  AND bucket >= :baseline_start AND bucket + interval '1 hour' <= :start
            ) b ON true
            ORDER BY r.time DESC, r.entity_id, r.measurement_name, r.source_id
        """)
        rows = (await self.connection.execute(statement, {
            "start": start, "end": end, "baseline_start": baseline_start,
            "row_limit": limit + 1, "baseline_limit": baseline_limit,
        })).all()
        return BriefingRecords(list(rows[:limit]), len(rows) > limit, "briefing.novelty.pooled_z.v1")
