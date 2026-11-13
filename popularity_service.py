"""Resumable metadata-only popularity snapshots for authors and discovery feeds."""
from collections import OrderedDict
import copy
import re
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import uuid
import weakref

from account_pages import source_body
from pixiv_account import AccountError
from pixiv_adapter import (PixivPolicyError, valid_pixiv_id, normalize_search_item,
    build_user_profile_all_url, build_user_profile_works_url,CONTENT_MODES,matches_content_mode)
from pixiv_app_gallery import normalize_work, continuation
from search_service import parse_search_date_range, parse_search_query, parse_search_tags
from request_coordinator import checked_lock

CARD_FIELDS = ("id", "source", "userId", "title", "artist", "thumb", "width", "height", "pages",
               "bookmarks", "date", "sortDate", "workType", "restriction", "aiGenerated", "rank", "rankKind")


class PopularityService:
    def __init__(self, *, pages, context, discovery, app_get, web_get, detail, search_get=None):
        self.pages, self.context, self.discovery = pages, context, discovery
        self.app_get, self.web_get, self.detail = app_get, web_get, detail
        self.search_get = search_get
        self.guard = threading.RLock()
        self.jobs = OrderedDict()
        # Active readers hold their lock strongly, including while waiting.
        self.locks = weakref.WeakValueDictionary()
        self.epoch = 0

    def invalidate(self):
        with self.guard:
            self.epoch += 1
            self.jobs.clear()

    def _check(self, generation, epoch):
        self.pages.check(generation)
        with self.guard:
            if epoch != self.epoch:
                raise AccountError("cache_cleared", "排序缓存已清理，请重新准备")

    def _save(self, owner, method, job, epoch, generation, *, current_key=None):
        snapshot = copy.deepcopy(job)
        with self.pages.publication(generation), self.guard:
            if epoch != self.epoch:
                raise AccountError("cache_cleared", "排序缓存已清理，请重新准备")
            records = [(["popularity-index", job["index"]], snapshot)]
            if current_key is not None:records.append((current_key, job["index"]))
            self.pages.saved_cache.save_many(owner, method, records)
            self.jobs[owner, method, job["index"]] = snapshot
            self.jobs.move_to_end((owner, method, job["index"]))
            while len(self.jobs) > 12:
                self.jobs.popitem(last=False)

    def _new(self, kind, uid, batch, mode, work_type, include_ai, start, end, method, generation):
        job = {"index": uuid.uuid4().hex, "kind": kind, "uid": uid, "batch": batch, "mode": mode,
               "workType": work_type, "includeAi": include_ai, "startDate": start, "endDate": end,
               "created": time.time(), "items": [], "seen": [], "paths": [], "visitedPaths": [], "webIds": None,
               "webOffset": 0, "sourceOffset": 0, "enrichOffset": 0, "collected": False, "ready": False}
        if kind == "author":
            if method == "app":
                types = ["manga"] if work_type == "manga" else ["illust"] if work_type in {"illustration", "ugoira"} else ["illust", "manga"]
                job["paths"] = ["/v1/user/illusts?" + urlencode({"user_id": uid, "type": part, "filter": "for_android"}) for part in types]
        elif kind == "search":
            job["sourcePage"] = 1
        elif kind == "recommended":
            source = self.discovery.snapshot(batch, generation=generation)
            if source["kind"] != kind or source["mode"] != mode:
                raise AccountError("source_changed", "推荐来源已改变，请重新选择排序")
            rows = [*source["items"], *[item for values in source["pending"].values() for item in values]]
            self._append(job, rows)
            job["collected"] = True
        else:
            source = self.discovery.snapshot(batch, generation=generation)
            if source["kind"] != kind or mode != "safe":
                raise AccountError("source_changed", "榜单来源已改变，请重新选择排序")
            job["rank"] = source["rankMode"]
        return job

    def _append(self, job, rows):
        seen = set(job["seen"])
        dates = parse_search_date_range(job["startDate"], job["endDate"])
        additions = []
        for item in rows:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            if job["kind"] == "author" and item["userId"] != job["uid"]:
                raise AccountError("invalid_data", "作者作品身份与请求不符")
            if not matches_content_mode(item["restriction"],job["mode"]):
                continue
            if job["workType"] != "all" and item["workType"] != job["workType"]:
                continue
            if not job["includeAi"] and item["aiGenerated"]:
                continue
            if dates and not dates.contains(item["date"]):
                continue
            additions.append({key: item[key] for key in CARD_FIELDS if key in item})
        job["items"].extend(additions)
        job["seen"] = list(seen)

    def _author_batch(self, job, method, generation):
        if method == "app":
            path = job["paths"][0]
            if path in job["visitedPaths"]:
                raise AccountError("cursor", "作者元数据续页出现循环，请刷新后重试")
            raw = self.app_get(path, generation)
            rows = raw.get("illusts")
            if not isinstance(rows, list) or len(rows) > 1000:
                raise AccountError("invalid_data", "作者元数据分页格式异常")
            next_path = continuation(raw, "/v1/user/illusts")
            if next_path == path:
                raise AccountError("cursor", "作者元数据续页未前进")
            if next_path:
                query = parse_qs(urlsplit(next_path).query)
                current_type = parse_qs(urlsplit(path).query)["type"]
                if query.get("user_id") != [job["uid"]] or query.get("type") != current_type:
                    raise AccountError("cursor", "作者元数据续页身份或类型不符")
            normalized = []
            for row in rows:
                try:
                    normalized.append(normalize_work(row, allow_r18=job["mode"] != "safe", require_bookmarks=False))
                except PixivPolicyError:
                    continue
            self._append(job, normalized)
            job["visitedPaths"].append(path)
            if next_path:
                job["paths"][0] = next_path
            else:
                job["paths"].pop(0)
            job["collected"] = not job["paths"]
            return
        if job["webIds"] is None:
            body = source_body(self.web_get(build_user_profile_all_url(job["uid"])))
            types = ["manga"] if job["workType"] == "manga" else ["illusts"] if job["workType"] in {"illustration", "ugoira"} else ["illusts", "manga"]
            identifiers = []
            for part in types:
                values = body.get(part) or {}
                if not isinstance(values, dict) or any(not valid_pixiv_id(str(value)) for value in values):
                    raise AccountError("invalid_data", "作者作品索引格式异常")
                identifiers.extend(map(str, values))
            job["webIds"] = sorted(set(identifiers), key=int, reverse=True)
            job["collected"] = not identifiers
            return
        ids = job["webIds"][job["webOffset"]:job["webOffset"] + 48]
        body = source_body(self.web_get(build_user_profile_works_url(job["uid"], ids)))
        values = body.get("works", body)
        if not isinstance(values, (dict, list)):
            raise AccountError("invalid_data", "作者作品摘要格式异常")
        rows = values.values() if isinstance(values, dict) else values
        normalized = []
        for row in rows:
            try:
                item = normalize_search_item(row, allow_r18=job["mode"] != "safe")
            except PixivPolicyError:
                continue
            if item["id"] not in ids:
                raise AccountError("invalid_data", "作者作品摘要身份不符")
            normalized.append(item)
        self._append(job, normalized)
        job["webOffset"] += len(ids)
        job["collected"] = job["webOffset"] >= len(job["webIds"])

    def read(self, *, generation, kind, uid="", batch="", mode="safe", work_type="all", include_ai=True,
             start="", end="", index="", page=1, refresh=False, query="", fuzzy=False, cancel_event=None):
        if kind not in {"author", "ranking", "recommended", "search"} or mode not in CONTENT_MODES:
            raise ValueError("排序来源无效")
        if work_type not in {"all", "illustration", "manga", "ugoira"} or type(include_ai) is not bool:
            raise ValueError("排序筛选无效")
        if type(page) is not int or not 1 <= page <= 100000:
            raise ValueError("排序页码无效")
        if kind == "author" and not valid_pixiv_id(uid) or kind in {"ranking","recommended"} and not re.fullmatch(r"[a-f0-9]{32}", batch):
            raise ValueError("排序作品来源无效")
        if kind == "search":
            parsed = parse_search_query(query)
            if parsed.kind != "tags" or type(fuzzy) is not bool:
                raise ValueError("搜索候选池条件无效")
            query = "；".join(parse_search_tags(parsed.value))
        if index and not re.fullmatch(r"[a-f0-9]{32}", index):
            raise ValueError("排序快照无效")
        parse_search_date_range(start, end)
        owner, method = self.context(generation)
        descriptor = [kind, uid, batch, mode, work_type, include_ai, start, end]
        if kind == "search":
            descriptor.extend([query, fuzzy])
        with self.guard:
            epoch = self.epoch
            lock = self.locks.setdefault((owner, method, *descriptor), threading.RLock())
        with checked_lock(lock,lambda:self._check(generation,epoch)):
            self._check(generation, epoch)
            current_key = ["popularity-current", *descriptor]
            requested_index = index
            if not index and not refresh:
                index = self.pages.saved_cache.read(owner, method, current_key) or ""
            with self.guard:
                job = self.jobs.get((owner, method, index))
            if job is None and index:
                job = self.pages.saved_cache.read(owner, method, ["popularity-index", index])
            if requested_index and job is None:
                raise AccountError("cache_miss", "排序快照已清理，请重新准备")
            if job and job["descriptor"] != descriptor:
                raise AccountError("source_changed", "排序快照与当前条件不符")
            if job is not None:
                job = copy.deepcopy(job)
            if job is None:
                job = self._new(kind, uid, batch, mode, work_type, include_ai, start, end, method, generation)
                job["descriptor"] = descriptor
                if kind == "search":
                    job.update(query=query, fuzzy=fuzzy)
                self._save(owner, method, job, epoch, generation, current_key=current_key)
                with self.pages.publication(generation), self.guard:
                    self.pages.saved_cache.retain_popularity(owner, method)
            deadline = time.monotonic() + 18
            reads = 0
            while not job["ready"] and reads < 3 and time.monotonic() < deadline:
                self._check(generation, epoch)
                if not job["collected"]:
                    if kind == "author":
                        self._author_batch(job, method, generation)
                    elif kind == "search":
                        result = self.search_get(job, generation, cancel_event)
                        self._append(job, result["items"])
                        job["collected"] = len(job["items"]) >= 500 or not result["hasMore"]
                        job["items"] = job["items"][:500]
                        if len(result["items"]) >= 36:
                            job["sourcePage"] += 1
                    else:
                        result = self.discovery.read(generation=generation, kind="ranking", rank=job["rank"],
                            batch=batch, offset=job["sourceOffset"])
                        self._append(job, result["items"])
                        job["sourceOffset"] = result["nextOffset"]
                        job["collected"] = result["nextOffset"] is None
                    reads += 1
                else:
                    while job["enrichOffset"] < len(job["items"]) and job["items"][job["enrichOffset"]].get("bookmarks") is not None:
                        job["enrichOffset"] += 1
                    if job["enrichOffset"] < len(job["items"]):
                        item = job["items"][job["enrichOffset"]]
                        fresh = self.detail(item["id"], job["mode"], generation)
                        count = fresh.get("bookmarks")
                        if type(count) is not int or count < 0:
                            raise AccountError("incomplete", "部分作品暂未提供收藏数，可稍后重试")
                        item["bookmarks"] = count
                        job["enrichOffset"] += 1
                        reads += 1
                    else:
                        job["items"].sort(key=lambda item: (item["bookmarks"], item.get("sortDate", ""), int(item["id"])), reverse=True)
                        job["ready"] = True
                self._check(generation, epoch)
                self._save(owner, method, job, epoch, generation)
            offset = (page - 1) * 36
            total = len(job["items"])
            if job["ready"] and offset >= total and page != 1:
                raise ValueError("排序页码超出范围")
            result = {"index": job["index"], "ready": job["ready"], "loaded": total, "counted": job["enrichOffset"], "total": total if job["ready"] else None,
                    "items": copy.deepcopy(job["items"][offset:offset + 36]) if job["ready"] else [],
                    "page": page, "pages": max(1, (total + 35) // 36) if job["ready"] else None,
                    "created": job["created"], "kind": kind, "limit": 500 if kind == "search" else None,
                    "authorizationGeneration": generation}
            self._check(generation, epoch)
            return result
