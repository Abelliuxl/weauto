#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
import subprocess
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

import numpy as np
import pyautogui

from wechat_rpa.config import load_config
from wechat_rpa.detector import ChatRowState, detect_chat_rows
from wechat_rpa.ocr import OcrEngine
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug script: detect WeChat chat rows and click each row from top to bottom."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to TOML config file (default: ./config.toml)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help="How many detect+click cycles to run (default: 1)",
    )
    parser.add_argument(
        "--cycle-wait-sec",
        type=float,
        default=1.0,
        help="Wait between cycles (default: 1.0)",
    )
    parser.add_argument(
        "--click-delay-sec",
        type=float,
        default=0.8,
        help="Wait after each row click (default: 0.8)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=0,
        help="Only click top N rows (0 means all detected rows)",
    )
    parser.add_argument(
        "--scan-retries",
        type=int,
        default=6,
        help="Retry detect when rows=0 in each cycle (default: 6)",
    )
    parser.add_argument(
        "--scan-retry-wait-sec",
        type=float,
        default=0.45,
        help="Wait between empty-detect retries (default: 0.45)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print rows and click coordinates; do not click.",
    )
    return parser.parse_args()


def _activate_wechat(app_name: str) -> None:
    aliases = [x.strip() for x in app_name.split("|") if x.strip()]
    if "WeChat" in aliases and "微信" not in aliases:
        aliases.append("微信")
    if not aliases:
        aliases = ["WeChat", "微信"]

    for app in aliases:
        proc = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to activate'],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
    print(f"[warn] failed to activate app, tried aliases={aliases}")


def _safe_click(x: int, y: int, move_sec: float, hold_sec: float) -> None:
    pyautogui.moveTo(x, y, duration=max(0.0, move_sec))
    pyautogui.mouseDown()
    time.sleep(max(0.01, hold_sec))
    pyautogui.mouseUp()


def _to_np_rgb(shot) -> np.ndarray:
    return np.array(shot.convert("RGB"))


def _sorted_rows(rows: list[ChatRowState]) -> list[ChatRowState]:
    return sorted(rows, key=lambda row: row.row_idx)


def _is_ignored_title(title: str, keywords: list[str]) -> bool:
    txt = (title or "").strip()
    if not txt:
        return False
    return any(k and k in txt for k in keywords)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    ocr_engine = OcrEngine()

    print("[start] click-row debug started")
    print(
        f"[start] config={args.config} cycles={args.cycles} click_delay={args.click_delay_sec}s "
        f"top_n={args.top_n or 'all'} dry_run={args.dry_run} "
        f"scan_retries={args.scan_retries}"
    )

    for cycle in range(1, max(1, args.cycles) + 1):
        _activate_wechat(cfg.app_name)
        time.sleep(max(0.0, cfg.activate_wait_sec))

        try:
            bounds = get_front_window_bounds(cfg.app_name)
        except WindowNotFoundError as exc:
            print(f"[warn] cycle={cycle} {exc}")
            time.sleep(max(0.1, args.cycle_wait_sec))
            continue

        rows: list[ChatRowState] = []
        tries = max(1, args.scan_retries)
        for attempt in range(1, tries + 1):
            shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
            shot_rgb = _to_np_rgb(shot)
            detected = detect_chat_rows(shot_rgb, bounds, cfg, ocr_engine)
            rows = _sorted_rows(detected.rows)
            if rows:
                break
            print(f"[scan-empty] cycle={cycle} attempt={attempt}/{tries}")
            if attempt < tries:
                time.sleep(max(0.05, args.scan_retry_wait_sec))

        if args.top_n > 0:
            rows = rows[: args.top_n]

        print(
            f"[cycle] id={cycle} bounds=({bounds.x},{bounds.y},{bounds.width},{bounds.height}) "
            f"rows={len(rows)}"
        )
        for row in rows:
            click_x = bounds.x + int(bounds.width * row.click_x_ratio)
            click_y = bounds.y + int(bounds.height * row.click_y_ratio)
            print(
                f"[row] idx={row.row_idx} unread={row.has_unread_badge} mention={row.has_mention} "
                f"title={row.title!r} preview={row.preview!r} click=({click_x},{click_y})"
            )

        for row in rows:
            if _is_ignored_title(row.title, cfg.ignore_title_keywords):
                print(f"[skip-title] cycle={cycle} idx={row.row_idx} title={row.title!r}")
                continue
            click_x = bounds.x + int(bounds.width * row.click_x_ratio)
            click_y = bounds.y + int(bounds.height * row.click_y_ratio)
            print(f"[click] cycle={cycle} idx={row.row_idx} at=({click_x},{click_y})")
            if not args.dry_run:
                _activate_wechat(cfg.app_name)
                time.sleep(0.05)
                _safe_click(click_x, click_y, cfg.click_move_duration_sec, cfg.mouse_down_hold_sec)
            time.sleep(max(0.05, args.click_delay_sec))

        if cycle < args.cycles:
            time.sleep(max(0.1, args.cycle_wait_sec))

    print("[done] click-row debug finished")


if __name__ == "__main__":
    main()
