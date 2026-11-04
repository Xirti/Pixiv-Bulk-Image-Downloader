"""Public browser metadata used to keep login language and API language aligned."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile


@dataclass(frozen=True)
class LoginContext:
    user_agent: str = ""
    languages: tuple[str, ...] = ("zh-CN", "zh")

    @property
    def accept_language(self) -> str:
        return ",".join(language if index == 0 else f"{language};q={1-index/10:.1f}"
                        for index, language in enumerate(self.languages))

    @property
    def browser_version(self) -> str:
        match = re.search(r"(?:Edg|Chrome|Firefox)/[0-9.]+", self.user_agent)
        return match[0] if match else "unavailable"

    def public_data(self) -> dict:
        return {"userAgent": self.user_agent, "languages": list(self.languages)}


def parse_login_context(raw) -> LoginContext:
    if not isinstance(raw, dict):
        raise ValueError("登录浏览器信息格式无效")
    agent, languages = raw.get("userAgent"), raw.get("languages")
    if not isinstance(agent, str) or len(agent) > 512 or not agent.isascii() or "\r" in agent or "\n" in agent:
        raise ValueError("登录浏览器标识无效")
    if not isinstance(languages, list) or not 1 <= len(languages) <= 8:
        raise ValueError("登录浏览器语言无效")
    normalized = []
    for language in languages:
        if not isinstance(language, str) or len(language) > 64 or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
            raise ValueError("登录浏览器语言无效")
        if language.lower() not in {value.lower() for value in normalized}:
            normalized.append(language)
    return LoginContext(agent, tuple(normalized))


def load_login_context(path: Path) -> LoginContext:
    try:
        return parse_login_context(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return LoginContext()
    except (OSError, ValueError, UnicodeError):
        logging.getLogger("moku.http").warning("Login browser metadata could not be restored; using Chinese language defaults")
        return LoginContext()


def save_login_context(path: Path, context: LoginContext):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="login-context-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(context.public_data(), stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
