#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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
            "Missing tkinter. Run `./carlibrate_unread.sh config.toml` "
            "or use `.venv312/bin/python carlibrate_unread_badge_ui.py --config config.toml`."
        ) from exc
    raise

from PIL import ImageTk

from wechat_rpa.config import load_config
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual calibrator for unread badge OCR circle in one chat row."
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


def _upsert_section(text: str, section: str, items: list[tuple[str, str]]) -> str:
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
    block = [hdr] + [f"{k} = {v}" for k, v in items]
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


class BadgeCircleUI:
    def __init__(self, image, circle: dict, on_save) -> None:
        self.image = image
        self.img_w, self.img_h = image.size
        self.circle = circle
        self.on_save = on_save
        self.root = tk.Tk()
        self.root.title("红点数字区域校准（圆形）")

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
            text="拖动圆内=移动圆心；拖动右侧小方块=调整半径。锚点为左下角，请贴合头像左下角基准。",
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

    def _handle_xy(self) -> tuple[int, int]:
        return self.circle["x"] + self.circle["r"], self.circle["y"]

    def _inside(self, x: int, y: int) -> bool:
        dx = x - self.circle["x"]
        dy = y - self.circle["y"]
        return (dx * dx + dy * dy) <= (self.circle["r"] * self.circle["r"])

    def _near_handle(self, x: int, y: int) -> bool:
        hx, hy = self._handle_xy()
        return abs(x - hx) <= 10 and abs(y - hy) <= 10

    def _redraw(self) -> None:
        self.canvas.delete("overlay")
        cx, cy, r = self.circle["x"], self.circle["y"], self.circle["r"]
        color = "#ff4d4f"
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=2, tags=("overlay",))
        self.canvas.create_text(cx, cy - r - 12, text="UNREAD", fill=color, tags=("overlay",))
        hx, hy = self._handle_xy()
        self.canvas.create_rectangle(hx - 6, hy - 6, hx + 6, hy + 6, fill=color, outline=color, tags=("overlay",))

    def _on_press(self, event) -> None:
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        if self._near_handle(x, y):
            self.mode = "resize"
            return
        if self._inside(x, y):
            self.mode = "move"
            self.drag_offset = (x - self.circle["x"], y - self.circle["y"])
            return
        self.mode = ""

    def _on_motion(self, event) -> None:
        if not self.mode:
            return
        x = int(self.canvas.canvasx(event.x))
        y = int(self.canvas.canvasy(event.y))
        if self.mode == "move":
            ox, oy = self.drag_offset
            self.circle["x"] = max(0, min(self.img_w - 1, x - ox))
            self.circle["y"] = max(0, min(self.img_h - 1, y - oy))
        else:
            dx = x - self.circle["x"]
            dy = y - self.circle["y"]
            nr = int((dx * dx + dy * dy) ** 0.5)
            max_r = min(
                self.circle["x"],
                self.img_w - 1 - self.circle["x"],
                self.circle["y"],
                self.img_h - 1 - self.circle["y"],
            )
            self.circle["r"] = max(4, min(max_r, nr))
        self._redraw()

    def _on_release(self, _event) -> None:
        self.mode = ""

    def _save(self) -> None:
        if self.circle["r"] < 4:
            messagebox.showwarning("提示", "半径太小。")
            return
        self.on_save(self.circle, self.img_w, self.img_h)
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
    cx = int(box_w * cfg.unread_badge_circle.x)
    y_from_bottom = int(box_h * cfg.unread_badge_circle.y)
    cy = box_h - y_from_bottom
    r = int(min(box_w, box_h) * cfg.unread_badge_circle.r)
    circle = {
        "x": max(0, min(box_w - 1, cx)),
        "y": max(0, min(box_h - 1, cy)),
        "r": max(4, min(min(box_w, box_h) // 2, r if r > 0 else 8)),
    }
    print(
        f"[start] unread-calibrator row_box=({box_x},{box_y},{box_w},{box_h}) "
        f"circle=({circle['x']},{circle['y']},r={circle['r']})"
    )

    def _save(c: dict, w: int, h: int) -> None:
        rx = max(0.0, min(1.0, c["x"] / max(1, w)))
        ry = max(0.0, min(1.0, (h - c["y"]) / max(1, h)))
        rr = max(0.02, min(0.49, c["r"] / max(1, min(w, h))))
        text = config_path.read_text(encoding="utf-8")
        text = _upsert_section(
            text,
            "unread_badge_circle",
            [
                ("enabled", "true"),
                ("x", f"{rx:.6f}"),
                ("y", f"{ry:.6f}"),
                ("r", f"{rr:.6f}"),
            ],
        )
        config_path.write_text(text, encoding="utf-8")
        print(f"[saved] unread_badge_circle=(enabled=true,x={rx:.4f},y={ry:.4f},r={rr:.4f})")

    ui = BadgeCircleUI(image=shot, circle=circle, on_save=_save)
    ui.run()


if __name__ == "__main__":
    main()
