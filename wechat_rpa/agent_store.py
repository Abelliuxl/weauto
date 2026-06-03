from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")


class HotFile:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path) if isinstance(path, str) else path
        self._mtime: float = 0
        self._content: str = ""

    def read(self) -> str:
        if not self.path.is_file():
            self._content = ""
            self._mtime = 0
            return ""
        try:
            mtime = self.path.stat().st_mtime
            if mtime > self._mtime:
                self._content = self.path.read_text(encoding="utf-8", errors="replace")
                self._mtime = mtime
        except OSError:
            pass
        return self._content

    def write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding="utf-8")
        self._content = content
        self._mtime = self.path.stat().st_mtime

    def exists(self) -> bool:
        return self.path.is_file()


class MemoryStore:
    ALLOWED_NAMES = {"core", "timeline"}

    def __init__(self, base_dir: str | Path = DATA_DIR / "memory") -> None:
        self.base = Path(base_dir)
        self._files: dict[str, HotFile] = {}

    @classmethod
    def normalize_name(cls, name: str) -> str:
        clean = str(name or "").strip().lower()
        if clean in cls.ALLOWED_NAMES:
            return clean
        if clean in {"history", "events", "event", "recent", "最近关键事件", "时间线", "timeline.md"}:
            return "timeline"
        return "core"

    def _get(self, name: str) -> HotFile:
        name = self.normalize_name(name)
        if name not in self._files:
            self._files[name] = HotFile(self.base / f"{name}.md")
        return self._files[name]

    def read(self, name: str) -> str:
        return self._get(name).read()

    def write(self, name: str, content: str) -> None:
        self._get(name).write(content)

    def backup(self, name: str) -> Path | None:
        clean = self.normalize_name(name)
        path = self.base / f"{clean}.md"
        if not path.is_file():
            return None
        backup_dir = self.base / ".backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{clean}-{ts}.md"
        backup_path.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        return backup_path

    def load_all(self) -> dict[str, str]:
        return {name: self.read(name) for name in sorted(self.ALLOWED_NAMES)}


class SkillStore:
    def __init__(self, base_dir: str | Path = DATA_DIR / "skills") -> None:
        self.base = Path(base_dir)
        self._files: dict[str, HotFile] = {}

    @staticmethod
    def normalize_name(name: str) -> str:
        clean = str(name or "").strip().replace("\\", "/")
        clean = re.sub(r"^(?:data/)?skills/", "", clean, flags=re.I)
        clean = re.sub(r"/?SKILL\.md$", "", clean, flags=re.I)
        parts = [part.strip() for part in clean.split("/") if part.strip()]
        clean = parts[-1] if parts else ""
        clean = re.sub(r"\s+", "-", clean)
        clean = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "-", clean).strip(".-")
        return clean[:80] or "skill"

    def _path(self, name: str) -> Path:
        return self.base / self.normalize_name(name) / "SKILL.md"

    def _get(self, name: str) -> HotFile:
        key = self.normalize_name(name)
        if key not in self._files:
            self._files[key] = HotFile(self._path(key))
        return self._files[key]

    def list(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir())

    def read(self, name: str) -> str:
        return self._get(name).read()

    def write(self, name: str, content: str) -> None:
        self._get(name).write(content)

    def backup(self, name: str) -> Path | None:
        path = self._path(name)
        if not path.is_file():
            return None
        backup_dir = path.parent / ".backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"SKILL-{ts}.md"
        backup_path.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        return backup_path

    def cleanup(self) -> None:
        if not self.base.is_dir():
            return
        for d in list(self.base.iterdir()):
            if not d.is_dir():
                continue
            skill_file = d / "SKILL.md"
            if not skill_file.is_file() or not skill_file.read_text(encoding="utf-8", errors="replace").strip():
                shutil.rmtree(d, ignore_errors=True)

    def delete(self, name: str) -> None:
        path = self._path(name)
        parent = path.parent
        if parent.is_dir():
            shutil.rmtree(parent)
        self._files.pop(self.normalize_name(name), None)


