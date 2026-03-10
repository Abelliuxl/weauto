from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import EmbeddingConfig, RerankConfig


_DEFAULT_FILES: dict[str, str] = {
    "AGENTS.md": """# AGENTS.md

这是机器人工作区。

规则：
- 回复要自然、简洁、像真人，不要客服腔。
- 优先先看上下文，再行动。
- 要记住的事写进文件，不要只放在“脑子里”。
- 长期重要信息写到 MEMORY.md。
- 每日发生的事写到 memory/YYYY-MM-DD.md。
""",
    "SOUL.md": """# SOUL.md

你是微信里的游戏搭子型助手。

- 像真人玩家说话，简洁自然，不要客服腔
- 先看上下文和记忆，事实类问题先检索再答
- 允许轻度调侃，但不升级冲突
- 对外部会话谨慎，不泄露无关私密上下文
""",
    "IDENTITY.md": """# IDENTITY.md

- 名字：萨比
- 定位：真人玩家风格的微信助手
- 母亲：example_mother（小名示例称呼），创造者之一
- 父亲兼管理员：example_admin，创造者和管理者
""",
    "USER.md": """# USER.md

- 主人：本机使用者
- 备注：管理员会话可以更新长期记忆和行为规则；默认按年轻玩家风格回复
""",
    "TOOLS.md": """# TOOLS.md

- 当前项目：weauto
- 能力：检测微信窗口、截图、识别新消息、自动回复
""",
    "MEMORY.md": """# MEMORY.md

这里放长期记忆。

- 重要偏好
- 稳定事实
- 长期约定
""",
}

_NAME_PATTERNS = [
    re.compile(r"(?:我是|我叫|叫我|名字叫)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,12})"),
    re.compile(r"(?:他叫|她叫|ta叫|TA叫|这是)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,12})"),
    re.compile(
        r"([A-Za-z0-9_\u4e00-\u9fff]{2,12})是我(?:朋友|同事|同学|对象|老婆|老公|媳妇|男朋友|女朋友|家人)"
    ),
]

_FACT_PATTERNS = [
    re.compile(r"^我(?:喜欢|讨厌|现在在|目前在|最近在|住在|住|想|准备|打算|负责|做)\S+"),
    re.compile(r"^(?:他|她|ta|TA)(?:喜欢|讨厌|现在在|目前在|最近在|住在|住|准备|打算|负责|做)\S+"),
    re.compile(r".*(?:工作|上班|项目|副本|开荒|生日|结婚|请假|出差|搬家|住院|怀孕).*"),
]

_ALIAS_PATTERNS = [
    re.compile(
        r"(?:我叫|我是)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,12})[，,、 ]*(?:你叫我|叫我)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,12})"
    ),
    re.compile(
        r"([A-Za-z0-9_\u4e00-\u9fff]{2,12})(?:也叫|又叫|别名叫|外号叫)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,12})"
    ),
]

_REL_WORDS = (
    "朋友|同事|同学|对象|老婆|老公|媳妇|男朋友|女朋友|家人|姐姐|哥哥|弟弟|妹妹|"
    "领导|老板|客户|队友|搭子|室友|合伙人"
)
_RELATION_PATTERNS = [
    re.compile(rf"([A-Za-z0-9_\u4e00-\u9fff]{{2,12}})是我(?:的)?({_REL_WORDS})"),
    re.compile(rf"我是([A-Za-z0-9_\u4e00-\u9fff]{{2,12}})的({_REL_WORDS})"),
    re.compile(
        rf"([A-Za-z0-9_\u4e00-\u9fff]{{2,12}})是([A-Za-z0-9_\u4e00-\u9fff]{{2,12}})的({_REL_WORDS})"
    ),
]


@dataclass
class MemoryHit:
    path: str
    score: float
    snippet: str


