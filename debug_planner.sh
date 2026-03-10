#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv312}"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
  else
    PYTHON_BIN="python3"
  fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[setup] creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

for env_file in "$ROOT_DIR/.env.weauto" "$ROOT_DIR/.env"; do
  if [[ -f "$env_file" ]]; then
    echo "[env] loading $(basename "$env_file")"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
done

CFG_PATH="${1:-config.toml}"
LATEST_MSG="${2:-帮我查一下今天魔兽世界更新，顺便看看蓝贴有没有改动。}"
REASON="${3:-new_message}"
TITLE="${4:-群-测试}"
IS_GROUP_RAW="${5:-true}"
MAX_ACTIONS_RAW="${6:-2}"

echo "[start] planner debug probe"
echo "[cfg]   path=${CFG_PATH}"
echo "[input] title=${TITLE} reason=${REASON} is_group=${IS_GROUP_RAW} max_actions=${MAX_ACTIONS_RAW}"
echo "[input] latest=${LATEST_MSG}"

python - "$CFG_PATH" "$LATEST_MSG" "$REASON" "$TITLE" "$IS_GROUP_RAW" "$MAX_ACTIONS_RAW" <<'PY'
import json
import os
import re
import sys
import time

from wechat_rpa.config import load_config
from wechat_rpa.llm import LlmReplyGenerator


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on"}


cfg_path = sys.argv[1]
latest_msg = sys.argv[2]
reason = sys.argv[3]
title = sys.argv[4]
is_group = _truthy(sys.argv[5])
try:
    max_actions = max(1, int(sys.argv[6]))
except Exception:
    max_actions = 2

cfg = load_config(cfg_path)
planner = LlmReplyGenerator(cfg.llm_planner, cfg.vision)

print(
    "[planner] "
    f"enabled={cfg.llm_planner.enabled} "
    f"base_url={cfg.llm_planner.base_url or '-'} "
    f"base_url_env={cfg.llm_planner.base_url_env or '-'} "
    f"api_key_env={cfg.llm_planner.api_key_env or '-'} "
    f"model={cfg.llm_planner.model}"
)

api_env = str(cfg.llm_planner.api_key_env or "").strip()
api_ok = bool(cfg.llm_planner.api_key) or bool(api_env and os.getenv(api_env))
print(f"[planner] api_key_present={api_ok} env_name={api_env or '-'}")

tools = [
    "remember_session_fact",
    "remember_session_event",
    "set_session_summary",
    "search_memory",
]
if cfg.tavily_enabled and (cfg.tavily_api_key or (cfg.tavily_api_key_env and os.getenv(cfg.tavily_api_key_env))):
    tools.append("web_search")
tools.extend(["remember_long_term", "mute_session", "unmute_session"])

chat_context = "U: 你能联网查一下吗 | U: 最好带上来源"
environment_context = f"[current_time]\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
session_context = "[长期摘要]用户偏好先给结论再给依据"
workspace_context = "[SOUL.md]事实类问题优先检索再答"
memory_recall = ""

if not planner.is_enabled():
    print("[err] planner backend not enabled or missing base_url/model")
    sys.exit(2)

started = time.perf_counter()
try:
    plan = planner.plan_actions(
        title=title,
        is_group=is_group,
        reason=reason,
        latest_message=latest_msg,
        chat_context=chat_context,
        environment_context=environment_context,
        session_context=session_context,
        workspace_context=workspace_context,
        memory_recall=memory_recall,
        available_tools=tools,
        max_actions=max_actions,
    )
except Exception as exc:
    print(f"[err] planner call failed: {exc}")
    sys.exit(1)
elapsed = time.perf_counter() - started

actions = plan.get("actions") if isinstance(plan, dict) else None
reply_hint = plan.get("reply_hint") if isinstance(plan, dict) else None
action_count = len(actions) if isinstance(actions, list) else 0

print(f"[ok] elapsed={elapsed:.2f}s actions={action_count} reply_hint={bool(str(reply_hint or '').strip())}")
print("[output] planner json:")
print(json.dumps(plan, ensure_ascii=False, indent=2))

if isinstance(actions, list):
    names = [str(x.get("tool", "")).strip() for x in actions if isinstance(x, dict)]
    names = [x for x in names if x]
    print(f"[summary] tools={','.join(names) if names else '-'}")
PY
