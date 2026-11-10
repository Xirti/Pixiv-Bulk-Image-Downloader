"""Maintainer-only Ed25519 release signer. Never included in the application.

Keys are encrypted with Windows DPAPI CurrentUser. Portable backups use a
password-encrypted PKCS#8 PEM; passwords are read only from the terminal.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from edition_profile import PROJECT_REPOSITORY
from release_security import (MANIFEST_SIGNATURE, RELEASE_METADATA, RELEASE_SIGNATURE,
    UpdateError, public_key_id, read_bounded, signing_message, verify_release)
from release_trust import SIGNATURE_THRESHOLD, TRUSTED_RELEASE_KEYS
from update_package import sha256, verify_bundle, version_key

ROOT = Path(__file__).resolve().parent
KEY_HEADER = b"MOKU-DPAPI-ED25519-1\0"


def protect_key(raw: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise UpdateError("本地发布密钥使用 Windows 当前用户保护，请在 Windows 上操作")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    storage = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), storage)
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    try:
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise UpdateError(f"无法保护或读取发布私钥（Windows 错误 {ctypes.get_last_error()}）；请使用创建它的 Windows 账号，或恢复加密备份")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(storage, 0, len(raw))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel32.LocalFree(output.data)


def key_location(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if path != path.resolve() or path.is_relative_to(ROOT):
        raise UpdateError("私钥必须存放在源码仓库之外，且路径不能经过链接")
    return path


def private_key(path: Path) -> Ed25519PrivateKey:
    payload = read_bounded(key_location(path), 8192)
    if not payload.startswith(KEY_HEADER):
        raise UpdateError("发布私钥文件格式无效")
    return Ed25519PrivateKey.from_private_bytes(protect_key(payload[len(KEY_HEADER):], decrypt=True))


def write_key(path: Path, key: Ed25519PrivateKey) -> None:
    path = key_location(path)
    # A new dedicated directory avoids modifying permissions on unrelated files.
    if path.parent.exists() or path.exists():
        raise UpdateError("为避免覆盖密钥，请指定尚不存在的独立私钥子目录")
    path.parent.mkdir(parents=True, exist_ok=False)
    system = Path(os.environ["SystemRoot"]) / "System32"
    principal = subprocess.check_output([str(system / "WindowsPowerShell/v1.0/powershell.exe"),
        "-NoProfile", "-NonInteractive", "-Command", "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"], text=True).strip()
    if not principal.startswith("S-1-") or any(c not in "S-0123456789" for c in principal):
        raise UpdateError("无法确定当前 Windows 用户的密钥访问权限")
    subprocess.run([str(system / "icacls.exe"), str(path.parent), "/inheritance:r", "/grant:r",
        f"*{principal}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], check=True, capture_output=True)
    encoded = KEY_HEADER + protect_key(key.private_bytes_raw())
    with path.open("xb") as output:
        output.write(encoded)


def check_key(key: Ed25519PrivateKey) -> str:
    public = key.public_key().public_bytes_raw()
    key_id = public_key_id(public)
    if SIGNATURE_THRESHOLD != 1:
        raise UpdateError("当前发布工具只允许单发布密钥；不要更改阈值来模拟多签")
    if TRUSTED_RELEASE_KEYS.get(key_id) != public.hex():
        raise UpdateError("此私钥对应的公钥尚未加入 release_trust.py")
    return key_id


def sign_payload(key: Ed25519PrivateKey, payload: bytes, purpose: str) -> bytes:
    key_id = check_key(key)
    envelope = {"schemaVersion": 1, "signatures": [{"keyId": key_id,
        "signature": base64.b64encode(key.sign(signing_message(payload, purpose))).decode("ascii")}]}
    return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")


def backup_password() -> bytes:
    password = getpass.getpass("备份密码（至少 16 个字符，不会显示）：")
    if len(password) < 16 or password != getpass.getpass("再次输入备份密码："):
        raise UpdateError("密码过短或两次输入不一致")
    return password.encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "check-key", "sign-manifest", "sign-release", "verify-bundle", "export-backup", "import-backup"))
    parser.add_argument("--key", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--register", action="store_true", help="init only: add the public key to release_trust.py")
    args = parser.parse_args()
    if args.action == "verify-bundle":
        if not args.root or not args.version:
            parser.error("verify-bundle requires --root and --version")
        verify_bundle(args.root, args.version)
        print("Signed bundle verified")
        return
    if not args.key:
        parser.error("this action requires --key")
    if args.action == "init":
        key = Ed25519PrivateKey.generate()
        write_key(args.key, key)
        public = key.public_key().public_bytes_raw()
        key_id = public_key_id(public)
        if args.register:
            anchors = {**TRUSTED_RELEASE_KEYS, key_id: public.hex()}
            (ROOT / "release_trust.py").write_text(
                '"""Public release trust anchors. NEVER add private keys to this file.\n\n'
                'Rotation: ship an overlap release signed by the old key before switching.\n"""\n\n'
                f'TRUSTED_RELEASE_KEYS: dict[str, str] = {anchors!r}\nSIGNATURE_THRESHOLD = 1\n', encoding="utf-8")
        print("Release public key ID: " + key_id)
        print("Private key protected with Windows DPAPI CurrentUser; create an encrypted offline backup.")
        return
    if args.action == "import-backup":
        if not args.backup:
            parser.error("import-backup requires --backup")
        restored = serialization.load_pem_private_key(read_bounded(args.backup),
            password=getpass.getpass("备份密码：").encode("utf-8"))
        if not isinstance(restored, Ed25519PrivateKey):
            raise UpdateError("备份不是 Ed25519 发布密钥")
        check_key(restored)
        write_key(args.key, restored)
        print("Encrypted backup restored for this Windows user")
        return
    key = private_key(args.key)
    key_id = check_key(key)
    if args.action == "check-key":
        print("Configured signing key verified: " + key_id)
    elif args.action == "export-backup":
        if not args.backup:
            parser.error("export-backup requires --backup")
        backup = key_location(args.backup)
        encoded = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(backup_password()))
        with backup.open("xb") as output:
            output.write(encoded)
        print("Password-encrypted backup created; keep it offline and separate from its password")
    elif args.action == "sign-manifest":
        if not args.root:
            parser.error("sign-manifest requires --root")
        payload = read_bounded(args.root / "BUILD_MANIFEST.json", 4 * 1024 * 1024)
        if json.loads(payload).get("schemaVersion") != 4:
            raise UpdateError("只允许签署启用发布保护的 schema-4 构建")
        signature = args.root / MANIFEST_SIGNATURE
        signature.parent.mkdir(parents=True, exist_ok=True)
        signature.write_bytes(sign_payload(key, payload, "bundle-manifest"))
        verify_bundle(args.root, json.loads(payload)["version"])
        print("Bundle manifest signed and verified")
    elif args.action == "sign-release":
        if not args.root or not args.version:
            parser.error("sign-release requires --root and --version")
        version_key(args.version)
        filename = f"MOKU-v{args.version}-windows-x64.zip"
        archive = args.root / filename
        row = {"schemaVersion": 1, "repository": PROJECT_REPOSITORY, "platform": "windows-x64",
            "version": args.version, "filename": filename, "size": archive.stat().st_size,
            "sha256": sha256(archive), "manifestSha256": sha256(args.root / "BUILD_MANIFEST.json"),
            "expires": int(time.time()) + 365 * 86400}
        payload = (json.dumps(row, indent=2) + "\n").encode("utf-8")
        signature = sign_payload(key, payload, "release-metadata")
        verify_release(payload, signature, args.version)
        for name, data in ((RELEASE_METADATA, payload), (RELEASE_SIGNATURE, signature)):
            with (args.root / name).open("xb") as output:
                output.write(data)
        print("Release archive metadata signed and verified")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        # Never print key bytes or passwords, including cryptography tracebacks.
        print(f"Signing failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
