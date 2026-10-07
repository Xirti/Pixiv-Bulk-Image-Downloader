import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node required")
class WorkspaceSyncFrontendTests(unittest.TestCase):
    def test_retry_keeps_newer_metadata_edits_when_an_earlier_save_reply_is_lost(self):
        self.run_js(r'''
stored.basket = [row([0])];
stored.basket[0].item = {...item,pages:2};
await sync.restore();
const workingRequest = request;
let failReply;
request = async (url,options) => {
  if(!url.endsWith("/basket")) return workingRequest(url,options);
  await workingRequest(url,options);
  return new Promise((resolve,reject)=>{failReply=()=>reject(new Error("reply lost"));});
};
selection.remember({...selection.get(item.id).item,pages:3});
await new Promise(resolve=>setImmediate(resolve));
selection.setPage({...item,pages:4},3,true);
failReply();
await sync.settled();
assert.equal(stored.basket[0].item.pages,3);
request = workingRequest;
await sync.retry();
assert.equal(stored.basket[0].item.pages,4);
assert.deepEqual(stored.basket[0].pages,[0,3]);
assert.deepEqual([...selection.get(item.id).pages],[0,3]);
assert.equal(status.error,null);
''')

    def test_retry_rebases_newer_metadata_edits_on_an_acknowledged_save(self):
        self.run_js(r'''
stored.basket = [row([0])];
stored.basket[0].item = {...item,pages:2};
await sync.restore();
const workingRequest = request;
let finish, writes = 0;
request = async (url,options) => {
  if(!url.endsWith("/basket")) return workingRequest(url,options);
  if(++writes === 1) {
    const result = await workingRequest(url,options);
    return new Promise(resolve=>{finish=()=>resolve(result);});
  }
  throw new Error("offline");
};
selection.remember({...selection.get(item.id).item,pages:3});
await new Promise(resolve=>setImmediate(resolve));
selection.setPage({...item,pages:4},3,true);
finish();
await sync.settled();
assert.equal(stored.basket[0].item.pages,3);
assert.match(status.error.message,/offline/);
request = workingRequest;
await sync.retry();
assert.equal(stored.basket[0].item.pages,4);
assert.deepEqual(stored.basket[0].pages,[0,3]);
assert.deepEqual([...selection.get(item.id).pages],[0,3]);
assert.equal(status.error,null);
''')

    def test_metadata_refresh_does_not_roll_back_another_windows_new_page_count(self):
        self.run_js(r'''
stored.basket = [row([0])];
stored.basket[0].item = {...item,pages:2};
await sync.restore();
const workingRequest = request;
request = async (url,options) => {
  if(url.endsWith("/basket")) throw new Error("offline");
  return workingRequest(url,options);
};
selection.remember({...selection.get(item.id).item,title:"Local updated title"});
await sync.settled();
stored.basket = [row([0,2])];
stored.revision = 1;
request = workingRequest;
await sync.retry();
assert.deepEqual(stored.basket[0].pages,[0,2]);
assert.equal(stored.basket[0].item.pages,3);
assert.equal(stored.basket[0].item.title,"Local updated title");
''')

    def test_first_restore_does_not_apply_a_different_accounts_response(self):
        self.run_js(r'''
stored.authorizationGeneration = 2;
await sync.restore();
assert.equal(selection.size,0);
assert.equal(scopeChanges,1);
assert.equal(sync.scope,null);
''')

    def test_initial_read_failure_recovers_and_keeps_saved_and_new_choices(self):
        self.run_js(r'''
const workingRequest = request;
request = async () => {throw new Error("library busy");};
await sync.restore();
assert.equal(sync.ready,false);
assert.match(status.error.message,/library busy/);
request = workingRequest;
const other = {...item,id:"456",pages:1};
selection.choose([other],{context:{kind:"tags",value:"dog"},resultPage:2});
await sync.settled();
assert.equal(sync.ready,true);
assert.deepEqual(stored.basket.map(row=>row.id),["123","456"]);
assert.deepEqual([...selection.get("123").pages],[0]);
assert.equal(selection.get("456").context.value,"dog");
assert.equal(status.error,null);
''')

    def test_late_old_save_reply_cannot_change_new_account_state(self):
        self.run_js(r'''
await sync.restore();
const workingRequest = request;
let finish;
request = (url,options) => url.endsWith("/basket")
  ? new Promise(resolve=>{finish=resolve;}) : workingRequest(url,options);
selection.setPage(item,1,true);
const oldSave = sync.settled();
await new Promise(resolve=>setImmediate(resolve));
sync.reset();
selection.revoke([item.id]);
stored = {...stored,scope:"account-B",revision:50,basket:[row([2])]};
request = workingRequest;
await sync.restore();
finish({revision:1});
await oldSave;
assert.equal(sync.scope,"account-B");
assert.equal(status.revision,50);
assert.deepEqual([...selection.get(item.id).pages],[2]);
selection.setPage(item,1,true);
await sync.settled();
assert.deepEqual(stored.basket[0].pages,[1,2]);
assert.equal(stored.revision,51);
''')

    def test_edit_made_while_save_is_pending_is_saved_after_acknowledgement(self):
        self.run_js(r'''
await sync.restore();
const workingRequest = request;
let finish;
request = async (url,options) => {
  if(url.endsWith("/basket") && !finish) {
    const result = await workingRequest(url,options);
    return new Promise(resolve=>{finish=()=>resolve(result);});
  }
  return workingRequest(url,options);
};
selection.setPage(item,1,true);
await new Promise(resolve=>setImmediate(resolve));
selection.setPage(item,0,false);
finish();
await sync.settled();
assert.equal(stored.revision,2);
assert.deepEqual(stored.basket[0].pages,[1]);
assert.equal(status.error,null);
''')

    def run_js(self, assertions):
        source = "\n".join((ROOT / "web" / name).read_text(encoding="utf-8")
                           for name in ("selection-store.js", "workspace-sync.js"))
        fixture = r'''
const assert = require("node:assert/strict");
const item = {id:"123",source:"pixiv",pages:3,restriction:"safe",title:"Cat",artist:"Artist",tags:[]};
const row = pages => ({id:item.id,item,pages,context:{kind:"tags",value:"cat"},resultPage:1});
let stored = {scope:"public",revision:0,basket:[row([0])],tasks:[],recent:[]};
let status, locked = false, scopeChanges = 0;
let authorizationGeneration = 1;
let request = async (url, options) => {
  if(url === "/api/workspace") return structuredClone(stored);
  assert.equal(url, "/api/workspace/basket");
  const body = JSON.parse(options.body);
  assert.equal(body.revision, stored.revision);
  assert.equal(body.scope, stored.scope);
  stored.basket = body.basket;
  return {revision:++stored.revision};
};
const selection = createSelectionStore({maxPages:1000,onChange:()=>sync.changed()});
const sync = createWorkspaceSync({request:(...args)=>request(...args),readBasket:()=>selection.snapshot(),
  applyWorkspace:data=>assert.equal(selection.restore(data.basket,{allowOverflow:true}).accepted,true),
  canRestore:()=>!locked,onStatus:value=>{status=value;},onScopeChanged:()=>{scopeChanges++;},
  getAuthorizationGeneration:()=>authorizationGeneration});
'''
        script = source + fixture + "\n(async()=>{" + assertions + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
        result = subprocess.run([shutil.which("node")], input=script, text=True, encoding="utf-8",
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_retry_preserves_another_windows_new_pages_when_last_local_page_is_cancelled(self):
        self.run_js(r'''
await sync.restore();
const workingRequest = request;
request = async (url, options) => {
  if(url.endsWith("/basket")) throw new Error("offline");
  return workingRequest(url, options);
};
selection.setPage(item,0,false);
await sync.settled();
assert.equal(selection.size,0);
stored.basket = [row([0,1])];
stored.revision = 1;
request = workingRequest;
await sync.retry();
assert.deepEqual(stored.basket[0]?.pages,[1]);
assert.deepEqual([...selection.get(item.id).pages],[1]);
assert.equal(status.error,null);
''')
