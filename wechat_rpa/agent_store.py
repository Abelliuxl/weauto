from __future__ import annotations

import re
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

    def _path(self, name: str) -> Path:
        return self.base / name / "SKILL.md"

    def _get(self, name: str) -> HotFile:
        if name not in self._files:
            self._files[name] = HotFile(self._path(name))
        return self._files[name]

    def list(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir())

    def read(self, name: str) -> str:
        return self._get(name).read()

    def write(self, name: str, content: str) -> None:
        self._get(name).write(content)

    def cleanup(self) -> None:
        if not self.base.is_dir():
            return
        for d in list(self.base.iterdir()):
            if not d.is_dir():
                continue
            skill_file = d / "SKILL.md"
            if not skill_file.is_file() or not skill_file.read_text(encoding="utf-8", errors="replace").strip():
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()

    def list(self) -> list[str]:
        if not self.base.is_dir():
            return []
        return sorted(d.name for d in self.base.iterdir() if d.is_dir())

    def read(self, name: str) -> str:
        return self._get(name).read()

    def write(self, name: str, content: str) -> None:
        self._get(name).write(content)

    def delete(self, name: str) -> None:
        path = self._path(name)
        if path.is_file():
            path.unlink()
        parent = path.parent
        if parent.is_dir():
            for f in parent.iterdir():
                f.unlink()
            parent.rmdir()
        self._files.pop(name, None)


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
    def __init__(self, base_dir: str | Path = DATA_DIR / "chat_history", max_lines: int = 500) -> None:
        self.base = Path(base_dir)
        self.max_lines = max_lines
        self._files: dict[str, HotFile] = {}

    def _safe_name(self, name: str) -> str:
        s = name.replace(" ", "_").replace("/", "_").replace("@", "_at_")
        return "".join(c for c in s if c.isalnum() or c in "_-.")

    def _path(self, name: str) -> Path:
        return self.base / f"{self._safe_name(name)}.txt"

    def append(self, chat_title: str, sender: str, text: str, ts: str = "") -> None:
        path = self._path(chat_title)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not ts:
            ts = time.strftime("%Y-%m-%d %H:%M")
        line = f"[{ts}] {sender}: {text}" if sender else f"[{ts}] {text}"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        self._trim(path)

    def _trim(self, path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > self.max_lines:
                path.write_text("\n".join(lines[-self.max_lines:]) + "\n", encoding="utf-8")
        except OSError:
            pass

    def read_recent(self, chat_title: str, limit: int = 50) -> str:
        path = self._path(chat_title)
        if not path.is_file():
            return ""
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-limit:])
        except OSError:
            return ""

    def read_all_recent(self, limit_per_chat: int = 20, max_chats: int = 8) -> str:
        if not self.base.is_dir():
            return ""
        files = sorted(self.base.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        sections: list[str] = []
        count = 0
        for f in files:
            if f.suffix != ".txt" or count >= max_chats:
                continue
            chat = f.stem
            lines = self.read_recent(chat, limit=limit_per_chat)
            if lines:
                sections.append(f"--- {chat} ---\n{lines}")
                count += 1
        return "\n\n".join(sections)
