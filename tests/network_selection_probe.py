from __future__ import annotations

import json
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


state_before = server.windows_proxy_state()
proxy = server.refresh_network_opener()
report = {
    "state": state_before,
    "selectedProxy": proxy,
    "pixivProxy": server.PIXIV_PROXY,
}
started = time.monotonic()
try:
    raw, content_type = server.pixiv_request("https://www.pixiv.net/", max_bytes=1024 * 1024)
    report["request"] = {
        "ok": True,
        "bytes": len(raw),
        "contentType": content_type,
        "ms": round((time.monotonic() - started) * 1000),
    }
except Exception as exc:
    report["request"] = {
        "ok": False,
        "exception": type(exc).__name__,
        "error": str(exc),
        "ms": round((time.monotonic() - started) * 1000),
    }
print(json.dumps(report, ensure_ascii=False, indent=2))
