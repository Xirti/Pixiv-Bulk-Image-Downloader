"""Signed release boundaries; verification is offline and independent of Pixiv."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import time

from edition_profile import PROJECT_REPOSITORY
from release_trust import SIGNATURE_THRESHOLD, TRUSTED_RELEASE_KEYS

MANIFEST_SIGNATURE = "_internal/security/manifest.sig.json"
RELEASE_METADATA = "MOKU-update.json"
RELEASE_SIGNATURE = "MOKU-update.sig.json"
MAX_METADATA = 64 * 1024
DOMAIN = b"MOKU signed release v1\0"


class UpdateError(ValueError):
    pass


def read_bounded(path: Path, limit: int = MAX_METADATA) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError("签名或更新清单过大")
    return raw


def public_key_id(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def signing_message(payload: bytes, purpose: str) -> bytes:
    return DOMAIN + purpose.encode("ascii") + b"\0" + payload


def verify_signature(payload: bytes, envelope: bytes, purpose: str) -> None:
    """Only compiled-in public keys can authorize metadata, never bundled keys."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not TRUSTED_RELEASE_KEYS or not 1 <= SIGNATURE_THRESHOLD <= len(TRUSTED_RELEASE_KEYS):
        raise UpdateError("此构建尚未配置发布公钥，请从项目主页获取正式版本")
    try:
        raw = json.loads(envelope)
        if (not isinstance(raw, dict) or raw.get("schemaVersion") != 1
                or not isinstance(raw.get("signatures"), list) or not 1 <= len(raw["signatures"]) <= 16):
            raise UpdateError("发布签名格式无效")
        accepted = set()
        for row in raw["signatures"]:
            if (not isinstance(row, dict) or not isinstance(row.get("keyId"), str)
                    or not isinstance(row.get("signature"), str)):
                raise UpdateError("发布签名格式无效")
            key_id = row["keyId"]
            if key_id not in TRUSTED_RELEASE_KEYS or key_id in accepted:
                continue
            public = bytes.fromhex(TRUSTED_RELEASE_KEYS[key_id])
            if public_key_id(public) != key_id:
                raise UpdateError("内置发布公钥标识无效")
            signature = base64.b64decode(row["signature"], validate=True)
            Ed25519PublicKey.from_public_bytes(public).verify(signature, signing_message(payload, purpose))
            accepted.add(key_id)
    except (ValueError, InvalidSignature) as exc:
        raise UpdateError("发布签名验证失败，已停止更新") from exc
    if len(accepted) < SIGNATURE_THRESHOLD:
        raise UpdateError("缺少可信发布签名，已停止更新")


def verify_release(payload: bytes, envelope: bytes, expected_version: str) -> dict:
    if len(payload) > MAX_METADATA or len(envelope) > MAX_METADATA:
        raise UpdateError("发布元数据过大")
    verify_signature(payload, envelope, "release-metadata")
    try:
        row = json.loads(payload)
    except ValueError as exc:
        raise UpdateError("发布元数据格式无效") from exc
    fields = {"schemaVersion", "repository", "platform", "version", "filename", "size", "sha256", "manifestSha256", "expires"}
    if (not isinstance(row, dict) or row.keys() != fields or row["schemaVersion"] != 1
            or row["repository"] != PROJECT_REPOSITORY or row["platform"] != "windows-x64"
            or row["version"] != expected_version
            or row["filename"] != f"MOKU-v{expected_version}-windows-x64.zip"
            or type(row["size"]) is not int or not 0 < row["size"] <= 512 * 1024 * 1024
            or any(not isinstance(row[name], str) or not re.fullmatch(r"[0-9a-f]{64}", row[name]) for name in ("sha256", "manifestSha256"))
            or type(row["expires"]) is not int):
        raise UpdateError("发布元数据与当前项目、版本或平台不匹配")
    if row["expires"] <= time.time():
        raise UpdateError("更新签名元数据已过期，请检查系统时间或等待新版；现有程序仍可使用")
    return row


def verify_installation(root: Path, version: str) -> None:
    """Check managed components once per launch, never user data or account state."""
    from update_package import verify_bundle
    # Explicit development builds have a schema-3 manifest and no signature.
    # A release EXE is bound to schema 4 by its embedded build policy.
    from release_build_policy import SIGNED_BUILD
    if not SIGNED_BUILD:
        return
    verify_bundle(root, version, installed=True)
