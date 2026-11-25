"""Portable binary replacement and crash recovery. Runs before server imports."""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time

from update_package import MANAGED, UpdateError, verify_bundle, version_key
from release_security import RELEASE_METADATA, RELEASE_SIGNATURE, read_bounded, verify_release

LOG = logging.getLogger("moku.updates")
RECOVERY_ENTRY = "MOKU-Recover.lnk"
CREATE_RECOVERY_LINK = """
$ErrorActionPreference = 'Stop'
$values = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class RecoveryLink {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink {}

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW {
        void GetPath();
        void GetIDList();
        void SetIDList(IntPtr id);
        void GetDescription();
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string description);
        void GetWorkingDirectory();
        void SetWorkingDirectory();
        void GetArguments();
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string arguments);
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr ILCreateFromPath(string path);

    public static void Save(string link, string target, string arguments) {
        var item = ILCreateFromPath(target);
        if (item == IntPtr.Zero) throw new InvalidOperationException("MOKU recovery helper path unavailable");
        try {
            var shortcut = (IShellLinkW)new ShellLink();
            shortcut.SetIDList(item);
            shortcut.SetDescription("Restore MOKU after an interrupted update");
            shortcut.SetArguments(arguments);
            ((IPersistFile)shortcut).Save(link, true);
        } finally {
            Marshal.FreeCoTaskMem(item);
        }
    }
}
'@
[RecoveryLink]::Save($values.link, $values.target, $values.arguments)
"""


def work_root(target):
    identity=hashlib.sha256(str(target).casefold().encode()).hexdigest()[:16]
    return target.parent/(".moku-update-"+identity)


def write_json(path, payload):
    temporary=path.with_suffix(".tmp")
    with temporary.open("w",encoding="utf-8") as output:
        json.dump(payload,output);output.flush();os.fsync(output.fileno())
    os.replace(temporary,path)


def checked_root(target, *, require_executable=True):
    target=Path(target).absolute()
    if (target != target.resolve() or target == Path(target.anchor) or not target.is_dir()
            or require_executable and not (target / "MOKU.exe").is_file()):
        raise UpdateError("更新目标目录无效或含链接")
    for name in MANAGED:
        path=target/name
        if path.is_symlink() or getattr(path,"is_junction",lambda:False)():raise UpdateError("程序文件不能是链接")
    return target


def _read_transaction(target):
    journal = work_root(target) / "transaction.json"
    if not journal.exists():
        return None
    if journal.resolve() != journal:
        raise UpdateError("更新恢复记录不能是链接")
    record = json.loads(read_bounded(journal))
    if not isinstance(record, dict) or record.get("target") != str(target):
        raise UpdateError("恢复目标不一致")
    return record


def _pending_transaction(target):
    record = _read_transaction(target)
    if record is None:
        return None
    if record.get("phase") in ("complete", "rolled-back"):
        return None
    if record.get("phase") not in ("installing", "awaiting-start"):
        raise UpdateError("更新恢复阶段无效")
    names, existed = record.get("names"), record.get("existed")
    if (not isinstance(names, list) or not names or len(names) > len(MANAGED)
            or any(not isinstance(name, str) or name not in MANAGED for name in names)
            or len(set(names)) != len(names) or "MOKU.exe" not in names
            or not isinstance(existed, list) or len(existed) > len(names)
            or any(not isinstance(name, str) or name not in names for name in existed)
            or "MOKU.exe" not in existed):
        raise UpdateError("更新恢复组件记录无效")
    backup = work_root(target) / "previous"
    if record.get("backup") != str(backup) or backup.resolve() != backup or not backup.is_dir():
        raise UpdateError("更新备份位置无效")
    for name in names:
        path = backup / name
        if path.is_symlink() or path.is_junction():
            raise UpdateError("更新备份组件不能是链接")
    if not (target / "MOKU.exe").is_file() and not (backup / "MOKU.exe").is_file():
        raise UpdateError("缺少可恢复的 MOKU.exe")
    return record


