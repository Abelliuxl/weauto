#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from wechat_rpa.config import load_config


def _slug(text: str) -> str:
    raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", text or "").strip("-").lower()
    return raw[:80] or "session"


def _expand_title_variants(text: str) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    variants: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in variants:
            variants.append(value)

    normalized = (
        clean.replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("（", "(")
        .replace("）", ")")
    )
    base = re.sub(r"\(\d+\)\s*$", "", normalized).strip()
    add(clean)
    add(normalized)
    add(base)
    if base.startswith("群-"):
        add("群" + base[2:])
    elif base.startswith("群") and len(base) > 1:
        add("群-" + base[1:])
    return variants


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _collect_title_candidates(
    *,
    session_key: str,
    index_data: dict,
    workspace_state_path: Path,
) -> list[str]:
    out: list[str] = []

    def add(value: object) -> None:
        for item in _expand_title_variants(str(value or "").strip()):
            if item not in out:
                out.append(item)

    add(session_key)
    meta = {}
    sessions = index_data.get("sessions", {})
    if isinstance(sessions, dict):
        meta = sessions.get(session_key, {}) if isinstance(sessions.get(session_key, {}), dict) else {}
    for item in meta.get("titles", []) if isinstance(meta, dict) else []:
        add(item)
    aliases = index_data.get("aliases", {})
    if isinstance(aliases, dict):
        for alias_key, alias_target in aliases.items():
            if str(alias_target).strip() == session_key:
                add(alias_key)
    state = _load_json(workspace_state_path)
    if isinstance(state, dict):
        add(state.get("title", ""))
    return out


def _delete_file(path: Path, *, dry_run: bool, removed: list[str]) -> None:
    if not path.exists():
        return
    removed.append(str(path))
    if not dry_run:
        path.unlink()


def _clean_daily_memory(
    *,
    memory_dir: Path,
    title_candidates: list[str],
    dry_run: bool,
) -> list[str]:
    touched: list[str] = []
    if not memory_dir.exists():
        return touched
    tokens = [f"[{value}]" for value in title_candidates if value]
    if not tokens:
        return touched
    for path in sorted(memory_dir.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", path.name):
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except Exception:
            continue
        lines = original.splitlines()
        filtered = [line for line in lines if not any(token in line for token in tokens)]
        if filtered == lines:
            continue
        new_text = "\n".join(filtered).rstrip() + "\n"
        touched.append(f"{path} (-{len(lines) - len(filtered)} lines)")
        if not dry_run:
            path.write_text(new_text, encoding="utf-8")
    return touched


def clear_session_data(config_path: Path, session_key: str, *, dry_run: bool) -> int:
    cfg = load_config(config_path)
    base_dir = config_path.resolve().parent
    memory_path = _resolve_path(base_dir, cfg.memory_store_path)
    workspace_root = _resolve_path(base_dir, cfg.workspace_dir)
    memory_dir = workspace_root / "memory"
    session_dir = memory_dir / "sessions"
    session_state_dir = memory_dir / "session_state"

    index_data = _load_json(memory_path)
    sessions = index_data.get("sessions", {})
    aliases = index_data.get("aliases", {})
    if not isinstance(sessions, dict):
        sessions = {}
    if not isinstance(aliases, dict):
        aliases = {}

    session_meta = sessions.get(session_key, {}) if isinstance(sessions.get(session_key, {}), dict) else {}
    relpath = str(session_meta.get("path", "")).strip()
    payload_paths = []
    if relpath:
        payload_paths.append((memory_path.parent / relpath).resolve())
    payload_paths.append((memory_path.parent / f"{memory_path.stem}.sessions" / f"{_slug(session_key)}.json").resolve())

    workspace_md = session_dir / f"{_slug(session_key)}.md"
    workspace_state = session_state_dir / f"{_slug(session_key)}.json"
    title_candidates = _collect_title_candidates(
        session_key=session_key,
        index_data=index_data,
        workspace_state_path=workspace_state,
    )

    removed_files: list[str] = []
    updated_sections: list[str] = []

    if session_key in sessions:
        sessions.pop(session_key, None)
        updated_sections.append(f"index:sessions:{session_key}")
    alias_keys = [key for key, value in aliases.items() if str(value).strip() == session_key]
    for key in alias_keys:
        aliases.pop(key, None)
    if alias_keys:
        updated_sections.append(f"index:aliases:{','.join(alias_keys)}")

    for path in dict.fromkeys(payload_paths):
        _delete_file(path, dry_run=dry_run, removed=removed_files)
    _delete_file(workspace_md, dry_run=dry_run, removed=removed_files)
    _delete_file(workspace_state, dry_run=dry_run, removed=removed_files)

    daily_touched = _clean_daily_memory(
        memory_dir=memory_dir,
        title_candidates=title_candidates,
        dry_run=dry_run,
    )

    if updated_sections and not dry_run:
        index_data["sessions"] = sessions
        index_data["aliases"] = aliases
        index_data["saved_at"] = int(time.time())
        _save_json(memory_path, index_data)

    found_any = bool(updated_sections or removed_files or daily_touched)
    if not found_any:
        print(f"[clear-session] no data found for session_key={session_key}")
        return 1

    mode = "dry-run" if dry_run else "done"
    print(f"[clear-session] {mode} session_key={session_key}")
    if title_candidates:
        print(f"  title_candidates={', '.join(title_candidates)}")
    if updated_sections:
        print("  updated_index:")
        for item in updated_sections:
            print(f"    - {item}")
    if removed_files:
        print("  removed_files:")
        for item in removed_files:
            print(f"    - {item}")
    if daily_touched:
        print("  cleaned_daily_memory:")
        for item in daily_touched:
            print(f"    - {item}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear all stored memory data for a single session key."
    )
    parser.add_argument("session_key", help="Exact session key to clear, for example: 群3")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without changing files",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[clear-session] config not found: {config_path}", file=sys.stderr)
        return 2
    return clear_session_data(config_path, args.session_key, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
