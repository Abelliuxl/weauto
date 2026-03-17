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

if [[ -f requirements.txt ]]; then
  python -m pip install -r requirements.txt >/dev/null
fi

export COMFYUI_BASE_URL="${COMFYUI_BASE_URL:-http://192.168.5.35:8188}"
export BRIDGE_HOST="${BRIDGE_HOST:-127.0.0.1}"
export BRIDGE_PORT="${BRIDGE_PORT:-8787}"
export BRIDGE_OUTPUT_DIR="${BRIDGE_OUTPUT_DIR:-$ROOT_DIR/data/generated_images}"

if [[ -z "${COMFY_WORKFLOW_PATH:-}" ]]; then
  if [[ -f "$ROOT_DIR/workflow_api.json" ]]; then
    export COMFY_WORKFLOW_PATH="$ROOT_DIR/workflow_api.json"
  else
    echo "[error] missing COMFY_WORKFLOW_PATH"
    echo "        export COMFY_WORKFLOW_PATH=/absolute/path/to/workflow_api.json"
    echo "        (ComfyUI: enable Dev Mode -> Save (API Format))"
    exit 1
  fi
fi

if [[ -z "${BRIDGE_API_KEY:-}" ]]; then
  echo "[warn] BRIDGE_API_KEY is empty (LAN without auth)"
fi

echo "[bridge] comfy=$COMFYUI_BASE_URL"
echo "[bridge] listen=http://$BRIDGE_HOST:$BRIDGE_PORT"
echo "[bridge] workflow=$COMFY_WORKFLOW_PATH"
echo "[bridge] output=$BRIDGE_OUTPUT_DIR"

python -u tools/comfyui_openai_images_bridge.py
