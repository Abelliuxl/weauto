#!/usr/bin/env python3
"""Fix corrupted sender names in chat history date files.

Scans data/chat_history/<chat>/YYYY-MM-DD.txt files for lines where
the sender is the chat room name but the message text starts with
a known person name, indicating the OCR merged the name into the text.

Rewrites: [time] U(群-临沧): 巴音布鲁克之士 message
      to: [time] U(巴音布鲁克之士): message
"""
import re
from pathlib import Path

from wechat_rpa.people_aliases import PersonAliasResolver

CHAT_HISTORY_DIR = Path("data/chat_history")
ALIASES_PATH = "data/config/PEOPLE_ALIASES.md"
LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+([AU])(?:\(([^)]*)\))?:\s+(.*)$"
)


def main():
    resolver = PersonAliasResolver(ALIASES_PATH, enabled=True)
    resolver.resolve("_trigger_load_")
    reverse_aliases: dict[str, str] = {}
    for canonical, aliases in resolver._reverse_aliases.items():
        for alias in aliases:
            reverse_aliases[alias] = canonical
        reverse_aliases[canonical] = canonical
    print(f"Loaded {len(reverse_aliases)} known person names/aliases")

    fixed_count = 0
    for chat_dir in sorted(CHAT_HISTORY_DIR.iterdir()):
        if not chat_dir.is_dir():
            continue
        if chat_dir.name.startswith("_"):
            continue
        for date_file in sorted(chat_dir.glob("*.txt")):
            lines = date_file.read_text(encoding="utf-8", errors="replace").splitlines()
            new_lines = []
            file_fixed = 0
            for line in lines:
                m = LINE_RE.match(line)
                if not m:
                    new_lines.append(line)
                    continue
                time_part = m.group(1)
                role = m.group(2)
                sender = (m.group(3) or "").strip()
                text = m.group(4)

                if role != "U" or not sender:
                    new_lines.append(line)
                    continue

                stripped = re.sub(r"\s+", " ", text).strip()
                if not stripped:
                    new_lines.append(line)
                    continue

                matched = False

                # 1) Exact alias match (longest first)
                for alias in sorted(reverse_aliases, key=len, reverse=True):
                    if stripped.startswith(alias + " ") or stripped.startswith(alias + "\n"):
                        rest = stripped[len(alias):].strip()
                        if rest:
                            new_line = f"[{time_part}] U({alias}): {rest}"
                            new_lines.append(new_line)
                            file_fixed += 1
                            matched = True
                            break
                    elif stripped == alias:
                        new_line = f"[{time_part}] U({alias}):"
                        new_lines.append(new_line)
                        file_fixed += 1
                        matched = True
                        break

                # 2) Try resolve() on the first word (catches wildcards like *cong)
                if not matched and " " in stripped:
                    first_word = stripped.split(" ", 1)[0]
                    resolved = resolver.resolve(first_word)
                    if resolved and resolved != first_word:
                        rest = stripped[len(first_word):].strip()
                        if rest:
                            new_line = f"[{time_part}] U({first_word}): {rest}"
                            new_lines.append(new_line)
                            file_fixed += 1
                            matched = True

                if not matched:
                    new_lines.append(line)

            if file_fixed:
                date_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                print(f"  {chat_dir.name}/{date_file.name}: fixed {file_fixed} lines")
                fixed_count += file_fixed

    if fixed_count:
        print(f"\nTotal: {fixed_count} lines fixed.")
    else:
        print("\nNo corrupted sender names found.")


if __name__ == "__main__":
    main()
