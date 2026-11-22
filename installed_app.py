"""Current-user installation registration, separate from portable user data."""
from __future__ import annotations

import logging
import ctypes
import json
import os
from pathlib import Path
import shutil
import winreg

from edition_profile import DATA_FOLDER

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MOKU_is1"
LOG = logging.getLogger("moku.installation")
class InstalledDataCleanupError(OSError):
    def __init__(self, failures):
        self.remaining = tuple(failures)
        lines = [f"{name}：{error}" for name, error in failures.items()]
        super().__init__("个人数据未清理完毕。请处理以下项目后再次清理：\n" + "\n".join(lines))


INSTALLATION_GUARD_NAME = r"Local\MOKU.InstallationGuard.v1"
MAINTENANCE_GATE_NAME = r"Local\MOKU.MaintenanceGate.v1"


def hold_installation_guard(*, maintenance: bool = False) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    gate = None
    if not maintenance:
        gate = kernel.CreateMutexW(None, False, MAINTENANCE_GATE_NAME)
        if not gate:
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel.WaitForSingleObject(gate, 0)
        if result not in (0, 0x80):
            error = ctypes.get_last_error()
            kernel.CloseHandle(gate)
            if result == 258:
                raise ValueError("MOKU 正在安装或卸载，请完成后再启动")
            raise ctypes.WinError(error)
    try:
        handle = kernel.CreateMutexW(None, False, INSTALLATION_GUARD_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        # Windows releases this presence marker when the process fully exits.
        return handle
    finally:
        if gate:
            kernel.ReleaseMutex(gate)
            kernel.CloseHandle(gate)


def is_installed(program_root: Path) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location, kind = winreg.QueryValueEx(key, "InstallLocation")
    except FileNotFoundError:
        return False
    if kind != winreg.REG_SZ or not location:
        return False
    return (Path(location) / "app").resolve() == program_root.resolve()


def installed_data_root(program_root: Path) -> Path:
    if is_installed(program_root):
        return Path(os.environ["LOCALAPPDATA"]) / DATA_FOLDER
    return program_root


def refresh_installed_version(program_root: Path, version: str) -> None:
    """Keep Windows' installed-app version current after an in-app upgrade."""
    try:
        if is_installed(program_root):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
    except OSError:
        LOG.warning("Could not update Windows installation version", exc_info=True)


def clear_installed_user_data(program_root: Path) -> None:
    """Remove explicitly owned metadata. Download files have no cleanup entry."""
    if not is_installed(program_root):
        raise ValueError("请通过已安装的 MOKU 卸载程序清理数据")
    data = Path(os.environ["LOCALAPPDATA"]) / DATA_FOLDER
    files = [
        "library/" + name
        for name in (
            "downloads.sqlite3", "requests.sqlite3", "catalog.sqlite3", "workspace.sqlite3",
            "workspace.v1-backup.sqlite3",
            "account-browse.sqlite3", "account-cache.sqlite3", "cloud-state.sqlite3",
            "guide-v1.seen", "login-context.json", "request-settings.json", "auth-method.json",
            "pixiv-logout-revision", "pixiv-logout-revision.tmp",
            "pixiv-web-session-revision", "pixiv-web-session-revision.tmp",
            "pixiv-app-session-revision", "pixiv-app-session-revision.tmp",
            "browser-profile.json", "app-updates.json", "app-updates.last-check",
            "app-updates.trust-state.json", "account-callback.json", "source-search/settings.json",
        )
    ]
    files += [name + suffix for name in files if name.endswith(".sqlite3") for suffix in ("-wal", "-shm", "-journal")]
    files += [temporary for name in files if name.endswith(".json")
              for temporary in (name + ".tmp", str(Path(name).with_suffix(".tmp")))]
    files += ["logs/moku-app.log" + suffix for suffix in ("", ".1", ".2", ".3")]
    files += ["runtime/backend.json", "runtime/backend.json.tmp"]
    directories = ["WebView2Sessions", "library/source-search/guest-profile"]
    paths = [data / name for name in files + directories]
    # Preflight every deletion, including browser profile descendants, before touching credentials.
    for path in paths:
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("个人数据目录含链接，请先检查目录")
        if path.is_dir():
            for current, children, names in os.walk(path, followlinks=False):
                for name in children + names:
                    candidate = Path(current) / name
                    if candidate.is_symlink() or candidate.is_junction():
                        raise ValueError("个人数据目录含链接，请先检查目录")
    runtime = data / "runtime/backend.json"
    if runtime.is_file():
        from release_security import read_bounded
        state = json.loads(read_bounded(runtime))
        if not isinstance(state, dict) or type(state.get("pid")) is not int or state["pid"] <= 0:
            raise ValueError("运行记录无效，请检查 MOKU 运行状态")
        if state["pid"] != os.getpid():
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000 | 0x100000, False, state["pid"])
            if handle:
                try:
                    executable = ctypes.create_unicode_buffer(32768)
                    length = ctypes.c_ulong(len(executable))
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        if not kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if Path(executable.value).name.casefold() == "moku.exe":
                            raise ValueError("请关闭所有 MOKU 窗口后再清除个人数据")
                finally:
                    kernel.CloseHandle(handle)
            elif ctypes.get_last_error() != 87:
                raise ctypes.WinError(ctypes.get_last_error())
    from auth_store import delete_persistent_session
    from pixiv_account import CredentialVault
    from source_search import SourceKey

    failures = {}

    def attempt(name, operation):
        try:
            operation()
        except (OSError, ValueError) as error:
            failures[name] = error
            return False
        return True

    attempt("Pixiv 网页凭据", delete_persistent_session)
    attempt("Pixiv App 凭据", lambda: CredentialVault().delete())

    def delete_source_key():
        source_key = SourceKey()
        if not source_key.disabled:
            source_key.delete()

    attempt("SauceNAO API Key", delete_source_key)
    callback = data / "library/account-callback.json"
    callback_removed = attempt("Pixiv 回调注册", lambda: _clear_installed_callback(program_root, callback))
    for path in paths:
        name = path.relative_to(data).as_posix()
        if path == callback and not callback_removed and path.is_file():
            failures[name] = ValueError("保留注册归属以供重试")
            continue
        if path.is_dir():
            attempt(name, lambda: shutil.rmtree(path))
        else:
            attempt(name, lambda: path.unlink(missing_ok=True))
    for directory in [data / name for name in ("library/source-search", "library", "runtime", "logs")] + [data]:
        def remove_empty():
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        attempt(directory.relative_to(data).as_posix(), remove_empty)

    if failures:
        raise InstalledDataCleanupError(failures) from ExceptionGroup("Failed installed data cleanup",
                                                                    list(failures.values()))


def _clear_installed_callback(program_root, callback):
    from account_authorization import PROTOCOL, protocol_guard
    from release_security import read_bounded

    hive = winreg.HKEY_CURRENT_USER
    suffixes = (r"\shell\open\command", r"\shell\open", r"\shell", "")
    expected = f'"{program_root / "MOKU.exe"}" --pixiv-account-callback "%1"'
    with protocol_guard():
        try:
            with winreg.OpenKey(hive, PROTOCOL + suffixes[0]) as key:
                command = winreg.QueryValueEx(key, "")[0]
        except FileNotFoundError:
            try:
                with winreg.OpenKey(hive, PROTOCOL) as key:
                    owner = winreg.QueryValueEx(key, "MOKU Owner")[0]
                record = json.loads(read_bounded(callback))
            except FileNotFoundError:
                return
            if not isinstance(record, dict) or record.get("owner") != owner:
                return
            command = record.get("command")
        if command != expected:
            return
        for suffix in suffixes:
            try:
                winreg.DeleteKey(hive, PROTOCOL + suffix)
            except FileNotFoundError:
                continue
