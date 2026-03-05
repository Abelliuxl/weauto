from __future__ import annotations

from dataclasses import dataclass
import os

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR


@dataclass
class OcrLine:
    text: str
    score: float
    x_center: float
    y_center: float


class OcrEngine:
    def __init__(self) -> None:
        self._engine = RapidOCR()
        # Default: use original image directly. Set WEAUTO_OCR_ENHANCE=1 to enable preprocessing.
        self._enhance = os.environ.get("WEAUTO_OCR_ENHANCE", "0") == "1"
        self._target_short_side = 900
        self._max_upscale = 3.2

    def _prepare_image(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        if image.ndim == 2:
            img = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            img = image

        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img, 1.0

        short_side = float(min(h, w))
        scale = 1.0
        if short_side > 0:
            scale = min(self._max_upscale, self._target_short_side / short_side)
            if scale < 1.0:
                scale = 1.0

        if scale > 1.02:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Mild unsharp mask improves small Chinese/number glyph edges.
        blur = cv2.GaussianBlur(img, (0, 0), 1.0)
        img = cv2.addWeighted(img, 1.35, blur, -0.35, 0)
        return img, scale

    def _run_engine(self, image: np.ndarray):
        result, _ = self._engine(image)
        if result:
            return result

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        result, _ = self._engine(cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR))
        if result:
            return result
        result, _ = self._engine(cv2.cvtColor(255 - bw, cv2.COLOR_GRAY2BGR))
        return result

    def detect_lines(self, image: np.ndarray) -> list[OcrLine]:
        if self._enhance:
            prepared, scale = self._prepare_image(image)
            result = self._run_engine(prepared)
        else:
            prepared = image
            scale = 1.0
            result, _ = self._engine(prepared)
        if not result:
            return []

        lines: list[OcrLine] = []
        for item in result:
            box, txt, score = item
            if not txt:
                continue

            pts = np.array(box, dtype=np.float32)
            if scale > 1.0:
                pts = pts / scale
            x_center = float(np.mean(pts[:, 0]))
            y_center = float(np.mean(pts[:, 1]))
            lines.append(
                OcrLine(
                    text=str(txt).strip(),
                    score=float(score),
                    x_center=x_center,
                    y_center=y_center,
                )
            )

        lines.sort(key=lambda line: line.y_center)
        return lines
