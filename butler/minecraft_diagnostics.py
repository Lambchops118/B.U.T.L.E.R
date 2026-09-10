from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from butler.filesystem_diagnostics import ScopedFilesystemDiagnostics


DEFAULT_NOISY_GLOBS = (
    "libraries/**",
    "versions/**",
    "backups/**",
    "world/region/**",
    "world/DIM*/**",
    "world/entities/**",
    "world/poi/**",
    ".git/**",
    "cache/**",
    "crash-reports/old/**",
)
VERSION_SUFFIX_RE = re.compile(r"[-_+](?:mc)?\d[\w.+-]*$")


def configured_minecraft_root() -> Path:
    raw_root = os.getenv("MINECRAFT_SERVER_DIR", "").strip()
    if not raw_root:
        raise RuntimeError("MINECRAFT_SERVER_DIR is not set.")

    root = Path(raw_root).expanduser().resolve()
    if not root.exists():
        raise RuntimeError(f"Minecraft server root does not exist: {root}")
    if not root.is_dir():
        raise RuntimeError(f"MINECRAFT_SERVER_DIR is not a directory: {root}")
    return root


@dataclass
class MinecraftDiagnostics(ScopedFilesystemDiagnostics):
    default_excludes: tuple[str, ...] = DEFAULT_NOISY_GLOBS

    @classmethod
    def from_env(cls) -> "MinecraftDiagnostics":
        return cls(
            root=configured_minecraft_root(),
            rg_timeout_seconds=max(1.0, float(os.getenv("MINECRAFT_MCP_RG_TIMEOUT", "20"))),
            max_text_bytes=max(4096, int(os.getenv("MINECRAFT_MCP_MAX_TEXT_BYTES", "1000000"))),
        )

    def summarize_server_layout(
        self,
        *,
        relative_path: str = ".",
        max_depth: int = 2,
        max_entries_per_dir: int = 30,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.summarize_directory(
            relative_path=relative_path,
            max_depth=max_depth,
            max_entries_per_dir=max_entries_per_dir,
            include_hidden=include_hidden,
            extra_excludes=extra_excludes,
        )

    def find_recent_logs(self, *, max_results: int = 10) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        max_results = max(1, min(max_results, 100))

        for relative in ("logs", "crash-reports", "kubejs", "scripts"):
            directory = self.root / relative
            if not directory.exists() or not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                if self._is_excluded(path, include_hidden=False):
                    continue
                stat = path.stat()
                candidates.append(
                    {
                        "path": path.relative_to(self.root).as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                        "modified_epoch": stat.st_mtime,
                    }
                )

        candidates.sort(key=lambda item: item["modified_epoch"], reverse=True)
        trimmed = candidates[:max_results]
        for item in trimmed:
            item.pop("modified_epoch", None)
        return {
            "root": str(self.root),
            "files": trimmed,
            "truncated": len(candidates) > max_results,
        }

    def list_mod_jars(self, *, max_results: int = 300) -> dict[str, Any]:
        mods_dir = self.root / "mods"
        if not mods_dir.exists():
            return {"mods_dir": "mods", "files": [], "missing": True}

        max_results = max(1, min(max_results, 2000))
        files = []
        for path in sorted(mods_dir.glob("*.jar"), key=lambda candidate: candidate.name.lower()):
            stat = path.stat()
            files.append(
                {
                    "path": path.relative_to(self.root).as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return {
            "mods_dir": "mods",
            "files": files[:max_results],
            "truncated": len(files) > max_results,
        }

    def detect_duplicate_mods(self, *, max_results: int = 50) -> dict[str, Any]:
        listing = self.list_mod_jars(max_results=5000)
        groups: dict[str, list[str]] = {}
        for item in listing.get("files", []):
            path_text = str(item["path"])
            stem = Path(path_text).stem.lower()
            normalized = VERSION_SUFFIX_RE.sub("", stem).strip("-_+")
            groups.setdefault(normalized or stem, []).append(path_text)

        duplicates = [
            {"normalized_name": name, "paths": sorted(paths)}
            for name, paths in groups.items()
            if len(paths) > 1
        ]
        duplicates.sort(key=lambda item: (len(item["paths"]) * -1, item["normalized_name"]))
        max_results = max(1, min(max_results, 200))
        return {
            "duplicates": duplicates[:max_results],
            "truncated": len(duplicates) > max_results,
        }
