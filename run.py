#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def _maybe_reexec_with_project_venv() -> None:
    if sys.version_info < (3, 13):
        return

    root_dir = Path(__file__).resolve().parent
    venv_python = root_dir / ".venv312" / "bin" / "python"
    if not venv_python.exists():
        return

    if Path(sys.executable).resolve() == venv_python.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_maybe_reexec_with_project_venv()

from wechat_rpa.bot import WeChatGuiRpaBot
from wechat_rpa.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WeChat macOS GUI-only RPA (no hook/injection/db access)."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to TOML config file (default: ./config.toml)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    bot = WeChatGuiRpaBot(cfg)
    bot.run_forever()


if __name__ == "__main__":
    main()
