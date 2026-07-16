"""Simulator CLI.

    python -m talos.awareness.simulator --scenario suite
    python -m talos.awareness.simulator --scenario overflow --host 127.0.0.1 --port 1885

Defaults target the local TEST broker (127.0.0.1:1885), never the production
Raspberry Pi broker — pass --host explicitly for that.
"""

from __future__ import annotations

import argparse
import asyncio

from talos.awareness.simulator.publisher import (
    SCENARIO_NAMES,
    SimulatedDevice,
    build_scenario,
    publish_messages,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talos.awareness.simulator", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="broker host (default: test broker)")
    parser.add_argument("--port", type=int, default=1885, help="broker port (default: 1885)")
    parser.add_argument(
        "--scenario", default="suite", choices=SCENARIO_NAMES, help="traffic scenario"
    )
    parser.add_argument("--repeat", type=int, default=1, help="repeat the scenario N times")
    args = parser.parse_args(argv)

    device = SimulatedDevice()
    total = 0
    for _ in range(max(1, args.repeat)):
        messages = build_scenario(args.scenario, device)
        total += asyncio.run(
            publish_messages(messages, host=args.host, port=args.port)
        )
    print(f"published {total} message(s) for scenario '{args.scenario}' to {args.host}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
