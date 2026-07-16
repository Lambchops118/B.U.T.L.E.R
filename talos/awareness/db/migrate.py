"""Programmatic Alembic helpers shared by the CLI, health checks, and tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_INI_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI_PATH))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to_head(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")


def expected_head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()
