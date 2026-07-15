from __future__ import annotations

import base64
from dataclasses import dataclass, field
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import AppConfig


PROTOCOL_VERSION = 1


@dataclass
class LongBridgeResult:
    replies: list[str] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)
    send: bool = True

    @property
    def reply(self) -> str:
        return "\n".join(part.strip() for part in self.replies if part.strip()).strip()


@dataclass
class LongBridgeOutbound:
    conversation_id: str
    conversation_title: str
    result: LongBridgeResult


@dataclass
class _PendingTurn:
    request_id: str
    frame: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    replies: list[str] = field(default_factory=list)
    attachments: list[Path] = field(default_factory=list)
    error: str = ""
    send: bool = True


class LongBridgeClient:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._ready = threading.Event()
        self._started = False
        self._thread: threading.Thread | None = None
        self._pending: dict[str, _PendingTurn] = {}
        self._pending_lock = threading.Lock()
        self._recent_completed: dict[str, float] = {}
        self._last_error = ""
        self._last_connected_at = 0.0
        self._outbound_handler: Callable[[LongBridgeOutbound], None] | None = None

    def set_outbound_handler(
        self,
        handler: Callable[[LongBridgeOutbound], None],
    ) -> None:
        self._outbound_handler = handler

    def enabled(self) -> bool:
        return bool(
            self.cfg.processing_mode == "long_bridge"
            and self.cfg.long_bridge_url.strip()
            and self.cfg.long_bridge_token.strip()
        )

    def start(self) -> None:
        if self._started or not self.enabled():
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name="weauto-long-bridge",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._ready.clear()
        self._connected.clear()
        with self._pending_lock:
            pending_turns = list(self._pending.values())
        for pending in pending_turns:
            pending.error = "long bridge client closed"
            pending.done.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def status_text(self) -> str:
        if not self.enabled():
            return "disabled"
        if self._ready.is_set():
            return "connected"
        if self._connected.is_set():
            return "handshaking"
        if self._last_error:
            return f"disconnected ({self._last_error[:160]})"
        return "connecting"

    def request_reply(self, event: dict[str, Any]) -> LongBridgeResult:
        if not self.enabled():
            raise RuntimeError("long bridge is not configured")
        self.start()

        request_id = str(event.get("event_id") or uuid.uuid4())
        frame = {
            "type": "message.create",
            "version": PROTOCOL_VERSION,
            "event_id": request_id,
            "account_id": self.cfg.long_bridge_account_id,
            "message": event.get("message") or {},
        }
        pending = _PendingTurn(request_id=request_id, frame=frame)
        with self._pending_lock:
            now = time.monotonic()
            dedupe_sec = max(
                0.0,
                float(getattr(self.cfg, "long_bridge_inbound_dedupe_sec", 120.0)),
            )
            cutoff = now - dedupe_sec
            self._recent_completed = {
                key: completed_at
                for key, completed_at in self._recent_completed.items()
                if completed_at >= cutoff
            }
            if request_id in self._pending or request_id in self._recent_completed:
                return LongBridgeResult(send=False)
            self._pending[request_id] = pending

        timeout = max(5.0, float(self.cfg.long_bridge_timeout_sec))
        if not pending.done.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise RuntimeError(f"long bridge timeout after {timeout:.1f}s")

        with self._pending_lock:
            self._pending.pop(request_id, None)
        if pending.error:
            raise RuntimeError(pending.error)
        with self._pending_lock:
            self._recent_completed[request_id] = time.monotonic()
        return LongBridgeResult(
            replies=list(pending.replies),
            attachments=list(pending.attachments),
            send=pending.send,
        )

    def build_attachment(self, path_value: str) -> dict[str, Any] | None:
        path = Path(str(path_value or "")).expanduser()
        if not path.is_file():
            return None
        max_bytes = max(1, int(self.cfg.long_bridge_attachment_max_mb)) * 1024 * 1024
        size = path.stat().st_size
        if size > max_bytes:
            raise RuntimeError(
                f"long bridge attachment exceeds {self.cfg.long_bridge_attachment_max_mb}MB: "
                f"{path.name}"
            )
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return {
            "name": path.name,
            "mime_type": mime_type,
            "size": size,
            "data_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    def _run(self) -> None:
        reconnect = max(0.2, float(self.cfg.long_bridge_reconnect_min_sec))
        reconnect_max = max(reconnect, float(self.cfg.long_bridge_reconnect_max_sec))
        while not self._stop.is_set():
            try:
                self._run_connection()
                reconnect = max(0.2, float(self.cfg.long_bridge_reconnect_min_sec))
            except Exception as exc:
                self._last_error = self._compact_error(exc)
                self._ready.clear()
                self._connected.clear()
                print(f"[long-bridge] disconnected: {self._last_error}", flush=True)
                if self._stop.wait(reconnect):
                    break
                reconnect = min(reconnect_max, reconnect * 2.0)

    def _run_connection(self) -> None:
        from websockets.sync.client import connect

        url = self._connection_url()
        headers = {
            "Authorization": f"Bearer {self.cfg.long_bridge_token}",
            "X-Weauto-Account": self.cfg.long_bridge_account_id,
        }
        with connect(
            url,
            additional_headers=headers,
            open_timeout=10.0,
            close_timeout=3.0,
            proxy=None,
            max_size=max(
                2_000_000,
                int(self.cfg.long_bridge_attachment_max_mb) * 1024 * 1024 * 2,
            ),
        ) as websocket:
            self._connected.set()
            self._last_connected_at = time.time()
            self._last_error = ""
            websocket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "version": PROTOCOL_VERSION,
                        "account_id": self.cfg.long_bridge_account_id,
                        "client": "weauto",
                        "capabilities": {
                            "chat_types": ["direct", "group"],
                            "attachments": True,
                            "typing": False,
                        },
                    },
                    ensure_ascii=False,
                )
            )

            last_heartbeat = time.monotonic()
            sent_request_ids: set[str] = set()
            while not self._stop.is_set():
                if self._ready.is_set():
                    self._send_pending(websocket, sent_request_ids)
                now = time.monotonic()
                if now - last_heartbeat >= max(
                    5.0, float(self.cfg.long_bridge_heartbeat_sec)
                ):
                    websocket.send(
                        json.dumps(
                            {"type": "ping", "timestamp_ms": int(time.time() * 1000)}
                        )
                    )
                    last_heartbeat = now
                try:
                    raw = websocket.recv(timeout=0.25)
                except TimeoutError:
                    continue
                if raw is None:
                    raise RuntimeError("long bridge connection closed")
                self._handle_frame(websocket, raw)

    def _send_pending(
        self,
        websocket: Any,
        sent_request_ids: set[str],
    ) -> None:
        with self._pending_lock:
            pending_turns = list(self._pending.values())
        for pending in pending_turns:
            if pending.request_id in sent_request_ids:
                continue
            websocket.send(json.dumps(pending.frame, ensure_ascii=False))
            sent_request_ids.add(pending.request_id)

    def _handle_frame(self, websocket: Any, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return
        frame_type = str(data.get("type") or "")
        if frame_type == "ready":
            if not self._ready.is_set():
                print("[long-bridge] connected", flush=True)
            self._ready.set()
            return
        if frame_type == "ping":
            websocket.send(
                json.dumps(
                    {"type": "pong", "timestamp_ms": int(time.time() * 1000)}
                )
            )
            return
        if frame_type in {"pong", "ack"}:
            return

        request_id = str(data.get("request_id") or data.get("event_id") or "")
        if frame_type == "message.send":
            text = str(data.get("text") or "").strip()
            saved_attachments: list[Path] = []
            for attachment in data.get("attachments") or []:
                saved = self._save_attachment(
                    request_id or str(data.get("message_id") or uuid.uuid4()),
                    attachment,
                )
                if saved:
                    saved_attachments.append(saved)
            message_id = str(data.get("message_id") or "")
            if message_id:
                websocket.send(
                    json.dumps(
                        {
                            "type": "message.received",
                            "message_id": message_id,
                            "request_id": request_id,
                            "ok": True,
                        }
                    )
                )
            with self._pending_lock:
                pending = self._pending.get(request_id)
            if pending:
                if text:
                    pending.replies.append(text)
                pending.attachments.extend(saved_attachments)
            else:
                self._dispatch_proactive(
                    data,
                    LongBridgeResult(
                        replies=[text] if text else [],
                        attachments=saved_attachments,
                    ),
                )
            return

        if not request_id:
            return
        with self._pending_lock:
            pending = self._pending.get(request_id)
        if not pending:
            return

        if frame_type == "turn.complete":
            status = str(data.get("status") or "ok")
            pending.send = bool(data.get("send", True))
            if status != "ok":
                pending.error = str(data.get("error") or f"remote turn {status}")
            pending.done.set()
            return

        if frame_type == "error":
            pending.error = str(data.get("message") or "remote bridge error")
            pending.done.set()

    def _dispatch_proactive(
        self,
        data: dict[str, Any],
        result: LongBridgeResult,
    ) -> None:
        if not self._outbound_handler or not (result.reply or result.attachments):
            return
        conversation = data.get("conversation")
        if not isinstance(conversation, dict):
            conversation = {}
        outbound = LongBridgeOutbound(
            conversation_id=str(conversation.get("id") or data.get("to") or "").strip(),
            conversation_title=str(
                conversation.get("title") or data.get("to") or ""
            ).strip(),
            result=result,
        )
        threading.Thread(
            target=self._outbound_handler,
            args=(outbound,),
            name="weauto-long-bridge-outbound",
            daemon=True,
        ).start()

    def _save_attachment(
        self,
        request_id: str,
        attachment: object,
    ) -> Path | None:
        if not isinstance(attachment, dict):
            return None
        encoded = str(attachment.get("data_base64") or "")
        if not encoded:
            return None
        raw = base64.b64decode(encoded, validate=True)
        max_bytes = max(1, int(self.cfg.long_bridge_attachment_max_mb)) * 1024 * 1024
        if len(raw) > max_bytes:
            raise RuntimeError("remote attachment exceeds configured size limit")
        name = re.sub(r"[^0-9A-Za-z._-]+", "_", str(attachment.get("name") or "file"))
        name = name.strip("._") or "file"
        output_dir = Path("data/long_bridge/received") / request_id
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / name
        if target.exists():
            target = output_dir / f"{uuid.uuid4().hex[:8]}-{name}"
        target.write_bytes(raw)
        return target.resolve()

    def _connection_url(self) -> str:
        raw = self.cfg.long_bridge_url.strip()
        parts = urlsplit(raw)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault("account_id", self.cfg.long_bridge_account_id)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    @staticmethod
    def _compact_error(exc: BaseException) -> str:
        return re.sub(r"\s+", " ", str(exc or exc.__class__.__name__)).strip()[:240]
