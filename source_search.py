"""Bounded SauceNAO queries; independent of Pixiv credentials and downloads."""
from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from source_results import SourceSearchError, normalize_candidates, parse_api, pixiv_id, thumbnail_url

from PIL import Image, ImageOps, UnidentifiedImageError
from auth_store import CREDENTIALW, CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ERROR_NOT_FOUND, advapi32, persistent_session_disabled


MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 40_000_000
QUERY_LONG_EDGE = 1200
HOME = "https://saucenao.com/"
RECEIPT_TTL = 900
RECEIPT_LIMIT = 256


def prepare_image(raw, *, minimum_side=16):
    if not raw or len(raw) > MAX_UPLOAD:
        raise SourceSearchError("请选择不超过 12 MiB 的图片", 413)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP"}:
                raise SourceSearchError("支持 JPG、PNG、WebP、GIF 和 BMP 图片")
            if source.width * source.height > MAX_PIXELS or min(source.size) < minimum_side:
                raise SourceSearchError(f"图片需至少 {minimum_side}×{minimum_side}，且不超过 4000 万像素")
            source.seek(0)
            frame = ImageOps.exif_transpose(source).convert("RGBA")
            frame.thumbnail((QUERY_LONG_EDGE, QUERY_LONG_EDGE), Image.Resampling.LANCZOS)
            image = Image.new("RGB", frame.size, "white")
            image.paste(frame, mask=frame.getchannel("A"))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
            return output.getvalue(), image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise SourceSearchError("图片无法解码或损坏，请换一张图片") from error


