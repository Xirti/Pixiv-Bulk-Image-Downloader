"""Public artist metadata and exact-name candidates, separate from artwork downloads."""
from __future__ import annotations

import copy
import html
import re
import threading
import time
import urllib.parse
from collections import OrderedDict

from pixiv_adapter import (PixivPolicyError, build_user_profile_all_url, build_user_search_url,
                           is_allowed_pixiv_url, normalize_search_item, valid_pixiv_id)


def _text(value, limit=10000):
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()[:limit] if isinstance(value, str) else ""


def _image(value):
    return value if is_allowed_pixiv_url(value, image_only=True) else ""


def normalize_artist(raw, expected_id=None):
    if not isinstance(raw, dict):
        raise PixivPolicyError("Pixiv 作者资料格式异常")
    user_id = str(raw.get("userId") or raw.get("id") or "")
    if not valid_pixiv_id(user_id) or (expected_id is not None and user_id != expected_id):
        raise PixivPolicyError("Pixiv 作者身份不匹配")
    background = raw.get("background")
    banner = _image(background.get("url")) if isinstance(background, dict) and not background.get("isPrivate") else ""
    region = raw.get("region")
    name = raw.get("name") or raw.get("userName")
    name = name.strip()[:120] if isinstance(name, str) else ""
    return {
        "userId": user_id, "name": name or f"Pixiv 用户 {user_id}",
        "avatar": _image(raw.get("imageBig") or raw.get("profileImageUrl") or raw.get("image")),
        "background": banner, "description": _text(raw.get("comment") or raw.get("description")),
        "region": _text(region.get("name"), 120) if isinstance(region, dict) else "",
        "webpage": _text(raw.get("webpage"), 1000),
    }


def normalize_candidates(body, name, page):
    if not isinstance(body, dict) or not isinstance(body.get("page"), dict):
        raise PixivPolicyError("Pixiv 作者候选格式异常")
    users, ids = body.get("users"), body["page"].get("userIds")
    if not isinstance(users, (list, dict)) or not isinstance(ids, list) or len(ids) > 100:
        raise PixivPolicyError("Pixiv 作者候选列表格式异常")
    rows = users.values() if isinstance(users, dict) else users
    by_id = {}
    for raw in rows:
        try:
            artist = normalize_artist(raw)
        except PixivPolicyError:
            continue
        if artist["name"].casefold() == name.casefold():
            by_id[artist["userId"]] = artist
    candidates = [by_id[str(uid)] for uid in ids if str(uid) in by_id]
    return {"query": name, "page": page, "candidates": candidates, "hasMore": len(ids) >= 20}


def normalize_overview(body, user_id):
    if not isinstance(body, dict):
        raise PixivPolicyError("Pixiv 作者作品索引格式异常")
    counts = {}
    for key in ("illusts", "manga"):
        rows = body.get(key) or {}
        if not isinstance(rows, (dict, list)):
            raise PixivPolicyError("Pixiv 作者作品索引字段异常")
        counts[key] = len(rows)
    pickup = body.get("pickup") or []
    if not isinstance(pickup, list):
        raise PixivPolicyError("Pixiv 作者精选格式异常")
    featured = []
    for raw in pickup[:12]:
        if not isinstance(raw, dict) or raw.get("type") not in {"illust", "manga"}:
            continue
        try:
            item = normalize_search_item(raw)
        except PixivPolicyError:
            continue
        if item["userId"] == user_id:
            featured.append(item)
    return {"userId": user_id, "counts": counts, "featured": featured}


class ArtistService:
    def __init__(self, fetch):
        self._fetch = fetch
        self._cache = OrderedDict()
        self._pending = {}
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._pending.clear()

    def read(self, kind, target, *, generation, page=1, cancel_event=None, refresh=False):
        if kind not in {"profile", "overview", "candidates"}:
            raise PixivPolicyError("不支持的作者读取")
        if kind == "candidates":
            target = target.strip()
            if not 1 <= len(target) <= 60 or not 1 <= page <= 1000:
                raise PixivPolicyError("作者名称或页码无效")
            url = build_user_search_url(target, page=page)
        else:
            if not valid_pixiv_id(target):
                raise PixivPolicyError("作者 UID 无效")
            url = (f"https://www.pixiv.net/ajax/user/{target}?full=1&lang=zh" if kind == "profile"
                   else build_user_profile_all_url(target))
        key = (generation, kind, target, page)
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return copy.deepcopy(cached[1])
            pending=object();self._pending[key]=pending
        try:
            payload = self._fetch(url, cancel_event)
            body = payload.get("body")
            result = (normalize_artist(body, target) if kind == "profile" else
                      normalize_overview(body, target) if kind == "overview" else
                      normalize_candidates(body, target, page))
            if cancel_event is not None and cancel_event.is_set():
                raise PixivPolicyError("作者读取已取消")
            with self._lock:
                if self._pending.get(key) is not pending:
                    cached=self._cache.get(key)
                    return copy.deepcopy(cached[1]) if cached else result
                self._cache[key] = (time.monotonic() + 300, copy.deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > 48:
                    self._cache.popitem(last=False)
            return result
        finally:
            with self._lock:
                if self._pending.get(key) is pending:self._pending.pop(key)
