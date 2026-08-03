from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from .config import AppConfig


IS_WINDOWS = sys.platform == "win32"


class SendError(RuntimeError):
    pass


class WeChatGuiSender:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def activate(self) -> None:
        if IS_WINDOWS:
            from .win32 import activate_app

            if activate_app(self.cfg.app_name) is None:
                print(f"[warn] failed to activate app: {self.cfg.app_name!r}")
            return

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
        if IS_WINDOWS:
            from .win32 import activate_app

            target = activate_app(self.cfg.app_name, clean_title)
            if target is None or target.title.strip().casefold() != clean_title.casefold():
                self.activate()
                return False
            time.sleep(max(0.05, self.cfg.activate_wait_sec))
            return True

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
        if (name of w) is "{quoted_title}" then
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

    def mention_timing(self) -> tuple[float, float, float]:
        return (
            max(0.0, float(self.cfg.mention_after_paste_delay_sec)),
            max(0.0, float(self.cfg.mention_after_backspace_delay_sec)),
            max(0.0, float(self.cfg.mention_after_confirm_delay_sec)),
        )

    @staticmethod
    def apple_quote(raw: str) -> str:
        return str(raw or "").replace("\\", "\\\\").replace('"', '\\"')

    def click_chat_input_for_window(self, title: str) -> bool:
        clean_title = str(title or "").strip()
        if not clean_title:
            return False
        if IS_WINDOWS:
            from .win32 import activate_app

            target = activate_app(self.cfg.app_name, clean_title)
            if target is None or target.title.strip().casefold() != clean_title.casefold():
                return False
            time.sleep(max(0.05, self.cfg.activate_wait_sec))
            point = getattr(self.cfg, "input_point", None)
            ratio_x = float(getattr(point, "x", 0.5))
            ratio_y = float(getattr(point, "y", 0.92))
            self.safe_click(
                target.x + int(target.width * ratio_x),
                target.y + int(target.height * ratio_y),
            )
            return True

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
        if (name of w) is "{quoted_title}" then
          perform action "AXRaise" of w
          set p to position of w
          set s to size of w
          set inputX to (item 1 of p) + ((item 1 of s) div 2)
          set inputY to (item 2 of p) + (item 2 of s) - 52
          click at {{inputX, inputY}}
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
        return False

    def paste_and_send(self, message: str) -> None:
        import pyautogui
        import pyperclip

        pyperclip.copy(message)
        time.sleep(0.05)
        delay_sec = self.send_delay_sec()

        if IS_WINDOWS:
            pyautogui.hotkey("ctrl", "v")
            time.sleep(delay_sec)
            pyautogui.press("enter")
            return

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

    def mention_and_send(self, mention_name: str, message: str) -> None:
        import pyautogui
        import pyperclip

        mention = str(mention_name or "").strip().lstrip("@").strip()
        body = str(message or "").strip()
        if not mention or not body:
            self.paste_and_send(body)
            return

        suffix = str(self.cfg.mention_trigger_suffix or "A")[:8] or "A"
        after_paste, after_backspace, after_confirm = self.mention_timing()

        if IS_WINDOWS:
            pyperclip.copy(f"@{mention}{suffix}")
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(after_paste)
            pyautogui.press("backspace")
            time.sleep(after_backspace)
            pyautogui.press("enter")
            time.sleep(after_confirm)
            pyperclip.copy(f" {body}")
            time.sleep(0.05)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(self.send_delay_sec())
            pyautogui.press("enter")
            return

        pyperclip.copy(f"@{mention}{suffix}")
        time.sleep(0.05)
        trigger_script = 'tell application "System Events" to keystroke "v" using command down'
        backspace_script = 'tell application "System Events" to key code 51'
        enter_script = 'tell application "System Events" to key code 36'
        proc = subprocess.run(
            [
                "osascript",
                "-e",
                trigger_script,
                "-e",
                f"delay {after_paste:.3f}",
                "-e",
                backspace_script,
                "-e",
                f"delay {after_backspace:.3f}",
                "-e",
                enter_script,
                "-e",
                f"delay {after_confirm:.3f}",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"[warn] osascript mention trigger failed, fallback to pyautogui: {proc.stderr.strip()}")
            pyautogui.keyDown("command")
            pyautogui.press("v")
            pyautogui.keyUp("command")
            time.sleep(after_paste)
            pyautogui.press("backspace")
            time.sleep(after_backspace)
            pyautogui.press("enter")
            time.sleep(after_confirm)

        pyperclip.copy(f" {body}")
        time.sleep(0.05)
        delay_sec = self.send_delay_sec()
        paste_script = 'tell application "System Events" to keystroke "v" using command down'
        send_proc = subprocess.run(
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
        if send_proc.returncode == 0:
            return

        print(f"[warn] osascript mention body send failed, fallback to pyautogui: {send_proc.stderr.strip()}")
        pyautogui.keyDown("command")
        pyautogui.press("v")
        pyautogui.keyUp("command")
        time.sleep(delay_sec)
        pyautogui.press("enter")

    def paste_and_send_to_window(self, title: str, message: str) -> bool:
        raised = self.activate_chat_window(title)
        if not raised:
            return False
        self.paste_and_send(message)
        return True

    def mention_and_send_to_window(self, title: str, mention_name: str, message: str) -> bool:
        raised = self.activate_chat_window(title) if IS_WINDOWS else self.click_chat_input_for_window(title)
        if (not IS_WINDOWS) and not raised:
            raised = self.activate_chat_window(title)
        if not raised:
            return False
        self.mention_and_send(mention_name, message)
        return True

    def paste_file_and_send(self, file_path: Path) -> bool:
        import pyautogui

        target = Path(file_path).expanduser().resolve()
        if not target.exists():
            print(f"[warn] file-send skipped, file not found: {target}")
            return False

        delay_sec = self.send_delay_sec()
        if IS_WINDOWS:
            from .win32 import copy_files_to_clipboard

            try:
                copy_files_to_clipboard([target])
            except Exception as exc:  # noqa: BLE001 - sending should fail open
                print(f"[warn] Windows file clipboard failed: {exc}")
                return False
            pyautogui.hotkey("ctrl", "v")
            time.sleep(delay_sec)
            pyautogui.press("enter")
            return True

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
        if not raised:
            return False
        sent = self.paste_file_and_send(file_path)
        return bool(sent)
