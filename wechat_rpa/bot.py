from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import builtins
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request

import numpy as np
import pyautogui
import pyperclip

from .config import AppConfig
from .detector import ChatRowState, detect_chat_rows
from .llm import LlmReplyGenerator, prepare_terminal_for_log_line
from .ocr import OcrEngine
from .workspace_context import WorkspaceContextManager
from .window import WindowNotFoundError, get_front_window_bounds, screenshot_region

pyautogui.PAUSE = 0.1

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

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
    (re.compile(r"^\[start\]"), "\033[96m"),
    (re.compile(r"^\[init\]"), "\033[96m"),
    (re.compile(r"^\[cycle\]"), "\033[94m"),
    (re.compile(r"^\[row\]"), "\033[90m"),
    (re.compile(r"^\[event\]"), "\033[95m"),
    (re.compile(r"^\[focus-"), "\033[36m"),
    (re.compile(r"^\[ctx\]"), "\033[97m"),
    (re.compile(r"^\[ocr"), "\033[96m"),
    (re.compile(r"^\[vision\]"), "\033[36m"),
    (re.compile(r"^\[llm\]"), "\033[35m"),
    (re.compile(r"^\[agent\]"), "\033[96m"),
    (re.compile(r"^\[heartbeat\]"), "\033[96m"),
    (re.compile(r"^\[action\]"), "\033[92m"),
    (re.compile(r"^\[sent\]"), "\033[92m"),
    (re.compile(r"^\[dry-run\]"), "\033[93m"),
    (re.compile(r"^\[reply-repeat\]"), "\033[33m"),
    (re.compile(r"^\[memory"), "\033[32m"),
    (re.compile(r"^\[admin-cmd\]"), "\033[95m"),
    (re.compile(r"^\[skip-"), "\033[90m"),
    (re.compile(r"^\[idle\]"), "\033[90m"),
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
        prepare_terminal_for_log_line()
        merged = _colorize_log_line(merged)
    out_kwargs = dict(kwargs)
    out_kwargs["sep"] = ""
    return _BUILTIN_PRINT(merged, **out_kwargs)


@dataclass
class RowMemory:
    session_key: str
    fingerprint: str
    preview_norm: str
    last_sent_norm: str
    has_unread_badge: bool
    pending_unread: bool
    pending_normal: bool
    has_mention: bool
    last_replied_at: float


@dataclass
class ChatContextSnapshot:
    text: str
    last_side: str
    last_line: str
    last_user_message: str = ""
    recent_messages: list[str] | None = None
    recent_structured: list[dict] | None = None
    chat_records: list[dict] | None = None
    memory_summary: str = ""
    memory_time_hints: list[str] | None = None
    memory_people: list[dict] | None = None
    memory_facts: list[str] | None = None
    memory_events: list[str] | None = None
    memory_relations: list[dict] | None = None
    environment_text: str = ""
    schema: str = ""
    source: str = "vision"


@dataclass
class FocusResult:
    bounds: "WindowBounds"
    matched: bool
    resolved_row: ChatRowState | None
    seen_header: str = ""


@dataclass
class SessionState:
    short: list[str]
    history: list[dict]
    summary: str
    muted: bool
    titles: set[str]
    loaded: bool = True


