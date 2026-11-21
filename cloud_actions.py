"""Pixiv cloud reads/writes with account fences, explicit intent and read-back."""
import hashlib
from contextlib import ExitStack, contextmanager
import json
import re
import threading
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from cloud_state import relationship_stamp
from account_pages import HomepageIdentity, source_body
from pixiv_account import ACCOUNT_WRITE_PREFLIGHT, AccountError
from pixiv_app_gallery import continuation
from pixiv_adapter import PixivPolicyError, valid_pixiv_id
from request_coordinator import REQUEST_PRIORITY, checked_lock


def validate_target(raw):
    kind, target = raw.get("kind"), raw.get("id")
    if not isinstance(kind, str) or kind not in {"bookmark", "follow"} or not isinstance(target, str) or not valid_pixiv_id(target):
        raise ValueError("收藏／关注目标无效")
    return kind, target


def validate_intent(raw):
    kind, target = validate_target(raw)
    active, privacy, tags, request_id, expected = (raw.get(key) for key in ("active", "privacy", "tags", "requestId", "expected"))
    if type(active) is not bool or privacy not in ("public", "private"):
        raise ValueError("请选择公开或私密")
    if tags is not None and (not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(tag, str) or not tag.strip()
            or len(tag) > 100 or any(ord(c) < 32 for c in tag) for tag in tags)):
        raise ValueError("收藏标签最多 10 个，每个最多 100 字")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,100}", request_id):
        raise ValueError("操作编号无效")
    if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{24}", expected)):
        raise ValueError("请先读取当前云端状态")
    return {"kind": kind, "id": target, "active": active, "privacy": privacy,
            "tags": list(dict.fromkeys(tag.strip() for tag in tags)) if tags is not None else None, "requestId": request_id, "expected": expected}


class CloudActionError(AccountError):
    def __init__(self, kind, message, *, state=None, official=False):
        super().__init__(kind, message)
        self.state, self.official = state, official


