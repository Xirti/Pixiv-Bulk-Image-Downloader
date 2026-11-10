"""Small durable basket and paused download queue, with no image URLs."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager, closing
from pathlib import Path


class TaskConflictError(ValueError):
    pass


class WorkspaceCapacityError(ValueError):
    pass


class TaskCapacityError(ValueError):
    def __init__(self, own_count: int, other_count: int):
        super().__init__("未完成任务已满（最多 20 项）")
        self.own_count = own_count
        self.other_count = other_count


def _integer(value, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid queue number")
    return value


def _id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,30}", value):
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
            or value["kind"] not in ("tags", "pid", "uid", "author")
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
    if value.get("quality", "original") not in ("original", "regular") or value.get("ugoiraFormat", "source") not in ("source", "gif", "mp4"):
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
    if not isinstance(value, dict) or value.get("kind") not in ("single", "batch"):
        raise ValueError("invalid queue task")
    kind = value["kind"]
    policy = value.get("chunkPolicy")
    if policy is not None and policy != "animation-artwork-v1":
        raise ValueError("invalid queue chunk policy")
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
    failure = value.get("conversionFailure")
    if failure is not None:
        if (not isinstance(failure, dict) or set(failure) != {"artworkId", "code"}
                or not isinstance(failure["code"], str) or not re.fullmatch(r"[a-z_]{1,40}", failure["code"])):
            raise ValueError("invalid conversion failure")
        failure = {"artworkId": _id(failure["artworkId"]), "code": failure["code"]}
    selection_key=value.get("selectionKey")
    if selection_key is not None:
        if not isinstance(selection_key,str) or len(selection_key)>100000:raise ValueError("invalid progress identity")
        groups=json.loads(selection_key)
        if not isinstance(groups,list) or len(groups)>1000:raise ValueError("invalid progress identity")
        for group in groups:
            if not isinstance(group,dict) or set(group)!={"id","pages"}:raise ValueError("invalid progress identity")
            _id(group["id"]);_pages(group["pages"])
    return {**({"selectionKey":selection_key} if selection_key is not None else {}),**({"conversionFailure": failure} if failure is not None else {}),
            **({"chunkPolicy": policy} if policy is not None else {}),
            "id": _request_id(value.get("id")), "kind": kind, "taskOptions": options,
            "revision": _integer(value.get("revision", 0), 0, 2147483647),
            "remainingChunks": chunks, "completedBatches": completed, "totalBatches": total,
            "savedCount": _integer(value.get("savedCount", 0), 0, 1000),
            "fileCount": _integer(value.get("fileCount", 0), 0, 2000),
            "skippedCount": _integer(value.get("skippedCount", 0), 0, 1000),
            "firstSaved": str(value.get("firstSaved") or "")[:32768], "status": "paused"}


def _visible_basket(rows: list, scope: str) -> list[dict]:
    public = {row["id"]: row for owner, row in rows if owner == "public"}
    visible = {}
    for owner, row in rows:
        if owner not in {scope, "public"}:
            continue
        shared = public.get(row["id"])
        if owner == "public" or shared is None:
            visible.setdefault(row["id"], row)
            continue
        # A formerly restricted work may now be in the shared basket.
        # Keep this account's choices and origin, using its newer public metadata.
        pages = sorted(set(row["pages"]) | set(shared["pages"]))
        visible[row["id"]] = {**row, "item": shared["item"],
                              "pages": [page for page in pages if page < shared["item"]["pages"]],
                              "archived": row.get("archived", True) or shared.get("archived", True)}
    return list(visible.values())


def _preserve_basket_ownership(rows: list, previous: list, scope: str) -> list:
    owned = {row["id"]: row for owner, row in previous if owner == scope and scope != "public"}
    public = {row["id"]: row for owner, row in previous if owner == "public"}
    result = []
    for owner, row in rows:
        private = owned.get(row["id"])
        if owner != "public" or private is None:
            result.append((owner, row))
            continue
        # Public metadata must not promote this account's existing private choices.
        selected, private_pages = set(row["pages"]), set(private["pages"])
        kept_private = sorted(selected & private_pages)
        if kept_private:
            result.append((scope, {**row, "pages": kept_private, "context": private["context"],
                                   "resultPage": private["resultPage"]}))
        shared = public.get(row["id"])
        shared_pages = set(shared["pages"]) if shared else set()
        kept_public = sorted((selected - private_pages) | (selected & shared_pages))
        if kept_public:
            origin = shared or {"context": {"kind": "pid", "value": row["id"]}, "resultPage": 1}
            result.append(("public", {**row, "pages": kept_public, "context": origin["context"],
                                      "resultPage": origin["resultPage"]}))
    return result + [(owner, row) for owner, row in previous if owner not in {scope, "public"}]


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
                if version not in {0, 1, 2}:
                    raise ValueError("unsupported workspace schema")
                if version == 1:
                    backup=self.path.with_name(self.path.stem+".v1-backup.sqlite3")
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as destination:connection.backup(destination)
                if version == 0:
                    connection.execute("CREATE TABLE IF NOT EXISTS basket (singleton INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL)")
                    connection.execute("CREATE TABLE IF NOT EXISTS recent (singleton INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
                    connection.execute("PRAGMA user_version=1")
                # Retain only task identities so a lost initial-save reply
                # cannot turn a removed task into a new create operation.
                connection.execute("CREATE TABLE IF NOT EXISTS task_removals (id TEXT PRIMARY KEY) WITHOUT ROWID")
                connection.execute("CREATE TABLE IF NOT EXISTS task_chunks (task_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,ordinal)) WITHOUT ROWID")
                if version != 2:connection.execute("PRAGMA user_version=2")
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
                    result["basket"] = _visible_basket(json.loads(row[1]), scope)
                result["tasks"] = [self._expand_task(connection,json.loads(row[0])) for row in connection.execute("SELECT payload FROM tasks WHERE scope=? ORDER BY rowid", (scope,))]
                row = connection.execute("SELECT payload FROM recent WHERE singleton=1").fetchone()
                if row:
                    result["recent"] = [item for owner, item in json.loads(row[0]) if owner in {scope, "public"}]
        return result

    def remember_search(self, value: dict, scope: str) -> None:
        self._update_recent_search(value, scope, remove=False)

    def delete_recent_search(self, value: dict, scope: str) -> None:
        self._update_recent_search(value, scope, remove=True)

    def clear_recent_searches(self, scope: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row=connection.execute("SELECT payload FROM recent WHERE singleton=1").fetchone()
            recent=json.loads(row[0]) if row else []
            retained=[(owner,value) for owner,value in recent if owner not in {scope,"public"}]
            connection.execute("INSERT OR REPLACE INTO recent VALUES(1,?)",(json.dumps(retained,ensure_ascii=False),))

    def _update_recent_search(self, value: dict, scope: str, *, remove: bool) -> None:
        if not isinstance(value, dict) or set(value) != {"tag", "filters"}:
            raise ValueError("invalid recent search")
        tag, filters = value["tag"], value["filters"]
        if not isinstance(tag, str) or not 1 <= len(tag) <= 500 or not isinstance(filters, dict):
            raise ValueError("invalid recent search")
        if set(filters) != {"mode", "workType", "includeAi", "fuzzy", "startDate", "endDate"}:
            raise ValueError("invalid recent filters")
        if (filters["mode"] not in ("safe", "r18", "all") or filters["workType"] not in ("all", "illustration", "manga", "ugoira")
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
            recent = [(old_owner, old) for old_owner, old in recent if old != value or old_owner != owner]
            if not remove:
                recent.insert(0, (owner, value))
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
            if pages[-1] >= count or raw.get("restriction", "safe") not in ("safe", "r18"):
                raise ValueError("invalid basket pages")
            if raw.get("restriction") == "r18" and scope == "public":
                raise ValueError("restricted basket needs account")
            item = {"id": artwork, "source": "pixiv", "pages": count,
                    "title": str(raw.get("title") or "未命名作品")[:1000],
                    "artist": str(raw.get("artist") or "未知画师")[:500],
                    "restriction": raw.get("restriction", "safe"),
                    "workType": raw.get("workType") if raw.get("workType") in ("illustration", "manga", "ugoira") else "illustration",
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
            normalized = _preserve_basket_ownership(normalized, json.loads(old[1]) if old else [], scope)
            scopes = {"public", *(owner for owner, _row in normalized)}
            if any(sum(len(row["pages"]) for row in _visible_basket(normalized, owner)) > 1000 for owner in scopes):
                raise WorkspaceCapacityError("共享采集篮容量不足，请减少公开作品的选择后重试")
            connection.execute("INSERT OR REPLACE INTO basket VALUES (1,?,?)", (revision + 1, json.dumps(normalized, ensure_ascii=False)))
        return revision + 1

    def save_task(self, value: dict, scope: str, *, expected: dict | None = None,
                  require_existing: bool = False) -> dict:
        task = normalize_task(value)
        previous = normalize_task(expected) if expected is not None else None
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT scope,payload FROM tasks WHERE id=?", (task["id"],)).fetchone()
            if connection.execute("SELECT 1 FROM task_removals WHERE id=?", (task["id"],)).fetchone():
                raise TaskConflictError("任务已移除，请重新发起下载")
            if old and old[0] != scope:
                raise ValueError("task belongs to another account")
            stored = self._expand_task(connection,json.loads(old[1])) if old else None
            if stored:
                # A lost reply may be retried with the previous version. Exact
                # state replays are harmless; stale mutations must re-read.
                if stored["revision"] and {k: v for k, v in task.items() if k != "revision"} == {k: v for k, v in stored.items() if k != "revision"}:
                    return stored
                if task["revision"] != stored["revision"]:
                    raise TaskConflictError("任务进度已在另一个窗口更新，请重新读取任务")
            elif require_existing or task["revision"] or previous is not None:
                raise TaskConflictError("任务已移除，请重新读取任务")
            if stored and stored.get("chunkPolicy") and stored.get("chunkPolicy") != task.get("chunkPolicy"):
                raise TaskConflictError("任务分批方式已更新，请重新读取任务")
            if previous is not None:
                migration = (stored and not stored.get("chunkPolicy") and task.get("chunkPolicy")
                             and stored["kind"] == task["kind"] and stored["taskOptions"] == task["taskOptions"])
                changing_options = stored and (stored["kind"] != task["kind"] or stored["taskOptions"] != task["taskOptions"])
                if previous["id"] != task["id"] or not stored or stored != previous or (changing_options and not stored.get("conversionFailure") and not migration):
                    raise TaskConflictError("任务已在另一个窗口更新，请重新读取后调整")
            elif stored and (stored["kind"] != task["kind"] or stored["taskOptions"] != task["taskOptions"]):
                # A stale window must not overwrite an explicitly adjusted task.
                raise TaskConflictError("任务参数已在另一个窗口调整，请重新读取任务")
            total, own = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(scope=?),0) FROM tasks", (scope,),
            ).fetchone()
            if not old and total >= 20:
                raise TaskCapacityError(own, total - own)
            task["revision"] += 1
            connection.execute("DELETE FROM task_chunks WHERE task_id=?",(task["id"],))
            connection.executemany("INSERT INTO task_chunks VALUES (?,?,?)",[(task["id"],i,json.dumps(chunk,ensure_ascii=False)) for i,chunk in enumerate(task["remainingChunks"])])
            compact={k:v for k,v in task.items() if k!="remainingChunks"}
            compact["_cursor"]=0
            connection.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?)", (task["id"], scope, json.dumps(compact, ensure_ascii=False)))
        return task

    def _expand_task(self,connection,task):
        if "_cursor" in task:
            cursor=task.pop("_cursor")
            task.pop("_lastRequest",None)
            task["remainingChunks"]=[json.loads(row[0]) for row in connection.execute("SELECT payload FROM task_chunks WHERE task_id=? AND ordinal>=? ORDER BY ordinal",(task["id"],cursor))]
        return normalize_task(task)

    def advance_task(self, value, scope):
        if not isinstance(value,dict) or set(value)!={"id","revision","requestId","progress"}:raise ValueError("invalid checkpoint")
        task_id=_request_id(value["id"]);request_id=_request_id(value["requestId"])
        revision=_integer(value["revision"],0,2147483647)
        progress=value["progress"]
        if not isinstance(progress,dict) or set(progress)!={"savedCount","fileCount","skippedCount","firstSaved"}:raise ValueError("invalid checkpoint progress")
        for key,maximum in (("savedCount",1000),("fileCount",2000),("skippedCount",1000)):_integer(progress[key],0,maximum)
        if not isinstance(progress["firstSaved"],str) or len(progress["firstSaved"])>32768:raise ValueError("invalid checkpoint path")
        with self._lock,self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row=connection.execute("SELECT payload FROM tasks WHERE id=? AND scope=?",(task_id,scope)).fetchone()
            if not row:raise TaskConflictError("任务已移除，请重新读取")
            task=json.loads(row[0])
            if task.get("_lastRequest")==request_id and task["revision"]==revision+1:return {"revision":task["revision"]}
            if task["revision"]!=revision or "_cursor" not in task:raise TaskConflictError("任务已更新，请重新读取")
            chunk=connection.execute("SELECT payload FROM task_chunks WHERE task_id=? AND ordinal=?",(task_id,task["_cursor"])).fetchone()
            if not chunk or json.loads(chunk[0])["requestId"]!=request_id:raise TaskConflictError("下载回执与任务不符")
            if any(progress[key]<task[key] for key in ("savedCount","fileCount","skippedCount")):raise ValueError("checkpoint went backwards")
            task.update(progress);task["completedBatches"]+=1;task["_cursor"]+=1;task["revision"]+=1;task["_lastRequest"]=request_id
            if task["completedBatches"]>=task["totalBatches"]:raise ValueError("completed task must be removed")
            connection.execute("UPDATE tasks SET payload=? WHERE id=?",(json.dumps(task,ensure_ascii=False),task_id))
            return {"revision":task["revision"]}

    def delete_task(self, task_id: str, scope: str, *, revision: int | None = None) -> bool:
        _request_id(task_id)
        if revision is not None:
            _integer(revision, 0, 2147483647)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM tasks WHERE id=? AND scope=?", (task_id, scope)).fetchone()
            if row and revision is not None and json.loads(row[0]).get("revision", 0) != revision:
                raise TaskConflictError("任务已在另一个窗口更新，请重新读取后移除")
            if row:
                connection.execute("INSERT OR IGNORE INTO task_removals VALUES (?)", (task_id,))
            if row:connection.execute("DELETE FROM task_chunks WHERE task_id=?",(task_id,))
            cursor = connection.execute("DELETE FROM tasks WHERE id=? AND scope=?", (task_id, scope))
            return cursor.rowcount > 0

    def has_task(self, task_id: str, scope: str) -> bool:
        _request_id(task_id)
        with self._lock:
            if not self.path.exists():
                return False
            with self._connection() as connection:
                return connection.execute("SELECT 1 FROM tasks WHERE id=? AND scope=?", (task_id, scope)).fetchone() is not None

    def get_task(self, task_id: str, scope: str, *, head: int | None = None) -> dict | None:
        _request_id(task_id)
        with self._lock:
            if not self.path.exists():
                return None
            with self._connection() as connection:
                row = connection.execute("SELECT payload FROM tasks WHERE id=? AND scope=?", (task_id, scope)).fetchone()
                if not row:return None
                task=json.loads(row[0])
                if head is not None and "_cursor" in task:
                    task["remainingChunks"]=[json.loads(chunk[0]) for chunk in connection.execute(
                        "SELECT payload FROM task_chunks WHERE task_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?",(task_id,task["_cursor"],head))]
                    task.pop("_cursor");task.pop("_lastRequest",None)
                    return task
                return self._expand_task(connection,task)
