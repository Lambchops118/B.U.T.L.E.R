"""Awareness backend entrypoint.

    python -m butler.awareness            # serve the internal API (default)
    python -m butler.awareness serve
    python -m butler.awareness migrate    # alembic upgrade head
    python -m butler.awareness check      # component health to stdout; exit code
    python -m butler.awareness retention [--execute]   # dry-run plan by default
    python -m butler.awareness consolidate             # memory consolidation pass
    python -m butler.awareness backup [--verify]       # pg_dump + prune (+ restore check)

Check exit codes: 0 healthy, 1 degraded, 2 unavailable, 3 configuration error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from butler.awareness.config import AwarenessSettings, SettingsError, load_settings


def _configure_windows_event_loop_policy() -> None:
    """Use the selector loop required by aiomqtt on Windows.

    Python's default Windows proactor loop does not implement add_reader or
    add_writer, which aiomqtt uses for socket readiness. Configure the policy
    before Uvicorn creates the awareness server's event loop.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _uvicorn_loop_factory():
    """Return an aiomqtt-compatible loop factory for Uvicorn on Windows."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop
    return "auto"


def _cmd_serve(settings: AwarenessSettings) -> int:
    import uvicorn

    from butler.awareness.api.app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        loop=_uvicorn_loop_factory(),
        log_config=None,
    )
    return 0


def _cmd_migrate(settings: AwarenessSettings) -> int:
    from butler.awareness.db.migrate import expected_head_revision, upgrade_to_head

    upgrade_to_head(settings.database_url)
    print(f"database migrated to head revision: {expected_head_revision()}")
    return 0


def _cmd_check(settings: AwarenessSettings) -> int:
    from butler.awareness.db.session import build_engine
    from butler.awareness.health.service import DEGRADED, HEALTHY, HealthService

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
    from butler.awareness.db.session import build_engine
    from butler.awareness.retention.service import RetentionService

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
    from butler.awareness.db.session import build_engine
    from butler.awareness.memory.service import MemoryService

    async def _run() -> dict:
        engine = build_engine(settings)
        try:
            return await MemoryService(engine, settings).consolidate()
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(_run()), indent=2, default=str))
    return 0


def _cmd_backup(settings: AwarenessSettings, verify: bool) -> int:
    from butler.awareness import backup as backup_module

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
    _configure_windows_event_loop_policy()

    parser = argparse.ArgumentParser(prog="butler.awareness", description=__doc__)
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
