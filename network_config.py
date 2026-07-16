from __future__ import annotations

import urllib.parse


LOOPBACK_PROXY_HOSTS = {"127.0.0.1", "localhost", "::1"}


def normalize_loopback_proxy(value: str) -> str:
    text = str(value or "").strip()
    if ";" in text or "=" in text:
        entries = {
            key.strip().lower(): entry.strip()
            for part in text.split(";")
            if "=" in part
            for key, entry in [part.split("=", 1)]
        }
        text = entries.get("https") or entries.get("http") or ""
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or host not in LOOPBACK_PROXY_HOSTS
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if not port or not 1 <= port <= 65535:
        return ""
    display_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{display_host}:{port}"
