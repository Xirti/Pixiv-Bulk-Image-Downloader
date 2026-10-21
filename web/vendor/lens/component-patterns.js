import {opticalIcon,addOpticalRim} from './optical-components.js';
import {opticalSelection,clearOpticalState} from './internal/optics.js';

let patternId=0;
const patternNode=(tag,className,text)=>{const el=document.createElement(tag);el.className=className;if(text!==undefined)el.textContent=text;return el;};
const patternSync=el=>el.dispatchEvent(new CustomEvent('lg:sync',{bubbles:true}));
const patternLabel=value=>{if(typeof value!=='string'||!value.trim())throw new TypeError('A non-empty accessible label is required');return value;};
const patternOptions=options=>{if(!options?.length||new Set(options.map(item=>item.value)).size!==options.length||options.some(item=>typeof item.value!=='string'))throw new TypeError('Use unique string option values');options.forEach(item=>patternLabel(item.label));};

/** Reusable versions of the restored lab's icon, connection, tab, dock and menu controls. */
export function registerPatternComponents(defineComponent,createComponent){
  defineComponent('icon-button',({label,icon='heart',pressed,disabled=false,onChange=()=>{},onPress=()=>{}}={})=>{
    let current=Boolean(pressed),toggle=typeof pressed==='boolean',initialized=false;
    const control=createComponent('button',{label:patternLabel(label),disabled,sound:icon==='heart'?'favorite':'select',onPress:event=>{if(toggle)setValue(!current,{emit:true});onPress(event);}}),el=control.element;
    el.classList.add('lg-icon-button');el.setAttribute('aria-label',label);el.replaceChildren(opticalIcon(icon));addOpticalRim(el);
    const setValue=(next,{emit=false}={})=>{const changed=current!==Boolean(next);current=Boolean(next);if(toggle)el.setAttribute('aria-pressed',String(current));opticalSelection(el,current,'green',initialized);initialized=true;if(changed&&emit)onChange(current);};setValue(current);
    return {...control,get value(){return current;},setValue,destroy(){control.destroy();clearOpticalState(el);el.getAnimations({subtree:true}).forEach(animation=>animation.cancel());}};
  });
  defineComponent('toggle-tile',({label,icon='wifi',checked=false,onLabel='On',offLabel='Off',disabled=false,onChange=()=>{}}={})=>{
    let current=Boolean(checked),initialized=false;
    const control=createComponent('button',{label:patternLabel(label),disabled,sound:'toggle',onPress:()=>setValue(!current,{emit:true})}),el=control.element,copy=patternNode('span','lg-tile-copy'),status=patternNode('span','lg-tile-status');
    el.classList.add('lg-toggle-tile');el.setAttribute('role','switch');el.setAttribute('aria-label',label);copy.append(patternNode('strong','',label),status);el.replaceChildren(opticalIcon(icon),copy);addOpticalRim(el);
    const setValue=(value,{emit=false}={})=>{const changed=current!==Boolean(value);current=Boolean(value);el.setAttribute('aria-checked',String(current));status.textContent=current?onLabel:offLabel;opticalSelection(el,current,'green',initialized);initialized=true;if(changed&&emit)onChange(current);};setValue(current);
    return {...control,get value(){return current;},setValue,destroy(){control.destroy();clearOpticalState(el);el.getAnimations({subtree:true}).forEach(animation=>animation.cancel());}};
  });
  const segmented=(kind,{label,options=[],value,onChange=()=>{}}={})=>{
    patternLabel(label);patternOptions(options);const enabled=options.filter(item=>!item.disabled);if(!enabled.length)throw new TypeError('At least one option must be enabled');
    let current=value??enabled[0].value;if(!enabled.some(item=>item.value===current))throw new TypeError('Invalid selection');
    const abort=new AbortController(),signal=abort.signal,id=++patternId,el=patternNode('div',`lg-${kind}`),bar=patternNode(kind==='tabs'?'div':'nav','lg-segment-bar'),indicator=patternNode('span','lg-segment-indicator lg-surface'),entries=[];
    el.dataset.lgSegments='true';bar.setAttribute('aria-label',label);bar.setAttribute('role',kind==='tabs'?'tablist':'navigation');indicator.dataset.glass='tab';indicator.setAttribute('aria-hidden','true');bar.append(indicator);el.append(bar);
    const align=()=>{const button=entries.find(item=>item.option.value===current)?.button;if(!button)return;indicator.style.width=`${button.offsetWidth}px`;indicator.style.setProperty('--lg-segment-x',`${button.offsetLeft}px`);patternSync(el);};
    for(const option of options){
      const button=patternNode('button','lg-segment-option'),panel=kind==='tabs'?patternNode('div','lg-tab-panel'):null;button.type='button';button.dataset.value=option.value;button.disabled=Boolean(option.disabled);button.dataset.lgSound='select';
      if(kind==='dock')button.append(opticalIcon(option.icon||'focus'));button.append(patternNode('span','',option.label));
      if(panel){button.setAttribute('role','tab');button.id=`lg-tab-${id}-${entries.length}`;panel.id=`lg-tab-panel-${id}-${entries.length}`;button.setAttribute('aria-controls',panel.id);panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.tabIndex=0;if(option.content!==undefined)panel.append(typeof option.content==='string'?document.createTextNode(option.content):option.content);el.append(panel);}
      button.addEventListener('click',()=>setValue(option.value,{emit:true}),{signal});bar.append(button);entries.push({option,button,panel});
    }
    const setValue=(next,{emit=false}={})=>{if(!enabled.some(item=>item.value===next))return false;const changed=current!==next;current=next;for(const {option,button,panel} of entries){const selected=option.value===current;button.tabIndex=selected?0:-1;if(panel){button.setAttribute('aria-selected',String(selected));panel.hidden=!selected;}else if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}align();if(changed&&emit)onChange(current);return true;};
    bar.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)||event.altKey||event.ctrlKey||event.metaKey)return;event.preventDefault();const index=enabled.findIndex(item=>item.value===current),next=event.key==='Home'?0:event.key==='End'?enabled.length-1:(index+(event.key==='ArrowRight'?1:-1)+enabled.length)%enabled.length;setValue(enabled[next].value,{emit:true});entries.find(item=>item.option.value===current).button.focus();},{signal});
    el.addEventListener('lg:set',event=>{if(event.target===el)setValue(event.detail.value,{emit:Boolean(event.detail.emit)});},{signal});
    const observer=new ResizeObserver(align);observer.observe(bar);setValue(current);
    return {element:el,get value(){return current;},setValue,destroy(){abort.abort();observer.disconnect();}};
  };
  defineComponent('tabs',props=>segmented('tabs',props));
  defineComponent('dock',props=>segmented('dock',props));
  defineComponent('menu',({label,options=[],onAction=()=>{}}={})=>{
    patternOptions(options);const abort=new AbortController(),signal=abort.signal,id=++patternId,el=patternNode('div','lg-menu-control'),panel=patternNode('div','lg-menu-popover lg-surface'),buttons=[];
    let opened=false,animation=null,destroyed=false;
    const control=createComponent('button',{label:patternLabel(label),onPress:()=>setOpen(!opened)}),trigger=control.element;trigger.removeAttribute('data-lg-drag');trigger.prepend(opticalIcon('more'));trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-controls',`lg-menu-${id}`);trigger.setAttribute('aria-expanded','false');
    panel.id=`lg-menu-${id}`;panel.dataset.glass='menu';panel.setAttribute('role','menu');panel.setAttribute('aria-label',label);panel.hidden=true;el.append(trigger,panel);
    for(const option of options){const button=patternNode('button','lg-menu-item',option.label);button.type='button';button.setAttribute('role','menuitem');button.disabled=Boolean(option.disabled);button.tabIndex=-1;button.dataset.lgSound='select';if(option.icon)button.prepend(opticalIcon(option.icon));button.addEventListener('click',()=>{if(button.disabled)return;setOpen(false,true);onAction(option.value);},{signal});panel.append(button);buttons.push(button);}
    const available=()=>buttons.filter(button=>!button.disabled);
    const setOpen=(next,returnFocus=false,last=false)=>{
      if(destroyed||next===opened)return;opened=next;trigger.setAttribute('aria-expanded',String(next));animation?.cancel();panel.hidden=false;
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches){animation=panel.animate(next?[{opacity:0,transform:'translateY(-7px) scale(.97)'},{opacity:1,transform:'none'}]:[{opacity:1,transform:'none'},{opacity:0,transform:'translateY(-5px) scale(.98)'}],{duration:next?220:150,easing:'cubic-bezier(.16,1,.3,1)'});if(!next)animation.onfinish=()=>{panel.hidden=true;patternSync(el);};}else panel.hidden=!next;
      if(next)(last?available().at(-1):available()[0])?.focus();else if(returnFocus)trigger.focus();patternSync(el);
    };
    trigger.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();setOpen(true,false,event.key==='ArrowUp');}},{signal});
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();setOpen(false,true);}else if(event.key==='Tab')setOpen(false);else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const all=available(),index=all.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?all.length-1:(index+(event.key==='ArrowDown'?1:-1)+all.length)%all.length;all[next]?.focus();}},{signal});
    document.addEventListener('pointerdown',event=>{if(!el.contains(event.target))setOpen(false);},{signal});
    el.addEventListener('focusout',()=>queueMicrotask(()=>{if(!destroyed&&!el.contains(document.activeElement))setOpen(false);}),{signal});
    return {element:el,open:()=>setOpen(true),close:()=>setOpen(false,true),destroy(){destroyed=true;animation?.cancel();abort.abort();control.destroy();}};
  });
}
