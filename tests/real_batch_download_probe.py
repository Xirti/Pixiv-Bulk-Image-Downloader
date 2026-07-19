import json
import os

import tempfile
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

PORT = 0
OUTPUT = ROOT / "tests" / "real_batch_download_result.json"


def wait_health(port: int) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/health",
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return json.loads(response.read())["requestToken"]
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("batch probe server not ready")


def fake_detail(artwork_id: str, allow_r18: bool = False) -> dict:
    if artwork_id not in {"910001", "910002"}:
        raise server.PixivPolicyError("unexpected artwork")
    pages = 2 if artwork_id == "910001" else 3
    item = {
        "id": artwork_id,
        "restriction": "safe",
        "source": "pixiv",
        "title": f"真实批量回归_{artwork_id}",
        "artist": "MOKU QA",
        "tags": ["回归"],
        "pages": pages,
        "width": 1200,
        "height": 900,
        "bookmarks": 0,
        "date": "2026-07-13",
        "description": "",
        "qualities": [{"id": "regular", "label": "常规", "width": 1200, "height": 900}],
        "formats": [{"id": "source", "label": "源格式"}],
        "pageImages": [],
    }
    for page in range(pages):
        remote = f"https://i.pximg.net/img-original/img/2026/07/13/00/00/00/{artwork_id}_p{page}.png"
        proxy = server.authorize_image_proxy("/api/pixiv/image?" + __import__("urllib.parse").parse.urlencode({"url": remote}), artwork_id, "safe")
        item["pageImages"].append({"width": 1200, "height": 900, "regular": proxy, "original": proxy})
    server.PIXIV_CACHE[artwork_id] = item
    return item


def fake_request(url: str, image_only: bool = False, max_bytes: int = server.MAX_REMOTE_BYTES, session_value: str = ""):
    if not image_only or not url.startswith("https://i.pximg.net/"):
        raise server.PixivPolicyError("unexpected request")
    raw = b"\x89PNG\r\n\x1a\n" + (b"MOKU-REAL-BATCH-" + url.encode("utf-8"))
    return raw, "image/png"


def main() -> None:
    server.PIXIV_CACHE.clear()
    server.IMAGE_TOKENS.clear()
    output_root = Path(tempfile.mkdtemp(prefix="moku-real-batch-"))
    httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_port
    import threading
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    with patch.object(server, "pixiv_detail", side_effect=fake_detail), patch.object(server, "pixiv_request", side_effect=fake_request):
        thread.start()
        try:
            request_token = wait_health(port)
            payload = {
                "groups": [
                    {"id": "910001", "pages": [1]},
                    {"id": "910002", "pages": [0]},
                ],
                "quality": "regular",
                "saveRoot": str(output_root),
                "createFolder": True,
                "context": {"kind": "tags", "value": "回归"},
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/pixiv/batch-download",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-MOKU-Request-Token": request_token,
                    "Sec-Fetch-Site": "same-origin",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read())
                status = response.status
            files = []
            for raw_path in result["saved"]:
                path = output_root / Path(raw_path)
                raw = path.read_bytes()
                files.append({
                    "path": str(path),
                    "bytes": len(raw),
                    "headHex": raw[:16].hex(),
                    "signatureOk": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                    "exists": path.exists(),
                })
            report = {"http": status, "payload": payload, "response": result, "files": files, "outputRoot": str(output_root)}
            # Routine verification must not mutate a tracked build input and
            # invalidate the already verified executable manifest.
            if os.environ.get("MOKU_WRITE_PROBE_RESULT", "").strip() == "1":
                OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            assert status == 200
            assert result["artworks"] == 2 and result["pages"] == 2
            assert len(files) == 2 and all(row["signatureOk"] for row in files)
            assert any("_p1.png" in row["path"] for row in files)
            assert any("_p0.png" in row["path"] for row in files)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
