#!/usr/bin/env python3
"""Clean tool output records from session history files.

Reads data/session_memory.json index + data/session_memory.sessions/*.json files,
removes all history records where source=="tool", and writes back cleaned files.
Creates a backup of the original files before modifying.
"""
import json
import shutil
import sys
import time
from pathlib import Path

SESSION_INDEX = Path("data/session_memory.json")
SESSIONS_DIR = Path("data/session_memory.sessions")
BACKUP_DIR = Path("data/session_memory_backup_pre_clean")


def backup_original() -> None:
    if BACKUP_DIR.exists():
        print(f"[skip] Backup dir already exists: {BACKUP_DIR}")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_INDEX.exists():
        shutil.copy2(SESSION_INDEX, BACKUP_DIR / SESSION_INDEX.name)
    if SESSIONS_DIR.is_dir():
        dest = BACKUP_DIR / SESSIONS_DIR.name
        shutil.copytree(SESSIONS_DIR, dest)
    print(f"[backup] Original files backed up to {BACKUP_DIR}")


def clean_session_file(session_path: Path) -> int:
    try:
        raw = session_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  [error] Cannot read {session_path.name}: {e}")
        return 0
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"  [error] Invalid JSON in {session_path.name}: {e}")
        return 0
    history = data.get("history", [])
    if not history:
        return 0
    removed = 0
    cleaned = []
    for rec in history:
        if isinstance(rec, dict) and rec.get("source") == "tool":
            removed += 1
        else:
            cleaned.append(rec)
    if removed == 0:
        return 0
    data["history"] = cleaned
    data["saved_at"] = int(time.time())
    try:
        session_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"  [error] Cannot write {session_path.name}: {e}")
        return 0
    return removed


def rebuild_index() -> dict[str, dict]:
    try:
        raw = SESSION_INDEX.read_text(encoding="utf-8")
        index = json.loads(raw)
    except Exception as e:
        print(f"[error] Cannot read index: {e}")
        sys.exit(1)
    aliases = index.get("aliases", {})
    sessions = index.get("sessions", {})
    for key, meta in sessions.items():
        relpath = meta.get("path", "")
        session_path = SESSIONS_DIR / Path(relpath).name
        if session_path.exists():
            try:
                data = json.loads(session_path.read_text(encoding="utf-8"))
                history = data.get("history", [])
                meta["history_count"] = len(history)
                meta["updated_at"] = data.get("saved_at", meta.get("updated_at", 0))
            except Exception:
                pass
    index["sessions"] = sessions
    index["aliases"] = aliases
    return index


def main() -> None:
    if not SESSION_INDEX.exists():
        print("[skip] No session index found, nothing to clean.")
        return
    if not SESSIONS_DIR.is_dir():
        print("[skip] No sessions directory found.")
        return

    backup_original()

    print("Cleaning session files...")
    total_removed = 0
    files_cleaned = 0
    for session_path in sorted(SESSIONS_DIR.glob("*.json")):
        removed = clean_session_file(session_path)
        if removed > 0:
            print(f"  [clean] {session_path.name}: removed {removed} tool records")
            total_removed += removed
            files_cleaned += 1

    if total_removed == 0:
        print("[done] No tool records found in any session.")
        return

    print(f"\n[{files_cleaned}] files cleaned, [{total_removed}] tool records removed total.")

    print("Rebuilding index...")
    new_index = rebuild_index()
    SESSION_INDEX.write_text(
        json.dumps(new_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("[done] Index rebuilt and saved.")

    print(f"\nAll done. Backup at: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
