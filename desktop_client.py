from __future__ import annotations

import json
import logging
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
        proxy: str = "",
        *,
        window_factory: Callable = webview.create_window,
        poll_interval: float = 0.6,
        timeout: float = 600.0,
        backend_url: str = "",
        desktop_auth_token: str = "",
        auth_request: Callable | None = None,
    ) -> None:
        self._window = None
        self.proxy = proxy
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

    def pixiv_login(self, remember: bool = False) -> dict:
        if not self._login_lock.acquire(blocking=False):
            return {"ok": False, "error": "Pixiv 登录窗口已经打开"}
        login = None
        last_cookie_summary = None
        try:
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
                LOG.info("desktop auth accepted host=www.pixiv.net cookie_shape=eligible")
                result = self._auth_request(
                    "session", {"session": value, "remember": bool(remember)},
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
        except DesktopLoginCancelled as exc:
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

    def select_folder(self) -> dict:
        if self._window is None:
            return {"selected": "", "cancelled": True, "error": "桌面窗口尚未准备好"}
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        selected = str(result[0]) if result else ""
        return {"selected": selected, "cancelled": not bool(selected)}


def start_desktop(
    url: str,
    storage_path: Path,
    proxy: str = "",
    startup: Callable | None = None,
    *,
    desktop_auth_token: str = "",
) -> None:
    api = DesktopApi(
        proxy,
        backend_url=url,
        desktop_auth_token=desktop_auth_token,
    )
    window = webview.create_window(
        "MOKU — Pixiv 标签采集册",
        url,
        js_api=api,
        width=1280,
        height=820,
        min_size=(900, 620),
        resizable=True,
        background_color="#fbf7ed",
    )
    api._window = window
    storage_path.mkdir(parents=True, exist_ok=True)
    if startup is None:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage_path), debug=False)
    else:
        webview.start(startup, args=[window], gui="edgechromium", private_mode=False, storage_path=str(storage_path), debug=False)
