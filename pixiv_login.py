from __future__ import annotations

import time
from typing import Any

from auth_store import validate_session_value


LOGIN = "https://www.pixiv.net/login.php?return_to=https%3A%2F%2Fwww.pixiv.net%2F"


def session_cookie_metadata(cookies: list[dict[str, Any]], now: float | None = None) -> dict[str, int]:
    """Return diagnostics that never include cookie values."""
    current = time.time() if now is None else float(now)
    summary = {
        "totalRows": len(cookies),
        "phpRows": 0,
        "eligibleRows": 0,
        "sessionRows": 0,
        "expiredRows": 0,
    }
    for row in cookies:
        if row.get("name") != "PHPSESSID":
            continue
        summary["phpRows"] += 1
        try:
            expires = float(row.get("expires") or 0)
        except (TypeError, ValueError):
            expires = -1
        if expires == 0:
            summary["sessionRows"] += 1
        elif 0 < expires <= current:
            summary["expiredRows"] += 1
        if _eligible_cookie(row, expires, current):
            summary["eligibleRows"] += 1
    return summary


def _eligible_cookie(row: dict[str, Any], expires: float, now: float) -> bool:
    return (
        row.get("domain") in {".pixiv.net", "www.pixiv.net"}
        and row.get("path") == "/"
        and row.get("secure") is True
        and row.get("httpOnly") is True
        and expires >= 0
        and not (expires > 0 and expires <= now)
        and not row.get("partitionKey")
    )


def select_session_cookie(cookies: list[dict[str, Any]], now: float | None = None) -> str:
    """Select one strict Pixiv session candidate from WebView2 cookie rows."""
    current = time.time() if now is None else float(now)
    candidates: set[str] = set()
    for row in cookies:
        if row.get("name") != "PHPSESSID":
            continue
        try:
            expires = float(row.get("expires") or 0)
        except (TypeError, ValueError):
            continue
        if not _eligible_cookie(row, expires, current):
            continue
        try:
            candidates.add(validate_session_value(str(row.get("value") or "")))
        except ValueError:
            continue
    if len(candidates) != 1:
        raise ValueError("Pixiv会话候选数量异常")
    return next(iter(candidates))
