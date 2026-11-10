# SPDX-License-Identifier: MIT
# MOKU — Xirti / Aperia
# Source: https://github.com/Xirti/Pixiv-Bulk-Image-Downloader
from __future__ import annotations

import json
import queue
import copy
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import http.client
import logging
import os
import re
import random
import shutil
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import time
import secrets
import sqlite3
import socket
import threading
import weakref
import winreg
from collections import OrderedDict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import BinaryIO

from auth_store import (
    delete_session,
    session_cookie_header as web_session_cookie_header,
    store_session,
    validate_session_value,
)
from folder_picker import select_folder
from login_context import load_login_context, parse_login_context, save_login_context
from download_requests import DownloadRequestError, DownloadRequests
from download_pipeline import DownloadAhead, DownloadSource, consume_downloads, stream_downloads
from download_history import DownloadHistory, history_path
from local_catalog import LocalCatalog
from workspace_store import TaskConflictError, TaskCapacityError, WorkspaceCapacityError, WorkspaceStore
from preview_resources import PreviewBusyError, PreviewCancelledError, PreviewResources
from ugoira_export import MAX_ARCHIVE_BYTES as MAX_UGOIRA_BYTES, MAX_EXPANDED_BYTES as MAX_UGOIRA_TEMP_BYTES, UgoiraCancelledError, UgoiraExportError, export_formats, export_ugoira, require_mp4_encoder
from request_coordinator import REQUEST_PRIORITY, RequestCoordinator, RequestDeferredError, RequestBlockedError
from browser_transport import BrowserTransport
from browser_profile import load_browser_profile
from shared_request_budget import SharedRequestBudget
from network_config import normalize_loopback_proxy
from network_transport import PooledOpener, classify_network_error
from pixiv_adapter import PixivPolicyError, build_download_context, build_search_url, build_ugoira_meta_url, build_user_profile_all_url, build_user_profile_works_url, build_user_search_url, is_allowed_pixiv_url, matches_tag_groups, normalize_detail, normalize_search_item, publication_date, publication_day_upper_bound, publication_timestamp, resolve_download_target, resolve_web_path, safe_artwork_stem, search_response_block, should_retry_status, valid_pixiv_id
from search_service import SearchDateRange, SearchResultIndex, SearchInputError, SearchPageCache, build_result_page_rows, build_search_tag_groups, parse_search_date_range, parse_search_query, parse_search_tags, plan_download_chunks, prefetch_item_count, resolve_source_modes, result_window_trim_count
from version import __version__

from runtime_identity import CODE_GENERATION_FILES, compute_code_generation
from artist_service import ArtistService
from account_pages import AccountPages, ACCOUNT_CACHE_ONLY, source_body
from pixiv_adapter import bookmark_count, validate_detail_policy
from discovery_service import DiscoveryService, DiscoveryInputError
from popularity_service import PopularityService
from account_authorization import AccountAuthorization
from pixiv_account import AccountError
from auth_method import AuthMethod
from pixiv_app_gallery import AppGallery, normalize_work
from cloud_state import CloudState, validate_preferences
from cloud_actions import CloudActions, CloudActionError, validate_target, validate_intent
from web_cloud import WebCloudTransport
from source_search import SourceSearch, SourceSearchError, MAX_UPLOAD
from app_updates import AppUpdates, UpdateError


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DOWNLOADS = ROOT / "downloads"
DOWNLOADS.mkdir(exist_ok=True)
LOGIN_CONTEXT = load_login_context(history_path().with_name("login-context.json"))
DOWNLOAD_CHUNK_ARTWORKS = 20
DOWNLOAD_CHUNK_PAGES = 200
MAX_HISTORY_REQUESTS = 24
MAX_HISTORY_SECONDS = 45.0
MAX_USER_SEARCH_REQUESTS = 8
MAX_USER_SEARCH_SECONDS = 40.0
SEARCH_REMOTE_TIMEOUT_SECONDS = 12.0
MAX_SEARCH_NETWORK_WORKERS = 4
SEARCH_NETWORK_POLL_SECONDS = 0.05
SEARCH_PER_PAGE = 36
SEARCH_PREFETCH_AHEAD = 3
SEARCH_KEEP_BEHIND = 6
MAX_SEARCH_SESSIONS = 12
SEARCH_SESSION_IDLE_SECONDS = 30 * 60.0
MAX_HISTORY_SOURCES = 32
MAX_AUTHOR_RESOLUTION_CACHE = 64
AUTHOR_RESOLUTION_TTL_SECONDS = 5 * 60.0
MAX_IMAGE_TOKENS = 4096
MAX_PIXIV_CACHE_ITEMS = 256
MAX_TRACKED_SEARCH_REQUESTS = 64
MAX_SEARCH_CANCEL_TOMBSTONES = 64
SEARCH_CANCEL_TOMBSTONE_SECONDS = 120.0
MAX_ACTIVE_DOWNLOAD_TASKS = 2
PROTOCOL_VERSION = 5
APPLICATION_ID = "MOKU.PixivTagGallery"
CODE_GENERATION = os.getenv("MOKU_CODE_GENERATION") or compute_code_generation()
INSTANCE_ID = os.getenv("MOKU_INSTANCE_ID") or secrets.token_hex(16)
REQUEST_TOKEN = secrets.token_urlsafe(32)
DESKTOP_AUTH_TOKEN = os.getenv("MOKU_DESKTOP_AUTH_TOKEN") or secrets.token_urlsafe(32)
DESKTOP_AUTH_HEADER = "X-MOKU-Desktop-Capability"
FOLDER_PICKER_LOCK = threading.Lock()
# Single-artwork and batch requests share this process-wide limiter. The HTTP
# server may create many request threads, but only a small bounded number may
# consume Pixiv bandwidth and local staging/publish resources at once.
DOWNLOAD_TASK_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_DOWNLOAD_TASKS)
DOWNLOAD_REQUESTS = DownloadRequests(path=history_path().with_name("requests.sqlite3"))
DOWNLOAD_HISTORY = DownloadHistory(history_path())
LOCAL_CATALOG = LocalCatalog(history_path().with_name("catalog.sqlite3"))
WORKSPACE_STORE = WorkspaceStore(history_path().with_name("workspace.sqlite3"))
GUIDE_SEEN_MARKER = history_path().with_name("guide-v1.seen")
REQUEST_COORDINATOR = RequestCoordinator(history_path().with_name("request-settings.json"), shared=SharedRequestBudget())
AUTH_METHOD = AuthMethod(history_path().with_name("auth-method.json"))
SOURCE_SEARCH = SourceSearch(history_path().with_name("source-search"), proxy=lambda: PIXIV_PROXY)
APP_UPDATES = AppUpdates(history_path().with_name("app-updates.json"))
DOWNLOAD_AHEAD = DownloadAhead(capacity=2)
MAX_AHEAD_BYTES = 1024 * 1024 * 1024


def workspace_scope() -> str:
    if AUTH_METHOD.get()=="app":
        uid=ACCOUNTS.account().snapshot()["uid"]
        return "account:"+hashlib.sha256(uid.encode("utf-8")).hexdigest() if uid else "public"
    cookie = session_cookie_header().get("Cookie", "")
    if not cookie:
        return "public"
    user = re.match(r"PHPSESSID=(\d+)_", cookie)
    identity = user[1] if user else cookie
    return "account:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def session_cookie_header():
    return web_session_cookie_header() if AUTH_METHOD.get()=="web" else {}
# Search requests run through a small process-wide pool. A cancelled HTTP
# handler can stop waiting immediately even if urllib is still establishing a
# connection, while the worker cap prevents abandoned connects from piling up.
SEARCH_NETWORK_SLOTS = threading.BoundedSemaphore(MAX_SEARCH_NETWORK_WORKERS)
SEARCH_NETWORK_POOL = ThreadPoolExecutor(
    max_workers=MAX_SEARCH_NETWORK_WORKERS,
    thread_name_prefix="moku-pixiv-search",
)
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
    if not valid_request_token(handler):
        return 403, "请求授权令牌无效"
    if str(handler.headers.get_content_type() or "").lower() != "application/json":
        return 415, "请求必须使用 application/json"
    return None


def validate_desktop_auth_request(handler) -> tuple[int, str] | None:
    """Authorize native-host auth IPC without exposing its capability to JS."""
    if not trusted_local_request(handler):
        return 403, "桌面认证只允许本机调用"
    supplied = str(handler.headers.get(DESKTOP_AUTH_HEADER) or "")
    if not supplied or not supplied.isascii() or not hmac.compare_digest(supplied, DESKTOP_AUTH_TOKEN):
        return 403, "桌面认证能力无效"
    if str(handler.headers.get_content_type() or "").lower() != "application/json":
        return 415, "请求必须使用 application/json"
    return None


def valid_request_token(handler) -> bool:
    supplied = str(handler.headers.get("X-MOKU-Request-Token") or "")
    return bool(supplied) and supplied.isascii() and hmac.compare_digest(supplied, REQUEST_TOKEN)


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

    def __init__(self, *args, **kwargs):
        self._download_idle = threading.Condition()
        self._download_jobs: set[threading.Event] = set()
        self._closing_downloads = False
        super().__init__(*args, **kwargs)

    @contextmanager
    def download_job(self):
        cancel = threading.Event()
        with self._download_idle:
            if self._closing_downloads:
                raise DownloadRequestError("应用正在退出，下载任务已暂停", 503)
            self._download_jobs.add(cancel)
        try:
            yield cancel
        finally:
            with self._download_idle:
                self._download_jobs.remove(cancel)
                self._download_idle.notify_all()

    def server_close(self):
        DOWNLOAD_AHEAD.clear()
        with self._download_idle:
            self._closing_downloads = True
            for cancel in self._download_jobs:
                cancel.set()
            while self._download_jobs:
                self._download_idle.wait()
        DOWNLOAD_AHEAD.close()
        super().server_close()


PIXIV_HEADERS = {"Referer": "https://www.pixiv.net/", "User-Agent": "Mozilla/5.0 PixivTagGallery/0.1", "Accept-Language": LOGIN_CONTEXT.accept_language}
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
AUTHOR_RESOLUTION_CACHE: OrderedDict[str, tuple[float, str, str]] = OrderedDict()
AUTHOR_RESOLUTION_CACHE_LOCK = threading.Lock()
SEARCH_REQUESTS: OrderedDict[str, dict] = OrderedDict()
SEARCH_REQUESTS_LOCK = threading.Lock()
AUTHORIZATION_GENERATION = 0
AUTHORIZATION_ACTIVE = False
UGOIRA_RESOURCES = PreviewResources()
DETAIL_RESOURCES = PreviewResources(max_bytes=8 * 1024 * 1024, max_loads=32, max_workers=4)


class AuthorizationRevokedError(PixivPolicyError):
    pass


class SearchCancelledError(RuntimeError):
    pass


class SearchRequestConflictError(RuntimeError):
    pass


class SearchRequestLimitError(RuntimeError):
    pass


def parse_search_request_id(value: object, *, required: bool = False) -> str:
    request_id = str(value or "").strip()
    if not request_id and not required:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_id):
        raise SearchInputError("搜索请求标识无效")
    return request_id


def parse_search_snapshot_id(value: object) -> str:
    snapshot_id = str(value or "").strip()
    if snapshot_id and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", snapshot_id):
        raise SearchInputError("搜索快照标识无效")
    return snapshot_id


def _prune_search_requests_locked(now: float) -> None:
    for request_id, entry in list(SEARCH_REQUESTS.items()):
        if not entry["active"] and entry["expiresAt"] <= now:
            SEARCH_REQUESTS.pop(request_id, None)
    while sum(not entry["active"] for entry in SEARCH_REQUESTS.values()) > MAX_SEARCH_CANCEL_TOMBSTONES:
        stale_id = next((
            request_id for request_id, entry in SEARCH_REQUESTS.items()
            if not entry["active"]
        ), None)
        if stale_id is None:
            break
        SEARCH_REQUESTS.pop(stale_id, None)


def _search_request_capacity_available_locked() -> bool:
    return sum(bool(entry["active"]) for entry in SEARCH_REQUESTS.values()) < MAX_TRACKED_SEARCH_REQUESTS


def register_search_request(request_id: str) -> threading.Event:
    clean = parse_search_request_id(request_id, required=True)
    now = time.monotonic()
    with SEARCH_REQUESTS_LOCK:
        _prune_search_requests_locked(now)
        entry = SEARCH_REQUESTS.pop(clean, None)
        if entry is not None and entry["active"]:
            SEARCH_REQUESTS[clean] = entry
            raise SearchRequestConflictError("搜索请求标识正在使用")
        if not _search_request_capacity_available_locked():
            if entry is not None:
                SEARCH_REQUESTS[clean] = entry
            raise SearchRequestLimitError("同时进行的搜索过多，请先停止正在运行的搜索")
        event = entry["event"] if entry is not None else threading.Event()
        SEARCH_REQUESTS[clean] = {
            "event": event,
            "active": True,
            "expiresAt": now + SEARCH_CANCEL_TOMBSTONE_SECONDS,
        }
        return event


def cancel_search_request(request_id: str) -> bool:
    clean = parse_search_request_id(request_id, required=True)
    now = time.monotonic()
    with SEARCH_REQUESTS_LOCK:
        _prune_search_requests_locked(now)
        entry = SEARCH_REQUESTS.pop(clean, None)
        found = entry is not None and bool(entry["active"])
        event = entry["event"] if entry is not None else threading.Event()
        event.set()
        SEARCH_REQUESTS[clean] = {
            "event": event,
            "active": bool(entry and entry["active"]),
            "expiresAt": now + SEARCH_CANCEL_TOMBSTONE_SECONDS,
        }
        _prune_search_requests_locked(now)
        return found


def release_search_request(request_id: str, event: threading.Event) -> None:
    with SEARCH_REQUESTS_LOCK:
        entry = SEARCH_REQUESTS.get(request_id)
        if entry is not None and entry["event"] is event:
            SEARCH_REQUESTS.pop(request_id, None)


def cancel_all_search_requests() -> None:
    with SEARCH_REQUESTS_LOCK:
        for entry in SEARCH_REQUESTS.values():
            entry["event"].set()
        SEARCH_REQUESTS.clear()


def raise_if_search_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SearchCancelledError("搜索已取消")


def authorization_generation() -> int:
    with SEARCH_SESSION_LOCKS_GUARD:
        return AUTHORIZATION_GENERATION


def assert_authorization_generation(expected: int | None) -> None:
    if expected is None:
        return
    with SEARCH_SESSION_LOCKS_GUARD:
        if not AUTHORIZATION_ACTIVE or AUTHORIZATION_GENERATION != expected:
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


def _item_image_tokens_current(
    item: dict,
    *,
    now: float | None = None,
    require_thumb: bool = True,
) -> bool:
    artwork_id = str(item.get("id") or "")
    pages = item.get("pageImages")
    thumb = str(item.get("thumb") or "")
    if not artwork_id.isdigit() or not isinstance(pages, list) or not pages:
        return False
    if require_thumb and not thumb:
        return False
    proxy_urls = [thumb] if require_thumb else []
    for page in pages:
        if not isinstance(page, dict):
            return False
        # Ugoira pages carry no original still image; empty slots hold no token.
        proxy_urls.extend(
            url for url in (
                str(page.get(quality) or "")
                for quality in ("regular", "original")
            ) if url
        )
    current = time.time() if now is None else float(now)
    with PIXIV_STATE_LOCK:
        for proxy_url in proxy_urls:
            token = urllib.parse.parse_qs(
                urllib.parse.urlsplit(proxy_url).query
            ).get("token", [""])[0]
            approved = IMAGE_TOKENS.get(token)
            if not approved or approved[0] < current or str(approved[1]) != artwork_id:
                return False
    return True


def pixiv_item_for_download(
    artwork_id: str,
    *,
    allow_r18: bool,
    authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    require_thumb: bool = False,
    network_timeout: float | None = None,
    refresh: bool = False,
) -> dict:
    raise_if_search_cancelled(cancel_event)
    cached = None if refresh else get_cached_pixiv_item(artwork_id)
    if cached is not None and _item_image_tokens_current(
        cached, require_thumb=require_thumb,
    ):
        if cached.get("restriction") == "r18":
            if not allow_r18:
                raise PixivPolicyError("restricted artwork")
            if authorization_epoch is None:
                raise AuthorizationRevokedError("Pixiv 授权已撤销，请重新发起下载")
            assert_authorization_generation(authorization_epoch)
        raise_if_search_cancelled(cancel_event)
        return cached
    if authorization_epoch is None and cancel_event is None and network_timeout is None:
        return pixiv_detail(artwork_id, allow_r18=allow_r18)
    kwargs = {"authorization_epoch": authorization_epoch}
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    if network_timeout is not None:
        kwargs["network_timeout"] = network_timeout
    return pixiv_detail(artwork_id, allow_r18=allow_r18, **kwargs)


def search_session_scope(session_key: tuple) -> str:
    if session_key and session_key[0] == "tags" and len(session_key) >= 3:
        return str(session_key[2])
    if session_key and session_key[0] == "user" and len(session_key) >= 5:
        return str(session_key[4])
    return str(session_key[1]) if len(session_key) >= 2 else ""


def clear_authorized_state(*, force: bool = True) -> bool:
    global AUTHORIZATION_GENERATION, AUTHORIZATION_ACTIVE
    with SEARCH_SESSION_LOCKS_GUARD:
        if not force and not AUTHORIZATION_ACTIVE:
            return False
        AUTHORIZATION_ACTIVE = False
        AUTHORIZATION_GENERATION += 1
        UGOIRA_RESOURCES.clear()
        DETAIL_RESOURCES.clear()
        ARTISTS.clear()
        APP_GALLERY.clear()
        POPULARITY.invalidate()
        ACCOUNTS.invalidate()
        restricted_sessions = list(SEARCH_SESSIONS)
        with PIXIV_STATE_LOCK:
            PIXIV_CACHE.clear()
            for token, row in list(IMAGE_TOKENS.items()):
                if len(row) >= 4 and row[3] in {"r18", "account"}: IMAGE_TOKENS.pop(token, None)
            for key in list(HISTORY_CACHE):
                if len(key) >= 2 and key[1] == "r18": HISTORY_CACHE.pop(key, None)
        for session_key in restricted_sessions:
            _drop_search_session(session_key)
    return True


def disconnect_authorized_session() -> None:
    # Login, logout, and authorization snapshots share one linearization lock.
    # Otherwise a concurrent login can land between credential deletion and
    # cache revocation, leaving a new credential that silently reactivates after
    # logout has already reported success.
    with SEARCH_SESSION_LOCKS_GUARD:
        try:
            ACCOUNT_AUTH.close()
            try:
                ACCOUNTS.disconnect()
            finally:
                delete_session()
        finally:
            # In-memory authorization and restricted capabilities are revoked
            # even when Credential Manager cannot durably remove the credential.
            clear_authorized_state(force=True)


def connect_authorized_session(value: str, *, remember: bool) -> None:
    # Validate before touching the current account, then make the account
    # switch indivisible with respect to authorization snapshots. The previous
    # credential is removed first so a failed replacement cannot resurrect it.
    clean_value = validate_session_value(value)
    with SEARCH_SESSION_LOCKS_GUARD:
        clear_authorized_state(force=True)
        delete_session()
        store_session(clean_value, remember=remember)
        mark_authorized_session()
        REQUEST_COORDINATOR.session_connected()


def mark_authorized_session() -> None:
    """Compatibility hook: local session presence is the authorization state."""
    global AUTHORIZATION_ACTIVE
    with SEARCH_SESSION_LOCKS_GUARD:
        AUTHORIZATION_ACTIVE = True


def validated_authorization(force: bool = False) -> tuple[bool, int | None]:
    """Atomically validate the local session and capture its revocation epoch."""
    global AUTHORIZATION_ACTIVE
    # Credential observation and epoch capture must be indivisible. Logout
    # removes the credential before taking this guard, then revokes the epoch
    # under it, so it cannot be inserted between these two values.
    with SEARCH_SESSION_LOCKS_GUARD:
        app = ACCOUNTS.account().snapshot() if AUTH_METHOD.get()=="app" else None
        present = app["connected"] and not app["needsAuthorization"] if app is not None else bool(session_cookie_header())
        if not present:
            clear_authorized_state(force=False)
            return False, None
        AUTHORIZATION_ACTIVE = True
        return True, AUTHORIZATION_GENERATION


def auth_status_snapshot() -> dict:
    with SEARCH_SESSION_LOCKS_GUARD:
        session_present, _epoch = validated_authorization()
        return {
            "loggedIn": session_present,
            "sessionPresent": session_present,
            "authState": "authorized" if session_present else "unauthenticated",
            "authorizationGeneration": AUTHORIZATION_GENERATION,
            "authMethod": AUTH_METHOD.get(),
        }


def history_lock_for(*parts) -> threading.Lock:
    key = tuple(str(part) if not isinstance(part, tuple) else part for part in parts)
    with _HISTORY_LOCKS_GUARD:
        lock = _HISTORY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _HISTORY_LOCKS[key] = lock
        return lock


def validated_session(force: bool = False) -> bool:
    return validated_authorization(force=force)[0]


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PixivPolicyError("Pixiv 响应包含重定向，已按安全策略拒绝")


class StrictLoopbackProxyHandler(urllib.request.ProxyHandler):
    """Route through an explicitly selected local proxy without OS bypass rules."""

    def proxy_open(self, request, proxy, target_type):
        normalized = normalize_loopback_proxy(proxy)
        if not normalized or normalized != proxy:
            raise urllib.error.URLError("invalid loopback proxy")
        original_type = request.type
        parsed = urllib.parse.urlsplit(normalized)
        request.set_proxy(parsed.netloc, parsed.scheme)
        if original_type == parsed.scheme or original_type == "https":
            return None
        return self.parent.open(request, timeout=request.timeout)


PIXIV_OPENER = PooledOpener(StrictLoopbackProxyHandler({}), NoRedirectHandler())
PIXIV_PROXY = ""
PIXIV_NETWORK_FINGERPRINT: tuple[str] | None = None
PIXIV_NETWORK_CHECKED_AT = 0.0
PIXIV_NETWORK_LOCK = threading.Lock()
NETWORK_RECHECK_SECONDS = 1.0


def _prune_image_tokens_locked(current: float) -> None:
    for token, row in list(IMAGE_TOKENS.items()):
        if not row or row[0] < current:
            IMAGE_TOKENS.pop(token, None)
    overflow = len(IMAGE_TOKENS) - MAX_IMAGE_TOKENS
    if overflow > 0:
        for token, _row in sorted(
            IMAGE_TOKENS.items(), key=lambda pair: pair[1][0]
        )[:overflow]:
            IMAGE_TOKENS.pop(token, None)


