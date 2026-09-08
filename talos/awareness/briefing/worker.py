"""Clock/transition polling and the isolated briefing outbox handler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from talos.awareness.alerts.service import parse_quiet_hours, quiet_hours_deferral
from talos.awareness.briefing.feedback import apply_preferences, preferences
from talos.awareness.briefing.selection import select
from talos.awareness.briefing.service import BriefingStore, unavailable_ids
from talos.awareness.briefing.speech import VERSION as SPEECH_VERSION, candidate_text, render_batch
from talos.awareness.context.briefing import BriefingAssembler
from talos.awareness.logging_utils import get_logger
from talos.awareness.notifications.base import DeliveryResult, NotificationContent
from talos.awareness.outbox.worker import OutboxDeferred

logger = get_logger("talos.awareness.briefing")


class BriefingWorker:
    def __init__(self, engine, settings):
        self.store = BriefingStore(engine, settings)
        self.settings = settings
        self.state, self.last_error = "stopped", None
        self.queued = 0

    def status(self):
        return {"state": self.state, "last_error": self.last_error, "queued": self.queued,
                "enabled": self.settings.briefing_enabled}

    async def run(self, stop):
        self.state = "running" if self.settings.briefing_enabled else "disabled"
        try:
            while not stop.is_set():
                try:
                    self.queued += await self.store.enqueue_due(datetime.now(timezone.utc))
                    self.last_error = None
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    logger.error("briefing moment poll failed: %s", self.last_error)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.briefing_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.state = "stopped"


class BriefingHandler:
    def __init__(self, engine, settings, adapters, *, model=None):
        self.engine, self.settings, self.adapters, self.model = engine, settings, adapters, model
        self.store = BriefingStore(engine, settings)

    async def __call__(self, claimed):
        try:
            async with self.store.serialized():
                await self._process(claimed["key"])
        except OutboxDeferred:
            raise
        except Exception as exc:
            # Outbox persists this sanitized evidence and retries with its bounds.
            raise RuntimeError(f"briefing failed: {type(exc).__name__}") from exc

    async def _process(self, key):
        payload = await self.store.load(key)  # retries reuse the durable selection
        if payload["state"] in {"delivered", "silent"}:
            return
        if not self.settings.briefing_enabled:
            from datetime import timedelta
            raise OutboxDeferred(datetime.now(timezone.utc)+timedelta(minutes=5))
        if payload["state"] == "new":
            assembled = await BriefingAssembler(self.engine, self.settings).build(payload["kind"])
            speakable = [c for c in assembled["candidates"] if candidate_text(c)]
            chosen, audit = await select(speakable, self.settings, model=self.model)
            audit["speech_filtered_ids"] = [c["item_id"] for c in assembled["candidates"] if not candidate_text(c)]
            payload.update(state="prepared" if chosen else "silent", remaining=chosen,
                           window=assembled["window"], selection=audit,
                           assembly_audit={k: assembled[k] for k in ("audit", "queries", "truncated", "feedback_audit", "unavailable_ids")})
            await self.store.save(payload)
        if payload["state"] == "silent":
            return
        now = datetime.now(timezone.utc)
        async with self.engine.connect() as connection:
            remaining = payload["remaining"]
            unavailable = await unavailable_ids(connection, [c["item_id"] for c in remaining])
            remaining = [c for c in remaining if c["item_id"] not in unavailable]
            remaining, feedback_audit = apply_preferences(remaining, await preferences(connection, remaining, now))
            remaining = [c for c in remaining if candidate_text(c)]
        payload["remaining"] = remaining
        payload["selection"]["delivery_recheck"] = {"unavailable_ids": sorted(unavailable), "feedback": feedback_audit}
        if not remaining:
            payload["state"] = "silent"
            await self.store.save(payload)
            return
        deferred = quiet_hours_deferral(now, parse_quiet_hours(self.settings.quiet_hours))
        eligible = [c for c in remaining if not deferred or c["priority"] == 1]
        if not eligible:
            await self.store.save(payload)
            raise OutboxDeferred(deferred)
        batch = eligible[:self.settings.briefing_max_items]
        batch_ids = {c["item_id"] for c in batch}
        after = [c for c in remaining if c["item_id"] not in batch_ids]
        payload["selection"]["speech_version"] = SPEECH_VERSION
        # Diagnostic candidate text stays in provenance, never in speech.
        content = NotificationContent(title=f"{payload['kind'].capitalize()} briefing",
            body=render_batch(batch, kind=payload["kind"], part=payload.get("part", 0)),
            severity="critical" if any(c["priority"] == 1 for c in batch) else "notice")
        channel = self.settings.briefing_channel
        adapter = self.adapters.get(channel)
        # Don't mark speech delivered merely because a log fallback succeeded.
        try:
            result = await adapter.send(content) if adapter else DeliveryResult(False, "channel unavailable")
        except Exception:
            result = DeliveryResult(False, "adapter error")
        await self.store.record(payload, batch, after, result, channel, content=content)
        if not result.confirmed:
            raise RuntimeError("briefing adapter did not confirm")
