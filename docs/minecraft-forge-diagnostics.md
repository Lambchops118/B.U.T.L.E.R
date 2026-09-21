# Minecraft Forge Diagnostics

This Butler checkout can attach a Minecraft-focused MCP toolset for diagnosing large Forge or modpack server directories without granting broad filesystem access.

This is a specialization on top of Butler's general filesystem support, not the core filesystem design. If you want broad local-machine inspection/search capability, start with `BUTLER_FILESYSTEM_ROOTS`; use this helper when Minecraft-specific heuristics are valuable.

## What Gets Added

When `MINECRAFT_SERVER_DIR` is set, Butler appends two MCP servers at startup:

- `minecraft-filesystem`
  - Uses the official `@modelcontextprotocol/server-filesystem` package.
  - Is scoped to `MINECRAFT_SERVER_DIR` by command-line root arguments.
  - Exposes read-only filesystem tools by default from the agent's perspective.
- `minecraft-search`
  - Uses Butler's local `butler.mcp_minecraft_diagnostics_server`.
  - Wraps `rg` without a shell.
  - Rejects any path that resolves outside `MINECRAFT_SERVER_DIR`.
  - Adds helper tools for layout summaries, recent logs, text search, mod jar listing, duplicate-mod heuristics, and text diffs.

## Why Butler Does Not Use `mcp-ripgrep` Directly

The upstream `mcp-ripgrep` package is useful for general-purpose search, but its current implementation accepts arbitrary caller-supplied paths and shells out to `rg` without a root confinement layer. That is not compatible with the "Minecraft server directory only" safety requirement, so Butler keeps the official filesystem server and replaces the ripgrep surface with a repo-local root-scoped wrapper.

## Prerequisites

- Python 3.10+ Butler environment
- Node.js with `npx`
- ripgrep on PATH

On macOS:

```bash
brew install ripgrep
```

The filesystem server package is typically fetched on demand via:

```bash
npx -y @modelcontextprotocol/server-filesystem --help
```

## `.env` Setup

Add these lines to `.env`:

```env
MINECRAFT_SERVER_DIR=/absolute/path/to/minecraft-server
MINECRAFT_MCP_ALLOW_WRITES=0
```

Optional knobs:

- `MINECRAFT_MCP_MODE=eager|lazy|sidecar_autostart|sidecar_manual`
- `MINECRAFT_MCP_FILESYSTEM_TIMEOUT=120`
- `MINECRAFT_MCP_SEARCH_TIMEOUT=30`
- `MINECRAFT_MCP_RG_TIMEOUT=20`
- `MINECRAFT_MCP_MAX_TEXT_BYTES=1000000`

Restart Butler after any `.env` change.

## Safety Defaults

- Butler scopes filesystem access to `MINECRAFT_SERVER_DIR` only.
- The diagnostics search server enforces the same root and rejects symlink escapes.
- No raw `.jar`, `.class`, `.png`, `.ogg`, `.dat`, `.mca`, `.sqlite`, or `.zip` dumps are performed by the custom diagnostics server.
- The default search/file traversal excludes noisy paths:
  - `libraries/`
  - `versions/`
  - `backups/`
  - `world/region/`
  - `world/DIM*/`
  - `world/entities/`
  - `world/poi/`
  - `.git/`
  - `cache/`
  - `crash-reports/old/`
- Write-capable official filesystem tools stay hidden unless `MINECRAFT_MCP_ALLOW_WRITES=1`.
- Even when writes are enabled, Butler's prompt overlay tells the agent to require explicit confirmation and prefer diffs or dry runs first.

## Verification

Run:

```bash
.venv-main/bin/python tools/verify_minecraft_mcp_setup.py
```

The smoke check attempts to verify:

1. The configured Minecraft root resolves and exists.
2. The filesystem MCP server can list the configured root.
3. Filesystem access outside the root is rejected.
4. The Minecraft search MCP tools are visible.
5. Search works inside the server root.

If the filesystem package is not already available and `npx` cannot fetch it, the script will fail with the relevant startup error.

## Recommended Workflow

Use prompts in this shape:

```text
Diagnose my Forge server. Start with logs/latest.log and the newest crash report. Find the most likely bad config, mod, datapack, or script. Do not modify files; give me ranked suspects with evidence.
```

Expected investigation flow:

1. Summarize the tree at shallow depth.
2. Find the newest logs and crash reports.
3. Search for `ERROR`, `WARN`, `Exception`, `Caused by`, `Failed`, `Missing`, `Registry`, `Mixin`, `ModLoadingException`, and likely mod IDs.
4. Correlate the failure with `mods/`, `config/`, `defaultconfigs/`, `world/serverconfig/`, `kubejs/`, `scripts/`, and `datapacks/`.
5. Produce ranked suspects and safe next steps.

## No Slash Command Surface

This repo does not currently have a custom slash-command registry for Butler prompts, so there is no `/diagnose-minecraft-server` command to register yet. The example prompt above is the intended reusable workflow entrypoint for now.
