const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/theme.js'), 'utf8');
function setup({supported=true,reduced=false,readyFailure=false}={}) {
  const root={dataset:{},animate(frames,options){api.frames=frames;api.options=options;return {finished:Promise.resolve(),cancel(){api.cancelled=true;}};}};
  const button={attrs:{},setAttribute(k,v){this.attrs[k]=v;},getBoundingClientRect(){return {left:900,top:20,width:80,height:40};}};
  const media=new Map(),events={},stored=[];
  const api={root,button,stored,events};
  const doc={documentElement:root,hidden:false,readyState:'complete',getElementById(){return button;},addEventListener(type,fn){events[type]=fn;}};
  for(const query of ['(prefers-color-scheme: dark)','(prefers-reduced-motion: reduce)','(forced-colors: active)'])media.set(query,{matches:query==='(prefers-color-scheme: dark)'||query==='(prefers-reduced-motion: reduce)'&&reduced,addEventListener(type,fn){this.change=fn;}});
  if(supported)doc.startViewTransition=commit=>{
    api.transitions=(api.transitions||0)+1;commit();
    api.transition={ready:readyFailure?Promise.reject(new Error('snapshot unavailable')):Promise.resolve(),
      finished:new Promise(resolve=>{api.finish=resolve;}),skipTransition(){api.skipped=true;api.finish();}};
    return api.transition;
  };
  vm.runInNewContext(source,{document:doc,window:{addEventListener(type,fn){events[type]=fn;}},matchMedia:q=>media.get(q),localStorage:{getItem(){return null;},setItem(k,v){stored.push(v);}},innerWidth:1200,innerHeight:800,Math});
  return api;
}
(async()=>{
  const normal=setup();
  const first=normal.button.onclick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(normal.root.dataset.theme,'light');
  assert.equal(normal.transitions,1);
  assert.equal(normal.options.pseudoElement,'::view-transition-new(root)');
  assert.equal(normal.options.duration,720);
  assert.equal(normal.frames[0].clipPath,'circle(0px at 940px 40px)');
  const radius=Math.hypot(940,760);
  assert.equal(normal.frames[1].clipPath,'circle('+radius+'px at 940px 40px)');
  await normal.button.onclick();assert.equal(normal.transitions,1,'rapid clicks must not overlap snapshots');
  normal.finish();await first;
  assert.equal(normal.root.dataset.themeSweep,undefined);
  const reverse=normal.button.onclick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(normal.root.dataset.theme,'dark');normal.finish();await reverse;
  const fallback=setup({supported:false});await fallback.button.onclick();assert.equal(fallback.root.dataset.theme,'light');assert.equal(fallback.transitions,undefined);
  const staticTheme=setup({reduced:true});await staticTheme.button.onclick();assert.equal(staticTheme.root.dataset.theme,'light');assert.equal(staticTheme.transitions,undefined);
  const failed=setup({readyFailure:true});await failed.button.onclick();assert.equal(failed.root.dataset.theme,'light');assert.equal(failed.root.dataset.themeSweep,undefined);
  const hidden=setup();const pending=hidden.button.onclick();await new Promise(resolve=>setImmediate(resolve));hidden.events.pagehide();hidden.finish();await pending;
  assert.equal(hidden.skipped,true);assert.equal(hidden.root.dataset.themeSweep,undefined);
  console.log('Theme sweep: button origin, viewport coverage, both themes, click lock, fallback, reduced motion and cancellation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});

