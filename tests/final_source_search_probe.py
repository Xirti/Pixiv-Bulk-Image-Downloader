from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, timeout: float, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> None:
    port = free_port()
    env = os.environ.copy()
    env.update({
        "PORT": str(port),
        "MOKU_INSTANCE_ID": "final-source-search-probe",
        "MOKU_CODE_GENERATION": "final-source-search-probe",
    })
    process = subprocess.Popen(
        [sys.executable, "server.py"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                status, health = request_json(base + "/api/health", 1)
                if status == 200 and health.get("instanceId") == "final-source-search-probe":
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise TimeoutError("source server did not become healthy")
        protected_headers = {"X-MOKU-Request-Token": str(health["requestToken"])}

        query = urllib.parse.urlencode({
            "tag": "猫 犬", "page": 1, "mode": "safe",
            "workType": "all", "includeAi": "true",
        })
        started = time.monotonic()
        status, search = request_json(base + "/api/pixiv/search?" + query, 90, protected_headers)
        elapsed = time.monotonic() - started
        ids = [str(row.get("id")) for row in search.get("items", [])]
        assert status == 200
        assert search.get("tags") == ["猫", "犬"]
        assert search.get("availablePages") == [1, 2, 3, 4]
        assert search.get("preloadedThrough") == 4
        assert len(ids) == 36 and len(ids) == len(set(ids))

        account_status, account = request_json(base + "/api/status", 10, protected_headers)
        assert account_status == 200
        connected = bool(account.get("loggedIn"))
        all_query = urllib.parse.urlencode({
            "tag": "猫", "page": 1, "mode": "all",
            "workType": "all", "includeAi": "true",
        })
        all_status, all_body = request_json(base + "/api/pixiv/search?" + all_query, 90, protected_headers)
        if connected:
            assert all_status == 200, all_body.get("error")
            assert all_body.get("scope") == "all"
        else:
            assert all_status == 403
            assert "授权" in str(all_body.get("error") or "")

        print(json.dumps({
            "ok": True,
            "status": status,
            "tags": search["tags"],
            "itemsOnPage": len(ids),
            "availablePages": search["availablePages"],
            "preloadedThrough": search["preloadedThrough"],
            "uniqueIds": len(set(ids)),
            "elapsedSeconds": round(elapsed, 3),
            "accountConnected": connected,
            "allScopeStatus": all_status,
        }, ensure_ascii=False))
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


if __name__ == "__main__":
    main()
