from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import webview

from pixiv_login import LOGIN, select_session_cookie, session_cookie_metadata
from network_config import normalize_loopback_proxy
from version import __version__


LOG = logging.getLogger("moku.desktop")


class DesktopLoginCancelled(RuntimeError):
    pass


def desktop_auth_request(
    backend_url: str,
    capability: str,
    action: str,
    payload: dict,
    *,
    timeout: float = 8.0,
) -> dict:
    """Send account material directly from the native host to its backend."""
    try:
        parsed = urlsplit(str(backend_url or ""))
        port = parsed.port
    except ValueError:
        return {"ok": False, "error": "桌面后端地址无效"}
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not port
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return {"ok": False, "error": "桌面后端地址无效"}
    secret = str(capability or "")
    if not 24 <= len(secret) <= 256 or not secret.isascii():
        return {"ok": False, "error": "桌面认证能力不可用"}
    route = {
        "session": "api/desktop/auth/session",
        "logout": "api/desktop/auth/logout",
        "context": "api/desktop/auth/context",
    }.get(str(action))
    if route is None:
        return {"ok": False, "error": "桌面认证操作无效"}
    raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(
        str(backend_url).rstrip("/") + "/" + route,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-MOKU-Desktop-Capability": secret,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=max(1.0, min(float(timeout), 15.0))) as response:
            response_raw = response.read(65537)
    except urllib.error.HTTPError as exc:
        response_raw = exc.read(65537)
    except (OSError, ValueError):
        return {"ok": False, "error": "无法连接 MOKU 本机后端"}
    if len(response_raw) > 65536:
        return {"ok": False, "error": "MOKU 本机后端响应过大"}
    try:
        result = json.loads(response_raw)
    except (UnicodeError, json.JSONDecodeError):
        return {"ok": False, "error": "MOKU 本机后端响应无效"}
    if not isinstance(result, dict):
        return {"ok": False, "error": "MOKU 本机后端响应无效"}
    return result


def is_completed_pixiv_login_url(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return False
    path = parsed.path.lower().rstrip("/")
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.pixiv.net"
        and path not in {"/login.php", "/login"}
    )


def desktop_cookie_rows(cookie_containers: list[SimpleCookie]) -> list[dict]:
    """Convert pywebview's SimpleCookie containers into strict Pixiv cookie rows."""
    rows: list[dict] = []
    for container in cookie_containers or []:
        if not isinstance(container, SimpleCookie):
            continue
        for name, morsel in container.items():
            expires_raw = str(morsel["expires"] or "")
            try:
                if " 0001 " in expires_raw:
                    expires = 0.0
                else:
                    expires = parsedate_to_datetime(expires_raw).timestamp()
            except (TypeError, ValueError, OverflowError, OSError):
                expires = 0.0
            rows.append({
                "name": str(name),
                "value": str(morsel.value),
                "domain": str(morsel["domain"] or ""),
                "path": str(morsel["path"] or ""),
                "expires": expires,
                "secure": bool(morsel["secure"]),
                "httpOnly": bool(morsel["httponly"]),
            })
    return rows


class DesktopApi:
    def __init__(
        self,
        *,
        window_factory: Callable = webview.create_window,
        poll_interval: float = 0.6,
        timeout: float = 600.0,
        backend_url: str = "",
        desktop_auth_token: str = "",
        auth_request: Callable | None = None,
        proxy_override: str = "",
    ) -> None:
        self._window = None
        self._proxy_override = proxy_override
        self._window_factory = window_factory
        self.poll_interval = max(0.0, float(poll_interval))
        self.timeout = max(0.01, float(timeout))
        self._login_lock = threading.Lock()
        self._backend_url = str(backend_url or "")
        self._desktop_auth_token = str(desktop_auth_token or "")
        self._auth_request = auth_request or (
            lambda action, payload: desktop_auth_request(
                self._backend_url,
                self._desktop_auth_token,
                action,
                payload,
            )
        )

    def _notify(self, text: str) -> None:
        if self._window is None:
            return
        script = "document.querySelector('#authStateText').textContent=" + json.dumps(text, ensure_ascii=False)
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass

    def _clear_shared_cookies(self) -> None:
        if self._window is None:
            return
        try:
            self._window.clear_cookies()
        except Exception:
            pass

    def _login_network_context(self) -> dict:
        context = self._auth_request("context", {})
        if (not isinstance(context, dict) or context.get("ok") is not True
                or type(context.get("authorizationGeneration")) is not int
                or context.get("proxySource") not in {"environment", "system"}
                or not isinstance(context.get("proxy"), str)):
            raise ValueError("无法读取登录网络状态，请重试")
        expected_proxy = context["proxy"] if context["proxySource"] == "environment" else ""
        if expected_proxy != self._proxy_override:
            raise ValueError("代理设置已变化，请重启 MOKU 后登录")
        return context

    def pixiv_login(self, remember: bool = False) -> dict:
        if not self._login_lock.acquire(blocking=False):
            return {"ok": False, "error": "Pixiv 登录窗口已经打开"}
        login = None
        last_cookie_summary = None
        try:
            context = self._login_network_context()
            self._clear_shared_cookies()
            self._notify("正在桌面登录窗口中监控 Pixiv 授权状态…")
            login = self._window_factory(
                "MOKU — Pixiv 官方登录",
                url=LOGIN,
                width=1040,
                height=820,
                min_size=(720, 620),
                resizable=True,
                background_color="#fbf7ed",
            )
            if login is None:
                raise RuntimeError("无法创建 Pixiv 桌面登录窗口")
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if login.events.closed.is_set():
                    raise DesktopLoginCancelled("已取消 Pixiv 桌面登录")
                if not login.events.loaded.is_set():
                    if self.poll_interval:
                        time.sleep(self.poll_interval)
                    continue
                try:
                    rows = desktop_cookie_rows(login.get_cookies() or [])
                    summary = session_cookie_metadata(rows)
                    if summary != last_cookie_summary:
                        LOG.info(
                            "desktop auth cookie_scan total=%s php=%s eligible=%s session=%s expired=%s",
                            summary["totalRows"],
                            summary["phpRows"],
                            summary["eligibleRows"],
                            summary["sessionRows"],
                            summary["expiredRows"],
                        )
                        last_cookie_summary = summary
                    value = select_session_cookie(rows)
                except (ValueError, RuntimeError):
                    if self.poll_interval:
                        time.sleep(self.poll_interval)
                    continue
                current_url = str(login.get_current_url() or "")
                if not is_completed_pixiv_login_url(current_url):
                    self._notify("已检测到登录 Cookie，等待 Pixiv 返回首页…")
                    if self.poll_interval:
                        time.sleep(self.poll_interval)
                    continue
                self._login_network_context()
                browser_context = None
                try:
                    browser_context = login.evaluate_js("({userAgent:navigator.userAgent,languages:Array.from(navigator.languages)})")
                except Exception:
                    LOG.warning("Login browser metadata unavailable; continuing session authorization")
                LOG.info("desktop auth accepted host=www.pixiv.net cookie_shape=eligible")
                result = self._auth_request(
                    "session", {"session": value, "remember": bool(remember),
                                "authorizationGeneration": context["authorizationGeneration"],
                                "browserContext": browser_context},
                )
                if not isinstance(result, dict) or not result.get("ok"):
                    return {
                        "ok": False,
                        "error": str(
                            (result or {}).get("error")
                            if isinstance(result, dict)
                            else ""
                        ) or "Pixiv 会话未能提交到 MOKU 后端",
                    }
                self._notify("Pixiv 账户已连接。")
                return {"ok": True, "remembered": bool(remember)}
            return {"ok": False, "error": "Pixiv 桌面登录等待超时，请重试"}
        except (DesktopLoginCancelled, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": "Pixiv 桌面登录失败，请重试"}
        finally:
            if login is not None and not login.events.closed.is_set():
                try:
                    login.destroy()
                except Exception:
                    pass
            self._clear_shared_cookies()
            self._login_lock.release()

    def pixiv_logout(self) -> dict:
        result = self._auth_request("logout", {})
        if not isinstance(result, dict):
            return {"ok": False, "error": "MOKU 本机后端响应无效"}
        return result

    def window_action(self, action: str) -> dict:
        if not isinstance(action, str) or action not in {"state", "drag", "minimize", "toggle-maximize", "close"}:
            return {"ok": False, "error": "窗口操作无效"}
        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未准备好"}
        try:
            from System import Func, Object

            def dispatch():
                native = self._window.native
                frame = self._window._native_frame
                if action == "drag":
                    frame.drag()
                elif action == "minimize":
                    frame.command(0xF020)
                elif action == "toggle-maximize":
                    frame.command(0xF120 if frame.maximized else 0xF030)
                elif action == "close":
                    native.Close()
                    return {"ok": True}
                return {"ok": True, "maximized": frame.maximized}

            return self._window.native.Invoke(Func[Object](dispatch))
        except Exception:
            LOG.exception("Desktop window action failed")
            return {"ok": False, "error": "窗口操作失败，请重试"}

    def select_folder(self) -> dict:
        if self._window is None:
            return {"selected": "", "cancelled": True, "error": "桌面窗口尚未准备好"}
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        selected = str(result[0]) if result else ""
        return {"selected": selected, "cancelled": not bool(selected)}

    def _clipboard_transfer(self, text: str | None = None) -> dict:
        if self._window is None:
            return {"ok": False, "error": "桌面窗口尚未准备好"}
        try:
            current = urlsplit(self._window.get_current_url() or "")
            backend = urlsplit(self._backend_url)
            if (
                backend.scheme != "http" or backend.hostname not in {"127.0.0.1", "localhost", "::1"}
                or current.scheme != backend.scheme or current.netloc != backend.netloc
            ):
                return {"ok": False, "error": "剪贴板仅供 MOKU 页面使用"}
            return self._native_clipboard_transfer(text)
        except Exception:
            return {"ok": False, "error": "剪贴板暂时不可用，请重试"}

    def _native_clipboard_transfer(self, text: str | None) -> dict:
        from System import Func, Object
        from System.Windows.Forms import Clipboard

        def transfer():
            if text is None:
                return {"ok": True, "text": str(Clipboard.GetText())}
            Clipboard.SetText(text)
            return {"ok": True}

        # Windows clipboard access requires the native UI (STA) thread.
        return self._window.native.Invoke(Func[Object](transfer))

    def read_clipboard(self) -> dict:
        return self._clipboard_transfer()

    def write_clipboard(self, text: str) -> dict:
        if not isinstance(text, str) or not text or len(text) > 2 * 1024 * 1024:
            return {"ok": False, "error": "没有可复制的文字或文字过长"}
        return self._clipboard_transfer(text)


@contextmanager
def selected_webview_proxy(proxy: str):
    """Use the backend's selected local proxy for every window in this host."""
    if not proxy:
        yield
        return
    clean = normalize_loopback_proxy(proxy)
    if not clean:
        raise ValueError("桌面代理地址无效")
    name = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
    inherited = os.environ.get(name)
    os.environ[name] = (
        f"{inherited or ''} --proxy-server={clean} "
        "--proxy-bypass-list=127.0.0.1;localhost;[::1]"
    ).strip()
    try:
        yield
    finally:
        if inherited is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = inherited


def _fit_main_window(window) -> None:
    """Set a centred restore size, then fill the monitor work area before first show."""
    import ctypes
    from ctypes import wintypes
    from System.Drawing import Size
    from System.Windows.Forms import FormStartPosition, FormWindowState, Screen
    from native_window_frame import NativeWindowFrame

    native = window.native
    native.WindowState = FormWindowState.Normal
    window._native_frame = NativeWindowFrame(native)
    area = Screen.FromHandle(native.Handle).WorkingArea
    get_dpi = ctypes.windll.user32.GetDpiForWindow
    get_dpi.argtypes = [wintypes.HWND]
    get_dpi.restype = wintypes.UINT
    dpi = get_dpi(native.Handle.ToInt64())
    if not dpi:
        raise ctypes.WinError()
    scale = dpi / 96.0
    width = int(min(1180 * scale, area.Width * .88))
    height = int(min(760 * scale, area.Height * .88))
    native.StartPosition = FormStartPosition.Manual
    native.MinimumSize = Size(min(int(480 * scale), width), min(int(360 * scale), height))
    native.SetBounds(area.X + (area.Width - width) // 2,
                     area.Y + (area.Height - height) // 2, width, height)
    native.WindowState = FormWindowState.Maximized


def start_desktop(
    url: str,
    storage_path: Path,
    proxy: str = "",
    startup: Callable | None = None,
    *,
    desktop_auth_token: str = "",
) -> None:
    environment_proxy = normalize_loopback_proxy(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "")
    native_override = environment_proxy if environment_proxy == normalize_loopback_proxy(proxy) else ""
    api = DesktopApi(
        proxy_override=native_override,
        backend_url=url,
        desktop_auth_token=desktop_auth_token,
    )
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    window = webview.create_window(
        f"MOKU Lite v{__version__} — Pixiv 搜图与采集",
        url,
        js_api=api,
        width=1180,
        height=760,
        min_size=(480, 360),
        resizable=True,
        background_color="#fbf7ed",
        text_select=True,
        frameless=True,
        easy_drag=False,
        maximized=True,
    )
    window.events.before_show += _fit_main_window
    api._window = window
    storage_path.mkdir(parents=True, exist_ok=True)
    # Windows proxy selection remains dynamic in the native browser. Only an
    # explicit environment proxy needs bridging: WebView2 ignores HTTPS_PROXY.
    with selected_webview_proxy(native_override):
        if startup is None:
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage_path), debug=False)
        else:
            webview.start(startup, args=[window], gui="edgechromium", private_mode=False, storage_path=str(storage_path), debug=False)
