from __future__ import annotations

import ctypes
import re
from ctypes import wintypes

TARGET = "MOKU.Pixiv.PHPSESSID"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
_MEMORY_SESSION = ""


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


advapi32 = ctypes.WinDLL("Advapi32.dll")
advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
advapi32.CredWriteW.restype = wintypes.BOOL
advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
advapi32.CredReadW.restype = wintypes.BOOL
advapi32.CredFree.argtypes = [wintypes.LPVOID]
advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]


def validate_session_value(value: str) -> str:
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9._~-]{8,256}", value):
        raise ValueError("invalid Pixiv session value")
    return value


def write_persistent_session(value: str) -> None:
    value = validate_session_value(value)
    raw = value.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    cred = CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=TARGET, CredentialBlobSize=len(raw), CredentialBlob=blob, Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="Pixiv session")
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError()


def read_persistent_session() -> str:
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
    advapi32.CredDeleteW(TARGET, CRED_TYPE_GENERIC, 0)


def delete_session() -> None:
    clear_memory_session()
    delete_persistent_session()


def clear_memory_session() -> None:
    global _MEMORY_SESSION
    _MEMORY_SESSION = ""


def store_session(value: str, remember: bool = False) -> None:
    global _MEMORY_SESSION
    value = validate_session_value(value)
    _MEMORY_SESSION = value
    if remember:
        write_persistent_session(value)
    else:
        delete_persistent_session()


def read_session() -> str:
    return _MEMORY_SESSION or read_persistent_session()


# Backward-compatible name for existing callers/tests.
write_session = write_persistent_session


def session_cookie_header() -> dict[str, str]:
    value = read_session()
    return {"Cookie": f"PHPSESSID={value}"} if value else {}
