"""Phase 6 integration: validated memory, supersession, hybrid search, outage.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

Ollama is deliberately NOT required: the embedding path exercises the real
outage behavior (queued outbox work, full-text search keeps working) and the
vector component is tested by injecting an embedding directly.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class MemoryIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_mem_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            ollama_host="http://127.0.0.1:1",  # guaranteed embedding outage
        )
        from urllib.parse import quote_plus

        self.admin_dsn = (
            f"postgresql://{quote_plus(base.db_user)}:"
            f"{quote_plus(base.db_password.get_secret_value())}"
            f"@{base.db_host}:{base.db_port}/postgres"
        )
        if not asyncio.run(self._create_scratch_database()):
            self.skipTest("awareness Postgres is not reachable (start docker compose)")

        from talos.awareness.db.migrate import upgrade_to_head

        upgrade_to_head(self.settings.database_url)

    async def _create_scratch_database(self) -> bool:
        import asyncpg

        try:
            connection = await asyncpg.connect(self.admin_dsn, timeout=3)
        except Exception:
            return False
        try:
            await connection.execute(f'CREATE DATABASE "{self.scratch_name}"')
        finally:
            await connection.close()
        return True

    def tearDown(self) -> None:
        if not hasattr(self, "scratch_name"):
            return

        async def _drop() -> None:
            import asyncpg

            try:
                connection = await asyncpg.connect(self.admin_dsn, timeout=3)
            except Exception:
                return
            try:
                await connection.execute(
                    f'DROP DATABASE IF EXISTS "{self.scratch_name}" WITH (FORCE)'
                )
            finally:
                await connection.close()

        asyncio.run(_drop())

    def test_memory_lifecycle(self) -> None:
        asyncio.run(self._run_flow())

    async def _run_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.session import build_engine
        from talos.awareness.memory.service import (
            CandidateProposal,
            EvidenceRef,
            MemoryService,
        )
        from talos.awareness.outbox.worker import OutboxWorker
        from talos.awareness.memory.embeddings import EmbeddingHandler
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            service = MemoryService(engine, self.settings)

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            # --- deterministic explicit preference ---------------------------
            first = await service.write_deterministic(
                statement="favorite_color: blue",
                scope="user",
                structured_content={"key": "favorite_color", "value": "blue"},
                evidence=[
                    EvidenceRef(kind="user_confirmation", reference="session:test")
                ],
            )
            self.assertTrue(first["created"])
            rows = await query(
                "SELECT status, sensitivity, confidence FROM memories "
                "WHERE memory_id = :m", m=first["memory_id"]
            )
            self.assertEqual(rows[0].status, "active")
            provenance = await query(
                "SELECT kind FROM memory_provenance WHERE memory_id = :m",
                m=first["memory_id"],
            )
            self.assertEqual(provenance[0].kind, "user_confirmation")

            # duplicate re-extraction is idempotent
            again = await service.write_deterministic(
                statement="favorite_color: blue",
                scope="user",
                structured_content={"key": "favorite_color", "value": "blue"},
            )
            self.assertFalse(again["created"])
            self.assertEqual(again["memory_id"], first["memory_id"])

            # --- embedding outage: queued, retried, full-text still works ----
            outbox = await query(
                "SELECT status FROM outbox WHERE work_type = 'embedding'"
            )
            self.assertEqual(len(outbox), 1)
            worker = OutboxWorker(
                engine,
                self.settings,
                {"embedding": EmbeddingHandler(engine, self.settings)},
            )
            await worker.run_once()  # fails against 127.0.0.1:1 → backoff
            outbox = await query(
                "SELECT status, attempt_count FROM outbox WHERE work_type = 'embedding'"
            )
            self.assertEqual(outbox[0].status, "pending")  # queued for retry
            self.assertEqual(outbox[0].attempt_count, 1)
            found = await service.search("favorite color")
            self.assertEqual(len(found["results"]), 1)  # full-text unaffected
            self.assertFalse(found["vector_used"])
            self.assertGreater(
                found["results"][0]["component_scores"]["full_text"], 0
            )

            # --- changed preference: supersession, never overwrite -----------
            changed = await service.write_deterministic(
                statement="favorite_color: green",
                scope="user",
                structured_content={"key": "favorite_color", "value": "green"},
                evidence=[
                    EvidenceRef(kind="user_confirmation", reference="session:test")
                ],
            )
            self.assertTrue(changed["created"])
            self.assertEqual(changed["superseded"], first["memory_id"])
            old = await query(
                "SELECT status, valid_to, superseded_at FROM memories WHERE memory_id = :m",
                m=first["memory_id"],
            )
            self.assertEqual(old[0].status, "superseded")
            self.assertIsNotNone(old[0].valid_to)
            links = await query(
                "SELECT relation FROM memory_relationships "
                "WHERE from_memory_id = :new AND to_memory_id = :old",
                new=changed["memory_id"], old=first["memory_id"],
            )
            self.assertEqual(links[0].relation, "supersedes")
            found = await service.search("favorite color")
            statements = [r["statement"] for r in found["results"]]
            self.assertIn("favorite_color: green", statements)
            self.assertNotIn("favorite_color: blue", statements)  # superseded excluded

            # --- candidate path: unsupported claim rejected with audit -------
            rejected = await service.propose_candidate(
                CandidateProposal(
                    statement="The owner has a pet dragon.",
                    evidence=[
                        EvidenceRef(kind="event", reference=str(uuid.uuid4()))
                    ],
                    proposing_model="test-model",
                )
            )
            self.assertFalse(rejected["accepted"])
            self.assertEqual(rejected["decision"], "rejected")
            self.assertIn("does not exist", rejected["reason"])
            audit = await query(
                "SELECT status, metadata FROM memories WHERE memory_id = :m",
                m=rejected["memory_id"],
            )
            self.assertEqual(audit[0].status, "rejected")
            self.assertEqual(audit[0].metadata["decision"]["model"], "test-model")

            # --- candidate with real evidence accepted ------------------------
            accepted = await service.propose_candidate(
                CandidateProposal(
                    statement="The user prefers metric units.",
                    scope="user",
                    structured_content={"key": "units", "value": "metric"},
                    evidence=[
                        EvidenceRef(kind="user_confirmation", reference="session:t2")
                    ],
                    proposing_model="test-model",
                )
            )
            self.assertTrue(accepted["accepted"])

            # --- inconclusive conflict: both kept, related --------------------
            conflicting = await service.propose_candidate(
                CandidateProposal(
                    statement="The user seems to prefer imperial units.",
                    scope="user",
                    structured_content={"key": "units", "value": "imperial"},
                    evidence=[
                        EvidenceRef(kind="source", reference="weak-inference")
                    ],
                    proposing_model="test-model",
                )
            )
            self.assertTrue(conflicting["accepted"])
            self.assertEqual(conflicting["conflicts_with"], accepted["memory_id"])
            both = await query(
                "SELECT count(*) AS n FROM memories WHERE status = 'active' "
                "AND structured_content->>'key' = 'units'"
            )
            self.assertEqual(both[0].n, 2)  # no invented certainty

            # --- vector component with an injected embedding -------------------
            fake = [0.1] * 768
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "INSERT INTO memory_embeddings "
                        "(memory_id, embedding, model, dimension, content_hash) "
                        "VALUES (:m, :e, 'test', 768, 'x')"
                    ),
                    {"m": accepted["memory_id"], "e": str(fake)},
                )
            found = await service.search(
                "units preference", query_embedding=fake
            )
            self.assertTrue(found["vector_used"])
            top = found["results"][0]
            self.assertAlmostEqual(
                top["component_scores"]["vector"], 1.0, places=5
            )

            # --- sensitivity filtering ----------------------------------------
            await service.write_deterministic(
                statement="secret: the vault code is hidden",
                scope="user",
                sensitivity="restricted",
                structured_content={"key": "vault", "value": "hidden"},
            )
            found = await service.search("vault code", max_sensitivity="personal")
            self.assertEqual(found["results"], [])
            found = await service.search("vault code", max_sensitivity="restricted")
            self.assertEqual(len(found["results"]), 1)

            # --- access counters do not touch validity -------------------------
            counters = await query(
                "SELECT access_count, valid_to FROM memories WHERE memory_id = :m",
                m=changed["memory_id"],
            )
            self.assertGreaterEqual(counters[0].access_count, 1)
            self.assertIsNone(counters[0].valid_to)

            # --- audited deletion ----------------------------------------------
            from uuid import UUID

            self.assertTrue(
                await service.delete(UUID(conflicting["memory_id"]), reason="test cleanup")
            )
            gone = await query(
                "SELECT status, metadata FROM memories WHERE memory_id = :m",
                m=conflicting["memory_id"],
            )
            self.assertEqual(gone[0].status, "deleted")
            self.assertEqual(gone[0].metadata["deletion"]["reason"], "test cleanup")
        finally:
            await engine.dispose()

    def test_overflow_episode_linkage(self) -> None:
        asyncio.run(self._run_episode_flow())

    async def _run_episode_flow(self) -> None:
        import json as jsonlib

        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.memory.service import MemoryService
        from talos.awareness.outbox.worker import OutboxWorker
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
        from talos.awareness.rules.engine import RuleEngine
        from talos.awareness.rules.policy import load_policy

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            pipeline = IngestionPipeline(
                engine,
                sources,
                self.settings,
                rule_engine=RuleEngine(load_policy(), AlertService(self.settings)),
            )
            boot = f"boot-{uuid.uuid4().hex[:6]}"

            async def overflow(value: bool, sequence: int) -> None:
                result = await pipeline.handle(
                    InboundMessage(
                        topic="home/sim/greenhouse/event",
                        payload=jsonlib.dumps(
                            {
                                "event_id": str(uuid.uuid4()),
                                "event_type": "plant.overflow.detected",
                                "severity": "critical",
                                "sequence": sequence,
                                "boot_id": boot,
                                "payload": {"overflow": value, "zone": 1},
                            }
                        ).encode(),
                    )
                )
                assert result == "accepted", result

            await overflow(True, 1)
            await overflow(False, 2)  # deterministic resolution queues episode

            memory_service = MemoryService(engine, self.settings)

            async def episode_handler(payload: dict) -> None:
                from uuid import UUID

                await memory_service.create_episode_from_alert(UUID(payload["alert_id"]))

            worker = OutboxWorker(
                engine, self.settings, {"memory_episode": episode_handler}
            )
            processed = 0
            for _ in range(3):
                processed += await worker.run_once()
            async with engine.connect() as connection:
                episodes = (
                    await connection.execute(
                        sa.text(
                            "SELECT memory_id, statement FROM memories "
                            "WHERE memory_type = 'episodic' AND status = 'active'"
                        )
                    )
                ).all()
                self.assertEqual(len(episodes), 1)
                self.assertIn("Incident overflow (critical)", episodes[0].statement)
                linked = (
                    await connection.execute(
                        sa.text(
                            "SELECT kind, count(*) AS n FROM memory_provenance "
                            "WHERE memory_id = :m GROUP BY kind ORDER BY kind"
                        ),
                        {"m": episodes[0].memory_id},
                    )
                ).all()
            kinds = {row.kind: row.n for row in linked}
            self.assertEqual(kinds.get("alert"), 1)
            self.assertGreaterEqual(kinds.get("event", 0), 2)  # evidence linked
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
