from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        body = b"<!doctype html><title>cookie lifecycle</title>ready"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if self.path == "/set":
            self.send_header(
                "Set-Cookie",
                "MOKU_LIFECYCLE=fictional-only; Path=/; HttpOnly; SameSite=Lax",
            )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def metadata(containers) -> list[dict]:
    rows = []
    for container in containers or []:
        for name, morsel in container.items():
            if str(name) != "MOKU_LIFECYCLE":
                continue
            rows.append({
                "name": str(name),
                "domain": str(morsel["domain"] or ""),
                "path": str(morsel["path"] or ""),
                "expires": str(morsel["expires"] or ""),
                "secure": bool(morsel["secure"]),
                "httpOnly": bool(morsel["httponly"]),
            })
    return rows


def main() -> None:
    output = Path(os.environ["MOKU_COOKIE_PROBE_RESULT"])
    profile = Path(tempfile.mkdtemp(prefix="moku-cookie-lifecycle-"))
    os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    main_window = webview.create_window("MOKU cookie lifecycle main", base + "/set")
    result = {
        "beforeChild": [],
        "afterChild": [],
        "afterExplicitClear": [],
        "ok": False,
        "error": "",
    }

    def probe(window):
        child = None
        try:
            if not window.events.loaded.wait(20):
                raise TimeoutError("main window did not load")
            result["beforeChild"] = metadata(window.get_cookies())
            child = webview.create_window("MOKU cookie lifecycle child", base + "/plain")
            if not child.events.loaded.wait(20):
                raise TimeoutError("child window did not load")
            result["afterChild"] = metadata(child.get_cookies())
            window.clear_cookies()
            result["afterExplicitClear"] = metadata(child.get_cookies())
            result["ok"] = (
                len(result["beforeChild"]) == 1
                and len(result["afterChild"]) == 1
                and not result["afterExplicitClear"]
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            output.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
            if child is not None and not child.events.closed.is_set():
                child.destroy()
            window.destroy()

    try:
        webview.start(
            probe,
            args=[main_window],
            gui="edgechromium",
            private_mode=False,
            storage_path=str(profile),
            debug=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        shutil.rmtree(profile, ignore_errors=True)
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
