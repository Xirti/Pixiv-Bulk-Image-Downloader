from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import server
from pixiv_adapter import normalize_detail, publication_date
from search_service import SearchInputError, parse_search_date_range


ROOT = Path(__file__).resolve().parents[1]


class PublicationDateTests(unittest.TestCase):
    def test_validates_complete_real_and_ordered_dates(self):
        self.assertIsNone(parse_search_date_range())
        bounds = parse_search_date_range("2024-02-29", "2024-03-01")
        self.assertTrue(bounds.contains("2024-02-29"))
        self.assertTrue(bounds.contains("2024-03-01"))
        self.assertFalse(bounds.contains(""))
        self.assertFalse(bounds.contains("2024-03-02"))
        for start, end in (
            ("2024-01-01", ""), ("", "2024-01-01"),
            ("2023-02-29", "2024-01-01"), ("20240101", "2024-01-02"),
            ("2024-01-02", "2024-01-01"), ("wrong", "2024-01-01"),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(SearchInputError):
                parse_search_date_range(start, end)

    def test_publication_day_uses_japan_time_including_detail(self):
        timestamp = "2024-01-01T08:00:00-08:00"
        self.assertEqual(publication_date(timestamp), "2024-01-02")
        self.assertEqual(publication_date("2024-01-01T14:59:59Z"), "2024-01-01")
        self.assertEqual(publication_date("2024-01-01T15:00:00Z"), "2024-01-02")
        self.assertEqual(publication_date("2024-01-01"), "2024-01-01")
        self.assertEqual(publication_date("not a date"), "")
        item = normalize_detail({
            "id": "1", "xRestrict": 0, "isUnlisted": False,
            "isLoginOnly": False, "createDate": timestamp,
        }, [])
        self.assertEqual(item["date"], "2024-01-02")


class DateFilteredSearchTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    @staticmethod
    def row(number, day="2024-01-07", work_type=0):
        return {
            "id": str(number), "title": "cat", "userName": "artist", "userId": "9",
            "url": f"https://i.pximg.net/{number}.jpg", "tags": ["cat"],
            "pageCount": 1, "width": 10, "height": 10, "xRestrict": 0,
            "isUnlisted": False, "isMasked": False, "visibilityScope": 0,
            "illustType": work_type, "aiType": 1, "createDate": day,
        }

    def search(self, page=1, work_type="all", **kwargs):
        return server.search_pixiv_results(
            "cat", "safe", page, work_type, True, authorized=False,
            start_date="2024-01-01", end_date="2024-01-07", **kwargs,
        )

    def test_all_work_types_send_remote_dates_and_exclude_out_of_range_rows(self):
        for kind, block, number_type, remote_type in (
            ("all", "illustManga", 0, "all"),
            ("illustration", "illust", 0, "illust"),
            ("manga", "manga", 1, "manga"),
            ("ugoira", "illust", 2, "ugoira"),
        ):
            with self.subTest(kind=kind):
                server.reset_search_caches()

                def source(url, _cancel):
                    query = parse_qs(urlsplit(url).query)
                    self.assertEqual(query["scd"], ["2024-01-01"])
                    self.assertEqual(query["ecd"], ["2024-01-07"])
                    self.assertEqual(query["type"], [remote_type])
                    return {"body": {block: {"total": 3, "lastPage": 1, "data": [
                        self.row(1, "2024-01-01", number_type),
                        self.row(2, "2024-01-07", number_type),
                        self.row(3, "2024-01-08", number_type),
                    ]}}}

                with patch.object(server, "search_pixiv_json", side_effect=source) as remote:
                    result = self.search(work_type=kind)
                self.assertEqual({row["id"] for row in result["items"]}, {"1", "2"})
                self.assertFalse(result["hasMore"])
                self.assertEqual(remote.call_count, 1, "search continued below the user's date range")

    def test_paging_prefetch_and_backward_reload_keep_the_original_dates(self):
        def source(url, _cancel):
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["scd"], ["2024-01-01"])
            self.assertEqual(query["ecd"], ["2024-01-07"])
            page = int(query["p"][0])
            return {"body": {"illustManga": {"total": 360, "lastPage": 6, "data": [
                self.row(number) for number in range((page - 1) * 60 + 1, page * 60 + 1)
            ]}}}

        with patch.object(server, "search_pixiv_json", side_effect=source):
            first = self.search()
            warmed = self.search(prefetch=True)
            second = self.search(page=2)
            later = self.search(page=9)
            back = self.search()
        self.assertEqual(len(first["items"]), 36)
        self.assertEqual(warmed["preloadedThrough"], 4)
        self.assertEqual(len(second["items"]), 36)
        self.assertTrue(later["availablePages"][0] > 1)
        self.assertEqual([row["id"] for row in back["items"]], [row["id"] for row in first["items"]])

    def test_different_ranges_have_separate_caches(self):
        def source(url, _cancel):
            day = parse_qs(urlsplit(url).query)["ecd"][0]
            number = 1 if day == "2024-01-07" else 2
            return {"body": {"illustManga": {"total": 1, "lastPage": 1, "data": [self.row(number, day)]}}}

        with patch.object(server, "search_pixiv_json", side_effect=source) as remote:
            first = self.search()
            other = server.search_pixiv_results(
                "cat", "safe", 1, "all", True, authorized=False,
                start_date="2024-02-01", end_date="2024-02-07",
            )
            back = self.search()
        self.assertEqual(first["items"][0]["id"], "1")
        self.assertEqual(other["items"][0]["id"], "2")
        self.assertEqual(back["items"][0]["id"], "1")
        self.assertEqual(remote.call_count, 2)

    def test_exactly_full_last_page_does_not_offer_an_empty_next_page(self):
        data = {"body": {"illustManga": {"total": 36, "lastPage": 1, "data": [
            self.row(number) for number in range(1, 37)
        ]}}}
        with patch.object(server, "search_pixiv_json", return_value=data) as remote:
            result = self.search()
        self.assertEqual(len(result["items"]), 36)
        self.assertFalse(result["hasMore"])
        self.assertEqual(remote.call_count, 1)

    def test_high_density_split_and_empty_windows_never_escape_bounds(self):
        calls = []

        def source(url, _cancel):
            query = parse_qs(urlsplit(url).query)
            start, end = query["scd"][0], query["ecd"][0]
            calls.append((start, end))
            self.assertTrue("2024-01-01" <= start <= end <= "2024-01-07")
            dense = len(calls) == 1
            return {"body": {"illustManga": {"total": 601 if dense else 0, "lastPage": 0, "data": []}}}

        with patch.object(server, "search_pixiv_json", side_effect=source):
            result = self.search()
        self.assertEqual(calls, [
            ("2024-01-01", "2024-01-07"), ("2024-01-05", "2024-01-07"), ("2024-01-01", "2024-01-04"),
        ])
        self.assertFalse(result["hasMore"])

    def test_author_search_checks_beyond_the_first_batch_and_pid_honors_dates(self):
        def works(_user, ids):
            return [self.row(number, "2024-01-07" if int(number) <= 20 else "2025-01-01") for number in ids]

        with patch.object(server, "load_user_profile_ids", return_value=list(map(str, range(100, 0, -1)))), \
                patch.object(server, "load_user_profile_works", side_effect=works) as batches:
            result = server.search_pixiv_results(
                "uid:9", "safe", 1, "all", True, authorized=False,
                start_date="2024-01-01", end_date="2024-01-07",
            )
        self.assertEqual(len(result["items"]), 20)
        self.assertEqual(batches.call_count, 3)
        self.assertFalse(result["hasMore"])
        for day, count in (("2024-01-01", 1), ("2024-01-07", 1), ("2024-01-08", 0), ("", 0)):
            with patch.object(server, "pixiv_item_for_download", return_value={
                "id": "1", "restriction": "safe", "workType": "illustration", "date": day,
            }):
                direct = server.search_pixiv_results(
                    "pid:1", "safe", 1, "all", True, authorized=False,
                    start_date="2024-01-01", end_date="2024-01-07",
                )
            self.assertEqual(direct["total"], count)

    def test_invalid_dates_do_not_start_network_requests(self):
        with patch.object(server, "search_pixiv_json") as remote, self.assertRaises(SearchInputError):
            server.search_pixiv_results(
                "cat", "safe", 1, "all", True, authorized=False,
                start_date="2024-01-01", end_date="not-a-date",
            )
        remote.assert_not_called()


class PublicationDateFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_presets_validation_and_committed_filters_survive_input_changes(self):
        harness = (ROOT / "tests/frontend_harness.js").read_text(encoding="utf-8")
        app = "\n".join((ROOT / "web" / name).read_text(encoding="utf-8") for name in (
            "ugoira-preview.js", "selection-store.js", "artwork-detail-view.js", "download-history.js", "app.js",
        ))
        assertions = r'''
workspaceLoading = false;
syncSearchScopedControls();
const assert = require("node:assert/strict");
assert.deepEqual(publicationDateBounds("1", "", "", new Date("2024-03-01T15:00:00Z")), {
  startDate: "2024-03-02", endDate: "2024-03-02",
});
assert.deepEqual(publicationDateBounds("7", "", "", new Date("2024-03-01T15:00:00Z")), {
  startDate: "2024-02-25", endDate: "2024-03-02",
});
assert.deepEqual(publicationDateBounds("30", "", "", new Date("2024-03-01T14:59:59Z")), {
  startDate: "2024-02-01", endDate: "2024-03-01",
});
assert.deepEqual(publicationDateBounds("all", "old", "old"), {startDate: "", endDate: ""});
fakeElement("#datePreset").value = "custom";
fakeElement("#startDate").value = "2024-01-01";
fakeElement("#endDate").value = "2024-01-07";
syncDateInputs();
assert.equal(fakeElement("#customDates").hidden, false);
assert.equal(fakeElement("#startDate").required, true);
assert.equal(fakeElement("#startDate").disabled, false);
fakeElement("#endDate").value = "2023-01-01";
syncDateInputs();
assert.ok(fakeElement("#endDate").validationMessage);
fakeElement("#endDate").value = "2024-01-07";
syncDateInputs();
assert.equal(fakeElement("#endDate").validationMessage, "");
const calls = [];
fetchJson = async (url) => {
  calls.push(new URL(url, "http://localhost").searchParams);
  return {items: [], page: 1, availablePages: [1], total: 0, hasMore: false};
};
(async () => {
  await search("cat", 1, readSearchFilters());
  fakeElement("#startDate").value = "2025-01-01";
  fakeElement("#endDate").value = "2025-01-07";
  await search("cat", 2, activeSearchFilters);
  assert.equal(calls[1].get("startDate"), "2024-01-01");
  assert.equal(calls[1].get("endDate"), "2024-01-07");
  searchHasMore = true;
  preloadedThrough = 1;
  items = Array.from({length: 36}, () => ({}));
  scheduleSearchPrefetch("cat", currentPage, activeSearchFilters, searchGeneration);
  await new Promise((resolve) => setTimeout(resolve, 750));
  assert.equal(calls[2].get("prefetch"), "true");
  assert.equal(calls[2].get("startDate"), "2024-01-01");
  assert.equal(calls[2].get("endDate"), "2024-01-07");
  cancelSearchPrefetch();
  fakeElement("#datePreset").value = "all";
  syncDateInputs();
  assert.equal(fakeElement("#customDates").hidden, true);
  assert.equal(fakeElement("#startDate").required, false);
  assert.equal(fakeElement("#startDate").disabled, true);
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(
            [shutil.which("node")], input=harness + app + assertions,
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
