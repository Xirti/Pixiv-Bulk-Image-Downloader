"""Offline real-browser checks; no Pixiv credentials, requests or downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from playwright.sync_api import sync_playwright
from current_revision_browser_probe import check_theme
from test_publication_date_filter import DateFilteredSearchTests


def main():
    queries = []

    def source(url, _cancel):
        query = parse_qs(urlsplit(url).query)
        queries.append(query)
        assert query["scd"] == ["2024-01-01"], query
        assert query["ecd"] == ["2024-01-07"], query
        page = int(query["p"][0])
        return {"body": {"illustManga": {"total": 360, "lastPage": 6, "data": [
            DateFilteredSearchTests.row(number)
            for number in range((page - 1) * 60 + 1, page * 60 + 1)
        ]}}}

    httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    errors = []
    screenshots = Path(tempfile.mkdtemp(prefix="moku-date-nav-"))
    try:
        with patch.object(server, "search_pixiv_json", side_effect=source), sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 820}, reduced_motion="reduce")
                page.set_default_timeout(8000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/pixiv/image?*", lambda route: route.fulfill(
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#53675b"/></svg>',
                ))
                page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                page.locator("#datePreset").select_option("custom")
                page.locator("#startDate").fill("2024-01-01")
                page.locator("#endDate").fill("2024-01-07")
                themes = []
                for width, height in ((1280, 820), (375, 812), (320, 740)):
                    page.set_viewport_size({"width": width, "height": height})
                    for theme in ("dark", "light"):
                        themes.append(check_theme(page, theme))
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
                        rail = page.locator(".page-rail").bounding_box()
                        assert rail and rail["width"] <= 64 and rail["x"] == 0, rail
                        icons = page.locator(".page-rail a")
                        first, second = icons.nth(0).bounding_box(), icons.nth(1).bounding_box()
                        assert second["y"] - first["y"] - first["height"] >= 24
                        assert page.locator(".page-rail a svg").count() == 3
                        assert page.locator(".page-rail").inner_text().strip() == ""
                        assert page.evaluate("""(() => {
                            const title = document.querySelector('.home-title-text').getBoundingClientRect();
                            const main = document.querySelector('main').getBoundingClientRect();
                            return Math.abs((title.left + title.right - main.left - main.right) / 2) < 1;
                        })()""")
                        page.screenshot(path=str(screenshots / f"home-{theme}-{width}.png"))

                page.set_viewport_size({"width": 1280, "height": 820})
                page.locator("#endDate").fill("2023-01-01")
                page.locator("#searchSubmit").click()
                assert not queries, "invalid dates reached the remote search"
                assert page.locator("#endDate").evaluate("input => !input.checkValidity()")
                page.locator("#endDate").fill("2024-01-07")
                page.locator("#tag").fill("cat")
                page.locator("#searchSubmit").click()
                page.wait_for_function("() => document.querySelectorAll('#grid .card').length === 36")
                assert "2024-01-01 至 2024-01-07" in page.locator("#count").inner_text()
                page.wait_for_function("() => document.querySelector('#backTop').classList.contains('is-visible')")
                top = page.locator("#backTop").bounding_box()
                chrome = page.locator("body > header").bounding_box()
                assert top and top["y"] <= chrome["height"] + 16 and top["x"] > 1200, top
                page.locator("#backTop").click()
                page.wait_for_function("() => scrollY === 0")
                page.locator('.page-rail a[href="#detail"]').click()
                page.wait_for_function("() => document.querySelector('.page-rail a[href=\"#detail\"]').hasAttribute('aria-current')")
                page.locator('.page-rail a[href="#gallery"]').click()
                page.wait_for_function("() => document.querySelector('.page-rail a[href=\"#gallery\"]').hasAttribute('aria-current')")
                page.screenshot(path=str(screenshots / "gallery-light-1280.png"))
                page.locator('.page-rail a[href="#home"]').click()
                page.wait_for_function("() => document.querySelector('.page-rail a[href=\"#home\"]').hasAttribute('aria-current')")
                page.locator("#datePreset").select_option("all")
                assert page.locator("#customDates").is_hidden()
                assert page.locator("#startDate").is_disabled()
                assert not errors, errors
                print(json.dumps({"ok": True, "themes": themes, "scriptErrors": errors,
                                  "remoteRequests": len(queries), "screenshots": str(screenshots)}, ensure_ascii=False))
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
