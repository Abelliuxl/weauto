"""Capture+OCR worker subprocess.

The high-frequency detached-window capture (``capture_window_by_id``) and the
new-message OCR (``VisibleMessageParser.parse``) both allocate native memory
that Python's garbage collector cannot reclaim:

* macOS ``CGWindowListCreateImage`` grows Mach/CoreGraphics memory per call.
* cv2 / onnxruntime intermediate buffers fragment native heaps under load.

Over hours of running on large chat windows this drives the main bot process
RSS into the multi-GB range, eventually forcing a full-process restart via the
memory watchdog. That restart tears down sessions, the long bridge, and
persistent memory state.

This module isolates the capture (and optionally the OCR parse) inside a
dedicated child process. The child holds all the leaky native memory,
self-checks its RSS after each request, and exits cleanly when it exceeds the
limit — the bot (in ``wechat_rpa.bot``) spawns a fresh worker in its place.
The main bot process therefore never needs to restart for this reason.

IPC protocol (one JSON object per line, UTF-8, on stdout/stdin):

* worker -> bot:  ``{"ready": true}`` once after engine warmup.
* bot -> worker:  ``{"mode": "capture_only"|"parse", "window_id": 123,
                   "title": "...", "capture_backend": "quartz"}``
* worker -> bot (capture_only):
                   ``{"image_path": "/tmp/.../win.png", "body_hash": "...",
                      "image_size": {"width": w, "height": h}}``
                   The bot loads the PNG (cheap PIL read, no CoreGraphics),
                   uses it for the vision LLM / hash, then deletes the file.
* worker -> bot (parse):
                   ``{"snapshot": {...VisibleChatSnapshot as dict...}``
                   OR ``{"error": "..."}`` (fail-open on the bot side).
* worker -> bot:  ``{"__exit__": true, "reason": "rss_limit"}`` then exits
                   when its own RSS exceeds the configured limit.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import AppConfig
from .visible_message_parser import VisibleChatSnapshot, VisibleMessageParser


def _current_rss_mb() -> int:
    try:
        proc = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if proc.returncode == 0:
            return max(0, int((proc.stdout or "0").strip() or "0") // 1024)
    except Exception:
        pass
    return 0


def _emit(payload: dict[str, Any]) -> None:
    """Write one JSON line to stdout and flush so the bot can read it promptly."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _body_hash(image) -> str:
    """Downsample the chat body to a 96x160 grayscale and SHA-1 it.

    Mirrors ``WeChatGuiRpaBot._detached_chat_body_hash`` so the bot's change
    detection works identically whether capture happens in-process or here.
    """
    from PIL import Image as _PILImage

    width, height = image.size
    body_y1, body_y2 = VisibleMessageParser._chat_body_bounds(height)
    body_y1 = max(0, min(height, body_y1))
    body_y2 = max(body_y1, min(height, body_y2))
    crop = image.crop((0, body_y1, width, body_y2))
    try:
        small = crop.convert("L").resize((96, 160))
        try:
            return hashlib.sha1(small.tobytes()).hexdigest()
        finally:
            small.close()
    finally:
        crop.close()


# Per-worker temp dir for capture_only PNGs. Recreated on each worker spawn;
# cleaned up when the worker process exits (OS reaps /tmp) or is recycled.
_CAPTURE_TMP_DIR: Path | None = None


def _capture_tmp_dir() -> Path:
    global _CAPTURE_TMP_DIR
    if _CAPTURE_TMP_DIR is None or not _CAPTURE_TMP_DIR.exists():
        _CAPTURE_TMP_DIR = Path(tempfile.mkdtemp(prefix="weauto-capture-"))
    return _CAPTURE_TMP_DIR


