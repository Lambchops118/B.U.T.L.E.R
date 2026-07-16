"""Generic outbox worker: claim, execute, retry, dead-letter (C10, Phase 4).

At-least-once semantics with idempotent handlers — never exactly-once
(OUTBOX-004). Claiming uses ``FOR UPDATE SKIP LOCKED`` over bounded batches;
a lock older than the configured stale window is considered abandoned by a
crashed worker and reclaimable. Failures back off exponentially with jitter
and dead-letter after the attempt budget, keeping a sanitized error. Handler
work (network, adapters) runs OUTSIDE the claim transaction.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import OutboxItem
from talos.awareness.ingestion.mqtt_client import backoff_delay
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.outbox")

Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def retry_outbox_item(engine: AsyncEngine, outbox_id: int) -> bool:
    """Manual retry of a failed/dead-lettered item (OUTBOX-003)."""
    async with engine.begin() as connection:
        result = await connection.execute(
            sa.update(OutboxItem)
            .where(
                OutboxItem.outbox_id == outbox_id,
                OutboxItem.status.in_(("failed", "dead_letter")),
            )
            .values(
                status="pending",
                next_attempt_at=None,
                locked_at=None,
                locked_by=None,
                available_at=datetime.now(timezone.utc),
            )
        )
    return bool(result.rowcount)


class OutboxWorker:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: AwarenessSettings,
        handlers: dict[str, Handler],
        *,
        worker_id: str = "awareness-outbox-1",
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._handlers = handlers
        self._worker_id = worker_id
        self._state = "stopped"
        self._last_error: str | None = None
        self._processed = 0
        self._failed = 0

    async def status(self) -> dict[str, Any]:
        backlog = oldest_age = None
        try:
            async with self._engine.connect() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            sa.func.count().label("backlog"),
                            sa.func.min(OutboxItem.available_at).label("oldest"),
                        ).where(OutboxItem.status == "pending")
                    )
                ).one()
            backlog = row.backlog
            if row.oldest is not None:
                oldest_age = (datetime.now(timezone.utc) - row.oldest).total_seconds()
        except Exception:  # health must not crash on DB outage
            pass
        return {
            "state": self._state,
            "handlers": sorted(self._handlers),
            "processed": self._processed,
            "failed": self._failed,
            "backlog": backlog,
            "oldest_pending_age_seconds": oldest_age,
            "last_error": self._last_error,
        }

    async def run(self, stop: asyncio.Event) -> None:
        self._state = "running"
        try:
            while not stop.is_set():
                try:
                    processed = await self.run_once()
                    self._last_error = None
                except Exception as exc:
                    processed = 0
                    self._last_error = str(exc)[:300]
                    logger.exception("outbox pass failed", extra={"component": "outbox"})
                if processed == 0:
                    try:
                        await asyncio.wait_for(
                            stop.wait(),
                            timeout=self._settings.outbox_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
        finally:
            self._state = "stopped"

    async def run_once(self) -> int:
        """Claim and process one bounded batch; returns items processed."""
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self._settings.outbox_stale_lock_seconds)
        async with self._engine.begin() as connection:
            claimable = (
                sa.select(OutboxItem.outbox_id)
                .where(
                    OutboxItem.status == "pending",
                    OutboxItem.available_at <= now,
                    sa.or_(
                        OutboxItem.next_attempt_at.is_(None),
                        OutboxItem.next_attempt_at <= now,
                    ),
                    sa.or_(
                        OutboxItem.locked_at.is_(None),
                        OutboxItem.locked_at < stale_before,
                    ),
                )
                .order_by(OutboxItem.outbox_id)
                .limit(self._settings.outbox_batch_size)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            rows = (
                await connection.execute(
                    sa.update(OutboxItem)
                    .where(OutboxItem.outbox_id.in_(claimable))
                    .values(locked_at=now, locked_by=self._worker_id)
                    .returning(
                        OutboxItem.outbox_id,
                        OutboxItem.work_type,
                        OutboxItem.payload,
                        OutboxItem.attempt_count,
                    )
                )
            ).all()

        for row in rows:
            await self._process(row)
        return len(rows)

    async def _process(self, row: Any) -> None:
        handler = self._handlers.get(row.work_type)
        try:
            if handler is None:
                raise RuntimeError(f"no handler registered for {row.work_type!r}")
            await handler(dict(row.payload or {}))
        except Exception as exc:
            await self._record_failure(row, str(exc))
            return
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.update(OutboxItem)
                .where(OutboxItem.outbox_id == row.outbox_id)
                .values(
                    status="completed",
                    completed_at=datetime.now(timezone.utc),
                    locked_at=None,
                    locked_by=None,
                )
            )
        self._processed += 1

    async def _record_failure(self, row: Any, error: str) -> None:
        self._failed += 1
        attempts = row.attempt_count + 1
        exhausted = attempts >= self._settings.outbox_max_attempts
        values: dict[str, Any] = {
            "attempt_count": attempts,
            "last_error": error[:500],  # sanitized: no payload echo, no secrets
            "locked_at": None,
            "locked_by": None,
        }
        if exhausted:
            values["status"] = "dead_letter"
            logger.error(
                "outbox item %s dead-lettered after %d attempts: %s",
                row.outbox_id,
                attempts,
                error[:200],
                extra={"component": "outbox"},
            )
        else:
            values["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(
                seconds=backoff_delay(attempts)
            )
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.update(OutboxItem)
                .where(OutboxItem.outbox_id == row.outbox_id)
                .values(**values)
            )
