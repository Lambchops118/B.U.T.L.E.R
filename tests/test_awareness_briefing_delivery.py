"""9B–9D local scratch-database tests. No live notification or Ollama calls."""

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

try:
    import sqlalchemy as sa
    from tests import test_awareness_state_integration as fixture
    from talos.awareness.briefing.feedback import BriefingFeedback, record_feedback
    from talos.awareness.briefing.service import BriefingStore
    from talos.awareness.briefing.worker import BriefingHandler
    from talos.awareness.context.briefing import BriefingAssembler
    from talos.awareness.context.broker import SituationBroker
    from talos.awareness.db.models import Alert, AttentionItem, Event, Memory, NotificationDelivery, OutboxItem, StateTransition
    from talos.awareness.db.session import build_engine
    from talos.awareness.notifications.base import DeliveryResult
    from talos.awareness.outbox.worker import OutboxWorker
    from talos.awareness.registry.bootstrap import seed_registry
except ImportError as exc:
    raise unittest.SkipTest(f"awareness dependencies unavailable: {exc}")


class BriefingDeliveryTest(unittest.TestCase):
    setUp = fixture.StateTelemetryIntegrationTest.setUp
    tearDown = fixture.StateTelemetryIntegrationTest.tearDown
    _create_scratch_database = fixture.StateTelemetryIntegrationTest._create_scratch_database

    async def setup_flow(self):
        settings = self.settings.model_copy(update={"briefing_enabled": True, "briefing_schedule_time": "00:00",
                                                    "briefing_model_enabled": False})
        engine = build_engine(settings)
        await seed_registry(engine)
        return engine, settings, BriefingStore(engine, settings)

    async def enqueue(self, store, key="briefing:test", kind="test"):
        async with store.engine.begin() as connection:
            await store._enqueue(connection, key, {"key": key, "root_key": key, "kind": kind,
                "state": "new", "triggered_at": datetime.now(timezone.utc).isoformat()}, datetime.now(timezone.utc))

    async def event(self, engine):
        event_id = uuid.uuid4()
        async with engine.begin() as connection:
            await connection.execute(sa.insert(Event).values(event_id=event_id, schema_version=1,
                event_type="agent.job.completed", entity_id="talos", source_id="talos_agent",
                received_at=datetime.now(timezone.utc)-timedelta(seconds=1), severity="info", provenance={}))
        return event_id

    def test_moments_are_idempotent_and_arrival_requires_transition(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                now = datetime.now(timezone.utc)
                async with engine.begin() as connection:
                    for status, before in (("stale", True), ("current", True)):
                        await connection.execute(sa.insert(StateTransition).values(entity_id="owner", property_name="present",
                            occurred_at=now-timedelta(seconds=2), from_status=status, to_status="current", reason="fixture",
                            from_value={"value": before}, to_value={"value": True}))
                self.assertEqual(await store.enqueue_due(now), 2)  # morning + one real arrival
                self.assertEqual(await BriefingStore(engine, settings).enqueue_due(now), 0)
                async with engine.connect() as connection:
                    self.assertEqual((await connection.execute(sa.select(sa.func.count()).select_from(OutboxItem))).scalar_one(), 2)
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_failed_delivery_retries_frozen_selection_then_never_repeats(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                attention_id = uuid.uuid4()
                async with engine.begin() as connection:
                    await connection.execute(sa.insert(AttentionItem).values(attention_item_id=attention_id,
                        entity_id="owner", reason="Stored pending task", created_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
                await self.enqueue(store)
                adapter = AsyncMock()
                adapter.send.side_effect = [DeliveryResult(False), DeliveryResult(True)]
                handler = BriefingHandler(engine, settings, {"voice": adapter})
                with self.assertRaises(RuntimeError):
                    await handler({"key": "briefing:test"})
                async with engine.connect() as connection:
                    status = (await connection.execute(sa.select(AttentionItem.delivery_status).where(
                        AttentionItem.attention_item_id == attention_id))).scalar_one()
                self.assertEqual(status, "pending")
                self.assertEqual((await store.load("briefing:test"))["state"], "prepared")
                await handler({"key": "briefing:test"})
                await handler({"key": "briefing:test"})  # crash after handler/ before outbox completion
                self.assertEqual(adapter.send.await_count, 2)
                async with engine.connect() as connection:
                    self.assertEqual((await connection.execute(sa.select(AttentionItem.delivery_status).where(
                        AttentionItem.attention_item_id == attention_id))).scalar_one(), "delivered")
                receipts = await store.recent()
                self.assertEqual([r["status"] for r in receipts], ["delivered", "failed"])
                self.assertEqual(receipts[0]["audit"]["selection"]["selection_mode"], "deterministic_fallback")
                self.assertEqual((await BriefingAssembler(engine, settings).build("arrival"))["candidates"], [])
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_event_receipts_deduplicate_across_kinds(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                event_id = await self.event(engine)
                await self.enqueue(store)
                adapter = AsyncMock(); adapter.send.return_value = DeliveryResult(True)
                await BriefingHandler(engine, settings, {"voice": adapter})({"key": "briefing:test"})
                self.assertEqual(adapter.send.call_args.args[0].body, "A background job completed earlier.")
                receipts = await store.recent()
                self.assertEqual(receipts[0]["audit"]["announcement"], {
                    "title": "Test briefing", "text": "A background job completed earlier.",
                    "playback_confirmed": False})
                snapshot = await SituationBroker(engine, settings).build()
                self.assertIn("A background job completed earlier.", snapshot["text"])
                self.assertIn("playback unconfirmed", snapshot["text"])
                self.assertIn(f"event:{event_id}", snapshot["text"])
                result = await BriefingAssembler(engine, settings).build("arrival")
                self.assertNotIn(f"event:{event_id}", [c["item_id"] for c in result["candidates"]])
                self.assertIn(f"event:{event_id}", result["unavailable_ids"])
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_announcement_context_excludes_failed_silent_old_and_legacy_receipts(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                now = datetime.now(timezone.utc)
                async with engine.begin() as connection:
                    for channel, status, age, text in (
                        ("voice", "failed", 0, "FAILED"),
                        ("gui", "delivered", 0, "GUI_ONLY"),
                        ("voice", "delivered", 25, "EXPIRED"),
                        ("voice", "delivered", 0, None),
                        ("voice", "delivered", 2, "A reminder was queued."),
                    ):
                        await connection.execute(sa.insert(NotificationDelivery).values(
                            channel=channel, status=status, attempted_at=now-timedelta(hours=age),
                            metadata_json={"announcement": {"text": text}} if text else {}))
                snapshot = await SituationBroker(engine, settings).build()
                self.assertIn("A reminder was queued.", snapshot["text"])
                for excluded in ("FAILED", "GUI_ONLY", "EXPIRED"):
                    self.assertNotIn(excluded, snapshot["text"])
                tiny = await SituationBroker(engine, settings).build(budget_tokens=1)
                self.assertNotIn("A reminder was queued.", tiny["text"])
                self.assertTrue(tiny["truncated"])
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_critical_overflow_uses_capped_durable_batches(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                settings = settings.model_copy(update={"briefing_max_items": 1, "briefing_model_enabled": True, "chat_model": "test"})
                async with engine.begin() as connection:
                    for index in range(2):
                        await connection.execute(sa.insert(Alert).values(alert_type="fixture", severity="critical",
                            entity_id="fan", title=f"Critical {index}", last_updated_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
                await self.enqueue(store)
                adapter = AsyncMock(); adapter.send.return_value = DeliveryResult(True)
                model = AsyncMock(return_value={"chosen": []})
                handler = BriefingHandler(engine, settings, {"voice": adapter}, model=model)
                await handler({"key": "briefing:test"})
                await handler({"key": "briefing:test:part:1"})
                self.assertEqual(adapter.send.await_count, 2)
                self.assertEqual(model.await_count, 1)
                receipts = await store.recent()
                self.assertEqual(len(receipts), 2)
                self.assertTrue(all(len(r["audit"]["item_ids"]) == 1 for r in receipts))
                self.assertTrue(all(call.args[0].severity == "critical" for call in adapter.send.call_args_list))
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_quiet_hours_defer_without_failed_attempt_and_recheck_feedback(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                local = datetime.now().astimezone()
                quiet = f"{local:%H:%M}-{local+timedelta(hours=1):%H:%M}"
                settings = settings.model_copy(update={"quiet_hours": quiet})
                await self.event(engine)
                await self.enqueue(store)
                adapter = AsyncMock(); adapter.send.return_value = DeliveryResult(True)
                handler = BriefingHandler(engine, settings, {"voice": adapter})
                worker = OutboxWorker(engine, settings, {"briefing": handler}, work_types=("briefing",))
                self.assertEqual(await worker.run_once(), 1)
                adapter.send.assert_not_called()
                async with engine.connect() as connection:
                    row = (await connection.execute(sa.select(OutboxItem.attempt_count, OutboxItem.available_at))).one()
                    self.assertEqual(row.attempt_count, 0)
                    self.assertGreater(row.available_at, datetime.now(timezone.utc))
                await record_feedback(engine, settings, BriefingFeedback(category="agent_outcome", value="dismiss"))
                handler = BriefingHandler(engine, settings.model_copy(update={"quiet_hours": ""}), {"voice": adapter})
                await handler({"key": "briefing:test"})
                adapter.send.assert_not_called()
                self.assertEqual((await store.load("briefing:test"))["state"], "silent")
                self.assertEqual((await BriefingAssembler(engine, settings).build("arrival"))["candidates"], [])
                await record_feedback(engine, settings, BriefingFeedback(category="agent_outcome", value="interest"))
                result = await BriefingAssembler(engine, settings).build("arrival")
                self.assertEqual(len(result["candidates"]), 1)
                self.assertGreater(result["candidates"][0]["relevance"], 0)
                async with engine.connect() as connection:
                    active = (await connection.execute(sa.select(Memory).where(Memory.status == "active"))).all()
                    self.assertEqual(len(active), 1)
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_empty_assembly_is_silent_and_worker_claims_are_isolated(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                await self.enqueue(store)
                async with engine.begin() as connection:
                    await connection.execute(sa.insert(OutboxItem).values(work_type="notification", payload={}))
                ordinary = AsyncMock()
                self.assertEqual(await OutboxWorker(engine, settings, {"notification": ordinary},
                    exclude_work_types=("briefing",)).run_once(), 1)
                ordinary.assert_awaited_once()
                adapter, model = AsyncMock(), AsyncMock()
                handler = BriefingHandler(engine, settings, {"voice": adapter}, model=model)
                self.assertEqual(await OutboxWorker(engine, settings, {"briefing": handler},
                    work_types=("briefing",)).run_once(), 1)
                adapter.send.assert_not_called()
                model.assert_not_called()
                self.assertEqual((await store.load("briefing:test"))["state"], "silent")
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_api_auth_strict_feedback_and_no_remote_trigger(self):
        async def flow():
            from fastapi import FastAPI
            from pydantic import SecretStr
            import httpx
            from talos.awareness.api.routes.briefing import router
            from talos.awareness.api.app import create_app
            engine, settings, store = await self.setup_flow()
            try:
                settings = settings.model_copy(update={"api_token": SecretStr("test-briefing-token")})
                app = FastAPI()
                app.state.engine, app.state.settings = engine, settings
                app.include_router(router)
                headers = {"Authorization": "Bearer test-briefing-token"}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                    self.assertEqual((await client.post("/briefings/feedback", json={"category": "novelty", "value": "dismiss"})).status_code, 401)
                    self.assertEqual((await client.post("/briefings/feedback", headers=headers,
                        json={"category": "novelty", "value": "dismiss", "trigger": True})).status_code, 422)
                    self.assertEqual((await client.post("/briefings/feedback", headers=headers,
                        json={"category": "novelty", "value": "dismiss"})).status_code, 200)
                    self.assertEqual((await client.get("/briefings", headers=headers)).json(), {"deliveries": []})
                    self.assertEqual((await client.post("/briefings/trigger", headers=headers)).status_code, 404)
                paths = create_app(settings).openapi()["paths"]
                self.assertIn("/briefings/feedback", paths)
                self.assertNotIn("/briefings/trigger", paths)
            finally:
                await engine.dispose()
        asyncio.run(flow())

    def test_cancelled_model_releases_briefing_session_lock(self):
        async def flow():
            engine, settings, store = await self.setup_flow()
            try:
                settings = settings.model_copy(update={"briefing_model_enabled": True, "chat_model": "test"})
                await self.event(engine)
                await self.enqueue(store)
                started = asyncio.Event()
                async def blocked(prompt):
                    started.set()
                    await asyncio.Event().wait()
                handler = BriefingHandler(engine, settings, {}, model=blocked)
                task = asyncio.create_task(handler({"key": "briefing:test"}))
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                async with store.serialized():
                    self.assertEqual((await store.load("briefing:test"))["state"], "new")
            finally:
                await engine.dispose()
        asyncio.run(flow())
