# 运行方式与控制面板

## 1. 入口选择

| 场景 | 入口 |
|---|---|
| macOS 日常 GUI 使用 | 双击 `WeAuto.app` |
| Windows 日常 GUI 使用 | 双击 `WeAuto.vbs` |
| 菜单栏 + Web UI + Bot | `./start_app.sh` |
| 系统托盘 + Web UI + Bot | `.\start_app.ps1` 或 `start_app.cmd` |
| SSH / 无菜单栏 | `./start_app.sh --headless` |
| Windows 无托盘 | `.\start_app.ps1 --headless` |
| 启动面板但不拉起 Bot | `./start_app.sh --no-bot` |
| 直接调试 Bot | `./start_rpa.sh config.toml` |
| 周期性强制重启 | `./start_rpa_watchdog.sh config.toml` |
| 直接运行 Python | `python run.py --config config.toml` |

不要同时运行多个入口，它们会重复扫描和处理同一批微信窗口。

## 2. `WeAuto.app`

App bundle 位于项目根目录：

```text
WeAuto.app/
  Contents/
    Info.plist
    MacOS/launcher
    Resources/AppIcon.icns
    Resources/icon_512.png
```

特性：

- `LSUIElement=1`，不显示普通 Dock 窗口
- Finder 显示 WeAuto 图标
- launcher 使用项目内 `.venv312/bin/python`
- 通过相对路径定位仓库，因此 App 本体不能脱离项目
- launcher 自身与菜单回调异常写入 `logs/app_launcher.log`

如果虚拟环境尚未创建，先运行一次 `./start_app.sh`。

## 3. `start_app.sh`

该脚本负责依赖同步和控制层启动：

```bash
./start_app.sh
./start_app.sh --headless
./start_app.sh --no-bot
./start_app.sh --host 0.0.0.0 --port 8721
./start_app.sh --config other.toml
```

最终执行：

```bash
python -u -m app.main
```

Windows 的 `start_app.ps1` 执行同一 Python 入口，并使用 `.venv312\Scripts\python.exe`；
`start_app.cmd` 是便于双击的包装，`WeAuto.vbs` 是隐藏控制台的日常入口。

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--config` | 配置文件路径 |
| `--headless` | 不运行菜单栏 App |
| `--no-bot` | supervisor 启动但不立即创建 Bot |
| `--host` | 覆盖 `[webui].host` |
| `--port` | 覆盖 `[webui].port` |
| `-v` | 控制层详细日志 |

`--no-bot` 在有菜单栏时可通过菜单栏“启动 Bot”恢复；Web UI 本身没有写操作。

## 4. 菜单栏 / 系统托盘功能

macOS 菜单栏和 Windows 系统托盘每 2 秒刷新一次，提供：

- Bot 运行状态
- 运行时长和重启次数
- bridge 模式和连接状态
- 最近活动和 heartbeat
- 打开 Web 控制台
- 启动、停止、重启 Bot
- 开关崩溃自动重启
- 优雅退出整个控制层

菜单栏标题使用绿色或红色状态标记，并显示运行时长或“已停”。

## 5. Web 控制台

默认地址：

<http://127.0.0.1:8721>

页面包括：

- 概览：进程、运行时间、重启、bridge、活动、heartbeat、最近回复
- 会话：从 `data/chat_history/_index.json` 读取会话和最近消息
- 日志：历史日志快照和 SSE 实时流

Web UI 是只读的。清屏只清除浏览器页面中的显示，不删除磁盘日志。

### API

| 路径 | 内容 |
|---|---|
| `GET /api/status` | supervisor 与 runtime state |
| `GET /api/sessions` | 会话索引 |
| `GET /api/messages?session=&limit=` | 会话消息 |
| `GET /api/logs/recent?lines=&tag=` | 环形日志快照 |
| `GET /api/logs/stream` | SSE 实时日志 |
| `GET /api/control` | 当前 running / auto_restart 状态 |

所有 POST 请求返回 `405 Method Not Allowed`。

## 6. 远程访问

```bash
./start_app.sh --headless --host 0.0.0.0 --port 8721
```

当前 Web UI 无身份认证。只应在可信内网使用，或者保留 `127.0.0.1` 并通过 SSH 转发：

```bash
ssh -L 8721:127.0.0.1:8721 user@mac-host
```

## 7. 直接 Bot 模式

```bash
./start_rpa.sh config.toml
```

该入口：

- 初始化 Python 环境
- 加载 `.env.weauto` / `.env`
- 创建缺失的配置文件
- 管理日志保留
- 将 stdout 写入 `logs/rpa_*.log`
- 把 warn / error / skip 行写入 `logs/issues.log`

适合需要直接观察 stdout 和使用 `Ctrl+C` 的调试场景。

## 8. Watchdog

```bash
./start_rpa_watchdog.sh config.toml
```

默认每 21600 秒（6 小时）终止并重新启动 `start_rpa.sh`。可用环境变量：

- `RESTART_INTERVAL_SEC`
- `RESTART_COOLDOWN_SEC`
- `CHECK_INTERVAL_SEC`
- `FORCE_KILL_AFTER_SEC`

这与控制面板的“崩溃自动重启”不同：watchdog 会按固定周期主动重启。

## 9. 恢复模式

`run.py` 还提供历史恢复命令：

```bash
python run.py recover --config config.toml --recover-countdown 3
python run.py recover-auto --config config.toml --recover-countdown 3
```

- `recover`：逐页截图，人工上滑并确认下一页
- `recover-auto`：截图后自动点击和上滑，直到无法继续

这两种模式依赖旧主窗口区域配置，不属于 detached 日常运行路径。
