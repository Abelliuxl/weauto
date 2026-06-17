from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from wechat_rpa import detached_window_receiver


def test_capture_window_by_id_uses_temp_file_not_dash(monkeypatch):
    calls = []

    def fake_run(cmd, *, capture_output, timeout):
        calls.append(cmd)
        assert capture_output is True
        assert timeout == 15
        assert cmd[:4] == ["screencapture", "-l", "123", "-x"]
        assert cmd[-1] != "-"
        Image.new("RGB", (2, 2), "white").save(cmd[-1])
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(detached_window_receiver.subprocess, "run", fake_run)

    image = detached_window_receiver.capture_window_by_id(123, backend="screencapture")

    assert image.size == (2, 2)
    assert calls
    assert not Path(calls[0][-1]).exists()


def test_capture_window_by_id_quartz_backend(monkeypatch):
    calls = []

    def fake_quartz(window_id):
        calls.append(window_id)
        return Image.new("RGB", (3, 3), "white")

    monkeypatch.setattr(detached_window_receiver, "_capture_window_by_id_quartz", fake_quartz)

    image = detached_window_receiver.capture_window_by_id(456, backend="quartz")

    assert image.size == (3, 3)
    assert calls == [456]


def test_screen_capture_access_helpers(monkeypatch):
    monkeypatch.setattr(
        detached_window_receiver.Quartz,
        "CGPreflightScreenCaptureAccess",
        lambda: False,
    )
    monkeypatch.setattr(
        detached_window_receiver.Quartz,
        "CGRequestScreenCaptureAccess",
        lambda: True,
    )

    assert detached_window_receiver.screen_capture_access_granted() is False
    assert detached_window_receiver.request_screen_capture_access() is True


def test_visible_window_owner_summary(monkeypatch):
    monkeypatch.setattr(
        detached_window_receiver.Quartz,
        "CGWindowListCopyWindowInfo",
        lambda *_args: [
            {"kCGWindowLayer": 0, "kCGWindowOwnerName": "WeChat"},
            {"kCGWindowLayer": 0, "kCGWindowOwnerName": "WeChat"},
            {"kCGWindowLayer": 0, "kCGWindowOwnerName": ""},
            {"kCGWindowLayer": 25, "kCGWindowOwnerName": "WeChat"},
        ],
    )

    assert detached_window_receiver.visible_window_owner_summary() == "WeChat:2, <unnamed>:1"