def _recovery_helper(target, record):
    name = record.get("helper")
    if not isinstance(name, str):
        raise UpdateError("更新恢复程序位置无效")
    helper = Path(name)
    if (helper.parent.parent.parent != work_root(target) or helper.name != "MOKU.exe"
            or helper.parent.name != "helper" or helper.resolve() != helper or not helper.is_file()):
        raise UpdateError("更新恢复程序位置无效")
    return helper


@contextmanager
def _stage_recovery_entry(target, helper):
    entry = target / RECOVERY_ENTRY
    if entry.exists() or entry.is_symlink():
        raise UpdateError("目录中已存在同名文件，无法准备更新恢复入口")
    # Explorer expands literal %VAR% in both paths and arguments. Store the
    # executable as a file-system item ID and encode the target argument.
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    target_argument = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
    with tempfile.TemporaryDirectory(dir=target, prefix=".moku-recovery-") as directory:
        temporary = Path(directory) / RECOVERY_ENTRY
        values = json.dumps({"link": str(temporary), "target": str(helper),
                             "arguments": f"--recover-update --shortcut {target_argument}"})
        try:
            subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", CREATE_RECOVERY_LINK],
                           input=values, capture_output=True, text=True, check=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateError("无法创建更新恢复快捷方式") from error
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        yield temporary, hashlib.sha256(read_bounded(temporary)).hexdigest()


def _remove_recovery_entry(target, record):
    entry = target / RECOVERY_ENTRY
    try:
        if (record and record.get("recoveryEntrySha256") and entry.is_file() and entry.resolve() == entry
                and hashlib.sha256(read_bounded(entry)).hexdigest() == record["recoveryEntrySha256"]):
            entry.unlink()
    except (OSError, UpdateError) as error:
        LOG.warning("Update recovery entry cleanup deferred: %s", error)


def remove_managed(path, parent):
    # Every recursive removal has a verified absolute target and managed name.
    if path.parent!=parent or path.name not in MANAGED or path.resolve()!=path:
        raise UpdateError("拒绝清理程序目录之外的文件")
    if path.is_dir():shutil.rmtree(path)
    else:path.unlink(missing_ok=True)


def rollback(target, record):
    backup=Path(record["backup"])
    if backup.parent!=work_root(target) or backup.name!="previous":raise UpdateError("更新备份位置无效")
    for name in reversed(record["names"]):
        if name not in MANAGED:raise UpdateError("恢复清单无效")
        old=backup/name;installed=target/name
        if old.exists():
            remove_managed(installed,target);os.replace(old,installed)
        elif name not in record["existed"]:
            remove_managed(installed,target)


def _rollback_transaction(target, record):
    rollback(target, record)
    record["phase"] = "rolled-back"
    write_json(work_root(target) / "transaction.json", record)
    _remove_recovery_entry(target, record)


def recover(target):
    """A separate helper can recover a transaction even without the target EXE."""
    target = checked_root(target, require_executable=False)
    record = _pending_transaction(target)
    if record is None:
        return False
    _rollback_transaction(target, record)
    return True


def _create_program_mutex(kernel, target, purpose):
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    name = f"Local\\MOKU.{purpose}.{work_root(target).name}"
    handle = kernel.CreateMutexW(None, False, name)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    return handle, error == 183  # ERROR_ALREADY_EXISTS


@contextmanager
def _program_gate(target):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    handle, _ = _create_program_mutex(kernel, target, "ProgramGate")
    try:
        result = kernel.WaitForSingleObject(handle, 0)
        if result == 258:  # WAIT_TIMEOUT
            raise UpdateError("程序组件正在更换，请稍后重试")
        if result not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield kernel
        finally:
            kernel.ReleaseMutex(handle)
    finally:
        kernel.CloseHandle(handle)


def hold_program_guard(target):
    """Keep a per-installation presence marker until this process exits."""
    with _program_gate(target) as kernel:
        handle, _ = _create_program_mutex(kernel, target, "ProgramRunning")
        return handle


