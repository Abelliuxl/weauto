from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator


def test_nvidia_thinking_uses_chat_template_controls():
    llm = LlmReplyGenerator(LlmConfig())

    payload, controlled = llm._apply_reasoning_controls(
        {"model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "messages": []},
        "https://integrate.api.nvidia.com/v1",
        exclude=False,
        effort="high",
        think_mode="on",
        reasoning_budget=4096,
    )

    assert controlled is True
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert payload["reasoning_budget"] == 4096
    assert "reasoning_effort" not in payload
    assert "reasoning" not in payload


def test_nvidia_non_thinking_omits_reasoning_budget():
    llm = LlmReplyGenerator(LlmConfig())

    payload, controlled = llm._apply_reasoning_controls(
        {
            "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "messages": [],
            "reasoning_budget": 8192,
        },
        "https://integrate.api.nvidia.com/v1",
        exclude=False,
        effort="",
        think_mode="off",
        reasoning_budget=4096,
    )

    assert controlled is True
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_budget" not in payload
