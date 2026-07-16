"""Idempotent registry seeding for the known deployment (C18 startup step).

Seeds the locations, entities, and sources this installation is known to
have — the two Pico W boards publishing on the legacy ``status/{pin}`` topics
and the simulator device used for development and tests. ``ON CONFLICT DO
NOTHING`` preserves any operator edits made after the first boot.

Note on the legacy topics: both Picos publish ``status/16`` in firmware (a
known collision documented in DISCOVERY.md). Ownership here assigns
``status/16`` to the fan and 17–19 to the pump; the collision is only truly
fixable in firmware, which is out of scope per the owner's decision to use
simulated hardware for now.
"""

from __future__ import annotations

from typing import Any

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
        "metadata": {"legacy": "pin_status"},
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
