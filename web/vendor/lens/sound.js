import {tokens} from './tokens.js';

// Short original synthesized cues; no network, samples, music or idle audio.
export const soundCues=Object.freeze({
  press:{duration:.07,tones:[[260,190,.17]],noise:.015},
  grab:{duration:.12,tones:[[210,340,.21],[520,430,.045]]},
  release:{duration:.15,tones:[[370,240,.17],[740,480,.025]]},
  select:{duration:.105,tones:[[760,620,.17],[1140,960,.035]]},
  switchOn:{duration:.15,tones:[[420,660,.20],[840,990,.035]]},
  switchOff:{duration:.14,tones:[[570,330,.18]]},
  tick:{duration:.045,tones:[[840,660,.10]],noise:.018,cooldown:90},
  type:{duration:.035,tones:[[440,310,.065]],noise:.035,cooldown:100},
  success:{duration:.28,tones:[[660,660,.15],[990,990,.085,.085]]},
  favorite:{duration:.21,tones:[[590,740,.16],[1180,1480,.04,.035]]},
  dismiss:{duration:.15,tones:[[420,185,.14]],noise:.012},
  restore:{duration:.18,tones:[[330,590,.16]]},
  reset:{duration:.18,tones:[[540,290,.13],[270,400,.06,.05]]},
});
export function synthesizeCue(definition,sampleRate=48000){
  const d=typeof definition==='string'?soundCues[definition]:definition;
  if(!d||!Number.isFinite(d.duration)||d.duration<=0||d.duration>1||!Array.isArray(d.tones)||!d.tones.length)throw new TypeError('Invalid sound cue');
  if(!Number.isFinite(sampleRate)||sampleRate<8000||sampleRate>192000)throw new TypeError('Invalid sample rate');
  for(const tone of d.tones)if(tone.length<3||tone.some(v=>!Number.isFinite(v))||tone[0]<=0||tone[1]<=0||tone[2]<0||tone[2]>1||(tone[3]??0)<0||(tone[3]??0)>=d.duration)throw new TypeError('Invalid tone');
  if(d.noise!==undefined&&(!Number.isFinite(d.noise)||d.noise<0||d.noise>1))throw new TypeError('Invalid noise');
  const data=new Float32Array(Math.ceil(d.duration*sampleRate));let random=1709,filtered=0;
  for(let i=0;i<data.length;i++){
    const t=i/sampleRate,tail=Math.min(1,(data.length-1-i)/Math.max(1,sampleRate*.012));let sample=0;
    for(const [from,to,gain,delay=0] of d.tones){const u=t-delay;if(u<0)continue;const length=d.duration-delay,phase=2*Math.PI*(from*u+(to-from)*u*u/(2*length));sample+=Math.sin(phase)*gain*Math.min(1,u/.004)*Math.exp(-u*7/length);}
    random=(Math.imul(random,1664525)+1013904223)>>>0;filtered=.58*filtered+.42*(random/4294967295*2-1);
    sample+=filtered*(d.noise||0)*Math.min(1,t/.003)*Math.exp(-t*12/d.duration);
    data[i]=Math.max(-.8,Math.min(.8,sample))*tail;
  }
  return data;
}

