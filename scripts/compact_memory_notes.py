#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wechat_rpa.action_processor import _normalize_managed_heading  # noqa: E402


def _backup_file(path: Path, *, backup_root: Path) -> Path:
    rel = path.relative_to(ROOT)
    backup_path = backup_root / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return backup_path


def _compact_file(path: Path, *, heading: str, max_items: int, dry_run: bool, backup_root: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    raw = path.read_text(encoding="utf-8", errors="replace")
    compacted = _normalize_managed_heading(raw, heading=heading, max_items=max_items)
    if compacted == raw:
        return False, "unchanged"
    if not dry_run:
        _backup_file(path, backup_root=backup_root)
        path.write_text(compacted, encoding="utf-8")
    return True, f"{len(raw)} -> {len(compacted)} chars"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact weauto append-only memory and person impression markdown files.")
    parser.add_argument("--memory-max-items", type=int, default=200)
    parser.add_argument("--impression-max-items", type=int, default=80)
    parser.add_argument("--memory-dir", default="data/memory")
    parser.add_argument("--people-dir", default="data/people")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    memory_max = max(20, int(args.memory_max_items))
    impression_max = max(20, int(args.impression_max_items))
    backup_root = ROOT / "data" / ".backup" / f"compact-memory-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    targets: list[tuple[Path, str, int]] = []
    memory_dir = ROOT / args.memory_dir
    for name in ("core.md", "timeline.md"):
        targets.append((memory_dir / name, "## 追加记忆", memory_max))

    people_dir = ROOT / args.people_dir
    if people_dir.is_dir():
        for path in sorted(people_dir.glob("*.md")):
            targets.append((path, "## 补充观察", impression_max))

    changed = 0
    for path, heading, max_items in targets:
        did_change, detail = _compact_file(
            path,
            heading=heading,
            max_items=max_items,
            dry_run=bool(args.dry_run),
            backup_root=backup_root,
        )
        if did_change:
            changed += 1
            print(f"changed {path.relative_to(ROOT)}: {detail}")
        elif detail != "missing":
            print(f"ok      {path.relative_to(ROOT)}: {detail}")

    mode = "dry-run" if args.dry_run else "write"
    print(f"done mode={mode} changed_files={changed} backup_dir={backup_root.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
