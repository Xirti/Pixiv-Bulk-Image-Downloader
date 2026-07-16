from __future__ import annotations

import json
import os
import shutil
import subprocess

import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("MOKU_PROBE_EXE") or ROOT / "dist" / "MOKU" / "MOKU.exe").resolve()


def request_json(url: str, timeout: float, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> None:
    if not EXE.is_file():
        raise FileNotFoundError(EXE)
    probe_root = Path(tempfile.mkdtemp(prefix="moku-packaged-search-"))
    runtime_dir = probe_root / "runtime"
    runtime_dir.mkdir()
    descriptor = runtime_dir / "backend.json"
    env = os.environ.copy()
    env.update({
        "MOKU_RUNTIME_DIR": str(runtime_dir),
        "MOKU_MUTEX_NAME": "Local\\MOKU.PackagedSearch." + os.urandom(12).hex(),
        "MOKU_NO_BROWSER": "1",
        "MOKU_TEST_EXIT_AFTER_SECONDS": "180",
    })
    process = subprocess.Popen(
        [str(EXE), "--serve-only"], cwd=EXE.parent, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        base = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"packaged EXE exited early: {process.returncode}")
            if descriptor.is_file():
                try:
                    runtime = json.loads(descriptor.read_text(encoding="utf-8-sig"))
                    base = f"http://127.0.0.1:{int(runtime['port'])}"
                    status, health = request_json(base + "/api/health", 2)
                    if status == 200 and health.get("instanceId") == runtime.get("instanceId"):
                        break
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    pass
            time.sleep(0.15)
        else:
            raise TimeoutError("packaged backend did not become healthy")
        protected_headers = {"X-MOKU-Request-Token": str(health["requestToken"])}

        query = urllib.parse.urlencode({
            "tag": "猫 犬", "page": 1, "mode": "safe",
            "workType": "all", "includeAi": "true",
        })
        started = time.monotonic()
        status, search = request_json(base + "/api/pixiv/search?" + query, 90, protected_headers)
        elapsed = time.monotonic() - started
        ids = [str(row.get("id")) for row in search.get("items", [])]
        assert status == 200, search.get("error")
        assert search.get("tags") == ["猫", "犬"]
        assert search.get("availablePages") == [1, 2, 3, 4]
        assert search.get("preloadedThrough") == 4
        assert len(ids) == 36 and len(ids) == len(set(ids))

        status_code, account = request_json(base + "/api/status", 10, protected_headers)
        assert status_code == 200
        connected = bool(account.get("loggedIn"))
        all_query = urllib.parse.urlencode({
            "tag": "猫", "page": 1, "mode": "all",
            "workType": "all", "includeAi": "true",
        })
        all_status, all_result = request_json(base + "/api/pixiv/search?" + all_query, 90, protected_headers)
        if connected:
            assert all_status == 200, all_result.get("error")
            assert all_result.get("scope") == "all"
        else:
            assert all_status == 403

        print(json.dumps({
            "ok": True,
            "exe": str(EXE),
            "tags": search["tags"],
            "itemsOnPage": len(ids),
            "uniqueIds": len(set(ids)),
            "availablePages": search["availablePages"],
            "preloadedThrough": search["preloadedThrough"],
            "elapsedSeconds": round(elapsed, 3),
            "accountConnected": connected,
            "allScopeStatus": all_status,
        }, ensure_ascii=False))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        shutil.rmtree(probe_root, ignore_errors=True)


if __name__ == "__main__":
    main()
