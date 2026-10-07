import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("ugoira-preview.js", "selection-store.js", "artwork-detail-view.js", "download-history.js", "app.js")


@unittest.skipUnless(shutil.which("node"), "Node required")
class RoadmapFrontendTests(unittest.TestCase):
    def test_preview_refresh_does_not_rewrite_unchanged_basket_choices(self):
        self.run_js("""
          workspaceReady = true;
          workspaceScope = 'public';
          const item = {id:'123',source:'pixiv',pages:3,restriction:'safe',title:'Cat',artist:'Artist',tags:['cat'],workType:'illustration',thumb:'/old'};
          let writes = 0;
          fetchJson = async (url, options) => {
            assert.equal(url, '/api/workspace/basket');
            writes++;
            return {revision:writes};
          };
          selection.choose([item], {context:{kind:'tags',value:'cat'},resultPage:1});
          await basketSaveChain;
          assert.equal(writes, 1);
          for(let i=0;i<5;i++) rememberArtworkDetail({...item,thumb:'/fresh-'+i,bookmarks:10+i,pageImages:[{regular:'/image-'+i}]});
          await basketSaveChain;
          assert.equal(writes, 1, 'refreshing only image URLs and metrics changed the saved basket');
          selection.setPage(item, 1, false);
          await basketSaveChain;
          assert.equal(writes, 2);
          assert.deepEqual([...selection.get('123').pages], [0,2]);
        """)

    def test_legacy_over_capacity_basket_can_be_restored_and_trimmed_without_losing_choices(self):
        self.run_js("""
          lastKnownLoggedIn = true;
          const privateItem = {id:'123',pages:600,restriction:'r18',title:'A',artist:'artist',tags:[]};
          const publicItem = {id:'456',pages:900,restriction:'safe',title:'B',artist:'artist',tags:[]};
          let lastSaved = 0;
          fetchJson = async (url, options) => {
            if(url === '/api/workspace') return {scope:'account-A',revision:1,tasks:[],recent:[],basket:[
              {id:'123',item:privateItem,pages:Array.from({length:600},(_,i)=>i)},
              {id:'456',item:publicItem,pages:Array.from({length:900},(_,i)=>i)}]};
            if(url === '/api/workspace/basket') {
              const total = JSON.parse(options.body).basket.reduce((count,row)=>count+row.pages.length,0);
              if(total>1000) { const error=new Error('采集篮超过容量'); error.basketCapacity=true; throw error; }
              lastSaved=total; return {revision:2};
            }
            throw new Error('Unexpected '+url);
          };
          await restoreWorkspace();
          assert.equal(workspaceReady, true);
          assert.equal(selection.pageCount, 1500);
          assert.equal(selection.setPage({...publicItem,pages:901},900,true).reason, 'capacity');
          selection.setPage(publicItem,899,false);
          await basketSaveChain;
          assert.equal(workspaceReady, true);
          assert.equal(selection.pageCount, 1499);
          selection.remove(['123']);
          await basketSaveChain;
          assert.equal(lastSaved, 899);
          assert.equal(selection.pageCount, 899);
          clearTimeout(taskDockTimer);
        """)

    def test_account_change_waits_for_download_unlock_before_restoring(self):
        self.run_js("""
          const item = {id:'123',pages:3,restriction:'safe',title:'Cat',artist:'Artist',tags:[]};
          selection.restore([{id:'123',item,pages:[0,1,2],context:{kind:'author',value:'private-origin'}}]);
          workspaceReady = true;
          workspaceScope = 'account-A';
          lastKnownLoggedIn = true;
          lastKnownAuthorizationGeneration = 1;
          setBasketSelectionLocked(true);
          let loads = 0;
          fetchJson = async url => {
            if(url === '/api/status') return {loggedIn:true,authorizationGeneration:2};
            if(url === '/api/workspace') { loads++; return {scope:'account-B',revision:1,
              basket:[{id:'123',item,pages:[1],context:{kind:'tags',value:'cat'}}],recent:[],tasks:[]}; }
            throw new Error('Unexpected '+url);
          };
          await syncAuthStatus();
          assert.equal(loads, 0);
          assert.equal(workspaceLoading, true);
          assert.equal(selection.size, 0, 'download locking must not retain the previous account choices');
          assert.equal(workspaceScope, null);
          setBasketSelectionLocked(false);
          for(let i=0;i<10 && !workspaceReady;i++) await new Promise(resolve => setTimeout(resolve,0));
          assert.equal(loads, 1);
          assert.equal(workspaceReady, true);
          assert.equal(workspaceScope, 'account-B');
          assert.deepEqual([...selection.get('123').pages], [1]);
          assert.equal(selection.get('123').context.value, 'cat');
          clearTimeout(taskDockTimer);
        """)

    def test_failed_new_account_restore_does_not_leave_old_safe_private_choices_visible(self):
        self.run_js("""
          lastKnownLoggedIn = true;
          lastKnownAuthorizationGeneration = 1;
          let loads = 0;
          fetchJson = async url => {
            if(url === '/api/status') return {loggedIn:true,authorizationGeneration:2};
            if(url === '/api/workspace' && ++loads === 1) return {
              scope:'account-A',revision:2,tasks:[],recent:[],basket:[{
                id:'123',pages:[0,1,2],item:{id:'123',pages:3,restriction:'safe',title:'Cat',artist:'Artist',tags:[]},
                context:{kind:'author',value:'private-origin'},resultPage:8,archived:true
              }]
            };
            throw new Error('workspace unavailable');
          };
          await restoreWorkspace();
          assert.equal(selection.pageCount, 3);
          await syncAuthStatus();
          assert.equal(loads, 2);
          assert.equal(workspaceReady, false);
          assert.equal(workspaceLoading, false);
          assert.equal(selection.size, 0, 'the old account choices remain visible after failed restore');
          assert.equal(workspaceScope, null);
          assert.equal(fakeElement('#openBatch').disabled, true);
          assert.equal(fakeElement('#batchDownload').disabled, true);
          clearTimeout(taskDockTimer);
        """)

    def test_account_change_during_initial_restore_ignores_late_old_response(self):
        self.run_js("""
          lastKnownLoggedIn = true;
          lastKnownAuthorizationGeneration = 1;
          let finishOldRestore;
          let loads = 0;
          fetchJson = async url => {
            if(url === '/api/status') return {loggedIn:true,authorizationGeneration:2};
            if(url === '/api/workspace' && ++loads === 1) return new Promise(resolve => { finishOldRestore = resolve; });
            if(url === '/api/workspace') return {scope:'account-B',revision:2,basket:[],recent:[{tag:'B',filters:{mode:'safe'}}],tasks:[]};
            throw new Error('Unexpected '+url);
          };
          const oldRestore = restoreWorkspace();
          await syncAuthStatus();
          assert.equal(workspaceScope, 'account-B');
          finishOldRestore({scope:'account-A',revision:1,basket:[],recent:[{tag:'A',filters:{mode:'r18'}}],tasks:[]});
          await oldRestore;
          assert.equal(workspaceReady, true);
          assert.equal(workspaceScope, 'account-B');
          assert.equal(readRecentSearches()[0].tag, 'B');
          clearTimeout(taskDockTimer);
        """)

    def test_account_change_clears_old_recent_searches_and_loads_current_tasks(self):
        self.run_js("""
          workspaceReady = true;
          lastKnownLoggedIn = true;
          lastKnownAuthorizationGeneration = 1;
          savedRecentSearches = [{tag:'private-A',filters:{mode:'r18'}}];
          let loads = 0;
          fetchJson = async url => {
            if(url === '/api/status') return {loggedIn:true,authorizationGeneration:2};
            if(url === '/api/workspace') { loads++; return {scope:'account-B',revision:2,basket:[],recent:[{tag:'public-B',filters:{mode:'safe'}}],tasks:[]}; }
            throw new Error('Unexpected '+url);
          };
          await syncAuthStatus();
          assert.equal(loads, 1);
          assert.equal(readRecentSearches()[0].tag, 'public-B');
          clearTimeout(taskDockTimer);
        """)

    def test_restored_basket_keeps_exact_selection_and_queue_waits_for_resume(self):
        self.run_js("""
          let downloads = 0;
          const saved = {id:'saved-task-000001', kind:'batch', taskOptions:{quality:'original',ugoiraFormat:'gif',saveRoot:'C:/art',createFolder:false,groupArtworks:false,skipExisting:true},
            remainingChunks:[{requestId:'saved-chunk-000001',groups:[{id:'123',pages:[2]}],pageCount:1,context:{kind:'tags',value:'cat'}}],
            savedCount:2,fileCount:2,skippedCount:0,firstSaved:'cat.png',completedBatches:1,totalBatches:2};
          fetchJson = async (url, options) => {
            if(url === '/api/workspace') return {revision:4,basket:[{id:'123',pages:[2,8],item:{id:'123',pages:100,title:'Cat',artist:'artist',restriction:'safe',tags:[]},context:{kind:'tags',value:'cat'},resultPage:8,archived:true}],tasks:[saved]};
            if(url === '/api/workspace/task') return {ok:true};
            if(url === '/api/workspace/task/delete') return {ok:true};
            if(url.startsWith('/api/library/catalog')) return {pages:{}};
            if(url === '/api/pixiv/batch-download') {
              downloads++;
              const body = JSON.parse(options.body);
              assert.equal(body.quality, 'original');
              assert.equal(body.ugoiraFormat, 'gif');
              assert.equal(body.requestId, 'saved-chunk-000001');
              assert.deepEqual(body.groups, [{id:'123',pages:[2]}]);
              return {pages:1,saved:['cat2.png']};
            }
            throw new Error('Unexpected '+url);
          };
          await restoreWorkspace();
          assert.equal(downloads, 0);
          assert.deepEqual([...selection.get('123').pages], [2,8]);
          assert.equal(selection.pageCount, 2);
          assert.equal(pendingTasks.size, 1);
          await resumeSavedTask(pendingTasks.get(saved.id));
          assert.equal(downloads, 1);
          assert.equal(pendingTasks.size, 0);
          assert.equal(selection.pageCount, 2);
          clearTimeout(taskDockTimer);
        """)

    def test_failed_chunk_retains_its_identity_and_saved_progress(self):
        self.run_js("""
          workspaceReady = true;
          let storedTask;
          let calls = 0;
          fetchJson = async (url, options) => {
            if(url === '/api/workspace/task') { storedTask = JSON.parse(options.body).task; return {ok:true}; }
            if(url === '/api/workspace/task/delete') return {ok:true};
            if(url === '/api/pixiv/batch-download') { if(++calls === 2) throw new Error('offline'); return {pages:1,saved:['saved.png']}; }
            throw new Error('Unexpected '+url);
          };
          const task = prepareDownloadTask([{groups:[{id:'123',pages:[0]}],pageCount:1,context:{kind:'tags',value:'cat'}},{groups:[{id:'123',pages:[1]}],pageCount:1,context:{kind:'tags',value:'cat'}}], readDownloadOptions(), null);
          const secondId = task.remainingChunks[1].requestId;
          await assert.rejects(executeDownloadTask(task, chunk => savedTaskRequest(task, chunk), () => {}), /offline/);
          assert.equal(task.savedCount, 1);
          assert.equal(task.remainingChunks.length, 1);
          assert.equal(storedTask.remainingChunks[0].requestId, secondId);
          assert.equal(storedTask.completedBatches, 1);
          await executeDownloadTask(task, chunk => savedTaskRequest(task, chunk), () => {});
          assert.equal(task.savedCount, 2);
          assert.equal(task.remainingChunks.length, 0);
          assert.equal(pendingTasks.size, 0);
        """)

    def run_js(self, code):
        script = (ROOT / "tests/frontend_harness.js").read_text(encoding="utf-8")
        script += "\n" + "\n".join((ROOT / "web" / name).read_text(encoding="utf-8") for name in SOURCES)
        script += "\nworkspaceLoading = false; syncSearchScopedControls(); const assert = require('node:assert/strict');\n"
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
          assert.equal(stored.size, 0);
          savedRecentSearches = [];
          assert.deepEqual(readRecentSearches(), []);
        """)
