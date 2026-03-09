from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import random
import re
from typing import Any

import cv2
import numpy as np

from .config import OcrConfig


@dataclass
class OcrLine:
    text: str
    score: float
    x_center: float
    y_center: float


@dataclass
class _RawOcrHit:
    box: np.ndarray
    text: str
    score: float


def _normalize_backend_name(name: str) -> str:
    raw = (name or "").strip().lower()
    if not raw:
        return ""
    aliases = {
        "rapid": "rapidocr",
        "rapidocr": "rapidocr",
        "rapidocr_onnxruntime": "rapidocr",
        "paddle": "paddleocr",
        "paddleocr": "paddleocr",
        "cn": "cnocr",
        "cnocr": "cnocr",
    }
    return aliases.get(raw, raw)


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_quad_points(raw_box: object) -> np.ndarray | None:
    if raw_box is None:
        return None
    try:
        pts = np.array(raw_box, dtype=np.float32)
    except Exception:
        return None
    if pts.size < 8:
        return None
    if pts.ndim == 1:
        pts = pts.reshape(-1, 2)
    else:
        pts = pts.reshape(-1, 2)
    if pts.shape[0] < 4:
        return None
    if pts.shape[0] > 4:
        pts = pts[:4]
    return pts


def _parse_text_score(primary: object, secondary: object | None = None) -> tuple[str, float]:
    if isinstance(primary, str):
        return primary.strip(), _safe_float(secondary, 0.0)
    if isinstance(primary, (list, tuple)) and primary:
        text = str(primary[0]).strip()
        score = _safe_float(primary[1], _safe_float(secondary, 0.0)) if len(primary) >= 2 else _safe_float(secondary, 0.0)
        return text, score
    if isinstance(primary, dict):
        text = (
            str(
                primary.get("text")
                or primary.get("rec_text")
                or primary.get("label")
                or primary.get("transcription")
                or ""
            ).strip()
        )
        score = _safe_float(
            primary.get("score")
            or primary.get("rec_score")
            or primary.get("confidence")
            or primary.get("prob"),
            _safe_float(secondary, 0.0),
        )
        return text, score
    return "", _safe_float(secondary, 0.0)


def _collect_raw_hits(raw: object, out: list[_RawOcrHit]) -> None:
    if raw is None:
        return

    if isinstance(raw, dict):
        # PaddleOCR v3 style aggregated output:
        # {"dt_polys":[...], "rec_texts":[...], "rec_scores":[...], ...}
        polys = raw.get("dt_polys")
        if polys is None:
            polys = raw.get("rec_polys")
        texts = raw.get("rec_texts")
        scores = raw.get("rec_scores")
        if isinstance(texts, (list, tuple)) and polys is not None:
            try:
                poly_items = list(polys)
            except Exception:
                poly_items = []
            score_items = list(scores) if isinstance(scores, (list, tuple)) else []
            for i, txt in enumerate(texts):
                pts = _to_quad_points(poly_items[i] if i < len(poly_items) else None)
                if pts is None:
                    continue
                text = str(txt or "").strip()
                if not text:
                    continue
                score = _safe_float(score_items[i], 0.0) if i < len(score_items) else 0.0
                out.append(_RawOcrHit(box=pts, text=text, score=score))
            if out:
                return

        box = (
            raw.get("box")
            or raw.get("position")
            or raw.get("points")
            or raw.get("bbox")
            or raw.get("poly")
        )
        text, score = _parse_text_score(raw, None)
        pts = _to_quad_points(box)
        if pts is not None and text:
            out.append(_RawOcrHit(box=pts, text=text, score=score))
            return
        for value in raw.values():
            _collect_raw_hits(value, out)
        return

    if isinstance(raw, (list, tuple)):
        if len(raw) >= 2:
            pts = _to_quad_points(raw[0])
            if pts is not None:
                text, score = _parse_text_score(raw[1], raw[2] if len(raw) >= 3 else None)
                if text:
                    out.append(_RawOcrHit(box=pts, text=text, score=score))
                    return
        for item in raw:
            _collect_raw_hits(item, out)


