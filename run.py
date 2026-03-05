#!/usr/bin/env python3
from __future__ import annotations

import argparse

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
