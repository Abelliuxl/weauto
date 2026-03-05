#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

def _maybe_reexec_with_project_venv() -> None:
    # Python 3.14 often lacks _tkinter on macOS; prefer the project's tested venv.
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
            "Missing tkinter for current Python. Run `./carlibrate_title.sh config.toml` "
            "or use `.venv312/bin/python carlibrate_title_ui.py --config config.toml`."
        ) from exc
    raise

from PIL import ImageTk

from wechat_rpa.config import load_config
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual calibrator for WeChat right-panel title OCR region."
    )
    parser.add_argument(
        "config_positional",
        nargs="?",
        default="",
        help="Config path (positional alias of --config).",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default="config.toml",
        help="Path to TOML config file (default: ./config.toml)",
    )
    parser.add_argument(
        "--section",
        default="chat_title_region",
        help="TOML section to write (default: chat_title_region).",
    )
    parser.add_argument(
        "--enable-key",
        action="append",
        default=[],
        help="Top-level bool key to set true after save. Can be passed multiple times.",
    )
    parser.add_argument(
        "--ui-title",
        default="WeChat 标题栏区域校准",
        help="Window title shown in calibrator UI.",
    )
    parser.add_argument(
        "--label",
        default="TITLE",
        help="Overlay label text in calibrator UI.",
    )
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

    block = [
        hdr,
        f"x = {x:.6f}",
        f"y = {y:.6f}",
        f"w = {w:.6f}",
        f"h = {h:.6f}",
    ]
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


class TitleCalibratorUI:
    def __init__(self, image, box: dict, on_save, *, window_title: str, overlay_label: str) -> None:
        self.image = image
        self.img_w, self.img_h = image.size
        self.box = box
        self.on_save = on_save
        self.overlay_label = overlay_label
        self.root = tk.Tk()
        self.root.title(window_title)

        max_w = self.root.winfo_screenwidth() - 80
        max_h = self.root.winfo_screenheight() - 180
        win_w = min(max_w, self.img_w + 40)
        win_h = min(max_h, self.img_h + 120)
        self.root.geometry(f"{max(860, win_w)}x{max(620, win_h)}")

        bar = tk.Frame(self.root)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Button(bar, text="保存并启用", command=self._save).pack(side=tk.RIGHT)
        tk.Button(bar, text="取消", command=self.root.destroy).pack(side=tk.RIGHT, padx=(0, 6))
        tk.Label(
            self.root,
            text="拖动框内部=移动；拖动右下角小方块=缩放。框应覆盖右侧聊天标题文字区域。",
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

    def _near_handle(self, x: int, y: int) -> bool:
        x2, y2 = self.box["x"] + self.box["w"], self.box["y"] + self.box["h"]
        return abs(x - x2) <= 10 and abs(y - y2) <= 10

    def _inside(self, x: int, y: int) -> bool:
        return (
            self.box["x"] <= x <= self.box["x"] + self.box["w"]
            and self.box["y"] <= y <= self.box["y"] + self.box["h"]
        )

    def _redraw(self) -> None:
        self.canvas.delete("overlay")
        x1, y1 = self.box["x"], self.box["y"]
        x2, y2 = x1 + self.box["w"], y1 + self.box["h"]
        color = "#ff5fd1"
        self.canvas.create_rectangle(
            x1, y1, x2, y2, outline=color, width=2, tags=("overlay",)
        )
        self.canvas.create_text(
            x1 + 50,
            y1 + 16,
            text=self.overlay_label,
            fill=color,
            font=("Helvetica", 13, "bold"),
            tags=("overlay",),
        )
        self.canvas.create_rectangle(
            x2 - 7, y2 - 7, x2 + 7, y2 + 7, fill=color, outline=color, tags=("overlay",)
        )

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
            nx = max(0, min(self.img_w - self.box["w"], x - ox))
            ny = max(0, min(self.img_h - self.box["h"], y - oy))
            self.box["x"], self.box["y"] = nx, ny
        else:
            nw = max(30, min(self.img_w - self.box["x"], x - self.box["x"]))
            nh = max(18, min(self.img_h - self.box["y"], y - self.box["y"]))
            self.box["w"], self.box["h"] = nw, nh
        self._redraw()

    def _on_release(self, _event) -> None:
        self.mode = ""

    def _save(self) -> None:
        if self.box["w"] < 30 or self.box["h"] < 18:
            messagebox.showwarning("提示", "标题区域过小，请放大后再保存。")
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

    shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
    region = getattr(cfg, args.section, cfg.chat_title_region)
    box = {
        "x": int(bounds.width * region.x),
        "y": int(bounds.height * region.y),
        "w": int(bounds.width * region.w),
        "h": int(bounds.height * region.h),
    }

    print(
        f"[start] title-calibrator window=({bounds.width}x{bounds.height}) "
        f"box=({box['x']},{box['y']},{box['w']},{box['h']})"
    )

    def _save(b: dict, w: int, h: int) -> None:
        rx = max(0.0, min(1.0, b["x"] / w))
        ry = max(0.0, min(1.0, b["y"] / h))
        rw = max(0.0, min(1.0, b["w"] / w))
        rh = max(0.0, min(1.0, b["h"] / h))

        text = config_path.read_text(encoding="utf-8")
        text = _upsert_region_section(text, args.section, rx, ry, rw, rh)
        text = _upsert_top_level_key(text, "focus_verify_enabled", "true")
        for key in args.enable_key:
            clean_key = (key or "").strip()
            if clean_key:
                text = _upsert_top_level_key(text, clean_key, "true")
        config_path.write_text(text, encoding="utf-8")
        print(
            f"[saved] {args.section}="
            f"({rx:.4f},{ry:.4f},{rw:.4f},{rh:.4f}) in {config_path}"
        )

    ui = TitleCalibratorUI(
        image=shot,
        box=box,
        on_save=_save,
        window_title=args.ui_title,
        overlay_label=args.label,
    )
    ui.run()


if __name__ == "__main__":
    main()
