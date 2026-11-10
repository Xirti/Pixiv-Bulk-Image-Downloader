"""Untrusted release archive boundary; no application/server startup side effects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from release_security import MANIFEST_SIGNATURE, UpdateError, read_bounded, verify_signature

MANAGED = frozenset({"MOKU.exe", "_internal", "MOKU.exe.config", "BUILD_MANIFEST.json",
    "SHA256.txt", "RELEASE_NOTES.md", "LICENSE", "PRIVACY.md", "SECURITY.md",
    "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LICENSES.txt"})
MAX_ARCHIVE = 512*1024*1024
MAX_EXPANDED = 1536*1024*1024
MAX_FILES = 12000


def version_key(text):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?", text)
    if not match:
        raise UpdateError("版本号格式不受支持")
    return (*map(int, match.group(1,2,3)), {"alpha":0,"beta":1,"rc":2,None:3}[match[4]], int(match[5] or 0))


def safe_relative(raw):
    if not isinstance(raw, str) or not raw or "\\" in raw or raw.startswith("/"):
        raise UpdateError("更新包包含无效路径")
    path=PurePosixPath(raw)
    if any(part in {"",".",".."} or part.rstrip(" .")!=part or re.search(r'[<>:"|?*\x00-\x1f]',part)
           or re.fullmatch(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?",part,re.I) for part in raw.split("/")):
        raise UpdateError("更新包包含不安全的路径")
    if path.parts[0] not in MANAGED or len(path.parts)>1 and path.parts[0]!="_internal":
        raise UpdateError("更新包包含程序目录之外的内容")
    return path.as_posix()


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateError("已取消更新下载")


def verify_bundle(root, expected_version, *, installed=False, expected_manifest_sha256=None, cancel_event=None):
    """Validate the entire file set and manifest hashes, independent of this build."""
    root=Path(os.path.abspath(root))
    _check_cancelled(cancel_event)
    if root!=root.resolve():raise UpdateError("程序目录不能经过链接")
    marker=root/"BUILD_MANIFEST.json"
    raw=read_bounded(marker,4*1024*1024)
    if expected_manifest_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_manifest_sha256:
        raise UpdateError("更新清单与已签名的发布信息不一致")
    verify_signature(raw,read_bounded(root/MANIFEST_SIGNATURE),"bundle-manifest")
    manifest=json.loads(raw)
    if (not isinstance(manifest,dict) or manifest.get("schemaVersion")!=4
            or manifest.get("version")!=expected_version.removeprefix("v")
            or not isinstance(manifest.get("distributionFiles"),dict)):
        raise UpdateError("更新清单或版本不匹配")
    files=manifest["distributionFiles"]
    if not files or len(files)>MAX_FILES:raise UpdateError("更新清单文件数无效")
    actual=set()
    pending=[(root,"")]
    while pending:
        directory,prefix=pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                _check_cancelled(cancel_event)
                if installed and not prefix and entry.name not in MANAGED:continue
                if entry.is_symlink() or getattr(entry.stat(follow_symlinks=False),"st_file_attributes",0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise UpdateError("程序组件不允许链接或重解析点")
                relative=prefix+entry.name
                if entry.is_dir(follow_symlinks=False):pending.append((Path(entry.path),relative+"/"))
                elif entry.is_file(follow_symlinks=False):actual.add(relative)
                else:raise UpdateError("程序组件不是普通文件")
                if len(actual)+len(pending)>MAX_FILES:raise UpdateError("程序组件数超限")
    folded=set()
    for raw,digest in files.items():
        _check_cancelled(cancel_event)
        relative=safe_relative(raw)
        if relative.casefold() in folded or not isinstance(digest,str) or not re.fullmatch(r"[a-fA-F0-9]{64}",digest):
            raise UpdateError("更新清单存在重复路径或无效摘要")
        folded.add(relative.casefold())
        path=root/relative
        if relative not in actual or sha256(path)!=digest.lower():
            raise UpdateError("更新包文件校验失败："+relative)
    excluded={"BUILD_MANIFEST.json",MANIFEST_SIGNATURE}
    if (actual!=set(files)|excluded or not (root/"MOKU.exe").is_file()
            or not (root/"_internal").is_dir() or "MOKU.exe" not in files):
        raise UpdateError("更新包文件集合不完整")
    if not isinstance(manifest.get("exeSha256"),str) or manifest["exeSha256"].lower()!=files["MOKU.exe"].lower():raise UpdateError("程序摘要不匹配")
    return manifest


def extract_package(archive, destination, version, *, expected_manifest_sha256, cancel_event=None):
    """Preflight all paths and sizes before creating a file; refuse overwrite."""
    destination=Path(destination)
    _check_cancelled(cancel_event)
    with zipfile.ZipFile(archive) as source:
        rows=source.infolist()
        if len(rows)>MAX_FILES or sum(row.file_size for row in rows)>MAX_EXPANDED:
            raise UpdateError("更新包解压大小超限")
        seen=set();files=[]
        for row in rows:
            _check_cancelled(cancel_event)
            if row.filename=="MOKU/" and row.is_dir():continue
            parts=PurePosixPath(row.filename).parts
            if len(parts)<2 or parts[0]!="MOKU":raise UpdateError("更新包根目录应为 MOKU")
            name=safe_relative(row.filename[len("MOKU/"):].rstrip("/"))
            if stat.S_ISLNK(row.external_attr>>16) or row.flag_bits&1:raise UpdateError("更新包不允许链接或加密文件")
            if name.casefold() in seen:raise UpdateError("更新包路径重复")
            seen.add(name.casefold())
            if not row.is_dir():files.append((row,name))
        destination.mkdir(parents=True,exist_ok=False)
        for row,name in files:
            _check_cancelled(cancel_event)
            path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
            with source.open(row) as reader,path.open("xb") as writer:
                while True:
                    _check_cancelled(cancel_event)
                    chunk=reader.read(1024*1024)
                    if not chunk:break
                    _check_cancelled(cancel_event)
                    writer.write(chunk)
    return verify_bundle(destination,version,expected_manifest_sha256=expected_manifest_sha256,cancel_event=cancel_event)
