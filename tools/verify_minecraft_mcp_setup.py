from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.mcp_client.client import LocalMcpClient, _load_mcp_server_configs
from butler.minecraft_diagnostics import configured_minecraft_root


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    root = configured_minecraft_root()
    configs = _load_mcp_server_configs()
    client = LocalMcpClient(configs)

    try:
        tools = client.list_tools(refresh=True)
        tool_names = {tool["name"] for tool in tools}

        _expect("minecraft_search_text" in tool_names, "minecraft_search_text tool is not visible")
        _expect(
            "minecraft_fs_list_directory" in tool_names,
            "minecraft_fs_list_directory tool is not visible",
        )

        listing = client.call_tool("minecraft_fs_list_directory", {"path": str(root)})
        _expect(listing.strip() != "", "filesystem MCP returned an empty root listing")

        try:
            client.call_tool("minecraft_fs_get_file_info", {"path": str(root.parent)})
        except Exception:
            outside_rejected = True
        else:
            outside_rejected = False
        _expect(outside_rejected, "filesystem MCP unexpectedly accessed a path outside the root")

        search_target = "logs" if (root / "logs").exists() else "."
        search_result = client.call_tool(
            "minecraft_search_text",
            {
                "pattern": "ERROR|Exception|WARN",
                "relative_path": search_target,
                "max_results": 20,
            },
        )

        summary = {
            "root": str(root),
            "tool_count": len(tool_names),
            "search_target": search_target,
            "filesystem_root_listing_preview": listing.splitlines()[:10],
            "search_preview": search_result.splitlines()[:20],
        }
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
