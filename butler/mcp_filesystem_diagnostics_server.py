from __future__ import annotations

from butler.filesystem_diagnostics import MultiRootFilesystemDiagnostics
from butler.mcp_servers.base import create_server


def create_filesystem_diagnostics_server():
    diagnostics = MultiRootFilesystemDiagnostics.from_env()
    server = create_server("filesystem-diagnostics")

    @server.tool()
    def describe_roots() -> dict:
        """List the configured filesystem roots available to the diagnostics helper."""
        return diagnostics.describe_roots()

    @server.tool()
    def summarize_directory(
        root: str | None = None,
        relative_path: str = ".",
        max_depth: int = 2,
        max_entries_per_dir: int = 30,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
    ) -> dict:
        """Summarize a directory tree inside the configured filesystem roots."""
        return diagnostics.summarize_directory(
            root=root,
            relative_path=relative_path,
            max_depth=max_depth,
            max_entries_per_dir=max_entries_per_dir,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
        )

    @server.tool()
    def find_recent_files(
        root: str | None = None,
        relative_path: str = ".",
        globs: list[str] | None = None,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
        max_results: int = 20,
    ) -> dict:
        """List the most recently modified files inside the configured filesystem roots."""
        return diagnostics.find_recent_files(
            root=root,
            relative_path=relative_path,
            globs=globs,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
            max_results=max_results,
        )

    @server.tool()
    def list_files(
        root: str | None = None,
        relative_path: str = ".",
        globs: list[str] | None = None,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
        max_results: int = 200,
    ) -> dict:
        """List files quickly with ripgrep's filesystem walker inside the configured roots."""
        return diagnostics.list_files(
            root=root,
            relative_path=relative_path,
            globs=globs,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
            max_results=max_results,
        )

    @server.tool()
    def search_text(
        pattern: str,
        root: str | None = None,
        relative_path: str = ".",
        fixed_strings: bool = False,
        case_sensitive: bool | None = None,
        context_lines: int = 2,
        max_results: int = 200,
        include_hidden: bool = False,
        globs: list[str] | None = None,
        extra_excludes: list[str] | None = None,
    ) -> dict:
        """Search text inside the configured filesystem roots without scanning binaries."""
        return diagnostics.search_text(
            pattern=pattern,
            root=root,
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
        root: str | None = None,
        context_lines: int = 3,
        max_diff_lines: int = 400,
    ) -> dict:
        """Diff two text files inside the configured filesystem roots."""
        return diagnostics.compare_text_files(
            path_a=path_a,
            path_b=path_b,
            root=root,
            context_lines=context_lines,
            max_diff_lines=max_diff_lines,
        )

    return server


def main() -> int:
    server = create_filesystem_diagnostics_server()
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
