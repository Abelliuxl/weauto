from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import quote


BASE_URL = "https://wow.blizzard.cn/character/#"


def _norm(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "")


def _split_aliases(value: object) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.replace("，", "|").replace(",", "|").split("|") if x.strip()]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _server_matches(row: dict[str, str], query: str) -> bool:
    q = _norm(query)
    if not q:
        return False
    names = [row.get("server", ""), row.get("realm_slug", ""), *_split_aliases(row.get("aliases", ""))]
    return any(_norm(name) == q for name in names)


def _character_matches(row: dict[str, str], query: str) -> bool:
    q = _norm(query)
    if not q:
        return False
    names = [row.get("character", ""), *_split_aliases(row.get("aliases", ""))]
    return any(_norm(name) == q for name in names)


def _player_matches(row: dict[str, str], query: str) -> bool:
    q = _norm(query)
    if not q:
        return False
    names = [row.get("player", ""), *_split_aliases(row.get("aliases", ""))]
    return any(_norm(name) == q for name in names)


def _class_matches(row: dict[str, str], query: str) -> bool:
    q = _norm(query)
    if not q:
        return True
    klass = _norm(row.get("class", ""))
    aliases = {
        "dk": "死亡骑士",
        "dh": "恶魔猎手",
        "zs": "战士",
        "fs": "法师",
        "ms": "牧师",
        "qs": "圣骑士",
        "sm": "萨满",
        "lr": "猎人",
        "ss": "术士",
        "xd": "德鲁伊",
        "dz": "盗贼",
        "ws": "武僧",
        "唤魔": "唤魔师",
    }
    q = _norm(aliases.get(q, q))
    return klass == q or q in klass or klass in q


def _build_url(character: str, realm_slug: str) -> str:
    encoded = quote(character, safe="")
    return f"{BASE_URL}/{realm_slug}/{encoded}?q={encoded}"


def build(
    *,
    character: str = "",
    server: str = "",
    player: str = "",
    class_name: str = "",
    skill_dir: str | Path | None = None,
) -> dict[str, object]:
    base = Path(skill_dir) if skill_dir else Path(__file__).resolve().parent
    characters = _read_tsv(base / "characters.tsv")
    servers = _read_tsv(base / "servers.tsv")

    matches: list[dict[str, str]] = []
    if character and server:
        matches = [{"player": player, "character": character, "server": server, "class": class_name, "aliases": ""}]
    elif character:
        matches = [row for row in characters if _character_matches(row, character)]
    elif player:
        matches = [
            row
            for row in characters
            if _player_matches(row, player) and _class_matches(row, class_name)
        ]

    if not matches:
        return {
            "ok": False,
            "error": "没有找到匹配角色",
            "candidates": [],
        }
    if len(matches) > 1:
        return {
            "ok": False,
            "error": "匹配到多个角色，需要补充角色名、服务器或职业",
            "candidates": [_candidate(row) for row in matches[:8]],
        }

    row = matches[0]
    server_name = row.get("server", "") or server
    server_row = next((item for item in servers if _server_matches(item, server_name)), None)
    if server_row is None:
        return {
            "ok": False,
            "error": f"没有找到服务器 slug: {server_name}",
            "candidates": [_candidate(row)],
        }

    character_name = row.get("character", "") or character
    realm_slug = server_row.get("realm_slug", "").strip()
    url = _build_url(character_name, realm_slug)
    status = server_row.get("status", "").strip()
    note = "服务器 slug 来自 draft 表，可能需要人工修正。" if status == "draft" else ""
    message = (
        f"{row.get('player') or player} 的{row.get('class') or class_name}号："
        f"{character_name}（{server_name}）\n{url}"
    ).strip()
    if note:
        message += f"\n注：{note}"
    return {
        "ok": True,
        "player": row.get("player", "") or player,
        "character": character_name,
        "server": server_name,
        "class": row.get("class", "") or class_name,
        "realm_slug": realm_slug,
        "server_status": status,
        "url": url,
        "message": message,
        "note": note,
    }


def _candidate(row: dict[str, str]) -> dict[str, str]:
    return {
        "player": row.get("player", ""),
        "character": row.get("character", ""),
        "server": row.get("server", ""),
        "class": row.get("class", ""),
    }
