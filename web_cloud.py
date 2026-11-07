"""Bounded, single-attempt Web writes; shares the application's request budget."""
import json
import threading

from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException
from pixiv_account import AccountError


class WebCloudTransport:
    def __init__(self, coordinator, context):
        self.coordinator, self.context = coordinator, context
        self.lock = threading.Lock()
        self.session = None

    def post(self, path, payload, csrf, form, generation):
        # Only CloudActions selects these endpoints; this is not a general-purpose proxy.
        if path not in {"/ajax/illusts/bookmarks/add", "/ajax/illusts/bookmarks/remove", "/ajax/illusts/bookmarks/remove_tags", "/bookmark_add.php",
                        "/rpc_group_setting.php", "/ajax/following/user/restrict_change"}:
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
                raise AccountError("rejected", f"官网拒绝本次操作（HTTP {response.status_code}），未自动更改隐私")
            self.coordinator.success()
            return raw
        finally:
            self.lock.release()
