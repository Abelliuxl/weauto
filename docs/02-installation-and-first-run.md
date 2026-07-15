# 安装与首次运行

## 1. 环境要求

- macOS 12 或更高版本，或 Windows 10/11 64 位
- Python 3.12
- uv（Windows 可用 `winget install --id=astral-sh.uv -e`）
- 已登录的微信桌面版
- 可用的文本 LLM 服务
- macOS 需要辅助功能和屏幕录制权限
- Windows 需要已登录、未锁屏的交互式桌面会话

项目声明的 Python 版本范围是 `>=3.12,<3.13`。`run.py` 在误用 Python 3.13+ 时会尝试
重新执行 macOS 的 `.venv312/bin/python` 或 Windows 的 `.venv312\Scripts\python.exe`。

## 2. 创建本地配置

macOS：

```bash
cp config.toml.example config.toml
cp .env.example .env.weauto
```

Windows PowerShell：

```powershell
Copy-Item config.toml.example config.toml
Copy-Item .env.example .env.weauto
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

## 4. 平台权限与桌面会话

### macOS

在“系统设置 → 隐私与安全性”中授权：

- 辅助功能：用于激活窗口、点击、粘贴和发送
- 屏幕录制：用于窗口截图和 OCR / Vision

从终端启动时，授权对象通常是 Terminal、iTerm 或 Python。从 `.app` 启动时，系统可能
要求单独授权 WeAuto。

权限改变后应完全退出并重新启动相关进程。

### Windows

Windows 不需要单独的屏幕录制授权，但 GUI 自动化必须运行在交互式桌面中：

- 运行期间不要锁屏、注销或切换到无人值守的服务会话
- 微信和 WeAuto 使用相同权限级别；通常两者都不要“以管理员身份运行”
- 多显示器和系统缩放受支持，首次仍应以 `dry_run = true` 校验点击位置
- Windows 微信 4.x 的主进程名是 `Weixin.exe`，代码会同时识别旧版 `WeChat.exe`，并排除 `WeChatAppEx` 插件进程

## 5. 首次启动

macOS：

```bash
./start_app.sh
```

Windows：

```powershell
.\start_app.ps1
```

也可以双击 `start_app.cmd`。首次初始化成功后，日常可双击 `WeAuto.vbs` 隐藏终端窗口，
只保留系统托盘图标。

脚本会：

1. 创建或复用 `.venv312`
2. 优先使用 `uv sync`
3. 按当前平台安装依赖（pyobjc / rumps 仅 macOS，pystray 仅 Windows）
4. 安装 Playwright Chromium
5. 加载 `.env.weauto` 和 `.env`
6. 启动 supervisor、Web UI 和菜单栏 / 系统托盘 App

首次依赖安装可能需要网络。

## 6. 首次验证

保持：

```toml
dry_run = true
log_verbose = true
```

然后验证：

1. macOS 菜单栏或 Windows 系统托盘出现 WeAuto 状态
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

完成 `.venv312` 初始化后，macOS 双击仓库根目录的 `WeAuto.app`，Windows 双击
`WeAuto.vbs`。

`.app` 内的 launcher 通过相对路径寻找项目根目录，所以 App 本体必须留在仓库内。需要放到
桌面或“应用程序”时，应创建替身；也可以固定到 Dock。

Windows 的 `WeAuto.vbs` 同样通过相对路径寻找 `start_app.ps1`，因此也应留在仓库内；可为
它创建桌面快捷方式。

## 8. 最小安全基线

- 首次运行必须使用 `dry_run = true`
- 默认让 Web UI 绑定 `127.0.0.1`
- 只在可信内网使用 `0.0.0.0`
- `python_sandbox_restricted = true`
- 为群聊配置明确的触发规则
- 先用测试会话验证发送和 @ 行为
