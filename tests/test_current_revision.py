import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = (ROOT / "tests" / "frontend_harness.js").read_text(encoding="utf-8")
APP = "\n".join(
    (ROOT / "web" / name).read_text(encoding="utf-8")
    for name in ("ugoira-preview.js", "selection-store.js", "artwork-detail-view.js", "app.js")
)
FIXTURE = r'''
const assert = require("node:assert/strict");
const artwork = (id, count = 3, token = "old") => ({
  id, pages: count, source: "pixiv", restriction: "safe", title: id, artist: "artist",
  tags: [], width: 1, height: 1, bookmarks: 0, description: "", date: "",
  thumb: `/${token}-0`,
  pageImages: Array.from({length: count}, (_, page) => ({regular: `/${token}-${page}`, original: `/${token}-original-${page}`})),
  qualities: [{id: "regular", label: "regular", width: 1, height: 1}],
  formats: [{id: "source", label: "source"}],
});
const choose = (item) => {
  selection.choose([item], {context: activeSearchContext, resultPage: currentPage});
};
'''


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class CurrentRevisionTests(unittest.TestCase):
    def run_frontend(self, scenario):
        script = HARNESS + "\n" + APP + "\n" + FIXTURE + "\n" + (
            "(async () => {\n" + scenario + "\n})().catch(error => {"
            "console.error(error.stack); process.exitCode = 1; })"
            ".finally(() => { if (taskDockTimer) clearTimeout(taskDockTimer); });"
        )
        result = subprocess.run(
            [shutil.which("node")], input=script, cwd=ROOT,
            text=True, encoding="utf-8", capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loading_a_new_work_removes_the_previous_selection_controls(self):
        self.run_frontend(r'''
const first = artwork("10");
const next = {...artwork("20"), pageImages: undefined};
items = [first, next];
activeArtworkId = first.id;
renderDetail(first, 0);
let finish;
fetchJson = () => new Promise(resolve => { finish = resolve; });
const pending = select(1);
assert.equal(currentDetailItem, null, "loading work B retained work A's detail state");
assert.equal($("#collectionPages").innerHTML, "", "loading work B left work A's checkboxes active");
assert.equal($("#viewAll").hidden, true);
finish(artwork("20"));
await pending;
assert.equal(currentDetailItem.id, "20");
''')

    def test_animation_format_choice_is_carried_into_single_and_batch_downloads(self):
        self.run_frontend(r'''
const item = {...artwork("movie", 1), workType: "ugoira", formats: [
  {id: "source", label: "ZIP"}, {id: "gif", label: "GIF"}, {id: "mp4", label: "MP4"}
]};
animationFormats.mp4 = false;
renderDetail(item, 0);
assert.match($("#format").innerHTML, /value="mp4" disabled/);
$("#format").value = "gif";
updateFormatHint();
assert.equal(readDownloadOptions().ugoiraFormat, "gif");
assert.equal($("#download").textContent, "下载动图 GIF ↓");
assert.match($("#formatHint").textContent, /10ms/);
renderBatchDownloadOptions();
assert.equal(readDownloadOptions().ugoiraFormat, "gif");
assert.match($("#format").innerHTML, /静态图保留源格式/);
singleDownloadPending = true;
$("#download").textContent = "正在保存…";
$("#format").value = "source";
updateFormatHint();
assert.equal($("#download").textContent, "正在保存…");
''')

    def test_hover_leave_sends_cancellation_through_the_real_page_binding(self):
        self.run_frontend(r'''
requestToken = "local-test-token";
let finish, cancelled;
fetchJson = () => new Promise(resolve => { finish = resolve; });
globalThis.fetch = async (url, options) => {
  if (url === "/api/pixiv/ugoira/cancel") cancelled = JSON.parse(options.body);
  return {ok: true, json: async () => ({ok: true})};
};
const host = new FakeElement();
host.dataset = {};
const pending = ugoiraPreview.start(host, "1");
for (let i = 0; i < 40 && !finish; i += 1) await new Promise(resolve => setTimeout(resolve, 10));
assert.ok(finish);
ugoiraPreview.stop(host);
finish({frames: [{file: "0.jpg", delay: 100}]});
await pending;
await Promise.resolve();
assert.match(cancelled.requestId, /^ugoira_/);
''')

    def test_detail_decks_share_behavior_but_not_locked_state(self):
        self.run_frontend(r'''
const makeView = () => {
  const deck = new FakeElement(), hint = new FakeElement();
  const cards = [0, 1, 2].map(page => { const card = new FakeElement(); card.dataset.page = String(page); return card; });
  deck.querySelectorAll = selector => selector === ".deck-card" ? cards : [];
  const fields = Object.fromEntries(["Title", "WorkType", "Desc", "Artist", "Size", "Bookmarks", "Date", "Tags"].map(name => [name, new FakeElement()]));
  const view = createArtworkDetailView({deck, hint, fields, escape: esc, installImages: () => {}});
  view.render(artwork("deck", 3));
  return {view, cards, fields};
};
const normal = makeView(), basket = makeView();
normal.cards[0].onclick();
assert.equal(normal.cards[0].getAttribute("aria-pressed"), "true");
normal.cards[1].onclick();
assert.equal(normal.cards[0].getAttribute("aria-pressed"), "true", "another card displaced the lock");
basket.cards[2].onclick();
assert.equal(basket.cards[2].getAttribute("aria-pressed"), "true");
normal.cards[0].onclick();
assert.equal(normal.cards[1].classList.contains("deck-inert"), false);
assert.equal(basket.cards[2].classList.contains("deck-locked"), true, "one detail unlocked another detail");
basket.view.render(artwork("replacement", 3));
assert.equal(basket.cards[2].getAttribute("aria-pressed"), "false");
assert.equal(basket.fields.Title.textContent, "replacement");
''')

    def test_normal_detail_page_choice_does_not_inherit_a_previous_basket_origin(self):
        self.run_frontend(r'''
const item = artwork("origin", 3);
batchCandidateContextByArtwork.set(item.id, {kind: "tags", value: "old-basket"});
batchCandidateResultPageByArtwork.set(item.id, 2);
currentPage = 9;
activeSearchContext = {kind: "tags", value: "new-search"};
renderDetail(item, 0);
const box = $("#collectionPages").querySelectorAll("[data-collection-page]")[0];
box.checked = true;
box.onchange();
assert.equal(selection.get(item.id).context.value, "new-search");
assert.equal(selection.get(item.id).resultPage, 9);
''')

    def test_partial_page_continues_the_same_page_before_advance(self):
        self.run_frontend(r'''
items = Array.from({length: 10}, (_, i) => artwork(String(i)));
currentPage = 1;
pageNumbers = [1, 2];
preloadedThrough = 2;
searchHasMore = true;
scheduleSearchPrefetch("cat", 1, activeSearchFilters, searchGeneration);
assert.equal(prefetchTimer, null, "short-page warm-up hid newly loaded results");
renderPagination();
assert.ok($("#pagination").innerHTML.includes('data-page="1" aria-label="继续加载当前页"'));
assert.ok(/<button[^>]*disabled[^>]*data-page="2"/.test($("#pagination").innerHTML), "future numeric page skipped an incomplete current page");
let requestedPage;
search = async (_tag, page) => { requestedPage = page; };
navigateToPage(2);
assert.equal(requestedPage, 1, "programmatic forward navigation skipped undisplayed current-page results");
''')

    def test_previous_page_remains_reachable_after_its_cache_is_evicted(self):
        self.run_frontend(r'''
items = Array.from({length: 36}, (_, i) => artwork(String(i)));
currentPage = 4;
firstAvailablePage = 4;
pageNumbers = [4, 5, 6, 7];
preloadedThrough = 7;
searchHasMore = true;
renderPagination();
const previous = $("#pagination").innerHTML.match(/<button([^>]*)aria-label="上一页"/)[1];
assert.ok(!previous.includes("disabled"), "cache eviction disabled the user's only route back");
assert.ok($("#pagination").innerHTML.includes('data-page="1"'), "page one disappeared instead of remaining reloadable");
let requestedPage;
search = async (_tag, page) => { requestedPage = page; };
navigateToPage(3);
assert.equal(requestedPage, 3);
''')

    def test_animation_zip_failure_preserves_the_network_diagnostic(self):
        self.run_frontend(r'''
requestToken = "probe-token";
fetch = async () => ({ok: false, status: 502, json: async () => ({error: "动图预览失败：证书或 TLS 连接异常"})});
await assert.rejects(fetchBytes("/api/pixiv/ugoira/1?mode=zip"), /证书或 TLS/);
''')

    def test_entering_batch_mode_invalidates_an_inflight_single_detail(self):
        self.run_frontend(r'''
const next = {...artwork("20"), pageImages: undefined};
items = [next];
let finish;
fetchJson = () => new Promise(resolve => { finish = resolve; });
const pending = select(0);
const controller = detailController;
showBatchDetail();
const options = $("#format").innerHTML;
finish({...artwork("20"), workType: "ugoira"});
await pending;
assert.equal(controller.signal.aborted, true, "batch navigation did not cancel single detail loading");
assert.equal($("#format").innerHTML, options, "old detail response replaced batch download options");
''')

    def test_basket_image_refresh_survives_next_page_window(self):
        self.run_frontend(r'''
const stale = artwork("30", 100, "expired");
const fresh = artwork("30", 100, "fresh");
choose(stale);
batchCandidateItems = [stale];
activeArtworkId = stale.id;
currentDetailItem = stale;
renderBasketArtworkDetail(stale);
fetchJson = async () => fresh;
assert.equal(await refreshArtworkPreview(stale.id), true);
$("#basketPageMore").onclick();
assert.ok($("#basketPages").innerHTML.includes('/fresh-48'), "basket pagination restored expired URLs after refresh");
assert.equal(basketDetailItem, fresh);
''')

    def test_image_error_does_not_remove_a_newer_source(self):
        self.run_frontend(r'''
const item = artwork("40");
activeArtworkId = item.id;
currentDetailItem = item;
const image = new FakeElement();
image.dataset.detailArtwork = item.id;
image.dataset.detailPage = "0";
image.setAttribute("src", "/expired");
detailImages.push(image);
installImageFallbacks({querySelectorAll: () => [image]});
let reject;
fetchJson = () => new Promise((_resolve, fail) => { reject = fail; });
const pending = image.listeners.get("error")();
image.setAttribute("src", "/newer-preview");
reject(new Error("old refresh failed"));
await pending;
assert.equal(image.src, "/newer-preview", "old image error erased a newer preview URL");
''')

    def test_single_download_splits_large_selections_and_resumes_after_failure(self):
        self.run_frontend(r'''
const item = artwork("50", 401);
items = [item];
activeArtworkId = item.id;
currentDetailItem = item;
choose(item);
const requests = [];
let failOnce = true;
fetchJson = async (url, options) => {
  const body = JSON.parse(options.body);
  assert.equal(url, "/api/pixiv/download");
  assert.ok(body.pages.length <= 200, "single artwork sent more than 200 selected pages in one request");
  requests.push(body.pages);
  if (body.pages[0] === 200 && failOnce) { failOnce = false; throw new Error("interrupted"); }
  return {pages: body.pages.length, saved: ["saved.jpg"]};
};
await $("#download").onclick();
await $("#download").onclick();
assert.deepEqual(requests.map(pages => [pages[0], pages.length]), [[0,200],[200,200],[200,200],[400,1]]);
assert.ok($("#toast").textContent.includes("401"), "resume lost the completed page count");
''')

    def test_folder_picker_failure_is_reported_as_failure(self):
        self.run_frontend(r'''
window.pywebview = {api: {select_folder: async () => ({selected: "", cancelled: true, error: "picker failed"})}};
await $("#browseFolder").onclick();
assert.equal($("#toast").textContent, "picker failed", "folder picker failure was reported as cancellation");
assert.equal($("#browseFolder").disabled, false);
''')

    def test_pending_download_is_not_counted_as_a_completed_chunk(self):
        self.run_frontend(r'''
const chunk = {groups: [{id: "50", pages: [0]}], pageCount: 1};
const task = prepareDownloadTask([chunk], {}, null);
const ids = [];
fetchJson = async (_url, options) => {
  ids.push(JSON.parse(options.body).requestId);
  return ids.length === 1 ? {pending: true, retryAfterMs: 1} : {pages: 1, saved: ["saved.jpg"]};
};
await executeDownloadTask(task, () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100}), () => {});
assert.equal(ids.length, 2, "pending response was mistaken for successful publication");
assert.match(ids[0], /^[A-Za-z0-9_-]{16,96}$/);
assert.equal(ids[0], ids[1], "poll changed the download request identity");
assert.equal(task.savedCount, 1);
assert.equal(task.fileCount, 1);
''')

    def test_failed_chunk_reuses_its_request_identity_on_retry(self):
        self.run_frontend(r'''
const chunk = {groups: [{id: "50", pages: [0]}], pageCount: 1};
const task = prepareDownloadTask([chunk], {}, null);
const ids = [];
fetchJson = async (_url, options) => {
  ids.push(JSON.parse(options.body).requestId);
  if (ids.length === 1) throw new Error("connection lost");
  return {pages: 1, saved: ["saved.jpg"]};
};
const request = () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100});
await assert.rejects(executeDownloadTask(task, request, () => {}));
await executeDownloadTask(task, request, () => {});
assert.match(ids[0], /^[A-Za-z0-9_-]{16,96}$/);
assert.equal(ids[0], ids[1]);
''')

    def test_hover_preview_releases_work_when_page_is_hidden(self):
        self.run_frontend(r'''
let stops = 0;
let clears = 0;
ugoiraPreview.stop = () => { stops += 1; };
ugoiraPreview.clear = () => { clears += 1; };
document.hidden = true;
documentListeners.get("visibilitychange")();
assert.equal(stops, 1);
windowListeners.get("pagehide")();
assert.equal(clears, 1);
''')

    def test_prefetch_updates_navigation_without_replacing_displayed_results(self):
        self.run_frontend(r'''
let warm;
const originalSetTimeout = setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => {
  if (delay === 650) { warm = callback; return 123; }
  return originalSetTimeout(callback, delay, ...args);
};
fetchJson = async (url) => {
  const query = new URL(url, "http://localhost").searchParams;
  return query.get("prefetch") === "true"
    ? {items: [], page: 1, availablePages: [1,2,3,4], preloadedThrough: 4, hasMore: true}
    : {items: Array.from({length: 36}, (_, i) => artwork(String(101 + i))), page: 1, availablePages: [1], preloadedThrough: 1, hasMore: true, total: 36};
};
await search("cat");
const displayed = grid.innerHTML;
assert.equal(typeof warm, "function", "first result delivery never scheduled background prefetch");
await warm();
assert.equal(grid.innerHTML, displayed, "prefetch replaced visible results and their image URLs");
assert.equal(preloadedThrough, 4);
assert.equal(items[0].id, "101");
''')

    def test_new_search_cancels_prefetch_and_ignores_its_late_metadata(self):
        self.run_frontend(r'''
let warm, finish, signal;
const originalSetTimeout = setTimeout;
globalThis.setTimeout = (callback, delay, ...args) => {
  if (delay === 650) { warm = callback; return 123; }
  return originalSetTimeout(callback, delay, ...args);
};
fetchJson = async (url, options) => {
  const query = new URL(url, "http://localhost").searchParams;
  if (query.get("prefetch") === "true") {
    signal = options.signal;
    return new Promise(resolve => { finish = resolve; });
  }
  return {items: Array.from({length: 36}, (_, i) => artwork(i === 0 ? query.get("tag") : `extra-${i}`)), page: 1, availablePages: [1], preloadedThrough: 1, hasMore: true};
};
await search("101");
const pending = warm();
await search("102");
assert.equal(signal.aborted, true);
finish({items: [], availablePages: [1,2,3,4], preloadedThrough: 4, hasMore: false});
await pending;
assert.equal(items[0].id, "102");
assert.equal(preloadedThrough, 1, "old prefetch rewrote the new query's cached range");
assert.equal(searchHasMore, true);
cancelSearchPrefetch();
''')

    def test_account_switch_clears_old_restricted_views_and_retry_identity(self):
        self.run_frontend(r'''
const old = {...artwork("70"), restriction: "r18"};
items = [old];
choose(old);
lastKnownLoggedIn = true;
lastKnownAuthorizationGeneration = 10;
resumableSingleTask = {old: true};
resumableBatchTask = {old: true};
fetchJson = async () => ({loggedIn: true, authorizationGeneration: 11});
await syncAuthStatus();
assert.equal(selection.has(old.id), false, "account switch retained the previous account's restricted selection");
assert.equal(resumableSingleTask, null);
assert.equal(resumableBatchTask, null);
assert.ok($("#count").textContent.includes("已变更"));
''')

    def test_timeout_recovery_keeps_one_identity_until_publication_completes(self):
        self.run_frontend(r'''
const task = prepareDownloadTask([{pageCount: 1}], {}, null);
const ids = [];
fetchJson = async (_url, options) => {
  ids.push(JSON.parse(options.body).requestId);
  if (ids.length === 1) { const error = new Error("timeout"); error.timedOut = true; throw error; }
  if (ids.length === 2) return {pending: true, retryAfterMs: 1};
  return {pages: 1, saved: ["saved.jpg"]};
};
await executeDownloadTask(task, () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100}), () => {});
assert.equal(ids.length, 3);
assert.equal(new Set(ids).size, 1);
assert.equal(task.savedCount, 1);
''')

    def test_conflicted_request_identity_can_be_renewed_on_manual_retry(self):
        self.run_frontend(r'''
const task = prepareDownloadTask([{pageCount: 1}], {}, null);
const ids = [];
fetchJson = async (_url, options) => {
  ids.push(JSON.parse(options.body).requestId);
  if (ids.length === 1) { const error = new Error("conflict"); error.requestIdConflict = true; throw error; }
  return {pages: 1, saved: ["saved.jpg"]};
};
const request = () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100});
await assert.rejects(executeDownloadTask(task, request, () => {}));
await executeDownloadTask(task, request, () => {});
assert.notEqual(ids[0], ids[1]);
assert.equal(task.savedCount, 1);
''')

    def test_account_change_stops_the_next_chunk_of_an_old_task(self):
        self.run_frontend(r'''
lastKnownLoggedIn = true;
lastKnownAuthorizationGeneration = 10;
const task = prepareDownloadTask([{pageCount: 1}, {pageCount: 1}], {}, null);
let finish;
let requests = 0;
fetchJson = async (url) => {
  if (url === "/api/status") return {loggedIn: true, authorizationGeneration: 11};
  requests += 1;
  return new Promise(resolve => { finish = resolve; });
};
const pending = executeDownloadTask(task, () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100}), () => {});
await syncAuthStatus();
finish({pages: 1, saved: ["saved.jpg"]});
await assert.rejects(pending, /账户状态已变更/);
assert.equal(requests, 1, "old task silently continued its next chunk under the replacement account");
''')

    def test_initial_auth_status_does_not_discard_an_existing_public_retry(self):
        self.run_frontend(r'''
const task = prepareDownloadTask([{pageCount: 1}], {}, null);
lastKnownLoggedIn = null;
lastKnownAuthorizationGeneration = null;
fetchJson = async () => ({loggedIn: false, authorizationGeneration: 0});
await syncAuthStatus();
fetchJson = async () => ({pages: 1, saved: ["saved.jpg"]});
await executeDownloadTask(task, () => ({endpoint: "/api/pixiv/download", body: {}, timeout: 100}), () => {});
assert.equal(task.savedCount, 1);
''')

    def test_old_download_failure_does_not_offer_resume_after_account_change(self):
        for control in ("#download", "#batchDownload"):
            with self.subTest(control=control):
                self.run_frontend('const control = "' + control + '";\n' + r'''
const item = artwork("80");
items = [item];
activeArtworkId = item.id;
currentDetailItem = item;
choose(item);
lastKnownLoggedIn = true;
lastKnownAuthorizationGeneration = 10;
let fail;
fetchJson = async (url) => {
  if (url === "/api/status") return {loggedIn: true, authorizationGeneration: 11};
  return new Promise((_resolve, reject) => { fail = reject; });
};
const pending = $(control).onclick();
assert.equal(typeof fail, "function");
await syncAuthStatus();
fail(new Error("old connection failed"));
await pending;
assert.equal(resumableSingleTask, null);
assert.equal(resumableBatchTask, null);
assert.ok(!$("#toast").textContent.includes("只继续"), "old task offered resume after its account was replaced");
assert.ok($("#toast").textContent.includes("重新确认"));
''')


if __name__ == "__main__":
    unittest.main()
