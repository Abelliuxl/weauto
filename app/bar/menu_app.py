"""macOS menu-bar app (rumps) for the control panel.

Rumps wraps an ``NSApplication``; its ``run()`` must be on the main thread and
blocks forever (it owns the CFRunLoop). The supervisor and web server run in
daemon threads, so we start them first and then hand control to rumps.

The icon color is encoded in the template image's tint via the status item
title: we use a small colored emoji plus a short status string, which avoids
the need to ship image assets. A real template icon could be added later.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from typing import Any

import rumps

from ..config import WebUIConfig
from ..supervisor import BotSupervisor


class ControlBarApp(rumps.App):
    def __init__(self, supervisor: BotSupervisor, cfg: WebUIConfig) -> None:
        super().__init__(
            name="WeAuto",
            title="…",
            quit_button=None,  # we provide our own Quit for graceful shutdown
        )
        self.sup = supervisor
        self.cfg = cfg
        self._web_url = cfg.web_url()
        # Build menu items.
        self.item_status = rumps.MenuItem("状态: …", callback=None)
        self.item_status.enabled = False
        self.item_uptime = rumps.MenuItem("运行: …", callback=None)
        self.item_uptime.enabled = False
        self.item_bridge = rumps.MenuItem("桥接: …", callback=None)
        self.item_bridge.enabled = False
        self.item_activity = rumps.MenuItem("最近活动: …", callback=None)
        self.item_activity.enabled = False

        self.item_open = rumps.MenuItem("打开 Web 控制台", callback=self.on_open)
        self.item_restart = rumps.MenuItem("重启 Bot", callback=self.on_restart)
        self.item_stop = rumps.MenuItem("停止 Bot", callback=self.on_stop)
        self.item_start = rumps.MenuItem("启动 Bot", callback=self.on_start)
        self.item_auto = rumps.MenuItem(
            "自动重启", callback=self.on_toggle_auto
        )
        self.item_about = rumps.MenuItem("关于 WeAuto 控制面板", callback=self.on_about)
        self.item_quit = rumps.MenuItem("退出", callback=self.on_quit)

        self.menu = [
            self.item_status,
            self.item_uptime,
            self.item_bridge,
            self.item_activity,
            None,  # separator
            self.item_open,
            None,
            self.item_restart,
            self.item_stop,
            self.item_start,
            self.item_auto,
            None,
            self.item_about,
            self.item_quit,
        ]
        # First refresh right away, then on a timer.
        self.refresh()
        self._timer = rumps.Timer(self._tick, 2)
        self._timer.start()
        # Hook supervisor changes so we refresh promptly after restart/stop.
        self.sup.on_change = self._on_change

    # ------------------------------------------------------------------ #
    # Periodic refresh
    # ------------------------------------------------------------------ #
    def _tick(self, _sender: Any) -> None:
        self.refresh()

    def _on_change(self) -> None:
        # Called from supervisor threads; defer UI updates to the main thread.
        try:
            rumps.Timer(lambda _s: self.refresh(), 0.0).start()  # type: ignore[call-arg]
        except Exception:
            # Fallback: just refresh next tick (<=2s).
            pass

    def refresh(self) -> None:
        st = self.sup.status()
        now = time.time()
        # Title (icon + short text). rumps title is plain text; emoji gives the
        # color cue since we don't ship a template image.
        if st.running:
            dot = "🟢"
            title = f"{dot} {self._fmt_dur(st.uptime_sec)}"
        else:
            dot = "🔴"
            title = f"{dot} 已停"
        self.title = title

        self.item_status.title = (
            f"状态: {'运行中' if st.running else '已停止'}"
            + (f" (退出码 {st.last_exit_code})" if not st.running and st.last_exit_code is not None else "")
        )
        self.item_uptime.title = f"运行: {self._fmt_dur(st.uptime_sec)}  ·  重启 {st.restart_count} 次"
        self.item_bridge.title = f"桥接: {st.bridge_mode} / {st.bridge_state}"
        act = self._fmt_ago(st.last_activity_at, now)
        hb = self._fmt_ago(st.last_heartbeat_at, now)
        self.item_activity.title = f"活动: {act}  ·  心跳: {hb}"

        # Toggle states
        self.item_auto.state = st.auto_restart
        self.item_start.enabled = not st.running
        self.item_stop.enabled = st.running
        self.item_restart.enabled = True

    # ------------------------------------------------------------------ #
    # Menu callbacks
    # ------------------------------------------------------------------ #
    def on_open(self, _sender: Any) -> None:
        try:
            webbrowser.open(self._web_url)
        except Exception:
            rumps.notification("WeAuto", "无法打开浏览器", self._web_url)

    def on_restart(self, _sender: Any) -> None:
        rumps.notification("WeAuto", "重启 Bot", "正在重启子进程…")
        threading.Thread(target=self.sup.restart_bot, daemon=True).start()

    def on_stop(self, _sender: Any) -> None:
        rumps.notification("WeAuto", "停止 Bot", "已请求停止（不会自动重启）")
        threading.Thread(target=self.sup.stop_bot, daemon=True).start()

    def on_start(self, _sender: Any) -> None:
        rumps.notification("WeAuto", "启动 Bot", "正在拉起子进程…")
        threading.Thread(target=self.sup.start_bot, daemon=True).start()

    def on_toggle_auto(self, sender: rumps.MenuItem) -> None:
        new_state = not sender.state
        sender.state = new_state
        self.sup.set_auto_restart(new_state)
        rumps.notification(
            "WeAuto", "自动重启", "已开启" if new_state else "已关闭"
        )

    def on_about(self, _sender: Any) -> None:
        rumps.alert(
            title="WeAuto 控制面板",
            message=(
                "微信 macOS GUI RPA 机器人的菜单栏守护面板。\n\n"
                f"Web 控制台: {self._web_url}\n"
                f"配置: {self.cfg.config_path.name}\n"
                "Bot 核心未做任何改动。"
            ),
        )

    def on_quit(self, _sender: Any) -> None:
        try:
            rumps.notification("WeAuto", "退出", "正在停止 Bot 并退出…")
            self.sup.shutdown()
        finally:
            rumps.quit_application()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _fmt_dur(sec: float) -> str:
        if not sec or sec < 0:
            return "—"
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        if h > 0:
            return f"{h}h{m:02d}m"
        if m > 0:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    @staticmethod
    def _fmt_ago(ts: float | None, now: float) -> str:
        if not ts:
            return "—"
        delta = now - ts
        if delta < 0:
            return "刚刚"
        if delta < 60:
            return f"{int(delta)}秒前"
        if delta < 3600:
            return f"{int(delta // 60)}分钟前"
        if delta < 86400:
            return f"{int(delta // 3600)}小时前"
        return f"{int(delta // 86400)}天前"


def run(supervisor: BotSupervisor, cfg: WebUIConfig) -> None:
    """Construct the app and run its main loop (blocks)."""
    app = ControlBarApp(supervisor=supervisor, cfg=cfg)
    app.run()
