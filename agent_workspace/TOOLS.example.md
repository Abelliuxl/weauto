# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics - the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

### iMessage 发送（示例）

示例联系人手机号: `+8613XXXXXXXXX`

使用 osascript 调用 Messages.app:

```bash
/usr/bin/osascript -e "tell application \"Messages\" to send \"消息内容\" to participant \"+8613XXXXXXXXX\""
```

注意：需要保持 Messages.app 可以正常响应，可能会有超时问题。

---

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

<!-- HEARTBEAT_TOOLS_START -->
Skills define _how_ tools work. This file is for _your_ specifics - the stuff that's unique to your setup。iMessage 发送:示例联系人手机号: +8613XXXXXXXXX 使用 osascript 调用 Messages.app:/usr/bin/osascript -e "tell application \"Messages\" to send \"消息内容\" to participant \"+8613XXXXXXXXX\"" 注意：需要保持 Messages.app 可以正常响应，可能会有超时问题。
<!-- HEARTBEAT_TOOLS_END -->
