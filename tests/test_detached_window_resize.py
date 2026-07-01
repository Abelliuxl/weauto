from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.config import load_config
from wechat_rpa.detached_window_receiver import DetachedWindowInfo


def test_load_config_accepts_detached_window_resize_settings(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "detached_window_resize_on_start = true",
                "detached_window_standard_width = 852",
                "detached_window_standard_height = 970",
                'ignore_exact_titles = ["微信", "WeChat"]',
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.detached_window_resize_on_start is True
    assert cfg.detached_window_standard_width == 852
    assert cfg.detached_window_standard_height == 970
    assert cfg.ignore_exact_titles == ["微信", "WeChat"]


def test_resize_detached_windows_on_start_only_resizes_mismatched_windows(monkeypatch):
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_window_resize_on_start=True,
        detached_window_standard_width=852,
        detached_window_standard_height=970,
    )
    bot._detached_windows = lambda: [
        DetachedWindowInfo(101, "WeChat", "群-临沧", 0, 25, 852, 970),
        DetachedWindowInfo(202, "WeChat", "群-魔兽", 0, 25, 1328, 970),
    ]
    calls = []

    def fake_set_size(window, *, width, height):
        calls.append((window.title, width, height))
        return True

    monkeypatch.setattr("wechat_rpa.bot.set_detached_wechat_window_size", fake_set_size)

    bot._resize_detached_windows_on_start()

    assert calls == [("群-魔兽", 852, 970)]


def test_detached_windows_excludes_main_wechat_window_by_exact_title():
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_window_title_filter=[],
        ignore_title_keywords=[],
        ignore_exact_titles=["微信"],
    )
    bot._enumerate_detached_windows = lambda: [
        DetachedWindowInfo(101, "WeChat", "微信", 0, 25, 852, 970),
        DetachedWindowInfo(202, "WeChat", "群-魔兽", 0, 25, 852, 970),
    ]

    windows = bot._detached_windows()

    assert [window.title for window in windows] == ["群-魔兽"]


def test_resize_detached_windows_on_start_is_disabled_by_config(monkeypatch):
    bot = object.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        detached_window_resize_on_start=False,
        detached_window_standard_width=852,
        detached_window_standard_height=970,
    )
    bot._detached_windows = lambda: [
        DetachedWindowInfo(202, "WeChat", "群-魔兽", 0, 25, 1328, 970),
    ]

    def fail_set_size(*_args, **_kwargs):
        raise AssertionError("resize must not run when disabled")

    monkeypatch.setattr("wechat_rpa.bot.set_detached_wechat_window_size", fail_set_size)

    bot._resize_detached_windows_on_start()
