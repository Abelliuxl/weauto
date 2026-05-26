from __future__ import annotations

from pathlib import Path
import subprocess
import time

from .config import AppConfig


class SendError(RuntimeError):
    pass


class WeChatGuiSender:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def activate(self) -> None:
        aliases = [x.strip() for x in self.cfg.app_name.split("|") if x.strip()]
        if "WeChat" in aliases and "微信" not in aliases:
            aliases.append("微信")
        if not aliases:
            aliases = ["WeChat", "微信"]

        for app in aliases:
            proc = subprocess.run(
                ["osascript", "-e", f'tell application "{app}" to activate'],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return

        print(f"[warn] failed to activate app, tried aliases={aliases}")

    def activate_chat_window(self, title: str) -> bool:
        """Raise a detached chat window before sending keyboard input."""
        clean_title = str(title or "").strip()
        if not clean_title:
            self.activate()
            return False
        aliases = [x.strip() for x in self.cfg.app_name.split("|") if x.strip()]
        if "WeChat" in aliases and "微信" not in aliases:
            aliases.append("微信")
        if not aliases:
            aliases = ["WeChat", "微信"]

        quoted_title = self.apple_quote(clean_title)
        for app in aliases:
            script = f'''
tell application "{self.apple_quote(app)}" to activate
tell application "System Events"
  tell process "{self.apple_quote(app)}"
    set frontmost to true
    repeat with w in windows
      try
        if (name of w) contains "{quoted_title}" then
          perform action "AXRaise" of w
          return "ok"
        end if
      end try
    end repeat
  end tell
end tell
return "not_found"
'''
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip() == "ok":
                time.sleep(max(0.05, self.cfg.activate_wait_sec))
                return True
        self.activate()
        return False

    def safe_click(self, x: int, y: int) -> None:
        import pyautogui

        pyautogui.moveTo(x, y, duration=max(0.0, self.cfg.click_move_duration_sec))
        pyautogui.mouseDown()
        time.sleep(max(0.01, self.cfg.mouse_down_hold_sec))
        pyautogui.mouseUp()

    def send_delay_sec(self) -> float:
        return max(0.0, float(self.cfg.send_after_paste_delay_sec))

    @staticmethod
    def apple_quote(raw: str) -> str:
        return str(raw or "").replace("\\", "\\\\").replace('"', '\\"')

    def paste_and_send(self, message: str) -> None:
        import pyautogui
        import pyperclip

        pyperclip.copy(message)
        time.sleep(0.05)
        delay_sec = self.send_delay_sec()

        paste_script = 'tell application "System Events" to keystroke "v" using command down'
        enter_script = 'tell application "System Events" to key code 36'
        proc = subprocess.run(
            [
                "osascript",
                "-e",
                paste_script,
                "-e",
                f"delay {delay_sec:.3f}",
                "-e",
                enter_script,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return

        print(f"[warn] osascript paste failed, fallback to pyautogui: {proc.stderr.strip()}")
        pyautogui.keyDown("command")
        pyautogui.press("v")
        pyautogui.keyUp("command")
        time.sleep(delay_sec)
        pyautogui.press("enter")

    def paste_and_send_to_window(self, title: str, message: str) -> bool:
        raised = self.activate_chat_window(title)
        self.paste_and_send(message)
        return raised

    def paste_file_and_send(self, file_path: Path) -> bool:
        import pyautogui

        target = Path(file_path).expanduser().resolve()
        if not target.exists():
            print(f"[warn] file-send skipped, file not found: {target}")
            return False

        delay_sec = self.send_delay_sec()
        set_clip_script = f'set the clipboard to (POSIX file "{self.apple_quote(str(target))}")'
        paste_script = 'tell application "System Events" to keystroke "v" using command down'
        enter_script = 'tell application "System Events" to key code 36'
        proc = subprocess.run(
            [
                "osascript",
                "-e",
                set_clip_script,
                "-e",
                paste_script,
                "-e",
                f"delay {delay_sec:.3f}",
                "-e",
                enter_script,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return True

        print(f"[warn] osascript file paste failed, fallback to pyautogui: {proc.stderr.strip()}")
        clip_proc = subprocess.run(
            ["osascript", "-e", set_clip_script],
            capture_output=True,
            text=True,
        )
        if clip_proc.returncode != 0:
            print(f"[warn] osascript set file clipboard failed: {clip_proc.stderr.strip()}")
            return False
        pyautogui.keyDown("command")
        pyautogui.press("v")
        pyautogui.keyUp("command")
        time.sleep(delay_sec)
        pyautogui.press("enter")
        return True

    def paste_file_and_send_to_window(self, title: str, file_path: Path) -> bool:
        raised = self.activate_chat_window(title)
        sent = self.paste_file_and_send(file_path)
        return bool(raised and sent)
