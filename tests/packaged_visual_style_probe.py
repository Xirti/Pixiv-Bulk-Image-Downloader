from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import websocket

from packaged_native_click_login_probe import (
    evaluate,
    free_port,
    launch,
    main_target,
    stop,
    wait_until,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    args = parser.parse_args()
    exe = Path(args.exe).resolve()
    root = Path(tempfile.mkdtemp(prefix="moku-visual-probe-"))
    process = None
    result = {
        "ok": False,
        "folderButton": {},
        "lightRibbons": {},
        "artDepth": {},
        "galleryControls": {},
        "galleryControlsAfterScroll": {},
        "viewport": {},
        "batchFlow": {},
        "error": "",
    }
    try:
        port = free_port()
        process, base, _ = launch(exe, root, port)
        target = wait_until(lambda: main_target(port, base), 25, "visual probe CDP target")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        counter = [0]
        try:
            wait_until(
                lambda: evaluate(ws, counter, "document.readyState === 'complete' && !!document.querySelector('#browseFolder')"),
                20,
                "visual probe page",
            )
            visual = evaluate(ws, counter, """(() => {
                const button = getComputedStyle(document.querySelector('#browseFolder'));
                const root = document.querySelector('.light-ribbons');
                const ribbons = [...document.querySelectorAll('.light-ribbon')];
                const depth = document.querySelector('.art-depth');
                const depthStyle = getComputedStyle(depth);
                const bodyStyle = getComputedStyle(document.body);
                const pagerDock = document.querySelector('.pagination-dock');
                const pagerStyle = getComputedStyle(pagerDock);
                const selectAll = document.querySelector('#selectAllPage');
                const clearPage = document.querySelector('#clearPageSelection');
                return {
                    button: {color: button.color, backgroundImage: button.backgroundImage},
                    ribbons: {
                        count: ribbons.length,
                        decorative: root?.getAttribute('aria-hidden') === 'true',
                        pointerEvents: getComputedStyle(root).pointerEvents,
                        animation: getComputedStyle(ribbons[0]).animationName,
                        conservative: document.documentElement.classList.contains('conservative')
                    },
                    artDepth: {
                        moonRings: document.querySelectorAll('.moon-ring').length,
                        moonRingSegments: document.querySelectorAll('.moon-ring').length ? 2 : 0,
                        constellations: document.querySelectorAll('.art-constellation').length,
                        decorative: depth?.getAttribute('aria-hidden') === 'true',
                        pointerEvents: depthStyle.pointerEvents,
                        backgroundLayers: bodyStyle.backgroundImage.split('gradient').length - 1
                    },
                    galleryControls: {
                        selectAllVisible: !!selectAll && getComputedStyle(selectAll).display !== 'none',
                        clearPageVisible: !!clearPage && getComputedStyle(clearPage).display !== 'none',
                        pagerPosition: pagerStyle.position,
                        pagerBottom: pagerStyle.bottom,
                        pagerPointerEvents: pagerStyle.pointerEvents,
                        pagerDisplay: pagerStyle.display
                    },
                    viewport: {
                        width: innerWidth,
                        height: innerHeight,
                        scrollWidth: document.documentElement.scrollWidth,
                        scrollHeight: document.documentElement.scrollHeight,
                        bodyBackgroundImage: bodyStyle.backgroundImage
                    }
                };
            })()""")
            result["folderButton"] = visual["button"]
            result["lightRibbons"] = visual["ribbons"]
            result["artDepth"] = visual["artDepth"]
            result["galleryControls"] = visual["galleryControls"]
            after_scroll = evaluate(ws, counter, """(() => {
                const gallery = document.querySelector('#gallery');
                const dock = document.querySelector('.pagination-dock');
                document.querySelector('#pagination').innerHTML = '<button type="button">1</button>';
                gallery.scrollIntoView({block:'start'});
                updatePaginationDock();
                const style = getComputedStyle(dock);
                return {visible: style.display === 'flex', position: style.position, bottom: style.bottom};
            })()""")
            result["galleryControlsAfterScroll"] = after_scroll
            result["viewport"] = visual["viewport"]
            batch_flow = evaluate(ws, counter, """(async () => {
                const pixel = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
                const artwork = (id, title, pages) => ({
                    id,
                    title,
                    artist: "Probe",
                    tags: ["probe"],
                    pages,
                    thumb: pixel,
                    bookmarks: 1,
                    source: "pixiv",
                    description: "",
                    width: 100,
                    height: 100,
                    date: "2026-01-01",
                    qualities: [{id: "regular", label: "regular", width: 100, height: 100}],
                    formats: [{id: "source", label: "source"}],
                    pageImages: Array.from({length: pages}, () => ({regular: pixel, original: pixel}))
                });
                const change = (input, checked) => {
                    input.checked = checked;
                    input.dispatchEvent(new Event("change", {bubbles: true}));
                };
                const snapshot = () => ({
                    cards: document.querySelectorAll("#batchCollections .batch-collection").length,
                    selectedWorks: document.querySelectorAll("[data-batch-select]:checked").length,
                    selectedResults: document.querySelectorAll("#grid [data-select]:checked").length,
                    multiLabel: document.querySelector('[data-batch-artwork="probe-multi"] small')?.textContent || "",
                    summary: document.querySelector("#batchSummary")?.textContent || ""
                });
                clearAllSelection();
                items = [artwork("probe-single", "Single", 1), artwork("probe-multi", "Multi", 4)];
                activeSearchContext = {kind: "tags", value: "probe"};
                currentPage = 1;
                render();
                document.querySelector("#openBatch").click();
                await Promise.resolve();
                const card = document.querySelector('[data-batch-artwork="probe-multi"]').getBoundingClientRect();
                const check = document.querySelector('[data-batch-select="probe-multi"] + span').getBoundingClientRect();
                const badgeElement = document.querySelector('[data-batch-artwork="probe-multi"] .batch-page-count');
                const badge = badgeElement.getBoundingClientRect();
                const badgeLabel = badgeElement.textContent;
                const open = document.querySelector('[data-batch-artwork="probe-multi"] [data-open-collection]').getBoundingClientRect();
                const initial = snapshot();
                document.querySelector('[data-batch-artwork="probe-multi"] [data-open-collection]').click();
                await Promise.resolve();
                const detailDefault = {
                    pages: document.querySelectorAll("[data-collection-page]").length,
                    selectedPages: document.querySelectorAll("[data-collection-page]:checked").length,
                    returnVisible: !document.querySelector("#returnToBatch").hidden
                };
                change(document.querySelector('[data-collection-page="1"]'), false);
                document.querySelector("#returnToBatch").click();
                const partial = snapshot();
                change(document.querySelector('[data-batch-select="probe-multi"]'), false);
                const removed = snapshot();
                document.querySelector('[data-batch-artwork="probe-multi"] [data-open-collection]').click();
                await Promise.resolve();
                const selectedAfterRemoval = document.querySelectorAll("[data-collection-page]:checked").length;
                change(document.querySelector('[data-collection-page="2"]'), true);
                document.querySelector("#returnToBatch").click();
                const restored = snapshot();
                clearAllSelection();
                const normalItem = artwork("probe-normal", "Normal", 2);
                delete normalItem.pageImages;
                items = [normalItem];
                render();
                const originalFetchJson = fetchJson;
                let releaseNormal;
                fetchJson = () => new Promise((resolve) => { releaseNormal = () => resolve(artwork("probe-normal", "Late normal detail", 2)); });
                select(0);
                await Promise.resolve();
                openCurrentPageBatch();
                releaseNormal();
                await Promise.resolve();
                await Promise.resolve();
                const normalToBatchGuard = {
                    title: document.querySelector("#dTitle").textContent,
                    workspaceVisible: !document.querySelector("#batchWorkspace").hidden
                };
                const staleItem = artwork("probe-stale", "Stale", 2);
                delete staleItem.pageImages;
                batchCandidateItems = [staleItem];
                selectedArtworks.set(staleItem.id, staleItem);
                selectedArtworkIds.add(staleItem.id);
                selectedPagesByArtwork.set(staleItem.id, new Set([0, 1]));
                renderBatchWorkspace();
                const staleButton = document.querySelector('[data-batch-artwork="probe-stale"] [data-open-collection]');
                let releaseStale;
                fetchJson = () => new Promise((resolve) => { releaseStale = () => resolve(artwork("probe-stale", "Stale detail", 2)); });
                staleButton.click();
                viewGeneration += 1;
                render();
                releaseStale();
                await Promise.resolve();
                const staleGuard = {
                    title: document.querySelector("#dTitle").textContent,
                    resultTitle: document.querySelector("#grid h3")?.textContent || ""
                };
                fetchJson = originalFetchJson;
                const geometry = {
                    separate: check.right + 8 <= badge.left,
                    inside: check.left >= card.left && badge.right <= card.right,
                    openArea: open.width >= card.width - 2 && open.height > 100,
                    overflow: document.documentElement.scrollWidth > innerWidth,
                    badge: badgeLabel
                };
                return {
                    initial,
                    detailDefault,
                    partial,
                    removed,
                    selectedAfterRemoval,
                    restored,
                    normalToBatchGuard,
                    staleGuard,
                    geometry,
                    ok: initial.cards === 2
                        && initial.selectedWorks === 2
                        && initial.selectedResults === 2
                        && initial.multiLabel.includes("4/4")
                        && detailDefault.pages === 4
                        && detailDefault.selectedPages === 4
                        && detailDefault.returnVisible
                        && partial.cards === 2
                        && partial.selectedWorks === 2
                        && partial.selectedResults === 2
                        && partial.multiLabel.includes("3/4")
                        && removed.cards === 2
                        && removed.selectedWorks === 1
                        && removed.selectedResults === 1
                        && removed.multiLabel.includes("0/4")
                        && selectedAfterRemoval === 0
                        && restored.cards === 2
                        && restored.selectedWorks === 2
                        && restored.selectedResults === 2
                        && restored.multiLabel.includes("1/4")
                        && normalToBatchGuard.title === "打包详情"
                        && normalToBatchGuard.workspaceVisible
                        && staleGuard.title !== "Stale detail"
                        && staleGuard.resultTitle === "Normal"
                        && geometry.separate
                        && geometry.inside
                        && geometry.openArea
                        && !geometry.overflow
                        && geometry.badge === "4P"
                };
            })()""", await_promise=True)
            result["batchFlow"] = batch_flow
        finally:
            ws.close()
        result["ok"] = (
            result["folderButton"].get("color") == "rgb(10, 17, 26)"
            and "linear-gradient" in result["folderButton"].get("backgroundImage", "")
            and result["lightRibbons"].get("count") == 3
            and result["lightRibbons"].get("decorative")
            and result["lightRibbons"].get("pointerEvents") == "none"
            and result["lightRibbons"].get("animation") == "none"
            and result["lightRibbons"].get("conservative")
            and result["artDepth"].get("moonRings") == 1
            and result["artDepth"].get("moonRingSegments") == 2
            and result["artDepth"].get("constellations") == 2
            and result["artDepth"].get("decorative")
            and result["artDepth"].get("pointerEvents") == "none"
            and result["artDepth"].get("backgroundLayers", 0) >= 2
            and result["galleryControls"].get("selectAllVisible")
            and result["galleryControls"].get("clearPageVisible")
            and result["galleryControls"].get("pagerPosition") == "fixed"
            and result["galleryControls"].get("pagerBottom") == "0px"
            and result["galleryControls"].get("pagerPointerEvents") == "none"
            and result["galleryControls"].get("pagerDisplay") == "none"
            and result["galleryControlsAfterScroll"].get("visible")
            and result["galleryControlsAfterScroll"].get("position") == "fixed"
            and result["galleryControlsAfterScroll"].get("bottom") == "0px"
            and result["viewport"].get("scrollWidth", 0) <= result["viewport"].get("width", 0)
            and result["viewport"].get("scrollHeight", 0) > result["viewport"].get("height", 0)
            and result["viewport"].get("bodyBackgroundImage") != "none"
            and result["batchFlow"].get("ok")
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop(process, root)
        shutil.rmtree(root, ignore_errors=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
