"""Windows-owned sizing, moving and snap for the custom application caption."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes


LOG = logging.getLogger("moku.window")
_SUBCLASS_ID = 0x4D4F4B55
_WINDOW_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
    wintypes.LPARAM, ctypes.c_size_t, ctypes.c_size_t,
)


class _WindowPosition(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("after", wintypes.HWND),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int), ("flags", wintypes.UINT)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


class _MinMaxInfo(ctypes.Structure):
    _fields_ = [("reserved", wintypes.POINT), ("max_size", wintypes.POINT),
                ("max_position", wintypes.POINT), ("min_track", wintypes.POINT),
                ("max_track", wintypes.POINT)]


class NativeWindowFrame:
    def __init__(self, native, browser_navigation=None) -> None:
        # Change native styles without rebuilding the existing WebView host.
        self.hwnd = native.Handle.ToInt64()
        self.browser_navigation = browser_navigation
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.restype = wintypes.UINT
        self.user32.AdjustWindowRectExForDpi.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD,
                                                       wintypes.BOOL, wintypes.DWORD, wintypes.UINT]
        self.user32.AdjustWindowRectExForDpi.restype = wintypes.BOOL
        self.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SendMessageW.restype = ctypes.c_ssize_t
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.user32.IsZoomed.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
        self.user32.GetMonitorInfoW.restype = wintypes.BOOL
        self.comctl32.DefSubclassProc.argtypes = self.user32.SendMessageW.argtypes
        self.comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
        self.comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, _WINDOW_PROC, ctypes.c_size_t, ctypes.c_size_t]
        self.comctl32.SetWindowSubclass.restype = wintypes.BOOL
        self.comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, _WINDOW_PROC, ctypes.c_size_t]
        self.comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongW.restype = wintypes.LONG
        self.user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        self.user32.SetWindowLongW.restype = wintypes.LONG
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self._callback = _WINDOW_PROC(self._window_proc)
        self._was_maximized = self.maximized
        self._was_minimized = bool(self.user32.IsIconic(self.hwnd))
        self._restoring_bounds = None
        if not self.comctl32.SetWindowSubclass(self.hwnd, self._callback, _SUBCLASS_ID, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        style = self.user32.GetWindowLongW(self.hwnd, -16)
        ctypes.set_last_error(0)
        previous = self.user32.SetWindowLongW(self.hwnd, -16, (style | 0x000F0000) & ~0x00C00000)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0037):
            raise ctypes.WinError(ctypes.get_last_error())

    @property
    def maximized(self) -> bool:
        return bool(self.user32.IsZoomed(self.hwnd))

    def command(self, command: int) -> None:
        self.user32.SendMessageW(self.hwnd, 0x0112, command, 0)

    def drag(self) -> None:
        # The bridge can arrive after mouse-up; that must not start a move loop.
        if self.user32.GetAsyncKeyState(1) & 0x8000:
            self.user32.ReleaseCapture()
            self.user32.SendMessageW(self.hwnd, 0x00A1, 2, self.user32.GetMessagePos())

    def _window_proc(self, hwnd, message, wparam, lparam, subclass_id, reference) -> int:
        try:
            if message == 0x0319 and self.browser_navigation:  # WM_APPCOMMAND from mouse drivers.
                command = (lparam >> 16) & 0x0FFF
                if command in {1, 2} and self.browser_navigation(-1 if command == 1 else 1):
                    return 1
            if (message == 0x0047 and (self._was_maximized or self._was_minimized)
                    and not self.maximized and not self.user32.IsIconic(hwnd)):
                # Windows has chosen the restore/snap rectangle. Keep its bounds
                # while WinForms processes the change using its caption metrics.
                rect = wintypes.RECT()
                if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._restoring_bounds = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                try:
                    return self.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
                finally:
                    self._restoring_bounds = None
            if message == 0x0046:  # WM_WINDOWPOSCHANGING
                result = self.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
                if self._restoring_bounds:
                    position = ctypes.cast(lparam, ctypes.POINTER(_WindowPosition)).contents
                    position.x, position.y, position.width, position.height = self._restoring_bounds
                    position.flags &= ~3  # SWP_NOSIZE | SWP_NOMOVE
                return result
            if message == 0x0005:  # WM_SIZE
                self._was_minimized = wparam == 1
                if not self._was_minimized:
                    self._was_maximized = wparam == 2
            if message == 0x0024:  # WM_GETMINMAXINFO: borderless maximize uses the monitor work area.
                result = self.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
                monitor = self.user32.MonitorFromWindow(hwnd, 2)
                info = _MonitorInfo()
                info.size = ctypes.sizeof(info)
                if not self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                dpi = self.user32.GetDpiForWindow(hwnd)
                frame = wintypes.RECT()
                style = self.user32.GetWindowLongW(hwnd, -16)
                extended_style = self.user32.GetWindowLongW(hwnd, -20)
                if not self.user32.AdjustWindowRectExForDpi(ctypes.byref(frame), style, False, extended_style, dpi):
                    raise ctypes.WinError(ctypes.get_last_error())
                x_frame, y_frame = -frame.left, -frame.top
                limits = ctypes.cast(lparam, ctypes.POINTER(_MinMaxInfo)).contents
                limits.max_position.x = info.work.left - info.monitor.left - x_frame
                limits.max_position.y = info.work.top - info.monitor.top - y_frame
                limits.max_size.x = info.work.right - info.work.left + 2 * x_frame
                limits.max_size.y = info.work.bottom - info.work.top + 2 * y_frame
                return result
            if message == 0x0082:  # WM_NCDESTROY: detach before forwarding destruction.
                self.comctl32.RemoveWindowSubclass(hwnd, self._callback, _SUBCLASS_ID)
        except Exception:
            LOG.exception("Native window frame message failed: %s", message)
        return self.comctl32.DefSubclassProc(hwnd, message, wparam, lparam)
