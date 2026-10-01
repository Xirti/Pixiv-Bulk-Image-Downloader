"""Offline Edge smoke check of the real page, with no account or download writes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from playwright.sync_api import sync_playwright


class ProbeHandler(server.Handler):
    requests = []
    detail_revision = 0
    fail_once = True

    @classmethod
    def artwork(cls):
        token = f"revision-{cls.detail_revision}"
        return {
            "id": "50", "pages": 401, "source": "pixiv", "restriction": "safe",
            "title": "Offline 401-page artwork", "artist": "Probe", "tags": ["probe"],
            "description": "offline fixture", "width": 10, "height": 10,
            "date": "2026-09-30", "bookmarks": 0,
            "thumb": f"/api/pixiv/image?token={token}-0",
            "pageImages": [{
                "regular": f"/api/pixiv/image?token={token}-{page}",
                "original": f"/api/pixiv/image?token={token}-original-{page}",
            } for page in range(401)],
            "qualities": [
                {"id": "regular", "label": "Regular", "width": 10, "height": 10},
                {"id": "original", "label": "Original", "width": 10, "height": 10},
            ],
            "formats": [{"id": "source", "label": "Source"}],
        }

    def _get_pixiv_search(self, _request):
        item = self.artwork()
        item.pop("pageImages")
        return self.send_json({
            "items": [item], "page": 1, "availablePages": [1], "preloadedThrough": 1,
            "hasMore": False, "total": 1, "perPage": 36, "label": "Offline probe",
        })

    def _get_pixiv_detail(self, _artwork_id):
        type(self).detail_revision += 1
        return self.send_json(self.artwork())

    def _post_pixiv_download(self, data):
        pages = data["pages"]
        type(self).requests.append(pages)
        if pages[0] == 200 and type(self).fail_once:
            type(self).fail_once = False
            return {"error": "offline interruption"}, 502
        return {"pages": len(pages), "saved": ["offline.jpg"]}, 200


def wait_for(page, expression):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(50)
    raise TimeoutError(expression)


def check_theme(page, theme):
    if page.evaluate("document.documentElement.dataset.theme") != theme:
        page.locator("#themeToggle").click()
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    return page.evaluate(r"""(() => {
        const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
        const lum = color => rgb(color).slice(0,3).map(x => {
            x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4;
        }).reduce((sum, x, i) => sum + x * [.2126,.7152,.0722][i], 0);
        const failures = [];
        let minimum = 100, checked = 0;
        for (const node of document.querySelectorAll("body *")) {
            if (!node.getClientRects().length || node.disabled || (!node.matches("input:not([type=checkbox]),select,textarea") && ![...node.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))) continue;
            const style = getComputedStyle(node);
            let parent = node, background = "rgb(22,22,22)";
            while (parent) {
                const value = getComputedStyle(parent).backgroundColor;
                const channels = rgb(value);
                if (channels.length === 3 || channels[3] >= .95) { background = value; break; }
                parent = parent.parentElement;
            }
            const values = [lum(style.color), lum(background)].sort((a,b) => a-b);
            const ratio = (values[1] + .05) / (values[0] + .05);
            const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
            const required = large ? 3 : 4.5;
            checked++;
            minimum = Math.min(minimum, ratio);
            if (ratio < required) failures.push({node: node.id || node.className || node.tagName, text: node.textContent.slice(0,30), ratio});
        }
        if (failures.length) throw new Error(JSON.stringify(failures));
        return {theme: document.documentElement.dataset.theme, checked, minimum: Math.round(minimum * 100) / 100};
    })()""")


def main():
    httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as browser_runtime:
            browser = browser_runtime.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 820})
                page.set_default_timeout(8000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/pixiv/image?*", lambda route: route.fulfill(
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="blue"/></svg>',
                ))
                page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                assert page.locator(".brand-stage, .brand-choices, #brandPlay").count() == 0
                screenshots = Path(tempfile.mkdtemp(prefix="moku-flash-visual-"))
                themes = []
                for width, height in ((1280, 820), (375, 812)):
                    page.set_viewport_size({"width": width, "height": height})
                    for theme in ("dark", "light"):
                        themes.append(check_theme(page, theme))
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(screenshots / f"home-{theme}-{width}.png"))
                page.set_viewport_size({"width": 1280, "height": 820})
                page.reload()
                assert page.evaluate("document.documentElement.dataset.theme") == "light"
                page.locator("#searchSubmit").click()
                page.locator("[data-select='0']").wait_for()
                page.locator("[data-select='0']").check()
                page.locator(".card .poster").click()
                wait_for(page, "currentDetailItem?.id === '50'")
                for theme in ("dark", "light"):
                    themes.append(check_theme(page, theme))
                page.locator("#download").click()
                wait_for(page, "!singleDownloadPending && !!resumableSingleTask")
                page.locator("#download").click()
                wait_for(page, "!singleDownloadPending && !resumableSingleTask")
                assert [(row[0], len(row)) for row in ProbeHandler.requests] == [
                    (0, 200), (200, 200), (200, 200), (400, 1),
                ]
                assert "401" in page.locator("#toast").inner_text()
                page.locator("#quality").select_option("original")
                page.evaluate("openBatchHub()")
                page.locator("#openBasketPicker").click()
                page.locator("[data-open-collection='50']").click()
                wait_for(page, "basketDetailItem?.id === '50'")
                for theme in ("dark", "light"):
                    page.evaluate("document.querySelector('#themeToggle').click()")
                    themes.append(check_theme(page, theme))
                assert page.evaluate("refreshArtworkPreview('50')") is True
                page.locator("#basketPageMore").click()
                assert "revision-2-48" in page.locator("#basketPages img").first.get_attribute("src")
                page.locator("#basketBack").click()
                page.locator("#basketBack").click()
                assert page.locator("#quality").input_value() == "original"
                assert page.locator("#basketPage").is_hidden()
                assert page.evaluate("document.body.classList.contains('batch-mode')")
                for dialog in ("#helpDialog", "#capacityDialog", "#selectionLimitDialog", "#loginDialog"):
                    page.evaluate("(selector) => document.querySelector(selector).showModal()", dialog)
                    for theme in ("dark", "light"):
                        if page.evaluate("document.documentElement.dataset.theme") != theme:
                            page.evaluate("document.querySelector('#themeToggle').click()")
                        themes.append(check_theme(page, theme))
                    page.evaluate("(selector) => document.querySelector(selector).close()", dialog)
                for width, height in ((1280, 820), (375, 812)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("document.querySelector('#home').scrollIntoView()")

                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert not errors, errors
                print(json.dumps({"ok": True, "downloadChunks": [len(row) for row in ProbeHandler.requests],
                                  "basketFreshPages": True, "qualityPreserved": True,
                                  "viewports": [1280, 375], "scriptErrors": errors,
                                  "themes": themes, "screenshots": str(screenshots)}))
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
