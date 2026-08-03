from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS menu-bar only")


def _bare_app():
    from app.bar.menu_app import ControlBarApp

    app = object.__new__(ControlBarApp)
    calls: list[str] = []
    app.sup = SimpleNamespace(
        restart_bot=lambda: calls.append("restart"),
        stop_bot=lambda: calls.append("stop"),
        start_bot=lambda: calls.append("start"),
    )
    return app, calls


def test_restart_runs_when_notification_is_unavailable(monkeypatch) -> None:
    from app.bar import menu_app

    app, calls = _bare_app()

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(menu_app.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        menu_app.rumps,
        "notification",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no bundle id")),
    )

    app.on_restart(None)

    assert calls == ["restart"]


def test_start_and_stop_actions_are_dispatched(monkeypatch) -> None:
    app, calls = _bare_app()
    dispatched: list[str] = []
    monkeypatch.setattr(
        app,
        "_run_action",
        lambda name, action: (dispatched.append(name), action()),
    )
    monkeypatch.setattr(app, "_notify", lambda *_args: None)

    app.on_stop(None)
    app.on_start(None)

    assert dispatched == ["stop", "start"]
    assert calls == ["stop", "start"]
