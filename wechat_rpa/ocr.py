from __future__ import annotations

from dataclasses import dataclass

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

    def detect_lines(self, image: np.ndarray) -> list[OcrLine]:
        result, _ = self._engine(image)
        if not result:
            return []

        lines: list[OcrLine] = []
        for item in result:
            box, txt, score = item
            if not txt:
                continue

            pts = np.array(box, dtype=np.float32)
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
