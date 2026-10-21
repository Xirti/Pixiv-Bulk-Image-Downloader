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
        toggle_block = APP[
            APP.index("function toggleArtworkSelection"):
            APP.index("function selectAllCurrentPage")
        ]
        self.assertIn("selection.choose([item], origin)", toggle_block)
        self.assertIn("reportSelectionRejection(result)", toggle_block)
        self.assertNotIn("Array.from", toggle_block)

    def test_clear_detail_removes_stale_page_controls(self):
        clear_block = APP[APP.index("function clearDetail"):APP.index("function updateSelectionBar")]
        self.assertIn('$("#collectionPages").innerHTML = ""', clear_block)
        self.assertIn("closeBasketPage()", clear_block)

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
            self.assertEqual(data.get("applicationId"), "MOKU.PixivTagGallery.GlassLab01")
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
