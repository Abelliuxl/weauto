#!/usr/bin/env bash
# Start the WeAuto control panel (menu-bar app + web server + bot supervisor).
#
# This is the modern entry point: no terminal window needed. It sets up the
# same venv / dependencies / env files as start_rpa.sh, then launches the
# control panel which supervises the bot as a subprocess and exposes a
# read-only web UI. The bot's stdout is captured by the supervisor (not tee'd
# to a terminal), so this script runs cleanly in the background.
#
# Usage:
#   ./start_app.sh                       # menu-bar app + web + bot
#   ./start_app.sh --headless            # no menu bar (SSH / CI); web + bot only
#   ./start_app.sh --no-bot              # panel only, do not spawn the bot
#   ./start_app.sh --host 0.0.0.0 --port 8721   # allow remote access
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
DEPS_MARKER="$VENV_DIR/.deps_installed"
PLAYWRIGHT_MARKER="$VENV_DIR/.playwright_chromium_installed"

_needs_refresh() {
  local marker="$1"
  shift
  if [[ ! -f "$marker" ]]; then
    return 0
  fi
  local dep=""
  for dep in "$@"; do
    if [[ -f "$dep" && "$dep" -nt "$marker" ]]; then
      return 0
    fi
  done
  return 1
}

# Optional local env files (not committed) for secrets/endpoints.
for env_file in "$ROOT_DIR/.env.weauto" "$ROOT_DIR/.env"; do
  if [[ -f "$env_file" ]]; then
    echo "[env] loading $(basename "$env_file")"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
done

# --- dependency setup (mirrors start_rpa.sh) ---
UV_BIN="${UV_BIN:-}"
if [[ -z "$UV_BIN" ]] && command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
fi
if [[ -z "$UV_BIN" && -x "$VENV_DIR/bin/uv" ]]; then
  UV_BIN="$VENV_DIR/bin/uv"
fi

if [[ -n "$UV_BIN" && -f pyproject.toml ]]; then
  echo "[setup] syncing dependencies with uv"
  UV_PROJECT_ENVIRONMENT="$VENV_DIR" "$UV_BIN" sync --python "$PYTHON_BIN"
else
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[setup] creating venv: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  if _needs_refresh "$DEPS_MARKER" requirements.txt pyproject.toml; then
    echo "[setup] installing dependencies with pip fallback"
    python -m pip install --upgrade pip >/dev/null
    python -m pip install -r requirements.txt
    date > "$DEPS_MARKER"
  fi
fi

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

if python -c "import playwright" >/dev/null 2>&1; then
  if _needs_refresh "$PLAYWRIGHT_MARKER" requirements.txt pyproject.toml uv.lock; then
    echo "[setup] installing Playwright Chromium"
    python -m playwright install chromium
    date > "$PLAYWRIGHT_MARKER"
  fi
fi

# High-res Quartz capture leaks native memory in long-running loops on some
# macOS builds. Keep it off by default; the bot reads this env var directly.
export WEAUTO_SCREENSHOT_HIGH_RES="${WEAUTO_SCREENSHOT_HIGH_RES:-0}"

# Pass remaining args through to the control panel.
echo "[run] python -u -m app.main $*"
exec python -u -m app.main "$@"
