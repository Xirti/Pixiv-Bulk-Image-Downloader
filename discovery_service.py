"""Ordered discovery snapshots. Metadata and full continuation context, never credentials/capabilities."""
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import re
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import uuid

from account_pages import source_body
from pixiv_account import AccountError
from pixiv_adapter import (PixivPolicyError, normalize_search_item, valid_pixiv_id,
                           small_full_preview_url, publication_date, publication_timestamp)
from pixiv_app_gallery import normalize_work, continuation, proxy_image

RANKS = {"day":"daily", "week":"weekly", "month":"monthly", "week_rookie":"rookie", "week_original":"original"}


class DiscoveryInputError(ValueError):
    """Invalid caller parameters, distinct from upstream data/auth failures."""


def normalize_web_rank(raw):
    # ranking.php has a different contract from search. Fixed non-adult ranking modes
    # plus its explicit visibility flags own this boundary; never fabricate search-policy fields.
    if not isinstance(raw, dict) or raw.get("is_masked") is not False or raw.get("bookmarkable") is not True:
        raise PixivPolicyError("榜单作品不可展示")
    identifier, author = str(raw.get("illust_id", "")), str(raw.get("user_id", ""))
    if not valid_pixiv_id(identifier) or not valid_pixiv_id(author):
        raise PixivPolicyError("榜单作品身份无效")
    kind = str(raw.get("illust_type", ""))
    dimensions = []
    for field in ("width","height","illust_page_count","rank"):
        value=raw.get(field)
        if isinstance(value,str) and re.fullmatch(r"[1-9][0-9]{0,8}",value):value=int(value)
        dimensions.append(value)
    if kind not in {"0", "1", "2"} or any(type(n) is not int or n < 1 for n in dimensions):
        raise PixivPolicyError("榜单作品尺寸或名次无效")
    source = raw.get("url")
    proxy_image(source)
    if not re.match(re.escape(identifier) + r"(?:_|-)", urlsplit(source).path.rsplit("/", 1)[-1]):
        raise PixivPolicyError("榜单缩略图身份不符")
    tags = raw.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise PixivPolicyError("榜单标签无效")
    stamp = raw.get("illust_upload_timestamp")
    if isinstance(stamp,str) and re.fullmatch(r"[0-9]{1,11}",stamp):stamp=int(stamp)
    date = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if type(stamp) is int and 0 <= stamp <= 32503680000 else ""
    return {"id":identifier,"source":"pixiv","restriction":"safe","title":str(raw.get("title") or "未命名作品"),
            "artist":str(raw.get("user_name") or "未知画师"),"userId":author,"width":dimensions[0],"height":dimensions[1],
            "pages":dimensions[2],"rank":dimensions[3],"rankKind":"official","tags":tags,"bookmarks":None,
            "date":publication_date(date),"sortDate":publication_timestamp(date),"description":"",
            "workType":{"0":"illustration","1":"manga","2":"ugoira"}[kind],"aiGenerated":False,
            "thumb":proxy_image(small_full_preview_url(source)),"qualities":[],"formats":[]}


