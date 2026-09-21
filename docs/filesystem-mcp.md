# Filesystem MCP Support

Butler can attach a general local-filesystem toolset instead of a Minecraft-specific one-off workflow.

When `BUTLER_FILESYSTEM_ROOTS` is configured, Butler appends two complementary MCP servers:

- the official `@modelcontextprotocol/server-filesystem` server for directory listing, metadata, reads, and optional writes
- a Butler-owned diagnostics server for shallow tree summaries, recent-file inspection, root-scoped ripgrep search, and text diffs

## Configuration

Add one or more allowed roots to `.env`:

```env
BUTLER_FILESYSTEM_ROOTS=["/Users/you/projects","/Volumes/shared/reference"]
BUTLER_FILESYSTEM_ALLOW_WRITES=0
```

Notes:

- `BUTLER_FILESYSTEM_ROOTS` accepts either:
  - a JSON array of paths
  - a single JSON string path
  - a plain platform-separated string, though JSON is preferred
- Relative paths are resolved relative to the Butler repo root.
- Butler prefixes both filesystem toolsets with `fs_` by default.
- If multiple roots are configured, the diagnostics tools accept an optional `root` argument so the agent can target a specific configured root.

## Safety Defaults

- The official filesystem server is limited to the configured roots only.
- The Butler diagnostics server enforces the same root scope.
- Butler hides write-capable filesystem tools by default.
- To expose writes, set:

```env
BUTLER_FILESYSTEM_ALLOW_WRITES=1
```

Even with writes exposed, Butler's prompt guidance still prefers explaining evidence and showing diffs before modifying files.

## What This Enables

Once configured, Butler can use the combined filesystem MCP toolset for:

- listing directories
- building directory trees
- finding recent files
- reading text files
- reading media files
- getting file metadata
- searching for files by name/pattern
- searching file contents with ripgrep
- diffing text files
- editing/writing/moving files when writes are enabled

## Verification

Run:

```bash
.venv-main/bin/python tools/verify_filesystem_mcp_setup.py
```

The smoke check verifies:

1. Filesystem MCP tools are visible.
2. The configured root can be listed.
3. The Butler diagnostics search tools are visible and can search inside the root.
4. Access outside the configured roots is rejected.

## Interaction Style

When using the general filesystem server, Butler should still:

- prefer shallow inspection before deep reads
- use absolute paths when helpful
- avoid dumping large files blindly
- explain risky edits before making them