/** Fine paper grain and a light glass resonance, synthesized without samples. */
export function synthesizeFriction(sampleRate=48000){
  if(!Number.isFinite(sampleRate)||sampleRate<8000||sampleRate>192000)throw new TypeError('Invalid sample rate');
  const data=new Float32Array(Math.round(sampleRate*2));let random=4217,low=0,grain=0;
  for(let i=0;i<data.length;i++){
    random=(Math.imul(random,1664525)+1013904223)>>>0;
    const white=random/4294967295*2-1,t=i/sampleRate;
    low+=.035*(white-low);grain+=.32*(white-grain);
    const texture=(grain-low)*.72+white*.10;
    const envelope=.78+.13*Math.sin(2*Math.PI*37*t)+.09*Math.sin(2*Math.PI*83*t);
    const seam=Math.min(1,i/(sampleRate*.006),(data.length-1-i)/(sampleRate*.006));
    data[i]=texture*envelope*seam;
  }
  return data;
}
export class GlassSound {
  constructor({enabled=tokens.audio.enabled,volume=tokens.audio.volume,storageKey=tokens.audio.storageKey,storage,contextFactory,now=()=>performance.now(),onChange}={}){
    this.storageKey=storageKey;this.now=now;this.onChange=onChange;this.destroyed=false;this.context=null;this.voices=new Set();this.cache=new Map();this.cues=new Map(Object.entries(soundCues));this.last=new Map();this.revision=0;this.played=0;
    this.friction=null;this.frictionIntent=false;this.frictionGeneration=0;this.frictionSpeed=0;this.frictionTimer=0;
    this.contextFactory=contextFactory||(()=>{const Constructor=globalThis.AudioContext||globalThis.webkitAudioContext;return Constructor?new Constructor():null;});
    try{this.storage=storage===undefined?globalThis.localStorage:storage;const saved=JSON.parse(this.storage?.getItem(storageKey)||'null');if(saved&&typeof saved.enabled==='boolean')enabled=saved.enabled;if(saved&&Number.isFinite(saved.volume))volume=saved.volume;}catch{}
    this.enabled=Boolean(enabled);this.volume=this.clampVolume(volume);this.status='idle';
  }
  clampVolume(value){if(!Number.isFinite(value))throw new TypeError('Volume must be finite');return Math.max(0,Math.min(1,value));}
  snapshot(){return {enabled:this.enabled,volume:this.volume,status:this.status,played:this.played,activeVoices:this.voices.size,frictionActive:Boolean(this.friction&&this.frictionIntent&&this.frictionSpeed>0)};}
  ensureContext(){
    if(!this.context){this.context=this.contextFactory();if(!this.context){this.status='unavailable';this.onChange?.(this.snapshot());return false;}this.master=this.context.createGain();this.master.gain.value=this.volume;this.master.connect(this.context.destination);}
    return true;
  }
  set({enabled=this.enabled,volume=this.volume}={}){
    this.volume=this.clampVolume(volume);this.enabled=Boolean(enabled);this.revision++;
    if(!this.enabled||!this.volume)this.stop();
    if(this.master)this.master.gain.setTargetAtTime(this.enabled?this.volume:0,this.context.currentTime,.012);
    try{this.storage?.setItem(this.storageKey,JSON.stringify({enabled:this.enabled,volume:this.volume}));}catch{}
    this.onChange?.(this.snapshot());
  }
  register(name,definition){if(!name||this.cues.has(name))throw new Error('Sound cue name must be new');synthesizeCue(definition,8000);this.cues.set(name,structuredClone(definition));return this;}
  async play(name='select'){
    if(this.destroyed||!this.enabled||this.volume===0)return false;
    const cue=this.cues.get(name);if(!cue)throw new Error(`Unknown sound cue: ${name}`);
    const started=this.now(),last=this.last.get(name)??-Infinity;
    if(started-last<(cue.cooldown??tokens.audio.cooldown))return false;
    this.last.set(name,started);const revision=this.revision;
    try{
      if(!this.ensureContext())return false;
      if(this.context.state!=='running')await this.context.resume();
      if(this.destroyed||!this.enabled||revision!==this.revision||this.now()-started>220||this.context.state!=='running')return false;
      if(!this.cache.has(name)){const pcm=synthesizeCue(cue,this.context.sampleRate),buffer=this.context.createBuffer(1,pcm.length,this.context.sampleRate);buffer.copyToChannel(pcm,0);this.cache.set(name,buffer);}
      if(this.voices.size>=tokens.audio.maxVoices){const oldest=this.voices.values().next().value;oldest.stop();oldest.disconnect();this.voices.delete(oldest);}
      const source=this.context.createBufferSource();source.buffer=this.cache.get(name);source.connect(this.master);this.voices.add(source);
      source.onended=()=>{source.disconnect();this.voices.delete(source);};source.start();this.status='ready';this.played++;this.onChange?.(this.snapshot());return true;
    }catch{this.status='unavailable';this.onChange?.(this.snapshot());return false;}
  }
  async startFriction(){
    this.endFriction(true);
    if(this.destroyed||!this.enabled||!this.volume)return false;
    this.frictionIntent=true;const generation=++this.frictionGeneration;
    try{
      if(!this.ensureContext()){this.frictionIntent=false;return false;}
      if(this.context.state!=='running')await this.context.resume();
      if(this.destroyed||!this.enabled||!this.frictionIntent||generation!==this.frictionGeneration||this.context.state!=='running')return false;
      if(!this.cache.has('@friction')){const pcm=synthesizeFriction(this.context.sampleRate),buffer=this.context.createBuffer(1,pcm.length,this.context.sampleRate);buffer.copyToChannel(pcm,0);this.cache.set('@friction',buffer);}
      const source=this.context.createBufferSource(),filter=this.context.createBiquadFilter(),gain=this.context.createGain();
      source.buffer=this.cache.get('@friction');source.loop=true;filter.type='bandpass';filter.Q.value=.65;gain.gain.value=0;
      source.connect(filter);filter.connect(gain);gain.connect(this.master);
      const voice={source,filter,gain};this.friction=voice;
      source.onended=()=>{source.disconnect();filter.disconnect();gain.disconnect();if(this.friction===voice)this.friction=null;};
      source.start();this.status='ready';this.updateFriction(this.frictionSpeed);this.onChange?.(this.snapshot());return true;
    }catch{this.endFriction(true);this.status='unavailable';this.onChange?.(this.snapshot());return false;}
  }
  updateFriction(speed=0){
    if(!this.frictionIntent)return;
    this.frictionSpeed=Number.isFinite(speed)?Math.max(0,speed):0;
    const amount=Math.min(1,Math.max(0,(this.frictionSpeed-8)/1100));
    if(this.friction){const {source,filter,gain}=this.friction,time=this.context.currentTime;
      gain.gain.setTargetAtTime(Math.pow(amount,.55)*.42,time,.025);
      filter.frequency.setTargetAtTime(650+amount*1900,time,.035);
      source.playbackRate.setTargetAtTime(.82+amount*.45,time,.035);
    }
    clearTimeout(this.frictionTimer);
    if(this.frictionSpeed>0)this.frictionTimer=setTimeout(()=>this.updateFriction(0),90);
  }
  endFriction(immediate=false){
    this.frictionIntent=false;this.frictionSpeed=0;this.frictionGeneration++;clearTimeout(this.frictionTimer);
    const voice=this.friction;if(!voice)return;
    this.friction=null;
    try{
      voice.gain.gain.setTargetAtTime(0,this.context.currentTime,.018);
      voice.source.stop(this.context.currentTime+(immediate?0:.09));
      if(immediate){voice.source.disconnect();voice.filter.disconnect();voice.gain.disconnect();}
    }catch{}
  }
  stop(){this.revision++;this.endFriction(true);for(const source of this.voices){try{source.stop();source.disconnect();}catch{}}this.voices.clear();}
  destroy(){if(this.destroyed)return;this.destroyed=true;this.stop();this.cache.clear();this.master?.disconnect();this.context?.close().catch(()=>{});this.onChange=null;}
}