class SourceKey:
    def __init__(self):
        self.target = os.getenv("MOKU_SOURCE_CREDENTIAL_TARGET", "MOKU.SauceNAO.ApiKey")
        self.disabled = persistent_session_disabled() and not os.getenv("MOKU_SOURCE_CREDENTIAL_TARGET")

    def read(self):
        if self.disabled:
            return ""
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not advapi32.CredReadW(self.target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return ""
            raise ctypes.WinError()
        try:
            value = ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode("ascii")
        finally:
            advapi32.CredFree(pointer)
        if not re.fullmatch(r"[A-Za-z0-9]{20,128}", value):
            raise SourceSearchError("已保存的 SauceNAO Key 无法读取，请重新配置")
        return value

    def save(self, value):
        if self.disabled:
            raise SourceSearchError("当前隔离运行不保存凭据")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{20,128}", value):
            raise SourceSearchError("API Key 应为 20～128 位英文字母或数字")
        raw = value.encode("ascii")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=self.target, CredentialBlobSize=len(raw),
                                CredentialBlob=blob, Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="SauceNAO API")
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()

    def delete(self):
        if self.disabled:
            raise SourceSearchError("当前隔离运行不修改凭据")
        if not advapi32.CredDeleteW(self.target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
            raise ctypes.WinError()


@dataclass(slots=True)
class _SourceReceipt:
    created: float
    fingerprint: tuple[str, str] | None = None
    job_id: str | None = None
    result: dict | None = None


class SourceSearch:
    def __init__(self, directory: Path, proxy=lambda: ""):
        self.directory = directory
        self._closed = False
        self.proxy = proxy
        self.vault = SourceKey()
        self.lock = threading.RLock()
        self.jobs = OrderedDict()
        self.receipts: OrderedDict[str, _SourceReceipt] = OrderedDict()
        self.cache = OrderedDict()
        self.thumbnails = OrderedDict()
        self.thumbnail_bytes = 0
        self.process = None
        self.browser_identity = None
        self.api_browser_preferred = False
        self.website_login = "unknown"
        self.active = None
        self.epoch = 0
        self.cache_epoch = 0
        self.requests = []
        self.short_limit = 4
        self.settings_path = directory / "settings.json"
        self.mode = "guest"
        self.settings_error = ""
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.settings_error = "搜图设置无法读取，暂用游客方式；请重新保存搜图设置"
        else:
            if not isinstance(settings, dict) or not isinstance(settings.get("mode"), str) or settings["mode"] not in {"guest", "key"}:
                self.settings_error = "搜图设置损坏，暂用游客方式；请重新保存搜图设置"
            else:
                self.mode = settings["mode"]

    def _check_open(self):
        if self._closed:
            raise SourceSearchError("搜图服务已关闭，请重新打开程序", 503)

    def settings(self):
        with self.lock:
            return {"mode": self.mode, "keyConfigured": bool(self.vault.read()), "websiteLogin": self.website_login,
                    "error": self.settings_error, "cacheEpoch": self.cache_epoch}

    def cache_state(self):
        with self.lock:
            return {"cacheEpoch": self.cache_epoch}

    def configure(self, data):
        with self.lock:
            self._check_open()
            action = data.get("action", "save")
            if not isinstance(action, str) or action not in {"save", "clear", "reset-guest", "remove-key"}:
                raise SourceSearchError("搜图设置操作无效")
            if data.get("action") == "clear":
                active = self._job(self.active)
                if active and active["state"] in {"queued", "searching", "verification"}:
                    raise SourceSearchError("请先完成或取消当前搜图，再清除缓存", 409)
                self.cache.clear()
                self.thumbnails.clear()
                self.thumbnail_bytes = 0
                for job in self.jobs.values():
                    job.update(candidates=[], quota={}, image=b"", browserFallback=False)
                    if job["state"] == "done":
                        job["message"] = "搜图缓存已清除，请重新查询"
                self.epoch += 1
                self.cache_epoch = self.epoch
                return self.cache_state()
            if data.get("action") == "reset-guest":
                if self.process is not None and self.process.poll() is None:
                    raise SourceSearchError("请先关闭官网窗口，再重置会话", 409)
                profile = (self.directory / "guest-profile").absolute()
                if profile.exists():
                    if profile.is_symlink() or profile.is_junction() or profile.parent != self.directory.absolute():
                        raise SourceSearchError("游客资料路径异常，未执行清除")
                    shutil.rmtree(profile)
                self.website_login = "unknown"
                return self.settings()
            mode = data.get("mode", self.mode)
            if not isinstance(mode, str) or mode not in {"guest", "key"}:
                raise SourceSearchError("搜图方式无效")
            if data.get("action") == "remove-key":
                self.api_browser_preferred = False
                self.vault.delete()
                self.cache.clear()
                self.epoch += 1
            elif "key" in data:
                self.api_browser_preferred = False
                self.vault.save(data["key"])
                self.cache.clear()
                self.epoch += 1
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"mode": mode}), encoding="utf-8")
            os.replace(temporary, self.settings_path)
            self.mode = mode
            self.settings_error = ""
            return self.settings()

    def _job(self, identity):
        # Call under self.lock before deciding whether an operation still owns a task.
        job = self.jobs.get(identity)
        if job is not None and time.monotonic() - job["created"] > 600:
            if job["state"] in {"queued", "searching", "verification"}:
                self._fail(job, "查询已超时，请关闭官网窗口后重试")
            elif job["browserFallback"]:
                job.update(image=b"", browserFallback=False, message="API 验证任务已过期，请重新查询")
        return job

    def snapshot(self, identity):
        with self.lock:
            job = self._job(identity)
            if job is None:
                raise SourceSearchError("搜图记录已过期，请重新查询", 404)
            if (job["state"] in {"queued", "searching", "verification"} and job["browser"]
                    and identity == self.active and self.process is not None and self.process.poll() is not None):
                backend = job.get("browserHandoff")
                if backend is None:
                    self._fail(job, "官网窗口未能完成查询，请重试")
                else:
                    try:
                        self._open_browser(job, backend)
                    except SourceSearchError:
                        # The launch failure already belongs to this polled job.
                        pass
            return self._public_job(job)

    def _public_job(self, job):
        return {**{key: job[key] for key in
                   ("id", "mode", "state", "message", "candidates", "quota", "cached", "browserFallback")},
                "resultEpoch": job["epoch"], "cacheEpoch": self.cache_epoch}

    def _receipt(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", request_id):
            raise SourceSearchError("搜图请求编号无效")
        cutoff = time.monotonic() - RECEIPT_TTL
        while self.receipts and next(iter(self.receipts.values())).created <= cutoff:
            self.receipts.popitem(last=False)
        return self.receipts.get(request_id)

    def _remember_receipt(self, request_id, *, job_id=None, fingerprint=None, result=None):
        if len(self.receipts) >= RECEIPT_LIMIT:
            raise SourceSearchError("短期搜图回执已满，请稍后再试", 429)
        receipt = _SourceReceipt(time.monotonic(), fingerprint, job_id, result)
        self.receipts[request_id] = receipt
        return receipt

    def _receipt_result(self, receipt):
        if receipt.job_id is not None:
            return self.snapshot(receipt.job_id)
        return {**receipt.result, "cacheEpoch": self.cache_epoch}

    def _retain_job(self, job):
        identity = job["id"]
        self.jobs[identity] = job
        if len(self.jobs) <= 8:
            return
        retired = next(key for key in self.jobs if key not in {identity, self.active, self.browser_identity})
        old = self._job(retired)
        self.jobs.pop(retired)
        result = self._public_job(old)
        if result["state"] == "done":
            result["message"] = "原查询已完成，结果已从任务列表回收"
        result.update(candidates=[], quota={}, cached=False, browserFallback=False)
        for receipt in self.receipts.values():
            if receipt.job_id == retired:
                receipt.job_id = None
                receipt.result = result

    def receipt(self, request_id):
        with self.lock:
            self._check_open()
            receipt = self._receipt(request_id)
            if receipt is None:
                raise SourceSearchError("没有找到这次搜图的回执", 404)
            return self._receipt_result(receipt)

    def start(self, raw, mode, backend, *, request_id=None):
        if mode not in {"guest", "key"}:
            raise SourceSearchError("搜图方式无效")
        image, (width, height) = prepare_image(raw)
        digest = hashlib.sha256(image).hexdigest()
        with self.lock:
            self._check_open()
            fingerprint = (mode, digest)
            if request_id is not None:
                receipt = self._receipt(request_id)
                if receipt is not None:
                    if receipt.fingerprint is None:
                        receipt.fingerprint = fingerprint
                        receipt.result["mode"] = mode
                    elif receipt.fingerprint != fingerprint:
                        raise SourceSearchError("搜图请求编号已用于其他图片或方式", 409)
                    return self._receipt_result(receipt)
            old = self._job(self.active)
            if old and old["state"] in {"queued", "searching", "verification"}:
                if old["hash"] != digest or old["mode"] != mode:
                    raise SourceSearchError("已有搜图进行中，请先取消或等待", 409)
                if request_id is not None:
                    if sum(receipt.job_id == old["id"] for receipt in self.receipts.values()) >= 8:
                        raise SourceSearchError("已有搜图进行中，请先完成当前查询", 409)
                    self._remember_receipt(request_id, job_id=old["id"], fingerprint=fingerprint)
                return self.snapshot(old["id"])
            identity = secrets.token_urlsafe(18)
            job = {"id": identity, "hash": digest, "mode": mode, "state": "queued", "message": "正在准备查询…",
                   "candidates": [], "quota": {}, "cached": False, "browserFallback": False, "secret": secrets.token_urlsafe(32),
                   "image": image, "width": width, "height": height, "epoch": self.epoch, "browser": mode == "guest", "awaitingResponse": False, "created": time.monotonic()}
            cached = self.cache.get((mode, digest))
            if cached and time.monotonic() - cached[0] < 900:
                job.update(state="done", candidates=cached[1], quota=cached[2], cached=True, message="已复用最近的搜图结果", image=b"")
            now = time.monotonic()
            self.requests = [stamp for stamp in self.requests if now - stamp < 30]
            key = None
            if job["state"] != "done":
                if len(self.requests) >= self.short_limit:
                    job.update(state="error", message="短时间查询较多，请等待 30 秒再试", image=b"")
                elif mode == "key":
                    key = self.vault.read()
                    if not key:
                        job.update(state="error", message="请先配置 SauceNAO API Key", image=b"")
            if request_id is not None:
                self._remember_receipt(request_id, job_id=identity, fingerprint=fingerprint)
            self._retain_job(job)
            if job["state"] in {"done", "error"}:
                return self.snapshot(identity)
            if mode == "key":
                if self.api_browser_preferred:
                    job.update(browser=True, state="verification", message="正在复用 API 验证会话")
                    self.active = identity
                    self._open_browser(job, backend)
                    return self.snapshot(identity)
                self.active = identity
                try:
                    threading.Thread(target=self._query, args=(job, key), daemon=True, name="source-search").start()
                except (RuntimeError, OSError) as error:
                    self._fail(job, "无法启动搜图任务，请重试")
                    raise SourceSearchError(job["message"], 503) from error
            else:
                self._open_browser(job, backend)
                self.active = identity
                job.update(state="searching", message="官网窗口已打开，请按需手动完成验证")
            return self.snapshot(identity)

    def _open_browser(self, job, backend):
        if self.process is not None and self.process.poll() is None:
            # The existing window only owns this job once it requests its payload.
            job["browserHandoff"] = backend
            return
        job.pop("browserHandoff", None)
        identity = job["id"]
        environment = os.environ.copy()
        environment.pop("MOKU_DESKTOP_AUTH_TOKEN", None)
        environment.update(MOKU_SOURCE_JOB=identity, MOKU_SOURCE_SECRET=job["secret"], MOKU_SOURCE_BACKEND=backend,
                           MOKU_SOURCE_PROFILE=str(self.directory / "guest-profile"), MOKU_SOURCE_PROXY=self.proxy())
        command = [sys.executable, "--source-guest"] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).with_name("moku_app.py")), "--source-guest"]
        try:
            self.process = subprocess.Popen(command, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            self.browser_identity = identity
        except OSError as error:
            job.update(state="error", message="无法打开官网窗口，请重试", image=b"")
            raise SourceSearchError(job["message"], 503) from error

    def continue_api(self, identity, backend):
        with self.lock:
            self._check_open()
            job = self._job(identity)
            if not job or job["mode"] != "key" or not (job["browserFallback"] or job["browser"]) or not job["image"]:
                raise SourceSearchError("API 验证任务已过期，请重新选择图片查询", 409)
            if job["epoch"] != self.epoch:
                self._fail(job, "API 设置已修改，请重新查询")
                raise SourceSearchError("API 设置已修改，请重新查询", 409)
            active = self._job(self.active)
            if active and active["id"] != identity and active["state"] in {"queued", "searching", "verification"}:
                raise SourceSearchError("请先完成或取消当前查询", 409)
            if job["browser"] and job["state"] in {"searching", "verification"}:
                return self.snapshot(identity)
            job.update(browser=True, state="verification", browserFallback=False, awaitingResponse=False,
                       message="API 网页验证中，请在窗口完成验证后继续 API 查询")
            self.active = identity
            self._open_browser(job, backend)
            self.api_browser_preferred = True
            return self.snapshot(identity)

    def open_login(self, backend):
        with self.lock:
            self._check_open()
            active = self._job(self.active)
            if active and active["state"] in {"queued", "searching", "verification"}:
                if active["mode"] == "login":
                    return self.snapshot(active["id"])
                raise SourceSearchError("请先完成或取消当前查询，也可使用官网窗口菜单中的登录入口", 409)
            identity = secrets.token_urlsafe(18)
            job = {"id": identity, "hash": "", "mode": "login", "browser": True,
                   "state": "verification", "message": "请在 SauceNAO 官网窗口登录",
                   "image": b"", "candidates": [], "quota": {}, "cached": False,
                   "browserFallback": False, "awaitingResponse": False, "epoch": self.epoch,
                   "secret": secrets.token_urlsafe(32), "created": time.monotonic()}
            self._retain_job(job)
            self.active = identity
            self.website_login = "unknown"
            self._open_browser(job, backend)
            return self.snapshot(identity)

    def cancel(self, identity=None, *, request_id=None):
        with self.lock:
            if request_id is not None:
                receipt = self._receipt(request_id)
                if receipt is None:
                    cancelled = {"id": None, "mode": None, "state": "cancelled", "message": "已取消查询，尚未发出",
                                 "candidates": [], "quota": {}, "cached": False, "browserFallback": False,
                                 "resultEpoch": self.epoch}
                    receipt = self._remember_receipt(request_id, result=cancelled)
                if receipt.job_id is None:
                    return {"ok": True, "job": self._receipt_result(receipt)}
                identity = receipt.job_id
            job = self._job(identity)
            if job and job["state"] not in {"done", "error", "cancelled"}:
                job.update(state="cancelled", awaitingResponse=False, image=b"", message="已取消查询；已发出的请求可能仍计入额度")
            return {"ok": True, "job": self.snapshot(identity) if job else None}

    def _opener(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        proxy = self.proxy()
        handler = urllib.request.ProxyHandler({"https": proxy}) if proxy else urllib.request.ProxyHandler()
        return urllib.request.build_opener(handler, NoRedirect())

    def _api_request(self, job, key):
        boundary = "Moku" + secrets.token_hex(12)
        fields = {"output_type": "2", "numres": "16"}
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields.items()]
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="query.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode(),
                      job["image"], f'\r\n--{boundary}--\r\n'.encode()])
        query = urllib.parse.urlencode({"api_key": key, "output_type": "2", "numres": "16"})
        return urllib.request.Request(HOME + "search.php?" + query, data=b"".join(parts), headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary, "Accept": "application/json", "User-Agent": "MOKU/2.1.0"})


    def _query(self, job, key):
        try:
            request = self._api_request(job, key)
            opener = self._opener()
            with self.lock:
                if self._job(job["id"]) is not job or job["state"] not in {"queued", "searching", "verification"}:
                    return
                if job["epoch"] != self.epoch:
                    self._fail(job, "搜图设置已修改，请重新查询")
                    return
                self._reserve_send()
                job.update(state="searching", message="正在向 SauceNAO 查询…")
            with opener.open(request, timeout=35) as response:
                raw = response.read(1024 * 1024 + 1)
                content_type = response.headers.get_content_type()
            if len(raw) > 1024 * 1024:
                raise SourceSearchError("服务商响应过大", 502)
            if content_type != "application/json":
                raise SourceSearchError("API 请求需要网页验证，请点击「验证并继续 API」", 403)
            candidates, quota = parse_api(json.loads(raw))
            self._complete(job, candidates, quota)
        except urllib.error.HTTPError as error:
            message = "API 返回 403，可打开官网查看提示并继续" if error.code == 403 else "API Key 无效，请检查配置" if error.code == 401 else "查询过于频繁或额度不足，请稍后再试" if error.code == 429 else f"搜图服务返回 HTTP {error.code}"
            self._fail(job, message, browser_fallback=error.code == 403)
        except SourceSearchError as error:
            self._fail(job, str(error), browser_fallback=error.status == 403)
        except (OSError, ValueError, urllib.error.URLError):
            self._fail(job, "搜图连接失败或响应无效；为避免重复消耗额度，请手动重试")
        finally:
            with self.lock:
                if not job["browserFallback"] and not job["browser"]:
                    job["image"] = b""

    def _fail(self, job, message, *, browser_fallback=False):
        with self.lock:
            if job["state"] != "cancelled":
                job.update(state="error", awaitingResponse=False, message=message, image=job["image"] if browser_fallback else b"", browserFallback=browser_fallback)

    def _complete(self, job, candidates, quota):
        with self.lock:
            if self._job(job["id"]) is not job or job["state"] not in {"queued", "searching", "verification"}:
                return
            if job["epoch"] != self.epoch:
                self._fail(job, "搜图设置已修改，请重新查询")
                return
            for row in candidates:
                source = row.pop("thumbnail")
                if source:
                    token = secrets.token_urlsafe(24)
                    self.thumbnails[token] = {"url": source, "bytes": None}
                    row["thumb"] = "/api/source-search/image?token=" + token
                else:
                    row["thumb"] = ""
            # 16 retained query groups plus bounded job records, up to 64
            # sources each. Metadata outlives the separately bounded bytes.
            while len(self.thumbnails) > 1536:
                _,old=self.thumbnails.popitem(last=False)
                self.thumbnail_bytes-=len(old["bytes"] or b"")
            job.update(state="done", awaitingResponse=False, message="已找到匹配来源" if candidates else "没有找到匹配来源", candidates=candidates, quota=quota, image=b"")
            if quota.get("short_limit", 0) > 0:
                self.short_limit = quota["short_limit"]
            self.cache[(job["mode"], job["hash"])] = (time.monotonic(), candidates, quota)
            while len(self.cache) > 16:
                self.cache.popitem(last=False)

    def _reserve_send(self):
        now = time.monotonic()
        self.requests = [stamp for stamp in self.requests if now - stamp < 30]
        if len(self.requests) >= self.short_limit:
            raise SourceSearchError("请等待 30 秒后再提交查询", 429)
        self.requests.append(now)

    def browser(self, identity, secret, data):
        with self.lock:
            self._check_open()
            job = self._job(identity)
            if job is None or not secret.isascii() or not secrets.compare_digest(job["secret"], secret) or not job["browser"]:
                raise SourceSearchError("官网窗口授权无效", 403)
            action = data.get("action")
            if action == "payload":
                if job["state"] in {"cancelled", "done", "error"}:
                    return {"image": "", "state": job["state"], "mode": job["mode"]}
                self.browser_identity = identity
                job.pop("browserHandoff", None)
                return {"image": base64.b64encode(job["image"]).decode(), "state": job["state"], "mode": job["mode"]}
            if action == "session":
                state = data.get("login")
                if state not in ("signed-in", "signed-out"):
                    raise SourceSearchError("官网登录状态无效")
                self.website_login = state
                if job["mode"] == "login" and state == "signed-in" and job["state"] in {"queued", "searching", "verification"}:
                    job.update(state="done", message="SauceNAO 官网已登录")
                return {"state": job["state"]}
            if action == "api-request":
                if job["mode"] != "key" or job["state"] not in {"searching", "verification"} or not job["image"]:
                    raise SourceSearchError("API 验证请求无效", 409)
                if job["epoch"] != self.epoch:
                    self._fail(job, "API 设置已修改，请重新查询")
                    raise SourceSearchError("API 设置已修改", 409)
                if job["awaitingResponse"]:
                    raise SourceSearchError("API 请求正在等待响应，请勿重复提交", 409)
                key = self.vault.read()
                if not key:
                    self._fail(job, "请重新配置 API Key")
                    raise SourceSearchError("请重新配置 API Key", 401)
                request = self._api_request(job, key)
                return {"url": request.full_url, "body": base64.b64encode(request.data).decode(), "contentType": request.get_header("Content-type")}
            if action == "poll":
                active = self._job(self.active)
                if active and active["id"] != identity and active["browser"] and active["state"] in {"queued", "searching", "verification"}:
                    return {"state": job["state"], "next": {"id": active["id"], "secret": active["secret"]}}
                return {"state": job["state"]}
            if job["state"] in {"cancelled", "done", "error"}:
                return {"state": job["state"]}
            previous = job["state"]
            if action in {"verification", "result", "not-sent", "access-denied", "rate-limited"} and data.get("attempt") != job.get("attempt"):
                raise SourceSearchError("请求已交接，请使用当前官网页面", 409)
            if action == "result":
                if not job["awaitingResponse"]:
                    raise SourceSearchError("没有等待中的查询", 409)
                try:
                    if job["mode"] == "key" and "json" not in data:
                        raise SourceSearchError("API 返回了网页内容，请继续 API 验证后重试")
                    candidates, quota = parse_api(data["json"]) if "json" in data else (normalize_candidates(data.get("rows")), {})
                    self._complete(job, candidates, quota)
                except SourceSearchError as error:
                    self._fail(job, str(error))
            elif action == "verification":
                job.update(state="verification", awaitingResponse=False, message="请在官网窗口手动通过验证，再点击窗口上方「提交查询」")
            elif action == "access-denied":
                job.update(state="verification", awaitingResponse=False, message="官网返回 403，请查看官网提示。如有验证，请手动完成")
            elif action == "rate-limited":
                self._fail(job, "官网限制了查询频率或额度，请稍后再试")
            elif action == "submitted":
                if self.browser_identity != identity or job["mode"] == "login" or not job["image"]:
                    raise SourceSearchError("官网窗口没有领到可提交的任务", 409)
                if job["epoch"] != self.epoch:
                    self._fail(job, "搜图设置已修改，请重新查询")
                    return {"state": "error"}
                if job["awaitingResponse"]:
                    raise SourceSearchError("查询正在等待响应，请勿重复提交", 409)
                self._reserve_send()
                attempt = job.get("attempt", 0) + 1
                job.update(attempt=attempt, awaitingResponse=True, state="searching",
                           message="官网正在查询，请等待结果；无需重复提交")
                return {"state":"searching","attempt":attempt}
            elif action == "not-sent":
                job.update(awaitingResponse=False, message="官网尚未准备好，可在窗口重试")
            elif action == "closed":
                self._fail(job, "官网窗口已关闭，可重新查询")
            elif action == "error":
                self._fail(job, "官网暂未完成查询；请检查验证、额度或网络后重试")
            elif action == "connection-error":
                self._fail(job, "官网连接失败，请关闭官网窗口后重试；请检查当前网络能否访问 SauceNAO")
            else:
                raise SourceSearchError("游客操作无效")
            if previous != job["state"] or action in {"submitted", "result"}:
                logging.getLogger("moku.source").info("guest %s -> %s, candidates=%d", action, job["state"], len(job["candidates"]))
            return {"state": job["state"], "count": len(job["candidates"])}

    @contextmanager
    def _thumbnail_publication(self, token, expected=None):
        with self.lock:
            self._check_open()
            row = self.thumbnails.get(token)
            if row is None or (expected is not None and row is not expected):
                raise SourceSearchError("候选缩略图已过期", 404)
            yield row

    def _read_thumbnail(self, url):
        try:
            with self._opener().open(urllib.request.Request(url, headers={"User-Agent": "MOKU/2.1.0"}), timeout=12) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise SourceSearchError("缩略图过大", 502)
            image, _ = prepare_image(raw, minimum_side=1)
        except SourceSearchError as error:
            raise SourceSearchError("来源缩略图暂不可读，可重试或打开来源链接", 502) from error
        except (OSError, urllib.error.URLError) as error:
            raise SourceSearchError("候选缩略图加载失败", 502) from error
        return image

    def image(self, token, *, deliver=None):
        with self._thumbnail_publication(token) as row:
            self.thumbnails.move_to_end(token)
            image = row["bytes"]
        if image is None:
            loaded = self._read_thumbnail(row["url"])
            with self._thumbnail_publication(token, row):
                if row["bytes"] is None:
                    row["bytes"] = loaded
                    self.thumbnail_bytes += len(loaded)
                    for old in self.thumbnails.values():
                        if self.thumbnail_bytes <= 16 * 1024 * 1024:
                            break
                        if old is not row and old["bytes"] is not None:
                            self.thumbnail_bytes -= len(old["bytes"])
                            old["bytes"] = None
                image = row["bytes"]
        if deliver is not None:
            return deliver(image, lambda: self._thumbnail_publication(token, row))
        return image

    def close(self):
        with self.lock:
            self._closed = True
            self.cache.clear()
            self.thumbnails.clear()
            self.thumbnail_bytes = 0
            for job in self.jobs.values():
                job["image"] = b""
                if job["state"] not in {"done", "error"}:
                    job.update(state="cancelled", image=b"")
