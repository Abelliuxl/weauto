#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


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
try:
    import tkinter as tk
    from tkinter import messagebox
except ModuleNotFoundError as exc:
    if exc.name == "_tkinter":
        raise SystemExit(
            "Missing tkinter. Run `./carlibrate_preview.sh config.toml` "
            "or use `.venv312/bin/python carlibrate_preview_region_ui.py --config config.toml`."
        ) from exc
    raise

from PIL import ImageTk

from wechat_rpa.config import load_config
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual calibrator for row preview OCR region."
    )
    parser.add_argument("config_positional", nargs="?", default="", help="Config path.")
    parser.add_argument("--config", dest="config", default="config.toml", help="TOML path.")
    args = parser.parse_args()
    if args.config_positional:
        args.config = args.config_positional
    return args


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


def _upsert_region_section(text: str, section: str, x: float, y: float, w: float, h: float) -> str:
    lines = text.splitlines()
    hdr = f"[{section}]"
    start = -1
    end = -1
    for i, line in enumerate(lines):
        if line.strip() == hdr:
            start = i
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("["):
                j += 1
            end = j
            break
    block = [hdr, f"x = {x:.6f}", f"y = {y:.6f}", f"w = {w:.6f}", f"h = {h:.6f}"]
    if start >= 0:
        lines = lines[:start] + block + lines[end:]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _upsert_top_level_key(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=.*$")
    line = f"{key} = {value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    lines = text.splitlines()
    insert_at = len(lines)
    for i, raw in enumerate(lines):
        if raw.strip().startswith("["):
            insert_at = i
            break
    lines.insert(insert_at, line)
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _first_row_box(cfg, bounds) -> tuple[int, int, int, int]:
    if cfg.use_manual_row_boxes:
        path = Path(cfg.manual_row_boxes_path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                boxes = raw.get("boxes", []) if isinstance(raw, dict) else []
                if isinstance(boxes, list) and boxes:
                    b0 = boxes[0] if isinstance(boxes[0], dict) else {}
                    x = int(bounds.width * float(b0.get("x", 0.0)))
                    y = int(bounds.height * float(b0.get("y", 0.0)))
                    w = int(bounds.width * float(b0.get("w", 0.0)))
                    h = int(bounds.height * float(b0.get("h", 0.0)))
                    if w >= 20 and h >= 12:
                        return x, y, w, h
            except Exception:
                pass
    x = int(bounds.width * cfg.list_region.x)
    y = int(bounds.height * cfg.list_region.y)
    w = int(bounds.width * cfg.list_region.w)
    h = max(16, int(bounds.height * cfg.row_height_ratio))
    return x, y, w, h


class PreviewRegionUI:
    def __init__(self, image, box: dict, on_save) -> None:
        self.image = image
        self.img_w, self.img_h = image.size
        self.box = box
        self.on_save = on_save
        self.root = tk.Tk()
        self.root.title("Preview 文本区域校准")

        max_w = self.root.winfo_screenwidth() - 80
        max_h = self.root.winfo_screenheight() - 180
        win_w = min(max_w, self.img_w + 40)
        win_h = min(max_h, self.img_h + 120)
        self.root.geometry(f"{max(760, win_w)}x{max(520, win_h)}")

        bar = tk.Frame(self.root)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Button(bar, text="保存并启用", command=self._save).pack(side=tk.RIGHT)
        tk.Button(bar, text="取消", command=self.root.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        tk.Label(
            self.root,
            text="拖动框内部=移动；拖动右下角小方块=缩放。锚点为左下角，请按左下基准标注。",
            anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(0, 4))

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

        self.mode = ""
        self.drag_offset = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Escape>", lambda _: self.root.destroy())
        self._redraw()

    def _inside(self, x: int, y: int) -> bool:
        return self.box["x"] <= x <= self.box["x"] + self.box["w"] and self.box["y"] <= y <= self.box["y"] + self.box["h"]

    def _near_handle(self, x: int, y: int) -> bool:
        x2 = self.box["x"] + self.box["w"]
        y2 = self.box["y"] + self.box["h"]
        return abs(x - x2) <= 10 and abs(y - y2) <= 10

    def _redraw(self) -> None:
        self.canvas.delete("overlay")
        x1, y1 = self.box["x"], self.box["y"]
        x2, y2 = x1 + self.box["w"], y1 + self.box["h"]
        color = "#00b96b"
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, tags=("overlay",))
        self.canvas.create_text(x1 + 40, y1 + 14, text="PREVIEW", fill=color, tags=("overlay",))
        self.canvas.create_rectangle(x2 - 7, y2 - 7, x2 + 7, y2 + 7, fill=color, outline=color, tags=("overlay",))

    def _on_press(self, event) -> None:
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        if self._near_handle(x, y):
            self.mode = "resize"
            return
        if self._inside(x, y):
            self.mode = "move"
            self.drag_offset = (x - self.box["x"], y - self.box["y"])
            return
        self.mode = ""

    def _on_motion(self, event) -> None:
        if not self.mode:
            return
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        if self.mode == "move":
            ox, oy = self.drag_offset
            self.box["x"] = max(0, min(self.img_w - self.box["w"], x - ox))
            self.box["y"] = max(0, min(self.img_h - self.box["h"], y - oy))
        else:
            self.box["w"] = max(12, min(self.img_w - self.box["x"], x - self.box["x"]))
            self.box["h"] = max(10, min(self.img_h - self.box["y"], y - self.box["y"]))
        self._redraw()

    def _on_release(self, _event) -> None:
        self.mode = ""

    def _save(self) -> None:
        if self.box["w"] < 12 or self.box["h"] < 10:
            messagebox.showwarning("提示", "区域太小。")
            return
        self.on_save(self.box, self.img_w, self.img_h)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    config_path = Path(args.config)
    _activate_wechat(cfg.app_name)
    time.sleep(max(0.0, cfg.activate_wait_sec))
    try:
        bounds = get_front_window_bounds(cfg.app_name)
    except WindowNotFoundError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1)

    box_x, box_y, box_w, box_h = _first_row_box(cfg, bounds)
    shot = screenshot_region(bounds.x + box_x, bounds.y + box_y, box_w, box_h)
    init_w = int(box_w * cfg.preview_text_region.w)
    init_h = int(box_h * cfg.preview_text_region.h)
    y_from_bottom = int(box_h * cfg.preview_text_region.y)
    init = {
        "x": int(box_w * cfg.preview_text_region.x),
        "y": box_h - y_from_bottom - init_h,
        "w": init_w,
        "h": init_h,
    }
    init["x"] = max(0, min(box_w - 1, init["x"]))
    init["y"] = max(0, min(box_h - 1, init["y"]))
    init["w"] = max(12, min(box_w - init["x"], init["w"] if init["w"] > 0 else 20))
    init["h"] = max(10, min(box_h - init["y"], init["h"] if init["h"] > 0 else 14))
    print(
        f"[start] preview-calibrator row_box=({box_x},{box_y},{box_w},{box_h}) "
        f"region=({init['x']},{init['y']},{init['w']},{init['h']})"
    )

    def _save(b: dict, w: int, h: int) -> None:
        rx = max(0.0, min(1.0, b["x"] / max(1, w)))
        ry = max(0.0, min(1.0, (h - (b["y"] + b["h"])) / max(1, h)))
        rw = max(0.0, min(1.0, b["w"] / max(1, w)))
        rh = max(0.0, min(1.0, b["h"] / max(1, h)))
        text = config_path.read_text(encoding="utf-8")
        text = _upsert_region_section(text, "preview_text_region", rx, ry, rw, rh)
        text = _upsert_top_level_key(text, "preview_region_enabled", "true")
        config_path.write_text(text, encoding="utf-8")
        print(
            f"[saved] preview_text_region=({rx:.4f},{ry:.4f},{rw:.4f},{rh:.4f}) "
            "preview_region_enabled=true"
        )

    ui = PreviewRegionUI(image=shot, box=init, on_save=_save)
    ui.run()


if __name__ == "__main__":
    main()