def run_worker(
    *,
    ocr_cfg,
    capture_backend: str,
    max_rss_mb: int,
    log_fn=None,
) -> None:
    """Run the capture (+ optional OCR) worker loop until stdin closes or RSS
    exceeds the limit.

    The RapidOCR engine is constructed ONCE here (warmup cost paid up front)
    and reused for every ``parse`` request — this is the one place rapidocr/cv2
    get imported for the detached-window path. ``capture_only`` requests never
    touch OCR.
    """
    # Import locally so the module can be imported (e.g. in tests) without
    # pulling in the heavy OCR / Quartz stack.
    from .ocr import OcrEngine
    from .detached_window_receiver import capture_window_by_id, list_detached_wechat_windows

    engine = OcrEngine(ocr_cfg, log_fn=log_fn)
    parser = VisibleMessageParser(engine)

    _emit({"ready": True})

    while True:
        raw_line = sys.stdin.readline()
        if not raw_line:
            break
        try:
            request = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            _emit({"error": "invalid request json"})
            continue

        # Check RSS BEFORE doing the heavy capture: if we are already over
        # budget, hand control back to the bot so it spawns a fresh worker for
        # this request. This avoids both losing an expensive result and leaving
        # a stale __exit__ line in the pipe.
        if max_rss_mb > 0 and _current_rss_mb() >= max_rss_mb:
            _emit({"__exit__": True, "reason": "rss_limit", "rss_mb": _current_rss_mb()})
            break

        mode = str(request.get("mode", "parse") or "parse").strip().lower()

        # list_windows: enumerate detached WeChat windows WITHOUT capturing.
        # CGWindowListCopyWindowInfo also leaks Mach message memory, so it must
        # live in the worker too, not the main process.
        if mode == "list_windows":
            app_name = str(request.get("app_name", "WeChat") or "WeChat")
            try:
                found = list_detached_wechat_windows(app_name)
                payload = {
                    "windows": [
                        {
                            "window_id": int(w.window_id),
                            "owner": str(w.owner),
                            "title": str(w.title),
                            "x": int(w.x),
                            "y": int(w.y),
                            "width": int(w.width),
                            "height": int(w.height),
                        }
                        for w in found
                    ]
                }
            except Exception as exc:  # noqa: BLE001 - worker must stay alive
                _emit({"error": f"list_windows failed: {type(exc).__name__}: {exc}"})
                continue
            _emit(payload)
            continue

        window_id = int(request.get("window_id", 0))
        title = str(request.get("title", "") or "")
        backend = str(request.get("capture_backend") or capture_backend)

        try:
            image = capture_window_by_id(window_id, backend=backend)
        except Exception as exc:  # noqa: BLE001 - worker must stay alive
            _emit({"error": f"capture failed: {type(exc).__name__}: {exc}"})
            continue

        try:
            if mode == "capture_only":
                # Persist the capture to a temp PNG the bot can cheaply read
                # back. CoreGraphics memory stays in THIS process; the bot only
                # does a PIL file read (no Mach growth).
                tmp_path = _capture_tmp_dir() / f"win_{window_id}.png"
                image.save(tmp_path)
                payload = {
                    "image_path": str(tmp_path),
                    "body_hash": _body_hash(image),
                    "image_size": {"width": int(image.size[0]), "height": int(image.size[1])},
                }
            else:
                image_output_dir_raw = request.get("image_output_dir")
                image_output_dir: Path | None = None
                if image_output_dir_raw:
                    image_output_dir = Path(str(image_output_dir_raw))
                include_debug = bool(request.get("include_debug", False))
                snapshot = parser.parse(
                    image,
                    window_id=window_id,
                    title=title,
                    image_output_dir=image_output_dir,
                    include_debug=include_debug,
                )
                payload = {"snapshot": asdict(snapshot)}
        except Exception as exc:  # noqa: BLE001 - worker must stay alive
            _emit({"error": f"{type(exc).__name__}: {exc}"})
            continue
        finally:
            try:
                image.close()
            except Exception:
                pass

        _emit(payload)


def _worker_main() -> None:
    """Entry point used by the bot when spawning the worker subprocess.

    Configuration is passed via environment variables (kept simple and stable
    across bot versions) rather than argv, to avoid quoting issues.
    """
    from .config import load_config

    cfg: AppConfig = load_config(os.environ.get("WEAUTO_CONFIG_PATH") or "config.toml")
    run_worker(
        ocr_cfg=cfg.ocr,
        capture_backend=cfg.detached_window_capture_backend,
        max_rss_mb=int(os.environ.get("WEAUTO_OCR_WORKER_MAX_RSS_MB", "0") or "0"),
    )


if __name__ == "__main__":
    _worker_main()
