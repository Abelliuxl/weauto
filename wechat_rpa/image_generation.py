from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .config import ImageGenerationConfig


class ImageGenerationError(RuntimeError):
    pass


class ImageGenerator:
    def __init__(self, cfg: ImageGenerationConfig) -> None:
        self.cfg = cfg

    def status_text(self) -> str:
        if not self.cfg.enabled:
            return "disabled (image_generation.enabled=false)"
        if not str(self.cfg.base_url or "").strip():
            return "blocked (missing base_url)"
        if not str(self.cfg.model or "").strip():
            return "blocked (missing model)"
        if not self.resolve_api_key():
            return f"blocked (missing api key: {self.key_hint()})"
        return (
            "available "
            f"provider={self.cfg.provider} "
            f"model={self.cfg.model} "
            f"default_size={self.cfg.default_size}"
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
        return f"image_generation.api_key or env {env_name}" if env_name else "image_generation.api_key"

    def resolve_api_key(self) -> str:
        if self.cfg.api_key:
            return self.cfg.api_key
        env_name = (self.cfg.api_key_env or "").strip()
        if not env_name:
            return ""
        return os.getenv(env_name, "")

    @staticmethod
    def clean_prompt(raw: object, *, limit: int = 280) -> str:
        return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit]

    def normalize_size(self, raw: object) -> str:
        value = re.sub(
            r"\s+",
            "",
            str(raw or self.cfg.default_size).strip().lower(),
        )
        if not re.fullmatch(r"\d{2,4}x\d{2,4}", value):
            return self.cfg.default_size
        try:
            width_txt, height_txt = value.split("x", 1)
            width = int(width_txt)
            height = int(height_txt)
        except Exception:
            return self.cfg.default_size
        if width < 256 or height < 256 or width > 2048 or height > 2048:
            return self.cfg.default_size
        return f"{width}x{height}"

    @staticmethod
    def _dashscope_size(size: str) -> str:
        clean = re.sub(r"\s+", "", str(size or "")).lower()
        if re.fullmatch(r"\d{2,4}x\d{2,4}", clean):
            width_txt, height_txt = clean.split("x", 1)
            return f"{int(width_txt)}*{int(height_txt)}"
        return "1024*1024"

    @staticmethod
    def _guess_suffix(url_text: str, content_type: str = "") -> str:
        parsed = urllib.parse.urlparse(str(url_text or ""))
        ext = Path(parsed.path).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            return ext
        clean_type = str(content_type or "").lower()
        if "webp" in clean_type:
            return ".webp"
        if "jpeg" in clean_type or "jpg" in clean_type:
            return ".jpg"
        if "gif" in clean_type:
            return ".gif"
        return ".png"

    @staticmethod
    def _next_output_path(output_dir: Path, *, prompt: str, suffix: str) -> Path:
        digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:10]
        now = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        millis = int((now - int(now)) * 1000)
        nonce = hashlib.sha1(f"{time.time_ns()}|{os.getpid()}".encode("utf-8")).hexdigest()[:6]
        base = f"image_{stamp}_{millis:03d}_{digest}_{nonce}"
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

    def _download_image_bytes(self, *, image_url: str, b64_json: str) -> tuple[bytes, str]:
        image_bytes = b""
        suffix = ".png"
        if image_url:
            req = urllib.request.Request(
                url=image_url,
                method="GET",
                headers={"User-Agent": "weauto-image-downloader/1.0"},
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
                if not b64_json:
                    raise ImageGenerationError(f"image download failed: {exc}") from exc

        if (not image_bytes) and b64_json:
            try:
                image_bytes = base64.b64decode(b64_json, validate=True)
            except Exception as exc:
                raise ImageGenerationError(f"image decode failed: {exc}") from exc
            suffix = ".png"
        return image_bytes, suffix

    def _openai_compat(self, *, api_key: str, prompt: str, size: str) -> tuple[bytes, str, int | None]:
        base_url = str(self.cfg.base_url or "").rstrip("/")
        endpoint = base_url if base_url.endswith("/images/generations") else f"{base_url}/images/generations"
        payload = {
            "model": str(self.cfg.model or "").strip(),
            "prompt": prompt,
            "size": size,
            "n": 1,
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
            raise ImageGenerationError(
                f"image generation http error: {exc.code} {self._compact(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ImageGenerationError(f"image generation network error: {exc}") from exc

        try:
            data = json.loads(raw)
        except Exception as exc:
            raise ImageGenerationError("image generation response is not valid json") from exc
        if not isinstance(data, dict):
            raise ImageGenerationError("image generation response is not a json object")

        candidates = data.get("images")
        if not isinstance(candidates, list) or not candidates:
            candidates = data.get("data")
        if not isinstance(candidates, list) or not candidates:
            raise ImageGenerationError("image generation response missing images/data")
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        if not isinstance(first, dict):
            raise ImageGenerationError("image generation response item invalid")

        image_url = str(first.get("url", "")).strip()
        b64_json = str(first.get("b64_json", "")).strip()
        image_bytes, suffix = self._download_image_bytes(image_url=image_url, b64_json=b64_json)
        seed = self._optional_int(data.get("seed"))
        return image_bytes, suffix, seed

    def _dashscope(self, *, api_key: str, prompt: str, size: str) -> tuple[bytes, str, int | None]:
        base_url = str(self.cfg.base_url or "").rstrip("/")
        endpoint = (
            base_url
            if base_url.endswith("/multimodal-generation/generation")
            else f"{base_url}/multimodal-generation/generation"
        )
        payload = {
            "model": str(self.cfg.model or "").strip(),
            "input": {
                "messages": [
                    {"role": "user", "content": [{"text": prompt}]},
                ]
            },
            "parameters": {
                "size": self._dashscope_size(size),
                "prompt_extend": False,
            },
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
            raise ImageGenerationError(
                f"dashscope image generation http error: {exc.code} {self._compact(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ImageGenerationError(f"dashscope image generation network error: {exc}") from exc

        try:
            data = json.loads(raw)
        except Exception as exc:
            raise ImageGenerationError("dashscope image generation response is not valid json") from exc
        if not isinstance(data, dict):
            raise ImageGenerationError("dashscope image generation response is not a json object")

        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        choices = output.get("choices") if isinstance(output.get("choices"), list) else []
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), list) else []
        image_url = ""
        for item in content:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("image", "")).strip()
            if candidate:
                image_url = candidate
                break
        if not image_url:
            raise ImageGenerationError("dashscope image generation response missing image url")

        image_bytes, suffix = self._download_image_bytes(image_url=image_url, b64_json="")
        return image_bytes, suffix, self._optional_int(output.get("seed"))

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _append_history(self, *, output_dir: Path, file_path: Path, prompt: str, size: str, seed: int | None) -> None:
        payload = {
            "ts": int(time.time()),
            "file": str(file_path),
            "name": file_path.name,
            "size_bytes": int(file_path.stat().st_size),
            "provider": str(self.cfg.provider or "").strip(),
            "model": str(self.cfg.model or "").strip(),
            "size": str(size or "").strip(),
            "seed": seed,
            "prompt": str(prompt or "").strip(),
        }
        try:
            with (output_dir / "history.jsonl").open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            return

    def generate_file(self, *, prompt: str, size: str = "") -> Path:
        clean_prompt = self.clean_prompt(prompt, limit=280)
        if not clean_prompt:
            raise ImageGenerationError("image prompt is empty")
        if not self.is_available():
            raise ImageGenerationError(self.status_text())

        api_key = self.resolve_api_key()
        requested_size = self.normalize_size(size)
        provider = str(self.cfg.provider or "openai_compat").strip().lower()
        if provider == "dashscope_z_image":
            image_bytes, suffix, seed = self._dashscope(
                api_key=api_key,
                prompt=clean_prompt,
                size=requested_size,
            )
        else:
            image_bytes, suffix, seed = self._openai_compat(
                api_key=api_key,
                prompt=clean_prompt,
                size=requested_size,
            )

        if not image_bytes:
            raise ImageGenerationError("image generation returned empty image payload")
        output_dir = Path(self.cfg.output_dir or "data/generated_images").expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        out = self._next_output_path(output_dir, prompt=clean_prompt, suffix=suffix)
        out.write_bytes(image_bytes)
        self._append_history(
            output_dir=output_dir,
            file_path=out,
            prompt=clean_prompt,
            size=requested_size,
            seed=seed,
        )
        return out
