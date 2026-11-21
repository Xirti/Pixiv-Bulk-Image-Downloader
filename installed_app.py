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
            "account-browse.sqlite3", "account-cache.sqlite3", "cloud-state.sqlite3",
            "guide-v1.seen", "login-context.json", "request-settings.json", "auth-method.json",
            "pixiv-logout-revision", "pixiv-logout-revision.tmp",
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
    delete_persistent_session()
    CredentialVault().delete()
    source_key = SourceKey()
    if not source_key.disabled:
        source_key.delete()
    protocol = r"Software\Classes\pixiv"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, protocol + r"\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
    except FileNotFoundError:
        command = None
    if command == f'"{program_root / "MOKU.exe"}" --pixiv-account-callback "%1"':
        for suffix in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, protocol + suffix)
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for name in ("library/source-search", "library", "runtime", "logs"):
        directory = data / name
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if data.is_dir() and not any(data.iterdir()):
        data.rmdir()
