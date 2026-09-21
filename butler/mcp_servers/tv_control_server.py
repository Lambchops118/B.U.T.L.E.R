from __future__ import annotations

from .base import create_server, register_all
from .providers import register_tv_control_tools


def create_tv_control_server():
    server = create_server("butler-tv-control")
    return register_all(
        server,
        [
            register_tv_control_tools,
        ],
    )
