from __future__ import annotations

import builtins
import json
import os
import ssl
import base64
import io
import re
import sys
import time
import urllib.error
import urllib.request

import certifi

from PIL import Image

from .config import LlmConfig, VisionConfig

_BUILTIN_PRINT = builtins.print
_COLOR_ENABLED = bool(
    os.getenv("FORCE_COLOR", "").strip()
    or (
        sys.stdout.isatty()
        and (not os.getenv("NO_COLOR", "").strip())
        and os.getenv("TERM", "").lower() != "dumb"
    )
)
_COLOR_RESET = "\033[0m"
_LOG_COLOR_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\[timing\]"), "\033[94m"),
    (re.compile(r"^\[error\]"), "\033[91m"),
    (re.compile(r"^\[fatal\]"), "\033[91m"),
    (re.compile(r"^Traceback \(most recent call last\):"), "\033[91m"),
    (re.compile(r"^[A-Za-z_][A-Za-z0-9_]*Error:"), "\033[91m"),
    (re.compile(r"^KeyboardInterrupt$"), "\033[91m"),
    (re.compile(r"^\[warn\]"), "\033[33m"),
]


def _colorize_log_line(text: str) -> str:
    if (not _COLOR_ENABLED) or (not text):
        return text
    clean = str(text)
    for pattern, color in _LOG_COLOR_RULES:
        if pattern.match(clean):
            return f"{color}{clean}{_COLOR_RESET}"
    return clean


def print(*args, **kwargs):  # type: ignore[override]
    if not args:
        return _BUILTIN_PRINT(*args, **kwargs)
    sep = kwargs.get("sep", " ")
    merged = sep.join(str(x) for x in args)
    file_obj = kwargs.get("file", sys.stdout)
    if file_obj in (None, sys.stdout, sys.stderr):
        merged = _colorize_log_line(merged)
    out_kwargs = dict(kwargs)
    out_kwargs["sep"] = ""
    return _BUILTIN_PRINT(merged, **out_kwargs)


def prepare_terminal_for_log_line() -> None:
    """Compatibility hook used by bot log printing.

    Older builds used a tty spinner in this module and required clearing the
    current terminal line before normal log output. Spinner logic is absent in
    the current file, so this is intentionally a no-op.
    """
    return None


