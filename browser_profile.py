"""Validated, durable browser configuration shared by transport instances."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import urllib.parse


PROFILE_REVISION = 1
SUPPORTED_PROFILES = ("chrome150", "chrome151", "chrome152")
DEFAULT_PROFILE = "chrome152"
LOG = logging.getLogger("moku.http")


@dataclass(frozen=True)
class BrowserProfile:
    name: str = DEFAULT_PROFILE
    revision: int = PROFILE_REVISION
    language: str = "zh-CN,zh;q=0.9"

    def headers(self, url: str, headers: dict[str, str]) -> dict[str, str]:
        """Caller has validated the destination; this profile owns identity headers."""
        owned = {"user-agent", "accept", "accept-language", "referer",
                 "upgrade-insecure-requests"}
        result = {key: value for key, value in headers.items()
                  if key.lower() not in owned and not key.lower().startswith("sec-ch-")
                  and not key.lower().startswith("sec-fetch-")}
        parsed = urllib.parse.urlsplit(url)
        cdn = parsed.hostname == "i.pximg.net"
        image = cdn and not parsed.path.lower().endswith(".zip")
        result.update({
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8" if image else "*/*" if cdn else "application/json, text/plain, */*",
            "Accept-Language": self.language,
            "Referer": "https://www.pixiv.net/",
            "Sec-Fetch-Dest": "image" if image else "empty",
            "Sec-Fetch-Mode": "no-cors" if image else "cors",
            "Sec-Fetch-Site": "cross-site" if cdn else "same-origin",
            "Sec-Fetch-User": "",
            "Upgrade-Insecure-Requests": "",
        })
        return result


def load_browser_profile(path: Path) -> BrowserProfile:
    """Resolve once at startup. Persist only public settings, never credentials."""
    reason = "created"
    profile = BrowserProfile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (ValueError, UnicodeError):
        reason = "invalid-settings"
    else:
        if not isinstance(data, dict):
            reason = "invalid-settings"
        elif type(data.get("schema")) is not int or type(data.get("revision")) is not int:
            reason = "invalid-settings"
        elif data["schema"] > 1 or data["revision"] > PROFILE_REVISION:
            raise ValueError("浏览器配置来自较新版本，请使用对应版本的 MOKU")
        elif data["schema"] != 1 or data["revision"] < 0:
            reason = "invalid-settings"
        elif not isinstance(data.get("profile"), str) or data["profile"] not in SUPPORTED_PROFILES:
            reason = "unsupported-profile"
        else:
            profile = BrowserProfile(name=data["profile"])
            if data["revision"] == PROFILE_REVISION:
                return profile
            reason = "revision-upgrade"

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="browser-profile-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "revision": profile.revision, "profile": profile.name}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    LOG.info("Pixiv browser configuration reason=%s profile=%s revision=%s", reason, profile.name, profile.revision)
    return profile
