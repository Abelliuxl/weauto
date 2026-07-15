"""Windows system-tray control panel with parity to the macOS menu bar."""
from __future__ import annotations

from pathlib import Path
import threading
import webbrowser

from PIL import Image
import pystray

from ..config import WebUIConfig
from ..supervisor import BotSupervisor


class ControlTrayApp:
    def __init__(self, supervisor: BotSupervisor, cfg: WebUIConfig) -> None:
        self.sup = supervisor
        self.cfg = cfg
        self._web_url = cfg.web_url()
        self._stop = threading.Event()
        icon_path = (
            Path(__file__).resolve().parents[2]
            / "WeAuto.app"
            / "Contents"
            / "Resources"
            / "icon_512.png"
        )
        image = Image.open(icon_path).convert("RGBA")
        self.icon = pystray.Icon(
            "WeAuto",
            image,
            "WeAuto",
            menu=pystray.Menu(
                pystray.MenuItem(self._status_text, self._noop, enabled=False),
                pystray.MenuItem(self._uptime_text, self._noop, enabled=False),
                pystray.MenuItem(self._bridge_text, self._noop, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("打开 Web 控制台", self._open, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("重启 Bot", self._restart),
                pystray.MenuItem("停止 Bot", self._stop_bot, enabled=self._can_stop),
                pystray.MenuItem("启动 Bot", self._start_bot, enabled=self._can_start),
                pystray.MenuItem("自动重启", self._toggle_auto, checked=self._auto_checked),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("关于 WeAuto", self._about),
                pystray.MenuItem("退出", self._quit),
            ),
        )
        self.sup.on_change = self.refresh

    def run(self) -> None:
        threading.Thread(target=self._refresh_loop, name="weauto-tray-refresh", daemon=True).start()
        self.icon.run()

    def _refresh_loop(self) -> None:
        while not self._stop.wait(2.0):
            self.refresh()

    def refresh(self) -> None:
        try:
            status = self.sup.status()
            state = "运行中" if status.running else "已停止"
            self.icon.title = f"WeAuto · {state} · {self._fmt_dur(status.uptime_sec)}"
            self.icon.update_menu()
        except Exception:
            pass

    def _status_text(self, _item) -> str:
        status = self.sup.status()
        text = f"状态: {'运行中' if status.running else '已停止'}"
        if not status.running and status.last_exit_code is not None:
            text += f" (退出码 {status.last_exit_code})"
        return text

    def _uptime_text(self, _item) -> str:
        status = self.sup.status()
        return f"运行: {self._fmt_dur(status.uptime_sec)} · 重启 {status.restart_count} 次"

    def _bridge_text(self, _item) -> str:
        status = self.sup.status()
        return f"桥接: {status.bridge_mode} / {status.bridge_state}"

    def _can_stop(self, _item) -> bool:
        return self.sup.status().running

    def _can_start(self, _item) -> bool:
        return not self.sup.status().running

    def _auto_checked(self, _item) -> bool:
        return self.sup.status().auto_restart

    @staticmethod
    def _noop(_icon, _item) -> None:
        return None

    def _notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def _open(self, _icon, _item) -> None:
        webbrowser.open(self._web_url)

    def _restart(self, _icon, _item) -> None:
        self._notify("重启 Bot", "正在重启子进程…")
        threading.Thread(target=self.sup.restart_bot, daemon=True).start()

    def _stop_bot(self, _icon, _item) -> None:
        self._notify("停止 Bot", "已请求停止（不会自动重启）")
        threading.Thread(target=self.sup.stop_bot, daemon=True).start()

    def _start_bot(self, _icon, _item) -> None:
        self._notify("启动 Bot", "正在拉起子进程…")
        threading.Thread(target=self.sup.start_bot, daemon=True).start()

    def _toggle_auto(self, _icon, _item) -> None:
        self.sup.set_auto_restart(not self.sup.status().auto_restart)
        self.refresh()

    def _about(self, _icon, _item) -> None:
        self._notify("WeAuto 控制面板", f"Windows 微信 GUI RPA\n{self._web_url}")

    def _quit(self, _icon, _item) -> None:
        self._stop.set()
        self.sup.shutdown()
        self.icon.stop()

    @staticmethod
    def _fmt_dur(sec: float) -> str:
        if not sec or sec < 0:
            return "—"
        hours = int(sec // 3600)
        minutes = int((sec % 3600) // 60)
        seconds = int(sec % 60)
        if hours:
            return f"{hours}h{minutes:02d}m"
        if minutes:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"


def run(supervisor: BotSupervisor, cfg: WebUIConfig) -> None:
    ControlTrayApp(supervisor, cfg).run()
