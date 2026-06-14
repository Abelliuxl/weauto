# 安装与首次运行

## 1. 环境要求

- macOS 12 或更高版本
- Python 3.12
- 已登录的微信桌面版
- 可用的文本 LLM 服务
- 辅助功能和屏幕录制权限

项目声明的 Python 版本范围是 `>=3.12,<3.13`。`run.py` 在误用 Python 3.13+ 时会尝试
重新执行 `.venv312/bin/python`。

## 2. 创建本地配置

```bash
cp config.toml.example config.toml
cp .env.example .env.weauto
```

第二条是可选的。推荐把密钥放在 `.env.weauto`，把行为设置放在 `config.toml`。

至少需要配置：

```toml
dry_run = true

[llm]
enabled = true
base_url_env = "NVIDIA_BASE_URL"
api_key_env = "NVIDIA_API_KEY"
model = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
```

不要把真实密钥提交到 Git。

## 3. 准备微信窗口

推荐接收模式为：

```toml
receiver_mode = "detached_windows"
```

将需要处理的会话从微信主窗口拖出，使每个会话成为独立窗口。窗口标题用于识别会话，
因此不要让多个窗口使用无法区分的标题。

可用标题过滤限制处理范围：

```toml
detached_window_title_filter = ["测试群", "测试联系人"]
```

## 4. 授权 macOS 权限

在“系统设置 → 隐私与安全性”中授权：

- 辅助功能：用于激活窗口、点击、粘贴和发送
- 屏幕录制：用于窗口截图和 OCR / Vision

从终端启动时，授权对象通常是 Terminal、iTerm 或 Python。从 `.app` 启动时，系统可能
要求单独授权 WeAuto。

权限改变后应完全退出并重新启动相关进程。

## 5. 首次启动

```bash
./start_app.sh
```

脚本会：

1. 创建或复用 `.venv312`
2. 优先使用 `uv sync`
3. 没有 `uv` 时使用 `venv + pip`
4. 安装 Playwright Chromium
5. 加载 `.env.weauto` 和 `.env`
6. 启动 supervisor、Web UI 和菜单栏 App

首次依赖安装可能需要网络。

## 6. 首次验证

保持：

```toml
dry_run = true
log_verbose = true
```

然后验证：

1. 菜单栏出现 WeAuto 状态
2. <http://127.0.0.1:8721> 可以打开
3. 日志中能看到 detached window 数量
4. 测试窗口中的新消息产生 `[event]`
5. OCR 或 Vision 解析出的发送者和文本正确
6. 日志生成的回复符合预期但没有真正发送

确认后再设置：

```toml
dry_run = false
```

## 7. 日常启动

完成 `.venv312` 初始化后，双击仓库根目录的 `WeAuto.app`。

`.app` 内的 launcher 通过相对路径寻找项目根目录，所以 App 本体必须留在仓库内。需要放到
桌面或“应用程序”时，应创建替身；也可以固定到 Dock。

## 8. 最小安全基线

- 首次运行必须使用 `dry_run = true`
- 默认让 Web UI 绑定 `127.0.0.1`
- 只在可信内网使用 `0.0.0.0`
- `python_sandbox_restricted = true`
- 为群聊配置明确的触发规则
- 先用测试会话验证发送和 @ 行为
