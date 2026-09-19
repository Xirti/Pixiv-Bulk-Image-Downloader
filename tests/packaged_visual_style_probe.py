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
        "brandStage": {},
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
                const bodyStyle = getComputedStyle(document.body);
                const pagerDock = document.querySelector('.pagination-dock');
                const pagerStyle = getComputedStyle(pagerDock);
                const selectAll = document.querySelector('#selectAllPage');
                const clearPage = document.querySelector('#clearPageSelection');
                return {
                    button: {color: button.color, backgroundImage: button.backgroundImage},
                    brandStage: {
                        choices: document.querySelectorAll('.brand-choices button').length,
                        canvasReady: document.querySelector('#brandPlay').classList.contains('has-canvas'),
                        logosLoaded: [...document.querySelectorAll('.brand-choices img')].every(img => img.complete && img.naturalWidth > 0),
                        oldBackgroundRemoved: !document.querySelector('.art-depth'),
                        navigationDots: document.querySelectorAll('.page-rail a').length,
                        selectionDocked: !!document.querySelector('.pagination-dock #selectionBar')
                    },
                    galleryControls: {
                        selectAllVisible: !!selectAll && getComputedStyle(selectAll).display !== 'none',
                        clearPageVisible: !!clearPage && getComputedStyle(clearPage).display !== 'none',
                        pagerPosition: pagerStyle.position,
                        pagerBottom: pagerStyle.bottom,
                        pagerPointerEvents: pagerStyle.pointerEvents,
                        pagerDisplay: pagerStyle.display,
                        pagerActive: pagerDock.classList.contains("is-visible")
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
            result["brandStage"] = visual["brandStage"]
            result["galleryControls"] = visual["galleryControls"]
            after_scroll = evaluate(ws, counter, """(() => {
                const gallery = document.querySelector('#gallery');
                const dock = document.querySelector('.pagination-dock');
                const grid = document.querySelector('#grid');
                const pagination = document.querySelector('#pagination');
                const originalGrid = grid.innerHTML;
                const originalPagination = pagination.innerHTML;
                const spacer = document.createElement('div');
                spacer.style.height = `${innerHeight + 64}px`;
                grid.innerHTML = '<div style="height:240px;grid-column:1/-1"></div>';
                pagination.innerHTML = '<button type="button">1</button>';
                gallery.insertAdjacentElement('afterend', spacer);

                gallery.scrollIntoView({block:'start'});
                updatePaginationDock();
                const visibleStyle = getComputedStyle(dock);
                const visibleWithResults = dock.classList.contains('is-visible');
                const position = visibleStyle.position;
                const bottom = visibleStyle.bottom;
                const dockHeightWithResults = dock.getBoundingClientRect().height;
                const dockTopWithResults = innerHeight - dockHeightWithResults;
                const bottomWithResults = gallery.getBoundingClientRect().bottom;

                scrollTo(0, scrollY + bottomWithResults + 1);
                updatePaginationDock();
                const hiddenPastResults = !dock.classList.contains('is-visible');
                const bottomPastResults = gallery.getBoundingClientRect().bottom;

                gallery.scrollIntoView({block:'start'});
                updatePaginationDock();
                const visibleAfterReturn = dock.classList.contains('is-visible');
                const bottomAfterReturn = gallery.getBoundingClientRect().bottom;

                spacer.remove();
                grid.innerHTML = originalGrid;
                pagination.innerHTML = originalPagination;
                scrollTo(0, 0);
                updatePaginationDock();
                return {
                    visibleWithResults,
                    hiddenPastResults,
                    visibleAfterReturn,
                    position,
                    bottom,
                    dockHeightWithResults,
                    dockTopWithResults,
                    bottomWithResults,
                    bottomPastResults,
                    bottomAfterReturn
                };
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
                toggleArtworkSelection(items[0], true);
                toggleArtworkSelection(items[1], true);
                render();
                document.querySelector("#openBatch").click();
                await Promise.resolve();
                const directBasket = basketPageOpen() && document.querySelectorAll("#batchCollections .batch-collection").length === 2;
                openBatchHub();
                const summaryOnly = {
                    cards: document.querySelectorAll("#batchCollections .batch-collection").length,
                    summary: document.querySelector("#batchDetailSummary").textContent,
                    downloadVisible: getComputedStyle(document.querySelector("#batchDownload")).display !== "none"
                };
                document.querySelector("#openBasketPicker").click();
                const firstJump = {
                    cards: document.querySelectorAll("#batchCollections .batch-collection").length,
                    title: document.querySelector("#basketTitle").textContent
                };
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
                    returnVisible: !document.querySelector("#basketBack").hidden
                };
                change(document.querySelector('[data-collection-page="1"]'), false);
                const selectedPayloadAfterUncheck = selectedGroups().find((group) => group.id === "probe-multi")?.pages || [];
                document.querySelector("#basketBack").click();
                const partial = snapshot();
                change(document.querySelector('[data-batch-select="probe-multi"]'), false);
                const removed = snapshot();
                document.querySelector('[data-batch-artwork="probe-multi"] [data-open-collection]').click();
                await Promise.resolve();
                const selectedAfterRemoval = document.querySelectorAll("[data-collection-page]:checked").length;
                change(document.querySelector('[data-collection-page="2"]'), true);
                document.querySelector("#basketBack").click();
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
                toggleArtworkSelection(normalItem, true);
                openSelectionBasket();
                releaseNormal();
                await Promise.resolve();
                await Promise.resolve();
                const normalToBatchGuard = {
                    title: document.querySelector("#basketTitle").textContent,
                    workspaceVisible: basketPageOpen()
                };
                clearAllSelection();
                const clearRaceItem = artwork("probe-clear-race", "Clear race", 5);
                delete clearRaceItem.pageImages;
                items = [clearRaceItem];
                render();
                toggleArtworkSelection(clearRaceItem, true);
                let releaseClearRace;
                fetchJson = (_url, options) => new Promise((resolve, reject) => {
                    releaseClearRace = () => options?.signal?.aborted
                        ? reject(new DOMException("Aborted", "AbortError"))
                        : resolve(artwork("probe-clear-race", "Resurrected", 5));
                });
                openSelectionBasket();
                await Promise.resolve();
                document.querySelector("#openBasketPicker").click();
                document.querySelector('[data-open-collection="probe-clear-race"]').click();
                await Promise.resolve();
                document.querySelector("#clearSelection").click();
                releaseClearRace();
                await Promise.resolve();
                await Promise.resolve();
                const clearRaceGuard = {
                    workspaceHidden: !basketPageOpen(),
                    title: document.querySelector("#basketTitle").textContent,
                    selected: selectedArtworkIds.size,
                    imagePickerClosed: !document.body.classList.contains("basket-image-picker")
                };
                clearAllSelection();
                const singleJumpItem = artwork("probe-single-jump", "Single jump", 6);
                items = [singleJumpItem];
                render();
                toggleArtworkSelection(singleJumpItem, true);
                openBatchHub();
                const singleSummaryCards = document.querySelectorAll("#batchCollections .batch-collection").length;
                document.querySelector("#openBasketPicker").click();
                const singleArtworkCards = document.querySelectorAll("#batchCollections .batch-collection").length;
                document.querySelector('[data-open-collection="probe-single-jump"]').click();
                await Promise.resolve();
                const singlePageCards = document.querySelectorAll("[data-collection-page]").length;
                const singleTwoJumps = {singleSummaryCards, singleArtworkCards, singlePageCards};
                clearAllSelection();
                const capacityItems = Array.from({length: 1001}, (_, index) => artwork(`probe-capacity-${index}`, `Capacity ${index}`, 1));
                for (const item of capacityItems.slice(0, 300)) toggleArtworkSelection(item, true);
                const selectedAt300 = selectedArtworkIds.size;
                for (const item of capacityItems.slice(300, 1000)) toggleArtworkSelection(item, true);
                const overflowAccepted = toggleArtworkSelection(capacityItems[1000], true);
                const capacityGuard = {
                    selectedAt300,
                    selectedAtLimit: selectedPageCount(),
                    overflowAccepted,
                    dialogOpen: document.querySelector("#selectionLimitDialog").open
                };
                document.querySelector("#selectionLimitDialog").close();
                clearAllSelection();
                const staleItem = artwork("probe-stale", "Stale", 2);
                delete staleItem.pageImages;
                batchCandidateItems = [staleItem];
                selectedArtworks.set(staleItem.id, staleItem);
                selectedArtworkIds.add(staleItem.id);
                selectedPagesByArtwork.set(staleItem.id, new Set([0, 1]));
                openSelectionBasket();
                openBasketArtworkPicker();
                const staleButton = document.querySelector('[data-batch-artwork="probe-stale"] [data-open-collection]');
                let releaseStale;
                fetchJson = () => new Promise((resolve) => { releaseStale = () => resolve(artwork("probe-stale", "Stale detail", 2)); });
                staleButton.click();
                viewGeneration += 1;
                render();
                releaseStale();
                await Promise.resolve();
                const staleGuard = {
                    title: document.querySelector("#basketTitle").textContent,
                    resultTitle: document.querySelector("#grid h3")?.textContent || ""
                };
                clearAllSelection();
                const optionItem = artwork("probe-options", "Options", 401);
                items = [optionItem];
                activeSearchContext = {kind: "tags", value: "options"};
                render();
                toggleArtworkSelection(optionItem, true);
                openSelectionBasket();
                document.querySelector("#openBasketPicker").click();
                document.querySelector("#quality").value = "regular";
                document.querySelector("#saveRoot").value = "C:\\fixed";
                document.querySelector("#createFolder").checked = true;
                document.querySelector("#groupArtworks").checked = false;
                const optionPayloads = [];
                fetchJson = async (_url, options) => {
                    optionPayloads.push(JSON.parse(options.body));
                    if (optionPayloads.length === 1) {
                        document.querySelector("#quality").value = "changed";
                        document.querySelector("#saveRoot").value = "C:\\changed";
                        document.querySelector("#createFolder").checked = false;
                        document.querySelector("#groupArtworks").checked = true;
                    }
                    return {saved: []};
                };
                document.querySelector("#batchDownload").click();
                await new Promise((resolve) => setTimeout(resolve, 0));
                const optionSnapshot = {
                    requests: optionPayloads.length,
                    allStable: optionPayloads.every((payload) => payload.quality === "regular"
                        && payload.saveRoot === "C:\\fixed"
                        && payload.createFolder === true
                        && payload.groupArtworks === false),
                    pages: optionPayloads.reduce((total, payload) => total + payload.groups.reduce((sum, group) => sum + group.pages.length, 0), 0)
                };
                fetchJson = originalFetchJson;
                const geometry = {
                    separate: check.right + 8 <= badge.left,
                    inside: check.left >= card.left && badge.right <= card.right,
                    badgeTarget: open.width >= 30 && open.height >= 30,
                    overflow: document.documentElement.scrollWidth > innerWidth,
                    badge: badgeLabel
                };
                return {
                    summaryOnly,
                    firstJump,
                    initial,
                    detailDefault,
                    selectedPayloadAfterUncheck,
                    partial,
                    removed,
                    selectedAfterRemoval,
                    restored,
                    normalToBatchGuard,
                    clearRaceGuard,
                    singleTwoJumps,
                    capacityGuard,
                    staleGuard,
                    optionSnapshot,
                    geometry,
                    ok: directBasket && summaryOnly.cards === 0
                        && summaryOnly.summary.includes("2 个作品")
                        && summaryOnly.downloadVisible
                        && firstJump.cards === 2
                        && firstJump.title.includes("选择要下载的作品")
                        && initial.cards === 2
                        && initial.selectedWorks === 2
                        && initial.selectedResults === 2
                        && initial.multiLabel.includes("4/4")
                        && detailDefault.pages === 4
                        && detailDefault.selectedPages === 4
                        && detailDefault.returnVisible
                        && !selectedPayloadAfterUncheck.includes(1)
                        && selectedPayloadAfterUncheck.includes(0)
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
                        && normalToBatchGuard.title.includes("采集篮")
                        && normalToBatchGuard.workspaceVisible
                        && clearRaceGuard.workspaceHidden
                        && clearRaceGuard.title !== "Resurrected"
                        && clearRaceGuard.selected === 0
                        && clearRaceGuard.imagePickerClosed
                        && singleTwoJumps.singleSummaryCards === 0
                        && singleTwoJumps.singleArtworkCards === 1
                        && singleTwoJumps.singlePageCards === 6
                        && capacityGuard.selectedAt300 === 300
                        && capacityGuard.selectedAtLimit === 1000
                        && !capacityGuard.overflowAccepted
                        && capacityGuard.dialogOpen
                        && staleGuard.title !== "Stale detail"
                        && staleGuard.resultTitle === "Single jump"
                        && optionSnapshot.requests === 3
                        && optionSnapshot.allStable
                        && optionSnapshot.pages === 401
                        && geometry.separate
                        && geometry.inside
                        && geometry.badgeTarget
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
            and result["brandStage"].get("choices") == 6
            and result["brandStage"].get("canvasReady")
            and result["brandStage"].get("logosLoaded")
            and result["brandStage"].get("oldBackgroundRemoved")
            and result["brandStage"].get("navigationDots") == 3
            and result["brandStage"].get("selectionDocked")
            and result["galleryControls"].get("selectAllVisible")
            and result["galleryControls"].get("clearPageVisible")
            and result["galleryControls"].get("pagerPosition") == "fixed"
            and result["galleryControls"].get("pagerBottom") == "0px"
            and result["galleryControls"].get("pagerPointerEvents") == "none"
            and not result["galleryControls"].get("pagerActive")
            and result["galleryControlsAfterScroll"].get("visibleWithResults")
            and result["galleryControlsAfterScroll"].get("hiddenPastResults")
            and result["galleryControlsAfterScroll"].get("visibleAfterReturn")
            and result["galleryControlsAfterScroll"].get("dockHeightWithResults", 0) > 0
            and result["galleryControlsAfterScroll"].get("bottomWithResults", 0) > result["galleryControlsAfterScroll"].get("dockTopWithResults", 0)
            and result["galleryControlsAfterScroll"].get("bottomPastResults", 0) < 0
            and result["galleryControlsAfterScroll"].get("position") == "fixed"
            and result["galleryControlsAfterScroll"].get("bottom") == "0px"
            and result["viewport"].get("scrollWidth", 0) <= result["viewport"].get("width", 0)
            and result["viewport"].get("scrollHeight", 0) > result["viewport"].get("height", 0)
            and result["viewport"].get("bodyBackgroundImage") == "none"
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