@contextmanager
def _guard_replacement(target):
    # Holding the gate prevents ordinary startup between the live-process
    # check and the last managed-file move. Several desktop windows may coexist.
    with _program_gate(target) as kernel:
        handle, running = _create_program_mutex(kernel, target, "ProgramRunning")
        kernel.CloseHandle(handle)
        if running:
            raise UpdateError("请关闭该目录中的所有 MOKU 窗口后再试")
        yield


@contextmanager
def install_lock(target):
    import msvcrt
    work=work_root(target);work.mkdir(exist_ok=True)
    if work.resolve() != work or (work / "install.lock").resolve() != work / "install.lock":
        raise UpdateError("更新锁位置不能是链接")
    with (work/"install.lock").open("a+b") as stream:
        stream.seek(0);stream.write(b"0");stream.flush();stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:raise UpdateError("另一个更新流程正在进行") from exc
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def handoff_recovery(target):
    """A running EXE cannot replace itself. Use the retained helper and exit."""
    target = checked_root(target)
    if _pending_transaction(target) is None:
        return False
    # Refuse a second launch while an active helper owns replacement/startup.
    with install_lock(target):
        record = _pending_transaction(target)
        if record is None:
            return False
        helper = _recovery_helper(target, record)
        subprocess.Popen([str(helper),"--recover-update",str(target),str(os.getpid())],cwd=helper.parent,
            creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    return True


def run_recovery(target, pid, *, helper=None):
    target = checked_root(target, require_executable=False)
    if pid < 0:
        raise UpdateError("更新恢复进程编号无效")
    if pid:
        wait_for_exit(pid)
    with install_lock(target):
        record = _pending_transaction(target)
        if record is None:
            return
        if helper is not None and Path(helper).resolve() != _recovery_helper(target, record):
            raise UpdateError("恢复程序与当前更新事务不一致")
        with _guard_replacement(target):
            # Startup can confirm between the helper check and taking this gate.
            recovered = recover(target)
    if recovered:
        subprocess.Popen([str(target / "MOKU.exe")], cwd=target, close_fds=True)


def dispatch_recovery(arguments, *, helper=None):
    target, pid = arguments
    if target == "--shortcut":
        run_recovery(base64.b64decode(pid, validate=True).decode("utf-8"), 0, helper=helper)
    else:
        run_recovery(target, int(pid), helper=helper)


def apply_bundle(target, bundle, version, *, helper=None):
    target=checked_root(target);work=work_root(target)
    if bundle.parent.parent!=work or bundle.name!="MOKU" or bundle.resolve()!=bundle:raise UpdateError("更新暂存目录无效")
    try:
        current = json.loads(read_bounded(target / "BUILD_MANIFEST.json", 4 * 1024 * 1024))
    except (OSError, ValueError) as error:
        raise UpdateError("当前安装清单无法确认，请先恢复程序组件") from error
    current_version = current.get("version") if isinstance(current, dict) else None
    if not isinstance(current_version, str):
        raise UpdateError("当前安装版本无法确认，请先恢复程序组件")
    verify_bundle(target, current_version, installed=True)
    if version_key(version) <= version_key(current_version):
        raise UpdateError("拒绝安装当前或更旧的版本")
    metadata=verify_release(read_bounded(bundle.parent/RELEASE_METADATA),read_bounded(bundle.parent/RELEASE_SIGNATURE),version)
    verify_bundle(bundle,version,expected_manifest_sha256=metadata["manifestSha256"])
    work.mkdir(exist_ok=True);backup=work/"previous"
    # Only remove the previous *managed binary* backup after a new package has
    # been fully verified. User data is never copied into this directory.
    if backup.exists():
        if backup.resolve()!=backup:raise UpdateError("备份目录不能是链接")
        for child in backup.iterdir():remove_managed(child,backup)
        backup.rmdir()
    backup.mkdir()
    names=sorted({path.name for path in bundle.iterdir()}|{name for name in MANAGED if (target/name).exists()})
    record={"target":str(target),"backup":str(backup),"names":names,
        "existed":[name for name in names if (target/name).exists()],"phase":"installing","version":version,
        "helper":str(bundle.parent/"helper"/"MOKU.exe")}
    journal = work / "transaction.json"
    with ExitStack() as staging:
        if helper is not None:
            _remove_recovery_entry(target, _read_transaction(target))
            temporary, digest = staging.enter_context(_stage_recovery_entry(target, helper))
            record["recoveryEntrySha256"] = digest
        write_json(journal, record)
        try:
            if helper is not None:
                # Windows rename refuses to replace an existing destination.
                os.rename(temporary, target / RECOVERY_ENTRY)
            for name in names:
                destination = target / name
                if destination.exists():
                    os.replace(destination, backup / name)
                if (bundle / name).exists():
                    os.replace(bundle / name, destination)
            # The new app confirms startup before the backup is retired.
            record["phase"] = "awaiting-start"
            record["token"] = secrets.token_hex(24)
            write_json(journal, record)
            return record
        except Exception:
            _rollback_transaction(target, record)
            raise


def confirm_startup(target, token):
    journal=work_root(target)/"transaction.json"
    record=json.loads(journal.read_text(encoding="utf-8"))
    if record["phase"]!="awaiting-start" or record["target"]!=str(target) or not secrets.compare_digest(record["token"],token):
        raise UpdateError("更新启动确认不匹配")
    record["phase"]="complete";write_json(journal,record)
    _remove_recovery_entry(target, record)


def wait_for_exit(pid, timeout=90):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];kernel.WaitForSingleObject.restype=ctypes.c_ulong
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return  # Process already exited.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:raise UpdateError("程序尚未退出，更新已停止")
    finally:kernel.CloseHandle(handle)


