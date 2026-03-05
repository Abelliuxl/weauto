# WeAuto：微信 macOS GUI RPA

WeAuto 是一个仅基于 GUI 的微信自动化项目，核心能力是：

- 截图 + OCR 识别聊天列表与聊天区
- 基于未读/预览变化/@ 触发自动处理
- 可选接入 LLM 进行分流决策与回复生成
- 鼠标键盘自动化点击并发送消息

项目不使用 Hook、不注入进程、不读取微信数据库。

## 先做什么（建议顺序）

1. 复制配置文件：`cp config.toml.example config.toml`
2. 打开微信桌面端并固定窗口布局
3. 先完成校准（非常重要）：见 [校准流程文档](docs/01-calibration-workflow.md)
4. 校准后先用 `dry_run = true` 观察日志
5. 稳定后再改成 `dry_run = false` 开启真实发送

## 文档导航

- [文档总览](docs/README.md)
- [校准流程（先看）](docs/01-calibration-workflow.md)
- [配置文件完整参考](docs/02-configuration-reference.md)
- [运行与日常操作](docs/03-run-and-operations.md)
- [调试与故障排查](docs/04-debug-and-troubleshooting.md)
- [架构与代码地图](docs/05-architecture.md)

## 快速启动

```bash
./start_rpa.sh config.toml
```

`start_rpa.sh` 会自动：

- 创建并使用 `.venv312`
- 安装 `requirements.txt`
- 自动创建 `config.toml`（若不存在）
- 启动 `python run.py --config ...`
- 输出日志到 `logs/rpa_*.log`

也可直接启动：

```bash
python run.py --config config.toml
```

## 常用脚本速查

- 启动主程序：`./start_rpa.sh config.toml`
- 行框校准：`./carlibrate_rows.sh config.toml`
- 右侧标题栏校准：`./carlibrate_title.sh config.toml`
- 行标题区域校准：`./carlibrate_row_title.sh config.toml`
- 行预览区域校准：`./carlibrate_preview.sh config.toml`
- 未读红点圆形区域校准：`./carlibrate_unread.sh config.toml`
- 调试点击行：`./debug_click.sh config.toml --dry-run`
- 调试点击预览区域：`./debug_preview.sh config.toml --dry-run`
- 调试未读红点位置：`./debug_unread.sh config.toml --dry-run`

注意：脚本名采用仓库当前命名（`carlibrate_*`）。

## 运行前置条件

- macOS（依赖 Quartz + AppleScript）
- 微信桌面版已登录且窗口可见
- Python/终端已授予权限：
  - Accessibility（辅助功能）
  - Screen Recording（屏幕录制）

## 当前仓库结构（核心）

- `run.py`：入口
- `start_rpa.sh`：一键启动
- `config.toml.example`：配置模板
- `wechat_rpa/config.py`：配置加载与默认值
- `wechat_rpa/window.py`：窗口定位与截图
- `wechat_rpa/ocr.py`：OCR 引擎封装
- `wechat_rpa/detector.py`：聊天行检测与触发信息提取
- `wechat_rpa/llm.py`：LLM/Vision 调用
- `wechat_rpa/bot.py`：主循环、事件判定、回复执行

