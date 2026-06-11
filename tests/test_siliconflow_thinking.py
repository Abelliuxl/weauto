from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator


def test_siliconflow_thinking_on_uses_provider_specific_controls() -> None:
    client = LlmReplyGenerator(LlmConfig())

    payload, controlled = client._apply_reasoning_controls(
        {"model": "Qwen/Qwen3.6-35B-A3B"},
        "https://api.siliconflow.cn/v1",
        exclude=False,
        effort="",
        think_mode="on",
        reasoning_budget=4096,
    )

    assert controlled is True
    assert payload["enable_thinking"] is True
    assert payload["thinking_budget"] == 4096
    assert "think" not in payload
    assert "reasoning" not in payload


def test_siliconflow_thinking_off_disables_thinking() -> None:
    client = LlmReplyGenerator(LlmConfig())

    payload, controlled = client._apply_reasoning_controls(
        {"model": "Qwen/Qwen3.6-35B-A3B", "thinking_budget": 4096},
        "https://api.siliconflow.cn/v1",
        exclude=False,
        effort="",
        think_mode="off",
        reasoning_budget=4096,
    )

    assert controlled is True
    assert payload["enable_thinking"] is False
    assert "thinking_budget" not in payload
