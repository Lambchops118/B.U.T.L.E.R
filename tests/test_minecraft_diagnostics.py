from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.agent import runtime as agent_runtime
from butler.filesystem_diagnostics import MultiRootFilesystemDiagnostics
from butler.minecraft_diagnostics import MinecraftDiagnostics


class FilesystemDiagnosticsTests(unittest.TestCase):
    def test_multi_root_search_uses_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            path_a = Path(root_a) / "alpha.txt"
            path_b = Path(root_b) / "beta.txt"
            path_a.write_text("apple\n", encoding="utf-8")
            path_b.write_text("banana\n", encoding="utf-8")

            diagnostics = MultiRootFilesystemDiagnostics.from_roots([Path(root_a), Path(root_b)])
            result = diagnostics.search_text(pattern="banana", root=root_b)

        self.assertEqual(result["root"], str(Path(root_b).resolve()))
        self.assertTrue(result["matches_found"])
        self.assertIn("beta.txt", result["output"])

    def test_multi_root_compare_text_files_supports_cross_root_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            path_a = Path(root_a) / "config.toml"
            path_b = Path(root_b) / "config.toml"
            path_a.write_text("enabled = true\n", encoding="utf-8")
            path_b.write_text("enabled = false\n", encoding="utf-8")

            diagnostics = MultiRootFilesystemDiagnostics.from_roots([Path(root_a), Path(root_b)])
            result = diagnostics.compare_text_files(path_a=str(path_a), path_b=str(path_b))

        self.assertFalse(result["identical"])
        self.assertIn("-enabled = true", result["diff"])
        self.assertIn("+enabled = false", result["diff"])


class MinecraftDiagnosticsTests(unittest.TestCase):
    def test_search_text_finds_error_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            logs = root / "logs"
            logs.mkdir()
            (logs / "latest.log").write_text("INFO boot\nERROR bad mod\n", encoding="utf-8")

            diagnostics = MinecraftDiagnostics(root=root)
            result = diagnostics.search_text(pattern="ERROR", relative_path="logs")

        self.assertTrue(result["matches_found"])
        self.assertIn("latest.log", result["output"])
        self.assertIn("ERROR bad mod", result["output"])

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside_tmpdir:
            root = Path(tmpdir)
            outside = Path(outside_tmpdir)
            target = outside / "secret.txt"
            target.write_text("do not read", encoding="utf-8")
            link = root / "outside-link.txt"
            link.symlink_to(target)

            diagnostics = MinecraftDiagnostics(root=root)
            with self.assertRaises(ValueError):
                diagnostics.resolve_path("outside-link.txt")

    def test_compare_text_files_returns_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_a = root / "config-a.toml"
            file_b = root / "config-b.toml"
            file_a.write_text("enabled = true\nvalue = 1\n", encoding="utf-8")
            file_b.write_text("enabled = false\nvalue = 1\n", encoding="utf-8")

            diagnostics = MinecraftDiagnostics(root=root)
            result = diagnostics.compare_text_files(path_a="config-a.toml", path_b="config-b.toml")

        self.assertFalse(result["identical"])
        self.assertIn("--- config-a.toml", result["diff"])
        self.assertIn("+++ config-b.toml", result["diff"])
        self.assertIn("-enabled = true", result["diff"])
        self.assertIn("+enabled = false", result["diff"])


class MinecraftPromptOverlayTests(unittest.TestCase):
    def test_runtime_adds_minecraft_overlay_for_forge_request(self) -> None:
        tool_defs = [{"name": "minecraft_search_text"}]

        with patch.dict(os.environ, {"MINECRAFT_SERVER_DIR": "/srv/minecraft"}, clear=False):
            overlays = agent_runtime._domain_overlays_for_command(
                "Diagnose my Forge server latest.log",
                tool_defs,
            )

        self.assertIn("minecraft", overlays)

    def test_runtime_does_not_add_minecraft_overlay_for_generic_latest_log_request(self) -> None:
        overlays = agent_runtime._domain_overlays_for_command(
            "Please inspect /var/log/latest.log for errors",
            [],
        )

        self.assertNotIn("minecraft", overlays)


if __name__ == "__main__":
    unittest.main()
