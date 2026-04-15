#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-config.toml}"
EXTRA_ARGS=()
if (( "$#" > 1 )); then
  EXTRA_ARGS=("${@:2}")
fi

RESTART_INTERVAL_SEC="${RESTART_INTERVAL_SEC:-21600}"
RESTART_COOLDOWN_SEC="${RESTART_COOLDOWN_SEC:-8}"
CHECK_INTERVAL_SEC="${CHECK_INTERVAL_SEC:-5}"
FORCE_KILL_AFTER_SEC="${FORCE_KILL_AFTER_SEC:-30}"

if [[ ! "$RESTART_INTERVAL_SEC" =~ ^[0-9]+$ ]] || (( RESTART_INTERVAL_SEC <= 0 )); then
  echo "[fatal] RESTART_INTERVAL_SEC must be a positive integer, got: $RESTART_INTERVAL_SEC"
  exit 2
fi
if [[ ! "$RESTART_COOLDOWN_SEC" =~ ^[0-9]+$ ]]; then
  echo "[fatal] RESTART_COOLDOWN_SEC must be a non-negative integer, got: $RESTART_COOLDOWN_SEC"
  exit 2
fi
if [[ ! "$CHECK_INTERVAL_SEC" =~ ^[0-9]+$ ]] || (( CHECK_INTERVAL_SEC <= 0 )); then
  echo "[fatal] CHECK_INTERVAL_SEC must be a positive integer, got: $CHECK_INTERVAL_SEC"
  exit 2
fi
if [[ ! "$FORCE_KILL_AFTER_SEC" =~ ^[0-9]+$ ]] || (( FORCE_KILL_AFTER_SEC <= 0 )); then
  echo "[fatal] FORCE_KILL_AFTER_SEC must be a positive integer, got: $FORCE_KILL_AFTER_SEC"
  exit 2
fi

child_pid=""
stopping="0"

_stop_child() {
  local pid="${1:-}"
  local waited=0
  if [[ -z "$pid" ]]; then
    return 0
  fi
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  kill -TERM "$pid" >/dev/null 2>&1 || true
  while kill -0 "$pid" >/dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if (( waited >= FORCE_KILL_AFTER_SEC )); then
      kill -KILL "$pid" >/dev/null 2>&1 || true
      break
    fi
  done
  return 0
}

_on_signal() {
  stopping="1"
  _stop_child "$child_pid"
}

trap _on_signal INT TERM

echo "[watchdog] start config=$CONFIG_PATH interval=${RESTART_INTERVAL_SEC}s cooldown=${RESTART_COOLDOWN_SEC}s"

round=0
while true; do
  round=$((round + 1))
  started_at="$(date +%s)"
  deadline=$((started_at + RESTART_INTERVAL_SEC))

  echo "[watchdog] launch round=$round at=$(date '+%Y-%m-%d %H:%M:%S')"
  ./start_rpa.sh "$CONFIG_PATH" "${EXTRA_ARGS[@]}" &
  child_pid="$!"

  while true; do
    if ! kill -0 "$child_pid" >/dev/null 2>&1; then
      break
    fi
    now="$(date +%s)"
    if (( now >= deadline )); then
      echo "[watchdog] restart due: pid=$child_pid uptime=$((now - started_at))s"
      _stop_child "$child_pid"
      break
    fi
    sleep "$CHECK_INTERVAL_SEC"
  done

  wait "$child_pid" || true
  child_pid=""
  if [[ "$stopping" == "1" ]]; then
    echo "[watchdog] stopped by signal"
    exit 0
  fi

  if (( RESTART_COOLDOWN_SEC > 0 )); then
    sleep "$RESTART_COOLDOWN_SEC"
  fi
done
