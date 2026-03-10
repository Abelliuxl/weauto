#!/usr/bin/env bash
set -euo pipefail

CFG_PATH="${1:-config.toml}"
QUERY="${2:-wowhead 魔兽世界 今天 更新}"

if [[ -f ".env.weauto" ]]; then
  echo "[env] loading .env.weauto"
  set -a
  # shellcheck disable=SC1091
  source .env.weauto
  set +a
fi

echo "[start] rerank debug probe"
echo "[cfg] path=${CFG_PATH}"
echo "[query] ${QUERY}"

python - "$CFG_PATH" "$QUERY" <<'PY'
import re
import sys

from wechat_rpa.config import load_config
from wechat_rpa.workspace_context import WorkspaceContextManager

cfg_path = sys.argv[1]
query = sys.argv[2]
cfg = load_config(cfg_path)
workspace = WorkspaceContextManager(
    cfg.workspace_dir,
    enabled=cfg.workspace_enabled,
    embedding_cfg=cfg.embedding,
    rerank_cfg=cfg.rerank,
    memory_rerank_enabled=cfg.workspace_memory_rerank_enabled,
    memory_rerank_shortlist=cfg.workspace_memory_rerank_shortlist,
    memory_rerank_weight=cfg.workspace_memory_rerank_weight,
)

print(
    "[cfg] workspace_rerank "
    f"enabled={cfg.workspace_memory_rerank_enabled} "
    f"shortlist={cfg.workspace_memory_rerank_shortlist} "
    f"weight={cfg.workspace_memory_rerank_weight:.2f}"
)
print(
    "[cfg] rerank_model "
    f"enabled={cfg.rerank.enabled} "
    f"model={cfg.rerank.model} "
    f"base_url={cfg.rerank.base_url or '-'}"
)
print(f"[status] {workspace.rerank_status_text()}")

hits = workspace.search_memory(
    query=query,
    session_key="example_admin",
    include_global=True,
    limit=max(1, int(cfg.workspace_memory_search_limit)),
)
compact = re.sub(r"\s+", " ", hits or "").strip()
if compact:
    compact = compact[:320]
print(f"[result] hits={'(empty)' if not compact else compact}")
print("[done] 看日志中是否出现 '[memory-rerank] backend=model'。出现即代表已实际使用 rerank 模型。")
PY
