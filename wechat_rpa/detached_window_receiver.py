from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
import re

import Quartz
from PIL import Image

try:
    import objc
except Exception:  # pragma: no cover
    objc = None


@dataclass
class DetachedWindowInfo:
    window_id: int
    owner: str
    title: str
    x: int
    y: int
    width: int
    height: int


def _autorelease_pool():
    if objc is None:
        return nullcontext()
    return objc.autorelease_pool()


def _cgimage_to_pil(cg_image) -> Image.Image:
    width = int(Quartz.CGImageGetWidth(cg_image))
    height = int(Quartz.CGImageGetHeight(cg_image))
    if width <= 0 or height <= 0:
        raise RuntimeError("empty CGImage")
    bytes_per_row = int(Quartz.CGImageGetBytesPerRow(cg_image))
    provider = Quartz.CGImageGetDataProvider(cg_image)
    data = Quartz.CGDataProviderCopyData(provider)
    return Image.frombytes(
        "RGBA",
        (width, height),
        bytes(data),
        "raw",
        "BGRA",
        bytes_per_row,
        1,
    )


def _app_aliases(app_name: str) -> list[str]:
    aliases = [x.strip() for x in str(app_name or "").split("|") if x.strip()]
    if "WeChat" in aliases and "微信" not in aliases:
        aliases.append("微信")
    if not aliases:
        aliases = ["WeChat", "微信"]
    return aliases


def list_detached_wechat_windows(app_name: str = "WeChat") -> list[DetachedWindowInfo]:
    aliases = _app_aliases(app_name)
    windows: list[DetachedWindowInfo] = []
    with _autorelease_pool():
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
        )
        if window_list is None:
            return windows
        for item in window_list:
            owner = str(item.get("kCGWindowOwnerName", ""))
            if not any(alias.lower() in owner.lower() for alias in aliases):
                continue
            if int(item.get("kCGWindowLayer", 0)) != 0:
                continue
            bounds = item.get("kCGWindowBounds", {}) or {}
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            if width <= 0 or height <= 0:
                continue
            title = str(item.get("kCGWindowName", "") or "").strip()
            windows.append(
                DetachedWindowInfo(
                    window_id=int(item.get("kCGWindowNumber", 0)),
                    owner=owner,
                    title=title,
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=width,
                    height=height,
                )
            )
    return windows


def capture_window_by_id(window_id: int) -> Image.Image:
    with _autorelease_pool():
        cg_img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            int(window_id),
            Quartz.kCGWindowImageBoundsIgnoreFraming,
        )
        if cg_img is None:
            raise RuntimeError(f"window capture failed: window_id={window_id}")
        return _cgimage_to_pil(cg_img)


def safe_window_name(text: str) -> str:
    clean = re.sub(r"\s+", "_", str(text or "").strip())
    clean = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", clean)
    return clean[:80] or "untitled"


def save_window_capture(window: DetachedWindowInfo, image: Image.Image, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"id{window.window_id}_{safe_window_name(window.title)}.png"
    image.save(path)
    return path
