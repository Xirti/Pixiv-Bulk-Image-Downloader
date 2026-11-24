"""Read-only Pixiv account pages and per-account observed-work markers."""
from collections import OrderedDict
from contextlib import ExitStack, closing, contextmanager, nullcontext
from contextvars import ContextVar
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from pixiv_account import AccountError, AppAccount, AppTransport, CredentialVault, ACCOUNT_REQUEST_CHECK
from pixiv_app_config import CLIENT_CONFIG
from pixiv_adapter import (PixivPolicyError, normalize_search_item, is_allowed_pixiv_url,
    build_user_profile_all_url,build_user_profile_works_url,valid_pixiv_id,CONTENT_MODES,matches_content_mode)
from pixiv_app_gallery import normalize_work, artist_profile, continuation
from pixiv_read_trace import trace_artwork_rows
from artist_service import normalize_artist
from request_coordinator import checked_lock
from runtime_identity import named_mutex, process_presence

ACCOUNT_CACHE_ONLY = ContextVar("account_cache_only", default=False)
RELATION_VIEWS = frozenset({"bookmarks", "following"})
_DISPLAY_PENDING_KEY = json.dumps(["web-display-pending"])
_DISPLAY_REVISION_KEY = json.dumps(["display-cache-revision"])
_DISPLAY_SETTINGS_KEY = json.dumps(["web-display-settings"])
_PREVIOUS_WEB_FLAGS_KEY = json.dumps(["web-display-previous-flags"])
_WEB_POLICY_KEY = json.dumps(["web-policy-revision"])
_ACCOUNT_CLEAR_KEY = json.dumps(["account-clear-revision"])
_SNAPSHOT_KINDS = frozenset({"discovery-current", "discovery-feed", "popularity-current", "popularity-index"})


def _snapshot_record(key):
    # The leading space keeps new records outside the old capacity sweep.
    # Their unchanged JSON kind lets either version clear them deliberately.
    return " " + json.dumps(key)


def _snapshot_pattern(kind):
    return _snapshot_record([kind])[:-1] + ",%"


# Existing snapshots use their creation time until a refresh obtains an order.
_PUBLICATION_ORDER_SQL = "COALESCE(json_extract(payload,'$.publicationOrder'),json_extract(payload,'$.created')*1000)"


def _web_display_flags(settings):
    return tuple(settings.get(key) for key in ("ageRestriction", "sensitive", "showAI"))


