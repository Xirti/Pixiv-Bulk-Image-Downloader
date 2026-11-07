"""Pixiv App account credentials, native vault and guarded token refresh."""
from contextvars import ContextVar
from dataclasses import dataclass, field
import ctypes
import json
import math
import os
import re
import threading
import time
from urllib.parse import urlencode

from curl_cffi.requests import Session
from curl_cffi.requests.exceptions import RequestException
from auth_store import CREDENTIALW, CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ERROR_NOT_FOUND, advapi32, persistent_session_disabled
from request_coordinator import RequestCoordinator, REQUEST_PRIORITY
from shared_request_budget import SharedRequestBudget
from pixiv_adapter import PixivPolicyError


ACCOUNT_REQUEST_CHECK=ContextVar("account_request_check",default=lambda:None)

class AccountError(PixivPolicyError):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class Tokens:
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    expires: float
    uid: str
    name: str

    @classmethod
    def from_response(cls, raw, previous=None, now=None):
        if not isinstance(raw, dict):
            raise AccountError("invalid_data", "Pixiv 登录响应格式异常")
        access = raw.get("access_token")
        refresh = raw["refresh_token"] if "refresh_token" in raw else previous.refresh if previous else None
        duration = raw.get("expires_in")
        user = raw.get("user")
        uid = str(user.get("id", "")) if isinstance(user, dict) else previous.uid if previous else ""
        name = str(user.get("name", ""))[:80] if isinstance(user, dict) else previous.name if previous else ""
        if (not isinstance(access, str) or not access or not access.isascii() or len(access) > 4096
                or not isinstance(refresh, str) or not refresh or not refresh.isascii() or len(refresh) > 4096
                or any(char.isspace() for char in access + refresh)
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
                or not re.fullmatch(r"[0-9]{1,30}", uid)):
            raise AccountError("invalid_data", "Pixiv 未返回完整有效的账号凭据")
        if previous and uid != previous.uid:
            raise AccountError("identity_mismatch", "刷新返回了不同账号，未替换原凭据")
        return cls(access, refresh, (time.time() if now is None else now) + duration, uid, name)


