"""Unit tests for talos.awareness.config (no external dependencies)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


def _clean_env() -> "patch.dict":
    return patch.dict(os.environ, {}, clear=True)


class ConfigDefaultsTest(unittest.TestCase):
    def test_minimal_valid_settings_and_database_url(self) -> None:
        with _clean_env():
            settings = load_settings(_env_file=None, db_password="p@ss/w:rd")
        self.assertEqual(settings.db_host, "127.0.0.1")
        self.assertEqual(settings.db_port, 5433)
        self.assertEqual(settings.db_name, "talos_awareness")
        self.assertEqual(
            settings.database_url,
            "postgresql+asyncpg://talos:p%40ss%2Fw%3Ard@127.0.0.1:5433/talos_awareness",
        )

    def test_log_level_is_normalized(self) -> None:
        with _clean_env():
            settings = load_settings(_env_file=None, db_password="x", log_level="debug")
        self.assertEqual(settings.log_level, "DEBUG")

    def test_ollama_host_trailing_slash_stripped(self) -> None:
        with _clean_env():
            settings = load_settings(
                _env_file=None, db_password="x", ollama_host="http://10.0.0.2:11434/"
            )
        self.assertEqual(settings.ollama_host, "http://10.0.0.2:11434")

    def test_summary_contains_no_secrets(self) -> None:
        with _clean_env():
            settings = load_settings(
                _env_file=None, db_password="sekrit-value", mqtt_password="mq-sekrit"
            )
        summary_text = json.dumps(settings.summary(), default=str)
        self.assertNotIn("sekrit-value", summary_text)
        self.assertNotIn("mq-sekrit", summary_text)
        self.assertIn("127.0.0.1:5433/talos_awareness", summary_text)


class ConfigFailureTest(unittest.TestCase):
    def test_missing_password_names_env_var(self) -> None:
        with _clean_env():
            with self.assertRaises(SettingsError) as ctx:
                load_settings(_env_file=None)
        message = str(ctx.exception)
        self.assertIn("TALOS_AWARENESS_DB_PASSWORD", message)
        self.assertIn("required", message)

    def test_invalid_port_names_env_var(self) -> None:
        with _clean_env():
            with self.assertRaises(SettingsError) as ctx:
                load_settings(_env_file=None, db_password="x", db_port=99999)
        self.assertIn("TALOS_AWARENESS_DB_PORT", str(ctx.exception))

    def test_invalid_log_level_is_actionable(self) -> None:
        with _clean_env():
            with self.assertRaises(SettingsError) as ctx:
                load_settings(_env_file=None, db_password="x", log_level="chatty")
        self.assertIn("TALOS_AWARENESS_LOG_LEVEL", str(ctx.exception))

    def test_invalid_ollama_host_rejected(self) -> None:
        with _clean_env():
            with self.assertRaises(SettingsError):
                load_settings(_env_file=None, db_password="x", ollama_host="localhost:11434")


class ConfigEnvAliasTest(unittest.TestCase):
    def test_legacy_mqtt_broker_env_is_honored(self) -> None:
        with patch.dict(
            os.environ, {"MQTT_BROKER": "10.1.2.3", "MQTT_PORT": "1884"}, clear=True
        ):
            settings = AwarenessSettings(_env_file=None, db_password="x")
        self.assertEqual(settings.mqtt_host, "10.1.2.3")
        self.assertEqual(settings.mqtt_port, 1884)

    def test_awareness_specific_mqtt_env_wins_over_legacy(self) -> None:
        with patch.dict(
            os.environ,
            {"TALOS_AWARENESS_MQTT_HOST": "broker.internal", "MQTT_BROKER": "10.1.2.3"},
            clear=True,
        ):
            settings = AwarenessSettings(_env_file=None, db_password="x")
        self.assertEqual(settings.mqtt_host, "broker.internal")

    def test_prefixed_env_vars_apply(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TALOS_AWARENESS_DB_PASSWORD": "envpass",
                "TALOS_AWARENESS_API_PORT": "9000",
            },
            clear=True,
        ):
            settings = AwarenessSettings(_env_file=None)
        self.assertEqual(settings.db_password.get_secret_value(), "envpass")
        self.assertEqual(settings.api_port, 9000)


if __name__ == "__main__":
    unittest.main()
