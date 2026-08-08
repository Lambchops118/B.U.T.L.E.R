from __future__ import annotations

import argparse

from talos.mcp_servers.aggregate import create_aggregate_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talos.mcp_server")
    parser.add_argument(
        "--disable-provider",
        action="append",
        default=[],
        metavar="NAME[,NAME...]",
        help="Provider group(s) to leave unregistered (e.g. home_automation).",
    )
    args = parser.parse_args(argv)

    disabled: list[str] = []
    for raw in args.disable_provider:
        disabled.extend(part for part in str(raw).split(",") if part.strip())

    server = create_aggregate_server(disabled_providers=disabled)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