class HomepageIdentity(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_data(self, value):
        if self.active:
            self.chunks.append(value)

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def user_id(self):
        try:
            props = json.loads("".join(self.chunks))["props"]["pageProps"]
        except (ValueError, KeyError, TypeError) as error:
            raise AccountError("web_identity", "暂时无法确认官网账号，请重试") from error
        if not isinstance(props, dict):
            raise AccountError("web_identity", "暂时无法确认官网账号，请重试")
        if props.get("isLoggedIn") is False:
            raise AccountError("reauth", "网页登录已失效，请重新登录")
        user = props.get("gaUserData")
        if props.get("isLoggedIn") is not True or not isinstance(user, dict):
            raise AccountError("web_identity", "暂时无法确认官网账号，请重试")
        uid = str(user.get("userId", ""))
        if not re.fullmatch(r"[0-9]{1,30}", uid):
            raise AccountError("web_identity", "暂时无法确认官网账号，请重试")
        return uid


def image_url(value):
    return value if isinstance(value, str) and is_allowed_pixiv_url(value, image_only=True) else ""


def source_body(raw):
    body = raw.get("body")
    if not isinstance(body, dict):
        raise AccountError("invalid_data", "Pixiv 账号列表格式异常")
    return body


def source_total(value):
    if type(value) is not int or value < 0:
        raise AccountError("invalid_data", "Pixiv 账号列表数量异常")
    return value


class VisitStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=3)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS author_seen (owner TEXT, author TEXT, seen TEXT, observed TEXT, touched REAL, PRIMARY KEY(owner,author))")
            connection.execute("CREATE TABLE IF NOT EXISTS author_scopes (owner TEXT, scope TEXT, author TEXT, PRIMARY KEY(owner,scope,author))")
        except BaseException:
            connection.close()
            raise
        return connection

    def observe(self, owner, dates, *, scope=None, initial=True):
        markers = {}
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for author, stamp in dates.items():
                row = connection.execute("SELECT seen,observed FROM author_seen WHERE owner=? AND author=?", (owner, author)).fetchone()
                seed_existing = initial and row is not None and not row[0] and not connection.execute(
                    "SELECT 1 FROM author_scopes WHERE owner=? AND author=? LIMIT 1", (owner, author)).fetchone()
                if scope is not None:
                    connection.execute("INSERT OR IGNORE INTO author_scopes VALUES(?,?,?)", (owner,scope,author))
                if row is None:
                    connection.execute("INSERT INTO author_seen VALUES(?,?,?,?,?)", (owner, author, stamp if initial else "", stamp, time.time()))
                    markers[author] = not initial
                else:
                    observed = max(stamp, row[1])
                    # Discovery can observe a work before the first scheduled check.
                    # Seed only the timestamp that the initial check actually saw.
                    seen = stamp if seed_existing else row[0]
                    connection.execute("UPDATE author_seen SET seen=?,observed=?,touched=? WHERE owner=? AND author=?",
                                       (seen, observed, time.time(), owner, author))
                    markers[author] = observed > seen
        return markers

    def status(self, owner, scope=None, *, authors=None):
        source = "author_seen"
        filters, params = ["owner=?"], [owner]
        if scope is not None:
            source += " JOIN author_scopes USING (owner, author)"
            filters.append("scope=?")
            params.append(scope)
        if authors is not None:
            filters.append("author IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(authors))
        with self.lock, closing(self._connect()) as connection:
            rows = connection.execute(f"SELECT author,seen,observed FROM {source} WHERE " + " AND ".join(filters), params)
            return {author: {"isNew": observed > seen, "latestObserved": observed} for author, seen, observed in rows}

    def mark_seen(self, owner, author):
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("UPDATE author_seen SET seen=MAX(seen,observed),touched=? WHERE owner=? AND author=?", (time.time(), owner, author))

    def acknowledge(self, owner, authors, *, verified=()):
        accepted = {}
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for author, stamp in authors.items():
                if author not in verified and not connection.execute(
                        "SELECT 1 FROM author_seen WHERE owner=? AND author=? AND observed>=?", (owner, author, stamp)).fetchone():
                    continue
                connection.execute("INSERT INTO author_seen(owner,author,seen,observed,touched) VALUES(?,?,?,'',?) "
                    "ON CONFLICT(owner,author) DO UPDATE SET seen=MAX(seen,excluded.seen),touched=excluded.touched",
                    (owner, author, stamp, time.time()))
                accepted[author] = stamp
        return accepted

    def clear(self, owner):
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM author_seen WHERE owner=?", (owner,))
            connection.execute("DELETE FROM author_scopes WHERE owner=?", (owner,))


@dataclass(frozen=True)
class DiscoveryPage:
    feed: dict
    items: list


@dataclass(frozen=True)
class DisplayPublication:
    order: int | float
    batch: str
    usable: bool = True


@dataclass(frozen=True)
class AccountContinuation:
    path: str
    params: list
    position: int
    current: str
    scan: str
    following: str | None


@dataclass(frozen=True)
class AppRelationPage:
    data: dict
    cursor: AccountContinuation


class AccountCache:
    """Account metadata survives navigation/restarts; image capabilities are added only at response time."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._policy_lock = threading.Lock()
        self._policy_connection = None
        self._policy_observed = None
        self._policy_stamp = {}
        path_key = os.path.normcase(str(self.path.resolve()))
        self._mutex_prefix = "Local\\MOKU.Account." + hashlib.sha256(path_key.encode()).hexdigest()[:32]

    def owner_mutex(self, owner):
        return named_mutex(f"{self._mutex_prefix}.{owner}", timeout_seconds=5)

    def relation_mutex(self, owner, kind, target):
        # App and Web can change the same Pixiv relationship.
        return named_mutex(f"{self._mutex_prefix}.{owner}.{kind}.{target}", timeout_seconds=0)

    @contextmanager
    def web_settings_mutex(self, owner):
        with ExitStack() as locks:
            try:
                locks.enter_context(named_mutex(f"{self._mutex_prefix}.{owner}.web-settings", timeout_seconds=0))
            except TimeoutError as error:
                raise AccountError("settings_pending", "另一个窗口正在核对浏览设置，请稍后重试。") from error
            yield

    def _snapshot_key(self, owner, method, kind, identity):
        return f"{self._mutex_prefix}.{owner}.{kind}.{method}.{identity}"

    @contextmanager
    def pin_snapshot(self, owner, method, kind, identity):
        key = self._snapshot_key(owner, method, kind, identity)
        with ExitStack() as presence:
            with named_mutex(key + ".gate", timeout_seconds=5):
                presence.enter_context(process_presence(key))
            yield

    def _snapshot_can_be_retired(self, held, owner, method, kind, identity):
        name = self._snapshot_key(owner, method, kind, identity)
        try:
            # Keep the gate until deletion commits. A reader cannot pin it after this check.
            held.enter_context(named_mutex(name + ".gate", timeout_seconds=0))
        except TimeoutError:
            return False
        with process_presence(name) as active:
            return not active

    def _connect(self, *, shared_reader=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=.25 if shared_reader else 5,
            check_same_thread=not shared_reader)
        try:
            version, display_storage = connection.execute(
                "SELECT user_version, EXISTS(SELECT 1 FROM sqlite_master WHERE name='display_publication_order') "
                "FROM pragma_user_version").fetchone()
            if version not in (0, 1):
                raise ValueError("Unsupported account cache schema")
            if version == 0:
                connection.executescript("""BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS cached (owner TEXT, method TEXT, key TEXT, payload TEXT, PRIMARY KEY(owner,method,key));
                    CREATE TABLE IF NOT EXISTS cursor_visits (owner TEXT, method TEXT, scan TEXT, path TEXT, position INTEGER,
                        PRIMARY KEY(owner,method,scan,path));
                    PRAGMA user_version=1;
                    COMMIT;""")
            if not display_storage:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if not self._display_storage_initialized(connection):
                        self._migrate_display_storage(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _display_storage_initialized(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE name='display_publication_order'").fetchone() is not None

    @classmethod
    def _migrate_display_storage(cls, db):
        # Import once, under a writer reservation. Legacy writers keep their
        # original keys and cannot publish into the new authority afterwards.
        db.execute("""CREATE TABLE display_publication_order (
            owner TEXT, method TEXT, value INTEGER NOT NULL, PRIMARY KEY(owner,method))""")
        db.execute("""INSERT INTO display_publication_order
            SELECT owner,method,CAST(payload AS INTEGER) FROM cached WHERE key='["display-publication-order"]'""")
        db.execute("""DELETE FROM cached WHERE key='["display-publication-order"]'""")
        db.execute("""CREATE TABLE IF NOT EXISTS discovery_pages (
            owner TEXT, method TEXT, header TEXT, checkpoint INTEGER, payload TEXT NOT NULL,
            PRIMARY KEY(owner,method,header,checkpoint))""")
        db.execute("""CREATE TABLE IF NOT EXISTS discovery_offsets (
            owner TEXT, method TEXT, header TEXT, start_offset INTEGER, checkpoint INTEGER,
            PRIMARY KEY(owner,method,header,start_offset))""")
        # Legacy clear and display-setting operations also delete these headers.
        db.execute("""CREATE TRIGGER IF NOT EXISTS delete_discovery_pages AFTER DELETE ON cached
            WHEN json_extract(OLD.key,'$[0]')='discovery-feed' AND json_extract(OLD.key,'$[2]')='pages'
            BEGIN DELETE FROM discovery_pages WHERE owner=OLD.owner AND method=OLD.method AND header=OLD.key; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS delete_discovery_offsets AFTER DELETE ON cached
            WHEN json_extract(OLD.key,'$[0]')='discovery-feed' AND json_extract(OLD.key,'$[2]')='pages'
            BEGIN DELETE FROM discovery_offsets WHERE owner=OLD.owner AND method=OLD.method AND header=OLD.key; END""")
        db.execute("""CREATE TABLE IF NOT EXISTS popularity_progress (
            owner TEXT, method TEXT, key TEXT, base_rowid INTEGER, checkpoint INTEGER, payload TEXT NOT NULL,
            PRIMARY KEY(owner,method,key,base_rowid,checkpoint)) WITHOUT ROWID""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS delete_popularity_progress AFTER DELETE ON cached
            WHEN json_extract(OLD.key,'$[0]')='popularity-index'
            BEGIN DELETE FROM popularity_progress WHERE owner=OLD.owner AND method=OLD.method AND key=OLD.key; END""")

        db.execute("""INSERT INTO cached
            SELECT owner,method,' ' || key,payload FROM cached
            WHERE key LIKE '["popularity-index",%' OR key LIKE '["popularity-current",%'
               OR (key LIKE '["discovery-feed",%' AND json_extract(key,'$[2]')='pages')
            ORDER BY rowid""")
        db.execute("""INSERT INTO discovery_pages
            SELECT owner,method,' ' || header,checkpoint,payload FROM discovery_pages WHERE header LIKE '["discovery-feed",%'""")
        db.execute("""INSERT INTO discovery_offsets
            SELECT owner,method,' ' || header,start_offset,checkpoint FROM discovery_offsets WHERE header LIKE '["discovery-feed",%'""")
        db.execute("""INSERT INTO popularity_progress
            SELECT progress.owner,progress.method,snapshot.key,snapshot.rowid,progress.checkpoint,progress.payload
            FROM popularity_progress progress JOIN cached snapshot ON snapshot.owner=progress.owner
                AND snapshot.method=progress.method AND snapshot.key=' ' || progress.key
            JOIN cached legacy ON legacy.rowid=progress.base_rowid AND legacy.key=progress.key
            WHERE progress.key LIKE '["popularity-index",%'""")

        for owner, method, key, payload in db.execute(
                "SELECT owner,method,key,payload FROM cached WHERE key LIKE ? AND json_extract(key,'$[2]') IS NULL ORDER BY rowid",
                ('["discovery-feed",%',)):
            _, batch = json.loads(key)
            try:
                feed = json.loads(payload)
            except json.JSONDecodeError as error:
                logging.getLogger("moku.http").warning("Invalid legacy discovery snapshot for %s: %s", batch, error)
                continue
            if (not isinstance(feed, dict) or not isinstance(feed.get("items"), list)
                    or not isinstance(feed.get("seen"), list) or not isinstance(feed.get("visitedPaths", {}), dict)):
                continue
            chunk = feed.pop("items"), feed.pop("seen"), feed.pop("visitedPaths", {})
            header = _snapshot_record(["discovery-feed", batch, "pages"])
            feed.update(_checkpoint=1, _offsetCheckpoint=1, _itemCount=len(chunk[0]))
            if not cls._valid_discovery_header(feed, batch):
                continue
            if db.execute("INSERT OR IGNORE INTO cached VALUES(?,?,?,?)",
                          (owner, method, header, json.dumps(feed, ensure_ascii=False))).rowcount:
                db.execute("INSERT INTO discovery_pages VALUES(?,?,?,?,?)",
                           (owner, method, header, 1, json.dumps(chunk, ensure_ascii=False)))
                if chunk[0]:
                    db.execute("INSERT INTO discovery_offsets VALUES(?,?,?,?,?)", (owner, method, header, 0, 1))

        for owner, method, key, payload in db.execute(
                "SELECT owner,method,key,payload FROM cached WHERE key LIKE ? "
                "AND CASE WHEN json_valid(payload) THEN json_type(payload) END='text' ORDER BY rowid",
                ('["discovery-current",%',)).fetchall():
            pointer = json.loads(key)[:5]
            batch = json.loads(payload)
            incoming = cls._display_snapshot(db, owner, method, "discovery-feed", batch, "pages")
            if incoming is None:
                continue
            if cls._can_publish_display_snapshot(db, owner, method, pointer, (incoming.usable, incoming.order)):
                cls._save_rows(db, owner, method, [(pointer, batch)])

    def web_policy_stamps(self, owner):
        """Read policy for one UID, or for all known UIDs while a session is unresolved."""
        with self._policy_lock:
            if self._policy_connection is None:
                self._policy_connection = self._connect(shared_reader=True)
            db = self._policy_connection
            current = owner, db.execute("PRAGMA data_version").fetchone()[0]
            if current != self._policy_observed:
                query = "SELECT owner,key,payload FROM cached WHERE method='web' AND key IN (?,?,?)"
                keys = (_WEB_POLICY_KEY, _DISPLAY_PENDING_KEY, _DISPLAY_SETTINGS_KEY)
                rows = db.execute(query if owner is None else query + " AND owner=?",
                                  keys if owner is None else (*keys, owner))
                by_owner = {}
                for uid, key, payload in rows:
                    by_owner.setdefault(uid, {})[key] = payload
                stamps = {}
                for uid, values in by_owner.items():
                    settings = json.loads(values[_DISPLAY_SETTINGS_KEY]) if _DISPLAY_SETTINGS_KEY in values else None
                    flags = _web_display_flags(settings) if settings else None
                    stamps[uid] = values.get(_WEB_POLICY_KEY), values.get(_DISPLAY_PENDING_KEY), flags
                self._policy_stamp = stamps
                self._policy_observed = current
            return self._policy_stamp

    def close_policy_reader(self):
        with self._policy_lock:
            if self._policy_connection is not None:
                self._policy_connection.close()
                self._policy_connection = None
                self._policy_observed = None

    def read(self, owner, method, key, *, clear_revision=None):
        with self.lock, closing(self._connect()) as db:
            if self._is_display_key(key):
                self._begin_display_cache(db, owner, method)
            elif key == ["web-display-settings"]:
                db.execute("BEGIN")
                if self._display_update_pending(db, owner, method):
                    return None
            elif clear_revision is not None:
                db.execute("BEGIN")
            self._check_clear_revision(db, owner, clear_revision)
            if method == "web" and key[0] == "data" and key[1] in {"bookmarks", "following"}:
                if self._web_relation_rebase(db, owner, self.web_relation_group(key)) == "partial":
                    if key[4]:
                        raise AccountError("page_rebased", "账号列表发生变化，请从第一页继续")
                    return None
            row = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
                             (owner, method, _snapshot_record(key) if key[0] in _SNAPSHOT_KINDS else json.dumps(key))).fetchone()
            return json.loads(row[0]) if row else None

    def bind_default_snapshot(self, owner, method, pointer, held, *, revision):
        """Select and pin under the same SQLite writer reservation used by retirement."""
        with self.lock, closing(self._connect()) as db:
            self._begin_display_cache(db, owner, method, write=True, revision=revision)
            current = self._current_display_snapshot(db, owner, method, pointer)
            if current is None:
                return ""
            kind = "discovery" if pointer[0] == "discovery-current" else "popularity"
            held.enter_context(self.pin_snapshot(owner, method, kind, current.batch))
            return current.batch

    @classmethod
    def _decode_discovery_header(cls, payload, batch):
        try:
            feed = json.loads(payload)
        except json.JSONDecodeError as error:
            logging.getLogger("moku.http").warning("Invalid discovery snapshot header for %s: %s", batch, error)
            return None
        return feed if cls._valid_discovery_header(feed, batch) else None

    @staticmethod
    def _valid_discovery_header(feed, batch):
        if not isinstance(feed, dict) or feed.get("batch") != batch:
            return False
        fields = {"kind": str, "scope": str, "mode": str, "rankMode": str, "edition": str,
                  "window": int, "unavailable": int, "sourceCount": int, "stagnant": int,
                  "paths": dict, "pending": dict, "_checkpoint": int}
        if any(type(feed.get(name)) is not field_type for name, field_type in fields.items()) or feed["_checkpoint"] < 1:
            return False
        for value in (feed.get("created"), feed.get("publicationOrder", feed.get("created"))):
            if type(value) not in (int, float) or isinstance(value, float) and not math.isfinite(value):
                return False
        if "_offsetCheckpoint" in feed and (type(feed["_offsetCheckpoint"]) is not int or
                type(feed.get("_itemCount")) is not int or feed["_itemCount"] < 0):
            return False
        return True

    @classmethod
    def _display_snapshot(cls, db, owner, method, kind, identity, *suffix):
        key = _snapshot_record([kind, identity, *suffix])
        if kind == "discovery-feed":
            row = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
                (owner, method, key)).fetchone()
            feed = cls._decode_discovery_header(row[0], identity) if row else None
            if feed is None:
                return None
            return DisplayPublication(feed.get("publicationOrder", feed["created"] * 1000), identity, cls._discovery_usable(feed))
        row = db.execute(f"SELECT {_PUBLICATION_ORDER_SQL},json_extract(payload,'$.ready') "
            "FROM cached WHERE owner=? AND method=? AND key=?", (owner, method, key)).fetchone()
        return DisplayPublication(row[0], identity, bool(row[1])) if row is not None else None

    @staticmethod
    def _discovery_usable(header):
        return bool(header.get("_itemCount", 1)) or not (any(header["paths"].values()) or any(header["pending"].values()))

    @classmethod
    def _current_display_snapshot(cls, db, owner, method, pointer):
        row = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
            (owner, method, _snapshot_record(pointer))).fetchone()
        if row is None:
            return None
        identity = json.loads(row[0])
        if pointer[0] == "discovery-current":
            return cls._display_snapshot(db, owner, method, "discovery-feed", identity, "pages")
        return cls._display_snapshot(db, owner, method, "popularity-index", identity)

    @classmethod
    def _can_publish_display_snapshot(cls, db, owner, method, pointer, priority):
        current = cls._current_display_snapshot(db, owner, method, pointer)
        return current is None or priority > (current.usable, current.order)

    @classmethod
    def _begin_display_cache(cls, db, owner, method, *, write=False, revision=None, exclusive=False):
        db.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN IMMEDIATE" if write else "BEGIN")
        cls._require_display_ready(db, owner, method)
        if revision is not None and cls._display_revision(db, owner, method) != revision:
            raise AccountError("cache_cleared", "浏览缓存已更新，请重新打开页面")

    @classmethod
    def _display_revision(cls, db, owner, method):
        # Pre-revision releases also replace the settings row on confirmation.
        values = dict(db.execute("SELECT key,CASE WHEN key=? THEN rowid ELSE payload END"
            " FROM cached WHERE owner=? AND method=? AND key IN (?,?)",
            (_DISPLAY_SETTINGS_KEY, owner, method, _DISPLAY_REVISION_KEY, _DISPLAY_SETTINGS_KEY)))
        return json.dumps([cls._read_clear_revision(db, owner), values.get(_DISPLAY_REVISION_KEY),
            values.get(_DISPLAY_SETTINGS_KEY)])

    def display_revision(self, owner, method):
        with self.lock, closing(self._connect()) as db, db:
            self._begin_account_read(db, owner, anchor_web_settings=method == "web")
            self._require_display_ready(db, owner, method)
            return self._display_revision(db, owner, method)

    def reserve_display_publication(self, owner, method, pointer, *, revision):
        """Assign fresh reads their order before any upstream work starts."""
        with self.lock, closing(self._connect()) as db, db:
            self._begin_display_cache(db, owner, method, write=True, revision=revision)
            last = db.execute("SELECT value FROM display_publication_order WHERE owner=? AND method=?",
                (owner, method)).fetchone()
            current = self._current_display_snapshot(db, owner, method, pointer)
            order = int(max(time.time() * 1000, last[0] if last else 0, current.order if current else 0)) + 1
            db.execute("INSERT OR REPLACE INTO display_publication_order VALUES(?,?,?)", (owner, method, order))
            return order

    def read_discovery(self, owner, method, batch, *, page=None, warm=None, revision=None):
        key = ["discovery-feed", batch]
        with self.lock, closing(self._connect()) as db:
            self._begin_display_cache(db, owner, method, revision=revision)
            header = _snapshot_record([*key, "pages"])
            row = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
                (owner, method, header)).fetchone()
            if row is None:
                return None
            feed = self._decode_discovery_header(row[0], batch)
            if feed is None:
                return None
            if warm is not None and (warm.get("_checkpoint"), warm.get("_offsetCheckpoint")) == (
                    feed["_checkpoint"], feed.get("_offsetCheckpoint")):
                return warm
            if page is not None and feed.get("_offsetCheckpoint") == feed["_checkpoint"]:
                offset, limit, cached = page
                target = offset + limit
                if cached or target <= feed["_itemCount"] or not (any(feed["paths"].values()) or any(feed["pending"].values())):
                    items = []
                    if offset < feed["_itemCount"]:
                        # The last chunk beginning at or before offset can overlap the page.
                        for start, payload in db.execute("""SELECT r.start_offset, p.payload
                            FROM discovery_offsets r JOIN discovery_pages p USING(owner, method, header, checkpoint)
                            WHERE r.owner=? AND r.method=? AND r.header=? AND r.start_offset<?
                            AND r.start_offset >= (SELECT MAX(start_offset) FROM discovery_offsets
                                WHERE owner=? AND method=? AND header=? AND start_offset<=?)
                            ORDER BY r.start_offset""",
                            (owner, method, header, target, owner, method, header, offset)):
                            chunk, _, _ = json.loads(payload)
                            items.extend(chunk[max(0, offset - start):target - start])
                    return DiscoveryPage(feed, items)
            feed.update(items=[], seen=set(), visitedPaths={})
            for (payload,) in db.execute("SELECT payload FROM discovery_pages WHERE owner=? AND method=? AND header=? ORDER BY checkpoint",
                    (owner, method, header)):
                items, seen, visited = json.loads(payload)
                feed["items"].extend(items)
                feed["seen"].update(seen)
                for stream, paths in visited.items():
                    feed["visitedPaths"].setdefault(stream, []).extend(paths)
            return feed

    def save_discovery(self, owner, method, feed, previous, *, revision, current=None, before_commit=None):
        key = ["discovery-feed", feed["batch"]]
        header = _snapshot_record([*key, "pages"])
        checkpoint = previous["_checkpoint"] if previous else 0
        prefix = previous
        visited = {stream: paths[len(prefix.get("visitedPaths", {}).get(stream, ())) if prefix else 0:]
                   for stream, paths in feed.get("visitedPaths", {}).items()}
        start = len(prefix["items"]) if prefix else 0
        page = json.dumps([feed["items"][start:],
                           sorted(feed["seen"].difference(prefix["seen"]) if prefix else feed["seen"]),
                           visited], ensure_ascii=False)
        metadata = {name: value for name, value in feed.items() if name not in {"items", "seen", "visitedPaths"}}
        metadata["_checkpoint"] = checkpoint + 1
        metadata["_offsetCheckpoint"] = checkpoint + 1
        metadata["_itemCount"] = len(feed["items"])
        rows = [(owner, method, header, json.dumps(metadata, ensure_ascii=False))]
        with self.lock, closing(self._connect()) as db, db:
            # Acquire the commit lock before checking the request at publication.
            self._begin_display_cache(db, owner, method, write=True, revision=revision, exclusive=before_commit is not None)
            if current is not None:
                incoming = (self._discovery_usable(metadata), feed.get("publicationOrder", feed["created"] * 1000))
                if self._can_publish_display_snapshot(db, owner, method, current, incoming):
                    rows.append((owner, method, _snapshot_record(current), json.dumps(feed["batch"])))
            stored = db.execute("SELECT json_extract(payload,'$._checkpoint') FROM cached WHERE owner=? AND method=? AND key=?",
                (owner, method, header)).fetchone()
            if (stored[0] if stored else 0) != checkpoint:
                raise AccountError("cache_changed", "发现批次已改变，请重试")
            if previous is not None and previous.get("_offsetCheckpoint") != checkpoint:
                # Earlier paged snapshots can be indexed without rewriting their payloads.
                db.execute("DELETE FROM discovery_offsets WHERE owner=? AND method=? AND header=?",
                    (owner, method, header))
                db.execute("""INSERT INTO discovery_offsets
                    SELECT owner,method,header,SUM(size) OVER (ORDER BY checkpoint)-size,checkpoint
                    FROM (SELECT owner,method,header,checkpoint,json_array_length(payload,'$[0]') AS size
                        FROM discovery_pages WHERE owner=? AND method=? AND header=?) WHERE size>0""",
                    (owner, method, header))
            db.execute("INSERT INTO discovery_pages VALUES(?,?,?,?,?)",
                (owner, method, header, checkpoint + 1, page))
            if start < metadata["_itemCount"]:
                db.execute("INSERT INTO discovery_offsets VALUES(?,?,?,?,?)",
                    (owner, method, header, start, checkpoint + 1))
            # UPSERT avoids REPLACE's delete trigger when recursive_triggers is enabled.
            db.executemany("INSERT INTO cached VALUES(?,?,?,?) ON CONFLICT(owner,method,key) DO UPDATE SET payload=excluded.payload",
                rows)
            if before_commit is not None:
                before_commit()
        return {"_checkpoint": checkpoint + 1, "_offsetCheckpoint": checkpoint + 1,
                "_itemCount": metadata["_itemCount"]}

    def save(self, owner, method, key, value, *, revision=None):
        self.save_many(owner,method,[(key,value)], revision=revision)

    @staticmethod
    def _save_rows(db, owner, method, pages):
        db.executemany("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
            [(owner, method, _snapshot_record(key) if key[0] in _SNAPSHOT_KINDS else json.dumps(key),
              json.dumps(value, ensure_ascii=False)) for key, value in pages])

    def save_many(self, owner, method, pages, *, revision=None):
        pages = list(pages)
        with self.lock, closing(self._connect()) as db, db:
            if any(self._is_display_key(key) for key, _ in pages):
                self._begin_display_cache(db, owner, method, write=True, revision=revision)
            self._save_rows(db,owner,method,pages)

    def read_popularity(self, owner, method, key, *, revision, warm=None):
        with self.lock, closing(self._connect()) as db:
            self._begin_display_cache(db, owner, method, revision=revision)
            params = owner, method, _snapshot_record(key)
            row = db.execute("SELECT rowid FROM cached WHERE owner=? AND method=? AND key=?", params).fetchone()
            if row is None:
                return None, None
            rowid = row[0]
            progress = self._popularity_progress(db, *params, rowid)
            version = (rowid, progress)
            if warm is not None and warm[0] == version:
                return warm
            payload = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?", params).fetchone()[0]
            job = json.loads(payload)
            if progress:
                missing = set(job.get("missingOffsets", ()))
                for (payload,) in db.execute("SELECT payload FROM popularity_progress WHERE owner=? AND method=? AND key=? AND base_rowid=? ORDER BY checkpoint",
                        (*params, rowid)):
                    job["enrichOffset"], job["retryOffset"], changes = json.loads(payload)
                    for index, count in changes:
                        if count is None:
                            missing.add(index)
                        else:
                            job["items"][index]["bookmarks"] = count
                            missing.discard(index)
                job["missingOffsets"] = sorted(missing)
            return version, job

    @staticmethod
    def _popularity_progress(db, owner, method, key, rowid):
        return db.execute("SELECT COALESCE(MAX(checkpoint),0) FROM popularity_progress WHERE owner=? AND method=? AND key=? AND base_rowid=?",
            (owner, method, key, rowid)).fetchone()[0]

    def save_popularity(self, owner, method, snapshot, previous, *, revision, current=None, before_commit=None, changes=None):
        index = snapshot["index"]
        key = _snapshot_record(["popularity-index", index])
        params = owner, method, key
        payload = json.dumps(snapshot, ensure_ascii=False) if changes is None else json.dumps(
            [snapshot["enrichOffset"], snapshot["retryOffset"], changes])
        with self.lock, closing(self._connect()) as db, db:
            self._begin_display_cache(db, owner, method, write=True, revision=revision, exclusive=before_commit is not None)
            row = db.execute("SELECT rowid FROM cached WHERE owner=? AND method=? AND key=?", params).fetchone()
            version = (row[0], self._popularity_progress(db, *params, row[0])) if row else None
            if version != previous:
                raise AccountError("cache_changed", "排序快照已更新，请重试")
            if changes is not None:
                rowid, progress = version
                db.execute("INSERT INTO popularity_progress VALUES(?,?,?,?,?,?)", (*params, rowid, progress + 1, payload))
                version = (rowid, progress + 1)
            else:
                if current is not None:
                    incoming = (snapshot["ready"], snapshot.get("publicationOrder", snapshot["created"] * 1000))
                    if self._can_publish_display_snapshot(db, owner, method, current, incoming):
                        self._save_rows(db, owner, method, [(current, index)])
                if version and version[1]:
                    db.execute("DELETE FROM popularity_progress WHERE owner=? AND method=? AND key=?", params)
                row = db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)", (*params, payload))
                version = (row.lastrowid, 0)
            if before_commit is not None:
                before_commit()
            return version

    def read_cursor(self, owner, method, key):
        with self.lock, closing(self._connect()) as db:
            checkpoint = self._read_cursor(db, owner, method, key)
        if checkpoint is None:
            return None
        path, scan = checkpoint
        return path, scan or uuid.uuid4().hex

    @staticmethod
    def _read_cursor(db, owner, method, key):
        raw_key, state_key = json.dumps(key), json.dumps(["cursor-state", *key[1:]])
        values = {name: json.loads(payload) for name,payload in db.execute(
            "SELECT key,payload FROM cached WHERE owner=? AND method=? AND key IN (?,?)",
            (owner,method,raw_key,state_key))}
        path = values.get(raw_key)
        if path is None:
            return None
        if not isinstance(path,str):
            raise AccountError("cursor","账号分页缓存格式异常，请刷新")
        state = values.get(state_key)
        if state is not None and (not isinstance(state,dict) or not isinstance(state.get("path"),str)
                or not isinstance(state.get("scan"),str) or not re.fullmatch(r"[a-f0-9]{32}",state["scan"])):
            raise AccountError("cursor","账号分页检查点格式异常，请刷新")
        return path,state["scan"] if state is not None and state["path"]==path else None

    def page_version(self, owner, method, key, *, clear_revision=None):
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            self._check_clear_revision(db, owner, clear_revision)
            if (method == "web" and key[1] in {"bookmarks", "following"} and key[4]
                    and self._web_relation_rebase(db, owner, self.web_relation_group(key))):
                raise AccountError("page_rebased", "账号列表发生变化，请从第一页继续")
            return self._page_version(db, owner, method, key)

    @staticmethod
    def web_relation_group(key):
        return ("web-relation-rebase", key[1], key[3], *key[5:])

    @staticmethod
    def _web_relation_rebase(db, owner, group):
        row = db.execute("SELECT payload FROM cached WHERE owner=? AND method='web' AND key=?",
                         (owner, json.dumps(group))).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _page_version(db, owner, method, key):
        epoch_key = json.dumps(["relation-cache-epoch", key[1]])
        page_key = json.dumps(key)
        rows = {encoded: (rowid, epoch) for encoded, rowid, epoch in db.execute(
            "SELECT key,rowid,CASE WHEN key=? THEN payload END FROM cached WHERE owner=? AND method=? AND key IN (?,?)",
            (epoch_key, owner, method, epoch_key, page_key))}
        return rows.get(epoch_key, (None, None))[1], rows.get(page_key, (None, None))[0]

    @staticmethod
    def _advance_page_epochs(db, owner, method, views):
        AccountCache._save_rows(db, owner, method, [
            (["relation-cache-epoch", view], uuid.uuid4().hex) for view in views])

    def save_account_page(self, owner, method, key, result, expected, cursor, *, clear_revision, display_revision):
        with self.lock, closing(self._connect()) as db, db:
            if self._is_display_key(key):
                self._begin_display_cache(db, owner, method, write=True, revision=display_revision)
            else:
                db.execute("BEGIN IMMEDIATE")
            self._check_clear_revision(db, owner, clear_revision)
            if self._page_version(db, owner, method, key) != expected:
                raise AccountError("cache_changed", "账号列表已更新，请重新打开页面")
            if method == "web" and key[1] in {"bookmarks", "following"} and not key[4]:
                group = self.web_relation_group(key)
                if self._web_relation_rebase(db, owner, group):
                    db.execute("DELETE FROM cached WHERE owner=? AND method='web' AND "
                               "(key=? OR json_remove(key,'$[4]')=json(?))",
                               (owner, json.dumps(group), json.dumps([*key[:4], *key[5:]])))
                    self._advance_page_epochs(db, owner, method, (key[1],))
            self._save_rows(db, owner, method, [(key, result)])
            if cursor is not None:
                self._save_cursor(db, owner, method, ["cursor", cursor.path, cursor.params, result["nextOffset"]],
                    cursor.current, cursor.position, cursor.scan, cursor.following)

    def _save_cursor(self, db, owner, method, key, current, position, scan, following):
        if position:
            parent = self._read_cursor(db, owner, method, [*key[:-1], position])
            if parent is None:
                raise AccountError("page_retired", "账号分页已更新，分页记录已清理，请返回第一页")
            if parent not in ((current, scan), (current, None)):
                raise AccountError("cache_changed", "账号分页已更新，请返回第一页")
        if following is None:
            return
        if db.execute("SELECT 1 FROM cursor_visits WHERE owner=? AND method=? AND scan=? AND path=? AND position<=?",
                (owner,method,scan,following,position)).fetchone():
            raise AccountError("cursor","账号列表续页出现循环，请刷新后重试")
        new_scan = db.execute("SELECT 1 FROM cursor_visits WHERE owner=? AND method=? AND scan=? LIMIT 1",
            (owner,method,scan)).fetchone() is None
        db.execute("INSERT INTO cursor_visits VALUES(?,?,?,?,?) ON CONFLICT(owner,method,scan,path) "
            "DO UPDATE SET position=MIN(position,excluded.position)",(owner,method,scan,current,position))
        self._save_rows(db,owner,method,[(key,following),(["cursor-state",*key[1:]],{"path":following,"scan":scan})])
        if new_scan:
            obsolete = db.execute("SELECT scan FROM cursor_visits WHERE owner=? AND method=? GROUP BY scan "
                "ORDER BY MAX(rowid) DESC LIMIT -1 OFFSET 32",(owner,method)).fetchall()
            db.executemany("DELETE FROM cursor_visits WHERE owner=? AND method=? AND scan=?",
                [(owner,method,identity) for (identity,) in obsolete])

    @staticmethod
    def retire_display_snapshots(retain, owner, method, **options):
        # Publication has committed. This maintenance can be retried on the next publication.
        try:
            retain(owner, method, **options)
        except (sqlite3.Error, OSError) as exc:
            logging.getLogger("moku.http").warning("Display snapshot cleanup deferred (%s): %s", method, exc)

    def retain_discovery(self, owner, method, limit=32):
        with ExitStack() as held, self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            feeds = db.execute("SELECT key,json_extract(key,'$[1]') FROM cached WHERE owner=? AND method=? AND key LIKE ? "
                "ORDER BY rowid DESC LIMIT -1 OFFSET ?", (owner, method, _snapshot_pattern("discovery-feed"), limit)).fetchall()
            if not feeds:
                return
            protected = {batch for (batch,) in db.execute(
                "SELECT json_extract(payload,'$') FROM cached WHERE owner=? AND method=? AND key LIKE ?",
                (owner, method, _snapshot_pattern("discovery-current")))}
            if all(batch in protected for _, batch in feeds):
                return
            protected.update(batch for (batch,) in db.execute(
                "SELECT DISTINCT json_extract(payload,'$.batch') FROM cached WHERE owner=? AND method=? AND key LIKE ?"
                " AND json_extract(payload,'$.kind')='ranking' AND json_extract(payload,'$.collected')=0",
                (owner, method, _snapshot_pattern("popularity-index"))))
            removed = [(owner, method, key) for key, batch in feeds if batch not in protected
                and self._snapshot_can_be_retired(held, owner, method, "discovery", batch)]
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND key=?", removed)

    def retain_popularity(self, owner, method, limit=24, *, current=None):
        with ExitStack() as held, self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT key FROM cached WHERE owner=? AND method=? AND key LIKE ? "
                "ORDER BY rowid DESC LIMIT -1 OFFSET ?", (owner, method, _snapshot_pattern("popularity-index"), limit)).fetchall()
            protected = None
            if rows and current is not None:
                pointer = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
                    (owner, method, _snapshot_record(current))).fetchone()
                if pointer is not None:
                    protected = json.loads(pointer[0])
            paused = 0
            removed = []
            for (key,) in rows:
                _, index = json.loads(key)
                if index == protected:
                    continue
                if paused < 8 and db.execute(
                        "SELECT json_extract(payload,'$.ready') FROM cached WHERE owner=? AND method=? AND key=?",
                        (owner, method, key)).fetchone()[0] == 0:
                    paused += 1
                    continue
                if self._snapshot_can_be_retired(held, owner, method, "popularity", index):
                    removed.append((owner, method, key))
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND key=?",
                removed)
            db.execute("DELETE FROM cached WHERE owner=? AND method=? AND key LIKE ? AND json_extract(payload,'$') NOT IN "
                "(SELECT json_extract(key,'$[1]') FROM cached WHERE owner=? AND method=? AND key LIKE ?)",
                (owner,method,_snapshot_pattern("popularity-current"),owner,method,_snapshot_pattern("popularity-index")))

    def clear(self, owner):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # Drop the refreshable form, but keep the policy under which newly
            # rebuilt pages will be read. Repeated clears must keep that baseline.
            row = db.execute("SELECT payload FROM cached WHERE owner=? AND method='web' AND key=?",
                             (owner, _DISPLAY_SETTINGS_KEY)).fetchone()
            previous = json.loads(row[0]) if row else None
            if previous is not None:
                db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
                           (owner, "web", _PREVIOUS_WEB_FLAGS_KEY, json.dumps(_web_display_flags(previous))))
            db.execute("DELETE FROM cached WHERE owner=? AND key NOT IN (?,?,?)",
                (owner, _DISPLAY_PENDING_KEY, _WEB_POLICY_KEY, _PREVIOUS_WEB_FLAGS_KEY))
            db.execute("DELETE FROM cursor_visits WHERE owner=?",(owner,))
            db.execute("INSERT INTO cached VALUES(?,?,?,?)", (owner, "account", _ACCOUNT_CLEAR_KEY, json.dumps(uuid.uuid4().hex)))
            for method in ("app", "web"):
                self._advance_page_epochs(db, owner, method, sorted(RELATION_VIEWS))
                self._advance_display_revision(db, owner, method)

    def clear_revision(self, owner):
        with self.lock, closing(self._connect()) as db, db:
            return self._begin_account_read(db, owner)

    @classmethod
    def _begin_account_read(cls, db, owner, *, anchor_web_settings=False):
        db.execute("BEGIN")
        current = cls._read_clear_revision(db, owner)
        if current and (not anchor_web_settings or db.execute(
                "SELECT 1 FROM cached WHERE owner=? AND method='web' AND key=?",
                (owner, _DISPLAY_SETTINGS_KEY)).fetchone()):
            return current
        # Anchor the request before upstream work. Old versions delete this row
        # when clearing, even if this account did not have any cached pages yet.
        db.rollback()
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO cached VALUES(?,?,?,?)",
            (owner, "account", _ACCOUNT_CLEAR_KEY, json.dumps(uuid.uuid4().hex)))
        if anchor_web_settings:
            # New readers update this row in place. Legacy writers delete or
            # replace it, so their invalidations remain visible through rowid.
            db.execute("INSERT OR IGNORE INTO cached VALUES(?,?,?,?)",
                       (owner, "web", _DISPLAY_SETTINGS_KEY, "null"))
        return cls._read_clear_revision(db, owner)

    @staticmethod
    def _read_clear_revision(db, owner):
        row = db.execute("SELECT payload FROM cached WHERE owner=? AND method='account' AND key=?",
            (owner, _ACCOUNT_CLEAR_KEY)).fetchone()
        return row[0] if row else ""

    @classmethod
    def _check_clear_revision(cls, db, owner, expected):
        if expected is not None and cls._read_clear_revision(db, owner) != expected:
            raise AccountError("cache_cleared", "账号缓存已清理，请重新打开页面")

    @staticmethod
    def _is_display_key(key):
        return key[0] in _SNAPSHOT_KINDS or (
            key[0] == "data" and key[1] in {"bookmarks", "works"})

    @staticmethod
    def _delete_display_pages(db, owner, method):
        db.execute("DELETE FROM cached WHERE owner=? AND method=? AND "
            "((json_extract(key,'$[0]')='data' AND json_extract(key,'$[1]') IN ('bookmarks','works')) "
            "OR json_extract(key,'$[0]') IN ('discovery-current','discovery-feed','popularity-current','popularity-index'))",
            (owner, method))
        AccountCache._advance_page_epochs(db, owner, method, ("bookmarks", "works"))
        AccountCache._advance_display_revision(db, owner, method)

    def refresh_web_audience_cache(self, owner):
        # Persisted display pages predate the current Web card normalization.
        with self.lock, closing(self._connect()) as db, db:
            updated = db.execute("INSERT INTO cached VALUES(?,?,?,?) ON CONFLICT(owner,method,key) "
                "DO UPDATE SET payload=excluded.payload WHERE cached.payload<>excluded.payload",
                (owner, "web", '["web-artwork-audience"]', "3"))
            if updated.rowcount:
                self._delete_display_pages(db, owner, "web")

    @staticmethod
    def _advance_display_revision(db, owner, method):
        db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
            (owner, method, _DISPLAY_REVISION_KEY, json.dumps(uuid.uuid4().hex)))

    @staticmethod
    def _advance_web_policy(db, owner, method):
        db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
            (owner, method, _WEB_POLICY_KEY, json.dumps(uuid.uuid4().hex)))

    def begin_display_update(self, owner, method, before=None):
        pending = {"started": time.time()}
        if before is not None:
            pending["before"] = _web_display_flags(before)
        with self.lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
                (owner, method, _DISPLAY_PENDING_KEY, json.dumps(pending)))
            # Check the same invalidation before sending, retaining the pages until readback.
            db.execute("SAVEPOINT display_preflight")
            self._delete_display_pages(db, owner, method)
            db.execute("ROLLBACK TO display_preflight")
            db.execute("RELEASE display_preflight")

    def display_update_pending(self, owner, method):
        with self.lock, closing(self._connect()) as db:
            return self._display_update_pending(db, owner, method)

    @staticmethod
    def _display_update_pending(db, owner, method):
        return db.execute("SELECT 1 FROM cached WHERE owner=? AND method=? AND key=?",
            (owner, method, _DISPLAY_PENDING_KEY)).fetchone() is not None

    @classmethod
    def _require_display_ready(cls, db, owner, method):
        if cls._display_update_pending(db, owner, method):
            raise AccountError("settings_pending", "浏览设置正在核对，请稍后刷新。")

    def finish_display_update(self, owner, method, state):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            values = {key: json.loads(payload) for key, payload in db.execute(
                "SELECT key,payload FROM cached WHERE owner=? AND method=? AND key IN (?,?,?)",
                (owner, method, _DISPLAY_SETTINGS_KEY, _PREVIOUS_WEB_FLAGS_KEY, _DISPLAY_PENDING_KEY))}
            settings = values.get(_DISPLAY_SETTINGS_KEY)
            pending = values.get(_DISPLAY_PENDING_KEY)
            previous = _web_display_flags(settings) if settings is not None else values.get(_PREVIOUS_WEB_FLAGS_KEY)
            if previous is None and pending is not None:
                previous = pending.get("before")
            changed = tuple(previous) != _web_display_flags(state) if previous is not None else pending is not None
            db.execute("INSERT INTO cached VALUES(?,?,?,?) ON CONFLICT(owner,method,key) "
                "DO UPDATE SET payload=excluded.payload",
                (owner, method, _DISPLAY_SETTINGS_KEY, json.dumps(state)))
            db.execute("DELETE FROM cached WHERE owner=? AND method=? AND key IN (?,?)",
                (owner, method, _DISPLAY_PENDING_KEY, _PREVIOUS_WEB_FLAGS_KEY))
            if changed:
                self._delete_display_pages(db, owner, method)
                self._advance_web_policy(db, owner, method)
            return changed

    def entries(self, owner, method, views=None):
        with self.lock, closing(self._connect()) as db:
            return [(json.loads(key), json.loads(payload)) for key, _, payload in
                    self._entry_rows(db, owner, method, views)]

    def entry_versions(self, owner, method, views):
        with self.lock, closing(self._connect()) as db:
            return {tuple(json.loads(key)): rowid
                    for key, rowid in self._entry_rows(db, owner, method, views, include_payload=False)}

    @staticmethod
    def _entry_rows(db, owner, method, views, *, include_payload=True):
        columns = "key,rowid,payload" if include_payload else "key,rowid"
        return db.execute(
            f"SELECT {columns} FROM cached WHERE owner=? AND method=? AND key LIKE ?"+
            (" AND json_extract(key,'$[1]') IN ("+','.join('?' for _ in views)+')' if views is not None else ""),
            (owner,method,'["data",%',*views) if views is not None else (owner,method,'["data",%'))

    def update_entries(self, owner, method, views, update):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            entries = [(json.loads(key), json.loads(payload), rowid)
                       for key, rowid, payload in self._entry_rows(db, owner, method, views)]
            patches, evicted = update(entries)
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND key=?",
                           ((owner, method, json.dumps(key)) for key in evicted))
            patches = [(key, value) for key, value in patches
                       if key[0] != "web-relation-rebase" or value != "patched"
                       or self._web_relation_rebase(db, owner, key) != "partial"]
            self._save_rows(db,owner,method,patches)
            # An overlapping read can still have rows that no cached page contains yet.
            self._advance_page_epochs(db, owner, method, views)

    def finish_relation_scan(self, owner, method, view, app_path, retained):
        """Retire obsolete list pages and their continuation keys together."""
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT key FROM cached WHERE owner=? AND method=? AND
                ((json_extract(key,'$[0]')='data' AND json_extract(key,'$[1]')=?)
                OR (json_extract(key,'$[0]') IN ('cursor','cursor-state') AND json_extract(key,'$[1]')=?))""",
                (owner, method, view, app_path if method == "app" else None)).fetchall()
            removed = []
            for (encoded,) in rows:
                key = json.loads(encoded)
                if key[0] == "data":
                    scope, offset = key[3:5]
                    tag = key[5] if view == "bookmarks" else ""
                else:
                    params, offset = dict(key[2]), key[3]
                    scope, tag = params.get("restrict"), params.get("tag", "")
                    if scope not in retained:
                        continue
                if tag or offset not in retained.get(scope, ()):
                    removed.append((encoded, key))
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND key=?",
                ((owner, method, encoded) for encoded, _ in removed))
            self._advance_page_epochs(db, owner, method, (view,))

    def discard_relations(self, owner, method, views, paths):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany("DELETE FROM cursor_visits WHERE owner=? AND method=? AND substr(path,1,?)=?",
                           ((owner, method, len(path) + 1, path + "?") for path in paths))
            db.execute("DELETE FROM cached WHERE owner=? AND method=? AND (key LIKE ? OR "
                "key LIKE ?) AND json_extract(key,'$[1]') IN (" + ",".join("?" for _ in views) + ")",
                (owner, method, '["data",%', '["web-relation-rebase",%', *views))
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND (key LIKE ? OR key LIKE ?) AND "
                           "json_extract(key,'$[1]')=?",
                           ((owner, method, '["cursor",%', '["cursor-state",%', path) for path in paths))
            self._advance_page_epochs(db, owner, method, sorted(views))


