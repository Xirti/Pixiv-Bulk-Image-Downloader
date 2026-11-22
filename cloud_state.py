"""Small durable relationship index; it never stores image data or credentials."""
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


class ScanConflictError(ValueError):
    pass


DEFAULT_MACHINE = {"saveRoot": "", "createFolder": True, "previewLayout":"adaptive",
                   "previewAction":"expand", "previewSize":"large", "sensitiveMask":"both", "autoLoadPages":False,"showLoadButton":True,
                   "orderTags":"default", "orderAuthor":"default", "orderUid":"default", "orderPid":"default",
                   "orderRecommended":"default", "orderRanking":"default", "mediaMemory":"normal"}
DEFAULT_ACCOUNT = {"bookmarkDefault": "ask", "followDefault": "ask", "bookmarkLast": "private",
                   "followLast": "private", "autoSync": True, "syncMinutes": "10",
                   "autoRanking": True, "autoFollowing": True, "autoRecommended": True,"updateDots":True}


def validate_preferences(raw):
    if not isinstance(raw, dict) or raw.keys() - (DEFAULT_MACHINE.keys() | DEFAULT_ACCOUNT.keys()):
        raise ValueError("设置项无效")
    result = dict(raw)
    for key, value in raw.items():
        if key in {"orderTags","orderAuthor","orderUid","orderPid","orderRecommended","orderRanking"}:
            valid = isinstance(value,str) and value in {"default","popular"}
        elif key == "mediaMemory":
            valid = isinstance(value,str) and value in {"normal","low"}
        elif key == "previewLayout":
            valid = isinstance(value,str) and value in {"regular","adaptive"}
        elif key == "previewAction":
            valid = isinstance(value,str) and value in {"expand","popup"}
        elif key == "previewSize":
            valid = isinstance(value,str) and value in {"small","medium","large"}
        elif key == "sensitiveMask":
            valid = isinstance(value,str) and value in {"off","search","source","both"}
        elif key in {"bookmarkDefault", "followDefault"}:
            valid = isinstance(value, str) and value in {"ask", "public", "private"}
        elif key in {"bookmarkLast", "followLast"}:
            valid = isinstance(value, str) and value in {"public", "private"}
        elif key in {"autoSync", "createFolder", "autoRanking", "autoFollowing", "autoRecommended", "autoLoadPages","showLoadButton","updateDots"}:
            valid = type(value) is bool
        elif key == "saveRoot":
            valid = isinstance(value, str) and len(value) <= 4096 and not any(ord(c) < 32 for c in value)
            if valid:
                result[key] = value.strip()
        else:
            # Keep minutes as decimal text: long intervals need no timer-sized ceiling.
            text = str(value)
            valid = type(value) in (str, int) and re.fullmatch(r"[0-9]+", text, re.ASCII) is not None
            if valid:
                text = text.lstrip("0") or "0"
                valid = len(text) > 1 or text >= "3"
                result[key] = text
        if not valid:
            raise ValueError("同步间隔至少为 3 分钟（整数），其他设置须使用有效选项")
    return result


