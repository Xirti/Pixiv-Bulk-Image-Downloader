from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Hashable, Iterable


class SearchInputError(ValueError):
    pass


def parse_search_tags(value: str, *, max_tags: int = 6, max_length: int = 60) -> tuple[str, ...]:
    """Split whitespace-separated Pixiv tags into bounded OR targets."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split():
        tag = raw.strip()[:max_length]
        folded = tag.casefold()
        if not tag or folded in seen:
            continue
        seen.add(folded)
        result.append(tag)
        if len(result) >= max_tags:
            break
    return tuple(result or ["原创"])


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

    def clear(self) -> None:
        self._sessions.clear()

    def drop(self, key: Hashable) -> None:
        self._sessions.pop(key, None)

    def store_pages(self, key: Hashable, current_page: int, pages: dict[int, Iterable]) -> None:
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
        session = self._sessions.pop(key, None)
        if session is None:
            return None
        self._sessions[key] = session
        items = session.pages.get(max(1, int(page)))
        return list(items) if items is not None else None

    def available_pages(self, key: Hashable) -> list[int]:
        session = self._sessions.get(key)
        return sorted(session.pages) if session else []

    def preloaded_through(self, key: Hashable) -> int:
        pages = self.available_pages(key)
        return pages[-1] if pages else 0
