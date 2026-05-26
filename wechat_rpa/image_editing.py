from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .config import ImageEditingConfig


class ImageEditingError(RuntimeError):
    pass


class ImageEditor:
    def __init__(self, cfg: ImageEditingConfig) -> None:
        self.cfg = cfg

    def status_text(self) -> str:
        if not self.cfg.enabled:
            return "disabled (image_editing.enabled=false)"
        if not str(self.cfg.base_url or "").strip():
            return "blocked (missing base_url)"
        if not str(self.cfg.model or "").strip():
            return "blocked (missing model)"
        if not self.resolve_api_key():
            return f"blocked (missing api key: {self.key_hint()})"
        return (
            "available "
            f"provider={self.cfg.provider} "
            f"model={self.cfg.model}"
        )

    def is_available(self) -> bool:
        return (
            bool(self.cfg.enabled)
            and bool(str(self.cfg.base_url or "").strip())
            and bool(str(self.cfg.model or "").strip())
            and bool(self.resolve_api_key())
        )

    def key_hint(self) -> str:
        env_name = (self.cfg.api_key_env or "").strip()
        return f"image_editing.api_key or env {env_name}" if env_name else "image_editing.api_key"

    def resolve_api_key(self) -> str:
        if self.cfg.api_key:
            return self.cfg.api_key
        env_name = (self.cfg.api_key_env or "").strip()
        if not env_name:
            return ""
        return os.getenv(env_name, "")

    @staticmethod
    def clean_prompt(raw: object, *, limit: int = 800) -> str:
        return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit]

    @staticmethod
    def _dashscope_size(size: str) -> str:
        clean = re.sub(r"\s+", "", str(size or "")).lower()
        if re.fullmatch(r"\d{2,4}[x*]\d{2,4}", clean):
            width_txt, height_txt = re.split(r"[x*]", clean, 1)
            return f"{int(width_txt)}*{int(height_txt)}"
        return ""

    @staticmethod
    def _guess_suffix(url_text: str, content_type: str = "") -> str:
        parsed = urllib.parse.urlparse(str(url_text or ""))
        ext = Path(parsed.path).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
            return ".tif" if ext == ".tiff" else ext
        clean_type = str(content_type or "").lower()
        if "webp" in clean_type:
            return ".webp"
        if "jpeg" in clean_type or "jpg" in clean_type:
            return ".jpg"
        if "gif" in clean_type:
            return ".gif"
        if "bmp" in clean_type:
            return ".bmp"
        if "tiff" in clean_type or "tif" in clean_type:
            return ".tif"
        return ".png"

    @staticmethod
    def _next_output_path(output_dir: Path, *, prompt: str, suffix: str) -> Path:
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:10]
        now = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        millis = int((now - int(now)) * 1000)
        nonce = hashlib.sha1(f"{time.time_ns()}|{os.getpid()}".encode("utf-8")).hexdigest()[:6]
        base = f"edited_{stamp}_{millis:03d}_{digest}_{nonce}"
        out = output_dir / f"{base}{suffix}"
        idx = 1
        while out.exists():
            out = output_dir / f"{base}_{idx}{suffix}"
            idx += 1
        return out

    @staticmethod
    def _compact(text: str, *, limit: int = 260) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        return clean[:limit]

    def _download_image_bytes(self, image_url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(
            url=image_url,
            method="GET",
            headers={"User-Agent": "weauto-wx-cli-image-editor/1.0"},
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=max(5.0, float(self.cfg.download_timeout_sec)),
                context=ssl.create_default_context(),
            ) as resp:
                image_bytes = resp.read()
                content_type = str(resp.headers.get("Content-Type", "")).strip()
                suffix = self._guess_suffix(image_url, content_type)
        except Exception as exc:
            raise ImageEditingError(f"edited image download failed: {exc}") from exc
        return image_bytes, suffix

    def _encode_local_image(self, image_path: str) -> str:
        path = Path(image_path).expanduser()
        if not path.is_file():
            raise ImageEditingError(f"source image file not found: {path}")
        size = path.stat().st_size
        if size <= 0:
            raise ImageEditingError(f"source image is empty: {path}")
        if size > int(self.cfg.max_input_bytes):
            raise ImageEditingError(
                f"source image is too large: {size} bytes > {int(self.cfg.max_input_bytes)} bytes"
            )
        mime, _ = mimetypes.guess_type(str(path))
        if not mime or not mime.startswith("image/"):
            suffix = path.suffix.lower()
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".tif": "image/tiff",
                ".tiff": "image/tiff",
            }.get(suffix, "")
        if not mime:
            raise ImageEditingError(f"source image format is not supported: {path}")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _source_image_value(self, *, image_path: str = "", image_url: str = "") -> tuple[str, str]:
        url = str(image_url or "").strip()
        if url:
            return url, url
        path = str(image_path or "").strip()
        if not path:
            raise ImageEditingError("source image is missing")
        return self._encode_local_image(path), str(Path(path).expanduser())

    def _dashscope_qwen_edit(
        self,
        *,
        api_key: str,
        prompt: str,
        image_path: str = "",
        image_url: str = "",
        size: str = "",
    ) -> tuple[bytes, str, dict[str, Any]]:
        base_url = str(self.cfg.base_url or "").rstrip("/")
        endpoint = (
            base_url
            if base_url.endswith("/multimodal-generation/generation")
            else f"{base_url}/multimodal-generation/generation"
        )
        image_value, source_ref = self._source_image_value(image_path=image_path, image_url=image_url)
        parameters: dict[str, Any] = {
            "n": 1,
            "watermark": bool(self.cfg.watermark),
            "prompt_extend": bool(self.cfg.prompt_extend),
        }
        dashscope_size = self._dashscope_size(size or self.cfg.default_size)
        if dashscope_size:
            parameters["size"] = dashscope_size
        payload = {
            "model": str(self.cfg.model or "").strip(),
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": image_value},
                            {"text": prompt},
                        ],
                    },
                ]
            },
            "parameters": parameters,
        }
        req = urllib.request.Request(
            url=endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=max(5.0, float(self.cfg.timeout_sec)),
                context=ssl.create_default_context(),
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ImageEditingError(
                f"dashscope image edit http error: {exc.code} {self._compact(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ImageEditingError(f"dashscope image edit network error: {exc}") from exc

        try:
            data = json.loads(raw)
        except Exception as exc:
            raise ImageEditingError("dashscope image edit response is not valid json") from exc
        if not isinstance(data, dict):
            raise ImageEditingError("dashscope image edit response is not a json object")
        if data.get("code") or (data.get("message") and not data.get("output")):
            raise ImageEditingError(
                f"dashscope image edit error: {self._compact(str(data.get('code') or ''))} "
                f"{self._compact(str(data.get('message') or ''))}"
            )

        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        choices = output.get("choices") if isinstance(output.get("choices"), list) else []
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        result_url = ""
        for item in content:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("image") or item.get("url") or item.get("image_url") or "").strip()
            if candidate:
                result_url = candidate
                break
        if not result_url:
            raise ImageEditingError("dashscope image edit response missing image url")

        image_bytes, suffix = self._download_image_bytes(result_url)
        meta = {
            "source": source_ref,
            "result_url": result_url,
            "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            "request_id": data.get("request_id", ""),
        }
        return image_bytes, suffix, meta

    def _append_history(
        self,
        *,
        output_dir: Path,
        file_path: Path,
        prompt: str,
        source: str,
        size: str,
        meta: dict[str, Any],
    ) -> None:
        payload = {
            "ts": int(time.time()),
            "file": str(file_path),
            "name": file_path.name,
            "size_bytes": int(file_path.stat().st_size),
            "provider": str(self.cfg.provider or "").strip(),
            "model": str(self.cfg.model or "").strip(),
            "source": source,
            "size": str(size or "").strip(),
            "prompt": str(prompt or "").strip(),
            "request_id": str(meta.get("request_id", "") or ""),
            "usage": meta.get("usage") if isinstance(meta.get("usage"), dict) else {},
            "result_url": str(meta.get("result_url", "") or ""),
        }
        try:
            with (output_dir / "history.jsonl").open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            return

    def edit_file(
        self,
        *,
        prompt: str,
        image_path: str = "",
        image_url: str = "",
        size: str = "",
    ) -> Path:
        clean_prompt = self.clean_prompt(prompt)
        if not clean_prompt:
            raise ImageEditingError("image edit prompt is empty")
        if not self.is_available():
            raise ImageEditingError(self.status_text())
        if not str(image_path or image_url or "").strip():
            raise ImageEditingError("source image is missing")

        api_key = self.resolve_api_key()
        image_bytes, suffix, meta = self._dashscope_qwen_edit(
            api_key=api_key,
            prompt=clean_prompt,
            image_path=image_path,
            image_url=image_url,
            size=size,
        )
        if not image_bytes:
            raise ImageEditingError("image edit returned empty image payload")
        output_dir = Path(self.cfg.output_dir or "data/edited_images").expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        out = self._next_output_path(output_dir, prompt=clean_prompt, suffix=suffix)
        out.write_bytes(image_bytes)
        self._append_history(
            output_dir=output_dir,
            file_path=out,
            prompt=clean_prompt,
            source=str(meta.get("source") or image_url or image_path),
            size=self._dashscope_size(size or self.cfg.default_size),
            meta=meta,
        )
        return out
