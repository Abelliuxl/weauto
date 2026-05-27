# 运行与日常操作

## 1. 启动

```bash
# 一键启动（推荐）
./start_rpa.sh config.toml

# 直接运行
python run.py --config config.toml

# 定期重启守护（6h 默认间隔）
./start_rpa_watchdog.sh config.toml
```

`start_rpa.sh` 自动处理：
- 创建/复用 `.venv312` 虚拟环境
- 安装 `requirements.txt` 依赖
- 从 `config.toml.example` 复制初始配置（不存在时）
- 加载 `.env.weauto` / `.env` 环境变量
- 日志输出到 `logs/rpa_YYYYmmdd_HHMMSS.log`
- 自动清理旧日志（`LOG_KEEP_MAX_FILES` / `LOG_KEEP_MAX_TOTAL_MB`）

## 2. 前置条件

- macOS
- 微信桌面版已登录，聊天窗口处于 **detach 状态**（每个会话独立窗口）
- 终端/ Python 已授权：
  - Accessibility（辅助功能）
  - Screen Recording（屏幕录制）
- `.venv312` 已安装依赖

## 3. 运行模式

```bash
# 普通主循环
./start_rpa.sh config.toml

# 定期重启守护（推荐长时间跑）
./start_rpa_watchdog.sh config.toml
```

## 4. 主循环链路

1. 枚举所有 detach 微信窗口
2. 对每个窗口截图 + OCR 解析消息
3. `visible_message_state` 增量去重
4. 检测到新消息时触发事件处理
5. 可选 agent 工具规划循环
6. LLM 生成回复并发送
7. 空闲时可选 heartbeat 自驱维护

## 5. 心跳（Heartbeat）

触发条件：
- `heartbeat_enabled=true`
- 距离上次心跳超过 `heartbeat_interval_sec`
- 空闲超过 `heartbeat_min_idle_sec`

可用工具：`read_impression`、`write_impression`、`write_memory`

## 6. 运维

```bash
# 主程序
./start_rpa.sh config.toml

# 看日志
tail -f logs/rpa_*.log

# ComfyUI 图片生成桥接（可选）
./start_comfy_bridge.sh
```

调试详见：`docs/04-debug-and-troubleshooting.md`。