class DiscoveryService:
    def __init__(self, *, pages, context, follow_states):
        self.pages, self.context, self.follow_states = pages, context, follow_states
        self._guard = threading.RLock()
        self._feeds = OrderedDict()
        self._locks = {}
        self._epoch = 0

    def invalidate(self):
        with self._guard:
            self._epoch += 1
            self._feeds.clear()

    def _check(self, generation, epoch):
        self.pages.check(generation)
        with self._guard:
            if epoch != self._epoch:
                raise AccountError("cache_cleared", "发现缓存已清除，请重新打开")

    def _retain(self, owner, method, feed, epoch):
        # Caller holds _guard; never call the account-generation lock from here.
        if epoch != self._epoch:
            raise AccountError("cache_cleared", "发现缓存已清除，请重新打开")
        key=(owner,method,feed["batch"])
        self._feeds[key]=feed;self._feeds.move_to_end(key)
        while len(self._feeds)>32:self._feeds.popitem(last=False)

    @staticmethod
    def _response(feed, owner, method, generation, offset, target):
        items=feed["items"][offset:target]
        has_more=target<len(feed["items"]) or any(feed["paths"].values()) or any(feed["pending"].values())
        return {"items":copy.deepcopy(items),"uid":owner,"method":method,"batch":feed["batch"],"offset":offset,
                "nextOffset":offset+len(items) if has_more else None,"edition":feed["edition"],"unavailable":feed["unavailable"],
                "created":feed["created"],"kind":feed["kind"],"scope":feed["scope"],"rankMode":feed["rankMode"],"authorizationGeneration":generation}

    @staticmethod
    def _window():
        # Conservative boundary at the daily 12:00 JST publication window. This
        # guards continuation, and is deliberately not displayed as an edition date.
        return int((time.time() - 3 * 3600) // 86400)

    def _new(self, kind, scope, mode, rank, method):
        paths = {}
        if method == "app":
            if kind == "following":
                scopes = ["public", "private"] if scope == "all" else [scope]
                paths = {part:"/v2/illust/follow?"+urlencode({"restrict":part}) for part in scopes}
            elif kind == "recommended":
                paths = {"main":"/v1/illust/recommended?"+urlencode({"filter":"for_android","include_ranking_illusts":"false","include_privacy_policy":"false"})}
            else:
                paths = {"main":"/v1/illust/ranking?"+urlencode({"filter":"for_android","mode":rank})}
        else:
            paths = {"main":1}
        return {"batch":uuid.uuid4().hex,"kind":kind,"scope":scope,"mode":mode,"rankMode":rank,
                "paths":paths,"pending":{key:[] for key in paths},"items":[],"seen":[],"sourceCount":0,
                "edition":"","window":self._window(),"created":time.time(),"unavailable":0,"stagnant":0}

    def _app_batch(self, feed, stream, generation):
        path = feed["paths"][stream]
        endpoint = {"following":"/v2/illust/follow", "recommended":"/v1/illust/recommended", "ranking":"/v1/illust/ranking"}[feed["kind"]]
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if parsed.scheme or parsed.netloc or parsed.path != endpoint or len(path) > 30000:
            raise AccountError("cursor", "发现续页上下文无效")
        if feed["kind"] == "following" and query.get("restrict") != [stream]:
            raise AccountError("cursor", "关注新作范围不符")
        if feed["kind"] == "ranking":
            if query.get("mode") != [feed["rankMode"]]:
                raise AccountError("cursor", "榜种不符")
            if feed["sourceCount"] and feed["window"] != self._window():
                raise AccountError("ranking_changed", "榜单已进入新的更新窗口，请刷新后继续")
        raw = self.pages.account().request("GET", path, check=lambda:self.pages.check(generation))
        if not isinstance(raw, dict) or not isinstance(raw.get("illusts"), list):
            raise AccountError("invalid_data", "发现作品列表格式异常")
        next_path = continuation(raw, endpoint)
        if next_path == path:
            raise AccountError("cursor", "发现续页未前进")
        feed["paths"][stream] = next_path
        return raw["illusts"]

    def _web_batch(self, feed, stream, generation):
        page, kind = feed["paths"][stream], feed["kind"]
        if kind == "recommended":
            path = "/ajax/discovery/artworks?"+urlencode({"mode":feed["mode"],"limit":18,"lang":"zh"})
        elif kind == "following":
            path = "/ajax/follow_latest/illust?"+urlencode({"p":page,"mode":"r18" if feed["mode"]=="r18" else "all","lang":"zh"})
        else:
            args = {"mode":RANKS[feed["rankMode"]],"content":"all","format":"json","p":page}
            if feed["edition"]:args["date"] = feed["edition"]
            path = "/ranking.php?"+urlencode(args)
        raw = self.pages.request_json("https://www.pixiv.net"+path)
        self.pages.check(generation)
        if kind == "ranking":
            if not isinstance(raw,dict) or raw.get("mode") != RANKS[feed["rankMode"]] or raw.get("content") != "all":
                raise AccountError("invalid_data", "榜单来源与请求不符")
            edition = str(raw.get("date", ""))
            if not re.fullmatch(r"\d{8}", edition):
                raise AccountError("invalid_data", "榜单日期无效")
            if feed["edition"] and feed["edition"] != edition:
                raise AccountError("ranking_changed", "榜次已改变，请刷新后继续")
            feed["edition"] = edition
            rows, next_page = raw.get("contents"), raw.get("next")
            if next_page is not False and next_page is not None and (type(next_page) is not int or next_page <= page):
                raise AccountError("cursor", "榜单续页无效")
            feed["paths"][stream] = next_page or None
        else:
            body = source_body(raw)
            thumbnails = body.get("thumbnails")
            if not isinstance(thumbnails,dict):
                raise AccountError("invalid_data", "发现缩略图列表格式异常")
            rows = thumbnails.get("illust")
            if kind == "following":
                info = body.get("page")
                if not isinstance(info,dict) or type(info.get("isLastPage")) is not bool:
                    raise AccountError("invalid_data", "关注新作分页格式异常")
                feed["paths"][stream] = None if info["isLastPage"] else page+1
            else:
                feed["paths"][stream] = page+1 if rows else None
        if not isinstance(rows,list) or len(rows)>1000:
            raise AccountError("invalid_data", "发现作品数量或格式异常")
        return rows

    def _fetch(self, feed, stream, owner, method, generation):
        raw = self._app_batch(feed,stream,generation) if method=="app" else self._web_batch(feed,stream,generation)
        result = []
        seen = set(feed["seen"])
        for row in raw:
            feed["sourceCount"] += 1
            try:
                item = normalize_work(row,allow_r18=feed["mode"]!="safe") if method=="app" else normalize_web_rank(row) if feed["kind"]=="ranking" else normalize_search_item(row,allow_r18=feed["mode"]!="safe")
                source_url=parse_qs(urlsplit(item["thumb"]).query)["url"][0]
                if not re.match(re.escape(item["id"])+r"(?:_|-)",urlsplit(source_url).path.rsplit("/",1)[-1]):
                    raise PixivPolicyError("发现缩略图身份不符")
            except PixivPolicyError:
                feed["unavailable"] += 1
                continue
            if feed["mode"]=="r18" and item["restriction"]!="r18":continue
            if item["id"] in seen:continue
            if feed["kind"]=="ranking" and method=="app":
                item["rank"] = feed["sourceCount"]
                item["rankKind"] = "position"
            result.append(item)
            seen.add(item["id"])
        if method=="web" and feed["kind"]=="following" and feed["scope"]!="all":
            states = self.follow_states(owner,method,[item["userId"] for item in result])
            if any(not states.get(item["userId"],{}).get("known") or states[item["userId"]]["active"] and states[item["userId"]].get("privacy") is None for item in result):
                raise AccountError("sync_required", "请先在我的账号同步关注数据，再按公开范围筛选新作")
            result = [item for item in result if states[item["userId"]]["active"] and states[item["userId"]]["privacy"]==feed["scope"]]
        feed["seen"] = list(seen)
        if feed["kind"]=="following":result.sort(key=lambda item:(item["sortDate"],int(item["id"])),reverse=True)
        if not result and feed["kind"]=="recommended":
            feed["stagnant"] += 1
            if feed["stagnant"]>=3:feed["paths"][stream]=None
        else:feed["stagnant"]=0
        feed["pending"][stream].extend(result)

    def read(self, *, generation, kind="recommended", scope="all", mode="safe", rank="day", offset=0, batch="", refresh=False, cached=False):
        if kind not in {"recommended","following","ranking"} or scope not in {"all","public","private"} or mode not in {"safe","r18","all"} or rank not in RANKS:
            raise DiscoveryInputError("发现参数无效")
        if kind=="ranking" and mode!="safe":
            raise DiscoveryInputError("本版榜单仅提供全年龄范围")
        if kind!="following":scope="all"
        if kind!="ranking":rank="day"
        if type(offset) is not int or not 0<=offset<=100000 or batch and not re.fullmatch(r"[a-f0-9]{32}",batch):
            raise DiscoveryInputError("发现分页无效")
        owner, method = self.context(generation)
        requested_batch = bool(batch)
        descriptor = [kind,scope,mode,rank]
        with self._guard:
            epoch = self._epoch
            lock = self._locks.setdefault((owner,method,*descriptor),threading.RLock())
        with lock:
            current_key = ["discovery-current",*descriptor]
            if not batch and not refresh:batch = self.pages.saved_cache.read(owner,method,current_key) or ""
            key = (owner,method,batch)
            with self._guard:stored = self._feeds.get(key)
            if stored is None and batch and not refresh:stored = self.pages.saved_cache.read(owner,method,["discovery-feed",batch])
            if stored and [stored["kind"],stored["scope"],stored["mode"],stored["rankMode"]]!=descriptor:
                raise AccountError("source_changed", "发现来源与批次不符")
            if cached and (not stored or offset>=len(stored["items"]) and (any(stored["paths"].values()) or any(stored["pending"].values()))):
                return {"cached":False,"authorizationGeneration":generation}
            if not cached and not requested_batch and stored and kind=="ranking" and stored["window"]!=self._window():refresh=True
            if stored and not refresh:
                if offset>len(stored["items"]):raise AccountError("page_missing", "请从已有页面继续翻页")
                complete=not any(stored["paths"].values()) and not any(stored["pending"].values())
                if cached or offset+36<=len(stored["items"]) or complete:
                    self._check(generation,epoch)
                    with self._guard:self._retain(owner,method,stored,epoch)
                    return self._response(stored,owner,method,generation,offset,offset+36)
            feed = copy.deepcopy(stored) if stored and not refresh else self._new(kind,scope,mode,rank,method)
            if batch and not stored and not refresh:
                raise AccountError("cache_miss", "原发现批次已清除，请刷新当前页面")
            target = min(offset+36,len(feed["items"])) if cached else offset+36
            requests = 0
            emitted={item["id"] for item in feed["items"]}
            while len(feed["items"]) < target:
                for stream in feed["pending"]:
                    while not feed["pending"][stream] and feed["paths"][stream] is not None and requests<12:
                        self._check(generation,epoch)
                        self._fetch(feed,stream,owner,method,generation)
                        requests += 1
                # Never merge past a stream whose next head is still unknown.
                if any(not feed["pending"][s] and feed["paths"][s] is not None for s in feed["pending"]):break
                available = [s for s in feed["pending"] if feed["pending"][s]]
                if not available:break
                chosen = max(available,key=lambda s:(feed["pending"][s][0]["sortDate"],int(feed["pending"][s][0]["id"]))) if kind=="following" else available[0]
                item = feed["pending"][chosen].pop(0)
                if item["id"] not in emitted:
                    feed["items"].append(item);emitted.add(item["id"])
            if offset>len(feed["items"]):raise AccountError("page_missing", "请从已有页面继续翻页")
            items = feed["items"][offset:target]
            has_more = target<len(feed["items"]) or any(feed["paths"].values()) or any(feed["pending"].values())
            self._check(generation,epoch)
            with self._guard:
                if epoch != self._epoch:
                    raise AccountError("cache_cleared", "发现缓存已清除，请重新打开")
                self.pages.saved_cache.save(owner,method,["discovery-feed",feed["batch"]],feed)
                if not batch or refresh:self.pages.saved_cache.save(owner,method,current_key,feed["batch"])
                self._retain(owner,method,feed,epoch)
            if not items and has_more:raise AccountError("filtered", "当前范围暂未取得可展示作品，再次进入可继续读取，或调整范围")
            return self._response(feed,owner,method,generation,offset,target)
