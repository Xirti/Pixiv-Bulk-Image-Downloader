"""One pending system-browser authorization, with a temporary owned URI callback."""
import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
from pathlib import Path
import queue
import secrets
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import urllib.request
import winreg

from download_history import history_path
from pixiv_account import AccountError

PROTOCOL = r"Software\Classes\pixiv"
PROTOCOL_MUTEX = "Local\\MOKU.PixivAuthorization." + hashlib.sha256(str(Path.home()).casefold().encode()).hexdigest()[:32]


@contextmanager
def protocol_guard(name=PROTOCOL_MUTEX):
    """Serialize the shared URI registration, including orphan recovery/cleanup."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 1000)
        if result == 258:
            raise AccountError("authorization_busy", "另一窗口正在处理账号授权，请稍后重试")
        if result not in (0, 0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def decode_callback(value, state):
    if not isinstance(value, str) or not 1 <= len(value) <= 8192:
        raise ValueError("invalid_callback")
    parsed = urlsplit(value.strip())
    native = parsed.scheme == "pixiv" and parsed.netloc == "account" and parsed.path in ("", "/", "/login")
    web = parsed.scheme == "https" and parsed.netloc == "app-api.pixiv.net" and parsed.path == "/web/v1/users/auth/pixiv/callback"
    if not (native or web) or parsed.fragment:
        raise ValueError("invalid_callback_destination")
    params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
    if "error" in params or len(params.get("code", [])) != 1 or len(params.get("state", [])) > 1:
        raise ValueError("invalid_callback_fields")
    code = params["code"][0]
    if not 8 <= len(code) <= 1024 or not code.isascii() or any(char.isspace() for char in code):
        raise ValueError("invalid_callback_code")
    echoed = params.get("state", [None])[0]
    if echoed is not None and not hmac.compare_digest(echoed, state):
        raise ValueError("callback_state_mismatch")
    return code


def callback_file():
    return history_path().with_name("account-callback.json")


def deliver_callback(value):
    connection = json.loads(callback_file().read_text(encoding="utf-8"))
    port, token = connection["port"], connection["token"]
    if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(token, str):
        raise ValueError("invalid_local_callback")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/account/callback",
        data=json.dumps({"callback": value}).encode(),
        headers={"Content-Type": "application/json", "X-MOKU-Request-Token": token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=8) as response:
        if response.status != 200:
            raise ValueError("callback_not_accepted")


class AccountAuthorization:
    def __init__(self, pages):
        self.pages = pages
        self.lock = threading.RLock()
        self.flow = None

    def begin(self, port, token, generation):
        self.pages.check(generation)
        with self.lock, protocol_guard():
            if self.flow:
                if self.flow["generation"] != generation:
                    raise AccountError("revoked", "账号状态已改变，请重新打开我的账号")
                return self.flow["authorizeUrl"]
            try:account = self.pages.account()
            except AccountError as error:
                if error.kind!="invalid_credential":raise
                self.pages.disconnect()
                account=self.pages.account()
            expected_uid=account.snapshot()["uid"] or None
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}" --pixiv-account-callback "%1"'
            else:
                interpreter = Path(sys.executable).with_name("pythonw.exe")
                command = f'"{interpreter if interpreter.is_file() else Path(sys.executable)}" "{Path(__file__).with_name("moku_app.py")}" --pixiv-account-callback "%1"'
            self._recover_owned_callback(command)
            for hive, path in ((winreg.HKEY_CURRENT_USER, PROTOCOL), (winreg.HKEY_CLASSES_ROOT, "pixiv"),
                               (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\pixiv")):
                try:
                    with winreg.OpenKey(hive, path):
                        raise AccountError("protocol", "已有 Pixiv 回调关联，MOKU 未修改；请检查现有授权程序")
                except FileNotFoundError:
                    pass
            verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            url = "https://app-api.pixiv.net/web/v1/login?" + urlencode({"code_challenge": challenge,
                "code_challenge_method": "S256", "client": "pixiv-android", "state": state})
            flow = {"queue": queue.Queue(maxsize=1), "state": state, "command": command, "cancel": threading.Event(),
                    "generation": generation, "authorizeUrl": url, "account": account,
                    "callback": json.dumps({"port": port, "token": token, "command": command, "owner": state})}
            self.flow = flow
            try:
                path = callback_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + "." + state + ".tmp")
                try:
                    temporary.write_text(flow["callback"], encoding="utf-8")
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PROTOCOL) as key:
                    flow["registered"] = True
                    winreg.SetValueEx(key, "MOKU Owner", 0, winreg.REG_SZ, state)
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:MOKU Pixiv account")
                    winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PROTOCOL + r"\shell\open\command") as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
            except Exception:
                self._cleanup(flow)
                raise
            self.pages.auth_status = {"pending": True, "error": ""}
            epoch = account.snapshot()["generation"]

            def wait():
                try:
                    deadline = time.monotonic() + 600
                    while time.monotonic() < deadline and not flow["cancel"].is_set():
                        try:
                            code = flow["queue"].get(timeout=.5)
                            break
                        except queue.Empty:
                            continue
                    else:
                        raise AccountError("timeout", "授权等待已结束，可重新连接")
                    self.pages.check(generation)
                    account.connect(code, verifier, expected_uid, epoch)
                    self.pages.check(generation)
                    with self.lock:
                        if self.flow is flow and not flow["cancel"].is_set():
                            self.pages.invalidate()
                            self.pages.auth_status = {"pending": False, "error": ""}
                except Exception as error:
                    with self.lock:
                        if self.flow is flow:
                            self.pages.auth_status = {"pending": False, "error": str(error) if isinstance(error, AccountError) else "账号连接暂时失败，请重试"}
                finally:
                    with self.lock:
                        self._cleanup(flow)

            try:
                threading.Thread(target=wait, name="moku-account-auth", daemon=True).start()
            except Exception:
                self.pages.auth_status = {"pending": False, "error": "账号授权启动失败，请重试"}
                self._cleanup(flow)
                raise
            return url

    def _recover_owned_callback(self, command):
        """Reclaim only this executable's orphaned registration, never a live backend."""
        try:
            record=json.loads(callback_file().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (ValueError,UnicodeError):
            raise AccountError("protocol","回调记录损坏，请检查 MOKU 的回调关联")
        if not isinstance(record,dict) or type(record.get("port")) is not int or not 1024<=record["port"]<=65535:
            raise AccountError("protocol","回调记录无效，请检查 MOKU 的回调关联")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,PROTOCOL+r"\shell\open\command") as key:
                registered=winreg.QueryValueEx(key,"")[0]
        except FileNotFoundError:
            return
        if registered!=command or record.get("command",command)!=command:
            return
        try:
            with socket.create_connection(("127.0.0.1",record["port"]),timeout=.2):
                return
        except ConnectionRefusedError:
            pass
        except OSError:
            return
        for suffix in (r"\shell\open\command",r"\shell\open",r"\shell",""):
            try:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,PROTOCOL+suffix)
            except FileNotFoundError:pass
        callback_file().unlink(missing_ok=True)

    def cancel(self, url):
        with self.lock:
            if self.flow and self.flow["authorizeUrl"] == url:
                self.close()

    def receive(self, value):
        with self.lock:
            if not self.flow:
                raise ValueError("no_pending_authorization")
            self.flow["queue"].put_nowait(decode_callback(value, self.flow["state"]))

    def _cleanup(self, flow):
        if self.flow is not flow:
            return
        with protocol_guard():
            try:
                current = owner = None
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL + r"\shell\open\command") as key:
                        current = winreg.QueryValueEx(key, "")[0]
                except FileNotFoundError:
                    pass
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROTOCOL) as key:
                        owner = winreg.QueryValueEx(key, "MOKU Owner")[0]
                except FileNotFoundError:
                    pass
                path = callback_file()
                try:
                    owned_file = path.read_text(encoding="utf-8") == flow["callback"]
                except FileNotFoundError:
                    owned_file = False
                owned_key = owner == flow["state"] or owner is None and flow.get("registered") and owned_file
                if owned_key and current in (None, flow["command"]):
                    for suffix in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
                        try:
                            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, PROTOCOL + suffix)
                        except FileNotFoundError:
                            pass
                if owned_file:
                    path.unlink(missing_ok=True)
            finally:
                self.flow = None

    def close(self):
        with self.lock:
            if self.flow:
                self.flow["cancel"].set()
                self.flow["account"].fence()
                try:
                    self._cleanup(self.flow)
                finally:
                    self.pages.auth_status = {"pending": False, "error": ""}
