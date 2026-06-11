import shutil
import subprocess
import tempfile
from pathlib import Path

from wechat_rpa.config import LlmConfig
from wechat_rpa.llm import LlmReplyGenerator
from wechat_rpa.qweather import QWeatherClient, build_qweather_jwt


def test_agent_planner_parses_query_weather_native_tool_call():
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
        assert "query_weather" in tool_names
        return {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "query_weather",
                                    "arguments": '{"location":"临沧","days":7,"date":"2026-06-09","mode":"forecast"}',
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
        latest_message="临沧天气怎么样",
        available_tools=["query_weather"],
        max_actions=2,
    )

    assert plan["actions"] == [
        {
            "tool": "query_weather",
            "args": {"location": "临沧", "days": 7, "date": "2026-06-09", "mode": "forecast"},
            "reason": "tool_call",
        }
    ]


def test_qweather_client_requires_jwt_credentials():
    client = QWeatherClient(api_host="https://example.qweatherapi.com", auth_type="jwt")

    assert client.is_configured() is False
    assert "qweather_jwt_key_id" in client.status_text()


def test_qweather_forecast_endpoint_days():
    assert QWeatherClient._forecast_endpoint_days(1) == 3
    assert QWeatherClient._forecast_endpoint_days(3) == 3
    assert QWeatherClient._forecast_endpoint_days(4) == 7
    assert QWeatherClient._forecast_endpoint_days(8) == 10
    assert QWeatherClient._forecast_endpoint_days(11) == 15
    assert QWeatherClient._forecast_endpoint_days(16) == 30


def test_build_qweather_jwt_shape_with_ed25519_key():
    if not shutil.which("openssl"):
        return

    with tempfile.TemporaryDirectory() as raw_dir:
        key_path = Path(raw_dir) / "ed25519-private.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key_path)],
            check=True,
            capture_output=True,
        )

        token = build_qweather_jwt(
            key_id="test-kid",
            project_id="test-project",
            private_key_path=str(key_path),
            ttl_sec=900,
        )

    parts = token.split(".")
    assert len(parts) == 3
    assert all(parts)
