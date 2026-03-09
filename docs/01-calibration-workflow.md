# 校准流程（先做这个）

这份流程用于把 RPA 点击与 OCR 区域对齐到你的微信窗口布局。未校准直接运行，误点和误判概率会明显升高。

## 0. 前置准备

1. 复制配置：

```bash
cp config.toml.example config.toml
```

2. 打开微信桌面端，保持会话列表和聊天窗口可见。
3. 建议固定窗口大小与位置（后续运行尽量不要频繁改变）。
4. 保证系统权限已授权：

- Accessibility（辅助功能）
- Screen Recording（屏幕录制）

## 1. 校准会话行框（最重要）

命令：

```bash
./carlibrate_rows.sh config.toml
```

作用：

- 对聊天列表每一行建立手工框（`idx=0,1,2...` 对应从上到下）
- 检测时优先使用手工框，而不是仅靠行高估算

界面操作：

- 拖动框内部：移动
- 拖右下角小方块：缩放
- `+1行` / `-1行`：增减行框
- `保存并启用`：写入并生效

保存结果：

- 写入 `data/manual_row_boxes.json`
- 自动更新 `config.toml`：
  - `use_manual_row_boxes = true`
  - `manual_row_boxes_path = "..."`

## 2. 校准右侧聊天标题区域（点击后校验）

命令：

```bash
./carlibrate_title_group.sh config.toml
./carlibrate_title_private.sh config.toml
```

作用：

- 主流程点击会话后，会 OCR 这个区域判断“是否已切到目标会话”
- 分别写入 `[chat_title_region_group]` / `[chat_title_region_private]`

界面操作：

- 框覆盖右侧聊天顶部标题文字
- `保存并启用` 后写回 `config.toml`

保存结果：

- 更新 `[chat_title_region_group]`、`[chat_title_region_private]`
- 打开 `chat_title_region_group_enabled = true`、`chat_title_region_private_enabled = true`
- 将 `focus_verify_enabled = true`

## 3. 校准聊天记录截图区域（Vision 回复前必做）

命令：

```bash
./carlibrate_chat_context.sh config.toml
```

作用：

- 校准 `[chat_context_region]`
- 右侧聊天消息流会按这个区域截图，交给 Vision 输出标准化 `context + environment` JSON
- 框内应只覆盖消息流，不要包含顶部标题栏和底部输入框

界面操作：

- 拖动框内部：移动
- 拖右下角小方块：缩放
- `保存并启用`：写回 `config.toml`

保存结果：

- 更新 `[chat_context_region]`

## 4. 校准行内标题 OCR 区域（可提升标题识别稳定性）

命令：

```bash
./carlibrate_row_title.sh config.toml
```

作用：

- 校准 `[row_title_region]`
- 打开 `row_title_region_enabled = true`
- 用于修正聊天列表标题被截断或错读问题

坐标规则：

- 这是“单行局部坐标”，锚点是**左下角**（`x` 从左向右，`y` 从下向上）

## 5. 校准行内预览 OCR 区域（减少 preview 误判）

命令：

```bash
./carlibrate_preview.sh config.toml
```

作用：

- 校准 `[preview_text_region]`
- 打开 `preview_region_enabled = true`
- 新消息判定可更稳定地读取每行 preview

坐标规则：

- 同样是单行局部左下锚点

## 6. 校准未读红点圆形区域（推荐）

命令：

```bash
./carlibrate_unread.sh config.toml
```

作用：

- 校准 `[unread_badge_circle]`（圆心和半径）
- 打开 `unread_badge_circle.enabled = true`
- 检测时优先用圆形区域红色占比判断未读

界面操作：

- 拖动圆内：移动圆心
- 拖动右侧小方块：调整半径

坐标规则：

- 单行局部左下锚点

## 7. 用调试脚本验证点击是否准确

建议先都用 `--dry-run`。

### 6.1 验证行点击

```bash
./debug_click.sh config.toml --dry-run --cycles 1
```

### 6.2 验证 preview 区域点击点

```bash
./debug_preview.sh config.toml --dry-run --cycles 1
```

### 6.3 验证未读圆形区域

```bash
./debug_unread.sh config.toml --dry-run --cycles 1
```

`debug_unread` 会额外打印：

- `red_ratio`：圆内红色占比
- `threshold=0.333`：当前阈值
- `hit=True/False`：是否命中未读判定

## 8. 校准完成后的最小运行流程

1. 确保 `dry_run = true`
2. 启动：

```bash
./start_rpa.sh config.toml
```

3. 观察日志确认事件与目标行正确
4. 改为 `dry_run = false` 后再进行真实发送

## 校准验收清单

- 行框覆盖准确，索引顺序正确
- 点击后右侧标题能稳定匹配
- 聊天记录截图区域只包含消息流，Vision 输出稳定
- preview 与 row title OCR 文本基本可读
- 未读红点区域 `red_ratio` 与视觉感知一致
- `debug_* --dry-run` 坐标无明显偏移
