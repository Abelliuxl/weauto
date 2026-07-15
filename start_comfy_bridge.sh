#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${UV_PROJECT_ENVIRONMENT:-.venv312}"
UV_PYTHON="${UV_PYTHON:-3.12.13}"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
if [[ ! -x "$UV_BIN" ]] && command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
fi
if [[ ! -x "$UV_BIN" ]]; then
  echo "[fatal] standalone uv not found at $UV_BIN" >&2
  exit 1
fi
export UV_PROJECT_ENVIRONMENT="$VENV_DIR"
"$UV_BIN" sync --locked --python "$UV_PYTHON"
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