export function bindControlSounds(root,sound){
  const abort=new AbortController(),signal=abort.signal,lastRange=new WeakMap();
  const valid=element=>element&&!element.disabled&&element.getAttribute('aria-disabled')!=='true'&&!element.closest('[inert]');
  const playFor=element=>{
    if(!valid(element))return;let cue=element.dataset.lgSound;
    if(cue==='toggle')cue=(element.checked??(element.getAttribute('aria-checked')==='true'||element.getAttribute('aria-pressed')==='true'))?'switchOn':'switchOff';
    if(cue==='favorite')cue=element.getAttribute('aria-pressed')==='true'?'favorite':'switchOff';
    if(cue)sound.play(cue);
  };
  root.addEventListener('click',event=>{const element=event.target.closest('[data-lg-sound]');if(root.contains(element)&&element?.type!=='checkbox'&&element?.type!=='range')playFor(element);},{signal});
  root.addEventListener('change',event=>{if(event.target.type==='checkbox')playFor(event.target);},{signal});
  root.addEventListener('input',event=>{
    const input=event.target;if(!valid(input))return;
    if(input.matches('input[type=range][data-lg-sound]')){const value=Number(input.value),previous=lastRange.get(input);lastRange.set(input,value);if(previous!==value)sound.play('tick');}
    if(input.dataset.lgSound==='type'&&!event.isComposing)sound.play('type');
  },{signal});
  root.addEventListener('keydown',event=>{if(event.target.dataset.lgSoundKey&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))sound.play(event.target.dataset.lgSoundKey);},{signal});
  root.addEventListener('lg:gesture',event=>{
    const {phase,mode,moved}=event.detail;
    if(phase==='drag'&&['free','tether'].includes(mode))sound.play('grab');
    if(phase==='end'&&moved){if(mode==='toggle')sound.play(event.target.getAttribute('aria-checked')==='true'?'switchOn':'switchOff');else sound.play(mode==='segment'?'select':'release');}
  },{signal});
  root.addEventListener('lg:commit',event=>sound.play(event.detail.cue),{signal});
  let frictionDrag=null;
  const finishFriction=event=>{if(frictionDrag&&(!event||event.pointerId===undefined||event.pointerId===frictionDrag.id)){frictionDrag=null;sound.endFriction();}};
  root.addEventListener('pointerdown',event=>{
    const lens=event.target.closest('[data-glass=free],.lg-drop-selector,.lg-xy-pad,[data-lg-friction]');
    if(event.button!==0||!event.isPrimary||!valid(lens)||!root.contains(lens)||(lens.matches('.lg-xy-pad')&&lens.parentElement.dataset.variant!=='lens'))return;
    frictionDrag={id:event.pointerId,x:event.clientX,y:event.clientY,time:event.timeStamp};sound.startFriction();
  },{signal,passive:true});
  root.addEventListener('pointermove',event=>{
    if(frictionDrag?.id!==event.pointerId)return;
    const dt=Math.max(8,event.timeStamp-frictionDrag.time),speed=Math.hypot(event.clientX-frictionDrag.x,event.clientY-frictionDrag.y)*1000/dt;
    frictionDrag={id:event.pointerId,x:event.clientX,y:event.clientY,time:event.timeStamp};sound.updateFriction(speed);
  },{signal,passive:true});
  for(const type of ['pointerup','pointercancel','lostpointercapture'])root.addEventListener(type,finishFriction,{signal});
  root.addEventListener('keydown',event=>{if(event.key==='Escape')finishFriction();},{signal});
  const document=root.ownerDocument||root;document.addEventListener('visibilitychange',()=>{if(document.hidden){finishFriction();sound.stop();}},{signal});
  document.defaultView?.addEventListener('blur',()=>{finishFriction();sound.stop();},{signal});
  return ()=>{finishFriction();abort.abort();};
}
