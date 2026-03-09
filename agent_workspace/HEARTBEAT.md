# HEARTBEAT.md

# Keep this file empty (or with only comments) to skip heartbeat API calls.

# Add tasks below when you want the agent to check something periodically.

每次心跳优先执行以下维护任务（按顺序）：
- 使用工具 `maintain_memory`，参数 `{"days": 7}`，整理最近 7 天 daily memory 到 `MEMORY.md` 的托管区块。
- 使用工具 `refine_persona_files`，整理并去重 `SOUL.md`、`IDENTITY.md`、`USER.md`、`TOOLS.md` 的托管区块。

执行约束：
- 若没有新增有效内容，可跳过对应动作。
- 若本轮没有需要更新的内容，返回空 actions。
