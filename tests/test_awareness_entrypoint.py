from __future__ import annotations

from talos.awareness import __main__ as awareness_main


def test_windows_entrypoint_selects_mqtt_compatible_event_loop(monkeypatch) -> None:
    selected = []
    sentinel_policy = object()

    monkeypatch.setattr(awareness_main.sys, "platform", "win32")
    monkeypatch.setattr(
        awareness_main.asyncio,
        "WindowsSelectorEventLoopPolicy",
        lambda: sentinel_policy,
        raising=False,
    )
    monkeypatch.setattr(
        awareness_main.asyncio,
        "set_event_loop_policy",
        selected.append,
    )

    awareness_main._configure_windows_event_loop_policy()

    assert selected == [sentinel_policy]


def test_non_windows_entrypoint_leaves_event_loop_policy_unchanged(monkeypatch) -> None:
    selected = []

    monkeypatch.setattr(awareness_main.sys, "platform", "linux")
    monkeypatch.setattr(
        awareness_main.asyncio,
        "set_event_loop_policy",
        selected.append,
    )

    awareness_main._configure_windows_event_loop_policy()

    assert selected == []


def test_windows_uvicorn_uses_selector_loop(monkeypatch) -> None:
    monkeypatch.setattr(awareness_main.sys, "platform", "win32")

    assert (
        awareness_main._uvicorn_loop_factory()
        is awareness_main.asyncio.SelectorEventLoop
    )


def test_non_windows_uvicorn_keeps_automatic_loop_selection(monkeypatch) -> None:
    monkeypatch.setattr(awareness_main.sys, "platform", "linux")

    assert awareness_main._uvicorn_loop_factory() == "auto"
