#!/usr/bin/env python3
"""Migrate chat history from old JSON format to date-based text files.

Converts:
  Old: data/session_memory.json (index) + data/session_memory.sessions/*.json (history)
  New: data/chat_history/<chat_dir>/YYYY-MM-DD.txt (history) + data/chat_history/_index.json (metadata)

Skips tool output records (source="tool"). Original files are NOT deleted.
"""
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

OLD_INDEX = Path("data/session_memory.json")
OLD_SESSIONS_DIR = Path("data/session_memory.sessions")
NEW_BASE = Path("data/chat_history")
NEW_INDEX = NEW_BASE / "_index.json"


def safe_name(name: str) -> str:
    s = name.replace(" ", "_").replace("/", "_").replace("@", "_at_")
    return "".join(c for c in s if c.isalnum() or c in "_-.") or "unnamed"


def parse_old_format() -> tuple[dict, list]:
    if not OLD_INDEX.exists():
        print("[error] Old session index not found:", OLD_INDEX)
        sys.exit(1)

    raw = OLD_INDEX.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[error] Cannot parse {OLD_INDEX}: {e}")
        sys.exit(1)

    version = int(data.get("version", 0) or 0)
    sessions_raw = data.get("sessions", {})
    aliases_raw = data.get("aliases", {})

    if not isinstance(sessions_raw, dict):
        print("[error] sessions is not a dict")
        sys.exit(1)

    sessions = []
    for key, session_data in sessions_raw.items():
        if not isinstance(session_data, dict):
            continue
        key_str = str(key).strip()
        if not key_str:
            continue

        records: list[dict] = []
        titles: list[str] = []
        short: list[str] = []
        summary: str = ""
        muted: bool = False

        if version >= 3:
            relpath = str(session_data.get("path", "")).strip()
            session_file = OLD_SESSIONS_DIR / Path(relpath).name if relpath else None
            if session_file and session_file.exists():
                try:
                    payload = json.loads(session_file.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        history = payload.get("history", [])
                        if isinstance(history, list):
                            for rec in history:
                                if isinstance(rec, dict) and rec.get("source") != "tool":
                                    records.append(rec)
                        short = [str(x) for x in (payload.get("short", []) or []) if isinstance(payload.get("short", []), list)]
                        summary = str(payload.get("summary", ""))
                        muted = bool(payload.get("muted", False))
                        titles = [str(t) for t in (payload.get("titles", []) or []) if isinstance(payload.get("titles", []), list)]
                except Exception as e:
                    print(f"  [warn] Cannot read {session_file.name}: {e}")

            if not titles:
                titles = [str(t) for t in (session_data.get("titles", []) or []) if isinstance(session_data.get("titles", []), list)]
            if not short:
                short = [str(x) for x in (session_data.get("short", []) or []) if isinstance(session_data.get("short", []), list)]
            if not summary:
                summary = str(session_data.get("summary", ""))
            muted = bool(session_data.get("muted", muted))
        else:
            history = session_data.get("history", [])
            if isinstance(history, list):
                for rec in history:
                    if isinstance(rec, dict) and rec.get("source") != "tool":
                        records.append(rec)
            if not records:
                short_items = session_data.get("short", [])
                if isinstance(short_items, list):
                    for entry in short_items:
                        val = str(entry).strip()
                        if not val:
                            continue
                        role = "assistant" if val.startswith("A:") else "user"
                        text = val.split(":", 1)[1].strip() if ":" in val else val
                        records.append({
                            "role": role,
                            "content_type": "text",
                            "text": text,
                            "sender": "",
                            "source": "legacy",
                            "observed_at": 0,
                        })
            short = [str(x) for x in (session_data.get("short", []) or []) if isinstance(session_data.get("short", []), list)]
            summary = str(session_data.get("summary", ""))
            muted = bool(session_data.get("muted", False))
            titles = [str(t) for t in (session_data.get("titles", []) or []) if isinstance(session_data.get("titles", []), list)]

        sessions.append({
            "key": key_str,
            "records": records,
            "titles": titles,
            "short": short,
            "summary": summary,
            "muted": muted,
        })

    aliases = {}
    if isinstance(aliases_raw, dict):
        for k, v in aliases_raw.items():
            k2 = str(k).strip()
            v2 = str(v).strip()
            if k2 and v2:
                aliases[k2] = v2

    return {
        "version": 1,
        "sessions": {},
        "aliases": aliases,
    }, sessions


def write_date_files(sessions: list[dict]) -> dict[str, dict]:
    index_sessions: dict[str, dict] = {}
    now_ts = int(time.time())

    for sess in sessions:
        key = sess["key"]
        titles = sess["titles"]
        display_title = titles[0] if titles else key
        dir_name = safe_name(display_title)

        records = sess["records"]
        if not records:
            message_count = 0
        else:
            by_date: dict[str, list[dict]] = defaultdict(list)
            for rec in records:
                obs = rec.get("observed_at", 0)
                try:
                    ts = int(obs)
                except Exception:
                    ts = 0
                if ts <= 0:
                    ts = int(time.time())
                date_str = time.strftime("%Y-%m-%d", time.localtime(ts))
                by_date[date_str].append(rec)

            chat_dir = NEW_BASE / dir_name
            chat_dir.mkdir(parents=True, exist_ok=True)

            for date_str, date_records in sorted(by_date.items()):
                date_path = chat_dir / f"{date_str}.txt"
                with open(date_path, "w", encoding="utf-8") as f:
                    for rec in date_records:
                        obs = rec.get("observed_at", 0)
                        try:
                            ts = int(obs)
                        except Exception:
                            ts = int(time.time())
                        time_str = time.strftime("%H:%M:%S", time.localtime(ts))
                        role = str(rec.get("role", "user"))
                        sender = re.sub(r"\s+", " ", str(rec.get("sender", ""))).strip()
                        text = re.sub(r"\s+", " ", str(rec.get("text", ""))).strip()
                        if not text:
                            continue
                        if role == "user" and sender:
                            line = f"[{time_str}] U({sender}): {text}"
                        else:
                            prefix = "A" if role == "assistant" else "U"
                            line = f"[{time_str}] {prefix}: {text}"
                        f.write(line + "\n")

            message_count = len(records)

        index_sessions[key] = {
            "dir": dir_name,
            "short": sess["short"],
            "summary": sess["summary"],
            "muted": sess["muted"],
            "titles": titles,
            "message_count": message_count,
            "updated_at": now_ts,
        }

    return index_sessions


def main():
    print("Reading old format...")
    index, sessions = parse_old_format()
    print(f"  Found {len(sessions)} sessions")

    NEW_BASE.mkdir(parents=True, exist_ok=True)

    print("Writing date-based files...")
    index["sessions"] = write_date_files(sessions)

    total_records = sum(
        m.get("message_count", 0) for m in index["sessions"].values()
    )
    print(f"  Total records: {total_records}")

    print(f"Writing index: {NEW_INDEX}")
    NEW_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print()
    print("Migration complete!")
    print(f"  New index: {NEW_INDEX}")
    print(f"  Chat directories: {NEW_BASE}")
    print(f"  Total sessions: {len(index['sessions'])}")
    print(f"  Total messages: {total_records}")
    print()
    print("Original files are preserved at:")
    print(f"  {OLD_INDEX}")
    print(f"  {OLD_SESSIONS_DIR}")
    print()
    print("After verifying the new data works, you may delete the old files.")


if __name__ == "__main__":
    main()
