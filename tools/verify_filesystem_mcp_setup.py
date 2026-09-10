from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.mcp_client.client import (
    LocalMcpClient,
    _load_mcp_server_configs,
    _resolve_allowed_root_paths,
)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    roots = _resolve_allowed_root_paths(__import__("os").getenv("BUTLER_FILESYSTEM_ROOTS", ""))
    _expect(bool(roots), "BUTLER_FILESYSTEM_ROOTS is not configured")

    configs = _load_mcp_server_configs()
    client = LocalMcpClient(configs)
    try:
        tools = client.list_tools(refresh=True)
        tool_names = {tool["name"] for tool in tools}

        _expect("fs_list_directory" in tool_names, "fs_list_directory tool is not visible")
        _expect("fs_get_file_info" in tool_names, "fs_get_file_info tool is not visible")
        _expect("fs_search_text" in tool_names, "fs_search_text tool is not visible")

        root = roots[0]
        listing = client.call_tool("fs_list_directory", {"path": str(root)})
        _expect(listing.strip() != "", "filesystem MCP returned an empty root listing")

        marker = root / ".butler-fs-verify.txt"
        search_preview_source = "existing-files fallback"
        try:
            marker.write_text("filesystem verify marker\n", encoding="utf-8")
            search_pattern = "filesystem verify marker"
            search_preview_source = str(marker)
        except OSError:
            search_pattern = "the"

        try:
            search_result = client.call_tool(
                "fs_search_text",
                {
                    "pattern": search_pattern,
                    "root": str(root),
                    "relative_path": ".",
                    "max_results": 5,
                },
            )
        finally:
            marker.unlink(missing_ok=True)
        _expect(search_result.strip() != "", "filesystem diagnostics search returned an empty response")

        try:
            client.call_tool("fs_get_file_info", {"path": str(root.parent)})
        except Exception:
            outside_rejected = True
        else:
            outside_rejected = False
        _expect(outside_rejected, "filesystem MCP unexpectedly accessed a path outside the configured roots")

        summary = {
            "roots": [str(root_path) for root_path in roots],
            "tool_count": len(tool_names),
            "filesystem_root_listing_preview": listing.splitlines()[:10],
            "filesystem_search_source": search_preview_source,
            "filesystem_search_preview": search_result.splitlines()[:10],
        }
        print(json.dumps(summary, indent=2))
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main())