class CredentialVault:
    TARGET = "MOKU.Pixiv.AppOAuth"

    def __init__(self, target=None):
        self.target = target or os.environ.get("MOKU_APP_CREDENTIAL_TARGET") or self.TARGET
        self.legacy = False
        self.disabled = persistent_session_disabled() and target is None and not os.environ.get("MOKU_APP_CREDENTIAL_TARGET")

    def read(self):
        if self.disabled:
            return None
        value = self._read_target(self.target)
        if value is None and self.target == self.TARGET:
            value = self._read_target("MOKU.Pixiv.AppOAuth.Development")
            self.legacy = value is not None
        return value

    def _read_target(self, target):
        pointer = ctypes.POINTER(CREDENTIALW)()
        if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return None
            raise ctypes.WinError()
        try:
            credential = pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise AccountError("invalid_credential", "已保存的账号凭据无法读取，请重新连接") from error
        finally:
            advapi32.CredFree(pointer)
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise AccountError("invalid_credential", "账号凭据版本不受支持")
        values = [data.get(key) for key in ("access", "refresh", "expires", "uid", "name")]
        access, refresh, expires, uid, name = values
        if (not isinstance(access, str) or not access or not access.isascii() or len(access) > 4096
                or not isinstance(refresh, str) or not refresh or not refresh.isascii() or len(refresh) > 4096
                or any(char.isspace() for char in access + refresh)
                or type(expires) not in (int, float) or not math.isfinite(expires) or expires < 0
                or not isinstance(uid, str) or not re.fullmatch(r"[0-9]{1,30}", uid)
                or not isinstance(name, str) or len(name) > 80):
            raise AccountError("invalid_credential", "已保存的账号凭据格式异常")
        return Tokens(access, refresh, expires, uid, name)

    def save(self, tokens):
        if self.disabled:
            raise AccountError("isolated", "当前隔离运行禁止保存账号凭据")
        raw = json.dumps({"schema": 1, "access": tokens.access, "refresh": tokens.refresh,
                          "expires": tokens.expires, "uid": tokens.uid, "name": tokens.name}, ensure_ascii=False).encode("utf-8")
        if len(raw) > 2560:
            raise AccountError("credential_capacity", "账号凭据超过 Windows 保存容量，本次未保存")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=self.target, CredentialBlobSize=len(raw),
                                CredentialBlob=blob, Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="MOKU Pixiv App account")
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()

    def promote(self, tokens):
        if not self.legacy:
            return
        self.save(tokens)
        if not advapi32.CredDeleteW("MOKU.Pixiv.AppOAuth.Development", CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
            raise ctypes.WinError()
        self.legacy = False

    def delete(self):
        if self.disabled:
            return
        targets = [self.target]
        if self.target == self.TARGET:
            targets.append("MOKU.Pixiv.AppOAuth.Development")
        for target in targets:
            if not advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
                raise ctypes.WinError()


class AppTransport:
    HEADERS = {"Accept": "application/json", "App-OS": "android", "App-OS-Version": "14",
               "App-Version": "6.0.0", "User-Agent": "PixivAndroidApp/6.0.0 (Android 14)", "Accept-Language": "zh-cn"}

    def __init__(self, settings_path, proxy="", coordinator=None):
        self.coordinator = coordinator or RequestCoordinator(settings_path, shared=SharedRequestBudget())
        self.proxy = proxy
        self._session = Session(impersonate="chrome152", trust_env=False)
        self._network_lock = threading.Lock()

    def request(self, method, path, *, bearer=None, form=None, check=lambda: None):
        token_request = path == "/auth/token"
        if method not in {"GET", "POST"} or not path.startswith("/") or path.startswith("//") or "#" in path:
            raise ValueError("unsupported_app_request")
        url = ("https://oauth.secure.pixiv.net" if token_request else "https://app-api.pixiv.net") + path
        if token_request and (method != "POST" or bearer):
            raise ValueError("invalid_token_request")
        headers = dict(self.HEADERS)
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        chunks = []
        received = 0

        def consume(chunk):
            nonlocal received
            check()
            received += len(chunk)
            if received > 4 * 1024 * 1024:
                raise AccountError("response_size", "Pixiv 账号响应过大")
            chunks.append(chunk)
            return len(chunk)

        check()
        priority=0 if token_request else REQUEST_PRIORITY.get()
        while not self._network_lock.acquire(timeout=.05):
            check()
        try:
            return self._locked_request(method, url, headers, form, check, priority, chunks, consume)
        finally:
            self._network_lock.release()

    def _locked_request(self, method, url, headers, form, check, priority, chunks, consume):
        with self.coordinator.slot(False, check, priority=priority):
            for attempt in range(2 if method=="GET" else 1):
                self.coordinator.before_send(False, check, priority=priority, activity="search")
                chunks.clear()
                try:
                    response = self._session.request(method, url, headers=headers, data=form, proxy=self.proxy or None,
                                                     timeout=20, allow_redirects=False, discard_cookies=True, content_callback=consume)
                    break
                except RequestException as error:
                    check()
                    self.coordinator.failure()
                    if method=="GET" and attempt==0 and int(getattr(error,"code",0)) in {5,6,7,18,28,35,52,55,56}:
                        continue
                    raise AccountError("network", "Pixiv 账号连接暂时失败，请稍后重试；已保存的凭据仍保留") from error
        check()
        if response.status_code == 429:
            raise self.coordinator.limit(response.headers.get("Retry-After"))
        if not response.headers.get("Content-Type", "").startswith("application/json"):
            raise AccountError("verification" if response.status_code in (400, 403) else "invalid_data",
                               "Pixiv 要求网页验证或拒绝本次请求，请在官方页面处理" if response.status_code in (400, 403) else "Pixiv 返回了非 JSON 账号数据")
        try:
            data = json.loads(b"".join(chunks))
        except (UnicodeError, ValueError) as error:
            raise AccountError("invalid_data", "Pixiv 账号响应无法解析") from error
        if not isinstance(data, dict):
            raise AccountError("invalid_data", "Pixiv 账号响应结构异常")
        if response.status_code >= 400 or data.get("error") or data.get("has_error"):
            error = data.get("error")
            if response.status_code == 401 or error == "invalid_grant":
                raise AccountError("reauth", "Pixiv 登录凭据已失效，请重新连接")
            raise AccountError("rejected", f"Pixiv 拒绝本次操作（HTTP {response.status_code}），未自动改为公开")
        self.coordinator.success()
        return data

    def close(self):
        self._session.close()


class AppAccount:
    def __init__(self, vault, transport, client_config, clock=time.time):
        self.vault, self.transport, self.client_config, self.clock = vault, transport, client_config, clock
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._operations = threading.Lock()
        self._epoch = 0
        self._tokens = vault.read()
        self._refresh_error = ""
        self._reauth_required = False

    def snapshot(self):
        with self._lock:
            tokens = self._tokens
            return {"connected": tokens is not None, "name": tokens.name if tokens else "",
                    "uid": tokens.uid if tokens else "", "saved": tokens is not None,
                    "expiresIn": max(0, round(tokens.expires - self.clock())) if tokens else 0,
                    "refreshError": self._refresh_error, "generation": self._epoch, "needsAuthorization": self._reauth_required}

    def _check(self, epoch):
        with self._lock:
            if epoch != self._epoch:
                raise AccountError("revoked", "账号状态已改变，请重新操作")

    def _commit(self, tokens, epoch):
        with self._lock:
            self._check(epoch)
            self.vault.save(tokens)
            self._tokens = tokens
            self._refresh_error = ""
            self._reauth_required = False

    def connect(self, code, verifier, expected_uid=None, epoch=None):
        with self._lock:
            epoch = self._epoch if epoch is None else epoch
        self._check(epoch)
        raw = self.transport.request("POST", "/auth/token", form={**self.client_config,
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback", "include_policy": "true"},
            check=lambda: self._check(epoch))
        tokens = Tokens.from_response(raw, now=self.clock())
        if expected_uid and tokens.uid != expected_uid:
            raise AccountError("identity_mismatch", "授权账号与当前网页账号不同，本次未保存，请使用同一账号")
        detail = self.transport.request("GET", "/v1/user/detail?" + urlencode({"user_id": tokens.uid, "filter": "for_android"}),
                                        bearer=tokens.access, check=lambda: self._check(epoch))
        user = detail.get("user")
        if not isinstance(user, dict) or str(user.get("id")) != tokens.uid:
            raise AccountError("identity_mismatch", "App API 未确认相同账号，本次未保存")
        self._commit(tokens, epoch)
        self.transport.coordinator.session_connected()
        return self.snapshot()

    def access(self, force=False):
        with self._lock:
            tokens, epoch = self._tokens, self._epoch
            if tokens is None or self._reauth_required:
                raise AccountError("reauth", "请先连接 Pixiv 账号")
            if not force and tokens.expires - self.clock() > 300:
                return tokens, epoch
        with self._refresh_lock:
            with self._lock:
                self._check(epoch)
                tokens = self._tokens
                if not force and tokens.expires - self.clock() > 300:
                    return tokens, epoch
            try:
                raw = self.transport.request("POST", "/auth/token", form={**self.client_config,
                    "grant_type": "refresh_token", "refresh_token": tokens.refresh, "include_policy": "true"},
                    check=lambda: self._check(epoch))
                updated = Tokens.from_response(raw, previous=tokens, now=self.clock())
                self._commit(updated, epoch)
                return updated, epoch
            except Exception as error:
                with self._lock:
                    self._check(epoch)
                    self._refresh_error = str(error) if isinstance(error, AccountError) else "刷新暂时失败，请稍后重试"
                    if isinstance(error, AccountError) and error.kind == "reauth":
                        self._reauth_required = True
                    if not force and tokens.expires > self.clock() and not (isinstance(error, AccountError) and error.kind == "reauth"):
                        return tokens, epoch
                raise

    def request(self, method, path, *, form=None, expected_epoch=None, check=None):
        check=check or ACCOUNT_REQUEST_CHECK.get()
        check()
        tokens, epoch = self.access()
        if expected_epoch is not None:
            self._check(expected_epoch)
            if epoch != expected_epoch:
                raise AccountError("revoked", "账号状态已改变，请重新操作")
        def current():
            self._check(epoch)
            check()
        try:
            return self.transport.request(method, path, form=form, bearer=tokens.access, check=current)
        except AccountError as error:
            if error.kind!="reauth":raise
            if method=="GET":
                tokens, epoch = self.access(force=True)
                try:
                    return self.transport.request(method,path,form=form,bearer=tokens.access,check=current)
                except AccountError as retry:
                    if retry.kind!="reauth":raise
            with self._lock:
                self._check(epoch)
                self._reauth_required=True
                self._refresh_error="Pixiv 登录已失效，请重新授权"
            raise AccountError("reauth","Pixiv 登录已失效，请重新授权")

    def verify(self, force_refresh=False):
        tokens, epoch = self.access(force_refresh)
        raw = self.transport.request("GET", "/v1/user/detail?" + urlencode({"user_id": tokens.uid, "filter": "for_android"}),
                                     bearer=tokens.access, check=lambda: self._check(epoch))
        user = raw.get("user")
        if not isinstance(user, dict) or str(user.get("id")) != tokens.uid:
            raise AccountError("identity_mismatch", "账号资料与当前会话不一致")
        return {**self.snapshot(), "identityVerified": True, "refreshRequested": force_refresh}

    def promote_verified(self):
        with self._lock:
            if self._tokens:
                self.vault.promote(self._tokens)

    def fence(self):
        with self._lock:
            self._epoch += 1

    def logout(self):
        with self._lock:
            self._epoch += 1
            self._tokens = None
            self._refresh_error = ""
            self._reauth_required = False
            self.vault.delete()
