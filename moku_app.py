from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

import server


MUTEX_NAME = r"Local\MOKU.PixivTagGallery.Backend.v1"
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x80
LOG = logging.getLogger("moku.app")


def runtime_resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def writable_data_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_server_paths(resource_root: Path, data_root: Path) -> None:
    server.WEB = Path(resource_root) / "web"
    server.DOWNLOADS = Path(data_root) / "downloads"
    server.DOWNLOADS.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def named_mutex(name: str, timeout_seconds: float = 30.0):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel32.WaitForSingleObject(handle, max(0, int(timeout_seconds * 1000)))
        if result not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
            raise TimeoutError("MOKU backend launch lock timeout")
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_json(url: str, timeout: float = 3.0) -> dict:
    with _local_opener().open(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        data = json.loads(response.read(1024 * 1024))
    if not isinstance(data, dict):
        raise ValueError("invalid JSON response")
    return data


def load_runtime(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def healthy_runtime(runtime: dict | None) -> str | None:
    if not runtime:
        return None
    try:
        port = int(runtime["port"])
        instance_id = str(runtime["instanceId"])
        protocol = int(runtime["protocolVersion"])
        application_id = str(runtime["applicationId"])
        code_generation = str(runtime["codeGeneration"])
    except (KeyError, TypeError, ValueError):
        return None
    if protocol != server.PROTOCOL_VERSION or application_id != server.APPLICATION_ID or code_generation != server.CODE_GENERATION or not 1 <= port <= 65535 or not instance_id:
        return None
    url = f"http://127.0.0.1:{port}/"
    try:
        health = read_json(url + "api/health")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        return None
    if health.get("protocolVersion") != protocol or health.get("applicationId") != application_id or health.get("codeGeneration") != code_generation or health.get("instanceId") != instance_id:
        return None
    return url


def wait_ready(url: str, instance_id: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            health = read_json(url + "api/health")
            if health.get("instanceId") != instance_id:
                raise RuntimeError("backend instance mismatch")
            for path in ("", "style.css", "app.js"):
                with _local_opener().open(url + path, timeout=3) as response:
                    if response.status != 200:
                        raise RuntimeError(f"asset {path or '/'} returned HTTP {response.status}")
            return
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"MOKU backend did not become ready: {last_error}")


def write_runtime(path: Path, port: int, instance_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocolVersion": server.PROTOCOL_VERSION,
        "applicationId": server.APPLICATION_ID,
        "codeGeneration": server.CODE_GENERATION,
        "instanceId": instance_id,
        "pid": os.getpid(),
        "port": port,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    os.replace(temporary, path)


def configure_logging(data_root: Path) -> None:
    log_dir = Path(data_root) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "moku-app.log").resolve()
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            try:
                if Path(handler.baseFilename).resolve() == target:
                    root_logger.setLevel(logging.INFO)
                    return
            except (AttributeError, OSError):
                pass
        if isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(
        target,
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)


def show_fatal_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "MOKU", 0x10)
    except Exception:
        pass


def runtime_directory() -> Path:
    override = os.environ.get("MOKU_RUNTIME_DIR")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "MOKU" / "runtime"


def _start_webview(url: str, storage_path: Path, proxy: str) -> None:
    from desktop_client import start_desktop

    inherited = os.environ.get("WEBVIEW2_USER_DATA_FOLDER")
    try:
        os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
        start_desktop(url, storage_path, proxy)
    finally:
        if inherited is not None:
            os.environ["WEBVIEW2_USER_DATA_FOLDER"] = inherited


def remove_webview_profile(path: Path) -> bool:
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.25)
    LOG.warning("temporary WebView2 profile cleanup deferred")
    return False


def cleanup_stale_webview_profiles(root: Path, *, now: float | None = None) -> int:
    current = time.time() if now is None else float(now)
    removed = 0
    try:
        candidates = list(root.iterdir())
    except OSError:
        return 0
    for candidate in candidates:
        if not candidate.is_dir() or not candidate.name.startswith("session-"):
            continue
        try:
            age = current - candidate.stat().st_mtime
        except OSError:
            continue
        if age < 24 * 60 * 60:
            continue
        if remove_webview_profile(candidate):
            removed += 1
    return removed


def launch_desktop(url: str, proxy: str = "") -> None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    session_root = Path(local_app_data) / "MOKU" / "WebView2Sessions"
    session_root.mkdir(parents=True, exist_ok=True)
    cleanup_stale_webview_profiles(session_root)
    storage_path = Path(tempfile.mkdtemp(prefix="session-", dir=str(session_root)))
    try:
        _start_webview(url, storage_path, proxy)
    finally:
        remove_webview_profile(storage_path)


def _run_backend_for_test() -> str:
    raise RuntimeError("test backend hook was not configured")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve-only", action="store_true")
    parser.add_argument("--desktop-host-test", action="store_true")
    options, _unknown = parser.parse_known_args(argv)
    if options.desktop_host_test:
        launch_desktop(_run_backend_for_test())
        return 0

    resource_root = runtime_resource_root()
    data_root = writable_data_root()
    configure_logging(data_root)
    configure_server_paths(resource_root, data_root)
    server.refresh_network_opener()

    runtime_dir = runtime_directory()
    runtime_file = runtime_dir / "backend.json"
    mutex_name = os.environ.get("MOKU_MUTEX_NAME") or MUTEX_NAME
    httpd = None
    thread = None
    owns_backend = False

    with named_mutex(mutex_name):
        url = healthy_runtime(load_runtime(runtime_file))
        if url:
            LOG.info("reuse url=%s", url)
        else:
            server.INSTANCE_ID = secrets.token_hex(16)
            httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            port = int(httpd.server_port)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{port}/"
            try:
                wait_ready(url, server.INSTANCE_ID)
                write_runtime(runtime_file, port, server.INSTANCE_ID)
            except Exception:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                raise
            owns_backend = True
            LOG.info("start pid=%s url=%s instance=%s", os.getpid(), url, server.INSTANCE_ID)

    no_browser = options.serve_only or os.environ.get("MOKU_NO_BROWSER") == "1"
    exit_after = float(os.environ.get("MOKU_TEST_EXIT_AFTER_SECONDS") or 0)
    try:
        if no_browser:
            if not owns_backend:
                return 0
            if exit_after > 0:
                time.sleep(exit_after)
            else:
                while thread and thread.is_alive():
                    thread.join(timeout=3600)
        else:
            launch_desktop(url, server.PIXIV_PROXY)
    except KeyboardInterrupt:
        pass
    finally:
        if owns_backend and httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if owns_backend and thread is not None:
            thread.join(timeout=5)
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except Exception as exc:
        try:
            configure_logging(writable_data_root())
            LOG.exception("fatal startup error")
        except Exception:
            pass
        show_fatal_error(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
