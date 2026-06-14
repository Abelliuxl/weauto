"""HTTP request handlers (read-only API + SSE log stream).

All endpoints are GET and read-only, per the agreed scope. Data sources:
* process / bridge status  -> ``BotSupervisor.status()``
* chat sessions & messages -> ``wechat_rpa.agent_store.ChatHistoryStore``
* live log tail            -> ``BotSupervisor`` ring buffer

We import ``ChatHistoryStore`` only for its pure file-reading helpers
(``load_index`` / ``read_recent``); no bot instance is created.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from ..supervisor import BotSupervisor

# Optional, best-effort import of the bot's read-only store. We never
# instantiate the bot; we only call its pure file-reading helpers.
try:
    from wechat_rpa.agent_store import ChatHistoryStore  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - only happens if wechat_rpa is not importable
    ChatHistoryStore = None  # type: ignore[assignment,misc]


class Handlers:
    """Routes a parsed request to a handler that returns raw bytes."""

    def __init__(self, supervisor: BotSupervisor, static_dir: Path) -> None:
        self.sup = supervisor
        self.static_dir = static_dir
        self._chat_base = supervisor.project_root / "data" / "chat_history"

    # ------------------------------------------------------------------ #
    def handle(
        self, method: str, path: str, query: dict[str, list[str]], headers: dict[str, str]
    ) -> tuple[int, bytes, str, bytes | None]:
        """Return (status, content_type, ...). SSE returns a generator instead."""
        if method != "GET":
            return self._text(405, "Method Not Allowed")
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._serve_static("app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._serve_static("style.css", "text/css; charset=utf-8")
        if path == "/favicon.ico":
            return self._no_content()

        if path == "/api/status":
            return self._json(self.sup.status().to_dict())

        if path == "/api/sessions":
            return self._json(self._sessions())

        if path == "/api/messages":
            return self._json(self._messages(query))

        if path == "/api/logs/recent":
            return self._json(self._logs_recent(query))

        if path == "/api/logs/stream":
            # Signal SSE via a sentinel return; the server handles long-lived.
            return (200, "text/event-stream", "", _SSE_SENTINEL)

        if path == "/api/control" and method == "GET":
            # Read-only: just report current auto_restart + running state.
            st = self.sup.status()
            return self._json({"auto_restart": st.auto_restart, "running": st.running})

        return self._text(404, "Not Found")

    # ------------------------------------------------------------------ #
    # API implementations
    # ------------------------------------------------------------------ #
    def _sessions(self) -> dict[str, Any]:
        if ChatHistoryStore is None:
            return {"sessions": [], "note": "chat store unavailable"}
        store = ChatHistoryStore(base_dir=self._chat_base)
        try:
            index = store.load_index()
        except Exception:
            index = {"sessions": {}, "aliases": {}}
        sessions_raw = index.get("sessions", {}) if isinstance(index, dict) else {}
        aliases = index.get("aliases", {}) if isinstance(index, dict) else {}
        now = time.time()
        out: list[dict[str, Any]] = []
        for key, meta in sessions_raw.items():
            if not isinstance(meta, dict):
                continue
            titles = meta.get("titles") or []
            updated_at = _as_float(meta.get("updated_at"))
            out.append(
                {
                    "key": key,
                    "dir": meta.get("dir") or key,
                    "title": titles[0] if titles else key,
                    "titles": titles,
                    "muted": bool(meta.get("muted", False)),
                    "message_count": int(meta.get("message_count", 0) or 0),
                    "summary": str(meta.get("summary", "")),
                    "updated_at": updated_at,
                    "updated_ago_sec": (now - updated_at) if updated_at else None,
                }
            )
        out.sort(key=lambda s: (s.get("updated_at") or 0), reverse=True)
        return {"sessions": out, "aliases": aliases, "count": len(out)}

    def _messages(self, query: dict[str, list[str]]) -> dict[str, Any]:
        session = _first(query, "session", "").strip()
        limit = _first_int(query, "limit", 100)
        if not session:
            return {"messages": [], "error": "missing 'session' param"}
        if ChatHistoryStore is None:
            return {"messages": [], "error": "chat store unavailable"}
        store = ChatHistoryStore(base_dir=self._chat_base)
        # The index "key" (e.g. "群临沧") may differ from the on-disk dir name
        # (e.g. "群-临沧"), and read_recent() derives the dir via _safe_name(key)
        # which won't match. Resolve the real dir from the index first, then
        # fall back to the raw key/dirs that actually exist on disk.
        dir_name = self._resolve_session_dir(store, session)
        try:
            records = store.read_recent(dir_name, limit=max(1, min(500, limit)))
        except Exception as exc:  # noqa: BLE001
            return {"messages": [], "error": str(exc)}
        # Resolve a friendly title for the header.
        meta = store.load_meta(session) or {}
        titles = meta.get("titles") or [session]
        return {
            "session": session,
            "title": titles[0] if titles else session,
            "messages": records,
            "count": len(records),
        }

    def _resolve_session_dir(self, store: "ChatHistoryStore", session: str) -> str:
        """Return the on-disk directory name for a session key.

        Tries, in order: the index ``dir`` field for this key, the key itself,
        and any known dir whose stored key matches. Falls back to the raw
        session string so read_recent() can still attempt a lookup.
        """
        try:
            index = store.load_index()
        except Exception:
            index = {}
        sessions = index.get("sessions", {}) if isinstance(index, dict) else {}
        # Direct hit on the key.
        meta = sessions.get(session)
        if isinstance(meta, dict) and meta.get("dir"):
            return str(meta["dir"])
        # Reverse lookup: maybe `session` is already a dir name.
        for key, m in sessions.items():
            if not isinstance(m, dict):
                continue
            if m.get("dir") == session or key == session:
                return str(m.get("dir") or key)
        return session

    def _logs_recent(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = _first_int(query, "lines", 500)
        tag = _first(query, "tag", "").strip().lower()
        items = self.sup.recent_lines(limit=max(1, min(int(self.sup.cfg.log_ring_lines), limit)))
        lines: list[dict[str, Any]] = []
        for seq, text in items:
            if tag and f"[{tag}]" not in text:
                continue
            lines.append({"seq": seq, "text": text})
        return {"lines": lines, "count": len(lines)}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _json(self, obj: Any) -> tuple[int, bytes, str, bytes]:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        return (200, "application/json; charset=utf-8", "", body)

    def _text(self, status: int, text: str) -> tuple[int, bytes, str, bytes]:
        return (status, "text/plain; charset=utf-8", "", text.encode("utf-8"))

    def _no_content(self) -> tuple[int, bytes, str, bytes]:
        return (204, "text/plain", "", b"")

    def _serve_static(self, name: str, ctype: str) -> tuple[int, bytes, str, bytes]:
        path = (self.static_dir / name).resolve()
        try:
            path.relative_to(self.static_dir.resolve())
        except ValueError:
            return self._text(403, "Forbidden")
        if not path.is_file():
            return self._text(404, "Not Found")
        return (200, ctype, "", path.read_bytes())


# Sentinel returned for SSE so the server layer can switch to streaming mode.
_SSE_SENTINEL = b"__SSE__"


def run_sse_stream(
    supervisor: BotSupervisor,
    write: Callable[[bytes], None],
    stop_check: Callable[[], bool],
    heartbeat_sec: float = 15.0,
) -> None:
    """Drive one SSE connection until the client disconnects.

    Implements a simple long-poll over the supervisor's ring buffer: each loop
    waits for new lines (or a heartbeat timeout), then emits them as SSE
    ``data:`` events. We also send periodic ``: ping`` comments to keep the
    connection alive through proxies.
    """
    # Start from the latest known sequence so a freshly opened client doesn't
    # get a flood of history (history is available via /api/logs/recent).
    items = supervisor.recent_lines(limit=1)
    after_seq = items[-1][0] if items else 0
    write(b": connected\n\n")
    next_heartbeat = time.time() + heartbeat_sec
    while not stop_check():
        try:
            fresh = supervisor.wait_for_lines(after_seq, timeout=2.0)
        except Exception:
            fresh = []
        now = time.time()
        if fresh:
            after_seq = fresh[-1][0]
            for seq, text in fresh:
                payload = json.dumps({"seq": seq, "text": text}, ensure_ascii=False)
                # SSE data may not contain raw newlines; split them.
                for line in payload.splitlines() or [payload]:
                    write(f"data: {line}\n".encode("utf-8"))
                write(b"\n")
        if now >= next_heartbeat:
            write(b": ping\n\n")
            next_heartbeat = now + heartbeat_sec


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    vals = query.get(key)
    return vals[0] if vals else default


def _first_int(query: dict[str, list[str]], key: str, default: int) -> int:
    raw = _first(query, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_request_target(target: str) -> tuple[str, dict[str, list[str]]]:
    """Split an HTTP request target into (path, query)."""
    parts = urlsplit(target)
    query = parse_qs(parts.query, keep_blank_values=True)
    return parts.path or "/", query
