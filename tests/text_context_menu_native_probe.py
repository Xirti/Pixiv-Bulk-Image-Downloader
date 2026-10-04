"""Actual desktop JS bridge + UI-thread clipboard path, with fixture clipboard.

The test window is hidden. Its DOM context event is synthetic; real right clicks
are covered by text_context_menu_browser_probe.py. No OS clipboard is changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

os.environ["PYTHONNET_RUNTIME"] = "netfx"
os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop_client

HTML = b'''<!doctype html><meta charset="utf-8"><title>MOKU text-menu test</title>
<link rel="stylesheet" href="/style.css"><input id="input" value="cat fox">
<input id="readonly" readonly value="readonly"><p id="text">plain text</p>
<script src="/text-context-menu.js"></script>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        asset = self.path.split("?", 1)[0]
        body = (ROOT / "web" / asset.lstrip("/")).read_bytes() if asset in {"/style.css", "/text-context-menu.js"} else HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if body == HTML else "text/css" if asset == "/style.css" else "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    result = {"ok":False,"error":""}
    create = desktop_client.webview.create_window

    def hidden_window(*args, **kwargs):
        return create(*args, **kwargs, hidden=True, focus=False)

    def probe(window):
        try:
            assert window.events.loaded.wait(20), "desktop did not load"
            from System import Func, Object
            from System.Threading import Thread
            control = window.native.webview
            settings = control.Invoke(Func[Object](lambda: [bool(control.CoreWebView2.Settings.AreDefaultContextMenusEnabled), bool(control.CoreWebView2.Settings.AreDevToolsEnabled)]))
            assert settings == [False, False], settings
            assert window.evaluate_js("getComputedStyle(document.body).userSelect") != "none"
            clipboard = {"text":"paste fixture", "sta":False}

            class FixtureClipboard:
                @staticmethod
                def GetText():
                    clipboard["sta"] = str(Thread.CurrentThread.GetApartmentState()) == "STA"
                    return clipboard["text"]

                @staticmethod
                def SetText(text):
                    clipboard["sta"] = str(Thread.CurrentThread.GetApartmentState()) == "STA"
                    clipboard["text"] = text

            def wait_js(script):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if window.evaluate_js(script):
                        return
                    time.sleep(0.05)
                raise AssertionError(script)

            # Patch only the external clipboard provider, not our native Invoke,
            # origin guard, public bridge, menu or editor code.
            with patch.dict(sys.modules, {"System.Windows.Forms":SimpleNamespace(Clipboard=FixtureClipboard)}):
                window.evaluate_js("document.querySelector('#input').focus(); document.querySelector('#input').setSelectionRange(0,3); document.querySelector('#input').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:30,clientY:30}))")
                assert window.evaluate_js("!document.querySelector('#textContextMenu').hidden")
                window.evaluate_js("document.querySelector('[data-text-command=cut]').click()")
                wait_js("document.querySelector('#input').value === ' fox'")
                assert clipboard["text"] == "cat" and clipboard["sta"]
                window.evaluate_js("document.querySelector('#input').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true})); document.querySelector('[data-text-command=paste]').click()")
                wait_js("document.querySelector('#input').value === 'cat fox'")
                assert clipboard["sta"]
            window.load_url(f"http://127.0.0.1:{httpd.server_port}/?reload=1")
            assert window.events.loaded.wait(20)
            window.evaluate_js("document.querySelector('#readonly').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}))")
            assert window.evaluate_js("!document.querySelector('#textContextMenu').hidden && document.querySelector('[data-text-command=cut]').disabled && document.querySelector('[data-text-command=paste]').disabled")
            result.update(ok=True, desktopBridgeCutPaste=True, clipboardSTA=True, readonlyAfterReload=True, syntheticContextEvent=True, osClipboardUntouched=True)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            window.destroy()

    try:
        with tempfile.TemporaryDirectory(prefix="moku-text-native-", ignore_cleanup_errors=True) as directory, patch.object(desktop_client.webview, "create_window", side_effect=hidden_window):
            desktop_client.start_desktop(f"http://127.0.0.1:{httpd.server_port}/", Path(directory), startup=probe)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    print(json.dumps(result, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
