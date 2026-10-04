"""Small durable basket and paused download queue, with no image URLs."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class WorkspaceCapacityError(ValueError):
    pass


def _integer(value, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid queue number")
    return value


def _id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,30}", value):
        raise ValueError("invalid artwork ID")
    return value


def _request_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", value):
        raise ValueError("invalid task ID")
    return value


def _context(value) -> dict:
    if value is None:
        return {"kind": "tags", "value": "原创"}
    if (not isinstance(value, dict) or set(value) != {"kind", "value"}
            or value["kind"] not in {"tags", "pid", "uid", "author"}
            or not isinstance(value["value"], str) or not 1 <= len(value["value"]) <= 500):
        raise ValueError("invalid task context")
    return dict(value)


def _pages(value, maximum=1000) -> list[int]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError("invalid queue pages")
    return sorted({_integer(page, 0, 1000000) for page in value})


def _options(value, *, single=False) -> dict:
    keys = {"quality", "ugoiraFormat", "saveRoot", "createFolder", "groupArtworks", "skipExisting"}
    if single:
        keys |= {"id", "pages", "context"}
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError("invalid queue options")
    if value.get("quality", "original") not in {"original", "regular"} or value.get("ugoiraFormat", "source") not in {"source", "gif", "mp4"}:
        raise ValueError("invalid queue format")
    if any(type(value.get(key, False)) is not bool for key in ("createFolder", "groupArtworks", "skipExisting")):
        raise ValueError("invalid queue options")
    root = value.get("saveRoot", "")
    if not isinstance(root, str) or len(root) > 32768:
        raise ValueError("invalid queue directory")
    result = dict(value)
    if single:
        result["id"] = _id(value.get("id"))
        result["pages"] = _pages(value.get("pages"))
        if "context" in value:
            result["context"] = _context(value["context"])
    return result


def normalize_task(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in {"single", "batch"}:
        raise ValueError("invalid queue task")
    kind = value["kind"]
    options = value.get("taskOptions")
    if kind == "single":
        if not isinstance(options, dict) or set(options) != {"endpoint", "body"} or options["endpoint"] != "/api/pixiv/download":
            raise ValueError("invalid queue endpoint")
        options = {"endpoint": options["endpoint"], "body": _options(options["body"], single=True)}
    else:
        options = _options(options)
    raw_chunks = value.get("remainingChunks")
    if not isinstance(raw_chunks, list) or not 1 <= len(raw_chunks) <= 1000:
        raise ValueError("invalid queue chunks")
    chunks, total_pages, request_ids = [], 0, set()
    for raw in raw_chunks:
        if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list) or not 1 <= len(raw["groups"]) <= 20:
            raise ValueError("invalid queue groups")
        groups = [{"id": _id(group.get("id")), "pages": _pages(group.get("pages"), 200)}
                  for group in raw["groups"] if isinstance(group, dict)]
        count = sum(len(group["pages"]) for group in groups)
        if len(groups) != len(raw["groups"]) or not 1 <= count <= 200:
            raise ValueError("invalid queue size")
        if kind == "single" and (len(groups) != 1 or groups[0]["id"] != options["body"]["id"]):
            raise ValueError("invalid single queue groups")
        request_id = _request_id(raw.get("requestId"))
        if request_id in request_ids:
            raise ValueError("duplicate chunk ID")
        request_ids.add(request_id)
        chunks.append({"groups": groups, "pageCount": count, "requestId": request_id,
                       "context": _context(raw.get("context"))})
        total_pages += count
    if total_pages > 1000:
        raise ValueError("queue exceeds 1000 pages")
    completed = _integer(value.get("completedBatches", 0), 0, 1000)
    total = _integer(value.get("totalBatches"), 1, 1000)
    if completed + len(chunks) != total:
        raise ValueError("invalid queue progress")
    return {"id": _request_id(value.get("id")), "kind": kind, "taskOptions": options,
            "remainingChunks": chunks, "completedBatches": completed, "totalBatches": total,
            "savedCount": _integer(value.get("savedCount", 0), 0, 1000),
            "fileCount": _integer(value.get("fileCount", 0), 0, 2000),
            "skippedCount": _integer(value.get("skippedCount", 0), 0, 1000),
            "firstSaved": str(value.get("firstSaved") or "")[:32768], "status": "paused"}


class WorkspaceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            with connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise ValueError("unsupported workspace schema")
                connection.execute("CREATE TABLE IF NOT EXISTS basket (singleton INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL)")
                connection.execute("CREATE TABLE IF NOT EXISTS recent (singleton INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                connection.execute("PRAGMA user_version=1")
                yield connection
        finally:
            connection.close()

    def load(self, scope: str) -> dict:
        result = {"basket": [], "revision": 0, "tasks": [], "recent": [], "scope": scope}
        with self._lock:
            if not self.path.exists():
                return result
            with self._connection() as connection:
                row = connection.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
                if row:
                    result["revision"] = row[0]
                    result["basket"] = [item for owner, item in json.loads(row[1]) if owner in {scope, "public"}]
                result["tasks"] = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM tasks WHERE scope=? ORDER BY rowid", (scope,))]
                row = connection.execute("SELECT payload FROM recent WHERE singleton=1").fetchone()
                if row:
                    result["recent"] = [item for owner, item in json.loads(row[0]) if owner in {scope, "public"}]
        return result

    def remember_search(self, value: dict, scope: str) -> None:
        if not isinstance(value, dict) or set(value) != {"tag", "filters"}:
            raise ValueError("invalid recent search")
        tag, filters = value["tag"], value["filters"]
        if not isinstance(tag, str) or not 1 <= len(tag) <= 500 or not isinstance(filters, dict):
            raise ValueError("invalid recent search")
        if set(filters) != {"mode", "workType", "includeAi", "fuzzy", "startDate", "endDate"}:
            raise ValueError("invalid recent filters")
        if (filters["mode"] not in {"safe", "r18", "all"} or filters["workType"] not in {"all", "illustration", "manga", "ugoira"}
                or type(filters["includeAi"]) is not bool or type(filters["fuzzy"]) is not bool
                or any(not isinstance(filters[key], str) or not re.fullmatch(r"(?:\d{4}-\d{2}-\d{2})?", filters[key]) for key in ("startDate", "endDate"))):
            raise ValueError("invalid recent filters")
        owner = "public" if filters["mode"] == "safe" else scope
        if owner == "public" and filters["mode"] != "safe":
            raise ValueError("restricted search needs account")
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM recent WHERE singleton=1").fetchone()
            recent = json.loads(row[0]) if row else []
            recent = [(owner, value)] + [(old_owner, old) for old_owner, old in recent if old != value or old_owner != owner]
            connection.execute("INSERT OR REPLACE INTO recent VALUES (1,?)", (json.dumps(recent[:8], ensure_ascii=False),))

    def save_basket(self, rows: list[dict], scope: str, *, revision: int) -> int:
        _integer(revision, 0, 2**63 - 2)
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError("invalid basket")
        normalized, total, ids = [], 0, set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("item"), dict):
                raise ValueError("invalid basket item")
            artwork = _id(row.get("id"))
            if artwork in ids:
                raise ValueError("duplicate basket item")
            ids.add(artwork)
            pages = _pages(row.get("pages"))
            raw = row["item"]
            count = _integer(raw.get("pages"), 1, 1000001)
            if pages[-1] >= count or raw.get("restriction", "safe") not in {"safe", "r18"}:
                raise ValueError("invalid basket pages")
            if raw.get("restriction") == "r18" and scope == "public":
                raise ValueError("restricted basket needs account")
            item = {"id": artwork, "source": "pixiv", "pages": count,
                    "title": str(raw.get("title") or "未命名作品")[:1000],
                    "artist": str(raw.get("artist") or "未知画师")[:500],
                    "restriction": raw.get("restriction", "safe"),
                    "workType": raw.get("workType") if raw.get("workType") in {"illustration", "manga", "ugoira"} else "illustration",
                    "tags": [str(tag)[:100] for tag in raw.get("tags", [])[:30]] if isinstance(raw.get("tags", []), list) else [],
                    "bookmarks": None, "thumb": ""}
            # Thumbnails are fetched again on entry; no expiring capability is stored.
            item.pop("thumb")
            normalized.append((scope if item["restriction"] == "r18" else "public", {
                "id": artwork, "item": item, "pages": pages, "context": _context(row.get("context")),
                "resultPage": _integer(row.get("resultPage", 1), 1, 100000), "archived": bool(row.get("archived", True))}))
            total += len(pages)
        if total > 1000:
            raise WorkspaceCapacityError("采集篮容量为 1000 张，请取消部分选择后重试")
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
            if (old[0] if old else 0) != revision:
                raise ValueError("stale basket revision")
            if old:
                normalized += [(owner, row) for owner, row in json.loads(old[1]) if owner not in {scope, "public"}]
            owner_counts = {}
            for owner, row in normalized:
                owner_counts[owner] = owner_counts.get(owner, 0) + len(row["pages"])
            public_count = owner_counts.pop("public", 0)
            if public_count > 1000 or any(public_count + count > 1000 for count in owner_counts.values()):
                raise WorkspaceCapacityError("共享采集篮容量不足，请减少公开作品的选择后重试")
            connection.execute("INSERT OR REPLACE INTO basket VALUES (1,?,?)", (revision + 1, json.dumps(normalized, ensure_ascii=False)))
        return revision + 1

    def save_task(self, value: dict, scope: str) -> dict:
        task = normalize_task(value)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT scope FROM tasks WHERE id=?", (task["id"],)).fetchone()
            if old and old[0] != scope:
                raise ValueError("task belongs to another account")
            if not old and connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] >= 20:
                raise ValueError("最多保留 20 个未完成任务，请先移除旧任务")
            connection.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)", (task["id"], scope, json.dumps(task, ensure_ascii=False)))
        return task

    def delete_task(self, task_id: str, scope: str) -> bool:
        _request_id(task_id)
        with self._lock, self._connection() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id=? AND scope=?", (task_id, scope))
            return cursor.rowcount > 0

    def has_task(self, task_id: str, scope: str) -> bool:
        _request_id(task_id)
        with self._lock:
            if not self.path.exists():
                return False
            with self._connection() as connection:
                return connection.execute("SELECT 1 FROM tasks WHERE id=? AND scope=?", (task_id, scope)).fetchone() is not None
