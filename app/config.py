"""Read the optional ``[webui]`` section of ``config.toml``.

The bot's own :class:`wechat_rpa.config.AppConfig` ignores unknown top-level
tables, so adding ``[webui]`` is safe and zero-impact for the bot. We parse it
independently here rather than touching ``wechat_rpa.config``.
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib as _toml  # noqa: F401  (re-import for clarity)
else:  # pragma: no cover - project pins python >=3.12
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class WebUIConfig:
    host: str = "127.0.0.1"
    port: int = 8721
    log_ring_lines: int = 2000
    auto_restart: bool = True
    restart_min_interval_sec: float = 30.0
    # When false the supervisor stays stopped after the bot exits.
    supervisor_enabled: bool = True
    # Root dir of the project (where run.py / config.toml live).
    project_root: Path = Path(".")
    config_path: Path = Path("config.toml")

    def web_url(self, prefer_remote_hint: bool = False) -> str:
        """URL to show in the menu bar / open in a browser.

        When bound to all interfaces we cannot know which address the caller
        will use, so fall back to localhost (always reachable locally).
        """
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::", ""} else self.host
        return f"http://{host}:{self.port}"


def load_webui_config(config_path: str | Path = "config.toml") -> WebUIConfig:
    """Load ``[webui]`` from ``config_path`` with safe defaults."""
    path = Path(config_path).expanduser().resolve()
    data: dict = {}
    if path.is_file():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
    section = data.get("webui", {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        section = {}

    def _str(key: str, default: str) -> str:
        val = section.get(key, default)
        return str(val).strip() if val is not None else default

    def _int(key: str, default: int) -> int:
        try:
            return int(section.get(key, default))
        except (TypeError, ValueError):
            return default

    def _float(key: str, default: float) -> float:
        try:
            return float(section.get(key, default))
        except (TypeError, ValueError):
            return default

    def _bool(key: str, default: bool) -> bool:
        val = section.get(key, default)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in {"1", "true", "yes", "on"}
        return bool(val)

    return WebUIConfig(
        host=_str("host", "127.0.0.1"),
        port=_int("port", 8721),
        log_ring_lines=_int("log_ring_lines", 2000),
        auto_restart=_bool("auto_restart", True),
        restart_min_interval_sec=_float("restart_min_interval_sec", 30.0),
        supervisor_enabled=_bool("supervisor_enabled", True),
        project_root=path.parent,
        config_path=path,
    )
