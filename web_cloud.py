"""Bounded, single-attempt Web writes; shares the application's request budget."""
import json
import threading
import time

from account_pages import HomepageIdentity, source_body
from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException
from pixiv_account import AccountError


class WebCloudTransport:
    def __init__(self, coordinator, context):
        self.coordinator, self.context = coordinator, context
        self.lock = threading.Lock()
        self.session = None

    def post(self, path, payload, csrf, form, generation):
        # Callers select fixed account-operation endpoints.
        if path not in {"/ajax/illusts/bookmarks/add", "/ajax/illusts/bookmarks/remove", "/ajax/illusts/bookmarks/remove_tags", "/bookmark_add.php",
                        "/rpc_group_setting.php", "/ajax/following/user/restrict_change",
                        "/ajax/settings/user_x_restrict", "/ajax/settings/sensitive_view_setting", "/ajax/settings/hide_ai_works"}:
            raise ValueError("unsupported_cloud_write")
        while not self.lock.acquire(timeout=.1):
            self.context(generation)
        try:
            headers, proxy, check, profile = self.context(generation)
            if self.session is None:
                self.session = Session(impersonate=profile, trust_env=False)
            headers.update({"Origin": "https://www.pixiv.net", "Referer": "https://www.pixiv.net/",
                            "Accept": "application/json", "X-CSRF-Token": csrf})
            chunks, count = [], 0

            def consume(chunk):
                nonlocal count
                check()
                count += len(chunk)
                if count > 4 * 1024 * 1024:
                    raise AccountError("response_size", "云端操作响应过大")
                chunks.append(chunk)
                return len(chunk)

            with self.coordinator.slot(False, check, priority=0):
                self.coordinator.before_send(False, check, priority=0, activity="detail")
                try:
                    response = self.session.post("https://www.pixiv.net" + path, headers=headers,
                        **({"data": payload} if form else {"json": payload}), proxy=proxy or None,
                        timeout=25, allow_redirects=False, discard_cookies=True, content_callback=consume)
                except RequestException as error:
                    check()
                    raise AccountError("network", "云端操作连接中断，正在核对结果") from error
            check()
            if response.status_code == 429:
                raise self.coordinator.limit(response.headers.get("Retry-After"))
            if not response.headers.get("Content-Type", "").startswith("application/json"):
                raise AccountError("verification", "官网要求验证，请在官方窗口完成")
            try:
                raw = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError) as error:
                raise AccountError("invalid_data", "官网操作响应无法解析") from error
            if response.status_code >= 400 or not (isinstance(raw, dict) or raw == []) or isinstance(raw, dict) and raw.get("error"):
                raise AccountError("rejected", f"官网拒绝本次操作（HTTP {response.status_code}）")
            self.coordinator.success()
            return raw
        finally:
            self.lock.release()


