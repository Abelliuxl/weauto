# 开发与测试

## 1. 开发环境

推荐：

```bash
UV_PROJECT_ENVIRONMENT=.venv312 uv sync --python python3.12
source .venv312/bin/activate
```

或直接运行 `./start_app.sh` / `./start_rpa.sh` 让脚本准备环境。

依赖定义：

- `pyproject.toml`
- `uv.lock`
- `requirements.txt` 作为 pip fallback

## 2. 运行测试

`pytest` 当前没有列入运行时依赖。首次运行测试前，需要单独安装到开发环境：

```bash
.venv312/bin/python -m pip install pytest
```

然后运行：

```bash
.venv312/bin/python -m pytest
```

运行单文件：

```bash
.venv312/bin/python -m pytest tests/test_visible_message_state.py
```

运行单测试：

```bash
.venv312/bin/python -m pytest \
  tests/test_long_bridge.py::test_proactive_message_is_dispatched
```

## 3. 当前测试覆盖

测试集中覆盖：

- detached 窗口截图后端
- OCR 消息块分类
- 可见消息增量和锚点丢失
- detached 批处理选择
- Vision 消息规范化
- HTTP bridge 回复提取
- long bridge 协议、附件和主动消息
- Agent native tool calls 和 JSON repair
- 网页抓取 fallback 和搜索并行聚合
- 聊天历史读取与总结
- 记忆追加去重和压缩
- 人物别名热重载
- skill 存储和选择
- heartbeat 预算
- runtime state 恢复
- 图片生成
- QWeather JWT 和预报 endpoint
- NVIDIA、MiMo、SiliconFlow thinking 参数

GUI 真实点击、微信版本兼容和 macOS 权限仍需要人工集成验证。

## 4. 修改配置系统

新增配置项通常需要同时更新：

1. `AppConfig` 或相关子 dataclass
2. `load_config()`
3. `config.toml.example`
4. 对应功能的 capability / status 日志
5. 本文档的配置页
6. 测试

`[webui]` 字段应修改 `app/config.py`，不要加入 Bot 的 `AppConfig`。

## 5. 新增 Agent tool

需要同步：

1. `llm.py` 的 tool schema 和提示
2. `bot._available_agent_tools()` 的 capability gate
3. `ActionProcessor.execute_agent_actions()` 的执行分支
4. observation 和错误文本
5. heartbeat 工具表（如果允许）
6. 单元测试

只实现 executor 而不加入 available tools，planner 不会主动使用；只加入 schema 而没有
executor，则会产生 unknown tool。

## 6. 新增 provider

provider 模块应提供：

- 配置结构
- 环境变量解析
- `is_available()` / status 文本
- 超时和错误归一化
- 输出格式化
- capability gate
- 测试

网络 provider 失败不能破坏主循环；是否回退应显式配置。

## 7. 修改消息解析

修改 OCR、Vision 或增量状态时重点验证：

- 消息顺序
- self / other
- sender 和 sender_raw
- 文本 / 图片类型
- 尾部锚点丢失
- 窗口滚动
- 重启恢复
- 同一轮多消息选择

相关测试：

- `test_visible_message_parser.py`
- `test_visible_message_state.py`
- `test_detached_vision_messages.py`
- `test_detached_message_selection.py`

## 8. 调试工具

```bash
python debug_detached_windows.py --debug
python debug_detached_windows.py --watch --title "会话"
```

输出包括原始截图、标注图、消息 JSON 和文本摘要，适合在修改解析算法前后比较。

## 9. 手工验证清单

代码测试通过后，在 `dry_run=true` 下检查：

1. 单聊文本
2. 群聊普通消息
3. 群聊 mention
4. 连续多消息
5. 自己发送后的回显
6. 图片消息
7. 窗口关闭和重新打开
8. supervisor 重启恢复
9. bridge 失败路径
10. Web UI 会话和日志

最后才验证真实发送。

## 10. 提交前

```bash
git status --short
git diff --check
.venv312/bin/python -m pytest
```

确认没有提交：

- `.env.weauto`
- `config.toml` 中的秘密
- `data/secrets/`
- 私人聊天记录
- 人物印象
- 无关日志和生成文件