class WeChatGuiRpaBot:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.ocr_engine = OcrEngine(cfg.ocr, log_fn=print)
        self.llm_reply = LlmReplyGenerator(cfg.llm_reply, cfg.vision)
        self.llm_decision = LlmReplyGenerator(cfg.llm_decision, cfg.vision)
        self.llm_planner = LlmReplyGenerator(cfg.llm_planner, cfg.vision)
        self.llm_summary = LlmReplyGenerator(cfg.llm_summary, cfg.vision)
        self.llm_heartbeat = LlmReplyGenerator(cfg.llm_heartbeat, cfg.vision)
        self.llm = self.llm_reply
        self._baseline: dict[int, RowMemory] = {}
        # title_key -> (normalized_sent_text, sent_ts)
        self._sent_by_title: dict[str, tuple[str, float]] = {}
        self._sessions: dict[str, SessionState] = {}
        self._session_aliases: dict[str, str] = {}
        self._summary_turn_counter: dict[str, int] = defaultdict(int)
        self._memory_dirty = False
        self._memory_path = Path(self.cfg.memory_store_path)
        self._memory_session_dir = self._memory_path.parent / f"{self._memory_path.stem}.sessions"
        self._session_index: dict[str, dict] = {}
        self._last_normal_reply_at = 0.0
        self._workspace = WorkspaceContextManager(
            self.cfg.workspace_dir,
            enabled=self.cfg.workspace_enabled,
            embedding_cfg=self.cfg.embedding,
        )
        self._workspace.ensure_bootstrap_files()
        self._cycle = 0
        self._idle_streak = 0
        self._skip_first_action_pending = bool(self.cfg.skip_first_action_on_start)
        self._last_heartbeat_at = 0.0
        self._last_activity_at = 0.0
        self._load_persistent_memory()

    def _to_np_rgb(self, pil_image) -> np.ndarray:
        return np.asarray(pil_image.convert("RGB"), dtype=np.uint8)

    def _normalize_preview(self, text: str) -> str:
        s = re.sub(r"\s+", "", text or "")
        # Suppress OCR jitter from punctuation/ellipsis differences.
        s = re.sub(r"[.…·•,，:：;；\-—_]+", "", s)
        return s

    def _strip_preview_decorations(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        # Remove marker prefixes like [有人@我] / [Photo] / [You were mentioned]
        for _ in range(3):
            m = re.match(r"^\s*[\[【][^\]】]{1,28}[\]】]\s*", raw)
            if not m:
                break
            raw = raw[m.end() :].strip()

        sep = "：" if "：" in raw else (":" if ":" in raw else "")
        if not sep:
            return raw

        left, right = raw.split(sep, 1)
        sender = left.strip(" []【】()（）")
        if 1 <= len(sender) <= 24:
            return right.strip()
        return raw

    def _display_width(self, text: str) -> int:
        width = 0
        for ch in str(text):
            if ch == "\t":
                width += 4
                continue
            if unicodedata.east_asian_width(ch) in {"W", "F"}:
                width += 2
            else:
                width += 1
        return width

    def _fit_col(self, text: str, width: int) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if width <= 0:
            return clean
        cur_w = self._display_width(clean)
        if cur_w <= width:
            return clean + (" " * (width - cur_w))

        suffix = "..."
        suffix_w = self._display_width(suffix)
        out = ""
        out_w = 0
        for ch in clean:
            ch_w = 2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1
            if out_w + ch_w + suffix_w > width:
                break
            out += ch
            out_w += ch_w
        out += suffix
        out_w += suffix_w
        return out + (" " * max(0, width - out_w))

    @staticmethod
    def _yn(value: bool) -> str:
        return "Y" if value else "N"

    def _term_width(self) -> int:
        forced = (os.getenv("WEAUTO_LOG_WIDTH", "") or "").strip()
        if forced.isdigit():
            return max(60, int(forced))
        cols_env = (os.getenv("COLUMNS", "") or "").strip()
        if cols_env.isdigit():
            return max(60, int(cols_env))
        try:
            return max(60, int(shutil.get_terminal_size((140, 24)).columns))
        except Exception:
            return 140

    def _title_key(self, title: str) -> str:
        t = self._normalize_preview(title)
        t = re.sub(r"\d{1,2}:\d{2}", "", t)
        return t[:24]

    def _memory_session_relpath(self, key: str) -> str:
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", key or "").strip("-").lower()
        return f"{self._memory_path.stem}.sessions/{(slug[:80] or 'session')}.json"

    def _memory_session_path(self, key: str, relpath: str = "") -> Path:
        rel = (relpath or self._memory_session_relpath(key)).strip()
        return self._memory_path.parent / rel

    def _session_state_from_index(self, key: str, meta: dict) -> SessionState:
        short = meta.get("short", [])
        titles = meta.get("titles", [])
        return SessionState(
            short=[str(x) for x in short][-max(4, self.cfg.memory_short_max_items) :]
            if isinstance(short, list)
            else [],
            history=[],
            summary=str(meta.get("summary", ""))[: max(120, self.cfg.memory_summary_max_chars)],
            muted=bool(meta.get("muted", False)),
            titles=set(str(x) for x in titles) if isinstance(titles, list) else set(),
            loaded=False,
        )

    def _normalize_history_items(self, history: object) -> list[dict]:
        history_items: list[dict] = []
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
                if not text:
                    continue
                history_items.append(
                    {
                        "role": str(item.get("role", "unknown")).strip().lower(),
                        "content_type": str(item.get("content_type", "unknown")).strip().lower(),
                        "text": text[:220],
                        "sender": str(item.get("sender", "")).strip()[:40],
                        "source": str(item.get("source", "memory")).strip()[:20],
                        "observed_at": int(item.get("observed_at", 0) or 0),
                    }
                )
        return history_items

    def _load_session_payload(self, key: str, sess: SessionState) -> None:
        meta = self._session_index.get(key, {})
        path = self._memory_session_path(key, str(meta.get("path", "")))
        history_items: list[dict] = []
        payload: dict = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[warn] session payload load failed: key={key} path={path} err={exc}")
                payload = {}
        if isinstance(payload, dict):
            history_items = self._normalize_history_items(payload.get("history", []))
            if not sess.short:
                short = payload.get("short", [])
                if isinstance(short, list):
                    sess.short = [str(x) for x in short][-max(4, self.cfg.memory_short_max_items) :]
            if not sess.summary:
                sess.summary = str(payload.get("summary", ""))[: max(120, self.cfg.memory_summary_max_chars)]
            if not sess.titles:
                titles = payload.get("titles", [])
                if isinstance(titles, list):
                    sess.titles = set(str(x) for x in titles)
            if not sess.muted:
                sess.muted = bool(payload.get("muted", False))
        sess.history = (
            history_items[-max(0, self.cfg.memory_history_max_items) :]
            if self.cfg.memory_history_max_items > 0
            else history_items
        )
        sess.loaded = True

    def _get_or_create_session(self, key: str, *, load_history: bool = True) -> SessionState:
        sess = self._sessions.get(key)
        if sess is not None:
            if load_history and (not sess.loaded):
                self._load_session_payload(key, sess)
            return sess
        meta = self._session_index.get(key)
        if isinstance(meta, dict):
            sess = self._session_state_from_index(key, meta)
            self._sessions[key] = sess
            if load_history:
                self._load_session_payload(key, sess)
            return sess
        sess = SessionState(short=[], history=[], summary="", muted=False, titles=set(), loaded=True)
        self._sessions[key] = sess
        return sess

    def _remember_session_alias(self, alias: str, canonical: str) -> None:
        if not alias:
            return
        old = self._session_aliases.get(alias)
        if old == canonical:
            return
        self._session_aliases[alias] = canonical
        self._memory_dirty = True

    def _remember_session_title(self, key: str, title: str) -> None:
        clean = (title or "").strip()
        if not clean:
            return
        sess = self._get_or_create_session(key, load_history=False)
        if clean not in sess.titles:
            sess.titles.add(clean)
            self._memory_dirty = True

    def _canonical_session_key(self, title: str, row_idx: int) -> str:
        key = self._title_key(title)
        if not key:
            return f"row-{row_idx}"
        canonical = self._session_aliases.get(key, key)
        self._remember_session_alias(key, canonical)
        self._remember_session_title(canonical, title)
        return canonical

    def _session_key_for_row(self, row: ChatRowState) -> str:
        return self._canonical_session_key(row.title, row.row_idx)

    def _session_record_key(self, record: dict) -> str:
        role = str(record.get("role", "unknown")).strip().lower()
        content_type = str(record.get("content_type", "unknown")).strip().lower()
        sender = self._normalize_preview(str(record.get("sender", "")))[:40]
        text = self._normalize_preview(str(record.get("text", "")))[:140]
        return f"{role}|{content_type}|{sender}|{text}"

    def _append_session_record(
        self,
        row: ChatRowState,
        *,
        role: str,
        text: str,
        content_type: str = "text",
        sender: str = "",
        source: str = "runtime",
        count_turn: bool = True,
    ) -> None:
        clean = re.sub(r"\s+", " ", text or "").strip()
        if not clean:
            return

        role_map = {
            "U": "user",
            "A": "assistant",
            "user": "user",
            "assistant": "assistant",
        }
        norm_role = role_map.get(role, "unknown")
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        record = {
            "role": norm_role,
            "content_type": (content_type or "text").strip().lower() or "text",
            "text": clean[:220],
            "sender": (sender or "").strip()[:40],
            "source": (source or "runtime").strip()[:20],
            "observed_at": int(time.time()),
        }
        record_key = self._session_record_key(record)
        if sess.history and self._session_record_key(sess.history[-1]) == record_key:
            return

        sess.history.append(record)
        hist_limit = max(0, int(self.cfg.memory_history_max_items))
        if hist_limit > 0 and len(sess.history) > hist_limit:
            del sess.history[:-hist_limit]

        short_role = "A" if norm_role == "assistant" else ("U" if norm_role == "user" else "?")
        item = f"{short_role}:{clean[:140]}"
        if (not sess.short) or sess.short[-1] != item:
            sess.short.append(item)
            max_items = max(4, self.cfg.memory_short_max_items)
            if len(sess.short) > max_items:
                del sess.short[:-max_items]

        self._remember_session_title(key, row.title)
        if norm_role == "user" and count_turn:
            self._summary_turn_counter[key] += 1
        try:
            self._workspace.append_record(
                session_key=key,
                title=row.title,
                role=norm_role,
                text=clean,
            )
            self._workspace.remember_structured(
                session_key=key,
                title=row.title,
                records=[record],
            )
        except Exception as exc:
            if self.cfg.log_verbose:
                print(f"[warn] workspace memory append failed: {exc}")
        self._memory_dirty = True

    def _merge_session_records(
        self,
        row: ChatRowState,
        records: list[dict] | None,
        *,
        source: str = "vision",
    ) -> None:
        if not records:
            return
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        existing_keys = [self._session_record_key(x) for x in sess.history[-40:]]
        incoming: list[dict] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            text = str(record.get("text", "")).strip()
            if not text:
                continue
            incoming.append(
                {
                    "role": str(record.get("role", "unknown")).strip().lower(),
                    "content_type": str(record.get("content_type", "unknown")).strip().lower(),
                    "text": text[:220],
                    "sender": str(record.get("sender", "")).strip()[:40],
                    "source": source,
                }
            )
        if not incoming:
            return

        incoming_keys = [self._session_record_key(x) for x in incoming]
        overlap = 0
        max_overlap = min(len(existing_keys), len(incoming_keys))
        for size in range(max_overlap, 0, -1):
            if existing_keys[-size:] == incoming_keys[:size]:
                overlap = size
                break

        for record in incoming[overlap:]:
            self._append_session_record(
                row,
                role=str(record.get("role", "unknown")),
                text=str(record.get("text", "")),
                content_type=str(record.get("content_type", "unknown")),
                sender=str(record.get("sender", "")),
                source=source,
                count_turn=str(record.get("role", "")) == "user",
            )

    def _append_session_item(
        self,
        row: ChatRowState,
        role: str,
        text: str,
        count_turn: bool = True,
    ) -> None:
        self._append_session_record(
            row,
            role=role,
            text=text,
            content_type="text",
            sender="",
            source="runtime",
            count_turn=count_turn,
        )

    def _update_long_summary(self, row: ChatRowState) -> None:
        if not self.cfg.memory_enabled:
            return
        key = self._session_key_for_row(row)
        turns = self._summary_turn_counter.get(key, 0)
        if turns < max(1, self.cfg.memory_summary_update_every):
            return
        self._summary_turn_counter[key] = 0

        sess = self._get_or_create_session(key)
        short_tail = sess.short[-max(4, self.cfg.memory_summary_recent_items) :]
        if not short_tail:
            return

        prev = sess.summary
        try:
            new_summary = self.llm_summary.summarize_session(
                title=row.title, previous_summary=prev, short_items=short_tail
            )
        except Exception as exc:
            print(f"[warn] summary failed, keep old summary: {exc}")
            new_summary = prev

        if not new_summary:
            new_summary = prev
        if not new_summary and short_tail:
            new_summary = " | ".join(short_tail[-4:])
        new_summary = new_summary[: max(120, self.cfg.memory_summary_max_chars)]
        if new_summary != sess.summary:
            sess.summary = new_summary
            self._memory_dirty = True
            try:
                self._workspace.update_session_summary(
                    session_key=key,
                    title=row.title,
                    summary=sess.summary,
                )
            except Exception as exc:
                if self.cfg.log_verbose:
                    print(f"[warn] workspace summary update failed: {exc}")
            if self.cfg.log_verbose:
                print(
                    f"[memory-summary] key={key!r} size={len(sess.summary)} "
                    f"turns={self.cfg.memory_summary_update_every}"
                )

    def _build_session_context(self, row: ChatRowState) -> str:
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        short_n = max(2, self.cfg.memory_short_context_items)
        short_text = " | ".join(sess.short[-short_n:])
        long_text = (sess.summary or "").strip()
        if long_text and short_text:
            return f"[长期摘要]{long_text}\n[短期上下文]{short_text}"[:1200]
        if long_text:
            return f"[长期摘要]{long_text}"[:1200]
        return short_text[:1200]

    def _build_session_history_text(self, row: ChatRowState) -> str:
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        limit = max(4, int(self.cfg.memory_history_context_items))
        lines: list[str] = []
        for item in sess.history[-limit:]:
            role = str(item.get("role", "unknown"))
            text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
            if not text:
                continue
            prefix = "A" if role == "assistant" else ("U" if role == "user" else "?")
            lines.append(f"{prefix}:{text[:140]}")
        return " | ".join(lines)[:1600]

    def _workspace_context_for_row(self, row: ChatRowState, *, is_admin: bool) -> str:
        include_long_term = is_admin or (not self.cfg.workspace_memory_main_only)
        try:
            return self._workspace.build_prompt_context(include_long_term=include_long_term)
        except Exception as exc:
            if self.cfg.log_verbose:
                print(f"[warn] workspace context load failed: {exc}")
            return ""

    def _workspace_memory_recall_for_row(
        self,
        row: ChatRowState,
        query: str,
        *,
        is_admin: bool,
    ) -> str:
        include_global = is_admin or (not self.cfg.workspace_memory_main_only)
        try:
            session_key = self._session_key_for_row(row)
            brief = self._workspace.build_session_memory_brief(
                session_key=session_key,
                title=row.title,
                query=query,
            )
            raw_hits = self._workspace.search_memory(
                query=query,
                session_key=session_key,
                include_global=include_global,
                limit=max(1, int(self.cfg.workspace_memory_search_limit)),
            )
            parts = []
            if brief:
                parts.append(brief)
            if raw_hits:
                parts.append("[原始记忆片段]\n" + raw_hits)
            return "\n\n".join(parts)[:3600]
        except Exception as exc:
            if self.cfg.log_verbose:
                print(f"[warn] workspace memory search failed: {exc}")
            return ""

    def _apply_session_summary(self, row: ChatRowState, summary: str) -> None:
        clean = re.sub(r"\s+", " ", summary or "").strip()
        if not clean:
            return
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        clipped = clean[: max(120, self.cfg.memory_summary_max_chars)]
        if clipped != sess.summary:
            sess.summary = clipped
            self._memory_dirty = True
        try:
            self._workspace.update_session_summary(
                session_key=key,
                title=row.title,
                summary=clipped,
            )
        except Exception as exc:
            if self.cfg.log_verbose:
                print(f"[warn] workspace summary update failed: {exc}")

    def _apply_workspace_memory_update(self, row: ChatRowState, snapshot: ChatContextSnapshot) -> None:
        if not (
            snapshot.memory_summary
            or snapshot.memory_time_hints
            or snapshot.memory_people
            or snapshot.memory_facts
            or snapshot.memory_events
            or snapshot.memory_relations
        ):
            return
        key = self._session_key_for_row(row)
        events = list(snapshot.memory_events or [])
        if snapshot.memory_time_hints:
            for hint in snapshot.memory_time_hints:
                clean = re.sub(r"\s+", " ", str(hint or "")).strip()
                if not clean:
                    continue
                events.append(f"[时间线索] {clean}")
        try:
            self._workspace.remember_structured(
                session_key=key,
                title=row.title,
                summary=snapshot.memory_summary,
                people=snapshot.memory_people or [],
                facts=snapshot.memory_facts or [],
                events=events,
                relations=snapshot.memory_relations or [],
            )
        except Exception as exc:
            if self.cfg.log_verbose:
                print(f"[warn] workspace structured memory update failed: {exc}")

    def _build_environment_context(self, snapshot: ChatContextSnapshot) -> str:
        parts: list[str] = []
        summary = re.sub(r"\s+", " ", snapshot.memory_summary or "").strip()
        if summary:
            parts.append(f"[环境总结]\n- {summary[:240]}")

        time_hints = [re.sub(r"\s+", " ", str(x or "")).strip() for x in (snapshot.memory_time_hints or [])]
        time_hints = [x[:40] for x in time_hints if x]
        if time_hints:
            parts.append("[时间线索]\n" + "\n".join(f"- {x}" for x in time_hints[:8]))

        people_lines: list[str] = []
        for person in snapshot.memory_people or []:
            if not isinstance(person, dict):
                continue
            name = re.sub(r"\s+", " ", str(person.get("name", ""))).strip()[:24]
            alias = re.sub(r"\s+", " ", str(person.get("alias", ""))).strip()[:24]
            desc = re.sub(r"\s+", " ", str(person.get("description", ""))).strip()[:80]
            if not name and not alias and not desc:
                continue
            line = name or alias or "未知人物"
            tail = []
            if alias and alias != line:
                tail.append(f"别名={alias}")
            if desc:
                tail.append(f"说明={desc}")
            if tail:
                line += "；" + "；".join(tail)
            people_lines.append(f"- {line}")
        if people_lines:
            parts.append("[人物]\n" + "\n".join(people_lines[:8]))

        facts = [re.sub(r"\s+", " ", str(x or "")).strip()[:80] for x in (snapshot.memory_facts or [])]
        facts = [x for x in facts if x]
        if facts:
            parts.append("[事实]\n" + "\n".join(f"- {x}" for x in facts[:8]))

        events = [re.sub(r"\s+", " ", str(x or "")).strip()[:80] for x in (snapshot.memory_events or [])]
        events = [x for x in events if x]
        if events:
            parts.append("[事件]\n" + "\n".join(f"- {x}" for x in events[:8]))

        relation_lines: list[str] = []
        for relation in snapshot.memory_relations or []:
            if not isinstance(relation, dict):
                continue
            subject = re.sub(r"\s+", " ", str(relation.get("subject", ""))).strip()[:24]
            rel = re.sub(r"\s+", " ", str(relation.get("relation", ""))).strip()[:24]
            target = re.sub(r"\s+", " ", str(relation.get("target", ""))).strip()[:24]
            note = re.sub(r"\s+", " ", str(relation.get("note", ""))).strip()[:80]
            if not (subject and rel and target):
                continue
            line = f"- {subject} -> {rel} -> {target}"
            if note:
                line += f"；说明={note}"
            relation_lines.append(line)
        if relation_lines:
            parts.append("[关系]\n" + "\n".join(relation_lines[:8]))

        return "\n\n".join(parts)[:2200]

    def _load_persistent_memory(self) -> None:
        if not self.cfg.memory_enabled:
            return
        if not self._memory_path.exists():
            return
        try:
            raw = json.loads(self._memory_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] memory load failed: {exc}")
            return

        version = int(raw.get("version", 0) or 0)
        sessions = raw.get("sessions", {})
        if isinstance(sessions, dict):
            for key, data in sessions.items():
                if not isinstance(data, dict):
                    continue
                key_str = str(key).strip()
                if not key_str:
                    continue
                if version >= 3:
                    relpath = str(data.get("path", "")).strip() or self._memory_session_relpath(key_str)
                    meta = {
                        "path": relpath,
                        "short": [str(x) for x in (data.get("short", []) or [])][-max(4, self.cfg.memory_short_max_items) :]
                        if isinstance(data.get("short", []), list)
                        else [],
                        "summary": str(data.get("summary", ""))[: max(120, self.cfg.memory_summary_max_chars)],
                        "muted": bool(data.get("muted", False)),
                        "titles": [str(x) for x in (data.get("titles", []) or [])]
                        if isinstance(data.get("titles", []), list)
                        else [],
                        "history_count": int(data.get("history_count", 0) or 0),
                        "updated_at": int(data.get("updated_at", 0) or 0),
                    }
                    self._session_index[key_str] = meta
                    self._sessions[key_str] = self._session_state_from_index(key_str, meta)
                    continue

                short = data.get("short", [])
                history = data.get("history", [])
                titles = data.get("titles", [])
                history_items = self._normalize_history_items(history)
                if (not history_items) and isinstance(short, list):
                    for entry in short:
                        val = str(entry).strip()
                        if not val:
                            continue
                        role = "assistant" if val.startswith("A:") else ("user" if val.startswith("U:") else "unknown")
                        text = val.split(":", 1)[1].strip() if ":" in val else val
                        history_items.append(
                            {
                                "role": role,
                                "content_type": "text",
                                "text": text[:220],
                                "sender": "",
                                "source": "legacy",
                                "observed_at": 0,
                            }
                        )
                sess = SessionState(
                    short=[str(x) for x in short][-max(4, self.cfg.memory_short_max_items) :]
                    if isinstance(short, list)
                    else [],
                    history=history_items[
                        -max(0, self.cfg.memory_history_max_items) :
                    ]
                    if self.cfg.memory_history_max_items > 0
                    else history_items,
                    summary=str(data.get("summary", ""))[: max(120, self.cfg.memory_summary_max_chars)],
                    muted=bool(data.get("muted", False)),
                    titles=set(str(x) for x in titles) if isinstance(titles, list) else set(),
                    loaded=True,
                )
                self._sessions[key_str] = sess
                self._session_index[key_str] = {
                    "path": self._memory_session_relpath(key_str),
                    "short": list(sess.short),
                    "summary": sess.summary,
                    "muted": bool(sess.muted),
                    "titles": sorted(sess.titles),
                    "history_count": len(sess.history),
                    "updated_at": int(time.time()),
                }

        aliases = raw.get("aliases", {})
        if isinstance(aliases, dict):
            for k, v in aliases.items():
                k2 = str(k).strip()
                v2 = str(v).strip()
                if k2 and v2:
                    self._session_aliases[k2] = v2
        self._memory_dirty = False
        print(
            f"[memory] loaded sessions={len(self._sessions)} aliases={len(self._session_aliases)} "
            f"path={self._memory_path}"
        )

    def _save_persistent_memory(self) -> None:
        if not self.cfg.memory_enabled:
            return
        if not self._memory_dirty:
            return
        try:
            self._memory_path.parent.mkdir(parents=True, exist_ok=True)
            self._memory_session_dir.mkdir(parents=True, exist_ok=True)
            now_ts = int(time.time())
            sessions_payload: dict[str, dict] = {}
            all_keys = set(self._session_index.keys()) | set(self._sessions.keys())
            for key in sorted(all_keys):
                sess = self._sessions.get(key)
                if sess is None:
                    meta = self._session_index.get(key)
                    if isinstance(meta, dict):
                        sessions_payload[key] = dict(meta)
                    continue

                relpath = self._memory_session_relpath(key)
                sessions_payload[key] = {
                    "path": relpath,
                    "short": sess.short[-max(4, self.cfg.memory_short_max_items) :],
                    "summary": sess.summary[: max(120, self.cfg.memory_summary_max_chars)],
                    "muted": bool(sess.muted),
                    "titles": sorted(sess.titles),
                    "history_count": len(sess.history),
                    "updated_at": now_ts,
                }
                self._session_index[key] = dict(sessions_payload[key])
                if not sess.loaded:
                    continue
                session_payload = {
                    "version": 1,
                    "saved_at": now_ts,
                    "key": key,
                    "short": sess.short[-max(4, self.cfg.memory_short_max_items) :],
                    "history": sess.history,
                    "summary": sess.summary[: max(120, self.cfg.memory_summary_max_chars)],
                    "muted": bool(sess.muted),
                    "titles": sorted(sess.titles),
                }
                self._memory_session_path(key, relpath).write_text(
                    json.dumps(session_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            payload = {
                "version": 3,
                "saved_at": now_ts,
                "sessions": sessions_payload,
                "aliases": self._session_aliases,
            }
            self._memory_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._memory_dirty = False
        except Exception as exc:
            print(f"[warn] memory save failed: {exc}")

    def _is_preview_meaningfully_changed(self, old: str, new: str) -> bool:
        if not new:
            return False
        if old == new:
            return False
        ratio = SequenceMatcher(a=old, b=new).ratio()
        return ratio < 0.93

    def _is_self_echo(self, preview_norm: str, sent_norm: str) -> bool:
        if not preview_norm or not sent_norm:
            return False

        # Direct contain checks handle truncation from list preview.
        if preview_norm in sent_norm or sent_norm in preview_norm:
            return True

        # Fuzzy match preview vs sent prefix for OCR drift.
        n = min(len(preview_norm), len(sent_norm), 24)
        if n >= 6:
            ratio = SequenceMatcher(a=preview_norm[:n], b=sent_norm[:n]).ratio()
            if ratio >= 0.78:
                return True

        # Full-string fuzzy match for reordered/truncated OCR fragments.
        full_ratio = SequenceMatcher(a=preview_norm, b=sent_norm).ratio()
        if full_ratio >= 0.62:
            return True
        block = SequenceMatcher(a=preview_norm, b=sent_norm).find_longest_match(
            0, len(preview_norm), 0, len(sent_norm)
        )
        common = block.size
        min_len = min(len(preview_norm), len(sent_norm))
        if min_len >= 8 and common >= 8 and (common / max(1, min_len)) >= 0.45:
            return True
        return False

    def _is_preview_refresh_from_self(
        self,
        row: ChatRowState,
        preview_norm: str,
        prev_sent_norm: str,
        recent_sent_norm: str,
    ) -> bool:
        # Only apply to preview-refresh path to avoid suppressing real unread events.
        preview_payload_norm = self._normalize_preview(self._strip_preview_decorations(row.preview))
        preview_norms = [x for x in {preview_norm, preview_payload_norm} if x]
        if not preview_norms:
            return False

        sent_norms: list[str] = []
        for x in (prev_sent_norm, recent_sent_norm):
            if x:
                sent_norms.append(x)
        for txt in self._recent_assistant_replies(row, limit=3):
            n = self._normalize_preview(txt)
            if n:
                sent_norms.append(n)
        if not sent_norms:
            return False

        for pn in preview_norms:
            for sn in sent_norms:
                if self._is_self_echo(pn, sn):
                    return True
        return False

    def _get_recent_sent_for_row(self, row: ChatRowState, now: float) -> str:
        # 3 minutes anti-loop window
        window_sec = 180.0
        key = self._title_key(row.title)
        data = self._sent_by_title.get(key)
        if not data:
            return ""
        sent_norm, ts = data
        if now - ts > window_sec:
            self._sent_by_title.pop(key, None)
            return ""
        return sent_norm

    def _remember_sent_for_row(self, row: ChatRowState, sent_norm: str, now: float) -> None:
        if not sent_norm:
            return
        key = self._title_key(row.title)
        if key:
            self._sent_by_title[key] = (sent_norm, now)

    def _is_group_chat(self, row: ChatRowState) -> bool:
        title = row.title or ""
        for prefix in self.cfg.group_title_prefixes:
            if prefix and title.startswith(prefix):
                return True

        if self.cfg.group_detect_sender_prefix and "：" in (row.preview or ""):
            sender = row.preview.split("：", 1)[0].strip()
            if 1 <= len(sender) <= 24:
                return True

        return False

    def _has_sender_prefix(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False

        sep = "：" if "：" in raw else (":" if ":" in raw else "")
        if not sep:
            return False

        sender = raw.split(sep, 1)[0].strip(" []【】()（）")
        if not (1 <= len(sender) <= 24):
            return False

        # Filter out marker-style prefixes such as [有人@我]
        deny = ["有人@我", "@我", "新消息"]
        if any(x in sender for x in deny):
            return False
        return True

    def _normalize_title_text(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        # Ignore member count suffix like "(28)" in group headers.
        raw = re.sub(r"[（(]\d+[)）]", "", raw)
        raw = re.sub(r"\s+", "", raw)
        raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\-_]", "", raw)
        return raw.lower()

    def _pick_chat_title_region(self, is_group: bool | None) -> "RegionRatio":
        if is_group is True and self.cfg.chat_title_region_group_enabled:
            return self.cfg.chat_title_region_group
        if is_group is False and self.cfg.chat_title_region_private_enabled:
            return self.cfg.chat_title_region_private
        return self.cfg.chat_title_region

    def _extract_chat_header_text(self, bounds: "WindowBounds", is_group: bool | None = None) -> str:
        region = self._pick_chat_title_region(is_group)
        x = bounds.x + int(bounds.width * region.x)
        y = bounds.y + int(bounds.height * region.y)
        w = int(bounds.width * region.w)
        h = int(bounds.height * region.h)
        if w <= 0 or h <= 0:
            return ""
        shot = screenshot_region(x, y, w, h, high_res=True)
        bgr = self._to_np_rgb(shot)[:, :, ::-1]
        lines = self.ocr_engine.detect_lines(bgr)
        if not lines:
            return ""
        valid: list[tuple[float, float, str]] = []
        for line in lines:
            txt = (line.text or "").strip()
            if not txt or _TIME_RE.match(txt):
                continue
            norm = self._normalize_title_text(txt)
            if not norm:
                continue
            valid.append((line.y_center, line.x_center, txt))
        if not valid:
            return ""
        valid.sort(key=lambda it: (it[0], it[1]))
        return " ".join([x[2] for x in valid])[:120]

    def _is_chat_header_matched(self, expected_title: str, actual_header: str) -> bool:
        exp = self._normalize_title_text(expected_title)
        got = self._normalize_title_text(actual_header)
        if not exp:
            return True
        if not got:
            return False
        if exp in got or (len(got) >= 2 and got in exp):
            return True
        ratio = SequenceMatcher(a=exp, b=got).ratio()
        return ratio >= 0.62

    def _is_admin_session(self, row: ChatRowState) -> bool:
        if not self.cfg.admin_commands_enabled:
            return False
        title_key = self._title_key(row.title)
        if not title_key:
            return False
        for t in self.cfg.admin_session_titles:
            if title_key == self._title_key(t):
                return True
        return False

    def _is_immediate_reply_event(self, row: ChatRowState, reason: str) -> bool:
        if reason == "mention":
            return True
        return not self._is_group_chat(row)

    def _normal_reply_interval_active(self) -> bool:
        return float(self.cfg.normal_reply_interval_sec) > 0.0

    def _is_normal_reply_event(self, row: ChatRowState, reason: str) -> bool:
        return (not self._is_immediate_reply_event(row, reason)) and reason == "new_message"

    def _strip_sender_prefix(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        sep = "：" if "：" in raw else (":" if ":" in raw else "")
        if not sep:
            return raw
        left, right = raw.split(sep, 1)
        if 1 <= len(left.strip()) <= 24:
            return right.strip()
        return raw

    def _extract_admin_command_text(
        self,
        row: ChatRowState,
        context_snapshot: ChatContextSnapshot,
    ) -> str:
        prefix = (self.cfg.admin_command_prefix or "/").strip() or "/"
        candidates: list[str] = []
        if context_snapshot.last_user_message:
            candidates.append(context_snapshot.last_user_message)
        if context_snapshot.last_side == "other":
            candidates.append(context_snapshot.last_line)
        candidates.append(self._strip_sender_prefix(row.preview or ""))

        for text in candidates:
            clean = (text or "").strip()
            if clean.startswith(prefix):
                return clean
        return ""

    def _display_session_name(self, key: str) -> str:
        sess = self._sessions.get(key)
        if sess and sess.titles:
            return sorted(sess.titles, key=len)[-1]
        return key

    def _resolve_session_key_by_query(self, query: str) -> str | None:
        q = (query or "").strip()
        if not q:
            return None
        q_key = self._title_key(q)
        if q_key in self._session_aliases:
            return self._session_aliases[q_key]
        if q_key in self._sessions:
            return q_key

        # Fuzzy match by known titles.
        best_key = None
        best_score = 0.0
        for key, sess in self._sessions.items():
            names = set(sess.titles)
            names.add(key)
            for name in names:
                n_key = self._title_key(name)
                if q_key and (q_key in n_key or n_key in q_key):
                    return key
                score = SequenceMatcher(a=q_key or q, b=n_key or name).ratio()
                if score > best_score:
                    best_score = score
                    best_key = key
        if best_score >= 0.72:
            return best_key
        return None

    def _is_row_muted(self, row: ChatRowState) -> bool:
        key = self._session_key_for_row(row)
        sess = self._sessions.get(key)
        return bool(sess and sess.muted)

    def _handle_admin_command(self, cmd_line: str) -> str:
        if not cmd_line:
            return ""

        cmd = cmd_line.strip()
        parts = cmd.split()
        if not parts:
            return ""
        action = parts[0].lower()

        if action in ("/help", "/?"):
            return (
                "可用命令: /sessions, /mute 会话, /unmute 会话, "
                "/reset 会话, /merge 源会话 -> 目标会话, "
                "/remember 长期记忆"
            )

        if action == "/sessions":
            if not self._sessions:
                return "暂无会话缓存。"
            rows = []
            for key, sess in list(self._sessions.items())[:12]:
                tag = "MUTE" if sess.muted else "ON"
                rows.append(f"{tag}:{self._display_session_name(key)}")
            return "会话列表: " + " ; ".join(rows)

        if action in ("/mute", "/unmute", "/reset"):
            if len(parts) < 2:
                return f"用法: {action} 会话名"
            query = " ".join(parts[1:]).strip()
            key = self._resolve_session_key_by_query(query)
            if not key:
                return f"未找到会话: {query}"
            sess = self._get_or_create_session(key)
            if action == "/mute":
                sess.muted = True
                self._memory_dirty = True
                return f"已静音: {self._display_session_name(key)}"
            if action == "/unmute":
                sess.muted = False
                self._memory_dirty = True
                return f"已恢复: {self._display_session_name(key)}"
            sess.short = []
            sess.history = []
            sess.summary = ""
            self._summary_turn_counter[key] = 0
            try:
                self._workspace.reset_session_memory(
                    session_key=key,
                    title=self._display_session_name(key),
                )
            except Exception as exc:
                if self.cfg.log_verbose:
                    print(f"[warn] workspace session reset failed: {exc}")
            self._memory_dirty = True
            return f"已重置记忆: {self._display_session_name(key)}"

        if action == "/merge":
            body = cmd[len(parts[0]) :].strip()
            if "->" in body:
                src_q, dst_q = [x.strip() for x in body.split("->", 1)]
            else:
                merge_parts = body.split()
                if len(merge_parts) < 2:
                    return "用法: /merge 源会话 -> 目标会话"
                src_q, dst_q = merge_parts[0], " ".join(merge_parts[1:])
            src_key = self._resolve_session_key_by_query(src_q)
            dst_key = self._resolve_session_key_by_query(dst_q)
            if not src_key or not dst_key:
                return f"会话未找到: src={src_q}, dst={dst_q}"
            if src_key == dst_key:
                return "源会话和目标会话相同，无需合并。"
            src = self._get_or_create_session(src_key)
            dst = self._get_or_create_session(dst_key)
            src_name = self._display_session_name(src_key)
            dst_name = self._display_session_name(dst_key)
            merged = (dst.short + src.short)[-max(4, self.cfg.memory_short_max_items) :]
            dst.short = merged
            dst.history.extend(src.history)
            hist_limit = max(0, int(self.cfg.memory_history_max_items))
            if hist_limit > 0 and len(dst.history) > hist_limit:
                dst.history = dst.history[-hist_limit:]
            if src.summary and src.summary not in dst.summary:
                glue = " | " if dst.summary else ""
                dst.summary = (dst.summary + glue + src.summary)[
                    : max(120, self.cfg.memory_summary_max_chars)
                ]
            dst.muted = bool(dst.muted or src.muted)
            dst.titles.update(src.titles)

            for alias, key in list(self._session_aliases.items()):
                if key == src_key:
                    self._session_aliases[alias] = dst_key
            self._session_aliases[src_key] = dst_key

            self._sessions.pop(src_key, None)
            self._summary_turn_counter.pop(src_key, None)
            try:
                self._workspace.merge_session_memory(
                    src_key=src_key,
                    dst_key=dst_key,
                    dst_title=dst_name,
                )
            except Exception as exc:
                if self.cfg.log_verbose:
                    print(f"[warn] workspace session merge failed: {exc}")
            self._memory_dirty = True
            return f"已合并: {src_name} -> {dst_name}"

        if action == "/remember":
            body = cmd[len(parts[0]) :].strip()
            if not body:
                return "用法: /remember 需要长期记住的内容"
            try:
                self._workspace.append_long_term_memory(body)
                return "已写入 MEMORY.md"
            except Exception as exc:
                return f"写入长期记忆失败: {exc}"

        return f"未知命令: {cmd_line}"

    def _available_agent_tools(self, *, is_admin: bool) -> list[str]:
        tools = [
            "remember_session_fact",
            "remember_session_event",
            "set_session_summary",
            "search_memory",
        ]
        if self._has_web_search_tool():
            tools.append("web_search")
        if is_admin:
            tools.extend(
                [
                    "remember_long_term",
                    "mute_session",
                    "unmute_session",
                ]
            )
        return tools

    def _resolve_tavily_api_key(self) -> str:
        if self.cfg.tavily_api_key:
            return self.cfg.tavily_api_key
        env_name = (self.cfg.tavily_api_key_env or "").strip()
        if not env_name:
            return ""
        return os.getenv(env_name, "")

    def _has_web_search_tool(self) -> bool:
        return bool(self.cfg.tavily_enabled and self._resolve_tavily_api_key())

    def _web_search_status_text(self) -> str:
        if not self.cfg.tavily_enabled:
            return "disabled (tavily_enabled=false)"
        if self._resolve_tavily_api_key():
            return (
                f"available (tool=web_search provider=tavily "
                f"max_results={max(1, int(self.cfg.tavily_max_results))})"
            )
        env_name = (self.cfg.tavily_api_key_env or "").strip()
        if env_name:
            return f"blocked (missing api key: config.tavily_api_key or env {env_name})"
        return "blocked (missing api key: config.tavily_api_key)"

    def _tavily_search(self, query: str) -> str:
        clean_query = re.sub(r"\s+", " ", query or "").strip()[:120]
        if not clean_query:
            return ""
        api_key = self._resolve_tavily_api_key()
        if not api_key:
            raise RuntimeError("tavily api key missing")

        base = (self.cfg.tavily_base_url or "https://api.tavily.com").rstrip("/")
        url = base if base.endswith("/search") else (base + "/search")
        payload = {
            "api_key": api_key,
            "query": clean_query,
            "search_depth": "basic",
            "max_results": max(1, int(self.cfg.tavily_max_results)),
            "include_answer": True,
            "include_raw_content": False,
        }
        req = urllib.request.Request(
            url=url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            ssl_ctx = ssl.create_default_context()
            with urllib.request.urlopen(
                req,
                timeout=max(1.0, float(self.cfg.tavily_timeout_sec)),
                context=ssl_ctx,
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"tavily http error: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"tavily network error: {exc}") from exc

        data = json.loads(raw)
        lines: list[str] = []
        answer = re.sub(r"\s+", " ", str(data.get("answer", "") or "")).strip()
        if answer:
            lines.append(f"摘要: {answer[:240]}")
        results = data.get("results") if isinstance(data.get("results"), list) else []
        for item in results[: max(1, int(self.cfg.tavily_max_results))]:
            if not isinstance(item, dict):
                continue
            title = re.sub(r"\s+", " ", str(item.get("title", "") or "")).strip()[:90]
            url_item = str(item.get("url", "") or "").strip()[:160]
            content = re.sub(r"\s+", " ", str(item.get("content", "") or "")).strip()[:180]
            row = " | ".join([x for x in [title, url_item, content] if x])
            if row:
                lines.append(row)
        return "\n".join(lines)[:1200]

    def _execute_agent_actions(
        self,
        row: ChatRowState,
        actions: list[dict] | None,
        *,
        is_admin: bool,
        max_actions_override: int | None = None,
    ) -> tuple[str, str]:
        if not actions:
            return "", ""

        key = self._session_key_for_row(row)
        if max_actions_override is None:
            max_actions = max(1, int(self.cfg.agent_actions_max_per_turn))
        else:
            max_actions = max(1, int(max_actions_override))
        traces: list[str] = []
        observations: list[str] = []

        for idx, action in enumerate(actions[:max_actions], start=1):
            if not isinstance(action, dict):
                continue
            tool = str(action.get("tool", "")).strip()
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            reason = str(action.get("reason", "")).strip()[:40]
            status = ""
            obs = ""
            ok = False
            try:
                if tool == "remember_long_term":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        note = re.sub(r"\s+", " ", str(args.get("note", "")).strip())[:200]
                        if not note:
                            status = "skip (empty note)"
                        else:
                            self._workspace.append_long_term_memory(note)
                            status = "ok"
                            obs = f"长期记忆新增: {note}"
                            ok = True
                elif tool == "maintain_memory":
                    days_raw = args.get("days", 3)
                    try:
                        days = int(days_raw)
                    except Exception:
                        days = 3
                    done, detail = self._heartbeat_maintain_memory(days=max(1, min(14, days)))
                    status = "ok" if done else detail
                    obs = detail if done else ""
                    ok = done
                elif tool == "refine_persona_files":
                    done, detail = self._heartbeat_refine_persona_files()
                    status = "ok" if done else detail
                    obs = detail if done else ""
                    ok = done
                elif tool == "remember_session_fact":
                    fact = re.sub(r"\s+", " ", str(args.get("fact", "")).strip())[:120]
                    if not fact:
                        status = "skip (empty fact)"
                    else:
                        self._workspace.remember_structured(
                            session_key=key,
                            title=row.title,
                            facts=[fact],
                        )
                        status = "ok"
                        obs = f"会话事实已记录: {fact}"
                        ok = True
                elif tool == "remember_session_event":
                    event = re.sub(r"\s+", " ", str(args.get("event", "")).strip())[:120]
                    if not event:
                        status = "skip (empty event)"
                    else:
                        self._workspace.remember_structured(
                            session_key=key,
                            title=row.title,
                            events=[event],
                        )
                        status = "ok"
                        obs = f"会话事件已记录: {event}"
                        ok = True
                elif tool == "set_session_summary":
                    summary = re.sub(r"\s+", " ", str(args.get("summary", "")).strip())[:200]
                    if not summary:
                        status = "skip (empty summary)"
                    else:
                        self._apply_session_summary(row, summary)
                        status = "ok"
                        obs = f"会话摘要已更新: {summary}"
                        ok = True
                elif tool == "search_memory":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    if not query:
                        status = "skip (empty query)"
                    else:
                        include_global = is_admin or (not self.cfg.workspace_memory_main_only)
                        hits = self._workspace.search_memory(
                            query=query,
                            session_key=key,
                            include_global=include_global,
                            limit=max(1, int(self.cfg.workspace_memory_search_limit)),
                        )
                        compact = re.sub(r"\s+", " ", hits or "").strip()
                        if compact:
                            compact = compact[:260]
                            status = "ok"
                            obs = f"记忆检索[{query}]: {compact}"
                        else:
                            status = "ok (no-hit)"
                            obs = f"记忆检索[{query}]无命中"
                        ok = True
                elif tool == "web_search":
                    query = re.sub(r"\s+", " ", str(args.get("query", "")).strip())[:80]
                    if not query:
                        status = "skip (empty query)"
                    elif not self.cfg.tavily_enabled:
                        status = "skip (tavily disabled)"
                    else:
                        search_text = self._tavily_search(query)
                        if search_text:
                            status = "ok"
                            obs = f"网页检索[{query}]: {search_text}"
                        else:
                            status = "ok (no-hit)"
                            obs = f"网页检索[{query}]无命中"
                        ok = True
                elif tool == "mute_session":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        sess = self._get_or_create_session(key)
                        sess.muted = True
                        self._memory_dirty = True
                        status = "ok"
                        obs = "当前会话已静音"
                        ok = True
                elif tool == "unmute_session":
                    if not is_admin:
                        status = "deny (admin only)"
                    else:
                        sess = self._get_or_create_session(key)
                        sess.muted = False
                        self._memory_dirty = True
                        status = "ok"
                        obs = "当前会话已取消静音"
                        ok = True
                else:
                    status = "skip (unknown tool)"
            except Exception as exc:
                status = f"error ({exc})"

            trace = f"{idx}. {tool or '-'} -> {status}"
            if reason:
                trace += f" | {reason}"
            traces.append(trace)
            if obs:
                observations.append(obs)
            if ok:
                self._append_session_record(
                    row,
                    role="assistant",
                    text=f"[tool:{tool}] {obs or status}",
                    content_type="text",
                    sender="",
                    source="tool",
                    count_turn=False,
                )

        return "\n".join(traces)[:1500], "\n".join(observations)[:2200]

    def _load_heartbeat_tasks(self) -> str:
        if not self.cfg.workspace_enabled:
            return ""
        path = Path(self.cfg.workspace_dir) / "HEARTBEAT.md"
        if not path.exists():
            return ""
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        lines: list[str] = []
        for line in raw.splitlines():
            clean = line.strip()
            if not clean:
                continue
            # Skip markdown headings/comments; keep checklist bullets and normal text.
            if clean.startswith("#"):
                continue
            lines.append(clean)
        return "\n".join(lines)[:2800]

    @staticmethod
    def _parse_heartbeat_direct_actions(tasks_text: str) -> list[dict]:
        actions: list[dict] = []
        seen: set[str] = set()
        for line in (tasks_text or "").splitlines():
            clean = line.strip()
            if not clean:
                continue
            lowered = clean.lower()
            if "maintain_memory" in lowered and "maintain_memory" not in seen:
                days = 7
                m = re.search(r"days\s*[:=]\s*(\d{1,2})", clean, flags=re.IGNORECASE)
                if not m:
                    m = re.search(r"days\"\s*:\s*(\d{1,2})", clean, flags=re.IGNORECASE)
                if not m:
                    m = re.search(r"(\d{1,2})\s*天", clean)
                if m:
                    try:
                        days = int(m.group(1))
                    except Exception:
                        days = 7
                days = max(1, min(14, days))
                actions.append(
                    {
                        "tool": "maintain_memory",
                        "args": {"days": days},
                        "reason": "heartbeat direct task",
                    }
                )
                seen.add("maintain_memory")
            if "refine_persona_files" in lowered and "refine_persona_files" not in seen:
                actions.append(
                    {
                        "tool": "refine_persona_files",
                        "args": {},
                        "reason": "heartbeat direct task",
                    }
                )
                seen.add("refine_persona_files")
        return actions

    @staticmethod
    def _update_managed_block(
        raw: str,
        *,
        start_marker: str,
        end_marker: str,
        body: str,
    ) -> str:
        current = raw or ""
        payload = (body or "").strip()
        if not payload:
            return current
        block = f"{start_marker}\n{payload}\n{end_marker}"
        s_idx = current.find(start_marker)
        e_idx = current.find(end_marker)
        if s_idx >= 0 and e_idx > s_idx:
            e_end = e_idx + len(end_marker)
            merged = current[:s_idx].rstrip() + "\n\n" + block + current[e_end:]
            return merged.rstrip() + "\n"
        merged = current.rstrip()
        if merged:
            merged += "\n\n"
        merged += block + "\n"
        return merged

    def _collect_recent_daily_memory(self, *, days: int, max_chars: int = 6000) -> str:
        mem_dir = Path(self.cfg.workspace_dir) / "memory"
        if not mem_dir.exists():
            return ""
        files = sorted(mem_dir.glob("20??-??-??.md"), reverse=True)
        parts: list[str] = []
        for path in files[: max(1, int(days))]:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not text:
                continue
            parts.append(f"[{path.name}]\n{text[:2200]}")
            if len("\n\n".join(parts)) >= max_chars:
                break
        return "\n\n".join(parts)[:max_chars]

    def _heartbeat_llm_backends(self) -> list[tuple[str, LlmReplyGenerator]]:
        backends: list[tuple[str, LlmReplyGenerator]] = []
        seen: set[int] = set()
        for name, llm in (
            ("heartbeat", self.llm_heartbeat),
            ("summary", self.llm_summary),
            ("reply", self.llm_reply),
        ):
            if not llm.is_enabled():
                continue
            marker = id(llm)
            if marker in seen:
                continue
            seen.add(marker)
            backends.append((name, llm))
        return backends

    def _heartbeat_maintain_memory(self, *, days: int) -> tuple[bool, str]:
        if not self.cfg.workspace_enabled:
            return False, "skip (workspace disabled)"
        backends = self._heartbeat_llm_backends()
        if not backends:
            return False, "skip (llm disabled)"
        workspace = Path(self.cfg.workspace_dir)
        mem_path = workspace / "MEMORY.md"
        if not mem_path.exists():
            return False, "skip (MEMORY.md missing)"
        existing = mem_path.read_text(encoding="utf-8")
        recent = self._collect_recent_daily_memory(days=max(1, min(14, days)))
        if not recent:
            return False, "skip (no recent daily memory)"
        digest = ""
        backend_name = ""
        errors: list[str] = []
        for name, llm in backends:
            try:
                candidate = llm.heartbeat_memory_digest(
                    existing_memory=existing[:5000],
                    recent_daily_memory=recent,
                    max_items=12,
                ).strip()
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            if candidate:
                digest = candidate
                backend_name = name
                break
        if (not digest) and errors:
            return False, f"error ({errors[0]})"
        if not digest.strip():
            return False, "skip (digest empty)"
        updated = self._update_managed_block(
            existing,
            start_marker="<!-- HEARTBEAT_MEMORY_START -->",
            end_marker="<!-- HEARTBEAT_MEMORY_END -->",
            body=digest,
        )
        if updated != existing:
            mem_path.write_text(updated, encoding="utf-8")
            detail = f"MEMORY.md 已整理 (days={max(1, min(14, days))})"
            if backend_name:
                detail += f" via {backend_name}"
            return True, detail
        return False, "skip (memory unchanged)"

    def _heartbeat_refine_persona_files(self) -> tuple[bool, str]:
        if not self.cfg.workspace_enabled:
            return False, "skip (workspace disabled)"
        backends = self._heartbeat_llm_backends()
        if not backends:
            return False, "skip (llm disabled)"
        workspace = Path(self.cfg.workspace_dir)
        paths = {
            "soul": workspace / "SOUL.md",
            "identity": workspace / "IDENTITY.md",
            "user": workspace / "USER.md",
            "tools": workspace / "TOOLS.md",
            "memory": workspace / "MEMORY.md",
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")

        raw = {name: p.read_text(encoding="utf-8") for name, p in paths.items()}
        docs: dict[str, str] = {}
        backend_name = ""
        errors: list[str] = []
        for name, llm in backends:
            try:
                candidate = llm.heartbeat_refine_persona_docs(
                    soul=raw["soul"][:4000],
                    identity=raw["identity"][:4000],
                    user=raw["user"][:4000],
                    tools=raw["tools"][:4000],
                    memory=raw["memory"][:5000],
                )
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue
            if candidate:
                docs = candidate
                backend_name = name
                break
        if (not docs) and errors:
            return False, f"error ({errors[0]})"
        if not docs:
            return False, "skip (persona doc empty)"

        markers = {
            "soul": ("<!-- HEARTBEAT_SOUL_START -->", "<!-- HEARTBEAT_SOUL_END -->"),
            "identity": (
                "<!-- HEARTBEAT_IDENTITY_START -->",
                "<!-- HEARTBEAT_IDENTITY_END -->",
            ),
            "user": ("<!-- HEARTBEAT_USER_START -->", "<!-- HEARTBEAT_USER_END -->"),
            "tools": ("<!-- HEARTBEAT_TOOLS_START -->", "<!-- HEARTBEAT_TOOLS_END -->"),
        }
        changed = 0
        for key in ("soul", "identity", "user", "tools"):
            content = str(docs.get(key, "")).strip()
            if not content:
                continue
            path = paths[key]
            old = path.read_text(encoding="utf-8")
            start_marker, end_marker = markers[key]
            new_text = self._update_managed_block(
                old,
                start_marker=start_marker,
                end_marker=end_marker,
                body=content,
            )
            if new_text != old:
                path.write_text(new_text, encoding="utf-8")
                changed += 1

        if changed > 0:
            detail = f"设定文件已整理 {changed} 项"
            if backend_name:
                detail += f" via {backend_name}"
            return True, detail
        return False, "skip (persona unchanged)"

    def _heartbeat_virtual_row(self) -> ChatRowState:
        title = (
            str(self.cfg.admin_session_titles[0]).strip()
            if self.cfg.admin_session_titles
            else "__heartbeat__"
        )
        return ChatRowState(
            row_idx=-1,
            text="heartbeat",
            title=title or "__heartbeat__",
            preview="heartbeat",
            has_mention=False,
            has_unread_badge=False,
            fingerprint=f"heartbeat-{int(time.time())}",
            click_x_ratio=0.0,
            click_y_ratio=0.0,
        )

    def _available_heartbeat_tools(self) -> list[str]:
        tools = [
            "remember_session_fact",
            "remember_session_event",
            "set_session_summary",
            "search_memory",
            "maintain_memory",
            "refine_persona_files",
        ]
        if self.cfg.admin_commands_enabled:
            tools.append("remember_long_term")
        if self._has_web_search_tool():
            tools.append("web_search")
        return tools

    def _run_heartbeat(self, now: float, rows: list[ChatRowState]) -> bool:
        if not self.cfg.heartbeat_enabled:
            return False
        if not self._heartbeat_llm_backends():
            return False
        if not self.cfg.agent_actions_enabled:
            return False

        tasks_text = self._load_heartbeat_tasks()
        if not tasks_text:
            if self.cfg.log_verbose:
                print("[heartbeat] skipped (HEARTBEAT.md has no actionable lines)")
            return False

        row = self._heartbeat_virtual_row()
        if rows:
            for item in rows:
                if self._is_admin_session(item):
                    row = item
                    break

        tools = self._available_heartbeat_tools()
        if not tools:
            return False

        is_admin = bool(self.cfg.admin_commands_enabled)
        session_context = self._build_session_context(row)
        chat_context = self._build_session_history_text(row)
        workspace_context = self._workspace_context_for_row(row, is_admin=is_admin)
        memory_recall = self._workspace_memory_recall_for_row(
            row,
            tasks_text,
            is_admin=is_admin,
        )
        now_text = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        environment_context = (
            f"[heartbeat_prompt]\n{self.cfg.heartbeat_prompt}\n\n"
            f"[heartbeat_tasks]\n{tasks_text}\n\n"
            f"[current_time]\n{now_text}"
        )[:2200]

        planned_actions = self._parse_heartbeat_direct_actions(tasks_text)
        if not planned_actions:
            plan = self.llm_heartbeat.plan_actions(
                title=row.title,
                is_group=False,
                reason="heartbeat",
                latest_message=tasks_text.split("\n", 1)[0][:180],
                chat_context=chat_context,
                environment_context=environment_context,
                session_context=session_context,
                workspace_context=workspace_context,
                memory_recall=memory_recall,
                available_tools=tools,
                max_actions=max(1, int(self.cfg.heartbeat_max_actions)),
            )
            planned_actions = (
                plan.get("actions")
                if isinstance(plan, dict) and isinstance(plan.get("actions"), list)
                else []
            )
        if not planned_actions:
            if self.cfg.log_verbose:
                print("[heartbeat] no actions planned")
            return False

        trace, observations = self._execute_agent_actions(
            row,
            planned_actions,
            is_admin=is_admin,
            max_actions_override=self.cfg.heartbeat_max_actions,
        )
        print(
            f"[heartbeat] ran actions={len(planned_actions):>2} "
            f"title={self._fit_col(row.title, 14)}"
        )
        if trace:
            for line in trace.split("\n"):
                if line.strip():
                    print(f"            {line}")
        if observations:
            self._append_session_record(
                row,
                role="assistant",
                text=f"[heartbeat] {observations}",
                content_type="text",
                sender="",
                source="heartbeat",
                count_turn=False,
            )
        self._memory_dirty = True
        return True

    def _maybe_run_heartbeat(self, now: float, rows: list[ChatRowState]) -> bool:
        if not self.cfg.heartbeat_enabled:
            return False
        if (now - self._last_heartbeat_at) < float(self.cfg.heartbeat_interval_sec):
            return False
        if (now - self._last_activity_at) < float(self.cfg.heartbeat_min_idle_sec):
            return False
        self._last_heartbeat_at = now
        try:
            return self._run_heartbeat(now, rows)
        except Exception as exc:
            if self.cfg.heartbeat_fail_open:
                print(f"[warn] heartbeat failed, fail-open: {exc}")
                return False
            raise

    def _is_ignored_title(self, row: ChatRowState) -> bool:
        title = (row.title or "").strip()
        if not title:
            return False
        return any(keyword and keyword in title for keyword in self.cfg.ignore_title_keywords)

    def _should_reply_group(self, row: ChatRowState, reason: str) -> bool:
        if reason == "mention":
            return True
        preview = row.preview or ""
        if any(keyword and keyword in preview for keyword in self.cfg.group_reply_keywords):
            return True
        if self.cfg.group_only_reply_when_mentioned:
            return False
        return True

    def _should_use_llm_decision(self, is_group: bool) -> bool:
        if not self.llm_heartbeat.is_enabled():
            return False
        if not self.cfg.llm.decision_enabled:
            return False
        if is_group and self.cfg.llm.decision_on_group:
            return True
        if (not is_group) and self.cfg.llm.decision_on_private:
            return True
        return False

    def _llm_should_reply(self, row: ChatRowState, reason: str, is_group: bool) -> bool:
        return self._llm_should_reply_with_context(
            row=row,
            reason=reason,
            is_group=is_group,
            chat_context="",
            environment_context="",
            session_context="",
            workspace_context="",
            memory_recall="",
        )

    def _llm_should_reply_with_context(
        self,
        row: ChatRowState,
        reason: str,
        is_group: bool,
        chat_context: str,
        environment_context: str,
        session_context: str,
        workspace_context: str,
        memory_recall: str,
    ) -> bool:
        if reason == "mention":
            return True
        if not self._should_use_llm_decision(is_group):
            return True
        try:
            should_reply, why = self.llm.should_reply(
                title=row.title,
                preview=row.preview,
                reason=reason,
                is_group=is_group,
                chat_context=chat_context,
                environment_context=environment_context,
                session_context=session_context,
                workspace_context=workspace_context,
                memory_recall=memory_recall,
            )
            reason_w = max(24, self._term_width() - 46)
            print(
                f"[llm] row={row.row_idx:>2} | grp={self._yn(is_group)} | "
                f"decision={('reply' if should_reply else 'skip'):<5} | "
                f"reason={self._fit_col(why, reason_w)}"
            )
            return should_reply
        except Exception as exc:
            if self.cfg.llm.decision_fail_open:
                print(f"[warn] llm decision failed, fail-open: {exc}")
                return True
            print(f"[warn] llm decision failed, skip reply: {exc}")
            return False

    def _collect_focus_candidates(
        self,
        rows: list[ChatRowState],
        event_row: ChatRowState,
        reason: str,
    ) -> list[ChatRowState]:
        candidates: list[ChatRowState] = []
        seen_keys: set[str] = set()

        def _add_candidate(item: ChatRowState) -> None:
            key = self._title_key(item.title) or f"row-{item.row_idx}"
            if key in seen_keys:
                return
            seen_keys.add(key)
            candidates.append(item)

        _add_candidate(event_row)
        for item in rows:
            if self._is_ignored_title(item):
                continue
            prev = self._baseline.get(item.row_idx)
            pending_unread = bool(
                prev is not None
                and (prev.pending_unread or prev.pending_normal)
            )
            is_active = item.has_unread_badge or pending_unread
            if reason == "mention":
                is_active = is_active or item.has_mention
            if is_active:
                _add_candidate(item)
        return candidates

    def _match_focus_candidate(
        self,
        header: str,
        expected_row: ChatRowState,
        candidates: list[ChatRowState] | None,
    ) -> ChatRowState | None:
        clean_header = (header or "").strip()
        if not clean_header or not candidates:
            return None
        expected_key = self._title_key(expected_row.title)
        for item in candidates:
            item_key = self._title_key(item.title)
            if expected_key and item_key and item_key == expected_key:
                continue
            if self._is_chat_header_matched(item.title, clean_header):
                return item
        return None

    def _scan_rows_from_bounds(self, bounds: "WindowBounds") -> list[ChatRowState]:
        shot = screenshot_region(
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            high_res=True,
        )
        shot_rgb = self._to_np_rgb(shot)
        detected = detect_chat_rows(shot_rgb, bounds, self.cfg, self.ocr_engine)
        return detected.rows

    def _find_row_in_snapshot(
        self,
        rows: list[ChatRowState],
        expected_row: ChatRowState,
    ) -> ChatRowState | None:
        expected_key = self._title_key(expected_row.title)
        if expected_key:
            for item in rows:
                if self._title_key(item.title) == expected_key:
                    return item
        for item in rows:
            if item.row_idx == expected_row.row_idx:
                return item
        return None

    def _click_until_unread_cleared(
        self,
        row: ChatRowState,
        *,
        bounds: "WindowBounds",
    ) -> "WindowBounds":
        if not row.has_unread_badge:
            return bounds

        latest = bounds
        attempts = 0
        max_attempts = 40
        while attempts < max_attempts:
            time.sleep(0.5)
            try:
                latest = get_front_window_bounds(self.cfg.app_name)
                rows = self._scan_rows_from_bounds(latest)
            except Exception as exc:
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(f"[badge-check] row={row.row_idx:>2} | scan failed: {exc}")
                return latest

            matched = self._find_row_in_snapshot(rows, row)
            if matched is None:
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(
                        f"[badge-check] row={row.row_idx:>2} | "
                        f"title={self._fit_col(row.title, 14)} not found"
                    )
                return latest

            if not matched.has_unread_badge:
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(
                        f"[badge-cleared] row={row.row_idx:>2} | "
                        f"title={self._fit_col(matched.title, 14)} checks={attempts + 1}"
                    )
                return latest

            attempts += 1
            click_x = latest.x + int(latest.width * matched.click_x_ratio)
            click_y = latest.y + int(latest.height * matched.click_y_ratio)
            if self.cfg.log_verbose or self.cfg.debug_scan:
                print(
                    f"[badge-reclick] row={row.row_idx:>2} | "
                    f"title={self._fit_col(matched.title, 14)} attempt={attempts}/{max_attempts}"
                )
            self._safe_click(click_x, click_y)

        if self.cfg.log_verbose or self.cfg.debug_scan:
            print(
                f"[badge-still-unread] row={row.row_idx:>2} | "
                f"title={self._fit_col(row.title, 14)} attempts={max_attempts}"
            )
        return latest

    def _focus_chat(
        self,
        row: ChatRowState,
        *,
        focus_candidates: list[ChatRowState] | None = None,
        ensure_unread_clear: bool = False,
    ) -> FocusResult:
        self._activate_wechat()
        time.sleep(self.cfg.activate_wait_sec)
        bounds = get_front_window_bounds(self.cfg.app_name)
        is_group = self._is_group_chat(row)
        tries = max(1, self.cfg.focus_verify_max_clicks)
        latest = bounds
        last_header = ""

        if not self.cfg.focus_verify_enabled:
            row_x = latest.x + int(latest.width * row.click_x_ratio)
            row_y = latest.y + int(latest.height * row.click_y_ratio)
            self._safe_click(row_x, row_y)
            if ensure_unread_clear:
                latest = self._click_until_unread_cleared(row, bounds=latest)
            time.sleep(self.cfg.post_select_wait_sec)
            latest = get_front_window_bounds(self.cfg.app_name)
            return FocusResult(bounds=latest, matched=True, resolved_row=row, seen_header="")

        for i in range(1, tries + 1):
            latest = get_front_window_bounds(self.cfg.app_name)

            # First verify current focus without clicking.
            header = self._extract_chat_header_text(latest, is_group=is_group)
            last_header = header
            if self._is_chat_header_matched(row.title, header):
                if self.cfg.log_verbose:
                    seen_w = max(20, self._term_width() - 22)
                    print(
                        f"[focus-ok] row={row.row_idx:>2} | try={i:>2}/{tries:<2} | "
                        f"expect={self._fit_col(row.title, 14)}"
                    )
                    print(f"           seen={self._fit_col(header, seen_w)}")
                return FocusResult(bounds=latest, matched=True, resolved_row=row, seen_header=header)

            swapped = self._match_focus_candidate(header, row, focus_candidates)
            if swapped is not None:
                if self.cfg.log_verbose:
                    seen_w = max(20, self._term_width() - 22)
                    print(
                        f"[focus-swap] row={row.row_idx:>2} | try={i:>2}/{tries:<2} | "
                        f"expect={self._fit_col(row.title, 14)} -> "
                        f"use={self._fit_col(swapped.title, 14)}"
                    )
                    print(f"            seen={self._fit_col(header, seen_w)}")
                return FocusResult(
                    bounds=latest,
                    matched=True,
                    resolved_row=swapped,
                    seen_header=header,
                )

            # Not matched: click row once, then verify again.
            row_x = latest.x + int(latest.width * row.click_x_ratio)
            row_y = latest.y + int(latest.height * row.click_y_ratio)
            self._safe_click(row_x, row_y)
            if ensure_unread_clear:
                latest = self._click_until_unread_cleared(row, bounds=latest)
            time.sleep(self.cfg.post_select_wait_sec)
            latest = get_front_window_bounds(self.cfg.app_name)
            header = self._extract_chat_header_text(latest, is_group=is_group)
            last_header = header
            if self._is_chat_header_matched(row.title, header):
                if self.cfg.log_verbose:
                    seen_w = max(20, self._term_width() - 22)
                    print(
                        f"[focus-ok] row={row.row_idx:>2} | try={i:>2}/{tries:<2} | "
                        f"expect={self._fit_col(row.title, 14)}"
                    )
                    print(f"           seen={self._fit_col(header, seen_w)}")
                return FocusResult(bounds=latest, matched=True, resolved_row=row, seen_header=header)

            swapped = self._match_focus_candidate(header, row, focus_candidates)
            if swapped is not None:
                if self.cfg.log_verbose:
                    seen_w = max(20, self._term_width() - 22)
                    print(
                        f"[focus-swap] row={row.row_idx:>2} | try={i:>2}/{tries:<2} | "
                        f"expect={self._fit_col(row.title, 14)} -> "
                        f"use={self._fit_col(swapped.title, 14)}"
                    )
                    print(f"            seen={self._fit_col(header, seen_w)}")
                return FocusResult(
                    bounds=latest,
                    matched=True,
                    resolved_row=swapped,
                    seen_header=header,
                )

            if self.cfg.log_verbose:
                seen_w = max(20, self._term_width() - 22)
                print(
                    f"[focus-retry] row={row.row_idx:>2} | try={i:>2}/{tries:<2} | "
                    f"expect={self._fit_col(row.title, 14)}"
                )
                print(f"              seen={self._fit_col(header, seen_w)}")
            time.sleep(max(0.02, self.cfg.focus_verify_wait_sec))
        if self.cfg.log_verbose:
            print(f"[focus-fail] row={row.row_idx:>2} | expect={self._fit_col(row.title, 14)}")
        return FocusResult(bounds=latest, matched=False, resolved_row=None, seen_header=last_header)

    def _extract_chat_context(
        self,
        bounds: "WindowBounds",
        *,
        title: str = "",
        reason: str = "",
        is_group: bool = False,
        session_context: str = "",
        session_history: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
        latest_hint: str = "",
        preview: str = "",
    ) -> ChatContextSnapshot:
        x = bounds.x + int(bounds.width * self.cfg.chat_context_region.x)
        y = bounds.y + int(bounds.height * self.cfg.chat_context_region.y)
        w = int(bounds.width * self.cfg.chat_context_region.w)
        h = int(bounds.height * self.cfg.chat_context_region.h)
        if w <= 0 or h <= 0:
            return ChatContextSnapshot(text="", last_side="unknown", last_line="", source="none")

        shot = screenshot_region(x, y, w, h, high_res=True)
        try:
            parsed = self.llm.analyze_chat_image(
                image=shot,
                title=title,
                reason=reason,
                is_group=is_group,
                session_context=session_context,
                session_history=session_history,
                workspace_context=workspace_context,
                memory_recall=memory_recall,
                latest_hint=latest_hint,
                preview=preview,
            )
            context = parsed.get("context") if isinstance(parsed.get("context"), dict) else {}
            environment = (
                parsed.get("environment")
                if isinstance(parsed.get("environment"), dict)
                else (
                    parsed.get("memory_update")
                    if isinstance(parsed.get("memory_update"), dict)
                    else {}
                )
            )

            last_speaker = context.get("last_speaker", parsed.get("last_speaker", "unknown"))
            if last_speaker not in ("self", "other", "unknown"):
                last_speaker = "unknown"
            raw_last = context.get("last_message") or parsed.get("last_message") or ""
            if isinstance(raw_last, dict):
                last_message = str(raw_last.get("text", "")).strip()[:220]
            else:
                last_message = str(raw_last).strip()[:220]
            last_user_message = str(
                context.get("last_user_message", parsed.get("last_user_message", ""))
            ).strip()[:220]
            recent_messages = context.get("recent_messages") or parsed.get("recent_messages") or []
            if not isinstance(recent_messages, list):
                recent_messages = []
            recent_messages = [str(x).strip() for x in recent_messages if str(x).strip()][:20]

            text = " | ".join(recent_messages)[:900]
            if not text:
                text = last_message[:900]
            if self.cfg.log_verbose:
                last_w = max(24, self._term_width() - 15)
                print(
                    f"[vision] speaker={last_speaker:<7} | recent={len(recent_messages):>2}"
                )
                print(f"         last={self._fit_col(last_message, last_w)}")
            snapshot = ChatContextSnapshot(
                text=text,
                last_side=last_speaker,
                last_line=last_message,
                last_user_message=last_user_message,
                recent_messages=recent_messages,
                recent_structured=(
                    context.get("recent_structured")
                    if isinstance(context.get("recent_structured"), list)
                    else (parsed.get("recent_structured") or [])
                ),
                chat_records=(
                    context.get("chat_records")
                    if isinstance(context.get("chat_records"), list)
                    else (parsed.get("chat_records") or parsed.get("recent_structured") or [])
                ),
                memory_summary=str(environment.get("summary", "")).strip(),
                memory_time_hints=(
                    environment.get("time_hints")
                    if isinstance(environment.get("time_hints"), list)
                    else []
                ),
                memory_people=(
                    environment.get("people")
                    if isinstance(environment.get("people"), list)
                    else []
                ),
                memory_facts=(
                    environment.get("facts")
                    if isinstance(environment.get("facts"), list)
                    else []
                ),
                memory_events=(
                    environment.get("events")
                    if isinstance(environment.get("events"), list)
                    else []
                ),
                memory_relations=(
                    environment.get("relations")
                    if isinstance(environment.get("relations"), list)
                    else []
                ),
                schema=str(parsed.get("schema", "")),
                source="vision",
            )
            snapshot.environment_text = self._build_environment_context(snapshot)
            return snapshot
        except Exception as exc:
            if self.cfg.vision.fail_open:
                print(f"[warn] vision parse failed: {exc}")
                return ChatContextSnapshot(text="", last_side="unknown", last_line="", source="vision")
            raise

    def _log_cycle_snapshot(self, rows: list[ChatRowState], now: float) -> None:
        if not self.cfg.log_verbose:
            return
        print("")
        print(
            f"[cycle] id={self._cycle:>4} | ts={int(now):>10} | "
            f"rows={len(rows):>2} | baseline={len(self._baseline):>2}"
        )
        limit = max(1, self.cfg.log_snapshot_rows)
        for row in rows[:limit]:
            group = self._is_group_chat(row)
            key = self._session_key_for_row(row)
            preview_w = max(16, self._term_width() - 76)
            print(
                f"[row]   idx={row.row_idx:>2} | grp={self._yn(group)} | "
                f"unrd={self._yn(row.has_unread_badge)} | @={self._yn(row.has_mention)} | "
                f"key={self._fit_col(key, 10)} | title={self._fit_col(row.title, 14)} | "
                f"preview={self._fit_col(row.preview, preview_w)}"
            )

    def _set_baseline(self, rows: list[ChatRowState], now: float) -> None:
        self._baseline = {
            row.row_idx: RowMemory(
                session_key=self._session_key_for_row(row),
                fingerprint=row.fingerprint,
                preview_norm=self._normalize_preview(row.preview),
                last_sent_norm="",
                has_unread_badge=(
                    False if self.cfg.process_existing_unread_on_start else row.has_unread_badge
                ),
                pending_unread=(row.has_unread_badge if self.cfg.process_existing_unread_on_start else False),
                pending_normal=False,
                has_mention=(False if self.cfg.process_existing_unread_on_start else row.has_mention),
                last_replied_at=0.0,
            )
            for row in rows
        }
        print(f"[init] baseline rows={len(rows):>2} at={now:.0f}")

    def _pick_event(self, rows: list[ChatRowState], now: float) -> tuple[ChatRowState, str] | None:
        mention_candidates: list[ChatRowState] = []
        unread_candidates: list[ChatRowState] = []
        preview_candidates: list[ChatRowState] = []

        for row in rows:
            if self._is_ignored_title(row):
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(f"[skip-title] row={row.row_idx} title={row.title!r}")
                continue

            prev = self._baseline.get(row.row_idx)
            if prev is None:
                self._baseline[row.row_idx] = RowMemory(
                    session_key=self._session_key_for_row(row),
                    fingerprint=row.fingerprint,
                    preview_norm=self._normalize_preview(row.preview),
                    last_sent_norm="",
                    has_unread_badge=row.has_unread_badge,
                    pending_unread=row.has_unread_badge,
                    pending_normal=False,
                    has_mention=row.has_mention,
                    last_replied_at=0.0,
                )
                continue

            row_key = self._session_key_for_row(row)
            if prev.session_key != row_key:
                # Row index got rebound to another session after list reordering.
                # Reset row memory to avoid carrying unread/pending state across chats.
                self._baseline[row.row_idx] = RowMemory(
                    session_key=row_key,
                    fingerprint=row.fingerprint,
                    preview_norm=self._normalize_preview(row.preview),
                    last_sent_norm="",
                    has_unread_badge=row.has_unread_badge,
                    pending_unread=False,
                    pending_normal=False,
                    has_mention=row.has_mention,
                    last_replied_at=prev.last_replied_at,
                )
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(
                        f"[row-rebind] row={row.row_idx:>2} | "
                        f"key={self._fit_col(row_key, 14)}"
                    )
                continue

            preview_norm = self._normalize_preview(row.preview)
            preview_changed = self._is_preview_meaningfully_changed(
                prev.preview_norm, preview_norm
            )
            unread_rise = row.has_unread_badge and not prev.has_unread_badge
            unread_pending = prev.pending_unread
            normal_pending = prev.pending_normal
            mention_rise = row.has_mention and not prev.has_mention

            prev.fingerprint = row.fingerprint
            prev.session_key = row_key
            prev.preview_norm = preview_norm
            prev.has_unread_badge = row.has_unread_badge
            if unread_rise:
                prev.pending_unread = True
            if not row.has_unread_badge:
                prev.pending_unread = False
            prev.has_mention = row.has_mention

            recent_sent_norm = self._get_recent_sent_for_row(row, now)
            self_echo = self._is_self_echo(preview_norm, prev.last_sent_norm) or self._is_self_echo(
                preview_norm, recent_sent_norm
            )
            self_preview_refresh = preview_changed and self._is_preview_refresh_from_self(
                row=row,
                preview_norm=preview_norm,
                prev_sent_norm=prev.last_sent_norm,
                recent_sent_norm=recent_sent_norm,
            )

            if self.cfg.debug_scan and (preview_changed or unread_rise or mention_rise):
                print(
                    "[scan] "
                    f"row={row.row_idx} title={row.title!r} preview={row.preview!r} "
                    f"preview_changed={preview_changed} "
                    f"unread={row.has_unread_badge} mention={row.has_mention} "
                    f"self_echo={self_echo} pending_unread={unread_pending or unread_rise}"
                )

            unread_active = row.has_unread_badge

            if not (
                preview_changed
                or unread_rise
                or unread_pending
                or normal_pending
                or mention_rise
                or unread_active
            ):
                continue

            # Prevent reply loops when the preview is our own last sent text.
            if self_echo:
                continue
            if self_preview_refresh:
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    print(
                        f"[skip-self-preview] row={row.row_idx:>2} | "
                        f"title={self._fit_col(row.title, 14)}"
                    )
                    print(
                        f"                  preview={self._fit_col(row.preview, max(24, self._term_width() - 27))}"
                    )
                continue

            if now - prev.last_replied_at < self.cfg.action_cooldown_sec:
                if self.cfg.log_verbose or self.cfg.debug_scan:
                    remain = self.cfg.action_cooldown_sec - (now - prev.last_replied_at)
                    print(
                        f"[skip-cooldown] row={row.row_idx:>2} title={row.title!r} "
                        f"remain={max(0.0, remain):.1f}s"
                    )
                continue

            if row.has_mention and mention_rise:
                mention_candidates.append(row)
            elif row.has_unread_badge or unread_pending or normal_pending:
                unread_candidates.append(row)
            elif self.cfg.trigger_on_preview_change and preview_changed:
                preview_candidates.append(row)
            else:
                continue

        def _choose(candidates: list[ChatRowState], reason: str) -> tuple[ChatRowState, str] | None:
            for row in candidates:
                is_group = self._is_group_chat(row)
                is_admin = self._is_admin_session(row)
                if (not is_admin) and self._is_row_muted(row):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-muted] row={row.row_idx} title={row.title!r} "
                            f"reason={reason}"
                        )
                    continue
                if (
                    is_group
                    and reason == "new_message"
                    and self.cfg.group_require_sender_prefix_for_new_message
                    and (not self._has_sender_prefix(row.preview))
                ):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-group-prefix] row={row.row_idx} title={row.title!r} "
                            f"preview={row.preview!r}"
                        )
                    continue
                if is_group and self._should_use_llm_decision(is_group):
                    if (
                        self._is_normal_reply_event(row, reason)
                        and self._normal_reply_interval_active()
                        and (now - self._last_normal_reply_at) < self.cfg.normal_reply_interval_sec
                    ):
                        remain = self.cfg.normal_reply_interval_sec - (
                            now - self._last_normal_reply_at
                        )
                        mem = self._baseline.get(row.row_idx)
                        if mem is not None and not row.has_unread_badge:
                            mem.pending_normal = True
                        if self.cfg.log_verbose or self.cfg.debug_scan:
                            print(
                                f"[skip-normal-interval] row={row.row_idx:>2} "
                                f"title={row.title!r} remain={max(0.0, remain):.1f}s"
                            )
                        continue
                    return row, reason
                if is_group and not self._should_reply_group(row, reason):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-rule] group row={row.row_idx} title={row.title!r} "
                            f"reason={reason} preview={row.preview!r} "
                            f"group_only_reply_when_mentioned={self.cfg.group_only_reply_when_mentioned}"
                        )
                    continue
                if (
                    self._is_normal_reply_event(row, reason)
                    and self._normal_reply_interval_active()
                    and (now - self._last_normal_reply_at) < self.cfg.normal_reply_interval_sec
                ):
                    remain = self.cfg.normal_reply_interval_sec - (
                        now - self._last_normal_reply_at
                    )
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None and not row.has_unread_badge:
                        mem.pending_normal = True
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-normal-interval] row={row.row_idx:>2} "
                            f"title={row.title!r} remain={max(0.0, remain):.1f}s"
                        )
                    continue
                return row, reason
            return None

        chosen = _choose(mention_candidates, "mention")
        if chosen:
            return chosen
        chosen = _choose(unread_candidates, "new_message")
        if chosen:
            return chosen
        return _choose(preview_candidates, "new_message")

    def _recent_assistant_replies(self, row: ChatRowState, limit: int) -> list[str]:
        key = self._session_key_for_row(row)
        sess = self._get_or_create_session(key)
        out: list[str] = []
        for item in reversed(sess.short):
            if not item.startswith("A:"):
                continue
            txt = item.split(":", 1)[1].strip() if ":" in item else ""
            if not txt:
                continue
            out.append(txt)
            if len(out) >= max(1, limit):
                break
        return out

    def _is_reply_too_similar(self, reply: str, recent_replies: list[str]) -> bool:
        norm = self._normalize_preview(reply)
        if not norm or len(norm) < 4:
            return False
        threshold = max(0.6, min(0.98, self.cfg.llm_reply.anti_repeat_similarity))
        for old in recent_replies:
            old_norm = self._normalize_preview(old)
            if not old_norm:
                continue
            if norm in old_norm or old_norm in norm:
                return True
            n = min(len(norm), len(old_norm), 40)
            if n < 6:
                continue
            ratio = SequenceMatcher(a=norm[:n], b=old_norm[:n]).ratio()
            if ratio >= threshold:
                return True
        return False

    def _is_no_reply_signal(self, text: str) -> bool:
        raw = re.sub(r"\s+", " ", (text or "").strip())
        if not raw:
            return False
        upper = raw.upper()
        if re.fullmatch(r"\s*<?\[?\s*NO[\s_\-]?REPLY\s*\]?>?\s*", upper):
            return True
        lowered = raw.lower().strip(" 。.!！?？")
        exact_markers = {
            "无需回复",
            "不需要回复",
            "不回复",
            "保持沉默",
            "继续观察",
            "skip reply",
            "no reply",
            "stay silent",
            "heartbeat_ok",
        }
        return lowered in exact_markers

    def _reply_text(
        self,
        row: ChatRowState,
        reason: str,
        chat_context: str,
        environment_context: str,
        session_context: str,
        workspace_context: str,
        memory_recall: str,
        latest_message: str = "",
    ) -> str:
        fallback = (
            self.cfg.reply_on_mention if reason == "mention" else self.cfg.reply_on_new_message
        )
        if not self.llm_reply.is_reply_enabled():
            return fallback
        recent_replies = self._recent_assistant_replies(
                    row, max(1, self.cfg.llm_reply.anti_repeat_window)
        )
        retries = (
            max(0, self.cfg.llm_reply.anti_repeat_retry)
            if self.cfg.llm_reply.anti_repeat_enabled
            else 0
        )
        try:
            avoid = list(recent_replies)
            for attempt in range(retries + 1):
                text = self.llm_reply.generate(
                    title=row.title,
                    preview=row.preview,
                    reason=reason,
                    latest_message=latest_message,
                    chat_context=chat_context,
                    environment_context=environment_context,
                    session_context=session_context,
                    workspace_context=workspace_context,
                    memory_recall=memory_recall,
                    avoid_replies=(avoid if attempt > 0 else []),
                    allow_no_reply_signal=(
                        self.cfg.group_allow_llm_no_reply if self._is_group_chat(row) else False
                    ),
                )
                clean = self._sanitize_generated_reply(text, fallback=fallback)
                if self._is_no_reply_signal(clean):
                    if self._is_group_chat(row) and self.cfg.group_allow_llm_no_reply:
                        if self.cfg.log_verbose:
                            print(
                                f"[skip-no-reply] row={row.row_idx:>2} | "
                                f"title={self._fit_col(row.title, 14)}"
                            )
                        return ""
                    # If disabled (or in private), avoid accidental silence from model meta output.
                    if self.cfg.log_verbose:
                        print(
                            f"[warn] no-reply signal ignored, fallback used "
                            f"title={row.title!r}"
                        )
                    clean = fallback
                if self.cfg.llm_reply.anti_repeat_enabled and self._is_reply_too_similar(clean, recent_replies):
                    print(
                        f"[reply-repeat] row={row.row_idx} title={row.title!r} "
                        f"attempt={attempt + 1}/{retries + 1} reply={clean!r}"
                    )
                    avoid.append(clean)
                    if attempt < retries:
                        continue
                print(
                    f"[{self.llm_reply.reply_backend_name()}] generated reply len={len(text)} "
                    f"sanitized_len={len(clean)} "
                    f"attempt={attempt + 1}/{retries + 1}"
                )
                return clean
            return fallback
        except Exception as exc:
            print(f"[warn] reply backend failed, fallback to template: {exc}")
            return fallback

    def _sanitize_generated_reply(self, text: str, fallback: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return fallback
        # Remove markdown fences and collapse lines.
        raw = raw.replace("```", "").replace("\r", "\n")
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        merged = " ".join(lines).strip()
        if self._is_no_reply_signal(raw) or self._is_no_reply_signal(merged):
            return "[NO_REPLY]"

        def _clip_reply(s: str) -> str:
            val = re.sub(r"\s+", " ", (s or "").strip())
            return val

        def _looks_like_heading(s: str) -> bool:
            x = (s or "").strip().lower()
            return bool(
                re.match(r"^(\d+[\.)]|[-*])\s*", x)
                or x.startswith("thinking")
                or x.startswith("analysis")
                or x.startswith("reasoning")
                or x.startswith("步骤")
                or x.startswith("思考")
                or x.startswith("推理")
            )

        suspicious_markers = [
            "thinking process",
            "analyze the request",
            "analysis:",
            "reasoning:",
            "let me think",
            "chain of thought",
            "步骤",
            "思考过程",
            "推理过程",
            "分析请求",
        ]
        lower = merged.lower()
        has_reasoning = any(marker in lower for marker in suspicious_markers)

        # Try to extract explicit final answer block first.
        final_markers = [
            "final answer",
            "final response",
            "final reply",
            "最终回答",
            "最终回复",
            "回复如下",
            "答复如下",
        ]
        candidate = ""
        for mk in final_markers:
            idx = lower.rfind(mk)
            if idx >= 0:
                tail = merged[idx + len(mk) :].lstrip("：: -")
                candidate = _clip_reply(tail)
                if candidate:
                    break

        # If no explicit final marker, use the last non-heading line as fallback extraction.
        if not candidate and has_reasoning:
            for line in reversed(lines):
                if _looks_like_heading(line):
                    continue
                if len(line) < 2:
                    continue
                if re.search(r"[\u4e00-\u9fff]", line):
                    candidate = _clip_reply(line)
                    if candidate:
                        break

        # Avoid overly long multi-paragraph output for chat reply.
        if candidate:
            merged = candidate
        else:
            merged = _clip_reply(merged)
            if not merged:
                return fallback

        if self._is_no_reply_signal(merged):
            return "[NO_REPLY]"

        # If no CJK and no punctuation, treat as low quality.
        if not re.search(r"[\u4e00-\u9fff]", merged):
            return fallback
        return merged

    def _activate_wechat(self) -> None:
        aliases = [x.strip() for x in self.cfg.app_name.split("|") if x.strip()]
        if "WeChat" in aliases and "微信" not in aliases:
            aliases.append("微信")
        if not aliases:
            aliases = ["WeChat", "微信"]

        for app in aliases:
            proc = subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to activate'],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return

        print(f"[warn] failed to activate app, tried aliases={aliases}")

    def _safe_click(self, x: int, y: int) -> None:
        pyautogui.moveTo(x, y, duration=max(0.0, self.cfg.click_move_duration_sec))
        pyautogui.mouseDown()
        time.sleep(max(0.01, self.cfg.mouse_down_hold_sec))
        pyautogui.mouseUp()

    def _paste_and_send(self, message: str) -> None:
        pyperclip.copy(message)
        time.sleep(0.05)

        # Prefer AppleScript paste on macOS to reduce occasional literal "v" input.
        paste_script = 'tell application "System Events" to keystroke "v" using command down'
        enter_script = 'tell application "System Events" to key code 36'
        proc = subprocess.run(
            ["osascript", "-e", paste_script, "-e", enter_script],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return

        print(f"[warn] osascript paste failed, fallback to pyautogui: {proc.stderr.strip()}")
        pyautogui.keyDown("command")
        pyautogui.press("v")
        pyautogui.keyUp("command")
        time.sleep(0.06)
        pyautogui.press("enter")

    def _reply(
        self,
        row: ChatRowState,
        reason: str,
        focused_bounds=None,
        chat_context: str = "",
        environment_context: str = "",
        session_context: str = "",
        workspace_context: str = "",
        memory_recall: str = "",
        latest_message: str = "",
        force_message: str = "",
    ) -> str:
        preview_w = max(24, self._term_width() - 18)
        print(
            f"[action] row={row.row_idx:>2} | reason={reason:<14} | "
            f"title={self._fit_col(row.title, 14)}"
        )
        print(f"         preview={self._fit_col(row.preview, preview_w)}")
        message = force_message or self._reply_text(
            row,
            reason,
            chat_context,
            environment_context,
            session_context,
            workspace_context,
            memory_recall,
            latest_message=latest_message,
        )
        if not (message or "").strip():
            if self.cfg.log_verbose:
                print(
                    f"[skip-empty-reply] row={row.row_idx:>2} | "
                    f"title={self._fit_col(row.title, 14)}"
                )
            return ""
        if self.cfg.dry_run:
            msg_w = max(24, self._term_width() - 16)
            print(f"[dry-run] msg={self._fit_col(message, msg_w)}")
            return message

        if focused_bounds is not None:
            bounds = focused_bounds
        else:
            focus_result = self._focus_chat(row)
            if (not focus_result.matched) or (focus_result.resolved_row is None):
                seen_w = max(24, self._term_width() - 17)
                print(
                    f"[skip-focus] row={row.row_idx:>2} | "
                    f"expect={self._fit_col(row.title, 14)}"
                )
                if focus_result.seen_header:
                    print(f"            seen={self._fit_col(focus_result.seen_header, seen_w)}")
                return ""
            bounds = focus_result.bounds
        if self.cfg.focus_verify_enabled:
            seen = self._extract_chat_header_text(
                bounds, is_group=self._is_group_chat(row)
            )
            if not self._is_chat_header_matched(row.title, seen):
                seen_w = max(24, self._term_width() - 17)
                print(
                    f"[skip-focus] row={row.row_idx:>2} | "
                    f"expect={self._fit_col(row.title, 14)}"
                )
                print(f"            seen={self._fit_col(seen, seen_w)}")
                return ""

        input_x = bounds.x + int(bounds.width * self.cfg.input_point.x)
        input_y = bounds.y + int(bounds.height * self.cfg.input_point.y)
        self._safe_click(input_x, input_y)
        time.sleep(0.08)

        self._paste_and_send(message)
        msg_w = max(24, self._term_width() - 12)
        print(f"[sent] msg={self._fit_col(message, msg_w)}")
        return message

    def run_forever(self) -> None:
        self._last_activity_at = time.time()
        self._last_heartbeat_at = 0.0
        print("[start] WeChat GUI RPA started")
        print("[start] perms: Accessibility + Screen Recording")
        print(
            f"[start] decision: enabled={self.cfg.llm_decision.decision_enabled} "
            f"grp={self.cfg.llm_decision.decision_on_group} "
            f"priv={self.cfg.llm_decision.decision_on_private} "
            f"mention_only={self.cfg.group_only_reply_when_mentioned} "
            f"group_no_reply={self.cfg.group_allow_llm_no_reply}"
        )
        print(
            f"[start] cadence: poll={self.cfg.poll_interval_sec:.1f}s "
            f"cooldown={self.cfg.action_cooldown_sec:.1f}s "
            f"normal_reply={self.cfg.normal_reply_interval_sec:.1f}s "
            f"(immediate=private/@)"
        )
        print(
            f"[start] reply-backend: {self.llm_reply.reply_backend_name()} "
            f"(vision={self.llm_reply.is_vision_enabled()} llm={self.llm_reply.is_enabled()})"
        )
        print(
            f"[start] llm-profiles: reply={self.cfg.llm_reply.model} "
            f"decision={self.cfg.llm_decision.model} "
            f"planner={self.cfg.llm_planner.model} "
            f"summary={self.cfg.llm_summary.model} "
            f"heartbeat={self.cfg.llm_heartbeat.model}"
        )
        admin_titles = ",".join(self.cfg.admin_session_titles) if self.cfg.admin_session_titles else "-"
        admin_w = max(24, self._term_width() - 17)
        path_w = max(20, self._term_width() - 35)
        print(
            f"[start] memory: enabled={self.cfg.memory_enabled} "
            f"path={self._fit_col(str(self._memory_path), path_w)}"
        )
        print(
            f"[start] agent-actions: enabled={self.cfg.agent_actions_enabled} "
            f"max={self.cfg.agent_actions_max_per_turn} "
            f"fail_open={self.cfg.agent_actions_fail_open}"
        )
        print(
            f"[start] heartbeat: enabled={self.cfg.heartbeat_enabled} "
            f"interval={self.cfg.heartbeat_interval_sec:.1f}s "
            f"idle_min={self.cfg.heartbeat_min_idle_sec:.1f}s "
            f"max_actions={self.cfg.heartbeat_max_actions}"
        )
        print(f"[start] web-search: {self._web_search_status_text()}")
        print(f"        admin={self._fit_col(admin_titles, admin_w)}")
        while True:
            self._cycle += 1
            now = time.time()
            try:
                bounds = get_front_window_bounds(self.cfg.app_name)
            except WindowNotFoundError as exc:
                print(f"[warn] {exc}")
                time.sleep(self.cfg.poll_interval_sec)
                continue

            shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height, high_res=True)
            shot_rgb = self._to_np_rgb(shot)

            detected = detect_chat_rows(shot_rgb, bounds, self.cfg, self.ocr_engine)
            self._log_cycle_snapshot(detected.rows, now)
            if not self._baseline:
                self._set_baseline(detected.rows, now)
                time.sleep(self.cfg.poll_interval_sec)
                continue

            event = self._pick_event(detected.rows, now)
            if event:
                self._idle_streak = 0
                self._last_activity_at = now
                row, reason = event
                if self._skip_first_action_pending:
                    self._skip_first_action_pending = False
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                        mem.pending_normal = False
                    print(
                        f"[skip-startup] row={row.row_idx:>2} | "
                        f"reason={reason:<14} | title={self._fit_col(row.title, 14)}"
                    )
                    time.sleep(self.cfg.poll_interval_sec)
                    continue
                if self._is_ignored_title(row):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(f"[skip-title-hard] row={row.row_idx} title={row.title!r}")
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                        mem.pending_normal = False
                    time.sleep(self.cfg.poll_interval_sec)
                    continue
                if self.cfg.log_verbose:
                    print(
                        f"[event] id={self._cycle:>4} | row={row.row_idx:>2} | "
                        f"reason={reason:<14} | title={self._fit_col(row.title, 14)}"
                    )
                event_is_group = self._is_group_chat(row)
                event_is_admin = self._is_admin_session(row)
                event_skip_self_latest = (
                    self.cfg.skip_if_latest_chat_from_self
                    and (event_is_group or self.cfg.skip_if_latest_chat_from_self_private)
                )
                focused_bounds = None
                chat_context = ""
                environment_context = ""
                context_snapshot = ChatContextSnapshot(
                    text="",
                    last_side="unknown",
                    last_line="",
                    source="none",
                )
                need_context = (
                    self.llm.is_vision_enabled()
                    or (reason == "new_message" and event_skip_self_latest)
                    or event_is_admin
                    or (
                        self._should_use_llm_decision(event_is_group)
                        and self.cfg.llm_decision.decision_read_chat_context
                    )
                )
                focus_candidates = self._collect_focus_candidates(detected.rows, row, reason)
                if need_context:
                    focus_result = self._focus_chat(
                        row,
                        focus_candidates=focus_candidates,
                        ensure_unread_clear=(reason == "new_message"),
                    )
                    if (not focus_result.matched) or (focus_result.resolved_row is None):
                        seen_w = max(24, self._term_width() - 17)
                        print(
                            f"[skip-focus] row={row.row_idx:>2} | "
                            f"expect={self._fit_col(row.title, 14)}"
                        )
                        if focus_result.seen_header:
                            print(
                                f"            seen={self._fit_col(focus_result.seen_header, seen_w)}"
                            )
                        time.sleep(self.cfg.poll_interval_sec)
                        continue
                    resolved = focus_result.resolved_row
                    changed_target = (
                        resolved.row_idx != row.row_idx
                        or self._title_key(resolved.title) != self._title_key(row.title)
                    )
                    if changed_target and self.cfg.log_verbose:
                        print(
                            f"[focus-retarget] from={self._fit_col(row.title, 14)} "
                            f"-> to={self._fit_col(resolved.title, 14)}"
                        )
                    row = resolved
                    focused_bounds = focus_result.bounds

                is_group = self._is_group_chat(row)
                is_admin = self._is_admin_session(row)
                skip_self_latest = (
                    self.cfg.skip_if_latest_chat_from_self
                    and (is_group or self.cfg.skip_if_latest_chat_from_self_private)
                )
                session_context = self._build_session_context(row)
                session_history = self._build_session_history_text(row)
                workspace_context = self._workspace_context_for_row(
                    row,
                    is_admin=is_admin,
                )
                memory_recall = self._workspace_memory_recall_for_row(
                    row,
                    row.preview or row.text,
                    is_admin=is_admin,
                )
                if need_context:
                    context_snapshot = self._extract_chat_context(
                        focused_bounds,
                        title=row.title,
                        reason=reason,
                        is_group=is_group,
                        session_context=session_context,
                        session_history=session_history,
                        workspace_context=workspace_context,
                        memory_recall=memory_recall,
                        latest_hint=row.preview or row.text,
                        preview=row.preview,
                    )
                    if context_snapshot.chat_records:
                        self._merge_session_records(
                            row, context_snapshot.chat_records, source=context_snapshot.source
                        )
                    if context_snapshot.memory_summary:
                        self._apply_session_summary(row, context_snapshot.memory_summary)
                    self._apply_workspace_memory_update(row, context_snapshot)
                    chat_context = context_snapshot.text
                    environment_context = context_snapshot.environment_text
                    if self.cfg.debug_scan:
                        print(f"[context] row={row.row_idx} text={chat_context!r}")
                    if self.cfg.log_verbose:
                        line_w = max(24, self._term_width() - 12)
                        print(
                            f"[ctx] row={row.row_idx:>2} | "
                            f"src={self._fit_col(context_snapshot.source, 6)} | "
                            f"schema={self._fit_col(context_snapshot.schema or '-', 14)} | "
                            f"side={self._fit_col(context_snapshot.last_side, 7)}"
                        )
                        print(
                            f"      last={self._fit_col(context_snapshot.last_line, line_w)}"
                        )

                if (
                    reason == "new_message"
                    and skip_self_latest
                    and context_snapshot.last_side == "self"
                ):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-self-latest] row={row.row_idx} title={row.title!r} "
                            f"preview={row.preview!r} last_line={context_snapshot.last_line!r}"
                        )
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                        mem.pending_normal = False
                        self._save_persistent_memory()
                        time.sleep(self.cfg.poll_interval_sec)
                        continue

                cmd_line = ""
                if is_admin:
                    cmd_line = self._extract_admin_command_text(row, context_snapshot)
                    if cmd_line:
                        self._append_session_item(row, "U", cmd_line)
                        ack = self._handle_admin_command(cmd_line)
                        if self.cfg.log_verbose:
                            print(f"[admin-cmd] cmd={cmd_line!r} ack={ack!r}")
                        reply_text = self._reply(
                            row,
                            "admin_command",
                            focused_bounds=focused_bounds,
                            environment_context=environment_context,
                            workspace_context=workspace_context,
                            memory_recall=memory_recall,
                            force_message=ack or "命令已执行。",
                        )
                        mem = self._baseline.get(row.row_idx)
                        if mem is not None:
                            mem.last_replied_at = now
                            mem.last_sent_norm = self._normalize_preview(reply_text)
                            mem.pending_unread = False
                            mem.pending_normal = False
                        self._remember_sent_for_row(
                            row, self._normalize_preview(reply_text), now
                        )
                        self._append_session_item(row, "A", reply_text)
                        self._save_persistent_memory()
                        time.sleep(self.cfg.poll_interval_sec)
                        continue

                latest_user_message = ""
                if context_snapshot.last_user_message:
                    latest_user_message = context_snapshot.last_user_message
                elif context_snapshot.last_line and context_snapshot.last_side == "other":
                    latest_user_message = context_snapshot.last_line
                else:
                    latest_user_message = row.preview or row.text

                memory_recall = self._workspace_memory_recall_for_row(
                    row,
                    latest_user_message or row.preview or row.text,
                    is_admin=is_admin,
                )

                session_context = self._build_session_context(row)
                if (not context_snapshot.chat_records) and latest_user_message:
                    self._append_session_record(
                        row,
                        role="user",
                        text=latest_user_message,
                        content_type="text",
                        sender="",
                        source="list",
                        count_turn=True,
                    )
                    session_context = self._build_session_context(row)
                if not context_snapshot.memory_summary:
                    self._update_long_summary(row)
                    session_context = self._build_session_context(row)

                if self._is_immediate_reply_event(row, reason):
                    should_reply = True
                else:
                    should_reply = self._llm_should_reply_with_context(
                        row,
                        reason,
                        is_group,
                        chat_context,
                        environment_context,
                        session_context,
                        workspace_context,
                        memory_recall,
                    )

                if not should_reply:
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                        mem.pending_normal = False
                    self._save_persistent_memory()
                    time.sleep(self.cfg.poll_interval_sec)
                    continue

                planned_reply = ""
                if self.cfg.agent_actions_enabled:
                    tools = self._available_agent_tools(is_admin=is_admin)
                    if tools:
                        try:
                            plan = self.llm_planner.plan_actions(
                                title=row.title,
                                is_group=is_group,
                                reason=reason,
                                latest_message=latest_user_message,
                                chat_context=chat_context,
                                environment_context=environment_context,
                                session_context=session_context,
                                workspace_context=workspace_context,
                                memory_recall=memory_recall,
                                available_tools=tools,
                                max_actions=self.cfg.agent_actions_max_per_turn,
                            )
                            planned_actions = (
                                plan.get("actions")
                                if isinstance(plan, dict) and isinstance(plan.get("actions"), list)
                                else []
                            )
                            planner_reply_hint = (
                                str(plan.get("reply_hint", "")).strip()
                                if isinstance(plan, dict)
                                else ""
                            )
                            trace, observations = self._execute_agent_actions(
                                row,
                                planned_actions,
                                is_admin=is_admin,
                            )
                            if trace and self.cfg.log_verbose:
                                print(f"[agent] row={row.row_idx:>2} | actions={len(planned_actions):>2}")
                                for ln in trace.split("\n"):
                                    if ln.strip():
                                        print(f"        {ln}")
                            if observations:
                                memory_recall = (
                                    f"{memory_recall}\n\n[工具执行结果]\n{observations}".strip()
                                )[:3600]
                            if planner_reply_hint and not planned_reply:
                                fallback = (
                                    self.cfg.reply_on_mention
                                    if reason == "mention"
                                    else self.cfg.reply_on_new_message
                                )
                                planned_reply = self._sanitize_generated_reply(
                                    planner_reply_hint,
                                    fallback=fallback,
                                )
                        except Exception as exc:
                            if self.cfg.agent_actions_fail_open:
                                print(f"[warn] agent action planner failed, fail-open: {exc}")
                            else:
                                raise
                message = self._reply(
                    row,
                    reason,
                    focused_bounds=focused_bounds,
                    chat_context=chat_context,
                    environment_context=environment_context,
                    session_context=session_context,
                    workspace_context=workspace_context,
                    memory_recall=memory_recall,
                    latest_message=latest_user_message,
                    force_message=planned_reply,
                )
                mem = self._baseline.get(row.row_idx)
                if mem is not None:
                    mem.last_replied_at = now
                    sent_norm = self._normalize_preview(message)
                    mem.last_sent_norm = sent_norm
                    mem.pending_unread = False
                    mem.pending_normal = False
                    self._remember_sent_for_row(row, sent_norm, now)
                    if message and self._is_normal_reply_event(row, reason):
                        self._last_normal_reply_at = now
                self._append_session_item(row, "A", message)
                self._save_persistent_memory()
            else:
                self._idle_streak += 1
                if self.cfg.log_verbose:
                    print(f"[idle] id={self._cycle} streak={self._idle_streak}")
                self._maybe_run_heartbeat(now, detected.rows)
                self._save_persistent_memory()

            time.sleep(self.cfg.poll_interval_sec)
