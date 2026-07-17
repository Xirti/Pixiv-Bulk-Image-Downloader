from __future__ import annotations

import os
import sys
import tempfile
import unittest
import logging
from logging.handlers import RotatingFileHandler
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import server

ROOT = Path(__file__).resolve().parents[1]


class PackagedAppTests(unittest.TestCase):
    def test_logging_is_bounded_and_profile_cleanup_log_omits_absolute_path(self):
        import moku_app

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logging.shutdown()
            logger = logging.getLogger()
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            moku_app.configure_logging(root)
            try:
                rotating = [
                    handler for handler in logger.handlers
                    if isinstance(handler, RotatingFileHandler)
                ]
                self.assertEqual(len(rotating), 1)
                self.assertEqual(rotating[0].maxBytes, 5 * 1024 * 1024)
                self.assertEqual(rotating[0].backupCount, 2)

                secret = root / "private-user-path" / "session-1"
                secret.mkdir(parents=True)
                with patch.object(moku_app.shutil, "rmtree"), patch.object(
                    moku_app.time, "sleep"
                ), self.assertLogs("moku.app", level="WARNING") as captured:
                    self.assertFalse(moku_app.remove_webview_profile(secret))
                self.assertNotIn(str(secret), "\n".join(captured.output))
            finally:
                logging.shutdown()
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()

    def test_runtime_resource_root_prefers_pyinstaller_bundle(self):
        import moku_app

        with tempfile.TemporaryDirectory() as raw_root, patch.object(sys, "_MEIPASS", raw_root, create=True):
            self.assertEqual(moku_app.runtime_resource_root(), Path(raw_root))

    def test_configure_server_paths_separates_resources_and_writable_data(self):
        import moku_app

        old_web, old_downloads = server.WEB, server.DOWNLOADS
        try:
            with tempfile.TemporaryDirectory() as raw_root:
                root = Path(raw_root)
                resource_root = root / "bundle"
                data_root = root / "data"
                (resource_root / "web").mkdir(parents=True)
                moku_app.configure_server_paths(resource_root, data_root)
                self.assertEqual(server.WEB, resource_root / "web")
                self.assertEqual(server.DOWNLOADS, data_root / "downloads")
                self.assertTrue(server.DOWNLOADS.is_dir())
        finally:
            server.WEB, server.DOWNLOADS = old_web, old_downloads


    def test_embedded_backend_initializes_network_before_binding_http_server(self):
        import moku_app

        events = []
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            runtime = root / "runtime"
            runtime.mkdir()
            with patch.object(moku_app, "runtime_resource_root", return_value=root), \
                 patch.object(moku_app, "writable_data_root", return_value=root), \
                 patch.object(moku_app, "configure_logging"), \
                 patch.object(moku_app, "configure_server_paths"), \
                 patch.object(moku_app, "runtime_directory", return_value=runtime), \
                 patch.object(moku_app, "named_mutex", return_value=nullcontext()), \
                 patch.object(moku_app, "load_runtime", return_value=None), \
                 patch.object(moku_app, "healthy_runtime", return_value=""), \
                 patch.object(moku_app.server, "refresh_network_opener", side_effect=lambda: events.append("network") or ""), \
                 patch.object(moku_app.server, "LocalThreadingHTTPServer", side_effect=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bind-stop")) if not events.append("server") else None):
                with self.assertRaisesRegex(RuntimeError, "bind-stop"):
                    moku_app.run(["--serve-only"])
        self.assertEqual(events, ["network", "server"])

    def test_default_product_host_is_desktop_webview_without_external_edge_launcher(self):
        import moku_app

        with patch.object(moku_app, "launch_desktop") as desktop, patch.object(
            moku_app, "_run_backend_for_test", return_value="http://127.0.0.1:45678/"
        ):
            moku_app.run(["--desktop-host-test"])
        desktop.assert_called_once_with("http://127.0.0.1:45678/")
        source = Path(moku_app.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--app=", source)
        self.assertNotIn("def find_edge", source)

    def test_desktop_host_clears_inherited_webview_profile_override(self):
        import moku_app

        import desktop_client

        observed = []
        def start(*_args):
            observed.append(moku_app.os.environ.get("WEBVIEW2_USER_DATA_FOLDER"))

        with patch.dict(moku_app.os.environ, {"WEBVIEW2_USER_DATA_FOLDER": "foreign-profile"}, clear=False), patch.object(
            desktop_client, "start_desktop", side_effect=start
        ):
            moku_app.launch_desktop("http://127.0.0.1:45678/")
            self.assertEqual(moku_app.os.environ.get("WEBVIEW2_USER_DATA_FOLDER"), "foreign-profile")
        self.assertEqual(observed, [None])

    def test_desktop_host_forces_netfx_for_pywebview_and_restores_environment(self):
        import moku_app

        import desktop_client

        observed = []
        with patch.dict(moku_app.os.environ, {"PYTHONNET_RUNTIME": "coreclr"}, clear=False), patch.object(
            desktop_client, "start_desktop",
            side_effect=lambda *_args: observed.append(moku_app.os.environ.get("PYTHONNET_RUNTIME")),
        ):
            moku_app._start_webview(
                "http://127.0.0.1:45678/", Path("C:/tmp/moku-profile"), ""
            )
            self.assertEqual(moku_app.os.environ.get("PYTHONNET_RUNTIME"), "coreclr")
        self.assertEqual(observed, ["netfx"])

    def test_desktop_host_uses_disposable_profile_and_removes_it_after_close(self):
        import moku_app

        disposable = Path("C:/tmp/moku-webview-session")
        with tempfile.TemporaryDirectory() as raw_local:
            session_root = Path(raw_local) / "MOKU" / "WebView2Sessions"
            with patch.dict(moku_app.os.environ, {"LOCALAPPDATA": raw_local}, clear=False), patch.object(
                moku_app.tempfile, "mkdtemp", return_value=str(disposable)
            ) as make_profile, patch.object(moku_app, "_start_webview") as start, patch.object(
                moku_app, "remove_webview_profile"
            ) as remove:
                moku_app.launch_desktop("http://127.0.0.1:45678/", "http://127.0.0.1:7890")

        make_profile.assert_called_once_with(prefix="session-", dir=str(session_root))
        self.assertEqual(start.call_args.args[:3], (
            "http://127.0.0.1:45678/", disposable, "http://127.0.0.1:7890"
        ))
        remove.assert_called_once_with(disposable)

    def test_stale_profile_cleanup_only_removes_old_session_directories(self):
        import moku_app

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            old_session = root / "session-old"
            fresh_session = root / "session-fresh"
            unrelated = root / "important-user-folder"
            for path in (old_session, fresh_session, unrelated):
                path.mkdir()
            old_time = 1_600_000_000
            fresh_time = old_time + 90_000
            os.utime(old_session, (old_time, old_time))
            os.utime(fresh_session, (fresh_time, fresh_time))
            os.utime(unrelated, (old_time, old_time))

            removed = moku_app.cleanup_stale_webview_profiles(root, now=fresh_time)

            self.assertEqual(removed, 1)
            self.assertFalse(old_session.exists())
            self.assertTrue(fresh_session.exists())
            self.assertTrue(unrelated.exists())

    def test_spec_and_build_include_pywebview_desktop_host(self):
        spec = (ROOT / "MOKU.spec").read_text(encoding="utf-8-sig")
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8-sig")
        self.assertIn("moku_app.py", spec)
        self.assertIn("webview", spec.lower())
        self.assertIn("pywebview==6.2.1", lock.lower())
        self.assertIn("from search_service import", (ROOT / "server.py").read_text(encoding="utf-8"))
        build = (ROOT / "build-portable.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$env:MOKU_CODE_GENERATION = $null", build)
        self.assertIn("$env:MOKU_ENABLE_TEST_FIXTURES = $null", build)
        self.assertIn("^exe-sha256:[0-9a-f]{64}$", build)

    def test_spec_excludes_non_windows_x64_backends_and_debug_artifacts(self):
        spec = (ROOT / "MOKU.spec").read_text(encoding="utf-8-sig")
        for marker in (
            "pywebview-android.jar",
            "platforms/android/",
            "platforms/cef.py",
            "platforms/cocoa.py",
            "platforms/gtk.py",
            "platforms/mshtml.py",
            "platforms/qt.py",
            "webbrowserinterop.x86.dll",
            "ffi/dlls/x86/",
            ".pdb",
        ):
            self.assertIn(marker, spec)
        self.assertIn("PYWEBVIEW_REQUIRED_LOADER_RUNTIMES", spec)
        for runtime in (
            "runtimes/win-arm64/",
            "runtimes/win-x64/",
            "runtimes/win-x86/",
        ):
            self.assertIn(runtime, spec)
        excluded_block = spec.split("WINDOWS_X64_EXCLUDED_PATHS = (", 1)[1].split(")", 1)[0]
        self.assertNotIn("runtimes/win-arm64/", excluded_block)
        self.assertNotIn("runtimes/win-x86/", excluded_block)
        self.assertIn("filter_windows_x64", spec)
        self.assertIn("a.binaries = filter_windows_x64(a.binaries)", spec)
        self.assertIn("a.datas = filter_windows_x64(a.datas)", spec)
        self.assertIn("a.pure = filter_windows_x64(a.pure)", spec)
        self.assertLess(spec.index("a = Analysis("), spec.index("a.binaries = filter_windows_x64"))
        self.assertLess(spec.index("a.binaries = filter_windows_x64"), spec.index("pyz = PYZ"))
        self.assertIn("edgechromium", spec)
        self.assertIn("winforms", spec)


if __name__ == "__main__":
    unittest.main()
