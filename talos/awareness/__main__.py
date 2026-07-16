"""Awareness backend entrypoint.

    python -m talos.awareness            # serve the internal API (default)
    python -m talos.awareness serve
    python -m talos.awareness migrate    # alembic upgrade head
    python -m talos.awareness check      # component health to stdout; exit code

Check exit codes: 0 healthy, 1 degraded, 2 unavailable, 3 configuration error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from talos.awareness.config import AwarenessSettings, SettingsError, load_settings


def _cmd_serve(settings: AwarenessSettings) -> int:
    import uvicorn

    from talos.awareness.api.app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )
    return 0


def _cmd_migrate(settings: AwarenessSettings) -> int:
    from talos.awareness.db.migrate import expected_head_revision, upgrade_to_head

    upgrade_to_head(settings.database_url)
    print(f"database migrated to head revision: {expected_head_revision()}")
    return 0


def _cmd_check(settings: AwarenessSettings) -> int:
    from talos.awareness.db.session import build_engine
    from talos.awareness.health.service import DEGRADED, HEALTHY, HealthService

    async def _run() -> dict:
        engine = build_engine(settings)
        try:
            return await HealthService(engine).report()
        finally:
            await engine.dispose()

    report = asyncio.run(_run())
    print(json.dumps(report, indent=2, default=str))
    if report["status"] == HEALTHY:
        return 0
    if report["status"] == DEGRADED:
        return 1
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talos.awareness", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the internal API (default)")
    subparsers.add_parser("migrate", help="apply database migrations to head")
    subparsers.add_parser("check", help="print component health and exit with a status code")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 3

    command = args.command or "serve"
    if command == "serve":
        return _cmd_serve(settings)
    if command == "migrate":
        return _cmd_migrate(settings)
    return _cmd_check(settings)


if __name__ == "__main__":
    raise SystemExit(main())
