from __future__ import annotations

import json
import os
import ssl
import base64
import io
import re
import urllib.error
import urllib.request

import certifi

from PIL import Image

from .config import LlmConfig, VisionConfig


class LlmReplyGenerator:
    def __init__(self, cfg: LlmConfig, vision_cfg: VisionConfig | None = None) -> None:
        self.cfg = cfg
        self.vision_cfg = vision_cfg

    def is_enabled(self) -> bool:
        if not self.cfg.enabled:
            return False
        return bool(self._resolve_api_key())

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

    def _extract_json_payload(self, raw: str):
        text = (raw or "").strip()
        if not text:
            raise RuntimeError("empty json text")

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
            "conversation": {"title": "", "is_group": False},
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
        self, payload: dict, base_url: str, exclude: bool, effort: str
    ) -> tuple[dict, bool]:
        # Prefer provider-level controls to suppress chain-of-thought style outputs.
        if "openrouter.ai" not in (base_url or "").lower():
            return payload, False

        updated = dict(payload)
        reasoning_obj: dict[str, object] = {}
        normalized_effort = (effort or "").strip().lower()
        if normalized_effort in ("low", "medium", "high"):
            reasoning_obj["effort"] = normalized_effort

        # Some models cannot fully disable reasoning output; allow exclude toggle in config.
        if exclude:
            updated["include_reasoning"] = False
            reasoning_obj["exclude"] = True
        if reasoning_obj:
            updated["reasoning"] = reasoning_obj
        return updated, True

    def _request_completion(
        self,
        *,
        url: str,
        api_key: str,
        timeout_sec: float,
        payload: dict,
        label: str,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ssl_ctx) as resp:
                resp_text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{label} http error: {exc.code} {detail}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{label} network error: {exc}")
        return json.loads(resp_text)

    def _extract_content_from_completion(self, data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message", {}) if isinstance(first, dict) else {}

        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
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
                return "\n".join(chunks).strip()

        # Provider compatibility fallback.
        alt_text = first.get("text", "")
        if isinstance(alt_text, str) and alt_text.strip():
            return alt_text.strip()
        return ""

    def _post_chat(self, payload: dict) -> str:
        api_key = self._resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"missing api key: set llm.api_key or env {self.cfg.api_key_env}"
            )
        url = f"{self.cfg.base_url}/chat/completions"
        controlled_payload, controlled = self._apply_reasoning_controls(
            payload,
            self.cfg.base_url,
            exclude=self.cfg.reasoning_exclude,
            effort=self.cfg.reasoning_effort,
        )
        data = self._request_completion(
            url=url,
            api_key=api_key,
            timeout_sec=self.cfg.timeout_sec,
            payload=controlled_payload,
            label="llm",
        )
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

    def _post_chat_vision(self, payload: dict) -> str:
        if not self.vision_cfg:
            raise RuntimeError("vision config missing")
        api_key = self._resolve_vision_api_key()
        if not api_key:
            raise RuntimeError(
                f"missing vision api key: set vision.api_key or env {self.vision_cfg.api_key_env}"
            )
        url = f"{self.vision_cfg.base_url}/chat/completions"
        controlled_payload, controlled = self._apply_reasoning_controls(
            payload,
            self.vision_cfg.base_url,
            exclude=self.vision_cfg.reasoning_exclude,
            effort=self.vision_cfg.reasoning_effort,
        )
        data = self._request_completion(
            url=url,
            api_key=api_key,
            timeout_sec=self.vision_cfg.timeout_sec,
            payload=controlled_payload,
            label="vision",
        )
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

    def parse_chat_from_image(
        self,
        image: Image.Image,
        title: str,
    ) -> dict:
        if not self.vision_cfg or not self.vision_cfg.enabled:
            raise RuntimeError("vision disabled")
        if not self.vision_cfg.model:
            raise RuntimeError("vision model missing")

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        data_url = f"data:image/png;base64,{b64}"

        user_text = (
            f"会话标题: {title or '未知'}\n"
            "请阅读截图并输出严格 JSON（不能有 markdown 代码块）。\n"
            "schema 必须为 wechat_vision_v1，格式如下：\n"
            "{\n"
            "  \"schema\":\"wechat_vision_v1\",\n"
            "  \"conversation\":{\"title\":\"...\",\"is_group\":true},\n"
            "  \"last_message\":{\"role\":\"user|assistant|unknown\",\"content_type\":\"text|image|mixed|unknown\",\"text\":\"...\",\"sender\":\"...\",\"is_mention_me\":false},\n"
            "  \"recent_messages\":[\n"
            "    {\"role\":\"user|assistant|unknown\",\"content_type\":\"text|image|mixed|unknown\",\"text\":\"...\",\"sender\":\"...\",\"is_mention_me\":false}\n"
            "  ],\n"
            "  \"confidence\":0.0\n"
            "}\n"
            "规则：\n"
            "1) 绿色气泡是 assistant(我方)，白色/灰色是 user(他方)。\n"
            "2) recent_messages 最多10条，按时间从旧到新。\n"
            "3) 图片消息 text 用 [图片] 表示，mixed 可带文字摘要。\n"
            "4) 看不清就填 unknown 或空串，不要编造。"
        )
        payload = {
            "model": self.vision_cfg.model,
            "temperature": 0.0,
            "max_tokens": self.vision_cfg.max_tokens,
            "response_format": {"type": "json_object"},
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
            rescue_payload = {
                "model": self.vision_cfg.model,
                "temperature": 0.0,
                "max_tokens": max(320, min(self.vision_cfg.max_tokens, 640)),
                "response_format": {"type": "json_object"},
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

        def _norm_role(v: str) -> str:
            x = (v or "").strip().lower()
            if x in ("assistant", "self", "bot", "a"):
                return "assistant"
            if x in ("user", "other", "u", "human"):
                return "user"
            return "unknown"

        def _norm_type(v: str) -> str:
            x = (v or "").strip().lower()
            if x in ("text", "image", "mixed"):
                return x
            return "unknown"

        recent_structured: list[dict] = []
        raw_recent = data.get("recent_messages") or []
        if isinstance(raw_recent, list):
            for item in raw_recent:
                if not isinstance(item, dict):
                    s = str(item).strip()
                    if not s:
                        continue
                    role = "user" if s.startswith("U:") else ("assistant" if s.startswith("A:") else "unknown")
                    text = s.split(":", 1)[1].strip() if ":" in s else s
                    recent_structured.append(
                        {
                            "role": role,
                            "content_type": "unknown",
                            "text": text[:220],
                            "sender": "",
                            "is_mention_me": False,
                        }
                    )
                    continue

                role = _norm_role(str(item.get("role", "")))
                content_type = _norm_type(str(item.get("content_type", "")))
                text = str(item.get("text", "")).strip()
                sender = str(item.get("sender", "")).strip()
                mention = bool(item.get("is_mention_me", False))
                if content_type == "image" and not text:
                    text = "[图片]"
                recent_structured.append(
                    {
                        "role": role,
                        "content_type": content_type,
                        "text": text[:220],
                        "sender": sender[:40],
                        "is_mention_me": mention,
                    }
                )
        recent_structured = recent_structured[-10:]

        last_raw = data.get("last_message") or {}
        if isinstance(last_raw, dict):
            last_role = _norm_role(str(last_raw.get("role", "")))
            last_type = _norm_type(str(last_raw.get("content_type", "")))
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

        # Keep compatibility with existing history interface (U:/A: strings).
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

    def should_reply(
        self,
        title: str,
        preview: str,
        reason: str,
        is_group: bool,
        chat_context: str = "",
        session_context: str = "",
    ) -> tuple[bool, str]:
        user_prompt = (
            f"会话类型: {'群聊' if is_group else '私聊'}\n"
            f"触发原因: {reason}\n"
            f"会话标题: {title or '未知'}\n"
            f"最新预览: {preview or '无'}\n"
            f"最近聊天内容: {chat_context or '无'}\n"
            f"该会话历史上下文: {session_context or '无'}\n"
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

    def generate(
        self,
        title: str,
        preview: str,
        reason: str,
        chat_context: str = "",
        session_context: str = "",
        avoid_replies: list[str] | None = None,
    ) -> str:
        avoid_replies = avoid_replies or []
        avoid_txt = ""
        if avoid_replies:
            clipped = [re.sub(r"\s+", " ", x).strip()[:90] for x in avoid_replies if x and x.strip()]
            clipped = clipped[:5]
            if clipped:
                avoid_txt = (
                    "\n请避免与下列你最近回复重复（措辞和语义都要明显不同）：\n- "
                    + "\n- ".join(clipped)
                )
        user_prompt = (
            f"触发原因: {reason}\n"
            f"会话标题: {title or '未知'}\n"
            f"最新预览: {preview or '无'}\n"
            f"最近聊天内容: {chat_context or '无'}\n"
            f"该会话历史上下文: {session_context or '无'}\n"
            "请直接输出回复内容，不要解释。"
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
