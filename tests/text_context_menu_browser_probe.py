"""Real right-click popup and text-edit tests on the application, offline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"

from playwright.sync_api import sync_playwright
import server
from download_history import DownloadHistory


BRIDGE = """globalThis.clipboardProbe = {text:'粘贴文字',reads:0,writes:0,fail:false};
globalThis.pywebview = {api:{
 read_clipboard:async()=>{clipboardProbe.reads++;return {ok:true,text:clipboardProbe.text}},
 write_clipboard:async text=>{clipboardProbe.writes++;if(clipboardProbe.fail)return {ok:false};clipboardProbe.text=text;return {ok:true}}
}};"""


def main():
    errors = []
    with tempfile.TemporaryDirectory(prefix="moku-text-popup-") as directory, patch.object(
        server, "DOWNLOAD_HISTORY", DownloadHistory(Path(directory) / "history.sqlite3")
    ), patch.object(server, "validated_authorization", return_value=(False, None)):
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 820})
                    page.set_default_timeout(3000)
                    page.add_init_script(BRIDGE)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                    menu = page.locator("#textContextMenu")

                    def open_menu(selector):
                        page.locator(selector).click(button="right", position={"x":12,"y":12})
                        assert menu.is_visible(), "right-clicking the search field did not display a text menu"

                    def action(command):
                        page.locator(f'[data-text-command="{command}"]').click()

                    def select(start, end):
                        page.locator("#tag").evaluate("(node, bounds) => { node.focus(); node.setSelectionRange(...bounds); }", [start, end])

                    open_menu("#tag")
                    assert page.evaluate("clipboardProbe.reads === 0 && clipboardProbe.writes === 0")
                    action("selectAll")
                    assert page.locator("#tag").evaluate("node => node.selectionStart === 0 && node.selectionEnd === node.value.length")
                    open_menu("#tag")
                    action("copy")
                    page.wait_for_function("() => clipboardProbe.text === '猫耳'")
                    page.locator("#tag").fill("猫耳 fox")
                    select(0, 2)
                    open_menu("#tag")
                    action("cut")
                    page.wait_for_function("() => document.querySelector('#tag').value === ' fox'")
                    page.keyboard.press("Control+z")
                    assert page.locator("#tag").input_value() == "猫耳 fox", "cut lost native undo"
                    page.evaluate("clipboardProbe.text = '插画'")
                    select(0, 2)
                    open_menu("#tag")
                    action("paste")
                    page.wait_for_function("() => document.querySelector('#tag').value === '插画 fox'")
                    page.keyboard.press("Control+z")
                    assert page.locator("#tag").input_value() == "猫耳 fox", "paste lost native undo"
                    page.evaluate("clipboardProbe.fail = true")
                    select(0, 2)
                    open_menu("#tag")
                    action("cut")
                    page.wait_for_function("() => !document.querySelector('#textMenuNotice').hidden")
                    assert page.locator("#tag").input_value() == "猫耳 fox", "failed copy still deleted the selected text"
                    page.evaluate("clipboardProbe.fail = false; document.querySelector('#tag').readOnly = true")
                    open_menu("#tag")
                    assert page.locator('[data-text-command="cut"]').is_disabled()
                    assert page.locator('[data-text-command="paste"]').is_disabled()
                    page.keyboard.press("Escape")
                    page.evaluate("document.querySelector('#tag').readOnly = false")
                    open_menu(".search-help")
                    action("copy")
                    page.wait_for_function("() => clipboardProbe.text.includes('多个标签')")
                    page.locator("#helpBtn").click()
                    open_menu("#helpDialog h2")
                    assert menu.evaluate("node => node.parentElement.id") == "helpDialog"
                    assert menu.locator('[data-text-command="copy"]').evaluate("node => { const r=node.getBoundingClientRect(); return node.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }")
                    page.keyboard.press("Escape")
                    assert page.locator("#helpDialog").evaluate("node => node.open"), "Escape also closed the underlying dialog"
                    page.locator("#helpDialog .dialog-close").click()
                    page.locator("#datePreset").select_option("custom")
                    page.locator("#startDate").fill("2026-10-01")
                    page.evaluate("clipboardProbe.text = 'not a date'")
                    open_menu("#startDate")
                    action("paste")
                    page.wait_for_function("() => !document.querySelector('#textMenuNotice').hidden")
                    assert page.locator("#startDate").input_value() == "2026-10-01"
                    page.evaluate("clipboardProbe.text = '2026-10-02'")
                    open_menu("#startDate")
                    action("paste")
                    page.wait_for_function("() => document.querySelector('#startDate').value === '2026-10-02'")
                    page.evaluate("clipboardProbe.text = '2000-01-01'")
                    open_menu("#startDate")
                    action("paste")
                    page.wait_for_timeout(80)
                    assert page.locator("#startDate").input_value() == "2026-10-02", "paste bypassed the date minimum"
                    page.locator("#endDate").fill("2026-10-01")
                    assert page.locator("#endDate").evaluate("node => node.validity.customError")
                    page.evaluate("clipboardProbe.text = '2026-10-03'")
                    open_menu("#endDate")
                    action("paste")
                    page.wait_for_function("() => document.querySelector('#endDate').value === '2026-10-03'")
                    assert page.locator("#endDate").evaluate("node => node.validity.valid"), "pasting a valid date did not clear the previous date-order error"
                    page.locator("#navHistory").click()
                    page.evaluate("clipboardProbe.text = 'x'.repeat(200)")
                    open_menu("#historyQuery")
                    action("paste")
                    page.wait_for_function("() => document.querySelector('#historyQuery').value.length === 120")
                    page.locator("#historyQuery").focus()
                    page.keyboard.press("Shift+F10")
                    assert menu.is_visible(), "keyboard context menu did not open"
                    page.keyboard.press("ArrowDown")
                    page.keyboard.press("Escape")
                    assert page.locator("#historyPage").is_visible(), "closing the menu exited history"
                    for event in ("scroll", "resize"):
                        page.locator("#historyQuery").fill("abcdef")
                        page.locator("#historyQuery").evaluate("node => { node.focus(); node.setSelectionRange(0, 2); }")
                        open_menu("#historyQuery")
                        page.evaluate("event => window.dispatchEvent(new Event(event))", event)
                        assert not menu.is_visible(), f"{event} did not dismiss the menu"
                        assert page.locator("#historyQuery").evaluate("node => document.activeElement === node && node.selectionStart === 0 && node.selectionEnd === 2"), f"{event} dismissal lost focus or selection"
                        page.keyboard.type("X")
                        assert page.locator("#historyQuery").input_value() == "Xcdef", f"typing after {event} dismissal did not reach the field"
                    for width in (1280, 375, 320):
                        page.set_viewport_size({"width":width,"height":820})
                        for theme in ("dark", "light"):
                            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                            open_menu("#historyQuery")
                            box = menu.bounding_box()
                            assert box["x"] >= 0 and box["x"] + box["width"] <= width and box["y"] + box["height"] <= 820
                            assert menu.evaluate("node => getComputedStyle(node).color !== getComputedStyle(node).backgroundColor")
                            page.keyboard.press("Escape")
                    page.locator("#historyQuery").fill("原文字")
                    page.evaluate("() => { pywebview.api.read_clipboard = () => new Promise(resolve => { globalThis.releasePaste = () => resolve({ok:true,text:'旧操作'}); }); }")
                    open_menu("#historyQuery")
                    action("paste")
                    page.locator("#historyQuery").fill("新文字")
                    page.evaluate("releasePaste()")
                    page.wait_for_timeout(80)
                    assert page.locator("#historyQuery").input_value() == "新文字", "stale paste overwrote a new edit"
                    page.locator("#historyQuery").fill("abcd")
                    page.locator("#historyQuery").evaluate("node => { node.focus(); node.setSelectionRange(0, 2); }")
                    open_menu("#historyQuery")
                    action("paste")
                    page.keyboard.press("End")
                    assert page.locator("#historyQuery").evaluate("node => node.selectionStart === 4 && node.selectionEnd === 4")
                    page.evaluate("releasePaste()")
                    page.wait_for_timeout(80)
                    assert page.locator("#historyQuery").input_value() == "abcd", "pending paste overwrote the old selection after the caret moved"
                    assert not errors, errors
                    print(json.dumps({"ok": True, "popupVisible": True, "cutCopyPasteSelectAll":True,"undo":True,"readonly":True,"modal":True,"date":True,"dateMinimum":True,"maxLength":True,"stalePaste":True,"caretMovedPaste":True,"dismissalFocus":True,"themeViewports":6,"scriptErrors": errors}, ensure_ascii=False))
                finally:
                    browser.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
