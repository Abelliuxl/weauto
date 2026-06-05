import re
import time
from datetime import datetime
from types import SimpleNamespace

from wechat_rpa.action_processor import ActionProcessor
from wechat_rpa.agent_store import ChatHistoryStore
from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.detector import ChatRowState


def _ts(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(time.mktime(datetime(year, month, day, hour, minute, 0).timetuple()))


def _make_row(title: str = "real刘晓亮") -> ChatRowState:
    return ChatRowState(
        row_idx=1,
        text=title,
        title=title,
        preview="",
        has_mention=False,
        has_unread_badge=False,
        fingerprint="test-row",
        click_x_ratio=-1.0,
        click_y_ratio=-1.0,
    )


def _make_bot(tmp_path):
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(agent_actions_max_per_turn=4, log_verbose=False)
    bot.chat_history = ChatHistoryStore(tmp_path / "chat_history")
    bot.appended_records = []
    bot._session_key_for_row = lambda row: "session-key"
    bot._title_key = lambda title: re.sub(r"\s+", "", str(title or "")).lower()[:24]
    bot._compact_web_text = lambda value, limit=240: str(value)[:limit]
    bot._append_session_record = lambda *args, **kwargs: bot.appended_records.append((args, kwargs))
    return bot


def test_read_chat_history_by_date_returns_timestamped_daily_records(tmp_path):
    bot = _make_bot(tmp_path)
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 4, 23, 59), "role": "user", "sender": "旧消息", "text": "昨天的内容"},
    )
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 5, 0, 40), "role": "user", "sender": "陈冠东", "text": "节点居然可以不打中间这个大蝙蝠？"},
    )
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 5, 1, 3), "role": "user", "sender": "陈冠东", "text": "卧槽"},
    )

    trace, obs = ActionProcessor(bot).execute_agent_actions(
        _make_row(),
        [
            {
                "tool": "read_chat_history_by_date",
                "args": {"chat_title": "群-临沧", "date": "2026-06-05", "max_items": 800},
            }
        ],
        is_admin=True,
    )

    assert "read_chat_history_by_date -> ok" in trace
    assert "聊天记录[群-临沧][2026-06-05] 总记录=2" in obs
    assert "00:40 U(陈冠东): 节点居然可以不打中间这个大蝙蝠？" in obs
    assert "01:03 U(陈冠东): 卧槽" in obs
    assert "昨天的内容" not in obs
    assert bot.appended_records == []


def test_summarize_chat_history_returns_compact_material_by_hour_and_speaker(tmp_path):
    bot = _make_bot(tmp_path)
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 5, 0, 2), "role": "user", "sender": "朱洋", "text": "今天副本路线怎么走？"},
    )
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 5, 0, 18), "role": "user", "sender": "陈冠东", "text": "[图片] 游戏内地图界面，讨论节点路线"},
    )
    bot.chat_history.append(
        "群-临沧",
        {"observed_at": _ts(2026, 6, 5, 1, 3), "role": "user", "sender": "陈冠东", "text": "节点可以跳过中间大蝙蝠"},
    )

    trace, obs = ActionProcessor(bot).execute_agent_actions(
        _make_row(),
        [{"tool": "summarize_chat_history", "args": {"chat_title": "群-临沧", "date": "2026-06-05"}}],
        is_admin=True,
    )

    assert "summarize_chat_history -> ok" in trace
    assert "群聊摘要素材[群-临沧][2026-06-05]" in obs
    assert "总记录=3" in obs
    assert "活跃发言人=陈冠东(2)、朱洋(1)" in obs
    assert "[00:00]" in obs
    assert "[01:00]" in obs
    assert "[图片] 游戏内地图界面，讨论节点路线" in obs
    assert bot.appended_records == []
