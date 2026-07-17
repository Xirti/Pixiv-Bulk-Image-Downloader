import json
import os
import subprocess
import sys

import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "launch-moku.ps1").read_text(encoding="utf-8-sig")
PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe"


def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"server {port} did not become healthy")


class WorkflowRegressionTests(unittest.TestCase):
    def test_card_selection_initializes_all_pages_for_batch_payload(self):
        self.assertIn("selectedPagesByArtwork.set(item.id, new Set(Array.from({ length: item.pages }, (_, page) => page)))", APP)

    def test_clear_detail_removes_stale_page_controls(self):
        clear_block = APP[APP.index("function clearDetail"):APP.index("function updateSelectionBar")]
        self.assertIn('$("#collectionPages").innerHTML = ""', clear_block)
        self.assertIn('$("#returnToBatch").hidden = true', clear_block)

    def test_launcher_uses_runtime_descriptor_instead_of_commandline_path_match(self):
        self.assertIn("backend.json", LAUNCHER)
        self.assertIn("instanceId", LAUNCHER)
        self.assertIn("System.Threading.Mutex", LAUNCHER)
        self.assertIn("$ready = $false", LAUNCHER)
        self.assertIn("ReleaseMutex", LAUNCHER)
        self.assertIn("'Prompt','Desktop','Browser','Cancel'", LAUNCHER)
        self.assertNotIn("MOKU_TEST_CDP_PORT", LAUNCHER)
        self.assertNotIn("--remote-debugging-port=", LAUNCHER)
        self.assertIn("moku_app.py", LAUNCHER)
        self.assertIn("search_service.py", LAUNCHER)
        self.assertNotIn("Microsoft Edge not found", LAUNCHER)
        self.assertIn("$runtimeDir = Join-Path $env:LocalAppData 'MOKU/runtime'", LAUNCHER)
        self.assertNotIn("CommandLine -match [regex]::Escape($root)", LAUNCHER)
        self.assertNotIn("Get-NetTCPConnection", LAUNCHER)
        self.assertNotIn("$port++", LAUNCHER)
        self.assertIn("TcpListener", LAUNCHER)
        self.assertIn("[Net.IPAddress]::Loopback, 0", LAUNCHER)

    def test_server_health_identifies_instance_and_protocol(self):
        port = free_port()
        instance_id = "regression-instance"
        env = os.environ.copy()
        env["PORT"] = str(port)
        env["MOKU_INSTANCE_ID"] = instance_id
        env["MOKU_CODE_GENERATION"] = "regression-generation"
        process = subprocess.Popen(
            [str(PYTHON if PYTHON.exists() else sys.executable), "server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_health(port)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/health",
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                data = json.loads(response.read())
            self.assertEqual(data.get("instanceId"), instance_id)
            self.assertEqual(data.get("protocolVersion"), 5)
            self.assertEqual(data.get("applicationId"), "MOKU.PixivTagGallery")
            self.assertEqual(data.get("codeGeneration"), "regression-generation")
            self.assertTrue(data.get("requestToken"))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    unittest.main()
