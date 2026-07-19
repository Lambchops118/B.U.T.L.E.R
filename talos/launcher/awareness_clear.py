"""Truncate the awareness long-term memory tables.

Run with the awareness virtualenv from the repo root::

    .venv-awareness/Scripts/python.exe -m talos.launcher.awareness_clear

It reuses the awareness backend's own settings and database connection, so it
targets whatever Postgres the rest of the awareness system uses. Only the four
long-term *memory* tables are cleared; presence, state, history, alerts, and the
schema itself are left intact. ``CASCADE`` handles the inter-memory foreign
keys; ``RESTART IDENTITY`` resets the surrogate keys.

This is destructive and irreversible. The launcher only invokes it after an
explicit user confirmation.
"""

from __future__ import annotations

import asyncio
import sys

# Order does not matter with CASCADE, but list children first for clarity.
MEMORY_TABLES = [
    "memory_relationships",
    "memory_provenance",
    "memory_embeddings",
    "memories",
]


async def _run() -> None:
    from sqlalchemy import text

    from talos.awareness.config import load_settings
    from talos.awareness.db.session import build_engine

    settings = load_settings()
    engine = build_engine(settings)
    try:
        statement = (
            "TRUNCATE TABLE "
            + ", ".join(MEMORY_TABLES)
            + " RESTART IDENTITY CASCADE"
        )
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()
    print("cleared awareness memory tables: " + ", ".join(MEMORY_TABLES))


def main() -> int:
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - report any failure to the caller
        print(f"ERROR clearing awareness memory: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