class _RapidOcrBackend:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def detect_raw(self, image: np.ndarray) -> object:
        result, _ = self._engine(image)
        return result or []

    def detect_raw_with_fallback(self, image: np.ndarray) -> object:
        result = self.detect_raw(image)
        if result:
            return result
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        result, _ = self._engine(cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR))
        if result:
            return result
        result, _ = self._engine(cv2.cvtColor(255 - bw, cv2.COLOR_GRAY2BGR))
        return result or []


class _PaddleOcrBackend:
    name = "paddleocr"

    def __init__(self, *, lang: str, use_angle_cls: bool) -> None:
        from paddleocr import PaddleOCR

        self._use_angle_cls = bool(use_angle_cls)
        base_kwargs = {"lang": lang}
        attempts = [
            {"show_log": False, **base_kwargs, "use_angle_cls": self._use_angle_cls},
            {**base_kwargs, "use_angle_cls": self._use_angle_cls},
            {"show_log": False, **base_kwargs},
            {**base_kwargs},
            {},
        ]
        last_err: Exception | None = None
        for kwargs in attempts:
            try:
                self._engine = PaddleOCR(**kwargs)
                return
            except Exception as exc:  # pragma: no cover - depends on installed paddleocr version
                last_err = exc
        raise RuntimeError(f"paddleocr init failed after fallback attempts: {last_err}")

    def detect_raw(self, image: np.ndarray) -> object:
        try:
            return self._engine.ocr(image, cls=self._use_angle_cls)
        except Exception:
            return self._engine.ocr(image)


class _CnOcrBackend:
    name = "cnocr"

    def __init__(self) -> None:
        from cnocr import CnOcr

        self._engine = CnOcr()

    def detect_raw(self, image: np.ndarray) -> object:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return self._engine.ocr(rgb)


