from __future__ import annotations

from InfoPanel import screen

from butler.config import load_environment

from .client import RemoteDisplayQueue


def main() -> int:
    load_environment()
    updates = RemoteDisplayQueue()
    updates.start()
    try:
        screen.run_info_panel_gui(updates, 0.75)
    finally:
        updates.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
