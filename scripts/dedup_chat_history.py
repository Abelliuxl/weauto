#!/usr/bin/env python3
"""Deduplicate chat history date files.

Removes duplicate messages within each YYYY-MM-DD.txt file,
keeping only the first occurrence of each (sender, text) pair.
Deduplication is based on content, not timestamps.
"""
import re
from pathlib import Path

CHAT_HISTORY_DIR = Path("data/chat_history")
LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+([AU])(?:\(([^)]*)\))?:\s*(.*)$"
)


def main():
    total_removed = 0
    for chat_dir in sorted(CHAT_HISTORY_DIR.iterdir()):
        if not chat_dir.is_dir() or chat_dir.name.startswith("_"):
            continue
        for date_file in sorted(chat_dir.glob("*.txt")):
            lines = date_file.read_text(encoding="utf-8", errors="replace").splitlines()
            seen = set()
            cleaned = []
            removed = 0
            for line in lines:
                m = LINE_RE.match(line.strip())
                if m:
                    sender = (m.group(3) or "").strip()
                    text = m.group(4)
                    key = (sender, text)
                    if key in seen:
                        removed += 1
                    else:
                        seen.add(key)
                        cleaned.append(line)
                else:
                    cleaned.append(line)
            if removed:
                date_file.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
                print(f"  {chat_dir.name}/{date_file.name}: removed {removed} ({len(lines)} → {len(cleaned)})")
                total_removed += removed

    print(f"\nTotal: {total_removed} duplicate messages removed." if total_removed else "\nNo duplicates found.")


if __name__ == "__main__":
    main()
