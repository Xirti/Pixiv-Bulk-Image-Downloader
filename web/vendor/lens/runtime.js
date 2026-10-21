import {GlassRenderer} from './internal/renderer.js';
import {JellyController} from './internal/jelly.js';
import {GlassSound,bindControlSounds} from './sound.js';
import {tokens,resolveMaterial} from './tokens.js';
import {bindKeyboardFocus} from './focus.js';

export function createGlassSystem(stage,{canvas,material='thick',scene='aurora',artwork,motion=false,elasticity=tokens.motion.elasticity,audio={},onFallback=()=>{}}={}){
  if(!stage||!stage.querySelector)throw new TypeError('A stage element is required');
  const ownsCanvas=!canvas;canvas ||= stage.ownerDocument.createElement('canvas');canvas.setAttribute('aria-hidden','true');canvas.classList.add('lg-canvas');if(ownsCanvas)stage.prepend(canvas);stage.classList.add('lg-stage');stage.dataset.scene=scene;
  const initialMaterial=resolveMaterial(material);stage.style.setProperty('--lg-fallback-blur',`${4+initialMaterial.blur*24}px`);
  let renderer;try{renderer=new GlassRenderer(canvas,stage,{...initialMaterial,scene,artwork,motion});}catch(error){stage.classList.add('fallback');onFallback(error);}
  const jelly=new JellyController(stage,renderer,{intensity:elasticity}),sound=new GlassSound(audio),unbind=bindControlSounds(stage,sound),unbindFocus=bindKeyboardFocus(stage),abort=new AbortController(),observers=[],media=matchMedia('(prefers-reduced-motion: reduce)');renderer?.setMotionSource(jelly);
  const signal=abort.signal;media.addEventListener('change',event=>{jelly.reduced=event.matches;if(renderer){renderer.reducedMotion=event.matches;renderer.invalidate();}},{signal});
  const mount=(root=stage)=>{
    const all=selector=>[...(root.matches?.(selector)?[root]:[]),...root.querySelectorAll(selector)];
    for(const el of all('[data-lg-drag]'))if(!jelly.bodies.has(el)){
      const mode=el.dataset.lgDrag;jelly.register(el,{mode,maxTravel:mode==='free'?Infinity:el.matches('button')?18:75});
      if(mode==='free'){
        const update=()=>{const body=jelly.bodies.get(el),parent=el.parentElement,x=Math.round((.5+body.x.target/Math.max(1,parent.clientWidth))*100),y=Math.round((.5+body.y.target/Math.max(1,parent.clientHeight))*100);el.setAttribute('aria-valuenow',String(x));el.setAttribute('aria-valuetext',`X ${x}%, Y ${y}%`);};
        jelly.bodies.get(el).onPosition=update;
        update();
        el.addEventListener('keydown',event=>{const delta={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16],Home:[0,0]}[event.key];if(!delta)return;event.preventDefault();const b=jelly.bodies.get(el),p=el.parentElement,maxX=Math.max(0,(p.clientWidth-el.offsetWidth)/2-22),maxY=Math.max(0,(p.clientHeight-el.offsetHeight)/2-22);jelly.target(el,event.key==='Home'?0:Math.max(-maxX,Math.min(maxX,b.x.target+delta[0])),event.key==='Home'?0:Math.max(-maxY,Math.min(maxY,b.y.target+delta[1])));update();},{signal});
      }
    }
    for(const el of all('[data-lg-component=switch]')){
      const thumb=el.querySelector('[data-glass=thumb]');if(jelly.bodies.has(thumb))continue;
      jelly.register(thumb,{source:el,mode:'toggle',onCommit:checked=>el.dispatchEvent(new CustomEvent('lg:set',{detail:{value:checked,emit:true}}))});
      const sync=()=>jelly.target(thumb,el.getAttribute('aria-checked')==='true'?jelly.toggleTravel(el):0);el.addEventListener('lg:sync',sync,{signal});sync();
    }
    for(const el of all('[data-lg-component=slider]')){
      const input=el.querySelector('input'),thumb=el.querySelector('.lg-slider-thumb');if(jelly.bodies.has(thumb))continue;
      jelly.register(thumb,{source:input,mode:'range'});const sync=()=>{const p=(Number(input.value)-Number(input.min))/(Number(input.max)-Number(input.min));jelly.target(thumb,(input.clientWidth-24)*p);el.style.setProperty('--lg-range-fill',`${p*100}%`);};
      input.addEventListener('lg:sync',sync,{signal});const observer=new ResizeObserver(sync);observer.observe(input);observers.push(observer);sync();
    }
    for(const el of all('[data-lg-segments]')){
      const indicator=el.querySelector('.lg-segment-indicator'),bar=el.querySelector('.lg-segment-bar');if(jelly.bodies.has(indicator))continue;
      const buttons=()=>[...bar.querySelectorAll('button')].filter(button=>!button.disabled);
      jelly.register(indicator,{source:bar,mode:'segment',segments:buttons,onCommit:button=>el.dispatchEvent(new CustomEvent('lg:set',{detail:{value:button.dataset.value,emit:true}}))});
      let placed=false;const sync=()=>{const button=bar.querySelector('[aria-selected=true],[aria-current]');if(!button)return;indicator.style.width=`${button.offsetWidth}px`;jelly.target(indicator,button.offsetLeft,0,{snap:!placed});if(button.offsetWidth)placed=true;};el.addEventListener('lg:sync',sync,{signal});sync();
    }
    renderer?.invalidate(300);return api;
  };
  stage.addEventListener('lg:sync',()=>renderer?.invalidate(400),{signal});
  const api={renderer,jelly,sound,mount,setMaterial:value=>{const next=resolveMaterial(value);stage.style.setProperty('--lg-fallback-blur',`${4+next.blur*24}px`);renderer?.set(next);},setScene:value=>{stage.dataset.scene=value;renderer?.set({scene:value});},destroy(){abort.abort();observers.forEach(o=>o.disconnect());unbind();unbindFocus();sound.destroy();jelly.destroy();renderer?.destroy();if(ownsCanvas)canvas.remove();stage.classList.remove('webgl-ready');}};
  mount();return api;
}
