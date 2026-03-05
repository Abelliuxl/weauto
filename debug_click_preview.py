#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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

from wechat_rpa.config import AppConfig, load_config
from wechat_rpa.detector import ChatRowState, detect_chat_rows
from wechat_rpa.ocr import OcrEngine
from wechat_rpa.window import WindowBounds, WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug script: detect WeChat rows and click preview-region center for each row."
    )
    parser.add_argument("--config", default="config.toml", help="Path to TOML config file")
    parser.add_argument("--cycles", type=int, default=1, help="How many detect+click cycles to run")
    parser.add_argument("--cycle-wait-sec", type=float, default=1.0, help="Wait between cycles")
    parser.add_argument("--click-delay-sec", type=float, default=0.8, help="Wait after each click")
    parser.add_argument("--top-n", type=int, default=0, help="Only click top N rows (0 means all)")
    parser.add_argument(
        "--scan-retries",
        type=int,
        default=6,
        help="Retry detect when rows=0 in each cycle",
    )
    parser.add_argument(
        "--scan-retry-wait-sec",
        type=float,
        default=0.45,
        help="Wait between empty-detect retries",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only print click coordinates")
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


def _load_manual_boxes(cfg: AppConfig, bounds: WindowBounds) -> dict[int, tuple[int, int, int, int]]:
    out: dict[int, tuple[int, int, int, int]] = {}
    if not cfg.use_manual_row_boxes:
        return out
    path = Path(cfg.manual_row_boxes_path)
    if not path.exists():
        return out
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    boxes = raw.get("boxes", []) if isinstance(raw, dict) else []
    if not isinstance(boxes, list):
        return out
    for idx, box in enumerate(boxes):
        if not isinstance(box, dict):
            continue
        x = int(bounds.width * float(box.get("x", 0.0)))
        y = int(bounds.height * float(box.get("y", 0.0)))
        w = int(bounds.width * float(box.get("w", 0.0)))
        h = int(bounds.height * float(box.get("h", 0.0)))
        if w >= 8 and h >= 8:
            out[idx] = (x, y, w, h)
    return out


def _fallback_row_box(row: ChatRowState, cfg: AppConfig, bounds: WindowBounds) -> tuple[int, int, int, int]:
    list_x = int(bounds.width * cfg.list_region.x)
    list_y = int(bounds.height * cfg.list_region.y)
    list_w = int(bounds.width * cfg.list_region.w)
    list_h = int(bounds.height * cfg.list_region.h)
    row_h = max(20, int(list_h * cfg.row_height_ratio))
    center_y = int(bounds.height * row.click_y_ratio)
    row_y = center_y - (row_h // 2)
    min_y = list_y
    max_y = max(list_y, list_y + list_h - row_h)
    row_y = max(min_y, min(max_y, row_y))
    return (list_x, row_y, list_w, row_h)


def _resolve_row_box(
    row: ChatRowState,
    cfg: AppConfig,
    bounds: WindowBounds,
    manual_boxes: dict[int, tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    box = manual_boxes.get(row.row_idx)
    if box is not None:
        return box
    return _fallback_row_box(row, cfg, bounds)


def _target_preview_point(row_box: tuple[int, int, int, int], cfg: AppConfig) -> tuple[int, int]:
    row_x, row_y, row_w, row_h = row_box
    rx = cfg.preview_text_region.x
    ry = cfg.preview_text_region.y
    rw = cfg.preview_text_region.w
    rh = cfg.preview_text_region.h

    x1 = row_x + int(row_w * rx)
    box_w = int(row_w * rw)
    box_h = int(row_h * rh)
    y_from_bottom = int(row_h * ry)
    y2 = row_y + row_h - y_from_bottom
    y1 = y2 - box_h
    x2 = x1 + box_w

    x1 = max(row_x, min(row_x + row_w - 1, x1))
    y1 = max(row_y, min(row_y + row_h - 1, y1))
    x2 = max(row_x + 1, min(row_x + row_w, x2))
    y2 = max(row_y + 1, min(row_y + row_h, y2))

    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    cx = max(row_x + 1, min(row_x + row_w - 2, cx))
    cy = max(row_y + 1, min(row_y + row_h - 2, cy))
    return (cx, cy)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    ocr_engine = OcrEngine()

    print("[start] click-preview debug started")
    print(
        f"[start] config={args.config} cycles={args.cycles} click_delay={args.click_delay_sec}s "
        f"top_n={args.top_n or 'all'} dry_run={args.dry_run} scan_retries={args.scan_retries}"
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

        manual_boxes = _load_manual_boxes(cfg, bounds)
        print(
            f"[cycle] id={cycle} bounds=({bounds.x},{bounds.y},{bounds.width},{bounds.height}) "
            f"rows={len(rows)}"
        )

        click_targets: list[tuple[ChatRowState, tuple[int, int, int, int], tuple[int, int]]] = []
        for row in rows:
            box = _resolve_row_box(row, cfg, bounds, manual_boxes)
            tgt = _target_preview_point(box, cfg)
            click_targets.append((row, box, tgt))
            print(
                f"[row] idx={row.row_idx} unread={row.has_unread_badge} mention={row.has_mention} "
                f"title={row.title!r} preview={row.preview!r} box={box} target={tgt}"
            )

        for row, _box, tgt in click_targets:
            if _is_ignored_title(row.title, cfg.ignore_title_keywords):
                print(f"[skip-title] cycle={cycle} idx={row.row_idx} title={row.title!r}")
                continue
            click_x = bounds.x + tgt[0]
            click_y = bounds.y + tgt[1]
            print(f"[click-preview] cycle={cycle} idx={row.row_idx} at=({click_x},{click_y})")
            if not args.dry_run:
                _activate_wechat(cfg.app_name)
                time.sleep(0.05)
                _safe_click(click_x, click_y, cfg.click_move_duration_sec, cfg.mouse_down_hold_sec)
            time.sleep(max(0.05, args.click_delay_sec))

        if cycle < args.cycles:
            time.sleep(max(0.1, args.cycle_wait_sec))

    print("[done] click-preview debug finished")


if __name__ == "__main__":
    main()
