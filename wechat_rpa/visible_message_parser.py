from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
import time
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .ocr import OcrEngine

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


@dataclass
class MessageBlock:
    kind: str
    side: str
    bbox: list[int]


@dataclass
class VisibleMessage:
    side: str
    sender: str
    content_type: str
    text: str
    bbox: list[int]
    fingerprint: str
    mentions: list[str] | None = None
    image_hash: str = ""
    image_path: str = ""


@dataclass
class VisibleChatSnapshot:
    schema: str
    window_id: int
    title: str
    captured_at: float
    source: str
    image_size: dict[str, int]
    messages: list[dict[str, Any]]
    latest_message: dict[str, Any] | None
    debug: dict[str, Any] | None = None


def _normalize_text(text: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    # Common RapidOCR case drift for CLI names in the chat UI.
    return clean.replace("wX-", "wx-").replace("WX-", "wx-")


def _contains(box: list[int], x: float, y: float, *, pad: int = 0) -> bool:
    x1, y1, x2, y2 = box
    return x1 - pad <= x <= x2 + pad and y1 - pad <= y <= y2 + pad


def _merge_boxes(boxes: list[tuple[int, int, int, int]], *, y_tol: int = 18, x_tol: int = 12):
    out: list[tuple[int, int, int, int]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        x, y, bw, bh = box
        merged = False
        for idx, old in enumerate(out):
            ox, oy, ow, oh = old
            if (
                y <= oy + oh + y_tol
                and oy <= y + bh + y_tol
                and x <= ox + ow + x_tol
                and ox <= x + bw + x_tol
            ):
                nx1, ny1 = min(x, ox), min(y, oy)
                nx2, ny2 = max(x + bw, ox + ow), max(y + bh, oy + oh)
                out[idx] = (nx1, ny1, nx2 - nx1, ny2 - ny1)
                merged = True
                break
        if not merged:
            out.append(box)
    return out


class VisibleMessageParser:
    def __init__(self, ocr_engine: OcrEngine) -> None:
        self.ocr_engine = ocr_engine

    def parse(
        self,
        image: Image.Image | np.ndarray,
        *,
        window_id: int = 0,
        title: str = "",
        image_output_dir: Path | None = None,
        include_debug: bool = False,
    ) -> VisibleChatSnapshot:
        img_bgr = self._to_bgr(image)
        height, width = img_bgr.shape[:2]
        body_y1, body_y2 = self._chat_body_bounds(height)

        blocks = self._detect_blocks(img_bgr, body_y1=body_y1, body_y2=body_y2)
        ocr_started = time.perf_counter()
        raw_lines = self.ocr_engine.detect_lines(img_bgr[body_y1:body_y2, :])
        ocr_elapsed = time.perf_counter() - ocr_started
        full_lines = []
        for line in raw_lines:
            text = _normalize_text(line.text)
            if not text:
                continue
            full_lines.append(
                {
                    "text": text,
                    "score": round(float(line.score), 4),
                    "x": round(float(line.x_center), 1),
                    "y": round(float(line.y_center + body_y1), 1),
                }
            )

        image_boxes = [block.bbox for block in blocks if block.kind == "image"]
        messages: list[VisibleMessage] = []
        for block in blocks:
            if block.kind == "image":
                image_hash = self._crop_hash(img_bgr, block.bbox)
                image_path = ""
                if image_output_dir is not None:
                    image_path = str(self._save_image_crop(img_bgr, block.bbox, image_hash, image_output_dir))
                messages.append(
                    VisibleMessage(
                        side=block.side,
                        sender="self" if block.side == "self" else "",
                        content_type="image",
                        text="",
                        bbox=block.bbox,
                        image_hash=image_hash,
                        image_path=image_path,
                        fingerprint=f"{block.side}|image|{image_hash}",
                    )
                )
                continue

            text = self._extract_bubble_text(img_bgr, block.bbox, full_lines, image_boxes)
            if not text or _TIME_RE.match(text):
                continue
            sender = "self"
            if block.side == "other":
                sender = self._find_sender(block.bbox, full_lines, image_boxes, width)
                if sender and text.startswith(sender):
                    stripped = text[len(sender):].strip()
                    if stripped:
                        text = stripped
            mentions = re.findall(r"@[^\s@]+", text)
            messages.append(
                VisibleMessage(
                    side=block.side,
                    sender=sender,
                    content_type="text",
                    text=text,
                    mentions=mentions,
                    bbox=block.bbox,
                    fingerprint=f"{block.side}|text|{sender}|{text}",
                )
            )

        message_dicts = [self._message_to_dict(message) for message in sorted(messages, key=lambda m: m.bbox[1])]
        debug = None
        if include_debug:
            debug = {
                "ocr_elapsed_sec": round(ocr_elapsed, 4),
                "blocks": [asdict(block) for block in blocks],
                "full_ocr_lines": full_lines,
            }
        return VisibleChatSnapshot(
            schema="weauto_visible_messages_v1",
            window_id=int(window_id),
            title=str(title or ""),
            captured_at=round(time.time(), 3),
            source="detached_window_ui_blocks_rapidocr",
            image_size={"width": int(width), "height": int(height)},
            messages=message_dicts,
            latest_message=message_dicts[-1] if message_dicts else None,
            debug=debug,
        )

    @staticmethod
    def _to_bgr(image: Image.Image | np.ndarray) -> np.ndarray:
        if isinstance(image, np.ndarray):
            if image.ndim == 2:
                return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            return image.copy()
        rgb_image = image.convert("RGB")
        try:
            rgb = np.array(rgb_image, dtype=np.uint8, copy=True)
        finally:
            rgb_image.close()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    @staticmethod
    def _chat_body_bounds(height: int) -> tuple[int, int]:
        # Detached WeChat windows in this setup are retina captures. These
        # bounds skip the title/header and the input toolbar.
        return 190, min(height - 210, 1720)

    @staticmethod
    def text_anchors(image: Image.Image | np.ndarray, ocr_engine: OcrEngine, *, limit: int = 18) -> list[dict[str, Any]]:
        img_bgr = VisibleMessageParser._to_bgr(image)
        height = img_bgr.shape[0]
        body_y1, body_y2 = VisibleMessageParser._chat_body_bounds(height)
        body_y1 = max(0, min(height, body_y1))
        body_y2 = max(body_y1, min(height, body_y2))
        anchors: list[dict[str, Any]] = []
        for line in ocr_engine.detect_lines(img_bgr[body_y1:body_y2, :]):
            text = _normalize_text(line.text)
            key = VisibleMessageParser._anchor_text_key(text)
            if not key:
                continue
            anchors.append(
                {
                    "text": text[:120],
                    "key": key,
                    "x": round(float(line.x_center), 1),
                    "y": round(float(line.y_center + body_y1), 1),
                    "score": round(float(line.score), 4),
                }
            )
        anchors.sort(key=lambda item: (float(item["y"]), float(item["x"])))
        return anchors[-max(1, int(limit)) :]

    @staticmethod
    def _anchor_text_key(text: str) -> str:
        clean = _normalize_text(text)
        clean = re.sub(r"\s+", "", clean)
        if not clean or _TIME_RE.match(clean):
            return ""
        if len(clean) < 4:
            return ""
        if not re.search(r"[\w\u4e00-\u9fff]", clean):
            return ""
        return clean[:80]

    @staticmethod
    def chat_body_hash(image: Image.Image | np.ndarray, *, mask_media: bool = False) -> str:
        img_bgr = VisibleMessageParser._to_bgr(image)
        height, width = img_bgr.shape[:2]
        body_y1, body_y2 = VisibleMessageParser._chat_body_bounds(height)
        body_y1 = max(0, min(height, body_y1))
        body_y2 = max(body_y1, min(height, body_y2))
        if mask_media:
            img_bgr = img_bgr.copy()
            blocks = VisibleMessageParser._detect_blocks(img_bgr, body_y1=body_y1, body_y2=body_y2)
            for block in blocks:
                if block.kind != "image":
                    continue
                x1, y1, x2, y2 = [int(v) for v in block.bbox]
                x1 = max(0, min(width, x1))
                x2 = max(0, min(width, x2))
                y1 = max(body_y1, min(body_y2, y1))
                y2 = max(body_y1, min(body_y2, y2))
                if x2 > x1 and y2 > y1:
                    img_bgr[y1:y2, x1:x2] = (235, 235, 235)
        crop = img_bgr[body_y1:body_y2, :]
        small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (96, 160))
        return hashlib.sha1(small.tobytes()).hexdigest()

    @staticmethod
    def _detect_blocks(img_bgr: np.ndarray, *, body_y1: int, body_y2: int) -> list[MessageBlock]:
        height, width = img_bgr.shape[:2]
        b, g, r = cv2.split(img_bgr)
        body = np.zeros((height, width), dtype=np.uint8)
        body[body_y1:body_y2, :] = 1
        green = (
            (g > 190)
            & (r > 100)
            & (r < 185)
            & (b < 170)
            & ((g.astype(int) - r.astype(int)) > 35)
        )
        gray_bubble = (
            (r >= 225)
            & (r <= 245)
            & (g >= 225)
            & (g <= 245)
            & (b >= 225)
            & (b <= 245)
            & (
                (
                    np.maximum.reduce([r, g, b]).astype(int)
                    - np.minimum.reduce([r, g, b]).astype(int)
                )
                <= 16
            )
        )
        non_background = (
            (
                np.abs(r.astype(int) - 247)
                + np.abs(g.astype(int) - 247)
                + np.abs(b.astype(int) - 247)
            )
            > 28
        )
        avatars = VisibleMessageParser._detect_avatar_boxes(non_background & (body > 0), width)

        blocks: list[MessageBlock] = []
        for box in VisibleMessageParser._mask_boxes(non_background & (body > 0)):
            x, y, bw, bh = box
            bbox = [x, y, x + bw, y + bh]
            if x < max(90, int(width * 0.055)):
                continue
            if any(VisibleMessageParser._boxes_overlap(bbox, avatar_box, min_ratio=0.45) for _, avatar_box in avatars):
                continue
            if bw >= 80 and bh >= 80 and bw * bh >= 6000:
                if VisibleMessageParser._bubble_fill_ratio(green | gray_bubble, bbox) >= 0.45:
                    continue
                side = VisibleMessageParser._infer_block_side(bbox, width, avatars)
                blocks.append(MessageBlock(kind="image", side=side, bbox=bbox))

        for kind, mask in (("self_text", green), ("other_text", gray_bubble)):
            boxes = VisibleMessageParser._mask_boxes(mask & (body > 0))
            for x, y, bw, bh in _merge_boxes(boxes):
                if kind == "self_text":
                    if bh >= 35:
                        blocks.append(MessageBlock(kind="text", side="self", bbox=[x, y, x + bw, y + bh]))
                    continue
                if bw > width * 0.85:
                    continue
                if bw <= 120 or bh < 42:
                    continue
                if any(VisibleMessageParser._boxes_overlap([x, y, x + bw, y + bh], image.bbox, min_ratio=0.45) for image in blocks if image.kind == "image"):
                    continue
                side = VisibleMessageParser._infer_block_side([x, y, x + bw, y + bh], width, avatars)
                blocks.append(MessageBlock(kind="text", side=side, bbox=[x, y, x + bw, y + bh]))
        return sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0]))

    @staticmethod
    def _boxes_overlap(a: list[int], b: list[int], *, min_ratio: float = 0.25) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        if ix2 <= ix1 or iy2 <= iy1:
            return False
        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / min(area_a, area_b) >= min_ratio

    @staticmethod
    def _mask_boxes(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
        clean = mask.astype("uint8") * 255
        clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width * height >= 1800:
                boxes.append((x, y, width, height))
        return boxes

    @staticmethod
    def _bubble_fill_ratio(mask: np.ndarray, bbox: list[int]) -> float:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1 = max(0, min(mask.shape[1], x1))
        x2 = max(0, min(mask.shape[1], x2))
        y1 = max(0, min(mask.shape[0], y1))
        y2 = max(0, min(mask.shape[0], y2))
        if x2 <= x1 or y2 <= y1:
            return 0.0
        crop = mask[y1:y2, x1:x2]
        return float(np.count_nonzero(crop)) / float(crop.size)

    @staticmethod
    def _detect_avatar_boxes(mask: np.ndarray, window_width: int) -> list[tuple[str, list[int]]]:
        clean = mask.astype("uint8") * 255
        clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
        contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        avatars: list[tuple[str, list[int]]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width < 44 or height < 44 or width > 100 or height > 100:
                continue
            aspect = width / max(1, height)
            if aspect < 0.75 or aspect > 1.35:
                continue
            if x <= 130:
                avatars.append(("other", [x, y, x + width, y + height]))
            elif x >= window_width - 130:
                avatars.append(("self", [x, y, x + width, y + height]))
        return avatars

    @staticmethod
    def _infer_block_side(
        bbox: list[int],
        window_width: int,
        avatars: list[tuple[str, list[int]]],
    ) -> str:
        x1, y1, x2, y2 = bbox
        best_side = ""
        best_distance = 10_000
        for side, avatar in avatars:
            _, ay1, _, ay2 = avatar
            if ay2 < y1 - 24 or ay1 > y2 + 24:
                continue
            distance = min(abs(ay1 - y1), abs(ay2 - y1))
            if distance < best_distance:
                best_side = side
                best_distance = distance
        if best_side:
            return best_side
        return "self" if (window_width - x2) < x1 else "other"

    def _extract_bubble_text(
        self,
        img_bgr: np.ndarray,
        bbox: list[int],
        full_lines: list[dict[str, Any]],
        image_boxes: list[list[int]],
    ) -> str:
        bubble_lines = [
            line
            for line in full_lines
            if _contains(bbox, float(line["x"]), float(line["y"]), pad=8)
            and not any(_contains(box, float(line["x"]), float(line["y"]), pad=2) for box in image_boxes)
        ]
        bubble_lines.sort(key=lambda line: (float(line["y"]), float(line["x"])))
        text = _normalize_text("\n".join(str(line["text"]) for line in bubble_lines))
        if text:
            return text

        x1, y1, x2, y2 = bbox
        pad = 8
        crop = img_bgr[max(0, y1 - pad) : min(img_bgr.shape[0], y2 + pad), max(0, x1 - pad) : min(img_bgr.shape[1], x2 + pad)]
        local_lines = self.ocr_engine.detect_lines(crop)
        return _normalize_text("\n".join(line.text.strip() for line in local_lines if line.text.strip()))

    @staticmethod
    def _find_sender(
        bbox: list[int],
        full_lines: list[dict[str, Any]],
        image_boxes: list[list[int]],
        window_width: int,
    ) -> str:
        x1, y1, x2, _ = bbox
        candidates = []
        for line in full_lines:
            text = str(line["text"])
            x = float(line["x"])
            y = float(line["y"])
            if _TIME_RE.match(text):
                continue
            if any(_contains(box, x, y, pad=2) for box in image_boxes):
                continue
            if y1 - 55 <= y <= y1 + 15 and 80 <= x <= max(360, x2 + 160, window_width * 0.45):
                candidates.append(line)
        if not candidates:
            return ""
        return str(sorted(candidates, key=lambda line: (abs(float(line["y"]) - y1), abs(float(line["x"]) - x1)))[0]["text"])

    @staticmethod
    def _crop_hash(img_bgr: np.ndarray, bbox: list[int]) -> str:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        crop = img_bgr[max(0, y1) : min(img_bgr.shape[0], y2), max(0, x1) : min(img_bgr.shape[1], x2)]
        return hashlib.sha1(crop.tobytes()).hexdigest()[:16]

    @staticmethod
    def _message_to_dict(message: VisibleMessage) -> dict[str, Any]:
        payload = asdict(message)
        if payload.get("mentions") is None:
            payload.pop("mentions", None)
        if not payload.get("image_hash"):
            payload.pop("image_hash", None)
        if not payload.get("image_path"):
            payload.pop("image_path", None)
        return payload

    @staticmethod
    def _save_image_crop(img_bgr: np.ndarray, bbox: list[int], image_hash: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        x1, y1, x2, y2 = [int(v) for v in bbox]
        crop = img_bgr[max(0, y1) : min(img_bgr.shape[0], y2), max(0, x1) : min(img_bgr.shape[1], x2)]
        path = output_dir / f"{image_hash}.png"
        if crop.size > 0:
            out = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            try:
                out.save(path)
            finally:
                out.close()
        return path
