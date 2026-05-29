from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from .config import AppConfig


@dataclass
class BridgeResult:
    reply: str
    send: bool = True
    raw: dict[str, Any] | None = None


class BridgeClient:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def enabled(self) -> bool:
        if self.cfg.processing_mode != "bridge":
            return False
        if self.cfg.bridge_backend == "openclaw":
            return bool(self.cfg.bridge_openclaw_gateway_url.strip())
        return bool(self.cfg.bridge_url.strip())

    @staticmethod
    def _compact(value: str, limit: int = 500) -> str:
        clean = re.sub(r"\s+", " ", value or "").strip()
        return clean[:limit]

    @staticmethod
    def _choice_message(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                    elif isinstance(item, str):
                        parts.append(item)
                return "\n".join(parts)
        text = first.get("text")
        return text if isinstance(text, str) else ""

    @classmethod
    def _extract_reply(cls, data: dict[str, Any]) -> str:
        for key in ("reply", "message", "text", "content", "answer"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        replies = data.get("replies")
        if isinstance(replies, list):
            parts = [str(x).strip() for x in replies if str(x).strip()]
            if parts:
                return "\n".join(parts)
        return cls._choice_message(data)

    @staticmethod
    def _extract_openclaw_text(data: dict[str, Any]) -> str:
        result = data.get("result")
        if isinstance(result, dict):
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                parts: list[str] = []
                for item in payloads:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                if parts:
                    return "\n".join(parts)
            meta = result.get("meta")
            if isinstance(meta, dict):
                for key in ("finalAssistantVisibleText", "finalAssistantRawText"):
                    value = meta.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        for key in ("text", "message", "summary"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _extract_send(data: dict[str, Any]) -> bool:
        for key in ("send", "send_reply", "should_reply"):
            if key in data:
                return bool(data.get(key))
        return True

    def request_reply(self, payload: dict[str, Any]) -> BridgeResult:
        if self.cfg.bridge_backend == "openclaw":
            return self._request_openclaw_reply(payload)
        return self._request_http_reply(payload)

    def _request_http_reply(self, payload: dict[str, Any]) -> BridgeResult:
        url = self.cfg.bridge_url.strip()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "weauto-bridge/1",
        }
        if self.cfg.bridge_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.bridge_api_key}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        start = time.monotonic()
        try:
            with urllib.request.urlopen(
                req,
                timeout=float(self.cfg.bridge_timeout_sec),
                context=ssl.create_default_context(),
            ) as resp:
                raw = resp.read(2_000_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read(2000).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"bridge http error {exc.code}: {self._compact(detail)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"bridge network error: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"bridge returned non-json: {self._compact(raw)}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"bridge returned unsupported json type: {type(data).__name__}")
        reply = self._extract_reply(data).strip()
        send = self._extract_send(data)
        elapsed = time.monotonic() - start
        if elapsed > 3.0:
            data = dict(data)
            data["_elapsed_sec"] = elapsed
        return BridgeResult(reply=reply, send=send, raw=data)

    def _openclaw_prompt(self, payload: dict[str, Any]) -> str:
        return (
            "你是 weauto 的外部回复生成后端。weauto 本地已经完成了微信消息触发、"
            "群聊冷却、私聊/艾特判断和发送控制；除非事件明确不应回复，否则默认要生成回复。\n"
            "请根据下面 JSON 里的 message/context/chat 生成要发送到微信的最终文本。\n"
            "只输出最终回复文本，不要输出 JSON、Markdown 代码块或解释。"
            "如果确实不应发送，才输出 NO_REPLY。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    @staticmethod
    def _session_key_fragment(payload: dict[str, Any]) -> str:
        chat = payload.get("chat")
        raw = ""
        if isinstance(chat, dict):
            raw = str(chat.get("session_key") or chat.get("title") or "").strip()
        if not raw:
            raw = "unknown"
        clean = re.sub(r"[^0-9A-Za-z_.:-]+", "-", raw).strip("-")
        return clean[:120] or "unknown"

    def _openclaw_session_key(self, payload: dict[str, Any]) -> str:
        prefix = self.cfg.bridge_openclaw_session_prefix.strip().rstrip(":")
        return f"{prefix}:{self._session_key_fragment(payload)}"

    def _openclaw_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.cfg.bridge_openclaw_strip_proxy_env:
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                env.pop(key, None)
            no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
            parts = [x.strip() for x in no_proxy.split(",") if x.strip()]
            for item in ("127.0.0.1", "localhost", "192.168.0.0/16", "10.0.0.0/8"):
                if item not in parts:
                    parts.append(item)
            env["NO_PROXY"] = ",".join(parts)
            env["no_proxy"] = env["NO_PROXY"]
        return env

    def _request_openclaw_reply(self, payload: dict[str, Any]) -> BridgeResult:
        gateway_url = self.cfg.bridge_openclaw_gateway_url.strip()
        if not gateway_url:
            raise RuntimeError("bridge_openclaw_gateway_url is empty")
        token = self.cfg.bridge_openclaw_gateway_token.strip()
        params: dict[str, Any] = {
            "message": self._openclaw_prompt(payload),
            "sessionKey": self._openclaw_session_key(payload),
            "agentId": self.cfg.bridge_openclaw_agent_id.strip() or "main",
            "deliver": bool(self.cfg.bridge_openclaw_deliver),
            "idempotencyKey": f"weauto-{int(time.time() * 1000)}",
        }
        thinking = self.cfg.bridge_openclaw_thinking.strip()
        if thinking:
            params["thinking"] = thinking

        timeout_ms = int(max(1000.0, float(self.cfg.bridge_timeout_sec) * 1000.0))
        cmd = [
            self.cfg.bridge_openclaw_cli.strip() or "openclaw",
            "gateway",
            "call",
            "agent",
            "--json",
            "--url",
            gateway_url,
            "--timeout",
            str(timeout_ms),
            "--params",
            json.dumps(params, ensure_ascii=False),
        ]
        if token:
            cmd.extend(["--token", token])
        if self.cfg.bridge_openclaw_expect_final:
            cmd.append("--expect-final")

        start = time.monotonic()
        env = self._openclaw_env()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(8.0, float(self.cfg.bridge_timeout_sec) + 8.0),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("openclaw bridge timeout") from exc

        raw = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"openclaw bridge failed: {self._compact(raw, 900)}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"openclaw bridge returned non-json: {self._compact(raw)}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"openclaw bridge returned unsupported json type: {type(data).__name__}")

        reply = self._extract_openclaw_text(data).strip()
        send = bool(reply) and reply.strip().upper() not in {"NO_REPLY", "SKIP_REPLY"}
        elapsed = time.monotonic() - start
        if elapsed > 3.0:
            data = dict(data)
            data["_elapsed_sec"] = elapsed
        return BridgeResult(reply=reply if send else "", send=send, raw=data)
