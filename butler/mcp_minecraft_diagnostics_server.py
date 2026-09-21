from __future__ import annotations

from butler.mcp_servers.base import create_server
from butler.minecraft_diagnostics import MinecraftDiagnostics


def create_minecraft_diagnostics_server():
    diagnostics = MinecraftDiagnostics.from_env()
    server = create_server("minecraft-diagnostics")

    @server.tool()
    def summarize_server_layout(
        relative_path: str = ".",
        max_depth: int = 2,
        max_entries_per_dir: int = 30,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
    ) -> dict:
        """Summarize the Minecraft server directory tree without reading every file."""
        return diagnostics.summarize_server_layout(
            relative_path=relative_path,
            max_depth=max_depth,
            max_entries_per_dir=max_entries_per_dir,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
        )

    @server.tool()
    def find_recent_logs(max_results: int = 10) -> dict:
        """List the newest log, crash-report, and script-related files under the server root."""
        return diagnostics.find_recent_logs(max_results=max_results)

    @server.tool()
    def list_files(
        relative_path: str = ".",
        globs: list[str] | None = None,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
        max_results: int = 200,
    ) -> dict:
        """List files under the configured Minecraft server root using ripgrep's fast file walker."""
        return diagnostics.list_files(
            relative_path=relative_path,
            globs=globs,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
            max_results=max_results,
        )

    @server.tool()
    def search_text(
        pattern: str,
        relative_path: str = ".",
        fixed_strings: bool = False,
        case_sensitive: bool | None = None,
        context_lines: int = 2,
        max_results: int = 200,
        include_hidden: bool = False,
        globs: list[str] | None = None,
        extra_excludes: list[str] | None = None,
    ) -> dict:
        """Search text within the configured Minecraft server root without scanning binaries."""
        return diagnostics.search_text(
            pattern=pattern,
            relative_path=relative_path,
            fixed_strings=fixed_strings,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            max_results=max_results,
            include_hidden=include_hidden,
            globs=globs,
            extra_excludes=extra_excludes,
        )

    @server.tool()
    def compare_text_files(
        path_a: str,
        path_b: str,
        context_lines: int = 3,
        max_diff_lines: int = 400,
    ) -> dict:
        """Diff two text files under the configured Minecraft server root."""
        return diagnostics.compare_text_files(
            path_a=path_a,
            path_b=path_b,
            context_lines=context_lines,
            max_diff_lines=max_diff_lines,
        )

    @server.tool()
    def list_mod_jars(max_results: int = 300) -> dict:
        """List mod jar filenames from the mods directory without opening the jar contents."""
        return diagnostics.list_mod_jars(max_results=max_results)

    @server.tool()
    def detect_duplicate_mods(max_results: int = 50) -> dict:
        """Heuristically flag duplicate mod jars by normalized filename."""
        return diagnostics.detect_duplicate_mods(max_results=max_results)

    return server


def main() -> int:
    server = create_minecraft_diagnostics_server()
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
