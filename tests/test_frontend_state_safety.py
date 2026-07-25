import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def block(start: str, end: str) -> str:
    return APP[APP.index(start):APP.index(end, APP.index(start))]


class FrontendStateSafetyTests(unittest.TestCase):
    def test_authorization_loss_aborts_requests_and_removes_restricted_views(self):
        cleanup = block("function handleAuthorizationLoss", "function updateSelectionBar")
        self.assertIn("viewGeneration += 1", cleanup)
        self.assertIn("searchController.abort()", cleanup)
        self.assertIn("detailController.abort()", cleanup)
        self.assertIn('$("#safety").value = "safe"', cleanup)
        self.assertIn("closeAllViewer()", cleanup)
        self.assertIn("discardRestrictedSelections()", cleanup)
        self.assertIn("clearDetail(", cleanup)
        self.assertIn('grid.innerHTML =', cleanup)

        status = block("async function syncAuthStatus", "const helpDialog")
        self.assertIn("lastKnownLoggedIn === true && !logged", status)
        self.assertIn("handleAuthorizationLoss()", status)

    def test_search_commits_context_only_after_the_matching_response(self):
        search = block("async function search", "function syncResultSelectionControls")
        request = search.index("await fetchJson")
        identity_check = search.index("controller !== searchController")
        context_commit = search.index("activeSearchContext = requestedContext")
        filter_commit = search.index("activeSearchFilters = requestedFilters")
        self.assertLess(request, identity_check)
        self.assertLess(identity_check, context_commit)
        self.assertLess(identity_check, filter_commit)
        self.assertIn("resultSelectionEnabled = false", search[:request])
        self.assertIn('$("#download").disabled = true', search[:request])

        controls = block("function syncSearchScopedControls", "function clearDetail")
        self.assertIn('$("#openBatch").disabled', controls)
        self.assertIn('$("#clearSelection").disabled', controls)
        self.assertIn("basketSelectionLocked || searchPending", controls)
        open_basket = block("function openSelectionBasket", "function renderBasketSummary")
        self.assertIn("if (basketSelectionLocked || searchPending) return", open_basket)

        select_all = block("function selectAllCurrentPage", "function clearAllCurrentPage")
        clear_page = block("function clearAllCurrentPage", "async function search")
        self.assertIn("searchPending || !resultSelectionEnabled", select_all)
        self.assertIn("searchPending || !resultSelectionEnabled", clear_page)

    def test_pagination_uses_committed_filters_and_filter_changes_restart_page_one(self):
        navigation = block("function navigateToPage", "function archiveAndContinue")
        self.assertIn("{ ...activeSearchFilters }", navigation)

        listeners = block('$("#searchForm").onsubmit', '$("#browseFolder").onclick')
        for selector in ("#workType", "#includeAi", "#fuzzySearch", "#safety"):
            self.assertIn(selector, listeners)
        self.assertIn("search($(\"#tag\").value, 1, readSearchFilters())", listeners)

    def test_checkbox_keyboard_events_do_not_open_the_parent_card(self):
        render = block("function render()", "function renderPagination")
        self.assertIn('if (event.target.closest(".card-select")) return;', render)

    def test_expired_detail_images_use_one_deduplicated_refresh_path(self):
        self.assertIn("const detailRefreshes = new Map()", APP)
        self.assertIn("const detailRefreshAttempts = new Map()", APP)
        self.assertIn("DETAIL_REFRESH_COOLDOWN_MS", APP)
        refresh = block("async function refreshArtworkPreview", "function installImageFallbacks")
        self.assertIn("detailRefreshes.get(artworkId)", refresh)
        self.assertIn("detailRefreshes.set(artworkId", refresh)
        self.assertIn("detailRefreshes.delete(artworkId)", refresh)
        self.assertIn("detailRefreshAttempts.get(artworkId)", refresh)
        self.assertIn('fetchJson(`/api/pixiv/artwork/${artworkId}`', refresh)
        fallback = block("function installImageFallbacks", "function syncSearchScopedControls")
        self.assertIn("img.dataset.detailArtwork", fallback)
        self.assertIn("refreshArtworkPreview(artworkId)", fallback)
        self.assertIn("detailRefreshAttemptedUrl", fallback)
        self.assertIn("failedUrl", fallback)
        self.assertIn('data-detail-artwork="${esc(item.id)}"', APP)
        self.assertIn("delete img.dataset.detailRefreshAttemptedUrl", refresh)

    def test_large_basket_and_viewer_views_are_windowed_before_rendering(self):
        self.assertIn("const BASKET_ARTWORK_WINDOW = 120", APP)
        self.assertIn("const VIEWER_PAGE_WINDOW = 80", APP)
        picker = block("function openBasketArtworkPicker", "function selectedGroups")
        self.assertIn("chosen.slice(start, end)", picker)
        self.assertIn("data-basket-window", picker)
        viewer = block("function renderViewerWindow", "function openAllViewer")
        self.assertIn("item.pageImages.slice(start, end)", viewer)
        self.assertIn("data-viewer-window", viewer)
        self.assertIn("currentDetailItem", viewer)
        self.assertIn("renderViewerWindow(latestItem)", viewer)

    def test_pagination_scroll_updates_are_coalesced_to_one_animation_frame(self):
        scheduler = block("function schedulePaginationDockUpdate", "async function select")
        self.assertIn("requestAnimationFrame(run)", scheduler)
        self.assertIn('window.addEventListener("scroll", schedulePaginationDockUpdate, { passive: true })', scheduler)
        self.assertNotIn('window.addEventListener("scroll", updatePaginationDock', scheduler)

    def test_single_download_uses_the_explicit_current_detail_context(self):
        payload = block("function downloadPayload", "function scrollToResults")
        self.assertIn("currentDetailContext || activeSearchContext", payload)
        self.assertIn("context: downloadContext", payload)
        detail = block("function renderDetail", "function renderCollectionPageWindow")
        self.assertIn("currentDetailContext = { ...detailContext }", detail)
        basket_detail = block("async function openBatchCollection", '$("#returnToBatch").onclick')
        self.assertIn("batchCandidateContextByArtwork.get(item.id)", basket_detail)
        self.assertIn("renderDetail(item, items.findIndex", basket_detail)

    def test_failed_logout_still_reconciles_authorization_state(self):
        action = block('$("#authAction").onclick', 'document.querySelectorAll(".dialog-close")')
        self.assertIn("finally", action)
        self.assertIn("await syncAuthStatus()", action)
        self.assertLess(action.index("await syncAuthStatus()"), action.index("if (actionError)"))

    def test_select_all_validates_page_counts_before_allocating_page_sets(self):
        select_all = block("function selectAllCurrentPage", "function clearAllCurrentPage")
        self.assertIn("validatedArtworkPageCount(item)", select_all)
        self.assertIn("selectionWouldExceedPageLimit(additionalPages)", select_all)
        self.assertNotIn("Array.from", select_all)
        toggle = block("function toggleArtworkSelection", "function selectAllCurrentPage")
        self.assertLess(toggle.index("selectionWouldExceedPageLimit"), toggle.index("Array.from"))

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the browser-state harness")
    def test_search_and_logout_state_transitions_execute_atomically(self):
        harness = r'''
class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  toggle(name, enabled) { enabled ? this.values.add(name) : this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}
class FakeElement {
  constructor() {
    this.classList = new FakeClassList();
    this.dataset = {};
    this.children = [];
    this.options = [];
    this.selectedOptions = [];
    this.queryCache = new Map();
    this.listeners = new Map();
    this._innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.open = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = String(value); this.queryCache.clear(); }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  removeEventListener() {}
  querySelectorAll(selector) {
    if (selector !== "[data-viewer-window]:not([disabled])") return [];
    if (this.queryCache.has(selector)) return this.queryCache.get(selector);
    const nodes = [];
    const pattern = /<button\b([^>]*\bdata-viewer-window="([^"]+)"[^>]*)>/g;
    let match;
    while ((match = pattern.exec(this._innerHTML))) {
      if (/\bdisabled\b/.test(match[1])) continue;
      const node = new FakeElement();
      node.dataset.viewerWindow = match[2];
      nodes.push(node);
    }
    this.queryCache.set(selector, nodes);
    return nodes;
  }
  scrollIntoView() {}
  getBoundingClientRect() { return {bottom: 1}; }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name]; }
  removeAttribute(name) { delete this[name]; }
  closest() { return null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const fakeElements = new Map();
const detailImages = [];
const fakeElement = (selector) => {
  if (!fakeElements.has(selector)) fakeElements.set(selector, new FakeElement());
  return fakeElements.get(selector);
};
globalThis.document = {
  documentElement: new FakeElement(),
  body: new FakeElement(),
  querySelector: fakeElement,
  querySelectorAll: (selector) => selector === "[data-detail-artwork]" ? detailImages : [],
};
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.requestIdleCallback = () => {};
globalThis.window.addEventListener = () => {};
fakeElement("#safety").value = "safe";
fakeElement("#workType").value = "all";
fakeElement("#quality").value = "regular";
fakeElement("#format").value = "source";
fakeElement("#tag").value = "old";
'''
        assertions = r'''
(async () => {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const oldItem = {
    id: "10", restriction: "safe", source: "pixiv", title: "old", artist: "artist",
    tags: [], pages: 1, thumb: "/api/pixiv/image?token=old", bookmarks: 0,
    description: "", width: 1, height: 1, date: "", pageImages: [{regular: "/old", original: "/old"}],
    qualities: [{id: "regular", label: "regular", width: 1, height: 1}],
    formats: [{id: "source", label: "source"}],
  };
  items = [oldItem];
  activeTagQuery = "old";
  activeSearchContext = {kind: "tags", value: "old"};
  activeSearchFilters = {mode: "safe", workType: "all", includeAi: false, fuzzy: false};
  currentDetailItem = oldItem;
  activeArtworkId = oldItem.id;
  resultSelectionEnabled = true;
  selectedArtworkIds.add(oldItem.id);
  selectedArtworks.set(oldItem.id, oldItem);
  selectedPagesByArtwork.set(oldItem.id, new Set([0]));
  let releaseSearch;
  fetchJson = () => new Promise((resolve) => { releaseSearch = resolve; });
  const pending = search("author:new", 1, {mode: "safe", workType: "manga", includeAi: true, fuzzy: false});
  await Promise.resolve();
  check(activeSearchContext.value === "old", "pending search committed its context early");
  check(selectAllCurrentPage() === false, "pending search allowed stale result selection");
  check(document.querySelector("#download").disabled, "pending search left stale detail download enabled");
  check(document.querySelector("#openBatch").disabled, "pending search left the stale basket action enabled");
  check(document.querySelector("#clearSelection").disabled, "pending search left the stale clear action enabled");
  openSelectionBasket();
  check(batchCandidateItems.length === 0, "pending search opened the stale basket programmatically");
  document.querySelector("#clearSelection").onclick();
  check(selectedArtworkIds.has(oldItem.id), "pending search cleared stale selection programmatically");
  releaseSearch({
    items: [], page: 1, availablePages: [1], preloadedThrough: 1, hasMore: false,
    label: "new", tags: [], tag: "author:new", total: 0, perPage: 36,
  });
  await pending;
  check(activeSearchContext.kind === "author" && activeSearchContext.value === "new", "successful search did not commit context");
  check(activeSearchFilters.workType === "manga" && activeSearchFilters.includeAi, "successful search did not commit filters");

  const restricted = {...oldItem, id: "18", restriction: "r18", thumb: "/api/pixiv/image?token=r18"};
  items = [restricted];
  selectedArtworkIds.add(restricted.id);
  selectedArtworks.set(restricted.id, restricted);
  selectedPagesByArtwork.set(restricted.id, new Set([0]));
  currentDetailItem = restricted;
  activeArtworkId = restricted.id;
  document.querySelector("#grid").innerHTML = '<img src="/api/pixiv/image?token=r18">';
  document.querySelector("#deck").innerHTML = '<img src="/api/pixiv/image?token=r18-detail">';
  document.querySelector("#viewerGrid").innerHTML = '<img src="/api/pixiv/image?token=r18-viewer">';
  document.querySelector("#batchCollections").innerHTML = '<img src="/api/pixiv/image?token=r18-basket">';
  searchController = new AbortController();
  detailController = new AbortController();
  const searchSignal = searchController.signal;
  const detailSignal = detailController.signal;
  lastKnownLoggedIn = true;
  document.querySelector("#safety").value = "r18";
  fetchJson = async () => ({loggedIn: false});
  await syncAuthStatus();
  check(searchSignal.aborted && detailSignal.aborted, "authorization loss did not abort requests");
  check(document.querySelector("#safety").value === "safe", "authorization loss did not restore safe mode");
  check(!selectedArtworkIds.has(restricted.id), "authorization loss retained restricted selection");
  check(currentDetailItem === null && activeArtworkId === null, "authorization loss retained restricted detail");
  const dynamicMarkup = ["#grid", "#deck", "#collectionPages", "#viewerGrid", "#batchCollections"]
    .map((selector) => document.querySelector(selector).innerHTML).join("");
  check(!dynamicMarkup.includes("token=r18"), "authorization loss retained an R18 URL in the DOM");

  const failedLogoutItem = {...restricted, id: "19", thumb: "/api/pixiv/image?token=r18-failed-logout"};
  items = [failedLogoutItem];
  selectedArtworkIds.add(failedLogoutItem.id);
  selectedArtworks.set(failedLogoutItem.id, failedLogoutItem);
  selectedPagesByArtwork.set(failedLogoutItem.id, new Set([0]));
  currentDetailItem = failedLogoutItem;
  currentDetailContext = {kind: "tags", value: "restricted"};
  activeArtworkId = failedLogoutItem.id;
  document.querySelector("#grid").innerHTML = '<img src="/api/pixiv/image?token=r18-failed-logout">';
  document.querySelector("#authAction").textContent = "退出 Pixiv 账户";
  document.querySelector("#safety").value = "r18";
  lastKnownLoggedIn = true;
  let failedLogoutStatusChecks = 0;
  window.pywebview = {api: {
    pixiv_logout: async () => ({ok: false, error: "credential deletion failed"}),
    pixiv_login: async () => ({ok: false}),
  }};
  fetchJson = async (url) => {
    if (url === "/api/status") failedLogoutStatusChecks += 1;
    return {loggedIn: false};
  };
  await document.querySelector("#authAction").onclick();
  check(failedLogoutStatusChecks === 1, "failed logout did not reconcile server authorization state");
  check(!selectedArtworkIds.has(failedLogoutItem.id), "failed logout response retained restricted selection");
  check(currentDetailItem === null && currentDetailContext === null, "failed logout response retained restricted detail state");
  check(!document.querySelector("#grid").innerHTML.includes("r18-failed-logout"), "failed logout response retained restricted DOM");
  check(document.querySelector("#authStateText").textContent === "credential deletion failed", "failed logout error was not preserved after reconciliation");

  clearAllSelection();
  items = [{...oldItem, id: "huge", pages: Number.MAX_SAFE_INTEGER}];
  resultSelectionEnabled = true;
  check(selectAllCurrentPage() === false, "oversized page count bypassed the selection limit");
  check(!selectedArtworkIds.has("huge"), "oversized artwork was partially selected");

  const stalePages = Array.from({length: 401}, (_, page) => ({
    regular: `/api/pixiv/image?token=stale-${page}`,
    original: `/api/pixiv/image?token=stale-original-${page}`,
  }));
  const freshPages = Array.from({length: 401}, (_, page) => ({
    regular: `/api/pixiv/image?token=fresh-${page}`,
    original: `/api/pixiv/image?token=fresh-original-${page}`,
  }));
  const staleItem = {...oldItem, id: "77", pages: 401, pageImages: stalePages, thumb: stalePages[0].regular};
  const freshItem = {...staleItem, pageImages: freshPages, thumb: freshPages[0].regular};
  items = [staleItem];
  currentDetailItem = staleItem;
  activeArtworkId = staleItem.id;
  viewerPageOffset = 0;
  renderViewerWindow(staleItem);
  const nextViewerWindow = document.querySelector("#viewerGrid")
    .querySelectorAll("[data-viewer-window]:not([disabled])")[0];
  check(typeof nextViewerWindow?.onclick === "function", "viewer next-window control was not bound");

  const detailImage = new FakeElement();
  detailImage.dataset.detailArtwork = staleItem.id;
  detailImage.dataset.detailPage = "0";
  detailImage.setAttribute("src", stalePages[0].regular);
  detailImages.push(detailImage);
  installImageFallbacks({querySelectorAll: () => [detailImage]});
  let releaseRefresh;
  let refreshFetches = 0;
  fetchJson = () => {
    refreshFetches += 1;
    return new Promise((resolve) => { releaseRefresh = resolve; });
  };
  const firstRefresh = refreshArtworkPreview(staleItem.id);
  const duplicateRefresh = refreshArtworkPreview(staleItem.id);
  await Promise.resolve();
  check(refreshFetches === 1, "concurrent image failures started duplicate detail refreshes");
  releaseRefresh(freshItem);
  const refreshResults = await Promise.all([firstRefresh, duplicateRefresh]);
  check(refreshResults.every(Boolean), "valid refreshed detail was not committed");
  check(detailImage.src === freshPages[0].regular, "refreshed preview URL did not replace the expired URL");
  check(currentDetailItem === freshItem && items[0] === freshItem, "refreshed detail did not update current state");
  check(await refreshArtworkPreview(staleItem.id) === false, "refresh cooldown allowed an immediate retry loop");
  check(refreshFetches === 1, "refresh cooldown performed another network request");

  const secondFreshItem = {
    ...freshItem,
    pageImages: freshPages.map((page, index) => ({...page, regular: `/api/pixiv/image?token=second-fresh-${index}`})),
  };
  const realDateNow = Date.now;
  const previousAttemptAt = detailRefreshAttempts.get(staleItem.id);
  Date.now = () => previousAttemptAt + DETAIL_REFRESH_COOLDOWN_MS + 1;
  fetchJson = async () => { refreshFetches += 1; return secondFreshItem; };
  await detailImage.listeners.get("error")();
  Date.now = realDateNow;
  check(refreshFetches === 2, "post-cooldown refresh did not perform exactly one request");
  check(detailImage.src === secondFreshItem.pageImages[0].regular, "same image DOM could not self-heal after cooldown");
  check(detailImage.dataset.detailRefreshAttemptedUrl === undefined, "successful refresh retained a stale per-URL retry marker");

  nextViewerWindow.onclick();
  const viewerMarkup = document.querySelector("#viewerGrid").innerHTML;
  check((viewerMarkup.match(/<figure>/g) || []).length <= VIEWER_PAGE_WINDOW, "viewer rendered more than one page window");
  check(viewerMarkup.includes("token=second-fresh-80"), "viewer navigation restored stale preview tokens");
  check(!viewerMarkup.includes("token=stale-80"), "viewer navigation captured the stale artwork object");

  const originalContext = {kind: "author", value: "original-artist"};
  activeSearchContext = {kind: "tags", value: "new-search"};
  selectedContextByArtwork.set(staleItem.id, originalContext);
  currentDetailContext = {...activeSearchContext};
  const ordinaryPayload = downloadPayload(secondFreshItem, 0);
  check(ordinaryPayload.body.context.value === "new-search", "old basket context hijacked a normally opened detail");
  currentDetailContext = originalContext;
  const basketPayload = downloadPayload(secondFreshItem, 0);
  check(basketPayload.body.context === originalContext, "single basket download lost its original search context");

  batchCandidateItems = Array.from({length: 1000}, (_, index) => ({
    ...oldItem, id: String(1000 + index), title: `work-${index}`,
  }));
  basketArtworkOffset = 0;
  openBasketArtworkPicker();
  const basketMarkup = document.querySelector("#batchCollections").innerHTML;
  check((basketMarkup.match(/<article\b/g) || []).length <= BASKET_ARTWORK_WINDOW, "basket rendered more than one artwork window");
  check(basketMarkup.includes("data-basket-window"), "basket window navigation was not rendered");
  abortDetailRefreshes();
  check(detailRefreshes.size === 0 && detailRefreshAttempts.size === 0, "detail refresh state was not cleared");
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
'''
        result = subprocess.run(
            [shutil.which("node")],
            input=harness + APP + assertions,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