class OcrEngine:
    def __init__(
        self,
        cfg: OcrConfig | None = None,
        *,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = cfg or OcrConfig()
        self._log_fn = log_fn
        self._rng = random.Random()

        backend_raw = os.environ.get("WEAUTO_OCR_BACKEND", self._cfg.backend)
        self._backend_name = _normalize_backend_name(backend_raw)
        if not self._backend_name:
            self._backend_name = "rapidocr"
        self._ab_backend_name = _normalize_backend_name(self._cfg.ab_compare_backend)
        self._ab_sample_rate = max(0.0, min(1.0, float(self._cfg.ab_compare_sample_rate)))
        self._ab_max_text_len = max(24, int(self._cfg.ab_compare_max_text_len))
        self._target_short_side = max(320, int(self._cfg.target_short_side))
        self._max_upscale = max(1.0, float(self._cfg.max_upscale))

        env_enhance = os.environ.get("WEAUTO_OCR_ENHANCE")
        if env_enhance is None:
            self._enhance = bool(self._cfg.enhance)
        else:
            self._enhance = env_enhance.strip().lower() in {"1", "true", "yes", "on"}

        self._primary_backend = self._make_backend(self._backend_name, primary=True)
        self._ab_backend = None
        if self._ab_sample_rate > 0.0 and self._ab_backend_name:
            if self._ab_backend_name == self._backend_name:
                self._log("[warn] ocr-ab disabled: compare backend equals primary backend")
            else:
                try:
                    self._ab_backend = self._make_backend(self._ab_backend_name, primary=False)
                except Exception as exc:
                    self._ab_backend = None
                    self._log(
                        f"[warn] ocr-ab disabled: backend={self._ab_backend_name} init failed ({exc})"
                    )

        ab_desc = self._ab_backend_name if self._ab_backend is not None else "-"
        self._log(
            f"[ocr] backend={self._backend_name} enhance={'1' if self._enhance else '0'} "
            f"ab={ab_desc} sample={self._ab_sample_rate:.2f}"
        )

    def _log(self, text: str) -> None:
        if not self._log_fn:
            return
        try:
            self._log_fn(text)
        except Exception:
            return

    def _make_backend(self, name: str, *, primary: bool):
        normalized = _normalize_backend_name(name)
        if normalized not in {"rapidocr", "paddleocr", "cnocr"}:
            self._log(f"[warn] unsupported OCR backend={name!r}, fallback to rapidocr")
            normalized = "rapidocr"
        try:
            if normalized == "rapidocr":
                return _RapidOcrBackend()
            if normalized == "paddleocr":
                return _PaddleOcrBackend(
                    lang=self._cfg.paddle_lang,
                    use_angle_cls=self._cfg.paddle_use_angle_cls,
                )
            return _CnOcrBackend()
        except Exception as exc:
            if primary and normalized != "rapidocr":
                self._log(
                    f"[warn] OCR backend init failed: {normalized} ({exc}); fallback to rapidocr"
                )
                return _RapidOcrBackend()
            raise RuntimeError(f"OCR backend init failed: {normalized} ({exc})") from exc

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

    def _run_backend_raw(self, backend, image: np.ndarray) -> object:
        if isinstance(backend, _RapidOcrBackend) and self._enhance:
            return backend.detect_raw_with_fallback(image)
        return backend.detect_raw(image)

    def _raw_to_lines(self, raw: object, *, scale: float) -> list[OcrLine]:
        hits: list[_RawOcrHit] = []
        _collect_raw_hits(raw, hits)
        lines: list[OcrLine] = []
        for hit in hits:
            text = str(hit.text or "").strip()
            if not text:
                continue
            pts = hit.box / scale if scale > 1.0 else hit.box
            x_center = float(np.mean(pts[:, 0]))
            y_center = float(np.mean(pts[:, 1]))
            lines.append(
                OcrLine(
                    text=text,
                    score=float(hit.score),
                    x_center=x_center,
                    y_center=y_center,
                )
            )
        return lines

    def _sort_lines_reading_order(self, lines: list[OcrLine], image_h: int) -> list[OcrLine]:
        if len(lines) <= 1:
            return lines
        by_y = sorted(lines, key=lambda line: line.y_center)
        # Merge near y-centers as one row, then sort by x inside each row.
        tol = max(4.0, min(22.0, float(image_h) * 0.06))
        rows: list[dict[str, Any]] = []
        for line in by_y:
            matched = None
            for row in rows:
                if abs(line.y_center - float(row["y"])) <= tol:
                    matched = row
                    break
            if matched is None:
                rows.append({"y": float(line.y_center), "items": [line]})
                continue
            items: list[OcrLine] = matched["items"]
            items.append(line)
            matched["y"] = float(sum(x.y_center for x in items) / max(1, len(items)))

        ordered: list[OcrLine] = []
        for row in rows:
            items = sorted(row["items"], key=lambda line: line.x_center)
            ordered.extend(items)
        return ordered

    def _join_for_compare(self, lines: list[OcrLine]) -> str:
        merged = " | ".join((line.text or "").strip() for line in lines if (line.text or "").strip())
        merged = re.sub(r"\s+", " ", merged).strip()
        if len(merged) > self._ab_max_text_len:
            merged = merged[: self._ab_max_text_len - 3] + "..."
        return merged

    def _norm_for_compare(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "")).lower()

    def _detect_with_backend(
        self, backend, image: np.ndarray, *, scale: float
    ) -> list[OcrLine]:
        raw = self._run_backend_raw(backend, image)
        lines = self._raw_to_lines(raw, scale=scale)
        return self._sort_lines_reading_order(lines, image.shape[0])

    def _maybe_log_ab(
        self, image: np.ndarray, *, scale: float, primary_lines: list[OcrLine]
    ) -> None:
        if self._ab_backend is None:
            return
        if self._ab_sample_rate <= 0.0:
            return
        if self._rng.random() > self._ab_sample_rate:
            return
        try:
            secondary_lines = self._detect_with_backend(self._ab_backend, image, scale=scale)
        except Exception as exc:
            self._log(f"[warn] ocr-ab compare failed: {exc}")
            return
        a = self._join_for_compare(primary_lines)
        b = self._join_for_compare(secondary_lines)
        same = self._norm_for_compare(a) == self._norm_for_compare(b)
        self._log(
            f"[ocr-ab] {self._backend_name} vs {self._ab_backend_name} "
            f"| lines={len(primary_lines)}/{len(secondary_lines)} "
            f"| same={'Y' if same else 'N'} | A={a or '-'} | B={b or '-'}"
        )

    def detect_lines(self, image: np.ndarray) -> list[OcrLine]:
        if self._enhance:
            prepared, scale = self._prepare_image(image)
        else:
            prepared = image
            scale = 1.0
        lines = self._detect_with_backend(self._primary_backend, prepared, scale=scale)
        self._maybe_log_ab(prepared, scale=scale, primary_lines=lines)
        return lines
