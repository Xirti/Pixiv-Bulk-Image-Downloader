"""Windows-owned download execution and cross-process cooperative cancellation."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import logging
import os
from pathlib import Path
import threading


_KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
_KERNEL.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL.CreateMutexW.restype = wintypes.HANDLE
_KERNEL.ReleaseMutex.argtypes = [wintypes.HANDLE]
_KERNEL.ReleaseMutex.restype = wintypes.BOOL
_KERNEL.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL.CreateEventW.restype = wintypes.HANDLE
_KERNEL.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_KERNEL.OpenEventW.restype = wintypes.HANDLE
_KERNEL.SetEvent.argtypes = [wintypes.HANDLE]
_KERNEL.SetEvent.restype = wintypes.BOOL
_KERNEL.ResetEvent.argtypes = [wintypes.HANDLE]
_KERNEL.ResetEvent.restype = wintypes.BOOL
_KERNEL.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_KERNEL.WaitForSingleObject.restype = wintypes.DWORD
_KERNEL.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
_KERNEL.WaitForMultipleObjects.restype = wintypes.DWORD
_KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
_KERNEL.CloseHandle.restype = wintypes.BOOL


def _name(path: Path, request_id: str, scope: str | None = None) -> str:
    parts = [os.path.normcase(str(path.resolve())), request_id]
    if scope is not None:
        parts.append(scope)
    key = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return "Global\\MOKU.Download." + key


def _watch_cancel(cancel_handle, stop_handle, cancel: threading.Event) -> None:
    handles = (wintypes.HANDLE * 2)(cancel_handle, stop_handle)
    result = _KERNEL.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
    if result == 0:
        cancel.set()
    elif result != 1:
        # Continuing without a working cancellation channel would strand the
        # UI. Pause the operation and report the native failure instead.
        logging.getLogger("moku.http").error("Download cancellation wait failed: %s", ctypes.get_last_error())
        cancel.set()


def _stop_watcher(handle, thread: threading.Thread) -> None:
    _KERNEL.SetEvent(handle)
    thread.join()


@contextmanager
def _mutex(name: str, timeout_ms: int):
    with ExitStack() as cleanup:
        mutex = _KERNEL.CreateMutexW(None, False, name)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        cleanup.callback(_KERNEL.CloseHandle, mutex)
        result = _KERNEL.WaitForSingleObject(mutex, timeout_ms)
        if result == 258:
            yield False
            return
        if result not in (0, 0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        cleanup.callback(_KERNEL.ReleaseMutex, mutex)
        yield True


@contextmanager
def download_execution(path: Path, request_id: str, scope: str, cancel: threading.Event | None):
    """Own one request until its receipt is stored; observers get False.

    Mutex ownership lives on the HTTP thread and is reclaimed by Windows on
    process/thread exit. The cancellation event is scoped to the account.
    """
    with _mutex(_name(path, request_id), 0) as owner, ExitStack() as cleanup:
        if not owner:
            yield False
            return
        if cancel is not None:
            event_name = _name(path, request_id, scope)
            # Registration and cancellation share a short critical section:
            # ResetEvent must never erase an already acknowledged cancel.
            with _mutex(event_name + ".control", 1000) as registered:
                if not registered:
                    raise TimeoutError("下载控制通道繁忙，请重试")
                event = _KERNEL.CreateEventW(None, True, False, event_name)
                if not event:
                    raise ctypes.WinError(ctypes.get_last_error())
                cleanup.callback(_KERNEL.CloseHandle, event)
                if not _KERNEL.ResetEvent(event):
                    raise ctypes.WinError(ctypes.get_last_error())
            stopped = _KERNEL.CreateEventW(None, True, False, None)
            if not stopped:
                raise ctypes.WinError(ctypes.get_last_error())
            cleanup.callback(_KERNEL.CloseHandle, stopped)
            watcher = threading.Thread(target=_watch_cancel, args=(event, stopped, cancel),
                                       name="moku-download-cancel", daemon=True)
            watcher.start()
            cleanup.callback(_stop_watcher, stopped, watcher)
        yield True


def cancel_download_execution(path: Path, request_id: str, scope: str) -> bool:
    event_name = _name(path, request_id, scope)
    with _mutex(event_name + ".control", 1000) as registered:
        if not registered:
            raise TimeoutError("下载控制通道繁忙，请重试")
        handle = _KERNEL.OpenEventW(0x0002, False, event_name)
        if not handle:
            error = ctypes.get_last_error()
            if error == 2:
                return False
            raise ctypes.WinError(error)
        try:
            if not _KERNEL.SetEvent(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        finally:
            _KERNEL.CloseHandle(handle)
