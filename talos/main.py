from __future__ import annotations

import queue
import threading

from InfoPanel import kitchen_screen
from InfoPanel import screen as info_panel_screen

from talos import router
from talos.config import load_environment
from talos.scheduler import tasks
from talos.text import server as text_agent_server


DISPLAY_MODE = "info_panel"
DISPLAY_SCALE = 0.75


def _start_agent_warmup() -> None:
    """Warm the agent's first-turn caches without delaying the GUI."""
    from talos.agent.runtime import STARTUP_WARMUP_ENABLED, warm_agent_runtime

    if not STARTUP_WARMUP_ENABLED:
        return
    threading.Thread(
        target=warm_agent_runtime,
        name="talos-agent-warmup",
        daemon=True,
    ).start()


def main() -> int:
    load_environment()

    gui_queue = queue.Queue()
    central_queue = queue.Queue()

    router_thread = threading.Thread(
        target=router.router_loop,
        args=(central_queue, gui_queue),
        daemon=True,
    )
    router_thread.start()

    text_server = text_agent_server.start_text_agent_server(central_queue)
    scheduler = tasks.start_scheduler(gui_queue, central_queue)
    _start_agent_warmup()

    try:
        if DISPLAY_MODE == "info_panel":
            info_panel_screen.run_info_panel_gui(gui_queue, DISPLAY_SCALE)
        elif DISPLAY_MODE == "kitchen":
            kitchen_screen.screen_main()
    finally:
        central_queue.put(None)
        router_thread.join(timeout=2)

        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

        text_agent_server.shutdown_text_agent_server(text_server)
        print("Exiting cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
