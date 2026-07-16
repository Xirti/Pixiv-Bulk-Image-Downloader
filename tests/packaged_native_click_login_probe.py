from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlsplit

import websocket

WM_CLOSE = 0x0010
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SW_RESTORE = 9

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, EnumChildProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsHungAppWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def local_json(url: str, timeout: float = 3.0, headers: dict | None = None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers=headers or {})
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def title_for(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    text = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, text, len(text))
    return text.value


def class_for(hwnd: int) -> str:
    text = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, text, len(text))
    return text.value


def rect_for(hwnd: int) -> dict:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
    }


def top_windows(pid: int) -> list[dict]:
    rows: list[dict] = []

    @EnumWindowsProc
    def callback(hwnd, _state):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid and user32.IsWindowVisible(hwnd):
            rows.append({
                "hwnd": int(hwnd),
                "title": title_for(hwnd),
                "class": class_for(hwnd),
                "hung": bool(user32.IsHungAppWindow(hwnd)),
                "rect": rect_for(hwnd),
            })
        return True

    user32.EnumWindows(callback, 0)
    return rows


def child_windows(parent: int) -> list[dict]:
    rows: list[dict] = []

    @EnumChildProc
    def callback(hwnd, _state):
        rows.append({
            "hwnd": int(hwnd),
            "title": title_for(hwnd),
            "class": class_for(hwnd),
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "rect": rect_for(hwnd),
        })
        return True

    user32.EnumChildWindows(parent, callback, 0)
    return rows


def wait_until(predicate, timeout: float, label: str, interval: float = 0.15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, RuntimeError, websocket.WebSocketException):
            pass
        time.sleep(interval)
    raise TimeoutError(f"timeout waiting for {label}; last={last!r}")


def cdp(ws, ident: int, method: str, params: dict | None = None) -> dict:
    ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
    while True:
        row = json.loads(ws.recv())
        if row.get("id") == ident:
            if "error" in row:
                raise RuntimeError(json.dumps(row["error"], ensure_ascii=False))
            return row.get("result") or {}


def evaluate(ws, counter: list[int], expression: str, *, await_promise: bool = False):
    counter[0] += 1
    result = cdp(ws, counter[0], "Runtime.evaluate", {
        "expression": expression,
        "awaitPromise": await_promise,
        "returnByValue": True,
    })
    if result.get("exceptionDetails"):
        raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
    return (result.get("result") or {}).get("value")


def main_target(port: int, base: str):
    return next((
        row for row in local_json(f"http://127.0.0.1:{port}/json/list")
        if row.get("type") == "page" and str(row.get("url", "")).startswith(base)
    ), None)


def official_target(port: int):
    return next((
        row for row in local_json(f"http://127.0.0.1:{port}/json/list")
        if row.get("type") == "page"
        and urlsplit(str(row.get("url", ""))).scheme == "https"
        and urlsplit(str(row.get("url", ""))).hostname in {"www.pixiv.net", "accounts.pixiv.net"}
    ), None)


def launch(exe: Path, root: Path, port: int):
    local_app_data = root / "localappdata"
    runtime_dir = root / "runtime"
    local_app_data.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    env = os.environ.copy()
    env.update({
        "LOCALAPPDATA": str(local_app_data),
        "MOKU_RUNTIME_DIR": str(runtime_dir),
        "MOKU_MUTEX_NAME": "Local\\MOKU.NativeClick." + os.urandom(12).hex(),
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": (
            f"--remote-debugging-address=127.0.0.1 --remote-debugging-port={port} "
            "--remote-allow-origins=http://127.0.0.1"
        ),
    })
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env)
    descriptor_file = runtime_dir / "backend.json"
    descriptor = wait_until(
        lambda: json.loads(descriptor_file.read_text(encoding="utf-8-sig"))
        if descriptor_file.exists() else None,
        35,
        "backend descriptor",
    )
    base = f"http://127.0.0.1:{int(descriptor['port'])}/"
    main = wait_until(
        lambda: next((row for row in top_windows(process.pid) if row["title"] == "MOKU — Pixiv 标签采集册"), None),
        25,
        "main window",
    )
    user32.ShowWindow(main["hwnd"], SW_RESTORE)
    user32.MoveWindow(main["hwnd"], 80, 80, 1440, 900, True)
    time.sleep(1)
    return process, base, main["hwnd"]


