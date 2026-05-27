from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator


def test_json_repair_handles_cjk_inner_quotes_without_regex_escape_error():
    llm = object.__new__(LlmReplyGenerator)
    raw = '{"actions":[],"reply_hint":"他说"魔法少女"来了","send_reply":true,"task":{"status":"idle"}}'

    parsed = llm._extract_json_payload(raw)

    assert parsed["reply_hint"] == "他说“魔法少女”来了"


def test_agent_planner_parses_native_tool_calls():
    llm = LlmReplyGenerator(
        LlmConfig(
            enabled=True,
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        )
    )

    def fake_post(payload):
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "run_python",
                                    "arguments": '{"code":"print(pow(2, 10))"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

    llm._post_openai_chat_completion = fake_post  # type: ignore[method-assign]
    plan = llm.plan_actions(
        title="real刘晓亮",
        is_group=False,
        reason="new_message",
        latest_message="帮我算 2 的 10 次方",
        available_tools=["run_python", "web_search"],
        max_actions=2,
    )

    assert plan["actions"] == [
        {
            "tool": "run_python",
            "args": {"code": "print(pow(2, 10))"},
            "reason": "tool_call",
        }
    ]


def test_agent_planner_ignores_native_text_reply_when_no_tool_calls():
    llm = LlmReplyGenerator(
        LlmConfig(
            enabled=True,
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
        )
    )

    def fake_post(payload):
        assert "tools" in payload
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "老刘，我当前有的 skills 如下：不该由 planner 直接回复。",
                    }
                }
            ]
        }

    llm._post_openai_chat_completion = fake_post  # type: ignore[method-assign]
    plan = llm.plan_actions(
        title="real刘晓亮",
        is_group=False,
        reason="new_message",
        latest_message="告诉我你现在有的skills",
        available_tools=["read_chat_history", "run_python"],
        max_actions=2,
    )

    assert plan["actions"] == []
    assert plan["reply_hint"] == ""
    assert plan["send_reply"] is True
    assert plan["task"]["status"] == "idle"
