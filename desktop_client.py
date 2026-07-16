from __future__ import annotations

import json
import logging
import threading
import time
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import webview

from auth_store import store_session
from pixiv_login import LOGIN, select_session_cookie, session_cookie_metadata
from server import mark_authorized_session


LOG = logging.getLogger("moku.desktop")


class DesktopLoginCancelled(RuntimeError):
    pass


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
    ) -> None:
        self._window = None
        self.proxy = proxy
        self._window_factory = window_factory
        self.poll_interval = max(0.0, float(poll_interval))
        self.timeout = max(0.01, float(timeout))
        self._login_lock = threading.Lock()

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
                store_session(value, remember=bool(remember))
                mark_authorized_session()
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
        from server import disconnect_authorized_session
        disconnect_authorized_session()
        return {"ok": True}

    def select_folder(self) -> dict:
        if self._window is None:
            return {"selected": "", "cancelled": True, "error": "桌面窗口尚未准备好"}
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        selected = str(result[0]) if result else ""
        return {"selected": selected, "cancelled": not bool(selected)}


def start_desktop(url: str, storage_path: Path, proxy: str = "", startup: Callable | None = None) -> None:
    api = DesktopApi(proxy)
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