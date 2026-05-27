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
CONFIG_PATH="${1:-config.toml}"
shift || true
DEPS_MARKER="$VENV_DIR/.deps_installed"
LOG_DIR="$ROOT_DIR/logs"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[setup] creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if [[ ! -f "$DEPS_MARKER" || requirements.txt -nt "$DEPS_MARKER" ]]; then
  echo "[setup] installing dependencies"
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -r requirements.txt
  date > "$DEPS_MARKER"
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "[setup] $CONFIG_PATH not found, creating from config.toml.example"
  cp config.toml.example "$CONFIG_PATH"
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/calibrate_title_private_$(date +%Y%m%d_%H%M%S).log"

echo "[run] python carlibrate_title_ui.py --config $CONFIG_PATH --section chat_title_region_private --enable-key chat_title_region_private_enabled $*"
echo "[log] $LOG_FILE"
python -u carlibrate_title_ui.py \
  --config "$CONFIG_PATH" \
  --section "chat_title_region_private" \
  --enable-key "chat_title_region_private_enabled" \
  --ui-title "WeChat 私聊标题区域校准" \
  --label "PRIVATE" \
  "$@" 2>&1 | tee -a "$LOG_FILE"
