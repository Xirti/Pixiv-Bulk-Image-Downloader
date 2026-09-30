from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import moku_app
import server


ROOT = Path(__file__).resolve().parents[1]


class RuntimeRevisionTests(unittest.TestCase):
    def test_source_launcher_uses_the_backend_generation_algorithm(self):
        launcher = (ROOT / "launch-moku.ps1").read_text(encoding="utf-8-sig")
        start = launcher.index("$generationScript =")
        end = launcher.index("$runtimeDir =", start)
        source = launcher[start:end]
        # Exercise the launcher's real fingerprint code without starting a
        # persistent backend or touching the user's runtime descriptor.
        script = (
            "$ErrorActionPreference = 'Stop'; "
            + "$root = '" + str(ROOT).replace("'", "''") + "'; "
            + "$python = '" + sys.executable.replace("'", "''") + "'; "
            + source.replace("$python = Join-Path $env:LocalAppData 'Programs\\Python\\Python312\\python.exe'", "")
            + "\nWrite-Output $codeGeneration"
        )
        with tempfile.TemporaryDirectory() as other_directory:
            result = subprocess.run(
                [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-Command", script],
                cwd=other_directory, capture_output=True, text=True, timeout=15,
                env={**os.environ, "MOKU_CODE_GENERATION": "stale-inherited-value"},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), server.compute_code_generation(frozen=False))

    def test_closing_first_embedded_window_keeps_second_window_backend_alive(self):
        second_ready = threading.Event()
        close_second = threading.Event()
        urls: list[str] = []
        errors: list[Exception] = []
        worker = None
        original_web, original_downloads = server.WEB, server.DOWNLOADS
        original_instance, original_capability = server.INSTANCE_ID, server.DESKTOP_AUTH_TOKEN

        def second_process():
            try:
                moku_app.run([])
            except Exception as exc:
                errors.append(exc)
                second_ready.set()

        def desktop(url, _proxy, _capability):
            nonlocal worker
            urls.append(url)
            if len(urls) == 1:
                worker = threading.Thread(target=second_process)
                worker.start()
                self.assertTrue(second_ready.wait(5), "second desktop never opened")
            else:
                second_ready.set()
                close_second.wait(10)

        try:
            with tempfile.TemporaryDirectory() as temporary, patch.dict(
                os.environ, {"MOKU_NO_BROWSER": "0"}, clear=False,
            ), patch.object(moku_app, "runtime_resource_root", return_value=ROOT), patch.object(
                moku_app, "writable_data_root", return_value=Path(temporary),
            ), patch.object(moku_app, "runtime_directory", return_value=Path(temporary) / "runtime"), patch.object(
                moku_app, "configure_logging",
            ), patch.object(moku_app, "named_mutex", side_effect=lambda *_args: nullcontext()), patch.object(
                server, "refresh_network_opener",
            ), patch.object(moku_app, "launch_desktop", side_effect=desktop):
                try:
                    self.assertEqual(moku_app.run([]), 0)
                    self.assertFalse(errors, errors)
                    self.assertEqual(len(urls), 2)
                    # The first run has now shut down its owned backend. The
                    # other window must still have a usable real HTTP endpoint.
                    health = moku_app.read_json(urls[1] + "api/health", timeout=1)
                    self.assertTrue(health["ok"])
                finally:
                    close_second.set()
                    if worker is not None:
                        worker.join(timeout=5)
                        self.assertFalse(worker.is_alive())
        finally:
            server.WEB, server.DOWNLOADS = original_web, original_downloads
            server.INSTANCE_ID, server.DESKTOP_AUTH_TOKEN = original_instance, original_capability


if __name__ == "__main__":
    unittest.main()
