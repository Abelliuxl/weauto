from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import cv2
import numpy as np

from .config import AppConfig
from .ocr import OcrLine, OcrEngine
from .window import WindowBounds

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_UNREAD_NUM_RE = re.compile(r"^\d{1,3}$")


@dataclass
class ChatRowState:
    row_idx: int
    text: str
    title: str
    preview: str
    has_mention: bool
    has_unread_badge: bool
    fingerprint: str
    click_x_ratio: float
    click_y_ratio: float


@dataclass
class DetectionResult:
    rows: list[ChatRowState]


def _load_manual_row_boxes(cfg: AppConfig, bounds: WindowBounds) -> list[tuple[int, int, int, int]]:
    path = Path(cfg.manual_row_boxes_path)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    boxes_raw = raw.get("boxes", []) if isinstance(raw, dict) else []
    if not isinstance(boxes_raw, list):
        return []

    boxes: list[tuple[int, int, int, int]] = []
    for item in boxes_raw:
        if not isinstance(item, dict):
            continue
        try:
            rx = float(item.get("x", 0.0))
            ry = float(item.get("y", 0.0))
            rw = float(item.get("w", 0.0))
            rh = float(item.get("h", 0.0))
        except Exception:
            continue

        x = int(bounds.width * max(0.0, min(1.0, rx)))
        y = int(bounds.height * max(0.0, min(1.0, ry)))
        w = int(bounds.width * max(0.0, min(1.0, rw)))
        h = int(bounds.height * max(0.0, min(1.0, rh)))
        if w < 20 or h < 12:
            continue
        if x >= bounds.width or y >= bounds.height:
            continue
        w = min(w, max(1, bounds.width - x))
        h = min(h, max(1, bounds.height - y))
        boxes.append((x, y, w, h))
    return boxes


def _normalize_text(lines: list[OcrLine]) -> list[str]:
    values: list[str] = []
    for line in lines:
        txt = line.text.strip()
        if not txt:
            continue
        values.append(txt)
    return values


def _build_fingerprint(values: list[str]) -> str:
    normalized = " | ".join(values)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _estimate_row_start_y(ocr_lines: list[OcrLine], list_w: int, row_height: int) -> int:
    # Keep ratio-based row height; only auto-correct global vertical drift.
    candidates: list[float] = []
    for line in ocr_lines:
        txt = (line.text or "").strip()
        if not txt or _TIME_RE.match(txt):
            continue
        if "=" in txt and len(txt) >= 8:
            # ignore code/config-like lines from unexpected capture content
            continue
        # focus on title/preview area, ignore avatar extreme-left and right timestamp area
        if not (list_w * 0.16 <= line.x_center <= list_w * 0.84):
            continue
        candidates.append(float(line.y_center))

    if not candidates:
        return 0

    first_y = min(candidates)
    # Push buckets slightly upward to prevent progressive downward drift.
    start = int(round(first_y - (row_height * 0.46))) - 2
    # avoid aggressive shifts
    max_shift = int(row_height * 0.42)
    return max(0, min(max_shift, start))


