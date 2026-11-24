"""Small durable basket and paused download queue, with no image URLs."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager, closing
from pathlib import Path
from pixiv_adapter import CONTENT_MODES, CONTENT_RESTRICTIONS, RESTRICTED_CONTENT


class TaskConflictError(ValueError):
    pass


BASKET_RECEIPT_REVISIONS = 1024


class BasketRevisionConflict(ValueError):
    def __init__(self, *, receipt_expired=False):
        super().__init__("采集篮旧保存无法确认，请重新打开工作区核对" if receipt_expired
                         else "采集篮已在另一个窗口更新，请重试保存")
        self.receipt_expired = receipt_expired


class WorkspaceCapacityError(ValueError):
    pass


def account_scope(identity: str) -> str:
    return "account:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


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


def _visible_basket(rows: list, scope: str, checks: dict | None = None) -> list[dict]:
    public = {row["id"]: row for owner, row in rows if owner == "public"}
    visible = {}
    for owner, row in rows:
        if owner not in {scope, "public"}:
            continue
        shared = public.get(row["id"])
        if owner == "public" or shared is None:
            visible.setdefault(row["id"], row)
            continue
        # Use the shared metadata for display. A shorter summary cannot
        # confirm that this account's extra choices no longer exist.
        pages = sorted(set(row["pages"]) | set(shared["pages"]))
        count = shared["item"]["pages"]
        hidden = [page for page in row["pages"] if page >= count]
        if checks is not None and hidden:
            prior = checks.get(row["id"])
            pending = sorted(set(hidden) | set(prior["pages"] if prior else ()))
            check_input = (scope, row["id"], count, pending, prior["token"] if prior else None)
            token = hashlib.sha256(json.dumps(check_input).encode()).hexdigest()[:32]
            checks[row["id"]] = {"token": token, "pages": pending}
        visible[row["id"]] = {**row, "item": shared["item"],
                              "pages": [page for page in pages if page < count],
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
        shared = public.get(row["id"])
        hidden = {page for page in private_pages if shared and page >= shared["item"]["pages"]}
        kept_private = sorted((selected & private_pages) | hidden)
        if kept_private:
            metadata = private["item"] if hidden else row["item"]
            result.append((scope, {**row, "item": metadata, "pages": kept_private, "context": private["context"],
                                   "resultPage": private["resultPage"]}))
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
                connection.execute("""CREATE TABLE IF NOT EXISTS basket_page_checks (
                    scope TEXT NOT NULL, artwork TEXT NOT NULL, token TEXT NOT NULL, pages TEXT NOT NULL,
                    PRIMARY KEY(scope,artwork))""")
                # Retain only task identities so a lost initial-save reply
                # cannot turn a removed task into a new create operation.
                connection.execute("CREATE TABLE IF NOT EXISTS task_removals (id TEXT PRIMARY KEY) WITHOUT ROWID")
                connection.execute("CREATE TABLE IF NOT EXISTS task_chunks (task_id TEXT NOT NULL, ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,ordinal)) WITHOUT ROWID")
                connection.execute("CREATE TABLE IF NOT EXISTS web_workspace_owners (legacy_scope TEXT PRIMARY KEY, account_scope TEXT NOT NULL)")
                connection.execute("""CREATE TABLE IF NOT EXISTS basket_receipts (
                    request_id TEXT PRIMARY KEY, scope TEXT NOT NULL, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL
                ) WITHOUT ROWID""")
                connection.execute("""CREATE TABLE IF NOT EXISTS recent_writers (
                    writer TEXT PRIMARY KEY, scope TEXT NOT NULL, sequence INTEGER NOT NULL, fingerprint TEXT NOT NULL
                ) WITHOUT ROWID""")
                # Keep the v2.1.9 payload readable and invalidate its row
                # identities when that version replaces or updates the list.
                connection.execute("""CREATE TABLE IF NOT EXISTS recent_identity (
                    singleton INTEGER PRIMARY KEY, generation TEXT NOT NULL, ids TEXT)""")
                for event in ("INSERT", "UPDATE"):
                    # Old windows also replace the basket. Retire the check in the same
                    # transaction that removes the work, before its ID can be selected again.
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS prune_basket_checks_{event}
                        AFTER {event} ON basket WHEN EXISTS (SELECT 1 FROM basket_page_checks) BEGIN
                        DELETE FROM basket_page_checks WHERE NOT EXISTS (
                            SELECT 1 FROM json_each(NEW.payload) AS entry
                            WHERE json_extract(entry.value,'$[1].id') = basket_page_checks.artwork
                            AND json_extract(entry.value,'$[0]') IN (basket_page_checks.scope,'public'));
                        END""")
                    connection.execute(f"""CREATE TRIGGER IF NOT EXISTS recent_identity_{event}
                        AFTER {event} ON recent BEGIN
                            INSERT OR REPLACE INTO recent_identity VALUES (1, lower(hex(randomblob(16))), NULL);
                        END""")
                if version != 2:connection.execute("PRAGMA user_version=2")
                yield connection
        finally:
            connection.close()

    def confirmed_web_scope(self, legacy_scope: str) -> str | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT account_scope FROM web_workspace_owners WHERE legacy_scope=?",
                             (legacy_scope,)).fetchone()
            return row[0] if row else None

    def prior_web_scopes(self, scope: str) -> tuple[str, ...]:
        if scope == "public" or not self.path.is_file():
            return ()
        with self._lock, self._connection() as db:
            return tuple(row[0] for row in db.execute(
                "SELECT legacy_scope FROM web_workspace_owners WHERE account_scope=?", (scope,)))

    def confirm_web_scope(self, legacy_scope: str, scope: str) -> None:
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT account_scope FROM web_workspace_owners WHERE legacy_scope=?",
                                  (legacy_scope,)).fetchone()
            if existing:
                if existing[0] != scope:
                    raise TaskConflictError("网页会话确认了不同账号，请重新登录")
                return

            basket = db.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
            revision, entries = (basket[0], json.loads(basket[1])) if basket else (0, [])
            owned = {row["id"]: row for owner, row in entries if owner == scope}
            updated = [(owner, row) for owner, row in entries if owner != legacy_scope]
            for owner, row in entries:
                if owner != legacy_scope:
                    continue
                current = owned.get(row["id"])
                if current is None:
                    updated.append((scope, row))
                else:
                    pages = set(current["pages"]) | set(row["pages"])
                    count = min(current["item"]["pages"], row["item"]["pages"])
                    current["pages"] = sorted(page for page in pages if page < count)
                    disputed = pages.difference(current["pages"])
                    if disputed:
                        current["item"]["pages"] = count
                        prior = db.execute("SELECT pages FROM basket_page_checks WHERE scope=? AND artwork=?",
                                           (scope, row["id"])).fetchone()
                        if prior:
                            disputed.update(json.loads(prior[0]))
                        db.execute("INSERT OR REPLACE INTO basket_page_checks VALUES(?,?,?,?)",
                                   (scope, row["id"], secrets.token_hex(16), json.dumps(sorted(disputed))))
                    current["archived"] = current.get("archived", True) or row.get("archived", True)
            db.execute("INSERT OR REPLACE INTO basket VALUES(1,?,?)",
                       (revision + 1, json.dumps(updated, ensure_ascii=False)))
            recent = self._read_recent(db)
            if any(owner == legacy_scope for owner, _, _ in recent):
                merged = []
                for owner, item, identity in recent:
                    owner = scope if owner == legacy_scope else owner
                    if not any(old_owner == owner and old == item for old_owner, old, _ in merged):
                        merged.append((owner, item, identity))
                self._write_recent(db, merged)
            db.execute("UPDATE tasks SET scope=? WHERE scope=?", (scope, legacy_scope))
            db.execute("UPDATE recent_writers SET scope=? WHERE scope=?", (scope, legacy_scope))
            db.execute("UPDATE basket_receipts SET scope=? WHERE scope=?", (scope, legacy_scope))
            db.execute("INSERT INTO web_workspace_owners VALUES(?,?)", (legacy_scope, scope))

    @contextmanager
    def _write(self, scope: str):
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM web_workspace_owners WHERE legacy_scope=?", (scope,)).fetchone():
                raise TaskConflictError("账号工作区已更新，请重新读取")
            yield connection

    def load(self, scope: str) -> dict:
        result = {"basket": [], "revision": 0, "tasks": [], "recent": [], "scope": scope}
        with self._lock:
            if not self.path.exists():
                return result
            with self._connection() as connection:
                # Header, chunks and workspace sections belong to one snapshot,
                # including while another process updates this database.
                connection.execute("BEGIN")
                row = connection.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
                if row:
                    result["revision"] = row[0]
                    checks = self._stored_page_checks(connection, scope)
                    result["basket"] = _visible_basket(json.loads(row[1]), scope, checks)
                    if checks:
                        result["basketPageChecks"] = checks
                result["tasks"] = [self._expand_task(connection,json.loads(row[0])) for row in connection.execute("SELECT payload FROM tasks WHERE scope=? ORDER BY rowid", (scope,))]
                result["recent"] = self._visible_recent(self._read_recent(connection), scope)
        return result

    @staticmethod
    def _stored_page_checks(db, scope):
        return {artwork: {"token": token, "pages": json.loads(pages)} for artwork, token, pages in
                db.execute("SELECT artwork,token,pages FROM basket_page_checks WHERE scope=?", (scope,))}

    @staticmethod
    def _read_recent(connection):
        row = connection.execute("""SELECT r.payload, COALESCE(i.generation, 'legacy'), i.ids
                                    FROM recent r LEFT JOIN recent_identity i USING (singleton)
                                    WHERE r.singleton=1""").fetchone()
        if row is None:
            return []
        payload, generation, ids = row
        entries = json.loads(payload)
        if ids is None:
            ids = [hashlib.sha256(f"{generation}:{index}:{payload}".encode("utf-8")).hexdigest()[:32]
                   for index in range(len(entries))]
        else:
            ids = json.loads(ids)
        return [(owner, item, identity) for (owner, item), identity in zip(entries, ids, strict=True)]

    @staticmethod
    def _write_recent(connection, entries):
        connection.execute("INSERT OR REPLACE INTO recent VALUES (1,?)",
                           (json.dumps([(owner, item) for owner, item, _ in entries], ensure_ascii=False),))
        connection.execute("UPDATE recent_identity SET ids=? WHERE singleton=1",
                           (json.dumps([identity for _, _, identity in entries]),))

    @staticmethod
    def _visible_recent(entries, scope):
        return [{**item, "id": identity} for owner, item, identity in entries if owner in {scope, "public"}]

    def change_recent(self, scope, action, *, search=None, identities=None, writer=None, sequence=None):
        if action == "remember":
            value = self._recent_search(search, scope)
        elif (not isinstance(identities, list) or not 1 <= len(identities) <= 8
              or any(not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity)
                     for identity in identities)):
            raise ValueError("invalid recent identities")
        if writer is not None or sequence is not None:
            writer = _request_id(writer)
            _integer(sequence, 1, 2**53 - 1)
            fingerprint = hashlib.sha256(json.dumps([action, search, identities], sort_keys=True).encode()).hexdigest()
        with self._write(scope) as connection:
            entries = self._read_recent(connection)
            if writer is not None:
                previous = connection.execute("SELECT scope,sequence,fingerprint FROM recent_writers WHERE writer=?", (writer,)).fetchone()
                if previous:
                    if previous[0] != scope or (previous[1] == sequence and previous[2] != fingerprint):
                        raise TaskConflictError("最近搜索操作编号冲突")
                    if previous[1] >= sequence:
                        return self._visible_recent(entries, scope)
                connection.execute("INSERT OR REPLACE INTO recent_writers VALUES(?,?,?,?)", (writer, scope, sequence, fingerprint))
            if action == "remember":
                owner = "public" if value["filters"]["mode"] == "safe" else scope
                entries = [(owner, value, secrets.token_hex(16)),
                           *(entry for entry in entries if entry[:2] != (owner, value))][:8]
                self._write_recent(connection, entries)
            else:
                retained = [entry for entry in entries
                            if entry[0] not in {scope, "public"} or entry[2] not in identities]
                if retained != entries:
                    self._write_recent(connection, retained)
                entries = retained
            return self._visible_recent(entries, scope)

    @staticmethod
    def _recent_search(value, scope):
        if not isinstance(value, dict) or set(value) != {"tag", "filters"}:
            raise ValueError("invalid recent search")
        tag, filters = value["tag"], value["filters"]
        if not isinstance(tag, str) or not 1 <= len(tag) <= 500 or not isinstance(filters, dict):
            raise ValueError("invalid recent search")
        if set(filters) != {"mode", "workType", "includeAi", "fuzzy", "startDate", "endDate"}:
            raise ValueError("invalid recent filters")
        if (not isinstance(filters["mode"], str) or filters["mode"] not in CONTENT_MODES or filters["workType"] not in ("all", "illustration", "manga", "ugoira")
                or type(filters["includeAi"]) is not bool or type(filters["fuzzy"]) is not bool
                or any(not isinstance(filters[key], str) or not re.fullmatch(r"(?:\d{4}-\d{2}-\d{2})?", filters[key]) for key in ("startDate", "endDate"))):
            raise ValueError("invalid recent filters")
        if scope == "public" and filters["mode"] != "safe":
            raise ValueError("restricted search needs account")
        return value

    def save_basket(self, rows: list[dict], scope: str, *, revision: int, request_id=None) -> int:
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
            if pages[-1] >= count or raw.get("restriction", "safe") not in CONTENT_RESTRICTIONS.values():
                raise ValueError("invalid basket pages")
            login_only = raw.get("loginOnly", False)
            if type(login_only) is not bool:
                raise ValueError("invalid basket visibility")
            needs_account = login_only or raw.get("restriction") in RESTRICTED_CONTENT
            if needs_account and scope == "public":
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
            if login_only:
                item["loginOnly"] = True
            normalized.append((scope if needs_account else "public", {
                "id": artwork, "item": item, "pages": pages, "context": _context(row.get("context")),
                "resultPage": _integer(row.get("resultPage", 1), 1, 100000), "archived": bool(row.get("archived", True))}))
            total += len(pages)
        if total > 1000:
            raise WorkspaceCapacityError("采集篮容量为 1000 张，请取消部分选择后重试")
        if request_id is not None:
            request_id = _request_id(request_id)
            fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        with self._write(scope) as connection:
            if request_id is not None:
                receipt = connection.execute(
                    "SELECT scope,revision,fingerprint FROM basket_receipts WHERE request_id=?", (request_id,)
                ).fetchone()
                if receipt is not None:
                    if receipt != (scope, revision + 1, fingerprint):
                        raise TaskConflictError("原采集篮提交内容已变更，请重新读取")
                    return receipt[1]
            old = connection.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
            current_revision = old[0] if old else 0
            if current_revision != revision:
                raise BasketRevisionConflict(receipt_expired=request_id is not None and current_revision - revision > BASKET_RECEIPT_REVISIONS)
            normalized = _preserve_basket_ownership(normalized, json.loads(old[1]) if old else [], scope)
            scopes = {"public", *(owner for owner, _row in normalized)}
            if any(sum(len(row["pages"]) for row in _visible_basket(normalized, owner)) > 1000 for owner in scopes):
                raise WorkspaceCapacityError("共享采集篮容量不足，请减少公开作品的选择后重试")
            connection.execute("INSERT OR REPLACE INTO basket VALUES (1,?,?)", (revision + 1, json.dumps(normalized, ensure_ascii=False)))
            obsolete_checks = self._stored_page_checks(connection, scope).keys() - ids
            connection.executemany("DELETE FROM basket_page_checks WHERE scope=? AND artwork=?",
                                   ((scope, artwork) for artwork in obsolete_checks))
            if request_id is not None:
                connection.execute("INSERT INTO basket_receipts VALUES (?,?,?,?)",
                                   (request_id, scope, revision + 1, fingerprint))
                connection.execute("DELETE FROM basket_receipts WHERE revision<=?", (revision + 1 - BASKET_RECEIPT_REVISIONS,))
        return revision + 1

    def confirm_basket_pages(self, scope, artwork, token, count):
        artwork, token = _id(artwork), _request_id(token)
        count = _integer(count, 1, 1000001)
        with self._write(scope) as db:
            saved = db.execute("SELECT revision,payload FROM basket WHERE singleton=1").fetchone()
            if not saved:
                return
            revision, payload = saved
            entries = json.loads(payload)
            checks = self._stored_page_checks(db, scope)
            _visible_basket(entries, scope, checks)
            check = checks.get(artwork)
            if check is None or check["token"] != token:
                return
            pending = {page for page in check["pages"] if page < count}
            updated = []
            shared = None
            for owner, row in entries:
                if owner in (scope, "public") and row["id"] == artwork:
                    pages = {page for page in row["pages"] if page < count}
                    if owner == scope:
                        pages.update(pending)
                        pending.clear()
                    else:
                        shared = row
                    row["item"]["pages"] = count
                    row["pages"] = sorted(pages)
                    if not pages:
                        continue
                updated.append((owner, row))
            if pending and shared:
                # A change in classification must keep the account's old choices private.
                updated.append((scope, {**shared, "pages": sorted(pending)}))
            db.execute("UPDATE basket SET revision=?,payload=? WHERE singleton=1",
                       (revision + 1, json.dumps(updated, ensure_ascii=False)))
            db.execute("DELETE FROM basket_page_checks WHERE scope=? AND artwork=?", (scope, artwork))

    @contextmanager
    def pending_download_ids(self):
        """Hold a stable view of unconfirmed batch IDs across all accounts."""
        with self._lock:
            if not self.path.exists():
                yield []
                return
            with self._connection() as connection:
                # Queue edits wait until receipt pruning has committed.
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute("""
                    SELECT json_extract(chunk.payload, '$.requestId')
                    FROM tasks AS task JOIN task_chunks AS chunk ON chunk.task_id = task.id
                    WHERE chunk.ordinal >= json_extract(task.payload, '$._cursor')
                    UNION
                    SELECT json_extract(chunk.value, '$.requestId')
                    FROM tasks AS task, json_each(task.payload, '$.remainingChunks') AS chunk
                    WHERE json_type(task.payload, '$._cursor') IS NULL
                """)
                yield [row[0] for row in rows]

    def save_task(self, value: dict, scope: str, *, expected: dict | None = None,
                  require_existing: bool = False) -> dict:
        task = normalize_task(value)
        previous = normalize_task(expected) if expected is not None else None
        with self._write(scope) as connection:
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
        with self._write(scope) as connection:
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
        with self._write(scope) as connection:
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
                connection.execute("BEGIN")
                row = connection.execute("SELECT payload FROM tasks WHERE id=? AND scope=?", (task_id, scope)).fetchone()
                if not row:return None
                task=json.loads(row[0])
                if head is not None and "_cursor" in task:
                    task["remainingChunks"]=[json.loads(chunk[0]) for chunk in connection.execute(
                        "SELECT payload FROM task_chunks WHERE task_id=? AND ordinal>=? ORDER BY ordinal LIMIT ?",(task_id,task["_cursor"],head))]
                    task.pop("_cursor");task.pop("_lastRequest",None)
                    return task
                return self._expand_task(connection,task)
