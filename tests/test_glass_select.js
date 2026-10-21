const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Node extends EventTarget {
 constructor(tag='div'){super();this.tag=tag;this.attrs={};this.dataset={};this.style={};this.children=[];this.hidden=false;this.disabled=false;this.rebuilds=0;const set=new Set();this.classList={add:(...x)=>x.forEach(v=>set.add(v)),remove:(...x)=>x.forEach(v=>set.delete(v)),toggle:(x,on)=>on?set.add(x):set.delete(x)};}
 set textContent(value){this._text=value;this.textWrites=(this.textWrites||0)+1;}get textContent(){return this._text||'';}
 setAttribute(k,v){this.attrs[k]=String(v);}getAttribute(k){return this.attrs[k]??null;}removeAttribute(k){delete this.attrs[k];}
 append(...nodes){this.children.push(...nodes);nodes.forEach(n=>n.parent=this);}replaceChildren(...nodes){this.rebuilds++;this.children=[];this.append(...nodes);}
 after(node){this.sibling=node;}remove(){this.removed=true;}contains(node){return this===node||this.children.some(c=>c.contains?.(node));}
 closest(query){return query==='optgroup'?null:this;}getBoundingClientRect(){return {left:100,top:100,bottom:144,width:240,height:44};}
 get scrollHeight(){return 140;}get offsetHeight(){return 140;}scrollIntoView(){}focus(){this.focused=true;}
}
const owner=new Node('document');owner.body=new Node('body');owner.createElement=tag=>new Node(tag);
const select=new Node('select');select.ownerDocument=owner;select.labels=[];
select.options=['公开全年龄','R-18','全部'].map((label,index)=>Object.assign(new Node('option'),{label,index,selected:index===0}));
Object.defineProperty(select,'selectedIndex',{get(){return this.options.findIndex(o=>o.selected);},set(index){this.options.forEach((o,i)=>o.selected=i===index);}});
Object.defineProperty(select,'selectedOptions',{get(){return this.options.filter(o=>o.selected);}});select.contains=node=>select.options.includes(node);
const observers=[];class Observer {constructor(fn){this.callback=fn;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}}
const source=fs.readFileSync(path.join(__dirname,'../web/glass-select.js'),'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function enhanceSelect','function enhanceSelect');
const win=new Node('window');const context={window:win,innerWidth:1200,innerHeight:800,MutationObserver:Observer,AbortController,Event,queueMicrotask,performance,createComponent:(type,options)=>{const element=new Node('button');element.onclick=options.onPress;return {element,destroy(){element.remove();}};}};
vm.createContext(context);vm.runInContext(source+'\nglobalThis.mount=enhanceSelect;',context);
(async()=>{
 const api=context.mount(select,{material:'quiet'});const first=api.popup.children[0], initial=api.popup.rebuilds,captionWrites=api.trigger.children[0].textWrites;
 for(let i=0;i<10;i++)owner.dispatchEvent(new Event('click'));await new Promise(resolve=>setImmediate(resolve));
 assert.equal(api.popup.rebuilds,initial,'unrelated page clicks must not recreate unchanged options');
 assert.equal(api.trigger.children[0].textWrites,captionWrites,'unchanged captions must not invalidate the glass subtree');
 assert.equal(api.popup.children[0],first,'stable options retain their DOM identity');
 select.selectedIndex=1;owner.dispatchEvent(new Event('click'));await new Promise(resolve=>setImmediate(resolve));
 assert.equal(api.trigger.children[0].textContent,'R-18','programmatic history restoration must still update the caption');
 api.trigger.onclick();assert.equal(api.trigger.getAttribute('aria-expanded'),'true');
 const second=api.popup.children[1];api.sync();assert.equal(api.popup.children[1],second);
 select.options[1].disabled=true;observers[0].callback();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(api.popup.children[1].getAttribute('aria-disabled'),'true');
 let changes=0;select.addEventListener('change',()=>changes++);
 const key=new Event('keydown');Object.assign(key,{key:'End'});api.trigger.dispatchEvent(key);
 const commit=new Event('keydown');Object.assign(commit,{key:'Enter'});api.trigger.dispatchEvent(commit);
 assert.equal(select.selectedIndex,2);assert.equal(changes,1,'a committed choice dispatches one native change');
 select.disabled=true;api.sync();assert.equal(api.trigger.disabled,true);assert.equal(api.trigger.getAttribute('aria-expanded'),'false');
 api.destroy();assert.equal(observers[0].disconnected,true);assert.equal(api.popup.removed,true);
 console.log('Select bridge: unchanged options reused, history restore, live disabled state, keyboard/native change and teardown passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
