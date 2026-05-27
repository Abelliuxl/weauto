#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

CFG_PATH="${1:-config.toml}"
QUERY="${2:-项目记忆 sqlite 检索 嵌入 重排}"
SESSION_KEY="${3:-example_admin}"
INCLUDE_GLOBAL_RAW="${4:-true}"
LIMIT_RAW="${5:-3}"

if [[ -f ".env.weauto" ]]; then
  echo "[env] loading .env.weauto"
  set -a
  # shellcheck disable=SC1091
  source .env.weauto
  set +a
fi

echo "[start] sqlite memory debug probe"
echo "[cfg] path=${CFG_PATH}"
echo "[input] query=${QUERY}"
echo "[input] session_key=${SESSION_KEY} include_global=${INCLUDE_GLOBAL_RAW} limit=${LIMIT_RAW}"

python - "$CFG_PATH" "$QUERY" "$SESSION_KEY" "$INCLUDE_GLOBAL_RAW" "$LIMIT_RAW" <<'PY'
import os
import sqlite3
import sys

from wechat_rpa.config import load_config
from wechat_rpa.workspace_context import WorkspaceContextManager


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


cfg_path = sys.argv[1]
query = sys.argv[2]
session_key = sys.argv[3]
include_global = _truthy(sys.argv[4])
try:
    limit = max(1, int(sys.argv[5]))
except Exception:
    limit = 3

cfg = load_config(cfg_path)
workspace = WorkspaceContextManager(
    cfg.workspace_dir,
    enabled=cfg.workspace_enabled,
    embedding_cfg=cfg.embedding,
    rerank_cfg=cfg.rerank,
    memory_rerank_enabled=cfg.workspace_memory_rerank_enabled,
    memory_rerank_shortlist=cfg.workspace_memory_rerank_shortlist,
    memory_rerank_weight=cfg.workspace_memory_rerank_weight,
    memory_sqlite_enabled=cfg.workspace_memory_sqlite_enabled,
    memory_sqlite_path=cfg.workspace_memory_sqlite_path,
    memory_sqlite_sync_interval_sec=cfg.workspace_memory_sqlite_sync_interval_sec,
    memory_sqlite_fts_limit=cfg.workspace_memory_sqlite_fts_limit,
    memory_sqlite_vector_limit=cfg.workspace_memory_sqlite_vector_limit,
    memory_sqlite_chunk_chars=cfg.workspace_memory_sqlite_chunk_chars,
)

print(
    "[cfg] sqlite "
    f"enabled={cfg.workspace_memory_sqlite_enabled} "
    f"path={cfg.workspace_memory_sqlite_path} "
    f"sync_interval={cfg.workspace_memory_sqlite_sync_interval_sec}s "
    f"fts_limit={cfg.workspace_memory_sqlite_fts_limit} "
    f"vector_limit={cfg.workspace_memory_sqlite_vector_limit} "
    f"chunk_chars={cfg.workspace_memory_sqlite_chunk_chars}"
)
print(f"[status] {workspace.sqlite_status_text()}")

# Trigger one recall so sqlite index sync can run.
hits = workspace.search_memory(
    query=query,
    session_key=session_key,
    include_global=include_global,
    limit=limit,
)
print(f"[result] hits_empty={not bool(str(hits or '').strip())}")

db_path = workspace.memory_sqlite_path
if not str(db_path):
    print("[err] sqlite path unavailable")
    sys.exit(2)

db_path = os.path.abspath(str(db_path))
print(f"[db] path={db_path}")
print(f"[db] exists={os.path.exists(db_path)}")
if not os.path.exists(db_path):
    sys.exit(3)

conn = sqlite3.connect(db_path)
try:
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    print(f"[db] tables={','.join(tables) if tables else '-'}")

    for table in ("memory_sources", "memory_chunks", "memory_chunks_fts"):
        try:
            cnt = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"[db] count {table}={cnt}")
        except Exception as exc:
            print(f"[db] count {table}=ERR({exc})")
finally:
    conn.close()
PY