class PeopleStore:
    def __init__(self, base_dir: str | Path = DATA_DIR / "people") -> None:
        self.base = Path(base_dir)
        self._files: dict[str, HotFile] = {}

    def _path(self, name: str) -> Path:
        safe = name.replace(" ", "_").replace("/", "_")
        return self.base / f"{safe}.md"

    def _get(self, name: str) -> HotFile:
        path = self._path(name)
        key = str(path)
        if key not in self._files:
            self._files[key] = HotFile(path)
        return self._files[key]

    def list(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted(f.stem for f in self.base.iterdir() if f.suffix == ".md")

    def read(self, name: str) -> str:
        return self._get(name).read()

    def write(self, name: str, content: str) -> None:
        self._get(name).write(content)

    def cleanup(self) -> None:
        if not self.base.is_dir():
            return
        HASH_RE = re.compile(r"^(.+)_([0-9a-f]{8})\.md$")
        for f in sorted(self.base.iterdir(), key=lambda p: p.stat().st_mtime):
            m = HASH_RE.match(f.name)
            if not m:
                continue
            base_name = m.group(1)
            canonical_path = self.base / f"{base_name}.md"
            if canonical_path.exists():
                content = canonical_path.read_text(encoding="utf-8", errors="replace")
                extra = f.read_text(encoding="utf-8", errors="replace")
                if extra.strip() not in content:
                    canonical_path.write_text(
                        content.rstrip() + "\n\n## 历史印象\n" + extra.strip() + "\n", encoding="utf-8"
                    )
                f.unlink()
            else:
                f.rename(canonical_path)

    def all_impressions(self) -> str:
        parts: list[str] = []
        for name in self.list():
            content = self.read(name)
            if content.strip():
                parts.append(f"=== {name} ===\n{content.strip()}")
        return "\n\n".join(parts)


class ChatHistoryStore:
    LINE_RE = re.compile(
        r"^\[(\d{2}:\d{2}:\d{2})\]\s+([AU])(?:\(([^)]*)\))?:\s+(.*)$"
    )

    def __init__(self, base_dir: str | Path = DATA_DIR / "chat_history") -> None:
        self.base = Path(base_dir)
        self._index_path = self.base / "_index.json"

    @staticmethod
    def _safe_name(name: str) -> str:
        s = name.replace(" ", "_").replace("/", "_").replace("@", "_at_")
        return "".join(c for c in s if c.isalnum() or c in "_-.") or "unnamed"

    def _chat_dir(self, chat_name: str) -> Path:
        return self.base / self._safe_name(chat_name)

    def _date_path(self, chat_name: str, date_str: str) -> Path:
        return self._chat_dir(chat_name) / f"{date_str}.txt"

    def _list_date_files(self, chat_name: str) -> list[Path]:
        chat_dir = self._chat_dir(chat_name)
        if not chat_dir.is_dir():
            return []
        files: list[Path] = []
        for f in chat_dir.iterdir():
            if f.suffix == ".txt" and re.match(r"^\d{4}-\d{2}-\d{2}\.txt$", f.name):
                files.append(f)
        files.sort(key=lambda p: p.name)
        return files

    def append(self, chat_name: str, record: dict) -> None:
        obs = record.get("observed_at", int(time.time()))
        try:
            ts = int(obs)
        except Exception:
            ts = int(time.time())
        date_str = time.strftime("%Y-%m-%d", time.localtime(ts))
        time_str = time.strftime("%H:%M:%S", time.localtime(ts))
        role = str(record.get("role", "unknown"))
        sender = re.sub(r"\s+", " ", str(record.get("sender", ""))).strip()
        text = re.sub(r"\s+", " ", str(record.get("text", ""))).strip()
        if not text:
            return
        if role == "user" and sender:
            line = f"[{time_str}] U({sender}): {text}"
        else:
            prefix = "A" if role == "assistant" else "U"
            line = f"[{time_str}] {prefix}: {text}"
        path = self._date_path(chat_name, date_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        dedup_key = (sender, re.sub(r"\s+", "", text))
        try:
            if path.exists():
                with open(path, "rb") as f:
                    f.seek(max(0, path.stat().st_size - 262144))
                    tail = f.read().decode("utf-8", errors="replace").splitlines()
                    tail_lines = tail[-500:]
                    for tl in tail_lines:
                        m = self.LINE_RE.match(tl.strip())
                        if m:
                            s = (m.group(3) or "").strip()
                            t = re.sub(r"\s+", "", m.group(4))
                            if (s, t) == dedup_key:
                                return
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _parse_line(self, line: str, date_str: str) -> dict | None:
        m = self.LINE_RE.match(line)
        if not m:
            return None
        time_part = m.group(1)
        role_indicator = m.group(2)
        sender = (m.group(3) or "").strip()
        text = m.group(4)
        role = "user" if role_indicator == "U" else "assistant"
        try:
            dt = time.strptime(f"{date_str} {time_part}", "%Y-%m-%d %H:%M:%S")
            observed_at = int(time.mktime(dt))
        except Exception:
            observed_at = 0
        return {
            "role": role,
            "content_type": "text",
            "text": text,
            "sender": sender,
            "sender_raw": sender,
            "source": "runtime",
            "observed_at": observed_at,
        }

    def read_date(self, chat_name: str, date_str: str) -> list[dict]:
        path = self._date_path(chat_name, date_str)
        if not path.is_file():
            return []
        records: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                rec = self._parse_line(line.strip(), date_str)
                if rec:
                    records.append(rec)
        except OSError:
            pass
        return records

    def read_recent(self, chat_name: str, limit: int = 50) -> list[dict]:
        date_files = self._list_date_files(chat_name)
        records: list[dict] = []
        for fpath in reversed(date_files):
            date_str = fpath.stem
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                rec = self._parse_line(line.strip(), date_str)
                if rec:
                    records.append(rec)
                    if len(records) >= limit:
                        break
            if len(records) >= limit:
                break
        records.reverse()
        return records

    def load_all(self, chat_name: str) -> list[dict]:
        date_files = self._list_date_files(chat_name)
        records: list[dict] = []
        for fpath in date_files:
            date_str = fpath.stem
            try:
                for line in fpath.read_text(encoding="utf-8", errors="replace").splitlines():
                    rec = self._parse_line(line.strip(), date_str)
                    if rec:
                        records.append(rec)
            except OSError:
                pass
        return records

    def search(self, chat_name: str, query: str, limit: int = 10, context: int = 2) -> str:
        date_files = self._list_date_files(chat_name)
        if not date_files:
            return f"搜索 \"{query}\" 在 [{chat_name}]：无聊天记录"

        all_lines: list[dict] = []
        for fpath in date_files:
            date_str = fpath.stem
            try:
                for line in fpath.read_text(encoding="utf-8", errors="replace").splitlines():
                    rec = self._parse_line(line.strip(), date_str)
                    if rec:
                        all_lines.append(rec)
            except OSError:
                pass

        if not all_lines:
            return f"搜索 \"{query}\" 在 [{chat_name}]：无聊天记录"

        q = query.lower()
        matches: list[int] = []
        for i, rec in enumerate(all_lines):
            text = rec.get("text", "").lower()
            if q in text:
                matches.append(i)

        if not matches:
            return f"搜索 \"{query}\" 在 [{chat_name}]：未找到匹配记录"

        lines: list[str] = []
        displayed = 0
        ctx = max(0, min(5, int(context)))
        for match_idx in matches:
            if displayed >= limit:
                break
            start = max(0, match_idx - ctx)
            end = min(len(all_lines), match_idx + ctx + 1)
            for j in range(start, end):
                rec = all_lines[j]
                sender = rec.get("sender", "")
                ts = rec.get("observed_at", 0)
                try:
                    ts_str = time.strftime("%m-%d %H:%M", time.localtime(int(ts)))
                except Exception:
                    ts_str = "??-?? ??:??"
                prefix = ">>>" if j == match_idx else "   "
                text = rec.get("text", "")[:200]
                role = rec.get("role", "user")
                if role == "user" and sender:
                    lines.append(f"{prefix}[{ts_str}] {sender}: {text}")
                elif role == "assistant":
                    lines.append(f"{prefix}[{ts_str}] A: {text}")
                else:
                    lines.append(f"{prefix}[{ts_str}] ?: {text}")
            if lines and lines[-1] != "":
                lines.append("")
            displayed += 1
        if lines and lines[-1] == "":
            lines.pop()
        header = f"搜索 \"{query}\" 在 [{chat_name}] 找到 {len(matches)} 条匹配 (显示前 {displayed}):"
        return header + "\n" + "\n".join(lines)

    def load_index(self) -> dict:
        if not self._index_path.is_file():
            return {"version": 1, "sessions": {}, "aliases": {}}
        try:
            raw = self._index_path.read_text(encoding="utf-8")
            return json.loads(raw)
        except Exception:
            return {"version": 1, "sessions": {}, "aliases": {}}

    def save_index(self, index: dict) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_meta(self, chat_name: str) -> dict | None:
        index = self.load_index()
        sessions = index.get("sessions", {})
        for key, meta in sessions.items():
            if meta.get("dir") == self._safe_name(chat_name) or key == chat_name:
                return meta
        return None

    def save_meta(self, chat_name: str, key: str, meta: dict) -> None:
        index = self.load_index()
        if "sessions" not in index:
            index["sessions"] = {}
        meta["dir"] = self._safe_name(chat_name)
        index["sessions"][key] = meta
        self.save_index(index)

    def remove_session(self, key: str) -> None:
        index = self.load_index()
        if key in index.get("sessions", {}):
            del index["sessions"][key]
            self.save_index(index)

    def set_alias(self, alias_key: str, canonical_key: str) -> None:
        index = self.load_index()
        if "aliases" not in index:
            index["aliases"] = {}
        index["aliases"][alias_key] = canonical_key
        self.save_index(index)
