from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import json
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import server


class SearchSnapshotStreamTests(unittest.TestCase):
    """Exercise result behavior through the real search/Pixiv adapter seam."""

    def setUp(self):
        server.clear_authorized_state()
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()
        server.clear_authorized_state()

    @staticmethod
    def row(number, tags, day="2026-10-01", *, restriction=0, kind=0):
        return {
            "id": str(number), "title": "fixture", "userName": "artist", "userId": "9",
            "xRestrict": restriction, "isUnlisted": False,
            "url": f"https://i.pximg.net/{number}.jpg", "tags": tags,
            "createDate": day, "pageCount": 1, "illustType": kind, "aiType": 1,
        }

    def remote(self, sources):
        def response(url, _cancel):
            query = parse_qs(urlsplit(url).query)
            tag, mode = query["word"][0], query["mode"][0]
            rows = sources.get((tag, mode), sources.get(tag, []))
            rows = [row for row in rows
                    if query["scd"][0] <= row["createDate"][:10] <= query["ecd"][0]]
            page = int(query["p"][0])
            block = {"all": "illustManga", "illust": "illust", "manga": "manga", "ugoira": "illust"}[query["type"][0]]
            return {"body": {block: {
                "total": len(rows), "lastPage": (len(rows) + 59) // 60,
                "data": rows[(page - 1) * 60:page * 60],
            }}}
        return response

    def search(self, tag, page=1, **kwargs):
        return server.search_pixiv_results(
            tag, "safe", page, "all", True, authorized=False,
            start_date="2026-10-01", end_date="2026-10-07", **kwargs,
        )

    def test_deep_and_pages_do_not_repeat_works_from_a_lagging_broader_tag(self):
        common = [self.row(10000 - i, ["cat", "night"]) for i in range(600)]
        unrelated = [self.row(20000 - i, ["cat"], "2026-10-06") for i in range(540)]
        displayed = []
        with patch.object(server, "search_pixiv_json", side_effect=self.remote({
            "cat": unrelated + common, "night": common,
        })):
            for page in range(1, 36):
                result = self.search("cat;night", page)
                displayed.extend(row["id"] for row in result["items"])
                if not result["hasMore"] and page * 36 >= result["total"]:
                    break
        self.assertEqual(displayed, [str(10000 - i) for i in range(600)])
        self.assertEqual(result["total"], 600)

    def test_full_publication_timestamps_show_a_hot_tags_first_page_after_one_request(self):
        latest = datetime.fromisoformat("2026-10-06T12:10:00+09:00")
        rows = [self.row(10000 - index, ["cat"], (latest - timedelta(seconds=index)).isoformat())
                for index in range(600)]
        with patch.object(server, "search_pixiv_json", side_effect=self.remote({"cat": rows})) as remote:
            first = self.search("cat")
        self.assertEqual([row["id"] for row in first["items"]],
                         [str(10000 - index) for index in range(36)])
        self.assertTrue(all(row["date"] == "2026-10-06" for row in first["items"]))
        self.assertEqual(remote.call_count, 1)

    def test_cross_alias_same_second_ties_are_confirmed_before_committing_id_order(self):
        same_second = "2026-10-06T12:00:00+09:00"
        tied = [self.row(10000 - index, ["rem"], same_second) for index in range(60)]
        older = [self.row(9000 - index, ["rem"], "2026-10-06T11:00:00+09:00") for index in range(60)]
        aliases = [self.row(20000 - index, ["蕾姆"], same_second) for index in range(8)]
        aliases += [{**row, "tags": ["rem", "蕾姆"]} for row in tied[:4]]
        displayed = []
        with patch.object(server, "search_pixiv_json", side_effect=self.remote({
            "rem": tied + older, "蕾姆": aliases, "レム": [],
        })):
            for page in range(1, 5):
                displayed.extend(row["id"] for row in self.search("rem", page, fuzzy=True)["items"])
        self.assertEqual(displayed, [str(20000 - index) for index in range(8)]
                         + [str(10000 - index) for index in range(60)]
                         + [str(9000 - index) for index in range(60)])

    def test_a_day_only_source_keeps_same_day_timestamp_candidates_pending_until_confirmed(self):
        unknown = [self.row(10000 - index, ["rem"], "2026-10-06") for index in range(60)]
        later = [self.row(30000 - index, ["rem"], "2026-10-06T13:00:00+09:00") for index in range(60)]
        earlier = [self.row(20000 - index, ["蕾姆"], "2026-10-06T12:00:00+09:00") for index in range(60)]
        with patch.object(server, "search_pixiv_json", side_effect=self.remote({
            "rem": unknown + later, "蕾姆": earlier, "レム": [],
        })):
            first = self.search("rem", fuzzy=True)
        self.assertEqual([row["id"] for row in first["items"]],
                         [str(30000 - index) for index in range(36)])

    def test_alias_pages_are_globally_newest_first_without_reordering_displayed_pages(self):
        older = [self.row(10000 - i, ["rem"]) for i in range(60)]
        newer = [self.row(20000 - i, ["蕾姆"], "2026-10-06") for i in range(120)]
        displayed = []
        with patch.object(server, "search_pixiv_json", side_effect=self.remote({
            "rem": older, "蕾姆": newer, "レム": [],
        })):
            first = self.search("rem", fuzzy=True)
            displayed.extend(row["id"] for row in first["items"])
            for page in range(2, 6):
                result = self.search("rem", page, fuzzy=True)
                displayed.extend(row["id"] for row in result["items"])
            revisited = self.search("rem", fuzzy=True)
        self.assertEqual(displayed, [str(20000 - i) for i in range(120)]
                         + [str(10000 - i) for i in range(60)])
        self.assertEqual([row["id"] for row in revisited["items"]],
                         [row["id"] for row in first["items"]])

    def test_dense_day_with_a_small_budget_can_continue_the_same_page_then_prefetch(self):
        rows = [self.row(10000 - i, ["cat"]) for i in range(600)]
        with patch.object(server, "MAX_HISTORY_REQUESTS", 2), patch.object(
            server, "search_pixiv_json", side_effect=self.remote({"cat": rows}),
        ) as remote:
            result = self.search("cat")
            self.assertEqual(result["items"], [])
            self.assertTrue(result["hasMore"])
            self.assertTrue(result["budgetExhausted"])
            self.assertTrue(result["orderingPending"])
            for _ in range(8):
                calls_before = remote.call_count
                result = self.search("cat")
                self.assertLessEqual(remote.call_count - calls_before, 2)
                if result["items"]:
                    break
            self.assertEqual([row["id"] for row in result["items"]],
                             [str(10000 - i) for i in range(36)])
            warmed = self.search("cat", prefetch=True)
            second = self.search("cat", 2)
        self.assertEqual(warmed["preloadedThrough"], 4)
        self.assertEqual([row["id"] for row in second["items"]],
                         [str(10000 - i) for i in range(36, 72)])

    def test_research_uses_a_new_snapshot_without_changing_another_windows_pages(self):
        rows = [self.row(10000 - i, ["cat"], "2026-10-06") for i in range(48)]
        sources = {"cat": rows}
        with patch.object(server, "search_pixiv_json", side_effect=self.remote(sources)):
            first = self.search("cat", snapshot_id="snapshot-window-old")
            sources["cat"] = [self.row(20000, ["cat"], "2026-10-07"), *rows]
            fresh = self.search("cat", snapshot_id="snapshot-window-new")
            old_second = self.search("cat", 2, snapshot_id="snapshot-window-old")
            old_first = self.search("cat", snapshot_id="snapshot-window-old")
        self.assertEqual(fresh["items"][0]["id"], "20000")
        self.assertEqual([row["id"] for row in old_second["items"]],
                         [str(10000 - i) for i in range(36, 48)])
        self.assertEqual([row["id"] for row in old_first["items"]],
                         [row["id"] for row in first["items"]])

    def test_revisiting_an_evicted_page_keeps_the_original_snapshot_after_new_uploads(self):
        rows = [self.row(10000 - i, ["cat"]) for i in range(600)]
        sources = {"cat": rows}
        with patch.object(server, "search_pixiv_json", side_effect=self.remote(sources)):
            first = self.search("cat", snapshot_id="snapshot-window-old")
            self.search("cat", 10, snapshot_id="snapshot-window-old")
            sources["cat"] = [self.row(20000, ["cat"], "2026-10-07"), *rows]
            revisited = self.search("cat", snapshot_id="snapshot-window-old")
        self.assertEqual([row["id"] for row in revisited["items"]],
                         [row["id"] for row in first["items"]])

    def test_temporary_snapshot_indexes_are_cleaned_on_lru_idle_and_reset(self):
        with tempfile.TemporaryDirectory(prefix="moku-index-test-") as directory, patch.object(
            tempfile, "tempdir", directory,
        ), patch.object(server, "search_pixiv_json", side_effect=self.remote({
            "cat": [self.row(100, ["cat"])],
        })):
            for index in range(server.MAX_SEARCH_SESSIONS + 2):
                self.search("cat", snapshot_id=f"snapshot-lru-{index:04d}")
            self.assertEqual(len(list(Path(directory).iterdir())), server.MAX_SEARCH_SESSIONS)
            later = server.time.monotonic() + server.SEARCH_SESSION_IDLE_SECONDS + 1
            with patch.object(server.time, "monotonic", return_value=later):
                self.search("cat", snapshot_id="snapshot-after-idle")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            server.reset_search_caches()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_authorization_revocation_deletes_restricted_snapshot_indexes(self):
        server.mark_authorized_session()
        with tempfile.TemporaryDirectory(prefix="moku-index-test-") as directory, patch.object(
            tempfile, "tempdir", directory,
        ), patch.object(server, "search_pixiv_json", side_effect=self.remote({
            ("cat", "r18"): [self.row(100, ["cat"], restriction=1)],
        })):
            result = server.search_pixiv_results(
                "cat", "r18", 1, "all", True, authorized=True,
                authorization_epoch=server.authorization_generation(), snapshot_id="snapshot-restricted",
                start_date="2026-10-01", end_date="2026-10-07",
            )
            self.assertEqual([item["id"] for item in result["items"]], ["100"])
            files = list(Path(directory).glob("*/identities.sqlite3"))
            self.assertEqual(len(files), 1)
            self.assertNotIn(b"token=", files[0].read_bytes())
            self.assertNotIn(server.REQUEST_TOKEN.encode(), files[0].read_bytes())
            server.clear_authorized_state()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancelled_continuation_can_retry_without_losing_pending_rows_and_then_cleanup(self):
        rows = [self.row(10000 - i, ["cat"]) for i in range(120)]
        cancel = threading.Event()
        working_remote = self.remote({"cat": rows})

        def cancelled_remote(url, event):
            value = working_remote(url, event)
            cancel.set()
            return value

        with tempfile.TemporaryDirectory(prefix="moku-index-test-") as directory, patch.object(
            tempfile, "tempdir", directory,
        ), patch.object(server, "MAX_HISTORY_REQUESTS", 1):
            with patch.object(server, "search_pixiv_json", side_effect=working_remote):
                self.search("cat", snapshot_id="snapshot-cancelled")
            with patch.object(server, "search_pixiv_json", side_effect=cancelled_remote):
                with self.assertRaises(server.SearchCancelledError):
                    self.search("cat", snapshot_id="snapshot-cancelled", cancel_event=cancel)
            with patch.object(server, "search_pixiv_json", side_effect=working_remote):
                result = self.search("cat", snapshot_id="snapshot-cancelled")
            self.assertEqual([item["id"] for item in result["items"]],
                             [str(10000 - i) for i in range(36)])
            server.reset_search_caches()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_http_snapshot_identity_refreshes_only_the_new_windows_search(self):
        rows = [self.row(10000 - i, ["cat"], "2026-10-06") for i in range(48)]
        sources = {"cat": rows}
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def query(snapshot, page=1):
            params = urllib.parse.urlencode({"tag": "cat", "page": page, "includeAi": "true",
                "snapshotId": snapshot, "startDate": "2026-10-01", "endDate": "2026-10-07"})
            request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/pixiv/search?{params}",
                                             headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN})
            with opener.open(request, timeout=5) as response:
                return json.load(response)

        try:
            with patch.object(server, "session_cookie_header", return_value={}), patch.object(
                server, "search_pixiv_json", side_effect=self.remote(sources),
            ):
                old = query("snapshot-http-old")
                sources["cat"] = [self.row(20000, ["cat"], "2026-10-07"), *rows]
                fresh = query("snapshot-http-new")
                old_second = query("snapshot-http-old", 2)
            self.assertEqual(old["snapshotId"], "snapshot-http-old")
            self.assertEqual(old["availablePages"], [1, 2], "The final partial page must remain reachable")
            self.assertEqual(fresh["items"][0]["id"], "20000")
            self.assertEqual([item["id"] for item in old_second["items"]],
                             [str(10000 - i) for i in range(36, 48)])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)


