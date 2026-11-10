"""Portable binary replacement and crash recovery. Runs before server imports."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

from update_package import MANAGED, UpdateError, verify_bundle, version_key
from release_security import RELEASE_METADATA, RELEASE_SIGNATURE, read_bounded, verify_release
from version import __version__


def work_root(target):
    identity=hashlib.sha256(str(target).casefold().encode()).hexdigest()[:16]
    return target.parent/(".moku-update-"+identity)


def write_json(path, payload):
    temporary=path.with_suffix(".tmp")
    with temporary.open("w",encoding="utf-8") as output:
        json.dump(payload,output);output.flush();os.fsync(output.fileno())
    os.replace(temporary,path)


def checked_root(target):
    target=Path(target).absolute()
    if target!=target.resolve() or target==Path(target.anchor) or not (target/"MOKU.exe").is_file():
        raise UpdateError("更新目标目录无效或含链接")
    for name in MANAGED:
        path=target/name
        if path.is_symlink() or getattr(path,"is_junction",lambda:False)():raise UpdateError("程序文件不能是链接")
    return target


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


def recover(target):
    """Next ordinary launch recovers an interrupted transaction before imports."""
    target=checked_root(target);journal=work_root(target)/"transaction.json"
    if not journal.exists():return
    record=json.loads(journal.read_text(encoding="utf-8"))
    if record["target"]!=str(target):raise UpdateError("恢复目标不一致")
    if record["phase"] in {"installing","awaiting-start"}:
        rollback(target,record);record["phase"]="rolled-back";write_json(journal,record)


@contextmanager
def install_lock(target):
    import msvcrt
    work=work_root(target);work.mkdir(exist_ok=True)
    with (work/"install.lock").open("a+b") as stream:
        stream.seek(0);stream.write(b"0");stream.flush();stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:raise UpdateError("另一个更新流程正在进行") from exc
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def handoff_recovery(target):
    """A running EXE cannot replace itself. Use the retained helper and exit."""
    target=checked_root(target);journal=work_root(target)/"transaction.json"
    if not journal.exists():return False
    record=json.loads(journal.read_text(encoding="utf-8"))
    if record["phase"] not in {"installing","awaiting-start"}:return False
    helper=Path(record["helper"])
    if helper.parent.parent.parent!=work_root(target) or helper.name!="MOKU.exe" or helper.resolve()!=helper:
        raise UpdateError("更新恢复程序位置无效")
    # Refuse a second launch while an active helper owns replacement/startup.
    with install_lock(target):
        subprocess.Popen([str(helper),"--recover-update",str(target),str(os.getpid())],cwd=helper.parent,
            creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
    return True


def run_recovery(target, pid):
    target=checked_root(target)
    wait_for_exit(pid)
    with install_lock(target):
        recover(target)
        subprocess.Popen([str(target/"MOKU.exe")],cwd=target,close_fds=True)


def apply_bundle(target, bundle, version):
    target=checked_root(target);work=work_root(target)
    if bundle.parent.parent!=work or bundle.name!="MOKU" or bundle.resolve()!=bundle:raise UpdateError("更新暂存目录无效")
    if version_key(version)<=version_key(__version__):raise UpdateError("拒绝安装当前或更旧的版本")
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
    journal=work/"transaction.json";write_json(journal,record)
    try:
        for name in names:
            destination=target/name
            if destination.exists():os.replace(destination,backup/name)
            if (bundle/name).exists():os.replace(bundle/name,destination)
        # The main app confirms successful backend startup. A plain launch
        # before that point sees 'installing' and recovers instead of guessing.
        record["phase"]="awaiting-start";record["token"]=secrets.token_hex(24);write_json(journal,record)
        return record
    except Exception:
        rollback(target,record);record["phase"]="rolled-back";write_json(journal,record)
        raise


def confirm_startup(target, token):
    journal=work_root(target)/"transaction.json"
    record=json.loads(journal.read_text(encoding="utf-8"))
    if record["phase"]!="awaiting-start" or record["target"]!=str(target) or not secrets.compare_digest(record["token"],token):
        raise UpdateError("更新启动确认不匹配")
    record["phase"]="complete";write_json(journal,record)


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
        record=apply_bundle(target,bundle,plan["version"])
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
        rollback(target,record);record["phase"]="rolled-back";write_json(journal,record)
        raise UpdateError("新版启动失败，已恢复上一版")
    except Exception as exc:
        phase=json.loads(journal.read_text(encoding="utf-8"))["phase"] if journal.exists() else "untouched"
        if phase in {"rolled-back","complete","untouched"}:
            subprocess.Popen([str(target/"MOKU.exe")],cwd=target,close_fds=True)
        ctypes.windll.user32.MessageBoxW(None,str(exc),"MOKU 更新未完成",0x10)
