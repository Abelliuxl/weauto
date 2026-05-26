import json
from pathlib import Path
from types import SimpleNamespace

from wechat_rpa.config import ImageGenerationConfig, load_config
from wechat_rpa.action_processor import ActionProcessor
from wechat_rpa.image_generation import ImageGenerator
from wechat_rpa.sender import WeChatGuiSender


def test_config_example_uses_dashscope_z_image_defaults():
    cfg = load_config("config.toml.example")
    assert cfg.image_generation.provider == "dashscope_z_image"
    assert cfg.image_generation.base_url == "https://dashscope.aliyuncs.com/api/v1/services/aigc"
    assert cfg.image_generation.api_key_env == "DASHSCOPE_API_KEY"
    assert cfg.image_generation.model == "z-image-turbo"


def test_legacy_siliconflow_config_without_provider_stays_openai_compat(tmp_path):
    path = Path(tmp_path) / "config.toml"
    path.write_text(
        """
[image_generation]
enabled = true
base_url = "https://api.siliconflow.cn/v1"
api_key = "test-key"
model = "black-forest-labs/FLUX.1-dev"
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.image_generation.provider == "openai_compat"
    assert cfg.image_generation.model == "black-forest-labs/FLUX.1-dev"


def test_image_generator_status_and_size_validation(tmp_path):
    cfg = ImageGenerationConfig(
        enabled=True,
        api_key="test-key",
        output_dir=str(tmp_path),
    )
    generator = ImageGenerator(cfg)
    assert generator.is_available() is True
    assert "provider=dashscope_z_image" in generator.status_text()
    assert generator.normalize_size("512 x 768") == "512x768"
    assert generator.normalize_size("64x64") == "1024x1024"


def test_image_generator_writes_provider_history(tmp_path):
    cfg = ImageGenerationConfig(
        enabled=True,
        api_key="test-key",
        output_dir=str(tmp_path),
    )
    generator = ImageGenerator(cfg)

    def fake_dashscope(*, api_key: str, prompt: str, size: str):
        assert api_key == "test-key"
        assert prompt == "测试图片"
        assert size == "1024x1024"
        return b"png-bytes", ".png", 123

    generator._dashscope = fake_dashscope  # type: ignore[method-assign]
    out = generator.generate_file(prompt=" 测试图片 ", size="")

    assert out.is_file()
    assert out.read_bytes() == b"png-bytes"
    rows = [json.loads(line) for line in (Path(tmp_path) / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["provider"] == "dashscope_z_image"
    assert rows[-1]["model"] == "z-image-turbo"
    assert rows[-1]["seed"] == 123


def test_sender_apple_quote_escapes_applescript_path():
    assert WeChatGuiSender.apple_quote('/tmp/a "b" c\\d.png') == '/tmp/a \\"b\\" c\\\\d.png'


def test_action_processor_runs_session_fact_tool():
    class Workspace:
        def __init__(self):
            self.calls = []

        def remember_structured(self, **kwargs):
            self.calls.append(kwargs)

    class FakeBot:
        def __init__(self):
            self.cfg = SimpleNamespace(agent_actions_max_per_turn=4, log_verbose=False)
            self._workspace = Workspace()
            self.records = []

        def _session_key_for_row(self, row):
            return "session-key"

        def _append_session_record(self, *args, **kwargs):
            self.records.append((args, kwargs))

    bot = FakeBot()
    row = SimpleNamespace(title="群", row_idx=1)
    actions = [{"tool": "remember_session_fact", "args": {"fact": "  x   y  "}}]
    trace, obs = ActionProcessor(bot).execute_agent_actions(
        row,
        actions,
        is_admin=True,
        max_actions_override=3,
    )
    assert trace == "1. remember_session_fact -> ok"
    assert obs == "会话事实已记录: x y"
    assert bot._workspace.calls == [
        {"session_key": "session-key", "title": "群", "facts": ["x y"]}
    ]
    assert bot.records
