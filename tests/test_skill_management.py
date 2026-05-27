import tempfile
from pathlib import Path

from wechat_rpa.agent_store import SkillStore
from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator
from wechat_rpa.workspace_context import WorkspaceContextManager


def test_skill_store_writes_to_data_skills_shape():
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        store = SkillStore(root / "skills")

        assert store.normalize_name("data/skills/foo/SKILL.md") == "foo"
        store.write("../测试 skill", "# Test\n")

        assert store.list() == ["测试-skill"]
        assert store.read("测试 skill") == "# Test\n"
        assert (root / "skills" / "测试-skill" / "SKILL.md").is_file()

        store.delete("测试 skill")
        assert store.list() == []


def test_workspace_context_loads_data_skills():
    manager = WorkspaceContextManager("agent_workspace", enabled=True)

    context = manager.build_prompt_context(include_long_term=False)

    assert "[skills (" in context
    assert "wow-character-link" in context
    assert "build_wow_character_url" in context


def test_wow_character_link_skill_builder_is_callable():
    bot = object.__new__(WeChatGuiRpaBot)

    result = bot._build_wow_character_url({"player": "吴松竹", "class_name": "战士"})

    assert result["ok"] is True
    assert result["character"] == "体育老师"
    assert "wow.blizzard.cn/character" in result["url"]


def test_agent_planner_parses_wx_cli_skill_tools_from_native_tool_calls():
    llm = LlmReplyGenerator(
        LlmConfig(
            enabled=True,
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        )
    )

    def fake_post(payload):
        tool_names = {item["function"]["name"] for item in payload["tools"]}
        assert {"write_skill", "build_wow_character_url", "fetch_url"} <= tool_names
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "build_wow_character_url",
                                    "arguments": '{"player":"吴松竹","class_name":"战士"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

    llm._post_openai_chat_completion = fake_post  # type: ignore[method-assign]
    plan = llm.plan_actions(
        title="群-临沧",
        is_group=True,
        reason="mention",
        latest_message="吴工战士号主页发我",
        available_tools=["write_skill", "build_wow_character_url", "fetch_url"],
        max_actions=2,
    )

    assert plan["actions"] == [
        {
            "tool": "build_wow_character_url",
            "args": {"player": "吴松竹", "class_name": "战士"},
            "reason": "tool_call",
        }
    ]