class WorkspaceContextManager:
    def __init__(
        self,
        root: str | Path,
        *,
        enabled: bool = True,
        embedding_cfg: EmbeddingConfig | None = None,
        rerank_cfg: RerankConfig | None = None,
        memory_rerank_enabled: bool = False,
        memory_rerank_shortlist: int = 24,
        memory_rerank_weight: float = 2.5,
        memory_sqlite_enabled: bool = False,
        memory_sqlite_path: str | Path = "data/workspace_memory.sqlite3",
        memory_sqlite_sync_interval_sec: float = 20.0,
        memory_sqlite_fts_limit: int = 64,
        memory_sqlite_vector_limit: int = 24,
        memory_sqlite_chunk_chars: int = 320,
    ) -> None:
        self.enabled = bool(enabled)
        self.root = Path(root)
        self.memory_dir = self.root / "memory"
        self.session_dir = self.memory_dir / "sessions"
        self.session_state_dir = self.memory_dir / "session_state"
        self.embedding_cfg = embedding_cfg or EmbeddingConfig()
        self.rerank_cfg = rerank_cfg or RerankConfig()
        self.memory_rerank_enabled = bool(memory_rerank_enabled)
        self.memory_rerank_shortlist = max(1, int(memory_rerank_shortlist))
        self.memory_rerank_weight = max(0.0, float(memory_rerank_weight))
        self._embedding_cache: dict[str, list[float] | None] = {}
        self._embedding_warned = False
        self._rerank_warned = False
        self.memory_sqlite_enabled = bool(memory_sqlite_enabled)
        self.memory_sqlite_path = Path(memory_sqlite_path)
        self.memory_sqlite_sync_interval_sec = max(2.0, float(memory_sqlite_sync_interval_sec))
        self.memory_sqlite_fts_limit = max(4, int(memory_sqlite_fts_limit))
        self.memory_sqlite_vector_limit = max(1, int(memory_sqlite_vector_limit))
        self.memory_sqlite_chunk_chars = max(120, int(memory_sqlite_chunk_chars))
        self._memory_sqlite_conn: sqlite3.Connection | None = None
        self._memory_sqlite_warned = False
        self._memory_sqlite_dirty = True
        self._memory_sqlite_last_sync = 0.0

    def rerank_status_text(self) -> str:
        if not self.memory_rerank_enabled:
            return "disabled (workspace_memory_rerank_enabled=false)"
        reason = self._rerank_unavailable_reason()
        if reason:
            return f"disabled ({reason})"
        return (
            f"enabled model={self.rerank_cfg.model} "
            f"shortlist={self.memory_rerank_shortlist} "
            f"weight={self.memory_rerank_weight:.2f}"
        )

    def sqlite_status_text(self) -> str:
        if not self.memory_sqlite_enabled:
            return "disabled (workspace_memory_sqlite_enabled=false)"
        return (
            f"enabled path={self.memory_sqlite_path} "
            f"fts_limit={self.memory_sqlite_fts_limit} "
            f"vector_limit={self.memory_sqlite_vector_limit}"
        )

    def ensure_bootstrap_files(self) -> None:
        if not self.enabled:
            return
        touched = False
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_state_dir.mkdir(parents=True, exist_ok=True)
        for name, content in _DEFAULT_FILES.items():
            path = self.root / name
            if not path.exists():
                path.write_text(content.strip() + "\n", encoding="utf-8")
                touched = True
        if touched:
            self._mark_sqlite_dirty()

    def _safe_read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _safe_json_load(self, path: Path) -> dict:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return {}

    def _safe_json_dump(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._mark_sqlite_dirty()

    def build_prompt_context(self, *, include_long_term: bool) -> str:
        if not self.enabled:
            return ""
        names = ["AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md"]
        if include_long_term:
            names.append("MEMORY.md")
        parts: list[str] = []
        for name in names:
            path = self.root / name
            content = self._safe_read(path)
            if not content:
                continue
            parts.append(f"[{name}]\n{content[:4000]}")
        return "\n\n".join(parts)[:12000]

    def _format_record_line(
        self,
        *,
        stamp: str,
        title: str,
        role: str,
        text: str,
        sender: str = "",
    ) -> str:
        short_role = "A" if role == "assistant" else ("U" if role == "user" else "?")
        clean_sender = self._normalize_name(sender)
        if role == "user" and (not clean_sender):
            title_name = self._normalize_name(title)
            if title_name and (not title_name.startswith("群")):
                clean_sender = title_name
        sender_prefix = f"({clean_sender})" if (role == "user" and clean_sender) else ""
        return f"- [{stamp}] [{title}] {short_role}{sender_prefix}: {text}"

    def append_record(
        self,
        *,
        session_key: str,
        title: str,
        role: str,
        text: str,
        sender: str = "",
    ) -> None:
        if not self.enabled:
            return
        clean = self._clip_text(text, 500)
        if not clean:
            return

        now = datetime.now()
        day_path = self.memory_dir / f"{now:%Y-%m-%d}.md"
        session_path = self.session_dir / f"{self._slug(session_key or title or 'session')}.md"
        line = self._format_record_line(
            stamp=now.strftime("%Y-%m-%d %H:%M:%S"),
            title=(title or session_key),
            role=role,
            text=clean,
            sender=sender,
        )

        if not day_path.exists():
            day_path.write_text(f"# Daily Memory {now:%Y-%m-%d}\n\n", encoding="utf-8")
        with day_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        if not session_path.exists():
            session_path.write_text(
                f"# Session Memory\n\n- key: {session_key}\n- title: {title}\n\n",
                encoding="utf-8",
            )
        with session_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._mark_sqlite_dirty()

    def rewrite_session_records(
        self,
        *,
        session_key: str,
        title: str,
        records: list[dict] | None,
    ) -> None:
        if not self.enabled:
            return
        session_path = self.session_dir / f"{self._slug(session_key or title or 'session')}.md"
        lines = [f"# Session Memory", "", f"- key: {session_key}", f"- title: {title}", ""]
        for record in records or []:
            if not isinstance(record, dict):
                continue
            clean = self._clip_text(record.get("text", ""), 500)
            if not clean:
                continue
            observed_at = int(record.get("observed_at", 0) or 0)
            try:
                stamp = (
                    datetime.fromtimestamp(observed_at).strftime("%Y-%m-%d %H:%M:%S")
                    if observed_at > 0
                    else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            except Exception:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                self._format_record_line(
                    stamp=stamp,
                    title=(title or session_key),
                    role=str(record.get("role", "unknown")),
                    text=clean,
                    sender=str(record.get("sender", "")),
                )
            )
        session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._mark_sqlite_dirty()

    def append_long_term_memory(self, note: str) -> None:
        if not self.enabled:
            return
        clean = self._clip_text(note, 500)
        if not clean:
            return
        path = self.root / "MEMORY.md"
        if not path.exists():
            path.write_text(_DEFAULT_FILES["MEMORY.md"].strip() + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n- {clean}\n")
        self._mark_sqlite_dirty()

    def reset_session_memory(self, *, session_key: str, title: str) -> None:
        if not self.enabled:
            return
        path = self.session_dir / f"{self._slug(session_key or title or 'session')}.md"
        header = (
            "# Session Memory\n\n"
            f"- key: {session_key}\n"
            f"- title: {title}\n\n"
        )
        path.write_text(header, encoding="utf-8")
        self._safe_json_dump(
            self._session_state_path(session_key),
            self._new_session_state(session_key=session_key, title=title),
        )
        self._mark_sqlite_dirty()

    def merge_session_memory(
        self,
        *,
        src_key: str,
        dst_key: str,
        dst_title: str,
    ) -> None:
        if not self.enabled or src_key == dst_key:
            return
        src_md = self.session_dir / f"{self._slug(src_key)}.md"
        dst_md = self.session_dir / f"{self._slug(dst_key or dst_title)}.md"
        src_lines = self._session_memory_lines(src_md)
        dst_lines = self._session_memory_lines(dst_md)
        if src_lines:
            if not dst_md.exists():
                dst_md.write_text(
                    "# Session Memory\n\n"
                    f"- key: {dst_key}\n"
                    f"- title: {dst_title or dst_key}\n\n",
                    encoding="utf-8",
                )
            seen_lines = set(dst_lines)
            merged_lines = [line for line in src_lines if line not in seen_lines]
            if merged_lines:
                with dst_md.open("a", encoding="utf-8") as fh:
                    for line in merged_lines:
                        fh.write(line + "\n")
        src = self._load_session_state(session_key=src_key, title=src_key)
        dst = self._load_session_state(session_key=dst_key, title=dst_title)
        stamp = self._now_iso()

        if src.get("profile", {}).get("summary") and not dst.get("profile", {}).get("summary"):
            dst.setdefault("profile", {})["summary"] = src["profile"]["summary"]

        for person in src.get("people") or []:
            if not isinstance(person, dict):
                continue
            self._merge_person_payload(dst, person, stamp=stamp)

        for fact in src.get("facts") or []:
            if isinstance(fact, dict):
                self._upsert_fact(dst, str(fact.get("text", "")), stamp=stamp)

        for relation in src.get("relations") or []:
            if isinstance(relation, dict):
                self._upsert_relation(
                    dst,
                    subject=str(relation.get("subject", "")),
                    relation=str(relation.get("relation", "")),
                    target=str(relation.get("target", "")),
                    note=str(relation.get("note", "")),
                    stamp=stamp,
                )

        for event in src.get("events") or []:
            if isinstance(event, dict):
                self._append_event(dst, str(event.get("text", "")), stamp=stamp)

        dst["title"] = dst_title or str(dst.get("title", "") or dst_key)
        dst["updated_at"] = stamp
        self._save_session_state(dst)
        self._mark_sqlite_dirty()

    def update_session_summary(self, *, session_key: str, title: str, summary: str) -> None:
        if not self.enabled:
            return
        clean = self._clip_text(summary, 240)
        if not clean:
            return
        state = self._load_session_state(session_key=session_key, title=title)
        state.setdefault("profile", {})["summary"] = clean
        state["updated_at"] = self._now_iso()
        self._save_session_state(state)

    def _remember_structured_into_state(
        self,
        state: dict,
        *,
        title: str,
        records: list[dict] | None = None,
        summary: str = "",
        people: list[dict] | None = None,
        facts: list[str] | None = None,
        events: list[str] | None = None,
        relations: list[dict] | None = None,
        stamp: str,
    ) -> None:
        if summary:
            state.setdefault("profile", {})["summary"] = self._clip_text(summary, 240)

        for person in people or []:
            if not isinstance(person, dict):
                continue
            self._upsert_person(
                state,
                name=str(person.get("name", "")),
                alias=str(person.get("alias", "")),
                note=str(
                    person.get("description", "")
                    or person.get("identity", "")
                    or person.get("note", "")
                ),
                stamp=stamp,
            )

        for fact in facts or []:
            self._upsert_fact(state, str(fact), stamp=stamp)

        for event in events or []:
            self._append_event(state, str(event), stamp=stamp)

        for relation in relations or []:
            if not isinstance(relation, dict):
                continue
            self._upsert_relation(
                state,
                subject=str(relation.get("subject", "")),
                relation=str(relation.get("relation", "")),
                target=str(relation.get("target", "")),
                note=str(relation.get("note", "")),
                stamp=stamp,
            )

        for record in records or []:
            if not isinstance(record, dict):
                continue
            role = str(record.get("role", "unknown")).strip().lower()
            text = self._clip_text(record.get("text", ""), 220)
            sender = self._normalize_name(str(record.get("sender", "")))
            if role == "user" and (not sender):
                title_name = self._normalize_name(title)
                if title_name and (not title_name.startswith("群")):
                    sender = title_name
            if sender:
                self._upsert_person(
                    state,
                    name=sender,
                    alias="",
                    note="在该会话中出现的发送者",
                    stamp=stamp,
                )
            if role == "user":
                if sender:
                    self._append_event(state, f"{sender}: {text}", stamp=stamp)
                else:
                    self._append_event(state, text, stamp=stamp)
                for name in self._extract_names_from_text(text):
                    self._upsert_person(state, name=name, alias="", note="在聊天中被提及", stamp=stamp)
                for fact in self._extract_fact_candidates(text):
                    self._upsert_fact(state, fact, stamp=stamp)
                for canonical_name, alias_name in self._extract_alias_pairs(text, sender=sender):
                    self._merge_person_alias(
                        state,
                        canonical_name=canonical_name,
                        alias_name=alias_name,
                        note="在聊天中声明的别名",
                        stamp=stamp,
                    )
                for rel_subject, rel_name, rel_target, rel_note in self._extract_relation_candidates(
                    text,
                    sender=sender,
                ):
                    self._upsert_relation(
                        state,
                        subject=rel_subject,
                        relation=rel_name,
                        target=rel_target,
                        note=rel_note,
                        stamp=stamp,
                    )
            elif role == "assistant" and text:
                self._append_event(state, f"我回复: {text}", stamp=stamp)

    def rewrite_session_structured(
        self,
        *,
        session_key: str,
        title: str,
        records: list[dict] | None = None,
        summary: str = "",
    ) -> None:
        if not self.enabled:
            return
        state = self._new_session_state(session_key=session_key, title=title)
        stamp = self._now_iso()
        self._remember_structured_into_state(
            state,
            title=title,
            records=records,
            summary=summary,
            stamp=stamp,
        )
        state["updated_at"] = stamp
        self._save_session_state(state)

    def remember_structured(
        self,
        *,
        session_key: str,
        title: str,
        records: list[dict] | None = None,
        summary: str = "",
        people: list[dict] | None = None,
        facts: list[str] | None = None,
        events: list[str] | None = None,
        relations: list[dict] | None = None,
    ) -> None:
        if not self.enabled:
            return
        state = self._load_session_state(session_key=session_key, title=title)
        stamp = self._now_iso()
        self._remember_structured_into_state(
            state,
            title=title,
            records=records,
            summary=summary,
            people=people,
            facts=facts,
            events=events,
            relations=relations,
            stamp=stamp,
        )
        state["updated_at"] = stamp
        self._save_session_state(state)

    def build_session_memory_brief(
        self,
        *,
        session_key: str,
        title: str,
        query: str = "",
        max_chars: int = 2200,
    ) -> str:
        if not self.enabled:
            return ""
        state = self._load_session_state(session_key=session_key, title=title)
        if not state:
            return ""

        parts: list[str] = []
        summary = self._clip_text(state.get("profile", {}).get("summary", ""), 220)
        if summary:
            parts.append(f"[会话画像]\n- {summary}")

        people_pool = [
            person for person in (state.get("people") or []) if self._is_valid_person_entry(person)
        ]
        roster_query = self._looks_like_group_roster_query(query)

        if query.strip():
            people_candidates = self._prepare_rank_candidates(
                items=people_pool,
                query=query,
                text_getter=lambda item: " ".join(
                    [
                        str(item.get("name", "")),
                        " ".join(item.get("aliases") or []),
                        " ".join(item.get("notes") or []),
                    ]
                ),
                base_boost_getter=lambda item: float(item.get("mentions", 0) or 0) * 0.08,
                shortlist=14,
            )
            facts_candidates = self._prepare_rank_candidates(
                items=state.get("facts") or [],
                query=query,
                text_getter=lambda item: str(item.get("text", "")),
                base_boost_getter=lambda item: float(item.get("hits", 0) or 0) * 0.08,
                shortlist=14,
            )
            relations_candidates = self._prepare_rank_candidates(
                items=state.get("relations") or [],
                query=query,
                text_getter=lambda item: " ".join(
                    [
                        str(item.get("subject", "")),
                        str(item.get("relation", "")),
                        str(item.get("target", "")),
                        str(item.get("note", "")),
                    ]
                ),
                base_boost_getter=lambda item: float(item.get("hits", 0) or 0) * 0.08,
                shortlist=14,
            )
            recent_events = list(reversed((state.get("events") or [])[-12:]))
            events_candidates = self._prepare_rank_candidates(
                items=recent_events,
                query=query,
                text_getter=lambda item: str(item.get("text", "")),
                shortlist=12,
            )
            embedding_scores = self._embedding_similarity_map(
                query,
                [
                    candidate["text"]
                    for candidate in (
                        people_candidates
                        + facts_candidates
                        + relations_candidates
                        + events_candidates
                    )
                    if candidate.get("text")
                ],
            )
            people_limit = 16 if roster_query else 8
            people = self._finalize_rank_candidates(people_candidates, embedding_scores)[:people_limit]
            facts = self._finalize_rank_candidates(facts_candidates, embedding_scores)[:8]
            relations = self._finalize_rank_candidates(relations_candidates, embedding_scores)[:8]
            events = self._finalize_rank_candidates(events_candidates, embedding_scores)[:6]
        else:
            people = list(people_pool)[:8]
            facts = list(state.get("facts") or [])[:8]
            relations = list(state.get("relations") or [])[:8]
            events = list(reversed((state.get("events") or [])[-6:]))

        if people:
            lines = []
            for person in people:
                name = str(person.get("name", "")).strip()
                aliases = [
                    self._clip_text(x, 20)
                    for x in (person.get("aliases") or [])
                    if str(x).strip()
                ]
                notes = [
                    self._clip_text(x, 40)
                    for x in (person.get("notes") or [])
                    if str(x).strip()
                ]
                tail = []
                if aliases:
                    tail.append("别名=" + ",".join(aliases[:3]))
                if notes:
                    tail.append("说明=" + " / ".join(notes[:2]))
                if name:
                    lines.append(f"- {name}" + (f"；{';'.join(tail)}" if tail else ""))
            if lines:
                parts.append("[人物]\n" + "\n".join(lines[:8]))

        if relations:
            lines = []
            for item in relations:
                subject = str(item.get("subject", "")).strip()
                relation = str(item.get("relation", "")).strip()
                target = str(item.get("target", "")).strip()
                note = self._clip_text(item.get("note", ""), 40)
                if not subject or not relation or not target:
                    continue
                line = f"- {subject} -> {relation} -> {target}"
                if note:
                    line += f"；说明={note}"
                lines.append(line)
            if lines:
                parts.append("[关系]\n" + "\n".join(lines[:8]))

        if facts:
            lines = [f"- {self._clip_text(item.get('text', ''), 80)}" for item in facts]
            lines = [line for line in lines if line.strip() != "-"]
            if lines:
                parts.append("[事实]\n" + "\n".join(lines[:8]))

        if events:
            lines = []
            for item in events:
                stamp = str(item.get("ts", ""))[:10]
                text = self._clip_text(item.get("text", ""), 90)
                if not text:
                    continue
                lines.append(f"- {stamp} {text}" if stamp else f"- {text}")
            if lines:
                parts.append("[最近事件]\n" + "\n".join(lines[:6]))

        return "\n\n".join(parts)[:max_chars]

    def search_memory(
        self,
        *,
        query: str,
        session_key: str,
        include_global: bool,
        limit: int = 3,
    ) -> str:
        if not self.enabled:
            return ""
        clean_query = re.sub(r"\s+", " ", query or "").strip()
        if len(clean_query) < 2:
            return ""

        hits: list[MemoryHit] = []
        if self.memory_sqlite_enabled:
            hits = self._search_memory_hits_sqlite(
                clean_query,
                session_key=session_key,
                include_global=include_global,
                limit=max(1, limit),
            )
        if not hits:
            for path in self._candidate_files(session_key=session_key, include_global=include_global):
                content = self._safe_read(path)
                if not content:
                    continue
                hits.extend(self._score_file(path, content, clean_query))

        hits.sort(key=lambda item: item.score, reverse=True)
        if hits and self.memory_rerank_enabled:
            hits = self._rerank_memory_hits(
                clean_query,
                hits,
                limit=max(1, limit),
            )
        lines: list[str] = []
        seen: set[str] = set()
        for hit in hits:
            key = f"{hit.path}|{hit.snippet}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[{hit.path}] {hit.snippet}")
            if len(lines) >= max(1, limit):
                break
        return "\n".join(lines)[:2400]

    def _mark_sqlite_dirty(self) -> None:
        if not self.memory_sqlite_enabled:
            return
        self._memory_sqlite_dirty = True

    def _sqlite_conn(self) -> sqlite3.Connection | None:
        if (not self.enabled) or (not self.memory_sqlite_enabled):
            return None
        if self._memory_sqlite_conn is not None:
            return self._memory_sqlite_conn
        try:
            db_path = self.memory_sqlite_path
            if not db_path.is_absolute():
                db_path = (Path.cwd() / db_path).resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), timeout=12.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._sqlite_init_schema(conn)
            self._memory_sqlite_conn = conn
            self.memory_sqlite_path = db_path
            return conn
        except Exception as exc:
            if not self._memory_sqlite_warned:
                print(f"[warn] sqlite memory index unavailable, fallback to file recall: {exc}")
                self._memory_sqlite_warned = True
            return None

    def _sqlite_init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_sources (
                source_path TEXT PRIMARY KEY,
                source_hash TEXT NOT NULL,
                scope TEXT NOT NULL,
                session_slug TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                scope TEXT NOT NULL,
                session_slug TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                snippet TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_chunks_scope ON memory_chunks(scope, session_slug)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_chunks_source ON memory_chunks(source_path)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts "
            "USING fts5(snippet, tokenize='unicode61')"
        )
        conn.commit()

    def _collect_index_sources(self) -> list[tuple[Path, str, str]]:
        out: list[tuple[Path, str, str]] = []
        long_term = self.root / "MEMORY.md"
        if long_term.exists():
            out.append((long_term, "long_term", ""))
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.is_file():
                out.append((path, "daily", ""))
        for path in sorted(self.session_dir.glob("*.md")):
            if path.is_file():
                out.append((path, "session", self._slug(path.stem)))
        for path in sorted(self.session_state_dir.glob("*.json")):
            if path.is_file():
                out.append((path, "session_state", self._slug(path.stem)))
        return out

    def _source_relpath(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except Exception:
            return str(path)

    def _split_index_chunks(self, text: str) -> list[str]:
        clean_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not clean_text.strip():
            return []
        limit = self.memory_sqlite_chunk_chars
        chunks: list[str] = []
        sections = [sec.strip() for sec in re.split(r"\n\s*\n", clean_text) if sec.strip()]
        if not sections:
            sections = [clean_text.strip()]

        for section in sections:
            lines = []
            for line in section.splitlines():
                normalized = re.sub(r"\s+", " ", line).strip()
                if not normalized:
                    continue
                if normalized.startswith("#"):
                    continue
                lines.append(normalized)
            if not lines:
                continue
            merged = " ".join(lines).strip()
            if len(merged) <= limit:
                chunks.append(merged)
                continue

            sentences = [x.strip() for x in re.split(r"(?<=[。！？!?；;])\s*", merged) if x.strip()]
            if not sentences:
                sentences = [merged]
            buf = ""
            for sent in sentences:
                if len(sent) > limit:
                    if buf:
                        chunks.append(buf[:limit])
                        buf = ""
                    for i in range(0, len(sent), limit):
                        part = sent[i : i + limit].strip()
                        if part:
                            chunks.append(part)
                    continue
                candidate = f"{buf} {sent}".strip() if buf else sent
                if len(candidate) <= limit:
                    buf = candidate
                else:
                    if buf:
                        chunks.append(buf)
                    buf = sent
            if buf:
                chunks.append(buf)
        uniq: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            normalized = re.sub(r"\s+", " ", chunk).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(normalized)
            if len(uniq) >= 3000:
                break
        return uniq

    def _session_state_text(self, path: Path) -> str:
        data = self._safe_json_load(path)
        if not data:
            return ""
        lines: list[str] = []
        title = self._clip_text(data.get("title", ""), 40)
        if title:
            lines.append(f"会话: {title}")
        summary = self._clip_text((data.get("profile") or {}).get("summary", ""), 240)
        if summary:
            lines.append(f"会话摘要: {summary}")
        for person in (data.get("people") or [])[:80]:
            if not isinstance(person, dict):
                continue
            name = self._clip_text(person.get("name", ""), 24)
            if not name:
                continue
            aliases = ",".join(
                self._clip_text(item, 16) for item in (person.get("aliases") or [])[:3] if str(item).strip()
            )
            notes = " / ".join(
                self._clip_text(item, 36) for item in (person.get("notes") or [])[:2] if str(item).strip()
            )
            tail = " ".join(x for x in [aliases, notes] if x)
            lines.append(f"人物: {name}" + (f" {tail}" if tail else ""))
        for fact in (data.get("facts") or [])[:120]:
            if not isinstance(fact, dict):
                continue
            text = self._clip_text(fact.get("text", ""), 180)
            if text:
                lines.append(f"事实: {text}")
        for rel in (data.get("relations") or [])[:120]:
            if not isinstance(rel, dict):
                continue
            subject = self._clip_text(rel.get("subject", ""), 24)
            relation = self._clip_text(rel.get("relation", ""), 20)
            target = self._clip_text(rel.get("target", ""), 24)
            if subject and relation and target:
                note = self._clip_text(rel.get("note", ""), 60)
                lines.append(
                    f"关系: {subject} -> {relation} -> {target}" + (f" ({note})" if note else "")
                )
        for event in (data.get("events") or [])[-160:]:
            if not isinstance(event, dict):
                continue
            text = self._clip_text(event.get("text", ""), 180)
            if text:
                lines.append(f"事件: {text}")
        return "\n".join(lines)

    def _source_chunks(self, path: Path, scope: str) -> list[str]:
        if scope == "session_state":
            return self._split_index_chunks(self._session_state_text(path))
        return self._split_index_chunks(self._safe_read(path))

    def _sync_memory_sqlite_index(self, *, force: bool = False) -> None:
        if (not self.memory_sqlite_enabled) or (not self.enabled):
            return
        now = time.time()
        if (
            (not force)
            and (self._memory_sqlite_last_sync > 0.0)
            and ((now - self._memory_sqlite_last_sync) < self.memory_sqlite_sync_interval_sec)
        ):
            return
        conn = self._sqlite_conn()
        if conn is None:
            return

        sources = self._collect_index_sources()
        current_paths = {self._source_relpath(path) for path, _, _ in sources}
        existing_rows = conn.execute(
            "SELECT source_path, source_hash, scope, session_slug FROM memory_sources"
        ).fetchall()
        existing_map = {
            str(row["source_path"]): (
                str(row["source_hash"]),
                str(row["scope"]),
                str(row["session_slug"]),
            )
            for row in existing_rows
        }

        touched = False
        with conn:
            for stale_path in sorted(set(existing_map.keys()) - current_paths):
                conn.execute(
                    "DELETE FROM memory_chunks_fts WHERE rowid IN "
                    "(SELECT id FROM memory_chunks WHERE source_path = ?)",
                    (stale_path,),
                )
                conn.execute("DELETE FROM memory_chunks WHERE source_path = ?", (stale_path,))
                conn.execute("DELETE FROM memory_sources WHERE source_path = ?", (stale_path,))
                touched = True

            for path, scope, session_slug in sources:
                relpath = self._source_relpath(path)
                try:
                    raw_bytes = path.read_bytes()
                except Exception:
                    raw_bytes = b""
                source_hash = hashlib.sha1(raw_bytes).hexdigest()
                prev = existing_map.get(relpath)
                if prev and prev == (source_hash, scope, session_slug):
                    continue

                conn.execute(
                    "DELETE FROM memory_chunks_fts WHERE rowid IN "
                    "(SELECT id FROM memory_chunks WHERE source_path = ?)",
                    (relpath,),
                )
                conn.execute("DELETE FROM memory_chunks WHERE source_path = ?", (relpath,))
                chunks = self._source_chunks(path, scope)
                for idx, snippet in enumerate(chunks):
                    cur = conn.execute(
                        "INSERT INTO memory_chunks(source_path, scope, session_slug, chunk_index, snippet) "
                        "VALUES(?,?,?,?,?)",
                        (relpath, scope, session_slug, idx, snippet),
                    )
                    row_id = int(cur.lastrowid or 0)
                    if row_id > 0:
                        conn.execute(
                            "INSERT INTO memory_chunks_fts(rowid, snippet) VALUES(?, ?)",
                            (row_id, snippet),
                        )
                conn.execute(
                    "INSERT INTO memory_sources(source_path, source_hash, scope, session_slug, updated_at) "
                    "VALUES(?,?,?,?,?) "
                    "ON CONFLICT(source_path) DO UPDATE SET "
                    "source_hash=excluded.source_hash, "
                    "scope=excluded.scope, "
                    "session_slug=excluded.session_slug, "
                    "updated_at=excluded.updated_at",
                    (relpath, source_hash, scope, session_slug, int(now)),
                )
                touched = True

        self._memory_sqlite_last_sync = now
        self._memory_sqlite_dirty = False
        if touched:
            print(
                f"[memory-sqlite] synced sources={len(sources)} path={self.memory_sqlite_path}"
            )

    def _sqlite_scope_clause(
        self,
        *,
        include_global: bool,
        session_key: str,
    ) -> tuple[str, list[str]]:
        if include_global:
            return "", []
        session_slug = self._slug(session_key or "")
        return " AND c.scope IN ('session','session_state') AND c.session_slug = ? ", [session_slug]

    def _sqlite_match_query(self, query: str) -> str:
        tokens = self._tokens(query)
        if not tokens:
            clean = re.sub(r"\s+", " ", query or "").strip()
            if not clean:
                return ""
            return f"\"{clean.replace('\"', '')[:80]}\""
        phrases = [f"\"{token.replace('\"', '')}\"" for token in tokens[:12]]
        return " OR ".join(phrases)

    def _search_memory_hits_sqlite(
        self,
        query: str,
        *,
        session_key: str,
        include_global: bool,
        limit: int,
    ) -> list[MemoryHit]:
        self._sync_memory_sqlite_index()
        conn = self._sqlite_conn()
        if conn is None:
            return []

        match_query = self._sqlite_match_query(query)
        if not match_query:
            return []
        scope_clause, scope_params = self._sqlite_scope_clause(
            include_global=include_global,
            session_key=session_key,
        )
        fetch_limit = max(max(1, limit), self.memory_sqlite_fts_limit)
        sql = (
            "SELECT c.source_path, c.snippet, bm25(memory_chunks_fts) AS bm "
            "FROM memory_chunks_fts "
            "JOIN memory_chunks c ON c.id = memory_chunks_fts.rowid "
            "WHERE memory_chunks_fts MATCH ? "
            + scope_clause
            + " ORDER BY bm ASC LIMIT ?"
        )
        params: list[object] = [match_query]
        params.extend(scope_params)
        params.append(fetch_limit)

        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:
            if not self._memory_sqlite_warned:
                print(f"[warn] sqlite FTS query failed, fallback to file recall: {exc}")
                self._memory_sqlite_warned = True
            return []

        hits: list[MemoryHit] = []
        query_tokens = self._tokens(query)
        for row in rows:
            snippet = self._clip_text(row["snippet"], 320)
            if not snippet:
                continue
            try:
                bm25_raw = float(row["bm"])
            except Exception:
                bm25_raw = 0.0
            lexical = 1.0 / (1.0 + abs(bm25_raw))
            norm = self._norm(snippet)
            lexical += 0.15 * sum(1 for token in query_tokens if token in norm)
            hits.append(
                MemoryHit(
                    path=str(row["source_path"]),
                    score=float(lexical),
                    snippet=snippet,
                )
            )
        if not hits:
            return []

        if self._embedding_enabled():
            shortlist = hits[: max(1, self.memory_sqlite_vector_limit)]
            embed_scores = self._embedding_similarity_map(
                query,
                [item.snippet for item in shortlist],
            )
            if embed_scores:
                for item in hits:
                    item.score += float(embed_scores.get(item.snippet, 0.0) or 0.0) * 2.0
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits

    def _rerank_memory_hits(
        self,
        query: str,
        hits: list[MemoryHit],
        *,
        limit: int,
    ) -> list[MemoryHit]:
        shortlist_size = max(limit, self.memory_rerank_shortlist)
        shortlist: list[MemoryHit] = []
        seen: set[str] = set()
        for hit in hits:
            key = f"{hit.path}|{hit.snippet}"
            if key in seen:
                continue
            seen.add(key)
            shortlist.append(hit)
            if len(shortlist) >= shortlist_size:
                break
        if len(shortlist) < 2:
            return hits

        started = time.perf_counter()
        backend = "none"
        scores_by_index: dict[int, float] = {}
        if self._rerank_enabled():
            scores_by_index = self._rerank_index_scores(
                query,
                [item.snippet for item in shortlist],
            )
            if scores_by_index:
                backend = "model"
        if (not scores_by_index) and self._embedding_enabled():
            embedding_scores = self._embedding_similarity_map(
                query,
                [item.snippet for item in shortlist],
            )
            if embedding_scores:
                backend = "embedding"
                scores_by_index = {
                    idx: float(embedding_scores.get(item.snippet, 0.0) or 0.0)
                    for idx, item in enumerate(shortlist)
                }
        elapsed = time.perf_counter() - started

        if not scores_by_index:
            print(
                f"[memory-rerank] backend={backend} query={self._clip_text(query, 40)!r} "
                f"candidates={len(shortlist)} scored=0 elapsed={elapsed:.2f}s"
            )
            return hits

        weighted: list[tuple[float, MemoryHit]] = []
        for idx, item in enumerate(shortlist):
            extra = float(scores_by_index.get(idx, 0.0) or 0.0)
            weighted.append((float(item.score) + extra * self.memory_rerank_weight, item))
        weighted.sort(key=lambda pair: pair[0], reverse=True)
        reordered = [item for _, item in weighted]
        shortlisted_keys = {f"{item.path}|{item.snippet}" for item in shortlist}
        tail = [item for item in hits if f"{item.path}|{item.snippet}" not in shortlisted_keys]
        print(
            f"[memory-rerank] backend={backend} query={self._clip_text(query, 40)!r} "
            f"candidates={len(shortlist)} scored={len(scores_by_index)} "
            f"elapsed={elapsed:.2f}s weight={self.memory_rerank_weight:.2f}"
        )
        return reordered + tail

    def _candidate_files(self, *, session_key: str, include_global: bool) -> list[Path]:
        out: list[Path] = []
        session_file = self.session_dir / f"{self._slug(session_key or 'session')}.md"
        if session_file.exists():
            out.append(session_file)
        if include_global:
            memory_file = self.root / "MEMORY.md"
            if memory_file.exists():
                out.append(memory_file)
            daily_files = sorted(self.memory_dir.glob("*.md"), reverse=True)[:5]
            out.extend([path for path in daily_files if path.is_file()])
        return out

    def _session_memory_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        text = self._safe_read(path)
        if not text:
            return []
        out: list[str] = []
        for line in text.splitlines():
            clean = line.rstrip()
            if not clean:
                continue
            if clean == "# Session Memory" or clean.startswith("- key:") or clean.startswith("- title:"):
                continue
            out.append(clean)
        return out

    def _score_file(self, path: Path, content: str, query: str) -> list[MemoryHit]:
        paragraphs = [x.strip() for x in re.split(r"\n\s*\n", content) if x.strip()]
        if not paragraphs:
            paragraphs = [content.strip()]
        tokens = self._tokens(query)
        query_norm = self._norm(query)
        hits: list[MemoryHit] = []
        for para in paragraphs:
            para_norm = self._norm(para)
            if not para_norm:
                continue
            score = 0.0
            if query_norm and (query_norm in para_norm or para_norm in query_norm):
                score += 4.0
            token_hits = sum(1 for token in tokens if token in para_norm)
            score += float(token_hits)
            common = len(set(tokens) & set(self._tokens(para)))
            score += common * 0.5
            ratio = self._rough_ratio(query_norm, para_norm)
            score += ratio * 2.0
            if score < 1.2:
                continue
            snippet = re.sub(r"\s+", " ", para).strip()[:260]
            hits.append(
                MemoryHit(
                    path=str(path.relative_to(self.root)),
                    score=score,
                    snippet=snippet,
                )
            )
        return hits

    def _prepare_rank_candidates(
        self,
        *,
        items: list[dict],
        query: str,
        text_getter,
        base_boost_getter=None,
        shortlist: int,
    ) -> list[dict]:
        clean_query = self._clip_text(query, 160)
        if not clean_query:
            return [{"item": item, "text": self._clip_text(text_getter(item), 320), "score": 0.0} for item in items[:shortlist]]

        q_norm = self._norm(clean_query)
        q_tokens = self._tokens(clean_query)
        ranked: list[dict] = []
        for item in items:
            text = self._clip_text(text_getter(item), 320)
            if not text:
                continue
            norm = self._norm(text)
            score = 0.0
            if q_norm and (q_norm in norm or norm in q_norm):
                score += 3.0
            score += sum(1.0 for token in q_tokens if token in norm)
            score += self._rough_ratio(q_norm, norm) * 2.0
            if callable(base_boost_getter):
                try:
                    score += float(base_boost_getter(item) or 0.0)
                except Exception:
                    pass
            ranked.append({"item": item, "text": text, "score": score})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[: max(1, shortlist)] or [
            {"item": item, "text": self._clip_text(text_getter(item), 320), "score": 0.0}
            for item in items[:shortlist]
        ]

    def _finalize_rank_candidates(
        self,
        candidates: list[dict],
        embedding_scores: dict[str, float],
    ) -> list[dict]:
        ranked: list[tuple[float, dict]] = []
        for candidate in candidates:
            score = float(candidate.get("score", 0.0) or 0.0)
            text = str(candidate.get("text", ""))
            score += embedding_scores.get(text, 0.0) * 3.0
            ranked.append((score, candidate.get("item") or {}))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked]

    def _tokens(self, text: str) -> list[str]:
        raw = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", text or "")
        out: list[str] = []
        seen: set[str] = set()
        for token in raw:
            t = token.strip().lower()
            if len(t) < 2:
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out[:12]

    def _norm(self, text: str) -> str:
        raw = re.sub(r"\s+", "", text or "").lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)

    def _rough_ratio(self, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        short = a if len(a) <= len(b) else b
        long = b if len(a) <= len(b) else a
        if short in long:
            return min(1.0, len(short) / max(1, len(long)))
        common = 0
        for token in self._tokens(short):
            if token in long:
                common += len(token)
        return common / max(1, len(short))

    def _session_state_path(self, session_key: str) -> Path:
        return self.session_state_dir / f"{self._slug(session_key)}.json"

    def _new_session_state(self, *, session_key: str, title: str) -> dict:
        return {
            "session_key": session_key,
            "title": title,
            "updated_at": self._now_iso(),
            "profile": {"summary": ""},
            "people": [],
            "facts": [],
            "relations": [],
            "events": [],
        }

    def _load_session_state(self, *, session_key: str, title: str) -> dict:
        path = self._session_state_path(session_key)
        state = self._safe_json_load(path)
        if not state:
            state = self._new_session_state(session_key=session_key, title=title)
        if not state.get("session_key"):
            state["session_key"] = session_key
        if title and (not state.get("title")):
            state["title"] = title
        state.setdefault("profile", {}).setdefault("summary", "")
        state.setdefault("people", [])
        state.setdefault("facts", [])
        state.setdefault("relations", [])
        state.setdefault("events", [])
        return state

    def _save_session_state(self, state: dict) -> None:
        session_key = str(state.get("session_key", "")).strip()
        if not session_key:
            return
        self._compact_session_state(state)
        self._safe_json_dump(self._session_state_path(session_key), state)

    def _find_person_entry(self, state: dict, name: str) -> dict | None:
        norm_name = self._norm(self._normalize_name(name))
        if not norm_name:
            return None
        for person in state.setdefault("people", []):
            existing_name = self._norm(str(person.get("name", "")))
            aliases = [self._norm(str(x)) for x in (person.get("aliases") or [])]
            if norm_name and (norm_name == existing_name or norm_name in aliases):
                return person
        return None

    def _merge_people_entries(self, state: dict, target: dict, source: dict, *, stamp: str) -> None:
        if target is source:
            return
        for alias in [source.get("name", "")] + list(source.get("aliases") or []):
            clean_alias = self._normalize_name(str(alias))
            if clean_alias and clean_alias != str(target.get("name", "")) and clean_alias not in (target.get("aliases") or []):
                target.setdefault("aliases", []).append(clean_alias)
        for note in source.get("notes") or []:
            clean_note = self._clip_text(note, 80)
            if clean_note and clean_note not in (target.get("notes") or []):
                target.setdefault("notes", []).append(clean_note)
        target["mentions"] = int(target.get("mentions", 0) or 0) + int(source.get("mentions", 0) or 0)
        target["last_seen"] = max(str(target.get("last_seen", "")), str(source.get("last_seen", "")), stamp)
        people = state.setdefault("people", [])
        if source in people:
            people.remove(source)

    def _merge_person_payload(self, state: dict, payload: dict, *, stamp: str) -> None:
        self._upsert_person(
            state,
            name=str(payload.get("name", "")),
            alias="",
            note="; ".join(str(x) for x in (payload.get("notes") or [])[:2]),
            stamp=stamp,
        )
        for alias in payload.get("aliases") or []:
            self._merge_person_alias(
                state,
                canonical_name=str(payload.get("name", "")),
                alias_name=str(alias),
                note="会话合并保留的别名",
                stamp=stamp,
            )

    def _merge_person_alias(
        self,
        state: dict,
        *,
        canonical_name: str,
        alias_name: str,
        note: str,
        stamp: str,
    ) -> None:
        clean_name = self._normalize_name(canonical_name)
        clean_alias = self._normalize_name(alias_name)
        if not clean_name or not clean_alias or clean_name == clean_alias:
            self._upsert_person(state, name=clean_name or clean_alias, alias="", note=note, stamp=stamp)
            return
        people = state.setdefault("people", [])
        target = self._find_person_entry(state, clean_name)
        alias_target = self._find_person_entry(state, clean_alias)

        if target is None and alias_target is not None:
            alias_target["name"] = clean_name
            target = alias_target
        if target is None:
            target = {
                "name": clean_name,
                "aliases": [],
                "notes": [],
                "mentions": 0,
                "last_seen": stamp,
            }
            people.append(target)
        if alias_target is not None and alias_target is not target:
            self._merge_people_entries(state, target, alias_target, stamp=stamp)
        if clean_alias not in (target.get("aliases") or []) and clean_alias != str(target.get("name", "")):
            target.setdefault("aliases", []).append(clean_alias)
        clean_note = self._clip_text(note, 80)
        if clean_note and clean_note not in (target.get("notes") or []):
            target.setdefault("notes", []).append(clean_note)
        target["mentions"] = int(target.get("mentions", 0) or 0) + 1
        target["last_seen"] = stamp

    def _upsert_person(
        self,
        state: dict,
        *,
        name: str,
        alias: str,
        note: str,
        stamp: str,
    ) -> None:
        clean_name = self._normalize_name(name)
        if not clean_name:
            return
        if _looks_like_placeholder_person(clean_name):
            return
        clean_alias = self._normalize_name(alias)
        clean_note = self._clip_text(note, 80)
        people = state.setdefault("people", [])
        target = self._find_person_entry(state, clean_name)
        if target is None:
            target = {
                "name": clean_name,
                "aliases": [],
                "notes": [],
                "mentions": 0,
                "last_seen": stamp,
            }
            people.append(target)
        if clean_alias and clean_alias != clean_name:
            alias_target = self._find_person_entry(state, clean_alias)
            if alias_target is not None and alias_target is not target:
                self._merge_people_entries(state, target, alias_target, stamp=stamp)
            if clean_alias not in (target.get("aliases") or []):
                target.setdefault("aliases", []).append(clean_alias)
        if clean_note and clean_note not in (target.get("notes") or []):
            target.setdefault("notes", []).append(clean_note)
        target["mentions"] = int(target.get("mentions", 0) or 0) + 1
        target["last_seen"] = stamp

    def _is_valid_person_entry(self, person: dict) -> bool:
        if not isinstance(person, dict):
            return False
        name = self._normalize_name(str(person.get("name", "")))
        if not name:
            return False
        return not _looks_like_placeholder_person(name)

    @staticmethod
    def _looks_like_group_roster_query(query: str) -> bool:
        raw = re.sub(r"\s+", " ", (query or "").strip())
        if not raw:
            return False
        markers = (
            "群成员",
            "每个人",
            "大家",
            "分别",
            "印象",
            "特点",
            "介绍一下",
            "评价一下",
            "总结一下",
        )
        return any(marker in raw for marker in markers)

    def _upsert_fact(self, state: dict, text: str, *, stamp: str) -> None:
        clean = self._clip_text(text, 120)
        if not clean:
            return
        facts = state.setdefault("facts", [])
        norm = self._norm(clean)
        target = None
        for fact in facts:
            if self._norm(str(fact.get("text", ""))) == norm:
                target = fact
                break
        if target is None:
            target = {"text": clean, "hits": 0, "last_seen": stamp}
            facts.append(target)
        target["hits"] = int(target.get("hits", 0) or 0) + 1
        target["last_seen"] = stamp

    def _upsert_relation(
        self,
        state: dict,
        *,
        subject: str,
        relation: str,
        target: str,
        note: str,
        stamp: str,
    ) -> None:
        clean_subject = self._normalize_name(subject)
        clean_target = self._normalize_name(target)
        clean_relation = self._normalize_relation(relation)
        clean_note = self._clip_text(note, 80)
        if not clean_subject or not clean_target or not clean_relation:
            return
        self._upsert_person(state, name=clean_subject, alias="", note="在关系中出现", stamp=stamp)
        self._upsert_person(state, name=clean_target, alias="", note="在关系中出现", stamp=stamp)
        relations = state.setdefault("relations", [])
        key = "|".join(
            [
                self._norm(clean_subject),
                self._norm(clean_relation),
                self._norm(clean_target),
            ]
        )
        target_rel = None
        for item in relations:
            existing_key = "|".join(
                [
                    self._norm(str(item.get("subject", ""))),
                    self._norm(str(item.get("relation", ""))),
                    self._norm(str(item.get("target", ""))),
                ]
            )
            if existing_key == key:
                target_rel = item
                break
        if target_rel is None:
            target_rel = {
                "subject": clean_subject,
                "relation": clean_relation,
                "target": clean_target,
                "note": "",
                "hits": 0,
                "last_seen": stamp,
            }
            relations.append(target_rel)
        if clean_note:
            target_rel["note"] = clean_note
        target_rel["hits"] = int(target_rel.get("hits", 0) or 0) + 1
        target_rel["last_seen"] = stamp

    def _append_event(self, state: dict, text: str, *, stamp: str) -> None:
        clean = self._clip_text(text, 140)
        if not clean:
            return
        events = state.setdefault("events", [])
        if events and self._norm(str(events[-1].get("text", ""))) == self._norm(clean):
            events[-1]["ts"] = stamp
            return
        events.append({"text": clean, "ts": stamp})

    def _extract_names_from_text(self, text: str) -> list[str]:
        clean = self._clip_text(text, 220)
        out: list[str] = []
        seen: set[str] = set()
        for pattern in _NAME_PATTERNS:
            for match in pattern.findall(clean):
                name = self._normalize_name(match)
                key = self._norm(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(name)
        return out[:6]

    def _extract_fact_candidates(self, text: str) -> list[str]:
        clean = self._clip_text(text, 220)
        if not clean or len(clean) < 4 or len(clean) > 120:
            return []
        if clean.endswith(("?", "？")):
            return []
        out: list[str] = []
        for pattern in _FACT_PATTERNS:
            if pattern.search(clean):
                out.append(clean)
                break
        return out[:2]

    def _extract_alias_pairs(self, text: str, *, sender: str) -> list[tuple[str, str]]:
        clean = self._clip_text(text, 220)
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for pattern in _ALIAS_PATTERNS:
            for match in pattern.findall(clean):
                if not isinstance(match, tuple) or len(match) < 2:
                    continue
                left = self._normalize_name(match[0])
                right = self._normalize_name(match[1])
                if not left or not right or left == right:
                    continue
                canonical = left
                alias = right
                clean_sender = self._normalize_name(sender)
                if clean_sender:
                    sender_norm = self._norm(clean_sender)
                    left_norm = self._norm(left)
                    right_norm = self._norm(right)
                    if sender_norm == right_norm and sender_norm != left_norm:
                        canonical = clean_sender
                        alias = left
                    elif sender_norm == left_norm:
                        canonical = clean_sender
                        alias = right
                key = f"{self._norm(canonical)}|{self._norm(alias)}"
                if key in seen:
                    continue
                seen.add(key)
                out.append((canonical, alias))
        return out[:4]

    def _extract_relation_candidates(
        self,
        text: str,
        *,
        sender: str,
    ) -> list[tuple[str, str, str, str]]:
        clean = self._clip_text(text, 220)
        clean_sender = self._normalize_name(sender)
        out: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()
        for idx, pattern in enumerate(_RELATION_PATTERNS):
            for match in pattern.findall(clean):
                if not isinstance(match, tuple):
                    continue
                subject = ""
                target = ""
                relation = ""
                if idx == 0 and len(match) >= 2:
                    if not clean_sender:
                        continue
                    target = self._normalize_name(match[0])
                    relation = self._normalize_relation(match[1])
                    subject = clean_sender
                elif idx == 1 and len(match) >= 2:
                    if not clean_sender:
                        continue
                    target = self._normalize_name(match[0])
                    relation = self._normalize_relation(match[1])
                    subject = clean_sender
                elif idx == 2 and len(match) >= 3:
                    subject = self._normalize_name(match[0])
                    target = self._normalize_name(match[1])
                    relation = self._normalize_relation(match[2])
                if not subject or not target or not relation:
                    continue
                key = "|".join([self._norm(subject), self._norm(relation), self._norm(target)])
                if key in seen:
                    continue
                seen.add(key)
                out.append((subject, relation, target, clean[:80]))
        return out[:4]

    def _normalize_name(self, text: str) -> str:
        clean = self._clip_text(text, 24)
        clean = clean.strip(" []【】()（）,，。.!！?？:：;；\"'“”‘’")
        clean = re.sub(r"^(real|test|tmp)[-_ ]*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"(也行|都行|就行|可以|呀|啊|呢|吧)$", "", clean)
        if len(clean) < 2:
            return ""
        if _looks_like_noise_name(clean):
            return ""
        return clean

    def _normalize_relation(self, text: str) -> str:
        clean = self._clip_text(text, 24)
        clean = clean.strip(" []【】()（）,，。.!！?？:：;；的")
        if len(clean) < 1:
            return ""
        return clean

    def _compact_session_state(self, state: dict) -> None:
        people = state.setdefault("people", [])
        merged_people: list[dict] = []
        for person in people:
            if not isinstance(person, dict):
                continue
            name = self._normalize_name(str(person.get("name", "")))
            if not name:
                continue
            existing = None
            norm_name = self._norm(name)
            for target in merged_people:
                target_norm = self._norm(str(target.get("name", "")))
                aliases = [self._norm(str(x)) for x in (target.get("aliases") or [])]
                if norm_name == target_norm or norm_name in aliases:
                    existing = target
                    break
            if existing is None:
                existing = {
                    "name": name,
                    "aliases": [],
                    "notes": [],
                    "mentions": 0,
                    "last_seen": str(person.get("last_seen", "")),
                }
                merged_people.append(existing)
            for alias in person.get("aliases") or []:
                clean_alias = self._normalize_name(str(alias))
                if clean_alias and clean_alias != existing["name"] and clean_alias not in existing["aliases"]:
                    existing["aliases"].append(clean_alias)
            for note in person.get("notes") or []:
                clean_note = self._clip_text(note, 80)
                if clean_note and clean_note not in existing["notes"]:
                    existing["notes"].append(clean_note)
            existing["mentions"] = int(existing.get("mentions", 0) or 0) + int(person.get("mentions", 0) or 0)
            existing["last_seen"] = max(str(existing.get("last_seen", "")), str(person.get("last_seen", "")))

        for person in merged_people:
            person["aliases"] = (person.get("aliases") or [])[:8]
            person["notes"] = (person.get("notes") or [])[-4:]
        merged_people = [person for person in merged_people if self._is_valid_person_entry(person)]
        merged_people.sort(
            key=lambda item: (-int(item.get("mentions", 0) or 0), str(item.get("last_seen", ""))),
            reverse=False,
        )
        state["people"] = merged_people[:80]

        facts_map: dict[str, dict] = {}
        for fact in state.setdefault("facts", []):
            if not isinstance(fact, dict):
                continue
            text = self._clip_text(fact.get("text", ""), 120)
            key = self._norm(text)
            if not key:
                continue
            target = facts_map.setdefault(
                key,
                {"text": text, "hits": 0, "last_seen": str(fact.get("last_seen", ""))},
            )
            target["hits"] = int(target.get("hits", 0) or 0) + int(fact.get("hits", 0) or 0)
            target["last_seen"] = max(str(target.get("last_seen", "")), str(fact.get("last_seen", "")))
        facts = list(facts_map.values())
        facts.sort(key=lambda item: (-int(item.get("hits", 0) or 0), str(item.get("last_seen", ""))), reverse=False)
        state["facts"] = facts[:120]

        relation_map: dict[str, dict] = {}
        for relation in state.setdefault("relations", []):
            if not isinstance(relation, dict):
                continue
            subject = self._normalize_name(str(relation.get("subject", "")))
            target = self._normalize_name(str(relation.get("target", "")))
            rel_name = self._normalize_relation(str(relation.get("relation", "")))
            if not subject or not target or not rel_name:
                continue
            key = "|".join([self._norm(subject), self._norm(rel_name), self._norm(target)])
            target_item = relation_map.setdefault(
                key,
                {
                    "subject": subject,
                    "relation": rel_name,
                    "target": target,
                    "note": "",
                    "hits": 0,
                    "last_seen": str(relation.get("last_seen", "")),
                },
            )
            note = self._clip_text(relation.get("note", ""), 80)
            if note:
                target_item["note"] = note
            target_item["hits"] = int(target_item.get("hits", 0) or 0) + int(relation.get("hits", 0) or 0)
            target_item["last_seen"] = max(
                str(target_item.get("last_seen", "")),
                str(relation.get("last_seen", "")),
            )
        relation_items = list(relation_map.values())
        relation_items.sort(
            key=lambda item: (-int(item.get("hits", 0) or 0), str(item.get("last_seen", ""))),
            reverse=False,
        )
        state["relations"] = relation_items[:120]

        compact_events: list[dict] = []
        seen_event_keys: set[str] = set()
        for event in reversed(state.setdefault("events", [])):
            if not isinstance(event, dict):
                continue
            text = self._clip_text(event.get("text", ""), 140)
            if not text:
                continue
            key = self._norm(text)
            if key in seen_event_keys and len(compact_events) >= 40:
                continue
            seen_event_keys.add(key)
            compact_events.append({"text": text, "ts": str(event.get("ts", ""))})
            if len(compact_events) >= 120:
                break
        state["events"] = list(reversed(compact_events))
        state["updated_at"] = self._now_iso()

    def _embedding_api_key(self) -> str:
        if self.embedding_cfg.api_key:
            return self.embedding_cfg.api_key
        if self.embedding_cfg.api_key_env:
            return os.getenv(self.embedding_cfg.api_key_env, "")
        return ""

    def _rerank_api_key(self) -> str:
        if self.rerank_cfg.api_key:
            return self.rerank_cfg.api_key
        if self.rerank_cfg.api_key_env:
            return os.getenv(self.rerank_cfg.api_key_env, "")
        return ""

    def _rerank_unavailable_reason(self) -> str:
        if not self.enabled:
            return "workspace disabled"
        if not self.memory_rerank_enabled:
            return "workspace_memory_rerank_enabled=false"
        if not self.rerank_cfg.enabled:
            return "rerank.enabled=false"
        if not self.rerank_cfg.base_url:
            return "missing rerank base_url"
        if not self.rerank_cfg.model:
            return "missing rerank model"
        if not self._rerank_api_key():
            return "missing rerank api_key"
        return ""

    def _rerank_enabled(self) -> bool:
        return self._rerank_unavailable_reason() == ""

    def _rerank_index_scores(self, query: str, documents: list[str]) -> dict[int, float]:
        if not self._rerank_enabled():
            return {}
        clean_query = self._clip_text(query, 240)
        docs: list[str] = []
        doc_positions: list[int] = []
        for idx, text in enumerate(documents):
            clean = self._clip_text(text, 320)
            if not clean:
                continue
            docs.append(clean)
            doc_positions.append(idx)
        if not clean_query or (not docs):
            return {}
        payload = json.dumps(
            {
                "model": self.rerank_cfg.model,
                "query": clean_query,
                "documents": docs,
                "top_n": len(docs),
                "return_documents": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._rerank_api_key()}",
        }
        url = self.rerank_cfg.base_url.rstrip("/") + "/rerank"
        timeout_sec = max(2.0, float(self.rerank_cfg.timeout_sec))
        max_attempts = 2
        raw = ""
        last_exc: Exception | None = None
        last_elapsed = 0.0
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            req = urllib_request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                last_exc = None
                break
            except (urllib_error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_exc = exc
                last_elapsed = time.perf_counter() - started
                if self._is_timeout_error(exc) and attempt < max_attempts:
                    continue
                if not self._rerank_warned:
                    print(
                        "[warn] memory rerank request failed, fallback: "
                        f"elapsed={last_elapsed:.2f}s timeout={timeout_sec:.1f}s "
                        f"inputs={len(docs)} attempt={attempt}/{max_attempts} err={exc}"
                    )
                    self._rerank_warned = True
                return {}

        if last_exc is not None:
            if not self._rerank_warned:
                print(
                    "[warn] memory rerank request failed, fallback: "
                    f"elapsed={last_elapsed:.2f}s timeout={timeout_sec:.1f}s "
                    f"inputs={len(docs)} attempts={max_attempts} err={last_exc}"
                )
                self._rerank_warned = True
            return {}

        try:
            data = json.loads(raw)
        except Exception:
            if not self._rerank_warned:
                print("[warn] memory rerank response invalid, fallback")
                self._rerank_warned = True
            return {}

        items: list | None = None
        if isinstance(data, dict):
            for key in ("results", "data", "output", "rerank"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break
        elif isinstance(data, list):
            items = data
        if not isinstance(items, list):
            if not self._rerank_warned:
                print("[warn] memory rerank payload missing results, fallback")
                self._rerank_warned = True
            return {}

        out: dict[int, float] = {}
        for pos, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index", pos))
            except Exception:
                index = pos
            raw_score = item.get("relevance_score", item.get("score", item.get("similarity", 0.0)))
            try:
                score = float(raw_score)
            except Exception:
                score = 0.0
            if 0 <= index < len(doc_positions):
                out[doc_positions[index]] = score
        return out

    def _embedding_enabled(self) -> bool:
        return bool(
            self.enabled
            and self.embedding_cfg.enabled
            and self.embedding_cfg.base_url
            and self.embedding_cfg.model
            and self._embedding_api_key()
        )

    def _embedding_similarity_map(self, query: str, texts: list[str]) -> dict[str, float]:
        if not self._embedding_enabled():
            return {}
        clean_query = self._clip_text(query, 240)
        uniq_texts: list[str] = []
        seen: set[str] = set()
        for text in texts:
            clean = self._clip_text(text, 320)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            uniq_texts.append(clean)
        if not clean_query or not uniq_texts:
            return {}
        vectors = self._embed_texts([clean_query] + uniq_texts)
        if not vectors or not vectors[0]:
            return {}
        query_vector = vectors[0]
        assert query_vector is not None
        out: dict[str, float] = {}
        for text, vector in zip(uniq_texts, vectors[1:]):
            if not vector:
                continue
            out[text] = max(0.0, self._cosine_similarity(query_vector, vector))
        return out

    def _embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        results: list[list[float] | None] = [None] * len(texts)
        if not self._embedding_enabled():
            return results
        misses: list[str] = []
        miss_positions: list[int] = []
        for idx, text in enumerate(texts):
            clean = self._clip_text(text, 320)
            if not clean:
                continue
            if clean in self._embedding_cache:
                results[idx] = self._embedding_cache[clean]
                continue
            misses.append(clean)
            miss_positions.append(idx)
        if not misses:
            return results

        payload = json.dumps(
            {"model": self.embedding_cfg.model, "input": misses},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._embedding_api_key()}",
        }
        url = self.embedding_cfg.base_url.rstrip("/") + "/embeddings"
        timeout_sec = max(2.0, float(self.embedding_cfg.timeout_sec))
        max_attempts = 2
        raw = ""
        last_exc: Exception | None = None
        last_elapsed = 0.0
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            req = urllib_request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urllib_request.urlopen(req, timeout=timeout_sec) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                last_exc = None
                break
            except (urllib_error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_exc = exc
                last_elapsed = time.perf_counter() - started
                if self._is_timeout_error(exc) and attempt < max_attempts:
                    continue
                if not self._embedding_warned:
                    print(
                        "[warn] embedding request failed, fallback to lexical recall: "
                        f"elapsed={last_elapsed:.2f}s timeout={timeout_sec:.1f}s "
                        f"inputs={len(misses)} attempt={attempt}/{max_attempts} err={exc}"
                    )
                    self._embedding_warned = True
                return results

        if last_exc is not None:
            if not self._embedding_warned:
                print(
                    "[warn] embedding request failed, fallback to lexical recall: "
                    f"elapsed={last_elapsed:.2f}s timeout={timeout_sec:.1f}s "
                    f"inputs={len(misses)} attempts={max_attempts} err={last_exc}"
                )
                self._embedding_warned = True
            return results

        try:
            data = json.loads(raw)
        except Exception:
            if not self._embedding_warned:
                print("[warn] embedding response invalid, fallback to lexical recall")
                self._embedding_warned = True
            return results

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            if not self._embedding_warned:
                print("[warn] embedding payload missing data, fallback to lexical recall")
                self._embedding_warned = True
            return results

        ordered: list[list[float] | None] = [None] * len(misses)
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index", 0) or 0)
            except Exception:
                index = 0
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                continue
            vector = [float(x) for x in embedding]
            if 0 <= index < len(ordered):
                ordered[index] = vector

        for miss, pos, vector in zip(misses, miss_positions, ordered):
            self._embedding_cache[miss] = vector
            results[pos] = vector
        return results

    def _is_timeout_error(self, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, urllib_error.URLError):
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return True
            return "timeout" in str(reason).lower()
        return "timeout" in str(exc).lower()

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _clip_text(self, text: object, limit: int) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if not clean:
            return ""
        return clean[: max(8, limit)]

    def _slug(self, text: str) -> str:
        raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", text or "").strip("-").lower()
        return raw[:80] or "session"

    def _now_iso(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _looks_like_noise_name(text: str) -> bool:
    lower = (text or "").strip().lower()
    if lower in {"图片", "photo", "image", "unknown", "系统消息"}:
        return True
    if re.fullmatch(r"[\d_ -]+", lower or ""):
        return True
    return False


def _looks_like_placeholder_person(text: str) -> bool:
    clean = (text or "").strip()
    if not clean:
        return True
    if _looks_like_noise_name(clean):
        return True
    exact = {
        "群成员",
        "其他群成员",
        "群聊成员",
        "其他人",
        "大家",
        "有人",
        "未知人物",
        "我方",
    }
    if clean in exact:
        return True
    if "群成员" in clean or "群聊成员" in clean or "群友" in clean:
        return True
    suffixes = (
        "相关消息",
        "消息",
        "截图",
        "图片",
        "视频",
        "内容",
        "讨论",
        "位置",
        "方法",
        "结果",
        "经历",
        "活动",
        "任务",
        "关卡",
        "地图",
        "线索",
    )
    return any(clean.endswith(suffix) for suffix in suffixes)
