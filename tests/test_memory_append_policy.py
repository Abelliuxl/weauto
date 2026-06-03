from types import SimpleNamespace

from wechat_rpa.action_processor import ActionProcessor, _append_unique_note


class FakeMemoryStore:
    def __init__(self, files):
        self.files = dict(files)

    def normalize_name(self, name):
        return "timeline" if str(name).lower() == "timeline" else "core"

    def read(self, name):
        return self.files.get(self.normalize_name(name), "")

    def write(self, name, content):
        self.files[self.normalize_name(name)] = content

    def backup(self, name):
        return None


class FakeReadMemoryBot:
    def __init__(self, files):
        self.cfg = SimpleNamespace(agent_actions_max_per_turn=4, log_verbose=False)
        self.agent_memory = FakeMemoryStore(files)

    def _session_key_for_row(self, row):
        return "session-key"

    def _append_session_record(self, *args, **kwargs):
        raise AssertionError("memory tools should not be appended to chat history")


def test_append_unique_note_dedups_source_time_and_light_rewording():
    existing = (
        "# timeline\n\n"
        "## 追加记忆\n"
        "- 2026-05-28: 刘晓亮说代练还没打完评级，怀疑代练拿他号凑人。"
        "（来源：heartbeat memory phase 2026-05-28 22:16）\n"
    )

    merged, changed = _append_unique_note(
        existing,
        heading="## 追加记忆",
        bullet=(
            "- 2026-05-28: 刘晓亮说评级代练还没打完，怀疑自己的号被拿去凑人。"
            "（来源：heartbeat memory phase 2026-05-28 22:26）"
        ),
    )

    assert changed is False
    assert merged == existing


def test_append_unique_note_reuses_existing_heading_for_new_fact():
    existing = (
        "# 张三\n\n"
        "## 补充观察\n"
        "- 2026-05-28: 张三喜欢玩战士。\n"
    )

    merged, changed = _append_unique_note(
        existing,
        heading="## 补充观察",
        bullet="- 2026-05-29: 张三最近在练法师。",
    )

    assert changed is True
    assert merged.count("## 补充观察") == 1
    assert "- 2026-05-28: 张三喜欢玩战士。" in merged
    assert "- 2026-05-29: 张三最近在练法师。" in merged


def test_append_unique_note_replaces_similar_fact_when_new_one_is_more_complete():
    existing = (
        "# timeline\n\n"
        "## 追加记忆\n"
        "- 2026-05-28: 刘晓亮说代练还没打完评级。\n"
    )

    merged, changed = _append_unique_note(
        existing,
        heading="## 追加记忆",
        bullet="- 2026-05-28: 刘晓亮说代练还没打完评级，怀疑代练拿他的号给别人凑人。",
    )

    assert changed is True
    assert "怀疑代练拿他的号给别人凑人" in merged
    assert merged.count("刘晓亮说代练还没打完评级") == 1


def test_append_unique_note_collapses_duplicate_headings_and_prunes_old_bullets():
    existing = (
        "# timeline\n\n"
        "## 追加记忆\n"
        "- 2026-05-26: 旧事实一。\n\n"
        "## 其他\n"
        "- 保留。\n\n"
        "## 追加记忆\n"
        "- 2026-05-27: 旧事实二。\n"
    )

    merged, changed = _append_unique_note(
        existing,
        heading="## 追加记忆",
        bullet="- 2026-05-28: 新事实三。",
        max_items=2,
    )

    assert changed is True
    assert merged.count("## 追加记忆") == 1
    assert "旧事实一" not in merged
    assert "旧事实二" in merged
    assert "新事实三" in merged
    assert "## 其他" in merged
    assert "- 保留。" in merged


def test_read_memory_returns_tail_for_long_append_only_files():
    long_core = "开头记忆\n" + ("旧内容\n" * 1600) + "最新尾部事实: 不要重复追加\n"
    bot = FakeReadMemoryBot({"core": long_core, "timeline": ""})
    row = SimpleNamespace(title="admin", row_idx=1)

    trace, obs = ActionProcessor(bot).execute_agent_actions(
        row,
        [{"tool": "read_memory", "args": {"name": "core"}}],
        is_admin=True,
    )

    assert "read_memory -> ok" in trace
    assert "开头记忆" in obs
    assert "最新尾部事实: 不要重复追加" in obs
    assert "中间已省略" in obs


def test_read_memory_all_keeps_tail_from_core_and_timeline():
    long_core = "core开头\n" + ("core旧内容\n" * 1200) + "core最新事实\n"
    long_timeline = "timeline开头\n" + ("timeline旧内容\n" * 1200) + "timeline最新事件\n"
    bot = FakeReadMemoryBot({"core": long_core, "timeline": long_timeline})
    row = SimpleNamespace(title="admin", row_idx=1)

    _, obs = ActionProcessor(bot).execute_agent_actions(
        row,
        [{"tool": "read_memory", "args": {"name": "all"}}],
        is_admin=True,
    )

    assert "core开头" in obs
    assert "core最新事实" in obs
    assert "timeline开头" in obs
    assert "timeline最新事件" in obs