def stop(process, root: Path, *, remove_root: bool = True):
    if process is not None and process.poll() is None:
        main = next((row for row in top_windows(process.pid) if row["title"] == "MOKU — Pixiv 标签采集册"), None)
        if main:
            user32.PostMessageW(main["hwnd"], WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if remove_root:
        shutil.rmtree(root, ignore_errors=True)


def renderer_for(main_hwnd: int) -> dict:
    children = child_windows(main_hwnd)
    renderers = [row for row in children if row["class"] == "Chrome_RenderWidgetHostHWND" and row["visible"]]
    if not renderers:
        raise RuntimeError(f"renderer HWND not found: {[row['class'] for row in children]}")
    return max(renderers, key=lambda row: row["rect"]["width"] * row["rect"]["height"])


def native_click(renderer: dict, viewport: dict, box: dict) -> dict:
    width = renderer["rect"]["width"]
    height = renderer["rect"]["height"]
    x_css = box["x"] + box["width"] / 2
    y_css = box["y"] + box["height"] / 2
    x = round(x_css * width / viewport["width"])
    y = round(y_css * height / viewport["height"])
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    user32.PostMessageW(renderer["hwnd"], WM_MOUSEMOVE, 0, lparam)
    user32.PostMessageW(renderer["hwnd"], WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    user32.PostMessageW(renderer["hwnd"], WM_LBUTTONUP, 0, lparam)
    return {"x": x, "y": y, "hwnd": renderer["hwnd"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    args = parser.parse_args()
    exe = Path(args.exe).resolve()
    calibration_root = Path(tempfile.mkdtemp(prefix="moku-native-calibration-"))
    actual_root = Path(tempfile.mkdtemp(prefix="moku-native-actual-"))
    calibration = None
    actual = None
    result = {
        "ok": False,
        "calibrated": False,
        "unauthenticated": False,
        "nativeClicks": [],
        "loginWindow": False,
        "loginUrl": "",
        "cancelText": "",
        "mainResponsive": False,
        "processExited": False,
        "sessionProfilesAfterExit": -1,
        "error": "",
    }
    try:
        calibration_port = free_port()
        calibration, calibration_base, calibration_hwnd = launch(exe, calibration_root, calibration_port)
        target = wait_until(lambda: main_target(calibration_port, calibration_base), 25, "calibration CDP target")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        counter = [0]
        try:
            wait_until(
                lambda: evaluate(ws, counter, "document.readyState === 'complete' && !!window.pywebview?.api?.pixiv_login"),
                20,
                "calibration page",
            )
            geometry = evaluate(ws, counter, """(() => {
                const rect = selector => {
                    const r = document.querySelector(selector).getBoundingClientRect();
                    return {x:r.x,y:r.y,width:r.width,height:r.height};
                };
                const login = rect('#loginBtn');
                const dialog = document.querySelector('#loginDialog');
                dialog.showModal();
                const action = rect('#authAction');
                dialog.close();
                return {viewport:{width:innerWidth,height:innerHeight},login,action};
            })()""")
        finally:
            ws.close()
        calibration_renderer = renderer_for(calibration_hwnd)
        geometry["renderer"] = calibration_renderer["rect"]
        result["calibrated"] = True
        stop(calibration, calibration_root)
        calibration = None

        actual_port = free_port()
        actual, actual_base, actual_hwnd = launch(exe, actual_root, actual_port)
        health = local_json(actual_base + "api/health")
        protected_headers = {"X-MOKU-Request-Token": str(health["requestToken"])}
        status = wait_until(
            lambda: (
                value if (value := local_json(actual_base + "api/status", headers=protected_headers))
                and value.get("authState") != "checking" else None
            ),
            35,
            "stable auth status",
        )
        if status.get("loggedIn"):
            raise RuntimeError("existing authorization detected; refusing to click logout")
        result["unauthenticated"] = True
        renderer = renderer_for(actual_hwnd)
        result["nativeClicks"].append(native_click(renderer, geometry["viewport"], geometry["login"]))
        time.sleep(1)
        result["nativeClicks"].append(native_click(renderer, geometry["viewport"], geometry["action"]))

        login = wait_until(
            lambda: next((row for row in top_windows(actual.pid) if row["title"] == "MOKU — Pixiv 官方登录"), None),
            25,
            "official login window",
        )
        result["loginWindow"] = True
        target = wait_until(lambda: official_target(actual_port), 30, "official Pixiv target")
        parts = urlsplit(str(target.get("url", "")))
        result["loginUrl"] = f"{parts.scheme}://{parts.hostname}{parts.path}"
        user32.PostMessageW(login["hwnd"], WM_CLOSE, 0, 0)
        wait_until(
            lambda: not any(row["title"] == "MOKU — Pixiv 官方登录" for row in top_windows(actual.pid)),
            15,
            "login window close",
        )

        main = wait_until(lambda: main_target(actual_port, actual_base), 15, "main target after cancellation")
        ws = websocket.create_connection(main["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        counter = [0]
        try:
            result["cancelText"] = wait_until(
                lambda: (
                    text if "取消" in str(text := evaluate(
                        ws, counter, "document.querySelector('#authStateText')?.textContent || ''"
                    )) else None
                ),
                20,
                "cancel feedback",
            )
        finally:
            ws.close()
        current = next((row for row in top_windows(actual.pid) if row["title"] == "MOKU — Pixiv 标签采集册"), None)
        result["mainResponsive"] = bool(current and not current["hung"])
        result["ok"] = (
            len(result["nativeClicks"]) == 2
            and result["loginWindow"]
            and parts.hostname in {"www.pixiv.net", "accounts.pixiv.net"}
            and bool(result["cancelText"])
            and result["mainResponsive"]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop(calibration, calibration_root)
        stop(actual, actual_root, remove_root=False)
        session_root = actual_root / "localappdata" / "MOKU" / "WebView2Sessions"
        result["sessionProfilesAfterExit"] = len(list(session_root.glob("session-*"))) if session_root.exists() else 0
        result["processExited"] = (
            (calibration is None or calibration.poll() is not None)
            and (actual is None or actual.poll() is not None)
        )
        if result["sessionProfilesAfterExit"] != 0:
            result["ok"] = False
            result["error"] = result["error"] or "temporary WebView2 session profile remained after exit"
        shutil.rmtree(actual_root, ignore_errors=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
