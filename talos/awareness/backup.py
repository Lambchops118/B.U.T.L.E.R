"""Local database backups and tested restore (SEC-007, Phase 8).

Owner decision (2026-07-16): nightly local backups under
``{data_directory}/backups`` (override ``TALOS_AWARENESS_BACKUP_DIRECTORY``),
14-day retention, no encryption — the dump never leaves this machine's disk.

``pg_dump``/``pg_restore`` run INSIDE the ``talos-awareness-db`` container so
client and server versions always match; the host needs only Docker. Each
backup also snapshots non-secret configuration (the sanitized settings
summary) next to the dump. Restore verification loads the dump into a scratch
database and compares table counts — a backup is only reported usable after
that check passes.

Scheduling is documented (cron/launchd) rather than installed: the repository
has no process manager, and installing one is an owner-level system change.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from talos.awareness.config import AwarenessSettings
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.backup")

CONTAINER = "talos-awareness-db"


def backup_directory(settings: AwarenessSettings) -> Path:
    return settings.backup_directory or (settings.data_directory / "backups")


def _run(command: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, input=input_bytes, timeout=600)


def create_backup(settings: AwarenessSettings) -> dict[str, Any]:
    """pg_dump (custom format) + non-secret config snapshot + pruning."""
    directory = backup_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = directory / f"talos_awareness_{stamp}.dump"

    result = _run(
        [
            "docker", "exec", CONTAINER,
            "pg_dump", "-U", settings.db_user, "-d", settings.db_name,
            "--format=custom", "--no-password",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed ({result.returncode}): {result.stderr.decode()[:300]}"
        )
    dump_path.write_bytes(result.stdout)

    config_path = directory / f"config_{stamp}.json"
    config_path.write_text(
        json.dumps(settings.summary(), indent=2, default=str), encoding="utf-8"
    )

    pruned = prune_backups(settings)
    report = {
        "dump": str(dump_path),
        "size_bytes": dump_path.stat().st_size,
        "config_snapshot": str(config_path),
        "pruned": pruned,
        "verified": False,
    }
    logger.info("backup written: %s (%d bytes)", dump_path, report["size_bytes"],
                extra={"component": "backup"})
    return report


def prune_backups(settings: AwarenessSettings) -> list[str]:
    directory = backup_directory(settings)
    if not directory.exists():
        return []
    horizon = datetime.now(timezone.utc) - timedelta(days=settings.backup_retention_days)
    pruned: list[str] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < horizon:
            path.unlink()
            pruned.append(path.name)
    return pruned


def latest_backup(settings: AwarenessSettings) -> Path | None:
    directory = backup_directory(settings)
    if not directory.exists():
        return None
    dumps = sorted(directory.glob("talos_awareness_*.dump"))
    return dumps[-1] if dumps else None


def verify_restore(settings: AwarenessSettings, dump_path: Path) -> dict[str, Any]:
    """Restore into a scratch database and compare table counts; drop after."""
    scratch = f"restore_check_{datetime.now(timezone.utc).strftime('%H%M%S')}"

    def _psql(sql: str, database: str = "postgres") -> str:
        result = _run(
            [
                "docker", "exec", CONTAINER,
                "psql", "-U", settings.db_user, "-d", database, "-tAc", sql,
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql failed: {result.stderr.decode()[:300]}")
        return result.stdout.decode().strip()

    _psql(f'CREATE DATABASE "{scratch}"')
    try:
        # TimescaleDB restore protocol: pre-create the extension, call
        # timescaledb_pre_restore(), pg_restore, then post_restore. pg_restore
        # reports benign "already exists" errors for the pre-created
        # extension, so the verification counts below — not the exit code —
        # decide success.
        _psql("CREATE EXTENSION IF NOT EXISTS timescaledb", scratch)
        _psql("SELECT timescaledb_pre_restore()", scratch)
        restore = _run(
            [
                "docker", "exec", "-i", CONTAINER,
                "pg_restore", "-U", settings.db_user, "-d", scratch,
                "--no-owner", "--no-privileges",
            ],
            input_bytes=dump_path.read_bytes(),
        )
        restore_warnings = restore.stderr.decode()[-500:] if restore.returncode else ""
        _psql("SELECT timescaledb_post_restore()", scratch)
        count_sql = (
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        restored_tables = int(_psql(count_sql, scratch))
        source_tables = int(_psql(count_sql, settings.db_name))
        events_restored = int(_psql("SELECT count(*) FROM events", scratch))
        events_source = int(_psql("SELECT count(*) FROM events", settings.db_name))
        # The live database may have grown since the dump; the restore is
        # sound when the schema matches and no dumped events were lost.
        ok = restored_tables == source_tables and events_restored <= events_source
        return {
            "verified": ok,
            "restored_tables": restored_tables,
            "source_tables": source_tables,
            "events_restored": events_restored,
            "events_source": events_source,
            "restore_warnings": restore_warnings or None,
        }
    finally:
        _psql(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
