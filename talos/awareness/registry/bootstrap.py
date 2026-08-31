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

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.models import Entity, Location, Source

_LOCATIONS: list[dict[str, Any]] = [
    {"location_id": "home", "display_name": "Home", "kind": "building"},
]

_ENTITIES: list[dict[str, Any]] = [
    {"entity_id": "fan", "display_name": "Room fan", "entity_type": "device", "location_id": "home"},
    {"entity_id": "quad_pump", "display_name": "Quad plant pump controller", "entity_type": "controller", "location_id": "home"},
    {"entity_id": "plant_pot_1", "display_name": "Plant pot 1", "entity_type": "plant", "location_id": "home"},
    {"entity_id": "plant_pot_2", "display_name": "Plant pot 2", "entity_type": "plant", "location_id": "home"},
    {"entity_id": "sim_greenhouse", "display_name": "Simulated greenhouse device", "entity_type": "device", "location_id": "home"},
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
        # The fan firmware publishes only when a command changes the pin: it
        # has no heartbeat, so silence means "nobody touched the fan", not
        # "the board is gone". Zero disables silence-based offline detection.
        "offline_after_seconds": 0,
        "metadata": {"legacy": "pin_status", "pin": 16, "value_inverted": True},
    },
    {
        # Retired: the board this described was reflashed with the canonical
        # firmware (quad_pump-2.0.0), which publishes nothing on status/17-19.
        # Left disabled rather than deleted so its history keeps a valid
        # foreign key and a legacy reflash only needs the flag flipped back.
        "source_id": "quad_pump_pico",
        "source_type": "microcontroller",
        "display_name": "Quad pump Pico W (legacy status topics)",
        "transport": "mqtt",
        "entity_id": "quad_pump",
        "location_id": "home",
        "clock_quality": "server_received",
        "allowed_topics": ["status/17", "status/18", "status/19"],
        "enabled": False,
        "offline_after_seconds": 0,
        "metadata": {"legacy": "pin_status"},
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
        # Deadlines derived from the firmware's own cadences
        # (qp_config.HEARTBEAT_INTERVAL_MS = 30 s,
        # STATE_SNAPSHOT_INTERVAL_MS = 300 s): offline after six missed
        # heartbeats, and state rows stale after three missed snapshots so a
        # snapshot arriving exactly on its own period does not flap.
        "offline_after_seconds": 180,
        "stale_after_seconds": 900,
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
    # The legacy pump source outlived its firmware: the board now runs
    # quad_pump-2.0.0 and publishes only on home/irrigation/quad_pump/*, so
    # this row's last_received_at froze at the reflash and the freshness
    # worker has been reporting the (working) quad pump as offline ever
    # since. Disabling it retires the row; the freshness worker resolves the
    # incident it left open.
    {
        "source_id": "quad_pump_pico",
        "column": "enabled",
        "expected": True,
        "value": False,
    },
    {
        "source_id": "quad_pump_pico",
        "column": "offline_after_seconds",
        "expected": None,
        "value": 0,
    },
    # The fan Pico publishes only when commanded — no heartbeat, so silence
    # is not evidence of failure.
    {
        "source_id": "fan_pico",
        "column": "offline_after_seconds",
        "expected": None,
        "value": 0,
    },
    # Canonical pump deadlines from the firmware cadences (see _SOURCES).
    {
        "source_id": "quad_pump_canonical",
        "column": "offline_after_seconds",
        "expected": None,
        "value": 180,
    },
    {
        "source_id": "quad_pump_canonical",
        "column": "stale_after_seconds",
        "expected": None,
        "value": 900,
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
