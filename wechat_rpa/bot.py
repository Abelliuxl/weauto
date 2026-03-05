from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import subprocess
import time

import numpy as np
import pyautogui
import pyperclip

from .config import AppConfig
from .detector import ChatRowState, detect_chat_rows
from .llm import LlmReplyGenerator
from .ocr import OcrEngine
from .window import WindowNotFoundError, get_front_window_bounds, screenshot_region

pyautogui.PAUSE = 0.1

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


@dataclass
class RowMemory:
    fingerprint: str
    preview_norm: str
    last_sent_norm: str
    has_unread_badge: bool
    pending_unread: bool
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
    schema: str = ""
    source: str = "ocr"


@dataclass
class SessionState:
    short: list[str]
    summary: str
    muted: bool
    titles: set[str]


class WeChatGuiRpaBot:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.ocr_engine = OcrEngine()
        self.llm = LlmReplyGenerator(cfg.llm, cfg.vision)
        self._baseline: dict[int, RowMemory] = {}
        # title_key -> (normalized_sent_text, sent_ts)
        self._sent_by_title: dict[str, tuple[str, float]] = {}
        self._sessions: dict[str, SessionState] = {}
        self._session_aliases: dict[str, str] = {}
        self._summary_turn_counter: dict[str, int] = defaultdict(int)
        self._memory_dirty = False
        self._memory_path = Path(self.cfg.memory_store_path)
        self._cycle = 0
        self._idle_streak = 0
        self._skip_first_action_pending = bool(self.cfg.skip_first_action_on_start)
        self._load_persistent_memory()

    def _to_np_rgb(self, pil_image) -> np.ndarray:
        return np.asarray(pil_image.convert("RGB"), dtype=np.uint8)

    def _normalize_preview(self, text: str) -> str:
        s = re.sub(r"\s+", "", text or "")
        # Suppress OCR jitter from punctuation/ellipsis differences.
        s = re.sub(r"[.…·•,，:：;；\-—_]+", "", s)
        return s

    def _title_key(self, title: str) -> str:
        t = self._normalize_preview(title)
        t = re.sub(r"\d{1,2}:\d{2}", "", t)
        return t[:24]

    def _get_or_create_session(self, key: str) -> SessionState:
        sess = self._sessions.get(key)
        if sess is not None:
            return sess
        sess = SessionState(short=[], summary="", muted=False, titles=set())
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
        sess = self._get_or_create_session(key)
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

    def _append_session_item(
        self, row: ChatRowState, role: str, text: str, count_turn: bool = True
    ) -> None:
        if not text:
            return
        key = self._session_key_for_row(row)
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return
        item = f"{role}:{clean[:140]}"
        sess = self._get_or_create_session(key)
        if sess.short and sess.short[-1] == item:
            return
        sess.short.append(item)
        max_items = max(4, self.cfg.memory_short_max_items)
        if len(sess.short) > max_items:
            del sess.short[:-max_items]
        self._remember_session_title(key, row.title)
        if role == "U" and count_turn:
            self._summary_turn_counter[key] += 1
        self._memory_dirty = True

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
            new_summary = self.llm.summarize_session(
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

        sessions = raw.get("sessions", {})
        if isinstance(sessions, dict):
            for key, data in sessions.items():
                if not isinstance(data, dict):
                    continue
                short = data.get("short", [])
                titles = data.get("titles", [])
                sess = SessionState(
                    short=[str(x) for x in short][-max(4, self.cfg.memory_short_max_items) :]
                    if isinstance(short, list)
                    else [],
                    summary=str(data.get("summary", ""))[: max(120, self.cfg.memory_summary_max_chars)],
                    muted=bool(data.get("muted", False)),
                    titles=set(str(x) for x in titles) if isinstance(titles, list) else set(),
                )
                self._sessions[str(key)] = sess

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
            payload = {
                "version": 1,
                "saved_at": int(time.time()),
                "sessions": {
                    key: {
                        "short": sess.short[-max(4, self.cfg.memory_short_max_items) :],
                        "summary": sess.summary[: max(120, self.cfg.memory_summary_max_chars)],
                        "muted": bool(sess.muted),
                        "titles": sorted(sess.titles),
                    }
                    for key, sess in self._sessions.items()
                },
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

    def _extract_chat_header_text(self, bounds: "WindowBounds") -> str:
        x = bounds.x + int(bounds.width * self.cfg.chat_title_region.x)
        y = bounds.y + int(bounds.height * self.cfg.chat_title_region.y)
        w = int(bounds.width * self.cfg.chat_title_region.w)
        h = int(bounds.height * self.cfg.chat_title_region.h)
        if w <= 0 or h <= 0:
            return ""
        shot = screenshot_region(x, y, w, h)
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
                "/reset 会话, /merge 源会话 -> 目标会话"
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
            sess.summary = ""
            self._summary_turn_counter[key] = 0
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
            self._memory_dirty = True
            return f"已合并: {src_name} -> {dst_name}"

        return f"未知命令: {cmd_line}"

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
        if not self.llm.is_enabled():
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
            row=row, reason=reason, is_group=is_group, chat_context="", session_context=""
        )

    def _llm_should_reply_with_context(
        self,
        row: ChatRowState,
        reason: str,
        is_group: bool,
        chat_context: str,
        session_context: str,
    ) -> bool:
        if not self._should_use_llm_decision(is_group):
            return True
        try:
            should_reply, why = self.llm.should_reply(
                title=row.title,
                preview=row.preview,
                reason=reason,
                is_group=is_group,
                chat_context=chat_context,
                session_context=session_context,
            )
            print(
                f"[llm-decision] row={row.row_idx} group={is_group} "
                f"decision={'reply' if should_reply else 'skip'} reason={why}"
            )
            return should_reply
        except Exception as exc:
            if self.cfg.llm.decision_fail_open:
                print(f"[warn] llm decision failed, fail-open: {exc}")
                return True
            print(f"[warn] llm decision failed, skip reply: {exc}")
            return False

    def _focus_chat(self, row: ChatRowState) -> "WindowBounds":
        self._activate_wechat()
        time.sleep(self.cfg.activate_wait_sec)
        bounds = get_front_window_bounds(self.cfg.app_name)
        row_x = bounds.x + int(bounds.width * row.click_x_ratio)
        row_y = bounds.y + int(bounds.height * row.click_y_ratio)
        tries = max(1, self.cfg.focus_verify_max_clicks)
        latest = bounds
        for i in range(1, tries + 1):
            self._safe_click(row_x, row_y)
            time.sleep(self.cfg.post_select_wait_sec)
            latest = get_front_window_bounds(self.cfg.app_name)
            if not self.cfg.focus_verify_enabled:
                return latest
            header = self._extract_chat_header_text(latest)
            if self._is_chat_header_matched(row.title, header):
                if self.cfg.log_verbose:
                    print(
                        f"[focus-ok] row={row.row_idx} try={i}/{tries} "
                        f"expect={row.title!r} seen={header!r}"
                    )
                return latest
            if self.cfg.log_verbose:
                print(
                    f"[focus-retry] row={row.row_idx} try={i}/{tries} "
                    f"expect={row.title!r} seen={header!r}"
                )
            time.sleep(max(0.02, self.cfg.focus_verify_wait_sec))
        return latest

    def _extract_chat_context(self, bounds: "WindowBounds", title: str = "") -> ChatContextSnapshot:
        x = bounds.x + int(bounds.width * self.cfg.chat_context_region.x)
        y = bounds.y + int(bounds.height * self.cfg.chat_context_region.y)
        w = int(bounds.width * self.cfg.chat_context_region.w)
        h = int(bounds.height * self.cfg.chat_context_region.h)
        if w <= 0 or h <= 0:
            return ChatContextSnapshot(text="", last_side="unknown", last_line="")

        shot = screenshot_region(x, y, w, h)
        ocr_snapshot = self._extract_chat_context_ocr(shot, w)
        vision_snapshot = self._extract_chat_context_vision(shot, title)
        if vision_snapshot:
            has_payload = bool(
                (vision_snapshot.text or "").strip()
                or (vision_snapshot.last_line or "").strip()
                or (vision_snapshot.last_user_message or "").strip()
                or vision_snapshot.recent_messages
            )
            if has_payload:
                # Prefer structured multimodal parsing when available.
                return vision_snapshot
            if self.cfg.log_verbose:
                print("[warn] vision context empty, fallback ocr")
        return ocr_snapshot

    def _extract_chat_context_ocr(self, shot, w: int) -> ChatContextSnapshot:
        rgb = self._to_np_rgb(shot)
        bgr = rgb[:, :, ::-1]
        lines = self.ocr_engine.detect_lines(bgr)
        if not lines:
            return ChatContextSnapshot(text="", last_side="unknown", last_line="")

        content: list[tuple[str, str]] = []
        for line in lines:
            text = (line.text or "").strip()
            if not text or _TIME_RE.match(text):
                continue
            x_ratio = float(line.x_center) / float(max(1, w))
            side = "self" if x_ratio >= self.cfg.chat_self_x_ratio else "other"
            content.append((text, side))

        if not content:
            return ChatContextSnapshot(text="", last_side="unknown", last_line="")

        tail = content[-self.cfg.chat_context_max_lines :]
        context = " | ".join([x[0] for x in tail])[:700]
        last_text, last_side = tail[-1]
        return ChatContextSnapshot(text=context, last_side=last_side, last_line=last_text[:120])

    def _extract_chat_context_vision(self, shot, title: str) -> ChatContextSnapshot | None:
        if not self.cfg.vision.enabled:
            return None
        try:
            parsed = self.llm.parse_chat_from_image(shot, title=title)
            last_speaker = parsed.get("last_speaker", "unknown")
            if last_speaker not in ("self", "other", "unknown"):
                last_speaker = "unknown"
            last_message = str(parsed.get("last_message", "")).strip()[:220]
            last_user_message = str(parsed.get("last_user_message", "")).strip()[:220]
            recent_messages = parsed.get("recent_messages") or []
            if not isinstance(recent_messages, list):
                recent_messages = []
            recent_messages = [str(x).strip() for x in recent_messages if str(x).strip()][:10]

            text = " | ".join(recent_messages)[:900]
            if not text:
                text = last_message[:900]
            if self.cfg.log_verbose:
                print(
                    f"[vision-context] speaker={last_speaker} "
                    f"last={last_message!r} recent={len(recent_messages)}"
                )
            return ChatContextSnapshot(
                text=text,
                last_side=last_speaker,
                last_line=last_message,
                last_user_message=last_user_message,
                recent_messages=recent_messages,
                recent_structured=parsed.get("recent_structured") or [],
                schema=str(parsed.get("schema", "")),
                source="vision",
            )
        except Exception as exc:
            if self.cfg.vision.fail_open:
                print(f"[warn] vision parse failed, fallback ocr: {exc}")
                return None
            raise

    def _log_cycle_snapshot(self, rows: list[ChatRowState], now: float) -> None:
        if not self.cfg.log_verbose:
            return
        print(
            f"[cycle] id={self._cycle} ts={int(now)} rows={len(rows)} "
            f"baseline={len(self._baseline)}"
        )
        limit = max(1, self.cfg.log_snapshot_rows)
        for row in rows[:limit]:
            group = self._is_group_chat(row)
            key = self._session_key_for_row(row)
            print(
                f"[row] idx={row.row_idx} group={group} unread={row.has_unread_badge} "
                f"mention={row.has_mention} key={key!r} title={row.title!r} "
                f"preview={row.preview!r}"
            )

    def _set_baseline(self, rows: list[ChatRowState], now: float) -> None:
        self._baseline = {
            row.row_idx: RowMemory(
                fingerprint=row.fingerprint,
                preview_norm=self._normalize_preview(row.preview),
                last_sent_norm="",
                has_unread_badge=(
                    False if self.cfg.process_existing_unread_on_start else row.has_unread_badge
                ),
                pending_unread=(row.has_unread_badge if self.cfg.process_existing_unread_on_start else False),
                has_mention=(False if self.cfg.process_existing_unread_on_start else row.has_mention),
                last_replied_at=0.0,
            )
            for row in rows
        }
        print(f"[init] baseline rows={len(rows)} at={now:.0f}")

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
                    fingerprint=row.fingerprint,
                    preview_norm=self._normalize_preview(row.preview),
                    last_sent_norm="",
                    has_unread_badge=row.has_unread_badge,
                    pending_unread=row.has_unread_badge,
                    has_mention=row.has_mention,
                    last_replied_at=0.0,
                )
                continue

            changed = row.fingerprint != prev.fingerprint
            preview_norm = self._normalize_preview(row.preview)
            preview_changed = self._is_preview_meaningfully_changed(
                prev.preview_norm, preview_norm
            )
            unread_rise = row.has_unread_badge and not prev.has_unread_badge
            unread_pending = prev.pending_unread
            mention_rise = row.has_mention and not prev.has_mention

            prev.fingerprint = row.fingerprint
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

            if self.cfg.debug_scan and (changed or preview_changed or unread_rise or mention_rise):
                print(
                    "[scan] "
                    f"row={row.row_idx} title={row.title!r} preview={row.preview!r} "
                    f"changed={changed} preview_changed={preview_changed} "
                    f"unread={row.has_unread_badge} mention={row.has_mention} "
                    f"self_echo={self_echo} pending_unread={unread_pending or unread_rise}"
                )

            if not (changed or preview_changed or unread_rise or unread_pending or mention_rise):
                continue

            # Prevent reply loops when the preview is our own last sent text.
            if self_echo:
                continue

            if now - prev.last_replied_at < self.cfg.action_cooldown_sec:
                continue

            if row.has_mention and (changed or mention_rise):
                mention_candidates.append(row)
            elif row.has_unread_badge and (changed or unread_rise or unread_pending):
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
                    return row, reason
                if is_group and not self._should_reply_group(row, reason):
                    if self.cfg.log_verbose or self.cfg.debug_scan:
                        print(
                            f"[skip-rule] group row={row.row_idx} title={row.title!r} "
                            f"reason={reason} preview={row.preview!r} "
                            f"group_only_reply_when_mentioned={self.cfg.group_only_reply_when_mentioned}"
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
        threshold = max(0.6, min(0.98, self.cfg.llm.anti_repeat_similarity))
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

    def _reply_text(self, row: ChatRowState, reason: str, chat_context: str, session_context: str) -> str:
        fallback = (
            self.cfg.reply_on_mention if reason == "mention" else self.cfg.reply_on_new_message
        )
        if not self.llm.is_enabled():
            return fallback
        recent_replies = self._recent_assistant_replies(
            row, max(1, self.cfg.llm.anti_repeat_window)
        )
        retries = max(0, self.cfg.llm.anti_repeat_retry) if self.cfg.llm.anti_repeat_enabled else 0
        try:
            avoid = list(recent_replies)
            for attempt in range(retries + 1):
                text = self.llm.generate(
                    title=row.title,
                    preview=row.preview,
                    reason=reason,
                    chat_context=chat_context,
                    session_context=session_context,
                    avoid_replies=(avoid if attempt > 0 else []),
                )
                clean = self._sanitize_generated_reply(text, fallback=fallback)
                if self.cfg.llm.anti_repeat_enabled and self._is_reply_too_similar(clean, recent_replies):
                    print(
                        f"[llm-repeat] row={row.row_idx} title={row.title!r} "
                        f"attempt={attempt + 1}/{retries + 1} reply={clean!r}"
                    )
                    avoid.append(clean)
                    if attempt < retries:
                        continue
                print(
                    f"[llm] generated reply len={len(text)} sanitized_len={len(clean)} "
                    f"attempt={attempt + 1}/{retries + 1}"
                )
                return clean
            return fallback
        except Exception as exc:
            print(f"[warn] llm failed, fallback to template: {exc}")
            return fallback

    def _sanitize_generated_reply(self, text: str, fallback: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return fallback
        # Remove markdown fences and collapse lines.
        raw = raw.replace("```", "").replace("\r", "\n")
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        merged = " ".join(lines).strip()

        def _clip_reply(s: str) -> str:
            val = re.sub(r"\s+", " ", (s or "").strip())
            if not val:
                return ""
            if len(val) > 140:
                val = val[:140].rstrip(" ,，。;；:：")
                if val:
                    val += "。"
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
        session_context: str = "",
        force_message: str = "",
    ) -> str:
        print(
            f"[action] reason={reason} row={row.row_idx} title={row.title!r} "
            f"preview={row.preview!r}"
        )
        message = force_message or self._reply_text(row, reason, chat_context, session_context)
        if self.cfg.dry_run:
            print(f"[dry-run] would send: {message}")
            return message

        bounds = focused_bounds if focused_bounds is not None else self._focus_chat(row)

        input_x = bounds.x + int(bounds.width * self.cfg.input_point.x)
        input_y = bounds.y + int(bounds.height * self.cfg.input_point.y)
        self._safe_click(input_x, input_y)
        time.sleep(0.05)
        self._safe_click(input_x, input_y)
        time.sleep(0.08)

        self._paste_and_send(message)
        print(f"[sent] {message}")
        return message

    def run_forever(self) -> None:
        print("[start] WeChat GUI RPA started")
        print(
            "[start] Make sure macOS permissions are granted: "
            "Accessibility + Screen Recording"
        )
        print(
            "[start] "
            f"group_only_reply_when_mentioned={self.cfg.group_only_reply_when_mentioned} "
            f"decision_enabled={self.cfg.llm.decision_enabled} "
            f"decision_on_group={self.cfg.llm.decision_on_group} "
            f"decision_on_private={self.cfg.llm.decision_on_private}"
        )
        print(
            "[start] "
            f"memory_enabled={self.cfg.memory_enabled} "
            f"memory_path={self._memory_path} "
            f"admin_titles={self.cfg.admin_session_titles}"
        )
        while True:
            self._cycle += 1
            now = time.time()
            try:
                bounds = get_front_window_bounds(self.cfg.app_name)
            except WindowNotFoundError as exc:
                print(f"[warn] {exc}")
                time.sleep(self.cfg.poll_interval_sec)
                continue

            shot = screenshot_region(bounds.x, bounds.y, bounds.width, bounds.height)
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
                row, reason = event
                if self._skip_first_action_pending:
                    self._skip_first_action_pending = False
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                    print(
                        f"[skip-startup-first-action] row={row.row_idx} "
                        f"reason={reason} title={row.title!r}"
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
                    time.sleep(self.cfg.poll_interval_sec)
                    continue
                if self.cfg.log_verbose:
                    print(
                        f"[event] id={self._cycle} row={row.row_idx} reason={reason} "
                        f"title={row.title!r}"
                    )
                is_group = self._is_group_chat(row)
                is_admin = self._is_admin_session(row)
                focused_bounds = None
                chat_context = ""
                context_snapshot = ChatContextSnapshot(text="", last_side="unknown", last_line="")
                need_context = not self.cfg.dry_run and (
                    (reason == "new_message" and self.cfg.skip_if_latest_chat_from_self)
                    or is_admin
                    or (
                        self._should_use_llm_decision(is_group)
                        and self.cfg.llm.decision_read_chat_context
                    )
                )
                if need_context:
                    focused_bounds = self._focus_chat(row)
                    context_snapshot = self._extract_chat_context(focused_bounds, title=row.title)
                    chat_context = context_snapshot.text
                    if self.cfg.debug_scan:
                        print(f"[context] row={row.row_idx} text={chat_context!r}")
                    if self.cfg.log_verbose:
                        print(
                            f"[context-meta] row={row.row_idx} source={context_snapshot.source} "
                            f"schema={context_snapshot.schema or '-'} "
                            f"last_side={context_snapshot.last_side} "
                            f"last_line={context_snapshot.last_line!r}"
                        )

                if (
                    reason == "new_message"
                    and self.cfg.skip_if_latest_chat_from_self
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
                            force_message=ack or "命令已执行。",
                        )
                        mem = self._baseline.get(row.row_idx)
                        if mem is not None:
                            mem.last_replied_at = now
                            mem.last_sent_norm = self._normalize_preview(reply_text)
                            mem.pending_unread = False
                        self._remember_sent_for_row(
                            row, self._normalize_preview(reply_text), now
                        )
                        self._append_session_item(row, "A", reply_text)
                        self._save_persistent_memory()
                        time.sleep(self.cfg.poll_interval_sec)
                        continue

                if context_snapshot.last_user_message:
                    self._append_session_item(row, "U", context_snapshot.last_user_message)
                elif context_snapshot.last_line and context_snapshot.last_side == "other":
                    self._append_session_item(row, "U", context_snapshot.last_line)
                else:
                    self._append_session_item(row, "U", row.preview or row.text)

                if context_snapshot.recent_messages:
                    for msg in context_snapshot.recent_messages[-6:]:
                        role = "U" if msg.startswith("U:") else ("A" if msg.startswith("A:") else "")
                        text = msg.split(":", 1)[1].strip() if ":" in msg else msg
                        if role and text:
                            self._append_session_item(row, role, text, count_turn=False)
                self._update_long_summary(row)
                session_context = self._build_session_context(row)

                if not self._llm_should_reply_with_context(
                    row, reason, is_group, chat_context, session_context
                ):
                    mem = self._baseline.get(row.row_idx)
                    if mem is not None:
                        mem.last_replied_at = now
                        mem.pending_unread = False
                    self._save_persistent_memory()
                    time.sleep(self.cfg.poll_interval_sec)
                    continue
                message = self._reply(
                    row,
                    reason,
                    focused_bounds=focused_bounds,
                    chat_context=chat_context,
                    session_context=session_context,
                )
                mem = self._baseline.get(row.row_idx)
                if mem is not None:
                    mem.last_replied_at = now
                    sent_norm = self._normalize_preview(message)
                    mem.last_sent_norm = sent_norm
                    mem.pending_unread = False
                    self._remember_sent_for_row(row, sent_norm, now)
                self._append_session_item(row, "A", message)
                self._save_persistent_memory()
            else:
                self._idle_streak += 1
                if self.cfg.log_verbose:
                    print(f"[idle] id={self._cycle} streak={self._idle_streak}")
                self._save_persistent_memory()

            time.sleep(self.cfg.poll_interval_sec)
