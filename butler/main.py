from __future__ import annotations

import queue
import threading
import time

from butler import router
from butler.config import load_environment
from butler.display import DisplayState
from butler.scheduler import tasks
from butler.sms import server as sms_server
from butler.text import server as text_agent_server


def _start_agent_warmup() -> None:
    """Warm the agent's first-turn caches without delaying service startup."""
    from butler.agent.runtime import STARTUP_WARMUP_ENABLED, warm_agent_runtime

    if not STARTUP_WARMUP_ENABLED:
        return
    threading.Thread(
        target=warm_agent_runtime,
        name="butler-agent-warmup",
        daemon=True,
    ).start()


def main() -> int:
    load_environment()

    display_state = DisplayState()
    central_queue = queue.Queue()

    router_thread = threading.Thread(
        target=router.router_loop,
        args=(central_queue, display_state),
        daemon=True,
    )
    router_thread.start()

    text_server = text_agent_server.start_text_agent_server(
        central_queue,
        display_state=display_state,
    )
    sms = sms_server.start_sms_server(central_queue)
    scheduler = tasks.start_scheduler(display_state, central_queue)
    _start_agent_warmup()

    try:
        print("Main agent running headless. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping main agent.")
    finally:
        central_queue.put(None)
        router_thread.join(timeout=2)

        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

        text_agent_server.shutdown_text_agent_server(text_server)
        sms_server.shutdown_sms_server(sms)
        print("Exiting cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
