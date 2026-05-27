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

import cv2
import numpy as np
import pyautogui

from wechat_rpa.config import AppConfig, load_config
from wechat_rpa.detector import ChatRowState, detect_chat_rows
from wechat_rpa.ocr import OcrEngine
from wechat_rpa.window import WindowBounds, WindowNotFoundError, get_front_window_bounds, screenshot_region

_UNREAD_RED_RATIO_THRESHOLD = 1.0 / 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug script: detect WeChat rows and click unread-badge anchor for each row."
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
    parser.add_argument(
        "--do-click",
        action="store_true",
        help="Actually click unread point (default: only move mouse)",
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


def _safe_move(x: int, y: int, move_sec: float) -> None:
    pyautogui.moveTo(x, y, duration=max(0.0, move_sec))


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


def _target_unread_point(row_box: tuple[int, int, int, int], cfg: AppConfig) -> tuple[int, int]:
    row_x, row_y, row_w, row_h = row_box
    cx = row_x + int(row_w * cfg.unread_badge_circle.x)
    cy = row_y + row_h - int(row_h * cfg.unread_badge_circle.y)
    cx = max(row_x + 1, min(row_x + row_w - 2, cx))
    cy = max(row_y + 1, min(row_y + row_h - 2, cy))
    return (cx, cy)


def _probe_unread_red(
    bounds: WindowBounds,
    row_box: tuple[int, int, int, int],
    cfg: AppConfig,
) -> tuple[tuple[int, int] | None, float, bool]:
    row_x, row_y, row_w, row_h = row_box
    shot = screenshot_region(bounds.x + row_x, bounds.y + row_y, row_w, row_h)
    row_rgb = np.array(shot.convert("RGB"))
    row_bgr = cv2.cvtColor(row_rgb, cv2.COLOR_RGB2BGR)
    h, w = row_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return (None, 0.0, False)

    cx = int(w * max(0.0, min(1.0, cfg.unread_badge_circle.x)))
    y_from_bottom = int(h * max(0.0, min(1.0, cfg.unread_badge_circle.y)))
    cy = h - y_from_bottom
    cy = max(0, min(h - 1, cy))
    r = int(min(w, h) * max(0.02, min(0.49, cfg.unread_badge_circle.r)))
    r = max(4, r)

    x1 = max(0, cx - r)
    y1 = max(0, cy - r)
    x2 = min(w, cx + r)
    y2 = min(h, cy + r)
    if x2 - x1 < 6 or y2 - y1 < 6:
        return (None, 0.0, False)

    roi = row_bgr[y1:y2, x1:x2]
    mh, mw = roi.shape[:2]
    circle_mask = np.zeros((mh, mw), dtype=np.uint8)
    cv2.circle(circle_mask, (cx - x1, cy - y1), r, 255, -1)
    circle_px = int(np.count_nonzero(circle_mask))
    if circle_px <= 0:
        return ((mw, mh), 0.0, False)

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(
        hsv, np.array([0, 45, 70], dtype=np.uint8), np.array([20, 255, 255], dtype=np.uint8)
    ) | cv2.inRange(
        hsv, np.array([160, 45, 70], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8)
    )
    kernel = np.ones((2, 2), np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)

    red_px = int(np.count_nonzero((red_mask > 0) & (circle_mask > 0)))
    red_ratio = float(red_px) / float(circle_px)
    return ((mw, mh), red_ratio, red_ratio >= _UNREAD_RED_RATIO_THRESHOLD)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    ocr_engine = OcrEngine()

    print("[start] click-unread debug started")
    print(
        f"[start] config={args.config} cycles={args.cycles} click_delay={args.click_delay_sec}s "
        f"top_n={args.top_n or 'all'} dry_run={args.dry_run} "
        f"scan_retries={args.scan_retries} do_click={args.do_click}"
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
            tgt = _target_unread_point(box, cfg)
            click_targets.append((row, box, tgt))
            print(
                f"[row] idx={row.row_idx} unread={row.has_unread_badge} mention={row.has_mention} "
                f"title={row.title!r} preview={row.preview!r} box={box} target={tgt}"
            )

        for row, box, tgt in click_targets:
            if _is_ignored_title(row.title, cfg.ignore_title_keywords):
                print(f"[skip-title] cycle={cycle} idx={row.row_idx} title={row.title!r}")
                continue
            click_x = bounds.x + tgt[0]
            click_y = bounds.y + tgt[1]
            action_tag = "click-unread" if args.do_click else "hover-unread"
            print(f"[{action_tag}] cycle={cycle} idx={row.row_idx} at=({click_x},{click_y})")
            if not args.dry_run:
                _activate_wechat(cfg.app_name)
                time.sleep(0.05)
                if args.do_click:
                    _safe_click(click_x, click_y, cfg.click_move_duration_sec, cfg.mouse_down_hold_sec)
                else:
                    _safe_move(click_x, click_y, cfg.click_move_duration_sec)
                time.sleep(0.06)

            roi_size, red_ratio, red_hit = _probe_unread_red(bounds, box, cfg)
            print(
                f"[red-unread] cycle={cycle} idx={row.row_idx} "
                f"roi={roi_size} red_ratio={red_ratio:.3f} "
                f"threshold={_UNREAD_RED_RATIO_THRESHOLD:.3f} hit={red_hit}"
            )
            time.sleep(max(0.05, args.click_delay_sec))

        if cycle < args.cycles:
            time.sleep(max(0.1, args.cycle_wait_sec))

    print("[done] click-unread debug finished")


if __name__ == "__main__":
    main()
