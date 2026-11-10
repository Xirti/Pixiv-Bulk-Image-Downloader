"""Opt-in GitHub updates, isolated from Pixiv auth, transport and account caches."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
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
from release_security import MAX_METADATA, RELEASE_METADATA, RELEASE_SIGNATURE, verify_release
from version import __version__
from edition_profile import PROJECT_REPOSITORY

REPOSITORY=PROJECT_REPOSITORY
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
        identity=hashlib.sha256(str(self.install_root).casefold().encode()).hexdigest()[:16]
        self.work=self.install_root.parent/(".moku-update-"+identity)
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
        self.attempt_path=self.path.with_suffix(".last-check")
        self.trust_path=self.path.with_suffix(".trust-state.json")
        if self.attempt_path.exists():
            try:self.last_attempt=float(self.attempt_path.read_text(encoding="ascii"))
            except (OSError,ValueError) as exc:LOG.warning("update check timestamp could not be read: %s",exc)

    @staticmethod
    def validate_options(raw):
        if not isinstance(raw,dict) or raw.keys()-{"channel","automatic"}:raise UpdateError("更新设置无效")
        if "channel" in raw and (not isinstance(raw["channel"],str) or raw["channel"] not in {"stable","beta"}):raise UpdateError("更新通道无效")
        if "automatic" in raw and type(raw["automatic"]) is not bool:raise UpdateError("自动检查设置无效")
        return raw

    def snapshot(self):
        with self.lock:return {**copy.deepcopy(self.state),"options":dict(self.options),"installable":self.frozen}

    def configure(self, raw):
        patch=self.validate_options(raw)
        with self.lock:
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
            if self.state["phase"] in {"checking","downloading","verifying","restarting"}:raise UpdateError("更新操作正在进行")
            if action=="download" and (self.state["phase"]!="available" or not self.state["release"]):raise UpdateError("请先检查可用更新")
            if action not in {"check","download"}:raise UpdateError("更新操作无效")
            if action=="check":self.bundle=None
            self.cancel.clear();self.state.update(phase="checking" if action=="check" else "downloading",error="",received=0)
            channel=self.options["channel"];release=copy.deepcopy(self.state["release"])
            self.thread=threading.Thread(target=self._run,args=(action,channel,release),name="moku-updates",daemon=True);self.thread.start()
        return self.snapshot()

    def _open(self, url):
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
                self.last_attempt=time.time()
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
            (job/"job.json").write_text(json.dumps({"target":str(self.install_root)}),encoding="utf-8")
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
            archive.unlink();self.bundle=job/"MOKU"
            self.cleanup()
            with self.lock:self.state.update(phase="ready",error="")
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
            plan={"target":str(self.install_root),"bundle":str(self.bundle),"version":self.state["release"]["version"],"pid":os.getpid()}
            plan_path=helper.parent/"plan.json";plan_path.write_text(json.dumps(plan),encoding="utf-8")
            subprocess.Popen([str(helper/"MOKU.exe"),"--apply-update",str(plan_path)],cwd=helper,
                creationflags=subprocess.CREATE_NO_WINDOW,close_fds=True)
            self.restarting=True;self.state["phase"]="restarting"
            return {"ok":True}

    def start_scheduler(self):
        def schedule():
            while not self.timer_stop.wait(30):
                with self.lock:due=self.options["automatic"] and time.time()-self.last_attempt>=86400 and self.state["phase"] not in {"checking","downloading","verifying","ready","restarting"}
                if due:
                    try:self.start("check")
                    except UpdateError:pass  # A foreground operation acquired it first.
        threading.Thread(target=schedule,name="moku-update-check",daemon=True).start()

    def cleanup(self):
        """Retain the newest staged helper and one binary rollback copy."""
        if not self.work.is_dir() or self.work.resolve()!=self.work:return
        jobs=[]
        for path in self.work.glob("package-*"):
            marker=path/"job.json"
            if path.resolve()!=path or not path.is_dir() or not marker.is_file():continue
            try:
                if json.loads(marker.read_text(encoding="utf-8"))!={"target":str(self.install_root)}:continue
                jobs.append(path)
            except (OSError,ValueError) as exc:LOG.warning("update cleanup marker could not be read: %s",exc)
        jobs.sort(key=lambda path:path.stat().st_mtime,reverse=True)
        for path in jobs[1:]:
            try:shutil.rmtree(path)
            except OSError as exc:LOG.warning("old update staging cleanup deferred: %s",exc)

    def close(self):
        self.timer_stop.set();self.cancel.set()
