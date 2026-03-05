from __future__ import annotations

from dataclasses import dataclass

import Quartz
import pyautogui


@dataclass
class WindowBounds:
    x: int
    y: int
    width: int
    height: int
    window_id: int


class WindowNotFoundError(RuntimeError):
    pass


def get_front_window_bounds(app_name: str) -> WindowBounds:
    # Allow aliases like "WeChat|微信" and fuzzy matching against owner name.
    aliases = [x.strip() for x in app_name.split("|") if x.strip()]
    if "WeChat" in aliases and "微信" not in aliases:
        aliases.append("微信")

    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )

    candidates: list[WindowBounds] = []
    for window in window_list:
        owner = window.get("kCGWindowOwnerName", "")
        if not any(alias.lower() in str(owner).lower() for alias in aliases):
            continue

        layer = int(window.get("kCGWindowLayer", 0))
        if layer != 0:
            continue

        bounds = window.get("kCGWindowBounds", {})
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
        if width <= 0 or height <= 0:
            continue

        candidates.append(
            WindowBounds(
                x=int(bounds.get("X", 0)),
                y=int(bounds.get("Y", 0)),
                width=width,
                height=height,
                window_id=int(window.get("kCGWindowNumber", 0)),
            )
        )

    if not candidates:
        raise WindowNotFoundError(
            f"WeChat window not found: app_name={app_name!r}. "
            "Please open WeChat desktop and keep at least one chat window visible."
        )

    # Prefer the largest visible main window.
    return max(candidates, key=lambda w: w.width * w.height)


def screenshot_region(left: int, top: int, width: int, height: int):
    return pyautogui.screenshot(region=(left, top, width, height))
