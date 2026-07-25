from __future__ import annotations

import ctypes
import os
import re
import threading
from ctypes import wintypes

TARGET = "MOKU.Pixiv.PHPSESSID"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
_MEMORY_SESSION = ""
_SESSION_CACHE_UNSET = object()
_PERSISTENT_SESSION_CACHE: str | object = _SESSION_CACHE_UNSET
_SESSION_LOCK = threading.RLock()


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
advapi32.CredWriteW.restype = wintypes.BOOL
advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
advapi32.CredReadW.restype = wintypes.BOOL
advapi32.CredFree.argtypes = [wintypes.LPVOID]
advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
advapi32.CredDeleteW.restype = wintypes.BOOL


def validate_session_value(value: str) -> str:
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9._~-]{8,256}", value):
        raise ValueError("invalid Pixiv session value")
    return value


def persistent_session_disabled() -> bool:
    """Return true only for explicitly isolated automated probes."""
    return os.environ.get("MOKU_DISABLE_PERSISTENT_SESSION", "").strip() == "1"


def write_persistent_session(value: str) -> None:
    if persistent_session_disabled():
        return
    value = validate_session_value(value)
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    cred = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=TARGET, CredentialBlobSize=len(raw), CredentialBlob=blob, Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="Pixiv session")
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError()


def read_persistent_session() -> str:
    if persistent_session_disabled():
        return ""
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not advapi32.CredReadW(TARGET, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return ""
    try:
        cred = pointer.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return validate_session_value(raw.decode("utf-16-le"))
    except (UnicodeError, ValueError):
        return ""
    finally:
        advapi32.CredFree(pointer)


def delete_persistent_session() -> None:
    if persistent_session_disabled():
        return
    ctypes.set_last_error(0)
    if advapi32.CredDeleteW(TARGET, CRED_TYPE_GENERIC, 0):
        return
    error = ctypes.get_last_error()
    if error != ERROR_NOT_FOUND:
        raise ctypes.WinError(error)


def delete_session() -> None:
    global _MEMORY_SESSION, _PERSISTENT_SESSION_CACHE
    with _SESSION_LOCK:
        # Commit the in-process logout before touching Credential Manager. If
        # durable deletion fails, this backend must not immediately reload the
        # old credential and silently undo the user's logout.
        _MEMORY_SESSION = ""
        _PERSISTENT_SESSION_CACHE = ""
        delete_persistent_session()


def clear_memory_session() -> None:
    global _MEMORY_SESSION
    with _SESSION_LOCK:
        _MEMORY_SESSION = ""


def store_session(value: str, remember: bool = False) -> None:
    global _MEMORY_SESSION, _PERSISTENT_SESSION_CACHE
    value = validate_session_value(value)
    with _SESSION_LOCK:
        if remember:
            # Persist first. A failed CredWrite must leave the old in-memory
            # authorization untouched instead of committing a half-login.
            write_persistent_session(value)
            persistent_value = "" if persistent_session_disabled() else value
        else:
            delete_persistent_session()
            persistent_value = ""
        _MEMORY_SESSION = value
        _PERSISTENT_SESSION_CACHE = persistent_value


def read_session() -> str:
    global _PERSISTENT_SESSION_CACHE
    with _SESSION_LOCK:
        if _MEMORY_SESSION:
            return _MEMORY_SESSION
        if _PERSISTENT_SESSION_CACHE is _SESSION_CACHE_UNSET:
            _PERSISTENT_SESSION_CACHE = read_persistent_session()
        return str(_PERSISTENT_SESSION_CACHE or "")


# Backward-compatible name for existing callers/tests.
write_session = write_persistent_session


def session_cookie_header() -> dict[str, str]:
    value = read_session()
    return {"Cookie": f"PHPSESSID={value}"} if value else {}
