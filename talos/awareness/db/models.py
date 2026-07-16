"""SQLAlchemy models for the awareness subsystem (Phase 1 tables).

The Alembic migration under ``db/migrations/versions`` is the deployment
source of truth; an integration test asserts this metadata and the migrated
database stay identical, so edit both together.

Conventions:
- timezone-aware UTC ``TIMESTAMPTZ`` everywhere;
- flexible payloads as JSONB, attribute ``metadata_json`` mapped to a
  ``metadata`` column (the attribute name is reserved by SQLAlchemy);
- closed status vocabularies enforced with CHECK constraints (kept as plain
  text to avoid PostgreSQL enum migration pain);
- natural text primary keys for registry rows (``kitchen``, ``plant_zone_1``)
  and UUIDs for high-volume rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SEVERITIES = ("debug", "info", "notice", "warning", "critical")
STATE_STATUSES = ("current", "stale", "unknown", "conflicting", "offline", "inferred", "scheduled")
ALERT_STATUSES = ("open", "acknowledged", "suppressed", "resolved", "expired")
INTERRUPTIBILITY_LEVELS = ("immediate", "interrupt_when_safe", "next_interaction", "passive")
ATTENTION_DELIVERY_STATUSES = ("pending", "delivering", "delivered", "failed", "expired", "cancelled")
SOURCE_HEALTH_STATUSES = ("healthy", "degraded", "stale", "offline", "misconfigured", "unauthorized", "unknown")
CLOCK_QUALITIES = ("unknown", "unsynchronized", "device_local", "device_synced", "gateway_stamped", "server_received")
OUTBOX_STATUSES = ("pending", "completed", "failed", "dead_letter")
ENTITY_TYPES = (
    "person",
    "plant",
    "device",
    "controller",
    "sensor",
    "actuator",
    "service",
    "automation",
    "agent",
    "notification_endpoint",
)


def _in_check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: sa.TIMESTAMP(timezone=True),
        dict[str, Any]: JSONB,
        UUID: PG_UUID(as_uuid=True),
    }


class Location(Base):
    __tablename__ = "locations"

    location_id: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(sa.String(200))
    kind: Mapped[str] = mapped_column(sa.String(50), server_default="room")
    parent_location_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"), onupdate=sa.func.now())


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        _in_check("entity_type", ENTITY_TYPES, "entity_type_valid"),
        sa.Index("ix_entities_entity_type", "entity_type"),
        sa.Index("ix_entities_location_id", "location_id"),
    )

    entity_id: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(sa.String(200))
    entity_type: Mapped[str] = mapped_column(sa.String(50))
    location_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(server_default=sa.text("true"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"), onupdate=sa.func.now())


class EntityRelationship(Base):
    __tablename__ = "entity_relationships"
    __table_args__ = (
        sa.Index("ix_entity_relationships_subject", "subject_entity_id", "relation"),
        sa.Index("ix_entity_relationships_object", "object_entity_id", "relation"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    subject_entity_id: Mapped[str] = mapped_column(
        sa.ForeignKey("entities.entity_id", ondelete="CASCADE")
    )
    relation: Mapped[str] = mapped_column(sa.String(100))
    object_entity_id: Mapped[str] = mapped_column(
        sa.ForeignKey("entities.entity_id", ondelete="CASCADE")
    )
    valid_from: Mapped[datetime | None] = mapped_column()
    valid_to: Mapped[datetime | None] = mapped_column()
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        _in_check("clock_quality", CLOCK_QUALITIES, "clock_quality_valid"),
        _in_check("health_status", SOURCE_HEALTH_STATUSES, "health_status_valid"),
        sa.Index("ix_sources_health_status", "health_status"),
        sa.Index("ix_sources_entity_id", "entity_id"),
    )

    source_id: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    source_type: Mapped[str] = mapped_column(sa.String(100))
    display_name: Mapped[str] = mapped_column(sa.String(200))
    transport: Mapped[str] = mapped_column(sa.String(64))
    entity_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("entities.entity_id", ondelete="SET NULL")
    )
    location_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("locations.location_id", ondelete="SET NULL")
    )
    firmware_version: Mapped[str | None] = mapped_column(sa.String(100))
    software_version: Mapped[str | None] = mapped_column(sa.String(100))
    schema_version: Mapped[int] = mapped_column(server_default=sa.text("1"))
    expected_update_interval_seconds: Mapped[float | None] = mapped_column()
    stale_after_seconds: Mapped[float | None] = mapped_column()
    offline_after_seconds: Mapped[float | None] = mapped_column()
    clock_quality: Mapped[str] = mapped_column(sa.String(30), server_default="unknown")
    last_observed_at: Mapped[datetime | None] = mapped_column()
    last_received_at: Mapped[datetime | None] = mapped_column()
    last_sequence: Mapped[int | None] = mapped_column(sa.BigInteger)
    last_boot_id: Mapped[str | None] = mapped_column(sa.String(200))
    health_status: Mapped[str] = mapped_column(sa.String(30), server_default="unknown")
    enabled: Mapped[bool] = mapped_column(server_default=sa.text("true"))
    authentication_identity: Mapped[str | None] = mapped_column(sa.String(200))
    # MQTT topics (exact or trailing-# patterns) this source may publish on (C17).
    allowed_topics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"), onupdate=sa.func.now())


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        _in_check("severity", SEVERITIES, "severity_valid"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.Index("ix_events_received_at", "received_at"),
        sa.Index("ix_events_observed_at", "observed_at"),
        sa.Index("ix_events_source_received", "source_id", "received_at"),
        sa.Index("ix_events_entity_received", "entity_id", "received_at"),
        sa.Index("ix_events_type_received", "event_type", "received_at"),
        sa.Index("ix_events_severity_received", "severity", "received_at"),
        sa.Index("ix_events_correlation_id", "correlation_id"),
        sa.Index("ix_events_causation_id", "causation_id"),
        # Duplicate detection for sources with sequence numbers (C1): a
        # legitimate reset is distinguished by a new source_boot_id.
        sa.Index(
            "uq_events_source_boot_sequence",
            "source_id",
            "source_boot_id",
            "sequence",
            unique=True,
            postgresql_where=sa.text("sequence IS NOT NULL AND source_boot_id IS NOT NULL"),
        ),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(sa.String(200))
    # Deliberately not a foreign key: events about not-yet-registered entities
    # must still be recordable; registry linkage is validated at the app level.
    entity_id: Mapped[str | None] = mapped_column(sa.String(200))
    source_id: Mapped[str] = mapped_column(
        sa.ForeignKey("sources.source_id", ondelete="RESTRICT")
    )
    location_id: Mapped[str | None] = mapped_column(sa.String(200))
    observed_at: Mapped[datetime | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column()
    processed_at: Mapped[datetime | None] = mapped_column()
    sequence: Mapped[int | None] = mapped_column(sa.BigInteger)
    source_boot_id: Mapped[str | None] = mapped_column(sa.String(200))
    correlation_id: Mapped[str | None] = mapped_column(sa.String(200))
    causation_id: Mapped[str | None] = mapped_column(sa.String(200))
    severity: Mapped[str] = mapped_column(sa.String(20))
    confidence: Mapped[float] = mapped_column(server_default=sa.text("1.0"))
    retention_class: Mapped[str | None] = mapped_column(sa.String(100))
    expires_at: Mapped[datetime | None] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))


class DeadLetterEvent(Base):
    __tablename__ = "dead_letter_events"
    __table_args__ = (
        sa.Index("ix_dead_letter_events_received_at", "received_at"),
        sa.Index("ix_dead_letter_events_reason", "reason"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sa.text("gen_random_uuid()"))
    received_at: Mapped[datetime] = mapped_column()
    transport: Mapped[str] = mapped_column(sa.String(64))
    topic_or_endpoint: Mapped[str | None] = mapped_column(sa.String(512))
    reason: Mapped[str] = mapped_column(sa.String(100))
    raw_payload: Mapped[str | None] = mapped_column(sa.Text)
    error_detail: Mapped[str | None] = mapped_column(sa.Text)
    source_hint: Mapped[str | None] = mapped_column(sa.String(200))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))


class CurrentState(Base):
    __tablename__ = "current_state"
    __table_args__ = (
        _in_check("state_status", STATE_STATUSES, "state_status_valid"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.Index("ix_current_state_status", "state_status"),
        sa.Index("ix_current_state_expires_at", "expires_at"),
        sa.Index("ix_current_state_source_id", "source_id"),
    )

    # One active row per (entity_id, property_name) — enforced by the PK.
    entity_id: Mapped[str] = mapped_column(
        sa.ForeignKey("entities.entity_id", ondelete="CASCADE"), primary_key=True
    )
    property_name: Mapped[str] = mapped_column(sa.String(200), primary_key=True)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    value_type: Mapped[str | None] = mapped_column(sa.String(50))
    observed_at: Mapped[datetime | None] = mapped_column()
    received_at: Mapped[datetime | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"), onupdate=sa.func.now())
    valid_from: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[datetime | None] = mapped_column()
    confidence: Mapped[float] = mapped_column(server_default=sa.text("1.0"))
    source_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("sources.source_id", ondelete="SET NULL")
    )
    source_event_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("events.event_id", ondelete="SET NULL")
    )
    state_status: Mapped[str] = mapped_column(sa.String(30), server_default="unknown")
    authority_rank: Mapped[int] = mapped_column(server_default=sa.text("0"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        _in_check("status", ALERT_STATUSES, "status_valid"),
        _in_check("severity", SEVERITIES, "severity_valid"),
        sa.Index("ix_alerts_status_severity", "status", "severity"),
        sa.Index("ix_alerts_entity_id", "entity_id"),
        sa.Index("ix_alerts_opened_at", "opened_at"),
        # One live incident per deduplication key (C8): repeats update the
        # existing alert instead of opening a new one.
        sa.Index(
            "uq_alerts_active_dedup",
            "deduplication_key",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('open', 'acknowledged') AND deduplication_key IS NOT NULL"
            ),
        ),
    )

    alert_id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sa.text("gen_random_uuid()"))
    alert_type: Mapped[str] = mapped_column(sa.String(100))
    severity: Mapped[str] = mapped_column(sa.String(20))
    entity_id: Mapped[str | None] = mapped_column(
        sa.ForeignKey("entities.entity_id", ondelete="SET NULL")
    )
    location_id: Mapped[str | None] = mapped_column(sa.String(200))
    title: Mapped[str] = mapped_column(sa.String(300))
    description: Mapped[str | None] = mapped_column(sa.Text)
    opened_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    last_updated_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    resolved_at: Mapped[datetime | None] = mapped_column()
    acknowledged_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(sa.String(20), server_default="open")
    deduplication_key: Mapped[str | None] = mapped_column(sa.String(300))
    occurrence_count: Mapped[int] = mapped_column(server_default=sa.text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    recommended_actions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'[]'::jsonb"))
    notification_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        sa.UniqueConstraint("alert_id", "event_id", name="uq_alert_events_alert_event"),
        sa.Index("ix_alert_events_alert_id", "alert_id"),
        sa.Index("ix_alert_events_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    alert_id: Mapped[UUID] = mapped_column(sa.ForeignKey("alerts.alert_id", ondelete="CASCADE"))
    # RESTRICT protects unresolved-alert evidence from retention deletion at
    # the database level (C15); retention must unlink first, per policy.
    event_id: Mapped[UUID] = mapped_column(sa.ForeignKey("events.event_id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(sa.String(30), server_default="evidence")
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))


class AttentionItem(Base):
    __tablename__ = "attention_items"
    __table_args__ = (
        _in_check("interruptibility", INTERRUPTIBILITY_LEVELS, "interruptibility_valid"),
        _in_check("delivery_status", ATTENTION_DELIVERY_STATUSES, "delivery_status_valid"),
        sa.Index("ix_attention_items_delivery", "delivery_status", "available_after"),
        sa.Index("ix_attention_items_alert_id", "alert_id"),
        sa.Index("ix_attention_items_cooldown_key", "cooldown_key"),
    )

    attention_item_id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=sa.text("gen_random_uuid()")
    )
    priority: Mapped[int] = mapped_column(server_default=sa.text("5"))
    reason: Mapped[str] = mapped_column(sa.Text)
    entity_id: Mapped[str | None] = mapped_column(sa.String(200))
    alert_id: Mapped[UUID | None] = mapped_column(
        sa.ForeignKey("alerts.alert_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    available_after: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[datetime | None] = mapped_column()
    interruptibility: Mapped[str] = mapped_column(sa.String(30), server_default="next_interaction")
    preferred_channel: Mapped[str | None] = mapped_column(sa.String(100))
    cooldown_key: Mapped[str | None] = mapped_column(sa.String(300))
    delivery_status: Mapped[str] = mapped_column(sa.String(20), server_default="pending")
    conversation_relevance: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"))


class OutboxItem(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        _in_check("status", OUTBOX_STATUSES, "status_valid"),
        sa.Index("ix_outbox_status_available", "status", "available_at"),
        sa.Index("ix_outbox_status_next_attempt", "status", "next_attempt_at"),
        sa.Index("ix_outbox_work_type", "work_type"),
    )

    outbox_id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    work_type: Mapped[str] = mapped_column(sa.String(100))
    aggregate_type: Mapped[str | None] = mapped_column(sa.String(100))
    aggregate_id: Mapped[str | None] = mapped_column(sa.String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sa.text("'{}'::jsonb"))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(300), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    available_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
    attempt_count: Mapped[int] = mapped_column(server_default=sa.text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    locked_at: Mapped[datetime | None] = mapped_column()
    locked_by: Mapped[str | None] = mapped_column(sa.String(200))
    completed_at: Mapped[datetime | None] = mapped_column()
    status: Mapped[str] = mapped_column(sa.String(20), server_default="pending")


class SchemaRegistryEntry(Base):
    __tablename__ = "schema_registry"
    __table_args__ = (
        sa.UniqueConstraint("kind", "name", "version", name="uq_schema_registry_kind_name_version"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(sa.String(100))
    name: Mapped[str] = mapped_column(sa.String(200))
    version: Mapped[int] = mapped_column()
    definition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=sa.text("now()"))
