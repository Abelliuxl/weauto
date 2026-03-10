#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata


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
            "Missing tkinter. Run `./carlibrate_recover_auto.sh config.toml` "
            "or use `.venv312/bin/python carlibrate_recover_auto_ui.py --config config.toml`."
        ) from exc
    raise

import pyautogui
from PIL import ImageTk

from wechat_rpa.config import PointRatio, load_config
from wechat_rpa.window import WindowNotFoundError, get_front_window_bounds, screenshot_region

SCROLL_STEP = 120
CROSSHAIR_BOX = 44


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visual calibrator for recover-auto click point and scroll amplitude."
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


def _upsert_point_section(text: str, section: str, x: float, y: float) -> str:
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
    block = [hdr, f"x = {x:.6f}", f"y = {y:.6f}"]
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


def _safe_click(x: int, y: int) -> None:
    pyautogui.moveTo(x, y, duration=0.12)
    pyautogui.mouseDown()
    time.sleep(0.03)
    pyautogui.mouseUp()


def _perform_scroll(amount: int) -> None:
    total = int(amount)
    if total == 0:
        return
    direction = 1 if total > 0 else -1
    remaining = abs(total)
    while remaining > 0:
        step = min(120, remaining)
        pyautogui.scroll(direction * step)
        remaining -= step
        time.sleep(0.015)


def _parse_scroll_input(raw: str, current: int) -> int | None:
    answer = unicodedata.normalize("NFKC", raw or "").strip()
    if not answer:
        return current
    if re.fullmatch(r"[+-]\d+", answer):
        return max(1, current + int(answer))
    if re.fullmatch(r"\d+", answer):
        return max(1, int(answer))
    return None


def _save_click_point(config_path: Path, point: PointRatio) -> None:
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    text = _upsert_point_section(
        text,
        "recover_auto_click_point",
        point.x,
        point.y,
    )
    config_path.write_text(text, encoding="utf-8")