class WebDisplaySettings:
    """Account display settings with verified identity and server readback."""

    FIELDS = ("ageRestriction", "sensitive", "showAI")

    def __init__(self, pages, context, post, changed, *, revision):
        self.pages = pages
        self.context = context
        self.post = post
        self.changed = changed
        self.revision = revision

    def _owner(self, generation):
        owner, method = self.context(generation)
        if method != "web":
            raise AccountError("web_required", "此项需网页登录，请在登录管理中选择网页登录。")
        return owner

    def _read_remote(self, owner, generation):
        user = source_body(self.pages.request_json("https://www.pixiv.net/ajax/settings/self")).get("user_status")
        if not isinstance(user, dict) or user.get("is_logged_in") is not True:
            raise AccountError("reauth", "网页登录已失效，请重新登录。")
        if str(user.get("user_id")) != owner:
            raise AccountError("revoked", "官网账号已改变，请重新打开设置。")
        restriction = user.get("user_x_restrict")
        sensitive = user.get("sensitive_view_setting")
        hide_ai = user.get("hide_ai_works")
        age = user.get("age")
        if (restriction not in ("0", "1", "2") or type(sensitive) is not int or sensitive not in (0, 1)
                or type(hide_ai) is not bool or type(age) is not int):
            raise AccountError("invalid_data", "Pixiv 浏览设置格式异常，请重试。")
        self.pages.check(generation)
        return {"ageRestriction": int(restriction), "sensitive": bool(sensitive), "showAI": not hide_ai,
                "canChangeAge": age >= 18, "updatedAt": time.time()}

    def _publish(self, owner, generation, state):
        with self.pages.publication(generation), self.pages.credential_publication():
            self.pages.check(generation)
            self.pages.saved_cache.finish_display_update(owner, "web", state)
            self.changed(owner, generation)

    def _snapshot(self, generation, state):
        with self.pages.publication(generation):
            return {"settings": state, "authorizationGeneration": generation,
                    "browsingGeneration": self.revision()}

    def read(self, generation, refresh=False):
        owner = self._owner(generation)
        with self.pages.saved_cache.web_settings_mutex(owner):
            self.pages.check(generation)
            cached = self.pages.saved_cache.read(owner, "web", ["web-display-settings"])
            if cached is not None and not refresh:
                self.pages.check(generation)
                return self._snapshot(generation, cached)
            state = self._read_remote(owner, generation)
            recovering = self.pages.saved_cache.display_update_pending(owner, "web")
            if recovering or cached is not None and any(cached[key] != state[key] for key in self.FIELDS):
                self._publish(owner, generation, state)
            else:
                with self.pages.publication(generation):
                    self.pages.saved_cache.save(owner, "web", ["web-display-settings"], state)
            return self._snapshot(generation, state)

    def change(self, generation, key, value):
        if not isinstance(key, str):
            raise ValueError("Pixiv 浏览设置选项无效")
        if key == "ageRestriction":
            valid = type(value) is int and value in (0, 1, 2)
        else:
            valid = key in {"sensitive", "showAI"} and type(value) is bool
        if not valid:
            raise ValueError("Pixiv 浏览设置选项无效")
        owner = self._owner(generation)
        with self.pages.saved_cache.web_settings_mutex(owner):
            self.pages.check(generation)
            cached = self.pages.saved_cache.read(owner, "web", ["web-display-settings"])
            before = self._read_remote(owner, generation)
            if before[key] == value:
                recovering = self.pages.saved_cache.display_update_pending(owner, "web")
                if recovering or cached is not None and any(cached[field] != before[field] for field in self.FIELDS):
                    self._publish(owner, generation, before)
                else:
                    with self.pages.publication(generation):
                        self.pages.saved_cache.save(owner, "web", ["web-display-settings"], before)
                return self._snapshot(generation, before)
            if key == "ageRestriction" and value and not before["canChangeAge"]:
                raise AccountError("eligibility", "请先在 Pixiv 官网确认出生日期及年龄限制设置。")
            parser = HomepageIdentity()
            parser.feed(self.pages.request_homepage())
            if parser.user_id() != owner:
                raise AccountError("revoked", "官网账号已改变，请重新打开设置。")
            try:
                props = json.loads("".join(parser.chunks))["props"]["pageProps"]
                state = json.loads(props["serverSerializedPreloadedState"])
            except (KeyError, ValueError, TypeError) as error:
                raise AccountError("invalid_data", "官网验证信息格式已变化，请在官网设置后刷新。") from error
            api = state.get("api") if isinstance(state, dict) else None
            csrf = api.get("token") if isinstance(api, dict) else None
            if not isinstance(csrf, str) or not csrf or len(csrf) > 4096:
                raise AccountError("reauth", "官网验证信息不可用，请重新登录。")
            path, payload = {
                "ageRestriction": ("/ajax/settings/user_x_restrict", {"userXRestrict": value}),
                "sensitive": ("/ajax/settings/sensitive_view_setting", {"sensitiveViewSetting": int(value)}),
                "showAI": ("/ajax/settings/hide_ai_works", {"hideAiWorks": int(not value)}),
            }[key]
            with self.pages.publication(generation), self.pages.credential_publication():
                self.pages.check(generation)
                self.pages.saved_cache.begin_display_update(owner, "web")
                self.changed(owner, generation)
            failure = None
            try:
                self.post(path, payload, csrf, False, generation)
            except AccountError as error:
                self.pages.check(generation)
                failure = error
            # Keep pending state durable until readback confirms the outcome.
            after = self._read_remote(owner, generation)
            self._publish(owner, generation, after)
            if after[key] != value:
                if failure is not None:
                    raise failure
                raise AccountError("unconfirmed", "Pixiv 尚未应用该设置，请在官网确认后刷新。")
            return self._snapshot(generation, after)
