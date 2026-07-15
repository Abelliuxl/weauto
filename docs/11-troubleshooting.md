# 故障排查

## 1. 建议顺序

1. 保持 `dry_run = true`
2. 查看 `logs/issues.log`
3. 查看当前 `logs/rpa_*.log`
4. 打开 `log_verbose = true`
5. 用 `debug_detached_windows.py` 隔离窗口解析
6. 再检查 LLM、bridge 或工具 provider

先区分“没有检测到消息”和“检测到了但没有回复”。

## 2. App 无法启动

检查：

```bash
test -x .venv312/bin/python
plutil -p WeAuto.app/Contents/Info.plist
```

常见原因：

- 尚未运行 `./start_app.sh` 初始化环境
- App 被移出项目目录
- launcher 没有执行权限
- Python 依赖损坏
- macOS 阻止未签名的本地 App

可直接运行 `./start_app.sh` 获取终端错误。

Windows 先在 PowerShell 运行 `.\start_app.ps1` 查看错误。重点检查：

- `uv --version` 是否可用；未安装时执行 `winget install --id=astral-sh.uv -e`
- `.venv312\Scripts\python.exe` 是否存在
- PowerShell 执行策略是否允许脚本；可使用 `powershell -ExecutionPolicy Bypass -File .\start_app.ps1`
- `onnxruntime` 应由锁文件在 Windows 解析为 `<1.21`，这是当前 Windows 10 / 旧款 CPU 的兼容基线

## 3. 菜单栏 / 系统托盘不存在

- 确认当前是 macOS GUI 登录会话
- SSH 环境使用 `--headless`
- 检查 `rumps` 和 Cocoa 依赖
- 确认没有另一个 WeAuto 实例
- Windows 检查托盘的隐藏图标区域和 `pystray` 依赖；服务会话应使用 `--headless`

App 设置了 `LSUIElement`，没有普通 Dock 窗口是预期行为。

## 4. Web UI 无法访问

```bash
lsof -nP -iTCP:8721 -sTCP:LISTEN
curl http://127.0.0.1:8721/api/status
```

Windows 可使用 `Get-NetTCPConnection -LocalPort 8721` 和
`Invoke-RestMethod http://127.0.0.1:8721/api/status`。

若端口占用，使用：

```bash
./start_app.sh --port 8722
```

远程访问时确认：

- `host = "0.0.0.0"`
- macOS / Windows 防火墙允许端口
- 客户端使用运行 WeAuto 机器的局域网 IP
- 网络属于可信环境

## 5. 检测不到窗口

检查：

- 会话是否已拖成独立窗口
- `app_name` 是否匹配当前微信进程
- `detached_window_title_filter` 是否过滤了目标
- macOS 屏幕录制权限；Windows 上 WeAuto 与微信应使用相同权限级别
- 窗口是否最小化或不可见

运行：

```bash
python debug_detached_windows.py --config config.toml
```

如果 `windows.json` 为空，问题在窗口枚举；如果有窗口但消息为空，问题在截图或解析。

## 6. OCR 错误

尝试：

```toml
[ocr]
backend = "rapidocr"
enhance = true
target_short_side = 1200
```

并启用：

```toml
detached_debug_save = true
```

检查窗口缩放、字体大小、浅色 / 深色主题和聊天气泡区域。OCR 后端 A/B 功能可用于比较。

## 7. Vision 不工作

必须同时满足：

- `detached_vision_parse_enabled = true`
- `[vision].enabled = true`
- endpoint、key 和 model 有效
- 模型支持图片输入

查看：

- `[warn] detached vision parse failed`
- `response_format_json_object` 是否被 provider 支持
- `max_tokens` 是否足够
- thinking 控制是否被 provider 接受

临时设置 `vision.fail_open = true` 可以回退 OCR。

## 8. 检测到消息但不回复

查找日志：

- `[skip-muted]`
- `[skip-normal-interval]`
- decision skip
- group mention policy
- `[NO_REPLY]`
- `dry_run`

检查：

- 会话是否 muted
- 群聊是否要求 mention
- 是否命中 `ignore_title_keywords`
- 是否处于 action cooldown
- 是否是自己的最新消息
- `llm_decision` 是否 fail-closed

## 9. 重复回复或漏消息

检查：

- 是否同时运行多个实例
- `runtime_state.json` 是否被删除或损坏
- 微信窗口是否频繁改变标题
- Vision 与 OCR 是否产生不稳定消息结构
- visible state 是否反复丢失尾部锚点

不要在 Bot 运行时删除 `data/runtime_state.json` 或会话索引。

## 10. 发送失败

常见原因：

- 辅助功能权限缺失
- 目标窗口焦点错误
- 输入法或剪贴板被其他程序改变
- 微信弹窗覆盖输入区
- 文件路径不存在
- `send_after_paste_delay_sec` 太短

先在测试会话手工验证文本，再验证图片和 @。

## 11. LLM 失败

检查：

- `base_url` 是否包含正确 API 前缀
- 环境变量是否已由 `.env.weauto` 加载
- 模型名是否正确
- `api_format`
- timeout
- provider 是否支持 response format 和 reasoning 参数

打开 `debug_log_payload` / `debug_log_response` 时注意日志可能包含敏感上下文。

## 12. Agent tool 不出现

工具使用 capability gate。检查：

- Agent actions 是否启用
- 对应 provider 的 enabled、key、endpoint 和 model
- QWeather 私钥文件
- Playwright Chromium
- image output 目录权限
- 本地 skill builder 是否存在

工具 executor 存在并不代表 planner 一定能看到它。

## 13. Bridge 失败

### HTTP bridge

```bash
curl -v http://127.0.0.1:8766/
```

检查 URL、Bearer token、JSON 响应和 timeout。

### OpenClaw 短桥接

检查 `openclaw` 命令、Gateway URL、token、agent ID 和代理环境。

### Long bridge

检查：

- 插件已安装并重启 Gateway
- `/weauto/channel` 路径
- 两端 token
- account ID
- WebSocket 网络可达
- attachment size

菜单栏和 Web 概览会显示 bridge 状态，详细错误在日志中。

## 14. 内存增长或频繁重启

- 使用 `detached_window_capture_backend = "screencapture"`
- 检查 `memory_watchdog_max_rss_mb`
- 检查 supervisor 自动重启与 watchdog 是否同时启用
- 查看退出码和重启次数
- 减少 `detached_debug_save`
- 检查日志、截图和图片目录增长

Quartz 捕获只应在确认性能收益且能监控内存时使用。

## 15. 数据问题

操作前停止 Bot 并备份 `data/`。

- 重复聊天行：`scripts/dedup_chat_history.py`
- 人物发送者错误：`scripts/fix_corrupted_senders.py`
- 记忆追加区过长：`scripts/compact_memory_notes.py`
- 旧格式迁移：先检查脚本源码和备份，不要直接套用当前数据

`scripts/clean_tool_history.py` 面向旧存储格式，不用于当前日期文件。
