import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("ugoira-preview.js", "selection-store.js", "artwork-detail-view.js", "download-history.js", "app.js")


@unittest.skipUnless(shutil.which("node"), "Node required")
class RoadmapFrontendTests(unittest.TestCase):
    def run_js(self, code):
        script = (ROOT / "tests/frontend_harness.js").read_text(encoding="utf-8")
        script += "\n" + "\n".join((ROOT / "web" / name).read_text(encoding="utf-8") for name in SOURCES)
        script += "\nconst assert = require('node:assert/strict');\n"
        script += "\n(async () => {" + code + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run([shutil.which("node")], input=script, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_paging_keeps_grid_and_failed_page_does_not_replace_committed_page(self):
        self.run_js("""
          activeTagQuery = 'cat';
          activeSearchFilters = readSearchFilters();
          resultSelectionEnabled = true;
          items = [{id:'123', title:'old', artist:'artist', tags:[], pages:1}];
          render();
          const oldGrid = grid.innerHTML;
          let rejectSearch;
          fetchJson = () => new Promise((resolve, reject) => { rejectSearch = reject; });
          const pending = search('cat', 2, activeSearchFilters);
          assert.equal(grid.innerHTML, oldGrid);
          assert.equal(resultSelectionEnabled, false);
          rejectSearch(new Error('offline'));
          await pending;
          assert.equal(currentPage, 1);
          assert.equal(grid.innerHTML, oldGrid);
          assert.equal(resultSelectionEnabled, true);
          assert.match(fakeElement('#count').textContent, /第 2 页/);
          const newQuery = search('dog', 1, activeSearchFilters);
          assert.notEqual(grid.innerHTML, oldGrid);
          rejectSearch(new Error('offline'));
          await newQuery;
        """)

    def test_recent_search_restores_all_filters_and_is_bounded(self):
        self.run_js("""
          const stored = new Map();
          globalThis.localStorage = {getItem:key => stored.get(key), setItem:(key,value) => stored.set(key,value)};
          const filters = {mode:'safe',workType:'ugoira',includeAi:true,fuzzy:true,startDate:'2026-01-01',endDate:'2026-01-03'};
          rememberSearch('cat', filters);
          rememberSearch('cat', filters);
          assert.equal(readRecentSearches().length, 1);
          assert.deepEqual(readRecentSearches()[0], {tag:'cat', filters});
          restoreSearch(readRecentSearches()[0]);
          assert.equal(fakeElement('#tag').value, 'cat');
          assert.deepEqual(readSearchFilters(), filters);
          for(let i=0;i<20;i++) rememberSearch('tag'+i, filters);
          assert.equal(readRecentSearches().length, 8);
          stored.set('moku.recentSearches', '{broken');
          assert.deepEqual(readRecentSearches(), []);
        """)
