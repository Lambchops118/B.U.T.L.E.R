from __future__ import annotations

import difflib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_TEXT_BYTES = 1_000_000
DEFAULT_RG_TIMEOUT_SECONDS = 20.0


def resolve_allowed_root_paths(raw_value: str, *, base_dir: Path | None = None) -> list[Path]:
    raw = str(raw_value or "").strip()
    if not raw:
        return []

    parts: list[str]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parts = [part.strip() for part in raw.split(os.pathsep) if part.strip()]
    else:
        if isinstance(parsed, str):
            parts = [parsed.strip()] if parsed.strip() else []
        elif isinstance(parsed, list):
            parts = [str(item).strip() for item in parsed if str(item).strip()]
        else:
            raise ValueError(
                "Filesystem roots must be a JSON string, a JSON array of paths, "
                f"or an {os.pathsep!r}-separated string."
            )

    repo_root = base_dir or Path(__file__).resolve().parents[1]
    resolved: list[Path] = []
    seen: set[Path] = set()
    for item in parts:
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved.append(candidate)
    return resolved


def _optional_string_list(raw_value: str) -> tuple[str, ...]:
    raw = str(raw_value or "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        values = [part.strip() for part in raw.split(os.pathsep) if part.strip()]
    else:
        if isinstance(parsed, str):
            values = [parsed.strip()] if parsed.strip() else []
        elif isinstance(parsed, list):
            values = [str(item).strip() for item in parsed if str(item).strip()]
        else:
            raise ValueError(
                "Filesystem exclude globs must be a JSON string, JSON array, "
                f"or an {os.pathsep!r}-separated string."
            )
    return tuple(value for value in values if value)


def _utc_timestamp(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _normalize_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    text = relative.as_posix()
    return text or "."


def _glob_matches(relative_path: str, pattern: str) -> bool:
    posix_path = PurePosixPath(relative_path)
    variants = [pattern]
    if pattern.endswith("/**"):
        variants.append(pattern[:-3])
    return any(posix_path.match(candidate) for candidate in variants)


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    return b"\x00" in chunk


def _read_text_file(path: Path, *, max_bytes: int) -> list[str]:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path.name} is too large to compare safely ({size} bytes).")
    if _looks_binary(path):
        raise ValueError(f"{path.name} appears to be a binary file.")
    return path.read_text(encoding="utf-8").splitlines()


@dataclass
class ScopedFilesystemDiagnostics:
    root: Path
    rg_timeout_seconds: float = DEFAULT_RG_TIMEOUT_SECONDS
    max_text_bytes: int = DEFAULT_TEXT_BYTES
    default_excludes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        if not self.root.exists():
            raise RuntimeError(f"Filesystem root does not exist: {self.root}")
        if not self.root.is_dir():
            raise RuntimeError(f"Filesystem root is not a directory: {self.root}")

    def resolve_path(self, requested_path: str, *, must_exist: bool = True) -> Path:
        normalized = (requested_path or ".").strip() or "."
        if normalized in {".", "/"}:
            candidate = self.root
        else:
            raw_path = Path(normalized).expanduser()
            candidate = raw_path if raw_path.is_absolute() else self.root / raw_path

        if must_exist:
            if not candidate.exists():
                raise FileNotFoundError(f"Path does not exist inside the configured root: {normalized}")
            resolved = candidate.resolve()
            self._ensure_inside_root(resolved)
            return resolved

        parent = candidate.parent if candidate.name else candidate
        if not parent.exists():
            resolved_parent = parent.resolve()
            self._ensure_inside_root(resolved_parent)
            return candidate

        resolved_parent = parent.resolve()
        self._ensure_inside_root(resolved_parent)
        return candidate

    def contains_path(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return resolved == self.root or self.root in resolved.parents

    def _ensure_inside_root(self, path: Path) -> None:
        if path != self.root and self.root not in path.parents:
            raise ValueError(
                f"Refusing to access '{path}' because it resolves outside the configured "
                f"root '{self.root}'."
            )

    def _exclude_globs(self, extra_excludes: list[str] | None = None) -> list[str]:
        globs = list(self.default_excludes)
        for pattern in extra_excludes or []:
            cleaned = str(pattern or "").strip()
            if cleaned:
                globs.append(cleaned)
        return globs

    def _is_excluded(
        self,
        path: Path,
        *,
        include_hidden: bool,
        extra_excludes: list[str] | None = None,
    ) -> bool:
        relative = _normalize_relative_path(path, self.root)
        if relative == ".":
            return False

        parts = [part for part in PurePosixPath(relative).parts if part not in {"."}]
        if not include_hidden and any(part.startswith(".") for part in parts):
            return True

        return any(_glob_matches(relative, pattern) for pattern in self._exclude_globs(extra_excludes))

    def _relative_arg(self, path: Path) -> str:
        relative = _normalize_relative_path(path, self.root)
        return "." if relative == "." else relative

    def _run_rg(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                args,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.rg_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ripgrep (rg) is not installed or is not in PATH. Install it with `brew install ripgrep`."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ripgrep timed out after {self.rg_timeout_seconds:.1f}s while scanning the filesystem root."
            ) from exc

        if completed.returncode not in {0, 1}:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown ripgrep failure"
            raise RuntimeError(detail)
        return completed

    def summarize_directory(
        self,
        *,
        relative_path: str = ".",
        max_depth: int = 2,
        max_entries_per_dir: int = 30,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
    ) -> dict[str, Any]:
        target = self.resolve_path(relative_path)
        max_depth = max(0, min(max_depth, 6))
        max_entries_per_dir = max(1, min(max_entries_per_dir, 200))

        def build_tree(path: Path, depth: int) -> dict[str, Any]:
            item: dict[str, Any] = {
                "path": _normalize_relative_path(path, self.root),
                "type": "directory" if path.is_dir() else "file",
            }
            if not path.is_dir():
                item["size_bytes"] = path.stat().st_size
                return item

            if depth >= max_depth:
                item["children"] = []
                item["truncated"] = True
                return item

            children: list[dict[str, Any]] = []
            entries = sorted(
                path.iterdir(),
                key=lambda candidate: (candidate.is_file(), candidate.name.lower()),
            )
            truncated = False
            for entry in entries:
                if self._is_excluded(
                    entry,
                    include_hidden=include_hidden,
                    extra_excludes=extra_excludes,
                ):
                    continue
                if len(children) >= max_entries_per_dir:
                    truncated = True
                    break

                child: dict[str, Any] = {
                    "path": _normalize_relative_path(entry, self.root),
                    "type": "directory" if entry.is_dir() else "file",
                    "symlink": entry.is_symlink(),
                }
                if entry.is_symlink():
                    try:
                        resolved = entry.resolve()
                        child["resolves_to"] = str(resolved)
                        child["outside_root"] = resolved != self.root and self.root not in resolved.parents
                    except OSError as exc:
                        child["symlink_error"] = str(exc)

                if entry.is_dir() and not entry.is_symlink():
                    child.update(build_tree(entry, depth + 1))
                else:
                    try:
                        child["size_bytes"] = entry.stat().st_size
                    except OSError:
                        pass
                children.append(child)

            item["children"] = children
            if truncated:
                item["truncated"] = True
            return item

        return {
            "root": str(self.root),
            "path": _normalize_relative_path(target, self.root),
            "max_depth": max_depth,
            "max_entries_per_dir": max_entries_per_dir,
            "exclude_globs": self._exclude_globs(extra_excludes),
            "tree": build_tree(target, 0),
        }

    def find_recent_files(
        self,
        *,
        relative_path: str = ".",
        globs: list[str] | None = None,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
        max_results: int = 20,
    ) -> dict[str, Any]:
        target = self.resolve_path(relative_path)
        max_results = max(1, min(max_results, 500))
        cleaned_globs = [str(pattern or "").strip() for pattern in globs or [] if str(pattern or "").strip()]

        candidates: list[dict[str, Any]] = []
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if self._is_excluded(path, include_hidden=include_hidden, extra_excludes=extra_excludes):
                continue
            relative = _normalize_relative_path(path, self.root)
            if cleaned_globs and not any(_glob_matches(relative, pattern) for pattern in cleaned_globs):
                continue
            stat = path.stat()
            candidates.append(
                {
                    "path": relative,
                    "size_bytes": stat.st_size,
                    "modified_at": _utc_timestamp(stat.st_mtime),
                    "modified_epoch": stat.st_mtime,
                }
            )

        candidates.sort(key=lambda item: item["modified_epoch"], reverse=True)
        trimmed = candidates[:max_results]
        for item in trimmed:
            item.pop("modified_epoch", None)
        return {
            "root": str(self.root),
            "path": _normalize_relative_path(target, self.root),
            "files": trimmed,
            "truncated": len(candidates) > max_results,
        }

    def list_files(
        self,
        *,
        relative_path: str = ".",
        globs: list[str] | None = None,
        include_hidden: bool = False,
        extra_excludes: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        target = self.resolve_path(relative_path)
        max_results = max(1, min(max_results, 2000))

        args = ["rg", "--files", "--color", "never"]
        if include_hidden:
            args.append("--hidden")
        for pattern in globs or []:
            cleaned = str(pattern or "").strip()
            if cleaned:
                args.extend(["-g", cleaned])
        for pattern in self._exclude_globs(extra_excludes):
            args.extend(["-g", f"!{pattern}"])
        args.append(self._relative_arg(target))

        completed = self._run_rg(args)
        files = [line for line in completed.stdout.splitlines() if line]
        return {
            "root": str(self.root),
            "path": _normalize_relative_path(target, self.root),
            "files": files[:max_results],
            "truncated": len(files) > max_results,
        }

    def search_text(
        self,
        *,
        pattern: str,
        relative_path: str = ".",
        fixed_strings: bool = False,
        case_sensitive: bool | None = None,
        context_lines: int = 2,
        max_results: int = 200,
        include_hidden: bool = False,
        globs: list[str] | None = None,
        extra_excludes: list[str] | None = None,
    ) -> dict[str, Any]:
        if not pattern.strip():
            raise ValueError("pattern is required")

        target = self.resolve_path(relative_path)
        context_lines = max(0, min(context_lines, 20))
        max_results = max(1, min(max_results, 500))

        args = ["rg", "--line-number", "--color", "never", "--max-count", str(max_results)]
        if context_lines:
            args.extend(["--context", str(context_lines)])
        if fixed_strings:
            args.append("--fixed-strings")
        if case_sensitive is True:
            args.append("--case-sensitive")
        elif case_sensitive is False:
            args.append("--ignore-case")
        if include_hidden:
            args.append("--hidden")
        for pattern_glob in globs or []:
            cleaned = str(pattern_glob or "").strip()
            if cleaned:
                args.extend(["-g", cleaned])
        for exclude_glob in self._exclude_globs(extra_excludes):
            args.extend(["-g", f"!{exclude_glob}"])
        args.extend([pattern, self._relative_arg(target)])

        completed = self._run_rg(args)
        output = completed.stdout.strip()
        lines = output.splitlines() if output else []
        preview = "\n".join(lines[:400])
        return {
            "root": str(self.root),
            "path": _normalize_relative_path(target, self.root),
            "pattern": pattern,
            "matches_found": bool(lines),
            "output": preview or "No matches found.",
            "truncated": len(lines) > 400,
        }

    def compare_text_files(
        self,
        *,
        path_a: str,
        path_b: str,
        context_lines: int = 3,
        max_diff_lines: int = 400,
    ) -> dict[str, Any]:
        file_a = self.resolve_path(path_a)
        file_b = self.resolve_path(path_b)
        context_lines = max(0, min(context_lines, 20))
        max_diff_lines = max(20, min(max_diff_lines, 2000))

        lines_a = _read_text_file(file_a, max_bytes=self.max_text_bytes)
        lines_b = _read_text_file(file_b, max_bytes=self.max_text_bytes)
        diff = list(
            difflib.unified_diff(
                lines_a,
                lines_b,
                fromfile=_normalize_relative_path(file_a, self.root),
                tofile=_normalize_relative_path(file_b, self.root),
                lineterm="",
                n=context_lines,
            )
        )
        preview = "\n".join(diff[:max_diff_lines])
        return {
            "root": str(self.root),
            "path_a": _normalize_relative_path(file_a, self.root),
            "path_b": _normalize_relative_path(file_b, self.root),
            "identical": not diff,
            "diff": preview,
            "truncated": len(diff) > max_diff_lines,
        }


@dataclass
class MultiRootFilesystemDiagnostics:
    scopes: list[ScopedFilesystemDiagnostics]

    @classmethod
    def from_roots(
        cls,
        roots: list[Path],
        *,
        rg_timeout_seconds: float = DEFAULT_RG_TIMEOUT_SECONDS,
        max_text_bytes: int = DEFAULT_TEXT_BYTES,
        default_excludes: tuple[str, ...] = (),
    ) -> "MultiRootFilesystemDiagnostics":
        if not roots:
            raise RuntimeError("No filesystem roots were configured.")
        scopes = [
            ScopedFilesystemDiagnostics(
                root=root,
                rg_timeout_seconds=rg_timeout_seconds,
                max_text_bytes=max_text_bytes,
                default_excludes=default_excludes,
            )
            for root in roots
        ]
        return cls(scopes=scopes)

    @classmethod
    def from_env(cls) -> "MultiRootFilesystemDiagnostics":
        roots = resolve_allowed_root_paths(os.getenv("BUTLER_FILESYSTEM_ROOTS", ""))
        return cls.from_roots(
            roots,
            rg_timeout_seconds=max(1.0, float(os.getenv("BUTLER_FILESYSTEM_RG_TIMEOUT", "20"))),
            max_text_bytes=max(4096, int(os.getenv("BUTLER_FILESYSTEM_MAX_TEXT_BYTES", str(DEFAULT_TEXT_BYTES)))),
            default_excludes=_optional_string_list(os.getenv("BUTLER_FILESYSTEM_DEFAULT_EXCLUDES", "")),
        )

    def describe_roots(self) -> dict[str, Any]:
        return {"roots": [str(scope.root) for scope in self.scopes], "count": len(self.scopes)}

    def _scope_for_root(self, root: str | None) -> ScopedFilesystemDiagnostics | None:
        if root is None or not str(root).strip():
            return None
        requested = Path(str(root).strip()).expanduser().resolve()
        candidates = [scope for scope in self.scopes if scope.contains_path(requested)]
        if not candidates:
            allowed = ", ".join(str(scope.root) for scope in self.scopes)
            raise ValueError(f"Requested root '{requested}' is outside the configured roots: {allowed}")
        candidates.sort(key=lambda scope: len(str(scope.root)), reverse=True)
        return candidates[0]

    def _scope_for_path(self, path_text: str | None) -> ScopedFilesystemDiagnostics | None:
        normalized = str(path_text or "").strip()
        if not normalized:
            return None
        raw_path = Path(normalized).expanduser()
        if not raw_path.is_absolute():
            return None
        resolved = raw_path.resolve()
        candidates = [scope for scope in self.scopes if scope.contains_path(resolved)]
        if not candidates:
            return None
        candidates.sort(key=lambda scope: len(str(scope.root)), reverse=True)
        return candidates[0]

    def _resolve_scope(self, root: str | None = None, path_hint: str | None = None) -> ScopedFilesystemDiagnostics:
        explicit = self._scope_for_root(root)
        if explicit is not None:
            return explicit
        hinted = self._scope_for_path(path_hint)
        if hinted is not None:
            return hinted
        return self.scopes[0]

    def summarize_directory(self, *, root: str | None = None, **kwargs: Any) -> dict[str, Any]:
        scope = self._resolve_scope(root=root, path_hint=str(kwargs.get("relative_path") or ""))
        return scope.summarize_directory(**kwargs)

    def find_recent_files(self, *, root: str | None = None, **kwargs: Any) -> dict[str, Any]:
        scope = self._resolve_scope(root=root, path_hint=str(kwargs.get("relative_path") or ""))
        return scope.find_recent_files(**kwargs)

    def list_files(self, *, root: str | None = None, **kwargs: Any) -> dict[str, Any]:
        scope = self._resolve_scope(root=root, path_hint=str(kwargs.get("relative_path") or ""))
        return scope.list_files(**kwargs)

    def search_text(self, *, root: str | None = None, **kwargs: Any) -> dict[str, Any]:
        scope = self._resolve_scope(root=root, path_hint=str(kwargs.get("relative_path") or ""))
        return scope.search_text(**kwargs)

    def compare_text_files(
        self,
        *,
        root: str | None = None,
        path_a: str,
        path_b: str,
        context_lines: int = 3,
        max_diff_lines: int = 400,
    ) -> dict[str, Any]:
        scope_a = self._resolve_scope(root=root, path_hint=path_a)
        scope_b = self._resolve_scope(root=root, path_hint=path_b)
        if scope_a.root == scope_b.root:
            return scope_a.compare_text_files(
                path_a=path_a,
                path_b=path_b,
                context_lines=context_lines,
                max_diff_lines=max_diff_lines,
            )

        file_a = scope_a.resolve_path(path_a)
        file_b = scope_b.resolve_path(path_b)
        lines_a = _read_text_file(file_a, max_bytes=scope_a.max_text_bytes)
        lines_b = _read_text_file(file_b, max_bytes=scope_b.max_text_bytes)
        diff = list(
            difflib.unified_diff(
                lines_a,
                lines_b,
                fromfile=str(file_a),
                tofile=str(file_b),
                lineterm="",
                n=max(0, min(context_lines, 20)),
            )
        )
        preview = "\n".join(diff[: max(20, min(max_diff_lines, 2000))])
        return {
            "root_a": str(scope_a.root),
            "root_b": str(scope_b.root),
            "path_a": str(file_a),
            "path_b": str(file_b),
            "identical": not diff,
            "diff": preview,
            "truncated": len(diff) > max(20, min(max_diff_lines, 2000)),
        }