def prune_image_tokens(now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    with PIXIV_STATE_LOCK:
        _prune_image_tokens_locked(current)


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
    if len(row) >= 4 and row[3] in {"r18", "account"}:
        return "no-store"
    return "no-store" if len(row) >= 6 else "private,max-age=3600"


def assert_image_token_authorization(row: tuple) -> None:
    if len(row) < 4 or row[3] not in {"r18", "account"}:
        return
    epoch = row[6] if len(row) >= 7 else (row[4] if len(row) == 5 else None)
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        raise AuthorizationRevokedError("R-18 图片授权已失效")
    assert_authorization_generation(epoch)


def authorize_image_proxy(
    proxy_url: str, artwork_id: str, restriction: str = "safe", *,
    search_session: tuple | None = None, search_page: int | None = None,
    authorization_epoch: int | None = None,
) -> str:
    return _authorize_image_proxy_batch([(
        proxy_url,
        artwork_id,
        restriction,
        search_session,
        search_page,
        authorization_epoch,
    )])[0]


def _authorize_image_proxy_batch(entries: list[tuple]) -> list[str]:
    if len(entries) > MAX_IMAGE_TOKENS:
        raise PixivPolicyError("图片授权数量超出上限")
    expires = time.time() + 3600
    prepared: list[tuple[str, tuple]] = []
    for (
        proxy_url,
        artwork_id,
        restriction,
        search_session,
        search_page,
        authorization_epoch,
    ) in entries:
        remote_url = urllib.parse.parse_qs(
            urllib.parse.urlsplit(str(proxy_url)).query
        ).get("url", [""])[0]
        if not is_allowed_pixiv_url(remote_url, image_only=True):
            raise PixivPolicyError("invalid approved image")
        if restriction not in {"safe", "r18", "account"}:
            raise PixivPolicyError("invalid image restriction")
        token = secrets.token_urlsafe(24)
        row: tuple = (expires, artwork_id, remote_url, restriction)
        if search_session is not None and search_page is not None:
            row += (search_session, max(1, int(search_page)))
        if restriction in {"r18", "account"}:
            if authorization_epoch is None:
                raise AuthorizationRevokedError("R-18 图片授权已失效")
            assert_authorization_generation(authorization_epoch)
            # Search metadata retains its historical positions (4 and 5);
            # restricted rows append their account-generation binding last.
            row += (authorization_epoch,)
        prepared.append((token, row))
    with SEARCH_SESSION_LOCKS_GUARD:
        for _token, row in prepared:
            assert_image_token_authorization(row)
        with PIXIV_STATE_LOCK:
            _prune_image_tokens_locked(time.time())
            for token, row in prepared:
                IMAGE_TOKENS[token] = row
            # Enforce the bound in the same critical section as insertion.
            # Logout and parallel authorizations cannot leave an over-limit or
            # post-revocation restricted token behind.
            _prune_image_tokens_locked(time.time())
    return [
        "/api/pixiv/image?" + urllib.parse.urlencode({"token": token})
        for token, _row in prepared
    ]


def authorize_item_images(
    item: dict, *, search_session: tuple | None = None, search_page: int | None = None,
    authorization_epoch: int | None = None, account_bound: bool = False,
) -> dict:
    artwork_id = str(item["id"]); restriction = "account" if account_bound else str(item.get("restriction") or "safe")
    targets: list[tuple[dict, str]] = []
    entries: list[tuple] = []
    if item.get("thumb"):
        targets.append((item, "thumb"))
        entries.append((
            item["thumb"], artwork_id, restriction, search_session, search_page,
            authorization_epoch,
        ))
    for page in item.get("pageImages") or []:
        # Skip empty ugoira "original" slots instead of refusing to authorize.
        for key in ("regular", "original"):
            if not page.get(key):
                continue
            targets.append((page, key))
            entries.append((
                page[key], artwork_id, restriction, None, None, authorization_epoch,
            ))
    for (target, key), approved in zip(
        targets, _authorize_image_proxy_batch(entries), strict=True,
    ):
        target[key] = approved
    return item


def revoke_item_image_tokens(item: dict) -> None:
    tokens: set[str] = set()
    candidates = [item.get("thumb")]
    for page in item.get("pageImages") or []:
        if isinstance(page, dict):
            candidates.extend((page.get("regular"), page.get("original")))
    for proxy_url in candidates:
        token = urllib.parse.parse_qs(
            urllib.parse.urlsplit(str(proxy_url or "")).query
        ).get("token", [""])[0]
        if token:
            tokens.add(token)
    with PIXIV_STATE_LOCK:
        for token in tokens:
            IMAGE_TOKENS.pop(token, None)


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
        configuration = (PIXIV_OPENER.configuration if isinstance(PIXIV_OPENER, BrowserTransport)
                         else load_browser_profile(history_path().with_name("browser-profile.json")))
        configuration = replace(configuration, language=LOGIN_CONTEXT.accept_language)
        opener = BrowserTransport(proxy, profile=configuration)
        HTTP_LOG.info("Pixiv browser transport profile=%s revision=%s", opener.profile, configuration.revision)
        previous = PIXIV_OPENER
        PIXIV_OPENER = opener
        PIXIV_PROXY = proxy
        PIXIV_NETWORK_FINGERPRINT = fingerprint
        previous.close()
        return proxy


def refresh_network_opener() -> str:
    return _update_network_opener(max_age=0.0)


def ensure_network_opener_current() -> str:
    return _update_network_opener(max_age=NETWORK_RECHECK_SECONDS)


def _read_cancellable_response(
    response,
    *,
    max_bytes: int,
    cancel_event: threading.Event,
    deadline: float,
    output: BinaryIO | None = None,
    on_chunk: Callable[[int], None] | None = None,
) -> bytes | int:
    """Read a response while a small watcher can close a blocked socket."""
    watcher_done = threading.Event()
    deadline_reached = threading.Event()

    def interrupt_response() -> None:
        # Buffered HTTP readers can hold their own lock while blocked in recv().
        # Shutting down the underlying CPython socket wakes that read first.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        stream_socket = getattr(raw, "_sock", None)
        if stream_socket is not None:
            try:
                stream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            response.close()
        except OSError:
            pass

    def interrupt_blocked_read() -> None:
        while not watcher_done.wait(0.05):
            if cancel_event.is_set():
                interrupt_response()
                return
            if time.monotonic() >= deadline:
                deadline_reached.set()
                interrupt_response()
                return

    watcher = threading.Thread(target=interrupt_blocked_read, daemon=True)
    watcher.start()
    chunks: list[bytes] = []
    received = 0
    try:
        while received <= max_bytes:
            raise_if_search_cancelled(cancel_event)
            if deadline_reached.is_set() or time.monotonic() >= deadline:
                raise TimeoutError("Pixiv response deadline exceeded")
            try:
                chunk = response.read(min(64 * 1024, max_bytes + 1 - received))
            except Exception:
                if cancel_event.is_set():
                    raise SearchCancelledError("搜索已取消") from None
                if deadline_reached.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("Pixiv response deadline exceeded") from None
                raise
            if not chunk:
                break
            if output is None:
                chunks.append(chunk)
            else:
                output.write(chunk)
            received += len(chunk)
            if on_chunk is not None:
                on_chunk(len(chunk))
        raise_if_search_cancelled(cancel_event)
        if deadline_reached.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Pixiv response deadline exceeded")
        return b"".join(chunks) if output is None else received
    finally:
        watcher_done.set()
        watcher.join(timeout=0.2)


def pixiv_request(
    url: str, image_only: bool = False, max_bytes: int = MAX_REMOTE_BYTES,
    session_value: str = "", *, anonymous: bool = False, timeout: float = 25,
    attempts: int = 2, cancel_event: threading.Event | None = None,
    output: BinaryIO | None = None, max_duration: float | None = None,
    download: bool = False,
    archive: bool = False,
) -> tuple[bytes, str]:
    """Read small responses in memory, or stream to a seekable caller-owned file."""
    if not is_allowed_pixiv_url(url, image_only=image_only):
        raise PixivPolicyError("不允许的 Pixiv 地址")
    request_headers = dict(PIXIV_HEADERS)
    with SEARCH_SESSION_LOCKS_GUARD:
        if session_value:
            if image_only: raise PixivPolicyError("账户会话不得发送到图片CDN")
            request_headers["Cookie"] = f"PHPSESSID={session_value}"
        elif not image_only and not anonymous:
            request_headers.update(session_cookie_header())
        epoch = AUTHORIZATION_GENERATION

    def check_request():
        raise_if_search_cancelled(cancel_event)
        if "Cookie" in request_headers and authorization_generation() != epoch:
            raise AuthorizationRevokedError("Pixiv 账户状态已变更，请重新发起请求")

    def block_response(kind):
        with SEARCH_SESSION_LOCKS_GUARD:
            check_request()
            return REQUEST_COORDINATOR.block(kind)

    last_error = None
    priority = REQUEST_PRIORITY.get()
    request_path = urllib.parse.urlsplit(url).path
    activity = "detail" if request_path.startswith("/ajax/illust/") else "browse" if image_only else "search"
    attempt_count = max(1, min(int(attempts), 2))
    request_timeout = max(1.0, min(float(timeout), 90.0))
    for attempt in range(attempt_count):
        raise_if_search_cancelled(cancel_event)
        request = urllib.request.Request(url, headers=request_headers)
        request._moku_check = check_request
        sample = None
        succeeded = False
        track_download = download and image_only and priority == 1

        def before_send():
            nonlocal sample
            REQUEST_COORDINATOR.before_send(image_only, check_request, priority=priority, activity=activity)
            if track_download:
                sample = REQUEST_COORDINATOR.start_download_sample(opener, archive=archive)

        def received_chunk(size):
            if sample is not None:
                sample.received(size)

        request._moku_before_send = before_send
        request._moku_phase = "open"
        request._moku_reused = False
        with PIXIV_NETWORK_LOCK:
            opener, proxy = PIXIV_OPENER, PIXIV_PROXY
        started = time.monotonic()
        try:
            with REQUEST_COORDINATOR.slot(image_only, check_request, download=download, priority=priority):
                deadline = time.monotonic() + (max_duration if max_duration is not None else request_timeout)
                request._moku_deadline = deadline
                if output is not None:
                    output.seek(0)
                    output.truncate()
                if isinstance(opener, BrowserTransport):
                    request._moku_phase = "browser_transfer"
                    raw, content_type = opener.fetch(
                        url, request_headers, output=output, maximum=max_bytes,
                        timeout=request_timeout, deadline=deadline, check=check_request,
                        before_send=request._moku_before_send,
                        **({"on_chunk": received_chunk} if track_download else {}),
                    )
                    if content_type == "text/html":
                        detect_pixiv_verification_response(raw, output)
                    REQUEST_COORDINATOR.success()
                    succeeded = True
                    return raw, content_type
                with opener.open(request, timeout=request_timeout) as response:
                    final_url = response.geturl()
                    if not is_allowed_pixiv_url(final_url, image_only=image_only):
                        raise PixivPolicyError("Pixiv 重定向到了不允许的地址")
                    if response.status == 206 or response.headers.get("Content-Range"):
                        raise PixivPolicyError("Pixiv 返回了局部内容，请重新下载完整文件")
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
                    request._moku_phase = "response_body"
                    raw = _read_cancellable_response(
                        response,
                        max_bytes=max_bytes,
                        cancel_event=cancel_event or threading.Event(),
                        deadline=deadline,
                        **({"output": output} if output is not None else {}),
                        **({"on_chunk": received_chunk} if track_download else {}),
                    )
                    received = len(raw) if output is None else raw
                    if received > max_bytes:
                        raise PixivPolicyError("远程响应过大")
                    if declared_length is not None and received != declared_length:
                        raise http.client.IncompleteRead(raw if output is None else b"", declared_length - received)
                    if content_type == "text/html":
                        detect_pixiv_verification_response(raw, output)
                    REQUEST_COORDINATOR.success()
                    succeeded = True
                    return raw if output is None else b"", content_type
        except RequestBlockedError as exc:
            if exc.kind in {"verification_required", "authentication_required"}:
                raise block_response(exc.kind) from exc
            raise
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
                if exc.code == 429:
                    raise REQUEST_COORDINATOR.limit(exc.headers.get("Retry-After")) from exc
                if exc.headers.get("cf-mitigated") == "challenge":
                    raise block_response("verification_required") from exc
                if exc.code == 401:
                    raise block_response("authentication_required") from exc
                if exc.code == 403:
                    raise RequestBlockedError("access_denied") from exc
                if exc.code in {404, 410}:
                    raise RequestBlockedError("not_found") from exc
            raise_if_search_cancelled(cancel_event)
            failure = classify_network_error(exc)
            retryable = should_retry_status(exc.code) if isinstance(exc, urllib.error.HTTPError) else failure.retryable
            if retryable:
                REQUEST_COORDINATOR.failure()
            retry = retryable and attempt < attempt_count - 1
            HTTP_LOG.warning(
                "Pixiv request failed host=%s route=%s phase=%s attempt=%d/%d kind=%s code=%s ms=%d reused=%s retry=%s profile=%s",
                urllib.parse.urlsplit(url).hostname, proxy or "direct-or-tun", request._moku_phase,
                attempt + 1, attempt_count, failure.kind, failure.code,
                round((time.monotonic() - started) * 1000),
                "unavailable" if isinstance(opener, BrowserTransport) else request._moku_reused, retry,
                getattr(opener, "profile", "compatibility"),
            )
            if not retry:
                raise
            last_error = exc
        finally:
            REQUEST_COORDINATOR.finish_download_sample(sample, succeeded)
        delay = .6 * (2 ** attempt) + random.uniform(0, .4)
        if cancel_event is None:
            time.sleep(delay)
        elif cancel_event.wait(delay):
            raise SearchCancelledError("搜索已取消")
    raise last_error or PixivPolicyError("Pixiv 连接失败")


def detect_pixiv_verification(raw: bytes) -> None:
    prefix = raw[:16384].lower()
    if b"/cdn-cgi/challenge-platform/" in prefix or b"cf-chl-" in prefix:
        raise RequestBlockedError("verification_required")


def detect_pixiv_verification_response(raw: bytes, output: BinaryIO | None) -> None:
    if output is not None:
        position = output.tell()
        output.seek(0)
        raw = output.read(16384)
        output.seek(position)
    detect_pixiv_verification(raw)


def pixiv_json(
    url: str, *, timeout: float = 25, attempts: int = 2,
    cancel_event: threading.Event | None = None,
) -> dict:
    request_options = {
        "max_bytes": 8 * 1024 * 1024,
        "timeout": timeout,
        "attempts": attempts,
    }
    if cancel_event is not None:
        request_options["cancel_event"] = cancel_event
    raw, content_type = pixiv_request(url, **request_options)
    if content_type != "application/json":
        raise PixivPolicyError("Pixiv 返回了非 JSON 数据")
    try:
        data = json.loads(raw)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise PixivPolicyError("Pixiv 返回的数据无法解析") from exc
    if not isinstance(data, dict):
        raise PixivPolicyError("Pixiv 返回的数据格式异常")
    if data.get("error"):
        error = data["error"]
        message = error.get("message") if isinstance(error, dict) else data.get("message")
        if message == "Rate Limit":
            raise REQUEST_COORDINATOR.limit(None)
        raise PixivPolicyError("Pixiv API 拒绝了当前请求")
    return data


def search_pixiv_json(url: str, cancel_event: threading.Event | None) -> dict:
    raise_if_search_cancelled(cancel_event)
    while not SEARCH_NETWORK_SLOTS.acquire(timeout=SEARCH_NETWORK_POLL_SECONDS):
        raise_if_search_cancelled(cancel_event)
    options = {}
    if cancel_event is not None:
        options = {
            "timeout": SEARCH_REMOTE_TIMEOUT_SECONDS,
            "attempts": 2,
            "cancel_event": cancel_event,
        }
    try:
        raise_if_search_cancelled(cancel_event)
        future = SEARCH_NETWORK_POOL.submit(copy_context().run, pixiv_json, url, **options)
    except BaseException:
        SEARCH_NETWORK_SLOTS.release()
        raise
    future.add_done_callback(lambda _future: SEARCH_NETWORK_SLOTS.release())
    if cancel_event is None:
        return future.result()
    while True:
        if cancel_event.is_set():
            future.cancel()
            raise SearchCancelledError("搜索已取消")
        try:
            data = future.result(timeout=SEARCH_NETWORK_POLL_SECONDS)
            break
        except FutureTimeoutError:
            # concurrent.futures.TimeoutError aliases built-in TimeoutError on
            # current Python versions. A completed future therefore means the
            # poll raced with completion. Re-read the completed future so a
            # successful result is preserved and a worker exception propagates.
            if future.done():
                try:
                    data = future.result()
                except Exception:
                    raise_if_search_cancelled(cancel_event)
                    raise
                break
        except Exception:
            raise_if_search_cancelled(cancel_event)
            raise
    raise_if_search_cancelled(cancel_event)
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
    return classify_network_error(exc).kind


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
    except RequestDeferredError as exc:
        return {
            "name": name, "ok": False, "errorKind": "waiting", "errorCode": exc.kind,
            "message": str(exc), "retryAfterSeconds": exc.retry_after,
            "ms": round((time.monotonic() - started) * 1000),
        }
    except RequestBlockedError as exc:
        return {
            "name": name, "ok": False, "errorKind": "paused", "errorCode": exc.kind,
            "message": str(exc), "ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        failure = classify_network_error(exc)
        return {
            "name": name, "ok": False, "errorKind": failure.kind, "errorCode": failure.code,
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
    failures = {row.get("errorKind") for row in checks if not row.get("ok")}
    pixiv_ok = results.get("pixiv", False)
    cdn_ok = results.get("cdn", False)
    if "paused" in failures:
        headline = "请求已暂停，网络检测暂缓"
        guidance = "请在 Pixiv 官方页面完成登录或验证，再点击继续请求后检测。"
    elif "waiting" in failures:
        seconds = max(row.get("retryAfterSeconds", 0) for row in checks)
        headline = "请求冷却中，网络检测暂缓" if any(row.get("errorCode") == "upstream_rate_limit" for row in checks) else "请求正在排队，网络检测暂缓"
        guidance = f"请在 {seconds} 秒后重新检测。"
    elif pixiv_ok and cdn_ok:
        headline = "当前网络可以使用 Pixiv"
        guidance = "Pixiv 主站和图片线路均已匿名测试通过。"
    elif pixiv_ok:
        headline = "Pixiv 主站可用，但图片线路异常"
        guidance = "搜索可能可用，但缩略图和下载可能失败；请检查代理是否同时代理 i.pximg.net。"
    elif state.get("proxyEnabled") and not selected_proxy:
        headline = "系统代理已开启，但 MOKU 无法使用该配置"
        guidance = "MOKU 只接受本机 HTTP 代理入口；请确认代理地址是 127.0.0.1 或 localhost，且端口正在运行。"
    elif "certificate" in failures:
        headline = "Pixiv 连接的证书校验失败"
        guidance = "请检查系统日期和时间，以及本机代理是否使用已受信任的证书。"
    elif "tls_protocol" in failures:
        headline = "TLS 协议或本地代理入口异常"
        guidance = "请核对本机 HTTP 代理的协议和端口，例如 http://127.0.0.1:7890。"
    elif "dns" in failures:
        headline = "Pixiv 域名解析失败"
        guidance = "请检查当前网络或代理的 DNS 配置，再重新检测。"
    elif selected_proxy and failures.intersection({"tls", "reset", "proxy"}):
        headline = "当前代理到 Pixiv 的连接异常"
        guidance = "请检查当前节点到 Pixiv 主站和图片 CDN 的链路，更换可用节点后重新检测。"
    else:
        headline = "当前网络无法连接 Pixiv"
        guidance = "请开启可用的 Windows 系统代理或 TUN 全局模式，再重新检测。"
    return {"routeLabel": route, "headline": headline, "guidance": guidance}


def history_cache_key(tag: str, mode: str, namespace: tuple | None = None) -> tuple:
    return (tag, mode) if namespace is None else ("search", namespace, tag, mode)


def _history_state(tag: str, mode: str, namespace: tuple | None = None, date_range: SearchDateRange | None = None) -> dict:
    key = history_cache_key(tag, mode, namespace)
    with SEARCH_SESSION_LOCKS_GUARD:
        state = HISTORY_CACHE.get(key)
        if state is None:
            state = {
                "items": [], "ids": set(), "queue": [],
                "nextEnd": date_range.end if date_range else datetime.now(timezone(timedelta(hours=9))).date(),
                "minDate": max(date(2007, 9, 10), date_range.start) if date_range else date(2007, 9, 10),
                "baseOffset": 0, "exhausted": False, "budgetExhausted": False,
                "truncatedDates": [], "touched": time.monotonic(),
            }
            HISTORY_CACHE[key] = state
        state["touched"] = time.monotonic()
        if len(HISTORY_CACHE) > MAX_HISTORY_SOURCES:
            with _HISTORY_LOCKS_GUARD:
                active_keys = set(_HISTORY_LOCKS)
            candidates = sorted(
                (
                    (row.get("touched", 0.0), cache_key)
                    for cache_key, row in HISTORY_CACHE.items()
                    if cache_key != key and cache_key not in active_keys
                ),
                key=lambda candidate: (candidate[0], repr(candidate[1])),
            )
            for _touched, stale_key in candidates[: len(HISTORY_CACHE) - MAX_HISTORY_SOURCES]:
                HISTORY_CACHE.pop(stale_key, None)
        return state


def _queue_older_window(state: dict) -> None:
    if state["exhausted"]: return
    epoch = state.get("minDate", date(2007, 9, 10)); end = state["nextEnd"]
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
    work_type: str = "all",
    max_requests: int = MAX_HISTORY_REQUESTS,
    max_seconds: float = MAX_HISTORY_SECONDS,
    budget: dict | None = None,
    namespace: tuple | None = None,
    date_range: SearchDateRange | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    if date_range is not None and namespace is None:
        namespace = ("dates", date_range)
    cache_key = history_cache_key(tag, mode, namespace)
    with history_lock_for(*cache_key):
        state = _history_state(tag, mode, namespace, date_range)
        state["budgetExhausted"] = False
        if budget is None:
            budget = {"started": time.monotonic(), "requests": 0}

        def budget_available() -> bool:
            raise_if_search_cancelled(cancel_event)
            request_limit = min(max_requests, budget.get("requestLimit", max_requests))
            deadline = min(budget["started"] + max_seconds, budget.get("deadline", float("inf")))
            return budget["requests"] < request_limit and time.monotonic() < deadline

        def consume_request() -> None:
            budget["requests"] += 1

        while state.get("baseOffset", 0) + len(state["items"]) < need_count and not state["exhausted"]:
            raise_if_search_cancelled(cancel_event)
            if not budget_available():
                state["budgetExhausted"] = True
                break
            if not state["queue"]: _queue_older_window(state)
            if not state["queue"]: break
            window = state["queue"][0]
            if not window["initialized"]:
                consume_request()
                block = search_response_block(search_pixiv_json(
                    build_search_url(tag, 1, mode=mode, start_date=window["start"], end_date=window["end"], work_type=work_type),
                    cancel_event,
                ), work_type)
                total = int(block.get("total") or 0)
                first_data = block.get("data") or []
                if mode == "r18" and total > 0:
                    for raw in first_data:
                        try:
                            candidate = normalize_search_item(raw, allow_r18=allow_r18)
                        except PixivPolicyError:
                            continue
                        if candidate["restriction"] == "r18":
                            break
                    else:
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
                block = search_response_block(search_pixiv_json(
                    build_search_url(tag, window["page"], mode=mode, start_date=window["start"], end_date=window["end"], work_type=work_type),
                    cancel_event,
                ), work_type)
                rows = block.get("data") or []
            for raw in rows:
                try: candidate = normalize_search_item(raw, allow_r18=allow_r18)
                except PixivPolicyError: continue
                artwork_id = candidate["id"]
                if artwork_id in state["ids"]: continue
                if date_range and not date_range.contains(candidate["date"]): continue
                if mode == "r18" and candidate["restriction"] != "r18": continue
                state["ids"].add(artwork_id); state["items"].append(raw)
            # date_d bounds publication time, not ID order within one timestamp.
            # Real timestamps let the first batch confirm a useful prefix;
            # day-only values conservatively keep that day's tie pending.
            if rows and isinstance(rows[-1], dict):
                last = rows[-1]
                last_day = publication_date(last.get("createDate"))
                last_id = str(last.get("id") or "")
                if last_day and last_id.isascii() and last_id.isdigit():
                    window["unreadBound"] = (publication_timestamp(last.get("createDate")) or publication_day_upper_bound(last_day), None)
            window["page"] += 1
            if window["page"] > window["pages"]:
                state["queue"].pop(0)
                if not state["queue"] and state["nextEnd"] < state["minDate"]:
                    state["exhausted"] = True
        return state


ARTISTS = ArtistService(search_pixiv_json)
ACCOUNTS = AccountPages(directory=history_path().parent, request_json=pixiv_json,
    request_homepage=lambda: pixiv_request("https://www.pixiv.net/", max_bytes=8*1024*1024)[0].decode("utf-8"),
    generation=authorization_generation, coordinator=REQUEST_COORDINATOR, proxy=lambda: PIXIV_PROXY,method=AUTH_METHOD.get)
ACCOUNT_AUTH = AccountAuthorization(ACCOUNTS)
CLOUD_STATE = CloudState(history_path().with_name("cloud-state.sqlite3"))


def cloud_web_context(generation):
    assert_authorization_generation(generation)
    if AUTH_METHOD.get() != "web":
        raise AccountError("method", "当前未选择网页登录")
    headers = dict(PIXIV_HEADERS)
    headers.update(web_session_cookie_header())
    if not headers.get("Cookie"):
        raise AccountError("reauth", "请先完成网页登录")
    with PIXIV_NETWORK_LOCK:
        return headers, PIXIV_PROXY, lambda: assert_authorization_generation(generation), getattr(PIXIV_OPENER, "profile", "chrome152")


CLOUD_WEB = WebCloudTransport(REQUEST_COORDINATOR, cloud_web_context)
CLOUD = CloudActions(ACCOUNTS, CLOUD_STATE, CLOUD_WEB.post, SEARCH_SESSION_LOCKS_GUARD)
DISCOVERY = DiscoveryService(pages=ACCOUNTS, context=CLOUD.context,
    follow_states=lambda owner,method,authors:CLOUD_STATE.many(owner,method,[],authors)["follow"])


def check_app_request(generation, cancel_event=None):
    raise_if_search_cancelled(cancel_event)
    assert_authorization_generation(generation)


APP_GALLERY=AppGallery(lambda path,check:ACCOUNTS.account().request("GET",path,check=check),check_app_request)

def popularity_bookmark_metadata(identifier, mode, generation):
    ACCOUNTS.check(generation)
    if AUTH_METHOD.get()=="app":
        raw=ACCOUNTS.account().request("GET","/v1/illust/detail?"+urllib.parse.urlencode({"illust_id":identifier,"filter":"for_android"}),
            check=lambda:ACCOUNTS.check(generation))
        body=normalize_work(raw.get("illust"),allow_r18=mode!="safe",require_bookmarks=False)
        if body["id"]!=identifier:
            raise AccountError("invalid_data","收藏数摘要的作品身份不符")
        return {"bookmarks":body["bookmarks"]}
    body=source_body(pixiv_json(f"https://www.pixiv.net/ajax/illust/{identifier}?lang=zh"))
    if str(body.get("id"))!=identifier:
        raise AccountError("invalid_data","收藏数摘要的作品身份不符")
    validate_detail_policy(body,allow_r18=mode!="safe")
    ACCOUNTS.check(generation)
    return {"bookmarks":bookmark_count(body)}

def popularity_search_metadata(job, generation, cancel_event):
    ACCOUNTS.check(generation)
    result=search_pixiv_results(job["query"],job["mode"],job["sourcePage"],job["workType"],job["includeAi"],
        authorized=True,authorization_epoch=generation,cancel_event=cancel_event,fuzzy=job["fuzzy"],
        start_date=job["startDate"],end_date=job["endDate"],snapshot_id=job["index"],authorize_images=False)
    ACCOUNTS.check(generation)
    return result


POPULARITY=PopularityService(pages=ACCOUNTS, context=CLOUD.context, discovery=DISCOVERY,
    app_get=lambda path,generation:ACCOUNTS.account().request("GET",path,check=lambda:ACCOUNTS.check(generation)),
    web_get=lambda url:search_pixiv_json(url,None),
    detail=popularity_bookmark_metadata, search_get=popularity_search_metadata)


def reset_search_caches() -> None:
    cancel_all_search_requests()
    ARTISTS.clear()
    with SEARCH_SESSION_LOCKS_GUARD, PIXIV_STATE_LOCK:
        for token, row in list(IMAGE_TOKENS.items()):
            if len(row) >= 6:
                IMAGE_TOKENS.pop(token, None)
        HISTORY_CACHE.clear()
        SEARCH_PAGE_CACHE.clear()
        for session in SEARCH_SESSIONS.values():
            if session.get("identityIndex") is not None:
                session["identityIndex"].close()
        SEARCH_SESSIONS.clear()
        SEARCH_SOURCE_OFFSETS.clear()
    with AUTHOR_RESOLUTION_CACHE_LOCK:
        AUTHOR_RESOLUTION_CACHE.clear()


def _drop_search_session(session_key: tuple) -> None:
    with SEARCH_SESSION_LOCKS_GUARD:
        prune_search_image_tokens(session_key)
        session = SEARCH_SESSIONS.pop(session_key, None)
        if session is not None and session.get("identityIndex") is not None:
            session["identityIndex"].close()
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


@contextmanager
def locked_search_session(
    session_key: tuple,
    cancel_event: threading.Event | None = None,
):
    lock = search_session_lock(session_key)
    while not lock.acquire(timeout=0.1):
        raise_if_search_cancelled(cancel_event)
    try:
        raise_if_search_cancelled(cancel_event)
        yield
    finally:
        lock.release()


def _touch_search_session(session_key: tuple) -> dict:
    with SEARCH_SESSION_LOCKS_GUARD:
        now = time.monotonic()
        active_keys = set(SEARCH_SESSION_LOCKS)
        for stale_key, old in list(SEARCH_SESSIONS.items()):
            if stale_key != session_key and stale_key not in active_keys and now - old.get("touched", now) >= SEARCH_SESSION_IDLE_SECONDS:
                _drop_search_session(stale_key)
        session = SEARCH_SESSIONS.pop(session_key, None)
        if session is None:
            session = {
                "items": [], "seen": set(), "baseIndex": 0,
                "sourceDone": {}, "budgetExhausted": False, "truncatedDates": set(),
            }
        session["touched"] = now
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


def assert_search_commit_allowed(
    session_key: tuple,
    session: dict,
    scope: str,
    authorization_epoch: int | None,
    cancel_event: threading.Event | None,
) -> None:
    if scope in {"r18", "all"}:
        assert_authorization_generation(authorization_epoch)
    raise_if_search_cancelled(cancel_event)
    if SEARCH_SESSIONS.get(session_key) is not session:
        raise SearchCancelledError("搜索缓存已重置")


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
    *,
    cancel_event: threading.Event | None = None,
    date_range: SearchDateRange | None = None,
) -> dict:
    state = extend_history(
        tag, mode, need_count, allow_r18=allow_r18, budget=budget,
        namespace=session_key, cancel_event=cancel_event, work_type=session_key[3],
        date_range=date_range,
    )
    source_key = (session_key, tag, mode)
    base = int(state.get("baseOffset", 0))
    with SEARCH_SESSION_LOCKS_GUARD:
        offset = max(base, int(SEARCH_SOURCE_OFFSETS.get(source_key, base)))
    start = min(len(state["items"]), max(0, offset - base))
    rows = list(state["items"][start:])
    has_more = not state["exhausted"] or bool(state["queue"])
    result = {
        "rows": rows,
        "nextOffset": base + len(state["items"]),
        "hasMore": has_more,
        "budgetExhausted": bool(state.get("budgetExhausted")),
        "truncatedDates": list(state.get("truncatedDates") or []),
        "upperBound": _history_unread_bound(state),
    }
    return result


def _history_unread_bound(state: dict) -> tuple[str, int | None] | None:
    if state["exhausted"]:
        return None
    if state["queue"]:
        window = state["queue"][0]
        return window.get("unreadBound", (publication_day_upper_bound(window["end"]), None))
    next_end = state["nextEnd"]
    return (publication_day_upper_bound(next_end), None) if next_end >= state["minDate"] else None


def _unread_bound_key(bound: tuple[str, int | None]) -> tuple[str, int | float]:
    return bound[0], float("inf") if bound[1] is None else bound[1]


def _newest_unread_bound(session: dict, sources: list, initial_day: str):
    bounds = [
        session.get("sourceBounds", {}).get(source, (publication_day_upper_bound(initial_day), None))
        for source in sources if not session["sourceDone"].get(source)
    ]
    return max((bound for bound in bounds if bound is not None), key=_unread_bound_key, default=None)


@contextmanager
def _allocated_search_source_budget(budget: dict, remaining_sources: int):
    """Reserve a fair share while retaining the shared request/time totals."""
    now = time.monotonic()
    count = max(1, remaining_sources)
    remaining_requests = max(0, MAX_HISTORY_REQUESTS - budget["requests"])
    remaining_seconds = max(0.0, budget["started"] + MAX_HISTORY_SECONDS - now)
    budget["requestLimit"] = min(
        MAX_HISTORY_REQUESTS, budget["requests"] + max(1, remaining_requests // count),
    )
    budget["deadline"] = now + remaining_seconds / count
    try:
        yield
    finally:
        budget.pop("requestLimit", None)
        budget.pop("deadline", None)


def _search_sort_key(item: dict) -> tuple[str, int]:
    try:
        artwork_number = int(item.get("id") or 0)
    except (TypeError, ValueError):
        artwork_number = 0
    return str(item.get("sortDate") or item.get("date") or ""), artwork_number


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
        user_id for user_id in map(str, ordered_ids) if valid_pixiv_id(user_id)
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


def resolve_author_user(
    author: str,
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str]:
    raise_if_search_cancelled(cancel_event)
    cache_key = str(author).strip().casefold()
    now = time.monotonic()
    with AUTHOR_RESOLUTION_CACHE_LOCK:
        cached = AUTHOR_RESOLUTION_CACHE.pop(cache_key, None)
        if cached is not None and cached[0] > now:
            AUTHOR_RESOLUTION_CACHE[cache_key] = cached
            return cached[1], cached[2]
    body = search_pixiv_json(build_user_search_url(author), cancel_event).get("body") or {}
    rows = _author_rows_from_payload(body)
    target = str(author).strip().casefold()
    matches = [
        row for row in rows
        if str(row.get("name") or row.get("userName") or "").strip().casefold() == target
    ]
    if len(matches) > 1:
        raise SearchInputError("找到多个同名作者，请先选择作者或使用 UID 搜索")
    exact = matches[0] if matches else None
    if exact is None:
        raise SearchInputError(f"未找到名称完全匹配“{author}”的 Pixiv 画师；可改用 uid:数字 精确搜索")
    user_id = str(exact.get("userId") or exact.get("id") or "")
    if not valid_pixiv_id(user_id):
        raise SearchInputError("Pixiv 未返回有效的画师用户 ID")
    resolved_name = str(exact.get("name") or exact.get("userName") or author)
    with AUTHOR_RESOLUTION_CACHE_LOCK:
        for key, row in list(AUTHOR_RESOLUTION_CACHE.items()):
            if row[0] <= now:
                AUTHOR_RESOLUTION_CACHE.pop(key, None)
        AUTHOR_RESOLUTION_CACHE[cache_key] = (
            now + AUTHOR_RESOLUTION_TTL_SECONDS,
            user_id,
            resolved_name,
        )
        while len(AUTHOR_RESOLUTION_CACHE) > MAX_AUTHOR_RESOLUTION_CACHE:
            AUTHOR_RESOLUTION_CACHE.popitem(last=False)
    return user_id, resolved_name


def load_user_profile_ids(
    user_id: str,
    *,
    work_type: str = "all",
    cancel_event: threading.Event | None = None,
) -> list[str]:
    body = search_pixiv_json(build_user_profile_all_url(user_id), cancel_event).get("body") or {}
    if not isinstance(body, dict):
        raise PixivPolicyError("Pixiv 画师作品索引格式异常")
    ids: set[str] = set()
    # Pixiv's index distinguishes manga from illustration/ugoira IDs, but
    # does not distinguish static illustrations from animation; rows still
    # receive the exact type and authorization checks after batch loading.
    categories = ("illusts", "manga") if work_type == "all" else (
        ("manga",) if work_type == "manga" else ("illusts",)
    )
    for category in categories:
        rows = body.get(category)
        candidates = rows.keys() if isinstance(rows, dict) else (rows if isinstance(rows, list) else [])
        for artwork_id in candidates:
            clean = str(artwork_id or "")
            if valid_pixiv_id(clean):
                ids.add(clean)
    return sorted(ids, key=int, reverse=True)


def load_user_profile_works(
    user_id: str,
    artwork_ids: list[str],
    *,
    cancel_event: threading.Event | None = None,
) -> list[dict]:
    body = search_pixiv_json(build_user_profile_works_url(user_id, artwork_ids), cancel_event).get("body") or {}
    if not isinstance(body, dict):
        raise PixivPolicyError("Pixiv 画师作品结果格式异常")
    works = body.get("works")
    if works is None:
        works = body
    if isinstance(works, dict):
        rows = list(works.values())
    elif isinstance(works, list):
        rows = works
    else:
        raise PixivPolicyError("Pixiv 画师作品结果格式异常")
    # Keep response-container validation above, but isolate individual bad rows
    # before the normal per-artwork policy and field checks.
    return [row for row in rows if isinstance(row, dict)]


def search_user_results(
    query_kind: str, target: str, scope: str, page: int,
    work_type: str, include_ai: bool, *, authorized: bool, fuzzy: bool = False,
    authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    prefetch: bool = False,
    prefetch_ahead: int = SEARCH_PREFETCH_AHEAD,
    date_range: SearchDateRange | None = None,
    snapshot_id: str = "",
) -> dict:
    raise_if_search_cancelled(cancel_event)
    resolve_source_modes(scope, authorized=authorized)
    if query_kind == "author":
        user_id, resolved_name = (
            resolve_author_user(target)
            if cancel_event is None
            else resolve_author_user(target, cancel_event=cancel_event)
        )
    else:
        user_id, resolved_name = target, ""
    page = max(1, int(page))
    session_key = (
        "user", query_kind, target.casefold(), user_id,
        scope, work_type, bool(include_ai), bool(fuzzy),
    )
    if date_range is not None:
        session_key += (date_range,)
    if snapshot_id:
        session_key += (("snapshot", snapshot_id),)
    desired_items = prefetch_item_count(
        page, per_page=SEARCH_PER_PAGE, ahead=prefetch_ahead if prefetch else 0,
    )

    with locked_search_session(session_key, cancel_event):
        raise_if_search_cancelled(cancel_event)
        session = _touch_search_session(session_key)
        with SEARCH_SESSION_LOCKS_GUARD:
            assert_search_commit_allowed(session_key, session, scope, authorization_epoch, cancel_event)
            result_index = session.get("identityIndex")
            if result_index is None:
                result_index = session["identityIndex"] = SearchResultIndex()
            window_end = int(session["baseIndex"]) + len(session["items"])
            if ((page - 1) * SEARCH_PER_PAGE < int(session["baseIndex"])
                    or min(desired_items, result_index.emitted_count) > window_end):
                start = min(result_index.emitted_count, (max(1, page - SEARCH_KEEP_BEHIND) - 1) * SEARCH_PER_PAGE)
                end = min(result_index.emitted_count, (page + SEARCH_PREFETCH_AHEAD) * SEARCH_PER_PAGE)
                session["items"] = result_index.read_results(start, end)
                session["baseIndex"] = start
                session["seen"] = {item["id"] for item in session["items"]}
        if "profileIds" not in session or session.get("targetUserId") != user_id:
            profile_ids = (
                load_user_profile_ids(user_id, work_type=work_type)
                if cancel_event is None
                else load_user_profile_ids(user_id, work_type=work_type, cancel_event=cancel_event)
            )
            with SEARCH_SESSION_LOCKS_GUARD:
                assert_search_commit_allowed(
                    session_key, session, scope, authorization_epoch, cancel_event,
                )
                session["profileIds"] = profile_ids
                session["profileOffset"] = 0
                session["targetUserId"] = user_id
                session["artist"] = resolved_name
        ids = session["profileIds"]
        request_started = time.monotonic()
        request_count = 0
        while (
            result_index.emitted_count < desired_items
            and int(session["profileOffset"]) < len(ids)
            and request_count < MAX_USER_SEARCH_REQUESTS
            and time.monotonic() - request_started < MAX_USER_SEARCH_SECONDS
        ):
            raise_if_search_cancelled(cancel_event)
            start = int(session["profileOffset"])
            batch_ids = ids[start:start + 48]
            raw_rows = (
                load_user_profile_works(user_id, batch_ids)
                if cancel_event is None
                else load_user_profile_works(
                    user_id, batch_ids, cancel_event=cancel_event,
                )
            )
            raise_if_search_cancelled(cancel_event)
            # Commit the cursor only after the request and basic response-shape
            # validation succeed. Transient failures must retry this same batch.
            request_count += 1
            incoming: list[dict] = []
            for raw in raw_rows:
                try:
                    candidate = normalize_search_item(raw, allow_r18=scope in {"r18", "all"})
                except PixivPolicyError:
                    continue
                if candidate["userId"] != user_id:
                    continue
                if scope in {"safe", "r18"} and candidate["restriction"] != scope:
                    continue
                if work_type != "all" and candidate["workType"] != work_type:
                    continue
                if not include_ai and candidate["aiGenerated"]:
                    continue
                if date_range and not date_range.contains(candidate["date"]):
                    continue
                incoming.append(candidate)
            incoming.sort(key=_search_sort_key, reverse=True)
            with SEARCH_SESSION_LOCKS_GUARD:
                assert_search_commit_allowed(
                    session_key, session, scope, authorization_epoch, cancel_event,
                )
                committed = result_index.append_results(incoming)
                session["items"].extend(committed)
                session["seen"].update(candidate["id"] for candidate in committed)
                # Row conversion is part of consuming this batch. Do not skip the
                # IDs if an unexpected malformed field aborts conversion midway.
                session["profileOffset"] = start + len(batch_ids)

        loaded = result_index.emitted_count
        exhausted = int(session["profileOffset"]) >= len(ids)
        budget_exhausted = not exhausted and (
            request_count >= MAX_USER_SEARCH_REQUESTS
            or time.monotonic() - request_started >= MAX_USER_SEARCH_SECONDS
        )
        complete_through = loaded // SEARCH_PER_PAGE
        requested_page_start = (page - 1) * SEARCH_PER_PAGE
        if loaded > requested_page_start:
            complete_through = max(complete_through, page)
        if (exhausted or budget_exhausted) and loaded:
            complete_through = max(
                complete_through,
                (loaded + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE,
            )
        page_rows = build_result_page_rows(
            session["items"],
            base_index=int(session["baseIndex"]),
            current_page=page,
            complete_through=complete_through,
            per_page=SEARCH_PER_PAGE,
            ahead=SEARCH_PREFETCH_AHEAD,
        )
        with SEARCH_SESSION_LOCKS_GUARD:
            assert_search_commit_allowed(
                session_key, session, scope, authorization_epoch, cancel_event,
            )
            SEARCH_PAGE_CACHE.store_pages(session_key, page, page_rows)
            selected = SEARCH_PAGE_CACHE.get_page(session_key, page) or []
            available_pages = SEARCH_PAGE_CACHE.available_pages(session_key)
            remove_count = result_window_trim_count(
                len(session["items"]),
                base_index=int(session["baseIndex"]),
                current_page=page,
                per_page=SEARCH_PER_PAGE,
                keep_behind=SEARCH_KEEP_BEHIND,
            )
            if remove_count:
                del session["items"][:remove_count]
                session["baseIndex"] += remove_count
                session["seen"] = {str(item["id"]) for item in session["items"]}
            prune_search_image_tokens(
                session_key, retained_pages=set(available_pages), replace_page=None if prefetch else page,
            )
            authorized_items = [] if prefetch else [
                authorize_item_images(
                    copy.deepcopy(item),
                    search_session=session_key,
                    search_page=page,
                    authorization_epoch=authorization_epoch,
                )
                for item in selected
            ]
            preloaded_through = SEARCH_PAGE_CACHE.preloaded_through(session_key)
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
            "preloadedThrough": preloaded_through,
            "items": authorized_items, "perPage": SEARCH_PER_PAGE, "hasMore": not exhausted,
            "budgetExhausted": budget_exhausted, "truncatedDates": [],
            "workType": work_type, "includeAi": bool(include_ai),
            "mode": "pixiv-user-search",
            "snapshotId": snapshot_id,
        }


def search_artwork_result(
    artwork_id: str, scope: str, page: int, work_type: str, include_ai: bool,
    *, authorized: bool, authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    date_range: SearchDateRange | None = None,
) -> dict:
    raise_if_search_cancelled(cancel_event)
    resolve_source_modes(scope, authorized=authorized)
    network_cancel_event = cancel_event or threading.Event()
    try:
        item = pixiv_item_for_download(
            artwork_id,
            allow_r18=scope in {"r18", "all"},
            authorization_epoch=authorization_epoch,
            cancel_event=network_cancel_event,
            require_thumb=True,
        )
    except PixivPolicyError as exc:
        if scope != "safe" or str(exc) != "restricted artwork":
            raise
        item = None
    raise_if_search_cancelled(cancel_event)

    matches = item is not None
    if matches and scope != "all" and item.get("restriction") != scope:
        matches = False
    if matches and work_type != "all" and item.get("workType") != work_type:
        matches = False
    if matches and not include_ai and bool(item.get("aiGenerated")):
        matches = False
    if matches and date_range and not date_range.contains(item.get("date") or ""):
        matches = False

    requested_page = max(1, int(page))
    selected = [item] if matches and requested_page == 1 else []
    label = str((item or {}).get("title") or f"Pixiv 作品 {artwork_id}")
    return {
        "tag": artwork_id, "tags": [], "label": label,
        "artist": str((item or {}).get("artist") or ""),
        "searchType": "pid", "targetArtworkId": artwork_id, "scope": scope,
        "total": int(matches), "reportedTotal": int(matches), "page": requested_page,
        "pages": 1, "pageNumbers": [1], "availablePages": [1],
        "preloadedThrough": 1, "items": selected, "perPage": SEARCH_PER_PAGE,
        "hasMore": False, "budgetExhausted": False, "truncatedDates": [],
        "workType": work_type, "includeAi": bool(include_ai),
        "mode": "pixiv-artwork-search",
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
    cancel_event: threading.Event | None = None,
    prefetch: bool = False,
    prefetch_ahead: int = SEARCH_PREFETCH_AHEAD,
    start_date: str = "",
    end_date: str = "",
    snapshot_id: str = "",
    authorize_images: bool = True,
) -> dict:
    raise_if_search_cancelled(cancel_event)
    snapshot_id = parse_search_snapshot_id(snapshot_id)
    if AUTH_METHOD.get()=="app":
        if not authorized:raise AccountError("reauth","请先在「我的账号」完成 App 授权，或选择网页登录")
        generation=authorization_generation()
        result=APP_GALLERY.search(tag_query,scope,page,work_type,include_ai,generation=generation,fuzzy=fuzzy,
            start_date=start_date,end_date=end_date,snapshot_id=snapshot_id,cancel_event=cancel_event,prefetch=prefetch)
        if authorize_images:
            result["items"]=[authorize_item_images(item,authorization_epoch=generation,account_bound=True) for item in result["items"]]
        assert_authorization_generation(generation)
        return result
    if work_type not in {"all", "illustration", "manga", "ugoira"}:
        raise SearchInputError("不支持的作品类型")
    date_range = parse_search_date_range(start_date, end_date)
    query = parse_search_query(tag_query)
    if query.kind == "pid":
        result = search_artwork_result(
            query.value, scope, page, work_type, include_ai,
            authorized=authorized,
            authorization_epoch=authorization_epoch,
            cancel_event=cancel_event,
            date_range=date_range,
        )
        result["snapshotId"] = snapshot_id
        return result
    if query.kind in {"uid", "author"}:
        kwargs = {
            "authorized": authorized,
            "fuzzy": fuzzy,
            "authorization_epoch": authorization_epoch,
        }
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        if prefetch:
            kwargs["prefetch"] = True
            kwargs["prefetch_ahead"] = prefetch_ahead
        if date_range is not None:
            kwargs["date_range"] = date_range
        if snapshot_id:
            kwargs["snapshot_id"] = snapshot_id
        return search_user_results(
            query.kind, query.value, scope, page, work_type, include_ai, **kwargs,
        )
    modes = resolve_source_modes(scope, authorized=authorized)
    tags = parse_search_tags(query.value)
    max_aliases = max(
        1,
        min(8, MAX_HISTORY_SOURCES // max(1, len(tags) * len(modes))),
    )
    tag_groups = build_search_tag_groups(
        query.value,
        fuzzy=fuzzy,
        max_tags=len(tags),
        max_aliases=max_aliases,
    )
    page = max(1, int(page))
    session_key = ("tags", tag_groups, scope, work_type, bool(include_ai), bool(fuzzy))
    if date_range is not None:
        session_key += (date_range,)
    if snapshot_id:
        session_key += (("snapshot", snapshot_id),)
    desired_items = prefetch_item_count(
        page, per_page=SEARCH_PER_PAGE, ahead=prefetch_ahead if prefetch else 0,
    )

    with locked_search_session(session_key, cancel_event):
        raise_if_search_cancelled(cancel_event)
        session = _touch_search_session(session_key)
        with SEARCH_SESSION_LOCKS_GUARD:
            assert_search_commit_allowed(session_key, session, scope, authorization_epoch, cancel_event)
            first_retained_page = int(session["baseIndex"]) // SEARCH_PER_PAGE + 1
            result_index = session.get("identityIndex")
            if result_index is not None and result_index.emitted_count and (
                page < first_retained_page
                or min(desired_items, result_index.emitted_count) > int(session["baseIndex"]) + len(session["items"])
            ):
                start = min(result_index.emitted_count, (max(1, page - SEARCH_KEEP_BEHIND) - 1) * SEARCH_PER_PAGE)
                end = min(result_index.emitted_count, (page + SEARCH_PREFETCH_AHEAD) * SEARCH_PER_PAGE)
                session["items"] = result_index.read_results(start, end)
                session["baseIndex"] = start
                session["seen"] = {item["id"] for item in session["items"]}
            elif page < first_retained_page:
                _drop_search_session(session_key)
                session = _touch_search_session(session_key)
            absolute_loaded = result_index.emitted_count if result_index is not None else 0
        budget = {"started": time.monotonic(), "requests": 0}
        source_tags = tuple(dict.fromkeys(alias for group in tag_groups for alias in group))
        sources = [(tag, mode) for tag in source_tags for mode in modes]
        source_options = {"date_range": date_range} if date_range is not None else {}
        initial_day = (date_range.end if date_range else datetime.now(timezone(timedelta(hours=9))).date()).isoformat()
        literal_and = not fuzzy and len(tag_groups) > 1
        session.setdefault("sourceBounds", {})
        session.setdefault("sourceAnchors", {})
        session["budgetExhausted"] = False
        if session.get("identityIndex") is None:
            session["identityIndex"] = SearchResultIndex()
        rounds = 0
        while absolute_loaded < desired_items and rounds < MAX_HISTORY_REQUESTS:
            raise_if_search_cancelled(cancel_event)
            with SEARCH_SESSION_LOCKS_GUARD:
                assert_search_commit_allowed(session_key, session, scope, authorization_epoch, cancel_event)
                ready = session["identityIndex"].take_ready(
                    _newest_unread_bound(session, sources, initial_day),
                    max(60, desired_items - absolute_loaded),
                )
                session["items"].extend(ready)
                session["seen"].update(item["id"] for item in ready)
            absolute_loaded = session["identityIndex"].emitted_count
            if absolute_loaded >= desired_items:
                break
            missing = desired_items - absolute_loaded
            # Foreground reads only the useful page target. Background prefetch
            # may over-read to absorb filtered rows without delaying first paint.
            per_source = max(
                SEARCH_PER_PAGE,
                (missing * (2 if prefetch else 1) + len(sources) - 1) // len(sources),
            )
            incoming: list[dict] = []
            source_commits: list[tuple[str, str, int]] = []
            source_done: dict[tuple[str, str], bool] = {}
            source_bounds: dict[tuple[str, str], tuple[str, int | None] | None] = {}
            source_anchors = dict(session["sourceAnchors"])
            round_budget_exhausted = False
            round_truncated_dates: set[str] = set()
            any_more = False
            any_rows = False
            cursor = int(session.get("sourceCursor", 0)) % len(sources)
            ordered_sources = list(enumerate(sources))
            ordered_sources = ordered_sources[cursor:] + ordered_sources[:cursor]
            newest_bound = _newest_unread_bound(session, sources, initial_day)
            pending_sources = [
                entry for entry in ordered_sources
                if not session["sourceDone"].get(entry[1])
                and (newest_bound is None or _unread_bound_key(
                    session["sourceBounds"].get(entry[1], (publication_day_upper_bound(initial_day), None))
                ) == _unread_bound_key(newest_bound))
            ]
            next_source_cursor = cursor
            for index, (source_index, (tag, mode)) in enumerate(pending_sources):
                raise_if_search_cancelled(cancel_event)
                source_key = (tag, mode)
                if literal_and and mode in source_anchors and source_anchors[mode] != tag:
                    source_done[source_key] = True
                    source_bounds[source_key] = None
                    continue
                with SEARCH_SESSION_LOCKS_GUARD:
                    absolute_offset = int(SEARCH_SOURCE_OFFSETS.get((session_key, tag, mode), 0))
                requests_before = budget["requests"]
                with _allocated_search_source_budget(budget, len(pending_sources) - index):
                    if cancel_event is None:
                        source = load_search_source(
                            session_key, tag, mode, absolute_offset + per_source,
                            mode == "r18", budget, **source_options,
                        )
                    else:
                        source = load_search_source(
                            session_key, tag, mode, absolute_offset + per_source,
                            mode == "r18", budget, cancel_event=cancel_event, **source_options,
                        )
                if budget["requests"] > requests_before:
                    next_source_cursor = (source_index + 1) % len(sources)
                round_budget_exhausted = round_budget_exhausted or source["budgetExhausted"]
                round_truncated_dates.update(source["truncatedDates"])
                source_done[source_key] = not source["hasMore"]
                next_offset = source.get("nextOffset")
                if next_offset is None:
                    next_offset = absolute_offset + len(source["rows"])
                source_commits.append((tag, mode, max(absolute_offset, int(next_offset))))
                any_more = any_more or source["hasMore"]
                any_rows = any_rows or bool(source["rows"])
                normalized_rows = []
                for raw in source["rows"]:
                    try:
                        candidate = normalize_search_item(raw, allow_r18=mode == "r18")
                    except PixivPolicyError:
                        continue
                    normalized_rows.append(candidate)
                    if work_type != "all" and candidate["workType"] != work_type:
                        continue
                    if not include_ai and candidate["aiGenerated"]:
                        continue
                    if matches_tag_groups(candidate.get("tags") or [], tag_groups):
                        incoming.append(candidate)
                        if literal_and and mode not in source_anchors:
                            # Every strict-AND match occurs in each literal tag
                            # stream. Once one confirms the intersection, the
                            # other streams in this scope are redundant.
                            source_anchors[mode] = tag
                if "upperBound" in source:
                    source_bounds[source_key] = source["upperBound"]
                elif not source["hasMore"]:
                    source_bounds[source_key] = None
                elif normalized_rows:
                    source_bounds[source_key] = min(map(_search_sort_key, normalized_rows))
                else:
                    source_bounds[source_key] = session["sourceBounds"].get(source_key, (publication_day_upper_bound(initial_day), None))
                if literal_and and mode in source_anchors:
                    for other_tag, other_mode in sources:
                        if other_mode == mode and other_tag != source_anchors[mode]:
                            source_done[(other_tag, other_mode)] = True
                            source_bounds[(other_tag, other_mode)] = None
            incoming.sort(key=_search_sort_key, reverse=True)
            with SEARCH_SESSION_LOCKS_GUARD:
                assert_search_commit_allowed(
                    session_key, session, scope, authorization_epoch, cancel_event,
                )
                session["budgetExhausted"] = (
                    session["budgetExhausted"] or round_budget_exhausted
                )
                session["truncatedDates"].update(round_truncated_dates)
                session["sourceDone"].update(source_done)
                session["sourceBounds"].update(source_bounds)
                session["sourceAnchors"] = source_anchors
                session["sourceCursor"] = next_source_cursor
                session["identityIndex"].add(incoming)
                ready = session["identityIndex"].take_ready(
                    _newest_unread_bound(session, sources, initial_day),
                    max(60, desired_items - absolute_loaded),
                )
                session["items"].extend(ready)
                session["seen"].update(item["id"] for item in ready)
                for source_tag, source_mode, next_offset in source_commits:
                    SEARCH_SOURCE_OFFSETS[(session_key, source_tag, source_mode)] = next_offset
            for source_tag, source_mode, _next_offset in source_commits:
                _trim_history_source(session_key, source_tag, source_mode)
            absolute_loaded = session["identityIndex"].emitted_count
            rounds += 1
            if not any_rows or (not any_more and not incoming):
                break
            if budget["requests"] >= MAX_HISTORY_REQUESTS:
                session["budgetExhausted"] = True
                break

        has_more = any(not session["sourceDone"].get(source) for source in sources) or bool(session["identityIndex"].pending_count)
        session["budgetExhausted"] = bool(has_more and absolute_loaded < desired_items)
        ordering_pending = bool(absolute_loaded < desired_items and session["identityIndex"].pending_count
                                and _newest_unread_bound(session, sources, initial_day) is not None)

        absolute_result_count = session["identityIndex"].emitted_count
        complete_through = absolute_result_count // SEARCH_PER_PAGE
        requested_page_start = (page - 1) * SEARCH_PER_PAGE
        if absolute_result_count > requested_page_start:
            complete_through = max(complete_through, page)
        if (session["budgetExhausted"] or not has_more) and absolute_result_count:
            complete_through = max(
                complete_through,
                (absolute_result_count + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE,
            )
        page_rows = build_result_page_rows(
            session["items"],
            base_index=int(session["baseIndex"]),
            current_page=page,
            complete_through=complete_through,
            per_page=SEARCH_PER_PAGE,
            ahead=SEARCH_PREFETCH_AHEAD,
        )
        with SEARCH_SESSION_LOCKS_GUARD:
            assert_search_commit_allowed(
                session_key, session, scope, authorization_epoch, cancel_event,
            )
            SEARCH_PAGE_CACHE.store_pages(session_key, page, page_rows)
            selected = SEARCH_PAGE_CACHE.get_page(session_key, page) or []
            available_pages = SEARCH_PAGE_CACHE.available_pages(session_key)

            remove_count = result_window_trim_count(
                len(session["items"]),
                base_index=int(session["baseIndex"]),
                current_page=page,
                per_page=SEARCH_PER_PAGE,
                keep_behind=SEARCH_KEEP_BEHIND,
            )
            if remove_count:
                del session["items"][:remove_count]
                session["baseIndex"] += remove_count
                session["seen"] = {str(item["id"]) for item in session["items"]}
            has_more = any(not session["sourceDone"].get(source) for source in sources) or bool(session["identityIndex"].pending_count)
            prune_search_image_tokens(
                session_key, retained_pages=set(available_pages), replace_page=None if prefetch else page,
            )
            authorized_items = [] if prefetch else [
                authorize_item_images(
                    copy.deepcopy(item),
                    search_session=session_key,
                    search_page=page,
                    authorization_epoch=authorization_epoch,
                )
                for item in selected
            ] if authorize_images else copy.deepcopy(selected)
            preloaded_through = SEARCH_PAGE_CACHE.preloaded_through(session_key)
        if scope in {"r18", "all"}:
            try:
                assert_authorization_generation(authorization_epoch)
            except AuthorizationRevokedError:
                prune_search_image_tokens(session_key)
                _drop_search_session(session_key)
                raise
        return {
            "tag": "；".join(tags), "tags": list(tags), "tagGroups": [list(group) for group in tag_groups], "fuzzy": bool(fuzzy), "scope": scope,
            "total": session["identityIndex"].emitted_count, "reportedTotal": None,
            "page": page, "pages": available_pages[-1] if available_pages else 1,
            "pageNumbers": available_pages, "availablePages": available_pages,
            "preloadedThrough": preloaded_through,
            "items": authorized_items, "perPage": SEARCH_PER_PAGE, "hasMore": has_more,
            "budgetExhausted": bool(session["budgetExhausted"]),
            "orderingPending": ordering_pending,
            "truncatedDates": sorted(session["truncatedDates"], reverse=True),
            "workType": work_type, "includeAi": bool(include_ai), "fuzzy": bool(fuzzy),
            "snapshotId": snapshot_id,
            "mode": "pixiv-authorized-all" if scope == "all" else (
                "pixiv-authorized-r18" if scope == "r18" else "pixiv-public-history"
            ),
        }


def pixiv_detail(
    artwork_id: str, allow_r18: bool = False, *,
    authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    network_timeout: float | None = None,
) -> dict:
    if not artwork_id.isdigit():
        raise PixivPolicyError("无效作品 ID")
    generation = authorization_generation()
    timeout = 90 if network_timeout is None else network_timeout
    try:
        item = DETAIL_RESOURCES.get(
            (artwork_id, generation, allow_r18, authorization_epoch, timeout),
            lambda event: _fetch_pixiv_detail(
                artwork_id, allow_r18, authorization_epoch=authorization_epoch,
                cancel_event=event, network_timeout=timeout,
            ), ttl=0, cancel_event=cancel_event,
        )
    except PreviewCancelledError as exc:
        raise SearchCancelledError("作品详情读取已取消") from exc
    except PreviewBusyError as exc:
        raise RequestDeferredError("request_queue", 2) from exc
    if authorization_generation() != generation:
        raise AuthorizationRevokedError("Pixiv 账户状态已变更，请重新打开作品")
    return item


def _fetch_pixiv_detail(
    artwork_id: str,
    allow_r18: bool = False,
    *,
    authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    network_timeout: float = 90,
) -> dict:
    if AUTH_METHOD.get()=="app":
        generation=authorization_generation()
        check_app_request(generation,cancel_event)
        normalized=APP_GALLERY.detail(artwork_id,generation=generation,allow_r18=allow_r18,cancel_event=cancel_event)
        item=authorize_item_images(normalized,authorization_epoch=generation,account_bound=True)
        try:
            check_app_request(generation,cancel_event)
            return cache_pixiv_item(item)
        except Exception:
            revoke_item_image_tokens(item)
            raise
    detail_url = f"https://www.pixiv.net/ajax/illust/{artwork_id}?lang=zh"
    pages_url = f"https://www.pixiv.net/ajax/illust/{artwork_id}/pages?lang=zh"
    detail = pixiv_json(detail_url, timeout=network_timeout, cancel_event=cancel_event).get("body") or {}
    raise_if_search_cancelled(cancel_event)
    pages = pixiv_json(pages_url, timeout=network_timeout, cancel_event=cancel_event).get("body") or []
    raise_if_search_cancelled(cancel_event)
    normalized = normalize_detail(detail, pages, allow_r18=allow_r18)
    if normalized.get("id") != artwork_id:
        raise PixivPolicyError("Pixiv 返回的作品 ID 不匹配")
    restricted = normalized.get("restriction") == "r18"
    if restricted:
        if authorization_epoch is None:
            raise AuthorizationRevokedError("Pixiv 授权已撤销，请重新打开作品")
        assert_authorization_generation(authorization_epoch)
    item = authorize_item_images(
        normalized,
        authorization_epoch=authorization_epoch,
    )
    try:
        raise_if_search_cancelled(cancel_event)
        if not restricted:
            return cache_pixiv_item(item)
        # Commit the restricted cache while holding the same generation guard
        # used by logout. If logout won the race, discard every token created by
        # this request and return no restricted response.
        with SEARCH_SESSION_LOCKS_GUARD:
            if (
                not AUTHORIZATION_ACTIVE
                or AUTHORIZATION_GENERATION != authorization_epoch
            ):
                raise AuthorizationRevokedError("Pixiv 授权已撤销，请重新打开作品")
            return cache_pixiv_item(item)
    except Exception:
        revoke_item_image_tokens(item)
        raise


UGOIRA_ZIP_MAGIC = b"PK\x03\x04"


def pixiv_ugoira_meta(
    artwork_id: str,
    *,
    authorization_epoch: int | None = None,
    cancel_event: threading.Event | None = None,
    network_timeout: float | None = None,
) -> dict:
    """Fetch the ugoira animation manifest (frame zip URLs and delays)."""
    if authorization_epoch is not None:
        assert_authorization_generation(authorization_epoch)
    try:
        meta = UGOIRA_RESOURCES.get(
            ("meta-download" if network_timeout is not None else "meta", artwork_id, authorization_epoch),
            lambda event: _fetch_ugoira_meta(artwork_id, authorization_epoch, event, timeout=network_timeout or 25),
            ttl=60, cancel_event=cancel_event,
        )
    except (PreviewCancelledError, SearchCancelledError) as exc:
        if authorization_epoch is not None:
            assert_authorization_generation(authorization_epoch)
        if cancel_event is not None:
            raise SearchCancelledError("动图信息读取已取消") from exc
        raise PixivPolicyError("动图信息读取已取消，请重试") from exc
    except PreviewBusyError as exc:
        raise PixivPolicyError("动图资源繁忙，请稍后再试") from exc
    if authorization_epoch is not None:
        assert_authorization_generation(authorization_epoch)
    return meta


def _fetch_ugoira_meta(artwork_id, authorization_epoch, cancel_event, *, timeout=25) -> dict:
    if AUTH_METHOD.get()=="app":
        return APP_GALLERY.ugoira(artwork_id,generation=authorization_generation(),cancel_event=cancel_event)
    body = pixiv_json(build_ugoira_meta_url(artwork_id), timeout=timeout, cancel_event=cancel_event).get("body") or {}
    if not isinstance(body, dict):
        raise PixivPolicyError("Pixiv 动图数据格式异常")
    original_src = str(body.get("originalSrc") or "")
    src = str(body.get("src") or "")
    if not is_allowed_pixiv_url(original_src, image_only=True) or not is_allowed_pixiv_url(src, image_only=True):
        raise PixivPolicyError("invalid image host")
    frames = body.get("frames")
    if not isinstance(frames, list) or not frames or any(
        not isinstance(frame, dict)
        or not str(frame.get("file") or "").strip()
        or not isinstance(frame.get("delay"), int)
        for frame in frames
    ):
        raise PixivPolicyError("Pixiv 动图帧数据格式异常")
    if authorization_epoch is not None:
        assert_authorization_generation(authorization_epoch)
    return {
        "src": src,
        "originalSrc": original_src,
        "mime": str(body.get("mime_type") or "image/jpeg"),
        "frames": [
            {"file": str(frame["file"]), "delay": int(frame["delay"])}
            for frame in frames
        ],
    }


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


def pixiv_error_payload(action: str, exc: Exception, *, saving: bool = False) -> dict:
    payload = {"error": public_pixiv_error(action, exc, saving=saving)}
    if isinstance(exc, RequestDeferredError):
        payload.update(errorKind=exc.kind, retryAfterSeconds=exc.retry_after)
    elif isinstance(exc, RequestBlockedError):
        payload["errorKind"] = exc.kind
    return payload


def public_pixiv_error(action: str, exc: Exception, *, saving: bool = False) -> str:
    data_error = isinstance(exc, (TypeError, ValueError, KeyError)) and not isinstance(
        exc, (PixivPolicyError, json.JSONDecodeError, OSError))
    if data_error:
        HTTP_LOG.exception("Unexpected %s data-processing failure", action)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {401, 403}:
            detail = "Pixiv 拒绝了访问，请检查登录状态或作品权限"
        elif exc.code == 429:
            detail = "Pixiv 请求过于频繁，请稍后重试"
        else:
            detail = "Pixiv 暂时返回异常响应"
    elif isinstance(exc, PixivPolicyError):
        detail = str(exc) or "请求不符合安全策略"
    elif isinstance(exc, (json.JSONDecodeError, UnicodeError)) or data_error:
        detail = "Pixiv 返回的数据格式异常"
    else:
        failure = classify_network_error(exc)
        labels = {
            "timeout": "连接超时",
            "refused": "连接被拒绝",
            "tls": "TLS 握手或连接异常",
            "certificate": "证书校验失败，请检查系统时间和代理证书信任",
            "tls_protocol": "TLS 协议异常，请检查本地代理协议与端口",
            "dns": "域名解析失败",
            "reset": "网络连接被重置或传输中断",
            "proxy": "本地代理隧道连接失败",
            "http": "Pixiv 暂时返回异常响应",
            "unavailable": "网络连接中断或不可用",
        }
        detail = labels.get(failure.kind, "网络连接中断或不可用")
        if failure.code in {"TLS_EOF", "UNEXPECTED_EOF_WHILE_READING"}:
            detail = "TLS 连接被提前关闭"
        if (
            saving
            and failure.kind == "unavailable"
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
    assert_image_token_authorization(approved)
    return str(approved[2])


@contextmanager
def owned_staging_batch():
    """Release partial staging on failure; transfer ownership on success."""
    staged: list[PublishedFileOwnership] = []
    try:
        yield staged
    except Exception as original:
        failures = _discard_owned_staging(staged)
        if failures:
            raise PublishRollbackError(
                f"暂存失败且有 {failures} 个文件未能安全清理"
            ) from original
        raise


@contextmanager
def _fetch_ugoira_source(
    item: dict, quality: str, staging_root: Path | None, *,
    authorization_epoch: int | None,
    cancel_event: threading.Event | None,
    maximum_bytes: int = MAX_UGOIRA_BYTES,
):
    artwork_id = str(item.get("id") or "")
    HTTP_LOG.info("download artwork=%s stage=animation-metadata", artwork_id)
    meta = pixiv_ugoira_meta(artwork_id, authorization_epoch=authorization_epoch, cancel_event=cancel_event, network_timeout=90)
    zip_url = str(meta["originalSrc"] if quality == "original" else meta["src"])
    # Temporary files keep large ZIPs and encoded outputs off the Python heap.
    # Publication still copies through the existing exclusively owned handles.
    with tempfile.TemporaryFile(dir=staging_root) as source:
        HTTP_LOG.info("download artwork=%s stage=archive", artwork_id)
        pixiv_request(zip_url, image_only=True, cancel_event=cancel_event,
                      max_bytes=maximum_bytes, output=source, timeout=90, max_duration=600, download=True, archive=True)
        source.seek(0)
        if not source.read(4).startswith(UGOIRA_ZIP_MAGIC):
            raise PixivPolicyError("Pixiv 动图数据不是有效的 ZIP")
        source.seek(0)
        if item.get("restriction") == "r18" and authorization_epoch is not None:
            assert_authorization_generation(authorization_epoch)
        yield source, meta, zip_url


def _animation_source_key(artwork_id: str, quality: str):
    with SEARCH_SESSION_LOCKS_GUARD:
        return workspace_scope(), AUTHORIZATION_GENERATION, artwork_id, quality


@contextmanager
def _download_ugoira_source(
    item: dict, quality: str, staging_root: Path, *,
    authorization_epoch: int | None, ugoira_format: str,
    cancel_event: threading.Event | None,
):
    if ugoira_format == "mp4":
        try:
            require_mp4_encoder()
        except UgoiraExportError as exc:
            exc.artwork_id = str(item["id"])
            raise
    with DOWNLOAD_AHEAD.take(_animation_source_key(str(item["id"]), quality), cancel_event) as prepared:
        if prepared is not None:
            raise_if_search_cancelled(cancel_event)
            HTTP_LOG.info("download artwork=%s stage=archive-reused", item["id"])
            yield prepared
            return
    with _fetch_ugoira_source(
        item, quality, staging_root, authorization_epoch=authorization_epoch,
        cancel_event=cancel_event,
    ) as prepared:
        yield prepared


def _stage_ugoira_source(
    item: dict, folder: Path, staging_root: Path, source: BinaryIO, meta: dict, zip_url: str,
    *, authorization_epoch: int | None, ugoira_format: str,
    cancel_event: threading.Event | None,
) -> list[PublishedFileOwnership]:
    artwork_id = str(item.get("id") or "")
    stem = safe_artwork_stem(str(item.get("title") or ""), artwork_id)
    if ugoira_format != "source":
        raise_if_search_cancelled(cancel_event)
        HTTP_LOG.info("download artwork=%s stage=convert format=%s frames=%d", artwork_id, ugoira_format, len(meta["frames"]))
        with tempfile.TemporaryFile(dir=staging_root) as converted:
            try:
                export_ugoira(source, meta["frames"], ugoira_format,
                              output=converted, cancel_event=cancel_event)
            except UgoiraCancelledError:
                raise SearchCancelledError("动图转换已取消") from None
            except UgoiraExportError as exc:
                exc.artwork_id = artwork_id
                raise
            raise_if_search_cancelled(cancel_event)
            if item.get("restriction") == "r18" and authorization_epoch is not None:
                assert_authorization_generation(authorization_epoch)
            converted.seek(0)
            with owned_staging_batch() as staged:
                staged.append(_create_owned_staged_file(staging_root, folder / f"{stem}_ugoira.{ugoira_format}", converted))
                return staged
    manifest = json.dumps(
        {"mime": meta["mime"], "frames": meta["frames"], "source": zip_url},
        ensure_ascii=False, indent=2,
    ).encode("utf-8")
    with owned_staging_batch() as staged:
        staged.append(_create_owned_staged_file(staging_root, folder / f"{stem}_ugoira.zip", source))
        staged.append(_create_owned_staged_file(staging_root, folder / f"{stem}_ugoira.frames.json", manifest))
        return staged


def stage_ugoira_zip(
    item: dict, quality: str, folder: Path, staging_root: Path,
    *, authorization_epoch: int | None = None, ugoira_format: str = "source",
    cancel_event: threading.Event | None = None,
) -> list[PublishedFileOwnership]:
    with _download_ugoira_source(
        item, quality, staging_root, authorization_epoch=authorization_epoch,
        ugoira_format=ugoira_format, cancel_event=cancel_event,
    ) as (source, meta, zip_url):
        return _stage_ugoira_source(
            item, folder, staging_root, source, meta, zip_url,
            authorization_epoch=authorization_epoch, ugoira_format=ugoira_format,
            cancel_event=cancel_event,
        )


def stage_artwork_pages(
    item: dict, selected_pages: list[int], quality: str, save_root: Path,
    create_folder: bool, staging_root: Path, *, download_context: dict | None = None,
    group_artwork: bool = False, authorization_epoch: int | None = None,
    ugoira_format: str = "source",
    cancel_event: threading.Event | None = None,
) -> list[PublishedFileOwnership]:
    with owned_staging_batch() as staged:
        stage_artwork_groups(
            [(item, selected_pages, authorization_epoch)], quality, save_root,
            create_folder, staging_root, staged, download_context=download_context,
            group_artwork=group_artwork, ugoira_format=ugoira_format,
            cancel_event=cancel_event,
        )
        return staged


def stage_artwork_groups(
    groups: Iterable[tuple],
    quality: str, save_root: Path, create_folder: bool, staging_root: Path,
    staged: list[PublishedFileOwnership], *, download_context: dict | None,
    group_artwork: bool, ugoira_format: str,
    cancel_event: threading.Event | None,
    lookahead: Callable[[], None] | None = None,
    resolve: Callable | None = None,
    select: Callable | None = None,
) -> dict[str, int]:
    """Use the active mode to stage sources; publish in selection order."""
    if resolve is None:
        groups = list(groups)
    counts: dict[str, int] = {}
    files_by_order = {}

    def expand(index, group, stop):
        if select is not None:
            group = select(index, group, stop)
        if group is None:
            return
        item, pages, epoch = group
        artwork_id = str(item.get("id") or "")
        page_images = item.get("pageImages")
        if not artwork_id.isdigit() or not isinstance(page_images, list):
            raise PixivPolicyError("作品详情不完整")
        if not pages or any(page < 0 or page >= len(page_images) for page in pages):
            raise PixivPolicyError("图片页码超出范围")
        folder = resolve_download_target(
            save_root, str(item.get("title") or ""), artwork_id, create_folder,
            context=download_context, group_artwork=group_artwork,
        )
        if item.get("workType") == "ugoira":
            yield DownloadSource((index, 0), (item, None, folder, epoch), True)
        else:
            for ordinal, page in enumerate(pages):
                image = page_images[page]
                if not isinstance(image, dict) or quality not in image:
                    raise PixivPolicyError("作品图片信息不完整")
                yield DownloadSource((index, ordinal), (item, page, folder, epoch), False)

    def jobs():
        for index, group in enumerate(groups):
            yield from expand(index, group, cancel_event)

    @contextmanager
    def prepare(job, stop):
        item, page, folder, epoch = job.payload
        if page is None:
            with _download_ugoira_source(
                item, quality, staging_root, authorization_epoch=epoch,
                ugoira_format=ugoira_format, cancel_event=stop,
            ) as animation:
                yield job, animation
        else:
            artwork_id = str(item["id"])
            remote = approved_image_url(str(item["pageImages"][page][quality]), artwork_id)
            with tempfile.TemporaryFile(dir=staging_root) as source:
                _, content_type = pixiv_request(
                    remote, image_only=True, cancel_event=stop, output=source,
                    timeout=60, max_duration=180, download=True,
                )
                extension = image_extension(content_type)
                source.seek(0)
                yield job, (source, remote, extension)

    def consume(prepared, stop):
        job, source_data = prepared
        item, page, folder, epoch = job.payload
        artwork_id = str(item["id"])
        if page is None:
            source, meta, zip_url = source_data
            if lookahead is not None:
                lookahead()
            files = _stage_ugoira_source(
                item, folder, staging_root, source, meta, zip_url,
                authorization_epoch=epoch, ugoira_format=ugoira_format,
                cancel_event=stop,
            )
        else:
            source, remote, extension = source_data
            if approved_image_url(str(item["pageImages"][page][quality]), artwork_id) != remote:
                raise PixivPolicyError("图片授权已失效")
            stem = safe_artwork_stem(str(item.get("title") or ""), artwork_id)
            files = [_create_owned_staged_file(staging_root, folder / f"{stem}_p{page}.{extension}", source)]
        completed.extend(files)
        files_by_order[job.order] = files
        counts[artwork_id] = counts.get(artwork_id, 0) + len(files)

    with owned_staging_batch() as completed:
        if resolve is not None:
            metrics = stream_downloads(
                groups, resolve, expand, prepare, consume, cancel_event=cancel_event,
                check=raise_if_search_cancelled, width=REQUEST_COORDINATOR.download_width,
                detail_width=REQUEST_COORDINATOR.detail_width,
            )
        else:
            metrics = consume_downloads(
                jobs(), prepare, consume, cancel_event=cancel_event,
                check=raise_if_search_cancelled, width=REQUEST_COORDINATOR.download_width,
                maximum=2 if any(item.get("workType") == "ugoira" for item, _, _ in groups) else 4,
            )
        ordered = [file for key in sorted(files_by_order) for file in files_by_order[key]]
        staged.extend(ordered)
    HTTP_LOG.info("download pipeline sources=%d peak=%d seconds=%.3f",
                  metrics["sources"], metrics["peakSources"], metrics["seconds"])
    return counts


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


def _write_windows_file_handle(handle: int, raw: bytes | BinaryIO) -> None:
    write_file = ctypes.windll.kernel32.WriteFile
    write_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    write_file.restype = wintypes.BOOL
    if isinstance(raw, bytes):
        view = memoryview(raw)
        chunks = (bytes(view[offset:offset + 1024 * 1024]) for offset in range(0, len(view), 1024 * 1024))
    else:
        chunks = iter(lambda: raw.read(1024 * 1024), b"")
    for chunk in chunks:
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
    raw: bytes | BinaryIO,
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
    authorization_epochs: set[int] | None = None,
    authorization_check: Callable[[], object] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[str], bool]:
    """Stage a complete request, then transfer the whole batch to publication."""
    staging_context = secure_staging_directory(save_root, prefix=prefix)
    with staging_context as staging_root:
        with owned_staging_batch() as staged:
            stage(staging_root, staged)
            raise_if_search_cancelled(cancel_event)

        def publish() -> list[str]:
            # Calling the publisher transfers ownership of the complete batch,
            # including entries it has not visited yet.
            return publish_staged_files(
                staging_root,
                staged,
                save_root=save_root,
                staging_locked=True,
                public_paths=True,
            )

        if authorization_epochs or authorization_check:
            # Keep the generation guard from the final validation through the
            # local commit. Logout is linearized after a completed publish, or
            # wins first and causes every staged restricted byte to be removed.
            with SEARCH_SESSION_LOCKS_GUARD:
                try:
                    for epoch in authorization_epochs or ():
                        assert_authorization_generation(epoch)
                    if authorization_check:
                        authorization_check()
                except Exception as original:
                    failures = _discard_owned_staging(staged)
                    if failures:
                        raise PublishRollbackError(
                            f"授权撤销后有 {failures} 个暂存文件未能安全清理"
                        ) from original
                    raise
                public_saved = publish()
        else:
            public_saved = publish()
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
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
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
        if code == 502 and obj.get("errorKind") in {"upstream_rate_limit", "request_queue"}:
            code = 503
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            if code == 502 and obj.get("errorKind") in {"verification_required", "authentication_required", "access_denied"}:
                code = 403
            self.send_response(code)
            if obj.get("errorKind") == "upstream_rate_limit":
                self.send_header("Retry-After", str(obj["retryAfterSeconds"]))
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

    def read_body(self, max_bytes: int) -> bytes:
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
        return raw

    def read_json_object(self, max_bytes: int) -> dict:
        raw = self.read_body(max_bytes)
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
        image_capability = path in {"/api/pixiv/image", "/api/source-search/image"}
        if path != "/api/health" and not image_capability and not valid_request_token(self):
            self.send_json({"error": "请求授权令牌无效"}, 403)
            return True
        return False

    def do_GET(self):
        request = urllib.parse.urlparse(self.path)
        if self._reject_untrusted_api_get(request.path):
            return
        if request.path.startswith("/api/source-search/"):
            try:
                query = urllib.parse.parse_qs(request.query)
                if request.path == "/api/source-search/settings":
                    return self.send_json(SOURCE_SEARCH.settings())
                if request.path == "/api/source-search/job":
                    return self.send_json(SOURCE_SEARCH.snapshot(query.get("id", [""])[0]))
                if request.path == "/api/source-search/image":
                    return self.send_bytes(SOURCE_SEARCH.image(query.get("token", [""])[0]), "image/jpeg", "private, max-age=900")
                return self.send_json({"error": "not found"}, 404)
            except SourceSearchError as exc:
                return self.send_json({"error": str(exc)}, exc.status)
            except OSError:
                return self.send_json({"error": "无法读取搜图配置或凭据，请检查本机权限"}, 500)
        if request.path == "/api/health":
            payload = {
                "ok": True,
                "applicationId": APPLICATION_ID,
                "codeGeneration": CODE_GENERATION,
                "instanceId": INSTANCE_ID,
                "protocolVersion": PROTOCOL_VERSION,
                "version": __version__,
                "animationFormats": export_formats(),
            }
            if health_request_may_disclose_token(self):
                payload["requestToken"] = REQUEST_TOKEN
            return self.send_json(payload)
        if request.path == "/api/updates":
            return self.send_json(APP_UPDATES.snapshot())
        if request.path in {"/api/settings", "/api/cloud/state"}:
            return self._get_cloud(request)
        if request.path.startswith("/api/account/"):
            return self._get_account(request)
        if request.path == "/api/discovery":
            return self._get_discovery(request)
        if request.path == "/api/popularity":
            return self._get_popularity(request)
        if request.path == "/api/ui/guide":
            try:
                return self.send_json({"seen": GUIDE_SEEN_MARKER.is_file()})
            except OSError:
                return self.send_json({"error": "无法读取使用说明记录，请检查资料目录"}, 500)
        if request.path == "/api/status":
            try:
                policy = REQUEST_COORDINATOR.snapshot()
                auth = auth_status_snapshot()
            except (PixivPolicyError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 503)
            logged_in = bool(auth["loggedIn"])
            return self.send_json({
                **auth,
                "mode": "pixiv-authorized" if logged_in else "pixiv-public",
                "authAvailable": True,
                "r18Available": logged_in,
                "message": "Pixiv 本机授权会话" if logged_in else "Pixiv 公开全年龄模式",
                "network": public_network_state(windows_proxy_state()),
                "requestPolicy": {**policy, "browserProfile": getattr(PIXIV_OPENER, "profile", "compatibility")},
            })
        if request.path == "/api/library/statistics":
            query = urllib.parse.parse_qs(request.query)
            try:
                raw_year = query.get("year", [None])[0]
                year = int(raw_year) if raw_year is not None else None
                result = DOWNLOAD_HISTORY.statistics(year=year)
            except ValueError:
                return self.send_json({"error": "统计年份或记录日期无效"}, 400)
            except (OSError, sqlite3.Error):
                return self.send_json({"error": "无法读取本机下载统计，请检查资料目录"}, 500)
            return self.send_json(result)
        if request.path == "/api/library/history":
            query = urllib.parse.parse_qs(request.query)
            try:
                page = int(query.get("page", ["1"])[0])
                text = query.get("query", [""])[0]
                if not 1 <= page <= 100000 or len(text) > 120:
                    raise ValueError("invalid pagination")
            except ValueError:
                return self.send_json({"error": "历史页码或搜索内容无效"}, 400)
            try:
                result = DOWNLOAD_HISTORY.list(page=page, query=text)
            except (OSError, ValueError, sqlite3.Error):
                return self.send_json({"error": "无法读取本机下载历史，请检查资料目录"}, 500)
            return self.send_json(result)
        if request.path == "/api/library/catalog":
            ids = urllib.parse.parse_qs(request.query).get("ids", [""])[0].split(",")
            if len(ids) > 100 or any(not id.isdigit() or len(id) > 30 for id in ids):
                return self.send_json({"error": "作品 ID 无效"}, 400)
            try:
                pages = LOCAL_CATALOG.downloaded_pages_many(ids)
                return self.send_json({"pages": {id: sorted(found) for id, found in pages.items()}})
            except (OSError, ValueError, sqlite3.Error):
                return self.send_json({"error": "无法检查已下载的文件"}, 500)
        if request.path == "/api/workspace":
            with SEARCH_SESSION_LOCKS_GUARD:
                scope = workspace_scope()
                generation = AUTHORIZATION_GENERATION
            try:
                result = WORKSPACE_STORE.load(scope)
            except (OSError, ValueError, sqlite3.Error):
                return self.send_json({"error": "无法读取采集篮和未完成任务"}, 500)
            with SEARCH_SESSION_LOCKS_GUARD:
                if scope != workspace_scope() or generation != AUTHORIZATION_GENERATION:
                    return self.send_json({"error": "Pixiv 账户状态已变更，请重新读取采集篮"}, 409)
                result["authorizationGeneration"] = generation
                return self.send_json(result)
        if request.path == "/api/network/policy":
            try:
                return self.send_json(REQUEST_COORDINATOR.snapshot())
            except (PixivPolicyError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 503)
        if request.path == "/api/network/diagnose":
            state = windows_proxy_state()
            try:
                selected_proxy = refresh_network_opener()
            except PIXIV_OPERATION_ERRORS as exc:
                return self.send_json(pixiv_error_payload("网络检测", exc), 502)
            checks = run_network_diagnostic_checks()
            result = public_network_state(state, selected_proxy=selected_proxy)
            result["checks"] = checks
            result["summary"] = human_network_summary(state, selected_proxy, checks)
            return self.send_json(result)
        if request.path.startswith("/api/pixiv/"):
            try:
                ensure_network_opener_current()
            except PIXIV_OPERATION_ERRORS as exc:
                return self.send_json(pixiv_error_payload("Pixiv 连接", exc), 502)
        if request.path == "/api/pixiv/search":
            return self._get_pixiv_search(request)
        artist_match = re.fullmatch(r"/api/pixiv/artist/(\d+)(/overview)?", request.path)
        if artist_match or request.path == "/api/pixiv/artists":
            return self._get_pixiv_artist(request, artist_match)
        artwork_match = re.fullmatch(r"/api/pixiv/artwork/(\d+)", request.path)
        if artwork_match:
            return self._get_pixiv_detail(request, artwork_match.group(1))
        ugoira_match = re.fullmatch(r"/api/pixiv/ugoira/(\d+)", request.path)
        if ugoira_match:
            return self._get_pixiv_ugoira(request, ugoira_match.group(1))
        if request.path == "/api/pixiv/image":
            return self._get_pixiv_image(request)
        super().do_GET()

    def _get_pixiv_artist(self, request, match):
        query = urllib.parse.parse_qs(request.query)
        try:
            request_id = parse_search_request_id(query.get("requestId", [""])[0]) or secrets.token_urlsafe(24)
            page = int(query.get("page", ["1"])[0])
            cancel_event = register_search_request(request_id)
        except (SearchInputError, ValueError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except (SearchRequestConflictError, SearchRequestLimitError) as exc:
            return self.send_json({"error": str(exc)}, 409)
        try:
            generation = authorization_generation()
            kind = "candidates" if match is None else "overview" if match.group(2) else "profile"
            target = query.get("name", [""])[0] if match is None else match.group(1)
            if AUTH_METHOD.get()=="app":
                connected,generation=validated_authorization()
                if not connected:raise AccountError("reauth","请先在「我的账号」完成 App 授权")
            reader=APP_GALLERY.artist if AUTH_METHOD.get()=="app" else ARTISTS.read
            result = reader(kind, target, generation=generation, page=page, cancel_event=cancel_event,
                                  refresh=query.get("refresh", [""])[0] == "1")
            if authorization_generation() != generation:
                raise AuthorizationRevokedError("作者读取期间账号已变更，请重试")
            profiles = result["candidates"] if kind == "candidates" else [result] if kind == "profile" else []
            for profile in profiles:
                for key in ("avatar", "background"):
                    if profile[key]:
                        proxy = "/api/pixiv/image?" + urllib.parse.urlencode({"url": profile[key]})
                        profile[key] = authorize_image_proxy(proxy, "artist:" + profile["userId"])
            if kind == "overview":
                result["featured"] = [authorize_item_images(item) for item in result["featured"]]
            if authorization_generation() != generation:
                raise AuthorizationRevokedError("作者读取期间账号已变更，请重试")
            return self.send_json(result)
        except SearchCancelledError:
            return self.send_json({"error": "作者读取已取消", "cancelled": True}, 409)
        except AuthorizationRevokedError as exc:
            return self.send_json({"error": str(exc)}, 403)
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json(pixiv_error_payload("作者资料", exc), 502)
        finally:
            release_search_request(request_id, cancel_event)

    def _cloud_generation(self, data):
        connected, generation = validated_authorization()
        expected = data.get("authorizationGeneration")
        if type(expected) is not int or expected != authorization_generation():
            raise AccountError("revoked", "账号已改变，请重新打开操作选项")
        if not connected:
            raise AccountError("reauth", "请先在我的账号登录")
        return generation

    def _get_cloud(self, request):
        try:
            with SEARCH_SESSION_LOCKS_GUARD:
                generation, scope = authorization_generation(), workspace_scope()
            if request.path == "/api/settings":
                result = CLOUD_STATE.preferences(scope)
                if generation != authorization_generation():
                    raise AccountError("revoked", "账号已改变，请重新读取设置")
                return self.send_json({"settings": result, "authorizationGeneration": generation})
            query = urllib.parse.parse_qs(request.query)
            works, authors = (list(dict.fromkeys(filter(None, query.get(key, [""])[0].split(",")))) for key in ("works", "authors"))
            if len(works) + len(authors) > 240 or any(not valid_pixiv_id(value) for value in works + authors):
                raise ValueError("作品或作者 ID 无效")
            connected, generation = validated_authorization()
            if not connected:
                raise AccountError("reauth", "请先在我的账号登录")
            owner, method = CLOUD.context(generation)
            result = CLOUD_STATE.many(owner, method, works, authors)
            with SEARCH_SESSION_LOCKS_GUARD:
                assert_authorization_generation(generation)
                return self.send_json({"states": result, "sync": CLOUD_STATE.sync_status(owner, method),
                    "owner": owner, "method": method, "authorizationGeneration": generation})
        except (ValueError, sqlite3.Error, OSError, PixivPolicyError) as error:
            return self._cloud_error(error)

    def _cloud_error(self, error):
        if isinstance(error, AccountError):
            payload = {"error": str(error), "errorKind": error.kind}
            if isinstance(error, CloudActionError):
                payload.update(state=error.state, official=error.official)
            return self.send_json(payload, 409 if error.kind in {"conflict", "revoked"} else 502)
        if isinstance(error, ValueError):
            return self.send_json({"error": str(error)}, 400)
        if isinstance(error, PixivPolicyError):
            return self.send_json(pixiv_error_payload("云端操作", error), 502)
        return self.send_json({"error": "无法读取或保存云端状态，请检查资料目录及网络"}, 502)

    def _post_cloud(self, data):
        action = urllib.parse.urlsplit(self.path).path.rsplit("/", 1)[-1]
        try:
            if action == "settings":
                changes = validate_preferences(data.get("settings"))
                with SEARCH_SESSION_LOCKS_GUARD:
                    generation = authorization_generation()
                    if type(data.get("authorizationGeneration")) is not int or data["authorizationGeneration"] != generation:
                        raise AccountError("revoked", "账号已改变，请重新打开设置")
                    settings = CLOUD_STATE.save_preferences(workspace_scope(), changes)
                return self.send_json({"settings": settings, "authorizationGeneration": generation})
            generation = self._cloud_generation(data)
            if action == "clear-cache":
                with CLOUD.lock:
                    owner, method = CLOUD.context(generation)
                    with SEARCH_SESSION_LOCKS_GUARD:
                        assert_authorization_generation(generation)
                        DISCOVERY.invalidate()
                        POPULARITY.invalidate()
                        ACCOUNTS.clear_cache(owner)
                        CLOUD_STATE.clear_account(owner)
                        CLOUD.scans = {key: value for key, value in CLOUD.scans.items() if key[0] != owner}
                return self.send_json({"ok": True, "authorizationGeneration": generation})
            ensure_network_opener_current()
            if action == "read":
                kind, target = validate_target(data)
                state = CLOUD.read(generation, kind, target)
                return self.send_json({"state": state, "authorizationGeneration": generation})
            if action == "write":
                intent = validate_intent(data)
                with SEARCH_SESSION_LOCKS_GUARD:
                    assert_authorization_generation(generation)
                    scope = workspace_scope()
                result = CLOUD.mutate(generation, scope, intent)
                assert_authorization_generation(generation)
                return self.send_json({**result, "authorizationGeneration": generation})
            kind = data.get("kind")
            if not isinstance(kind, str) or kind not in {"bookmark", "follow"} or type(data.get("force", False)) is not bool:
                raise ValueError("同步参数无效")
            result = CLOUD.sync(generation, kind, data.get("force", False))
            assert_authorization_generation(generation)
            return self.send_json({"sync": result, "authorizationGeneration": generation})
        except (ValueError, sqlite3.Error, OSError, PixivPolicyError) as error:
            return self._cloud_error(error)

    def _post_official_context(self, data):
        author = data.get("author")
        if not isinstance(author, str) or not valid_pixiv_id(author):
            return self.send_json({"error": "作者 ID 无效"}, 400)
        try:
            ensure_network_opener_current()
            with SEARCH_SESSION_LOCKS_GUARD:
                connected, generation = validated_authorization()
                if not connected or AUTH_METHOD.get() != "web":
                    raise AccountError("method", "官网会话窗口仅用于当前网页登录方式")
                value = web_session_cookie_header().get("Cookie", "").removeprefix("PHPSESSID=")
                return self.send_json({"ok": True, "session": value, "authorizationGeneration": generation})
        except (OSError, PixivPolicyError) as error:
            return self._cloud_error(error)

    def _get_discovery(self, request):
        query = urllib.parse.parse_qs(request.query)
        request_id=None;cancel_event=None;check_token=None;priority_token=None
        try:
            request_id=parse_search_request_id(query.get("requestId",[""])[0])
            try:offset=int(query.get("offset",["0"])[0])
            except ValueError:raise DiscoveryInputError("发现分页无效") from None
            if request_id:cancel_event=register_search_request(request_id)
            connected,generation=validated_authorization()
            if not connected:raise AccountError("reauth","请在我的账号登录后查看发现")
            from pixiv_account import ACCOUNT_REQUEST_CHECK
            def check():
                raise_if_search_cancelled(cancel_event)
                assert_authorization_generation(generation)
            check_token=ACCOUNT_REQUEST_CHECK.set(check)
            priority_token=REQUEST_PRIORITY.set(1 if query.get("background",[""])[0]=="1" else 0)
            ensure_network_opener_current()
            result=DISCOVERY.read(generation=generation,kind=query.get("kind",["recommended"])[0],
                scope=query.get("scope",["all"])[0],mode=query.get("mode",["safe"])[0],rank=query.get("rank",["day"])[0],
                offset=offset,batch=query.get("batch",[""])[0],
                refresh=query.get("refresh",[""])[0]=="1",cached=query.get("cached",[""])[0]=="1")
            if "items" in result:
                if result["kind"]=="following":
                    dates={}
                    for item in result["items"]:
                        if item["sortDate"]:
                            stamp=datetime.fromisoformat(item["sortDate"]).astimezone(timezone.utc).isoformat(timespec="seconds")
                            dates[item["userId"]]=max(dates.get(item["userId"],""),stamp)
                    ACCOUNTS.visits.observe(result["uid"],dates)
                result["items"]=[authorize_item_images(item,authorization_epoch=generation,account_bound=True) for item in result["items"]]
            check()
            return self.send_json(result)
        except (DiscoveryInputError,SearchInputError,SearchRequestLimitError) as error:
            return self.send_json({"error":str(error)},400)
        except SearchCancelledError:
            return self.send_json({"cancelled":True},499)
        except PIXIV_OPERATION_ERRORS as error:
            return self.send_json({"error":str(error) if isinstance(error,(AccountError,RequestDeferredError)) else "发现读取失败，请稍后刷新",
                "errorKind":getattr(error,"kind","read")},502)
        except sqlite3.Error:
            return self.send_json({"error":"发现缓存读写失败，请检查本地资料目录"},500)
        finally:
            if priority_token is not None:REQUEST_PRIORITY.reset(priority_token)
            if check_token is not None:ACCOUNT_REQUEST_CHECK.reset(check_token)
            if request_id and cancel_event is not None:release_search_request(request_id,cancel_event)

    def _get_popularity(self, request):
        query=urllib.parse.parse_qs(request.query)
        request_id=None;cancel_event=None;check_token=None;priority_token=None
        try:
            if query.get("includeAi",["true"])[0] not in {"true","false"} or query.get("fuzzy",["false"])[0] not in {"true","false"}:
                raise ValueError("搜索筛选无效")
            request_id=parse_search_request_id(query.get("requestId",[""])[0])
            if request_id:cancel_event=register_search_request(request_id)
            connected,generation=validated_authorization()
            if not connected:raise AccountError("reauth","请先连接 Pixiv 账号再准备人气排序")
            from pixiv_account import ACCOUNT_REQUEST_CHECK
            def check():
                raise_if_search_cancelled(cancel_event)
                assert_authorization_generation(generation)
            check_token=ACCOUNT_REQUEST_CHECK.set(check)
            priority_token=REQUEST_PRIORITY.set(1)
            ensure_network_opener_current()
            result=POPULARITY.read(generation=generation,kind=query.get("kind",[""])[0],
                uid=query.get("uid",[""])[0],batch=query.get("batch",[""])[0],mode=query.get("mode",["safe"])[0],
                work_type=query.get("workType",["all"])[0],include_ai=query.get("includeAi",["true"])[0]=="true",
                start=query.get("startDate",[""])[0],end=query.get("endDate",[""])[0],
                index=query.get("index",[""])[0],page=int(query.get("page",["1"])[0]),
                refresh=query.get("refresh",[""])[0]=="1",query=query.get("query",[""])[0],
                fuzzy=query.get("fuzzy",["false"])[0]=="true",cancel_event=cancel_event)
            result["items"]=[authorize_item_images(item,authorization_epoch=generation,account_bound=True) for item in result["items"]]
            check()
            return self.send_json(result)
        except (ValueError,SearchInputError,SearchRequestLimitError) as error:
            return self.send_json({"error":str(error)},400)
        except SearchCancelledError:
            return self.send_json({"cancelled":True},499)
        except PIXIV_OPERATION_ERRORS as error:
            return self.send_json({"error":str(error) if isinstance(error,(AccountError,RequestDeferredError)) else "排序元数据读取失败，请重试",
                "errorKind":getattr(error,"kind","read")},502)
        except sqlite3.Error:
            return self.send_json({"error":"排序缓存读写失败，请检查资料目录"},500)
        finally:
            if priority_token is not None:REQUEST_PRIORITY.reset(priority_token)
            if check_token is not None:ACCOUNT_REQUEST_CHECK.reset(check_token)
            if request_id and cancel_event is not None:release_search_request(request_id,cancel_event)

    def _get_account(self, request):
        kind = request.path.removeprefix("/api/account/")
        if kind == "authorization":
            try:
                generation=authorization_generation()
                snapshot = ACCOUNTS.account().snapshot()
                if generation!=authorization_generation():raise AccountError("revoked","账号状态已改变")
                return self.send_json({"connected": snapshot["connected"], "authorization": dict(ACCOUNTS.auth_status)})
            except PIXIV_OPERATION_ERRORS:
                return self.send_json({"error": "授权状态暂时无法读取，请重试"}, 502)
        if kind not in {"profile", "following", "followers", "bookmarks", "tags", "works", "updates"}:
            return self.send_json({"error": "not found"}, 404)
        query = urllib.parse.parse_qs(request.query)
        try:
            offset = int(query.get("offset", ["0"])[0])
            scope = query.get("scope", ["public"])[0]
            tag = query.get("tag", [""])[0]
            mode = query.get("mode", ["safe"])[0]
            if not 0 <= offset <= 100000 or scope not in {"public", "private"} or len(tag) > 100 or mode not in {"safe", "all", "r18"}:
                raise ValueError("invalid_account_query")
        except ValueError:
            return self.send_json({"error": "账号列表参数无效"}, 400)
        cancel_event=None;request_id=None;check_token=None;cache_token=None;priority_token=None
        try:
            request_id=parse_search_request_id(query.get("requestId",[""])[0])
            if request_id:cancel_event=register_search_request(request_id)
            connected, generation = validated_authorization()
            if not connected:
                raise AccountError("reauth", "请先在「我的账号」完成所选方式的登录")
            from pixiv_account import ACCOUNT_REQUEST_CHECK
            def current():
                raise_if_search_cancelled(cancel_event)
                assert_authorization_generation(generation)
            check_token=ACCOUNT_REQUEST_CHECK.set(current)
            current()
            ensure_network_opener_current()
            cache_token=ACCOUNT_CACHE_ONLY.set(query.get("cached",[""])[0]=="1")
            priority_token=REQUEST_PRIORITY.set(1 if query.get("background",[""])[0]=="1" else 0)
            result = ACCOUNTS.read(kind, scope=scope, offset=offset, tag=tag, generation=generation,
                mode=mode, refresh=query.get("refresh", [""])[0] == "1",
                check_updates=CLOUD_STATE.preferences(workspace_scope())["autoFollowing"])
            if kind=="updates" and result.get("authors"):
                states=CLOUD_STATE.many(result["uid"],AUTH_METHOD.get(),[],list(result["authors"]))["follow"]
                result["authors"]={author:value for author,value in result["authors"].items()
                    if not states[author]["known"] or states[author]["active"] and states[author]["privacy"] in {None,scope}}
            if kind in {"following","bookmarks"}:
                with SEARCH_SESSION_LOCKS_GUARD:
                    assert_authorization_generation(generation)
                    rows=result.get("items" if kind=="bookmarks" else "people",[])
                    CLOUD_STATE.remember_active(result["uid"],AUTH_METHOD.get(),"bookmark" if kind=="bookmarks" else "follow",[row["id"] for row in rows],scope)
            for field in ("avatar", "background"):
                if result.get(field):
                    result[field] = authorize_image_proxy("/api/pixiv/image?" + urllib.parse.urlencode({"url": result[field]}),
                        "account:" + result["uid"], "account", authorization_epoch=generation)
            for person in result.get("people", []):
                if person["avatar"]:
                    person["avatar"] = authorize_image_proxy("/api/pixiv/image?" + urllib.parse.urlencode({"url": person["avatar"]}),
                        "account-author:" + person["id"], "account", authorization_epoch=generation)
            if "items" in result:
                result["items"] = [authorize_item_images(item, authorization_epoch=generation, account_bound=True) for item in result["items"]]
            assert_authorization_generation(generation)
            return self.send_json(result)
        except SearchCancelledError:
            return self.send_json({"cancelled":True},499)
        except (SearchInputError,SearchRequestLimitError) as error:
            return self.send_json({"error":str(error)},400)
        except PIXIV_OPERATION_ERRORS as error:
            if isinstance(error,AccountError) and error.kind=="cache_miss":
                return self.send_json({"cached":False,"authorizationGeneration":generation})
            return self.send_json({"error": str(error) if isinstance(error, (AccountError, RequestDeferredError)) else "账户资料读取失败，请稍后重试"}, 502)
        except sqlite3.Error:
            return self.send_json({"error":"账号缓存读写失败，请检查资料目录或清除账号缓存"},500)
        finally:
            if cache_token is not None:ACCOUNT_CACHE_ONLY.reset(cache_token)
            if priority_token is not None:REQUEST_PRIORITY.reset(priority_token)
            if check_token is not None:ACCOUNT_REQUEST_CHECK.reset(check_token)
            if request_id and cancel_event is not None:release_search_request(request_id,cancel_event)

    def _post_account_seen(self, data):
        author = data.get("author")
        authors=data.get("authors")
        if authors is not None:
            if not isinstance(authors,dict) or len(authors)>500:
                return self.send_json({"error":"已读作者列表无效"},400)
            for identifier,stamp in authors.items():
                if not valid_pixiv_id(identifier) or not isinstance(stamp,str) or len(stamp)>32:
                    return self.send_json({"error":"已读作者或时间无效"},400)
                try:
                    parsed=datetime.fromisoformat(stamp)
                    if parsed.tzinfo is None or parsed.astimezone(timezone.utc).isoformat(timespec="seconds")!=stamp:raise ValueError()
                except ValueError:
                    return self.send_json({"error":"已读时间无效"},400)
        elif not isinstance(author, str) or not valid_pixiv_id(author):
            return self.send_json({"error": "作者 ID 无效"}, 400)
        try:
            generation = self._cloud_generation(data)
            if authors is not None:ACCOUNTS.acknowledge_updates(generation,authors)
            else:ACCOUNTS.seen(generation, author)
            assert_authorization_generation(generation)
            return self.send_json({"ok": True})
        except AccountError as error:
            return self._cloud_error(error)
        except PIXIV_OPERATION_ERRORS:
            return self.send_json({"error": "查看状态暂未保存，请重试"}, 502)
        except sqlite3.Error:
            return self.send_json({"error":"查看状态未能写入本机缓存，请重试"},500)

    def _post_account_callback(self, data):
        try:
            ACCOUNT_AUTH.receive(data.get("callback"))
            return self.send_json({"ok": True})
        except (ValueError, queue.Full):
            return self.send_json({"error": "本次授权回调无效或已接收"}, 400)

    def _post_desktop_app_connect(self, _data):
        try:
            if AUTH_METHOD.get()!="app":raise AccountError("method","当前已选择网页登录")
            ensure_network_opener_current()
            with SEARCH_SESSION_LOCKS_GUARD:
                generation=authorization_generation()
                url = ACCOUNT_AUTH.begin(self.server.server_port, REQUEST_TOKEN, generation)
            return self.send_json({"ok": True, "authorizeUrl": url})
        except PIXIV_OPERATION_ERRORS as error:
            return self.send_json({"error": str(error) if isinstance(error, AccountError) else "暂时无法开始账号授权"}, 502)

    def _post_desktop_app_cancel(self, data):
        url = data.get("authorizeUrl")
        if not isinstance(url, str) or len(url) > 4096:
            return self.send_json({"error": "授权取消参数无效"}, 400)
        ACCOUNT_AUTH.cancel(url)
        return self.send_json({"ok": True})

    def _post_auth_method(self,data):
        value=data.get("method")
        if not isinstance(value,str) or value not in {"app","web"}:return self.send_json({"error":"登录方式无效"},400)
        with self.server._download_idle:
            if self.server._download_jobs:return self.send_json({"error":"请等待下载结束后切换登录方式"},409)
        try:
            with SEARCH_SESSION_LOCKS_GUARD:
                if value!=AUTH_METHOD.get():
                    ACCOUNT_AUTH.close()
                    AUTH_METHOD.set(value)
                    clear_authorized_state(force=True)
            return self.send_json({"ok":True,**auth_status_snapshot()})
        except PIXIV_OPERATION_ERRORS:
            return self.send_json({"error":"登录方式未能保存，请重试"},500)

    def _get_pixiv_search(self, request):
        query = urllib.parse.parse_qs(request.query)
        tag_query = query.get("tag", ["原创"])[0]
        try:
            request_id = parse_search_request_id(query.get("requestId", [""])[0])
            snapshot_id = parse_search_snapshot_id(query.get("snapshotId", [""])[0])
        except SearchInputError as exc:
            return self.send_json({"error": str(exc)}, 400)
        if not request_id:
            request_id = secrets.token_urlsafe(24)
        if not snapshot_id:
            snapshot_id = secrets.token_urlsafe(24)
        search_scope = query.get("mode", ["safe"])[0]
        session_authorized, session_epoch = validated_authorization()
        if search_scope in {"r18", "all"} and not session_authorized:
            return self.send_json({"error": "R-18搜索需要先完成Pixiv账户授权"}, 403)
        work_type = query.get("workType", ["all"])[0]
        include_ai = query.get("includeAi", ["false"])[0].lower() == "true"
        fuzzy = query.get("fuzzy", ["false"])[0].lower() == "true"
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        try:
            cancel_event = register_search_request(request_id)
        except SearchRequestConflictError as exc:
            return self.send_json({"error": str(exc)}, 409)
        except SearchRequestLimitError as exc:
            return self.send_json({"error": str(exc)}, 429)
        try:
            try:
                authorization_epoch = (
                    session_epoch if search_scope in {"r18", "all"} else None
                )
                kwargs = {
                    "authorized": session_authorized,
                    "fuzzy": fuzzy,
                    "authorization_epoch": authorization_epoch,
                }
                kwargs["cancel_event"] = cancel_event
                kwargs["snapshot_id"] = snapshot_id
                if query.get("startDate", [""])[0] or query.get("endDate", [""])[0]:
                    kwargs["start_date"] = query.get("startDate", [""])[0]
                    kwargs["end_date"] = query.get("endDate", [""])[0]
                warm = query.get("warm", [""])[0] == "1"
                if warm:
                    state = REQUEST_COORDINATOR.snapshot()
                    with self.server._download_idle:
                        downloading = bool(self.server._download_jobs)
                    if state["cooldownSeconds"] or downloading:
                        return self.send_json({"prefetchSkipped": True})
                if query.get("prefetch", ["false"])[0].lower() == "true":
                    state = REQUEST_COORDINATOR.snapshot()
                    with self.server._download_idle:
                        downloading = bool(self.server._download_jobs)
                    if state["mode"] == "safe" or state["cooldownSeconds"] or downloading:
                        return self.send_json({"prefetchSkipped": True})
                    ahead = query.get("prefetchAhead", ["3"])[0]
                    if ahead not in {"1", "3"}:
                        raise SearchInputError("预取页数无效")
                    kwargs["prefetch"] = True
                    kwargs["prefetch_ahead"] = int(ahead)
                priority_token = REQUEST_PRIORITY.set(2 if kwargs.get("prefetch") or warm else 0)
                try:
                    result = search_pixiv_results(
                        tag_query, search_scope, page, work_type, include_ai, **kwargs,
                    )
                finally:
                    REQUEST_PRIORITY.reset(priority_token)
                return self.send_json(result)
            except SearchCancelledError:
                return self.send_json({"error": "搜索已取消", "cancelled": True}, 409)
            except SearchInputError as exc:
                status = 403 if "授权" in str(exc) else 400
                return self.send_json({"error": str(exc)}, status)
            except AuthorizationRevokedError as exc:
                return self.send_json({"error": str(exc)}, 403)
            except sqlite3.Error:
                return self.send_json({"error": "搜索临时索引不可用，请检查临时目录或磁盘空间后重试"}, 502)
            except PIXIV_OPERATION_ERRORS as exc:
                return self.send_json(pixiv_error_payload("Pixiv 搜索", exc), 502)
        finally:
            release_search_request(request_id, cancel_event)

    def _get_pixiv_detail(self, request, artwork_id: str):
        try:
            query = urllib.parse.parse_qs(request.query)
            request_id = parse_search_request_id(query.get("requestId", [""])[0]) or secrets.token_urlsafe(24)
            cancel_event = register_search_request(request_id)
        except SearchInputError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except SearchRequestConflictError as exc:
            return self.send_json({"error": str(exc)}, 409)
        except SearchRequestLimitError as exc:
            return self.send_json({"error": str(exc)}, 429)
        try:
            authorized, authorization_epoch = validated_authorization()
            return self.send_json(
                pixiv_item_for_download(
                    artwork_id,
                    allow_r18=authorized,
                    authorization_epoch=authorization_epoch,
                    require_thumb=True,
                    cancel_event=cancel_event,
                    refresh=query.get("refresh", [""])[0] == "1",
                ),
            )
        except SearchCancelledError:
            return self.send_json({"error": "作品详情读取已取消", "cancelled": True}, 409)
        except AuthorizationRevokedError as exc:
            return self.send_json({"error": str(exc)}, 403)
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json(pixiv_error_payload("作品详情", exc), 502)
        finally:
            release_search_request(request_id, cancel_event)

    def _get_pixiv_ugoira(self, request, artwork_id: str):
        """Serve ugoira hover previews: frame manifest JSON or the frame zip."""
        mode = urllib.parse.parse_qs(request.query).get("mode", ["meta"])[0]
        if mode not in {"meta", "zip"}:
            return self.send_json({"error": "mode 无效"}, 400)
        request_id = ""
        cancel_event = None
        try:
            request_id = parse_search_request_id(
                urllib.parse.parse_qs(request.query).get("requestId", [""])[0],
            )
            if request_id:
                cancel_event = register_search_request(request_id)
            raise_if_search_cancelled(cancel_event)
            authorized, authorization_epoch = validated_authorization()
            item = pixiv_item_for_download(
                artwork_id,
                allow_r18=authorized,
                authorization_epoch=authorization_epoch,
                cancel_event=cancel_event,
            )
            if item.get("workType") != "ugoira":
                raise PixivPolicyError("该作品不是动图")
            restricted = item.get("restriction") == "r18"
            if restricted and not authorized:
                raise PixivPolicyError("R-18 动图预览需要有效账户授权")
            meta = pixiv_ugoira_meta(
                artwork_id,
                authorization_epoch=authorization_epoch if restricted else None,
                cancel_event=cancel_event,
            )
            if mode == "meta":
                return self.send_json({
                    "frames": meta["frames"], "mime": meta["mime"],
                    "width": item.get("width"), "height": item.get("height"),
                })
            def fetch_zip(event):
                raw, _content_type = pixiv_request(str(meta["src"]), image_only=True, cancel_event=event)
                if not raw.startswith(UGOIRA_ZIP_MAGIC):
                    raise PixivPolicyError("Pixiv 动图数据不是有效的 ZIP")
                return raw

            raw = UGOIRA_RESOURCES.get(
                ("zip", artwork_id, authorization_epoch if restricted else None),
                fetch_zip, ttl=15, cancel_event=cancel_event,
            )
            raise_if_search_cancelled(cancel_event)
            # Mirror the download path: revoked authorization mid-flight must
            # not publish restricted bytes.
            if restricted and authorization_epoch is not None:
                assert_authorization_generation(authorization_epoch)
            if not raw.startswith(UGOIRA_ZIP_MAGIC):
                raise PixivPolicyError("Pixiv 动图数据不是有效的 ZIP")
            return self.send_bytes(raw, "application/zip", "private, no-store")
        except SearchInputError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except SearchRequestConflictError as exc:
            return self.send_json({"error": str(exc)}, 409)
        except (SearchRequestLimitError, PreviewBusyError) as exc:
            return self.send_json({"error": "动图预览繁忙，请稍后再试"}, 429)
        except (SearchCancelledError, PreviewCancelledError):
            return self.send_json({"cancelled": True}, 499)
        except AuthorizationRevokedError as exc:
            return self.send_json({"error": str(exc)}, 403)
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json(pixiv_error_payload("动图预览", exc), 502)
        finally:
            if request_id and cancel_event is not None:
                release_search_request(request_id, cancel_event)

    def _get_pixiv_image(self, request):
        token = urllib.parse.parse_qs(request.query).get("token", [""])[0]
        with PIXIV_STATE_LOCK:
            approved = IMAGE_TOKENS.get(token)
            if not approved or approved[0] < time.time():
                IMAGE_TOKENS.pop(token, None)
                approved = None
            else:
                approved = tuple(approved)
        # Session validation can revoke caches and therefore takes the
        # authorization-generation lock before PIXIV_STATE_LOCK. Never call it
        # while holding PIXIV_STATE_LOCK or restricted detail commit can deadlock.
        if approved is not None:
            try:
                assert_image_token_authorization(approved)
            except AuthorizationRevokedError:
                with PIXIV_STATE_LOCK:
                    if IMAGE_TOKENS.get(token) == approved:
                        IMAGE_TOKENS.pop(token, None)
                approved = None
        if approved is None:
            return self.send_json({"error": "图片授权已失效"}, 403)
        try:
            warm=urllib.parse.parse_qs(request.query).get("warm",[""])[0]
            priority_token = REQUEST_PRIORITY.set(1 if warm=="account" else 2 if warm=="1" else 0)
            try:
                raw, content_type = pixiv_request(str(approved[2]), image_only=True)
            finally:
                REQUEST_PRIORITY.reset(priority_token)
            with PIXIV_STATE_LOCK:
                still_approved = IMAGE_TOKENS.get(token)
                if still_approved != approved or approved[0] < time.time():
                    return self.send_json({"error": "图片授权已失效"}, 403)
            try:
                assert_image_token_authorization(approved)
            except AuthorizationRevokedError:
                return self.send_json({"error": "R-18 图片授权已失效"}, 403)
            if not content_type.startswith("image/"):
                raise PixivPolicyError("Pixiv 返回的不是图片")
            return self.send_bytes(raw, content_type, image_token_cache_control(approved))
        except PIXIV_OPERATION_ERRORS as exc:
            return self.send_json(pixiv_error_payload("图片代理", exc), 502)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/source-search/"):
            if path == "/api/source-search/query":
                authorization_error = None if trusted_local_request(self) and valid_request_token(self) else (403, "请求授权无效")
            elif path == "/api/source-search/browser":
                authorization_error = None if trusted_local_request(self) else (403, "只允许本机调用")
            else:
                authorization_error = validate_mutating_request(self)
            if authorization_error:
                return self.send_json({"error": authorization_error[1]}, authorization_error[0])
            try:
                if path == "/api/source-search/query":
                    raw = self.read_body(MAX_UPLOAD)
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    return self.send_json(SOURCE_SEARCH.start(raw, query.get("mode", [""])[0], f"http://127.0.0.1:{self.server.server_port}"))
                data = self.read_json_object(1024 * 1024 if path.endswith("/browser") else 4096)
                if path == "/api/source-search/login":
                    return self.send_json(SOURCE_SEARCH.open_login(f"http://127.0.0.1:{self.server.server_port}"))
                if path == "/api/source-search/continue":
                    identity = data.get("id")
                    if not isinstance(identity, str):
                        raise SourceSearchError("查询标识无效")
                    return self.send_json(SOURCE_SEARCH.continue_api(identity, f"http://127.0.0.1:{self.server.server_port}"))
                if path == "/api/source-search/browser":
                    identity = data.get("id")
                    if not isinstance(identity, str):
                        raise SourceSearchError("查询标识无效")
                    return self.send_json(SOURCE_SEARCH.browser(identity, self.headers.get("X-MOKU-Source-Capability", ""), data))
                if path == "/api/source-search/settings":
                    return self.send_json(SOURCE_SEARCH.configure(data))
                if path == "/api/source-search/cancel":
                    identity = data.get("id")
                    if not isinstance(identity, str):
                        raise SourceSearchError("查询标识无效")
                    return self.send_json(SOURCE_SEARCH.cancel(identity))
                return self.send_json({"error": "not found"}, 404)
            except (SourceSearchError, RequestInputError) as exc:
                return self.send_json({"error": str(exc)}, exc.status)
            except OSError:
                return self.send_json({"error": "搜图本机操作失败，请检查资料目录及凭据权限"}, 500)
        desktop_auth_path = path in {
            "/api/desktop/update-restart",
            "/api/desktop/auth/session",
            "/api/desktop/auth/logout",
            "/api/desktop/auth/context",
            "/api/desktop/auth/app-connect",
            "/api/desktop/auth/app-cancel",
            "/api/desktop/auth/official-context",
        }
        authorization_error = (
            validate_desktop_auth_request(self)
            if desktop_auth_path
            else validate_mutating_request(self)
        )
        if authorization_error:
            return self.send_json({"error": authorization_error[1]}, authorization_error[0])
        routes = {
            "/api/updates": (2048, self._post_updates),
            "/api/desktop/update-restart": (1024, self._post_update_restart),
            "/api/ui/guide": (1024, self._post_guide_seen),
            "/api/network/policy": (1024, self._post_request_policy),
            "/api/settings": (16384, self._post_cloud),
            "/api/cloud/read": (2048, self._post_cloud),
            "/api/cloud/write": (16384, self._post_cloud),
            "/api/cloud/sync": (2048, self._post_cloud),
            "/api/cloud/clear-cache": (2048, self._post_cloud),
            "/api/auth/logout": (4096, self._post_logout),
            "/api/auth/method": (1024,self._post_auth_method),
            "/api/desktop/auth/session": (4096, self._post_desktop_session),
            "/api/desktop/auth/logout": (4096, self._post_desktop_logout),
            "/api/desktop/auth/context": (1024, self._post_desktop_context),
            "/api/desktop/auth/app-connect": (1024, self._post_desktop_app_connect),
            "/api/desktop/auth/app-cancel": (8192, self._post_desktop_app_cancel),
            "/api/desktop/auth/official-context": (1024, self._post_official_context),
            "/api/account/seen": (65536, self._post_account_seen),
            "/api/account/callback": (16384, self._post_account_callback),
            "/api/system/select-folder": (4096, self._post_select_folder),
            "/api/library/history/clear": (4096, self._post_clear_history),
            "/api/library/history/delete": (65536, self._post_delete_history),
            "/api/library/history/open": (4096, self._post_open_history),
            "/api/workspace/basket": (8388608, self._post_save_basket),
            "/api/workspace/task": (4194304, self._post_save_task),
            "/api/workspace/task/delete": (4096, self._post_delete_task),
            "/api/workspace/task/migrate": (4096, self._post_migrate_task),
            "/api/workspace/recent": (8192, self._post_recent_search),
            "/api/pixiv/search/cancel": (4096, self._post_cancel_search),
            "/api/pixiv/ugoira/cancel": (4096, self._post_cancel_search),
            "/api/pixiv/download/cancel": (4096, self._post_cancel_download),
            "/api/pixiv/batch-download": (65536, self._post_pixiv_batch_download),
            "/api/pixiv/download": (16384, self._post_pixiv_download),
        }
        route = routes.get(path)
        if route is None:
            return self.send_json({"error": "not found"}, 404)
        try:
            data = self.read_json_object(route[0])
        except RequestInputError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        is_download_task = path in {
            "/api/pixiv/batch-download",
            "/api/pixiv/download",
        }
        if is_download_task:
            def download_result() -> tuple[dict, int]:
                with APP_UPDATES.gate:
                    if APP_UPDATES.restarting:
                        return {"error":"正在重启更新，请稍后再下载"},409
                    admitted=DOWNLOAD_TASK_SLOTS.acquire(blocking=False)
                    if admitted:APP_UPDATES.active_downloads+=1
                if not admitted:
                    return {
                        "error": "下载任务繁忙，请等待当前任务完成后重试",
                        "retryable": True,
                    }, 429
                try:
                    if data.get("queueId") is not None:
                        with SEARCH_SESSION_LOCKS_GUARD:
                            queued = WORKSPACE_STORE.get_task(data["queueId"], owner_scope, head=3)
                            if queued is None or data.get("requestId") != queued["remainingChunks"][0]["requestId"]:
                                raise DownloadRequestError("任务进度已更新，请重新读取任务", 409)
                    ensure_network_opener_current()
                    priority_token = REQUEST_PRIORITY.set(1)
                    completed = False
                    try:
                        DOWNLOAD_AHEAD.begin()
                        result = route[1](data)
                        completed = result[1] == 200
                        return result
                    finally:
                        DOWNLOAD_AHEAD.finish(keep=completed and not self._download_cancel.is_set())
                        REQUEST_PRIORITY.reset(priority_token)
                finally:
                    with APP_UPDATES.gate:
                        APP_UPDATES.active_downloads-=1
                        DOWNLOAD_TASK_SLOTS.release()

            try:
                with SEARCH_SESSION_LOCKS_GUARD:
                    authorized, epoch = validated_authorization()
                    owner_scope = workspace_scope()
                    if data.get("queueId") is not None:
                        if data.get("scope") != owner_scope or not WORKSPACE_STORE.has_task(data["queueId"], owner_scope):
                            return self.send_json({"error": "这项任务不属于当前账户，请重新确认下载"}, 403)
                        self._queued_authorization = (owner_scope, authorized, epoch)
                        self._queued_task = (data["queueId"], data.get("requestId"))
                if self.headers.get("X-MOKU-Download-Probe") == "1":
                    payload, status = DOWNLOAD_REQUESTS.probe(
                        data.get("requestId"), path, data,
                        scope=str(epoch) if authorized else "public",
                        durable_scope=owner_scope, output_root=DOWNLOADS,
                    )
                    if status == 204:
                        payload, status = {"inactive": True}, 200
                else:
                    with self.server.download_job() as cancel:
                        self._download_cancel = cancel
                        payload, status = DOWNLOAD_REQUESTS.run(
                            data.get("requestId"), path, data, download_result,
                            scope=str(epoch) if authorized else "public",
                            durable_scope=owner_scope, output_root=DOWNLOADS,
                            cancel_event=cancel,
                        )
            except SearchCancelledError:
                payload, status = {"error": "下载已暂停，重新打开后可继续", "retryable": True}, 503
            except DownloadRequestError as exc:
                payload, status = {"error": str(exc), "requestIdConflict": exc.status == 409}, exc.status
            except (TypeError, ValueError) as exc:
                payload, status = {"error": "下载请求格式无效"}, 400
            except (OSError, sqlite3.Error):
                payload, status = {"error": "无法读取下载任务"}, 500
            return self.send_json(payload, status)
        if path.startswith("/api/workspace/"):
            with SEARCH_SESSION_LOCKS_GUARD:
                if data.get("scope") != workspace_scope():
                    return self.send_json({"error": "Pixiv 账户状态已变更，请重开窗口或刷新任务"}, 409)
                return route[1](data)
        return route[1](data)

    def _post_updates(self, data: dict):
        try:
            action=data.get("action")
            if not isinstance(action,str):raise UpdateError("更新操作无效")
            if action=="configure":result=APP_UPDATES.configure(data.get("options"))
            elif action=="cancel":result=APP_UPDATES.cancel_download()
            elif action in {"check","download"}:result=APP_UPDATES.start(action)
            else:raise UpdateError("更新操作无效")
            return self.send_json(result)
        except UpdateError as exc:
            return self.send_json({"error":str(exc)},400)
        except OSError:
            return self.send_json({"error":"无法保存更新设置，请检查资料目录权限"},500)

    def _post_update_restart(self, _data: dict):
        try:return self.send_json(APP_UPDATES.restart())
        except UpdateError as exc:return self.send_json({"ok":False,"error":str(exc)},409)
        except OSError:return self.send_json({"ok":False,"error":"无法准备更新，请检查目录权限和剩余空间"},500)

    def _post_guide_seen(self, data: dict):
        if data.get("seen") is not True:
            return self.send_json({"error": "使用说明状态无效"}, 400)
        try:
            GUIDE_SEEN_MARKER.parent.mkdir(parents=True, exist_ok=True)
            GUIDE_SEEN_MARKER.touch(exist_ok=True)
        except OSError:
            return self.send_json({"error": "无法保存使用说明记录，请检查资料目录"}, 500)
        return self.send_json({"ok": True})

    def _post_request_policy(self, data: dict):
        if data.get("action") == "resume":
            try:
                return self.send_json(REQUEST_COORDINATOR.resume())
            except (PixivPolicyError, OSError):
                return self.send_json({"error": "暂停状态保存失败，请检查资料目录"}, 500)
        mode = data.get("mode")
        if not isinstance(mode, str) or mode not in RequestCoordinator.PROFILES:
            return self.send_json({"error": "请求模式无效"}, 400)
        try:
            state = REQUEST_COORDINATOR.set_mode(mode)
        except (PixivPolicyError, OSError):
            return self.send_json({"error": "请求模式保存失败，请检查资料目录"}, 500)
        if mode != "fast":
            DOWNLOAD_AHEAD.clear()
        return self.send_json(state)

    def _post_logout(self, _data: dict):
        try:
            disconnect_authorized_session()
            DOWNLOAD_AHEAD.clear()
            return self.send_json({"ok": True})
        except OSError:
            return self.send_json({
                "ok": False,
                "error": "当前会话已断开，但 Windows 凭据删除失败",
            }, 500)

    def _post_desktop_context(self, _data: dict):
        try:
            ensure_network_opener_current()
        except (PixivPolicyError, OSError) as exc:
            return self.send_json({"error": str(exc)}, 503)
        with SEARCH_SESSION_LOCKS_GUARD:
            generation = AUTHORIZATION_GENERATION
        with PIXIV_NETWORK_LOCK:
            return self.send_json({
                "ok": True, "proxy": PIXIV_PROXY,
                "proxySource": "environment" if normalize_loopback_proxy(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "") else "system",
                "authorizationGeneration": generation,
                "language": LOGIN_CONTEXT.accept_language,
                "browserProfile": getattr(PIXIV_OPENER, "profile", "compatibility"),
            })

    def _post_desktop_session(self, data: dict):
        value = data.get("session")
        remember = data.get("remember", False)
        if not isinstance(value, str) or not isinstance(remember, bool):
            return self.send_json({"error": "桌面认证请求格式无效"}, 400)
        generation = data.get("authorizationGeneration")
        if generation is not None and type(generation) is not int:
            return self.send_json({"error": "桌面认证状态无效"}, 400)
        try:
            context = parse_login_context(data["browserContext"]) if data.get("browserContext") is not None else None
            with SEARCH_SESSION_LOCKS_GUARD:
                if generation is not None and generation != AUTHORIZATION_GENERATION:
                    return self.send_json({"error": "账户状态已变化，请重新打开登录窗口"}, 409)
                connect_authorized_session(value, remember=remember)
                if context is not None:
                    global LOGIN_CONTEXT
                    LOGIN_CONTEXT = context
                    PIXIV_HEADERS["Accept-Language"] = context.accept_language
                    with PIXIV_NETWORK_LOCK:
                        if isinstance(PIXIV_OPENER, BrowserTransport):
                            PIXIV_OPENER.configuration = replace(PIXIV_OPENER.configuration, language=context.accept_language)
                    try:
                        save_login_context(history_path().with_name("login-context.json"), context)
                    except OSError:
                        HTTP_LOG.warning("Login succeeded; browser metadata persistence failed")
                    HTTP_LOG.info("Desktop login browser=%s backend=%s language=%s", context.browser_version, getattr(PIXIV_OPENER, "profile", "compatibility"), context.accept_language)
        except ValueError:
            return self.send_json({"error": "Pixiv 会话格式无效"}, 400)
        except OSError:
            return self.send_json({"error": "Pixiv 登录状态保存失败，请检查本机存储后重试"}, 500)
        return self.send_json({"ok": True, "remembered": remember})

    def _post_desktop_logout(self, _data: dict):
        return self._post_logout(_data)

    def _post_cancel_search(self, data: dict):
        try:
            request_id = parse_search_request_id(data.get("requestId"), required=True)
            found = cancel_search_request(request_id)
        except SearchInputError as exc:
            return self.send_json({"error": str(exc)}, 400)
        return self.send_json({"ok": True, "cancelled": found})

    def _post_cancel_download(self, data: dict):
        request_id = data.get("requestId")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", request_id):
            return self.send_json({"error": "下载任务编号无效"}, 400)
        try:
            with SEARCH_SESSION_LOCKS_GUARD:
                found = DOWNLOAD_REQUESTS.cancel(request_id, workspace_scope())
            DOWNLOAD_AHEAD.clear()
        except OSError:
            return self.send_json({"error": "无法暂停下载，请重试"}, 500)
        return self.send_json({"ok": True, "cancelled": found})

    def _post_select_folder(self, data: dict):
        if not FOLDER_PICKER_LOCK.acquire(blocking=False):
            return self.send_json({"error": "已有目录选择窗口正在打开"}, 409)
        try:
            result = select_folder(str(data.get("initial") or ""))
            return self.send_json(result, 500 if result.get("error") else 200)
        finally:
            FOLDER_PICKER_LOCK.release()

    def _post_save_basket(self, data: dict):
        try:
            revision = WORKSPACE_STORE.save_basket(data.get("basket"), workspace_scope(), revision=data.get("revision"))
            return self.send_json({"ok": True, "revision": revision})
        except WorkspaceCapacityError as exc:
            return self.send_json({"error": str(exc), "basketCapacity": True}, 409)
        except ValueError as exc:
            return self.send_json({"error": "采集篮已在另一个窗口更新，请重试保存" if "stale" in str(exc) else "采集篮数据无效"}, 409 if "stale" in str(exc) else 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "无法保存采集篮"}, 500)

    def _post_save_task(self, data: dict):
        try:
            update_only = data.get("updateOnly", False)
            if type(update_only) is not bool:
                raise ValueError("invalid task update mode")
            if "checkpoint" in data:
                task=WORKSPACE_STORE.advance_task(data["checkpoint"],workspace_scope())
                return self.send_json({"ok":True,"task":task})
            task = WORKSPACE_STORE.save_task(data.get("task"), workspace_scope(),
                                             require_existing=update_only,
                                             **({"expected": data["expectedTask"]} if "expectedTask" in data else {}))
            return self.send_json({"ok": True, "task": task})
        except TaskCapacityError as exc:
            return self.send_json({"error": str(exc), "taskCapacity": True,
                                   "ownTaskCount": exc.own_count, "otherTaskCount": exc.other_count}, 409)
        except TaskConflictError as exc:
            return self.send_json({"error": str(exc), "taskConflict": True}, 409)
        except ValueError:
            return self.send_json({"error": "未完成任务数据无效，请重新发起下载"}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "无法保存未完成任务"}, 500)

    def _post_migrate_task(self, data: dict):
        # Workspace routes hold the authorization guard. A new operation first
        # claims its request identity, then rechecks queue membership under the
        # same guard; migration cannot race an old request into publication.
        try:
            scope = workspace_scope()
            task = WORKSPACE_STORE.get_task(data.get("id"), scope)
            if task is None:
                return self.send_json({"removed": True})
            if task.get("chunkPolicy"):
                return self.send_json({"task": task})
            authorized, epoch = validated_authorization()
            updated = {**task, "remainingChunks": [], "chunkPolicy": "animation-artwork-v1"}
            basket = {row["id"]: row["item"] for row in WORKSPACE_STORE.load(scope)["basket"]}
            for chunk in task["remainingChunks"]:
                single = task["kind"] == "single"
                endpoint = task["taskOptions"]["endpoint"] if single else "/api/pixiv/batch-download"
                body = ({**task["taskOptions"]["body"], "pages": chunk["groups"][0]["pages"]} if single
                        else {**task["taskOptions"], "groups": chunk["groups"], "context": chunk["context"]})
                body.update(requestId=chunk["requestId"], queueId=task["id"], scope=scope)
                # 204 is a probe sentinel, never a successful download receipt.
                result, status = DOWNLOAD_REQUESTS.probe(chunk["requestId"], endpoint, body,
                                                        scope=str(epoch) if authorized else "public", durable_scope=scope,
                                                        output_root=DOWNLOADS)
                if status == 202:
                    return self.send_json({"pending": True, "requestId": chunk["requestId"]}, 202)
                if status == 200:
                    updated["completedBatches"] += 1
                    updated["savedCount"] += result.get("pages", chunk["pageCount"])
                    updated["fileCount"] += len(result.get("saved", []))
                    updated["skippedCount"] += result.get("skippedPages", 0)
                    updated["firstSaved"] = updated["firstSaved"] or next(iter(result.get("saved", [])), "")
                    if any(group["id"] == updated.get("conversionFailure", {}).get("artworkId") for group in chunk["groups"]):
                        updated.pop("conversionFailure", None)
                    continue
                pieces, ordinary = [], []
                for group in chunk["groups"]:
                    if single or basket.get(group["id"], {}).get("workType") not in {"illustration", "manga"}:
                        if ordinary:
                            pieces.append(ordinary)
                            ordinary = []
                        pieces.append([group])
                    else:
                        ordinary.append(group)
                if ordinary:
                    pieces.append(ordinary)
                if len(pieces) == 1:
                    updated["remainingChunks"].append(chunk)
                else:
                    updated["remainingChunks"].extend({"groups": groups, "pageCount": sum(len(g["pages"]) for g in groups),
                                                        "requestId": secrets.token_hex(16), "context": chunk["context"]}
                                                       for groups in pieces)
            updated["totalBatches"] = updated["completedBatches"] + len(updated["remainingChunks"])
            if updated["remainingChunks"]:
                updated = WORKSPACE_STORE.save_task(updated, scope, expected=task)
            else:
                WORKSPACE_STORE.delete_task(task["id"], scope, revision=task.get("revision", 0))
            return self.send_json({"task": updated})
        except DownloadRequestError as exc:
            return self.send_json({"error": str(exc)}, exc.status)
        except TaskConflictError as exc:
            return self.send_json({"error": str(exc), "taskConflict": True}, 409)
        except ValueError:
            return self.send_json({"error": "任务迁移数据无效，请重新读取任务"}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "无法核对旧任务进度"}, 500)

    def _post_recent_search(self, data: dict):
        try:
            action = data.get("action", "remember")
            if action not in ("remember", "remove", "clear"):
                raise ValueError("invalid recent action")
            with SEARCH_SESSION_LOCKS_GUARD:
                scope=workspace_scope()
                if data.get("scope")!=scope:return self.send_json({"error":"账号状态已改变，请重试"},409)
                if action=="clear":WORKSPACE_STORE.clear_recent_searches(scope)
                else:
                    operation = WORKSPACE_STORE.delete_recent_search if action == "remove" else WORKSPACE_STORE.remember_search
                    operation(data.get("search"), scope)
            return self.send_json({"ok": True})
        except ValueError:
            return self.send_json({"error": "搜索条件无效"}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "最近搜索未保存"}, 500)

    def _post_delete_task(self, data: dict):
        try:
            scope = workspace_scope()
            task = WORKSPACE_STORE.get_task(data.get("id"), scope)
            deleted = WORKSPACE_STORE.delete_task(data.get("id"), scope, revision=data.get("revision"))
        except TaskConflictError as exc:
            return self.send_json({"error": str(exc), "taskConflict": True}, 409)
        except ValueError:
            return self.send_json({"error": "任务编号无效"}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "无法移除任务"}, 500)
        payload = {"ok": True, "deleted": deleted}
        if deleted and task:
            DOWNLOAD_AHEAD.clear()
            try:
                DOWNLOAD_REQUESTS.cancel(task["remainingChunks"][0]["requestId"], scope)
            except OSError:
                HTTP_LOG.warning("Task removed; active download cancellation failed")
                payload["pauseWarning"] = "任务已移除，请在下载窗口暂停正在运行的批次"
        return self.send_json(payload)

    def _post_clear_history(self, data: dict):
        if data.get("confirm") is not True:
            return self.send_json({"error": "请确认清空记录（不会删除图片）"}, 400)
        try:
            DOWNLOAD_HISTORY.clear()
        except (OSError, ValueError, sqlite3.Error):
            return self.send_json({"error": "无法清空下载历史"}, 500)
        return self.send_json({"ok": True})

    def _post_delete_history(self, data: dict):
        if data.get("confirm") is not True:
            return self.send_json({"error": "请确认删除记录（不会删除文件）"}, 400)
        try:
            deleted = DOWNLOAD_HISTORY.delete(data.get("ids"))
        except ValueError:
            return self.send_json({"error": "请选择有效的历史记录"}, 400)
        except (OSError, sqlite3.Error):
            return self.send_json({"error": "无法删除下载历史"}, 500)
        return self.send_json({"ok": True, "deleted": deleted})

    def _post_open_history(self, data: dict):
        try:
            file = DOWNLOAD_HISTORY.first_file(data.get("id"))
            if file is None:
                return self.send_json({"error": "这条记录已删除，请刷新历史"}, 404)
            parent = _reject_reparse_components(Path(file).parent, "保存位置已变为链接")
            if not parent.is_dir():
                return self.send_json({"error": "保存文件夹已移动或删除"}, 404)
            os.startfile(parent)
            return self.send_json({"ok": True})
        except (OSError, ValueError, sqlite3.Error):
            return self.send_json({"error": "无法打开保存文件夹"}, 400)

    @staticmethod
    def _remaining_download_pages(item: dict, pages: list[int], quality: str, format: str,
                                  save_root: Path, create_folder: bool, context: dict | None,
                                  group_artworks: bool, skip_existing: bool,
                                  skipped_files: list[str] | None = None) -> list[int]:
        images = item.get("pageImages")
        if not isinstance(images, list) or any(page >= len(images) for page in pages):
            raise PixivPolicyError("图片页码超出范围")
        if not skip_existing:
            return pages
        directory = resolve_download_target(save_root, str(item.get("title") or ""), str(item["id"]),
                                            create_folder, context=context, group_artwork=group_artworks)
        try:
            present = LOCAL_CATALOG.existing_files(str(item["id"]), pages, quality,
                                                  format if item.get("workType") == "ugoira" else "source", directory)
        except sqlite3.Error:
            # A broken optional index must not make downloads unusable.
            HTTP_LOG.warning("下载目录索引读取失败，将正常下载")
            return pages
        except OSError as exc:
            raise PixivPolicyError("无法核验已下载文件，请检查原保存文件的访问权限与路径") from exc
        if skipped_files is not None:
            skipped_files.extend(str(file.relative_to(save_root)) for files in present.values() for file in files)
        return [page for page in pages if page not in present]

    @staticmethod
    def _record_download_catalog(groups: list[tuple[dict, list[int], int]], saved: list[str],
                                 save_root: Path, quality: str, format: str,
                                 create_folder: bool, context: dict | None, group_artworks: bool) -> str:
        try:
            offset = 0
            for item, pages, count in groups:
                files = [save_root / file for file in saved[offset:offset + count]]
                offset += count
                directory = resolve_download_target(save_root, str(item.get("title") or ""), str(item["id"]),
                                                    create_folder, context=context, group_artwork=group_artworks)
                animation = item.get("workType") == "ugoira"
                LOCAL_CATALOG.record(str(item["id"]), pages, quality, format if animation else "source",
                                     directory, files, animation=animation)
            if offset != len(saved):
                raise ValueError("catalog publication mismatch")
        except Exception:
            HTTP_LOG.warning("文件已保存，但下载目录索引写入失败")
            return "文件已保存，但下次可能无法跳过这些图片"
        return ""

    @staticmethod
    def _record_download_history(
        groups: list[tuple[dict, list[int], int]], saved: list[str],
        save_root: Path, quality: str, ugoira_format: str,
    ) -> str:
        # This is auxiliary metadata AFTER publication. Never retry a successful
        # download because the library is locked, full, corrupt, or unavailable.
        try:
            rows = []
            offset = 0
            for item, pages, file_count in groups:
                files = saved[offset:offset + file_count]
                offset += file_count
                rows.append({
                    "artworkId": item["id"], "title": item.get("title"), "artist": item.get("artist"),
                    "workType": item.get("workType"), "pages": pages,
                    "quality": quality, "format": ugoira_format if item.get("workType") == "ugoira" else "source",
                    "files": [str(save_root / file) for file in files],
                })
            if groups and offset != len(saved):
                raise ValueError("history publication mismatch")
            DOWNLOAD_HISTORY.record(secrets.token_hex(24), rows)
        except Exception:
            HTTP_LOG.warning("文件已保存，但下载历史写入失败")
            return "文件已保存，但下载历史记录失败"
        return ""

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
        quality = str(data.get("quality") or "original")
        create_folder = data.get("createFolder", True)
        if quality not in {"original", "regular"}:
            raise RequestInputError(400, "图片质量无效")
        if not isinstance(create_folder, bool):
            raise RequestInputError(400, "createFolder 必须是布尔值")
        if not isinstance(data.get("skipExisting", True), bool):
            raise RequestInputError(400, "skipExisting 必须是布尔值")
        return quality, create_folder

    @staticmethod
    def _ugoira_format(data: dict) -> str:
        value = data.get("ugoiraFormat", "source")
        if not isinstance(value, str) or value not in {"source", "gif", "mp4"}:
            raise RequestInputError(400, "动图下载格式无效")
        return value

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
    def _selected_download_pages(pages: object) -> list[int]:
        if not isinstance(pages, list) or not pages:
            raise RequestInputError(400, "请至少选择一张图片")
        if any(not isinstance(page, int) or isinstance(page, bool) or page < 0 for page in pages):
            raise RequestInputError(400, "图片页码无效")
        if len(pages) > DOWNLOAD_CHUNK_PAGES:
            raise RequestInputError(400, f"单次下载最多处理 {DOWNLOAD_CHUNK_PAGES} 张图片")
        return sorted(set(pages))

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
            normalized.setdefault(artwork_id, set()).update(Handler._selected_download_pages(pages))
        return normalized

    def _download_authorization_snapshot(self) -> tuple[bool, int | None]:
        with SEARCH_SESSION_LOCKS_GUARD:
            snapshot = validated_authorization()
            expected = getattr(self, "_queued_authorization", None)
            if expected is not None and expected != (workspace_scope(), *snapshot):
                raise AuthorizationRevokedError("Pixiv 账户状态已变更，请重新确认下载")
            queued_task = getattr(self, "_queued_task", None)
            if queued_task is not None:
                task_id, request_id = queued_task
                task = WORKSPACE_STORE.get_task(task_id, workspace_scope(), head=3)
                if task is None or task["remainingChunks"][0]["requestId"] != request_id:
                    raise SearchCancelledError("下载任务已移除或更新")
            return snapshot

    def _start_animation_lookahead(self, cancel_event: threading.Event | None):
        """Prepare up to two saved batches within a bounded disk allowance."""
        queued = getattr(self, "_queued_task", None)
        depth = REQUEST_COORDINATOR.download_lookahead()
        if queued is None or not depth:
            return
        try:
            with SEARCH_SESSION_LOCKS_GUARD:
                authorized, epoch = self._download_authorization_snapshot()
                scope = workspace_scope()
                task = WORKSPACE_STORE.get_task(queued[0], scope, head=3)
                if task["kind"] != "batch" or len(task["remainingChunks"]) < 2:
                    return
                upcoming = [chunk for chunk in task["remainingChunks"][1:1 + depth]
                            if len(chunk["groups"]) == 1 and chunk["groups"][0]["pages"] == [0]]
                options = task["taskOptions"]
                quality = options.get("quality", "original")
                authorization = (scope, authorized, epoch)

            save_root = self._save_root(options)
            # Reserve room for the current converted output and its owned copy.
            # Two equally bounded future archives total at most one GiB even
            # when the foreground archive limit is configured above 512 MiB.
            reserve = 2 * MAX_UGOIRA_BYTES + 64 * 1024 * 1024
            if (options.get("ugoiraFormat") == "mp4"
                    and save_root.stat().st_dev == Path(tempfile.gettempdir()).stat().st_dev):
                reserve += MAX_UGOIRA_TEMP_BYTES
            headroom = shutil.disk_usage(save_root).free - reserve
            source_limit = min(MAX_UGOIRA_BYTES, MAX_AHEAD_BYTES // 2, max(0, headroom) // max(1, len(upcoming)))
            if source_limit <= 0:
                DOWNLOAD_AHEAD.clear(wait=True)
                return
            keys = {_animation_source_key(chunk["groups"][0]["id"], quality) for chunk in upcoming}
            DOWNLOAD_AHEAD.retain(keys)

            @contextmanager
            def prepare(chunk, stop):
                artwork_id = chunk["groups"][0]["id"]

                def check_queue():
                    with SEARCH_SESSION_LOCKS_GUARD:
                        current = WORKSPACE_STORE.get_task(queued[0], scope, head=3)
                        if (authorization != (workspace_scope(), *validated_authorization())
                                or current is None
                                or not any(row == chunk for row in current["remainingChunks"][:3])):
                            raise SearchCancelledError("预取任务已移除或更新")

                check_queue()
                item = pixiv_item_for_download(
                    artwork_id, allow_r18=authorized, authorization_epoch=epoch,
                    cancel_event=stop, network_timeout=90,
                )
                if item.get("workType") != "ugoira":
                    yield None
                    return
                remaining = self._remaining_download_pages(
                    item, [0], quality, options.get("ugoiraFormat", "source"),
                    save_root, options.get("createFolder", False),
                    chunk["context"], options.get("groupArtworks", False),
                    options.get("skipExisting", True), [],
                )
                if not remaining:
                    yield None
                    return
                with secure_staging_directory(save_root, prefix=".moku-ahead-") as cache_root:
                    with _fetch_ugoira_source(
                        item, quality, cache_root, authorization_epoch=epoch,
                        cancel_event=stop, maximum_bytes=source_limit,
                    ) as prepared:
                        check_queue()
                        raise_if_search_cancelled(stop)
                        yield prepared

            for chunk in upcoming:
                key = _animation_source_key(chunk["groups"][0]["id"], quality)
                DOWNLOAD_AHEAD.start(key, lambda stop, chunk=chunk: prepare(chunk, stop), cancel_event, hold=True)
        except (OSError, sqlite3.Error, PixivPolicyError, RequestInputError) as exc:
            HTTP_LOG.info("download lookahead skipped category=%s", type(exc).__name__)

    def _post_pixiv_batch_download(self, data: dict) -> tuple[dict, int]:
        cancel_event = getattr(self, "_download_cancel", None)
        try:
            quality, create_folder = self._download_options(data)
            ugoira_format = self._ugoira_format(data)
            save_root = self._save_root(data)
            download_context = self._download_context(data, required=create_folder)
            group_artworks = data.get("groupArtworks", False)
            if not isinstance(group_artworks, bool):
                raise RequestInputError(400, "groupArtworks 必须是布尔值")
        except RequestInputError as exc:
            return {"error": str(exc)}, exc.status
        try:
            normalized = self._normalized_download_groups(data.get("groups"))
        except RequestInputError as exc:
            return {"error": str(exc)}, exc.status
        total_pages = sum(len(pages) for pages in normalized.values())
        if total_pages > DOWNLOAD_CHUNK_PAGES:
            return {"error": f"单次下载最多处理 {DOWNLOAD_CHUNK_PAGES} 张图片"}, 400
        chunk_groups = [{"id": artwork_id, "pages": sorted(pages)} for artwork_id, pages in normalized.items()]
        try:
            chunks = plan_download_chunks(
                chunk_groups,
                max_artworks=DOWNLOAD_CHUNK_ARTWORKS,
                max_pages=DOWNLOAD_CHUNK_PAGES,
            )
        except SearchInputError as exc:
            return {"error": str(exc)}, 400
        if len(chunks) > 1:
            return {
                "error": "请求必须按图片优先分块提交",
                "chunks": len(chunks),
                "maxPagesPerChunk": DOWNLOAD_CHUNK_PAGES,
                "maxArtworksPerChunk": DOWNLOAD_CHUNK_ARTWORKS,
            }, 400

        try:
            restricted_epochs: set[int] = set()
            history_groups: list[tuple[dict, list[int], int]] = []
            skipped_pages = 0
            skipped_files: list[str] = []

            def stage_batch(
                staging_root: Path,
                staged: list[PublishedFileOwnership],
            ) -> None:
                nonlocal skipped_pages
                selected_groups = {}
                skipped_by_order = {}

                def prepare_detail(group, stop):
                    artwork_id, page_set = group
                    raise_if_search_cancelled(stop)
                    HTTP_LOG.info("download artwork=%s stage=detail", artwork_id)
                    authorized, authorization_epoch = self._download_authorization_snapshot()
                    item = pixiv_item_for_download(
                        artwork_id, allow_r18=authorized,
                        authorization_epoch=authorization_epoch,
                        cancel_event=stop, network_timeout=90,
                    )
                    return artwork_id, page_set, authorized, authorization_epoch, item

                def select_detail(index, row, stop):
                    nonlocal skipped_pages
                    artwork_id, page_set, authorized, authorization_epoch, item = row
                    raise_if_search_cancelled(stop)
                    if item.get("restriction") == "r18" and not authorized:
                        raise PixivPolicyError("R-18 下载需要有效账户授权")
                    if item.get("restriction") == "r18":
                        if authorization_epoch is None:
                            raise AuthorizationRevokedError("R-18 下载授权已失效")
                        restricted_epochs.add(authorization_epoch)
                    skipped = []
                    pages = self._remaining_download_pages(
                        item, sorted(page_set), quality, ugoira_format,
                        save_root, create_folder, download_context, group_artworks,
                        data.get("skipExisting", True), skipped,
                    )
                    skipped_by_order[index] = skipped
                    skipped_pages += len(page_set) - len(pages)
                    if not pages:
                        return None
                    selected_groups[index] = (item, pages, authorization_epoch)
                    return selected_groups[index]

                counts = stage_artwork_groups(
                    normalized.items(), quality, save_root, create_folder, staging_root, staged,
                    download_context=download_context, group_artwork=group_artworks,
                    ugoira_format=ugoira_format, cancel_event=cancel_event,
                    lookahead=(lambda: self._start_animation_lookahead(cancel_event))
                              if len(normalized) == 1 else None,
                    resolve=prepare_detail, select=select_detail,
                )
                history_groups.extend((item, pages, counts[str(item["id"])])
                                      for _, (item, pages, _) in sorted(selected_groups.items()))
                skipped_files.extend(file for index in sorted(skipped_by_order)
                                     for file in skipped_by_order[index])

            public_saved, cleanup_pending = _stage_and_publish_download(
                save_root,
                prefix=".moku-batch-",
                stage=stage_batch,
                authorization_epochs=restricted_epochs,
                cancel_event=cancel_event,
                **({"authorization_check": self._download_authorization_snapshot}
                   if hasattr(self, "_queued_authorization") else {}),
            )
            response_payload = {
                "ok": True, "saved": public_saved,
                "artworks": len(normalized), "pages": total_pages,
                "skippedPages": skipped_pages,
                "skippedFiles": skipped_files,
                "catalogWarning": self._record_download_catalog(history_groups, public_saved, save_root, quality,
                                                               ugoira_format, create_folder, download_context, group_artworks),
                "cleanupPending": cleanup_pending,
                "historyWarning": self._record_download_history(history_groups, public_saved, save_root, quality, ugoira_format),
            }
            response_status = 200
        except AuthorizationRevokedError as exc:
            response_payload = {"error": str(exc)}
            response_status = 403
        except UgoiraExportError as exc:
            response_payload = {"error": public_pixiv_error("动图转换", exc),
                                "conversionFailure": {"artworkId": exc.artwork_id, "code": exc.code}}
            response_status = 422
        except PIXIV_OPERATION_ERRORS as exc:
            response_payload = pixiv_error_payload("批量下载", exc, saving=True)
            HTTP_LOG.warning("batch download failed category=%s", type(exc).__name__)
            response_status = 502
        return response_payload, response_status

    def _post_pixiv_download(self, data: dict) -> tuple[dict, int]:
        cancel_event = getattr(self, "_download_cancel", None)
        artwork_id = str(data.get("id") or "")
        if not artwork_id.isdigit():
            return {"error": "作品 ID 无效"}, 400
        try:
            quality, create_folder = self._download_options(data)
            ugoira_format = self._ugoira_format(data)
            save_root = self._save_root(data)
            download_context = self._download_context(data, required=False)
            selected_pages = self._selected_download_pages(data["pages"]) if "pages" in data else None
            group_artworks = data.get("groupArtworks", False)
            if not isinstance(group_artworks, bool):
                raise RequestInputError(400, "groupArtworks 必须是布尔值")
        except RequestInputError as exc:
            return {"error": str(exc)}, exc.status

        try:
            authorized, authorization_epoch = self._download_authorization_snapshot()
            HTTP_LOG.info("download artwork=%s stage=detail", artwork_id)
            item = pixiv_item_for_download(
                artwork_id,
                allow_r18=authorized,
                authorization_epoch=authorization_epoch,
                cancel_event=cancel_event,
                network_timeout=90,
            )
            page_images = item.get("pageImages")
            if not isinstance(page_images, list) or not page_images:
                raise PixivPolicyError("作品详情不完整")
            if selected_pages is None:
                selected_pages = list(range(len(page_images)))
            if len(selected_pages) > DOWNLOAD_CHUNK_PAGES:
                raise PixivPolicyError(
                    f"单作品下载最多处理 {DOWNLOAD_CHUNK_PAGES} 张图片"
                )
            if any(page >= len(page_images) for page in selected_pages):
                raise PixivPolicyError("图片页码超出范围")
            if item.get("restriction") == "r18" and not authorized:
                raise PixivPolicyError("R-18 下载需要有效账户授权")
            restricted_epochs = (
                {authorization_epoch}
                if item.get("restriction") == "r18"
                and authorization_epoch is not None
                else set()
            )
            skipped_files: list[str] = []
            pages_to_save = self._remaining_download_pages(item, selected_pages, quality, ugoira_format,
                                                          save_root, create_folder, download_context, group_artworks,
                                                          data.get("skipExisting", True), skipped_files)
            def stage_single(
                staging_root: Path,
                staged: list[PublishedFileOwnership],
            ) -> None:
                if not pages_to_save:
                    return
                staged.extend(stage_artwork_pages(
                    item, pages_to_save, quality, save_root,
                    create_folder, staging_root,
                    download_context=download_context,
                    group_artwork=group_artworks,
                    authorization_epoch=authorization_epoch,
                    cancel_event=cancel_event,
                    ugoira_format=ugoira_format,
                ))

            public_saved, cleanup_pending = _stage_and_publish_download(
                save_root,
                prefix=".moku-single-",
                stage=stage_single,
                authorization_epochs=restricted_epochs,
                cancel_event=cancel_event,
                **({"authorization_check": self._download_authorization_snapshot}
                   if hasattr(self, "_queued_authorization") else {}),
            )
            response_payload = {
                "ok": True, "saved": public_saved,
                "quality": quality, "source": "pixiv",
                "pages": len(selected_pages),
                "skippedPages": len(selected_pages) - len(pages_to_save),
                "skippedFiles": skipped_files,
                "catalogWarning": self._record_download_catalog([(item, pages_to_save, len(public_saved))] if pages_to_save else [],
                                                               public_saved, save_root, quality, ugoira_format,
                                                               create_folder, download_context, group_artworks),
                "cleanupPending": cleanup_pending,
                "historyWarning": self._record_download_history([(item, pages_to_save, len(public_saved))] if pages_to_save else [], public_saved, save_root, quality, ugoira_format),
            }
            response_status = 200
        except AuthorizationRevokedError as exc:
            response_payload = {"error": str(exc)}
            response_status = 403
        except UgoiraExportError as exc:
            response_payload = {"error": public_pixiv_error("动图转换", exc),
                                "conversionFailure": {"artworkId": exc.artwork_id, "code": exc.code}}
            response_status = 422
        except PIXIV_OPERATION_ERRORS as exc:
            response_payload = pixiv_error_payload("Pixiv 下载", exc, saving=True)
            response_status = 502
        return response_payload, response_status

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    proxy = refresh_network_opener()
    print(f"Pixiv Tag Gallery: http://127.0.0.1:{port}")
    print(f"Downloads: {DOWNLOADS}")
    print(f"Pixiv network: {proxy or 'direct/TUN'}")
    LocalThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
