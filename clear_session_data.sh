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
SESSION_KEY="${2:-}"
shift 2 || true

if [[ -z "$SESSION_KEY" ]]; then
  echo "usage: ./clear_session_data.sh <config.toml> <session_key> [--dry-run]"
  exit 2
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[setup] creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "[run] python clear_session_data.py --config $CONFIG_PATH \"$SESSION_KEY\" $*"
python clear_session_data.py --config "$CONFIG_PATH" "$SESSION_KEY" "$@"