class CloudActions:
    def __init__(self, pages, store, web_post, commit_guard):
        self.pages, self.store, self.web_post, self.commit_guard = pages, store, web_post, commit_guard
        self._scan_lock = threading.RLock()

    def context(self, generation):
        method = self.pages.method()
        if method == "app":
            snapshot = self.pages.account().snapshot()
            if not snapshot["connected"] or snapshot["needsAuthorization"]:
                raise AccountError("reauth", "请先在我的账号登录")
            owner = snapshot["uid"]
        else:
            owner = self.pages.web_owner(generation)
        self.pages.check(generation)
        return owner, method

    def _commit(self, generation, callback):
        with self.commit_guard:
            self.pages.check(generation)
            return callback()

    def _app(self, generation, method, path, form=None):
        return self.pages.account().request(method, path, form=form, check=lambda: self.pages.check(generation))

    def _web(self, generation, path):
        self.pages.check(generation)
        result = source_body(self.pages.request_json("https://www.pixiv.net" + path))
        self.pages.check(generation)
        return result

    def _scan_page(self, owner, method, kind, scope, cursor, generation, *, visited=()):
        """Validate one remote page before publishing relationships and display rows."""
        items = []
        if method == "app":
            endpoint = "/v1/user/bookmarks/illust" if kind == "bookmark" else "/v1/user/following"
            path = cursor or endpoint + "?" + urlencode({"user_id": owner, "restrict": scope, "filter": "for_android"})
            parsed = urlsplit(path)
            query = parse_qs(parsed.query)
            if parsed.scheme or parsed.netloc or parsed.path != endpoint or query.get("user_id") != [owner] or query.get("restrict") != [scope]:
                raise AccountError("cursor", "云端列表续页无效")
            raw = self._app(generation, "GET", path)
            rows = raw.get("illusts" if kind == "bookmark" else "user_previews")
            if not isinstance(rows, list):
                raise AccountError("invalid_data", "云端关系列表格式异常")
            for row in rows:
                entity = row if kind == "bookmark" else row.get("user") if isinstance(row, dict) else None
                target = str(entity.get("id", "")) if isinstance(entity, dict) else ""
                if not valid_pixiv_id(target):
                    raise AccountError("invalid_data", "云端关系列表缺少有效 ID，本轮未完成")
                items.append((target, {"active": True, "privacy": scope, "tags": None}))
            next_path = continuation(raw, endpoint, current=path, visited=visited)
            if next_path:
                parsed_next = urlsplit(next_path)
                values=parse_qs(parsed_next.query)
                if values.get("user_id")!=[owner] or values.get("restrict")!=[scope]:
                    raise AccountError("cursor","云端续页账号、范围或位置异常")
                if not rows:
                    raise AccountError("cursor", "空列表仍返回续页，本轮未完成")
            return items, next_path, rows, None
        offset = int(cursor or "0")
        endpoint = f"/ajax/user/{owner}/" + ("illusts/bookmarks" if kind == "bookmark" else "following")
        raw = self._web(generation, endpoint + "?" + urlencode({"offset": offset, "limit": 36 if kind=="bookmark" else 30,
                           "rest": "hide" if scope == "private" else "show", "tag": "", "lang": "zh"}))
        rows, total = raw.get("works" if kind == "bookmark" else "users"), raw.get("total")
        if not isinstance(rows, list) or type(total) is not int or total < 0:
            raise AccountError("invalid_data", "云端关系列表格式异常")
        for row in rows:
            target = str(row.get("id" if kind == "bookmark" else "userId", "")) if isinstance(row, dict) else ""
            if not valid_pixiv_id(target):
                raise AccountError("invalid_data", "云端关系列表缺少有效 ID，本轮未完成")
            bookmark = row.get("bookmarkData")
            bookmark_id = str(bookmark["id"]) if isinstance(bookmark, dict) and "id" in bookmark else None
            tag_index = raw.get("bookmarkTags")
            # Pixiv serializes an empty PHP map as [], and a populated map as an object.
            tags = tag_index.get(bookmark_id, []) if isinstance(tag_index, dict) else [] if tag_index == [] else None
            if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)):
                raise AccountError("invalid_data", "收藏标签格式异常")
            items.append((target, {"active": True, "privacy": scope, "tags": tags, "bookmarkId": bookmark_id}))
        next_offset = offset + len(rows)
        if next_offset < total and not rows:
            raise AccountError("cursor", "云端列表未读完但返回空页，本轮未完成")
        next_cursor = str(next_offset) if next_offset < total else None
        return items, next_cursor, rows, total

    def _read(self, owner, method, kind, target, generation):
        if method == "app" and kind == "bookmark":
            raw = self._app(generation, "GET", "/v2/illust/bookmark/detail?" + urlencode({"illust_id": target}))
            detail = raw.get("bookmark_detail")
            if not isinstance(detail, dict) or type(detail.get("is_bookmarked")) is not bool:
                raise AccountError("invalid_data", "收藏状态暂时无法确认")
            active = detail["is_bookmarked"]
            if not active:
                return {"active": False, "privacy": None, "tags": []}
            privacy, rows = detail.get("restrict"), detail.get("tags")
            if privacy not in ("public", "private") or not isinstance(rows, list):
                raise AccountError("invalid_data", "收藏隐私或标签暂时无法确认")
            tags = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("name"), str) or type(row.get("is_registered")) is not bool:
                    raise AccountError("invalid_data", "收藏标签格式异常")
                if row["is_registered"]:
                    tags.append(row["name"])
            return {"active": True, "privacy": privacy, "tags": tags}
        if method == "web" and kind == "bookmark":
            detail = self._web(generation, f"/ajax/illust/{target}/bookmarkData")
            if "bookmarkData" not in detail:
                raise AccountError("invalid_data", "收藏状态暂时无法确认")
            bookmark = detail["bookmarkData"]
            if bookmark is None:
                return {"active": False, "privacy": None, "tags": []}
            if not isinstance(bookmark, dict) or type(bookmark.get("private")) is not bool or not valid_pixiv_id(str(bookmark.get("id", ""))):
                raise AccountError("invalid_data", "收藏隐私暂时无法确认")
            privacy = "private" if bookmark["private"] else "public"
            # This small detail endpoint may omit personal tags. Locate them in the private/public bookmark list.
            tags = bookmark.get("tags")
            if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)):
                raise AccountError("invalid_data", "收藏标签格式异常")
            if tags is None:
                cursor = ""
                for _ in range(3):
                    entries, cursor, _, _ = self._scan_page(owner, method, kind, privacy, cursor, generation)
                    found = next((state for key, state in entries if key == target), None)
                    if found is not None:
                        tags = found["tags"]
                        break
                    if cursor is None:
                        break
            return {"active": True, "privacy": privacy, "tags": tags, "bookmarkId": str(bookmark["id"])}
        if method == "app":
            raw = self._app(generation, "GET", "/v1/user/follow/detail?" + urlencode({"user_id": target}))
            detail = raw.get("follow_detail")
            if not isinstance(detail, dict) or type(detail.get("is_followed")) is not bool:
                raise AccountError("invalid_data", "关注状态暂时无法确认")
            active = detail["is_followed"]
            privacy = detail.get("restrict") if active else None
            if active and privacy not in ("public", "private"):
                raise AccountError("invalid_data", "关注隐私暂时无法确认")
            return {"active": active, "privacy": privacy, "tags": []}
        raw = self._web(generation, f"/ajax/user/{target}?full=1&lang=zh")
        if type(raw.get("isFollowed")) is not bool:
            raise AccountError("invalid_data", "关注状态暂时无法确认")
        active, privacy = raw["isFollowed"], None
        if active:
            details = self._web(generation, "/ajax/following/user/details?" + urlencode({"user_id": target}))
            restrict = details.get("restrict")
            if restrict in (0, "0"):
                privacy = "public"
            elif restrict in (1, "1"):
                privacy = "private"
        return {"active": active, "privacy": privacy, "tags": []}

    def read(self, generation, kind, target):
        owner, method = self.context(generation)
        with self._relationship_operation(generation, owner, kind, target):
            with self.publication(generation, owner, method) as revision:
                previous = self._cached_relation(owner, method, kind, target)
            state = self._read(owner, method, kind, target, generation)
            with self.publication(generation, owner, method, revision=revision):
                current, _ = self._adopt_observation(owner, method, kind, target, previous, state)
                return current

    @contextmanager
    def _relationship_operation(self, generation, owner, kind, target):
        # Keep unrelated objects and local cache publication out of the network wait.
        with ExitStack() as ownership:
            try:
                ownership.enter_context(self.pages.saved_cache.relation_mutex(owner, kind, target))
            except TimeoutError:
                raise AccountError("relation_busy", "另一项操作正在核对此状态，请稍后重试") from None
            self.pages.check(generation)
            yield

    def _cached_relation(self, owner, method, kind, target):
        works, authors = ([target], []) if kind == "bookmark" else ([], [target])
        return self.store.many(owner, method, works, authors)[kind][target]

    def _adopt_observation(self, owner, method, kind, target, observed, state, *, before=None, before_pages=None):
        """Compare the object's state under the same lock used to publish it."""
        current = self._cached_relation(owner, method, kind, target)
        fields = ("stamp", "touched", "bookmarkId")
        if any(current.get(field) != observed.get(field) for field in fields):
            return current, (current.get("stamp") == relationship_stamp(state)
                             and current.get("bookmarkId") == state.get("bookmarkId"))
        previous = observed if before is None else {**before, "known": True}
        return self._record(owner, method, kind, target, previous, state, before_pages=before_pages), True

    def _record(self, owner, method, kind, target, previous, state, *, before_pages=None):
        """Use one publication path for verified state and display-cache repairs."""
        repair_id = uuid.uuid4().hex
        current = self.store.put(owner, method, kind, target, state, repair_id=repair_id)
        repair = not previous["known"] or relationship_stamp(previous)==current["stamp"]
        self.pages.note_relation(owner, method, kind, target, current if not previous["known"] else previous, current,
                                 repair=repair, before_pages=before_pages if before_pages is not None else {})
        self.store.finish_page_repairs(owner, method, [repair_id])
        return current

    @contextmanager
    def publication(self, generation, owner, method, *, revision=None):
        """Serialize the short commits spanning relationship and account caches."""
        with self.pages.cache_publication(generation, owner):
            if self.store.cache_clear_pending(owner):
                self.pages.clear_cache(owner)
                self.store.clear_account(owner)
            pending = self.store.pending_page_repairs(owner, method)
            if pending:
                self.pages.discard_relation_pages(owner, method, generation)
                self.store.finish_page_repairs(owner, method, pending, abandon_scan=True)
            current_revision = self.pages.saved_cache.clear_revision(owner)
            if revision is not None and revision != current_revision:
                raise AccountError("cache_cleared", "账号缓存已清理，请重新打开页面")
            yield current_revision

    def recover_pages(self, generation, *, context=None):
        owner, method = context if context is not None else self.context(generation)
        with self.publication(generation, owner, method):
            pass

    def clear_cache(self, generation, invalidate_displays):
        owner, method = self.context(generation)
        with self.publication(generation, owner, method):
            self.store.begin_cache_clear(owner)
            self.pages.clear_cache(owner)
            self.store.clear_account(owner)
            invalidate_displays()

    def _write(self, generation, owner, method, intent, before):
        kind, target, active, privacy = (intent[key] for key in ("kind", "id", "active", "privacy"))
        if method == "app":
            form = {"illust_id" if kind == "bookmark" else "user_id": target}
            if active:
                form["restrict"] = privacy
                if kind == "bookmark" and intent["tags"] is not None:
                    form["tags[]"] = " ".join(intent["tags"])
            path = ("/v2/illust/bookmark/add" if active else "/v1/illust/bookmark/delete") if kind == "bookmark" else "/v1/user/follow/" + ("add" if active else "delete")
            ACCOUNT_WRITE_PREFLIGHT.get()()
            self._app(generation, "POST", path, form)
            return
        parser = HomepageIdentity()
        parser.feed(self.pages.request_homepage())
        if parser.user_id() != owner:
            raise AccountError("revoked", "官网账号已改变")
        props = json.loads("".join(parser.chunks))["props"]["pageProps"]
        state = json.loads(props["serverSerializedPreloadedState"])
        token = state.get("api", {}).get("token")
        if not isinstance(token, str) or not token or len(token) > 4096:
            raise AccountError("reauth", "官网验证信息不可用，请重新登录")
        if kind == "bookmark":
            path, payload, form = ("/ajax/illusts/bookmarks/add", {"illust_id": target, "restrict": int(privacy == "private"),
                "tags": intent["tags"] or [], "comment": ""}, False) if active else ("/ajax/illusts/bookmarks/remove", {"bookmarkIds": [before["bookmarkId"]]}, False)
        elif not active:
            path, payload, form = "/rpc_group_setting.php", {"mode": "del", "type": "bookuser", "id": target}, True
        elif before["active"]:
            path, payload, form = "/ajax/following/user/restrict_change", {"user_id": target, "restrict": int(privacy == "private")}, True
        else:
            path, payload, form = "/bookmark_add.php", {"mode": "add", "type": "user", "user_id": target, "tag": "", "restrict": int(privacy == "private"), "format": "json"}, True
        preflight = ACCOUNT_WRITE_PREFLIGHT.get()
        preflight()
        self.web_post(path, payload, token, form, generation)
        if kind == "bookmark" and active and before["active"] and intent["tags"] is not None:
            removed = sorted(set(before["tags"]) - set(intent["tags"]))
            if removed:
                # Web add merges tags; an empty tags list does not clear existing tags.
                try:
                    preflight()
                    self.web_post("/ajax/illusts/bookmarks/remove_tags", {"removeTags": removed,
                        "bookmarkIds": [before["bookmarkId"]]}, token, False, generation)
                except CloudActionError as error:
                    if error.kind != "conflict":
                        raise
                    raise CloudActionError("unconfirmed", "收藏已部分提交，请核对当前标签", state=error.state) from error

    def mutate(self, generation, scope, intent):
        owner, method = self.context(generation)
        kind, target = intent["kind"], intent["id"]
        if kind == "follow" and target == owner:
            raise AccountError("self", "不能关注自己")
        with self._relationship_operation(generation, owner, kind, target):
            with self.publication(generation, owner, method) as revision:
                cached = self._cached_relation(owner, method, kind, target)
            before = self._read(owner, method, kind, target, generation)
            signature = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
            with self.publication(generation, owner, method, revision=revision):
                current = self._cached_relation(owner, method, kind, target)
                if current.get("stamp") != cached.get("stamp"):
                    raise CloudActionError("conflict", "账号状态已更新，请核对后重新操作", state=current)
                replay, result = self.store.operation(owner, method, intent["requestId"], signature)
                page_versions = None if replay else self.pages.relation_page_versions(owner, method, kind)

            def record(state, observed=cached, *, before=None, before_pages=None):
                with self.publication(generation, owner, method, revision=revision):
                    return self._adopt_observation(owner, method, kind, target, observed, state,
                                                   before=before, before_pages=before_pages)

            def complete(state):
                preferences = (scope, {kind + "Last": intent["privacy"]} if intent["active"] else {})
                return self._commit(generation, lambda: self.store.finish_operation(
                    owner, method, intent["requestId"], state, preferences=preferences))

            if replay:
                # A lost response never causes a second POST. A fresh read repairs the local view.
                current, accepted = record(before)
                confirmed = accepted and self._matches(intent, before)
                if confirmed and result is None:
                    preferences = complete(current)
                else:
                    preferences = self._commit(generation, lambda: self.store.preferences_snapshot(scope))
                return {"state": current, "confirmed": confirmed, "replayed": True, "preferences": preferences}
            conflict = before["active"] if intent["expected"] is None else (
                not cached["known"] or cached.get("stamp") != intent["expected"] or cached["active"] != before["active"] or
                before["active"] and any(cached.get(field) is not None and before.get(field) is not None and
                    (set(cached[field]) != set(before[field]) if field == "tags" else cached[field] != before[field]) for field in ("privacy", "tags")))
            if conflict:
                current, _ = record(before)
                raise CloudActionError("conflict", "云端状态已改变，请核对后重新操作", state=current)
            if kind == "bookmark" and intent["active"] and before["active"] and before["tags"] is None and intent["tags"] is not None:
                current, _ = record(before)
                raise CloudActionError("tags_unknown", "现有收藏标签尚未完整读取，请先在官网核对，避免覆盖", state=current)
            effective = {**intent, "tags": before["tags"]} if kind=="bookmark" and intent["tags"] is None else intent
            error = None
            needs_write = not self._matches(effective, before)

            def preflight():
                with self.publication(generation, owner, method, revision=revision):
                    current = self._cached_relation(owner, method, kind, target)
                    if (current.get("stamp"), current.get("bookmarkId")) != (cached.get("stamp"), cached.get("bookmarkId")):
                        raise CloudActionError("conflict", "此对象已更新，请核对后重新提交", state=current)

            try:
                if needs_write:
                    check = ACCOUNT_WRITE_PREFLIGHT.set(preflight)
                    try:
                        self._write(generation, owner, method, effective, before)
                    finally:
                        ACCOUNT_WRITE_PREFLIGHT.reset(check)
            except (PixivPolicyError, OSError, TimeoutError) as failure:
                if isinstance(failure, CloudActionError) and failure.kind == "conflict":
                    raise
                self.pages.check(generation)
                error = failure
            observed = cached
            try:
                if needs_write:
                    with self.publication(generation, owner, method, revision=revision):
                        observed = self._cached_relation(owner, method, kind, target)
                    after = self._read(owner, method, kind, target, generation)
                else:
                    after = before
            except (PixivPolicyError, OSError, TimeoutError) as failure:
                self.pages.check(generation)
                raise CloudActionError("unknown", "操作结果尚未确认，请刷新核对；不会自动重复提交", official=method == "web" and kind == "follow") from failure
            current, accepted = record(after, observed, before=before,
                                       before_pages=page_versions if needs_write else None)
            if not accepted:
                raise CloudActionError("unconfirmed", "关系状态已更新，请继续核对原操作", state=current)
            confirmed = self._matches(effective, after)
            if not confirmed:
                official = method == "web" and kind == "follow"
                raise CloudActionError("verification" if official else "unconfirmed", "请前往官网完成操作，再返回核对" if official else
                                       "云端结果尚未符合所选状态，请刷新核对；不会自动重试", state=current, official=official) from error
            preferences = complete(current)
            return {"state": current, "confirmed": True, "preferences": preferences}

    @staticmethod
    def _matches(intent, state):
        return state["active"] == intent["active"] and (not intent["active"] or
            state.get("privacy") == intent["privacy"] and (intent["kind"] == "follow" or intent["tags"] is None or
                state.get("tags") is not None and set(state["tags"]) == set(intent["tags"])))

    def sync(self, generation, kind, force=False):
        with checked_lock(self._scan_lock, lambda: self.pages.check(generation)):
            owner, method = self.context(generation)
            with self.publication(generation, owner, method) as revision:
                display_revision = self.pages.saved_cache.display_revision(owner, method)
                scan = self.store.sync_status(owner, method).get(kind)
                if force or not scan or not scan["running"] or scan.get("displayRevision") != display_revision:
                    scan = self.store.begin_scan(owner, method, kind, display_revision=display_revision)
            scope, offset = scan["scope"], scan["offset"]
            # Priority 2 is intentionally disabled in safe mode (image prefetch).
            # Relationship synchronization remains paced but usable in that mode.
            priority = REQUEST_PRIORITY.set(1)
            try:
                entries, cursor, rows, total = self._scan_page(owner, method, kind, scope, scan["cursor"], generation,
                    visited=scan["visitedPaths"].get(scope, []))
            finally:
                REQUEST_PRIORITY.reset(priority)
            with self.publication(generation, owner, method, revision=revision):
                if display_revision != self.pages.saved_cache.display_revision(owner, method):
                    raise AccountError("cache_cleared", "浏览设置已更新，请重新同步")
                repair_id = uuid.uuid4().hex
                result = self.store.scan_page(owner, method, kind, scan, entries, cursor, repair_id=repair_id)
                self.pages.cache_scan_page(owner, method, kind, scope, offset, rows, cursor, total, generation,
                    revision=display_revision)
                if not result["running"]:
                    self.pages.finish_scan(owner, method, kind, result["pageOffsets"], generation)
                # A local write made during this sweep wins over older list responses.
                for target, state in self.store.newer_relations(owner, method, kind, scan["version"]):
                    self.pages.note_relation(owner, method, kind, target, state, state, repair=True)
                self.store.finish_page_repairs(owner, method, [repair_id])
            return {**result, "page":{"kind":kind, "scope":scope, "offset":offset}}
