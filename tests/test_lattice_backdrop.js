const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const vendor=fs.readFileSync(path.join(__dirname,'../web/vendor/lattice/point-sets.js'),'utf8').replace(/export function /g,'function ');
const source=fs.readFileSync(path.join(__dirname,'../web/lattice-backdrop.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function mountLatticeBackdrop','function mountLatticeBackdrop');
const frames=new Map(),observers={resize:[],intersection:[],mutation:[]},media=new Map(),canvases=[];let id=0,time=0,arcs=0,lastPoint,copy,gridArcs=0,gridPaths=0,gridFills=0;
const context2d={save(){},restore(){},scale(){},translate(){},setTransform(){},clearRect(){},fillRect(){},beginPath(){},fill(path){if(path instanceof CachedPath)gridFills++;},createRadialGradient(){return {addColorStop(){}};},drawImage(...args){copy=args;},arc(x,y,r){assert.ok(Number.isFinite(x)&&Number.isFinite(y)&&r>0);if(r>1){arcs++;lastPoint=[x,y];}else gridArcs++;}};
class Canvas {constructor(){this.attrs={};canvases.push(this);}getContext(){return context2d;}setAttribute(k,v){this.attrs[k]=v;}remove(){this.removed=true;}}
const home=new EventTarget();home.getBoundingClientRect=()=>({left:64,top:68,width:1200,height:800});home.prepend=c=>home.canvas=c;
const doc=new EventTarget();doc.hidden=false;doc.documentElement={dataset:{theme:'dark'}};doc.createElement=()=>new Canvas();
const win=new EventTarget();
for(const q of ['(prefers-reduced-motion: reduce)','(forced-colors: active)']){const m=new EventTarget();m.matches=false;media.set(q,m);}
function Observer(kind){return class {constructor(fn){this.callback=fn;observers[kind].push(this);}observe(){}disconnect(){this.disconnected=true;}};}
class CachedPath {constructor(){gridPaths++;}moveTo(){}arc(){}}
const context={Path2D:CachedPath,document:doc,window:win,matchMedia:q=>media.get(q),devicePixelRatio:2,AbortController,ResizeObserver:Observer('resize'),IntersectionObserver:Observer('intersection'),MutationObserver:Observer('mutation'),requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:i=>frames.delete(i),Math,Float32Array};
vm.createContext(context);vm.runInContext(vendor+'\n'+source+'\nglobalThis.mount=mountLatticeBackdrop;',context);
let refreshed=0;
const api=context.mount(home,{onFrame(){refreshed++;}});
function advance(ms=42){time+=ms;const next=[...frames.values()];frames.clear();next.forEach(fn=>fn(time));}
assert.equal(home.canvas.attrs['aria-hidden'],'true');assert.ok(home.canvas.width*home.canvas.height<2_510_000);
observers.intersection[0].callback([{isIntersecting:true}]);assert.equal(frames.size,1);
assert.equal(gridPaths,1,'static dots are retained as a cached path');assert.ok(gridFills>0,'cached dots must still be painted');
const beforeGrid=gridArcs;const before=arcs,uploads=refreshed;advance();
assert.equal(gridArcs,beforeGrid,'static grid must reuse its path instead of constructing every circle each frame');
const cachedPaths=gridPaths;observers.resize[0].callback();assert.equal(gridPaths,cachedPaths,'same-size resize must retain the static path');assert.equal(arcs-before,1600);
assert.ok(refreshed>uploads,"visible background frames must refresh glass, rather than freeze its initial texture");
const initialPoint=lastPoint.slice();for(let i=0;i<265;i++)advance();
assert.notDeepEqual(lastPoint,initialPoint,'formation and rotation must change the actual painted field');
api.paint(context2d,{width:500,height:300,dpr:1},{getBoundingClientRect:()=>({left:100,top:200})});
assert.equal(copy[0],home.canvas,'glass must sample the same canvas currently shown outside');
assert.deepEqual(copy.slice(1),[-36,-132,1200,800],'canvas sampling must align to page coordinates');
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
console.log('Lattice backdrop: 1600 original formation points, cached static grid, pixel budget, visibility, workspace pause, theme, reduced motion, BFCache and cleanup passed.');
