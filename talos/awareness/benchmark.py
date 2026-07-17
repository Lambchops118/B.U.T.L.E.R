"""Representative ingestion benchmark (OPS-012, Phase 8).

    .venv-awareness/bin/python -m talos.awareness.benchmark [--events N]

Runs N simulated telemetry/heartbeat/state events through the full
deterministic pipeline (validation → dedup/sequence → transactional persist →
state/telemetry/rules effects) against a scratch database, then reports
acceptance counts, throughput, and per-event latency percentiles. No broker
round-trip is included — this measures the processing path the backend owns;
current real traffic is a few messages per day (DISCOVERY.md §9), so the
result establishes headroom, not a production requirement. Critical events
are never shed: every accepted event is fully processed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from datetime import datetime, timezone


async def run_benchmark(total_events: int) -> dict:
    from urllib.parse import quote_plus

    import asyncpg

    from talos.awareness.alerts.service import AlertService
    from talos.awareness.config import AwarenessSettings, load_settings
    from talos.awareness.db.migrate import upgrade_to_head
    from talos.awareness.db.session import build_engine
    from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
    from talos.awareness.registry.bootstrap import seed_registry
    from talos.awareness.registry.sources import SourceRepository
    from talos.awareness.rules.engine import RuleEngine
    from talos.awareness.rules.policy import load_policy

    base = load_settings()
    scratch = f"talos_awareness_bench_{uuid.uuid4().hex[:8]}"
    settings = AwarenessSettings(
        _env_file=None,
        db_password=base.db_password.get_secret_value(),
        db_host=base.db_host,
        db_port=base.db_port,
        db_user=base.db_user,
        db_name=scratch,
    )
    admin_dsn = (
        f"postgresql://{quote_plus(base.db_user)}:"
        f"{quote_plus(base.db_password.get_secret_value())}"
        f"@{base.db_host}:{base.db_port}/postgres"
    )
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{scratch}"')
    await admin.close()
    try:
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        engine = build_engine(settings)
        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            pipeline = IngestionPipeline(
                engine,
                sources,
                settings,
                rule_engine=RuleEngine(load_policy(), AlertService(settings)),
            )
            boot = f"boot-{uuid.uuid4().hex[:6]}"
            latencies: list[float] = []
            dispositions: dict[str, int] = {}
            start = time.perf_counter()
            for index in range(total_events):
                kind = index % 10
                if kind < 6:  # telemetry-dominated mix
                    topic = "home/sim/greenhouse/telemetry/temperature"
                    body = {"value": 70.0 + (index % 50) / 10, "unit": "F"}
                elif kind < 8:
                    topic = "home/sim/greenhouse/heartbeat"
                    body = {}
                else:
                    topic = "home/sim/greenhouse/state"
                    body = {"pump": "on" if index % 2 else "off"}
                payload = json.dumps(
                    {
                        "event_id": str(uuid.uuid4()),
                        "sequence": index + 1,
                        "boot_id": boot,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        **body,
                    }
                ).encode()
                begin = time.perf_counter()
                disposition = await pipeline.handle(
                    InboundMessage(topic=topic, payload=payload)
                )
                latencies.append((time.perf_counter() - begin) * 1000)
                dispositions[disposition] = dispositions.get(disposition, 0) + 1
            elapsed = time.perf_counter() - start
            latencies.sort()
            return {
                "events": total_events,
                "elapsed_seconds": round(elapsed, 3),
                "events_per_second": round(total_events / elapsed, 1),
                "dispositions": dispositions,
                "latency_ms": {
                    "p50": round(statistics.median(latencies), 2),
                    "p95": round(latencies[int(len(latencies) * 0.95) - 1], 2),
                    "max": round(latencies[-1], 2),
                },
                "database": "scratch (dropped afterwards)",
            }
        finally:
            await engine.dispose()
    finally:
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        await admin.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=2000)
    args = parser.parse_args()
    report = asyncio.run(run_benchmark(args.events))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
