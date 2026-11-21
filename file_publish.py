# SPDX-License-Identifier: MIT
# MOKU — Xirti / Aperia
# Source: https://github.com/Xirti/Pixiv-Bulk-Image-Downloader
"""Own Windows download staging, atomic publication and rollback.

Network and account checks belong to the caller. Publication serializes only
the final local transaction, after all staged data has been prepared.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import hmac
import logging
import os
import secrets
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pixiv_adapter import PixivPolicyError

PUBLISH_TRANSACTION_LOCK = threading.Lock()
LOG = logging.getLogger("moku.http")


def _is_link_or_reparse(path: Path) -> bool:
    target = Path(path)
    if target.is_symlink() or bool(getattr(target, "is_junction", lambda: False)()):
        return True
    if os.name != "nt" or not target.exists():
        return False
    attributes = ctypes.windll.kernel32.GetFileAttributesW(str(target))
    return attributes != 0xFFFFFFFF and bool(attributes & 0x400)


def _lexical_absolute(path: Path) -> Path:
    """Normalize dot segments without following links or reparse points."""
    return Path(os.path.abspath(os.fspath(path)))


def reject_reparse_components(path: Path, message: str) -> Path:
    """Reject every existing component before any filesystem write occurs."""
    candidate = _lexical_absolute(path)
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if _is_link_or_reparse(current):
            raise PixivPolicyError(message)
    return candidate


def _validated_publish_parent(
    save_root: Path,
    final: Path,
    *,
    created_dirs: list[Path] | None = None,
    retained_handles: list[int] | None = None,
) -> Path:
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    # Lexical containment is followed by handle-based component validation.
    # Do not resolve(): doing so follows a junction before it can be rejected.
    root = _lexical_absolute(save_root)
    candidate = _lexical_absolute(final)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PixivPolicyError("保存路径超出目标目录") from exc

    handles = _directory_lock_chain(root, [root])
    current = root
    retain = False
    try:
        for part in relative.parts[:-1]:
            child = current / part
            created = False
            try:
                handle = _open_directory_handle(
                    child, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
                )
            except FileNotFoundError:
                try:
                    child.mkdir()
                    created = True
                    if created_dirs is not None:
                        created_dirs.append(child)
                except FileExistsError:
                    pass
                handle = _open_directory_handle(
                    child, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
                )
            try:
                _handle_identity(handle)
            except Exception:
                _close_windows_handle(handle)
                raise
            handles.append(handle)
            current = child
        result = current / relative.name
        if retained_handles is not None:
            retained_handles.extend(handles)
            handles = []
            retain = True
        return result
    finally:
        if not retain:
            _close_directory_lock_chain(handles)


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


FILE_TRAVERSE = 0x0020
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_READ_ATTRIBUTES = 0x0080
SYNCHRONIZE_ACCESS = 0x00100000
DELETE_ACCESS = 0x00010000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
FILE_SHARE_ALL = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
WINDOWS_SECURE_PUBLICATION = os.name == "nt" and hasattr(ctypes, "windll")
FILE_DISPOSITION_INFO_CLASS = 4
FILE_RENAME_INFO_CLASS = 3


class FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOL)]


class FILE_RENAME_INFO(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOL),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


def _open_directory_handle(
    path: Path,
    *,
    share_mode: int = FILE_SHARE_ALL,
    access: int = FILE_TRAVERSE | FILE_READ_ATTRIBUTES,
) -> int:
    return _open_windows_handle(
        path,
        share_mode=share_mode,
        access=access,
        flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
    )


def _open_windows_handle(
    path: Path,
    *,
    share_mode: int,
    access: int,
    flags: int,
) -> int:
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path), access,
        share_mode, None, OPEN_EXISTING,
        flags, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError()
    return int(handle)


def _close_windows_handle(handle: int) -> None:
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _delete_empty_directory_on_close(handle: int) -> None:
    disposition = FILE_DISPOSITION_INFO(True)
    set_information = ctypes.windll.kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    if not set_information(
        handle,
        FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise ctypes.WinError()


def _rename_file_by_handle(handle: int, destination: Path) -> None:
    encoded = str(destination).encode("utf-16-le")
    name_offset = FILE_RENAME_INFO.FileName.offset
    # FILE_RENAME_INFO includes FileName[1] and ABI tail padding. Supplying only
    # FIELD_OFFSET + FileNameLength lets the kernel consume adjacent memory as
    # random filename suffixes. Over-allocate the complete structure plus name.
    buffer_size = ctypes.sizeof(FILE_RENAME_INFO) + len(encoded)
    buffer = ctypes.create_string_buffer(buffer_size)
    info = ctypes.cast(buffer, ctypes.POINTER(FILE_RENAME_INFO)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))

    set_information = ctypes.windll.kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    if not set_information(
        handle, FILE_RENAME_INFO_CLASS, buffer, buffer_size,
    ):
        raise ctypes.WinError()


def _handle_identity(handle: int) -> tuple[int, int, int]:
    info = _raw_handle_information(handle)
    if not info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        raise PixivPolicyError("保存路径组件不是目录")
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PixivPolicyError("保存目录包含链接或重解析点")
    return _identity_from_handle_information(info)


def _raw_handle_information(handle: int) -> BY_HANDLE_FILE_INFORMATION:
    info = BY_HANDLE_FILE_INFORMATION()
    get_info = ctypes.windll.kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL
    if not get_info(handle, ctypes.byref(info)):
        raise ctypes.WinError()
    return info


def _identity_from_handle_information(
    info: BY_HANDLE_FILE_INFORMATION,
) -> tuple[int, int, int]:
    return (
        int(info.dwVolumeSerialNumber),
        (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        int(info.dwFileAttributes),
    )


@dataclass(frozen=True)
class PublishedFileSnapshot:
    identity: tuple[int, int]
    size: int
    sha256: str


@dataclass
class PublishedFileOwnership:
    final: Path
    handle: int | None
    snapshot: PublishedFileSnapshot | None = None
    staged_handle: int | None = None
    staged_snapshot: PublishedFileSnapshot | None = None
    published: bool = False


class PublishedOwnershipError(PixivPolicyError):
    """A published path could not be proven to still contain our staged file."""


def _hash_windows_file_handle(handle: int) -> str:
    set_pointer = ctypes.windll.kernel32.SetFilePointerEx
    set_pointer.argtypes = [
        wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD,
    ]
    set_pointer.restype = wintypes.BOOL
    if not set_pointer(handle, 0, None, 0):
        raise ctypes.WinError()

    read_file = ctypes.windll.kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    read_file.restype = wintypes.BOOL
    digest = hashlib.sha256()
    buffer = ctypes.create_string_buffer(1024 * 1024)
    while True:
        count = wintypes.DWORD()
        if not read_file(handle, buffer, len(buffer), ctypes.byref(count), None):
            raise ctypes.WinError()
        if count.value == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[:count.value])


def _snapshot_windows_file_handle(handle: int) -> PublishedFileSnapshot:
    info = _raw_handle_information(handle)
    if info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
        raise PixivPolicyError("发布目标不是普通文件")
    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise PixivPolicyError("发布目标不能是链接或重解析点")
    return PublishedFileSnapshot(
        identity=(
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        ),
        size=(int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
        sha256=_hash_windows_file_handle(handle),
    )


def _own_staged_file(path: Path, final: Path) -> PublishedFileOwnership:
    """Snapshot a staging file while retaining exclusive ownership until commit."""
    handle = _open_windows_handle(
        path,
        share_mode=0,
        access=(
            FILE_READ_DATA | FILE_WRITE_DATA
            | FILE_READ_ATTRIBUTES | DELETE_ACCESS
        ),
        flags=FILE_FLAG_OPEN_REPARSE_POINT,
    )
    try:
        snapshot = _snapshot_windows_file_handle(handle)
        return PublishedFileOwnership(
            final=final,
            handle=handle,
            snapshot=snapshot,
            staged_handle=handle,
            staged_snapshot=snapshot,
        )
    except Exception:
        _close_windows_handle(handle)
        raise


def _write_windows_file_handle(handle: int, raw: bytes | BinaryIO) -> None:
    write_file = ctypes.windll.kernel32.WriteFile
    write_file.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    write_file.restype = wintypes.BOOL
    if isinstance(raw, bytes):
        view = memoryview(raw)
        chunks = (bytes(view[offset:offset + 1024 * 1024]) for offset in range(0, len(view), 1024 * 1024))
    else:
        chunks = iter(lambda: raw.read(1024 * 1024), b"")
    for chunk in chunks:
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not write_file(
            handle, buffer, len(chunk), ctypes.byref(written), None,
        ):
            raise ctypes.WinError()
        if written.value != len(chunk):
            raise OSError("暂存文件写入不完整")
    flush = ctypes.windll.kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    if not flush(handle):
        raise ctypes.WinError()


def create_staged_file(
    staging_root: Path,
    final: Path,
    raw: bytes | BinaryIO,
) -> PublishedFileOwnership:
    """Create, write, verify, and retain one zero-share staging file object."""
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    for _attempt in range(32):
        path = staging_root / f".page-{secrets.token_hex(16)}.part"
        handle = create_file(
            str(path),
            FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | DELETE_ACCESS,
            0,
            None,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            error = ctypes.WinError()
            if isinstance(error, FileExistsError):
                continue
            raise error
        owned_handle = int(handle)
        try:
            _write_windows_file_handle(owned_handle, raw)
            snapshot = _snapshot_windows_file_handle(owned_handle)
            return PublishedFileOwnership(
                final=final,
                handle=owned_handle,
                snapshot=snapshot,
                staged_handle=owned_handle,
                staged_snapshot=snapshot,
            )
        except Exception as original:
            cleanup_error: Exception | None = None
            try:
                _delete_empty_directory_on_close(owned_handle)
            except Exception as error:
                cleanup_error = error
            finally:
                _close_windows_handle(owned_handle)
            if cleanup_error is not None:
                raise PublishRollbackError(
                    "暂存失败且恢复文件保留在临时目录"
                ) from original
            raise
    raise PublishedOwnershipError("无法分配安全暂存文件名")


def _close_published_ownership(ownership: PublishedFileOwnership) -> None:
    closed: set[int] = set()
    for attribute in ("staged_handle", "handle"):
        handle = getattr(ownership, attribute)
        if handle is not None and handle not in closed:
            _close_windows_handle(handle)
            closed.add(handle)
        setattr(ownership, attribute, None)


def _delete_owned_published_file(ownership: PublishedFileOwnership) -> None:
    if ownership.handle is None:
        raise PublishedOwnershipError("发布目标所有权句柄已关闭")
    handle = ownership.handle
    try:
        _delete_empty_directory_on_close(handle)
    finally:
        _close_windows_handle(handle)
        ownership.handle = None
        if ownership.staged_handle == handle:
            ownership.staged_handle = None
    ownership.published = False


def _rollback_owned_files(
    ownerships: list[PublishedFileOwnership],
) -> list[Exception]:
    failures: list[Exception] = []
    for ownership in reversed(ownerships):
        try:
            if ownership.published:
                _delete_owned_published_file(ownership)
            elif ownership.staged_handle is not None:
                _delete_empty_directory_on_close(ownership.staged_handle)
        except Exception as exc:
            failures.append(exc)
        finally:
            _close_published_ownership(ownership)
    return failures


def discard_staging(
    ownerships: list[PublishedFileOwnership],
) -> int:
    return len(_rollback_owned_files(ownerships))


def _directory_lock_chain(root: Path, paths: list[Path]) -> list[int]:
    """Lock a validated root-to-leaf directory tree against retargeting.

    Handles are function-local, acquired in deterministic root-first order, and
    closed by the caller. Windows share-mode enforcement owns synchronization;
    no mutable Python state is shared between concurrent publications.
    """
    boundary = _lexical_absolute(root)
    anchor = Path(boundary.anchor)
    directories: dict[str, Path] = {os.path.normcase(str(anchor)): anchor}
    current = anchor
    for part in boundary.parts[1:]:
        current /= part
        directories[os.path.normcase(str(current))] = current
    for raw_path in paths:
        candidate = _lexical_absolute(raw_path)
        try:
            relative = candidate.relative_to(boundary)
        except ValueError as exc:
            raise PixivPolicyError("保存路径超出目标目录") from exc
        current = boundary
        for part in relative.parts:
            current /= part
            directories[os.path.normcase(str(current))] = current

    ordered = sorted(
        directories.values(),
        key=lambda path: (len(path.parts), os.path.normcase(str(path))),
    )
    handles: list[int] = []
    try:
        for current in ordered:
            handle = _open_directory_handle(
                current, share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            )
            try:
                _handle_identity(handle)
            except Exception:
                _close_windows_handle(handle)
                raise
            handles.append(handle)
    except Exception:
        for handle in reversed(handles):
            _close_windows_handle(handle)
        raise
    return handles


def _close_directory_lock_chain(handles: list[int]) -> None:
    for handle in reversed(handles):
        _close_windows_handle(handle)


def _remove_publish_directory(path: Path, *, boundary_root: Path) -> None:
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    locks = _directory_lock_chain(boundary_root, [path.parent])
    handle: int | None = None
    try:
        handle = _open_directory_handle(
            path,
            share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            access=FILE_TRAVERSE | FILE_READ_ATTRIBUTES | DELETE_ACCESS,
        )
        _handle_identity(handle)
        _delete_empty_directory_on_close(handle)
    finally:
        if handle is not None:
            _close_windows_handle(handle)
        _close_directory_lock_chain(locks)


class PublishRollbackError(PixivPolicyError):
    """Publication failed and one or more owned outputs could not be removed."""


class SecureStagingDirectory:
    """Hold the selected Windows directory identity for one download lifecycle."""

    def __init__(self, save_root: Path, *, prefix: str) -> None:
        self.save_root = _lexical_absolute(save_root)
        self.prefix = prefix
        self.path: Path | None = None
        self.cleanup_pending = False
        self._root_handles: list[int] = []
        self._staging_handle: int | None = None

    def __enter__(self) -> Path:
        if not WINDOWS_SECURE_PUBLICATION:
            raise PixivPolicyError("安全文件发布仅支持 Windows")
        if not self.save_root.is_dir():
            raise PixivPolicyError("保存根目录不存在")
        self._root_handles = _directory_lock_chain(
            self.save_root, [self.save_root],
        )
        try:
            self.path = Path(tempfile.mkdtemp(
                prefix=self.prefix, dir=str(self.save_root),
            ))
            self._staging_handle = _open_directory_handle(
                self.path,
                share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
            )
            _handle_identity(self._staging_handle)
            return self.path
        except Exception as original:
            cleanup_error: Exception | None = None
            try:
                if self._staging_handle is not None:
                    _close_windows_handle(self._staging_handle)
                    self._staging_handle = None
                if self.path is not None and self.path.exists():
                    if _is_link_or_reparse(self.path) or not self.path.is_dir():
                        raise PixivPolicyError("临时目录初始化后身份异常")
                    # No image bytes exist before __enter__ returns. rmdir is
                    # deliberately non-recursive and runs while the root is locked.
                    self.path.rmdir()
            except Exception as error:
                cleanup_error = error
            finally:
                self._close_handles()
            if cleanup_error is not None:
                raise PixivPolicyError("临时目录初始化失败且空目录清理失败") from original
            raise

    def _close_handles(self) -> None:
        if self._staging_handle is not None:
            _close_windows_handle(self._staging_handle)
            self._staging_handle = None
        if self._root_handles:
            _close_directory_lock_chain(self._root_handles)
            self._root_handles = []

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        preserve = isinstance(exc, PublishRollbackError)
        cleanup_error: Exception | None = None
        try:
            if not preserve and self.path is not None:
                # Staging is deliberately flat. Delete only ordinary files while
                # both root and staging identities remain locked; never recurse
                # through an unexpected directory or reparse point.
                for child in self.path.iterdir():
                    if _is_link_or_reparse(child) or not child.is_file():
                        raise PixivPolicyError("临时目录包含非普通文件")
                    child.unlink()
        except Exception as error:
            cleanup_error = error
        finally:
            if self._staging_handle is not None:
                _close_windows_handle(self._staging_handle)
                self._staging_handle = None
        try:
            if not preserve and cleanup_error is None and self.path is not None:
                # The directory is known empty. Non-recursive rmdir cannot follow
                # an attacker path; the locked root chain still prevents retargeting.
                self.path.rmdir()
        except Exception as error:
            cleanup_error = error
        finally:
            if self._root_handles:
                _close_directory_lock_chain(self._root_handles)
                self._root_handles = []
        if cleanup_error is not None and exc is None:
            # Publication and response-path construction already committed. A
            # best-effort staging cleanup failure must not turn success into 502.
            self.cleanup_pending = True
            LOG.warning("下载已提交；临时目录清理待处理")
        elif cleanup_error is not None:
            raise PixivPolicyError("下载失败且临时目录清理失败") from (exc or cleanup_error)
        return False


def secure_staging_directory(save_root: Path, *, prefix: str) -> SecureStagingDirectory:
    return SecureStagingDirectory(save_root, prefix=prefix)


def _same_file_content(
    left: PublishedFileSnapshot,
    right: PublishedFileSnapshot,
) -> bool:
    return left.size == right.size and hmac.compare_digest(left.sha256, right.sha256)


def _publish_owned_staged_file(
    ownership: PublishedFileOwnership,
    final: Path,
    *,
    boundary_root: Path,
    prelocked_parent: Path | None = None,
) -> None:
    """Publish one exclusively owned staging file without reopening it by path."""
    if ownership.staged_handle is None or ownership.staged_snapshot is None:
        raise PublishedOwnershipError("暂存文件所有权缺失")
    parents = [final.parent]
    if prelocked_parent is not None:
        locked = os.path.normcase(str(_lexical_absolute(prelocked_parent)))
        parents = [
            parent for parent in parents
            if os.path.normcase(str(_lexical_absolute(parent))) != locked
        ]
    locks = _directory_lock_chain(boundary_root, parents)
    try:
        try:
            target_handle = _open_windows_handle(
                final,
                share_mode=0,
                access=FILE_READ_DATA | FILE_READ_ATTRIBUTES,
                flags=FILE_FLAG_OPEN_REPARSE_POINT,
            )
        except FileNotFoundError:
            # Register ownership before the kernel rename. If a competitor wins
            # this name, fall through to the collision-safe sibling loop.
            ownership.final = final
            ownership.handle = ownership.staged_handle
            ownership.snapshot = ownership.staged_snapshot
            ownership.published = True
            try:
                _rename_file_by_handle(ownership.staged_handle, final)
                return
            except FileExistsError:
                ownership.published = False
        else:
            try:
                current = _snapshot_windows_file_handle(target_handle)
                if _same_file_content(current, ownership.staged_snapshot):
                    # Idempotent download: retain the existing complete file and
                    # leave the duplicate staging object for context cleanup.
                    ownership.final = final
                    ownership.handle = target_handle
                    ownership.snapshot = current
                    target_handle = None
                    return
            finally:
                if target_handle is not None:
                    _close_windows_handle(target_handle)

        # Never overwrite a different existing file. Publish the verified
        # staging object under a collision-safe sibling name using the same
        # zero-share handle and a no-replace kernel rename.
        for collision in range(1, 10_001):
            candidate = final.with_name(
                f"{final.stem} ({collision}){final.suffix}",
            )
            ownership.final = candidate
            ownership.handle = ownership.staged_handle
            ownership.snapshot = ownership.staged_snapshot
            ownership.published = True
            try:
                _rename_file_by_handle(ownership.staged_handle, candidate)
                return
            except FileExistsError:
                ownership.published = False
                continue
        raise PublishedOwnershipError("同名下载文件过多，无法分配安全文件名")
    finally:
        _close_directory_lock_chain(locks)


def publish_staged_files(
    staging_root: Path,
    staged: list[tuple[Path, Path] | PublishedFileOwnership],
    *,
    save_root: Path | None = None,
    staging_locked: bool = False,
    public_paths: bool = False,
    before_publish: Callable[[], None] | None = None,
) -> list[Path] | list[str]:
    """Serialize one complete local publish/rollback transaction.

    Network reads and staging have already completed. The lock covers every
    mutable destination operation, preventing concurrent request threads from
    deleting each other's published bytes.
    """
    if not WINDOWS_SECURE_PUBLICATION:
        raise PixivPolicyError("安全文件发布仅支持 Windows")
    if save_root is None:
        raise PixivPolicyError("安全文件发布必须绑定保存根目录")
    with PUBLISH_TRANSACTION_LOCK:
        return _publish_staged_files_locked(
            staging_root, staged, save_root=save_root,
            staging_locked=staging_locked, public_paths=public_paths, before_publish=before_publish,
        )


def _publish_staged_files_locked(
    staging_root: Path,
    staged: list[tuple[Path, Path] | PublishedFileOwnership],
    *,
    save_root: Path | None = None,
    staging_locked: bool = False,
    public_paths: bool = False,
    before_publish: Callable[[], None] | None = None,
) -> list[Path] | list[str]:
    """Publish a completed batch and restore every owned object on failure."""
    transactions: list[PublishedFileOwnership] = [
        entry for entry in staged
        if isinstance(entry, PublishedFileOwnership)
    ]
    created_dirs: list[Path] = []
    retained_parent_handles: list[int] = []
    try:
        if before_publish is not None:
            before_publish()
        for entry in staged:
            if isinstance(entry, PublishedFileOwnership):
                ownership = entry
                final = ownership.final
            else:
                temporary, final = entry
                ownership = None
            if save_root is not None:
                final = _validated_publish_parent(
                    save_root,
                    final,
                    created_dirs=created_dirs,
                    retained_handles=retained_parent_handles,
                )
            elif not final.parent.exists():
                final.parent.mkdir(parents=True, exist_ok=True)
            if ownership is None:
                ownership = _own_staged_file(temporary, final)
                transactions.append(ownership)
            else:
                ownership.final = final
                if ownership.staged_handle is None or ownership.staged_snapshot is None:
                    raise PublishedOwnershipError("暂存文件所有权已关闭")
            # Register before the first public mutation. Even a helper that raises
            # after its kernel operation leaves the current item rollback-capable.
            _publish_owned_staged_file(
                ownership,
                final,
                boundary_root=save_root,
                prelocked_parent=staging_root if staging_locked else None,
            )
            if (
                ownership.staged_handle is not None
                and ownership.staged_handle != ownership.handle
            ):
                _delete_empty_directory_on_close(ownership.staged_handle)
        saved = [ownership.final for ownership in transactions]
        # Compute response values while every published file handle remains open.
        # No committed path is resolved after ownership is released.
        if public_paths:
            return public_saved_files(save_root, saved)
        return saved
    except Exception as original:
        rollback_failures = _rollback_owned_files(transactions)
        if retained_parent_handles:
            _close_directory_lock_chain(retained_parent_handles)
            retained_parent_handles = []
        for directory in reversed(created_dirs):
            try:
                _remove_publish_directory(directory, boundary_root=save_root)
            except Exception as exc:
                rollback_failures.append(exc)
        if rollback_failures:
            raise PublishRollbackError(
                f"发布失败且有 {len(rollback_failures)} 项未能安全恢复；恢复文件保留在临时目录"
            ) from original
        raise
    finally:
        for ownership in transactions:
            _close_published_ownership(ownership)
        if retained_parent_handles:
            _close_directory_lock_chain(retained_parent_handles)


def public_saved_files(save_root: Path, saved: list[Path]) -> list[str]:
    root = _lexical_absolute(save_root)
    result: list[str] = []
    for path in saved:
        candidate = _lexical_absolute(path)
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise PixivPolicyError("保存结果超出目标目录") from exc
        if not relative.parts or relative.is_absolute():
            raise PixivPolicyError("保存结果不是有效文件路径")
        result.append(relative.as_posix())
    return result



@contextmanager
def owned_staging_batch():
    """Release partial staging on failure; transfer ownership on success."""
    staged: list[PublishedFileOwnership] = []
    try:
        yield staged
    except Exception as original:
        failures = discard_staging(staged)
        if failures:
            raise PublishRollbackError(
                f"暂存失败且有 {failures} 个文件未能安全清理"
            ) from original
        raise
