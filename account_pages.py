"""Read-only Pixiv account pages and per-account observed-work markers."""
from collections import OrderedDict
from contextlib import closing
from contextvars import ContextVar
import copy
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from pixiv_account import AccountError, AppAccount, AppTransport, CredentialVault, ACCOUNT_REQUEST_CHECK
from pixiv_app_config import CLIENT_CONFIG
from pixiv_adapter import (PixivPolicyError, normalize_search_item, is_allowed_pixiv_url,
    build_user_profile_all_url,build_user_profile_works_url,valid_pixiv_id)
from pixiv_app_gallery import normalize_work, artist_profile, continuation
from artist_service import normalize_artist

ACCOUNT_CACHE_ONLY = ContextVar("account_cache_only", default=False)


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
            uid = str(props.get("gaUserData", {}).get("userId", ""))
        except (ValueError, KeyError, TypeError) as error:
            raise AccountError("web_identity", "暂时无法确认官网账号，请重试") from error
        if props.get("isLoggedIn") is not True or not re.fullmatch(r"[0-9]{1,30}", uid):
            raise AccountError("web_identity", "请先连接 Pixiv 网页账号")
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
        connection.execute("CREATE TABLE IF NOT EXISTS author_seen (owner TEXT, author TEXT, seen TEXT, observed TEXT, touched REAL, PRIMARY KEY(owner,author))")
        return connection

    def observe(self, owner, dates):
        markers = {}
        with self.lock, closing(self._connect()) as connection, connection:
            for author, stamp in dates.items():
                row = connection.execute("SELECT seen,observed FROM author_seen WHERE owner=? AND author=?", (owner, author)).fetchone()
                if row is None:
                    connection.execute("INSERT INTO author_seen VALUES(?,?,?,?,?)", (owner, author, stamp, stamp, time.time()))
                    markers[author] = False
                else:
                    observed = max(stamp, row[1])
                    connection.execute("UPDATE author_seen SET observed=?,touched=? WHERE owner=? AND author=?", (observed, time.time(), owner, author))
                    markers[author] = observed > row[0]
        return markers

    def mark_seen(self, owner, author):
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("UPDATE author_seen SET seen=observed,touched=? WHERE owner=? AND author=?", (time.time(), owner, author))

    def clear(self, owner):
        with self.lock, closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM author_seen WHERE owner=?", (owner,))


