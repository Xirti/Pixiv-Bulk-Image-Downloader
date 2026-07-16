from __future__ import annotations

import argparse
import ctypes
import json
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def text(hwnd: int) -> str:
    size = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(max(size, 2))
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def windows_for(pid: int) -> list[dict]:
    rows = []
    @EnumWindowsProc
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            rows.append({
                "hwnd": int(hwnd), "title": text(hwnd), "class": class_name(hwnd),
                "visible": bool(user32.IsWindowVisible(hwnd)), "enabled": bool(user32.IsWindowEnabled(hwnd)),
            })
        return True
    if not user32.EnumWindows(callback, 0): raise ctypes.WinError(ctypes.get_last_error())
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("pid", type=int); parser.add_argument("--close-dialog", action="store_true")
    args = parser.parse_args(); rows = windows_for(args.pid)
    closed = []
    if args.close_dialog:
        for row in rows:
            if row["class"] == "#32770" and row["visible"]:
                user32.PostMessageW(row["hwnd"], 0x0010, 0, 0); closed.append(row["hwnd"])
    print(json.dumps({"windows": rows, "closedDialogs": closed}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
