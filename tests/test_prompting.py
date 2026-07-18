from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent.prompting import PromptAssembler, PromptContext


class PromptAssemblerTests(unittest.TestCase):
    def test_default_personality_enforces_home_presence_response_discipline(self) -> None:
        prompt = PromptAssembler().build(PromptContext(interaction_mode="voice"))

        self.assertIn("persistent presence in the home", prompt)
        self.assertIn("For routine commands, use the shortest confirmation", prompt)
        self.assertIn("Once the request is answered or completed, stop speaking", prompt)
        self.assertIn("Never claim an action succeeded", prompt)
        self.assertIn("Anything else?", prompt)

    def test_builds_prompt_from_base_overlay_and_memory_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_path = root / "base.md"
            voice_path = root / "voice.md"
            kicad_path = root / "kicad.md"
            base_path.write_text("Base identity.", encoding="utf-8")
            voice_path.write_text("Voice overlay.", encoding="utf-8")
            kicad_path.write_text("KiCad overlay.", encoding="utf-8")

            assembler = PromptAssembler(
                base_persona_path=base_path,
                overlay_paths={"voice": voice_path, "kicad": kicad_path},
            )

            prompt = assembler.build(
                PromptContext(
                    interaction_mode="voice",
                    domain_overlays=("kicad", "kicad"),
                    memory_block="Remembered preference: keep it brief.",
                )
            )

        self.assertIn("Base identity.", prompt)
        self.assertIn("Voice overlay.", prompt)
        self.assertEqual(prompt.count("KiCad overlay."), 1)
        self.assertIn("Memory Context (Runtime Injected)", prompt)
        self.assertIn("keep it brief", prompt)

    def test_unknown_overlay_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_path = root / "base.md"
            text_path = root / "text.md"
            base_path.write_text("Base identity.", encoding="utf-8")
            text_path.write_text("Text overlay.", encoding="utf-8")

            assembler = PromptAssembler(
                base_persona_path=base_path,
                overlay_paths={"text": text_path},
            )

            with self.assertRaises(ValueError):
                assembler.build(PromptContext(interaction_mode="text", domain_overlays=("missing",)))


if __name__ == "__main__":
    unittest.main()
