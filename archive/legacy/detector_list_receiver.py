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


def _title_quality(title: str) -> int:
    t = (title or "").strip()
    if not t:
        return -99
    score = len(t)
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        score += 3
    if _UNREAD_NUM_RE.match(t):
        score -= 8
    if t in {"群", "群-", "群—"}:
        score -= 7
    if t.endswith(("-", "—", ":", "：", "/", "／")):
        score -= 4
    return score


def _pick_better_title(base_title: str, region_title: str) -> str:
    b = (base_title or "").strip()
    r = (region_title or "").strip()
    if not r:
        return b
    if not b:
        return r
    return r if _title_quality(r) >= _title_quality(b) else b


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


def _extract_text_from_region(
    row_img_bgr: np.ndarray,
    region: "RegionRatio",
    ocr_engine: OcrEngine,
    *,
    title_mode: bool,
) -> str:
    h, w = row_img_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return ""
    rx = max(0.0, min(1.0, region.x))
    ry = max(0.0, min(1.0, region.y))
    rw = max(0.0, min(1.0, region.w))
    rh = max(0.0, min(1.0, region.h))
    x1 = int(w * rx)
    box_w = int(w * rw)
    box_h = int(h * rh)
    y_from_bottom = int(h * ry)
    y2 = h - y_from_bottom
    y1 = y2 - box_h
    x2 = x1 + box_w
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(w, x2)
    y2 = min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return ""
    roi = row_img_bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return ""
    lines = ocr_engine.detect_lines(roi)
    values = [v for v in _normalize_text(lines) if v and not _TIME_RE.match(v)]
    if not values:
        return ""
    if title_mode:
        parts = [x.strip() for x in values if x and x.strip()]
        if not parts:
            return ""
        if _UNREAD_NUM_RE.match(parts[0]) and len(parts) >= 2:
            parts = parts[1:]
        if not parts:
            return ""

        title = parts[0]
        # OCR may split title into tokens like "群-" + "上海"; stitch likely fragments.
        for nxt in parts[1:3]:
            if not nxt:
                continue
            if (
                title.endswith(("-", "—", ":", "：", "/", "／"))
                or title in {"群", "群-", "群—"}
                or len(title) <= 2
            ):
                title = f"{title}{nxt}"
            else:
                break
        return title[:48]
    return " ".join(values).strip()


def _extract_title_from_region(row_img_bgr: np.ndarray, cfg: AppConfig, ocr_engine: OcrEngine) -> str:
    if not cfg.row_title_region_enabled:
        return ""
    return _extract_text_from_region(
        row_img_bgr=row_img_bgr,
        region=cfg.row_title_region,
        ocr_engine=ocr_engine,
        title_mode=True,
    )


def _extract_preview_from_region(row_img_bgr: np.ndarray, cfg: AppConfig, ocr_engine: OcrEngine) -> str:
    if not cfg.preview_region_enabled:
        return ""
    return _extract_text_from_region(
        row_img_bgr=row_img_bgr,
        region=cfg.preview_text_region,
        ocr_engine=ocr_engine,
        title_mode=False,
    )


def _has_unread_number_in_circle(
    row_img_bgr: np.ndarray, cfg: AppConfig, ocr_engine: OcrEngine
) -> bool:
    if not cfg.unread_badge_circle.enabled:
        return False
    _ = ocr_engine
    h, w = row_img_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return False

    cx = int(w * max(0.0, min(1.0, cfg.unread_badge_circle.x)))
    y_from_bottom = int(h * max(0.0, min(1.0, cfg.unread_badge_circle.y)))
    cy = h - y_from_bottom
    cy = max(0, min(h - 1, cy))
    r = int(min(w, h) * max(0.02, min(0.49, cfg.unread_badge_circle.r)))
    r = max(4, r)

    x1 = max(0, cx - r)
    y1 = max(0, cy - r)
    x2 = min(w, cx + r)
    y2 = min(h, cy + r)
    if x2 - x1 < 6 or y2 - y1 < 6:
        return False

    roi = row_img_bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    mh, mw = roi.shape[:2]
    circle_mask = np.zeros((mh, mw), dtype=np.uint8)
    cv2.circle(circle_mask, (cx - x1, cy - y1), r, 255, -1)
    circle_px = int(np.count_nonzero(circle_mask))
    if circle_px <= 0:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red_mask = cv2.inRange(
        hsv, np.array([0, 45, 70], dtype=np.uint8), np.array([20, 255, 255], dtype=np.uint8)
    ) | cv2.inRange(
        hsv, np.array([160, 45, 70], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8)
    )
    kernel = np.ones((2, 2), np.uint8)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
    red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)

    red_px = int(np.count_nonzero((red_mask > 0) & (circle_mask > 0)))
    red_ratio = float(red_px) / float(circle_px)
    return red_ratio >= (1.0 / 3.0)


