# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

### iMessage 发送

雪宝的手机号: +8618388408505

使用 osascript 调用 Messages.app:

```bash
/usr/bin/osascript -e "tell application \"Messages\" to send \"消息内容\" to participant \"+8618388408505\""
```

注意：需要保持 Messages.app 可以正常响应，可能会有超时问题。

---

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

<!-- HEARTBEAT_TOOLS_START -->
iMessage发送：雪宝手机号+8618388408505，命令格式：`/usr/bin/osascript -e "tell application \"Messages\" to send \"消息内容\" to participant \"+8618388408505\""`，需保持Messages.app正常响应，可能超时。
联网配置：Tavily API Key: `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`，代理地址`192.168.5.100:7890`，配置在`~/.hermes/config.yaml`。
图片生成规范：
- 先理解用户需求重构详细prompt，不得直接复制原句。
- 固定添加画质提升词：masterpiece, best quality, ultra detailed, 8k, sharp focus, intricate details, cinematic lighting, volumetric lighting, global illumination, HDR, professional photography。
- 除非用户指定，生成总数不超过4张。
- 遵从雪宝妈妈意愿，拒绝绘制性感男性内容。
- 适配刘晓亮4K（3840×2160 16:9）二次元风景壁纸需求。
<!-- HEARTBEAT_TOOLS_END -->
