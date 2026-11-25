"""Opt-in GitHub updates, isolated from Pixiv auth, transport and account caches."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from update_package import MANAGED, MAX_ARCHIVE, UpdateError, extract_package, verify_bundle, version_key
from release_security import MAX_METADATA, RELEASE_METADATA, RELEASE_SIGNATURE, read_bounded, verify_release
from runtime_identity import process_identity
from update_install import work_root, write_json, handoff_status_path
from version import __version__
from edition_profile import PROJECT_REPOSITORY

REPOSITORY=PROJECT_REPOSITORY
CHECK_INTERVAL_SECONDS = 86400
LOG=logging.getLogger("moku.updates")


def select_release(rows, current, channel):
    if not isinstance(rows,list):raise UpdateError("GitHub 版本数据无效")
    candidates=[]
    for row in rows:
        if not isinstance(row,dict) or row.get("draft") or channel=="stable" and row.get("prerelease"):continue
        tag=row.get("tag_name","")
        if not isinstance(tag,str):continue
        try:key=version_key(tag)
        except UpdateError:continue  # Unrelated historical tags are not application releases.
        if key<=version_key(current) or channel=="stable" and key[3]!=3:continue
        assets=row.get("assets")
        if not isinstance(assets,list):continue
        name=f"MOKU-v{tag.removeprefix('v')}-windows-x64.zip"
        for asset in assets:
            if not isinstance(asset,dict) or asset.get("name")!=name:continue
            url=asset.get("browser_download_url","")
            prefix=f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
            digest=asset.get("digest","")
            size=asset.get("size")
            if (not isinstance(url,str) or not url.startswith(prefix) or urllib.parse.urlsplit(url).query
                    or not isinstance(digest,str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}",digest)
                    or type(size) is not int or not 0<size<=MAX_ARCHIVE):continue
            signatures={}
            for name,field in ((RELEASE_METADATA,"metadataUrl"),(RELEASE_SIGNATURE,"signatureUrl")):
                matches=[entry for entry in assets if isinstance(entry,dict) and entry.get("name")==name]
                if len(matches)==1:
                    entry=matches[0];address=entry.get("browser_download_url","");length=entry.get("size")
                    if address==prefix+name and type(length) is int and 0<length<=MAX_METADATA:
                        signatures[field]=address
            candidates.append((key,{"version":tag.removeprefix("v"),"url":url,"size":size,
                "sha256":digest[7:].lower(),"published":str(row.get("published_at") or "")[:32],**signatures}))
    return max(candidates,key=lambda item:item[0])[1] if candidates else None


class _ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed=urllib.parse.urlsplit(newurl)
        if parsed.scheme!="https" or parsed.hostname not in {"github.com","release-assets.githubusercontent.com","objects.githubusercontent.com"}:
            raise UpdateError("更新下载重定向地址无效")
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class AppUpdates:
    def __init__(self, path, *, install_root=None, opener=None):
        self.path=Path(path);self.lock=threading.RLock();self.gate=threading.RLock()
        self.frozen=bool(getattr(sys,"frozen",False))
        self.install_root=Path(install_root or (Path(sys.executable).parent if self.frozen else Path(__file__).parent)).resolve()
        self.work=work_root(self.install_root)
        self.opener=opener or urllib.request.build_opener(_ReleaseRedirects())
        self.options={"channel":"stable","automatic":False}
        config_error=""
        try:
            if self.path.exists():self.options.update(self.validate_options(json.loads(self.path.read_text(encoding="utf-8"))))
        except (OSError,ValueError) as exc:
            LOG.warning("update options could not be read: %s",exc)
            config_error="更新设置读取失败，已暂用手动检查"
        self.state={"phase":"idle","current":__version__,"release":None,"received":0,"error":config_error,"checked":0}
        self.cancel=threading.Event();self.thread=None;self.restarting=False;self.active_downloads=0
        self.bundle=None;self.timer_stop=threading.Event();self.last_attempt=0;self.allow_restart=False
        self.helper_process=None;self.helper_status=None
        self.attempt_path=self.path.with_suffix(".last-check")
        self.trust_path=self.path.with_suffix(".trust-state.json")
        self.next_check = time.monotonic()
        try:
            stamp = float(self.attempt_path.read_text(encoding="ascii"))
            now = time.time()
            if not math.isfinite(stamp) or not 0 <= stamp <= now:
                raise ValueError("update check timestamp outside elapsed wall time")
            self.last_attempt = stamp
            self.next_check += max(0, CHECK_INTERVAL_SECONDS - (now - stamp))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            LOG.warning("update check timestamp could not be read: %s", exc)

    @staticmethod
    def validate_options(raw):
        if not isinstance(raw,dict) or raw.keys()-{"channel","automatic"}:raise UpdateError("更新设置无效")
        if "channel" in raw and (not isinstance(raw["channel"],str) or raw["channel"] not in {"stable","beta"}):raise UpdateError("更新通道无效")
        if "automatic" in raw and type(raw["automatic"]) is not bool:raise UpdateError("自动检查设置无效")
        return raw

    def _check_open(self):
        if self.timer_stop.is_set():raise UpdateError("更新服务已关闭，请重新打开程序")

    def snapshot(self):
        with self.lock:return {**copy.deepcopy(self.state),"options":dict(self.options),"installable":self.frozen}

    def configure(self, raw):
        patch=self.validate_options(raw)
        with self.lock:
            self._check_open()
            if self.state["phase"] in {"checking","downloading","verifying","restarting"}:raise UpdateError("请等待当前更新操作结束")
            changed="channel" in patch and patch["channel"]!=self.options["channel"]
            options={**self.options,**patch};self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix(".tmp");temporary.write_text(json.dumps(options),encoding="utf-8");os.replace(temporary,self.path)
            self.options=options
            if changed:
                self.bundle=None;self.state.update(phase="idle",release=None,error="",checked=0)
        return self.snapshot()

    def start(self, action):
        with self.lock:
            self._check_open()
            if self.state["phase"] in {"checking","downloading","verifying","restarting"}:raise UpdateError("更新操作正在进行")
            if action=="download" and (self.state["phase"]!="available" or not self.state["release"]):raise UpdateError("请先检查可用更新")
            if action not in {"check","download"}:raise UpdateError("更新操作无效")
            channel=self.options["channel"];release=copy.deepcopy(self.state["release"])
            try:
                worker=threading.Thread(target=self._run,args=(action,channel,release),name="moku-updates",daemon=True)
                self.cancel.clear()
                worker.start()
            except (RuntimeError,OSError) as error:
                raise UpdateError("无法启动更新任务，请重试") from error
            self.thread=worker
            if action=="check":self.bundle=None
            self.state.update(phase="checking" if action=="check" else "downloading",error="",received=0)
        return self.snapshot()

    def _open(self, url):
        if self.cancel.is_set():raise UpdateError("更新操作已停止")
        return self.opener.open(urllib.request.Request(url,headers={"User-Agent":"MOKU/"+__version__,"Accept":"application/vnd.github+json"}),timeout=20)

    def _authenticate_release(self, release, channel):
        if not {"metadataUrl","signatureUrl"}<=release.keys():
            raise UpdateError("新版缺少发布签名，已停止更新；请等待签名版本")
        raw=[]
        for field in ("metadataUrl","signatureUrl"):
            with self._open(release[field]) as response:payload=response.read(MAX_METADATA+1)
            if len(payload)>MAX_METADATA:raise UpdateError("发布签名元数据过大")
            raw.append(payload)
        metadata=verify_release(*raw,release["version"])
        if any(metadata[field]!=release[field] for field in ("size","sha256")):
            raise UpdateError("GitHub 资源与签名信息不一致，已停止更新")
        # Independent per-channel floors preserve stable/beta switching. This
        # protects observed releases, not an OS administrator editing local data.
        floors={}
        if self.trust_path.exists():
            floors=json.loads(self.trust_path.read_text(encoding="utf-8"))
            if not isinstance(floors,dict) or floors.keys()-{"stable","beta"}:
                raise UpdateError("更新信任记录无效，现有版本不受影响")
            for item in floors.values():
                if (not isinstance(item,dict) or item.keys()!={"version","sha256"}
                        or not isinstance(item["version"],str) or not isinstance(item["sha256"],str)
                        or not re.fullmatch(r"[0-9a-f]{64}",item["sha256"])):
                    raise UpdateError("更新信任记录无效")
                version_key(item["version"])
        old=floors.get(channel)
        if old and (version_key(metadata["version"])<version_key(old["version"])
                or metadata["version"]==old["version"] and metadata["sha256"]!=old["sha256"]):
            raise UpdateError("检测到更新版本回退或同版本文件替换，已停止更新")
        floors[channel]={"version":metadata["version"],"sha256":metadata["sha256"]}
        temporary=self.trust_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(floors),encoding="utf-8");os.replace(temporary,self.trust_path)
        return {**release,"manifestSha256":metadata["manifestSha256"],
            "signedMetadata":raw[0].decode("utf-8"),"metadataSignature":raw[1].decode("utf-8")}

    def _run(self, action, channel, release):
        job=None
        try:
            if action=="check":
                with self.lock:
                    self.last_attempt = time.time()
                    self.next_check = time.monotonic() + CHECK_INTERVAL_SECONDS
                self.path.parent.mkdir(parents=True,exist_ok=True)
                self.attempt_path.write_text(str(self.last_attempt),encoding="ascii")
                endpoint="/releases/latest" if channel=="stable" else "/releases?per_page=100"
                with self._open("https://api.github.com/repos/"+REPOSITORY+endpoint) as response:
                    raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:raise UpdateError("GitHub 版本响应过大")
                rows=json.loads(raw);release=select_release([rows] if channel=="stable" else rows,__version__,channel)
                if release:release=self._authenticate_release(release,channel)
                with self.lock:self.state.update(phase="available" if release else "current",release=release,checked=time.time())
                return
            self.work.mkdir(exist_ok=True)
            if self.work.is_symlink() or getattr(self.work,"is_junction",lambda:False)():raise UpdateError("更新目录不能是链接")
            job=Path(tempfile.mkdtemp(prefix="package-",dir=self.work));archive=job/"release.zip"
            pid = os.getpid()
            write_json(job / "job.json", {"target": str(self.install_root), "owner": [pid, process_identity(pid)]})
            metadata=verify_release(release["signedMetadata"].encode("utf-8"),release["metadataSignature"].encode("utf-8"),release["version"])
            (job/RELEASE_METADATA).write_text(release["signedMetadata"],encoding="utf-8",newline="\n")
            (job/RELEASE_SIGNATURE).write_text(release["metadataSignature"],encoding="utf-8",newline="\n")
            digest=hashlib.sha256();received=0
            with self._open(release["url"]) as response,archive.open("xb") as output:
                while chunk:=response.read(256*1024):
                    if self.cancel.is_set():raise UpdateError("已取消更新下载")
                    received+=len(chunk)
                    if received>release["size"]:raise UpdateError("更新包大小不匹配")
                    digest.update(chunk);output.write(chunk)
                    with self.lock:self.state["received"]=received
            if received!=release["size"] or digest.hexdigest()!=release["sha256"]:raise UpdateError("更新包大小或 SHA-256 校验失败")
            with self.lock:self.state["phase"]="verifying"
            extract_package(archive,job/"MOKU",release["version"],expected_manifest_sha256=metadata["manifestSha256"],cancel_event=self.cancel)
            if self.cancel.is_set():raise UpdateError("已取消更新下载")
            archive.unlink()
            self.cleanup(keep=job)
            with self.lock:
                if self.cancel.is_set():
                    raise UpdateError("已取消更新下载")
                self.bundle = job / "MOKU"
                self.state.update(phase="ready", error="")
        except Exception as exc:
            LOG.warning("update %s failed: %s",action,exc)
            if job and job.is_relative_to(self.work) and job.parent==self.work:
                try:shutil.rmtree(job)
                except OSError:LOG.warning("update temporary files could not be removed: %s",job)
            message=str(exc) if isinstance(exc,UpdateError) else "更新服务暂不可用，请检查网络、代理与目录权限后重试"
            with self.lock:self.state.update(phase="available" if action=="download" else "error",error=message)

    def cancel_download(self):
        with self.lock:
            if self.state["phase"] not in {"downloading","verifying"}:raise UpdateError("当前没有更新下载")
            self.cancel.set()
        return self.snapshot()

    def restart(self):
        """Native host only. Gate download admission until the process exits."""
        with self.gate,self.lock:
            self._check_open()
            if self.restarting:
                try:
                    waiting=self._helper_phase()=="waiting"
                except Exception:
                    self._retire_helper()
                    raise
                if waiting:
                    return {"ok":True}
                self._retire_helper()
            if not self.frozen:raise UpdateError("源码运行不替换文件，请使用便携版")
            if self.active_downloads:raise UpdateError("请等待下载任务完成或中断后再重启更新")
            if not self.allow_restart:raise UpdateError("请关闭共享后端并重新打开 MOKU 桌面窗口后更新")
            if self.state["phase"]!="ready" or self.bundle is None:raise UpdateError("更新包尚未准备好")
            release=self.state["release"]
            metadata=verify_release(release["signedMetadata"].encode("utf-8"),release["metadataSignature"].encode("utf-8"),release["version"])
            verify_bundle(self.bundle,release["version"],expected_manifest_sha256=metadata["manifestSha256"])
            # Copy the current bootloader/runtime outside the install folder so
            # Windows can release every file to be replaced after this PID exits.
            helper=self.bundle.parent/"helper"
            if helper.exists():
                if helper.resolve()!=helper:raise UpdateError("更新辅助目录不能是链接")
                shutil.rmtree(helper)
            helper.mkdir(exist_ok=False)
            for name in MANAGED:
                source=self.install_root/name
                if source.is_dir():shutil.copytree(source,helper/name)
                elif source.is_file():shutil.copy2(source,helper/name)
            parent_identity=process_identity(os.getpid())
            if type(parent_identity) is not int or parent_identity<=0:
                raise UpdateError("无法确认更新父进程身份，请重试")
            token=secrets.token_hex(16)
            plan={"target":str(self.install_root),"bundle":str(self.bundle),"version":self.state["release"]["version"],
                  "pid":os.getpid(),"parentIdentity":parent_identity,"handoff":token}
            plan_path=helper.parent/"plan.json"
            write_json(plan_path, plan)
            process = subprocess.Popen([str(helper/"MOKU.exe"),"--apply-update",str(plan_path)],cwd=helper,
                creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
            self.helper_process=process;self.helper_status=handoff_status_path(plan_path,token)
            self.restarting=True;self.state["phase"]="restarting"
            try:
                marker = helper.parent / "job.json"
                ownership = json.loads(read_bounded(marker))
                ownership["helperProcess"] = [process.pid, process_identity(process.pid)]
                write_json(marker, ownership)
            except (OSError, ValueError) as error:
                LOG.warning("Update helper ownership could not be recorded: %s", error)
            try:
                deadline=time.monotonic()+30
                while True:
                    phase=self._helper_phase()
                    if phase=="waiting":break
                    if phase!="starting":raise UpdateError("更新助手未能接手，请重试")
                    if time.monotonic()>=deadline:raise UpdateError("更新助手启动确认超时，请重试")
                    time.sleep(.05)
            except Exception:
                self._retire_helper()
                raise
            return {"ok":True}

    def _helper_phase(self):
        if self.helper_process.poll() is not None:return "stopped"
        path=self.helper_status
        if path.resolve()!=path:raise UpdateError("更新交接状态不能是链接")
        try:
            state=json.loads(read_bounded(path))
        except FileNotFoundError:
            return "starting"
        except ValueError as error:
            raise UpdateError("更新交接状态无效") from error
        if (not isinstance(state,dict) or set(state)!={"phase"}
                or not isinstance(state["phase"],str) or state["phase"] not in {"waiting","failed"}):
            raise UpdateError("更新交接状态无效")
        return state["phase"] if self.helper_process.poll() is None else "stopped"

    def _retire_helper(self):
        if self.helper_process.poll() is None:self.helper_process.kill()
        try:
            self.helper_process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise UpdateError("更新助手尚未停止，请稍后重试") from error
        self.helper_process=None;self.helper_status=None
        self.restarting=False;self.state["phase"]="ready"

    def start_scheduler(self):
        def schedule():
            while not self.timer_stop.wait(30):
                with self.lock:
                    due = (self.options["automatic"] and time.monotonic() >= self.next_check
                           and self.state["phase"] not in {"checking","downloading","verifying","ready","restarting"})
                if due:
                    try:self.start("check")
                    except UpdateError:pass  # A foreground operation acquired it first.
        threading.Thread(target=schedule,name="moku-update-check",daemon=True).start()

    def cleanup(self, *, keep=None):
        """Retire only abandoned staging jobs, retaining one for diagnostics."""
        if not self.work.is_dir() or self.work.resolve()!=self.work:return
        journal = self.work / "transaction.json"
        if journal.exists():
            try:
                record = json.loads(read_bounded(journal))
            except (OSError, ValueError) as error:
                LOG.warning("Update staging cleanup deferred: %s", error)
                return
            if not isinstance(record, dict) or record.get("target") != str(self.install_root):
                LOG.warning("Update staging cleanup deferred: invalid transaction owner")
                return
            recovery = None
            if record.get("phase") in ("installing", "awaiting-start"):
                helper = record.get("helper")
                if not isinstance(helper, str):
                    LOG.warning("Update staging cleanup deferred: missing recovery helper")
                    return
                recovery = Path(helper).parent.parent
        else:
            recovery = None
        with self.lock:
            ready = self.bundle.parent if self.bundle is not None else None
        jobs=[]
        for path in self.work.glob("package-*"):
            marker=path/"job.json"
            if path in (keep, ready, recovery) or path.resolve()!=path or not path.is_dir() or not marker.is_file():
                continue
            try:
                record = json.loads(read_bounded(marker))
                if not isinstance(record, dict) or record.get("target") != str(self.install_root):
                    continue
                if any(self._owner_running(record.get(field)) for field in ("owner", "helperProcess")):
                    continue
                plan = path / "plan.json"
                # The parent publishes the plan before Popen can report a PID.
                if plan.is_file() and time.time() - plan.stat().st_mtime < 600:
                    continue
                jobs.append((path.stat().st_mtime, path))
            except (OSError,ValueError) as exc:LOG.warning("update cleanup marker could not be read: %s",exc)
        jobs.sort(reverse=True)
        for _, path in jobs[1:]:
            try:shutil.rmtree(path)
            except OSError as exc:LOG.warning("old update staging cleanup deferred: %s",exc)

    @staticmethod
    def _owner_running(stamp):
        if stamp is None:
            return False
        if (not isinstance(stamp, list) or len(stamp) != 2 or type(stamp[0]) is not int
                or not 0 < stamp[0] <= 0xffffffff or (stamp[1] is not None and type(stamp[1]) is not int)):
            raise ValueError("invalid update process ownership")
        pid, recorded = stamp
        current = process_identity(pid)
        return current is not None and (current == recorded or current == -1 or recorded == -1)

    def close(self):
        with self.lock:
            self.timer_stop.set();self.cancel.set()
