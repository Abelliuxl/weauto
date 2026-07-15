from types import SimpleNamespace
import sys

from PIL import Image
import pytest

from wechat_rpa import detached_window_receiver
from wechat_rpa import sender as sender_module
from wechat_rpa import win32
from wechat_rpa.sender import WeChatGuiSender
from wechat_rpa.ocr_worker import _current_rss_mb


def test_windows_wechat_aliases_include_weixin_but_not_plugin_process():
    aliases = {item.casefold() for item in win32.app_aliases("WeChat|微信")}

    assert {"wechat", "weixin", "微信"}.issubset(aliases)
    assert "wechatappex" not in aliases


def test_detached_receiver_dispatches_capture_to_win32(monkeypatch):
    calls = []
    monkeypatch.setattr(detached_window_receiver, "IS_WINDOWS", True)
    monkeypatch.setattr(
        win32,
        "capture_window",
        lambda hwnd: calls.append(hwnd) or Image.new("RGB", (4, 5), "white"),
    )

    image = detached_window_receiver.capture_window_by_id(123, backend="screencapture")

    assert image.size == (4, 5)
    assert calls == [123]


def test_windows_text_send_uses_ctrl_v(monkeypatch):
    events = []
    monkeypatch.setattr(sender_module, "IS_WINDOWS", True)
    monkeypatch.setattr("pyperclip.copy", lambda text: events.append(("copy", text)))
    monkeypatch.setattr("pyautogui.hotkey", lambda *keys: events.append(("hotkey", keys)))
    monkeypatch.setattr("pyautogui.press", lambda key: events.append(("press", key)))
    monkeypatch.setattr(sender_module.time, "sleep", lambda _seconds: None)
    cfg = SimpleNamespace(send_after_paste_delay_sec=0.0)

    WeChatGuiSender(cfg).paste_and_send("Windows hello")

    assert events == [
        ("copy", "Windows hello"),
        ("hotkey", ("ctrl", "v")),
        ("press", "enter"),
    ]


def test_windows_send_does_not_fall_through_to_wrong_chat(monkeypatch):
    monkeypatch.setattr(sender_module, "IS_WINDOWS", True)
    sender = WeChatGuiSender(SimpleNamespace())
    monkeypatch.setattr(sender, "activate_chat_window", lambda _title: False)
    monkeypatch.setattr(
        sender,
        "paste_and_send",
        lambda _message: pytest.fail("must not send when the target window is missing"),
    )

    assert sender.paste_and_send_to_window("missing", "hello") is False


def test_windows_send_activates_without_clicking_input(monkeypatch):
    monkeypatch.setattr(sender_module, "IS_WINDOWS", True)
    events = []
    sender = WeChatGuiSender(SimpleNamespace())
    monkeypatch.setattr(
        sender,
        "activate_chat_window",
        lambda title: events.append(("activate", title)) or True,
    )
    monkeypatch.setattr(
        sender,
        "click_chat_input_for_window",
        lambda _title: pytest.fail("Windows foreground send must not move the mouse"),
    )
    monkeypatch.setattr(
        sender,
        "paste_and_send",
        lambda message: events.append(("send", message)),
    )

    assert sender.paste_and_send_to_window("target", "hello") is True
    assert events == [("activate", "target"), ("send", "hello")]


def test_windows_mention_activates_without_clicking_input(monkeypatch):
    monkeypatch.setattr(sender_module, "IS_WINDOWS", True)
    events = []
    sender = WeChatGuiSender(SimpleNamespace())
    monkeypatch.setattr(
        sender,
        "activate_chat_window",
        lambda title: events.append(("activate", title)) or True,
    )
    monkeypatch.setattr(
        sender,
        "click_chat_input_for_window",
        lambda _title: pytest.fail("Windows foreground mention must not move the mouse"),
    )
    monkeypatch.setattr(
        sender,
        "mention_and_send",
        lambda mention, message: events.append(("mention", mention, message)),
    )

    assert sender.mention_and_send_to_window("group", "person", "hello") is True
    assert events == [("activate", "group"), ("mention", "person", "hello")]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process API")
def test_windows_rss_probe_returns_working_set():
    assert _current_rss_mb() > 0
