import json
import os
import subprocess
import sys

import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe"


def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(port: int, origin: str, token: str) -> tuple[int, dict]:
    body = json.dumps({"initial": ""}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/system/select-folder",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Origin": origin,
            "X-MOKU-Request-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class FolderPickerHttpSecurityTests(unittest.TestCase):
    def test_cross_site_origin_cannot_open_native_picker(self):
        port = free_port()
        env = os.environ.copy()
        env["PORT"] = str(port)
        process = subprocess.Popen(
            [str(PYTHON if PYTHON.exists() else sys.executable), "server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
            status, data = request(port, "https://evil.example", "invalid")
            self.assertEqual(status, 403)
            self.assertIn("本机同源", data["error"])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    unittest.main()
