from types import SimpleNamespace

from wechat_rpa.action_processor import ActionProcessor


class FakeWebBot:
    def __init__(self):
        self.cfg = SimpleNamespace(agent_actions_max_per_turn=4, log_verbose=False)
        self.fetch_calls = []
        self.browse_calls = []
        self.records = []

    def _session_key_for_row(self, row):
        return "session-key"

    def _fetch_url(self, url, *, max_chars, use_proxy):
        self.fetch_calls.append((url, max_chars, use_proxy))
        raise RuntimeError("fetch_url http error: 403 CloudFront denied")

    def _browse_url(self, url, *, max_chars, use_proxy):
        self.browse_calls.append((url, max_chars, use_proxy))
        return "rendered page text"

    def _compact_web_text(self, value, limit):
        return str(value)[:limit]

    def _append_session_record(self, *args, **kwargs):
        self.records.append((args, kwargs))


def test_fetch_url_403_falls_back_to_browse_url():
    bot = FakeWebBot()
    row = SimpleNamespace(title="real刘晓亮", row_idx=1)

    trace, obs = ActionProcessor(bot).execute_agent_actions(
        row,
        [{"tool": "fetch_url", "args": {"url": "https://example.com/page", "proxy": True}}],
        is_admin=True,
    )

    assert "403 fallback browse_url" in trace
    assert "rendered page text" in obs
    assert bot.fetch_calls == [("https://example.com/page", 6000, True)]
    assert bot.browse_calls == [("https://example.com/page", 10000, True)]
    assert bot.records


def test_wowhead_fetch_url_is_auto_upgraded_to_browse_url():
    bot = FakeWebBot()
    row = SimpleNamespace(title="real刘晓亮", row_idx=1)

    trace, obs = ActionProcessor(bot).execute_agent_actions(
        row,
        [{"tool": "fetch_url", "args": {"url": "https://www.wowhead.com/news", "proxy": True}}],
        is_admin=True,
    )

    assert "auto browse_url" in trace
    assert "rendered page text" in obs
    assert bot.fetch_calls == []
    assert bot.browse_calls == [("https://www.wowhead.com/news", 10000, True)]
