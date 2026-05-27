from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import WeChatGuiRpaBot
    from .detector import ChatRowState


class HeartbeatRunner:
    def __init__(self, bot: "WeChatGuiRpaBot") -> None:
        self.bot = bot

    def maybe_run(self, now: float, rows: list["ChatRowState"]) -> bool:
        bot = self.bot
        if not bot.cfg.heartbeat_enabled:
            return False
        if (now - bot._last_heartbeat_at) < float(bot.cfg.heartbeat_interval_sec):
            return False
        if (now - bot._last_activity_at) < float(bot.cfg.heartbeat_min_idle_sec):
            return False
        bot._last_heartbeat_at = now
        try:
            return bot._run_heartbeat(now, rows)
        except Exception as exc:
            if bot.cfg.heartbeat_fail_open:
                print(f"[warn] heartbeat failed, fail-open: {exc}")
                return False
            raise