def detect_chat_rows(
    screenshot_rgb: np.ndarray,
    bounds: WindowBounds,
    cfg: AppConfig,
    ocr_engine: OcrEngine,
) -> DetectionResult:
    img_h, img_w = screenshot_rgb.shape[:2]
    scale_x = float(img_w) / float(max(1, bounds.width))
    scale_y = float(img_h) / float(max(1, bounds.height))
    if scale_x <= 0 or scale_y <= 0:
        scale_x = 1.0
        scale_y = 1.0

    if cfg.use_manual_row_boxes:
        manual_boxes = _load_manual_row_boxes(cfg, bounds)
        if manual_boxes:
            rows: list[ChatRowState] = []
            for row_idx, (bx, by, bw, bh) in enumerate(manual_boxes):
                sx = int(round(bx * scale_x))
                sy = int(round(by * scale_y))
                sw = max(1, int(round(bw * scale_x)))
                sh = max(1, int(round(bh * scale_y)))
                sx = max(0, min(img_w - 1, sx))
                sy = max(0, min(img_h - 1, sy))
                sw = min(sw, img_w - sx)
                sh = min(sh, img_h - sy)
                row_rgb = screenshot_rgb[sy : sy + sh, sx : sx + sw]
                if row_rgb.size == 0:
                    continue
                row_bgr = cv2.cvtColor(row_rgb, cv2.COLOR_RGB2BGR)
                ocr_lines = ocr_engine.detect_lines(row_bgr)
                values = _normalize_text(ocr_lines)
                title, preview = _extract_title_preview(values)
                title_from_region = _extract_title_from_region(row_bgr, cfg, ocr_engine)
                if title_from_region:
                    title = _pick_better_title(title, title_from_region)
                preview_from_region = _extract_preview_from_region(row_bgr, cfg, ocr_engine)
                if preview_from_region:
                    preview = preview_from_region
                unread_badge = _has_unread_number_in_circle(row_bgr, cfg, ocr_engine)
                if (not cfg.unread_badge_circle.enabled) and (not unread_badge):
                    unread_badge = _has_unread_badge(
                        row_img_bgr=row_bgr, min_blob_pixels=cfg.unread_badge.min_blob_pixels
                    )
                    if not unread_badge:
                        unread_badge = any(_UNREAD_NUM_RE.match(v) for v in values[:3])
                mention_values = list(values)
                if preview:
                    mention_values.append(preview)
                has_mention = _contains_mention(
                    mention_values, cfg.mention_keywords, cfg.mention_any_at
                )
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

    x = int(bounds.width * cfg.list_region.x * scale_x)
    y = int(bounds.height * cfg.list_region.y * scale_y)
    w = int(bounds.width * cfg.list_region.w * scale_x)
    h = int(bounds.height * cfg.list_region.h * scale_y)

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

        row_bgr = list_bgr[row_top:row_bottom, :]
        title_from_region = _extract_title_from_region(row_bgr, cfg, ocr_engine)
        if title_from_region:
            title = _pick_better_title(title, title_from_region)
        preview_from_region = _extract_preview_from_region(row_bgr, cfg, ocr_engine)
        if preview_from_region:
            preview = preview_from_region

        unread_badge = _has_unread_number_in_circle(row_bgr, cfg, ocr_engine)
        if (not cfg.unread_badge_circle.enabled) and (not unread_badge):
            badge_margin = max(6, int(row_height * 0.12))
            badge_top = max(0, row_top - badge_margin)
            badge_bottom = min(h, row_bottom + badge_margin)
            row_bgr_ext = list_bgr[badge_top:badge_bottom, :]
            unread_badge = _has_unread_badge(
                row_img_bgr=row_bgr_ext, min_blob_pixels=cfg.unread_badge.min_blob_pixels
            )
            # OCR fallback: some badges are read as small standalone numbers.
            if not unread_badge:
                unread_badge = any(_UNREAD_NUM_RE.match(v) for v in values[:2])
        mention_values = list(values)
        if preview:
            mention_values.append(preview)
        has_mention = _contains_mention(
            mention_values, cfg.mention_keywords, cfg.mention_any_at
        )
        text = " ".join(values)

        # Use stable ratio click point inside row to avoid OCR-anchor drift.
        click_x_local = int(w * 0.24)
        click_y_local = (row_top + row_bottom) // 2

        window_click_x = x + click_x_local
        window_click_y = y + click_y_local
        click_x_ratio = max(
            0.0,
            min(1.0, (window_click_x / max(1.0, scale_x)) / max(1.0, bounds.width)),
        )
        click_y_ratio = max(
            0.0,
            min(1.0, (window_click_y / max(1.0, scale_y)) / max(1.0, bounds.height)),
        )

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
