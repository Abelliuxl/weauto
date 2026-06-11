import json
from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.visible_message_state import VisibleMessageStateStore


def _runtime_bot(path):
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        log_verbose=False,
        heartbeat_enabled=True,
        heartbeat_interval_sec=43200.0,
        heartbeat_min_idle_sec=0.0,
        receiver_mode="detached_windows",
    )
    bot._runtime_state_path = path
    bot._last_normal_reply_at = 0.0
    bot._last_heartbeat_at = 0.0
    bot._last_activity_at = 0.0
    bot.visible_message_state = VisibleMessageStateStore()
    bot._detached_bootstrapped = False
    bot._detached_watchdog_resume_window_ids = set()
    return bot


def test_runtime_state_persists_reply_heartbeat_and_activity_timestamps(tmp_path):
    path = tmp_path / "runtime_state.json"
    bot = _runtime_bot(path)

    bot._mark_normal_reply_at(1000.0)
    bot._mark_heartbeat_at(2000.0)
    bot._mark_activity_at(1500.0)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["last_normal_reply_at"] == 1000.0
    assert raw["last_heartbeat_at"] == 2000.0
    assert raw["last_activity_at"] == 1500.0

    restored = _runtime_bot(path)
    restored._load_runtime_state()

    assert restored._last_normal_reply_at == 1000.0
    assert restored._last_heartbeat_at == 2000.0
    assert restored._last_activity_at == 1500.0


def test_restored_heartbeat_timestamp_keeps_interval_after_restart(tmp_path):
    bot = _runtime_bot(tmp_path / "runtime_state.json")
    bot._last_heartbeat_at = 1000.0
    calls = []
    bot._run_heartbeat = lambda now, rows: calls.append((now, rows)) or True

    assert bot._maybe_run_heartbeat(1100.0, []) is False
    assert calls == []

    assert bot._maybe_run_heartbeat(44200.0, []) is True
    assert calls == [(44200.0, [])]
    assert bot._last_heartbeat_at == 44200.0


def test_runtime_state_restores_detached_watchdog_resume_and_consumes_marker(tmp_path):
    path = tmp_path / "runtime_state.json"
    bot = _runtime_bot(path)
    messages = [
        {
            "side": "other",
            "sender": "群-临沧",
            "content_type": "text",
            "text": "昨天群-临沧聊了什么",
            "bbox": [0, 0, 10, 10],
            "fingerprint": "vision|群-临沧|0",
            "source": "detached_window_vision_json",
        }
    ]
    seeded = bot.visible_message_state.update(window_id=42, messages=messages)
    assert [item["text"] for item in seeded] == ["昨天群-临沧聊了什么"]

    bot._save_runtime_state(detached_watchdog_resume=True)

    restored = _runtime_bot(path)
    restored._load_runtime_state()

    assert restored._detached_bootstrapped is True
    assert restored._detached_watchdog_resume_window_ids == {42}
    restored_messages = restored.visible_message_state.messages_for_window(42)
    assert [item["text"] for item in restored_messages] == ["昨天群-临沧聊了什么"]

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "detached_watchdog_resume" not in raw
