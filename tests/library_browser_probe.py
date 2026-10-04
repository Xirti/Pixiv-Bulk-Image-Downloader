"""Offline Edge checks, with real local publication into disposable folders."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlencode

os.environ["MOKU_DISABLE_PERSISTENT_SESSION"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from download_history import DownloadHistory
from PIL import Image
from playwright.sync_api import sync_playwright
from current_revision_browser_probe import check_theme


def artwork():
    return {
        "id": "11", "pages": 3, "source": "pixiv", "restriction": "safe",
        "title": "猫 <b>不要执行</b>", "artist": "画师", "tags": ["猫"],
        "workType": "illustration", "description": "离线验证", "date": "2026-10-03",
        "width": 4, "height": 4, "bookmarks": 1, "thumb": "/api/pixiv/image?token=probe",
        "pageImages": [{"regular": f"https://i.pximg.net/probe_p{page}.png", "original": f"https://i.pximg.net/probe_p{page}.png"} for page in range(3)],
        "qualities": [{"id": "regular", "label": "标准清晰度", "width": 4, "height": 4}, {"id": "original", "label": "原始清晰度", "width": 4, "height": 4}],
        "formats": [{"id": "source", "label": "保留源格式"}],
    }


def authorized_artwork(*_args, **_kwargs):
    item = artwork()
    for page in item["pageImages"]:
        for key in ("regular", "original"):
            page[key] = "/api/pixiv/image?" + urlencode({"url": page[key]})
    item["thumb"] = item["pageImages"][0]["regular"]
    return server.authorize_item_images(item)


class ProbeHandler(server.Handler):
    def _get_pixiv_search(self, _request):
        item = artwork()
        item.pop("pageImages")
        return self.send_json({"items": [item], "page": 1, "availablePages": [1], "total": 1, "perPage": 36, "hasMore": False, "preloadedThrough": 1})


def main():
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(png, "PNG")
    errors, requests, themes = [], [], []
    hold_download = [False]
    release_download = threading.Event()

    def image_bytes(*_args, **_kwargs):
        if hold_download[0] and not release_download.wait(20):
            raise TimeoutError("probe download was not released")
        return png.getvalue(), "image/png"

    screenshots = Path(tempfile.mkdtemp(prefix="moku-library-visual-"))
    with tempfile.TemporaryDirectory(prefix="moku-library-probe-") as directory:
        root = Path(directory)
        history = DownloadHistory(root / "library" / "downloads.sqlite3")
        for number in range(23):
            history.record(str(number), [{"artworkId": str(100 + number), "title": f"旧记录 {number}", "artist": "测试", "workType": "illustration", "quality": "regular", "format": "source", "pages": [0], "files": [str(root / f"old-{number}.png")]}])
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "DOWNLOAD_HISTORY", history), patch.object(server, "ensure_network_opener_current"), patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "pixiv_item_for_download", side_effect=authorized_artwork), patch.object(server, "pixiv_request", side_effect=image_bytes), sync_playwright() as runtime:
                browser = runtime.chromium.launch(channel="msedge", headless=True)
                try:
                    context = browser.new_context(viewport={"width": 1280, "height": 820}, reduced_motion="reduce", permissions=["clipboard-read", "clipboard-write"])
                    page = context.new_page()
                    # Spy on copying without replacing the user's OS clipboard.
                    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { globalThis.probeCopiedPaths = value; }}})")
                    page.set_default_timeout(8000)
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda request: requests.append(request.url))
                    page.route("**/api/pixiv/image?*", lambda route: route.fulfill(content_type="image/png", body=png.getvalue()))
                    page.goto(f"http://127.0.0.1:{httpd.server_port}/")
                    assert not any("/api/library/" in url for url in requests), "startup eagerly fetched history"
                    assert page.locator("main #detail").count() == 0
                    assert page.locator(".page-rail [title]").count() == 6
                    assert page.locator(".page-rail").evaluate("node => [...node.children].map(button => button.title)") == ["搜索", "预览选图", "采集篮", "下载打包", "下载历史", "收藏（暂未开放）"]
                    for theme in ("dark", "light"):
                        themes.append(check_theme(page, theme))
                        assert page.locator("#tag").evaluate("node => getComputedStyle(node).backgroundColor !== getComputedStyle(node.closest('.search-panel')).backgroundColor")
                        page.screenshot(path=str(screenshots / f"search-{theme}.png"))
                    page.locator("#datePreset").select_option("1")
                    bounds = page.evaluate("readSearchFilters()")
                    assert bounds["startDate"] == bounds["endDate"] and bounds["startDate"]
                    page.locator("#searchSubmit").click()
                    page.wait_for_function("() => !searchPending && items.length === 1")
                    page.locator('[data-select="0"]').check()
                    page.locator(".card .poster").click()
                    page.wait_for_function("() => currentDetailItem?.id === '11'")
                    assert page.locator("#downloadPage").is_visible()
                    assert page.locator("#gallery").evaluate("node => node.closest('main').inert")
                    page.locator("#saveRoot").fill(str(root))
                    assert page.locator("#quality").input_value() == "original"
                    assert "原始分辨率" in page.locator("#qualityText").inner_text()
                    page.locator("#download").click()
                    page.wait_for_function("() => !singleDownloadPending && document.querySelector('#toast').textContent.includes('已保存')")
                    assert "已保存 3 页，3 个文件" in page.locator("#toast").inner_text(), page.locator("#toast").inner_text()
                    saved = next(row for row in history.list()["items"] if row["artworkId"] == "11")
                    assert saved["pages"] == [0, 1, 2] and len(saved["files"]) == 3, saved
                    assert all(Path(file).is_file() for file in saved["files"]), saved
                    page.locator("#navFavorites").click()
                    assert "暂未开放" in page.locator("#favoritesPage").inner_text()
                    assert "先做" not in page.locator("#favoritesPage").inner_text()
                    page.locator("#favoritesBack").click()
                    assert page.locator("#quality").input_value() == "original"
                    assert page.locator("#saveRoot").input_value() == str(root)
                    page.locator("#navBasket").click()
                    page.locator('[data-open-collection="11"]').click()
                    page.wait_for_function("() => basketDetailItem?.id === '11'")
                    download_requests = sum("/api/pixiv/download" in url or "/api/pixiv/batch-download" in url for url in requests)
                    page.locator("#basketDownload").click()
                    assert page.locator("#downloadPage").is_visible()
                    assert page.evaluate("selection.pageCount") == 3
                    assert page.locator("#quality").input_value() == "original"
                    assert page.locator("#saveRoot").input_value() == str(root)
                    assert sum("/api/pixiv/download" in url or "/api/pixiv/batch-download" in url for url in requests) == download_requests
                    page.locator("#navBasket").click()
                    page.locator('[data-open-collection="11"]').click()
                    page.wait_for_function("() => basketDetailItem?.id === '11'")
                    page.locator('#basketPages [data-collection-page="1"]').uncheck()
                    page.locator("#navHistory").click()
                    page.wait_for_function("() => document.querySelectorAll('.history-record').length === 20")
                    assert page.locator("#basketPage").evaluate("node => node.inert")
                    page.locator('#historyPagination [data-history-page="2"]').click()
                    page.wait_for_function("() => document.querySelectorAll('.history-record').length === 4")
                    page.locator("#historyQuery").fill("猫")
                    page.locator('#historySearch button[type="submit"]').click()
                    page.wait_for_function("() => document.querySelectorAll('.history-record').length === 1")
                    assert page.locator(".history-record h3 b").count() == 0
                    assert page.locator(".history-record details").count() == 0
                    assert page.locator(".history-paths").is_visible()
                    assert page.locator(".history-paths").input_value() == "\n".join(saved["files"])
                    page.locator('[data-copy-history="0"]').click()
                    page.wait_for_function("() => document.querySelector('#historyStatus').textContent === '路径已复制'")
                    assert page.evaluate("globalThis.probeCopiedPaths") == "\n".join(saved["files"])
                    page.locator("#historyBack").click()
                    assert page.locator("#basketArtworkDetail").is_visible()
                    assert page.evaluate("[...selection.get('11').pages]") == [0, 2]
                    page.locator("#basketBack").click()
                    page.locator("#basketBack").click()
                    assert page.locator("#quality").input_value() == "original"
                    assert page.locator("#downloadPage").is_visible()
                    hold_download[0] = True
                    page.locator("#batchDownload").click()
                    page.wait_for_function("() => selection.locked")
                    page.locator("#navHistory").click()
                    page.locator('.page-rail a[href="#gallery"]').click(force=True)
                    assert page.locator("#historyPage").is_visible(), "navigation changed a live download context"
                    assert page.evaluate("[...selection.get('11').pages]") == [0, 2]
                    release_download.set()
                    page.wait_for_function("() => !selection.locked && document.querySelector('#toast').textContent.includes('已保存 2')")
                    page.wait_for_function("() => document.querySelectorAll('.history-record').length === 2")
                    page.locator("#historyBack").click()
                    assert history.list()["total"] == 25
                    for width, height in ((1280, 820), (375, 812), (320, 740)):
                        page.set_viewport_size({"width": width, "height": height})
                        for nav, view in ((".page-rail a[href='#detail']", "downloadPage"), ("#navHistory", "historyPage"), ("#navFavorites", "favoritesPage")):
                            page.locator(nav).click()
                            page.wait_for_function("view => !document.querySelector('#' + view).hidden", arg=view)
                            for theme in ("dark", "light"):
                                themes.append(check_theme(page, theme))
                                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (width, view)
                                top = page.locator(f"#{view}").bounding_box()["y"]
                                chrome = page.locator("body > header").bounding_box()
                                assert top >= chrome["y"] + chrome["height"] - 1, (width, view, top, chrome)
                                page.screenshot(path=str(screenshots / f"{view}-{theme}-{width}.png"))
                    page.set_viewport_size({"width": 1280, "height": 820})
                    page.locator("#navHistory").click()
                    page.locator("#historyQuery").fill("")
                    page.locator('#historySearch button[type="submit"]').click()
                    page.wait_for_function("() => document.querySelector('#historyStatus').textContent.startsWith('25 条')")
                    page.locator("#historyClear").click()
                    page.locator("#historyClearCancel").click()
                    assert history.list()["total"] == 25
                    page.locator('[data-delete-history]').first.click()
                    page.locator("#historyClearCancel").click()
                    assert history.list()["total"] == 25
                    page.locator('[data-delete-history]').first.click()
                    page.locator("#historyClearConfirm").click()
                    page.wait_for_function("() => document.querySelector('#historyStatus').textContent.startsWith('24 条')")
                    page.locator('[data-select-history]').first.check()
                    page.locator('#historyPagination [data-history-page="2"]').click()
                    page.wait_for_function("() => document.querySelectorAll('.history-record').length === 4")
                    page.locator("#historySelectAll").check()
                    assert page.locator("#historySelectedCount").inner_text() == "已选 5 条"
                    page.locator("#historyDeleteSelected").click()
                    assert "5 条" in page.locator("#historyDeleteTitle").inner_text()
                    page.locator("#historyClearConfirm").click()
                    page.wait_for_function("() => document.querySelector('#historyStatus').textContent.startsWith('19 条')")
                    assert page.locator(".history-record").count() == 19
                    assert page.locator("#historyPagination").inner_text() == ""
                    assert all(Path(file).is_file() for file in saved["files"])
                    page.locator("#historyClear").click()
                    page.locator("#historyClearConfirm").click()
                    page.wait_for_function("() => document.querySelector('#historyStatus').textContent.startsWith('0 条')")
                    assert history.list()["total"] == 0
                    assert all(Path(file).is_file() for file in saved["files"])
                    page.keyboard.press("Escape")
                    assert page.locator("#downloadPage").is_visible()
                    assert page.locator("#quality").input_value() == "original"
                    page.locator("#downloadBack").click()
                    assert page.locator("#downloadPage").is_hidden()
                    assert page.evaluate("selection.size") == 1
                    page.locator("#navBasket").click()
                    page.locator('[data-open-collection="11"]').click()
                    page.wait_for_function("() => basketDetailItem?.id === '11'")
                    page.locator("#basketClear").click()
                    page.locator("#basketClearCancel").click()
                    assert page.evaluate("selection.size") == 1
                    page.locator("#basketClear").click()
                    page.locator("#basketClearConfirm").click()
                    assert page.locator("#basketPage").is_visible()
                    assert page.evaluate("selection.size") == 0
                    assert "采集篮为空" in page.locator("#batchCollections").inner_text()
                    assert page.locator("#basketClear").is_disabled() and page.locator("#basketDownload").is_disabled()
                    assert all(Path(file).is_file() for file in saved["files"])
                    for width in (1280, 375, 320):
                        page.set_viewport_size({"width": width, "height": 820})
                        for theme in ("dark", "light"):
                            themes.append(check_theme(page, theme))
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (width, "basket")
                            for button in ("basketClear", "basketDownload"):
                                box = page.locator("#" + button).bounding_box()
                                assert box["x"] >= 0 and box["x"] + box["width"] <= width, (width, button, box)
                            page.screenshot(path=str(screenshots / f"basket-{theme}-{width}.png"))
                    assert not errors, errors
                    print(json.dumps({"ok": True, "independentViews": True, "historyDuringDownload": True, "publishedFiles": len(list(root.rglob("*.png"))), "copyPaths": True, "clearKeptFiles": True, "themesChecked": len(themes), "viewports": [1280, 375, 320], "scriptErrors": errors, "screenshots": str(screenshots)}, ensure_ascii=False))
                finally:
                    browser.close()
        finally:
            release_download.set()
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
