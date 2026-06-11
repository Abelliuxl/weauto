from __future__ import annotations

import builtins
import json
import os
import queue
import ssl
import base64
import io
import re
import sys
import threading
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

    def _api_format(self) -> str:
        value = str(getattr(self.cfg, "api_format", "openai") or "openai").strip().lower()
        if value == "anthropic":
            return "anthropic"
        return "openai"

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
    def _coerce_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except Exception:
            return None

    @staticmethod
    def _usage_triplet_from_response(data: dict) -> tuple[int | None, int | None, int | None, int | None]:
        # OpenAI-compatible usage shape.
        usage = data.get("usage")
        if isinstance(usage, dict):
            prompt = LlmReplyGenerator._coerce_int(
                usage.get("prompt_tokens", usage.get("input_tokens"))
            )
            completion = LlmReplyGenerator._coerce_int(
                usage.get("completion_tokens", usage.get("output_tokens"))
            )
            total = LlmReplyGenerator._coerce_int(usage.get("total_tokens"))
            if total is None and (prompt is not None or completion is not None):
                total = (prompt or 0) + (completion or 0)
            cached = None
            for key in ("prompt_tokens_details", "input_tokens_details"):
                detail = usage.get(key)
                if isinstance(detail, dict):
                    cached = LlmReplyGenerator._coerce_int(detail.get("cached_tokens"))
                    if cached is not None:
                        break
            return prompt, completion, total, cached

        # Ollama native shape.
        prompt = LlmReplyGenerator._coerce_int(data.get("prompt_eval_count"))
        completion = LlmReplyGenerator._coerce_int(data.get("eval_count"))
        total = None
        if prompt is not None or completion is not None:
            total = (prompt or 0) + (completion or 0)
        return prompt, completion, total, None

    @staticmethod
    def _fmt_usage_value(value: int | None) -> str:
        return str(value) if value is not None else "-"

    @staticmethod
    def _clip_model_name(name: str, limit: int = 80) -> str:
        clean = re.sub(r"\s+", " ", str(name or "").strip())
        if not clean:
            return "-"
        if len(clean) <= limit:
            return clean
        return clean[:limit] + "..."

    def _log_usage_line(self, *, label: str, data: dict, request_model: str) -> None:
        model_name = self._clip_model_name(str(data.get("model", "")).strip() or request_model)
        prompt, completion, total, cached = self._usage_triplet_from_response(data)
        if prompt is None and completion is None and total is None:
            print(f"[usage] {label} model={model_name} prompt=- completion=- total=-")
            return
        msg = (
            f"[usage] {label} model={model_name} "
            f"prompt={self._fmt_usage_value(prompt)} "
            f"completion={self._fmt_usage_value(completion)} "
            f"total={self._fmt_usage_value(total)}"
        )
        if cached is not None:
            msg += f" cached={cached}"
        print(msg)

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
            api_format = "openai"
        else:
            base = self.cfg.base_url or ""
            api_format = self._api_format()
        provider = "openrouter" if "openrouter.ai" in base.lower() else "other"
        think_value = self._ollama_think_value(think_raw)
        normalized_mode = (compat_think_mode or "default").strip().lower()
        if normalized_mode not in ("default", "on", "off"):
            normalized_mode = "default"
        think_effective = bool(native and (think_value is not None)) or (
            (not native) and normalized_mode in ("on", "off")
        )
        print(
            f"[debug-{label}] transport={'ollama_native' if native else f'{api_format}_compat'} "
            f"think_raw={think_raw!r} compat_think_mode={normalized_mode!r} "
            f"think_effective={think_effective} "
            f"reasoning_controls_applied={controlled} provider={provider}"
        )

    @staticmethod
    def _anthropic_messages_url(base_url: str) -> str:
        root = str(base_url or "").strip().rstrip("/")
        if root.endswith("/messages"):
            return root
        return f"{root}/messages"

    @staticmethod
    def _anthropic_text_block(text: str) -> dict[str, str]:
        return {"type": "text", "text": str(text or "")}

    def _openai_content_to_anthropic(self, content: object) -> list[dict]:
        blocks: list[dict] = []
        if isinstance(content, str):
            clean = content.strip()
            if clean:
                blocks.append(self._anthropic_text_block(clean))
            return blocks
        if not isinstance(content, list):
            return blocks
        for part in content:
            if isinstance(part, str):
                clean = part.strip()
                if clean:
                    blocks.append(self._anthropic_text_block(clean))
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).strip().lower()
            text = str(part.get("text", "") or "").strip()
            if text and part_type in ("", "text", "output_text", "input_text"):
                blocks.append(self._anthropic_text_block(text))
        return blocks

    def _anthropic_payload_from_chat(self, payload: dict) -> dict:
        messages = payload.get("messages") or []
        system_parts: list[str] = []
        anthropic_messages: list[dict] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            raw_role = str(message.get("role", "")).strip().lower()
            role = "system" if raw_role == "system" else self._norm_role(raw_role)
            blocks = self._openai_content_to_anthropic(message.get("content"))
            if not blocks:
                continue
            text_for_system = "\n".join(
                str(item.get("text", "")).strip() for item in blocks if str(item.get("text", "")).strip()
            ).strip()
            if role == "system":
                if text_for_system:
                    system_parts.append(text_for_system)
                continue
            if role not in ("user", "assistant"):
                role = "user"
            anthropic_messages.append({"role": role, "content": blocks})

        max_tokens = self._effective_text_max_tokens(payload.get("max_tokens"))
        if max_tokens is None:
            max_tokens = self._effective_text_max_tokens(self.cfg.max_tokens)
        if max_tokens is None:
            max_tokens = 4096

        out: dict[str, object] = {
            "model": str(payload.get("model", self.cfg.model) or self.cfg.model),
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }
        if system_parts:
            out["system"] = "\n\n".join(system_parts)
        temperature = payload.get("temperature")
        try:
            if temperature is not None:
                out["temperature"] = max(0.0, float(temperature))
        except Exception:
            pass
        return out

    @staticmethod
    def _anthropic_retry_payload_with_more_tokens(payload: dict) -> dict:
        patched = dict(payload)
        current = LlmReplyGenerator._effective_text_max_tokens(patched.get("max_tokens"))
        if current is None:
            current = 0
        bumped = max(512, current * 4 if current > 0 else 512)
        patched["max_tokens"] = min(4096, bumped)
        return patched

    @staticmethod
    def _anthropic_response_has_text(data: dict) -> bool:
        content = data.get("content")
        if not isinstance(content, list):
            return False
        for part in content:
            if not isinstance(part, dict):
                continue
            text = str(part.get("text", "") or part.get("content", "") or "").strip()
            if text and str(part.get("type", "")).strip().lower() in ("", "text", "output_text"):
                return True
        return False

    @staticmethod
    def _anthropic_needs_more_tokens(data: dict) -> bool:
        stop_reason = str(data.get("stop_reason", "") or "").strip().lower()
        if stop_reason not in ("max_tokens", "length"):
            return False
        return not LlmReplyGenerator._anthropic_response_has_text(data)

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
        decoder = json.JSONDecoder()
        for cand in candidates:
            for attempt in self._json_repair_attempts(cand):
                clean_attempt = attempt.lstrip("\ufeff \n\r\t")
                try:
                    data = json.loads(clean_attempt)
                    if isinstance(data, (dict, list)):
                        return data
                except Exception:
                    pass
                # Be tolerant to providers adding trailing text after a valid JSON object/array.
                try:
                    data, _ = decoder.raw_decode(clean_attempt)
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
        )
        attempts.append(t3)

        # 4) Normalize curly quotes only in key positions (avoid corrupting literal content).
        t4 = re.sub(r'([{\[,]\s*)[“”]([^“”]+)[“”]\s*:', r'\1"\2":', t3)
        attempts.append(t4)

        # 5) Unwrap quoted JSON blobs, e.g. '{"a":1}' or "{\"a\":1}".
        t5 = t4
        if len(t5) >= 2 and t5[0] == t5[-1] and t5[0] in ("'", '"'):
            inner = t5[1:-1].strip()
            if inner.startswith("{") or inner.startswith("["):
                t5 = inner
                attempts.append(t5)

        # 6) Fix stray quote before comma between object close and next key.
        # Example: {"args":{"query":"x"}","reason":"..."} -> {"args":{"query":"x"},"reason":"..."}
        t6 = re.sub(r'}\s*"\s*,\s*"', '},"', t5)
        attempts.append(t6)

        # 7) Escape unescaped double quotes between CJK characters inside string values.
        # LLMs often output literal "..." around quoted terms in Chinese content fields.
        cjk_chars = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
        cjk_or_punct = cjk_chars + r"，。、；："
        t7 = re.sub(
            rf'(?<=[{cjk_chars}])"([^"\n\r{{}}\[\]]*?[{cjk_chars}][^"\n\r{{}}\[\]]*?)"(?=[{cjk_or_punct}])',
            r"“\1”",
            t6,
        )
        t7 = re.sub(r'(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef，。、；：])', "“", t7)
        t7 = re.sub(r'(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef，。、；：])"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])', "”", t7)
        attempts.append(t7)

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
        reasoning_budget: int = 0,
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

        if "integrate.api.nvidia.com" in provider:
            # NVIDIA NIM uses chat-template controls rather than generic
            # reasoning_effort/reasoning fields.
            if normalized_mode in ("on", "off"):
                template_kwargs = dict(updated.get("chat_template_kwargs") or {})
                template_kwargs["enable_thinking"] = normalized_mode == "on"
                updated["chat_template_kwargs"] = template_kwargs
                if normalized_mode == "on" and reasoning_budget > 0:
                    updated["reasoning_budget"] = max(
                        1,
                        min(32768, int(reasoning_budget)),
                    )
                else:
                    updated.pop("reasoning_budget", None)
                controlled = True
            return updated, controlled

        if "xiaomimimo.com" in provider:
            # MiMo uses a provider-specific binary thinking switch:
            # {"thinking": {"type": "enabled"|"disabled"}}.
            # Do not send generic reasoning_effort to MiMo; it is not an effort-scale API.
            if normalized_mode == "on":
                updated["thinking"] = {"type": "enabled"}
                controlled = True
            elif normalized_mode == "off":
                updated["thinking"] = {"type": "disabled"}
                controlled = True
            return updated, controlled

        reasoning_obj: dict[str, object] = {}
        if normalized_effort in ("minimal", "low", "medium", "high"):
            # OpenRouter expects nested reasoning controls; avoid the extra
            # top-level compatibility field there so we keep effort=high
            # without triggering a raw-payload fallback.
            if "openrouter.ai" not in provider:
                updated["reasoning_effort"] = normalized_effort
            # Keep nested compatibility for providers that parse reasoning.effort.
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
        request_model: str = "",
        auth_mode: str = "bearer",
        keep_token_limits: bool = False,
    ) -> dict:
        model_name = str(request_model or payload.get("model", "")).strip()
        last_error: RuntimeError | None = None
        for attempt in (1, 2):
            resp_text = self._request_text(
                url=url,
                api_key=api_key,
                timeout_sec=timeout_sec,
                payload=payload,
                label=label,
                extra_headers=extra_headers,
                model=model_name,
                auth_mode=auth_mode,
                keep_token_limits=keep_token_limits,
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
                        self._log_usage_line(
                            label=label,
                            data=data,
                            request_model=model_name,
                        )
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
        model: str = "",
        auth_mode: str = "bearer",
        keep_token_limits: bool = False,
    ) -> str:
        request_payload = dict(payload) if keep_token_limits else self._drop_token_limits(payload)
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
        if api_key and auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif api_key and auth_mode == "x-api-key":
            headers["x-api-key"] = api_key
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers=headers,
        )
        started = time.monotonic()
        model_name = self._clip_model_name(model)
        progress_interval_sec = 1.0
        try:
            def _do_request() -> str:
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                with urllib.request.urlopen(req, timeout=timeout_sec, context=ssl_ctx) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            done_q: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

            def _worker() -> None:
                try:
                    done_q.put(("ok", _do_request()))
                except BaseException as exc:  # keep worker failures visible to caller
                    done_q.put(("err", exc))

            worker = threading.Thread(
                target=_worker,
                name=f"{label}-request",
                daemon=True,
            )
            worker.start()
            timeout_value = max(float(timeout_sec), 0.1)
            deadline = started + timeout_value
            last_logged_sec = -1
            while True:
                now = time.monotonic()
                remaining = deadline - now
                if remaining <= 0:
                    raise TimeoutError("forced cutoff at request deadline")
                wait_sec = min(progress_interval_sec, remaining)
                try:
                    state, payload = done_q.get(timeout=max(wait_sec, 0.05))
                except queue.Empty:
                    elapsed_live = int(time.monotonic() - started)
                    if elapsed_live != last_logged_sec:
                        print(
                            f"[timing] {label} model={model_name} "
                            f"running={elapsed_live:d}s timeout={float(timeout_sec):.1f}s"
                        )
                        last_logged_sec = elapsed_live
                    continue
                if state == "ok":
                    resp_text = str(payload)
                    break
                raise payload if isinstance(payload, BaseException) else RuntimeError(
                    f"{label} worker failed without exception object"
                )
            elapsed = time.monotonic() - started
            print(
                f"[timing] {label} model={model_name} "
                f"elapsed={elapsed:.2f}s timeout={float(timeout_sec):.1f}s"
            )
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
        except TimeoutError as exc:
            elapsed = time.monotonic() - started
            raise RuntimeError(
                f"{label} timeout after {elapsed:.2f}s "
                f"(timeout={float(timeout_sec):.1f}s): {exc}"
            )
        return resp_text

    def _extract_content_from_completion(self, data: dict) -> str:
        direct_content = data.get("content")
        if isinstance(direct_content, str):
            clean = self._strip_provider_think_content(direct_content)
            if clean:
                return clean.strip()
        content_blocks = data.get("content")
        if isinstance(content_blocks, list):
            chunks: list[str] = []
            for part in content_blocks:
                if isinstance(part, str) and part.strip():
                    chunks.append(part.strip())
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).strip().lower()
                text = str(part.get("text", "") or part.get("content", "") or "").strip()
                if part_type not in ("", "text", "output_text", "input_text", "thinking") and (not text):
                    continue
                if text:
                    chunks.append(text)
            if chunks:
                merged = self._strip_provider_think_content("\n".join(chunks))
                if merged:
                    return merged.strip()

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
        if isinstance(content, dict):
            text = str(content.get("text", "") or content.get("content", "") or "").strip()
            if text:
                clean = self._strip_provider_think_content(text)
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
                text = str(part.get("text", "") or part.get("content", "") or "").strip()
                if text:
                    chunks.append(text)
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

    @staticmethod
    def _first_chat_message(data: dict) -> dict:
        choices = data.get("choices") if isinstance(data, dict) else []
        if not isinstance(choices, list) or not choices:
            return {}
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        return message if isinstance(message, dict) else {}

    def _post_openai_chat_completion(self, payload: dict) -> dict:
        api_key = self._resolve_api_key()
        url = f"{self.cfg.base_url}/chat/completions"
        controlled_payload, controlled = self._apply_reasoning_controls(
            payload,
            self.cfg.base_url,
            exclude=self.cfg.reasoning_exclude,
            effort=self.cfg.reasoning_effort,
            think_mode=self.cfg.openai_compat_think_mode,
            reasoning_budget=self.cfg.reasoning_budget,
        )
        self._log_transport_debug(
            label="llm",
            native=False,
            think_raw=self.cfg.ollama_think,
            compat_think_mode=self.cfg.openai_compat_think_mode,
            controlled=controlled,
        )
        try:
            return self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.cfg.timeout_sec,
                payload=controlled_payload,
                label="llm",
            )
        except Exception as exc:
            if controlled and self._should_retry_without_controls(exc):
                print(f"[warn] llm controlled payload rejected, retrying raw payload: {exc}")
                return self._request_completion(
                    url=url,
                    api_key=api_key,
                    timeout_sec=self.cfg.timeout_sec,
                    payload=payload,
                    label="llm",
                )
            raise

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

        if self._api_format() == "anthropic":
            url = self._anthropic_messages_url(self.cfg.base_url)
            anthropic_payload = self._anthropic_payload_from_chat(payload)
            self._log_transport_debug(
                label="llm",
                native=False,
                think_raw=self.cfg.ollama_think,
                compat_think_mode=self.cfg.openai_compat_think_mode,
                controlled=False,
            )
            data = self._request_completion(
                url=url,
                api_key=api_key,
                timeout_sec=self.cfg.timeout_sec,
                payload=anthropic_payload,
                label="llm",
                extra_headers={"anthropic-version": "2023-06-01"},
                auth_mode="x-api-key",
                keep_token_limits=True,
            )
            if self._anthropic_needs_more_tokens(data):
                retry_payload = self._anthropic_retry_payload_with_more_tokens(anthropic_payload)
                print(
                    "[warn] anthropic response exhausted max_tokens before final text, "
                    f"retrying with max_tokens={retry_payload['max_tokens']}"
                )
                data = self._request_completion(
                    url=url,
                    api_key=api_key,
                    timeout_sec=self.cfg.timeout_sec,
                    payload=retry_payload,
                    label="llm",
                    extra_headers={"anthropic-version": "2023-06-01"},
                    auth_mode="x-api-key",
                    keep_token_limits=True,
                )
            content = self._extract_content_from_completion(data)
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
            reasoning_budget=self.cfg.reasoning_budget,
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

    def describe_image(self, image: Image.Image, *, prompt: str = "") -> str:
        if not self.is_vision_enabled():
            raise RuntimeError("vision disabled")
        data_url = self._image_to_data_url(image)
        vision_prompt = (
            prompt.strip()
            or "Describe this image in detail. If it contains text, transcribe the visible text. Answer in Chinese."
        )
        payload: dict = {
            "model": self.vision_cfg.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": self.vision_cfg.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        vision_max_tokens = self._effective_text_max_tokens(self.vision_cfg.max_tokens)
        if vision_max_tokens is not None:
            payload["max_tokens"] = max(256, min(vision_max_tokens, 1200))
        return self._post_chat_vision(payload).strip()

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
            '    "people": [{"name": "示例用户", "alias": "好友A", "description": "会找我帮忙查信息"}],\n'
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
            reasoning_budget=self.vision_cfg.reasoning_budget,
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

    @staticmethod
    def _normalize_visible_message_items(data: object, *, limit: int = 24) -> list[dict]:
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            if isinstance(data.get("messages"), list):
                raw_items = data.get("messages") or []
            elif isinstance(data.get("recent_messages"), list):
                raw_items = data.get("recent_messages") or []
            elif isinstance(data.get("chat_records"), list):
                raw_items = data.get("chat_records") or []
            else:
                raw_items = []
        else:
            raw_items = []

        messages: list[dict] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            sender = str(item.get("sender", "") or "").strip()
            if not sender:
                role = str(item.get("role", "") or "").strip().lower()
                sender = "我" if role == "assistant" else ""
            if not sender:
                continue
            raw_content = item.get("content", item.get("text", ""))
            content = None if raw_content is None else str(raw_content).strip()
            messages.append({"sender": sender[:40], "content": content})
            if len(messages) >= limit:
                break
        return messages

    def parse_visible_messages_from_image(
        self,
        image: Image.Image,
        title: str,
    ) -> dict:
        if not self.is_vision_enabled():
            raise RuntimeError("vision disabled")

        data_url = self._image_to_data_url(image)
        system_prompt = (
            "你是一个微信聊天截图识别助手。只输出 JSON，不要输出解释、前言或 Markdown。"
        )
        user_text = (
            f"会话标题: {title or '未知'}\n"
            "任务：识别我提供的微信聊天记录截图，按截图中从上到下的顺序，"
            "提取每一条聊天消息，并输出为固定 JSON 格式。\n"
            "要求：\n"
            "1. 只输出 JSON，不要输出解释、前言或 Markdown。\n"
            "2. JSON 只包含 messages 字段。\n"
            "3. 每条消息只包含 sender 和 content 两个字段。\n"
            "4. sender 必须填写截图中实际显示的发送者昵称。\n"
            "5. 如果是右侧绿色气泡，且截图中没有显示自己的昵称，sender 写 \"我\"。\n"
            "6. 如果是左侧消息，sender 写该消息上方或头像旁显示的昵称。\n"
            "7. 不要把 sender 固定写成 \"self\" 或 \"other\"，除非截图中发送者昵称本身就是这个词。\n"
            "8. content 填写该条消息的原文内容，尽量保持原文，不要总结、改写或补充。\n"
            "9. 如果消息是图片，content 格式为 \"[图片] 图片内容描述\"。\n"
            "10. 如果消息是表情，content 格式为 \"[表情] 表情内容描述\"。\n"
            "11. 如果消息是语音，content 格式为 \"[语音] 无法识别具体内容\"，除非截图中能看到语音转文字。\n"
            "12. 如果消息是文件、视频、链接、转账等非纯文本内容，也在开头用方括号标明类型，例如 \"[文件]\"、\"[视频]\"、\"[链接]\"、\"[转账]\"。\n"
            "13. 如果某条消息内容无法识别，content 写 null。\n"
            "14. 不要输出任何总结、分析、判断、情绪理解或截图外的信息。\n"
            "输出格式：{\"messages\":[{\"sender\":\"发送者昵称或我\",\"content\":\"消息原文或[图片] 图片描述\"}]}"
        )
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
        base_max_tokens = self._effective_text_max_tokens(self.vision_cfg.max_tokens)
        if base_max_tokens is not None:
            payload_body["max_tokens"] = max(256, min(base_max_tokens, 1200))
        payload = self._attach_vision_json_response_format(payload_body)
        content = self._post_chat_vision(payload)
        try:
            parsed = self._extract_json_payload(content)
        except Exception:
            rescue_payload_body = {
                "model": self.vision_cfg.model,
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt + " 必须只返回一个 JSON 对象。",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "只输出 JSON：{\"messages\":[{\"sender\":\"我或昵称\",\"content\":\"原文或null\"}]}。"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
            }
            if base_max_tokens is not None:
                rescue_payload_body["max_tokens"] = max(256, min(base_max_tokens, 800))
            rescue_payload = self._attach_vision_json_response_format(rescue_payload_body)
            content = self._post_chat_vision(rescue_payload)
            parsed = self._extract_json_payload(content)

        messages = self._normalize_visible_message_items(parsed)
        if not messages:
            raise RuntimeError("vision messages JSON contains no messages")
        return {"messages": messages}

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

    @staticmethod
    def _is_payment_gate_text(text: str) -> bool:
        raw = re.sub(r"\s+", " ", (text or "").strip())
        if not raw:
            return False
        markers = (
            "红包",
            "稿费",
            "茶钱",
            "转账",
            "打钱",
            "给钱",
            "收费",
            "付费",
        )
        return any(m in raw for m in markers)

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
        has_web_observation = (
            ("[工具执行结果]" in (memory_recall or ""))
            and any(
                marker in (memory_recall or "")
                for marker in ("网页检索[", "网页浏览[", "网页抓取[", "网页读取[")
            )
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
        if has_web_observation:
            source_guard = (
                "\n当前存在网页访问/检索工具结果。必须优先依据[工具执行结果]回答；"
                "如果工具结果与聊天历史冲突，以最新工具结果为准。"
                "网页浏览/网页抓取返回正文内容表示访问成功，禁止沿用历史里的 403 失败结论。"
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
            "严禁索要红包/稿费/转账或把回答设为先付款再说。"
        )
        mention_style = (
            "注意：这是@消息，对方在直接向你提问。请在自然口语的基础上，"
            "把回答写得比普通闲聊更完整详实——给出结论、关键数据/来源和必要细节。\n"
            if reason == "mention"
            else ""
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
            f"{mention_style}"
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

    @staticmethod
    def _agent_tool_specs_for_names(tool_names: list[str]) -> list[dict]:
        specs: dict[str, dict] = {
            "read_memory": {
                "description": "Read current core/timeline memory markdown. Use this before careful memory edits.",
                "properties": {
                    "name": {"type": "string", "enum": ["core", "timeline", "all"], "description": "Memory file to read."},
                },
            },
            "recall_memory": {
                "description": "Search durable memory, timeline, and person impressions for relevant snippets.",
                "properties": {
                    "query": {"type": "string", "description": "What to recall, <=80 chars."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            "remember_fact": {
                "description": "Append a short durable memory fact without replacing the whole memory file.",
                "properties": {
                    "scope": {"type": "string", "enum": ["core", "timeline"], "description": "core for stable facts/rules, timeline for dated events."},
                    "content": {"type": "string", "description": "Short evidence-based fact to remember."},
                    "source": {"type": "string", "description": "Optional source note, e.g. current chat."},
                },
                "required": ["content"],
            },
            "write_memory": {
                "description": "Replace core or timeline memory markdown.",
                "properties": {
                    "name": {"type": "string", "enum": ["core", "timeline"]},
                    "content": {"type": "string", "description": "Complete markdown content."},
                },
                "required": ["name", "content"],
            },
            "list_skills": {
                "description": "List all available skill names, summaries and keywords from data/skills/. No arguments needed.",
                "properties": {},
            },
            "read_skill": {
                "description": "Read one complete skill file from data/skills/<name>/SKILL.md before careful edits.",
                "properties": {"name": {"type": "string", "description": "Skill directory name."}},
                "required": ["name"],
            },
            "write_skill": {
                "description": "Save a reusable local strategy/procedure into data/skills/<name>/SKILL.md.",
                "properties": {
                    "name": {"type": "string", "description": "Skill directory name."},
                    "content": {"type": "string", "description": "Complete SKILL.md content."},
                },
                "required": ["name", "content"],
            },
            "update_skill": {
                "description": "Append a small rule or maintenance note to one skill while preserving existing content.",
                "properties": {
                    "name": {"type": "string", "description": "Skill directory name."},
                    "note": {"type": "string", "description": "Short grounded rule or update to append."},
                    "source": {"type": "string", "description": "Optional source note, e.g. current chat."},
                },
                "required": ["name", "note"],
            },
            "delete_skill": {
                "description": "Delete an obsolete saved skill from data/skills.",
                "properties": {"name": {"type": "string", "description": "Skill directory name."}},
                "required": ["name"],
            },
            "read_impression": {
                "description": "Read one person's stored impression before replying or updating it.",
                "properties": {"name": {"type": "string", "description": "Canonical Chinese name."}},
                "required": ["name"],
            },
            "read_chat_history": {
                "description": "Read recent chat history with timestamps. Defaults to current chat when chat_title is omitted.",
                "properties": {
                    "chat_title": {"type": "string", "description": "Exact chat title; optional."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            "read_chat_history_by_date": {
                "description": (
                    "Read many timestamped messages from one chat on a specific local date. "
                    "Use for detailed requests like '今天某群聊了什么', '昨天某群记录', or 'show me that day's chat'."
                ),
                "properties": {
                    "chat_title": {"type": "string", "description": "Exact chat title, e.g. 群-临沧."},
                    "date": {"type": "string", "description": "YYYY-MM-DD, MM-DD, today/今天, or yesterday/昨天. Defaults to today."},
                    "max_items": {"type": "integer", "minimum": 1, "maximum": 800},
                    "max_chars": {"type": "integer", "minimum": 1200, "maximum": 20000},
                },
                "required": ["chat_title"],
            },
            "summarize_chat_history": {
                "description": (
                    "Return compact timestamped summary material for one chat on one local date. "
                    "Use FIRST when asked '今天/刚才/昨天某群聊了什么' or to summarize a day's group chat. "
                    "Do not search the group name as a keyword before using this."
                ),
                "properties": {
                    "chat_title": {"type": "string", "description": "Exact chat title, e.g. 群-临沧."},
                    "date": {"type": "string", "description": "YYYY-MM-DD, MM-DD, today/今天, or yesterday/昨天. Defaults to today."},
                    "max_chars": {"type": "integer", "minimum": 1200, "maximum": 8000},
                },
                "required": ["chat_title"],
            },
            "search_chat_history": {
                "description": (
                    "Search ALL chat history by keywords (not just recent messages). "
                    "Use this when asked 'who said X', 'has anyone mentioned Y', 'search history for Z'. "
                    "Do NOT use it just to summarize what a named group chatted today; use summarize_chat_history/read_chat_history_by_date instead. "
                    "Returns matching messages with surrounding context."
                ),
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search for (Chinese or English)."},
                    "chat_title": {"type": "string", "description": "Exact chat title; optional. Defaults to current chat."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Max matching results to return."},
                    "context": {"type": "integer", "minimum": 0, "maximum": 5, "description": "Number of surrounding messages to include per match."},
                },
                "required": ["query"],
            },
            "run_python": {
                "description": (
                    "Run short sandboxed Python for math, statistics, date arithmetic, unit conversion, "
                    "modular arithmetic, or power calculations. Print the result. No files/network/system access. "
                    "NOTE: by default import/from...import are NOT supported due to restricted sandbox. "
                    "Pre-imported and usable directly: math, statistics, datetime, date, timedelta, Decimal, Fraction, "
                    "mean, median. Examples: math.gcd(a,b), datetime.now(), date.today(), Decimal('3.14'), "
                    "Fraction(1,3), statistics.mean([1,2,3]), pow(base, exp, mod). "
                    "Set python_sandbox_restricted=false in config to allow full Python (import, files, etc)."
                ),
                "properties": {"code": {"type": "string", "description": "Short Python code using print(...). No import statements."}},
                "required": ["code"],
            },
            "write_impression": {
                "description": "Replace one person's impression markdown.",
                "properties": {
                    "name": {"type": "string", "description": "Canonical Chinese name."},
                    "content": {"type": "string", "description": "Complete markdown content."},
                },
                "required": ["name", "content"],
            },
            "update_impression": {
                "description": "Append or merge a grounded observation into one person's impression while preserving existing content.",
                "properties": {
                    "name": {"type": "string", "description": "Canonical Chinese name."},
                    "note": {"type": "string", "description": "Short observation grounded in chat/context."},
                    "source": {"type": "string", "description": "Optional source note, e.g. chat title or date."},
                },
                "required": ["name", "note"],
            },
            "web_search": {
                "description": "Search public web pages with the configured provider.",
                "properties": {"query": {"type": "string", "description": "Search query, <=80 chars."}},
                "required": ["query"],
            },
            "search_web": {
                "description": "Search public web pages with Tavily.",
                "properties": {
                    "query": {"type": "string", "description": "Search query, <=80 chars."},
                    "proxy": {"type": "boolean", "description": "Accepted for wx-cli skill compatibility."},
                },
                "required": ["query"],
            },
            "search_web_brave": {
                "description": "Search public web pages with Brave Search.",
                "properties": {
                    "query": {"type": "string", "description": "Search query, <=80 chars."},
                    "proxy": {"type": "boolean", "description": "Accepted for wx-cli skill compatibility."},
                },
                "required": ["query"],
            },
            "web_search_volc": {
                "description": "Search the web with Volcengine Ark built-in trusted web search.",
                "properties": {"query": {"type": "string", "description": "Search query, <=80 chars."}},
                "required": ["query"],
            },
            "search_web_volc": {
                "description": "Search the web with Volcengine Ark built-in trusted web search.",
                "properties": {"query": {"type": "string", "description": "Search query, <=80 chars."}},
                "required": ["query"],
            },
            "fetch_url": {
                "description": "Fetch raw HTML/text from a URL and return readable text. Do not use for Wowhead; use browse_url.",
                "properties": {
                    "url": {"type": "string", "description": "HTTP/HTTPS URL."},
                    "proxy": {"type": "boolean", "description": "Use system proxy env if true; disable env proxy if false."},
                },
                "required": ["url"],
            },
            "browse_url": {
                "description": "Fetch a rendered page with Playwright when available. Use for Wowhead and other anti-bot/rendered pages.",
                "properties": {
                    "url": {"type": "string", "description": "HTTP/HTTPS URL."},
                    "proxy": {"type": "boolean", "description": "Use system proxy env if true; disable env proxy if false."},
                },
                "required": ["url"],
            },
            "build_wow_character_url": {
                "description": (
                    "Build an official WoW CN character page URL using data/skills/wow-character-link. "
                    "Use for 魔兽/WoW角色主页/玩家职业角色; do not use run_python for URL building."
                ),
                "properties": {
                    "title": {"type": "string", "description": "WeChat chat title; optional."},
                    "character": {"type": "string", "description": "Exact character name, if known."},
                    "server": {"type": "string", "description": "Chinese server name or realm slug, if known."},
                    "player": {"type": "string", "description": "Player name or alias."},
                    "class_name": {"type": "string", "description": "Class/job under the player, e.g. 战士."},
                },
            },
            "query_weather": {
                "description": (
                    "Query current weather and daily forecast for a city/location with QWeather. "
                    "Choose location, mode, days, date, or day_offset from the user's request."
                ),
                "properties": {
                    "location": {"type": "string", "description": "City/location name, e.g. 临沧, 北京, 上海浦东."},
                    "days": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Forecast days to request, up to 30."},
                    "date": {"type": "string", "description": "Optional target date in YYYY-MM-DD; use when the user asks a specific day."},
                    "day_offset": {"type": "integer", "minimum": 0, "maximum": 29, "description": "Optional offset from today: today=0, tomorrow=1."},
                    "mode": {"type": "string", "enum": ["now", "forecast", "both"], "description": "Weather data scope."},
                },
            },
            "generate_image": {
                "description": "Generate an image and send the local file.",
                "properties": {
                    "prompt": {"type": "string", "description": "Concrete image prompt, <=280 chars."},
                    "size": {"type": "string", "description": "Optional size like 1024x1024."},
                },
                "required": ["prompt"],
            },
            "edit_image": {
                "description": "Edit the latest/current image or a provided image path/url.",
                "properties": {
                    "prompt": {"type": "string", "description": "Concrete edit instruction, <=800 chars."},
                    "image_path": {"type": "string", "description": "Optional local image path."},
                    "image_url": {"type": "string", "description": "Optional image URL."},
                    "size": {"type": "string", "description": "Optional size like 1024x1024."},
                },
                "required": ["prompt"],
            },
            "mute_session": {                "required": ["note"],
            },
            "maintain_memory": {
                "description": "Admin only: consolidate recent memory into MEMORY.md.",
                "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 14}},
            },
            "maintain_person_impressions": {
                "description": "Admin only: maintain person impression files from recent chats.",
                "properties": {
                    "days": {"type": "integer", "minimum": 1, "maximum": 3650},
                    "max_people": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            "refine_persona_files": {
                "description": "Admin only: refine SOUL/IDENTITY/USER/TOOLS persona files.",
                "properties": {},
            },
            "mute_session": {"description": "Admin only: mute current session.", "properties": {}},
            "unmute_session": {"description": "Admin only: unmute current session.", "properties": {}},
        }
        out: list[dict] = []
        for name in tool_names:
            spec = specs.get(name)
            if not spec:
                continue
            parameters = {"type": "object", "properties": spec.get("properties", {})}
            required = spec.get("required")
            if isinstance(required, list):
                parameters["required"] = required
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.get("description", ""),
                        "parameters": parameters,
                    },
                }
            )
        return out

    def _parse_tool_call_arguments(self, raw: object) -> dict:
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except Exception:
            try:
                data = self._extract_json_payload(text)
            except Exception:
                return {}
        return dict(data) if isinstance(data, dict) else {}

    def _raw_actions_from_tool_calls(self, message: dict, *, tool_set: set[str]) -> list[dict]:
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raw_calls = []
        function_call = message.get("function_call")
        if isinstance(function_call, dict):
            raw_calls.append({"function": function_call})

        actions: list[dict] = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name", "")).strip()
            if not name or name not in tool_set:
                continue
            args = self._parse_tool_call_arguments(fn.get("arguments"))
            actions.append({"tool": name, "args": args, "reason": "tool_call"})
        return actions

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
        agent_task_state: str = "",
        planner_round_idx: int = 1,
        planner_round_total: int = 1,
        planner_total_actions_limit: int = 2,
        planner_total_actions_used: int = 0,
        planner_total_actions_remaining: int = 2,
    ) -> dict:
        if not self.is_enabled():
            return {"actions": [], "reply_hint": "", "send_reply": True, "task": {}}

        tools = [str(x).strip() for x in (available_tools or []) if str(x).strip()]
        if not tools:
            return {"actions": [], "reply_hint": "", "send_reply": True, "task": {}}
        tool_set = set(tools)
        round_idx = max(1, int(planner_round_idx))
        round_total = max(1, int(planner_round_total))
        total_limit = max(1, int(planner_total_actions_limit))
        total_used = max(0, int(planner_total_actions_used))
        total_remaining = max(0, min(total_limit, int(planner_total_actions_remaining)))

        tool_specs = {
            "list_skills": "args={} 列出 data/skills/ 下所有已注册技能的名称和摘要",
            "read_skill": "args={\"name\":\"技能名\"} 读取 data/skills/<name>/SKILL.md 完整内容；谨慎修改前先读",
            "read_memory": "args={\"name\":\"core|timeline|all\"} 读取当前长期记忆；谨慎改记忆前先读",
            "recall_memory": "args={\"query\":\"<=80字\",\"limit\":1-10} 检索 core/timeline/人物印象中的相关片段",
            "remember_fact": (
                "args={\"scope\":\"core|timeline\",\"content\":\"短事实\",\"source\":\"可选来源\"} "
                "追加一条长期记忆，不替换整个文件"
            ),
            "write_memory": (
                "args={\"name\":\"core|timeline\",\"content\":\"完整 markdown\"} "
                "完整替换 data/memory/core.md 或 timeline.md；仅在明确需要重写整份记忆时使用"
            ),
            "update_skill": (
                "args={\"name\":\"技能名\",\"note\":\"短规则/维护说明\",\"source\":\"可选来源\"} "
                "追加维护 data/skills/<name>/SKILL.md，保留原内容"
            ),
            "write_skill": (
                "args={\"name\":\"技能名\",\"content\":\"完整 SKILL.md\"} "
                "完整写入 data/skills/<name>/SKILL.md；仅在明确需要重写整份 skill 时使用"
            ),
            "delete_skill": "args={\"name\":\"技能名\"} 删除 data/skills/<name>",
            "read_impression": "args={\"name\":\"规范中文名\"} 读取 data/people/<name>.md 人物印象",
            "update_impression": (
                "args={\"name\":\"规范中文名\",\"note\":\"短观察\",\"source\":\"可选来源\"} "
                "追加/合并人物印象，保留原内容"
            ),
            "read_chat_history": (
                "args={\"chat_title\":\"可选，不填为当前会话\",\"limit\":1-100} "
                "读取带时间戳的最近聊天记录"
            ),
            "read_chat_history_by_date": (
                "args={\"chat_title\":\"群名\",\"date\":\"YYYY-MM-DD/today/今天/昨天\",\"max_items\":1-800,\"max_chars\":1200-20000} "
                "按本地日期读取某会话大量带时间戳记录；适合需要细看当天聊天明细"
            ),
            "summarize_chat_history": (
                "args={\"chat_title\":\"群名\",\"date\":\"YYYY-MM-DD/today/今天/昨天\",\"max_chars\":1200-8000} "
                "读取某会话某天的紧凑摘要素材。遇到“今天/刚才/昨天某群聊了什么”“总结某群今天内容”必须优先用它，不要先搜索群名"
            ),
            "search_chat_history": (
                "args={\"query\":\"关键词\",\"chat_title\":\"可选\",\"limit\":1-30,\"context\":0-5} "
                "按关键词搜索全量聊天记录。用于\"谁提过X\"\"聊过Y\"\"查历史Z\"。返回匹配行+前后文；不要用于按群名总结当天聊天"
            ),
            "run_python": (
                "args={\"code\":\"短代码，需 print 输出\"} "
                "仅用于数学、统计、日期计算；无文件/网络/系统访问。"
                "注意：沙盒默认限制 import/from...import；已预置可直接用：math, statistics, datetime, date, timedelta, Decimal, Fraction, mean, median；如 math.gcd(a,b), datetime.now(), Decimal('3.14'), pow(x,y,mod)。设 python_sandbox_restricted=false 可解除限制（允许 import、文件等）"
            ),
            "write_impression": (
                "args={\"name\":\"规范中文名\",\"content\":\"完整 markdown\"} "
                "完整替换 data/people/<name>.md；仅在明确需要重写整份人物印象时使用"
            ),
            "web_search": "args={\"query\":\"<=80字\"} 联网检索公开网页信息（provider 可配置）",
            "search_web": "args={\"query\":\"<=80字\"} Tavily 联网检索",
            "search_web_brave": "args={\"query\":\"<=80字\"} Brave 联网检索",
            "web_search_volc": "args={\"query\":\"<=80字\"} 联网检索（火山方舟内置搜索，单次可信模式）",
            "search_web_volc": "args={\"query\":\"<=80字\"} 联网检索（火山方舟内置搜索，单次可信模式）",
            "fetch_url": "args={\"url\":\"http(s)://...\"} 抓取静态网页/文本内容；Wowhead 不要用此工具",
            "browse_url": "args={\"url\":\"http(s)://...\"} 读取渲染后页面文本；Wowhead 优先用此工具",
            "build_wow_character_url": (
                "args={\"character\":\"角色名\",\"server\":\"服务器\"} 或 "
                "{\"player\":\"玩家/别名\",\"class_name\":\"职业\"} 构建国服魔兽角色主页链接"
            ),
            "query_weather": (
                "args={\"location\":\"城市/地点\",\"days\":1-30,\"date\":\"YYYY-MM-DD可选\","
                "\"day_offset\":\"今天0明天1可选\",\"mode\":\"now|forecast|both\"} "
                "查询实时天气/每日预报；地点、目标日期和天数由你根据用户问题自行填写"
            ),
            "generate_image": (
                "args={\"prompt\":\"<=280字\",\"size\":\"可选，如1024x1024\"} "
                "生成图片并发送本地文件"
            ),
            "edit_image": (
                "args={\"prompt\":\"<=800字\",\"image_path\":\"可选，不填用最近收到的图片\",\"size\":\"可选\"} "
                "编辑/改图并发送"
            ),
            "mute_session": "args={} 静音当前会话（仅管理员）",
            "unmute_session": "args={} 取消静音当前会话（仅管理员）",
        }
        tool_lines = [f"- {name}: {tool_specs.get(name, 'args={}')}" for name in tools]

        system_prompt = (
            "你是微信机器人动作规划器。"
            "你只负责决定是否调用工具，不负责写最终聊天回复。"
            "你只能从给定工具白名单中选择动作，不允许发明新工具。"
            "如果当前接口提供了 tools/function calling，优先使用原生工具调用；"
            "原生工具调用模式下：需要动作就调用工具；不需要动作就不要输出正文，让 actions 为空。"
            "禁止在 planner 阶段直接写给用户看的自然语言答案。"
            "如果接口没有工具调用能力，才严格输出一个 JSON 对象，不要输出 markdown、解释或前缀。"
            "JSON 中所有字符串值内的双引号必须转义为 \\\"，例如 content 字段中有中文引号时用 \\\"魔法少女\\\"。"
            "输出格式必须是："
            '{"actions":[{"tool":"...","args":{},"reason":"<=40字"}],"reply_hint":"<=120字","task":{"status":"idle|running|blocked|waiting_user|done","goal":"<=120字","plan":"<=200字","next_step":"<=120字","blocked_reason":"<=120字","continue_on_heartbeat":true|false}}。'
            "如果不需要动作，actions 返回空数组。reply_hint 可空串。"
            "task 字段必须始终给出；若没有持续任务，status=idle。"
            "若输入中已包含工具观察结果，可继续规划下一步动作；"
            "但禁止重复输出同一个 tool+args。"
            "检索证据优先级默认是：web_search_volc > web_search。"
            "若不同来源冲突，优先采用更高优先级来源，不要回退到低优先级覆盖高优先级结论。"
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
            f"当前任务状态: {agent_task_state or '无'}\n"
            f"规划轮次: 第{round_idx}/{round_total}轮\n"
            f"工具总预算: 总上限{total_limit}，已执行{total_used}，剩余{total_remaining}\n"
            "可用工具白名单:\n"
            + "\n".join(tool_lines)
            + "\n动作约束：\n"
            + f"1) actions 最多 {max(1, int(max_actions))} 个。\n"
            + "2) 参数必须简短、可执行，不要空参数对象里塞无关字段。\n"
            + "3) 对用户可见回复由主回复链路处理，这里只规划动作与 reply_hint。\n"
            + "4) reply_hint 必须是可直接发送给对方的中文短句；不能写策略说明、语气说明、风格说明，"
            + "不能写类似“顺着这个话题调侃”“用轻松语气回一句”“符合群聊氛围”这样的元提示。\n"
            + "5) reply_hint 不能索要红包/稿费/转账，不能以先给条件为前提拒绝回答。\n"
            + "6) 记忆、人物印象、技能维护优先使用 remember_fact、recall_memory、update_impression、read_skill、update_skill；不要为了追加一条信息就用 write_memory/write_impression/write_skill 完整替换。\n"
            + "7) 如果已有检索结果仍不足，请换关键词继续检索，不要机械重复同一参数。\n"
            + "8) 如果工具观察里出现 fetch_url 返回 403/Forbidden/CloudFront 拒绝访问，下一步必须对同一个 URL 改用 browse_url（Playwright 渲染），不要直接回复“访问不了”。\n"
            + "9) 若选择 web_search_volc/search_web_volc，本轮只保留它一个检索动作，不要再同时规划 web_search/search_web。\n"
            + "10) 若工具观察中已有网页检索结果（web_search/search_web/search_web_brave/web_search_volc/search_web_volc），默认直接信任该结果；"
            + "除非明确失败/无结果，否则不要再追加记忆写入动作。\n"
            + "11) 当 web_search_volc 与其他来源冲突时，以 web_search_volc 为准，并优先结束动作规划（actions 为空）。\n"
            + "12) planner 不负责决定是否回复；只要本轮被外层规则触发，最终回复链路默认继续执行。\n"
            + "13) 当用户明确要求作图/海报/配图时可用 generate_image，prompt 要具体且可执行；"
            + "若需求是纯文本答复，不要调用 generate_image。"
            + "\n13b) 当用户要求改图、修图、换风格、增删画面内容时优先用 edit_image；"
            + "如果当前消息或近期上下文已有图片，可省略 image_path。"
            + "\n14) 遇到数学、统计、日期、单位换算、取模、幂运算等需要精确计算的问题，必须先调用 run_python；run_python 沙盒默认限制 import，已预置 math/statistics/datetime/date/timedelta/Decimal/Fraction/mean/median 可直接用；设 python_sandbox_restricted=false 可解除所有限制；"
            + "没有 Python 工具观察结果时，不要直接给数值结论。"
            + "\n15) 遇到最新信息、官网公告、新闻、版本改动、价格、规则等时效事实，必须优先调用 web_search_volc/search_web_volc 或 web_search/search_web；"
            + "没有检索观察结果时，不要声称已经查过。"
            + "\n16) task.status=running 表示任务未完成，后续还要继续；若希望空闲时后台续跑，设 continue_on_heartbeat=true。"
            + "\n17) task.status=waiting_user 表示缺用户信息，continue_on_heartbeat 必须为 false。"
            + "\n18) task.status=done 表示当前任务已完成；task.status=blocked 表示被外部条件卡住，需写 blocked_reason。"
            + "\n19) 当用户要求你增加能力、沉淀流程、写 skill、以后按固定套路处理某类事时，先 list_skills/read_skill 判断是否已有类似技能；已有则优先 update_skill 追加维护，新技能才用 write_skill；过时技能用 delete_skill。"
            + "\n20) 若 [skills] 中的 skill 文件对某类任务有明确的查询流程/步骤规定，优先遵循 skill 规定，不受上述全局检索优先级约束。"
        )
        payload = {
            "model": self.cfg.model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        planner_max_tokens = self._effective_text_max_tokens(self.cfg.max_tokens)
        if planner_max_tokens is not None:
            if {"write_memory", "write_impression", "write_skill", "remember_fact", "update_impression", "update_skill"} & tool_set:
                payload["max_tokens"] = planner_max_tokens
            else:
                payload["max_tokens"] = max(120, min(520, planner_max_tokens))

        native_tool_specs = []
        if (not self._is_native_ollama_llm()) and self._api_format() == "openai":
            native_tool_specs = self._agent_tool_specs_for_names(tools)
        content = ""
        parsed: dict | None = None
        if native_tool_specs:
            native_payload = dict(payload)
            native_payload["tools"] = native_tool_specs
            native_payload["tool_choice"] = "auto"
            try:
                data = self._post_openai_chat_completion(native_payload)
                message = self._first_chat_message(data)
                tool_call_actions = self._raw_actions_from_tool_calls(message, tool_set=tool_set)
                if tool_call_actions:
                    parsed = {
                        "actions": tool_call_actions,
                        "reply_hint": "",
                        "send_reply": True,
                        "task": {"status": "idle"},
                    }
                else:
                    content = self._extract_content_from_completion(data)
                    stripped = str(content or "").strip()
                    if stripped and not stripped.startswith(("{", "[")):
                        parsed = {
                            "actions": [],
                            "reply_hint": "",
                            "send_reply": True,
                            "task": {"status": "idle"},
                        }
            except Exception as exc:
                print(f"[warn] agent native tool_calls unavailable, falling back to JSON actions: {exc}")

        if parsed is None:
            if not content:
                content = self._post_chat(payload)
            parsed_any = self._extract_json_payload(content)
        else:
            parsed_any = parsed
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
                parsed = {"actions": parsed_any, "reply_hint": "", "send_reply": True}
        else:
            raise RuntimeError(
                f"agent action planner returned unsupported json type: {type(parsed_any)}"
            )

        reply_hint = re.sub(r"\s+", " ", str(parsed.get("reply_hint", "") or "")).strip()[:180]
        if self._is_payment_gate_text(reply_hint):
            reply_hint = ""
        # Reply gating belongs to the outer event/cooldown rules. Keep this key
        # for caller compatibility, but ignore legacy planner send_reply=false.
        send_reply = True

        task_obj = parsed.get("task")
        task: dict[str, object] = {}
        if isinstance(task_obj, dict):
            status = re.sub(r"\s+", " ", str(task_obj.get("status", "")).strip()).lower()[:24]
            if status not in {"idle", "running", "blocked", "waiting_user", "done"}:
                status = "idle"
            goal = re.sub(r"\s+", " ", str(task_obj.get("goal", "")).strip())[:120]
            plan_text = task_obj.get("plan", "")
            if isinstance(plan_text, list):
                plan = "；".join(
                    re.sub(r"\s+", " ", str(x).strip())[:40]
                    for x in plan_text
                    if str(x).strip()
                )[:200]
            else:
                plan = re.sub(r"\s+", " ", str(plan_text).strip())[:200]
            next_step = re.sub(r"\s+", " ", str(task_obj.get("next_step", "")).strip())[:120]
            blocked_reason = re.sub(
                r"\s+",
                " ",
                str(task_obj.get("blocked_reason", "")).strip(),
            )[:120]
            raw_continue = task_obj.get("continue_on_heartbeat", False)
            if isinstance(raw_continue, bool):
                continue_on_heartbeat = raw_continue
            elif isinstance(raw_continue, str):
                continue_on_heartbeat = raw_continue.strip().lower() in {"1", "true", "yes", "on"}
            else:
                continue_on_heartbeat = bool(raw_continue)
            task = {
                "status": status,
                "goal": goal,
                "plan": plan,
                "next_step": next_step,
                "blocked_reason": blocked_reason,
                "continue_on_heartbeat": continue_on_heartbeat,
            }

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
                if tool == "list_skills":
                    args = {}
                elif tool == "read_skill":
                    name = re.sub(
                        r"\s+",
                        "-",
                        str(args_obj.get("name", "") or args_obj.get("skill", "")).strip(),
                    )[:80]
                    if not name:
                        continue
                    args = {"name": name}
                elif tool == "read_memory":
                    raw_name = str(args_obj.get("name", "") or args_obj.get("scope", "") or "all").strip().lower()
                    name = raw_name if raw_name in {"core", "timeline", "all"} else "all"
                    args = {"name": name}
                elif tool == "recall_memory":
                    query = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("query", "") or args_obj.get("text", "")).strip(),
                    )[:80]
                    if not query:
                        continue
                    limit_raw = args_obj.get("limit", 6)
                    try:
                        limit = int(limit_raw)
                    except Exception:
                        limit = 6
                    args = {"query": query, "limit": max(1, min(10, limit))}
                elif tool == "remember_fact":
                    raw_scope = str(args_obj.get("scope", "") or args_obj.get("name", "") or "core").strip().lower()
                    scope = "timeline" if raw_scope in {"timeline", "time", "history", "events"} else "core"
                    content = str(args_obj.get("content", "") or args_obj.get("fact", "") or args_obj.get("text", "")).strip()[:1200]
                    source = str(args_obj.get("source", "") or "").strip()[:80]
                    if not content:
                        continue
                    args = {"scope": scope, "content": content}
                    if source:
                        args["source"] = source
                elif tool == "write_memory":
                    raw_name = str(args_obj.get("name", "core")).strip().lower()
                    name = "timeline" if raw_name in {"timeline", "time", "history", "events"} else "core"
                    content = str(args_obj.get("content", "") or args_obj.get("text", "")).strip()[:12000]
                    if not content:
                        continue
                    args = {"name": name, "content": content}
                elif tool == "write_skill":
                    name = re.sub(
                        r"\s+",
                        "-",
                        str(args_obj.get("name", "") or args_obj.get("skill", "")).strip(),
                    )[:80]
                    content = str(args_obj.get("content", "") or args_obj.get("text", "")).strip()[:20000]
                    if (not name) or (not content):
                        continue
                    args = {"name": name, "content": content}
                elif tool == "update_skill":
                    name = re.sub(
                        r"\s+",
                        "-",
                        str(args_obj.get("name", "") or args_obj.get("skill", "")).strip(),
                    )[:80]
                    note = str(args_obj.get("note", "") or args_obj.get("content", "") or args_obj.get("text", "")).strip()[:1800]
                    source = str(args_obj.get("source", "") or "").strip()[:80]
                    if (not name) or (not note):
                        continue
                    args = {"name": name, "note": note}
                    if source:
                        args["source"] = source
                elif tool == "delete_skill":
                    name = re.sub(
                        r"\s+",
                        "-",
                        str(args_obj.get("name", "") or args_obj.get("skill", "")).strip(),
                    )[:80]
                    if not name:
                        continue
                    args = {"name": name}
                elif tool == "read_impression":
                    name = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("name", "") or args_obj.get("person", "")).strip(),
                    )[:40]
                    if not name:
                        continue
                    args = {"name": name}
                elif tool == "update_impression":
                    name = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("name", "") or args_obj.get("person", "")).strip(),
                    )[:40]
                    note = str(args_obj.get("note", "") or args_obj.get("content", "") or args_obj.get("text", "")).strip()[:1200]
                    source = str(args_obj.get("source", "") or "").strip()[:80]
                    if (not name) or (not note):
                        continue
                    args = {"name": name, "note": note}
                    if source:
                        args["source"] = source
                elif tool == "read_chat_history":
                    chat_title = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("chat_title", "") or args_obj.get("title", "")).strip(),
                    )[:80]
                    limit_raw = args_obj.get("limit", 50)
                    try:
                        limit = int(limit_raw)
                    except Exception:
                        limit = 50
                    args = {"chat_title": chat_title, "limit": max(1, min(100, limit))}
                elif tool == "read_chat_history_by_date":
                    chat_title = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("chat_title", "") or args_obj.get("title", "")).strip(),
                    )[:80]
                    if not chat_title:
                        continue
                    date_value = str(args_obj.get("date", "") or args_obj.get("day", "")).strip()[:20]
                    max_items_raw = args_obj.get("max_items", args_obj.get("limit", 400))
                    max_chars_raw = args_obj.get("max_chars", 12000)
                    try:
                        max_items = int(max_items_raw)
                    except Exception:
                        max_items = 400
                    try:
                        max_chars = int(max_chars_raw)
                    except Exception:
                        max_chars = 12000
                    args = {
                        "chat_title": chat_title,
                        "date": date_value,
                        "max_items": max(1, min(800, max_items)),
                        "max_chars": max(1200, min(20000, max_chars)),
                    }
                elif tool == "summarize_chat_history":
                    chat_title = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("chat_title", "") or args_obj.get("title", "")).strip(),
                    )[:80]
                    if not chat_title:
                        continue
                    date_value = str(args_obj.get("date", "") or args_obj.get("day", "")).strip()[:20]
                    max_chars_raw = args_obj.get("max_chars", 7200)
                    try:
                        max_chars = int(max_chars_raw)
                    except Exception:
                        max_chars = 7200
                    args = {
                        "chat_title": chat_title,
                        "date": date_value,
                        "max_chars": max(1200, min(8000, max_chars)),
                    }
                elif tool == "search_chat_history":
                    query = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("query", "")).strip(),
                    )[:120]
                    if not query:
                        continue
                    chat_title = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("chat_title", "") or args_obj.get("title", "")).strip(),
                    )[:80]
                    limit_raw = args_obj.get("limit", 10)
                    context_raw = args_obj.get("context", 2)
                    try:
                        limit = int(limit_raw)
                    except Exception:
                        limit = 10
                    try:
                        context = int(context_raw)
                    except Exception:
                        context = 2
                    args = {
                        "query": query,
                        "chat_title": chat_title,
                        "limit": max(1, min(30, limit)),
                        "context": max(0, min(5, context)),
                    }
                elif tool == "run_python":
                    code = str(args_obj.get("code", "") or args_obj.get("expression", "")).strip()[:4000]
                    if not code:
                        continue
                    args = {"code": code}
                elif tool == "write_impression":
                    name = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("name", "") or args_obj.get("person", "")).strip(),
                    )[:40]
                    content = str(args_obj.get("content", "") or args_obj.get("text", "")).strip()[:12000]
                    if (not name) or (not content):
                        continue
                    args = {"name": name, "content": content}
                elif tool in {"web_search", "search_web", "search_web_brave", "web_search_volc", "search_web_volc"}:
                    query = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("query", "") or args_obj.get("text", "")).strip(),
                    )[:80]
                    if not query:
                        continue
                    args = {"query": query}
                    if tool in {"search_web", "search_web_brave"} and "proxy" in args_obj:
                        proxy_raw = args_obj.get("proxy")
                        if isinstance(proxy_raw, str):
                            args["proxy"] = proxy_raw.strip().lower() in {"1", "true", "yes", "on"}
                        else:
                            args["proxy"] = bool(proxy_raw)
                elif tool in {"fetch_url", "browse_url"}:
                    url = re.sub(
                        r"\s+",
                        "",
                        str(args_obj.get("url", "") or args_obj.get("href", "")).strip(),
                    )[:1000]
                    if not url:
                        continue
                    args = {"url": url}
                    if "proxy" in args_obj:
                        proxy_raw = args_obj.get("proxy")
                        if isinstance(proxy_raw, str):
                            args["proxy"] = proxy_raw.strip().lower() in {"1", "true", "yes", "on"}
                        else:
                            args["proxy"] = bool(proxy_raw)
                elif tool == "build_wow_character_url":
                    args = {}
                    for src, dst, limit in (
                        ("title", "title", 80),
                        ("chat_title", "title", 80),
                        ("character", "character", 40),
                        ("server", "server", 40),
                        ("player", "player", 40),
                        ("class_name", "class_name", 40),
                        ("class", "class_name", 40),
                    ):
                        value = re.sub(r"\s+", " ", str(args_obj.get(src, "")).strip())[:limit]
                        if value and dst not in args:
                            args[dst] = value
                    if not any(args.get(k) for k in ("character", "server", "player", "class_name")):
                        continue
                elif tool == "query_weather":
                    location = re.sub(
                        r"\s+",
                        " ",
                        str(
                            args_obj.get("location", "")
                            or args_obj.get("city", "")
                            or args_obj.get("place", "")
                        ).strip(),
                    )[:80]
                    args = {}
                    if location:
                        args["location"] = location
                    days_raw = args_obj.get("days", args_obj.get("forecast_days", 3))
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 3
                    args["days"] = max(1, min(30, days))
                    date_value = re.sub(
                        r"\s+",
                        "",
                        str(args_obj.get("date", "") or args_obj.get("target_date", "")).strip(),
                    )[:20]
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
                        args["date"] = date_value
                    if "day_offset" in args_obj:
                        try:
                            offset = int(args_obj.get("day_offset"))
                        except Exception:
                            offset = -1
                        if 0 <= offset <= 29:
                            args["day_offset"] = offset
                    mode = str(args_obj.get("mode", "") or "").strip().lower()
                    if mode not in {"now", "forecast", "both"}:
                        mode = "both"
                    args["mode"] = mode
                elif tool == "generate_image":
                    prompt = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("prompt", "") or args_obj.get("text", "")).strip(),
                    )[:280]
                    if not prompt:
                        continue
                    args = {"prompt": prompt}
                    size_raw = re.sub(r"\s+", "", str(args_obj.get("size", "")).strip().lower())
                    if re.fullmatch(r"\d{2,4}x\d{2,4}", size_raw):
                        args["size"] = size_raw
                elif tool == "edit_image":
                    prompt = re.sub(
                        r"\s+",
                        " ",
                        str(args_obj.get("prompt", "") or args_obj.get("text", "")).strip(),
                    )[:800]
                    if not prompt:
                        continue
                    args = {"prompt": prompt}
                    image_path = str(
                        args_obj.get("image_path", "")
                        or args_obj.get("path", "")
                        or args_obj.get("file_path", "")
                    ).strip()[:500]
                    image_url = str(args_obj.get("image_url", "") or args_obj.get("url", "")).strip()[:1000]
                    size_raw = re.sub(r"\s+", "", str(args_obj.get("size", "")).strip().lower())
                    if image_path:
                        args["image_path"] = image_path
                    if image_url:
                        args["image_url"] = image_url
                    if re.fullmatch(r"\d{2,4}x\d{2,4}", size_raw):
                        args["size"] = size_raw
                elif tool == "maintain_memory":
                    days_raw = args_obj.get("days", 3)
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 3
                    days = max(1, min(14, days))
                    args = {"days": days}
                elif tool == "maintain_person_impressions":
                    days_raw = args_obj.get("days", 30)
                    max_people_raw = args_obj.get("max_people", 6)
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 30
                    try:
                        max_people = int(max_people_raw)
                    except Exception:
                        max_people = 6
                    args = {
                        "days": max(1, min(3650, days)),
                        "max_people": max(1, min(200, max_people)),
                    }
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
            "send_reply": send_reply,
            "task": task,
        }

    def heartbeat_person_impression_digest(
        self,
        *,
        name: str,
        aliases: list[str],
        notes: list[str],
        sessions: list[str],
        facts: list[str],
        events: list[str],
        relations: list[str],
        mentions: int = 0,
    ) -> dict[str, object]:
        if not self.is_enabled():
            return {}

        user_prompt = (
            "你是“人物印象整理器”，需要把聊天中某个人的信息整理成稳定记忆。\n"
            "请只输出 JSON 对象，字段固定为：keywords, impression, facts, events, relations。\n"
            "字段约束：\n"
            "1) keywords: 字符串数组，1-10 项，每项 <=16 字。\n"
            "2) impression: 一句话中文总结，<=220 字。\n"
            "3) facts/events/relations: 字符串数组，每个字段最多 8 项。\n"
            "4) 不要编造，宁缺毋滥；同义内容去重。\n\n"
            f"人物名: {name or '未知'}\n"
            f"别名候选: {', '.join(aliases) if aliases else '无'}\n"
            f"会话备注线索: {' | '.join(notes) if notes else '无'}\n"
            f"来源会话: {', '.join(sessions) if sessions else '无'}\n"
            f"提及频次: {max(0, int(mentions))}\n"
            f"相关事实: {' | '.join(facts) if facts else '无'}\n"
            f"相关事件: {' | '.join(events) if events else '无'}\n"
            f"相关关系: {' | '.join(relations) if relations else '无'}"
        )
        payload: dict[str, object] = {
            "model": self.cfg.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是记忆整理器。只输出 JSON，不要 markdown，不要解释。"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        }
        max_tokens = self._effective_text_max_tokens(self.cfg.max_tokens)
        if max_tokens is not None:
            payload["max_tokens"] = max(220, min(900, max_tokens))
        raw = self._post_chat(payload)
        parsed = self._extract_json_payload(raw)
        if not isinstance(parsed, dict):
            return {}

        def _normalize_list(raw_value: object, *, limit: int, item_limit: int) -> list[str]:
            values: list[object]
            if isinstance(raw_value, list):
                values = raw_value
            elif isinstance(raw_value, str):
                values = re.split(r"[\n,，;；|]+", raw_value)
            else:
                values = []
            out: list[str] = []
            seen: set[str] = set()
            for value in values:
                clean = re.sub(r"\s+", " ", str(value or "")).strip()
                if not clean:
                    continue
                clean = clean[: max(1, int(item_limit))]
                key = clean.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(clean)
                if len(out) >= max(1, int(limit)):
                    break
            return out

        impression = re.sub(
            r"\s+",
            " ",
            str(parsed.get("impression", "") or parsed.get("summary", "")).strip(),
        )[:220]
        keywords = _normalize_list(parsed.get("keywords"), limit=10, item_limit=16)
        facts_out = _normalize_list(parsed.get("facts"), limit=8, item_limit=70)
        events_out = _normalize_list(parsed.get("events"), limit=8, item_limit=90)
        relations_out = _normalize_list(parsed.get("relations"), limit=8, item_limit=90)
        return {
            "keywords": keywords,
            "impression": impression,
            "facts": facts_out,
            "events": events_out,
            "relations": relations_out,
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
