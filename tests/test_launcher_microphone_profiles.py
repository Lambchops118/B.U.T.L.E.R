from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from talos.launcher import config
from talos.launcher.__main__ import _build_parser
from talos.launcher.config import LauncherConfig
from talos.launcher.core import _microphone_env


class LauncherMicrophoneProfileTests(unittest.TestCase):
    def test_respeaker_profile_injects_safe_capture_contract(self):
        cfg = LauncherConfig(microphone_profile="respeaker")
        env = _microphone_env({"TALOS_BARGE_IN": "1"}, cfg)
        self.assertEqual(env["TALOS_MICROPHONE_PROFILE"], "respeaker")
        self.assertEqual(env["TALOS_RECOGNIZER_ENERGY_THRESHOLD"], "auto")
        self.assertEqual(env["TALOS_BARGE_IN"], "0")
        self.assertEqual(env["TALOS_IDLE_VAD_ENDPOINTING"], "0")

    def test_yeti_profile_preserves_existing_aec_rollout_settings(self):
        cfg = LauncherConfig(microphone_profile="yeti")
        env = _microphone_env(
            {"TALOS_BARGE_IN": "1", "TALOS_IDLE_VAD_ENDPOINTING": "1"},
            cfg,
        )
        self.assertEqual(env["TALOS_MICROPHONE_PROFILE"], "yeti")
        self.assertEqual(env["TALOS_RECOGNIZER_ENERGY_THRESHOLD"], "500")
        self.assertEqual(env["TALOS_BARGE_IN"], "1")
        self.assertEqual(env["TALOS_IDLE_VAD_ENDPOINTING"], "1")

    def test_old_launcher_config_defaults_to_current_respeaker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "launcher.config.json"
            path.write_text(json.dumps({"start_voice": True}), encoding="utf-8")
            with mock.patch.object(config, "LAUNCHER_CONFIG_PATH", path):
                loaded = LauncherConfig.load()
        self.assertEqual(loaded.microphone_profile, "respeaker")

    def test_invalid_saved_profile_normalizes_to_respeaker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "launcher.config.json"
            path.write_text(
                json.dumps({"microphone_profile": "not-a-device"}),
                encoding="utf-8",
            )
            with mock.patch.object(config, "LAUNCHER_CONFIG_PATH", path):
                loaded = LauncherConfig.load()
        self.assertEqual(loaded.microphone_profile, "respeaker")

    def test_headless_microphone_argument_accepts_both_profiles(self):
        parser = _build_parser()
        self.assertEqual(
            parser.parse_args(["--no-gui", "--microphone", "yeti"]).microphone,
            "yeti",
        )
        self.assertEqual(
            parser.parse_args(
                ["--no-gui", "--microphone", "respeaker"]
            ).microphone,
            "respeaker",
        )


if __name__ == "__main__":
    unittest.main()
