# 校准流程（上线前必做）

这份流程用于把点击坐标、OCR 区域、Vision 截图区域校准到你当前微信窗口布局。

如果不校准直接运行，最常见问题是：

- 点错会话
- 标题校验失败（`[skip-focus]`）
- 误判未读/漏判未读
- Vision 截图带入标题栏/输入框导致解析噪声

## 0. 前置准备

1. 复制配置：

```bash
cp config.toml.example config.toml
```

2. 打开微信桌面端，确保左侧会话列表 + 右侧聊天区都可见。
3. 固定窗口尺寸和位置（后续尽量不要改）。
4. 确认权限已授予：
- Accessibility（辅助功能）
- Screen Recording（屏幕录制）

推荐先把主程序保持在：`dry_run = true`。

## 1. 校准顺序（建议严格按顺序）

1. 行框（`manual_row_boxes`）
2. 右侧标题区域（群聊 + 私聊）
3. 聊天记录截图区域（Vision）
4. 行内标题 OCR 区域
5. 行内 preview OCR 区域
6. 未读圆形区域
7. recover-auto 点击点与滚动幅度（如果你要用 recover-auto）

这个顺序的原因：后面的校准依赖前面的行定位和标题校验稳定性。

## 2. 详细步骤

### 2.1 会话行框（最重要）

命令：

```bash
./carlibrate_rows.sh config.toml
```

你在 UI 里会做：

- 拖动框内部：移动
- 拖右下角小方块：缩放
- `+1行` / `-1行`：增减行框
- `保存并启用`

保存后会改动：

- 文件：`data/manual_row_boxes.json`
- 配置：`use_manual_row_boxes = true`、`manual_row_boxes_path = "..."`

验收标准：

- `idx=0,1,2...` 与微信列表从上到下顺序一致
- 每个框完整覆盖单行，不压到相邻行
- `./debug_click.sh config.toml --dry-run --cycles 1` 显示点击点在各自行框内部

常见坑：

- 行框互相重叠：后续会出现错点/跳行
- 窗口尺寸变了：行框相对比例虽可复用，但视觉效果会漂

### 2.2 右侧标题区域（focus 校验）

命令：

```bash
./carlibrate_title_group.sh config.toml
./carlibrate_title_private.sh config.toml
```

用途：点击会话后，主流程 OCR 这个区域判断“是否切到了目标会话”。

你在 UI 里会做：

- 框住右侧顶部标题文字（只要标题，不要覆盖太大空白）
- `保存并启用`

保存后会改动：

- `[chat_title_region_group]`
- `[chat_title_region_private]`
- `chat_title_region_group_enabled = true`
- `chat_title_region_private_enabled = true`
- `focus_verify_enabled = true`

验收标准：

- 运行主程序时，`[focus-ok]` 比例明显高于 `[focus-retry]/[focus-fail]`
- 极少出现 `[skip-focus]`

常见坑：

- 框太高把背景也包含进去，OCR 稳定性会差
- 只校准了群聊没校准私聊（或反过来），另一类会话会频繁 focus 失败

### 2.3 聊天记录截图区域（Vision 输入）

命令：

```bash
./carlibrate_chat_context.sh config.toml
```

用途：右侧聊天消息流会按这个区域截图，交给 Vision 解析成 `wechat_context_v2`。

你在 UI 里会做：

- 框住消息流主体
- 避免包含顶部标题栏、底部输入框、右侧过多空白
- `保存并启用`

保存后会改动：

- `[chat_context_region]`

验收标准：

- `log_verbose=true` 时 `[vision]` 输出 `last/recent` 与实际对话一致
- 无明显“把输入框内容当聊天消息”的情况

常见坑：

- 框包含输入框：Vision 会读到草稿/输入框噪声
- 框太窄：气泡文本被截断，导致 `last_user_message` 不完整

### 2.4 行内标题 OCR 区域（单行局部）

命令：

