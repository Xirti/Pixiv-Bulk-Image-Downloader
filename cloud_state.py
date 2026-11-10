"""Small durable relationship index; it never stores image data or credentials."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time


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

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.executescript("""
          CREATE TABLE IF NOT EXISTS preferences(scope TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS relations(owner TEXT, method TEXT, kind TEXT, target TEXT,
            active INTEGER NOT NULL, privacy TEXT, tags TEXT, bookmark_id TEXT, touched REAL,
            version INTEGER NOT NULL, PRIMARY KEY(owner,method,kind,target));
          CREATE TABLE IF NOT EXISTS scans(owner TEXT, method TEXT, kind TEXT, payload TEXT NOT NULL,
            PRIMARY KEY(owner,method,kind));
          CREATE TABLE IF NOT EXISTS scan_seen(owner TEXT, method TEXT, kind TEXT, target TEXT,
            PRIMARY KEY(owner,method,kind,target));
          CREATE TABLE IF NOT EXISTS operations(owner TEXT, method TEXT, request_id TEXT, signature TEXT,
            result TEXT, touched REAL, PRIMARY KEY(owner,method,request_id));
        """)
        return db

    def preferences(self, scope):
        with self.lock, closing(self._connect()) as db:
            values = {}
            for key in ("machine", scope):
                row = db.execute("SELECT payload FROM preferences WHERE scope=?", (key,)).fetchone()
                if row:
                    values.update(validate_preferences(json.loads(row[0])))
            return {**DEFAULT_MACHINE, **DEFAULT_ACCOUNT, **values}

    def save_preferences(self, scope, changes):
        # Caller has validated the external patch once.
        with self.lock, closing(self._connect()) as db, db:
            for key, fields in (("machine", DEFAULT_MACHINE), (scope, DEFAULT_ACCOUNT)):
                patch = {k: v for k, v in changes.items() if k in fields}
                if not patch:
                    continue
                row = db.execute("SELECT payload FROM preferences WHERE scope=?", (key,)).fetchone()
                value = json.loads(row[0]) if row else {}
                value.update(patch)
                db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value)))
        return self.preferences(scope)

    @staticmethod
    def _decode(row):
        if row is None:
            return {"known": False, "active": None, "privacy": None, "tags": None, "touched": None}
        value = {"known": True, "active": bool(row["active"]), "privacy": row["privacy"],
                 "tags": json.loads(row["tags"]) if row["tags"] is not None else None,
                 "bookmarkId": row["bookmark_id"], "touched": row["touched"]}
        value["stamp"] = relationship_stamp(value)
        return value

    def get(self, owner, method, kind, target):
        with self.lock, closing(self._connect()) as db:
            return self._decode(db.execute("SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",
                                          (owner, method, kind, target)).fetchone())

    def newer_relations(self, owner, method, kind, version):
        with self.lock, closing(self._connect()) as db:
            return [(row["target"],self._decode(row)) for row in db.execute(
                "SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND version>?",(owner,method,kind,version))]

    def many(self, owner, method, works, authors):
        result = {"bookmark": {}, "follow": {}}
        with self.lock, closing(self._connect()) as db:
            scans={row["kind"]:row["completed"] or 0 for row in db.execute(
                "SELECT kind,json_extract(payload,'$.completed') AS completed FROM scans WHERE owner=? AND method=?",(owner,method))}
            for kind, targets in (("bookmark", works), ("follow", authors)):
                completed=scans.get(kind,0);rows={}
                for offset in range(0,len(targets),400):
                    part=targets[offset:offset+400]
                    query="SELECT * FROM relations WHERE owner=? AND method=? AND kind=? AND target IN ("+','.join('?' for _ in part)+')'
                    rows.update((row['target'],row) for row in db.execute(query,(owner,method,kind,*part)))
                for target in targets:
                    state = self._decode(rows.get(target))
                    if not state["known"] and completed:
                        state = {"known": True, "active": False, "privacy": None, "tags": [], "touched": completed}
                        state["stamp"] = relationship_stamp(state)
                    result[kind][target] = state
        return result

    @staticmethod
    def _put(db, owner, method, kind, target, state, version):
        db.execute("INSERT OR REPLACE INTO relations VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (owner, method, kind, target, int(state["active"]), state.get("privacy"),
                    json.dumps(state["tags"], ensure_ascii=False) if state.get("tags") is not None else None,
                    state.get("bookmarkId"), time.time(), version))

    def put(self, owner, method, kind, target, state):
        with self.lock, closing(self._connect()) as db, db:
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM relations").fetchone()[0]
            self._put(db, owner, method, kind, target, state, version)
        return self.get(owner, method, kind, target)

    def remember_active(self, owner, method, kind, targets, privacy):
        with self.lock, closing(self._connect()) as db, db:
            scan=db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?",(owner,method,kind)).fetchone()
            if scan and json.loads(scan[0]).get("completed"):
                return
            version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM relations").fetchone()[0]
            for target in targets:
                found=db.execute("SELECT 1 FROM relations WHERE owner=? AND method=? AND kind=? AND target=?",(owner,method,kind,target)).fetchone()
                if not found:self._put(db,owner,method,kind,target,{"active":True,"privacy":privacy,"tags":None},version)

    def operation(self, owner, method, request_id, signature):
        with self.lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT signature,result FROM operations WHERE owner=? AND method=? AND request_id=?",
                             (owner, method, request_id)).fetchone()
            if row:
                if row[0] != signature:
                    raise ValueError("操作编号已用于另一项操作")
                return True, json.loads(row[1]) if row[1] else None
            db.execute("DELETE FROM operations WHERE touched<?", (time.time() - 7 * 86400,))
            db.execute("INSERT INTO operations VALUES(?,?,?,?,NULL,?)", (owner, method, request_id, signature, time.time()))
            return False, None

    def finish_operation(self, owner, method, request_id, state):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("UPDATE operations SET result=? WHERE owner=? AND method=? AND request_id=?",
                       (json.dumps(state), owner, method, request_id))

    def sync_status(self, owner, method):
        with self.lock, closing(self._connect()) as db:
            return {row["kind"]: json.loads(row["payload"]) for row in db.execute(
                "SELECT kind,payload FROM scans WHERE owner=? AND method=?", (owner, method))}

    def clear_account(self, owner):
        with self.lock, closing(self._connect()) as db, db:
            for table in ("relations", "scans", "scan_seen"):
                db.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))

    def begin_scan(self, owner, method, kind):
        with self.lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT payload FROM scans WHERE owner=? AND method=? AND kind=?", (owner, method, kind)).fetchone()
            old = json.loads(row[0]) if row else {}
            # A new process/read session starts a fresh bounded sweep. Never delete from a partial sweep.
            state = {"scope": "public", "cursor": "", "offset":0, "positions":{}, "finished":[], "running": True, "completed": old.get("completed", 0),
                     "started": time.time(), "version": db.execute("SELECT COALESCE(MAX(version),0) FROM relations").fetchone()[0]}
            db.execute("DELETE FROM scan_seen WHERE owner=? AND method=? AND kind=?", (owner, method, kind))
            db.execute("INSERT OR REPLACE INTO scans VALUES(?,?,?,?)", (owner, method, kind, json.dumps(state)))
            return state

    def scan_page(self, owner, method, kind, scan, items, next_cursor):
        with self.lock, closing(self._connect()) as db, db:
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
            else:
                updated.update(running=False, completed=time.time(), cursor="")
                db.execute("UPDATE relations SET active=0,privacy=NULL,tags='[]',bookmark_id=NULL,touched=? "
                           "WHERE owner=? AND method=? AND kind=? AND version<=? AND target NOT IN "
                           "(SELECT target FROM scan_seen WHERE owner=? AND method=? AND kind=?)",
                           (updated["completed"], owner, method, kind, scan["version"], owner, method, kind))
                db.execute("DELETE FROM scan_seen WHERE owner=? AND method=? AND kind=?", (owner, method, kind))
            db.execute("INSERT OR REPLACE INTO scans VALUES(?,?,?,?)", (owner, method, kind, json.dumps(updated)))
            return updated