def run(plan_path):
    plan_path=Path(plan_path).absolute();plan=json.loads(plan_path.read_text(encoding="utf-8"))
    target=checked_root(plan["target"])
    with install_lock(target):_run(plan_path,plan,target)


def _run(plan_path,plan,target):
    """Copied current executable invokes this branch, outside its install root."""
    bundle=Path(plan["bundle"])
    if (plan_path!=bundle.parent/"plan.json" or bundle.parent.parent!=work_root(target)
            or type(plan["pid"]) is not int or plan["pid"]<=0):raise UpdateError("更新计划无效")
    wait_for_exit(plan["pid"])
    journal=work_root(target)/"transaction.json"
    try:
        with _guard_replacement(target):
            if _pending_transaction(target) is not None:
                raise UpdateError("请先恢复上次未完成的更新")
            helper = _recovery_helper(target, {"helper": str(bundle.parent / "helper" / "MOKU.exe")})
            record = apply_bundle(target, bundle, plan["version"], helper=helper)
        process=subprocess.Popen([str(target/"MOKU.exe"),"--update-confirm",record["token"]],cwd=target,close_fds=True)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            status=json.loads(journal.read_text(encoding="utf-8"))
            if status["phase"]=="complete":return
            if process.poll() is not None:break
            time.sleep(.25)
        # A running application might merely be slow. Never replace binaries
        # beneath it; keep the backup and recover on a later failed startup.
        if process.poll() is None:
            ctypes.windll.user32.MessageBoxW(None,"新版启动尚未确认。请关闭 MOKU 后重新启动；上一版备份已保留。","MOKU 更新",0x30)
            return
        with _guard_replacement(target):
            recover(target)
        raise UpdateError("新版启动失败，已恢复上一版")
    except Exception as exc:
        phase=json.loads(journal.read_text(encoding="utf-8"))["phase"] if journal.exists() else "untouched"
        if phase in {"rolled-back","complete","untouched"}:
            subprocess.Popen([str(target/"MOKU.exe")],cwd=target,close_fds=True)
        ctypes.windll.user32.MessageBoxW(None,str(exc),"MOKU 更新未完成",0x10)