def _extract_title_preview(values: list[str]) -> tuple[str, str]:
    cleaned = [v for v in values if not _TIME_RE.match(v)]
    # OCR may read unread badge number as the first line (e.g. "2").
    # If there are other lines, drop such leading numeric badge-like tokens.
    while len(cleaned) >= 2 and _UNREAD_NUM_RE.match(cleaned[0].strip()):
        if _UNREAD_NUM_RE.match(cleaned[1].strip()):
            break
        cleaned = cleaned[1:]

    if not cleaned:
        return "", ""
    title = cleaned[0]
    preview = " ".join(cleaned[1:]) if len(cleaned) > 1 else ""

    def _is_sender_prefixed(text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        sep = "：" if "：" in raw else (":" if ":" in raw else "")
        if not sep:
            return False
        left = raw.split(sep, 1)[0].strip(" []【】()（）")
        return 1 <= len(left) <= 24

    # OCR may occasionally return preview first and title second in a row bucket.
    # Heuristic: preview commonly has sender prefix, title usually doesn't.
    if len(cleaned) >= 2:
        first = cleaned[0].strip()
        second = cleaned[1].strip()
        first_prefixed = _is_sender_prefixed(first)
        second_prefixed = _is_sender_prefixed(second)
        second_short = 1 <= len(second) <= 24
        first_long = len(first) >= 10
        # tighten condition to reduce accidental cross-row swaps
        if first_prefixed and (not second_prefixed) and second_short and first_long and len(cleaned) <= 3:
            title = second
            rest = [first] + cleaned[2:]
            preview = " ".join([x for x in rest if x]).strip()

    # If title is still a pure number, it's very likely unread badge OCR.
    # Try to split preview into "<title> <preview>" once.
    if _UNREAD_NUM_RE.match((title or "").strip()) and preview:
        m = re.match(r"^\s*([^\s]{1,36})\s+(.+?)\s*$", preview)
        if m:
            title = m.group(1).strip()
            preview = m.group(2).strip()
    return title, preview


def _contains_mention(values: list[str], keywords: list[str], mention_any_at: bool) -> bool:
    merged = " ".join(values)
    if not merged:
        return False
    safe_keywords = [k for k in keywords if k and k != "@"]
    if any(keyword in merged for keyword in safe_keywords):
        return True
    return mention_any_at and "@" in merged


def _has_unread_badge(row_img_bgr: np.ndarray, min_blob_pixels: int) -> bool:
    h, w = row_img_bgr.shape[:2]
    # WeChat on macOS often shows unread numeric dots near avatar top-left.
    rois = [
        row_img_bgr[0 : int(h * 0.6), 0 : int(w * 0.42)],
        row_img_bgr[0 : int(h * 0.85), int(w * 0.72) : w],
    ]

    # Wider red range for macOS WeChat badge shades.
    lower_red_1 = np.array([0, 45, 70], dtype=np.uint8)
    upper_red_1 = np.array([20, 255, 255], dtype=np.uint8)
    lower_red_2 = np.array([160, 45, 70], dtype=np.uint8)
    upper_red_2 = np.array([180, 255, 255], dtype=np.uint8)

    for roi in rois:
        if roi.size == 0:
            continue
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(
            hsv, lower_red_2, upper_red_2
        )
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # If red occupies enough area in badge ROI, treat as unread.
        red_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        if red_ratio >= 0.006:
            return True

        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        for label in range(1, num_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            bw = int(stats[label, cv2.CC_STAT_WIDTH])
            bh = int(stats[label, cv2.CC_STAT_HEIGHT])
            if bw <= 0 or bh <= 0:
                continue
            aspect = bw / bh
            # Filter out long red text blocks; keep badge-like blobs.
            if (min_blob_pixels // 2) <= area <= int(h * w * 0.10) and 0.45 <= aspect <= 2.2:
                return True
    return False


def detect_chat_rows(
    screenshot_rgb: np.ndarray,
    bounds: WindowBounds,
    cfg: AppConfig,
    ocr_engine: OcrEngine,
) -> DetectionResult:
    if cfg.use_manual_row_boxes:
        manual_boxes = _load_manual_row_boxes(cfg, bounds)
        if manual_boxes:
            rows: list[ChatRowState] = []
            for row_idx, (bx, by, bw, bh) in enumerate(manual_boxes):
                row_rgb = screenshot_rgb[by : by + bh, bx : bx + bw]
                if row_rgb.size == 0:
                    continue
                row_bgr = cv2.cvtColor(row_rgb, cv2.COLOR_RGB2BGR)
                ocr_lines = ocr_engine.detect_lines(row_bgr)
                values = _normalize_text(ocr_lines)
                title, preview = _extract_title_preview(values)
                unread_badge = _has_unread_badge(
                    row_img_bgr=row_bgr, min_blob_pixels=cfg.unread_badge.min_blob_pixels
                )
                if not unread_badge:
                    unread_badge = any(_UNREAD_NUM_RE.match(v) for v in values[:3])
                has_mention = _contains_mention(values, cfg.mention_keywords, cfg.mention_any_at)
                text = " ".join(values)
                click_x = bx + int(bw * 0.24)
                click_y = by + (bh // 2)
                click_x_ratio = max(0.0, min(1.0, click_x / max(1.0, bounds.width)))
                click_y_ratio = max(0.0, min(1.0, click_y / max(1.0, bounds.height)))
                fp_values = values if values else [f"unread:{int(unread_badge)}"]
                if not title and not preview and not unread_badge:
                    continue
                rows.append(
                    ChatRowState(
                        row_idx=row_idx,
                        text=text,
                        title=title,
                        preview=preview,
                        has_mention=has_mention,
                        has_unread_badge=unread_badge,
                        fingerprint=_build_fingerprint(fp_values),
                        click_x_ratio=click_x_ratio,
                        click_y_ratio=click_y_ratio,
                    )
                )
            return DetectionResult(rows=rows)

    x = int(bounds.width * cfg.list_region.x)
    y = int(bounds.height * cfg.list_region.y)
    w = int(bounds.width * cfg.list_region.w)
    h = int(bounds.height * cfg.list_region.h)

    list_rgb = screenshot_rgb[y : y + h, x : x + w]
    if list_rgb.size == 0:
        return DetectionResult(rows=[])

    list_bgr = cv2.cvtColor(list_rgb, cv2.COLOR_RGB2BGR)
    ocr_lines = ocr_engine.detect_lines(list_bgr)

    # Guardrail: skip obvious non-chat captures (e.g. code/config text) to avoid bad baseline.
    non_chat_like = 0
    for line in ocr_lines:
        txt = (line.text or "").strip()
        if not txt:
            continue
        if "=" in txt or txt.startswith("/Users") or txt.startswith("[vision]"):
            non_chat_like += 1
    if non_chat_like >= 4 and len(ocr_lines) >= 4:
        return DetectionResult(rows=[])

    row_height = max(20, int(h * cfg.row_height_ratio))
    start_y = _estimate_row_start_y(ocr_lines, w, row_height)
    rows: list[ChatRowState] = []

    for row_idx in range(cfg.rows_max):
        row_top = start_y + row_idx * row_height
        row_bottom = min(h, row_top + row_height)
        if row_top >= h:
            break

        bucket = [
            line
            for line in ocr_lines
            if row_top <= line.y_center < row_bottom and 0 <= line.x_center < w
        ]
        values = _normalize_text(bucket)
        title, preview = _extract_title_preview(values)

        if not title and not preview:
            continue

        badge_margin = max(6, int(row_height * 0.12))
        badge_top = max(0, row_top - badge_margin)
        badge_bottom = min(h, row_bottom + badge_margin)
        row_bgr = list_bgr[badge_top:badge_bottom, :]
        unread_badge = _has_unread_badge(
            row_img_bgr=row_bgr, min_blob_pixels=cfg.unread_badge.min_blob_pixels
        )
        # OCR fallback: some badges are read as small standalone numbers.
        if not unread_badge:
            unread_badge = any(_UNREAD_NUM_RE.match(v) for v in values[:2])
        has_mention = _contains_mention(values, cfg.mention_keywords, cfg.mention_any_at)
        text = " ".join(values)

        # Use stable ratio click point inside row to avoid OCR-anchor drift.
        click_x_local = int(w * 0.24)
        click_y_local = (row_top + row_bottom) // 2

        window_click_x = x + click_x_local
        window_click_y = y + click_y_local
        click_x_ratio = max(0.0, min(1.0, window_click_x / max(1.0, bounds.width)))
        click_y_ratio = max(0.0, min(1.0, window_click_y / max(1.0, bounds.height)))

        rows.append(
            ChatRowState(
                row_idx=row_idx,
                text=text,
                title=title,
                preview=preview,
                has_mention=has_mention,
                has_unread_badge=unread_badge,
                fingerprint=_build_fingerprint(values),
                click_x_ratio=click_x_ratio,
                click_y_ratio=click_y_ratio,
            )
        )

    return DetectionResult(rows=rows)
