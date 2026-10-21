import {createOpticalProgress,createDropSelect,createXYSlider,addOpticalRim} from './optical-components.js';
import {opticalSelection,clearOpticalState,opticColor} from './internal/optics.js';
import {registerPatternComponents} from './component-patterns.js';
import {createLensSurface} from './internal/lens-surface.js';
import {registerCardList} from './card-list.js';

let componentId=0;
const definitions=new Map();
const node=(tag,className,text)=>{const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;};
const accessibleLabel=label=>{if(typeof label!=='string'||!label.trim())throw new TypeError('A non-empty accessible label is required');return label;};
const surface=(el,kind)=>{el.classList.add('lg-surface');el.dataset.glass=kind;return el;};
const listenScope=()=>new AbortController();
function componentIcon(name){
  const paths={check:'m5 12 4 4L19 6',plus:'M12 5v14M5 12h14',minus:'M5 12h14',close:'m6 6 12 12M18 6 6 18',drag:'m9 5 3-3 3 3M12 2v20m-3-3 3 3 3-3M5 9l-3 3 3 3M2 12h20m-3-3 3 3-3 3'};
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),path=document.createElementNS(svg.namespaceURI,'path');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('lg-icon');path.setAttribute('d',paths[name]);svg.append(path);return svg;
}
export function bindSwitch(element,{checked=element.getAttribute('aria-checked')==='true',onChange=()=>{}}={}){
  const abort=listenScope();element.setAttribute('role','switch');element.dataset.lgSound='toggle';
  const setValue=(value,{emit=false}={})=>{const previous=element.getAttribute('aria-checked')==='true';element.setAttribute('aria-checked',String(Boolean(value)));if(emit&&previous!==Boolean(value))onChange(Boolean(value));element.dispatchEvent(new CustomEvent('lg:sync',{bubbles:true}));};
  setValue(checked);
  element.addEventListener('lg:set',event=>setValue(event.detail.value,{emit:Boolean(event.detail.emit)}),{signal:abort.signal});
  element.addEventListener('click',()=>{if(!element.disabled)setValue(element.getAttribute('aria-checked')!=='true',{emit:true});},{signal:abort.signal});
  element.addEventListener('keydown',event=>{if(!element.disabled&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setValue(event.key==='ArrowRight'||event.key==='End',{emit:true});element.dispatchEvent(new CustomEvent('lg:commit',{bubbles:true,detail:{cue:element.getAttribute('aria-checked')==='true'?'switchOn':'switchOff'}}));}},{signal:abort.signal});
  return {element,get value(){return element.getAttribute('aria-checked')==='true';},setValue,destroy:()=>abort.abort()};
}
export function bindChoiceGroup(element,{value,selectionColor='green',onChange=()=>{}}={}){
  opticColor(selectionColor);let initialized=false;
  const abort=listenScope(),buttons=()=>[...element.querySelectorAll('button[data-choice]')];element.setAttribute('role','group');
  const setValue=(next,{emit=false}={})=>{const all=buttons(),target=all.find(b=>b.dataset.choice===next&&!b.disabled);if(!target)return false;const old=all.find(b=>b.getAttribute('aria-pressed')==='true')?.dataset.choice;for(const button of all){const selected=button===target;button.setAttribute('aria-pressed',String(selected));button.tabIndex=selected?0:-1;button.style.setProperty('--glass-selection',selected?'1':'0');button.dataset.lgSound='select';addOpticalRim(button);opticalSelection(button,selected,selectionColor,initialized);}initialized=true;if(emit&&old!==next)onChange(next,target);return true;};
  setValue(value??buttons().find(button=>!button.disabled)?.dataset.choice);
  element.addEventListener('click',event=>{const button=event.target.closest('button[data-choice]');if(button&&element.contains(button)&&!button.disabled)setValue(button.dataset.choice,{emit:true});},{signal:abort.signal});
  element.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const all=buttons().filter(b=>!b.disabled);if(!all.length)return;event.preventDefault();const index=all.indexOf(event.target),next=event.key==='Home'?0:event.key==='End'?all.length-1:(index+(event.key==='ArrowRight'?1:-1)+all.length)%all.length;setValue(all[next].dataset.choice,{emit:true});all[next].focus();element.dispatchEvent(new CustomEvent('lg:commit',{bubbles:true,detail:{cue:'select'}}));},{signal:abort.signal});
  return {element,get value(){return buttons().find(b=>b.getAttribute('aria-pressed')==='true')?.dataset.choice;},setValue,destroy(){abort.abort();buttons().forEach(button=>{clearOpticalState(button);button.querySelector('.lg-optic-beam')?.getAnimations().forEach(animation=>animation.cancel());});}};
}
export function defineComponent(name,factory){if(!/^[a-z][a-z0-9-]*$/.test(name)||typeof factory!=='function'||definitions.has(name))throw new TypeError('Use a new component name and a factory');definitions.set(name,factory);}
export function createComponent(name,props={}){const factory=definitions.get(name);if(!factory)throw new Error(`Unknown component: ${name}`);return factory(props);}
export function listComponents(){return [...definitions.keys()];}

