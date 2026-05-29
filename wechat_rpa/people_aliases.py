from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


def _norm(text: str) -> str:
    raw = re.sub(r"\s+", "", text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)


def _looks_like_noise_name(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return True
    if lower in {
        "unknown",
        "unknown user",
        "sender",
        "member",
        "someone",
        "other",
        "user",
        "system",
        "self",
        "assistant",
    }:
        return True
    return bool(re.fullmatch(r"[\d_ -]+", lower or ""))


def _looks_like_placeholder_person(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean or _looks_like_noise_name(clean):
        return True
    return clean in {
        "群成员",
        "其他群成员",
        "其他成员",
        "群聊成员",
        "其他人",
        "大家",
        "有人",
        "未知人物",
        "未知",
        "系统",
        "系统提示",
        "用户",
        "未知用户",
        "对方",
    }


def _normalize_name(text: str) -> str:
    clean = str(text or "")[:32]
    clean = clean.strip(" []【】()（）,，。.!！?？:：;；\"'“”‘’")
    clean = re.sub(r"^(real|test|tmp)[-_ ]*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"(也行|都行|就行|可以|呀|啊|呢|吧)$", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) < 2 or len(clean) > 24:
        return ""
    if re.search(r"[，,。.!！?？:：;；|/\\\[\]{}<>]+", clean):
        return ""
    if "、" in clean or _looks_like_noise_name(clean):
        return ""
    return clean


@dataclass(frozen=True)
class PersonResolution:
    observed_name: str
    canonical_name: str
    aliases: tuple[str, ...] = ()


class PersonAliasResolver:
    def __init__(self, aliases_path: str = "", *, enabled: bool = True) -> None:
        self.aliases_path = str(aliases_path or "").strip()
        self.enabled = bool(enabled)
        self._cache_key = ""
        self._mapping: dict[str, str] = {}
        self._patterns: list[tuple[str, str, str]] = []
        self._reverse_aliases: dict[str, list[str]] = {}
        self._mention_mapping: dict[str, str] = {}

    @staticmethod
    def _clean_mention_name(text: str) -> str:
        clean = str(text or "").strip().lstrip("@").strip()
        clean = re.sub(r"[\r\n\t]+", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:40]

    @classmethod
    def _extract_inline_mention(cls, text: str) -> tuple[str, str]:
        raw = str(text or "")
        matches = list(re.finditer(r"-?\s*\[@([^\]\r\n]+)\]\s*$", raw))
        if not matches:
            return raw, ""
        match = matches[-1]
        mention = cls._clean_mention_name(match.group(1))
        return raw[: match.start()].rstrip(" ,，、；;|"), mention

    def _maybe_reload(self) -> None:
        if not self.enabled:
            self._cache_key = ""
            self._mapping = {}
            self._patterns = []
            self._reverse_aliases = {}
            self._mention_mapping = {}
            return

        path = Path(self.aliases_path).expanduser()
        if not self.aliases_path or not path.exists():
            self._cache_key = ""
            self._mapping = {}
            self._patterns = []
            self._reverse_aliases = {}
            self._mention_mapping = {}
            return

        try:
            st = path.stat()
            cache_key = f"{path.resolve()}:{int(st.st_mtime_ns)}:{st.st_size}"
        except Exception:
            cache_key = str(path.resolve())
        if cache_key == self._cache_key:
            return

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = ""

        mapping: dict[str, str] = {}
        patterns: list[tuple[str, str, str]] = []
        reverse_aliases: dict[str, list[str]] = {}
        mention_mapping: dict[str, str] = {}
        seen_patterns: set[str] = set()
        for line in raw.splitlines():
            clean = line.strip()
            lowered = clean.strip("# ").strip().lower()
            if lowered in {"[aliases]", "aliases", "people aliases", "person aliases"}:
                continue
            if not clean or clean.startswith("#"):
                continue
            if clean.startswith("- "):
                clean = clean[2:].strip()
            sep = ""
            for token in ("->", "=>", "="):
                if token in clean:
                    sep = token
                    break
            if not sep:
                continue
            left, right = [part.strip() for part in clean.split(sep, 1)]
            right, inline_mention = self._extract_inline_mention(right)

            canonical = _normalize_name(left)
            if not canonical or _looks_like_placeholder_person(canonical):
                continue
            reverse_aliases.setdefault(canonical, [])
            if inline_mention:
                mention_mapping[_norm(canonical)] = inline_mention
            for alias in [x.strip() for x in re.split(r"[，,、；;|]", right) if x.strip()]:
                has_star = "*" in alias
                starts_star = alias.startswith("*")
                ends_star = alias.endswith("*")
                if has_star and (starts_star or ends_star):
                    core = _normalize_name(alias.strip("*"))
                    token_norm = _norm(core)
                    if not core or not token_norm:
                        continue
                    mode = "contains" if (starts_star and ends_star) else ("suffix" if starts_star else "prefix")
                    pattern_key = f"{mode}|{token_norm}|{_norm(canonical)}"
                    if pattern_key in seen_patterns:
                        continue
                    seen_patterns.add(pattern_key)
                    patterns.append((mode, token_norm, canonical))
                    reverse_aliases[canonical].append(alias)
                    if inline_mention:
                        mention_mapping[token_norm] = inline_mention
                    continue

                clean_alias = _normalize_name(alias)
                if not clean_alias or _looks_like_placeholder_person(clean_alias):
                    continue
                if _norm(clean_alias) == _norm(canonical):
                    continue
                mapping[_norm(clean_alias)] = canonical
                if inline_mention:
                    mention_mapping[_norm(clean_alias)] = inline_mention
                if clean_alias not in reverse_aliases[canonical]:
                    reverse_aliases[canonical].append(clean_alias)
            mapping[_norm(canonical)] = canonical

        self._cache_key = cache_key
        self._mapping = mapping
        self._patterns = patterns
        self._reverse_aliases = reverse_aliases
        self._mention_mapping = mention_mapping

    def resolve(self, name: str) -> str:
        self._maybe_reload()
        clean = _normalize_name(name)
        if not clean or _looks_like_placeholder_person(clean):
            return ""
        norm_clean = _norm(clean)
        canonical = self._mapping.get(norm_clean, "")
        if not canonical:
            for mode, token, target in self._patterns:
                if mode == "suffix":
                    matched = norm_clean.endswith(token)
                elif mode == "prefix":
                    matched = norm_clean.startswith(token)
                else:
                    matched = token in norm_clean
                if matched:
                    canonical = target
                    break
        return _normalize_name(canonical) or clean

    def aliases_for(self, canonical_name: str) -> list[str]:
        self._maybe_reload()
        canonical = self.resolve(canonical_name)
        if not canonical:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in self._reverse_aliases.get(canonical, []):
            clean = str(item or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
        return out[:12]

    def mention_for(self, *names: str) -> str:
        self._maybe_reload()
        candidates: list[str] = []
        for name in names:
            clean = _normalize_name(name)
            if clean:
                candidates.append(clean)
                resolved = self.resolve(clean)
                if resolved and resolved not in candidates:
                    candidates.append(resolved)
        for candidate in candidates:
            mention = self._mention_mapping.get(_norm(candidate), "")
            if mention:
                return mention
        return ""

    def build_resolution(self, observed_name: str) -> PersonResolution:
        observed = _normalize_name(observed_name)
        canonical = self.resolve(observed)
        aliases = tuple(self.aliases_for(canonical)) if canonical else ()
        return PersonResolution(observed_name=observed, canonical_name=canonical, aliases=aliases)
