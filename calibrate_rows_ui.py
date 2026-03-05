#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import tkinter as tk
from tkinter import messagebox

import numpy as np
from PIL import Image, ImageTk

from wechat_rpa.config import AppConfig, load_config
from wechat_rpa.detector import detect_chat_rows
from wechat_rpa.ocr import OcrEngine
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual calibrator for WeChat conversation row boxes."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to TOML config file (default: ./config.toml)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path for manual row boxes (default: use config value)",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=0,
        help="Override initial row box count (0 means auto-detect)",
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


def _to_np_rgb(shot) -> np.ndarray:
    return np.array(shot.convert("RGB"))


def _auto_row_count(cfg: AppConfig, shot_rgb: np.ndarray, bounds, ocr_engine: OcrEngine) -> int:
    previous = cfg.use_manual_row_boxes
    cfg.use_manual_row_boxes = False
    try:
        rows = detect_chat_rows(shot_rgb, bounds, cfg, ocr_engine).rows
        count = len(rows)
    finally:
        cfg.use_manual_row_boxes = previous
    if count > 0:
        return count
    return max(4, min(cfg.rows_max, 8))


def _load_existing_boxes(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    boxes = raw.get("boxes", []) if isinstance(raw, dict) else []
    return boxes if isinstance(boxes, list) else []


def _make_default_boxes(cfg: AppConfig, width: int, height: int, count: int) -> list[dict]:
    list_x = int(width * cfg.list_region.x)
    list_y = int(height * cfg.list_region.y)
    list_w = int(width * cfg.list_region.w)
    list_h = int(height * cfg.list_region.h)
    row_h = max(20, int(height * cfg.row_height_ratio))
    rows: list[dict] = []
    for i in range(max(1, count)):
        top = list_y + i * row_h
        if top >= list_y + list_h:
            break
        h = min(row_h, (list_y + list_h) - top)
        rows.append(
            {
                "idx": i,
                "x": list_x,
                "y": top,
                "w": list_w,
                "h": max(14, h),
            }
        )
    return rows


def _boxes_from_ratio(raw_boxes: list[dict], width: int, height: int) -> list[dict]:
    boxes: list[dict] = []
    for i, item in enumerate(raw_boxes):
        if not isinstance(item, dict):
            continue
        try:
            x = int(float(item.get("x", 0.0)) * width)
            y = int(float(item.get("y", 0.0)) * height)
            w = int(float(item.get("w", 0.0)) * width)
            h = int(float(item.get("h", 0.0)) * height)
        except Exception:
            continue
        if w < 20 or h < 12:
            continue
        boxes.append({"idx": i, "x": x, "y": y, "w": w, "h": h})
    return boxes


def _upsert_top_level_key(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
    line = f'{key} = {value}'
    if pattern.search(text):
        return pattern.sub(line, text)

    lines = text.splitlines()
    insert_at = len(lines)
    for i, raw in enumerate(lines):
        if raw.strip().startswith("["):
            insert_at = i
            break
    lines.insert(insert_at, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _enable_manual_mode_in_config(config_path: Path, output_path: Path) -> None:
    if not config_path.exists():
        return
    text = config_path.read_text(encoding="utf-8")
    text = _upsert_top_level_key(text, "use_manual_row_boxes", "true")
    text = _upsert_top_level_key(text, "manual_row_boxes_path", json.dumps(str(output_path)))
    config_path.write_text(text, encoding="utf-8")


class CalibratorUI:
    def __init__(self, image: Image.Image, boxes: list[dict], save_cb) -> None:
        self.image = image
        self.img_w, self.img_h = self.image.size
        self.save_cb = save_cb
        self.root = tk.Tk()
        self.root.title("WeChat 会话行框校准")

        max_w = self.root.winfo_screenwidth() - 80
        max_h = self.root.winfo_screenheight() - 180
        win_w = min(max_w, self.img_w + 40)
        win_h = min(max_h, self.img_h + 120)
        self.root.geometry(f"{max(860, win_w)}x{max(620, win_h)}")

        toolbar = tk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Button(toolbar, text="+1行", command=self._add_row).pack(side=tk.LEFT)
        tk.Button(toolbar, text="-1行", command=self._remove_row).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(toolbar, text="保存并启用", command=self._save).pack(side=tk.RIGHT)
        tk.Button(toolbar, text="取消", command=self.root.destroy).pack(side=tk.RIGHT, padx=(0, 6))

        help_text = "拖动框内部=移动；拖动右下角小方块=缩放；编号按 0,1,2... 对应会话顺序。"
        tk.Label(self.root, text=help_text, anchor="w").pack(fill=tk.X, padx=8, pady=(0, 4))

        frame = tk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.canvas = tk.Canvas(frame, bg="#222")
        xbar = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ybar = tk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        xbar.pack(side=tk.BOTTOM, fill=tk.X)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tk_img = ImageTk.PhotoImage(self.image)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)
        self.canvas.configure(scrollregion=(0, 0, self.img_w, self.img_h))

        self.boxes = boxes
        self.selected_idx = 0 if boxes else -1
        self.mode = ""
        self.drag_offset = (0, 0)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda _: self.root.destroy())

        self._redraw()

    def _add_row(self) -> None:
        if not self.boxes:
            self.boxes.append({"idx": 0, "x": 60, "y": 120, "w": 360, "h": 82})
            self.selected_idx = 0
            self._redraw()
            return
        last = self.boxes[-1]
        y = min(self.img_h - 16 - last["h"], last["y"] + last["h"])
        self.boxes.append(
            {
                "idx": len(self.boxes),
                "x": last["x"],
                "y": y,
                "w": last["w"],
                "h": last["h"],
            }
        )
        self.selected_idx = len(self.boxes) - 1
        self._redraw()

    def _remove_row(self) -> None:
        if not self.boxes:
            return
        idx = self.selected_idx if 0 <= self.selected_idx < len(self.boxes) else len(self.boxes) - 1
        self.boxes.pop(idx)
        for i, b in enumerate(self.boxes):
            b["idx"] = i
        self.selected_idx = min(idx, len(self.boxes) - 1)
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("box")
        for i, box in enumerate(self.boxes):
            x1, y1 = box["x"], box["y"]
            x2, y2 = x1 + box["w"], y1 + box["h"]
            active = i == self.selected_idx
            color = "#33ccff" if active else "#ffd84d"
            self.canvas.create_rectangle(
                x1, y1, x2, y2, outline=color, width=2, tags=("box", f"row-{i}")
            )
            self.canvas.create_text(
                x1 + 18,
                y1 + 14,
                text=str(i),
                fill=color,
                font=("Helvetica", 14, "bold"),
                tags=("box",),
            )
            # resize handle
            self.canvas.create_rectangle(
                x2 - 7, y2 - 7, x2 + 7, y2 + 7, fill=color, outline=color, tags=("box",)
            )

    def _pick_box(self, x: int, y: int) -> int:
        for i in range(len(self.boxes) - 1, -1, -1):
            b = self.boxes[i]
            if b["x"] <= x <= b["x"] + b["w"] and b["y"] <= y <= b["y"] + b["h"]:
                return i
        return -1

    def _pick_resize_handle(self, x: int, y: int) -> int:
        # Prioritize resize handle hit-test globally. This avoids selecting
        # neighboring rows as "move" when boxes are vertically adjacent.
        for i in range(len(self.boxes) - 1, -1, -1):
            b = self.boxes[i]
            if self._near_resize_handle(b, x, y):
                return i
        return -1

    def _near_resize_handle(self, b: dict, x: int, y: int) -> bool:
        x2, y2 = b["x"] + b["w"], b["y"] + b["h"]
        return abs(x - x2) <= 10 and abs(y - y2) <= 10

    def _on_press(self, event) -> None:
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        handle_idx = self._pick_resize_handle(x, y)
        if handle_idx >= 0:
            self.selected_idx = handle_idx
            self.mode = "resize"
            self._redraw()
            return

        idx = self._pick_box(x, y)
        self.selected_idx = idx
        if idx < 0:
            self.mode = ""
            self._redraw()
            return
        box = self.boxes[idx]
        self.mode = "move"
        self.drag_offset = (x - box["x"], y - box["y"])
        self._redraw()

    def _on_motion(self, event) -> None:
        if self.selected_idx < 0 or not self.mode:
            return
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        b = self.boxes[self.selected_idx]

        if self.mode == "move":
            ox, oy = self.drag_offset
            nx = max(0, min(self.img_w - b["w"], x - ox))
            ny = max(0, min(self.img_h - b["h"], y - oy))
            b["x"], b["y"] = nx, ny
        elif self.mode == "resize":
            nw = max(20, min(self.img_w - b["x"], x - b["x"]))
            nh = max(12, min(self.img_h - b["y"], y - b["y"]))
            b["w"], b["h"] = nw, nh
        self._redraw()

    def _on_release(self, _event) -> None:
        self.mode = ""

    def _save(self) -> None:
        if not self.boxes:
            messagebox.showwarning("提示", "至少需要一个行框。")
            return
        self.save_cb(self.boxes, self.img_w, self.img_h)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    output_path = Path(args.output or cfg.manual_row_boxes_path)
    config_path = Path(args.config)

    ocr_engine = OcrEngine()
    _activate_wechat(cfg.app_name)
    time.sleep(max(0.0, cfg.activate_wait_sec))

    try:
        bounds = get_front_window_bounds(cfg.app_name)
    except WindowNotFoundError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1)

    shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
    shot_rgb = _to_np_rgb(shot)
    image = Image.fromarray(shot_rgb)

    count = args.rows if args.rows > 0 else _auto_row_count(cfg, shot_rgb, bounds, ocr_engine)
    existing = _boxes_from_ratio(_load_existing_boxes(output_path), bounds.width, bounds.height)
    if existing:
        boxes = existing
        count = len(existing)
    else:
        boxes = _make_default_boxes(cfg, bounds.width, bounds.height, count)

    print(
        f"[start] calibrator rows={count} window=({bounds.width}x{bounds.height}) "
        f"output={output_path}"
    )

    def _save(boxes_px: list[dict], w: int, h: int) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "manual_row_boxes_v1",
            "window_width": w,
            "window_height": h,
            "boxes": [
                {
                    "idx": i,
                    "x": round(max(0.0, min(1.0, b["x"] / w)), 6),
                    "y": round(max(0.0, min(1.0, b["y"] / h)), 6),
                    "w": round(max(0.0, min(1.0, b["w"] / w)), 6),
                    "h": round(max(0.0, min(1.0, b["h"] / h)), 6),
                }
                for i, b in enumerate(boxes_px)
            ],
            "updated_at": int(time.time()),
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _enable_manual_mode_in_config(config_path, output_path)
        print(f"[saved] boxes={len(payload['boxes'])} path={output_path}")
        print(
            f"[saved] config updated: use_manual_row_boxes=true, "
            f"manual_row_boxes_path={output_path}"
        )

    ui = CalibratorUI(image=image, boxes=boxes, save_cb=_save)
    ui.run()


if __name__ == "__main__":
    main()
