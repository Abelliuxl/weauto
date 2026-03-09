from __future__ import annotations

from dataclasses import dataclass
import os

import Quartz
import pyautogui
from PIL import Image


@dataclass
class WindowBounds:
    x: int
    y: int
    width: int
    height: int
    window_id: int


class WindowNotFoundError(RuntimeError):
    pass


def _cgimage_to_pil(cg_image) -> Image.Image:
    width = int(Quartz.CGImageGetWidth(cg_image))
    height = int(Quartz.CGImageGetHeight(cg_image))
    if width <= 0 or height <= 0:
        raise RuntimeError("empty CGImage")
    bytes_per_row = int(Quartz.CGImageGetBytesPerRow(cg_image))
    provider = Quartz.CGImageGetDataProvider(cg_image)
    data = Quartz.CGDataProviderCopyData(provider)
    # CoreGraphics little-endian 32-bit buffers are typically BGRA.
    return Image.frombuffer(
        "RGBA",
        (width, height),
        bytes(data),
        "raw",
        "BGRA",
        bytes_per_row,
        1,
    )


def get_front_window_bounds(app_name: str) -> WindowBounds:
    # Allow aliases like "WeChat|微信" and fuzzy matching against owner name.
    aliases = [x.strip() for x in app_name.split("|") if x.strip()]
    if "WeChat" in aliases and "微信" not in aliases:
        aliases.append("微信")

    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    if window_list is None:
        raise WindowNotFoundError(
            "Unable to query macOS window list. "
            "Please run in a logged-in desktop session and grant Screen Recording/Accessibility permissions."
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


def screenshot_region(left: int, top: int, width: int, height: int, *, high_res: bool = False):
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid screenshot region: width={width} height={height}")

    use_high_res = bool(high_res)
    env_force = os.environ.get("WEAUTO_SCREENSHOT_HIGH_RES", "").strip().lower()
    if env_force in {"1", "true", "yes", "on"}:
        use_high_res = True
    if env_force in {"0", "false", "no", "off"}:
        use_high_res = False

    if use_high_res:
        try:
            rect = Quartz.CGRectMake(int(left), int(top), int(width), int(height))
            cg_img = Quartz.CGWindowListCreateImage(
                rect,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault,
            )
            if cg_img is not None:
                return _cgimage_to_pil(cg_img)
        except Exception:
            pass

    return pyautogui.screenshot(region=(left, top, width, height))
