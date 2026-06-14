# 控制面板（菜单栏 App + Web UI）

WeAuto 提供一个现代入口 `start_app.sh`，替代需要常驻终端窗口的 `start_rpa.sh`。
它由三部分组成，全部封装在一个独立进程里：

1. **守护器（Supervisor）**：把现有 `run.py` 作为**子进程**拉起并守护，接管其
   stdout 日志（去 ANSI 写盘 + 过滤 `[warn|error|skip-]` 进 `issues.log`），崩溃自动
   重启。
2. **菜单栏 App**（macOS，基于 `rumps`）：顶部常驻图标，实时显示运行状态；点击下拉
   菜单可打开 Web 控制台、重启/停止/启动 Bot、切换自动重启、退出。
3. **Web 服务器**：标准库实现，提供只读状态面板、会话消息浏览、实时日志流（SSE），
   支持绑定 `0.0.0.0` 供**远程 IP 访问**。

> **零侵入承诺**：`bot.py` / `run.py` / `wechat_rpa/*` / `wechat_rpa/config.py` 均
> 未做任何改动。控制面板只读取 bot 已有的输出与文件（`runtime_state.json`、
> `chat_history/`、`logs/`）。原有的 `start_rpa.sh` 仍可独立使用。

## 1. 启动

### 双击启动（推荐，最接近原生 App）

双击项目根目录的 **`WeAuto.command`** 即可启动。它会：

1. 用 `nohup` 把控制面板脱离终端后台运行（`PPID=1`，完全独立）。
2. 启动菜单栏图标 + Web UI + Bot。
3. 约 1.5 秒后自动关闭自己弹出的 Terminal 窗口。

最终结果：**只剩菜单栏图标，没有任何终端窗口残留**。

> **首次使用前**：需先在终端跑一次 `./start_app.sh --headless --no-bot` 初始化
> `.venv312`（装依赖）。之后双击 `WeAuto.command` 即可，无需再开终端。

**放到桌面 / 「应用程序」文件夹**：把 `WeAuto.command` 拷贝（或做替身）到桌面、
Dock 或「应用程序」文件夹，以后像普通 App 一样双击/单击启动。
> 注意：`WeAuto.command` 内部用绝对路径定位项目根目录，所以如果移动的是**拷贝**
> 而非替身，需要保持原文件不动；推荐用替身（按住 ⌥⌘ 拖拽生成）。

### 命令行启动

```bash
# 默认：菜单栏 App + Web UI + Bot（与双击 .command 等价，但留在终端前台）
./start_app.sh

# 仅 Web UI + Bot，不起菜单栏（SSH 远程 / 无 GUI 环境）
./start_app.sh --headless

# 面板模式：不自动拉起 Bot，只看状态（手动从菜单栏/Web 启动）
./start_app.sh --no-bot

# 允许远程 IP 访问 Web UI（覆盖 config.toml 中的 [webui].host）
./start_app.sh --host 0.0.0.0 --port 8721
```

`start_app.sh` 复用 `start_rpa.sh` 的全部环境准备（venv / uv / Playwright / `.env`），
只是最后启动的是 `python -u -m app.main` 而非 `run.py`。

启动后：
- macOS 顶部菜单栏出现一个带状态 emoji 的图标（🟢 运行中 / 🔴 已停）。
- Web 控制台默认在 `http://127.0.0.1:8721`。
- 日志仍写入 `logs/rpa_YYYYmmdd_HHMMSS.log`（由 supervisor 接管，**不再需要终端**）。

## 2. 命令行参数

```
python -m app.main [--config config.toml] [--headless] [--no-bot]
                   [--host HOST] [--port PORT] [-v]
```

| 参数 | 说明 |
|------|------|
| `--config` | 配置文件路径（默认 `config.toml`） |
| `--headless` | 不启动菜单栏 App，只跑 supervisor + Web（SSH/CI 场景） |
| `--no-bot` | 不自动拉起 Bot；supervisor 空闲，可从菜单栏/Web 手动启动 |
| `--host` | 覆盖 `[webui].host`（如 `0.0.0.0` 允许远程） |
| `--port` | 覆盖 `[webui].port` |
| `-v` | supervisor 详细日志 |

## 3. 配置 `[webui]`

在 `config.toml` 末尾添加（bot 的 `AppConfig` 会忽略此未知段，无副作用）：

```toml
[webui]
host = "127.0.0.1"            # 127.0.0.1=仅本地；0.0.0.0=允许远程
port = 8721
log_ring_lines = 2000         # 内存环形缓冲行数（实时日志 / SSE）
auto_restart = true           # Bot 崩溃后自动重启
restart_min_interval_sec = 30 # 崩溃重启最小间隔（防风暴）
supervisor_enabled = true     # false 时 supervisor 空闲，需手动启停 Bot
```

## 4. 远程访问

Web UI 默认只监听 `127.0.0.1`。要远程访问：

```bash
./start_app.sh --host 0.0.0.0
# 或在 config.toml 中设 [webui].host = "0.0.0.0"
```

随后在同网络的另一台机器上访问 `http://<本机IP>:8721`。

> **安全提示**：当前版本**无鉴权**（按设计选择）。请确保仅在可信内网使用，
> 或通过 SSH 端口转发访问（默认 `127.0.0.1` 模式下，远程用
> `ssh -L 8721:127.0.0.1:8721 user@mac-host` 转发）。

