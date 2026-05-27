from __future__ import annotations

from pathlib import Path
import re


CONFIG_DIR = Path("data/config")
SKILLS_DIR = Path("data/skills")

_CONFIG_FILES = ["AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md", "SKILLS.md"]


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _clip_text(text: object, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    return clean[:max(8, limit)]


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,4}", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for token in raw:
        t = token.strip().lower()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:12]


def _norm(text: str) -> str:
    raw = re.sub(r"\s+", "", text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw)


def _rough_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    short = a if len(a) <= len(b) else b
    long = b if len(a) <= len(b) else a
    if short in long:
        return min(1.0, len(short) / max(1, len(long)))
    common = 0
    for token in _tokens(short):
        if token in long:
            common += len(token)
    return common / max(1, len(short))


def _iter_skill_paths(skills_dir: Path = SKILLS_DIR) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for path in sorted(skills_dir.glob("*/SKILL.md")):
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(path)
    for path in sorted(skills_dir.glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _skill_meta_from_path(path: Path) -> dict:
    try:
        rel = path.relative_to(Path.cwd()).as_posix()
    except Exception:
        try:
            rel = path.relative_to(SKILLS_DIR).as_posix()
        except Exception:
            rel = path.name
    content = _safe_read(path)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title = path.parent.name if path.name.upper() == "SKILL.MD" else path.stem
    summary = ""
    keywords: list[str] = []
    for line in lines[:12]:
        if line.startswith("# "):
            title = line[2:].strip() or title
            continue
        if line.startswith("- 用途:") or line.startswith("- 说明:"):
            summary = line.split(":", 1)[1].strip()[:120]
            continue
        if line.startswith("- 触发词:") or line.startswith("- 关键词:") or line.startswith("- tags:") or line.startswith("- keywords:"):
            raw = line.split(":", 1)[1].strip()
            keywords = [token.strip()[:24] for token in re.split(r"[，,、；;|]", raw) if token.strip()][:10]
            continue
        if (not summary) and (not line.startswith("#")) and (not line.startswith("##")):
            summary = line.lstrip("- ").strip()[:120]
    return {
        "name": _clip_text(title, 40) or title,
        "path": rel,
        "summary": _clip_text(summary, 120),
        "keywords": keywords,
        "content": content,
    }


def list_skills(skills_dir: Path = SKILLS_DIR) -> list[dict]:
    out: list[dict] = []
    for path in _iter_skill_paths(skills_dir):
        meta = _skill_meta_from_path(path)
        out.append({
            "name": meta.get("name", ""),
            "path": meta.get("path", ""),
            "summary": meta.get("summary", ""),
            "keywords": list(meta.get("keywords", []) or []),
        })
    return out


def select_skills(*, query: str, limit: int = 2, skills_dir: Path = SKILLS_DIR) -> list[dict]:
    clean_query = _clip_text(query, 240)
    if not clean_query:
        return []
    q_norm = _norm(clean_query)
    q_tokens = _tokens(clean_query)
    ranked: list[tuple[float, dict]] = []
    for path in _iter_skill_paths(skills_dir):
        meta = _skill_meta_from_path(path)
        hay = " ".join([
            str(meta.get("name", "")),
            str(meta.get("summary", "")),
            " ".join(str(x) for x in (meta.get("keywords", []) or [])),
            str(meta.get("content", ""))[:800],
        ])
        hay_norm = _norm(hay)
        score = 0.0
        if q_norm and hay_norm:
            score += 2.0 * _rough_ratio(q_norm, hay_norm)
        score += 0.6 * sum(1.0 for token in q_tokens if token in hay_norm)
        for keyword in meta.get("keywords", []) or []:
            norm_keyword = _norm(str(keyword))
            if not norm_keyword:
                continue
            if norm_keyword in q_norm or q_norm in norm_keyword:
                score += 1.8
        if score <= 0.45:
            continue
        ranked.append((score, meta))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [meta for _, meta in ranked[:max(1, int(limit))]]


def _skills_index_text(*, limit: int = 12, skills_dir: Path = SKILLS_DIR) -> str:
    rows: list[str] = []
    for meta in list_skills(skills_dir)[:max(1, int(limit))]:
        row = f"- {meta.get('name', '')}: {meta.get('summary', '') or '无摘要'}"
        keywords = [str(x).strip() for x in (meta.get("keywords", []) or []) if str(x).strip()]
        if keywords:
            row += f" | 触发词: {', '.join(keywords[:6])}"
        row += f" | 路径: {meta.get('path', '')}"
        rows.append(row[:220])
    return "\n".join(rows)[:2200]


def build_prompt_context(
    *,
    config_dir: Path = CONFIG_DIR,
    skills_dir: Path = SKILLS_DIR,
    include_long_term: bool = False,
    skill_query: str = "",
    max_skills: int = 2,
) -> str:
    names = list(_CONFIG_FILES)
    if include_long_term:
        names.append("MEMORY.md")
    parts: list[str] = []
    for name in names:
        path = config_dir / name
        content = _safe_read(path)
        if not content:
            continue
        parts.append(f"[{name}]\n{content[:4000]}")

    skill_texts: list[str] = []
    if skill_query:
        selected = select_skills(query=skill_query, limit=max_skills, skills_dir=skills_dir)
    else:
        selected = list_skills(skills_dir)[:max_skills]
    for idx, meta in enumerate(selected, start=1):
        name = _clip_text(meta.get("name", ""), 60) or "unnamed"
        rel_path = _clip_text(meta.get("path", ""), 160)
        content = str(meta.get("content", "") or "").strip()
        if not content:
            continue
        skill_texts.append(f"{idx}. {name} path={rel_path}\n{content[:5000]}")
    if skill_texts:
        parts.append(f"[skills ({len(skill_texts)} total)]\n" + "\n\n".join(skill_texts))
    return "\n\n".join(parts)[:24000]
