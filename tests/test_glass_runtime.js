// Presentation lifecycle probe. The real adapter runs against semantic DOM doubles;
// only GPU/Canvas and Lens construction are replaced, never application actions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Classes {
  constructor(...names) {this.values = new Set(names);}
  add(...names) {names.forEach(n => this.values.add(n));}
  remove(...names) {names.forEach(n => this.values.delete(n));}
  contains(name) {return this.values.has(name);}
}
const all = [];
class Element {
  constructor(id = '', classes = []) {
    this.id=id;this.classList=new Classes(...classes);this.dataset={};this.childNodes=[];this.parentElement=null;this.attrs={};this.hidden=false;
    this.rect={top:100,left:80,width:900,height:450,right:980,bottom:550};
    this.style={removeProperty(){}};all.push(this);
  }
  set className(value) {this.classList=new Classes(...value.split(/\s+/).filter(Boolean));}
  append(...children) {for (const child of children) {child.remove();child.parentElement=this;this.childNodes.push(child);}}
  prepend(child) {child.remove();child.parentElement=this;this.childNodes.unshift(child);}
  remove() {if (this.parentElement) this.parentElement.childNodes=this.parentElement.childNodes.filter(c => c!==this);this.parentElement=null;}
  setAttribute(name,value) {this.attrs[name]=value;}
  getAttribute(name) {return this.attrs[name] ?? null;}
  getClientRects() {for(let el=this;el;el=el.parentElement)if(el.hidden)return [];return [this.rect];}
  getBoundingClientRect() {return this.rect;}
  querySelectorAll(selector) {return this.childNodes.flatMap(c=>[c,...c.querySelectorAll('*')]).filter(c=>selector==='*'||selector==='[data-jelly]'&&c.dataset.jelly!==undefined||selector==='a,button'&&['a','button'].includes(c.tag)||selector==='span,button'&&['span','button'].includes(c.tag)||selector==='button'&&c.tag==='button');}
  matches(selector) {return selector.includes('.head-actions') && this.parentElement?.classList.contains('head-actions') || selector.includes('.dialog-close')&&this.classList.contains('dialog-close');}
  closest(selector) {for(let el=this;el;el=el.parentElement)if(selector==='.lg-stage'&&el.classList.contains('lg-stage'))return el;return null;}
}
const root = new Element('root'),body=new Element('body');root.append(body);root.dataset.theme='dark';
const home=new Element('home',['hero']);body.append(home);
const search=new Element('search',['search-panel']);home.append(search);
const form=new Element('searchForm'),tagInput=new Element('tag'),searchButton=new Element('searchSubmit');searchButton.tag='button';form.append(tagInput,searchButton);search.append(form);
for(const id of ['workType','safety','datePreset','quality','format']) {const node=new Element(id);form.append(node);}
const title=new Element('title');body.append(title);
const controls=new Element('glassLabControls');body.append(controls);
const selection=new Element('selectionBar');selection.hidden=true;body.append(selection);
const rail=new Element('rail',['page-rail']);rail.rect={top:0,left:0,width:64,height:800,right:64,bottom:800};body.append(rail);
for(let i=0;i<6;i++) {const item=new Element('rail'+i);item.tag=i<3?'a':'button';rail.append(item);}
const download=new Element('downloadPage',['workspace-page']);download.hidden=true;body.append(download);
const detail=new Element('detail');download.append(detail);
const basket=new Element('basketPage',['basket-page']);basket.hidden=true;body.append(basket);
const basketDetail=new Element('basketArtworkDetail');basketDetail.hidden=true;basket.append(basketDetail);
const dTags=new Element('dTags'),bTags=new Element('basketDetailTags'),recent=new Element('recentSearches');detail.append(dTags);basketDetail.append(bTags);search.append(recent);
const listeners={document:{},window:{}};const reduced={matches:false,addEventListener(type,fn){this.change=fn;}};
const observerLists={mutation:[],resize:[],intersection:[]};
function Observer(kind) {return class {constructor(callback){this.callback=callback;this.nodes=[];this.disconnected=false;observerLists[kind].push(this);}observe(node){this.nodes.push(node);}disconnect(){this.disconnected=true;} fire(){this.callback([]);}};}
const constructed=[];const live=new Set();
function createGlassSystem(stage,options) {
  assert.equal(options.canvas,undefined,'Lens must own the full canvas; no masked rim wrapper');
  stage.classList.add('lg-stage','webgl-ready');
  const canvas=new Element();canvas.className='lg-canvas';stage.prepend(canvas);
  const record={stage,options,canvas,destroyed:false};constructed.push(record);live.add(record);
  return {jelly:{intensity:.42,cancel(){}},setMaterial(value){record.material=value;},setScene(value){record.scene=value;},destroy(){record.destroyed=true;canvas.remove();live.delete(record);}};
}
const createdComponents=[];
function createComponent(type,options) {const element=new Element();if(options.children)element.append(...options.children);const component={element,options,destroy(){element.remove();}};createdComponents.push(component);return component;}
const document={documentElement:root,hidden:false,getElementById(id){return all.find(el=>el.id===id);},createElement(tag){const el=new Element();el.tag=tag;return el;},querySelector(selector){if(selector==='.search-panel')return search;if(selector==='.page-rail')return rail;if(selector==='.home-intro h1')return title;return [download,basket].find(el=>!el.hidden)||null;},querySelectorAll(){return [download,basket,basketDetail,selection];},addEventListener(type,fn){listeners.document[type]=fn;}};
const window={addEventListener(type,fn){listeners.window[type]=fn;}};
const source=fs.readFileSync(path.join(__dirname,'../web/glass-lab.js'),'utf8').replace(/^import .*;\r?\n/gm,'');
let checkboxMounted=false;
let ambientActive=false,ambientDestroyed=false;
vm.runInNewContext(source,{document,window,matchMedia:()=>reduced,innerWidth:1200,innerHeight:850,devicePixelRatio:1,AbortController,IntersectionObserver:Observer('intersection'),MutationObserver:Observer('mutation'),ResizeObserver:Observer('resize'),createComponent,createGlassSystem,enhanceSelect:()=>({destroy(){}}),mountDotTitle:()=>({destroy(){}}),mountLatticeBackdrop:node=>{assert.equal(node,home);return {paint(){},setActive(value){ambientActive=value;},destroy(){ambientDestroyed=true;}};},mountCheckboxFeedback:doc=>{assert.equal(doc,document);checkboxMounted=true;return {destroy(){checkboxMounted=false;}};}});
const stages=()=>[...live].map(r=>r.stage.id).sort();
assert.deepEqual(stages(),['rail','search']);
assert.equal(root.dataset.scrollOwner,'document');
assert.equal(checkboxMounted,true);
assert.equal(ambientActive,true);
assert.equal(constructed.find(r=>r.stage===search).options.motion,true);
assert.equal(constructed.find(r=>r.stage===rail).options.motion,false);
assert.equal(recent.dataset.glass,undefined);
assert.equal(searchButton.dataset.lgDrag,'press');
assert.equal(tagInput.parentElement,form,'business input identity and parent stay intact');
assert.equal(form.parentElement.parentElement,search);
assert.equal(constructed[0].options.material,'thick');
for(const item of rail.childNodes.filter(c=>c.tag==='a'||c.tag==='button'))assert.equal(item.dataset.glass,'small');
const originalTag=new Element();originalTag.tag='span';dTags.append(originalTag);
observerLists.mutation.find(o=>o.nodes.includes(dTags)).fire();
assert.equal(originalTag.dataset.glass,'small');
assert.equal(originalTag.parentElement,dTags);
const hiddenObserver=observerLists.mutation.find(o=>o.nodes.includes(download));
const opticalToggle=createdComponents.find(c=>c.options.label==='光学效果：开');
opticalToggle.options.onPress();assert.equal(live.size,0);
download.hidden=false;hiddenObserver.fire();assert.equal(root.dataset.scrollOwner,'workspace');
download.hidden=true;hiddenObserver.fire();assert.equal(root.dataset.scrollOwner,'document');
opticalToggle.options.onPress();assert.deepEqual(stages(),['rail','search']);
download.hidden=false;hiddenObserver.fire();assert.deepEqual(stages(),['detail','rail']);
assert.equal(root.dataset.scrollOwner,'workspace');
assert.equal(ambientActive,false);
assert.equal(tagInput.parentElement,form);
detail.rect.height=10000;observerLists.resize[0].fire();assert.deepEqual(stages(),['rail']);
detail.rect.height=450;observerLists.resize[0].fire();assert.deepEqual(stages(),['detail','rail']);
download.hidden=true;basket.hidden=false;basketDetail.hidden=false;hiddenObserver.fire();assert.deepEqual(stages(),['basketArtworkDetail','rail']);
assert.equal(root.dataset.scrollOwner,'workspace');
root.dataset.theme='light';observerLists.mutation.find(o=>o.nodes.includes(root)).fire();
for(const record of live)assert.equal(record.scene,'aurora');
basket.hidden=true;basketDetail.hidden=true;hiddenObserver.fire();
assert.equal(root.dataset.scrollOwner,'document');assert.deepEqual(stages(),['rail','search']);
basket.hidden=false;basketDetail.hidden=false;hiddenObserver.fire();
assert.equal(root.dataset.scrollOwner,'workspace');
listeners.window.pagehide({persisted:true});assert.equal(live.size,0);assert.equal(observerLists.resize[0].disconnected,false);
listeners.window.pageshow();assert.deepEqual(stages(),['basketArtworkDetail','rail']);
listeners.window.pagehide({persisted:false});assert.equal(live.size,0);
for(const observers of Object.values(observerLists))for(const observer of observers)assert.equal(observer.disconnected,true);
assert.equal(constructed.filter(r=>!r.destroyed).length,0);
assert.equal(all.filter(el=>el.parentElement&&el.classList.contains('lg-canvas')).length,0);
assert.equal(checkboxMounted,false);
assert.equal(ambientDestroyed,true);
console.log('Glass runtime: full Lens canvas, consistent rail, tag identity, workspace switching, GPU bounds, theme, BFCache and teardown passed.');
