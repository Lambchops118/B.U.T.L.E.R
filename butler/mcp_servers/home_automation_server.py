from __future__ import annotations

from .base import create_server, register_all
from .providers import register_home_automation_tools


def create_home_automation_server():
    server = create_server("butler-home-automation")
    return register_all(
        server,
        [
            register_home_automation_tools,
        ],
    )
