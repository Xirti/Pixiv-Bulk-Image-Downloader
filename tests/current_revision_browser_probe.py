"""Offline Edge smoke check of the real page, with no account or download writes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
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


def check_brand_motion(page):
    timings = {}
    for brand in ("openai", "claude-color", "grok", "deepseek-color", "kimi-color", "glm"):
        page.locator(f"button[data-brand='{brand}']").click()
        page.locator("#brandPlay").click()
        page.evaluate("window.motionSamples.length = 0")
        page.wait_for_timeout(750)
        samples = page.evaluate("window.motionSamples")
        assert len(samples) >= 10, (brand, len(samples))
        durations = sorted(sample["ms"] for sample in samples)
        timings[brand] = {
            "frames": len(samples),
            "meanCallbackMs": round(sum(durations) / len(durations), 3),
            "p95CallbackMs": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 3),
        }
    page.evaluate("document.body.classList.add('viewer-open')")
    page.wait_for_timeout(100)
    page.evaluate("window.motionSamples.length = 0")
    page.wait_for_timeout(250)
    assert page.evaluate("window.motionSamples.length") == 0, "decorative work continued behind viewer"
    page.evaluate("document.body.classList.remove('viewer-open')")
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_timeout(100)
    page.evaluate("window.motionSamples.length = 0")
    page.wait_for_timeout(250)
    assert page.evaluate("window.motionSamples.length") == 0, "reduced motion kept requesting decorative frames"
    page.emulate_media(reduced_motion="no-preference")
    page.locator("button[data-brand='openai']").click()
    return timings


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
                page.add_init_script("""
                    window.motionSamples = [];
                    const scheduleFrame = window.requestAnimationFrame.bind(window);
                    window.requestAnimationFrame = callback => scheduleFrame(now => {
                        const started = performance.now();
                        try { callback(now); }
                        finally {
                            if (window.motionSamples.length < 2000)
                                window.motionSamples.push({ms: performance.now() - started});
                        }
                    });
                """)
                page.set_default_timeout(8000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/pixiv/image?*", lambda route: route.fulfill(
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="blue"/></svg>',
                ))
                page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                wait_for(page, "document.querySelector('#brandPlay').classList.contains('has-canvas')")
                motion = check_brand_motion(page)
                page.locator("#searchSubmit").click()
                page.locator("[data-select='0']").wait_for()
                page.locator("[data-select='0']").check()
                page.locator(".card .poster").click()
                wait_for(page, "currentDetailItem?.id === '50'")
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
                assert page.evaluate("refreshArtworkPreview('50')") is True
                page.locator("#basketPageMore").click()
                assert "revision-2-48" in page.locator("#basketPages img").first.get_attribute("src")
                page.locator("#basketBack").click()
                page.locator("#basketBack").click()
                assert page.locator("#quality").input_value() == "original"
                assert page.locator("#basketPage").is_hidden()
                assert page.evaluate("document.body.classList.contains('batch-mode')")
                for width, height in ((1280, 820), (375, 812)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("document.querySelector('#home').scrollIntoView()")
                    wait_for(page, "document.querySelector('#brandPlay').classList.contains('has-canvas')")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                assert not errors, errors
                print(json.dumps({"ok": True, "downloadChunks": [len(row) for row in ProbeHandler.requests],
                                  "basketFreshPages": True, "qualityPreserved": True,
                                  "viewports": [1280, 375], "scriptErrors": errors,
                                  "brandMotion": motion, "overlayAndReducedMotionPaused": True}))
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
