from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def get(base: str, path: str, *, headers: dict | None = None, timeout: int = 120):
    request = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read()


def main() -> None:
    server.refresh_network_opener()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    host = {"Host": f"127.0.0.1:{httpd.server_port}", "Sec-Fetch-Site": "same-origin"}
    try:
        health_status, _, raw = get(base, "/api/health", headers=host)
        health = json.loads(raw)
        protected = {**host, "X-MOKU-Request-Token": str(health["requestToken"])}
        no_token_status, _, no_token_raw = get(
            base, "/api/pixiv/search?tag=%E7%8C%AB&page=1&mode=r18", headers=host
        )
        r18_status, _, r18_raw = get(
            base, "/api/pixiv/search?tag=%E7%8C%AB&page=1&mode=r18", headers=protected
        )
        removed_status, _, removed_raw = get(base, "/api/background/random", headers=protected)
        cross_status, _, cross_raw = get(
            base, "/api/health", headers={**host, "Sec-Fetch-Site": "cross-site"}
        )
        result = {
            "health": {"status": health_status, "token": bool(health.get("requestToken"))},
            "noToken": {"status": no_token_status, "error": json.loads(no_token_raw).get("error")},
            "r18Unauthorized": {"status": r18_status, "error": json.loads(r18_raw).get("error")},
            "removedBackground": {"status": removed_status, "bytes": len(removed_raw)},
            "crossSite": {"status": cross_status, "error": json.loads(cross_raw).get("error")},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert health_status == 200 and health.get("requestToken")
        assert no_token_status == 403
        assert r18_status == 403 and "授权" in str(result["r18Unauthorized"]["error"])
        assert removed_status == 404
        assert cross_status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