@unittest.skipUnless(shutil.which("node"), "Node required")
class SearchSnapshotFrontendTests(unittest.TestCase):
    def run_js(self, assertions):
        root = Path(__file__).resolve().parents[1]
        source = (root / "tests/frontend_harness.js").read_text(encoding="utf-8")
        source += "\n" + "\n".join((root / "web" / name).read_text(encoding="utf-8") for name in (
            "ugoira-preview.js", "selection-store.js", "artwork-detail-view.js",
            "download-history.js", "workspace-sync.js", "app.js",
        ))
        source += "\nconst assert = require('node:assert/strict');\n"
        source += "(async()=>{" + assertions + "\n})().catch(error=>{console.error(error);process.exitCode=1;})"
        source += ".finally(()=>{cancelSearchPrefetch();if(taskDockTimer)clearTimeout(taskDockTimer);});"
        result = subprocess.run([shutil.which("node")], input=source, text=True, encoding="utf-8",
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_search_and_recent_restore_are_fresh_but_navigation_and_prefetch_share_snapshot(self):
        self.run_js(r'''
const calls = [], timers = [];
const realTimeout = globalThis.setTimeout, realClear = globalThis.clearTimeout;
globalThis.setTimeout = (fn,ms,...args) => {
  if(ms !== 650) return realTimeout(fn,ms,...args);
  const timer = {fn,cancelled:false};timers.push(timer);return timer;
};
globalThis.clearTimeout = timer => timer?.fn ? timer.cancelled=true : realClear(timer);
fetchJson = async url => {
  const parsed = new URL(url,"http://localhost");
  if(parsed.pathname !== "/api/pixiv/search") return {pages:{}};
  const query = parsed.searchParams; calls.push(query);
  const page = Number(query.get("page"));
  return {items:Array.from({length:36},(_,index)=>({id:String(1000-index),pages:1,
    restriction:"safe",title:"Cat",artist:"Artist",tags:["cat"],thumb:"/image"})),
    page,total:180,perPage:36,availablePages:[1,2,3,4],hasMore:true,preloadedThrough:page,
    snapshotId:query.get("snapshotId")};
};
const filters = {mode:"safe",workType:"all",includeAi:false,fuzzy:false};
await search("cat",1,filters);
const first = calls[0].get("snapshotId");
assert.ok(first && first.length >= 16,"manual search must establish a snapshot");
navigateToPage(2);
await new Promise(resolve=>setImmediate(resolve));
assert.equal(calls[1].get("snapshotId"),first,"pagination continues the snapshot");
assert.notEqual(calls[0].get("requestId"),calls[1].get("requestId"));
await search("cat",1,filters);
const second = calls[2].get("snapshotId");
assert.notEqual(second,first,"repeating the same query refreshes its snapshot");
await timers.filter(timer=>!timer.cancelled).pop().fn();
assert.equal(calls[3].get("prefetch"),"true");
assert.equal(calls[3].get("snapshotId"),second,"prefetch shares the committed snapshot");
restoreSearch({tag:"cat",filters});
await new Promise(resolve=>setImmediate(resolve));
assert.notEqual(calls[4].get("snapshotId"),second,"restoring a recent search is a new search");
''')

    def test_a_budget_limited_ordering_prefix_offers_continue_not_no_matches(self):
        self.run_js(r'''
fetchJson = async url => {
  if(!url.startsWith("/api/pixiv/search?")) return {pages:{}};
  return {items:[],page:1,total:0,availablePages:[],hasMore:true,budgetExhausted:true,orderingPending:true};
};
await search("cat",1,{mode:"safe",workType:"all",includeAi:false,fuzzy:false});
assert.match(grid.innerHTML,/确认.*排序/);
assert.match(grid.innerHTML,/继续加载本页/);
assert.ok(!grid.innerHTML.includes("尚未找到"));
assert.match($("#count").textContent,/排序/);
''')


if __name__ == "__main__":
    unittest.main()
