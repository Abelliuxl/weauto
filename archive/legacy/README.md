# Legacy WeChat List Receiver

This directory keeps copies of the old whole-app chat-list based receiver and
sender code.

- `detector_list_receiver.py` is the previous left-list detector that used
  unread badges and preview changes.
- `sender_gui_legacy.py` is the sender implementation before detached-window
  sender activation support.

The active code path is moving toward detached chat windows captured by
macOS window id, with UI-block parsing and OCR over visible message bubbles.

## 2026-05-27 Workspace Cleanup

- `agent_workspace_legacy_skills_20260527/skills/` is the old
  `agent_workspace/skills` tree. Active skills now live in `data/skills`.
- `workspace_backups_20260527/` contains old `agent_workspace.backup-*`
  directories from earlier workspace layouts.
- `workspace_memory_20260527/` contains the old `agent_workspace/memory`
  runtime records and `data/workspace_memory.sqlite3` index. This was archived
  because it contained references to the retired `agent_workspace/skills`
  layout and could pollute new planner context.