```bash
./carlibrate_row_title.sh config.toml
```

用途：提升左侧每行标题 OCR 稳定性（对群名截断、错读很有帮助）。

保存后会改动：

- `[row_title_region]`
- `row_title_region_enabled = true`

坐标规则（关键）：

- 这是“单行局部坐标”
- 锚点是左下角（`x` 从左往右，`y` 从下往上）

验收标准：

- 日志里 `title` 文本错读明显减少
- 群名前缀（如 `群-`）更稳定

### 2.5 行内 preview OCR 区域（单行局部）

命令：

```bash
./carlibrate_preview.sh config.toml
```

用途：提升 `preview` 读取稳定性，减少误触发与漏触发。

保存后会改动：

- `[preview_text_region]`
- `preview_region_enabled = true`

坐标规则同上（左下锚点）。

验收标准：

- `preview` 与微信列表显示内容接近
- `skip-self-preview` 的触发更“合理”而不是乱跳

### 2.6 未读红点圆形区域（推荐开启）

命令：

```bash
./carlibrate_unread.sh config.toml
```

用途：优先按圆形区域红色占比判断未读，更稳定。

保存后会改动：

- `[unread_badge_circle]`
- `unread_badge_circle.enabled = true`

你在 UI 里会做：

- 拖动圆内：移动圆心
- 拖右侧小方块：调整半径
- `保存并启用`

验收标准：

```bash
./debug_unread.sh config.toml --dry-run --cycles 1
```

- 输出中 `red_ratio` 与你肉眼看到的红点一致
- 有红点时 `hit=True`，无红点时大多数为 `hit=False`

### 2.7 recover-auto 校准（按需）

如果你会用 `recoverauto/recover-auto`，建议校准：

```bash
./carlibrate_recover_auto.sh config.toml
```

这个工具会做两件事：

1. 校准 `recover_auto_click_point`（聊天区安全点击点）
2. 交互式调 `recover_auto_scroll_amount`（每轮上滑步数）

保存后会改动：

- `[recover_auto_click_point]`
- `recover_auto_scroll_amount`
- （可按需要手动微调）`recover_auto_scroll_pause_sec`

建议：

- 点击点选在聊天区稳定空白位置，避免点到链接/头像
- 滚动幅度调到“每次都明显上滑，但不容易越界错位”

### 2.8 输入框点击点（必要时手动修）

当前没有单独 UI 脚本校准 `input_point`，若发现发送时点错输入框，可手动微调：

- `[input_point].x`
- `[input_point].y`

用法：

1. 先 `dry_run=false` 小范围实测
2. 每次只改 0.01~0.03
3. 观察 `[sent]` 前后是否稳定

## 3. 校准后回归检查（建议完整跑一遍）

1. 行点击：

```bash
./debug_click.sh config.toml --dry-run --cycles 1
```

2. preview 点击：

```bash
./debug_preview.sh config.toml --dry-run --cycles 1
```

3. unread 圆形：

```bash
./debug_unread.sh config.toml --dry-run --cycles 1
```

4. 主程序 dry-run 观察 10~20 分钟：

```bash
./start_rpa.sh config.toml
```

重点看日志：

- `[skip-focus]` 是否很少
- `[skip-self-preview]` 是否符合预期
- 触发顺序是否合理（mention/new_message）

## 4. 上线最小流程

1. 校准完成并通过回归检查
2. 保持 `dry_run=true` 跑一段时间
3. 改 `dry_run=false`
4. 先在低风险会话试发

## 5. 复校建议（什么时候需要重新校准）

出现以下任意情况，建议从“2.1 行框”开始至少复校到 2.3：

- 微信窗口尺寸/位置明显变化
- 系统缩放比例变化
- 微信版本更新后 UI 布局变化
- 日志中 `skip-focus`、错点、未读误判突然增多

快速复校优先级：

1. 行框
2. 标题区域
3. 聊天上下文区域
4. 其他局部区域
