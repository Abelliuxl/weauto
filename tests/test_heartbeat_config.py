from types import SimpleNamespace
import threading

from wechat_rpa.bot import SessionState, WeChatGuiRpaBot


def _session(history: list[dict]) -> SessionState:
    return SessionState(
        short=[],
        history=history,
        summary="",
        muted=False,
        titles=set(),
    )


def _history(count: int) -> list[dict]:
    return [
        {
            "role": "user",
            "sender": f"sender{i:03d}",
            "text": f"message {i}",
            "observed_at": 1000 + i,
        }
        for i in range(count)
    ]


def test_heartbeat_recent_people_uses_configurable_limit_and_history_window() -> None:
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(people_aliases_enabled=False)
    bot._state_lock = threading.RLock()
    bot._session_index = {}
    bot._sessions = {"chat": _session(_history(150))}

    people = bot._heartbeat_recent_people(max_people=10, per_chat_records=100)

    assert people == [f"sender{i:03d}" for i in range(149, 139, -1)]


def test_run_heartbeat_uses_configured_people_settings_and_action_budget() -> None:
    bot = WeChatGuiRpaBot.__new__(WeChatGuiRpaBot)
    bot.cfg = SimpleNamespace(
        heartbeat_enabled=True,
        agent_actions_enabled=True,
        admin_commands_enabled=False,
        heartbeat_max_actions=5,
        heartbeat_max_people=10,
        heartbeat_people_history_records=100,
        log_verbose=False,
    )
    bot.llm_heartbeat = object()
    bot.agent_people = SimpleNamespace(read=lambda name: "")
    bot._memory_dirty = False
    bot._heartbeat_llm_backends = lambda: [("heartbeat", bot.llm_heartbeat)]
    bot._heartbeat_virtual_row = lambda: SimpleNamespace(title="__heartbeat__", row_idx=-1)
    bot._is_admin_session = lambda row: False
    bot._heartbeat_recent_chat_activity = lambda **kwargs: "recent chat"
    bot._heartbeat_memory_prompt_text = lambda *args, **kwargs: "memory prompt"
    bot._heartbeat_person_prompt_text = lambda *args, **kwargs: "person prompt"

    people_args = []
    bot._heartbeat_recent_people = lambda **kwargs: people_args.append(kwargs) or [
        f"sender{i}" for i in range(10)
    ]

    action_calls = []

    def execute_actions(row, actions, *, is_admin, max_actions_override=None):
        action_calls.append(actions[0]["tool"])
        return "", ""

    planner_calls = []

    def planner_loop(**kwargs):
        tool = kwargs["tools"][0]
        planner_calls.append(tool)
        count = 2 if tool == "remember_fact" else 1
        return "", "", count, True, {}

    bot._execute_agent_actions = execute_actions
    bot._run_agent_planner_loop = planner_loop

    assert bot._run_heartbeat(1000.0, []) is True

    assert people_args == [{"max_people": 10, "per_chat_records": 100}]
    assert action_calls == ["read_memory", "read_impression"]
    assert planner_calls == ["remember_fact", "update_impression"]