defineComponent('button',({label,variant='default',disabled=false,onPress=()=>{},sound='select'}={})=>{
  const el=createLensSurface({tag:'button',className:'lg-button',kind:'button',motion:'tether'}).element,abort=listenScope();el.textContent=accessibleLabel(label);el.type='button';el.disabled=disabled;el.dataset.variant=variant;el.dataset.lgSound=sound;
  el.addEventListener('click',event=>{if(!el.disabled)onPress(event);},{signal:abort.signal});
  return {element:el,setDisabled:value=>{el.disabled=Boolean(value);},destroy:()=>abort.abort()};
});
defineComponent('switch',({label,checked=false,onChange}={})=>{
  const el=node('button','lg-switch');el.type='button';el.setAttribute('aria-label',accessibleLabel(label));el.dataset.lgComponent='switch';
  el.append(surface(node('span','lg-switch-track'),'switch-track'));const thumb=surface(node('span','lg-switch-thumb'),'thumb');thumb.append(componentIcon('check'));el.append(thumb);
  return bindSwitch(el,{checked,onChange});
});
defineComponent('choices',({label,options=[],value,selectionColor,onChange}={})=>{
  const el=node('div','lg-choices');el.setAttribute('aria-label',accessibleLabel(label));if(!options.length)throw new TypeError('Choices require options');
  if(new Set(options.map(o=>o.value)).size!==options.length)throw new TypeError('Choice values must be unique');
  for(const option of options){const button=surface(node('button','lg-choice'),'chip');button.type='button';button.dataset.choice=option.value;button.dataset.lgDrag='tether';button.disabled=Boolean(option.disabled);button.append(componentIcon('check'),node('span','',accessibleLabel(option.label)));el.append(button);}
  return bindChoiceGroup(el,{value,selectionColor,onChange});
});
defineComponent('slider',({label,min=0,max=100,step=1,value=50,onChange=()=>{}}={})=>{
  if(![min,max,step,value].every(Number.isFinite)||max<=min||step<=0)throw new TypeError('Invalid slider range');
  const el=node('label','lg-slider'),head=node('span','lg-slider-heading'),output=node('output'),wrap=node('span','lg-slider-wrap'),input=node('input');
  head.append(node('span','',accessibleLabel(label)),output);input.type='range';input.setAttribute('aria-label',label);input.min=min;input.max=max;input.step=step;input.value=value;input.id=`lg-range-${++componentId}`;input.dataset.lgSound='tick';output.htmlFor=input.id;wrap.append(input,createLensSurface({tag:'span',className:'lg-slider-thumb',kind:'thumb'}).element);el.append(head,wrap);el.dataset.lgComponent='slider';
  const abort=listenScope(),setValue=(next,{emit=false}={})=>{if(!Number.isFinite(Number(next)))return;input.value=next;output.value=input.value;if(emit)onChange(Number(input.value));input.dispatchEvent(new CustomEvent('lg:sync',{bubbles:true}));};setValue(value);
  input.addEventListener('input',()=>setValue(input.value,{emit:true}),{signal:abort.signal});
  return {element:el,get value(){return Number(input.value);},setValue,destroy:()=>abort.abort()};
});
defineComponent('stepper',({label,min=5,max=60,step=5,value=25,unit='',decreaseLabel='Decrease',increaseLabel='Increase',onChange=()=>{}}={})=>{
  if(![min,max,step,value].every(Number.isFinite)||max<min||step<=0)throw new TypeError('Invalid stepper range');
  const el=surface(node('div','lg-stepper'),'stepper'),minus=node('button'),plus=node('button'),output=node('output'),abort=listenScope();el.setAttribute('role','group');el.setAttribute('aria-label',accessibleLabel(label));
  for(const [button,text,icon] of [[minus,decreaseLabel,'minus'],[plus,increaseLabel,'plus']]){button.type='button';button.setAttribute('aria-label',accessibleLabel(text));button.dataset.lgSound='tick';button.append(componentIcon(icon));}
  output.setAttribute('aria-live','polite');el.append(minus,output,plus);let current=value;
  const setValue=(next,{emit=false}={})=>{if(!Number.isFinite(Number(next)))return;const old=current;current=Math.min(max,Math.max(min,Number(next)));output.textContent=`${current}${unit?' '+unit:''}`;minus.disabled=current<=min;plus.disabled=current>=max;if(emit&&old!==current)onChange(current);};setValue(value);
  minus.addEventListener('click',()=>setValue(current-step,{emit:true}),{signal:abort.signal});plus.addEventListener('click',()=>setValue(current+step,{emit:true}),{signal:abort.signal});
  return {element:el,get value(){return current;},setValue,destroy:()=>abort.abort()};
});
defineComponent('notification',({title,description='',dismissLabel='Dismiss notification',onDismiss=()=>{}}={})=>{
  const el=surface(node('section','lg-notification'),'notification'),close=node('button','lg-notification-close'),abort=listenScope();el.dataset.lgDrag='tether';el.setAttribute('aria-label',accessibleLabel(title));el.append(node('strong','',title),node('p','',description));close.type='button';close.setAttribute('aria-label',accessibleLabel(dismissLabel));close.dataset.lgSound='dismiss';close.append(componentIcon('close'));el.append(close);
  close.addEventListener('click',()=>{el.hidden=true;onDismiss();el.dispatchEvent(new CustomEvent('lg:sync',{bubbles:true}));},{signal:abort.signal});
  return {element:el,show:()=>{el.hidden=false;el.dispatchEvent(new CustomEvent('lg:sync',{bubbles:true}));},destroy:()=>abort.abort()};
});
defineComponent('search',({label,placeholder='',onChange=()=>{}}={})=>{
  const el=surface(node('label','lg-search'),'search'),input=node('input'),abort=listenScope();input.type='search';input.setAttribute('aria-label',accessibleLabel(label));input.placeholder=placeholder;input.dataset.lgSound='type';el.append(input);input.addEventListener('input',()=>onChange(input.value),{signal:abort.signal});
  return {element:el,get value(){return input.value;},setValue:value=>{input.value=String(value);},destroy:()=>abort.abort()};
});
defineComponent('lens',({label,caption='Hold and drag'}={})=>{
  const lens=createLensSurface({className:'lg-lens',kind:'free',motion:'free'}),el=lens.element;el.tabIndex=0;el.setAttribute('role','slider');el.setAttribute('aria-label',accessibleLabel(label));el.setAttribute('aria-valuemin','0');el.setAttribute('aria-valuemax','100');el.setAttribute('aria-valuenow','50');el.dataset.lgSoundKey='tick';el.append(componentIcon('drag'),node('span','',caption));return lens;
});
defineComponent('panel',({label,children=[],motion='tether'}={})=>{if(!['tether','press','none'].includes(motion))throw new TypeError('Invalid panel motion');const lens=createLensSurface({tag:'section',className:'lg-panel',kind:'panel',motion}),el=lens.element;el.setAttribute('aria-label',accessibleLabel(label));for(const child of children)el.append(typeof child==='string'?document.createTextNode(child):child);return lens;});

defineComponent('progress',createOpticalProgress);
defineComponent('drop-select',createDropSelect);
defineComponent('xy-slider',createXYSlider);
registerPatternComponents(defineComponent,createComponent);
registerCardList(defineComponent,createComponent);
