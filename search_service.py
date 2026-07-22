from __future__ import annotations

import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Hashable, Iterable

from search_aliases import aliases_for, normalize_alias_key


class SearchInputError(ValueError):
    pass


@dataclass(frozen=True)
class SearchQuery:
    kind: str
    value: str


def parse_search_query(value: str, *, max_length: int = 60) -> SearchQuery:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?is)(pid|author)\s*[:：]\s*(.*)", text)
    if not match:
        return SearchQuery("tags", text or "原创")
    kind = match.group(1).casefold()
    target = match.group(2).strip()[:max_length]
    if not target:
        raise SearchInputError(f"{kind} 搜索缺少目标")
    if kind == "pid" and (not target.isascii() or not target.isdigit()):
        raise SearchInputError("pid 必须是 Pixiv 画师主页中的纯数字用户 ID")
    return SearchQuery(kind, target)


def parse_search_tags(value: str, *, max_tags: int = 6, max_length: int = 60) -> tuple[str, ...]:
    """Split semicolon-separated tags; whitespace inside a tag is significant."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[;；]", str(value or "")):
        tag = unicodedata.normalize("NFKC", raw).strip()[:max_length]
        folded = normalize_alias_key(tag)
        if not tag or folded in seen:
            continue
        seen.add(folded)
        result.append(tag)
        if len(result) >= max_tags:
            break
    return tuple(result or ["原创"])


def build_search_tag_groups(
    value: str, *, fuzzy: bool = False, max_tags: int = 6, max_aliases: int = 8,
) -> tuple[tuple[str, ...], ...]:
    """Build bounded AND groups; aliases within one group are OR targets."""
    groups: list[tuple[str, ...]] = []
    for tag in parse_search_tags(value, max_tags=max_tags):
        aliases = aliases_for(tag) if fuzzy else (tag,)
        unique: list[str] = []
        seen: set[str] = set()
        for alias in aliases[:max_aliases]:
            key = normalize_alias_key(alias)
            if key and key not in seen:
                seen.add(key)
                unique.append(alias)
        groups.append(tuple(unique or (tag,)))
    return tuple(groups or (("原创",),))


def plan_download_chunks(
    groups: Iterable[dict], *, max_artworks: int = 20, max_pages: int = 200,
) -> list[dict]:
    """Split selections by image count, including large single artworks."""
    clean: list[dict] = []
    for group in groups:
        artwork_id = str(group.get("id") or "")
        try:
            pages = sorted({int(page) for page in (group.get("pages") or [])})
        except (TypeError, ValueError):
            continue
        if artwork_id.isascii() and artwork_id.isdigit() and pages:
            clean.extend(
                {"id": artwork_id, "pages": pages[offset:offset + max_pages]}
                for offset in range(0, len(pages), max_pages)
            )
    chunks: list[dict] = []
    current: list[dict] = []
    page_count = 0
    for group in clean:
        group_pages = len(group["pages"])
        if current and (
            page_count + group_pages > max_pages
            or len(current) >= max_artworks
        ):
            chunks.append({"groups": current, "pageCount": page_count})
            current = []
            page_count = 0
        current.append(group)
        page_count += group_pages
    if current:
        chunks.append({"groups": current, "pageCount": page_count})
    return chunks


def resolve_source_modes(scope: str, *, authorized: bool) -> tuple[str, ...]:
    clean = str(scope or "safe")
    if clean == "safe":
        return ("safe",)
    if clean == "r18":
        if not authorized:
            raise SearchInputError("R-18搜索需要先完成Pixiv账户授权")
        return ("r18",)
    if clean == "all":
        if not authorized:
            raise SearchInputError("全部范围包含R-18，需要先完成Pixiv账户授权")
        return ("safe", "r18")
    raise SearchInputError("不支持的安全范围")


def prefetch_item_count(page: int, *, per_page: int = 36, ahead: int = 3) -> int:
    return (max(1, int(page)) + max(0, int(ahead))) * max(1, int(per_page))


@dataclass
class _PageSession:
    pages: dict[int, list[Any]] = field(default_factory=dict)


class SearchPageCache:
    """Small LRU of normalized result pages with a sliding backward window."""

    def __init__(self, *, keep_behind: int = 6, max_sessions: int = 12) -> None:
        self.keep_behind = max(0, int(keep_behind))
        self.max_sessions = max(1, int(max_sessions))
        self._sessions: OrderedDict[Hashable, _PageSession] = OrderedDict()
        self._lock = threading.RLock()

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def drop(self, key: Hashable) -> None:
        with self._lock:
            self._sessions.pop(key, None)

    def store_pages(self, key: Hashable, current_page: int, pages: dict[int, Iterable]) -> None:
        with self._lock:
            session = self._sessions.pop(key, _PageSession())
            for page, items in pages.items():
                number = max(1, int(page))
                session.pages[number] = list(items)
            oldest = max(1, int(current_page) - self.keep_behind)
            session.pages = {page: items for page, items in session.pages.items() if page >= oldest}
            self._sessions[key] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)

    def get_page(self, key: Hashable, page: int) -> list[Any] | None:
        with self._lock:
            session = self._sessions.pop(key, None)
            if session is None:
                return None
            self._sessions[key] = session
            items = session.pages.get(max(1, int(page)))
            return list(items) if items is not None else None

    def available_pages(self, key: Hashable) -> list[int]:
        with self._lock:
            session = self._sessions.get(key)
            return sorted(session.pages) if session else []

    def preloaded_through(self, key: Hashable) -> int:
        pages = self.available_pages(key)
        return pages[-1] if pages else 0
