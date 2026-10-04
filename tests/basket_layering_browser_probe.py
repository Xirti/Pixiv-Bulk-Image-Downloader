"""Offline regression: scrolled basket controls must never cover its toolbar."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from unittest.mock import patch

os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from library_browser_probe import artwork
from playwright.sync_api import sync_playwright


class ProbeHandler(server.Handler):
    def _get_pixiv_search(self, _request):
        rows = [{**artwork(), "id": str(100 + number), "title": f"作品 {number}"} for number in range(24)]
        return self.send_json({"items": rows, "page": 1, "availablePages": [1], "total": len(rows), "perPage": 36, "hasMore": False})


def main():
    errors, checks, downloads = [], [], []
    httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        with patch.object(server, "ensure_network_opener_current"), patch.object(server, "validated_authorization", return_value=(False, None)), sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 820}, reduced_motion="reduce")
                page.set_default_timeout(5000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: downloads.append(request.url) if request.url.split("?")[0].endswith(("/api/pixiv/download", "/api/pixiv/batch-download")) else None)
                page.route("**/api/pixiv/image?*", lambda route: route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4" fill="#ccc"/></svg>'))
                page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                page.locator("#searchSubmit").click()
                page.wait_for_function("() => !searchPending && items.length === 24")
                assert page.locator("#navBasket").get_attribute("aria-disabled") == "false"
                page.locator("#selectAllPage").click()
                page.locator("#navBasket").click()
                for width in (1280, 900, 375, 320):
                    page.set_viewport_size({"width": width, "height": 820})
                    for theme in ("dark", "light"):
                        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
                        result = page.evaluate("""() => {
                            const basket = document.querySelector('#basketPage');
                            const bar = document.querySelector('.basket-bar');
                            for (const selector of ['.batch-card-select', '.batch-page-count']) {
                                const control = document.querySelector(selector);
                                basket.scrollTop = 0;
                                const head = bar.getBoundingClientRect(), before = control.getBoundingClientRect();
                                basket.scrollTop = before.top + before.height / 2 - (head.bottom - 12);
                                const box = control.getBoundingClientRect();
                                const x = box.x + box.width / 2, y = box.y + box.height / 2;
                                if (!(y >= head.top && y < head.bottom)) throw new Error('probe failed to overlap toolbar');
                                const top = document.elementFromPoint(x, y);
                                if (!top?.closest('.basket-bar')) throw new Error(`${selector} covers toolbar: ${top?.outerHTML.slice(0, 180)}`);
                            }
                            for (const id of ['basketClear', 'basketDownload']) {
                                const button = document.getElementById(id), rect = button.getBoundingClientRect();
                                const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
                                if (!button.contains(hit)) throw new Error(`${id} is obstructed by ${hit?.outerHTML.slice(0, 100)}`);
                            }
                            return {width: innerWidth, theme: document.documentElement.dataset.theme, scroll: basket.scrollTop};
                        }""")
                        checks.append(result)
                        page.locator("#basketClear").click()
                        assert page.locator("#basketClearDialog").is_visible()
                        page.locator("#basketClearCancel").click()
                        assert page.evaluate("selection.pageCount") == 72
                page.locator("#basketDownload").click()
                assert page.locator("#downloadPage").is_visible()
                assert page.evaluate("selection.pageCount") == 72
                assert not downloads, downloads
                assert not errors, errors
                print(json.dumps({"ok": True, "toolbarHitTests": checks, "scriptErrors": errors}, ensure_ascii=False))
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
