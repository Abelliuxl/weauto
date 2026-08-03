#!/usr/bin/env python3
"""Generate the WeAuto.app icon (purple gradient + chat bubble + W).

Usage:
    python scripts/generate_app_icon.py

Rebuilds WeAuto.app/Contents/Resources/AppIcon.icns and icon_512.png.
Requires Pillow (already a project dependency) and iconutil (built into macOS).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_BUNDLE = PROJECT_ROOT / "WeAuto.app"
RESOURCES = APP_BUNDLE / "Contents" / "Resources"


def _rounded_gradient(size: int, radius: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            img.putpixel((x, y), (r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _load_font(size: int) -> ImageFont.ImageFont:
    for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    """Render the icon at ``size``×``size``."""
    img = _rounded_gradient(size, int(size * 0.22), (30, 40, 90), (100, 50, 150))
    draw = ImageDraw.Draw(img)

    # White chat bubble.
    bw, bh = int(size * 0.58), int(size * 0.44)
    cx, cy = size // 2, int(size * 0.44)
    r = int(min(bw, bh) * 0.22)
    x0, y0, x1, y1 = cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=(245, 247, 255, 255))
    tail = [
        (x0 + int(bw * 0.10), y1 - int(bh * 0.02)),
        (x0 + int(bw * 0.01), y1 + int(bh * 0.20)),
        (x0 + int(bw * 0.34), y1 - int(bh * 0.06)),
    ]
    draw.polygon(tail, fill=(245, 247, 255, 255))

    # Letter "W".
    font = _load_font(int(size * 0.28))
    try:
        bbox = font.getbbox("W")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        ox, oy = bbox[0], bbox[1]
    except Exception:
        tw, th, ox, oy = size // 3, size // 4, 0, 0
    draw.text((cx - tw // 2 - ox, cy - th // 2 - oy), "W", fill=(70, 45, 130, 255), font=font)

    # Three typing-indicator dots.
    dot_r = max(2, int(size * 0.035))
    dot_y = int(size * 0.75)
    spacing = int(size * 0.085)
    for i, alpha in enumerate((110, 170, 255)):
        dx = size // 2 + (i - 1) * spacing
        draw.ellipse([dx - dot_r, dot_y - dot_r, dx + dot_r, dot_y + dot_r], fill=(210, 215, 235, alpha))
    return img


def build_icns() -> None:
    RESOURCES.mkdir(parents=True, exist_ok=True)
    icons = {s: make_icon(s) for s in (16, 32, 64, 128, 256, 512, 1024)}
    icons[512].save(RESOURCES / "icon_512.png")
    iconset = Path("/tmp/WeAuto.iconset")
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for base, retina, name in (
        (16, 32, "icon_16x16"),
        (32, 64, "icon_32x32"),
        (128, 256, "icon_128x128"),
        (256, 512, "icon_256x256"),
        (512, 1024, "icon_512x512"),
    ):
        icons[base].save(iconset / f"{name}.png")
        icons[retina].save(iconset / f"{name}@2x.png")
    icns = RESOURCES / "AppIcon.icns"
    r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], capture_output=True, text=True)
    shutil.rmtree(iconset, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"iconutil failed: {r.stderr}")
    print(f"built {icns}")


if __name__ == "__main__":
    build_icns()
