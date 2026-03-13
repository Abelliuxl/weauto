# WeAuto：微信 macOS GUI RPA

![WeAuto Icon](docs/assets/weauto-icon.png)

WeAuto 是一个仅基于 GUI 的微信自动化项目（不 Hook、不注入、不读微信数据库）。

当前实现核心能力：

- 左侧会话列表截图 + OCR（`rapidocr/paddleocr/cnocr`）
- 触发检测：`mention` / `new_message` / `preview 变化`
- 右侧聊天区截图交给 Vision，解析为标准化 `context + environment` JSON
- 文本 LLM 负责是否回复（decision）+ 生成回复（reply）+ 会话摘要（summary）
- `agent_workspace/` 工作区记忆体系（长期记忆、每日记忆、会话记忆）
- 可选工具规划循环（memory/web_search/mute 等）
- 可选 heartbeat 空闲自驱任务（含 `HEARTBEAT.md` 直接指令解析）

## 快速开始（推荐顺序）

1. 复制配置：`cp config.toml.example config.toml`
2. 打开微信桌面端并固定窗口布局
3. 先完成校准：见 [docs/01-calibration-workflow.md](docs/01-calibration-workflow.md)
4. 先用 `dry_run=true` 跑一段时间观察日志
5. 稳定后再切 `dry_run=false`

## 启动方式

推荐：

```bash
./start_rpa.sh config.toml
```

`start_rpa.sh` 会自动：

- 创建/使用 `.venv312`
- 安装 `requirements.txt`
- 自动创建 `config.toml`（不存在时）
- 自动加载 `.env.weauto` 和 `.env`（若存在）
- 启动 `python -u run.py --config ...`
- 生成 `logs/rpa_YYYYmmdd_HHMMSS.log`
- 按文件数/总大小自动清理旧日志（`LOG_KEEP_MAX_FILES`、`LOG_KEEP_MAX_TOTAL_MB`）

也可直接运行：

```bash
python run.py --config config.toml
```

## 运行模式

普通主循环：

```bash
./start_rpa.sh config.toml
```

recover（手动翻页）：

```bash
./start_rpa.sh config.toml recover
```

recover-auto（自动安全点点击 + 持续上滑直到到顶）：

```bash
./start_rpa.sh config.toml recoverauto
# 或
./start_rpa.sh config.toml recover-auto
```

## 常用脚本

校准：

- `./carlibrate_rows.sh config.toml`
- `./carlibrate_title_group.sh config.toml`
- `./carlibrate_title_private.sh config.toml`
- `./carlibrate_chat_context.sh config.toml`
- `./carlibrate_row_title.sh config.toml`
- `./carlibrate_preview.sh config.toml`
- `./carlibrate_unread.sh config.toml`
- `./carlibrate_recover_auto.sh config.toml`

调试：

- `./debug_click.sh config.toml --dry-run`
- `./debug_preview.sh config.toml --dry-run`
- `./debug_unread.sh config.toml --dry-run`
- `./debug_planner.sh config.toml`
- `./debug_rerank.sh config.toml`
- `./debug_memory_sqlite.sh config.toml`
- `./debug_heartbeat.sh config.toml --show-tasks`

维护：

- `./clear_session_data.sh config.toml "会话key" --dry-run`

## 前置条件

- macOS
- 微信桌面版已登录且窗口可见
- 终端/Python 已授予：
  - Accessibility（辅助功能）
  - Screen Recording（屏幕录制）

## 配置入口

- 模板：`config.toml.example`
- 实际加载逻辑：`wechat_rpa/config.py`
- 详细配置说明：`docs/02-configuration-reference.md`

## 文档导航

- [文档总览](docs/README.md)
- [校准流程](docs/01-calibration-workflow.md)
- [配置参考](docs/02-configuration-reference.md)
- [运行与运维](docs/03-run-and-operations.md)
- [调试与排障](docs/04-debug-and-troubleshooting.md)
- [架构与代码地图](docs/05-architecture.md)
