from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator


def test_mimo_thinking_on_uses_provider_specific_control() -> None:
    llm = LlmReplyGenerator(LlmConfig())

    payload, controlled = llm._apply_reasoning_controls(
        {"model": "mimo-v2.5", "messages": []},
        "https://token-plan-cn.xiaomimimo.com/v1",
        exclude=False,
        effort="max",
        think_mode="on",
    )

    assert controlled is True
    assert payload["thinking"] == {"type": "enabled"}
    assert "reasoning_effort" not in payload
    assert "reasoning" not in payload


def test_mimo_thinking_off_uses_provider_specific_control() -> None:
    llm = LlmReplyGenerator(LlmConfig())

    payload, controlled = llm._apply_reasoning_controls(
        {"model": "mimo-v2.5", "messages": []},
        "https://token-plan-cn.xiaomimimo.com/v1",
        exclude=True,
        effort="high",
        think_mode="off",
    )

    assert controlled is True
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    assert "reasoning" not in payload