class AccountCache:
    """Account metadata survives navigation/restarts; image capabilities are added only at response time."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("CREATE TABLE IF NOT EXISTS cached (owner TEXT, method TEXT, key TEXT, payload TEXT, PRIMARY KEY(owner,method,key))")
        return connection

    def read(self, owner, method, key):
        with self.lock, closing(self._connect()) as db:
            row = db.execute("SELECT payload FROM cached WHERE owner=? AND method=? AND key=?",
                             (owner, method, json.dumps(key))).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, owner, method, key, value):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO cached VALUES(?,?,?,?)",
                       (owner, method, json.dumps(key), json.dumps(value, ensure_ascii=False)))

    def clear(self, owner):
        with self.lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM cached WHERE owner=?", (owner,))

    def entries(self, owner, method):
        with self.lock, closing(self._connect()) as db:
            return [(json.loads(key),json.loads(payload)) for key,payload in db.execute(
                "SELECT key,payload FROM cached WHERE owner=? AND method=? AND key LIKE ?", (owner,method,'["data",%'))]

    def remove(self, owner, method, keys):
        with self.lock, closing(self._connect()) as db, db:
            db.executemany("DELETE FROM cached WHERE owner=? AND method=? AND key=?",[(owner,method,json.dumps(key)) for key in keys])


class AccountPages:
    def __init__(self, *, directory, request_json, request_homepage, generation, coordinator, proxy, method=lambda:"web"):
        self.directory = Path(directory)
        self.request_json, self.request_homepage = request_json, request_homepage
        self.generation, self.coordinator, self.proxy = generation, coordinator, proxy
        self.method = method
        self._account = None
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._cursors = OrderedDict()
        self._cache_revision = 0
        self._loads = {}
        self._versions = {}
        self.visits = VisitStore(self.directory / "account-browse.sqlite3")
        self.saved_cache = AccountCache(self.directory / "account-cache.sqlite3")
        self.auth_status = {"pending": False, "error": ""}

    def account(self):
        with self._lock:
            if self._account is None:
                self._account = AppAccount(CredentialVault(), AppTransport(self.directory / "request-settings.json", self.proxy(), coordinator=self.coordinator), CLIENT_CONFIG)
            self._account.transport.proxy = self.proxy()
            return self._account

    def check(self, generation):
        ACCOUNT_REQUEST_CHECK.get()()
        if self.generation() != generation:
            raise AccountError("revoked", "账号已改变，请重新打开我的账户")

    def invalidate(self):
        with self._lock:
            self._cache_revision += 1
            self._cache.clear()
            self._cursors.clear()
            account = self._account
        if account:
            account.fence()

    def disconnect(self):
        self.invalidate()
        with self._lock:
            account=self._account
        if account is not None:
            account.logout()
        else:
            CredentialVault().delete()

    def close(self):
        if self._account:
            self._account.transport.close()

    def _cached(self, key, load, refresh=False):
        if ACCOUNT_CACHE_ONLY.get():
            return self._load_cached(key, load, refresh)
        if not refresh:
            token=ACCOUNT_CACHE_ONLY.set(True)
            try:
                return self._load_cached(key,load)
            except AccountError as error:
                if error.kind!="cache_miss":raise
            finally:
                ACCOUNT_CACHE_ONLY.reset(token)
        with self._lock:
            pending = self._loads.setdefault(key, threading.RLock())
        with pending:
            return self._load_cached(key, load, refresh)

    def _load_cached(self, key, load, refresh=False):
        persistent = len(key) > 2
        method = self.method()
        with self._lock:
            cache_revision = self._cache_revision
            version = self._versions.get(key,0)
            cached = self._cache.get(key)
            if cached and not refresh:
                self._cache.move_to_end(key)
                return copy.deepcopy(cached[1])
        if persistent and not refresh:
            cached = self.saved_cache.read(str(key[2]), method, ["data", *key[1:]])
            if cached is not None:
                self.check(key[0])
                with self._lock:
                    if cache_revision!=self._cache_revision:raise AccountError("cache_cleared","账号缓存已清除，请重新打开页面")
                    if version!=self._versions.get(key,0):
                        current=self._cache.get(key)
                        if current:return copy.deepcopy(current[1])
                        raise AccountError("cache_changed","账号列表已更新，请重新打开页面")
                    self._cache[key] = (time.monotonic(), copy.deepcopy(cached))
                return cached
        if ACCOUNT_CACHE_ONLY.get():
            raise AccountError("cache_miss", "账号数据尚未在本地准备好")
        result = load()
        self.check(key[0])
        with self._lock:
            if cache_revision != self._cache_revision:
                raise AccountError("cache_cleared", "账号缓存已清除，请重新打开页面")
            if version!=self._versions.get(key,0):
                current=self._cache.get(key)
                if current:return copy.deepcopy(current[1])
                raise AccountError("cache_changed","账号列表已更新，请重新打开页面")
            self._cache[key] = (time.monotonic(), copy.deepcopy(result))
            self._cache.move_to_end(key)
            if persistent:
                self.saved_cache.save(str(key[2]), method, ["data", *key[1:]], result)
        return result

    def clear_cache(self, owner):
        with self._lock:
            self._cache_revision += 1
            self._cache = OrderedDict((key, value) for key, value in self._cache.items() if key[1] == "owner")
            self._cursors.clear()
            self.saved_cache.clear(owner)
            self.visits.clear(owner)

    def cache_scan_page(self, owner, method, kind, scope, offset, rows, next_cursor, total, generation):
        """The same scan response supplies relationship state and display pages."""
        view = "bookmarks" if kind == "bookmark" else "following"
        next_offset = offset + len(rows) if next_cursor is not None else None
        modes = ("safe", "all", "r18") if kind == "bookmark" else ("all",)
        pages = []
        for mode in modes:
            page = self.relation_page(method, kind, rows, offset=offset, next_offset=next_offset, total=total, mode=mode)
            key = (generation, view, owner, scope, offset, "", mode) if kind == "bookmark" else (generation, view, owner, scope, offset)
            pages.append((key, page))
        with self._lock:
            self.check(generation)
            for key, page in pages:
                self._versions[key]=self._versions.get(key,0)+1
                self.saved_cache.save(owner, method, ["data", *key[1:]], page)
                self._cache[key] = (time.monotonic(), copy.deepcopy(page))
            if method == "app" and next_cursor is not None:
                path = "/v1/user/bookmarks/illust" if kind == "bookmark" else "/v1/user/following"
                params = {"user_id":owner, "restrict":scope, "filter":"for_android"}
                cursor_key = (generation,path,tuple(sorted(params.items())))
                self._cursors[cursor_key,next_offset] = next_cursor
                self.saved_cache.save(owner,method,["cursor",path,sorted(params.items()),next_offset],next_cursor)

    @staticmethod
    def relation_page(method, kind, rows, *, offset, next_offset, total=None, mode="safe"):
        """Normalize remote display rows once, shared by browsing and cloud scanning."""
        if kind == "bookmark":
            items=[];unavailable=0
            for row in rows:
                try:
                    item = normalize_work(row,allow_r18=mode!="safe") if method=="app" else normalize_search_item(row,allow_r18=mode!="safe")
                except PixivPolicyError:
                    unavailable+=1;continue
                if mode!="r18" or item["restriction"]=="r18":items.append(item)
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
        with self._lock:
            self.check(generation)
            obsolete=[key for key,_ in self.saved_cache.entries(owner,method) if key[1]==view and
                (key[4] not in offsets.get(key[3],[]) or kind=="bookmark" and key[5])]
            self.saved_cache.remove(owner,method,obsolete)
            for key in obsolete:
                memory_key=(generation,*key[1:])
                self._cache.pop(memory_key,None)
                self._versions[memory_key]=self._versions.get(memory_key,0)+1

    def note_relation(self, owner, method, kind, target, before, after):
        """Patch cached pages after a confirmed write; do not invalidate unrelated pages."""
        view, field = ("bookmarks", "items") if kind == "bookmark" else ("following", "people")
        entries = self.saved_cache.entries(owner, method)
        record = next((row for key,page in entries for row in page.get(field,[]) if row["id"]==target),None)
        for key,page in entries:
            changed = False
            if key[1] == view:
                scope,offset = key[3:5]
                fits = after["active"] and (after["privacy"] is None or after["privacy"]==scope)
                if kind == "bookmark":
                    tag,mode = key[5:7]
                    fits = fits and (not tag or after["tags"] is None or tag in after["tags"]) and (
                        record is None or mode=="all" or (mode=="r18")== (record["restriction"]=="r18"))
                rows = page[field]
                updated = [row for row in rows if row["id"]!=target or fits]
                if fits and offset==0 and record and not any(row["id"]==target for row in updated):updated.insert(0,record)
                if updated!=rows:
                    page[field]=updated
                    if page.get("total") is not None:page["total"]+=len(updated)-len(rows)
                    changed=True
            elif key[1]=="followers" and kind=="follow":
                for person in page["people"]:
                    if person["id"]==target and person["following"]!=after["active"]:
                        person["following"]=after["active"];changed=True
            elif key[1] in {"profile","profile-web"}:
                count = "following" if kind=="follow" else "publicBookmarks"
                delta = int(after["active"])-int(before["active"]) if kind=="follow" else (
                    int(after["active"] and after["privacy"]=="public")-int(before["active"] and before["privacy"]=="public"))
                if delta and page.get(count) is not None:page[count]=max(0,page[count]+delta);changed=True
            elif key[1]=="tags" and kind=="bookmark":
                for scope in ("public","private"):
                    old=set(before["tags"] or []) if before["active"] and before["privacy"]==scope else set()
                    new=set(after["tags"] or []) if after["active"] and after["privacy"]==scope else set()
                    for name in old|new:
                        delta=int(name in new)-int(name in old)
                        row=next((row for row in page[scope] if row["name"]==name),None)
                        if row:row["count"]=max(0,row["count"]+delta)
                        elif delta>0:page[scope].append({"name":name,"count":delta})
                    page[scope]=[row for row in page[scope] if row["count"]>0]
                changed=True
            if changed:
                self.saved_cache.save(owner,method,key,page)
                memory_key=(self.generation(),*key[1:])
                with self._lock:
                    if memory_key in self._cache:self._cache[memory_key]=(time.monotonic(),copy.deepcopy(page))

    def web_owner(self, generation):
        def load():
            parser = HomepageIdentity()
            parser.feed(self.request_homepage())
            return parser.user_id()
        return self._cached((generation, "owner"), load)

    def profile(self, generation, refresh=False):
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
            return {**self._cached((generation,"profile-web",owner),load_web,refresh),"authorization":{"pending":False,"error":""}}
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
            account.promote_verified()
            return result
        result = self._cached((generation, "profile", owner), load, refresh)
        return {**result, "authorization": dict(self.auth_status)}

    def latest_dates(self, generation, owner, scope, refresh=False):
        def load():
            raw = self.account().request("GET", "/v2/illust/follow?" + urlencode({"restrict": "private" if scope == "private" else "public"}))
            works = raw.get("illusts")
            if not isinstance(works, list):
                raise AccountError("invalid_data", "关注更新信息格式异常")
            dates = {}
            for work in works:
                if not isinstance(work, dict) or not isinstance(work.get("user"), dict):
                    continue
                author, stamp = str(work["user"].get("id", "")), work.get("create_date")
                if not re.fullmatch(r"[0-9]{1,30}", author) or not isinstance(stamp, str):
                    continue
                try:
                    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        continue
                    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
                except ValueError:
                    continue
                dates[author] = max(dates.get(author, ""), canonical)
            return dates
        return self._cached((generation, "updates", owner, scope), load, refresh)

    def read(self, kind, *, scope="public", offset=0, tag="", generation, allow_r18=False, refresh=False, mode=None):
        mode = mode or ("all" if allow_r18 else "safe")
        allow_r18 = mode != "safe"
        profile = self.profile(generation, refresh=refresh and kind == "profile")
        if not profile["connected"] or kind == "profile":
            return profile
        owner = profile["uid"]
        if kind == "following":
            result = self._cached((generation, kind, owner, scope, offset), lambda: self._app_people(owner,scope,offset,generation) if self.method()=="app" else self._people(owner, scope, offset), refresh)
            try:
                dates = self.latest_dates(generation, owner, scope, refresh) if self.method()=="app" else {}
                markers = self.visits.observe(owner, {person["id"]: dates[person["id"]] for person in result["people"] if person["id"] in dates})
                for person in result["people"]:
                    person["isNew"] = markers.get(person["id"], False)
                    person["latestObserved"] = dates.get(person["id"], "")
            except (PixivPolicyError, OSError, TimeoutError, ConnectionError) as error:
                if not isinstance(error,AccountError) or error.kind!="cache_miss":
                    result["updatesError"] = "更新信息暂未读取，可稍后刷新"
                self.check(generation)
        elif kind == "followers":
            if self.method()=="web":
                raise AccountError("capability","网页登录暂不提供粉丝列表，可在资料中选择 App 授权。")
            result = self._cached((generation, kind, owner, offset), lambda: self._followers(owner, offset), refresh)
        elif kind == "bookmarks":
            result = self._cached((generation, kind, owner, scope, offset, tag, mode), lambda: self._app_bookmarks(owner,scope,offset,tag,allow_r18,generation) if self.method()=="app" else self._bookmarks(owner, scope, offset, tag, allow_r18), refresh)
        elif kind == "tags":
            result = self._cached((generation, kind, owner), lambda: self._app_tags() if self.method()=="app" else self._tags(owner), refresh)
        elif kind == "updates":
            result={"dates":self.latest_dates(generation,owner,scope,refresh)} if self.method()=="app" else {"dates":{}}
        elif kind == "works":
            result=self._cached((generation,kind,owner,mode),lambda:self._own_works(owner,profile["workCount"],allow_r18,mode),refresh)
        else:
            raise AccountError("invalid_request", "账户页面不存在")
        if mode=="r18" and "items" in result:
            result["items"]=[item for item in result["items"] if item["restriction"]=="r18"]
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
                try:item=normalize_search_item(value,allow_r18=allow_r18)
                except PixivPolicyError:continue
                if item["userId"]==owner:rows.append(item)
        unique={item["id"]:item for item in rows if mode!="r18" or item["restriction"]=="r18"}
        return {"items":sorted(unique.values(),key=lambda item:(item["sortDate"],int(item["id"])),reverse=True)[:6]}

    def _app_page(self,path,params,offset,generation,rows_key):
        key=(generation,path,tuple(sorted(params.items())))
        with self._lock:
            cache_revision=self._cache_revision
            cursor=self._cursors.get((key,offset)) if offset else path+"?"+urlencode(params)
        if not cursor and offset:
            cursor=self.saved_cache.read(str(params["user_id"]),self.method(),["cursor",path,sorted(params.items()),offset])
        if not cursor:raise AccountError("cursor","分页记录已清理，请返回第一页刷新")
        raw=self.account().request("GET",cursor)
        rows=raw.get(rows_key)
        if not isinstance(rows,list):raise AccountError("invalid_data","App 列表格式异常")
        next_path=continuation(raw,path)
        if next_path:
            values=parse_qs(urlsplit(next_path).query)
            if any(values.get(name)!=[params[name]] for name in ("user_id","restrict") if name in params):
                raise AccountError("cursor","App 续页账号或公开范围不匹配")
            if "tag" in params and "tag" in values and values["tag"]!=[params["tag"]]:
                raise AccountError("cursor","App 续页收藏标签不匹配")
        next_offset=offset+max(1,len(rows)) if next_path else None
        if next_path:
            if next_path==cursor:raise AccountError("cursor","App 续页未前进")
            with self._lock:
                if cache_revision!=self._cache_revision:raise AccountError("cache_cleared","账号缓存已清除，请重新打开页面")
                self._cursors[key,next_offset]=next_path
                self.saved_cache.save(str(params["user_id"]),self.method(),["cursor",path,sorted(params.items()),next_offset],next_path)
        return rows,next_offset

    def _app_people(self,owner,scope,offset,generation):
        rows,next_offset=self._app_page("/v1/user/following",{"user_id":owner,"restrict":scope,"filter":"for_android"},offset,generation,"user_previews")
        return self.relation_page("app","follow",rows,offset=offset,next_offset=next_offset)

    def _app_bookmarks(self,owner,scope,offset,tag,allow_r18,generation):
        params={"user_id":owner,"restrict":scope,"filter":"for_android"}
        if tag:params["tag"]=tag
        rows,next_offset=self._app_page("/v1/user/bookmarks/illust",params,offset,generation,"illusts")
        return self.relation_page("app","bookmark",rows,offset=offset,next_offset=next_offset,mode="all" if allow_r18 else "safe")

    def _app_tags(self):
        result={}
        for scope in ("public","private"):
            path="/v1/user/bookmark-tags/illust?"+urlencode({"restrict":scope});tags=[]
            while path and len(tags)<300:
                raw=self.account().request("GET",path)
                rows=raw.get("bookmark_tags")
                if not isinstance(rows,list):raise AccountError("invalid_data","App 收藏标签格式异常")
                for row in rows[:300-len(tags)]:
                    if not isinstance(row,dict) or not isinstance(row.get("name"),str):raise AccountError("invalid_data","App 收藏标签内容异常")
                    tags.append({"name":row["name"],"count":source_total(row.get("count"))})
                following=continuation(raw,"/v1/user/bookmark-tags/illust")
                if following==path or following and not rows:raise AccountError("cursor","收藏标签续页未前进")
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

    def _bookmarks(self, owner, scope, offset, tag, allow_r18):
        body = source_body(self.request_json("https://www.pixiv.net/ajax/user/" + owner + "/illusts/bookmarks?" + urlencode({"tag": tag, "offset": offset, "limit": 36, "rest": "hide" if scope == "private" else "show", "lang": "zh"})))
        rows = body.get("works")
        if not isinstance(rows, list):
            raise AccountError("invalid_data", "收藏作品列表格式异常")
        total = source_total(body.get("total"))
        return self.relation_page("web","bookmark",rows,offset=offset,next_offset=offset+len(rows) if rows and offset+len(rows)<total else None,total=total,mode="all" if allow_r18 else "safe")

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

    def seen(self, generation, author):
        profile = self.profile(generation)
        if not profile["connected"]:
            raise AccountError("reauth", "请先连接账号功能")
        self.visits.mark_seen(profile["uid"], author)
