"""Awareness backend entrypoint.

    python -m talos.awareness            # serve the internal API (default)
    python -m talos.awareness serve
    python -m talos.awareness migrate    # alembic upgrade head
    python -m talos.awareness check      # component health to stdout; exit code
    python -m talos.awareness retention [--execute]   # dry-run plan by default
    python -m talos.awareness consolidate             # memory consolidation pass
    python -m talos.awareness backup [--verify]       # pg_dump + prune (+ restore check)

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


def _cmd_retention(settings: AwarenessSettings, execute: bool) -> int:
    from talos.awareness.db.session import build_engine
    from talos.awareness.retention.service import RetentionService

    async def _run() -> dict:
        engine = build_engine(settings)
        try:
            service = RetentionService(engine, settings)
            return await (service.execute() if execute else service.plan())
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(_run()), indent=2, default=str))
    return 0


def _cmd_consolidate(settings: AwarenessSettings) -> int:
    from talos.awareness.db.session import build_engine
    from talos.awareness.memory.service import MemoryService

    async def _run() -> dict:
        engine = build_engine(settings)
        try:
            return await MemoryService(engine, settings).consolidate()
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(_run()), indent=2, default=str))
    return 0


def _cmd_backup(settings: AwarenessSettings, verify: bool) -> int:
    from talos.awareness import backup as backup_module

    report = backup_module.create_backup(settings)
    if verify:
        from pathlib import Path

        verification = backup_module.verify_restore(settings, Path(report["dump"]))
        report.update(verification)
        print(json.dumps(report, indent=2, default=str))
        return 0 if verification["verified"] else 1
    print(json.dumps(report, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talos.awareness", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="run the internal API (default)")
    subparsers.add_parser("migrate", help="apply database migrations to head")
    subparsers.add_parser("check", help="print component health and exit with a status code")
    retention_parser = subparsers.add_parser(
        "retention", help="retention dry-run plan (default) or --execute"
    )
    retention_parser.add_argument(
        "--execute", action="store_true", help="delete per policy (default is dry run)"
    )
    subparsers.add_parser("consolidate", help="run one memory consolidation pass")
    backup_parser = subparsers.add_parser(
        "backup", help="pg_dump to the backup directory and prune old backups"
    )
    backup_parser.add_argument(
        "--verify", action="store_true", help="restore into a scratch DB and compare"
    )
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
    if command == "retention":
        return _cmd_retention(settings, args.execute)
    if command == "consolidate":
        return _cmd_consolidate(settings)
    if command == "backup":
        return _cmd_backup(settings, args.verify)
    return _cmd_check(settings)


if __name__ == "__main__":
    raise SystemExit(main())
