"""Idempotent registry seeding for the known deployment (C18 startup step).

Seeds the locations, entities, and sources this installation is known to
have — the two Pico W boards publishing on the legacy ``status/{pin}`` topics,
the canonical quad-pump firmware, and the simulator device used for
development and tests. ``ON CONFLICT DO NOTHING`` preserves any operator edits
made after the first boot.

Because inserts do nothing on conflict, editing a seed row above never reaches
a database that has already booted. ``_SOURCE_MIGRATIONS`` is the explicit
update path for that case: each entry names the exact previous value it
expects to find, so a field an operator deliberately changed is left alone.

Note on the legacy topics: both Picos publish ``status/16`` in firmware (a
known collision documented in DISCOVERY.md). Ownership here assigns
``status/16`` to the fan and 17–19 to the pump; the collision is only truly
fixable in firmware, which is out of scope per the owner's decision to use
simulated hardware for now.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.models import (
    Alert,
    AttentionItem,
    CurrentState,
    Entity,
    Location,
    Source,
    SourceHealthHistory,
)

_LOCATIONS: list[dict[str, Any]] = [
    {"location_id": "home", "display_name": "Home", "kind": "building"},
]

_ENTITIES: list[dict[str, Any]] = [
    {"entity_id": "fan", "display_name": "Room fan", "entity_type": "device", "location_id": "home"},
    {"entity_id": "quad_pump", "display_name": "Quad plant pump controller", "entity_type": "controller", "location_id": "home"},
    {"entity_id": "plant_pot_1", "display_name": "Plant pot 1", "entity_type": "plant", "location_id": "home"},
    {"entity_id": "plant_pot_2", "display_name": "Plant pot 2", "entity_type": "plant", "location_id": "home"},
    {"entity_id": "sim_greenhouse", "display_name": "Simulated greenhouse device", "entity_type": "device", "location_id": "home"},
    # The human and the agent are first-class entities: presence, interaction,
    # and agent-side outcomes attach to them exactly like device state attaches
    # to a pump. Without these rows nothing about the people in the house can
    # be recorded, because state and telemetry only bind to registered
    # entities (see IngestionPipeline._resolve_entity).
    {"entity_id": "owner", "display_name": "Owner", "entity_type": "person", "location_id": "home"},
    {"entity_id": "talos", "display_name": "TALOS agent", "entity_type": "agent", "location_id": "home"},
]

_SOURCES: list[dict[str, Any]] = [
    {
        "source_id": "fan_pico",
        "source_type": "microcontroller",
        "display_name": "Fan Pico W (legacy status topic)",
        "transport": "mqtt",
        "entity_id": "fan",
        "location_id": "home",
        "clock_quality": "server_received",  # firmware has no clock sync
        "allowed_topics": ["status/16"],
        "metadata": {"legacy": "pin_status", "pin": 16, "value_inverted": True},
    },
    {
        "source_id": "quad_pump_pico",
        "source_type": "microcontroller",
        "display_name": "Quad pump Pico W (legacy status topics)",
        "transport": "mqtt",
        "entity_id": "quad_pump",
        "location_id": "home",
        "clock_quality": "server_received",
        "allowed_topics": ["status/17", "status/18", "status/19"],
        # This read surface was superseded by the canonical firmware. It stays
        # registered so retained/late legacy evidence remains attributable,
        # but silence is not a fault now that the device reports canonically.
        "metadata": {
            "legacy": "pin_status",
            "superseded_by": "quad_pump_canonical",
            "offline_detection": False,
        },
    },
    {
        # Canonical quad-pump firmware (Peripherals/quad_pump). Separate from
        # quad_pump_pico because that source is routed through the legacy
        # pin_status normalizer, which cannot read canonical JSON envelopes.
        #
        # Topics are listed individually rather than as
        # ``home/irrigation/quad_pump/#`` so the source owns only what the
        # device publishes. The command topic is published *by* this backend
        # and is deliberately not owned here — action requests are already
        # durable in ``action_requests``.
        "source_id": "quad_pump_canonical",
        "source_type": "microcontroller",
        "display_name": "Quad pump Pico W (canonical firmware)",
        "transport": "mqtt",
        "entity_id": "quad_pump",
        "location_id": "home",
        # The firmware implements no clock synchronization, so ordering uses
        # the server receive time. Raise this only if NTP is added and proven.
        "clock_quality": "server_received",
        # Firmware snapshots state every 300 s. The former inherited 300 s
        # deadline raced that cadence and marked a healthy controller stale
        # between snapshots. Heartbeats still drive the tighter source-health
        # deadline independently.
        "stale_after_seconds": 420.0,
        "allowed_topics": [
            "home/irrigation/quad_pump/state",
            "home/irrigation/quad_pump/event",
            "home/irrigation/quad_pump/health",
            "home/irrigation/quad_pump/heartbeat",
            "home/irrigation/quad_pump/telemetry/+",
        ],
        "metadata": {"channels": [1, 2, 3, 4], "fuse_sensing": "unavailable"},
    },
    {
        "source_id": "sim_device",
        "source_type": "simulator",
        "display_name": "Awareness simulator device",
        "transport": "mqtt",
        "entity_id": "sim_greenhouse",
        "location_id": "home",
        "clock_quality": "device_synced",
        "allowed_topics": ["home/sim/#"],
        "metadata": {"simulator": True},
    },
    {
        # The main agent process reporting on the human and on itself. Its
        # topics live under home/ so the existing canonical normalizer handles
        # them unchanged, but metadata.allowed_transports pins the source to
        # the internal (loopback API) transport: the MQTT ingress subscribes
        # home/#, so without that pin anyone on the LAN broker could publish
        # fabricated presence or interaction events.
        #
        # clock_quality is gateway_stamped, not device_synced: the agent runs
        # on the same host as this backend and stamps observed_at from that
        # host clock, so the time is trustworthy for ordering but is still a
        # stamp applied by an intermediary rather than by a synchronized
        # device.
        #
        # Human presence is explicit state, not an inactivity timer. Ordinary
        # interaction may assert present, but silence never changes it to stale
        # or absent. Only an explicit user statement may write present=false.
        # This source is also excluded from offline detection because it reports
        # on interaction rather than on a schedule.
        "source_id": "talos_agent",
        "source_type": "agent",
        "display_name": "TALOS main agent (internal signals)",
        "transport": "internal",
        "entity_id": "talos",
        "location_id": "home",
        "clock_quality": "gateway_stamped",
        "allowed_topics": [
            "home/presence/owner/state",
            "home/interaction/owner/event",
            "home/agent/talos/event",
            "home/agent/talos/state",
        ],
        "metadata": {
            "internal": True,
            "allowed_transports": ["internal"],
            "offline_detection": False,
            "state_freshness_detection": False,
        },
    },
]


# Explicit updates for rows that already exist. ``expected`` is the value this
# file previously seeded; a row holding anything else was customized by an
# operator and is left untouched.
_SOURCE_MIGRATIONS: list[dict[str, Any]] = [
    {
        "source_id": "quad_pump_pico",
        "column": "display_name",
        "expected": "Quad pump Pico W (legacy status topics)",
        "value": "Quad pump Pico W (legacy status topics; superseded by quad_pump_canonical)",
    },
    {
        "source_id": "quad_pump_pico",
        "column": "metadata_json",
        "expected": {"legacy": "pin_status"},
        "value": {
            "legacy": "pin_status",
            "superseded_by": "quad_pump_canonical",
            "offline_detection": False,
        },
    },
    {
        "source_id": "quad_pump_canonical",
        "column": "stale_after_seconds",
        "expected": None,
        "value": 420.0,
    },
    {
        # Dropping the field from the seed only affects a fresh insert; the
        # deployed row still carried the old 15-minute presence timer, which is
        # the exact deadline that produced the stale/recovered churn.
        "source_id": "talos_agent",
        "column": "stale_after_seconds",
        "expected": 900.0,
        "value": None,
    },
    {
        "source_id": "talos_agent",
        "column": "metadata_json",
        "expected": {
            "internal": True,
            "allowed_transports": ["internal"],
            "offline_detection": False,
        },
        "value": {
            "internal": True,
            "allowed_transports": ["internal"],
            "offline_detection": False,
            "state_freshness_detection": False,
        },
    },
]


async def seed_registry(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for row in _LOCATIONS:
            await connection.execute(
                insert(Location).values(**row).on_conflict_do_nothing(index_elements=["location_id"])
            )
        for row in _ENTITIES:
            await connection.execute(
                insert(Entity).values(**row).on_conflict_do_nothing(index_elements=["entity_id"])
            )
        for row in _SOURCES:
            values = dict(row)
            values["metadata_json"] = values.pop("metadata", {})
            await connection.execute(
                insert(Source).values(**values).on_conflict_do_nothing(index_elements=["source_id"])
            )
        await apply_source_migrations(connection)
        await reconcile_non_reporting_sources(connection)
        await reconcile_never_expiring_state(connection)


async def apply_source_migrations(connection) -> int:
    """Apply ``_SOURCE_MIGRATIONS`` to already-seeded rows.

    Returns the number of rows changed. Each migration is conditional on the
    previously seeded value, so it is idempotent and never overwrites an
    intentional operator edit.
    """
    changed = 0
    for migration in _SOURCE_MIGRATIONS:
        column = getattr(Source, migration["column"])
        result = await connection.execute(
            sa.update(Source)
            .where(
                Source.source_id == migration["source_id"],
                column == migration["expected"],
            )
            .values(**{migration["column"]: migration["value"]})
        )
        changed += result.rowcount or 0
    return changed


async def reconcile_non_reporting_sources(connection) -> int:
    """Clear false offline incidents for sources whose silence is expected.

    Changing registry policy alone would stop future detections while leaving
    the durable source, state, alert, and attention rows saying ``offline``.
    Reconcile only exact policy opt-outs; real reporting sources are untouched.
    """
    now = datetime.now(timezone.utc)
    source_ids = list(
        (
            await connection.execute(
                sa.select(Source.source_id).where(
                    Source.metadata_json["offline_detection"].astext == "false"
                )
            )
        ).scalars()
    )
    if not source_ids:
        return 0
    offline_rows = (
        await connection.execute(
            sa.select(Source.source_id, Source.health_status).where(
                Source.source_id.in_(source_ids), Source.health_status == "offline"
            )
        )
    ).all()
    for row in offline_rows:
        await connection.execute(
            sa.insert(SourceHealthHistory).values(
                source_id=row.source_id,
                health_status="unknown",
                previous_status="offline",
                changed_at=now,
                reason="offline detection disabled by registry policy",
            )
        )
    changed = (
        await connection.execute(
            sa.update(Source)
            .where(Source.source_id.in_(source_ids), Source.health_status == "offline")
            .values(health_status="unknown", updated_at=now)
        )
    ).rowcount or 0
    await connection.execute(
        sa.update(CurrentState)
        .where(CurrentState.source_id.in_(source_ids), CurrentState.state_status == "offline")
        .values(state_status="stale")
    )
    keys = [f"source_offline:{source_id}" for source_id in source_ids]
    resolved = (
        await connection.execute(
            sa.update(Alert)
            .where(
                Alert.deduplication_key.in_(keys),
                Alert.status.in_(("open", "acknowledged")),
            )
            .values(status="resolved", resolved_at=now, last_updated_at=now)
            .returning(Alert.alert_id)
        )
    ).scalars().all()
    if resolved:
        await connection.execute(
            sa.update(AttentionItem)
            .where(
                AttentionItem.alert_id.in_(resolved),
                AttentionItem.delivery_status.in_(("pending", "delivering", "failed")),
            )
            .values(delivery_status="cancelled")
        )
    return changed


async def reconcile_never_expiring_state(connection) -> int:
    """Un-expire state rows belonging to sources that no longer expire.

    Turning off state expiry for a source stops future expiries but leaves
    whatever the old deadline already wrote. For owner presence that residue is
    the whole complaint: a reading marked ``stale`` during a quiet stretch keeps
    reading as "not detected as present" until the next interaction rewrites it,
    which is the same "you were away" the policy change was meant to end.

    Only exact opt-outs are touched, and only the status: the recorded value,
    its timestamps and its provenance are left exactly as they were, so nothing
    here invents a fact. No transition row is written -- the reading did not
    change, only the expiry policy applied to it did.
    """
    source_ids = list(
        (
            await connection.execute(
                sa.select(Source.source_id).where(
                    Source.metadata_json["state_freshness_detection"].astext == "false"
                )
            )
        ).scalars()
    )
    if not source_ids:
        return 0
    return (
        await connection.execute(
            sa.update(CurrentState)
            .where(
                CurrentState.source_id.in_(source_ids),
                CurrentState.state_status == "stale",
            )
            .values(state_status="current")
        )
    ).rowcount or 0
