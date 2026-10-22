/* The loss handler runs before the browser permits restoration. Own the deferred task. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/vendor/lens/internal/renderer.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export class GlassRenderer','class GlassRenderer');
const tasks=new Map();let id=0,inLoss=false,restores=0,starts=0;
class Observer {observe(){}disconnect(){}}
const context={tokens:{materials:{thick:{}}},matchMedia:()=>({matches:false}),ResizeObserver:Observer,MutationObserver:Observer,IntersectionObserver:Observer,
 AbortController,window:new EventTarget(),document:new EventTarget(),cancelAnimationFrame(){},
 setTimeout(fn){tasks.set(++id,fn);return id;},clearTimeout(key){tasks.delete(key);},
 observeGPU:()=>({active:true,destroy(){this.active=false;}})};
vm.createContext(context);vm.runInContext(source+'\nglobalThis.Renderer=GlassRenderer;',context);
class Renderer extends context.Renderer {startGPU(){starts++;this.suspended=false;this.lost=false;}}
function setup(){
 const canvas=Object.assign(new EventTarget(),{dataset:{}}),stage=Object.assign(new EventTarget(),{classList:{add(){},remove(){}}});
 const renderer=new Renderer(canvas,stage);
 renderer.suspended=false;renderer.gl={isContextLost:()=>true};
 renderer.contextExtension={restoreContext(){assert.equal(inLoss,false,'browser rejects restoreContext inside the loss event');restores++;},loseContext(){}};
 return {renderer,lose(){inLoss=true;try{canvas.dispatchEvent(new Event('webglcontextlost',{cancelable:true}));}finally{inLoss=false;}}};
}
function flush(){const pending=[...tasks.values()];tasks.clear();pending.forEach(fn=>fn());}
const live=setup();live.lose();
assert.equal(restores,0,'restore must wait until the loss event returns');assert.equal(tasks.size,1);
live.renderer.resumeGPU();assert.equal(tasks.size,1,'recovery requests coalesce');
flush();assert.equal(restores,1);assert.equal(live.renderer.canvas.dataset.gpuState,'restoring');
live.renderer.canvas.dispatchEvent(new Event('webglcontextrestored'));assert.equal(starts,1);
live.lose();live.renderer.suspendGPU();assert.equal(tasks.size,0,'suspending cancels pending recovery');flush();assert.equal(restores,1);
live.lose();assert.equal(tasks.size,1);live.renderer.gpuLease.active=false;flush();assert.equal(restores,1,'a departing lease must not reactivate the context');
live.renderer.gpuLease.active=true;live.lose();live.renderer.gl=null;live.renderer.destroy();assert.equal(tasks.size,0,'teardown cancels pending recovery');flush();assert.equal(restores,1);
const sleeping=setup();sleeping.renderer.gpuLease.active=false;sleeping.lose();assert.equal(tasks.size,0,'intentional GPU suspension remains asleep');
sleeping.renderer.gpuLease.active=true;sleeping.renderer.resumeGPU();flush();assert.equal(restores,2,'reacquiring a suspended lease restores the context');
console.log('Glass recovery: deferred/coalesced restore, active lease, suspension and teardown passed.');
