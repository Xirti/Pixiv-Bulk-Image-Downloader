"""Offline Edge smoke check of the real page, with no account or download writes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs

os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from playwright.sync_api import sync_playwright


class ProbeHandler(server.Handler):
    requests = []
    detail_revision = 0
    fail_once = True
    fail_basket_once = False
    partial_pages = []
    back_pages = []
    back_floor = 1

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
        query = parse_qs(_request.query)
        if query.get("tag") == ["back-probe"]:
            page = int(query.get("page", ["1"])[0])
            prefetch = query.get("prefetch") == ["true"]
            if page < type(self).back_floor:
                type(self).back_floor = 1
            type(self).back_floor = max(type(self).back_floor, page - 6)
            if not prefetch:
                type(self).back_pages.append(page)
            return self.send_json({
                "items": [] if prefetch else [{**self.artwork(), "id": str(i), "pages": 1} for i in range((page - 1) * 36 + 1, page * 36 + 1)],
                "page": page, "availablePages": list(range(type(self).back_floor, page + 4)),
                "preloadedThrough": page + 3, "hasMore": True, "total": page * 36, "perPage": 36,
            })
        if query.get("tag") == ["partial-probe"]:
            page = int(query.get("page", ["1"])[0])
            type(self).partial_pages.append(page)
            count = 10 if len(type(self).partial_pages) == 1 else 36
            return self.send_json({
                "items": [{**self.artwork(), "id": str(i), "pages": 1} for i in range((page - 1) * 36 + 1, (page - 1) * 36 + 1 + count)],
                "page": page, "availablePages": [1, 2], "preloadedThrough": 2,
                "hasMore": page == 1, "total": 72, "perPage": 36, "label": "Partial page probe",
            })
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

    def _post_save_basket(self, data):
        if type(self).fail_basket_once:
            type(self).fail_basket_once = False
            return self.send_json({"error": "采集篮暂时无法保存"}, 503)
        return super()._post_save_basket(data)


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
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    return page.evaluate(r"""(() => {
        const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
        const lum = color => rgb(color).slice(0,3).map(x => {
            x /= 255; return x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4;
        }).reduce((sum, x, i) => sum + x * [.2126,.7152,.0722][i], 0);
        const failures = [];
        let minimum = 100, checked = 0;
        for (const node of document.querySelectorAll("body *")) {
            if (!node.getClientRects().length || node.disabled || (!node.matches("input:not([type=checkbox]),select,textarea") && ![...node.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))) continue;
            if (!node.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
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
            if (ratio < required) failures.push({node: node.id || node.className || node.tagName, text: node.textContent.slice(0,30), color: style.color, background, theme: document.documentElement.dataset.theme, ratio});
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
                screenshots = Path(tempfile.mkdtemp(prefix="moku-lite-visual-"))
                themes = []
                for width, height in ((1280, 820), (375, 812)):
                    page.set_viewport_size({"width": width, "height": height})
                    for theme in ("dark", "light"):
                        themes.append(check_theme(page, theme))
                        centers = page.evaluate("""(() => {
                            const title = document.querySelector('.home-intro h1');
                            const text = title.firstChild.nodeType === Node.TEXT_NODE ? title.firstChild : title.firstChild.firstChild;
                            const range = document.createRange();
                            range.setStart(text, 0); range.setEnd(text, 5);
                            const rect = range.getBoundingClientRect();
                            const eyebrow = document.createRange();
                            eyebrow.selectNodeContents(document.querySelector('.home-intro .eyebrow'));
                            const mark = eyebrow.getBoundingClientRect();
                            const main = document.querySelector('main').getBoundingClientRect();
                            return {title: (rect.left + rect.right) / 2, wordmark: (mark.left + mark.right) / 2, target: (main.left + main.right) / 2};
                        })()""")
                        assert abs(centers["title"] - centers["target"]) < 1, centers
                        assert abs(centers["wordmark"] - centers["target"]) < 1, centers
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                        page.screenshot(path=str(screenshots / f"home-{theme}-{width}.png"))
                page.set_viewport_size({"width": 1280, "height": 820})
                page.reload()
                assert page.evaluate("document.documentElement.dataset.theme") == "light"
                page.locator("#tag").fill("back-probe")
                page.locator("#searchSubmit").click()
                wait_for(page, "currentPage === 1 && items.length === 36 && !searchPending")
                for target in range(2, 13):
                    page.locator('#pagination [aria-label="下一页"]').click()
                    wait_for(page, f"currentPage === {target} && !searchPending")
                assert page.evaluate("firstAvailablePage") == 6
                for target in range(11, 4, -1):
                    previous = page.locator('#pagination [aria-label="上一页"]')
                    assert previous.is_enabled(), f"cache boundary prevented returning to page {target}"
                    previous.click()
                    wait_for(page, f"currentPage === {target} && !searchPending")
                assert page.locator('#pagination [data-page="1"]').count() == 1
                page.locator('#pagination [data-page="1"]').click()
                wait_for(page, "currentPage === 1 && !searchPending")
                assert page.evaluate("items.map(item => item.id)") == [str(i) for i in range(1, 37)]
                page.locator("#tag").fill("partial-probe")
                page.locator("#searchSubmit").click()
                wait_for(page, "items.length === 10 && !searchPending")
                assert page.locator('#pagination [data-page="2"]').is_disabled()
                page.locator('#pagination [aria-label="继续加载当前页"]').click()
                wait_for(page, "items.length === 36 && !searchPending")
                page.locator('#pagination [aria-label="下一页"]').click()
                wait_for(page, "currentPage === 2 && !searchPending")
                assert page.evaluate("items.map(item => item.id)") == [str(i) for i in range(37, 73)]
                assert ProbeHandler.partial_pages == [1, 1, 2], ProbeHandler.partial_pages
                page.locator("#tag").fill("cat")
                page.locator("#searchSubmit").click()
                page.locator("[data-select='0']").wait_for()
                ProbeHandler.fail_basket_once = True
                page.locator("[data-select='0']").check()
                wait_for(page, "!document.querySelector('#workspaceNotice').hidden && !workspaceSync.loading")
                assert page.evaluate("selection.pageCount") == 401
                for theme in ("dark", "light"):
                    themes.append(check_theme(page, theme))
                page.set_viewport_size({"width": 375, "height": 812})
                assert page.locator("#workspaceRetry").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.locator("#workspaceRetry").click()
                wait_for(page, "workspaceSync.ready && document.querySelector('#workspaceNotice').hidden")
                assert server.WORKSPACE_STORE.load("public")["basket"][0]["pages"] == list(range(401))
                page.set_viewport_size({"width": 1280, "height": 820})
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
                page.locator('#basketDetailDeck [data-page="1"]').click()
                assert page.locator('#basketDetailDeck [data-page="1"]').get_attribute("aria-pressed") == "true"
                page.evaluate("workspaceSync.settled()")
                ProbeHandler.fail_basket_once = True
                page.locator('#basketPages [data-collection-page="48"]').uncheck()
                wait_for(page, "!document.querySelector('#workspaceNotice').hidden && !workspaceSync.loading")
                # Another window changes the saved basket while this one is
                # offline. Retry must update both the download choice and DOM.
                page.evaluate("""async () => {
                    const data = await fetchJson('/api/workspace');
                    data.basket[0].pages = data.basket[0].pages.filter(page => page !== 50);
                    await fetchJson('/api/workspace/basket', {method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({basket:data.basket,revision:data.revision,scope:data.scope})});
                }""")
                page.locator("#workspaceRetry").click()
                wait_for(page, "workspaceSync.ready && document.querySelector('#workspaceNotice').hidden")
                assert not page.locator('#basketPages [data-collection-page="48"]').is_checked()
                assert page.locator('#basketPages [data-collection-page="49"]').is_checked()
                assert not page.locator('#basketPages [data-collection-page="50"]').is_checked()
                assert page.locator("#batchSummary").inner_text() == "已选 399/401 张"
                assert page.evaluate("collectionPageOffset") == 48
                assert page.evaluate("currentDownloadPages(currentDetailItem).length") == 399
                assert page.locator('#basketDetailDeck [data-page="1"]').get_attribute("aria-pressed") == "true"
                assert page.locator("#quality").input_value() == "original"
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
                                  "basketFreshPages": True, "basketSaveRetry": True, "basketRetryViewReconciled": True,
                                  "qualityPreserved": True,
                                  "viewports": [1280, 375], "scriptErrors": errors,
                                  "themes": themes, "partialPageRequests": ProbeHandler.partial_pages,
                                  "evictedPageBackNavigation": ProbeHandler.back_pages, "screenshots": str(screenshots)}))
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
