#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-.venv38}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
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
LOG_FILE="$LOG_DIR/calibrate_rows_$(date +%Y%m%d_%H%M%S).log"

echo "[run] python calibrate_rows_ui.py --config $CONFIG_PATH $*"
echo "[log] $LOG_FILE"
python -u calibrate_rows_ui.py --config "$CONFIG_PATH" "$@" 2>&1 | tee -a "$LOG_FILE"
