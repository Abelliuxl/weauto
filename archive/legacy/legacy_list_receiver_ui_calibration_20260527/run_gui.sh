#!/usr/bin/env bash
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

if python3 - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  python3 ./weauto_gui.py
else
  echo "[gui] tkinter unavailable, fallback to web ui"
  echo "[gui] opening http://127.0.0.1:8765"
  (sleep 1; open "http://127.0.0.1:8765") >/dev/null 2>&1 || true
  exec python3 ./weauto_webui.py
fi