class AccountPages:
    _RELATION_VIEWS = {
        "bookmark": ("bookmarks", "profile", "profile-web", "tags", "works"),
        "follow": ("following", "followers", "profile", "profile-web"),
    }
    _RELATION_PATHS = {"bookmark": "/v1/user/bookmarks/illust", "follow": "/v1/user/following"}
    def __init__(self, *, directory, request_json, request_homepage, generation, coordinator, proxy, method=lambda:"web",
                 commit_guard=None, stopped=lambda:False, credential_publication=nullcontext,
                 login_publication=nullcontext, confirm_web_owner=None, invalidate_web_session=None):
        self.directory = Path(directory)
        self.request_json, self.request_homepage = request_json, request_homepage
        self.generation, self.coordinator, self.proxy = generation, coordinator, proxy
        self.method = method
        self.stopped = stopped
        self._closed = False
        self.commit_guard = commit_guard if commit_guard is not None else threading.RLock()
        self.credential_publication = credential_publication
        self.login_publication = login_publication
        self.confirm_web_owner = confirm_web_owner
        self.invalidate_web_session = invalidate_web_session
        self._app_credentials_revoked = False
        self._account = None
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._cache_revision = 0
        self._loads = {}
        self.visits = VisitStore(self.directory / "account-browse.sqlite3")
        self.saved_cache = AccountCache(self.directory / "account-cache.sqlite3")
        self.auth_status = {"pending": False, "error": ""}

    def account(self):
        with self._lock:
            if self._closed or self.stopped():
                raise AccountError("closed", "账号连接已关闭，请重新打开程序")
            if self._account is None:
                transport = AppTransport(self.directory / "request-settings.json", self.proxy, coordinator=self.coordinator)
                self._account = AppAccount(CredentialVault(), transport, CLIENT_CONFIG,
                    credential_publication=self.credential_publication, login_publication=self.login_publication,
                    credentials_revoked=self._app_credentials_revoked)
            return self._account

    def check(self, generation):
        ACCOUNT_REQUEST_CHECK.get()()
        if self._closed or self.stopped():
            raise AccountError("closed", "应用正在退出，请重新打开")
        if self.generation() != generation:
            raise AccountError("revoked", "账号已改变，请重新打开我的账户")

    @contextmanager
    def publication(self, generation):
        """Linearize local publication with account revocation, before module locks."""
        with checked_lock(self.commit_guard, lambda: self.check(generation)):
            yield

    @contextmanager
    def cache_publication(self, generation, owner, *, clear_revision=None):
        with self.publication(generation), self.saved_cache.owner_mutex(owner):
            self.check(generation)
            if clear_revision is not None and self.saved_cache.clear_revision(owner) != clear_revision:
                raise AccountError("cache_cleared", "账号缓存已清理，请重新打开页面")
            yield

    def invalidate(self):
        with self._lock:
            self._cache_revision += 1
            self._cache.clear()
            account = self._account
        if account:
            account.fence()

    def invalidate_display_views(self):
        with self._lock:
            self._cache_revision += 1

    def disconnect(self):
        self.invalidate()
        with self._lock:
            self._app_credentials_revoked = True
            account=self._account
        if account is not None:
            account.logout()
        else:
            CredentialVault().delete()

    def adopt_app_credentials(self, *, revoked):
        with self._lock:
            self._app_credentials_revoked = revoked
            if self._account is not None:
                if revoked:
                    self._account.forget_credentials()
                else:
                    self._account.adopt_credentials()

    def close(self):
        with self.commit_guard:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                account = self._account
            self.invalidate()
        self.saved_cache.close_policy_reader()
        if account is not None:
            account.transport.close()

    def _cached(self, key, load, refresh=False, *, clear_revision=None):
        if len(key) > 2 and clear_revision is None:
            clear_revision = self.saved_cache.clear_revision(key[2])
        if ACCOUNT_CACHE_ONLY.get():
            return self._load_cached(key, load, refresh, clear_revision=clear_revision)
        if not refresh:
            token=ACCOUNT_CACHE_ONLY.set(True)
            try:
                return self._load_cached(key, load, clear_revision=clear_revision)
            except AccountError as error:
                if error.kind not in {"cache_miss","cache_changed"}:raise
            finally:
                ACCOUNT_CACHE_ONLY.reset(token)
        with self._lock:
            pending = self._loads.setdefault(key, [threading.RLock(),0]);pending[1]+=1
        try:
            with checked_lock(pending[0],lambda:self.check(key[0])):
                try:
                    return self._load_cached(key, load, refresh, clear_revision=clear_revision)
                except AccountError as error:
                    if error.kind!="cache_changed":raise
                    # A write overlapped this first read, so no repaired page
                    # exists yet. Replace that obsolete response once.
                    return self._load_cached(key, load, clear_revision=clear_revision)
        finally:
            with self._lock:
                pending[1]-=1
                if not pending[1] and self._loads.get(key) is pending:del self._loads[key]

    def _load_cached(self, key, load, refresh=False, *, clear_revision):
        persistent = len(key) > 2
        method = self.method()
        durable_key = ["data", *key[1:]] if persistent else None
        web_relation_page = method == "web" and key[1] in {"following", "bookmarks"}
        with self._lock:
            cache_revision = self._cache_revision
            cached = self._cache.get(key) if not persistent else None

        previous_check = ACCOUNT_REQUEST_CHECK.get()

        def check_read():
            previous_check()
            with self._lock:
                if cache_revision != self._cache_revision:
                    raise AccountError("cache_cleared", "账号缓存已清除，请重新打开页面")

        if cached and key[1] == "owner" and time.monotonic() - cached[0] >= 60:
            cached = None
        if cached and not refresh:
            result = copy.deepcopy(cached[1])
            self.check(key[0])
            check_read()
            return result
        if persistent and not refresh:
            cached = self.saved_cache.read(key[2], method, durable_key, clear_revision=clear_revision)
            if cached is not None:
                self.check(key[0])
                check_read()
                if web_relation_page:
                    self._check_web_relation_offset(key, cached)
                if key[1] == "bookmarks":
                    scope, offset, _tag, mode = key[3:]
                    trace_artwork_rows(method + "-bookmarks", "cached-result", cached.get("items", ()),
                                       mode=mode, scope=scope, position=offset)
                return cached
        if ACCOUNT_CACHE_ONLY.get():
            raise AccountError("cache_miss", "账号数据尚未在本地准备好")
        revision = self.saved_cache.display_revision(key[2], method) if persistent and key[1] in {"bookmarks", "works"} else None
        if persistent:
            stamp = self.saved_cache.page_version(key[2], method, durable_key, clear_revision=clear_revision)
        token = ACCOUNT_REQUEST_CHECK.set(check_read)
        try:
            result = load()
        finally:
            ACCOUNT_REQUEST_CHECK.reset(token)
        if web_relation_page:
            self._check_web_relation_offset(key, result)
        with self.publication(key[0]), self._lock:
            check_read()
            if persistent:
                cursor = None
                if isinstance(result, AppRelationPage):
                    cursor, result = result.cursor, result.data
                self.saved_cache.save_account_page(key[2], method, durable_key, result, stamp, cursor,
                    clear_revision=clear_revision, display_revision=revision)
            else:
                # Only the verified session owner is process-local.
                self._cache[key] = (time.monotonic(), copy.deepcopy(result))
        return result

    @staticmethod
    def _check_web_relation_offset(key, page):
        view, offset = key[1], key[4]
        if not offset:
            return
        total = page["total"]
        if total is not None and offset >= total and not page["people" if view == "following" else "items"]:
            raise AccountError("page_retired", "账号分页已更新，请返回第一页刷新")

    def clear_cache(self, owner):
        with self._lock:
            self._cache_revision += 1
            self.saved_cache.clear(owner)
            self.visits.clear(owner)

    def cache_scan_page(self, owner, method, kind, scope, offset, rows, next_cursor, total, generation, *, revision=None):
        """The same scan response supplies relationship state and display pages."""
        view = "bookmarks" if kind == "bookmark" else "following"
        next_offset = offset + len(rows) if next_cursor is not None else None
        modes = ("safe", "all", "r18", "r18g") if kind == "bookmark" else ("all",)
        pages = []
        for mode in modes:
            page = self.relation_page(method, kind, rows, offset=offset, next_offset=next_offset, total=total, mode=mode)
            key = (generation, view, owner, scope, offset, "", mode) if kind == "bookmark" else (generation, view, owner, scope, offset)
            pages.append((key, page))
        stored = [(["data", *key[1:]], page) for key, page in pages]
        if method == "app" and next_cursor is not None:
            path = self._RELATION_PATHS[kind]
            params = {"user_id":owner, "restrict":scope, "filter":"for_android"}
            stored.append((["cursor",path,sorted(params.items()),next_offset],next_cursor))
            cursor_scan = uuid.uuid4().hex
            stored.append((["cursor-state",path,sorted(params.items()),next_offset],{"path":next_cursor,"scan":cursor_scan}))
        with self.publication(generation), self._lock:
            self.saved_cache.save_many(owner, method, stored, revision=revision)

    def discard_relation_pages(self, owner, method, generation, kinds):
        """Recover derived pages after an interrupted cross-database publication."""
        views = sorted({view for kind in kinds for view in self._RELATION_VIEWS[kind]})
        paths = sorted(self._RELATION_PATHS[kind] for kind in kinds) if method == "app" else ()
        with self.publication(generation), self._lock:
            self.saved_cache.discard_relations(owner, method, views, paths)

    @staticmethod
    def relation_page(method, kind, rows, *, offset, next_offset, total=None, mode="safe", scope=None):
        """Normalize remote display rows once, shared by browsing and cloud scanning."""
        if kind == "bookmark":
            items=[];unavailable=0
            for row in rows:
                try:
                    if method == "app":
                        item = normalize_work(row, allow_r18=mode != "safe")
                    else:
                        item = normalize_search_item(row, allow_r18=mode != "safe", logged_in=True)
                except PixivPolicyError:
                    unavailable+=1;continue
                if matches_content_mode(item["restriction"],mode):items.append(item)
            if scope is not None:
                trace_artwork_rows(method + "-bookmarks", "upstream", rows, mode=mode, scope=scope, position=offset)
                trace_artwork_rows(method + "-bookmarks", "after-filter", items, mode=mode, scope=scope, position=offset)
            return {"items":items,"unavailable":unavailable,"total":total if total is not None else len(rows) if offset==0 and next_offset is None else None,"nextOffset":next_offset}
        people=[]
        for row in rows:
            if method=="app":
                raw=row.get("user") if isinstance(row,dict) else None
                profile=artist_profile(raw)
                people.append({"id":profile["userId"],"name":profile["name"],"avatar":profile["avatar"],"bio":profile["description"],"bioLoaded":"comment" in raw,"following":True,"isNew":False})
            else:
                if not isinstance(row,dict) or not valid_pixiv_id(str(row.get("userId",""))):
                    raise AccountError("invalid_data","关注作者资料格式异常")
                people.append({"id":str(row["userId"]),"name":str(row.get("userName","")),"avatar":image_url(row.get("profileImageUrl")),"bio":str(row.get("userComment") or ""),"bioLoaded":"userComment" in row,"following":True,"isNew":False})
        return {"people":people,"total":total if total is not None else len(people) if offset==0 and next_offset is None else None,"nextOffset":next_offset}

    def finish_scan(self, owner, method, kind, offsets, generation):
        view="bookmarks" if kind=="bookmark" else "following"
        path = self._RELATION_PATHS[kind]
        retained = {scope: set(values) for scope, values in offsets.items()}
        with self.publication(generation), self._lock:
            self.saved_cache.finish_relation_scan(owner, method, view, path, retained)

    def relation_page_versions(self, owner, method, kind):
        return self.saved_cache.entry_versions(owner, method, self._RELATION_VIEWS[kind])

    def note_relation(self, owner, method, kind, target, before, after, *, repair=False, before_pages=None):
        """Patch cached pages after a confirmed write; do not invalidate unrelated pages."""
        generation=self.generation()
        view, field = ("bookmarks", "items") if kind == "bookmark" else ("following", "people")
        views = self._RELATION_VIEWS[kind]
        def patch_entries(entries):
            record = next((row for _,page,_ in entries for row in page.get(field,[]) if row["id"]==target),None)
            patches=[]
            evicted = []
            groups = {}
            changed_groups = set()
            refresh_groups = set()
            for key,page,rowid in entries:
                changed = False
                replaced = before_pages is not None and before_pages.get(tuple(key)) != rowid
                if key[1] == view:
                    scope,offset = key[3:5]
                    rows = page[field]
                    present = any(row["id"] == target for row in rows)
                    tag,mode = key[5:7] if kind=="bookmark" else ("","all")
                    def membership(state, *, visible=True):
                        if not state["active"] or state["privacy"] is not None and state["privacy"]!=scope:return False
                        if state["privacy"] is None and not present:return None
                        if tag:
                            if state["tags"] is None:return None
                            if tag not in state["tags"]:return False
                        if visible and mode!="all":
                            if record is None:return None
                            if not matches_content_mode(record["restriction"],mode):return False
                        return True
                    previous_member,next_member=membership(before),membership(after)
                    fits = next_member is True or next_member is None and present
                    if present and not fits:
                        page[field] = [row for row in rows if row["id"] != target]
                        changed=True
                    elif fits and not present and offset==0 and record:
                        page[field] = [record, *rows]
                        changed=True
                    # A sweep may return an old row after a newer local write. It
                    # repairs that page; it is not another relationship transition.
                    delta=int(next_member)-int(previous_member) if not repair and previous_member is not None and next_member is not None else len(page[field])-len(rows)
                    total = page.get("total")
                    ambiguous_total = replaced and delta and total is not None
                    if (ambiguous_total and kind == "follow" and not offset and page["nextOffset"] is None
                            and total == len(rows) and (not fits or present or record is not None)):
                        # A complete following page proves whether the target was already counted.
                        delta = len(page[field]) - len(rows)
                        ambiguous_total = False
                    if ambiguous_total and method == "app":
                        evicted.append(key)
                        continue
                    if not ambiguous_total and delta and total is not None:
                        page["total"] = max(0, total + delta)
                        changed = True
                    if method == "web":
                        group = self.saved_cache.web_relation_group(key)
                        groups.setdefault(group, {})[offset] = page
                        raw_before, raw_after = membership(before, visible=False), membership(after, visible=False)
                        if changed or ambiguous_total or raw_before != raw_after:
                            changed_groups.add(group)
                        if ambiguous_total or raw_before is False and raw_after is True and record is None:
                            refresh_groups.add(group)
                elif key[1]=="followers" and kind=="follow":
                    for person in page["people"]:
                        if person["id"]==target and person["following"]!=after["active"]:
                            person["following"]=after["active"];changed=True
                elif key[1] in {"profile","profile-web"}:
                    count = "following" if kind=="follow" else "publicBookmarks"
                    delta = int(after["active"])-int(before["active"]) if kind=="follow" else (
                        int(after["active"] and after["privacy"]=="public")-int(before["active"] and before["privacy"]=="public"))
                    if delta and page.get(count) is not None:
                        if replaced:
                            evicted.append(key)
                            continue
                        page[count]=max(0,page[count]+delta);changed=True
                elif (key[1]=="tags" and kind=="bookmark" and
                      all(not state["active"] or state["privacy"] is not None and state["tags"] is not None
                          for state in (before,after))):
                    for scope in ("public","private"):
                        old=set(before["tags"] or []) if before["active"] and before["privacy"]==scope else set()
                        new=set(after["tags"] or []) if after["active"] and after["privacy"]==scope else set()
                        if old == new:
                            continue
                        if replaced:
                            evicted.append(key)
                            break
                        for name in old^new:
                            delta=int(name in new)-int(name in old)
                            row=next((row for row in page[scope] if row["name"]==name),None)
                            if row:row["count"]=max(0,row["count"]+delta)
                            elif delta>0:page[scope].append({"name":name,"count":delta})
                        page[scope]=[row for row in page[scope] if row["count"]>0]
                        changed = True
                if changed:
                    patches.append((key,page))
            for group in changed_groups:
                pages = groups[group]
                offset = 0
                seen = set()
                complete = False
                total = pages[0]["total"] if 0 in pages else None
                while offset in pages:
                    page = pages[offset]
                    ids = {row["id"] for row in page[field]}
                    if (page["total"] != total or seen & ids or len(ids) != len(page[field])
                            or offset and total is not None and offset >= total and not ids):
                        break
                    seen.update(ids)
                    successor = page["nextOffset"]
                    if successor is None:
                        complete = True
                        break
                    if successor <= offset:
                        break
                    offset = successor
                status = "patched" if complete and group not in refresh_groups else "partial"
                patches.append((group, status))
            return patches, evicted
        with self.publication(generation), self._lock:
            self.saved_cache.update_entries(owner, method, views, patch_entries)

    def web_owner(self, generation):
        def load():
            parser = HomepageIdentity()
            parser.feed(self.request_homepage())
            try:
                owner = parser.user_id()
            except AccountError as error:
                if error.kind == "reauth" and self.invalidate_web_session is not None:
                    self.invalidate_web_session(generation)
                raise
            with self.cache_publication(generation, owner):
                self.saved_cache.refresh_web_audience_cache(owner)
            if self.confirm_web_owner is not None:
                self.confirm_web_owner(generation, owner)
            return owner
        return self._cached((generation, "owner"), load)

    def remember_web_owner(self, generation, owner):
        """Publish an identity already verified with the candidate login cookie."""
        with self.cache_publication(generation, owner), self._lock:
            self.saved_cache.refresh_web_audience_cache(owner)
            self._cache[(generation, "owner")] = (time.monotonic(), owner)
        if self.confirm_web_owner is not None:
            self.confirm_web_owner(generation, owner)

    def cached_web_owner(self, generation):
        with self._lock:
            cached = self._cache.get((generation, "owner"))
            return cached[1] if cached is not None else None

    def profile(self, generation, refresh=False, *, clear_revision=None):
        if self.method() == "web":
            owner = self.web_owner(generation)
            def load_web():
                raw = source_body(self.request_json(f"https://www.pixiv.net/ajax/user/{owner}?full=1&lang=zh"))
                profile = normalize_artist(raw, owner)
                counts = {}
                for field,key in (("following","following"),("publicBookmarks","illustBookmarks")):
                    value=raw.get(key)
                    counts[field]=value if type(value) is int and value>=0 else None
                own=[raw.get("illusts"),raw.get("manga")]
                counts["workCount"]=sum(own) if all(type(n) is int and n>=0 for n in own) else None
                return {"connected":True,"uid":owner,"name":profile["name"],"account":"","bio":profile["description"],
                    "avatar":profile["avatar"],"background":profile["background"],"region":profile["region"],"website":profile["webpage"],**counts,"method":"web"}
            return {**self._cached((generation,"profile-web",owner),load_web,refresh,clear_revision=clear_revision),
                "authorization":{"pending":False,"error":""}}
        account = self.account()
        snapshot = account.snapshot()
        if not snapshot["connected"]:
            return {"connected": False,"method":"app", "authorization": dict(self.auth_status)}
        owner=snapshot["uid"]

        def load():
            raw = account.request("GET", "/v1/user/detail?" + urlencode({"user_id": owner, "filter": "for_android"}))
            user, details = raw.get("user"), raw.get("profile")
            if not isinstance(user, dict) or str(user.get("id")) != owner or not isinstance(details, dict):
                raise AccountError("invalid_data", "Pixiv 未返回当前账号资料")
            avatar = user.get("profile_image_urls")
            result = {"connected": True, "uid": owner, "name": str(user.get("name", "")),
                "account": str(user.get("account", "")), "bio": str(user.get("comment", "")),
                "avatar": image_url(avatar.get("medium")) if isinstance(avatar, dict) else "",
                "background": image_url(details.get("background_image_url")),
                "following": source_total(details.get("total_follow_users")),
                "publicBookmarks": source_total(details.get("total_illust_bookmarks_public")),
                "region": str(details.get("region", "")), "website": str(details.get("webpage") or ""),
                "workCount":sum(details[key] for key in ("total_illusts","total_manga")) if all(type(details.get(key)) is int and details[key]>=0 for key in ("total_illusts","total_manga")) else None,
                "method":"app", "authorization": dict(self.auth_status)}
            self.check(generation)
            account.promote_verified(snapshot["generation"])
            return result
        result = self._cached((generation, "profile", owner), load, refresh, clear_revision=clear_revision)
        return {**result, "authorization": dict(self.auth_status)}

    def latest_dates(self, generation, owner, scope, refresh=False, *, clear_revision=None):
        method=self.method();feed_scope=scope if method=="app" else "all"
        if clear_revision is None:
            clear_revision = self.saved_cache.clear_revision(owner)
        def load():
            scan_key=["follow-update-scan",feed_scope]
            expected = self.saved_cache.read(owner, method, scan_key, clear_revision=clear_revision)
            scan = expected or {}
            initial=not scan
            checkpoint=scan.get("checkpoint","")
            path=scan.get("cursor") or ("/v2/illust/follow?"+urlencode({"restrict":scope}) if method=="app" else 1)
            cutoff=scan.get("cutoff","") if scan.get("cursor") else checkpoint
            head=scan.get("head","") if scan.get("cursor") else ""
            visited=list(scan.get("visitedPaths",[])) if scan.get("cursor") else []
            dates = {}
            for _ in range(3):
                self.check(generation)
                if method=="app":
                    raw=self.account().request("GET",path)
                    works=raw.get("illusts")
                    next_path=continuation(raw,"/v2/illust/follow",current=path,visited=visited)
                    if next_path and parse_qs(urlsplit(next_path).query).get("restrict")!=[scope]:
                        raise AccountError("cursor","关注新作续页无效")
                else:
                    body=source_body(self.request_json("https://www.pixiv.net/ajax/follow_latest/illust?"+urlencode({"p":path,"mode":"all","lang":"zh"})))
                    thumbnails,info=body.get("thumbnails"),body.get("page")
                    if not isinstance(thumbnails,dict) or not isinstance(info,dict) or type(info.get("isLastPage")) is not bool:
                        raise AccountError("invalid_data","关注新作分页格式异常")
                    works=thumbnails.get("illust");next_path=None if info["isLastPage"] else path+1
                if not isinstance(works,list) or len(works)>1000:
                    raise AccountError("invalid_data", "关注更新信息格式异常")
                if next_path and not works:
                    raise AccountError("cursor","关注更新返回空续页，请稍后重试")
                reached=False;page_dates={}
                for work in works:
                    if not isinstance(work,dict):continue
                    if method=="app":
                        if not isinstance(work.get("user"),dict):continue
                        author,stamp=str(work["user"].get("id","")),work.get("create_date")
                    else:author,stamp=str(work.get("userId","")),work.get("createDate")
                    if not valid_pixiv_id(author) or not isinstance(stamp,str):
                        continue
                    try:
                        parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
                        if parsed.tzinfo is None:continue
                        canonical=parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
                    except ValueError:
                        continue
                    head=max(head,canonical)
                    if cutoff and canonical<cutoff:reached=True;continue
                    page_dates[author]=max(page_dates.get(author,""),canonical)
                cursor=next_path if not initial and not reached and works else None
                visited.append(path)
                with self.cache_publication(generation, owner, clear_revision=clear_revision):
                    if self.saved_cache.read(owner, method, scan_key) != expected:
                        break
                    # A timestamp can span authors and pages. Revisit ties, skipping
                    # authors already observed at that time in this feed's scope.
                    boundary = [author for author, stamp in page_dates.items() if stamp == cutoff]
                    if boundary:
                        known = self.visits.status(owner, feed_scope, authors=boundary)
                        for author, marker in known.items():
                            if marker["latestObserved"] >= cutoff:
                                del page_dates[author]
                    self.visits.observe(owner,page_dates,scope=feed_scope,initial=initial)
                    expected = {"checkpoint":checkpoint if cursor else max(head,checkpoint), "cursor":cursor,
                        "cutoff":cutoff, "head":head if cursor else "", "visitedPaths":visited.copy() if cursor else []}
                    self.saved_cache.save(owner, method, scan_key, expected)
                for author,stamp in page_dates.items():dates[author]=max(dates.get(author,""),stamp)
                if not cursor:break
                path=cursor
            return dates
        return self._cached((generation, "updates", owner, feed_scope), load, refresh, clear_revision=clear_revision)

    def read(self, kind, *, scope="public", offset=0, tag="", generation, allow_r18=False, refresh=False,
             mode=None, check_updates=True, clear_revision=None):
        mode = mode or ("all" if allow_r18 else "safe")
        if mode not in CONTENT_MODES:raise AccountError("invalid_request", "内容范围无效")
        allow_r18 = mode != "safe"
        profile = self.profile(generation, refresh=refresh and kind == "profile", clear_revision=clear_revision)
        if not profile["connected"] or kind == "profile":
            return profile
        owner = profile["uid"]
        if clear_revision is None:
            clear_revision = self.saved_cache.clear_revision(owner)
        if kind == "following":
            result = self._cached((generation, kind, owner, scope, offset), lambda: self._app_people(owner,scope,offset,generation) if self.method()=="app" else self._people(owner, scope, offset), refresh, clear_revision=clear_revision)
            markers = self.visits.status(owner, scope if self.method() == "app" else None,
                                       authors=[person["id"] for person in result["people"]]) if check_updates else {}
            for person in result["people"]:
                person.update(markers.get(person["id"],{"isNew":False,"latestObserved":""}))
        elif kind == "followers":
            if self.method()=="web":
                raise AccountError("capability","网页登录暂不提供粉丝列表，可在资料中选择 App 授权。")
            result = self._cached((generation, kind, owner, offset), lambda: self._followers(owner, offset), refresh, clear_revision=clear_revision)
        elif kind == "bookmarks":
            result = self._cached((generation, kind, owner, scope, offset, tag, mode), lambda: self._app_bookmarks(owner,scope,offset,tag,mode,generation) if self.method()=="app" else self._bookmarks(owner, scope, offset, tag, mode), refresh, clear_revision=clear_revision)
        elif kind == "tags":
            result = self._cached((generation, kind, owner), lambda: self._app_tags() if self.method()=="app" else self._tags(owner), refresh, clear_revision=clear_revision)
        elif kind == "updates":
            feed_scope=scope if self.method()=="app" else "all"
            dates=self.latest_dates(generation,owner,scope,refresh,clear_revision=clear_revision) if check_updates else {}
            scan=self.saved_cache.read(owner,self.method(),["follow-update-scan",feed_scope],clear_revision=clear_revision) or {}
            result={"dates":dates,"authors":self.visits.status(owner,scope if self.method()=="app" else None) if check_updates else {},
                "hasMore":bool(scan.get("cursor")) if check_updates else False,"combined":self.method()=="web"}
        elif kind == "works":
            result=self._cached((generation,kind,owner,mode),lambda:self._own_works(owner,profile["workCount"],allow_r18,mode),refresh,clear_revision=clear_revision)
        else:
            raise AccountError("invalid_request", "账户页面不存在")
        if "items" in result:
            result["items"]=[item for item in result["items"] if matches_content_mode(item["restriction"],mode)]
        self.check(generation)
        return {**result, "connected": True, "uid": owner, "kind": kind, "scope": scope, "offset": offset}

    def _own_works(self,owner,count,allow_r18,mode="all"):
        if count==0:return {"items":[]}
        if self.method()=="app":
            rows=[]
            for kind in ("illust","manga"):
                raw=self.account().request("GET","/v1/user/illusts?"+urlencode({"user_id":owner,"type":kind,"filter":"for_android"}))
                works=raw.get("illusts")
                if not isinstance(works,list):raise AccountError("invalid_data","本人作品列表格式异常")
                for value in works[:12]:
                    try:item=normalize_work(value,allow_r18=allow_r18)
                    except PixivPolicyError:continue
                    if item["userId"]==owner:rows.append(item)
        else:
            index=source_body(self.request_json(build_user_profile_all_url(owner)))
            ids=set()
            for kind in ("illusts","manga"):
                values=index.get(kind) or {}
                if not isinstance(values,(list,dict)):raise AccountError("invalid_data","本人作品索引格式异常")
                ids.update(str(value) for value in values if valid_pixiv_id(str(value)))
            chosen=sorted(ids,key=int,reverse=True)[:12]
            if not chosen:return {"items":[]}
            data=source_body(self.request_json(build_user_profile_works_url(owner,chosen))).get("works")
            if not isinstance(data,dict):raise AccountError("invalid_data","本人作品信息格式异常")
            rows=[]
            for value in data.values():
                try:item=normalize_search_item(value,allow_r18=allow_r18,logged_in=True)
                except PixivPolicyError:continue
                if item["userId"]==owner:rows.append(item)
        unique={item["id"]:item for item in rows if matches_content_mode(item["restriction"],mode)}
        return {"items":sorted(unique.values(),key=lambda item:(item["sortDate"],int(item["id"])),reverse=True)[:6]}

    def _app_page(self,path,params,offset,rows_key):
        packed = sorted(params.items())
        checkpoint = (self.saved_cache.read_cursor(params["user_id"], "app", ["cursor",path,packed,offset])
            if offset else (path+"?"+urlencode(params),uuid.uuid4().hex))
        if not checkpoint:raise AccountError("page_retired","分页记录已清理，请返回第一页刷新")
        cursor,cursor_scan=checkpoint
        raw=self.account().request("GET",cursor)
        rows=raw.get(rows_key)
        if not isinstance(rows,list):raise AccountError("invalid_data","App 列表格式异常")
        next_path=continuation(raw,path,current=cursor)
        if not rows and next_path:raise AccountError("cursor","App 列表返回空续页，请刷新后重试")
        if next_path:
            values=parse_qs(urlsplit(next_path).query)
            if any(values.get(name)!=[params[name]] for name in ("user_id","restrict") if name in params):
                raise AccountError("cursor","App 续页账号或公开范围不匹配")
            if "tag" in params and "tag" in values and values["tag"]!=[params["tag"]]:
                raise AccountError("cursor","App 续页收藏标签不匹配")
            if "offset" in values:
                positions=values["offset"]
                previous=parse_qs(urlsplit(cursor).query).get("offset",["0"])
                if (len(positions)!=1 or len(previous)!=1 or not positions[0].isascii() or not positions[0].isdecimal()
                        or not previous[0].isascii() or not previous[0].isdecimal() or int(positions[0])<=int(previous[0])):
                    raise AccountError("cursor","App 列表续页位置未前进")
        next_offset=offset+len(rows) if next_path else None
        return rows, next_offset, AccountContinuation(path, packed, offset, cursor, cursor_scan, next_path)

    def _app_people(self,owner,scope,offset,generation):
        rows,next_offset,cursor=self._app_page("/v1/user/following",{"user_id":owner,"restrict":scope,"filter":"for_android"},offset,"user_previews")
        page = self.relation_page("app","follow",rows,offset=offset,next_offset=next_offset)
        return AppRelationPage(page, cursor)

    def _app_bookmarks(self,owner,scope,offset,tag,mode,generation):
        params={"user_id":owner,"restrict":scope,"filter":"for_android"}
        if tag:params["tag"]=tag
        rows,next_offset,cursor=self._app_page("/v1/user/bookmarks/illust",params,offset,"illusts")
        page = self.relation_page("app","bookmark",rows,offset=offset,next_offset=next_offset,mode=mode,scope=scope)
        return AppRelationPage(page, cursor)

    def _app_tags(self):
        result={}
        for scope in ("public","private"):
            path="/v1/user/bookmark-tags/illust?"+urlencode({"restrict":scope});tags=[];visited=set()
            while path and len(tags)<300:
                raw=self.account().request("GET",path)
                rows=raw.get("bookmark_tags")
                if not isinstance(rows,list):raise AccountError("invalid_data","App 收藏标签格式异常")
                for row in rows[:300-len(tags)]:
                    if not isinstance(row,dict) or not isinstance(row.get("name"),str):raise AccountError("invalid_data","App 收藏标签内容异常")
                    tags.append({"name":row["name"],"count":source_total(row.get("count"))})
                following=continuation(raw,"/v1/user/bookmark-tags/illust",current=path,visited=visited)
                if following and not rows:raise AccountError("cursor","收藏标签续页未前进")
                if following and parse_qs(urlsplit(following).query).get("restrict")!=[scope]:
                    raise AccountError("cursor","收藏标签续页公开范围不符")
                visited.add(path)
                path=following
            result[scope]=tags
        return result

    def _people(self, owner, scope, offset):
        body = source_body(self.request_json("https://www.pixiv.net/ajax/user/" + owner + "/following?" + urlencode({"offset": offset, "limit": 30, "rest": "hide" if scope == "private" else "show", "tag": "", "lang": "zh"})))
        rows = body.get("users")
        if not isinstance(rows, list):
            raise AccountError("invalid_data", "关注列表格式异常")
        total = source_total(body.get("total"))
        return self.relation_page("web","follow",rows,offset=offset,next_offset=offset+len(rows) if rows and offset+len(rows)<total else None,total=total)

    def _followers(self, owner, offset):
        raw = self.account().request("GET", "/v1/user/follower?" + urlencode({"user_id": owner, "filter": "for_android", "offset": offset}))
        rows = raw.get("user_previews")
        if not isinstance(rows, list):
            raise AccountError("invalid_data", "粉丝列表格式异常")
        people = []
        for row in rows:
            user = row.get("user") if isinstance(row, dict) else None
            if not isinstance(user, dict) or not re.fullmatch(r"[0-9]{1,30}", str(user.get("id", ""))):
                raise AccountError("invalid_data", "粉丝资料格式异常")
            avatar = user.get("profile_image_urls")
            people.append({"id": str(user["id"]), "name": str(user.get("name", "")),
                "avatar": image_url(avatar.get("medium")) if isinstance(avatar, dict) else "",
                "bio": str(user.get("comment") or ""), "bioLoaded": "comment" in user,
                "following": user.get("is_followed") is True, "isNew": False})
        next_url = raw.get("next_url")
        next_offset = None
        if next_url:
            if not isinstance(next_url, str):
                raise AccountError("invalid_data", "粉丝续页格式异常")
            parsed = urlsplit(next_url)
            values = parse_qs(parsed.query)
            if parsed.scheme != "https" or parsed.netloc != "app-api.pixiv.net" or parsed.path != "/v1/user/follower":
                raise AccountError("invalid_data", "粉丝续页地址异常")
            value = values.get("offset", [""])[0]
            if not value.isascii() or not value.isdigit() or not offset < int(value) <= 100000:
                raise AccountError("invalid_data", "粉丝续页位置异常")
            next_offset = int(value)
        return {"people": people, "total": len(people) if offset == 0 and next_offset is None else None, "nextOffset": next_offset}

    def _bookmarks(self, owner, scope, offset, tag, mode):
        body = source_body(self.request_json("https://www.pixiv.net/ajax/user/" + owner + "/illusts/bookmarks?" + urlencode({"tag": tag, "offset": offset, "limit": 36, "rest": "hide" if scope == "private" else "show", "lang": "zh"})))
        rows = body.get("works")
        if not isinstance(rows, list):
            raise AccountError("invalid_data", "收藏作品列表格式异常")
        total = source_total(body.get("total"))
        return self.relation_page("web","bookmark",rows,offset=offset,next_offset=offset+len(rows) if rows and offset+len(rows)<total else None,total=total,mode=mode,scope=scope)

    def _tags(self, owner):
        body = source_body(self.request_json(f"https://www.pixiv.net/ajax/user/{owner}/illusts/bookmark/tags?lang=zh"))
        result = {}
        for kind in ("public", "private"):
            rows = body.get(kind)
            if not isinstance(rows, list):
                raise AccountError("invalid_data", "收藏分类格式异常")
            result[kind] = [{"name": row["tag"], "count": source_total(row["cnt"])} for row in rows[:300]
                            if isinstance(row, dict) and isinstance(row.get("tag"), str) and type(row.get("cnt")) is int and row["cnt"] >= 0]
        return result