def relationship_stamp(state):
    return hashlib.sha256(json.dumps([state["active"], state.get("privacy"), state.get("tags")],
                                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:24]


class CloudState:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._clear_table_ready = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 1:
                if not self._clear_table_ready:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("CREATE TABLE IF NOT EXISTS cache_clear_pending(owner TEXT PRIMARY KEY)")
                    self._install_relation_clock(db)
                    db.commit()
                    self._clear_table_ready = True
                return db
            if version != 0:
                raise ValueError("Unsupported cloud state schema")
            db.executescript("""
          BEGIN IMMEDIATE;
          CREATE TABLE IF NOT EXISTS preferences(scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS preference_revision (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=0));
          CREATE TABLE IF NOT EXISTS relation_revision (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=0));
          CREATE TABLE IF NOT EXISTS relations(owner TEXT, method TEXT, kind TEXT, target TEXT,
            active INTEGER NOT NULL, privacy TEXT, tags TEXT, bookmark_id TEXT, touched REAL,
            version INTEGER NOT NULL, PRIMARY KEY(owner,method,kind,target));
          CREATE TABLE IF NOT EXISTS scans(owner TEXT, method TEXT, kind TEXT, payload TEXT NOT NULL,
            PRIMARY KEY(owner,method,kind));
          CREATE TABLE IF NOT EXISTS scan_seen(owner TEXT, method TEXT, kind TEXT, target TEXT,
            PRIMARY KEY(owner,method,kind,target));
          CREATE TABLE IF NOT EXISTS operations(owner TEXT, method TEXT, request_id TEXT, signature TEXT,
            result TEXT, touched REAL, PRIMARY KEY(owner,method,request_id));
          CREATE INDEX IF NOT EXISTS relations_version ON relations(version);
          CREATE TABLE IF NOT EXISTS page_repairs(owner TEXT, method TEXT, token TEXT,
            PRIMARY KEY(owner,method,token));
          CREATE TABLE IF NOT EXISTS cache_clear_pending(owner TEXT PRIMARY KEY);
            """)
            # Triggers also version writes made by an older running backend.
            for table, revision_table, prefix in (
                ("preferences", "preference_revision", "preference"),
                ("relations", "relation_revision", "relation"),
                ("scans", "relation_revision", "scan"),
            ):
                for action in ("INSERT", "UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {prefix}_{action.lower()}_revision
                        AFTER {action} ON {table} BEGIN
                          INSERT INTO {revision_table} VALUES(1,1)
                            ON CONFLICT(singleton) DO UPDATE SET version=version+1;
                        END""")
            self._install_relation_clock(db)
            db.execute("PRAGMA user_version=1")
            db.commit()
            self._clear_table_ready = True
        except BaseException:
            db.close()
            raise
        return db

    @staticmethod
    def _install_relation_clock(db):
        # Both v2.1.9 and current clients allocate versions using the indexed
        # MAX over relations. Preserve deleted versions in a reserved non-UID
        # row invisible to account queries, including when v2.1.9 clears data.
        db.execute("""INSERT INTO relations(owner,method,kind,target,active,touched,version)
            SELECT '', '', '', '', 0, 0, COALESCE(MAX(json_extract(payload,'$.version')),0) FROM scans WHERE 1
            ON CONFLICT(owner,method,kind,target) DO UPDATE SET version=excluded.version
            WHERE relations.version<excluded.version""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS retain_relation_version AFTER DELETE ON relations
            WHEN OLD.owner<>'' AND OLD.version>(SELECT version FROM relations
                WHERE owner='' AND method='' AND kind='' AND target='')
            BEGIN UPDATE relations SET version=OLD.version WHERE owner='' AND method='' AND kind='' AND target=''; END""")

    @staticmethod
    def _relation_clock(db):
        return db.execute("SELECT COALESCE(MAX(version),0) FROM relations").fetchone()[0]

    @contextmanager
    def _write(self):
        # Reserve the SQLite writer before reading a revision or merging a patch.
        # The Python lock alone cannot serialize another process/store instance.
        with self.lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _preferences_snapshot(db, scope):
        values = {}
        for key in ("machine", scope):
            row = db.execute("SELECT payload FROM preferences WHERE scope=?", (key,)).fetchone()
            if row:
                values.update(validate_preferences(json.loads(row[0])))
        row = db.execute("SELECT version FROM preference_revision WHERE singleton=1").fetchone()
        return {"settings": {**DEFAULT_MACHINE, **DEFAULT_ACCOUNT, **values},
                "settingsRevision": row[0] if row else 0}

    def preferences_snapshot(self, scope):
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            return self._preferences_snapshot(db, scope)

    def preferences(self, scope):
        return self.preferences_snapshot(scope)["settings"]

    @staticmethod
    def _save_preferences(db, scope, changes):
        for key, fields in (("machine", DEFAULT_MACHINE), (scope, DEFAULT_ACCOUNT)):
            patch = {k: v for k, v in changes.items() if k in fields}
            if not patch:
                continue
            row = db.execute("SELECT payload FROM preferences WHERE scope=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else {}
            value.update(patch)
            db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value)))

    def save_preferences(self, scope, changes):
        return self.save_preferences_snapshot(scope, changes)["settings"]

    def save_preferences_snapshot(self, scope, changes):
        # Caller has validated the external patch once.
        with self._write() as db:
            self._save_preferences(db, scope, changes)
            return self._preferences_snapshot(db, scope)

    @staticmethod
    def _decode(row, revision):
        if row is None:
            return {"known": False, "active": None, "privacy": None, "tags": None, "touched": None,
                    "relationRevision": revision}
        value = {"known": True, "active": bool(row["active"]), "privacy": row["privacy"],
                 "tags": json.loads(row["tags"]) if row["tags"] is not None else None,
                 "bookmarkId": row["bookmark_id"], "touched": row["touched"], "relationRevision": revision}
        value["stamp"] = relationship_stamp(value)
        return value

    @staticmethod
    def _relation_revision(db):
        row = db.execute("SELECT version FROM relation_revision WHERE singleton=1").fetchone()
        return row[0] if row else 0

    def get(self, owner, method, kind, target):
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            revision = self._relation_revision(db)
            return self._decode(db.execute("SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",
                                          (owner, method, kind, target)).fetchone(), revision)

    def newer_relations(self, owner, method, kind, version):
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            revision = self._relation_revision(db)
            return [(row["target"],self._decode(row, revision)) for row in db.execute(
                "SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND version>?",(owner,method,kind,version))]

    def many(self, owner, method, works, authors):
        result = {"bookmark": {}, "follow": {}}
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN")
            revision = self._relation_revision(db)
            for kind, targets in (("bookmark", works), ("follow", authors)):
                rows={}
                for offset in range(0,len(targets),400):
                    part=targets[offset:offset+400]
                    query="SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target IN ("+','.join('?' for _ in part)+')'
                    rows.update((row['target'],row) for row in db.execute(query,(owner,method,kind,*part)))
                for target in targets:
                    result[kind][target] = self._decode(rows.get(target), revision)
        return result

    @staticmethod
    def _put(db, owner, method, kind, target, state, version):
        db.execute("INSERT OR REPLACE INTO relations VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (owner, method, kind, target, int(state["active"]), state.get("privacy"),
                    json.dumps(state["tags"], ensure_ascii=False) if state.get("tags") is not None else None,
                    state.get("bookmarkId"), time.time(), version))

    def put(self, owner, method, kind, target, state, *, repair_id=None):
        with self._write() as db:
            version = self._relation_clock(db) + 1
            self._put(db, owner, method, kind, target, state, version)
            if repair_id is not None:
                db.execute("INSERT INTO page_repairs VALUES(?,?,?)", (owner, method, repair_id))
            current = self._decode(db.execute("SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",
                (owner, method, kind, target)).fetchone(), self._relation_revision(db))
        return current

    def put_verified(self, owner, method, peer, kind, target, state, repair_id, *, peer_observed):
        """Publish verified relationship facts to both methods in one transaction."""
        with self._write() as db:
            version = self._relation_clock(db) + 1
            revision = self._relation_revision(db)
            select = "SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target=?"
            prior = self._decode(db.execute(select, (owner, peer, kind, target)).fetchone(), revision)
            shared = {**state, "bookmarkId":None}
            if state["active"] and prior["active"]:
                for field in ("privacy", "tags"):
                    if shared.get(field) is None:
                        shared[field] = prior[field]
                # An App read does not carry the Web bookmark identifier.
                shared["bookmarkId"] = prior["bookmarkId"] if peer == "web" else None
            changes = {method: state}
            fields = ("stamp", "touched", "bookmarkId")
            peer_unchanged = all(prior.get(field) == peer_observed.get(field) for field in fields)
            if peer_unchanged and (not prior["known"] or relationship_stamp(shared) != prior["stamp"]
                                   or shared["bookmarkId"] != prior["bookmarkId"]):
                changes[peer] = shared
            elif peer_unchanged:
                # Reconfirming the same fact must also fence scans already in flight.
                db.execute("UPDATE relations SET version=?,touched=? WHERE owner=? AND method=? AND kind=? AND target=?",
                           (version, time.time(), owner, peer, kind, target))
            for destination, value in changes.items():
                self._put(db, owner, destination, kind, target, value, version)
                db.execute("INSERT INTO page_repairs VALUES(?,?,?)", (owner, destination, repair_id))
            revision = self._relation_revision(db)
            current = {destination: self._decode(db.execute(select, (owner, destination, kind, target)).fetchone(), revision)
                       for destination in changes}
            return current, prior

    def pending_page_repairs(self, owner, method):
        with self.lock, closing(self._connect()) as db:
            return [row[0] for row in db.execute("SELECT token FROM page_repairs WHERE owner=? AND method=?", (owner, method))]

    def finish_page_repairs(self, owner, method, tokens):
        with self._write() as db:
            db.executemany("DELETE FROM page_repairs WHERE owner=? AND method=? AND token=?",
                [(owner, method, token) for token in tokens])

    def recover_page_repairs(self, owner, method, tokens, kinds, *, legacy=False):
        with self._write() as db:
            for kind in kinds:
                row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?",
                                 (owner, method, kind)).fetchone()
                if row:
                    old = json.loads(row[0])
                    self._begin_scan(db, owner, method, kind, 0 if legacy else old["completed"],
                                     old.get("displayRevision"))
            db.executemany("DELETE FROM page_repairs WHERE owner=? AND method=? AND token=?",
                           ((owner, method, token) for token in tokens))

    def remember_active(self, owner, method, kind, targets, privacy):
        with self._write() as db:
            version=self._relation_clock(db) + 1
            for target in targets:
                found=db.execute("SELECT 1 FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",(owner,method,kind,target)).fetchone()
                if not found:self._put(db,owner,method,kind,target,{"active":True,"privacy":privacy,"tags":None},version)

    def operation(self, owner, method, request_id, signature, *, reserve=True):
        with self._write() as db:
            prepared = json.dumps("prepared")
            row = db.execute("SELECT signature,result FROM operations WHERE owner=? AND method=? AND request_id=?",
                             (owner, method, request_id)).fetchone()
            if row:
                if row[0] != signature:
                    raise ValueError("操作编号已用于另一项操作")
                if row[1] != prepared:
                    return True, json.loads(row[1]) if row[1] else None
                if reserve:
                    db.execute("UPDATE operations SET result=NULL,touched=? WHERE owner=? AND method=? AND request_id=?",
                               (time.time(), owner, method, request_id))
                return False, None
            now = time.time()
            # An unresolved receipt is the only record that this intent may
            # have reached Pixiv. Its age alone cannot authorize another write.
            db.execute("DELETE FROM operations WHERE result IS NOT NULL AND touched<?", (now - 7 * 86400,))
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)",
                       (owner, method, request_id, signature, None if reserve else prepared, now))
            return False, None

    def finish_operation(self, owner, method, request_id, state, *, preferences: tuple[str, dict] | None = None):
        with self._write() as db:
            row = db.execute("UPDATE operations SET result=?,touched=? WHERE owner=? AND method=? AND request_id=?",
                             (json.dumps(state), time.time(), owner, method, request_id))
            if row.rowcount != 1:
                raise ValueError("云端操作回执无法确认，请刷新核对")
            if preferences is not None:
                self._save_preferences(db, *preferences)
                return self._preferences_snapshot(db, preferences[0])

    def sync_status(self, owner, method):
        with self.lock, closing(self._connect()) as db:
            return {row["kind"]: json.loads(row["payload"]) for row in db.execute(
                "SELECT kind,payload FROM scans WHERE owner=? AND method=?", (owner, method))}

    def begin_cache_clear(self, owner):
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO cache_clear_pending VALUES(?)", (owner,))

    def cache_clear_pending(self, owner):
        with self.lock, closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM cache_clear_pending WHERE owner=?", (owner,)).fetchone() is not None

    def clear_account(self, owner):
        with self._write() as db:
            for table in ("relations", "scans", "scan_seen", "page_repairs"):
                db.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
            db.execute("DELETE FROM cache_clear_pending WHERE owner=?", (owner,))

    def begin_scan(self, owner, method, kind, *, display_revision=None):
        with self._write() as db:
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", (owner, method, kind)).fetchone()
            old = json.loads(row[0]) if row else {}
            return self._begin_scan(db, owner, method, kind, old.get("completed", 0), display_revision)

    @staticmethod
    def _begin_scan(db, owner, method, kind, completed, display_revision):
        state = {"id": uuid.uuid4().hex, "scope": "public", "cursor": "", "offset":0, "positions":{}, "finished":[],
                 "running": True, "completed": completed, "pageOffsets": {}, "visitedPaths": {},
                 "displayRevision": display_revision, "started": time.time(),
                 "version": CloudState._relation_clock(db)}
        db.execute("DELETE FROM scan_seen WHERE owner=? AND method=? AND kind=?", (owner, method, kind))
        db.execute("INSERT OR REPLACE INTO scans VALUES(?,?,?,?)", (owner, method, kind, json.dumps(state)))
        return state

    @staticmethod
    def _unseen_targets(db, owner, method, kind, scan, limit):
        return [row[0] for row in db.execute(
            "SELECT target FROM relations WHERE owner=? AND method=? AND kind=? AND active AND version<=? "
            "AND target>? AND NOT EXISTS (SELECT 1 FROM scan_seen AS seen WHERE seen.owner=relations.owner "
            "AND seen.method=relations.method AND seen.kind=relations.kind AND seen.target=relations.target) "
            "ORDER BY target LIMIT ?", (owner, method, kind, scan["version"], scan.get("verifiedThrough", ""), limit))]

    def scan_verification_targets(self, owner, method, kind, scan):
        with self.lock, closing(self._connect()) as db:
            return self._unseen_targets(db, owner, method, kind, scan, 4)

    def note_unusable_cursor(self, owner, method, kind, scan):
        with self._write() as db:
            key = owner, method, kind
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", key).fetchone()
            if not row:
                return
            current = json.loads(row[0])
            if (not current["running"] or current.get("pendingPage")
                    or any(current[field] != scan[field] for field in ("id", "scope", "cursor", "offset"))):
                return
            # One invalid response can be temporary. Restart only when the
            # same persisted continuation fails again.
            if current.get("cursorFailed"):
                self._begin_scan(db, *key, current["completed"], current.get("displayRevision"))
            else:
                current["cursorFailed"] = True
                db.execute("UPDATE scans SET payload=? WHERE owner=? AND method=? AND kind=?",
                           (json.dumps(current), *key))

    def _complete_scan(self, db, owner, method, kind, scan):
        if self._unseen_targets(db, owner, method, kind, scan, 1):
            return
        scan.pop("verifiedThrough", None)
        scan.update(running=False, completed=time.time(), cursor="")
        db.execute("DELETE FROM scan_seen WHERE owner=? AND method=? AND kind=?", (owner, method, kind))

    def scan_page(self, owner, method, kind, scan, items, next_cursor, *, repair_id=None):
        with self._write() as db:
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", (owner, method, kind)).fetchone()
            current = json.loads(row[0]) if row else None
            if (current is None or not current["running"] or current.get("id") != scan["id"] or current.get("pendingPage")
                    or any(current[key] != scan[key] for key in ("scope", "cursor", "offset"))):
                raise ScanConflictError("同步批次已更新，请重新同步")
            for target, state in items:
                db.execute("INSERT OR IGNORE INTO scan_seen VALUES(?,?,?,?)", (owner, method, kind, target))
                old = db.execute("SELECT version,tags FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",
                                 (owner, method, kind, target)).fetchone()
                if old and old[0] > scan["version"]:
                    continue
                if state.get("tags") is None and old and old[1] is not None:
                    state = {**state, "tags": json.loads(old[1])}
                self._put(db, owner, method, kind, target, state, scan["version"])
            updated = dict(scan)
            updated.pop("cursorFailed", None)
            offsets = {scope: list(values) for scope, values in scan.get("pageOffsets", {}).items()}
            offsets.setdefault(scan["scope"], []).append(scan["offset"])
            paths = {scope: list(values) for scope, values in scan.get("visitedPaths", {}).items()}
            paths.setdefault(scan["scope"], []).append(scan["cursor"])
            updated.update(pageOffsets=offsets, visitedPaths=paths)
            positions=dict(scan.get("positions",{}));finished=list(scan.get("finished",[]))
            if next_cursor is not None:
                if next_cursor == scan["cursor"]:
                    raise ValueError("云端列表续页未前进")
                positions[scan["scope"]] = {"cursor":next_cursor,"offset":scan.get("offset",0)+len(items)}
            else:
                finished.append(scan["scope"])
            updated.update(positions=positions,finished=finished)
            if len(finished)<2:
                other="private" if scan["scope"]=="public" else "public"
                next_scope=other if other not in finished else scan["scope"]
                updated.update(scope=next_scope,**positions.get(next_scope,{"cursor":"","offset":0}))
            elif repair_id is None:
                self._complete_scan(db, owner, method, kind, updated)
            if repair_id is not None:
                updated["pendingPage"] = repair_id
                db.execute("INSERT INTO page_repairs VALUES(?,?,?)", (owner, method, repair_id))
            db.execute("INSERT OR REPLACE INTO scans VALUES(?,?,?,?)", (owner, method, kind, json.dumps(updated)))
            return updated

    def publish_scan_page(self, owner, method, kind, repair_id):
        with self._write() as db:
            key = owner, method, kind
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", key).fetchone()
            state = json.loads(row[0]) if row else {}
            if state.pop("pendingPage", None) != repair_id:
                raise ScanConflictError("同步页已更新，请重新同步")
            if len(state["finished"]) == 2:
                self._complete_scan(db, *key, state)
            db.execute("UPDATE scans SET payload=? WHERE owner=? AND method=? AND kind=?", (json.dumps(state), *key))
            db.execute("DELETE FROM page_repairs WHERE owner=? AND method=? AND token=?", (owner, method, repair_id))
            return state

    def verify_scan_targets(self, owner, method, kind, scan, outcomes):
        with self._write() as db:
            key = owner, method, kind
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", key).fetchone()
            current = json.loads(row[0]) if row else {}
            if (not current.get("running") or current["id"] != scan["id"] or current.get("pendingPage")
                    or len(current["finished"]) != 2 or current.get("verifiedThrough") != scan.get("verifiedThrough")):
                raise ScanConflictError("同步进度已更新，请重新同步")
            for target, observed in outcomes:
                if observed is None:
                    continue
                old = db.execute("SELECT version,privacy,tags,bookmark_id FROM relations "
                                 "WHERE owner=? AND method=? AND kind=? AND target=? AND active", (*key, target)).fetchone()
                if old is None or old["version"] > scan["version"]:
                    continue
                if observed["active"]:
                    observed = {**observed, "privacy": observed["privacy"] or old["privacy"]}
                    if observed.get("tags") is None:
                        observed["tags"] = json.loads(old["tags"]) if old["tags"] is not None else None
                    if observed.get("bookmarkId") is None:
                        observed["bookmarkId"] = old["bookmark_id"]
                self._put(db, *key, target, observed, scan["version"])
            if outcomes:
                current["verifiedThrough"] = outcomes[-1][0]
            self._complete_scan(db, *key, current)
            db.execute("UPDATE scans SET payload=? WHERE owner=? AND method=? AND kind=?", (json.dumps(current), *key))
            return current
