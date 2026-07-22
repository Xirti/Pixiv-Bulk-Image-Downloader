from __future__ import annotations

import json
import copy
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import http.client
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import time
import secrets
import sys
import threading
import weakref
import winreg
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from auth_store import delete_session, session_cookie_header
from folder_picker import select_folder
from network_config import normalize_loopback_proxy
from pixiv_adapter import PixivPolicyError, build_download_context, build_search_url, build_user_profile_all_url, build_user_profile_works_url, build_user_search_url, is_allowed_pixiv_url, matches_tag_groups, normalize_detail, normalize_search_item, resolve_download_target, resolve_web_path, safe_artwork_stem, should_retry_status
from search_service import SearchInputError, SearchPageCache, build_search_tag_groups, parse_search_query, parse_search_tags, plan_download_chunks, prefetch_item_count, resolve_source_modes
from version import __version__

CODE_GENERATION_FILES = (
    "server.py", "auth_store.py", "fixture_gallery.py", "folder_picker.py",
    "pixiv_login.py", "moku_app.py", "desktop_client.py", "network_config.py",
    "pixiv_adapter.py", "search_aliases.py", "search_service.py", "version.py",
    "web/index.html", "web/app.js", "web/style.css",
)


def compute_code_generation(
    *, root: Path | None = None, files: tuple[str, ...] = CODE_GENERATION_FILES,
    executable: Path | None = None, frozen: bool | None = None,
) -> str:
    """Fingerprint the code that may share a persistent loopback backend."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    digest = hashlib.sha256()
    if is_frozen:
        target = Path(executable or sys.executable).resolve()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"exe-sha256:{digest.hexdigest()}"

    source_root = Path(root or Path(__file__).resolve().parent)
    for relative in files:
        path = source_root / relative
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return f"source-sha256:{digest.hexdigest()}"


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(exist_ok=True)
DOWNLOAD_CHUNK_ARTWORKS = 20
DOWNLOAD_CHUNK_PAGES = 200
MAX_HISTORY_REQUESTS = 24
MAX_HISTORY_SECONDS = 45.0
MAX_USER_SEARCH_REQUESTS = 8
MAX_USER_SEARCH_SECONDS = 40.0
SEARCH_PER_PAGE = 36
SEARCH_PREFETCH_AHEAD = 3
SEARCH_KEEP_BEHIND = 6
MAX_SEARCH_SESSIONS = 12
MAX_HISTORY_SOURCES = 32
MAX_IMAGE_TOKENS = 4096
MAX_PIXIV_CACHE_ITEMS = 256
PROTOCOL_VERSION = 5
APPLICATION_ID = "MOKU.PixivTagGallery"
TEST_FIXTURES_ENABLED = os.getenv("MOKU_ENABLE_TEST_FIXTURES") == "1"
CODE_GENERATION = (os.getenv("MOKU_CODE_GENERATION") or compute_code_generation()) + (
    ":fixtures" if TEST_FIXTURES_ENABLED else ""
)


def fixture_records(tag: str) -> list[dict]:
    if not TEST_FIXTURES_ENABLED:
        raise RuntimeError("fixture routes are disabled")
    from fixture_gallery import records

    return records(tag)


def fixture_artwork_svg(index: int, page: int, size: str) -> bytes:
    if not TEST_FIXTURES_ENABLED:
        raise RuntimeError("fixture routes are disabled")
    from fixture_gallery import artwork_svg

    return artwork_svg(index, page, size)
INSTANCE_ID = os.getenv("MOKU_INSTANCE_ID") or secrets.token_hex(16)
REQUEST_TOKEN = secrets.token_urlsafe(32)
FOLDER_PICKER_LOCK = threading.Lock()
# Network reads remain concurrent. This lock covers only the final local
# publish/exact-object rollback transaction shared by request threads.
PUBLISH_TRANSACTION_LOCK = threading.Lock()
HTTP_LOG = logging.getLogger("moku.http")


def _is_loopback_host(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = urllib.parse.urlsplit("//" + text)
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return False
    return (parsed.hostname or "").lower().rstrip(".") in {"127.0.0.1", "localhost", "::1"}


def trusted_local_request(handler) -> bool:
    try:
        if handler.client_address[0] not in {"127.0.0.1", "::1"}:
            return False
    except (AttributeError, IndexError, TypeError):
        return False
    request_host = str(handler.headers.get("Host") or "").strip()
    if not _is_loopback_host(request_host):
        return False
    if str(handler.headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        return False
    origin = str(handler.headers.get("Origin") or "").strip()
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlparse(origin)
        return (
            parsed.scheme == "http"
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and str(parsed.netloc).lower() == request_host.lower()
            and _is_loopback_host(parsed.netloc)
        )
    except ValueError:
        return False


def validate_mutating_request(handler) -> tuple[int, str] | None:
    """Authorize browser and local automation POST requests before side effects."""
    if not trusted_local_request(handler):
        return 403, "请求只允许本机同源界面调用"
    supplied = str(handler.headers.get("X-MOKU-Request-Token") or "")
    if not supplied or not hmac.compare_digest(supplied, REQUEST_TOKEN):
        return 403, "请求授权令牌无效"
    if str(handler.headers.get_content_type() or "").lower() != "application/json":
        return 415, "请求必须使用 application/json"
    return None


def valid_request_token(handler) -> bool:
    supplied = str(handler.headers.get("X-MOKU-Request-Token") or "")
    return bool(supplied) and hmac.compare_digest(supplied, REQUEST_TOKEN)


def health_request_may_disclose_token(handler) -> bool:
    """Only same-origin browser traffic may receive the process capability."""
    if not trusted_local_request(handler):
        return False
    fetch_site = str(handler.headers.get("Sec-Fetch-Site") or "").strip().lower()
    origin = str(handler.headers.get("Origin") or "").strip()
    return fetch_site in {"same-origin", "same-site"} or bool(origin)


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


PIXIV_HEADERS = {"Referer": "https://www.pixiv.net/", "User-Agent": "Mozilla/5.0 PixivTagGallery/0.1", "Accept-Language": "zh-CN,zh;q=0.9"}
MAX_REMOTE_BYTES = 40 * 1024 * 1024
PIXIV_CACHE: OrderedDict[str, dict] = OrderedDict()
IMAGE_TOKENS: dict[str, tuple] = {}
PIXIV_STATE_LOCK = threading.RLock()
HISTORY_CACHE: dict[tuple, dict] = {}
_HISTORY_LOCKS: weakref.WeakValueDictionary[tuple, threading.Lock] = weakref.WeakValueDictionary()
_HISTORY_LOCKS_GUARD = threading.Lock()
SEARCH_PAGE_CACHE = SearchPageCache(keep_behind=SEARCH_KEEP_BEHIND, max_sessions=MAX_SEARCH_SESSIONS)
SEARCH_SESSIONS: OrderedDict[tuple, dict] = OrderedDict()
SEARCH_SOURCE_OFFSETS: dict[tuple, int] = {}
SEARCH_SESSION_LOCKS: weakref.WeakValueDictionary[tuple, threading.Lock] = weakref.WeakValueDictionary()
SEARCH_SESSION_LOCKS_GUARD = threading.RLock()
AUTHORIZATION_GENERATION = 0


class AuthorizationRevokedError(PixivPolicyError):
    pass


def authorization_generation() -> int:
    with SEARCH_SESSION_LOCKS_GUARD:
        return AUTHORIZATION_GENERATION


def assert_authorization_generation(expected: int | None) -> None:
    if expected is None:
        return
    with SEARCH_SESSION_LOCKS_GUARD:
        if AUTHORIZATION_GENERATION != expected:
            raise AuthorizationRevokedError("Pixiv 授权已撤销，请重新发起搜索")


def cache_pixiv_item(item: dict) -> dict:
    artwork_id = str(item.get("id") or "")
    if not artwork_id.isdigit():
        raise PixivPolicyError("无效作品 ID")
    with PIXIV_STATE_LOCK:
        PIXIV_CACHE.pop(artwork_id, None)
        PIXIV_CACHE[artwork_id] = item
        while len(PIXIV_CACHE) > MAX_PIXIV_CACHE_ITEMS:
            PIXIV_CACHE.popitem(last=False)
    return item


def get_cached_pixiv_item(artwork_id: str) -> dict | None:
    with PIXIV_STATE_LOCK:
        item = PIXIV_CACHE.pop(str(artwork_id), None)
        if item is not None:
            PIXIV_CACHE[str(artwork_id)] = item
        return item


def _item_image_tokens_current(item: dict, *, now: float | None = None) -> bool:
    artwork_id = str(item.get("id") or "")
    pages = item.get("pageImages")
    if not artwork_id.isdigit() or not isinstance(pages, list) or not pages:
        return False
    current = time.time() if now is None else float(now)
    with PIXIV_STATE_LOCK:
        for page in pages:
            if not isinstance(page, dict):
                return False
            for quality in ("regular", "original"):
                proxy_url = str(page.get(quality) or "")
                token = urllib.parse.parse_qs(urllib.parse.urlsplit(proxy_url).query).get("token", [""])[0]
                approved = IMAGE_TOKENS.get(token)
                if not approved or approved[0] < current or str(approved[1]) != artwork_id:
                    return False
    return True


def pixiv_item_for_download(artwork_id: str, *, allow_r18: bool) -> dict:
    cached = get_cached_pixiv_item(artwork_id)
    if cached is not None and _item_image_tokens_current(cached):
        return cached
    return pixiv_detail(artwork_id, allow_r18=allow_r18)


def search_session_scope(session_key: tuple) -> str:
    if session_key and session_key[0] == "tags" and len(session_key) >= 3:
        return str(session_key[2])
    if session_key and session_key[0] == "user" and len(session_key) >= 5:
        return str(session_key[4])
    return str(session_key[1]) if len(session_key) >= 2 else ""


def clear_authorized_state() -> None:
    global AUTHORIZATION_GENERATION
    with SEARCH_SESSION_LOCKS_GUARD:
        AUTHORIZATION_GENERATION += 1
        restricted_sessions = [
            session_key for session_key in SEARCH_SESSIONS
            if search_session_scope(session_key) in {"r18", "all"}
        ]
        with PIXIV_STATE_LOCK:
            for token, row in list(IMAGE_TOKENS.items()):
                if len(row) >= 4 and row[3] == "r18": IMAGE_TOKENS.pop(token, None)
            for artwork_id, item in list(PIXIV_CACHE.items()):
                if item.get("restriction") == "r18": PIXIV_CACHE.pop(artwork_id, None)
            for key in list(HISTORY_CACHE):
                if len(key) >= 2 and key[1] == "r18": HISTORY_CACHE.pop(key, None)
        for session_key in restricted_sessions:
            _drop_search_session(session_key)


def disconnect_authorized_session() -> None:
    delete_session(); clear_authorized_state()


def mark_authorized_session() -> None:
    """Compatibility hook: local session presence is the authorization state."""
    return None


def auth_status_snapshot() -> dict:
    session_present = bool(session_cookie_header())
    if not session_present:
        clear_authorized_state()
        return {"loggedIn": False, "sessionPresent": False, "authState": "unauthenticated"}
    mark_authorized_session()
    return {"loggedIn": True, "sessionPresent": True, "authState": "authorized"}


def history_lock_for(*parts) -> threading.Lock:
    key = tuple(str(part) if not isinstance(part, tuple) else part for part in parts)
    with _HISTORY_LOCKS_GUARD:
        lock = _HISTORY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _HISTORY_LOCKS[key] = lock
        return lock


def validated_session(force: bool = False) -> bool:
    if not session_cookie_header():
        clear_authorized_state()
        return False
    mark_authorized_session()
    return True


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PixivPolicyError("Pixiv 响应包含重定向，已按安全策略拒绝")


PIXIV_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirectHandler)
PIXIV_PROXY = ""
PIXIV_NETWORK_FINGERPRINT: tuple[str] | None = None
PIXIV_NETWORK_CHECKED_AT = 0.0
PIXIV_NETWORK_LOCK = threading.Lock()
NETWORK_RECHECK_SECONDS = 1.0


def prune_image_tokens(now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    with PIXIV_STATE_LOCK:
        for token, row in list(IMAGE_TOKENS.items()):
            if not row or row[0] < current:
                IMAGE_TOKENS.pop(token, None)
        overflow = len(IMAGE_TOKENS) - MAX_IMAGE_TOKENS
        if overflow > 0:
            for token, _row in sorted(IMAGE_TOKENS.items(), key=lambda pair: pair[1][0])[:overflow]:
                IMAGE_TOKENS.pop(token, None)


def prune_search_image_tokens(
    session_key: tuple, *, retained_pages: set[int] | None = None,
    replace_page: int | None = None,
) -> None:
    with PIXIV_STATE_LOCK:
        for token, row in list(IMAGE_TOKENS.items()):
            if len(row) < 6 or row[4] != session_key:
                continue
            page = int(row[5])
            if retained_pages is None or page not in retained_pages or page == replace_page:
                IMAGE_TOKENS.pop(token, None)


def image_token_cache_control(row: tuple) -> str:
    return "no-store" if len(row) >= 6 else "private,max-age=3600"


def authorize_image_proxy(
    proxy_url: str, artwork_id: str, restriction: str = "safe", *,
    search_session: tuple | None = None, search_page: int | None = None,
) -> str:
    remote_url = urllib.parse.parse_qs(urllib.parse.urlsplit(proxy_url).query).get("url", [""])[0]
    if not is_allowed_pixiv_url(remote_url, image_only=True):
        raise PixivPolicyError("invalid approved image")
    if restriction not in {"safe", "r18"}: raise PixivPolicyError("invalid image restriction")
    prune_image_tokens()
    token = secrets.token_urlsafe(24)
    row: tuple = (time.time() + 3600, artwork_id, remote_url, restriction)
    if search_session is not None and search_page is not None:
        row += (search_session, max(1, int(search_page)))
    with PIXIV_STATE_LOCK:
        IMAGE_TOKENS[token] = row
    return "/api/pixiv/image?" + urllib.parse.urlencode({"token": token})


def authorize_item_images(
    item: dict, *, search_session: tuple | None = None, search_page: int | None = None,
) -> dict:
    artwork_id = str(item["id"]); restriction = str(item.get("restriction") or "safe")
    if item.get("thumb"):
        item["thumb"] = authorize_image_proxy(
            item["thumb"], artwork_id, restriction,
            search_session=search_session, search_page=search_page,
        )
    for page in item.get("pageImages") or []:
        page["regular"] = authorize_image_proxy(page["regular"], artwork_id, restriction)
        page["original"] = authorize_image_proxy(page["original"], artwork_id, restriction)
    return item


def _update_network_opener(*, max_age: float) -> str:
    global PIXIV_OPENER, PIXIV_PROXY, PIXIV_NETWORK_FINGERPRINT, PIXIV_NETWORK_CHECKED_AT
    with PIXIV_NETWORK_LOCK:
        now = time.monotonic()
        if (
            PIXIV_NETWORK_FINGERPRINT is not None
            and max_age > 0
            and now - PIXIV_NETWORK_CHECKED_AT < max_age
        ):
            return PIXIV_PROXY
        state = windows_proxy_state()
        proxy = normalize_loopback_proxy(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "")
        if not proxy and state.get("proxyEnabled"):
            proxy = normalize_loopback_proxy(state.get("proxyServer", ""))
        fingerprint = (proxy,)
        PIXIV_NETWORK_CHECKED_AT = now
        if fingerprint == PIXIV_NETWORK_FINGERPRINT:
            return PIXIV_PROXY
        proxy_map = {"http": proxy, "https": proxy} if proxy else {}
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxy_map), NoRedirectHandler,
        )
        PIXIV_OPENER = opener
        PIXIV_PROXY = proxy
        PIXIV_NETWORK_FINGERPRINT = fingerprint
        return proxy


def refresh_network_opener() -> str:
    return _update_network_opener(max_age=0.0)


def ensure_network_opener_current() -> str:
    return _update_network_opener(max_age=NETWORK_RECHECK_SECONDS)


def pixiv_request(
    url: str, image_only: bool = False, max_bytes: int = MAX_REMOTE_BYTES,
    session_value: str = "", *, anonymous: bool = False, timeout: float = 25, attempts: int = 2,
) -> tuple[bytes, str]:
    if not is_allowed_pixiv_url(url, image_only=image_only):
        raise PixivPolicyError("不允许的 Pixiv 地址")
    request_headers = dict(PIXIV_HEADERS)
    if session_value:
        if image_only: raise PixivPolicyError("账户会话不得发送到图片CDN")
        request_headers["Cookie"] = f"PHPSESSID={session_value}"
    elif not image_only and not anonymous:
        request_headers.update(session_cookie_header())
    request = urllib.request.Request(url, headers=request_headers)
    last_error = None
    attempt_count = max(1, min(int(attempts), 2))
    request_timeout = max(1.0, min(float(timeout), 25.0))
    for attempt in range(attempt_count):
        try:
            with PIXIV_OPENER.open(request, timeout=request_timeout) as response:
                final_url = response.geturl()
                if not is_allowed_pixiv_url(final_url, image_only=image_only):
                    raise PixivPolicyError("Pixiv 重定向到了不允许的地址")
                content_type = response.headers.get_content_type()
                length = response.headers.get("Content-Length")
                declared_length = None
                if length:
                    try:
                        declared_length = int(length)
                    except (TypeError, ValueError) as exc:
                        raise PixivPolicyError("Pixiv 返回了无效的 Content-Length") from exc
                    if declared_length < 0 or declared_length > max_bytes:
                        raise PixivPolicyError("远程响应过大")
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise PixivPolicyError("远程响应过大")
                if declared_length is not None and len(raw) != declared_length:
                    raise http.client.IncompleteRead(raw, declared_length)
                return raw, content_type
        except urllib.error.HTTPError as exc:
            if not should_retry_status(exc.code) or attempt == attempt_count - 1:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            if attempt == attempt_count - 1:
                raise
            last_error = exc
        time.sleep(.6)
    raise last_error or PixivPolicyError("Pixiv 连接失败")


def pixiv_json(url: str) -> dict:
    raw, content_type = pixiv_request(url, max_bytes=8 * 1024 * 1024)
    if content_type != "application/json":
        raise PixivPolicyError("Pixiv 返回了非 JSON 数据")
    try:
        data = json.loads(raw)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise PixivPolicyError("Pixiv 返回的数据无法解析") from exc
    if not isinstance(data, dict):
        raise PixivPolicyError("Pixiv 返回的数据格式异常")
    if data.get("error"):
        raise PixivPolicyError("Pixiv API 拒绝了当前请求")
    return data


def windows_proxy_state() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0]) == 1
            try: stored_server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
            except FileNotFoundError: stored_server = ""
            server = stored_server
            try: pac = str(winreg.QueryValueEx(key, "AutoConfigURL")[0])
            except FileNotFoundError: pac = ""
    except OSError:
        enabled, server, stored_server, pac = False, "", "", ""
    env_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or ""
    mode = "manual-env" if env_proxy else ("system-proxy" if enabled else "direct-or-tun")
    return {"mode": mode, "proxyEnabled": enabled, "proxyServer": server if enabled else "", "proxyStored": stored_server, "pac": pac if enabled else "", "environmentProxy": bool(env_proxy)}


def network_error_kind(exc: Exception) -> str:
    text = str(exc or "").casefold()
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text or "超时" in text:
        return "timeout"
    if "refused" in text or "actively refused" in text or "拒绝" in text:
        return "refused"
    if "ssl" in text or "tls" in text or "certificate" in text or "证书" in text:
        return "tls"
    if isinstance(exc, urllib.error.HTTPError):
        return "http"
    return "unavailable"


NETWORK_DIAGNOSTIC_TARGETS = (
    ("pixiv", "https://www.pixiv.net/", False),
    ("cdn", "https://i.pximg.net/img-original/img/2021/10/02/18/47/29/93172108_p1.jpg", True),
)


def _run_network_diagnostic_check(target: tuple[str, str, bool]) -> dict:
    name, url, image_only = target
    started = time.monotonic()
    try:
        raw, content_type = pixiv_request(
            url, image_only=image_only, max_bytes=1024 * 1024,
            anonymous=True, timeout=7, attempts=1,
        )
        return {
            "name": name, "ok": True, "contentType": content_type,
            "bytes": len(raw), "ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "name": name, "ok": False, "errorKind": network_error_kind(exc),
            "ms": round((time.monotonic() - started) * 1000),
        }


def run_network_diagnostic_checks() -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(NETWORK_DIAGNOSTIC_TARGETS), thread_name_prefix="moku-net-check") as pool:
        return list(pool.map(_run_network_diagnostic_check, NETWORK_DIAGNOSTIC_TARGETS))


def public_network_state(state: dict, *, selected_proxy: str = "") -> dict:
    """Expose route capability without leaking local proxy addresses or PAC URLs."""
    return {
        "mode": str(state.get("mode") or "direct-or-tun"),
        "proxyEnabled": bool(state.get("proxyEnabled")),
        "environmentProxy": bool(state.get("environmentProxy")),
        "proxySelected": bool(selected_proxy),
        "pacConfigured": bool(state.get("pac")),
    }


def human_network_summary(state: dict, selected_proxy: str, checks: list[dict]) -> dict:
    mode = str(state.get("mode") or "direct-or-tun")
    if selected_proxy:
        route = "Windows 系统代理" if mode == "system-proxy" else "MOKU 环境代理"
    elif state.get("proxyEnabled"):
        route = "系统代理配置不可用"
    else:
        route = "直连 / TUN"
    results = {str(row.get("name")): bool(row.get("ok")) for row in checks}
    pixiv_ok = results.get("pixiv", False)
    cdn_ok = results.get("cdn", False)
    if pixiv_ok and cdn_ok:
        headline = "当前网络可以使用 Pixiv"
        guidance = "Pixiv 主站和图片线路均已匿名测试通过。"
    elif pixiv_ok:
        headline = "Pixiv 主站可用，但图片线路异常"
        guidance = "搜索可能可用，但缩略图和下载可能失败；请检查代理是否同时代理 i.pximg.net。"
    elif state.get("proxyEnabled") and not selected_proxy:
        headline = "系统代理已开启，但 MOKU 无法使用该配置"
        guidance = "MOKU 只接受本机 HTTP 代理入口；请确认代理地址是 127.0.0.1 或 localhost，且端口正在运行。"
    else:
        headline = "当前网络无法连接 Pixiv"
        guidance = "请开启可用的 Windows 系统代理或 TUN 全局模式，再重新检测。"
    return {"routeLabel": route, "headline": headline, "guidance": guidance}


def history_cache_key(tag: str, mode: str, namespace: tuple | None = None) -> tuple:
    return (tag, mode) if namespace is None else ("search", namespace, tag, mode)


def _history_state(tag: str, mode: str, namespace: tuple | None = None) -> dict:
    key = history_cache_key(tag, mode, namespace)
    with SEARCH_SESSION_LOCKS_GUARD:
        state = HISTORY_CACHE.get(key)
        if state is None:
            state = {
                "items": [], "ids": set(), "queue": [], "nextEnd": date.today(),
                "baseOffset": 0, "exhausted": False, "budgetExhausted": False,
                "truncatedDates": [], "touched": time.monotonic(),
            }
            HISTORY_CACHE[key] = state
        state["touched"] = time.monotonic()
        if len(HISTORY_CACHE) > MAX_HISTORY_SOURCES:
            with _HISTORY_LOCKS_GUARD:
                active_keys = set(_HISTORY_LOCKS)
            candidates = sorted(
                (row.get("touched", 0.0), cache_key)
                for cache_key, row in HISTORY_CACHE.items()
                if cache_key != key and cache_key not in active_keys
            )
            for _touched, stale_key in candidates[: len(HISTORY_CACHE) - MAX_HISTORY_SOURCES]:
                HISTORY_CACHE.pop(stale_key, None)
        return state


def _queue_older_window(state: dict) -> None:
    if state["exhausted"]: return
    epoch = date(2007, 9, 10); end = state["nextEnd"]
    if end < epoch:
        state["exhausted"] = True; return
    start = max(epoch, end - timedelta(days=29))
    state["queue"].append({"start": start, "end": end, "initialized": False})
    state["nextEnd"] = start - timedelta(days=1)


def extend_history(
    tag: str,
    mode: str,
    need_count: int,
    allow_r18: bool = False,
    *,
    max_requests: int = MAX_HISTORY_REQUESTS,
    max_seconds: float = MAX_HISTORY_SECONDS,
    budget: dict | None = None,
    namespace: tuple | None = None,
) -> dict:
    cache_key = history_cache_key(tag, mode, namespace)
    with history_lock_for(*cache_key):
        state = _history_state(tag, mode, namespace)
        state["budgetExhausted"] = False
        if budget is None:
            budget = {"started": time.monotonic(), "requests": 0}

        def budget_available() -> bool:
            return budget["requests"] < max_requests and time.monotonic() - budget["started"] < max_seconds

        def consume_request() -> None:
            budget["requests"] += 1

        while state.get("baseOffset", 0) + len(state["items"]) < need_count and not state["exhausted"]:
            if not budget_available():
                state["budgetExhausted"] = True
                break
            if not state["queue"]: _queue_older_window(state)
            if not state["queue"]: break
            window = state["queue"][0]
            if not window["initialized"]:
                consume_request()
                block = (pixiv_json(build_search_url(tag, 1, mode=mode, start_date=window["start"], end_date=window["end"])).get("body") or {}).get("illustManga") or {}
                total = int(block.get("total") or 0)
                first_data = block.get("data") or []
                if mode == "r18" and total > 0 and not any(int(row.get("xRestrict", -1)) == 1 for row in first_data):
                    raise PixivPolicyError("账户设置不允许R-18搜索，或Pixiv未返回可用R-18内容")
                if total > 600 and window["start"] < window["end"]:
                    span = (window["end"] - window["start"]).days
                    mid = window["start"] + timedelta(days=span // 2)
                    newer = {"start": mid + timedelta(days=1), "end": window["end"], "initialized": False}
                    older = {"start": window["start"], "end": mid, "initialized": False}
                    state["queue"][0:1] = [newer, older]; continue
                pages = min(10, max(0, int(block.get("lastPage") or 0)))
                window.update({"initialized": True, "page": 1, "pages": pages, "firstRows": first_data})
                if total > 600:
                    state["truncatedDates"].append(window["start"].isoformat())
            if window["page"] > window["pages"]:
                state["queue"].pop(0); continue
            if window.get("firstRows") is not None:
                rows = window.pop("firstRows")
            else:
                if not budget_available():
                    state["budgetExhausted"] = True
                    break
                consume_request()
                block = (pixiv_json(build_search_url(tag, window["page"], mode=mode, start_date=window["start"], end_date=window["end"])).get("body") or {}).get("illustManga") or {}
                rows = block.get("data") or []
            for raw in rows:
                artwork_id = str(raw.get("id") or "")
                if not artwork_id or artwork_id in state["ids"]: continue
                if mode == "r18" and int(raw.get("xRestrict", -1)) != 1: continue
                try: normalize_search_item(raw, allow_r18=allow_r18)
                except PixivPolicyError: continue
                state["ids"].add(artwork_id); state["items"].append(raw)
            window["page"] += 1
            if window["page"] > window["pages"]: state["queue"].pop(0)
        return state


def reset_search_caches() -> None:
    with SEARCH_SESSION_LOCKS_GUARD, PIXIV_STATE_LOCK:
        for token, row in list(IMAGE_TOKENS.items()):
            if len(row) >= 6:
                IMAGE_TOKENS.pop(token, None)
        HISTORY_CACHE.clear()
        SEARCH_PAGE_CACHE.clear()
        SEARCH_SESSIONS.clear()
        SEARCH_SOURCE_OFFSETS.clear()


def _drop_search_session(session_key: tuple) -> None:
    with SEARCH_SESSION_LOCKS_GUARD:
        prune_search_image_tokens(session_key)
        SEARCH_SESSIONS.pop(session_key, None)
        SEARCH_PAGE_CACHE.drop(session_key)
        for source_key in [key for key in SEARCH_SOURCE_OFFSETS if key[0] == session_key]:
            SEARCH_SOURCE_OFFSETS.pop(source_key, None)
        for history_key in [
            key for key in HISTORY_CACHE
            if isinstance(key, tuple) and len(key) == 4 and key[0] == "search" and key[1] == session_key
        ]:
            HISTORY_CACHE.pop(history_key, None)


def search_session_lock(session_key: tuple) -> threading.Lock:
    with SEARCH_SESSION_LOCKS_GUARD:
        lock = SEARCH_SESSION_LOCKS.get(session_key)
        if lock is None:
            lock = threading.Lock()
            SEARCH_SESSION_LOCKS[session_key] = lock
        return lock


def _touch_search_session(session_key: tuple) -> dict:
    with SEARCH_SESSION_LOCKS_GUARD:
        session = SEARCH_SESSIONS.pop(session_key, None)
        if session is None:
            session = {
                "items": [], "seen": set(), "baseIndex": 0,
                "sourceDone": {}, "budgetExhausted": False, "truncatedDates": set(),
            }
        SEARCH_SESSIONS[session_key] = session
        while len(SEARCH_SESSIONS) > MAX_SEARCH_SESSIONS:
            active_keys = set(SEARCH_SESSION_LOCKS)
            stale_key = next((
                key for key in SEARCH_SESSIONS
                if key != session_key and key not in active_keys
            ), None)
            if stale_key is None:
                break
            _drop_search_session(stale_key)
        return session


def _trim_history_source(session_key: tuple, tag: str, mode: str) -> None:
    with SEARCH_SESSION_LOCKS_GUARD:
        state = HISTORY_CACHE.get(history_cache_key(tag, mode, session_key))
        if not state:
            return
        offsets = [
            offset for (_session, source_tag, source_mode), offset in SEARCH_SOURCE_OFFSETS.items()
            if _session == session_key and source_tag == tag and source_mode == mode
        ]
    if not offsets:
        return
    base = int(state.get("baseOffset", 0))
    keep_from = max(base, min(offsets) - SEARCH_KEEP_BEHIND * SEARCH_PER_PAGE)
    remove_count = min(len(state["items"]), keep_from - base)
    if remove_count <= 0:
        return
    removed = state["items"][:remove_count]
    del state["items"][:remove_count]
    state["baseOffset"] = base + remove_count
    retained_ids = {str(row.get("id") or "") for row in state["items"]}
    for row in removed:
        artwork_id = str(row.get("id") or "")
        if artwork_id and artwork_id not in retained_ids:
            state["ids"].discard(artwork_id)


def load_search_source(
    session_key: tuple,
    tag: str,
    mode: str,
    need_count: int,
    allow_r18: bool,
    budget: dict,
) -> dict:
    state = extend_history(
        tag, mode, need_count, allow_r18=allow_r18, budget=budget,
        namespace=session_key,
    )
    source_key = (session_key, tag, mode)
    base = int(state.get("baseOffset", 0))
    with SEARCH_SESSION_LOCKS_GUARD:
        offset = max(base, int(SEARCH_SOURCE_OFFSETS.get(source_key, base)))
    start = min(len(state["items"]), max(0, offset - base))
    rows = list(state["items"][start:])
    with SEARCH_SESSION_LOCKS_GUARD:
        SEARCH_SOURCE_OFFSETS[source_key] = base + len(state["items"])
    has_more = not state["exhausted"] or bool(state["queue"])
    result = {
        "rows": rows,
        "hasMore": has_more,
        "budgetExhausted": bool(state.get("budgetExhausted")),
        "truncatedDates": list(state.get("truncatedDates") or []),
    }
    _trim_history_source(session_key, tag, mode)
    return result


def _search_sort_key(item: dict) -> tuple[str, int]:
    try:
        artwork_number = int(item.get("id") or 0)
    except (TypeError, ValueError):
        artwork_number = 0
    return str(item.get("date") or ""), artwork_number


def _user_rows(payload: object) -> list[dict]:
    """Extract Pixiv user rows, including the real userPreviews[].user shape."""
    if isinstance(payload, list):
        result: list[dict] = []
        for value in payload:
            if not isinstance(value, dict):
                continue
            nested = value.get("user")
            result.append(nested if isinstance(nested, dict) else value)
        return result
    if not isinstance(payload, dict):
        return []
    nested_user = payload.get("user")
    if isinstance(nested_user, dict):
        return [nested_user]
    for key in ("data", "users", "userPreviews"):
        rows = _user_rows(payload.get(key))
        if rows:
            return rows
    return []


def _author_rows_from_payload(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise PixivPolicyError("Pixiv 画师搜索结果格式异常")
    page = payload.get("page")
    users = payload.get("users")
    if not isinstance(page, dict) or not isinstance(users, (list, dict)):
        raise PixivPolicyError("Pixiv 画师搜索结果格式异常")
    ordered_ids = page.get("userIds")
    if not isinstance(ordered_ids, list) or len(ordered_ids) > 100:
        raise PixivPolicyError("Pixiv 画师搜索结果格式异常")
    allowed_ids = {
        str(user_id) for user_id in ordered_ids
        if str(user_id).isascii() and str(user_id).isdigit()
    }
    rows = users.values() if isinstance(users, dict) else users
    normalized_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or row.get("userId") or "")
        if row_id in allowed_ids:
            normalized_rows.append(row)
    return normalized_rows


def resolve_author_user(author: str) -> tuple[str, str]:
    body = pixiv_json(build_user_search_url(author)).get("body") or {}
    rows = _author_rows_from_payload(body)
    target = str(author).strip().casefold()
    exact = next((
        row for row in rows
        if str(row.get("name") or row.get("userName") or "").strip().casefold() == target
    ), None)
    if exact is None:
        raise SearchInputError(f"未找到名称完全匹配“{author}”的 Pixiv 画师；可改用 pid:数字 精确搜索")
    user_id = str(exact.get("userId") or exact.get("id") or "")
    if not user_id.isascii() or not user_id.isdigit():
        raise SearchInputError("Pixiv 未返回有效的画师用户 ID")
    return user_id, str(exact.get("name") or exact.get("userName") or author)


def load_user_profile_ids(user_id: str) -> list[str]:
    body = pixiv_json(build_user_profile_all_url(user_id)).get("body") or {}
    if not isinstance(body, dict):
        raise PixivPolicyError("Pixiv 画师作品索引格式异常")
    ids: set[str] = set()
    for category in ("illusts", "manga"):
        rows = body.get(category)
        candidates = rows.keys() if isinstance(rows, dict) else (rows if isinstance(rows, list) else [])
        for artwork_id in candidates:
            clean = str(artwork_id or "")
            if clean.isascii() and clean.isdigit():
                ids.add(clean)
    return sorted(ids, key=int, reverse=True)


def load_user_profile_works(user_id: str, artwork_ids: list[str]) -> list[dict]:
    body = pixiv_json(build_user_profile_works_url(user_id, artwork_ids)).get("body") or {}
    works = body.get("works") if isinstance(body, dict) else None
    if works is None and isinstance(body, dict):
        works = body
    rows = list(works.values()) if isinstance(works, dict) else (works if isinstance(works, list) else [])
    return [row for row in rows if isinstance(row, dict)]


def search_user_results(
    query_kind: str, target: str, scope: str, page: int,
    work_type: str, include_ai: bool, *, authorized: bool, fuzzy: bool = False,
    authorization_epoch: int | None = None,
) -> dict:
    resolve_source_modes(scope, authorized=authorized)
    user_id, resolved_name = resolve_author_user(target) if query_kind == "author" else (target, "")
    page = max(1, int(page))
    session_key = (
        "user", query_kind, target.casefold(), user_id,
        scope, work_type, bool(include_ai), bool(fuzzy),
    )
    desired_items = prefetch_item_count(page, per_page=SEARCH_PER_PAGE, ahead=SEARCH_PREFETCH_AHEAD)

    with search_session_lock(session_key):
        session = _touch_search_session(session_key)
        if "profileIds" not in session or session.get("targetUserId") != user_id:
            session["profileIds"] = load_user_profile_ids(user_id)
            session["profileOffset"] = 0
            session["targetUserId"] = user_id
            session["artist"] = resolved_name
        ids = session["profileIds"]
        request_started = time.monotonic()
        request_count = 0
        while (
            len(session["items"]) < desired_items
            and int(session["profileOffset"]) < len(ids)
            and request_count < MAX_USER_SEARCH_REQUESTS
            and time.monotonic() - request_started < MAX_USER_SEARCH_SECONDS
        ):
            start = int(session["profileOffset"])
            batch_ids = ids[start:start + 48]
            session["profileOffset"] = start + len(batch_ids)
            raw_rows = load_user_profile_works(user_id, batch_ids)
            request_count += 1
            incoming: list[dict] = []
            for raw in raw_rows:
                if str(raw.get("userId") or "") != user_id:
                    continue
                restriction = int(raw.get("xRestrict", -1))
                if scope == "safe" and restriction != 0:
                    continue
                if scope == "r18" and restriction != 1:
                    continue
                try:
                    candidate = normalize_search_item(raw, allow_r18=scope in {"r18", "all"})
                except PixivPolicyError:
                    continue
                if work_type != "all" and candidate["workType"] != work_type:
                    continue
                if not include_ai and candidate["aiGenerated"]:
                    continue
                incoming.append(candidate)
            incoming.sort(key=_search_sort_key, reverse=True)
            for candidate in incoming:
                if candidate["id"] not in session["seen"]:
                    session["seen"].add(candidate["id"])
                    session["items"].append(candidate)

        loaded = len(session["items"])
        exhausted = int(session["profileOffset"]) >= len(ids)
        budget_exhausted = not exhausted and (
            request_count >= MAX_USER_SEARCH_REQUESTS
            or time.monotonic() - request_started >= MAX_USER_SEARCH_SECONDS
        )
        complete_through = loaded // SEARCH_PER_PAGE
        requested_page_start = (page - 1) * SEARCH_PER_PAGE
        if loaded > requested_page_start:
            complete_through = max(complete_through, page)
        elif (exhausted or budget_exhausted) and loaded:
            complete_through = (loaded + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE
        cache_through = min(page + SEARCH_PREFETCH_AHEAD, complete_through)
        page_rows = {
            number: session["items"][(number - 1) * SEARCH_PER_PAGE:number * SEARCH_PER_PAGE]
            for number in range(1, cache_through + 1)
        }
        SEARCH_PAGE_CACHE.store_pages(session_key, page, page_rows)
        selected = SEARCH_PAGE_CACHE.get_page(session_key, page) or []
        available_pages = SEARCH_PAGE_CACHE.available_pages(session_key)
        prune_search_image_tokens(session_key, retained_pages=set(available_pages), replace_page=page)
        authorized_items = [
            authorize_item_images(copy.deepcopy(item), search_session=session_key, search_page=page)
            for item in selected
        ]
        if scope in {"r18", "all"}:
            try:
                assert_authorization_generation(authorization_epoch)
            except AuthorizationRevokedError:
                prune_search_image_tokens(session_key)
                _drop_search_session(session_key)
                raise
        artist = str(session.get("artist") or resolved_name or f"Pixiv 用户 {user_id}")
        return {
            "tag": target, "tags": [], "label": artist,
            "artist": artist, "searchType": query_kind, "targetUserId": user_id, "scope": scope,
            "total": loaded, "reportedTotal": len(ids), "page": page,
            "pages": available_pages[-1] if available_pages else 1,
            "pageNumbers": available_pages, "availablePages": available_pages,
            "preloadedThrough": SEARCH_PAGE_CACHE.preloaded_through(session_key),
            "items": authorized_items, "perPage": SEARCH_PER_PAGE, "hasMore": not exhausted,
            "budgetExhausted": budget_exhausted, "truncatedDates": [],
            "workType": work_type, "includeAi": bool(include_ai),
            "mode": "pixiv-user-search",
        }


def search_pixiv_results(
    tag_query: str,
    scope: str,
    page: int,
    work_type: str,
    include_ai: bool,
    *,
    authorized: bool,
    fuzzy: bool = False,
    authorization_epoch: int | None = None,
) -> dict:
    if work_type not in {"all", "illustration", "manga", "ugoira"}:
        raise SearchInputError("不支持的作品类型")
    query = parse_search_query(tag_query)
    if query.kind in {"pid", "author"}:
        return search_user_results(
            query.kind, query.value, scope, page, work_type, include_ai,
            authorized=authorized, fuzzy=fuzzy, authorization_epoch=authorization_epoch,
        )
    tags = parse_search_tags(query.value)
    tag_groups = build_search_tag_groups(query.value, fuzzy=fuzzy)
    modes = resolve_source_modes(scope, authorized=authorized)
    page = max(1, int(page))
    session_key = ("tags", tag_groups, scope, work_type, bool(include_ai), bool(fuzzy))
    desired_items = prefetch_item_count(
        page, per_page=SEARCH_PER_PAGE, ahead=SEARCH_PREFETCH_AHEAD,
    )

    with search_session_lock(session_key):
        session = _touch_search_session(session_key)
        first_retained_page = int(session["baseIndex"]) // SEARCH_PER_PAGE + 1
        if page < first_retained_page:
            _drop_search_session(session_key)
            session = _touch_search_session(session_key)
        absolute_loaded = int(session["baseIndex"]) + len(session["items"])
        budget = {"started": time.monotonic(), "requests": 0}
        source_tags = tuple(dict.fromkeys(alias for group in tag_groups for alias in group))
        sources = [(tag, mode) for tag in source_tags for mode in modes]
        rounds = 0
        while absolute_loaded < desired_items and rounds < 8:
            missing = desired_items - absolute_loaded
            per_source = max(SEARCH_PER_PAGE, (missing * 2 + len(sources) - 1) // len(sources))
            incoming: list[dict] = []
            any_more = False
            any_rows = False
            for tag, mode in sources:
                source_key = (tag, mode)
                if session["sourceDone"].get(source_key):
                    continue
                with SEARCH_SESSION_LOCKS_GUARD:
                    absolute_offset = int(SEARCH_SOURCE_OFFSETS.get((session_key, tag, mode), 0))
                source = load_search_source(
                    session_key, tag, mode, absolute_offset + per_source,
                    mode == "r18", budget,
                )
                session["budgetExhausted"] = session["budgetExhausted"] or source["budgetExhausted"]
                session["truncatedDates"].update(source["truncatedDates"])
                session["sourceDone"][source_key] = not source["hasMore"]
                any_more = any_more or source["hasMore"]
                any_rows = any_rows or bool(source["rows"])
                for raw in source["rows"]:
                    try:
                        candidate = normalize_search_item(raw, allow_r18=mode == "r18")
                    except PixivPolicyError:
                        continue
                    if work_type != "all" and candidate["workType"] != work_type:
                        continue
                    if not include_ai and candidate["aiGenerated"]:
                        continue
                    incoming.append(candidate)
            incoming.sort(key=_search_sort_key, reverse=True)
            for candidate in incoming:
                if not matches_tag_groups(candidate.get("tags") or [], tag_groups):
                    continue
                artwork_id = candidate["id"]
                if artwork_id in session["seen"]:
                    continue
                session["seen"].add(artwork_id)
                session["items"].append(candidate)
            absolute_loaded = int(session["baseIndex"]) + len(session["items"])
            rounds += 1
            if not any_rows or (not any_more and not incoming):
                break
            if budget["requests"] >= MAX_HISTORY_REQUESTS:
                session["budgetExhausted"] = True
                break

        first_available_page = int(session["baseIndex"]) // SEARCH_PER_PAGE + 1
        absolute_result_count = int(session["baseIndex"]) + len(session["items"])
        complete_through = absolute_result_count // SEARCH_PER_PAGE
        requested_page_start = (page - 1) * SEARCH_PER_PAGE
        if absolute_result_count > requested_page_start:
            complete_through = max(complete_through, page)
        elif (
            (session["budgetExhausted"] or not any(not done for done in session["sourceDone"].values()))
            and session["items"]
        ):
            complete_through = max(
                complete_through,
                (absolute_result_count + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE,
            )
        cache_through = min(page + SEARCH_PREFETCH_AHEAD, complete_through)
        page_rows: dict[int, list] = {}
        for page_number in range(first_available_page, cache_through + 1):
            start_absolute = (page_number - 1) * SEARCH_PER_PAGE
            start = start_absolute - int(session["baseIndex"])
            if start < 0:
                continue
            page_rows[page_number] = session["items"][start:start + SEARCH_PER_PAGE]
        SEARCH_PAGE_CACHE.store_pages(session_key, page, page_rows)
        selected = SEARCH_PAGE_CACHE.get_page(session_key, page) or []
        available_pages = SEARCH_PAGE_CACHE.available_pages(session_key)

        oldest_page = max(1, page - SEARCH_KEEP_BEHIND)
        trim_to = (oldest_page - 1) * SEARCH_PER_PAGE
        if trim_to > int(session["baseIndex"]):
            remove_count = min(len(session["items"]), trim_to - int(session["baseIndex"]))
            del session["items"][:remove_count]
            session["baseIndex"] += remove_count
            session["seen"] = {str(item["id"]) for item in session["items"]}

        has_more = any(not done for done in session["sourceDone"].values())
        prune_search_image_tokens(
            session_key, retained_pages=set(available_pages), replace_page=page,
        )
        authorized_items = [
            authorize_item_images(copy.deepcopy(item), search_session=session_key, search_page=page)
            for item in selected
        ]
        if scope in {"r18", "all"}:
            try:
                assert_authorization_generation(authorization_epoch)
            except AuthorizationRevokedError:
                prune_search_image_tokens(session_key)
                _drop_search_session(session_key)
                raise
        return {
            "tag": "；".join(tags), "tags": list(tags), "tagGroups": [list(group) for group in tag_groups], "fuzzy": bool(fuzzy), "scope": scope,
            "total": int(session["baseIndex"]) + len(session["items"]), "reportedTotal": None,
            "page": page, "pages": available_pages[-1] if available_pages else 1,
            "pageNumbers": available_pages, "availablePages": available_pages,
            "preloadedThrough": SEARCH_PAGE_CACHE.preloaded_through(session_key),
            "items": authorized_items, "perPage": SEARCH_PER_PAGE, "hasMore": has_more,
            "budgetExhausted": bool(session["budgetExhausted"]),
            "truncatedDates": sorted(session["truncatedDates"], reverse=True),
            "workType": work_type, "includeAi": bool(include_ai), "fuzzy": bool(fuzzy),
            "mode": "pixiv-authorized-all" if scope == "all" else (
                "pixiv-authorized-r18" if scope == "r18" else "pixiv-public-history"
            ),
        }


def pixiv_detail(artwork_id: str, allow_r18: bool = False) -> dict:
    if not artwork_id.isdigit():
        raise PixivPolicyError("无效作品 ID")
    detail = pixiv_json(f"https://www.pixiv.net/ajax/illust/{artwork_id}?lang=zh").get("body") or {}
    pages = pixiv_json(f"https://www.pixiv.net/ajax/illust/{artwork_id}/pages?lang=zh").get("body") or []
    item = authorize_item_images(normalize_detail(detail, pages, allow_r18=allow_r18))
    return cache_pixiv_item(item)


IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def image_extension(content_type: str) -> str:
    extension = IMAGE_EXTENSIONS.get(str(content_type or "").split(";", 1)[0].strip().lower())
    if not extension:
        raise PixivPolicyError("不支持的图片格式")
    return extension


class RequestInputError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)


# URLError, TimeoutError, and ConnectionError remain covered by OSError.
PIXIV_OPERATION_ERRORS = (
    PixivPolicyError,
    OSError,
    http.client.HTTPException,
    json.JSONDecodeError,
    UnicodeError,
    TypeError,
    ValueError,
    KeyError,
)


def public_pixiv_error(action: str, exc: Exception, *, saving: bool = False) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {401, 403}:
            detail = "Pixiv 拒绝了访问，请检查登录状态或作品权限"
        elif exc.code == 429:
            detail = "Pixiv 请求过于频繁，请稍后重试"
        else:
            detail = "Pixiv 暂时返回异常响应"
    elif isinstance(exc, PixivPolicyError):
        detail = str(exc) or "请求不符合安全策略"
    elif isinstance(exc, (json.JSONDecodeError, UnicodeError, TypeError, ValueError, KeyError)):
        detail = "Pixiv 返回的数据格式异常"
    else:
        labels = {
            "timeout": "连接超时",
            "refused": "连接被拒绝",
            "tls": "证书或 TLS 连接异常",
            "http": "Pixiv 暂时返回异常响应",
            "unavailable": "网络连接中断或不可用",
        }
        detail = labels.get(network_error_kind(exc), "网络连接中断或不可用")
        if (
            saving
            and network_error_kind(exc) == "unavailable"
            and isinstance(exc, OSError)
            and not isinstance(
                exc, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
            )
        ):
            detail = "目标目录不可写、磁盘空间不足或文件被占用"
    return f"{action}失败：{detail}"


def approved_image_url(proxy_url: str, artwork_id: str) -> str:
    token = urllib.parse.parse_qs(urllib.parse.urlsplit(str(proxy_url)).query).get("token", [""])[0]
    with PIXIV_STATE_LOCK:
        approved = IMAGE_TOKENS.get(token)
        if not approved or approved[0] < time.time() or str(approved[1]) != str(artwork_id):
            raise PixivPolicyError("图片授权无效")
        approved = tuple(approved)
    if len(approved) >= 4 and approved[3] == "r18" and not validated_session():
        raise PixivPolicyError("R-18 图片授权已失效")
    return str(approved[2])


def stage_artwork_pages(
    item: dict, selected_pages: list[int], quality: str, save_root: Path,
    create_folder: bool, staging_root: Path, *, download_context: dict | None = None,
    group_artwork: bool = False,
) -> list[PublishedFileOwnership]:
    artwork_id = str(item.get("id") or "")
    page_images = item.get("pageImages")
    if not artwork_id.isdigit() or not isinstance(page_images, list):
        raise PixivPolicyError("作品详情不完整")
    folder = resolve_download_target(
        save_root, str(item.get("title") or ""), artwork_id, create_folder,
        context=download_context, group_artwork=group_artwork,
    )
    stem = safe_artwork_stem(str(item.get("title") or ""), artwork_id)
    staged: list[PublishedFileOwnership] = []
    try:
        for page_no in selected_pages:
            if page_no < 0 or page_no >= len(page_images):
                raise PixivPolicyError("图片页码超出范围")
            page = page_images[page_no]
            if not isinstance(page, dict) or quality not in page:
                raise PixivPolicyError("作品图片信息不完整")
            remote_url = approved_image_url(str(page[quality]), artwork_id)
            raw, content_type = pixiv_request(remote_url, image_only=True)
            # Re-check the capability after the network request. Logout can revoke
            # an R-18 token while bytes are in flight; revoked data must not publish.
            if approved_image_url(str(page[quality]), artwork_id) != remote_url:
                raise PixivPolicyError("图片授权已失效")
            extension = image_extension(content_type)
            final = folder / f"{stem}_p{page_no}.{extension}"
            # Creation, write, flush, digest, publication, and rollback all use
            # one zero-share handle. No close-and-reopen staging window exists.
            staged.append(_create_owned_staged_file(staging_root, final, raw))
        return staged
    except Exception as original:
        failures = _discard_owned_staging(staged)
        if failures:
            raise PublishRollbackError(
                f"暂存失败且有 {failures} 个文件未能安全清理"
            ) from original
        raise


def _is_link_or_reparse(path: Path) -> bool:
    target = Path(path)
    if target.is_symlink() or bool(getattr(target, "is_junction", lambda: False)()):
        return True
    if os.name != "nt" or not target.exists():
        return False
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(target))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x400)


def _lexical_absolute(path: Path) -> Path:
    """Normalize dot segments without following links or reparse points."""
    return Path(os.path.abspath(os.fspath(path)))


def _reject_reparse_components(path: Path, message: str) -> Path:
    """Reject every existing component before any filesystem write occurs."""
    candidate = _lexical_absolute(path)
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if _is_link_or_reparse(current):
            raise PixivPolicyError(message)
    return candidate


def _validated_publish_parent(
    save_root: Path,
    final: Path,
    *,
    created_dirs: list[Path] | None = None,
    retained_handles: list[int] | None = None,
) -> Path:
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    # Lexical containment is followed by handle-based component validation.
    # Do not resolve(): doing so follows a junction before it can be rejected.
    root = _lexical_absolute(save_root)
    candidate = _lexical_absolute(final)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PixivPolicyError("保存路径超出目标目录") from exc

    handles = _directory_lock_chain(root, [root])
    current = root
    retain = False
    try:
        for part in relative.parts[:-1]:
            child = current / part
            created = False
            try:
                handle = _open_directory_handle(
                    child, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
                )
            except FileNotFoundError:
                try:
                    child.mkdir()
                    created = True
                    if created_dirs is not None:
                        created_dirs.append(child)
                except FileExistsError:
                    pass
                handle = _open_directory_handle(
                    child, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
                )
            try:
                _handle_identity(handle)
            except Exception:
                _close_windows_handle(handle)
                raise
            handles.append(handle)
            current = child
        result = current / relative.name
        if retained_handles is not None:
            retained_handles.extend(handles)
            handles = []
            retain = True
        return result
    finally:
        if not retain:
            _close_directory_lock_chain(handles)


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


FILE_TRAVERSE = 0x0020
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE_ACCESS = 0x00100000
DELETE_ACCESS = 0x00010000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
FILE_SHARE_ALL = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
WINDOWS_SECURE_PUBLICATION = os.name == "nt" and hasattr(ctypes, "windll")
FILE_DISPOSITION_INFO_CLASS = 4
FILE_RENAME_INFO_CLASS = 3


class FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOL)]


class FILE_RENAME_INFO(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOL),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


def _open_directory_handle(
    path: Path,
    *,
    share_mode: int = FILE_SHARE_ALL,
    access: int = FILE_TRAVERSE | FILE_READ_ATTRIBUTES,
) -> int:
    return _open_windows_handle(
        path,
        share_mode=share_mode,
        access=access,
        flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
    )


def _open_windows_handle(
    path: Path,
    *,
    share_mode: int,
    access: int,
    flags: int,
) -> int:
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path), access,
        share_mode, None, OPEN_EXISTING,
        flags, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _delete_empty_directory_on_close(handle: int) -> None:
    disposition = FILE_DISPOSITION_INFO(True)
    set_information = ctypes.windll.kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    if not set_information(
        handle,
        FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise ctypes.WinError()


def _rename_file_by_handle(handle: int, destination: Path) -> None:
    encoded = str(destination).encode("utf-16-le")
    name_offset = FILE_RENAME_INFO.FileName.offset
    # FILE_RENAME_INFO includes FileName[1] and ABI tail padding. Supplying only
    # FIELD_OFFSET + FileNameLength lets the kernel consume adjacent memory as
    # random filename suffixes. Over-allocate the complete structure plus name.
    buffer_size = ctypes.sizeof(FILE_RENAME_INFO) + len(encoded)
    buffer = ctypes.create_string_buffer(buffer_size)
    info = ctypes.cast(buffer, ctypes.POINTER(FILE_RENAME_INFO)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))

    set_information = ctypes.windll.kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    if not set_information(
        handle, FILE_RENAME_INFO_CLASS, buffer, buffer_size,
    ):
        raise ctypes.WinError()


def _handle_identity(handle: int) -> tuple[int, int, int]:
    info = _raw_handle_information(handle)
    if not info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        raise PixivPolicyError("保存路径组件不是目录")
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PixivPolicyError("保存目录包含链接或重解析点")
    return _identity_from_handle_information(info)


def _raw_handle_information(handle: int) -> BY_HANDLE_FILE_INFORMATION:
    info = BY_HANDLE_FILE_INFORMATION()
    get_info = ctypes.windll.kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL
    if not get_info(handle, ctypes.byref(info)):
        raise ctypes.WinError()
    return info


def _identity_from_handle_information(
    info: BY_HANDLE_FILE_INFORMATION,
) -> tuple[int, int, int]:
    return (
        int(info.dwVolumeSerialNumber),
        (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        int(info.dwFileAttributes),
    )


@dataclass(frozen=True)
class PublishedFileSnapshot:
    identity: tuple[int, int]
    size: int
    sha256: str


@dataclass
class PublishedFileOwnership:
    final: Path
    handle: int | None
    snapshot: PublishedFileSnapshot | None = None
    staged_handle: int | None = None
    staged_snapshot: PublishedFileSnapshot | None = None
    published: bool = False


class PublishedOwnershipError(PixivPolicyError):
    """A published path could not be proven to still contain our staged file."""


def _hash_windows_file_handle(handle: int) -> str:
    set_pointer = ctypes.windll.kernel32.SetFilePointerEx
    set_pointer.argtypes = [
        wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_pointer.restype = wintypes.BOOL
    if not set_pointer(handle, 0, None, 0):
        raise ctypes.WinError()

    read_file = ctypes.windll.kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    read_file.restype = wintypes.BOOL
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        count = wintypes.DWORD()
        if not read_file(handle, buffer, len(buffer), ctypes.byref(count), None):
            raise ctypes.WinError()
        if count.value == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[:count.value])


def _snapshot_windows_file_handle(handle: int) -> PublishedFileSnapshot:
    info = _raw_handle_information(handle)
    if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        raise PixivPolicyError("发布目标不是普通文件")
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PixivPolicyError("发布目标不能是链接或重解析点")
    return PublishedFileSnapshot(
        identity=(
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        ),
        size=(int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
        sha256=_hash_windows_file_handle(handle),
    )


def _own_staged_file(path: Path, final: Path) -> PublishedFileOwnership:
    """Snapshot a staging file while retaining exclusive ownership until commit."""
    handle = _open_windows_handle(
        path,
        share_mode=0,
        access=(
            FILE_READ_DATA | FILE_WRITE_DATA
            | FILE_READ_ATTRIBUTES | DELETE_ACCESS
        ),
        flags=FILE_FLAG_OPEN_REPARSE_POINT,
    )
    try:
        snapshot = _snapshot_windows_file_handle(handle)
        return PublishedFileOwnership(
            final=final,
            handle=handle,
            snapshot=snapshot,
            staged_handle=handle,
            staged_snapshot=snapshot,
        )
    except Exception:
        _close_windows_handle(handle)
        raise


def _write_windows_file_handle(handle: int, raw: bytes) -> None:
    write_file = ctypes.windll.kernel32.WriteFile
    write_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    write_file.restype = wintypes.BOOL
    view = memoryview(raw)
    for offset in range(0, len(view), 1024 * 1024):
        chunk = bytes(view[offset:offset + 1024 * 1024])
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not write_file(
            handle, buffer, len(chunk), ctypes.byref(written), None,
        ):
            raise ctypes.WinError()
        if written.value != len(chunk):
            raise OSError("暂存文件写入不完整")
    flush = ctypes.windll.kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    if not flush(handle):
        raise ctypes.WinError()


def _create_owned_staged_file(
    staging_root: Path,
    final: Path,
    raw: bytes,
) -> PublishedFileOwnership:
    """Create, write, verify, and retain one zero-share staging file object."""
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    for _attempt in range(32):
        path = staging_root / f".page-{secrets.token_hex(16)}.part"
        handle = create_file(
            str(path),
            FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | DELETE_ACCESS,
            0,
            None,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            error = ctypes.WinError()
            if isinstance(error, FileExistsError):
                continue
            raise error
        owned_handle = int(handle)
        try:
            _write_windows_file_handle(owned_handle, raw)
            snapshot = _snapshot_windows_file_handle(owned_handle)
            return PublishedFileOwnership(
                final=final,
                handle=owned_handle,
                snapshot=snapshot,
                staged_handle=owned_handle,
                staged_snapshot=snapshot,
            )
        except Exception as original:
            cleanup_error: Exception | None = None
            try:
                _delete_empty_directory_on_close(owned_handle)
            except Exception as error:
                cleanup_error = error
            finally:
                _close_windows_handle(owned_handle)
            if cleanup_error is not None:
                raise PublishRollbackError(
                    "暂存失败且恢复文件保留在临时目录"
                ) from original
            raise
    raise PublishedOwnershipError("无法分配安全暂存文件名")


def _close_published_ownership(ownership: PublishedFileOwnership) -> None:
    closed: set[int] = set()
    for attribute in ("staged_handle", "handle"):
        handle = getattr(ownership, attribute)
        if handle is not None and handle not in closed:
            _close_windows_handle(handle)
            closed.add(handle)
        setattr(ownership, attribute, None)


def _delete_owned_published_file(ownership: PublishedFileOwnership) -> None:
    if ownership.handle is None:
        raise PublishedOwnershipError("发布目标所有权句柄已关闭")
    handle = ownership.handle
    try:
        _delete_empty_directory_on_close(handle)
    finally:
        _close_windows_handle(handle)
        ownership.handle = None
        if ownership.staged_handle == handle:
            ownership.staged_handle = None
    ownership.published = False


def _rollback_owned_files(
    ownerships: list[PublishedFileOwnership],
) -> list[Exception]:
    failures: list[Exception] = []
    for ownership in reversed(ownerships):
        try:
            if ownership.published:
                _delete_owned_published_file(ownership)
            elif ownership.staged_handle is not None:
                _delete_empty_directory_on_close(ownership.staged_handle)
        except Exception as exc:
            failures.append(exc)
        finally:
            _close_published_ownership(ownership)
    return failures


def _discard_owned_staging(
    ownerships: list[PublishedFileOwnership],
) -> int:
    return len(_rollback_owned_files(ownerships))


def _directory_lock_chain(root: Path, paths: list[Path]) -> list[int]:
    """Lock a validated root-to-leaf directory tree against retargeting.

    Handles are function-local, acquired in deterministic root-first order, and
    closed by the caller. Windows share-mode enforcement owns synchronization;
    no mutable Python state is shared between concurrent publications.
    """
    boundary = _lexical_absolute(root)
    anchor = Path(boundary.anchor)
    directories: dict[str, Path] = {os.path.normcase(str(anchor)): anchor}
    current = anchor
    for part in boundary.parts[1:]:
        current /= part
        directories[os.path.normcase(str(current))] = current
    for raw_path in paths:
        candidate = _lexical_absolute(raw_path)
        try:
            relative = candidate.relative_to(boundary)
        except ValueError as exc:
            raise PixivPolicyError("保存路径超出目标目录") from exc
        current = boundary
        for part in relative.parts:
            current /= part
            directories[os.path.normcase(str(current))] = current

    ordered = sorted(
        directories.values(),
        key=lambda path: (len(path.parts), os.path.normcase(str(path))),
    )
    handles: list[int] = []
    try:
        for current in ordered:
            handle = _open_directory_handle(
                current, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            )
            try:
                _handle_identity(handle)
            except Exception:
                _close_windows_handle(handle)
                raise
            handles.append(handle)
    except Exception:
        for handle in reversed(handles):
            _close_windows_handle(handle)
        raise
    return handles


def _close_directory_lock_chain(handles: list[int]) -> None:
    for handle in reversed(handles):
        _close_windows_handle(handle)


def _remove_publish_directory(path: Path, *, boundary_root: Path) -> None:
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    locks = _directory_lock_chain(boundary_root, [path.parent])
    handle: int | None = None
    try:
        handle = _open_directory_handle(
            path,
            share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            access=FILE_TRAVERSE | FILE_READ_ATTRIBUTES | DELETE_ACCESS,
        )
        _handle_identity(handle)
        _delete_empty_directory_on_close(handle)
    finally:
        if handle is not None:
            _close_windows_handle(handle)
        _close_directory_lock_chain(locks)


class PublishRollbackError(PixivPolicyError):
    """Publication failed and one or more owned outputs could not be removed."""


class SecureStagingDirectory:
    """Hold the selected Windows directory identity for one download lifecycle."""

    def __init__(self, save_root: Path, *, prefix: str) -> None:
        self.save_root = _lexical_absolute(save_root)
        self.prefix = prefix
        self.path: Path | None = None
        self.cleanup_pending = False
        self._root_handles: list[int] = []
        self._staging_handle: int | None = None

    def __enter__(self) -> Path:
        if not WINDOWS_SECURE_PUBLICATION:
            raise PixivPolicyError("安全文件发布仅支持 Windows")
        if not self.save_root.is_dir():
            raise PixivPolicyError("保存根目录不存在")
        self._root_handles = _directory_lock_chain(
            self.save_root, [self.save_root],
        )
        try:
            self.path = Path(tempfile.mkdtemp(
                prefix=self.prefix, dir=str(self.save_root),
            ))
            self._staging_handle = _open_directory_handle(
                self.path,
                share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            )
            _handle_identity(self._staging_handle)
            return self.path
        except Exception as original:
            cleanup_error: Exception | None = None
            try:
                if self._staging_handle is not None:
                    _close_windows_handle(self._staging_handle)
                    self._staging_handle = None
                if self.path is not None and self.path.exists():
                    if _is_link_or_reparse(self.path) or not self.path.is_dir():
                        raise PixivPolicyError("临时目录初始化后身份异常")
                    # No image bytes exist before __enter__ returns. rmdir is
                    # deliberately non-recursive and runs while the root is locked.
                    self.path.rmdir()
            except Exception as error:
                cleanup_error = error
            finally:
                self._close_handles()
            if cleanup_error is not None:
                raise PixivPolicyError("临时目录初始化失败且空目录清理失败") from original
            raise

    def _close_handles(self) -> None:
        if self._staging_handle is not None:
            _close_windows_handle(self._staging_handle)
            self._staging_handle = None
        if self._root_handles:
            _close_directory_lock_chain(self._root_handles)
            self._root_handles = []

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        preserve = isinstance(exc, PublishRollbackError)
        cleanup_error: Exception | None = None
        try:
            if not preserve and self.path is not None:
                # Staging is deliberately flat. Delete only ordinary files while
                # both root and staging identities remain locked; never recurse
                # through an unexpected directory or reparse point.
                for child in self.path.iterdir():
                    if _is_link_or_reparse(child) or not child.is_file():
                        raise PixivPolicyError("临时目录包含非普通文件")
                    child.unlink()
        except Exception as error:
            cleanup_error = error
        finally:
            if self._staging_handle is not None:
                _close_windows_handle(self._staging_handle)
                self._staging_handle = None
        try:
            if not preserve and cleanup_error is None and self.path is not None:
                # The directory is known empty. Non-recursive rmdir cannot follow
                # an attacker path; the locked root chain still prevents retargeting.
                self.path.rmdir()
        except Exception as error:
            cleanup_error = error
        finally:
            if self._root_handles:
                _close_directory_lock_chain(self._root_handles)
                self._root_handles = []
        if cleanup_error is not None and exc is None:
            # Publication and response-path construction already committed. A
            # best-effort staging cleanup failure must not turn success into 502.
            self.cleanup_pending = True
            HTTP_LOG.warning("下载已提交；临时目录清理待处理")
        elif cleanup_error is not None:
            raise PixivPolicyError("下载失败且临时目录清理失败") from (exc or cleanup_error)
        return False


def secure_staging_directory(save_root: Path, *, prefix: str) -> SecureStagingDirectory:
    return SecureStagingDirectory(save_root, prefix=prefix)


def _same_file_content(
    left: PublishedFileSnapshot,
    right: PublishedFileSnapshot,
) -> bool:
    return left.size == right.size and hmac.compare_digest(left.sha256, right.sha256)


def _publish_owned_staged_file(
    ownership: PublishedFileOwnership,
    final: Path,
    *,
    boundary_root: Path,
    prelocked_parent: Path | None = None,
) -> None:
    """Publish one exclusively owned staging file without reopening it by path."""
    if ownership.staged_handle is None or ownership.staged_snapshot is None:
        raise PublishedOwnershipError("暂存文件所有权缺失")
    parents = [final.parent]
    if prelocked_parent is not None:
        locked = os.path.normcase(str(_lexical_absolute(prelocked_parent)))
        parents = [
            parent for parent in parents
            if os.path.normcase(str(_lexical_absolute(parent))) != locked
        ]
    locks = _directory_lock_chain(boundary_root, parents)
    try:
        try:
            target_handle = _open_windows_handle(
                final,
                share_mode=0,
                access=FILE_READ_DATA | FILE_READ_ATTRIBUTES,
                flags=FILE_FLAG_OPEN_REPARSE_POINT,
            )
        except FileNotFoundError:
            # Register ownership before the kernel rename. If a competitor wins
            # this name, fall through to the collision-safe sibling loop.
            ownership.final = final
            ownership.handle = ownership.staged_handle
            ownership.snapshot = ownership.staged_snapshot
            ownership.published = True
            try:
                _rename_file_by_handle(ownership.staged_handle, final)
                return
            except FileExistsError:
                ownership.published = False
        else:
            try:
                current = _snapshot_windows_file_handle(target_handle)
                if _same_file_content(current, ownership.staged_snapshot):
                    # Idempotent download: retain the existing complete file and
                    # leave the duplicate staging object for context cleanup.
                    ownership.final = final
                    ownership.handle = target_handle
                    ownership.snapshot = current
                    target_handle = None
                    return
            finally:
                if target_handle is not None:
                    _close_windows_handle(target_handle)

        # Never overwrite a different existing file. Publish the verified
        # staging object under a collision-safe sibling name using the same
        # zero-share handle and a no-replace kernel rename.
        for collision in range(1, 10_001):
            candidate = final.with_name(
                f"{final.stem} ({collision}){final.suffix}",
            )
            ownership.final = candidate
            ownership.handle = ownership.staged_handle
            ownership.snapshot = ownership.staged_snapshot
            ownership.published = True
            try:
                _rename_file_by_handle(ownership.staged_handle, candidate)
                return
            except FileExistsError:
                ownership.published = False
                continue
        raise PublishedOwnershipError("同名下载文件过多，无法分配安全文件名")
    finally:
        _close_directory_lock_chain(locks)


def publish_staged_files(
    staging_root: Path,
    staged: list[tuple[Path, Path] | PublishedFileOwnership],
    *,
    save_root: Path | None = None,
    staging_locked: bool = False,
    public_paths: bool = False,
) -> list[Path] | list[str]:
    """Serialize one complete local publish/rollback transaction.

    Network reads and staging have already completed. The lock covers every
    mutable destination operation, preventing concurrent request threads from
    deleting each other's published bytes.
    """
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    if save_root is None:
        raise PixivPolicyError("安全文件发布必须绑定保存根目录")
    with PUBLISH_TRANSACTION_LOCK:
        return _publish_staged_files_locked(
            staging_root, staged, save_root=save_root,
            staging_locked=staging_locked, public_paths=public_paths,
        )


def _publish_staged_files_locked(
    staging_root: Path,
    staged: list[tuple[Path, Path] | PublishedFileOwnership],
    *,
    save_root: Path | None = None,
    staging_locked: bool = False,
    public_paths: bool = False,
) -> list[Path] | list[str]:
    """Publish a completed batch and restore every owned object on failure."""
    transactions: list[PublishedFileOwnership] = [
        entry for entry in staged
        if isinstance(entry, PublishedFileOwnership)
    ]
    created_dirs: list[Path] = []
    retained_parent_handles: list[int] = []
    try:
        for entry in staged:
            if isinstance(entry, PublishedFileOwnership):
                ownership = entry
                final = ownership.final
            else:
                temporary, final = entry
                ownership = None
            if save_root is not None:
                final = _validated_publish_parent(
                    save_root,
                    final,
                    created_dirs=created_dirs,
                    retained_handles=retained_parent_handles,
                )
            elif not final.parent.exists():
                final.parent.mkdir(parents=True, exist_ok=True)
            if ownership is None:
                ownership = _own_staged_file(temporary, final)
                transactions.append(ownership)
            else:
                ownership.final = final
                if ownership.staged_handle is None or ownership.staged_snapshot is None:
                    raise PublishedOwnershipError("暂存文件所有权已关闭")
            # Register before the first public mutation. Even a helper that raises
            # after its kernel operation leaves the current item rollback-capable.
            _publish_owned_staged_file(
                ownership,
                final,
                boundary_root=save_root,
                prelocked_parent=staging_root if staging_locked else None,
            )
            if (
                ownership.staged_handle is not None
                and ownership.staged_handle != ownership.handle
            ):
                _delete_empty_directory_on_close(ownership.staged_handle)
        saved = [ownership.final for ownership in transactions]
        # Compute response values while every published file handle remains open.
        # No committed path is resolved after ownership is released.
        if public_paths:
            return public_saved_files(save_root, saved)
        return saved
    except Exception as original:
        rollback_failures = _rollback_owned_files(transactions)
        if retained_parent_handles:
            _close_directory_lock_chain(retained_parent_handles)
            retained_parent_handles = []
        for directory in reversed(created_dirs):
            try:
                _remove_publish_directory(directory, boundary_root=save_root)
            except Exception as exc:
                rollback_failures.append(exc)
        if rollback_failures:
            raise PublishRollbackError(
                f"发布失败且有 {len(rollback_failures)} 项未能安全恢复；恢复文件保留在临时目录"
            ) from original
        raise
    finally:
        for ownership in transactions:
            _close_published_ownership(ownership)
        if retained_parent_handles:
            _close_directory_lock_chain(retained_parent_handles)


def public_saved_files(save_root: Path, saved: list[Path]) -> list[str]:
    root = _lexical_absolute(save_root)
    result: list[str] = []
    for path in saved:
        candidate = _lexical_absolute(path)
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise PixivPolicyError("保存结果超出目标目录") from exc
        if not relative.parts or relative.is_absolute():
            raise PixivPolicyError("保存结果不是有效文件路径")
        result.append(relative.as_posix())
    return result


def _stage_and_publish_download(
    save_root: Path,
    *,
    prefix: str,
    stage: Callable[[Path, list[PublishedFileOwnership]], None],
) -> tuple[list[str], bool]:
    """Stage a complete request, then transfer the whole batch to publication."""
    staging_context = secure_staging_directory(save_root, prefix=prefix)
    with staging_context as staging_root:
        staged: list[PublishedFileOwnership] = []
        try:
            stage(staging_root, staged)
        except Exception as original:
            failures = _discard_owned_staging(staged)
            if failures:
                raise PublishRollbackError(
                    f"暂存失败且有 {failures} 个文件未能安全清理"
                ) from original
            raise

        # Calling the publisher transfers ownership of the complete batch,
        # including entries it has not visited yet.
        public_saved = publish_staged_files(
            staging_root,
            staged,
            save_root=save_root,
            staging_locked=True,
            public_paths=True,
        )
    return public_saved, staging_context.cleanup_pending


class Handler(SimpleHTTPRequestHandler):
    request_body_timeout = 15.0

    def setup(self):
        super().setup()
        self.connection.settimeout(self.request_body_timeout)

    def log_message(self, _format, *args):
        safe_path = urllib.parse.urlsplit(str(self.path or "")).path or "/"
        status = str(args[1]) if len(args) > 1 else "-"
        size = str(args[2]) if len(args) > 2 else "-"
        HTTP_LOG.info("%s %s %s bytes=%s", self.command, safe_path, status, size)

    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def translate_path(self, path):
        try:
            return str(resolve_web_path(WEB, path))
        except PixivPolicyError:
            return str(WEB / "__not_found__")

    def send_json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            return

    def send_bytes(self, raw: bytes, content_type: str, cache_control: str) -> bool:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            return False

    def read_json_object(self, max_bytes: int) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise RequestInputError(400, "请求不支持 Transfer-Encoding")
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            raise RequestInputError(411, "请求必须提供一个 Content-Length")
        try:
            length = int(lengths[0])
        except (TypeError, ValueError) as exc:
            raise RequestInputError(400, "Content-Length 无效") from exc
        if length < 0:
            raise RequestInputError(400, "Content-Length 不能为负数")
        if length > max(0, int(max_bytes)):
            raise RequestInputError(413, "请求正文过大")
        try:
            raw = self.rfile.read(length)
        except TimeoutError as exc:
            raise RequestInputError(408, "读取请求正文超时") from exc
        if len(raw) != length:
            raise RequestInputError(400, "请求正文不完整")
        try:
            data = json.loads(raw or b"{}")
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RequestInputError(400, "请求正文不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise RequestInputError(400, "请求正文必须是 JSON 对象")
        return data

    def _reject_untrusted_api_get(self, path: str) -> bool:
        if not path.startswith("/api/"):
            return False
        if not trusted_local_request(self):
            self.send_json({"error": "请求只允许本机同源界面调用"}, 403)
            return True
        image_capability = path == "/api/pixiv/image" or bool(
            TEST_FIXTURES_ENABLED and re.fullmatch(r"/api/image/\d+/\d+", path)
        )
        if path != "/api/health" and not image_capability and not valid_request_token(self):
            self.send_json({"error": "请求授权令牌无效"}, 403)
            return True
        return False

    def do_GET(self):
        request = urllib.parse.urlparse(self.path)
        if self._reject_untrusted_api_get(request.path):
            return
        if request.path == "/api/health":
            payload = {
                "ok": True,
                "applicationId": APPLICATION_ID,
                "codeGeneration": CODE_GENERATION,
                "instanceId": INSTANCE_ID,
                "protocolVersion": PROTOCOL_VERSION,
                "version": __version__,
            }
            if health_request_may_disclose_token(self):
                payload["requestToken"] = REQUEST_TOKEN
            return self.send_json(payload)
        if request.path == "/api/status":
            auth = auth_status_snapshot()
            logged_in = bool(auth["loggedIn"])
            return self.send_json({
                **auth,
                "mode": "pixiv-authorized" if logged_in else "pixiv-public",
                "authAvailable": True,
                "r18Available": logged_in,
                "message": "Pixiv 本机授权会话" if logged_in else "Pixiv 公开全年龄模式",
                "network": public_network_state(windows_proxy_state()),
            })
        if request.path == "/api/network/diagnose":
            state = windows_proxy_state()
            selected_proxy = refresh_network_opener()
            checks = run_network_diagnostic_checks()
            result = public_network_state(state, selected_proxy=selected_proxy)
            result["checks"] = checks
            result["summary"] = human_network_summary(state, selected_proxy, checks)
            return self.send_json(result)
        if request.path.startswith("/api/pixiv/"):
            ensure_network_opener_current()
        if request.path == "/api/pixiv/search":
            return self._get_pixiv_search(request)
        artwork_match = re.fullmatch(r"/api/pixiv/artwork/(\d+)", request.path)
        if artwork_match:
            return self._get_pixiv_detail(artwork_match.group(1))
        if request.path == "/api/pixiv/image":
            return self._get_pixiv_image(request)
        if request.path == "/api/search":
            if not TEST_FIXTURES_ENABLED:
                return self.send_json({"error": "not found"}, 404)
            query = urllib.parse.parse_qs(request.query)
            tag = query.get("tag", ["原创"])[0][:60]
            try:
                page = max(1, int(query.get("page", ["1"])[0]))
            except ValueError:
                page = 1
            all_items = fixture_records(tag)
            per_page = 12
            start = (page - 1) * per_page
            return self.send_json({
                "tag": tag, "total": len(all_items), "page": page,
                "pages": 2, "items": all_items[start:start + per_page],
            })
        image_match = re.fullmatch(r"/api/image/(\d+)/(\d+)", request.path)
        if image_match:
            if not TEST_FIXTURES_ENABLED:
                return self.send_json({"error": "not found"}, 404)
            index, page = map(int, image_match.groups())
            size = urllib.parse.parse_qs(request.query).get("size", ["preview"])[0]
            if size not in {"original", "large", "preview"}:
                size = "preview"
            return self.send_bytes(
                fixture_artwork_svg(index, page, size), "image/svg+xml", "public,max-age=86400",
            )
        super().do_GET()

    def _get_pixiv_search(self, request):
        query = urllib.parse.parse_qs(request.query)
        tag_query = query.get("tag", ["原创"])[0]
        search_scope = query.get("mode", ["safe"])[0]
        if search_scope in {"r18", "all"} and not validated_session():
            return self.send_json({"error": "R-18搜索需要先完成Pixiv账户授权"}, 403)
        work_type = query.get("workType", ["all"])[0]
        include_ai = query.get("includeAi", ["false"])[0].lower() == "true"
        fuzzy = query.get("fuzzy", ["false"])[0].lower() == "true"
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        try:
            session_authorized = validated_session()
            authorization_epoch = authorization_generation() if search_scope in {"r18", "all"} else None
            result = search_pixiv_results(
                tag_query, search_scope, page, work_type, include_ai,
                authorized=session_authorized, fuzzy=fuzzy,
                authorization_epoch=authorization_epoch,
            )
            return self.send_json(result)
        except SearchInputError as exc:
            status = 403 if "授权" in str(exc) else 400
            return self.send_json({"error": str(exc)}, status)
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json({"error": public_pixiv_error("Pixiv 搜索", exc)}, 502)

    def _get_pixiv_detail(self, artwork_id: str):
        try:
            return self.send_json(
                pixiv_detail(artwork_id, allow_r18=validated_session()),
            )
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json({"error": public_pixiv_error("作品详情", exc)}, 502)

    def _get_pixiv_image(self, request):
        token = urllib.parse.parse_qs(request.query).get("token", [""])[0]
        with PIXIV_STATE_LOCK:
            approved = IMAGE_TOKENS.get(token)
            if not approved or approved[0] < time.time():
                IMAGE_TOKENS.pop(token, None)
                approved = None
            elif len(approved) >= 4 and approved[3] == "r18" and not validated_session():
                approved = None
            else:
                approved = tuple(approved)
        if approved is None:
            return self.send_json({"error": "图片授权已失效"}, 403)
        try:
            raw, content_type = pixiv_request(str(approved[2]), image_only=True)
            with PIXIV_STATE_LOCK:
                still_approved = IMAGE_TOKENS.get(token)
                if still_approved != approved or approved[0] < time.time():
                    return self.send_json({"error": "图片授权已失效"}, 403)
            if len(approved) >= 4 and approved[3] == "r18" and not validated_session():
                return self.send_json({"error": "R-18 图片授权已失效"}, 403)
            if not content_type.startswith("image/"):
                raise PixivPolicyError("Pixiv 返回的不是图片")
            return self.send_bytes(raw, content_type, image_token_cache_control(approved))
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json({"error": public_pixiv_error("图片代理", exc)}, 502)

    def do_POST(self):
        authorization_error = validate_mutating_request(self)
        if authorization_error:
            return self.send_json({"error": authorization_error[1]}, authorization_error[0])
        path = urllib.parse.urlsplit(self.path).path
        routes = {
            "/api/auth/logout": (4096, self._post_logout),
            "/api/system/select-folder": (4096, self._post_select_folder),
            "/api/pixiv/batch-download": (65536, self._post_pixiv_batch_download),
            "/api/pixiv/download": (16384, self._post_pixiv_download),
        }
        if TEST_FIXTURES_ENABLED:
            routes["/api/download"] = (16384, self._post_fixture_download)
        route = routes.get(path)
        if route is None:
            return self.send_json({"error": "not found"}, 404)
        try:
            data = self.read_json_object(route[0])
        except RequestInputError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        if path.startswith("/api/pixiv/"):
            ensure_network_opener_current()
        return route[1](data)

    def _post_logout(self, _data: dict):
        disconnect_authorized_session()
        return self.send_json({"ok": True})

    def _post_select_folder(self, data: dict):
        if not FOLDER_PICKER_LOCK.acquire(blocking=False):
            return self.send_json({"error": "已有目录选择窗口正在打开"}, 409)
        try:
            result = select_folder(str(data.get("initial") or ""))
            return self.send_json(result, 500 if result.get("error") else 200)
        finally:
            FOLDER_PICKER_LOCK.release()

    @staticmethod
    def _save_root(data: dict) -> Path:
        raw = str(data.get("saveRoot") or "").strip()
        root = Path(raw).expanduser() if raw else DOWNLOADS
        if not root.is_absolute():
            raise RequestInputError(400, "保存位置必须是绝对路径")
        try:
            candidate = _reject_reparse_components(
                root, "保存根目录不能是链接或重解析点",
            )
        except PixivPolicyError as exc:
            raise RequestInputError(400, str(exc)) from exc
        if not candidate.is_dir():
            raise RequestInputError(400, "保存根目录不存在或不是文件夹")
        return candidate

    @staticmethod
    def _download_options(data: dict) -> tuple[str, bool]:
        quality = str(data.get("quality") or "regular")
        create_folder = data.get("createFolder", True)
        if quality not in {"original", "regular"}:
            raise RequestInputError(400, "图片质量无效")
        if not isinstance(create_folder, bool):
            raise RequestInputError(400, "createFolder 必须是布尔值")
        return quality, create_folder

    @staticmethod
    def _download_context(data: dict, *, required: bool = False) -> dict | None:
        raw = data.get("context")
        if raw is None and not required:
            return None
        if not isinstance(raw, dict):
            raise RequestInputError(400, "下载目录上下文无效")
        try:
            return build_download_context(raw.get("kind"), raw.get("value"))
        except PixivPolicyError as exc:
            raise RequestInputError(400, "下载目录上下文无效") from exc

    @staticmethod
    def _normalized_download_groups(groups: object) -> OrderedDict[str, set[int]]:
        if not isinstance(groups, list) or not 1 <= len(groups) <= DOWNLOAD_CHUNK_ARTWORKS:
            raise RequestInputError(400, "批量选择范围无效")
        normalized: OrderedDict[str, set[int]] = OrderedDict()
        for group in groups:
            if not isinstance(group, dict):
                raise RequestInputError(400, "批量作品格式无效")
            artwork_id = str(group.get("id") or "")
            pages = group.get("pages")
            if not artwork_id.isdigit() or not isinstance(pages, list) or not pages:
                raise RequestInputError(400, "作品或图片页码无效")
            if any(not isinstance(page, int) or isinstance(page, bool) or page < 0 for page in pages):
                raise RequestInputError(400, "图片页码无效")
            normalized.setdefault(artwork_id, set()).update(pages)
        return normalized

    def _post_pixiv_batch_download(self, data: dict):
        try:
            quality, create_folder = self._download_options(data)
            save_root = self._save_root(data)
            download_context = self._download_context(data, required=create_folder)
            group_artworks = data.get("groupArtworks", False)
            if not isinstance(group_artworks, bool):
                raise RequestInputError(400, "groupArtworks 必须是布尔值")
        except RequestInputError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        try:
            normalized = self._normalized_download_groups(data.get("groups"))
        except RequestInputError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        total_pages = sum(len(pages) for pages in normalized.values())
        if total_pages > DOWNLOAD_CHUNK_PAGES:
            return self.send_json({"error": f"单次下载最多处理 {DOWNLOAD_CHUNK_PAGES} 张图片"}, 400)
        chunk_groups = [{"id": artwork_id, "pages": sorted(pages)} for artwork_id, pages in normalized.items()]
        try:
            chunks = plan_download_chunks(
                chunk_groups,
                max_artworks=DOWNLOAD_CHUNK_ARTWORKS,
                max_pages=DOWNLOAD_CHUNK_PAGES,
            )
        except SearchInputError as exc:
            return self.send_json({"error": str(exc)}, 400)
        if len(chunks) > 1:
            return self.send_json({
                "error": "请求必须按图片优先分块提交",
                "chunks": len(chunks),
                "maxPagesPerChunk": DOWNLOAD_CHUNK_PAGES,
                "maxArtworksPerChunk": DOWNLOAD_CHUNK_ARTWORKS,
            }, 400)

        try:
            def stage_batch(
                staging_root: Path,
                staged: list[PublishedFileOwnership],
            ) -> None:
                for artwork_id, page_set in normalized.items():
                    authorized = validated_session()
                    item = pixiv_item_for_download(artwork_id, allow_r18=authorized)
                    if item.get("restriction") == "r18" and not authorized:
                        raise PixivPolicyError("R-18 下载需要有效账户授权")
                    staged.extend(stage_artwork_pages(
                        item, sorted(page_set), quality, save_root,
                        create_folder, staging_root,
                        download_context=download_context,
                        group_artwork=group_artworks,
                    ))

            public_saved, cleanup_pending = _stage_and_publish_download(
                save_root,
                prefix=".moku-batch-",
                stage=stage_batch,
            )
            response_payload = {
                "ok": True, "saved": public_saved,
                "artworks": len(normalized), "pages": total_pages,
                "cleanupPending": cleanup_pending,
            }
            response_status = 200
        except PIXIV_OPERATION_ERRORS as exc:
            response_payload = {
                "error": public_pixiv_error("批量下载", exc, saving=True),
            }
            response_status = 502
        return self.send_json(response_payload, response_status)

    def _post_pixiv_download(self, data: dict):
        artwork_id = str(data.get("id") or "")
        if not artwork_id.isdigit():
            return self.send_json({"error": "作品 ID 无效"}, 400)
        try:
            quality, create_folder = self._download_options(data)
            save_root = self._save_root(data)
            download_context = self._download_context(data, required=False)
        except RequestInputError as exc:
            return self.send_json({"error": str(exc)}, exc.status)

        try:
            authorized = validated_session()
            item = pixiv_item_for_download(artwork_id, allow_r18=authorized)
            if item.get("restriction") == "r18" and not authorized:
                raise PixivPolicyError("R-18 下载需要有效账户授权")
            def stage_single(
                staging_root: Path,
                staged: list[PublishedFileOwnership],
            ) -> None:
                page_images = item.get("pageImages")
                if not isinstance(page_images, list):
                    raise PixivPolicyError("作品详情不完整")
                staged.extend(stage_artwork_pages(
                    item, list(range(len(page_images))), quality, save_root,
                    create_folder, staging_root,
                    download_context=download_context,
                ))

            public_saved, cleanup_pending = _stage_and_publish_download(
                save_root,
                prefix=".moku-single-",
                stage=stage_single,
            )
            response_payload = {
                "ok": True, "saved": public_saved,
                "quality": quality, "source": "pixiv",
                "cleanupPending": cleanup_pending,
            }
            response_status = 200
        except PIXIV_OPERATION_ERRORS as exc:
            response_payload = {
                "error": public_pixiv_error("Pixiv 下载", exc, saving=True),
            }
            response_status = 502
        return self.send_json(response_payload, response_status)

    def _post_fixture_download(self, data: dict):
        try:
            index = max(0, min(23, int(data.get("index", 0))))
            pages = min(20, max(1, int(data.get("pages", 1))))
        except (TypeError, ValueError):
            return self.send_json({"error": "invalid numeric fields"}, 400)
        quality = data.get("quality", "original")
        image_format = data.get("format", "source")
        if quality not in {"original", "large", "preview"} or image_format not in {"source", "svg"}:
            return self.send_json({"error": "unsupported quality or format"}, 400)
        folder_name = re.sub(
            r"[^\w\-\u4e00-\u9fff]+", "_", str(data.get("tag", "未分类")),
        )[:80] or "未分类"
        folder = DOWNLOADS / folder_name
        folder.mkdir(exist_ok=True)
        saved = []
        for page in range(pages):
            file = folder / f"{81024000 + index}_p{page}_{quality}.svg"
            file.write_bytes(fixture_artwork_svg(index, page, quality))
            saved.append(str(file))
        return self.send_json({
            "ok": True, "saved": saved, "quality": quality, "format": image_format,
        })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    proxy = refresh_network_opener()
    print(f"Pixiv Tag Gallery: http://127.0.0.1:{port}")
    print(f"Downloads: {DOWNLOADS}")
    print(f"Pixiv network: {proxy or 'direct/TUN'}")
    LocalThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