class LlmReplyGenerator:
    def __init__(
        self,
        cfg: LlmConfig,
        vision_cfg: VisionConfig | None = None,
    ) -> None:
        self.cfg = cfg
        self.vision_cfg = vision_cfg

    def is_enabled(self) -> bool:
        if not self.cfg.enabled:
            return False
        if not (self.cfg.base_url or "").strip():
            return False
        if not (self.cfg.model or "").strip():
            return False
        return True

    def is_vision_enabled(self) -> bool:
        if not self.vision_cfg or not self.vision_cfg.enabled:
            return False
        if not (self.vision_cfg.base_url or "").strip():
            return False
        if not (self.vision_cfg.model or "").strip():
            return False
        return True

    def is_reply_enabled(self) -> bool:
        return self.is_enabled()

    def reply_backend_name(self) -> str:
        if self.is_enabled():
            return "llm"
        return "template"

    def _resolve_api_key(self) -> str:
        if self.cfg.api_key:
            return self.cfg.api_key
        return os.getenv(self.cfg.api_key_env, "")

    def _resolve_vision_api_key(self) -> str:
        if not self.vision_cfg:
            return ""
        if self.vision_cfg.api_key:
            return self.vision_cfg.api_key
        return os.getenv(self.vision_cfg.api_key_env, "")

    def _is_debug_payload_enabled(self, label: str) -> bool:
        if label == "vision":
            return bool(self.vision_cfg and self.vision_cfg.debug_log_payload)
        return bool(self.cfg.debug_log_payload)

    def _is_debug_response_enabled(self, label: str) -> bool:
        if label == "vision":
            return bool(self.vision_cfg and self.vision_cfg.debug_log_response)
        return bool(self.cfg.debug_log_response)

    @staticmethod
    def _sanitize_payload_for_log(obj):
        if isinstance(obj, dict):
            return {str(k): LlmReplyGenerator._sanitize_payload_for_log(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [LlmReplyGenerator._sanitize_payload_for_log(x) for x in obj]
        if isinstance(obj, str):
            text = obj
            if text.startswith("data:image/") and "base64," in text:
                prefix = text.split(",", 1)[0]
                return f"{prefix},<base64:{len(text)} chars>"
            return text
        return obj

    @staticmethod
    def _preview_text(text: str, limit: int = 120000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n...(truncated {len(text) - limit} chars)"

    @staticmethod
    def _append_debug_blob(*, label: str, kind: str, text: str) -> None:
        path = str(os.getenv("WEAUTO_LOG_FILE", "")).strip()
        clean = str(text or "").strip()
        if (not path) or (not clean):
            return
        try:
            with open(path, "a", encoding="utf-8") as fp:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                fp.write(f"\n[debug-{label}] {kind} @ {stamp}\n")
                fp.write(clean)
                fp.write("\n")
        except Exception:
            return

    @staticmethod
    def _summarize_http_error_detail(detail: str, limit: int = 260) -> str:
        raw = str(detail or "").strip()
        if not raw:
            return "(empty error body)"
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict):
                    code = str(err.get("code", "")).strip()
                    msg = str(err.get("message", "")).strip()
                    typ = str(err.get("type", "")).strip()
                    pieces = [x for x in (code, typ, msg) if x]
                    compact = " | ".join(pieces) if pieces else raw
                else:
                    compact = raw
            else:
                compact = raw
        except Exception:
            compact = raw
        one_line = re.sub(r"\s+", " ", compact).strip()
        if len(one_line) <= limit:
            return one_line
        return one_line[:limit] + "..."

    @staticmethod
    def _effective_text_max_tokens(raw: object | None) -> int | None:
        try:
            value = int(raw) if raw is not None else 0
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    @staticmethod
    def _drop_response_format(payload: dict) -> dict:
        patched = dict(payload)
        patched.pop("response_format", None)
        return patched

    @staticmethod
    def _drop_token_limits(payload: dict) -> dict:
        patched = dict(payload)
        patched.pop("max_tokens", None)
        options = patched.get("options")
        if isinstance(options, dict):
            opt = dict(options)
            opt.pop("num_predict", None)
            patched["options"] = opt
        return patched

    @staticmethod
    def _is_response_format_unsupported(exc: Exception) -> bool:
        text = str(exc or "").lower()
        if "response_format" not in text:
            return False
        return any(
            token in text
            for token in (
                "json_object",
                "unsupported",
                "not supported",
                "not valid",
                "invalidparameter",
                "invalid parameter",
            )
        )

    def _log_transport_debug(
        self,
        *,
        label: str,
        native: bool,
        think_raw: str,
        compat_think_mode: str,
        controlled: bool,
    ) -> None:
        if not self._is_debug_payload_enabled(label):
            return
        if label == "vision":
            base = (self.vision_cfg.base_url if self.vision_cfg else "") or ""
        else:
            base = self.cfg.base_url or ""
        provider = "openrouter" if "openrouter.ai" in base.lower() else "other"
        think_value = self._ollama_think_value(think_raw)
        normalized_mode = (compat_think_mode or "default").strip().lower()
        if normalized_mode not in ("default", "on", "off"):
            normalized_mode = "default"
        think_effective = bool(native and (think_value is not None)) or (
            (not native) and normalized_mode in ("on", "off")
        )
        print(
            f"[debug-{label}] transport={'ollama_native' if native else 'openai_compat'} "
            f"think_raw={think_raw!r} compat_think_mode={normalized_mode!r} "
            f"think_effective={think_effective} "
            f"reasoning_controls_applied={controlled} provider={provider}"
        )

    def _extract_json_payload(self, raw: str):
        original = (raw or "").strip()
        if not original:
            raise RuntimeError("empty json text")
        text = self._strip_provider_think_content(original)
        if not text:
            short = original[:220].replace("\n", " ")
            raise RuntimeError(f"json parse failed; think-only/raw={short!r}")

        # Remove markdown fences if present.
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

        candidates: list[str] = [text]
        for ch in ("{", "["):
            idx = text.find(ch)
            if idx >= 0:
                candidates.append(text[idx:].strip())

        if "{" in text and "}" in text:
            candidates.append(text[text.find("{") : text.rfind("}") + 1].strip())
        if "[" in text and "]" in text:
            candidates.append(text[text.find("[") : text.rfind("]") + 1].strip())

        # Try parse with a few lightweight repair passes.
        for cand in candidates:
            for attempt in self._json_repair_attempts(cand):
                try:
                    data = json.loads(attempt)
                    if isinstance(data, (dict, list)):
                        return data
                except Exception:
                    continue

        # Recover partial items from truncated array output.
        for cand in candidates:
            recovered = self._recover_truncated_json_array(cand)
            if recovered:
                return recovered

        short = text[:220].replace("\n", " ")
        raise RuntimeError(f"json parse failed; raw={short!r}")

    @staticmethod
    def _strip_provider_think_content(raw: str) -> str:
        text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return ""
        # LongCat / generic think tag wrappers.
        cleaned = re.sub(
            r"<(?:longcat_)?think>\s*[\s\S]*?\s*</(?:longcat_)?think>",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        lowered = cleaned.lower()
        for marker in ("<longcat_think>", "<think>"):
            idx = lowered.find(marker)
            if idx >= 0:
                cleaned = cleaned[:idx].strip()
                break
        return cleaned

    def _recover_truncated_json_array(self, raw: str) -> list:
        text = (raw or "").strip()
        if not text:
            return []
        idx = text.find("[")
        if idx < 0:
            return []
        text = text[idx:]
        decoder = json.JSONDecoder()
        out: list = []
        pos = 1
        n = len(text)
        while pos < n:
            while pos < n and text[pos] in " \r\n\t,":
                pos += 1
            if pos >= n:
                break
            if text[pos] == "]":
                return out
            try:
                item, end_pos = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                # Truncated tail item; keep already parsed items.
                break
            out.append(item)
            pos = end_pos
        return out

    def _convert_vision_list_payload(self, data: list) -> dict:
        recent: list[dict] = []
        for item in data:
            if isinstance(item, dict):
                text = str(item.get("content", item.get("text", ""))).strip()
                raw_sender = str(item.get("sender", item.get("role", ""))).strip().lower()
                raw_type = str(item.get("type", item.get("content_type", ""))).strip().lower()
            else:
                text = str(item).strip()
                raw_sender = ""
                raw_type = ""

            if not text:
                continue

            if raw_sender in ("assistant", "self", "bot", "me", "a"):
                role = "assistant"
            elif raw_sender in ("user", "other", "u", "human"):
                role = "user"
            else:
                role = "unknown"

            if raw_type in ("text", "image", "mixed"):
                content_type = raw_type
            else:
                content_type = "unknown"

            recent.append(
                {
                    "role": role,
                    "content_type": content_type,
                    "text": text[:220],
                    "sender": str(item.get("sender", "")).strip()[:40] if isinstance(item, dict) else "",
                    "is_mention_me": False,
                }
            )
        recent = recent[-10:]
        if recent:
            last = recent[-1]
        else:
            last = {
                "role": "unknown",
                "content_type": "unknown",
                "text": "",
                "sender": "",
                "is_mention_me": False,
            }
        return {
            "schema": "wechat_vision_v1",
            "last_message": last,
            "recent_messages": recent,
            "confidence": 0.0,
        }

    def _json_repair_attempts(self, s: str) -> list[str]:
        base = (s or "").strip()
        if not base:
            return []
        attempts = [base]

        # 1) Remove trailing commas before object/array close.
        t1 = re.sub(r",\s*([}\]])", r"\1", base)
        attempts.append(t1)

        # 2) Insert comma between end quote and next key quote on newline.
        # Example: "foo"\n"bar": 1  -> "foo",\n"bar": 1
        t2 = re.sub(r'("\s*)\n(\s*")', r"\1,\n\2", t1)
        attempts.append(t2)

        # 3) Normalize fullwidth punctuation that occasionally appears in JSON-like text.
        t3 = (
            t2.replace("，", ",")
            .replace("：", ":")
            .replace("“", '"')
            .replace("”", '"')
        )
        attempts.append(t3)

        # De-duplicate while preserving order.
        out: list[str] = []
        seen: set[str] = set()
        for x in attempts:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _apply_reasoning_controls(
        self,
        payload: dict,
        base_url: str,
        exclude: bool,
        effort: str,
        think_mode: str,
    ) -> tuple[dict, bool]:
        # OpenAI-compatible provider-specific reasoning/think controls.
        # We inject a conservative superset and rely on fallback retry if provider rejects fields.
        provider = (base_url or "").lower()
        # LongCat returns HTTP 200 with empty body when these controls are present.
        # Keep payload raw for LongCat endpoints.
        if "longcat.chat" in provider:
            return dict(payload), False
        normalized_effort = (effort or "").strip().lower()
        normalized_mode = (think_mode or "default").strip().lower()
        if normalized_mode not in ("default", "on", "off"):
            normalized_mode = "default"

        updated = dict(payload)
        controlled = False

        reasoning_obj: dict[str, object] = {}
        if normalized_effort in ("minimal", "low", "medium", "high"):
            # Chat API-style effort control (e.g. some Doubao/OpenAI-compatible endpoints).
            updated["reasoning_effort"] = normalized_effort
            # Keep nested compatibility for providers that still parse reasoning.effort.
            reasoning_obj["effort"] = normalized_effort
            controlled = True

        # Explicit think mode for OpenAI-compatible requests.
        if normalized_mode == "on":
            updated["think"] = True
            updated["include_reasoning"] = True
            reasoning_obj["exclude"] = False
            controlled = True
        elif normalized_mode == "off":
            updated["think"] = False
            updated["include_reasoning"] = False
            reasoning_obj["exclude"] = True
            controlled = True
        else:
            # default: preserve provider/model default behavior.
            # Keep legacy exclude behavior only for OpenRouter.
            if exclude and ("openrouter.ai" in provider):
                updated["include_reasoning"] = False
                reasoning_obj["exclude"] = True
                controlled = True

        if reasoning_obj and controlled:
            updated["reasoning"] = reasoning_obj
            controlled = True

        return updated, controlled

    @staticmethod
    def _should_retry_without_controls(exc: Exception) -> bool:
        text = str(exc or "").lower()
        if "http error" not in text:
            return False
        if any(code in text for code in (" 400 ", " 401 ", " 403 ", " 404 ", " 405 ", " 409 ", " 415 ", " 422 ")):
            return True
        return any(
            token in text
            for token in (
                "bad request",
                "invalid",
                "unsupported",
                "unknown",
                "unrecognized",
                "not allowed",
            )
        )

    def _is_native_ollama_llm(self) -> bool:
        return bool(self.cfg.ollama_native)

    def _is_native_ollama_vision(self) -> bool:
        return bool(self.vision_cfg and self.vision_cfg.ollama_native)

    def _ollama_chat_url(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        for suffix in ("/chat/completions", "/v1", "/api/chat", "/api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return base.rstrip("/") + "/api/chat"

    def _data_url_to_base64(self, url: str) -> str:
        text = (url or "").strip()
        if text.startswith("data:") and "," in text:
            return text.split(",", 1)[1].strip()
        return text

    def _ollama_think_value(self, raw: str) -> object | None:
        value = (raw or "").strip().lower()
        if not value or value in ("auto", "default"):
            return None
        if value == "true":
            return True
        if value == "false":
            return False
        return value

    def _openai_to_ollama_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user")).strip() or "user"
            content = message.get("content", "")
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue

            if not isinstance(content, list):
                out.append({"role": role, "content": str(content or "")})
                continue

            text_parts: list[str] = []
            images: list[str] = []
            for part in content:
                if isinstance(part, str):
                    if part.strip():
                        text_parts.append(part.strip())
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).strip().lower()
                if part_type == "text":
                    text = str(part.get("text", "")).strip()
                    if text:
                        text_parts.append(text)
                elif part_type == "image_url":
                    image_url = part.get("image_url") or {}
                    if isinstance(image_url, dict):
                        url = str(image_url.get("url", "")).strip()
                    else:
                        url = str(image_url).strip()
                    if url:
                        images.append(self._data_url_to_base64(url))

            item = {"role": role, "content": "\n".join(text_parts).strip()}
            if images:
                item["images"] = images
            out.append(item)
        return out

    def _ollama_payload_from_chat(
        self,
        *,
        payload: dict,
        model: str,
        max_tokens: object | None,
        temperature: float,
        think: str,
    ) -> dict:
        options: dict[str, object] = {
            "temperature": max(0.0, float(temperature)),
        }
        normalized_max_tokens = self._effective_text_max_tokens(max_tokens)
        if normalized_max_tokens is not None:
            options["num_predict"] = normalized_max_tokens
        native_payload: dict[str, object] = {
            "model": model,
            "messages": self._openai_to_ollama_messages(payload.get("messages") or []),
            "stream": False,
            "options": options,
        }
        think_value = self._ollama_think_value(think)
        if think_value is not None:
            native_payload["think"] = think_value

        response_format = payload.get("response_format") or {}
        if response_format == {"type": "json_object"}:
            native_payload["format"] = "json"
        elif isinstance(response_format, dict) and response_format.get("json_schema"):
            native_payload["format"] = response_format.get("json_schema")
        elif payload.get("format") in ("json",):
            native_payload["format"] = payload.get("format")
        return native_payload

    def _request_completion(
        self,
        *,
        url: str,
        api_key: str,
        timeout_sec: float,
        payload: dict,
        label: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        last_error: RuntimeError | None = None
        for attempt in (1, 2):
            resp_text = self._request_text(
                url=url,
                api_key=api_key,
                timeout_sec=timeout_sec,
                payload=payload,
                label=label,
                extra_headers=extra_headers,
            )
            clean = str(resp_text or "").strip()
            if not clean:
                last_error = RuntimeError(
                    f"{label} empty response body (attempt {attempt}/2)"
                )
            else:
                try:
                    data = json.loads(clean)
                    if isinstance(data, dict):
                        return data
                    last_error = RuntimeError(
                        f"{label} response is not json object: {type(data).__name__}"
                    )
                except json.JSONDecodeError as exc:
                    preview = self._preview_text(clean, limit=320).replace("\n", "\\n")
                    last_error = RuntimeError(
                        f"{label} invalid json response (attempt {attempt}/2): {exc}; "
                        f"body={preview!r}"
                    )
            if attempt == 1:
                print(f"[warn] {label} response parse failed, retrying once: {last_error}")
                time.sleep(0.2)
        raise last_error or RuntimeError(f"{label} invalid response")

    def _request_text(
        self,
        *,
        url: str,
        api_key: str,
        timeout_sec: float,
        payload: dict,
        label: str,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        request_payload = self._drop_token_limits(payload)
        if self._is_debug_payload_enabled(label):
            safe_payload = self._sanitize_payload_for_log(request_payload)
            preview = self._preview_text(
                json.dumps(safe_payload, ensure_ascii=False, indent=2),
                limit=120000,
            )
            print(
                f"[debug-{label}] request url={url} timeout={float(timeout_sec):.1f}s "
                f"payload_chars={len(preview)}"
            )
            self._append_debug_blob(label=label, kind="request_payload", text=preview)
        body = json.dumps(request_payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers=headers,
        )
        started = time.monotonic()
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ssl_ctx) as resp:
                resp_text = resp.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - started
            print(f"[timing] {label} elapsed={elapsed:.2f}s timeout={float(timeout_sec):.1f}s")
            if self._is_debug_response_enabled(label):
                preview = self._preview_text(resp_text, limit=120000)
                print(
                    f"[debug-{label}] response_chars={len(resp_text)} "
                    f"preview_chars={len(preview)}"
                )
                self._append_debug_blob(label=label, kind="response_body", text=preview)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if self._is_debug_response_enabled(label):
                self._append_debug_blob(label=label, kind="error_body", text=detail)
            compact_detail = self._summarize_http_error_detail(detail)
            elapsed = time.monotonic() - started
            raise RuntimeError(
                f"{label} http error after {elapsed:.2f}s (timeout={float(timeout_sec):.1f}s): "
                f"{exc.code} {compact_detail}"
            )
        except urllib.error.URLError as exc:
            elapsed = time.monotonic() - started
            raise RuntimeError(
                f"{label} network error after {elapsed:.2f}s "
                f"(timeout={float(timeout_sec):.1f}s): {exc}"
            )
        return resp_text

    def _extract_content_from_completion(self, data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {}) if isinstance(first, dict) else {}

        content = message.get("content")
        if isinstance(content, str):
            clean = self._strip_provider_think_content(content)
            if clean:
                return clean.strip()
        if isinstance(content, list):
            chunks: list[str] = []
            for part in content:
                if isinstance(part, str):
                    if part.strip():
                        chunks.append(part.strip())
                    continue
                if not isinstance(part, dict):
                    continue
                if "text" in part and str(part.get("text", "")).strip():
                    chunks.append(str(part.get("text", "")).strip())
                elif part.get("type") == "output_text" and str(part.get("text", "")).strip():
                    chunks.append(str(part.get("text", "")).strip())
            if chunks:
                merged = self._strip_provider_think_content("\n".join(chunks))
                if merged:
                    return merged.strip()

        # Provider compatibility fallback.
        alt_text = first.get("text", "")
        if isinstance(alt_text, str) and alt_text.strip():
            clean = self._strip_provider_think_content(alt_text)
            if clean:
                return clean.strip()
        return ""

    def _extract_content_from_ollama_chat(self, data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        message = data.get("message") or {}
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return ""

    def _post_chat(self, payload: dict) -> str:
        api_key = self._resolve_api_key()
        native = self._is_native_ollama_llm()
        if native:
            self._log_transport_debug(
                label="llm",
                native=True,
                think_raw=self.cfg.ollama_think,
                compat_think_mode=self.cfg.openai_compat_think_mode,
                controlled=False,
            )
            url = self._ollama_chat_url(self.cfg.base_url)
            native_payload = self._ollama_payload_from_chat(
                payload=payload,
                model=self.cfg.model,
                max_tokens=self.cfg.max_tokens,
                temperature=self.cfg.temperature,
                think=self.cfg.ollama_think,
            )
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.cfg.timeout_sec,
                payload=native_payload,
                label="llm",
            )
            content = self._extract_content_from_ollama_chat(data)
            if content:
                return content
            raise RuntimeError("llm returned empty content")

        url = f"{self.cfg.base_url}/chat/completions"
        controlled_payload, controlled = self._apply_reasoning_controls(
            payload,
            self.cfg.base_url,
            exclude=self.cfg.reasoning_exclude,
            effort=self.cfg.reasoning_effort,
            think_mode=self.cfg.openai_compat_think_mode,
        )
        self._log_transport_debug(
            label="llm",
            native=False,
            think_raw=self.cfg.ollama_think,
            compat_think_mode=self.cfg.openai_compat_think_mode,
            controlled=controlled,
        )
        try:
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.cfg.timeout_sec,
                payload=controlled_payload,
                label="llm",
            )
        except Exception as exc:
            fallback_payload = payload
            if self._is_response_format_unsupported(exc):
                print(
                    "[warn] llm response_format unsupported, retrying without response_format"
                )
                fallback_payload = self._drop_response_format(payload)
                try:
                    data = self._request_completion(
                        url=url,
                        api_key=api_key,
                        timeout_sec=self.cfg.timeout_sec,
                        payload=self._drop_response_format(controlled_payload),
                        label="llm",
                    )
                except Exception as exc2:
                    exc = exc2
                else:
                    exc = None
            if exc is None:
                pass
            elif controlled and self._should_retry_without_controls(exc):
                print(f"[warn] llm controlled payload rejected, retrying raw payload: {exc}")
                data = self._request_completion(
                    url=url,
                    api_key=api_key,
                    timeout_sec=self.cfg.timeout_sec,
                    payload=fallback_payload,
                    label="llm",
                )
            else:
                raise
        content = self._extract_content_from_completion(data)
        if content:
            return content

        if controlled:
            # Some providers/models return null content when reasoning is excluded.
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.cfg.timeout_sec,
                payload=payload,
                label="llm",
            )
            content = self._extract_content_from_completion(data)
            if content:
                print("[warn] llm content empty with reasoning-off, retried without controls")
                return content

        raise RuntimeError("llm returned empty content")

    def _image_to_data_url(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    @staticmethod
    def _norm_role(v: str) -> str:
        x = (v or "").strip().lower()
        if x in ("assistant", "self", "bot", "a", "me"):
            return "assistant"
        if x in ("user", "other", "u", "human"):
            return "user"
        return "unknown"

    @staticmethod
    def _norm_type(v: str) -> str:
        x = (v or "").strip().lower()
        if x in ("text", "image", "mixed"):
            return x
        return "unknown"

    def _normalize_structured_records(self, raw_records: list, *, limit: int) -> list[dict]:
        normalized: list[dict] = []
        for item in raw_records or []:
            if not isinstance(item, dict):
                s = str(item).strip()
                if not s:
                    continue
                role = "assistant" if s.startswith("A:") else ("user" if s.startswith("U:") else "unknown")
                text = s.split(":", 1)[1].strip() if ":" in s else s
                normalized.append(
                    {
                        "role": role,
                        "content_type": "unknown",
                        "text": text[:220],
                        "sender": "",
                        "is_mention_me": False,
                    }
                )
                continue

            content_type = self._norm_type(str(item.get("content_type", "")))
            text = str(item.get("text", "")).strip()
            if content_type == "image" and not text:
                text = "[图片]"
            normalized.append(
                {
                    "role": self._norm_role(str(item.get("role", ""))),
                    "content_type": content_type,
                    "text": text[:220],
                    "sender": str(item.get("sender", "")).strip()[:40],
                    "is_mention_me": bool(item.get("is_mention_me", False)),
                }
            )
        return normalized[-max(1, limit) :]

    def _normalize_memory_people(self, raw_people: list | None, *, limit: int) -> list[dict]:
        out: list[dict] = []
        for item in raw_people or []:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()[:24]
                alias = str(item.get("alias", "")).strip()[:24]
                description = str(
                    item.get("description", "")
                    or item.get("identity", "")
                    or item.get("note", "")
                ).strip()[:80]
            else:
                name = str(item).strip()[:24]
                alias = ""
                description = ""
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "alias": alias,
                    "description": description,
                }
            )
        return out[-max(1, limit) :]

    def _normalize_memory_strings(
        self,
        raw_items: list | None,
        *,
        limit: int,
        max_chars: int,
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in raw_items or []:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            text = text[:max_chars]
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out[-max(1, limit) :]

    def _normalize_memory_relations(self, raw_items: list | None, *, limit: int) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject", "")).strip()[:24]
            relation = str(item.get("relation", "")).strip()[:24]
            target = str(item.get("target", "")).strip()[:24]
            note = str(item.get("note", "")).strip()[:80]
            if not subject or not relation or not target:
                continue
            key = f"{subject.lower()}|{relation.lower()}|{target.lower()}"
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "subject": subject,
                    "relation": relation,
                    "target": target,
                    "note": note,
                }
            )
        return out[-max(1, limit) :]

    def _build_legacy_recent(self, recent_structured: list[dict]) -> list[str]:
        legacy_recent: list[str] = []
        for item in recent_structured:
            role = str(item.get("role", "unknown"))
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            if role == "assistant":
                legacy_recent.append(f"A:{text}")
            elif role == "user":
                legacy_recent.append(f"U:{text}")
            else:
                legacy_recent.append(f"U:{text}")
        return legacy_recent

    @staticmethod
    def _vision_parse_schema_spec() -> str:
        return (
            "你只能输出一个 JSON 对象，并且只能包含以下顶层字段："
            'schema, last_message, recent_messages, confidence。\n'
            "字段约束：\n"
            '1) schema 固定为 "wechat_vision_v1"。\n'
            '2) last_message 必须是对象，字段固定为 role, content_type, text, sender, is_mention_me。\n'
            '3) recent_messages 必须是数组；数组每项都必须是对象，字段固定为 role, content_type, text, sender, is_mention_me。\n'
            '4) role 只能是 "user"、"assistant"、"unknown" 之一。\n'
            '5) content_type 只能是 "text"、"image"、"mixed"、"unknown" 之一。\n'
            "6) 如果消息是图片且主要内容清晰可辨，text 写成 [图片: ...] 形式的简短可见内容描述；如果看不清就写 [图片]。\n"
            "7) 图片描述只写能直接看见的内容，不要臆测，不要推断意图。\n"
            "8) is_mention_me 必须是布尔值 true/false。\n"
            "9) confidence 必须是 0 到 1 之间的数字。\n"
            "10) 不要输出 null；缺失时用空字符串、空数组、false、0.0 或 unknown。\n"
            "11) 不要添加 schema 之外的新字段，不要输出 markdown，不要输出解释。\n"
            "合法示例：\n"
            "{\n"
            '  "schema": "wechat_vision_v1",\n'
            '  "last_message": {\n'
            '    "role": "user",\n'
            '    "content_type": "image",\n'
            '    "text": "[图片: 微信聊天截图，内容是催今天几点开会]",\n'
            '    "sender": "示例用户",\n'
            '    "is_mention_me": true\n'
            "  },\n"
            '  "recent_messages": [\n'
            '    {"role": "assistant", "content_type": "text", "text": "收到，我看下。", "sender": "", "is_mention_me": false},\n'
            '    {"role": "user", "content_type": "image", "text": "[图片: 微信聊天截图，内容是催今天几点开会]", "sender": "示例用户", "is_mention_me": true}\n'
            "  ],\n"
            '  "confidence": 0.93\n'
            "}"
        )

    @staticmethod
    def _vision_context_schema_spec() -> str:
        return (
            "你只能输出一个 JSON 对象，并且只能包含以下顶层字段："
            'schema, context, environment, confidence。\n'
            "字段约束：\n"
            '1) schema 固定为 "wechat_context_v2"。\n'
            '2) context 必须是对象，字段固定为 chat_records, last_message。\n'
            '3) chat_records 必须是数组；数组每项都必须是对象，字段固定为 role, content_type, text, sender, is_mention_me。\n'
            '4) last_message 必须是对象，字段固定为 role, content_type, text, sender, is_mention_me。\n'
            '5) environment 必须是对象，字段固定为 summary, time_hints, people, facts, events, relations。\n'
            '6) people 数组每项字段固定为 name, alias, description。\n'
            '7) relations 数组每项字段固定为 subject, relation, target, note。\n'
            '8) role 只能是 "user"、"assistant"、"unknown" 之一。\n'
            '9) content_type 只能是 "text"、"image"、"mixed"、"unknown" 之一。\n'
            "10) 如果消息是图片且主要内容清晰可辨，text 写成 [图片: ...] 形式的简短可见内容描述；如果看不清就写 [图片]。\n"
            "11) time_hints 仅记录截图里能直接看见的时间线索（如“昨天 19:30”“周三”），不要臆测。\n"
            "12) 图片描述和环境信息只写可见事实，不要推断意图。\n"
            "13) is_mention_me 必须是布尔值 true/false。\n"
            "14) confidence 必须是 0 到 1 之间的数字。\n"
            "15) 不要输出 null；缺失时用空字符串、空数组、false、0.0 或 unknown。\n"
            "16) 不要添加 schema 之外的新字段，不要输出 markdown，不要输出解释。\n"
            "合法示例：\n"
            "{\n"
            '  "schema": "wechat_context_v2",\n'
            '  "context": {\n'
            '    "chat_records": [\n'
            '      {"role": "assistant", "content_type": "text", "text": "我看下。", "sender": "", "is_mention_me": false},\n'
            '      {"role": "user", "content_type": "image", "text": "[图片: 游戏资讯截图，内容是魔兽世界更新公告]", "sender": "示例用户", "is_mention_me": false}\n'
            "    ],\n"
            '    "last_message": {"role": "user", "content_type": "image", "text": "[图片: 游戏资讯截图，内容是魔兽世界更新公告]", "sender": "示例用户", "is_mention_me": false}\n'
            "  },\n"
            '  "environment": {\n'
            '    "summary": "对方在催问和游戏资讯相关的问题。",\n'
            '    "time_hints": ["昨天 19:30"],\n'
            '    "people": [{"name": "示例用户", "alias": "阿亮", "description": "会找我帮忙查信息"}],\n'
            '    "facts": ["示例用户最近关注魔兽世界资讯"],\n'
            '    "events": ["今天让助手检索魔兽世界最新消息"],\n'
            '    "relations": [{"subject": "示例用户", "relation": "常向我咨询", "target": "助手", "note": "多次让我帮忙查信息"}]\n'
            "  },\n"
            '  "confidence": 0.91\n'
            "}"
        )

    def _post_chat_vision(self, payload: dict) -> str:
        if not self.vision_cfg:
            raise RuntimeError("vision config missing")
        api_key = self._resolve_vision_api_key()
        native = self._is_native_ollama_vision()
        if native:
            self._log_transport_debug(
                label="vision",
                native=True,
                think_raw=self.vision_cfg.ollama_think,
                compat_think_mode=self.vision_cfg.openai_compat_think_mode,
                controlled=False,
            )
            url = self._ollama_chat_url(self.vision_cfg.base_url)
            native_payload = self._ollama_payload_from_chat(
                payload=payload,
                model=self.vision_cfg.model,
                max_tokens=self.vision_cfg.max_tokens,
                temperature=0.0,
                think=self.vision_cfg.ollama_think,
            )
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.vision_cfg.timeout_sec,
                payload=native_payload,
                label="vision",
            )
            content = self._extract_content_from_ollama_chat(data)
            if content:
                return content
            raise RuntimeError("vision returned empty content")

        url = f"{self.vision_cfg.base_url}/chat/completions"
        controlled_payload, controlled = self._apply_reasoning_controls(
            payload,
            self.vision_cfg.base_url,
            exclude=self.vision_cfg.reasoning_exclude,
            effort=self.vision_cfg.reasoning_effort,
            think_mode=self.vision_cfg.openai_compat_think_mode,
        )
        self._log_transport_debug(
            label="vision",
            native=False,
            think_raw=self.vision_cfg.ollama_think,
            compat_think_mode=self.vision_cfg.openai_compat_think_mode,
            controlled=controlled,
        )
        try:
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.vision_cfg.timeout_sec,
                payload=controlled_payload,
                label="vision",
            )
        except Exception as exc:
            fallback_payload = payload
            if self._is_response_format_unsupported(exc):
                print(
                    "[warn] vision response_format unsupported, retrying without response_format"
                )
                fallback_payload = self._drop_response_format(payload)
                try:
                    data = self._request_completion(
                        url=url,
                        api_key=api_key,
                        timeout_sec=self.vision_cfg.timeout_sec,
                        payload=self._drop_response_format(controlled_payload),
                        label="vision",
                    )
                except Exception as exc2:
                    exc = exc2
                else:
                    exc = None
            if exc is None:
                pass
            elif controlled and self._should_retry_without_controls(exc):
                print(f"[warn] vision controlled payload rejected, retrying raw payload: {exc}")
                data = self._request_completion(
                    url=url,
                    api_key=api_key,
                    timeout_sec=self.vision_cfg.timeout_sec,
                    payload=fallback_payload,
                    label="vision",
                )
            else:
                raise
        content = self._extract_content_from_completion(data)
        if content:
            return content

        if controlled:
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.vision_cfg.timeout_sec,
                payload=payload,
                label="vision",
            )
            content = self._extract_content_from_completion(data)
            if content:
                print("[warn] vision content empty with reasoning-off, retried without controls")
                return content

        raise RuntimeError("vision returned empty content")

    def _attach_vision_json_response_format(self, payload: dict) -> dict:
        patched = dict(payload)
        if self.vision_cfg and self.vision_cfg.response_format_json_object:
            patched["response_format"] = {"type": "json_object"}
        return patched

    def parse_chat_from_image(
        self,
        image: Image.Image,
        title: str,
    ) -> dict:
        if not self.is_vision_enabled():
            raise RuntimeError("vision disabled")

        data_url = self._image_to_data_url(image)

        user_text = (
            f"会话标题: {title or '未知'}\n"
            "请阅读截图并输出严格 JSON（不能有 markdown 代码块）。\n"
            "schema 必须为 wechat_vision_v1，格式如下：\n"
            "{\n"
            "  \"schema\":\"wechat_vision_v1\",\n"
            "  \"last_message\":{\"role\":\"user|assistant|unknown\",\"content_type\":\"text|image|mixed|unknown\",\"text\":\"...\",\"sender\":\"...\",\"is_mention_me\":false},\n"
            "  \"recent_messages\":[\n"
            "    {\"role\":\"user|assistant|unknown\",\"content_type\":\"text|image|mixed|unknown\",\"text\":\"...\",\"sender\":\"...\",\"is_mention_me\":false}\n"
            "  ],\n"
            "  \"confidence\":0.0\n"
            "}\n"
            "规则：\n"
            "1) 绿色气泡是 assistant(我方)，白色/灰色是 user(他方)。\n"
            "2) recent_messages 最多10条，按时间从旧到新。\n"
            "3) 图片消息如果主要内容清晰可辨，text 写成 [图片: ...] 形式的简短可见内容描述；如果看不清就写 [图片]。\n"
            "4) mixed 可带简短文字摘要；图片描述只写能直接看见的内容，不要编造，不要推断意图。\n"
            + self._vision_parse_schema_spec()
        )
        payload_body = {
            "model": self.vision_cfg.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": self.vision_cfg.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        base_max_tokens = self._effective_text_max_tokens(self.vision_cfg.max_tokens)
        if base_max_tokens is not None:
            payload_body["max_tokens"] = base_max_tokens
        payload = self._attach_vision_json_response_format(payload_body)
        content = self._post_chat_vision(payload)
        try:
            parsed = self._extract_json_payload(content)
            data = (
                self._convert_vision_list_payload(parsed)
                if isinstance(parsed, list)
                else parsed
            )
        except Exception:
            # Retry once with stricter instruction when provider returns preamble text.
            rescue_payload_body = {
                "model": self.vision_cfg.model,
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            self.vision_cfg.system_prompt
                            + " 必须只返回一个 JSON 对象，禁止任何前缀文字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "只输出 JSON 对象，不要说 Here is the JSON requested。"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            }
            if base_max_tokens is not None:
                rescue_payload_body["max_tokens"] = max(320, min(base_max_tokens, 640))
            rescue_payload = self._attach_vision_json_response_format(rescue_payload_body)
            content = self._post_chat_vision(rescue_payload)
            parsed = self._extract_json_payload(content)
            data = (
                self._convert_vision_list_payload(parsed)
                if isinstance(parsed, list)
                else parsed
            )

        # Backward-compat: accept legacy shape used in previous versions.
        if "schema" not in data and any(
            k in data for k in ("last_speaker", "last_message", "recent_messages")
        ):
            last_speaker = str(data.get("last_speaker", "unknown")).strip().lower()
            last_message = str(data.get("last_message", "")).strip()
            last_user_message = str(data.get("last_user_message", "")).strip()
            recent_messages = []
            for item in (data.get("recent_messages") or []):
                s = str(item).strip()
                if s:
                    recent_messages.append(s)
            return {
                "schema": "wechat_vision_legacy",
                "last_speaker": last_speaker,
                "last_message": last_message,
                "last_user_message": last_user_message,
                "recent_messages": recent_messages[:10],
                "recent_structured": [],
                "confidence": float(data.get("confidence", 0.0) or 0.0),
            }

        schema = str(data.get("schema", "")).strip()
        if schema != "wechat_vision_v1":
            raise RuntimeError(f"unexpected vision schema: {schema or 'missing'}")

        recent_structured = self._normalize_structured_records(
            data.get("recent_messages") or [],
            limit=10,
        )

        last_raw = data.get("last_message") or {}
        if isinstance(last_raw, dict):
            last_role = self._norm_role(str(last_raw.get("role", "")))
            last_type = self._norm_type(str(last_raw.get("content_type", "")))
            last_text = str(last_raw.get("text", "")).strip()
            if last_type == "image" and not last_text:
                last_text = "[图片]"
        else:
            last_role = "unknown"
            last_text = str(last_raw).strip()

        # fallback from recent if last_message missing.
        if not last_text and recent_structured:
            last_item = recent_structured[-1]
            last_role = str(last_item.get("role", "unknown"))
            last_text = str(last_item.get("text", "")).strip()

        last_user_message = ""
        for item in reversed(recent_structured):
            if str(item.get("role")) == "user":
                last_user_message = str(item.get("text", "")).strip()
                if last_user_message:
                    break
        if not last_user_message and last_role == "user":
            last_user_message = last_text

        legacy_recent = self._build_legacy_recent(recent_structured)

        if last_role == "assistant":
            last_speaker = "self"
        elif last_role == "user":
            last_speaker = "other"
        else:
            last_speaker = "unknown"

        return {
            "schema": schema,
            "last_speaker": last_speaker,
            "last_message": last_text[:220],
            "last_user_message": last_user_message[:220],
            "recent_messages": legacy_recent[-10:],
            "recent_structured": recent_structured,
            "confidence": float(data.get("confidence", 0.0) or 0.0),
        }

    def analyze_chat_image(
        self,
        *,
        image: Image.Image,
        title: str,
        reason: str,
        is_group: bool,
        session_context: str = "",
        session_history: str = "",
        latest_hint: str = "",
        preview: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
        avoid_replies: list[str] | None = None,
    ) -> dict:
        if not self.is_vision_enabled():
            raise RuntimeError("vision disabled")

        data_url = self._image_to_data_url(image)
        vision_max_tokens = self._effective_text_max_tokens(self.vision_cfg.max_tokens)
        primary_max_tokens = (
            max(320, min(vision_max_tokens, 1024)) if vision_max_tokens is not None else None
        )
        compact_max_tokens = (
            max(256, min(vision_max_tokens, 512)) if vision_max_tokens is not None else None
        )

        _ = (
            session_context,
            session_history,
            workspace_context,
            memory_recall,
            avoid_replies,
        )

        def _build_user_text(*, compact: bool) -> str:
            return (
                f"会话类型: {'群聊' if is_group else '私聊'}\n"
                f"触发原因: {reason}\n"
                f"会话标题: {title or '未知'}\n"
                f"列表预览: {preview or '无'}\n"
                f"最新消息提示: {latest_hint or '无'}\n"
                f"附加线索: {('仅截图可见信息' if compact else '请优先依赖截图内容，文字线索仅作辅助')}\n"
                "请阅读截图并输出如下 JSON：\n"
                "{\n"
                '  "schema":"wechat_context_v2",\n'
                '  "context":{"chat_records":[{"role":"user|assistant|unknown","content_type":"text|image|mixed|unknown","text":"...","sender":"...","is_mention_me":false}],"last_message":{"role":"user|assistant|unknown","content_type":"text|image|mixed|unknown","text":"...","sender":"...","is_mention_me":false}},\n'
                '  "environment":{"summary":"<=120字","time_hints":["<=40字"],"people":[{"name":"...","alias":"...","description":"<=40字"}],"facts":["<=60字"],"events":["<=60字"],"relations":[{"subject":"...","relation":"...","target":"...","note":"<=40字"}]},\n'
                '  "confidence":0.0\n'
                "}\n"
                "规则：\n"
                "1) 绿色气泡视为 assistant(我方)，白色/灰色气泡视为 user(对方)。\n"
                "2) context.chat_records 最多保留最近 20 条，按时间从旧到新。\n"
                "3) 图片消息如果主要内容清晰可辨，text 写成 [图片: ...] 形式的简短可见内容描述；如果看不清就写 [图片]。\n"
                "4) environment.time_hints 只写截图中能直接读出的时间线索（如今天/昨天/周三/19:30），看不见就空数组。\n"
                "5) environment.summary/facts/events/relations 只写截图可见内容，不要结合外部记忆推理。\n"
                "6) 这一层只做观察，不做是否回复判断，也不要生成回复文案。\n"
                + self._vision_context_schema_spec()
            )

        system_prompt = (
            f"{self.vision_cfg.system_prompt}\n"
            "你必须严格输出一个 JSON 对象，不能输出 markdown、解释或前缀文字。\n"
            "schema 必须为 wechat_context_v2。\n"
            "禁止输出 schema 之外的字段；禁止输出 null；字段名必须与 schema 完全一致。"
        )
        user_text = _build_user_text(compact=False)
        payload_body = {
            "model": self.vision_cfg.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                    },
                ],
        }
        if primary_max_tokens is not None:
            payload_body["max_tokens"] = primary_max_tokens
        payload = self._attach_vision_json_response_format(payload_body)
        try:
            content = self._post_chat_vision(payload)
        except Exception as exc:
            err = str(exc).lower()
            if ("timed out" not in err) and ("timeout" not in err):
                raise
            print("[warn] vision request timed out, retrying with compact context")
            compact_payload_body = {
                "model": self.vision_cfg.model,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _build_user_text(compact=True)},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            }
            if compact_max_tokens is not None:
                compact_payload_body["max_tokens"] = compact_max_tokens
            compact_payload = self._attach_vision_json_response_format(compact_payload_body)
            content = self._post_chat_vision(compact_payload)
        parsed = self._extract_json_payload(content)
        data = self._convert_vision_list_payload(parsed) if isinstance(parsed, list) else parsed

        schema = str(data.get("schema", "")).strip()
        if schema == "wechat_reply_v1":
            context_raw = {
                "chat_records": data.get("chat_records") or data.get("recent_messages") or [],
                "last_message": data.get("last_message") or {},
            }
            environment_raw = data.get("environment") or data.get("memory_update") or {}
            data = {
                "schema": "wechat_context_v2",
                "context": context_raw,
                "environment": environment_raw,
                "confidence": data.get("confidence", 0.0),
            }
            schema = "wechat_context_v2"
        if schema == "wechat_vision_v1":
            data = {
                "schema": "wechat_context_v2",
                "context": {
                    "chat_records": data.get("recent_messages") or [],
                    "last_message": data.get("last_message") or {},
                },
                "environment": {
                    "summary": "",
                    "time_hints": [],
                    "people": [],
                    "facts": [],
                    "events": [],
                    "relations": [],
                },
                "confidence": data.get("confidence", 0.0),
            }
            schema = "wechat_context_v2"
        if schema != "wechat_context_v2":
            raise RuntimeError(f"unexpected context schema: {schema or 'missing'}")

        context_raw = data.get("context") or {}
        if not isinstance(context_raw, dict):
            context_raw = {}
        environment_raw = data.get("environment") or data.get("memory_update") or {}
        if not isinstance(environment_raw, dict):
            environment_raw = {}

        chat_records = self._normalize_structured_records(
            context_raw.get("chat_records")
            or data.get("chat_records")
            or data.get("recent_messages")
            or [],
            limit=20,
        )

        last_raw = context_raw.get("last_message") or data.get("last_message") or {}
        if isinstance(last_raw, dict):
            last_message = {
                "role": self._norm_role(str(last_raw.get("role", ""))),
                "content_type": self._norm_type(str(last_raw.get("content_type", ""))),
                "text": str(last_raw.get("text", "")).strip()[:220],
                "sender": str(last_raw.get("sender", "")).strip()[:40],
                "is_mention_me": bool(last_raw.get("is_mention_me", False)),
            }
        else:
            last_message = {
                "role": "unknown",
                "content_type": "unknown",
                "text": str(last_raw).strip()[:220],
                "sender": "",
                "is_mention_me": False,
            }

        if (not last_message["text"]) and chat_records:
            last_message = dict(chat_records[-1])

        if environment_raw:
            memory_summary = str(environment_raw.get("summary", "")).strip()[:240]
            time_hints = self._normalize_memory_strings(
                environment_raw.get("time_hints")
                if isinstance(environment_raw.get("time_hints"), list)
                else [],
                limit=12,
                max_chars=40,
            )
            memory_people = self._normalize_memory_people(
                environment_raw.get("people")
                if isinstance(environment_raw.get("people"), list)
                else [],
                limit=10,
            )
            memory_facts = self._normalize_memory_strings(
                environment_raw.get("facts")
                if isinstance(environment_raw.get("facts"), list)
                else [],
                limit=12,
                max_chars=80,
            )
            memory_events = self._normalize_memory_strings(
                environment_raw.get("events")
                if isinstance(environment_raw.get("events"), list)
                else [],
                limit=12,
                max_chars=80,
            )
            memory_relations = self._normalize_memory_relations(
                environment_raw.get("relations")
                if isinstance(environment_raw.get("relations"), list)
                else [],
                limit=12,
            )
        else:
            memory_summary = ""
            time_hints = []
            memory_people = []
            memory_facts = []
            memory_events = []
            memory_relations = []

        legacy_recent = self._build_legacy_recent(chat_records)
        last_user_message = ""
        for item in reversed(chat_records):
            if str(item.get("role")) == "user":
                last_user_message = str(item.get("text", "")).strip()[:220]
                if last_user_message:
                    break

        last_role = str(last_message.get("role", "unknown"))
        last_speaker = "self" if last_role == "assistant" else ("other" if last_role == "user" else "unknown")
        return {
            "schema": "wechat_context_v2",
            "conversation": {
                "title": str(title or "").strip()[:80],
                "is_group": bool(is_group),
            },
            "context": {
                "chat_records": chat_records,
                "recent_messages": legacy_recent[-20:],
                "recent_structured": chat_records,
                "last_message": last_message,
                "last_speaker": last_speaker,
                "last_user_message": last_user_message,
            },
            "environment": {
                "summary": memory_summary,
                "time_hints": time_hints,
                "people": memory_people,
                "facts": memory_facts,
                "events": memory_events,
                "relations": memory_relations,
            },
            # Backward-compatible mirrors for legacy callers.
            "chat_records": chat_records,
            "recent_messages": legacy_recent[-20:],
            "recent_structured": chat_records,
            "last_message": last_message,
            "last_speaker": last_speaker,
            "last_user_message": last_user_message,
            "memory_update": {
                "summary": memory_summary,
                "time_hints": time_hints,
                "people": memory_people,
                "facts": memory_facts,
                "events": memory_events,
                "relations": memory_relations,
            },
            "confidence": float(data.get("confidence", 0.0) or 0.0),
        }

    def should_reply(
        self,
        title: str,
        preview: str,
        reason: str,
        is_group: bool,
        chat_context: str = "",
        environment_context: str = "",
        session_context: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
    ) -> tuple[bool, str]:
        user_prompt = (
            f"会话类型: {'群聊' if is_group else '私聊'}\n"
            f"触发原因: {reason}\n"
            f"会话标题: {title or '未知'}\n"
            f"最新预览: {preview or '无'}\n"
            f"最近聊天内容: {chat_context or '无'}\n"
            f"聊天环境信息: {environment_context or '无'}\n"
            f"该会话历史上下文: {session_context or '无'}\n"
            f"工作区规则与人格: {workspace_context or '无'}\n"
            f"相关记忆检索: {memory_recall or '无'}\n"
            f"我的关注偏好: {self.cfg.interest_hint or '无'}\n"
            "判断是否应该自动回复。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.0,
            "max_tokens": self.cfg.decision_max_tokens,
            "messages": [
                {"role": "system", "content": self.cfg.decision_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        content = self._post_chat(payload)
        try:
            if "{" in content:
                content = content[content.find("{") : content.rfind("}") + 1]
            data = json.loads(content)
            decision = str(data.get("decision", "")).strip().lower()
            why = str(data.get("reason", "")).strip()
            return decision == "reply", why or decision
        except Exception as exc:
            raise RuntimeError(f"llm decision parse error: {exc}; raw={content}")

    def _sarcasm_style_guard(self) -> str:
        level = str(getattr(self.cfg, "sarcasm_level", "low")).strip().lower()
        if level == "off":
            return "阴阳强度=off：保持自然直接，不用阴阳。"
        if level == "high":
            return (
                "阴阳强度=high：可明显带点阴阳味和轻微犯贱（最多 2 句短句），"
                "但别低俗、别刷屏。"
            )
        if level == "medium":
            return "阴阳强度=medium：可带一点阴阳味（建议 1 句内），保持活人感。"
        return "阴阳强度=low：可偶尔轻微阴阳（点到即止），以自然交流为主。"

    def generate(
        self,
        title: str,
        preview: str,
        reason: str,
        latest_message: str = "",
        chat_context: str = "",
        environment_context: str = "",
        session_context: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
        avoid_replies: list[str] | None = None,
        allow_no_reply_signal: bool = True,
    ) -> str:
        avoid_replies = avoid_replies or []
        has_web_search_observation = (
            ("[工具执行结果]" in (memory_recall or ""))
            and ("网页检索[" in (memory_recall or ""))
        )
        avoid_txt = ""
        if avoid_replies:
            clipped = [re.sub(r"\s+", " ", x).strip()[:90] for x in avoid_replies if x and x.strip()]
            clipped = clipped[:5]
            if clipped:
                avoid_txt = (
                    "\n请避免与下列你最近回复重复（措辞和语义都要明显不同）：\n- "
                    + "\n- ".join(clipped)
                )
        if has_web_search_observation:
            source_guard = (
                "\n当前存在网页检索结果。你可以基于[工具执行结果]中的网页检索内容回答，"
                "但不要编造不存在的官网、公告、链接、时间或搜索过程。"
            )
        else:
            source_guard = (
                "\n当前没有网页检索结果。禁止声称你用了websearch、看到了官网/官方公告/"
                "最新维护公告、或已联网核实；也不要把记忆检索、截图内容、历史对话包装成"
                "外部搜索结果。若对方追问你是否用了websearch，必须如实表述这次没有联网核验。"
            )
        sarcasm_guard = self._sarcasm_style_guard()
        task_guard = (
            "执行约束：无论语气是否阴阳，用户明确要求做的事要优先做到；"
            "不要只口头答应“去查/去做”却不给结果。"
            "做不到时要明确说明卡点和下一步。"
        )
        user_prompt = (
            f"触发原因: {reason}\n"
            f"会话标题: {title or '未知'}\n"
            f"最新预览: {preview or '无'}\n"
            f"最新一条对方消息: {latest_message or preview or '无'}\n"
            f"最近聊天内容: {chat_context or '无'}\n"
            f"聊天环境信息: {environment_context or '无'}\n"
            f"该会话历史上下文: {session_context or '无'}\n"
            f"工作区规则与人格: {workspace_context or '无'}\n"
            f"相关记忆检索: {memory_recall or '无'}\n"
            "回复风格硬约束：自然口语优先，禁止在句首或整句写括号动作描写（如“（...）”）；"
            "全句最多使用 1 个 emoji，能不用就不用；避免夸张拟人舞台腔。"
            f"{sarcasm_guard}"
            "遇到严肃求助或情绪敏感场景时，自动降低阴阳强度，优先清晰结论。\n"
            f"{task_guard}\n"
            "请直接输出回复内容，不要解释。"
            + (
                "如果判断当前不该回复，请仅输出 [NO_REPLY]。"
                if allow_no_reply_signal
                else "必须给出可直接发送的中文回复，不允许输出 [NO_REPLY]、无需回复、不回复。"
            )
            + source_guard
            + avoid_txt
        )

        payload = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "presence_penalty": self.cfg.presence_penalty,
            "frequency_penalty": self.cfg.frequency_penalty,
            "max_tokens": self.cfg.max_tokens,
            "messages": [
                {"role": "system", "content": self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        return self._post_chat(payload)

    def plan_actions(
        self,
        *,
        title: str,
        is_group: bool,
        reason: str,
        latest_message: str = "",
        chat_context: str = "",
        environment_context: str = "",
        session_context: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
        available_tools: list[str] | None = None,
        max_actions: int = 2,
    ) -> dict:
        if not self.is_enabled():
            return {"actions": [], "reply_hint": ""}

        tools = [str(x).strip() for x in (available_tools or []) if str(x).strip()]
        if not tools:
            return {"actions": [], "reply_hint": ""}
        tool_set = set(tools)

        tool_specs = {
            "remember_session_fact": "args={\"fact\":\"<=120字\"} 记录当前会话稳定事实",
            "remember_session_event": "args={\"event\":\"<=120字\"} 记录当前会话近期事件",
            "set_session_summary": "args={\"summary\":\"<=200字\"} 更新当前会话画像摘要",
            "search_memory": "args={\"query\":\"<=80字\"} 在记忆库检索相关片段",
            "web_search": "args={\"query\":\"<=80字\"} 联网检索公开网页信息（Tavily）",
            "remember_long_term": "args={\"note\":\"<=200字\"} 写入长期记忆（仅管理员）",
            "maintain_memory": "args={\"days\":1-14} 整理近期记忆到 MEMORY.md",
            "refine_persona_files": "args={} 整理 SOUL/IDENTITY/USER/TOOLS 设定文件",
            "mute_session": "args={} 静音当前会话（仅管理员）",
            "unmute_session": "args={} 取消静音当前会话（仅管理员）",
        }
        tool_lines = [f"- {name}: {tool_specs.get(name, 'args={}')}" for name in tools]

        system_prompt = (
            "你是微信机器人动作规划器。"
            "你只能从给定工具白名单中选择动作，不允许发明新工具。"
            "你必须严格输出一个 JSON 对象，不要输出 markdown、解释或前缀。"
            "输出格式必须是："
            '{"actions":[{"tool":"...","args":{},"reason":"<=40字"}],"reply_hint":"<=120字"}。'
            "如果不需要动作，actions 返回空数组。reply_hint 可空串。"
        )
        user_prompt = (
            f"会话类型: {'群聊' if is_group else '私聊'}\n"
            f"触发原因: {reason}\n"
            f"会话标题: {title or '未知'}\n"
            f"最新一条对方消息: {latest_message or '无'}\n"
            f"最近聊天内容: {chat_context or '无'}\n"
            f"聊天环境信息: {environment_context or '无'}\n"
            f"该会话历史上下文: {session_context or '无'}\n"
            f"工作区规则与人格: {workspace_context or '无'}\n"
            f"相关记忆检索: {memory_recall or '无'}\n"
            "可用工具白名单:\n"
            + "\n".join(tool_lines)
            + "\n动作约束：\n"
            + f"1) actions 最多 {max(1, int(max_actions))} 个。\n"
            + "2) 参数必须简短、可执行，不要空参数对象里塞无关字段。\n"
            + "3) 对用户可见回复由主回复链路处理，这里只规划动作与 reply_hint。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.0,
            "max_tokens": max(120, min(520, self.cfg.max_tokens)),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        content = self._post_chat(payload)
        parsed_any = self._extract_json_payload(content)
        if isinstance(parsed_any, dict):
            parsed = parsed_any
            # Some providers wrap the real payload as {"plan": {...}}.
            plan_obj = parsed.get("plan")
            if not isinstance(parsed.get("actions"), list) and isinstance(plan_obj, dict):
                parsed = plan_obj
        elif isinstance(parsed_any, list):
            # Be tolerant: model may output a top-level action array.
            if (
                len(parsed_any) == 1
                and isinstance(parsed_any[0], dict)
                and ("actions" in parsed_any[0] or "reply_hint" in parsed_any[0])
            ):
                parsed = parsed_any[0]
            else:
                parsed = {"actions": parsed_any, "reply_hint": ""}
        else:
            raise RuntimeError(
                f"agent action planner returned unsupported json type: {type(parsed_any)}"
            )

        reply_hint = re.sub(r"\s+", " ", str(parsed.get("reply_hint", "") or "")).strip()[:180]

        normalized: list[dict] = []
        raw_actions = parsed.get("actions")
        if isinstance(raw_actions, list):
            for item in raw_actions:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool", "")).strip()
                if not tool or tool not in tool_set:
                    continue
                args_raw = item.get("args")
                args_obj = args_raw if isinstance(args_raw, dict) else {}
                args: dict[str, str] = {}
                if tool == "remember_long_term":
                    note = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("note", "") or args_obj.get("text", "")).strip(),
                    )[:200]
                    if not note:
                        continue
                    args = {"note": note}
                elif tool == "remember_session_fact":
                    fact = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("fact", "") or args_obj.get("text", "")).strip(),
                    )[:120]
                    if not fact:
                        continue
                    args = {"fact": fact}
                elif tool == "remember_session_event":
                    event = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("event", "") or args_obj.get("text", "")).strip(),
                    )[:120]
                    if not event:
                        continue
                    args = {"event": event}
                elif tool == "set_session_summary":
                    summary = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("summary", "") or args_obj.get("text", "")).strip(),
                    )[:200]
                    if not summary:
                        continue
                    args = {"summary": summary}
                elif tool == "search_memory":
                    query = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("query", "") or args_obj.get("text", "")).strip(),
                    )[:80]
                    if not query:
                        continue
                    args = {"query": query}
                elif tool == "web_search":
                    query = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("query", "") or args_obj.get("text", "")).strip(),
                    )[:80]
                    if not query:
                        continue
                    args = {"query": query}
                elif tool == "maintain_memory":
                    days_raw = args_obj.get("days", 3)
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 3
                    days = max(1, min(14, days))
                    args = {"days": days}
                elif tool == "refine_persona_files":
                    args = {}
                elif tool in ("mute_session", "unmute_session"):
                    args = {}
                else:
                    continue

                action = {"tool": tool, "args": args}
                reason_txt = re.sub(r"\s+", " ", str(item.get("reason", "")).strip())[:40]
                if reason_txt:
                    action["reason"] = reason_txt
                normalized.append(action)
                if len(normalized) >= max(1, int(max_actions)):
                    break

        return {
            "actions": normalized,
            "reply_hint": reply_hint,
        }

    def heartbeat_memory_digest(
        self,
        *,
        existing_memory: str,
        recent_daily_memory: str,
        max_items: int = 12,
    ) -> str:
        if not self.is_enabled():
            return ""
        user_prompt = (
            "任务：把近期流水记忆整理为可长期保留的稳定记忆。\n"
            f"现有 MEMORY.md 内容:\n{existing_memory or '无'}\n\n"
            f"近期每日记忆:\n{recent_daily_memory or '无'}\n\n"
            f"输出要求：\n"
            f"1) 仅输出中文 markdown 列表（每行以 `- ` 开头），最多 {max(3, int(max_items))} 条。\n"
            "2) 只保留长期有效信息：稳定偏好、长期约定、关键关系、持续项目。\n"
            "3) 删除一次性琐碎流水，不要写时间戳。\n"
            "4) 不要输出解释、不要代码块。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.1,
            "max_tokens": max(220, min(820, self.cfg.max_tokens)),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是记忆整理器。只输出可直接写入 MEMORY.md 的精简项目列表。"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        }
        text = self._post_chat(payload).strip()
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in text.split("\n")]
        items = []
        for line in lines:
            if not line:
                continue
            if line.startswith("-"):
                item = line
            else:
                item = f"- {line.lstrip('-').strip()}"
            item = item[:200]
            if item.strip("- ").strip():
                items.append(item)
            if len(items) >= max(3, int(max_items)):
                break
        return "\n".join(items)

    def heartbeat_refine_persona_docs(
        self,
        *,
        soul: str,
        identity: str,
        user: str,
        tools: str,
        memory: str,
    ) -> dict[str, str]:
        if not self.is_enabled():
            return {}
        user_prompt = (
            "请根据以下材料，整理四个设定文件的“精简更新版”。\n"
            "输出 JSON，字段固定为 soul, identity, user, tools；"
            "每个字段都是 markdown 文本字符串，不要额外字段。\n\n"
            f"[SOUL.md]\n{soul or '无'}\n\n"
            f"[IDENTITY.md]\n{identity or '无'}\n\n"
            f"[USER.md]\n{user or '无'}\n\n"
            f"[TOOLS.md]\n{tools or '无'}\n\n"
            f"[MEMORY.md]\n{memory or '无'}\n\n"
            "要求：\n"
            "1) 各字段保持简洁（建议 8-20 行）。\n"
            "2) 去重、去冲突、去过时信息。\n"
            "3) 保留稳定人格、身份、用户关系、能力边界。\n"
            "4) 不要编造新的事实。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.1,
            "max_tokens": max(420, min(1300, self.cfg.max_tokens)),
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是设定文件整理器。必须只输出 JSON 对象，禁止 markdown 代码块与解释。"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        }
        raw = self._post_chat(payload)
        parsed = self._extract_json_payload(raw)
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, str] = {}
        for key in ("soul", "identity", "user", "tools"):
            raw_value = str(parsed.get(key, "") or "").replace("\r\n", "\n")
            lines = [ln.rstrip() for ln in raw_value.split("\n")]
            cleaned: list[str] = []
            last_blank = False
            for line in lines:
                text = line.strip()
                if not text:
                    if not last_blank:
                        cleaned.append("")
                    last_blank = True
                    continue
                cleaned.append(text)
                last_blank = False
            value = "\n".join(cleaned).strip()
            if not value:
                continue
            out[key] = value[:4000]
        return out

    def summarize_session(
        self,
        title: str,
        previous_summary: str,
        short_items: list[str],
    ) -> str:
        if not self.is_enabled():
            return previous_summary
        if not self.cfg.summary_enabled:
            return previous_summary

        recent = " | ".join(short_items[-12:]) if short_items else "无"
        user_prompt = (
            f"会话标题: {title or '未知'}\n"
            f"历史摘要: {previous_summary or '无'}\n"
            f"最近对话片段: {recent}\n"
            "请输出更新后的摘要，120字以内。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.1,
            "max_tokens": self.cfg.summary_max_tokens,
            "messages": [
                {"role": "system", "content": self.cfg.summary_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        return self._post_chat(payload).strip()
