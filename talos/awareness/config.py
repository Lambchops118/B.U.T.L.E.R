"""Typed configuration for the awareness backend.

All settings come from environment variables (``TALOS_AWARENESS_*``) or the
repository ``.env`` file, matching the existing TALOS convention. MQTT settings
fall back to the legacy ``MQTT_BROKER`` / ``MQTT_PORT`` names already used by
``talos/services/home_automation.py`` so one configuration drives both worlds.

Use :func:`load_settings` rather than instantiating ``AwarenessSettings``
directly: it converts pydantic validation failures into a single
:class:`SettingsError` whose message names the exact environment variables to
fix (a Phase 1 acceptance requirement).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Fields whose environment variable differs from the plain prefix+name form.
_ENV_ALIASES: dict[str, str] = {
    "mqtt_host": "TALOS_AWARENESS_MQTT_HOST (or legacy MQTT_BROKER)",
    "mqtt_port": "TALOS_AWARENESS_MQTT_PORT (or legacy MQTT_PORT)",
}


class SettingsError(RuntimeError):
    """Configuration is missing or invalid; message lists actionable fixes."""


class AwarenessSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TALOS_AWARENESS_",
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- database ---------------------------------------------------------
    db_host: str = "127.0.0.1"
    db_port: int = 5433  # 5432 is commonly taken by a host-level PostgreSQL
    db_name: str = "talos_awareness"
    db_user: str = "talos"
    db_password: SecretStr

    # --- internal API -----------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8600
    api_token: SecretStr | None = None  # enforced once state-changing routes exist

    # --- logging / storage ------------------------------------------------
    log_level: str = "INFO"
    data_directory: Path = REPO_ROOT / "db" / "awareness"

    # --- LLM host (same machine as this backend per owner decision) --------
    ollama_host: str = "http://127.0.0.1:11434"
    chat_model: str = ""
    embedding_model: str = ""

    # --- MQTT (consumed from Phase 2 onward) --------------------------------
    mqtt_enabled: bool = True
    mqtt_host: str = Field(
        default="192.168.1.160",
        validation_alias=AliasChoices("TALOS_AWARENESS_MQTT_HOST", "MQTT_BROKER"),
    )
    mqtt_port: int = Field(
        default=1883,
        validation_alias=AliasChoices("TALOS_AWARENESS_MQTT_PORT", "MQTT_PORT"),
    )
    mqtt_tls: bool = False
    mqtt_ca_path: Path | None = None
    mqtt_client_cert: Path | None = None
    mqtt_client_key: Path | None = None
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_client_id: str = "talos-awareness"
    mqtt_keepalive: int = 60
    mqtt_topic_prefix: str = ""

    # --- ingestion bounds ---------------------------------------------------
    max_event_payload_bytes: int = Field(default=65536, ge=1024)

    # --- state freshness (Phase 3) -------------------------------------------
    # Per-source values in the registry override these defaults.
    default_stale_after_seconds: float = Field(default=300.0, gt=0)
    default_offline_after_seconds: float = Field(default=900.0, gt=0)
    freshness_interval_seconds: float = Field(default=30.0, gt=0)

    # --- bounded read queries (Phase 3) --------------------------------------
    max_query_range_days: int = Field(default=31, ge=1)
    max_query_points: int = Field(default=10000, ge=1)
    max_event_page_size: int = Field(default=500, ge=1)

    # --- rules / alerts / notifications (Phase 4) -----------------------------
    rules_path: Path | None = None  # default: talos/awareness/rules/rules.toml
    quiet_hours: str = ""  # "HH:MM-HH:MM" local; noncritical only; empty = off
    notify_url: str = "http://127.0.0.1:8420"  # existing text server (GUI banner)
    notify_token: SecretStr | None = None  # text server bearer token
    notify_timeout_seconds: float = Field(default=5.0, gt=0)
    outbox_interval_seconds: float = Field(default=2.0, gt=0)
    outbox_batch_size: int = Field(default=20, ge=1)
    outbox_max_attempts: int = Field(default=8, ge=1)
    outbox_stale_lock_seconds: float = Field(default=300.0, gt=0)

    # --- situation / context (Phase 5) ----------------------------------------
    situation_budget_tokens: int = Field(default=600, ge=50)
    situation_max_items_per_section: int = Field(default=20, ge=1)
    situation_transition_window_minutes: int = Field(default=60, ge=1)

    @field_validator("db_port", "api_port", "mqtt_port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("must be a TCP port between 1 and 65535")
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError(f"must be one of {sorted(_LOG_LEVELS)}")
        return normalized

    @field_validator("ollama_host")
    @classmethod
    def _valid_ollama_host(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL, e.g. http://127.0.0.1:11434")
        return normalized

    @field_validator("db_host", "db_name", "db_user", "api_host", "mqtt_host", "mqtt_client_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @property
    def database_url(self) -> str:
        password = quote_plus(self.db_password.get_secret_value())
        user = quote_plus(self.db_user)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def summary(self) -> dict[str, Any]:
        """Non-secret configuration snapshot for logs and health output."""
        return {
            "db": f"{self.db_host}:{self.db_port}/{self.db_name}",
            "db_user": self.db_user,
            "api": f"{self.api_host}:{self.api_port}",
            "log_level": self.log_level,
            "data_directory": str(self.data_directory),
            "ollama_host": self.ollama_host,
            "chat_model": self.chat_model or "(unset)",
            "embedding_model": self.embedding_model or "(unset)",
            "mqtt": f"{self.mqtt_host}:{self.mqtt_port}",
            "mqtt_enabled": self.mqtt_enabled,
            "mqtt_tls": self.mqtt_tls,
            "mqtt_client_id": self.mqtt_client_id,
            "max_event_payload_bytes": self.max_event_payload_bytes,
        }


def _env_name_for(field: str) -> str:
    if field in _ENV_ALIASES:
        return _ENV_ALIASES[field]
    upper = field.upper()
    if upper.startswith("TALOS_AWARENESS_"):
        return upper
    return f"TALOS_AWARENESS_{upper}"


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Awareness configuration is invalid:"]
    for error in exc.errors():
        field = str(error["loc"][0]) if error.get("loc") else "(unknown)"
        env_name = _env_name_for(field)
        message = error.get("msg", "invalid value")
        if error.get("type") == "missing":
            message = "is required but not set"
        lines.append(f"  - {env_name}: {message}")
    lines.append(f"Set these in your environment or in {ENV_PATH} and retry.")
    return "\n".join(lines)


def load_settings(**overrides: Any) -> AwarenessSettings:
    """Load settings from env/.env, raising an actionable :class:`SettingsError`."""
    try:
        return AwarenessSettings(**overrides)
    except ValidationError as exc:
        raise SettingsError(_format_validation_error(exc)) from exc
