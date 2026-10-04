"""Verify real WebView2 text-menu configuration in a hidden desktop window.

No clipboard commands are executed; menu state is checked without reading or
changing the user's clipboard. No external requests or account login are used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

os.environ["PYTHONNET_RUNTIME"] = "netfx"
os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import desktop_client


HTML = """<!doctype html><meta charset="utf-8"><title>MOKU text menu test</title>
<input id="input" value="猫耳 fox"><textarea id="textarea">多行文字</textarea>
<input id="readonly" readonly value="只读文字"><p id="text">普通文字，可以选择复制。</p>
<style>body{padding:40px}input,textarea{display:block;width:300px;margin:12px}p{margin:30px}</style>
""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"ok": False, "cases": [], "error": ""}
    create_window = desktop_client.webview.create_window

    def hidden_window(*args, **kwargs):
        return create_window(*args, **kwargs, hidden=True, focus=False)

    def probe(window):
        try:
            if not window.events.loaded.wait(20):
                raise TimeoutError("desktop page did not load")
            from System import Func, Object
            from System.Collections.Generic import List
            from Microsoft.Web.WebView2.Core import CoreWebView2ContextMenuItem, CoreWebView2ContextMenuItemKind

            control = window.native.webview

            def native_call(callback):
                return control.Invoke(Func[Object](lambda: callback(control.CoreWebView2)))

            settings = native_call(lambda core: [bool(core.Settings.AreDefaultContextMenusEnabled), bool(core.Settings.AreDevToolsEnabled)])
            assert settings == [True, False], ("native text menu / developer tools settings", settings)
            assert window.evaluate_js("getComputedStyle(document.body).userSelect") != "none"

            def check_native_menu_collection(core):
                items = List[CoreWebView2ContextMenuItem]()
                items.Add(core.Environment.CreateContextMenuItem("test-navigation", None, CoreWebView2ContextMenuItemKind.Command))
                args = SimpleNamespace(MenuItems=items, Handled=False)
                desktop_client._filter_text_context_menu(None, args)
                return args.Handled and not len(items)

            assert native_call(check_native_menu_collection), "native .NET menu filtering failed"
            result["nativeMenuCollection"] = True
            for selector in ("#input", "#textarea", "#readonly", "#text"):
                selection = window.evaluate_js("""(() => {
                    const node = document.querySelector(%s);
                    if (node.matches('input,textarea')) {
                        node.focus(); node.setSelectionRange(0, 2);
                        return node.value.slice(node.selectionStart, node.selectionEnd);
                    } else {
                        const selection = getSelection(); selection.removeAllRanges();
                        const range = document.createRange(); range.selectNodeContents(node); selection.addRange(range);
                        return selection.toString();
                    }
                })()""" % json.dumps(selector))
                assert selection, selector
                result["cases"].append({"target": selector, "selectedText": selection})
            window.load_url(f"http://127.0.0.1:{server.server_port}/?reload=1")
            assert window.events.loaded.wait(20), "desktop page did not reload"
            assert native_call(lambda core: bool(core.Settings.AreDefaultContextMenusEnabled))
            assert window.evaluate_js("getComputedStyle(document.body).userSelect") != "none"
            result["menusEnabled"] = settings[0]
            result["devToolsEnabled"] = settings[1]
            result["reload"] = True
            result["ok"] = True
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            window.destroy()

    try:
        with tempfile.TemporaryDirectory(prefix="moku-text-menu-", ignore_cleanup_errors=True) as directory, patch.object(
            desktop_client.webview, "create_window", side_effect=hidden_window
        ):
            desktop_client.start_desktop(f"http://127.0.0.1:{server.server_port}/", Path(directory), startup=probe)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(json.dumps(result, ensure_ascii=False))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
