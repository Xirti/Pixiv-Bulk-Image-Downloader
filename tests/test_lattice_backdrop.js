const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const vendor=fs.readFileSync(path.join(__dirname,'../web/vendor/lattice/lorenz.js'),'utf8').replace('export function lorenz','function lorenz');
const source=fs.readFileSync(path.join(__dirname,'../web/lattice-backdrop.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function mountLatticeBackdrop','function mountLatticeBackdrop');
const frames=new Map(),observers={resize:[],intersection:[],mutation:[]},media=new Map(),canvases=[];let id=0,time=0,arcs=0;
const context2d={save(){},restore(){},scale(){},translate(){},setTransform(){},clearRect(){},fillRect(){},beginPath(){},fill(){},createRadialGradient(){return {addColorStop(){}};},arc(x,y,r){assert.ok(Number.isFinite(x)&&Number.isFinite(y)&&r>0);arcs++;}};
class Canvas {constructor(){this.attrs={};canvases.push(this);}getContext(){return context2d;}setAttribute(k,v){this.attrs[k]=v;}remove(){this.removed=true;}}
const home=new EventTarget();home.getBoundingClientRect=()=>({left:64,top:68,width:1200,height:800});home.prepend=c=>home.canvas=c;
const doc=new EventTarget();doc.hidden=false;doc.documentElement={dataset:{theme:'dark'}};doc.createElement=()=>new Canvas();
const win=new EventTarget();
for(const q of ['(prefers-reduced-motion: reduce)','(forced-colors: active)']){const m=new EventTarget();m.matches=false;media.set(q,m);}
function Observer(kind){return class {constructor(fn){this.callback=fn;observers[kind].push(this);}observe(){}disconnect(){this.disconnected=true;}};}
const context={document:doc,window:win,matchMedia:q=>media.get(q),devicePixelRatio:2,AbortController,ResizeObserver:Observer('resize'),IntersectionObserver:Observer('intersection'),MutationObserver:Observer('mutation'),requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:i=>frames.delete(i),Math,Float32Array};
vm.createContext(context);vm.runInContext(vendor+'\n'+source+'\nglobalThis.mount=mountLatticeBackdrop;',context);
const api=context.mount(home);
function advance(ms=42){time+=ms;const next=[...frames.values()];frames.clear();next.forEach(fn=>fn(time));}
assert.equal(home.canvas.attrs['aria-hidden'],'true');assert.ok(home.canvas.width*home.canvas.height<2_510_000);
observers.intersection[0].callback([{isIntersecting:true}]);assert.equal(frames.size,1);
const before=arcs;advance();assert.equal(arcs-before,760);
api.setActive(false);assert.equal(frames.size,0);
api.setActive(true);assert.equal(frames.size,1);
doc.hidden=true;doc.dispatchEvent(new Event('visibilitychange'));assert.equal(frames.size,0);
doc.hidden=false;doc.dispatchEvent(new Event('visibilitychange'));assert.equal(frames.size,1);
win.dispatchEvent(new Event('pagehide'));assert.equal(frames.size,0);
win.dispatchEvent(new Event('pageshow'));assert.equal(frames.size,1,'BFCache return must resume the active backdrop');
const reduced=media.get('(prefers-reduced-motion: reduce)');reduced.matches=true;reduced.dispatchEvent(new Event('change'));assert.equal(frames.size,0);
doc.documentElement.dataset.theme='light';observers.mutation[0].callback();assert.equal(frames.size,0);
reduced.matches=false;reduced.dispatchEvent(new Event('change'));assert.equal(frames.size,1);
observers.intersection[0].callback([{isIntersecting:false}]);assert.equal(frames.size,0);
api.destroy();assert.equal(home.canvas.removed,true);assert.equal(frames.size,0);assert.ok(Object.values(observers).flat().every(o=>o.disconnected));
console.log('Lattice backdrop: 760 finite points, pixel budget, visibility, workspace pause, theme, reduced motion, BFCache and cleanup passed.');
