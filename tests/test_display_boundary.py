from __future__ import annotations

import ipaddress
import json
import queue
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from butler.display.client import RemoteDisplayQueue
from butler.display.state import DisplayState
from butler.text.server import TextAgentHTTPServer, TextServerConfig


def _config() -> TextServerConfig:
    return TextServerConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        api_token="display-secret",
        request_timeout=5,
        terminal_request_timeout=0,
        allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),),
        phone_push_token="",
    )


class DisplayStateTests(unittest.TestCase):
    def test_legacy_router_messages_update_latest_snapshot(self) -> None:
        state = DisplayState()
        state.put(("VOICE_CMD", "Butler, status", "Everything is nominal."))
        state.put(("STATUS", {"temp": 72, "weather": "clear"}))
        state.put(("STATUS", {"temp": 73}))

        snapshot = state.snapshot()
        self.assertEqual(snapshot["sequence"], 3)
        self.assertEqual(snapshot["last_command"], "Butler, status")
        self.assertEqual(snapshot["last_response"], "Everything is nominal.")
        self.assertEqual(snapshot["statuses"], {"temp": 73, "weather": "clear"})

    def test_snapshot_is_not_mutable_shared_state(self) -> None:
        state = DisplayState()
        state.put(("STATUS", {"nested": {"value": 1}}))
        snapshot = state.snapshot()
        snapshot["statuses"]["nested"]["value"] = 2
        self.assertEqual(state.snapshot()["statuses"]["nested"]["value"], 1)

    def test_client_heartbeat_is_bounded_and_visible(self) -> None:
        state = DisplayState()
        state.record_client_heartbeat("panel-1", sequence=7)
        client = state.snapshot()["clients"]["panel-1"]
        self.assertEqual(client["sequence"], 7)
        self.assertEqual(client["status"], "rendered")


class DisplayEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = DisplayState()
        self.state.put(("VOICE_CMD", "hello", "good evening"))
        self.server = TextAgentHTTPServer(
            ("127.0.0.1", 0),
            queue.Queue(),
            _config(),
            display_state=self.state,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/display/state"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _get(self, token: str | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(self.url)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_state_requires_auth_and_includes_sleep_projection(self) -> None:
        self.assertEqual(self._get()[0], 401)
        with mock.patch("butler.text.server.sleep_mode.state", return_value={"asleep": True, "display_level": 0.05}):
            status, payload = self._get("display-secret")
        self.assertEqual(status, 200)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["last_command"], "hello")
        self.assertEqual(payload["sleep"], {"asleep": True, "display_level": 0.05})

    def test_display_heartbeat_requires_auth_and_records_sequence(self) -> None:
        request = urllib.request.Request(
            self.url.replace("/display/state", "/display/heartbeat"),
            data=json.dumps({"client_id": "panel-1", "sequence": 9}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer display-secret",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.state.snapshot()["clients"]["panel-1"]["sequence"], 9)

    def test_display_state_piggybacks_render_acknowledgement(self) -> None:
        request = urllib.request.Request(
            self.url + "?client_id=panel-2&sequence=11",
            headers={"Authorization": "Bearer display-secret"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
        self.assertEqual(self.state.snapshot()["clients"]["panel-2"]["sequence"], 11)


class RemoteDisplayQueueTests(unittest.TestCase):
    def test_queue_keeps_only_the_latest_snapshot(self) -> None:
        updates = RemoteDisplayQueue(base_url="http://example", token="x", poll_seconds=1)
        updates._publish({"sequence": 1})
        updates._publish({"sequence": 2})
        self.assertEqual(updates.get_nowait(), ("DISPLAY_STATE", {"sequence": 2}))
        with self.assertRaises(queue.Empty):
            updates.get_nowait()
        self.assertIn("sequence=2", updates._state_path())


class LauncherBoundaryTests(unittest.TestCase):
    def test_main_and_voice_share_runtime_while_display_is_separate(self) -> None:
        from butler.launcher.config import VENV_DIRS

        self.assertEqual(VENV_DIRS["main"], ".venv-runtime")
        self.assertEqual(VENV_DIRS["voice"], ".venv-runtime")
        self.assertEqual(VENV_DIRS["display"], ".venv-display")

    def test_local_display_is_pointed_at_the_local_agent(self) -> None:
        from butler.launcher.config import LauncherConfig
        from butler.launcher.core import Supervisor

        supervisor = Supervisor(LauncherConfig(), log=lambda *_args: None)
        with mock.patch("butler.launcher.core.venv_python", return_value=Path("python")), mock.patch.object(
            supervisor, "_spawn"
        ) as spawn:
            supervisor._start_display({"BUTLER_DISPLAY_AGENT_URL": "http://remote:8420"})

        name, command, child_env = spawn.call_args.args
        self.assertEqual(name, "display")
        self.assertEqual(command[-2:], ["-m", "butler.display.worker"])
        self.assertEqual(child_env["BUTLER_DISPLAY_AGENT_URL"], "http://127.0.0.1:8420")


if __name__ == "__main__":
    unittest.main()