## 5. 菜单栏 App 功能

| 菜单项 | 作用 |
|--------|------|
| 状态 / 运行 / 桥接 / 活动 | 实时只读信息（每 2 秒刷新） |
| 打开 Web 控制台 | 调系统浏览器打开 Web UI |
| 重启 Bot | 终止当前 Bot 子进程并立即重新拉起 |
| 停止 Bot | 停止 Bot 且**不自动重启**（`auto_restart` 暂时失效） |
| 启动 Bot | 在停止后重新拉起 Bot |
| 自动重启 | 勾选/取消崩溃自动重启 |
| 退出 | 优雅停止 Bot 并退出整个控制面板 |

图标标题会显示运行时长（如 `🟢 3h24m`）或停止状态（`🔴 已停`）。

## 6. Web UI 功能

三个标签页：

- **概览**：进程状态、运行时长、重启次数、桥接模式/状态、最近活动/心跳/回复时刻、
  PID、配置路径、日志路径。数据来自 `/api/status`（聚合 `runtime_state.json` +
  supervisor 内存状态）。
- **会话**：左侧会话列表（来自 `chat_history/_index.json`），右侧显示选中会话的
  最近 100 条消息（**只读**），支持过滤。数据来自 `/api/messages`。
- **日志**：实时日志流（SSE），支持自动滚动、暂停、按 tag 过滤
  （`cycle` / `event` / `warn` / `error` 等）、加载历史、清屏。不同 tag 用颜色区分。

### API 端点（全部 GET，只读）

| 路径 | 说明 |
|------|------|
| `GET /` | 单页 Web UI |
| `GET /api/status` | 进程 + runtime + 桥接聚合状态 |
| `GET /api/sessions` | 会话列表 |
| `GET /api/messages?session=&limit=` | 指定会话最近消息 |
| `GET /api/logs/recent?lines=&tag=` | 日志环形缓冲快照 |
| `GET /api/logs/stream` | SSE 实时日志流 |
| `GET /api/control` | 自动重启开关 + 运行状态 |

示例：
```bash
curl http://127.0.0.1:8721/api/status | jq
curl "http://127.0.0.1:8721/api/messages?session=real%E5%88%98%E6%99%93%E4%BA%AE&limit=20" | jq
```

## 7. 架构要点

```
┌─────────────────────────────────────────────────────────┐
│  app.main (主进程)                                       │
│                                                          │
│  主线程: rumps App.run()  ←─ NSApplication run loop      │
│                                                          │
│  daemon 线程:                                            │
│   ├─ BotSupervisor._supervise_loop  (监听崩溃/重启)      │
│   ├─ BotSupervisor._read_loop       (读 bot stdout)      │
│   └─ httpd.serve_forever           (Web + SSE)           │
│                                                          │
│  子进程: python -u run.py --config config.toml  (Bot)    │
└─────────────────────────────────────────────────────────┘
```

- **Supervisor 与 Bot 解耦**：Bot 是独立子进程，与菜单栏 run loop 互不干扰；
  Bot 崩溃不会拖垮面板，面板退出会优雅终止 Bot。
- **日志接管**：Supervisor 读取子进程 stdout 管道，去 ANSI 后写
  `logs/rpa_*.log`，并过滤 `[warn|error|skip-]` 行（带时间戳）进
  `logs/issues.log`。比旧版 `start_rpa.sh` 的 shell tee 管道更干净
  （旧版 issues.log 残留 ANSI 颜色码）。
- **状态只读**：Web 面板的所有数据都来自文件/内存的只读快照，绝不调用 bot 内部
  方法；会话消息直接复用 `wechat_rpa.agent_store.ChatHistoryStore` 的纯文件读取
  方法（不实例化 bot）。

## 8. 与 `start_rpa.sh` 的关系

| 场景 | 用哪个 |
|------|--------|
| **日常使用**（双击即跑，无终端残留） | 双击 `WeAuto.command` |
| 命令行启动（想留在终端前台看输出） | `./start_app.sh` |
| 纯命令行调试（想直接看到 stdout、用 Ctrl+C） | `./start_rpa.sh` |
| 定期重启守护（6h 强制重启） | `./start_rpa_watchdog.sh` |

两者共用同一份 `config.toml`、同一个 `.venv312`、同一份 `data/`，可随时切换。
`start_rpa.sh` 的行为完全未变。

## 9. 排障

- **菜单栏图标没出现**：确认在 macOS GUI 会话中运行（非纯 SSH）。
  SSH 下用 `--headless`，或通过 VNC/屏幕共享登录后运行。
- **端口被占用**（`Address already in use`）：`lsof -ti:8721 | xargs kill`，
  或换端口 `./start_app.sh --port 8722`。服务已启用 `SO_REUSEADDR`。
- **远程访问不通**：确认 `--host 0.0.0.0` 且 macOS 防火墙未拦 8721 端口
  （系统设置 → 网络 → 防火墙）。
- **Bot 反复崩溃重启**：菜单栏取消勾选"自动重启"，或设
  `[webui].auto_restart = false`，然后在 Web 日志页查看 `[warn]/[error]` 行定位。
- **Web 日志页空白**：Bot 必须在运行才会产生日志；`--no-bot` 模式下 ring buffer
  为空是正常的。点"加载历史"可回看已落盘的日志行。
