"""Small Win32 backend used by WeAuto on Windows.

The module intentionally uses only :mod:`ctypes` and Pillow so the core RPA
does not depend on pywin32.  It provides the native operations that Quartz and
AppleScript provide on macOS: top-level window discovery, activation, resize,
window capture, and a CF_HDROP file clipboard.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time

from PIL import Image, ImageGrab, ImageStat


IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class NativeWindow:
    hwnd: int
    owner: str
    title: str
    class_name: str
    process_path: str
    x: int
    y: int
    width: int
    height: int


if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # pragma: no cover - available on supported Windows builds
        dwmapi = None

    # ctypes defaults function results to 32-bit ``int``.  Explicit handle
    # signatures are required on 64-bit Windows or HWND/HDC/HBITMAP values can
    # be truncated and crash the capture process.
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
else:  # pragma: no cover - imported only by type checkers/non-Windows tests
    user32 = kernel32 = gdi32 = dwmapi = None


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise RuntimeError("Win32 backend is only available on Windows")


def enable_dpi_awareness() -> None:
    """Use physical pixels so Win32 bounds line up with pyautogui screenshots."""
    if not IS_WINDOWS:
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


enable_dpi_awareness()


def app_aliases(app_name: str) -> list[str]:
    aliases = [part.strip() for part in str(app_name or "").split("|") if part.strip()]
    lowered = {item.casefold() for item in aliases}
    if not aliases or lowered.intersection({"wechat", "weixin", "微信"}):
        for item in ("WeChat", "Weixin", "微信"):
            if item.casefold() not in lowered:
                aliases.append(item)
                lowered.add(item.casefold())
    return aliases or ["WeChat", "Weixin", "微信"]


def _process_path(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_text(hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(wintypes.HWND(hwnd)))
    buffer = ctypes.create_unicode_buffer(max(2, length + 1))
    user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value.strip()


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value.strip()


def _is_cloaked(hwnd: int) -> bool:
    if dwmapi is None:
        return False
    value = wintypes.DWORD(0)
    # DWMWA_CLOAKED = 14
    try:
        result = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value)
        )
        return result == 0 and bool(value.value)
    except (AttributeError, OSError):
        return False


def enumerate_windows(app_name: str | None = None) -> list[NativeWindow]:
    """Return visible top-level windows, optionally limited to the WeChat app."""
    _require_windows()
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    aliases = app_aliases(app_name or "") if app_name is not None else []
    process_aliases = {
        item.casefold()
        for item in aliases
        if item.isascii() and item.replace("-", "").replace("_", "").isalnum()
    }
    results: list[NativeWindow] = []
    process_cache: dict[int, str] = {}

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(int(hwnd)):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        pid_value = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
        pid = int(pid_value.value)
        path = process_cache.get(pid)
        if path is None:
            path = _process_path(pid)
            process_cache[pid] = path
        owner = Path(path).stem if path else ""
        title = _window_text(int(hwnd))

        if app_name is not None:
            owner_matches = owner.casefold() in process_aliases
            title_matches = any(alias.casefold() in title.casefold() for alias in aliases)
            if not owner_matches and not (not owner and title_matches):
                return True

        results.append(
            NativeWindow(
                hwnd=int(hwnd),
                owner=owner,
                title=title,
                class_name=_class_name(int(hwnd)),
                process_path=path,
                x=int(rect.left),
                y=int(rect.top),
                width=width,
                height=height,
            )
        )
        return True

    if not user32.EnumWindows(callback, 0):
        error = ctypes.get_last_error()
        if error:
            raise OSError(error, "EnumWindows failed")
    return results


def get_window(hwnd: int) -> NativeWindow | None:
    for window in enumerate_windows(None):
        if window.hwnd == int(hwnd):
            return window
    return None


def find_app_windows(app_name: str, title: str = "") -> list[NativeWindow]:
    windows = enumerate_windows(app_name)
    needle = str(title or "").strip().casefold()
    if needle:
        windows = [window for window in windows if needle in window.title.casefold()]
    return windows


def activate_window(hwnd: int) -> bool:
    """Restore and raise a window, using thread input attachment when needed."""
    _require_windows()
    hwnd_value = wintypes.HWND(int(hwnd))
    if not user32.IsWindow(hwnd_value):
        return False
    SW_RESTORE = 9
    if user32.IsIconic(hwnd_value):
        user32.ShowWindow(hwnd_value, SW_RESTORE)

    current_tid = int(kernel32.GetCurrentThreadId())
    foreground = user32.GetForegroundWindow()
    foreground_tid = int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
    target_tid = int(user32.GetWindowThreadProcessId(hwnd_value, None))
    attached: list[int] = []
    try:
        for tid in {foreground_tid, target_tid}:
            if tid and tid != current_tid and user32.AttachThreadInput(current_tid, tid, True):
                attached.append(tid)
        user32.BringWindowToTop(hwnd_value)
        user32.SetForegroundWindow(hwnd_value)
        user32.SetActiveWindow(hwnd_value)
        user32.SetFocus(hwnd_value)
    finally:
        for tid in attached:
            user32.AttachThreadInput(current_tid, tid, False)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def activate_app(app_name: str, title: str = "") -> NativeWindow | None:
    windows = find_app_windows(app_name, title)
    if not windows:
        return None
    # Prefer an exact title, then the largest matching app window.
    clean_title = str(title or "").strip().casefold()
    exact = [w for w in windows if w.title.casefold() == clean_title] if clean_title else []
    target = max(exact or windows, key=lambda item: item.width * item.height)
    activate_window(target.hwnd)
    return target


def resize_window(hwnd: int, width: int, height: int) -> bool:
    _require_windows()
    hwnd_value = wintypes.HWND(int(hwnd))
    if not user32.IsWindow(hwnd_value):
        return False
    if user32.IsIconic(hwnd_value) or user32.IsZoomed(hwnd_value):
        user32.ShowWindow(hwnd_value, 9)  # SW_RESTORE
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    return bool(
        user32.SetWindowPos(
            hwnd_value,
            None,
            0,
            0,
            max(1, int(width)),
            max(1, int(height)),
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        )
    )


def _print_window(hwnd: int, width: int, height: int) -> Image.Image | None:
    hwnd_value = wintypes.HWND(int(hwnd))
    window_dc = user32.GetWindowDC(hwnd_value)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height) if memory_dc else None
    previous = gdi32.SelectObject(memory_dc, bitmap) if bitmap else None
    try:
        if not bitmap or not user32.PrintWindow(hwnd_value, memory_dc, 0x00000002):
            return None
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down DIB
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info),
            0,
        )
        if lines != height:
            return None
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1).copy()
        extrema = ImageStat.Stat(image.convert("L")).extrema[0]
        if extrema[1] <= 5 or extrema[1] - extrema[0] <= 2:
            image.close()
            return None
        return image
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd_value, window_dc)


def capture_window(hwnd: int) -> Image.Image:
    """Capture one top-level window, falling back to its visible screen rect."""
    window = get_window(hwnd)
    if window is None:
        raise RuntimeError(f"window capture failed: hwnd={hwnd} not found")
    image = _print_window(window.hwnd, window.width, window.height)
    if image is not None:
        return image
    try:
        return ImageGrab.grab(
            bbox=(window.x, window.y, window.x + window.width, window.y + window.height),
            all_screens=True,
        )
    except Exception as exc:
        raise RuntimeError(f"window capture failed: hwnd={hwnd}") from exc


def copy_files_to_clipboard(paths: list[Path]) -> None:
    """Place files on the Windows clipboard as CF_HDROP entries."""
    _require_windows()
    clean_paths = [str(Path(path).expanduser().resolve()) for path in paths]
    if not clean_paths:
        raise ValueError("at least one file is required")
    payload = ("\0".join(clean_paths) + "\0\0").encode("utf-16-le")
    header = _DROPFILES()
    header.pFiles = ctypes.sizeof(_DROPFILES)
    header.fWide = True
    total_size = ctypes.sizeof(header) + len(payload)

    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040
    CF_HDROP = 15
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = wintypes.LPVOID
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total_size)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, ctypes.byref(header), ctypes.sizeof(header))
            ctypes.memmove(pointer + ctypes.sizeof(header), payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)

        opened = False
        for _ in range(20):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.025)
        if not opened:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            if not user32.SetClipboardData(CF_HDROP, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)