def _save_scroll_amount(config_path: Path, amount: int) -> None:
    if not config_path.exists():
        raise SystemExit(f"config not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    text = _upsert_top_level_key(text, "recover_auto_scroll_amount", str(int(amount)))
    config_path.write_text(text, encoding="utf-8")


def _point_to_abs(bounds, point: PointRatio) -> tuple[int, int]:
    return (
        bounds.x + int(bounds.width * point.x),
        bounds.y + int(bounds.height * point.y),
    )


class RecoverAutoPointUI:
    def __init__(self, image, *, init_point: PointRatio, on_save) -> None:
        self.image = image
        self.img_w, self.img_h = image.size
        self.on_save = on_save
        self.point_x = (
            int(self.img_w * init_point.x) if 0.0 <= init_point.x <= 1.0 else self.img_w // 2
        )
        self.point_y = (
            int(self.img_h * init_point.y) if 0.0 <= init_point.y <= 1.0 else self.img_h // 2
        )

        self.root = tk.Tk()
        self.root.title("Recover Auto 点击点校准")

        max_w = self.root.winfo_screenwidth() - 80
        max_h = self.root.winfo_screenheight() - 180
        win_w = min(max_w, self.img_w + 40)
        win_h = min(max_h, self.img_h + 140)
        self.root.geometry(f"{max(860, win_w)}x{max(620, win_h)}")

        bar = tk.Frame(self.root)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Button(bar, text="保存并继续", command=self._save).pack(side=tk.RIGHT)
        tk.Button(bar, text="取消", command=self._close).pack(side=tk.RIGHT, padx=(0, 6))
        tk.Label(
            self.root,
            text=(
                "点击聊天区里的安全空白点。绿色方框中心十字就是实际点击点，"
                "recover-auto 会一直点这里后再上滑。"
            ),
            anchor="w",
        ).pack(fill=tk.X, padx=8, pady=(0, 4))
        self.status_var = tk.StringVar()
        tk.Label(self.root, textvariable=self.status_var, anchor="w").pack(
            fill=tk.X, padx=8, pady=(0, 6)
        )

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
        self.canvas.bind("<Button-1>", self._on_click)
        self.root.bind("<Return>", lambda _event: self._save())
        self.root.bind("<Escape>", lambda _event: self._close())
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("overlay")
        x = self.point_x
        y = self.point_y
        half = CROSSHAIR_BOX // 2
        x1 = max(0, x - half)
        y1 = max(0, y - half)
        x2 = min(self.img_w, x + half)
        y2 = min(self.img_h, y + half)
        color = "#00b96b"
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, tags=("overlay",))
        self.canvas.create_line(x, y1 - 12, x, y2 + 12, fill=color, width=2, tags=("overlay",))
        self.canvas.create_line(x1 - 12, y, x2 + 12, y, fill=color, width=2, tags=("overlay",))
        self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color, tags=("overlay",))
        self.status_var.set(f"点击点: ({x}, {y})")

    def _on_click(self, event) -> None:
        self.point_x = max(0, min(self.img_w - 1, int(self.canvas.canvasx(event.x))))
        self.point_y = max(0, min(self.img_h - 1, int(self.canvas.canvasy(event.y))))
        self._redraw()

    def _save(self) -> None:
        if self.img_w <= 0 or self.img_h <= 0:
            messagebox.showwarning("提示", "截图尺寸无效。")
            return
        point = PointRatio(
            x=self.point_x / self.img_w,
            y=self.point_y / self.img_h,
        )
        self.on_save(point)
        self._close()

    def _close(self) -> None:
        self.root.withdraw()
        try:
            self.root.update()
        except tk.TclError:
            pass
        self.root.quit()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def _run_scroll_tuning(config_path: Path, point: PointRatio) -> None:
    cfg = load_config(config_path)
    amount = max(SCROLL_STEP, abs(int(cfg.recover_auto_scroll_amount)))
    pause_sec = max(0.15, float(cfg.recover_auto_scroll_pause_sec))
    try:
        input("[recover-auto] 点击点已保存。请切到目标聊天并放在可继续上滑的位置，按 Enter 开始幅度校准...")
    except EOFError:
        pass

    while True:
        _activate_wechat(cfg.app_name)
        time.sleep(max(0.0, cfg.activate_wait_sec))
        try:
            bounds = get_front_window_bounds(cfg.app_name)
        except WindowNotFoundError as exc:
            raise SystemExit(f"[error] {exc}") from exc
        click_x, click_y = _point_to_abs(bounds, point)
        _safe_click(click_x, click_y)
        time.sleep(0.08)
        _perform_scroll(amount)
        time.sleep(pause_sec)
        answer = input(
            f"[recover-auto] 当前上滚次数={amount}。输入 +120 / -80 调整，或输入 960 直接设定，直接回车确认: "
        )
        next_amount = _parse_scroll_input(answer, amount)
        if next_amount is None:
            print("[recover-auto] 无效输入，只接受 +数字 / -数字 / 数字 / 直接回车。")
            continue
        if next_amount == amount and not str(answer).strip():
            _save_scroll_amount(config_path, amount)
            print(f"[save] recover_auto_scroll_amount={amount} (scroll steps)")
            return

        _activate_wechat(cfg.app_name)
        time.sleep(max(0.0, cfg.activate_wait_sec))
        try:
            bounds = get_front_window_bounds(cfg.app_name)
        except WindowNotFoundError as exc:
            raise SystemExit(f"[error] {exc}") from exc
        click_x, click_y = _point_to_abs(bounds, point)
        _safe_click(click_x, click_y)
        time.sleep(0.08)
        _perform_scroll(-amount)
        time.sleep(pause_sec)
        print(f"[recover-auto] 次数更新: {amount} -> {next_amount}")
        amount = next_amount


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    cfg = load_config(config_path)

    _activate_wechat(cfg.app_name)
    time.sleep(max(0.0, cfg.activate_wait_sec))
    try:
        bounds = get_front_window_bounds(cfg.app_name)
    except WindowNotFoundError as exc:
        print(f"[error] {exc}")
        raise SystemExit(1)

    shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height, high_res=True)
    saved_point: dict[str, PointRatio] = {}

    def on_save(point: PointRatio) -> None:
        _save_click_point(config_path, point)
        saved_point["point"] = point
        print(
            f"[save] recover_auto_click_point=({point.x:.6f}, {point.y:.6f})"
        )

    ui = RecoverAutoPointUI(
        shot,
        init_point=cfg.recover_auto_click_point,
        on_save=on_save,
    )
    ui.run()
    point = saved_point.get("point")
    if point is None:
        print("[recover-auto] cancelled")
        return
    _run_scroll_tuning(config_path, point)


if __name__ == "__main__":
    main()
