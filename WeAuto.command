#!/usr/bin/env bash
# WeAuto 双击启动器（.command）
#
# 双击此文件即可启动 WeAuto 控制面板（菜单栏图标 + Web UI + Bot）。
# 启动后这个 Terminal 窗口会自动关闭，只留下菜单栏图标。
#
# 安装建议：把这个文件做成别名/拷贝放到桌面或「应用程序」文件夹，
# 双击即用。也可直接在 Finder 里双击运行。
#
# 原理：用 nohup 把控制面板脱离当前 shell 后台运行，脚本随即退出，
# Terminal 窗口自动关闭。控制面板本身不需要终端。

set -euo pipefail

# 定位项目根目录（本文件就放在根目录）。
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# 用项目的 venv python（与 start_app.sh 一致的依赖环境）。
PYTHON="$ROOT_DIR/.venv312/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  # venv 不存在时，回退到首次运行会自动建 venv 的 start_app.sh。
  # 但后台运行没法交互，所以这里直接报错提示用户先跑一次 start_app.sh。
  echo "⚠️  未找到 .venv312，请先在终端运行一次："
  echo "    ./start_app.sh --headless --no-bot"
  echo "    （用于初始化 venv 和安装依赖）"
  echo ""
  echo "按回车键关闭此窗口…"
  read -r _
  exit 1
fi

# 确保环境变量加载（与 start_app.sh 一致）。
for env_file in "$ROOT_DIR/.env.weauto" "$ROOT_DIR/.env"; do
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
done

export WEAUTO_SCREENSHOT_HIGH_RES="${WEAUTO_SCREENSHOT_HIGH_RES:-0}"

# 启动日志（nohup 后台进程的 stderr 落到这里，便于排障）。
APP_LOG="$ROOT_DIR/logs/app_launcher.log"
mkdir -p "$ROOT_DIR/logs"

# nohup + disown 让进程脱离当前 shell；重定向所有输出。
# 这样脚本退出、Terminal 关闭后，控制面板仍持续运行。
echo "[launcher] 启动 WeAuto 控制面板…"
nohup "$PYTHON" -u -m app.main >> "$APP_LOG" 2>&1 &
LAUNCHER_PID=$!
disown "$LAUNCHER_PID" 2>/dev/null || true

echo "[launcher] 已后台启动 (pid=$LAUNCHER_PID)"
echo "[launcher] 菜单栏图标应已出现，Web 控制台: http://127.0.0.1:8721"
echo "[launcher] 启动日志: $APP_LOG"

# 留 1.5 秒让用户看到反馈，然后退出（Terminal 窗口随之关闭）。
sleep 1.5

# 主动关闭当前 Terminal 窗口（若 osascript 可用且当前进程在 Terminal.app 里）。
# 这一步是可选的——脚本退出后 .command 窗口通常也会自动关闭，但显式关闭更可靠。
if command -v osascript >/dev/null 2>&1; then
  osascript -e 'tell application "Terminal" to close (every window whose name contains "WeAuto")' 2>/dev/null || true
fi

exit 0
