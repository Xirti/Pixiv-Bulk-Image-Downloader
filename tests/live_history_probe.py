from __future__ import annotations

import json
import sys
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def get_json(base: str, path: str, timeout: int = 300, headers: dict | None = None) -> dict:
    request = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    proxy = server.refresh_network_opener()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        health = get_json(base, "/api/health", 60)
        headers = {"X-MOKU-Request-Token": str(health["requestToken"])}
        status = get_json(base, "/api/status", 60, headers)
        pages = {}
        for page in (1, 17, 18):
            query = urllib.parse.urlencode({"tag": "猫", "page": page, "mode": "safe"})
            pages[page] = get_json(base, "/api/pixiv/search?" + query, 420, headers)
        ids = {page: {str(item["id"]) for item in data["items"]} for page, data in pages.items()}
        intersections = {
            "1x17": len(ids[1] & ids[17]),
            "1x18": len(ids[1] & ids[18]),
            "17x18": len(ids[17] & ids[18]),
        }
        result = {
            "proxy": proxy,
            "loggedIn": status.get("loggedIn"),
            "pages": {
                str(page): {
                    "items": len(data["items"]),
                    "loaded": data["total"],
                    "pageNumbers": data["pageNumbers"],
                    "hasMore": data["hasMore"],
                    "truncatedDates": data["truncatedDates"],
                    "firstId": data["items"][0]["id"] if data["items"] else None,
                    "lastId": data["items"][-1]["id"] if data["items"] else None,
                }
                for page, data in pages.items()
            },
            "intersections": intersections,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert not proxy or urllib.parse.urlsplit(proxy).hostname in {"127.0.0.1", "localhost", "::1"}
        assert all(len(pages[p]["items"]) == 36 for p in (1, 17, 18))
        assert pages[18]["total"] > 600
        assert all(value == 0 for value in intersections.values())
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    main()
