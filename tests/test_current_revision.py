import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = (ROOT / "tests" / "frontend_harness.js").read_text(encoding="utf-8")
APP = "\n".join(
    (ROOT / "web" / name).read_text(encoding="utf-8")
    for name in ("ugoira-preview.js", "app.js")
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
  selectedArtworkIds.add(item.id);
  selectedArtworks.set(item.id, item);
  selectedPagesByArtwork.set(item.id, new Set(Array.from({length: item.pages}, (_, page) => page)));
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
assert.equal(selectedArtworkIds.has(old.id), false, "account switch retained the previous account's restricted selection");
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
