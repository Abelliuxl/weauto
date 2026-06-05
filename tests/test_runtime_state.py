import json
from types import SimpleNamespace

from wechat_rpa.bot import WeChatGuiRpaBot


def _runtime_bot(path):
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        log_verbose=False,
        heartbeat_enabled=True,
        heartbeat_interval_sec=43200.0,
        heartbeat_min_idle_sec=0.0,
    )
    bot._runtime_state_path = path
    bot._last_normal_reply_at = 0.0
    bot._last_heartbeat_at = 0.0
    bot._last_activity_at = 0.0
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
