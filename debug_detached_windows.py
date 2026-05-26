#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from PIL import ImageDraw, ImageFont

from wechat_rpa.config import load_config
from wechat_rpa.detached_window_receiver import (
    capture_window_by_id,
    list_detached_wechat_windows,
    safe_window_name,
)
from wechat_rpa.ocr import OcrEngine
from wechat_rpa.visible_message_parser import VisibleMessageParser
from wechat_rpa.visible_message_state import VisibleMessageStateStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture detached WeChat windows and parse visible messages.")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--title", default="", help="Only parse windows whose title contains this text.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--debug", action="store_true", help="Include OCR debug lines in JSON.")
    parser.add_argument("--watch", action="store_true", help="Poll windows continuously and print new messages.")
    parser.add_argument("--interval", type=float, default=1.5)
    return parser.parse_args()


def annotate(image, messages: list[dict], path: Path) -> None:
    out = image.convert("RGB")
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 20)
    except Exception:
        font = ImageFont.load_default()
    for idx, msg in enumerate(messages, start=1):
        x1, y1, x2, y2 = [int(v) for v in msg.get("bbox", [0, 0, 0, 0])]
        color = (0, 170, 0) if msg.get("side") == "self" and msg.get("content_type") == "text" else (0, 90, 255)
        if msg.get("content_type") == "image":
            color = (255, 120, 0)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=4)
        label = f"{idx} {msg.get('side')} {msg.get('content_type')} {msg.get('sender') or ''}"[:45]
        draw.text((x1, max(0, y1 - 25)), label, fill=color, font=font)
    out.save(path)


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.output_dir or (Path.home() / "Downloads" / f"weauto_detached_windows_{stamp}"))
    out_root.mkdir(parents=True, exist_ok=True)

    windows = list_detached_wechat_windows(cfg.app_name)
    if args.title:
        windows = [w for w in windows if args.title in w.title]
    (out_root / "windows.json").write_text(
        json.dumps([asdict(w) for w in windows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ocr = OcrEngine(cfg.ocr, log_fn=print)
    parser = VisibleMessageParser(ocr)
    state = VisibleMessageStateStore()
    print(f"[detached] windows={len(windows)} out={out_root}")
    while True:
        if args.watch:
            windows = list_detached_wechat_windows(cfg.app_name)
            if args.title:
                windows = [w for w in windows if args.title in w.title]
        for window in windows:
            process_window(args, parser, state, out_root, window)
        if not args.watch:
            break
        time.sleep(max(0.2, float(args.interval)))


def process_window(
    args: argparse.Namespace,
    parser: VisibleMessageParser,
    state: VisibleMessageStateStore,
    out_root: Path,
    window,
) -> None:
    win_dir = out_root / f"id{window.window_id}_{safe_window_name(window.title)}"
    win_dir.mkdir(parents=True, exist_ok=True)
    image = capture_window_by_id(window.window_id)
    image.save(win_dir / "window_capture.png")
    snapshot = parser.parse(
        image,
        window_id=window.window_id,
        title=window.title,
        image_output_dir=win_dir / "images",
        include_debug=args.debug,
    )
    payload = asdict(snapshot)
    (win_dir / "visible_messages.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = []
    for idx, message in enumerate(snapshot.messages, start=1):
        lines.append(
            f"{idx:02d}. {message.get('side')} {message.get('sender') or '-'} "
            f"{message.get('content_type')} {message.get('text') or message.get('image_hash', '')}"
        )
    (win_dir / "visible_messages.txt").write_text("\n".join(lines), encoding="utf-8")
    annotate(image, snapshot.messages, win_dir / "messages_annotated.png")
    new_messages = state.update(window_id=window.window_id, messages=snapshot.messages)
    if args.watch:
        for message in new_messages:
            if not state.is_incoming(message):
                continue
            print(
                f"[new] id={window.window_id} title={window.title!r} "
                f"sender={message.get('sender') or '-'} "
                f"type={message.get('content_type')} "
                f"text={message.get('text') or message.get('image_hash', '')}"
            )
    else:
        print(f"[detached] id={window.window_id} title={window.title!r} messages={len(snapshot.messages)}")


if __name__ == "__main__":
    main()
