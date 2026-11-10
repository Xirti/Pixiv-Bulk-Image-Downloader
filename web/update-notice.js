"use strict";

// A non-modal update puppet. Only its burst accepts pointer input; the rod and
// the rest of its bounding box never cover the page's controls.
globalThis.createUpdateNotice=function({open,report}){
  const button=document.createElement("button");
  button.type="button";button.className="update-puppet";button.hidden=true;
  button.innerHTML=`<span class="update-puppet-motion">
    <svg class="update-puppet-rod" viewBox="0 0 186 168" aria-hidden="true"><path class="update-puppet-rod-shadow" d="M186 152 Q162 152 153 135 L105 77"/><path d="M186 152 Q162 152 153 135 L105 77"/></svg>
    <span class="update-puppet-burst"><svg viewBox="0 0 232 156" preserveAspectRatio="none" aria-hidden="true"><path d="M116 5 130 21 151 9 158 29 185 19 184 41 213 39 202 59 228 72 207 87 225 109 198 113 202 139 175 132 164 153 143 139 122 153 106 136 81 150 75 130 47 139 51 116 22 117 33 96 5 85 27 69 11 49 40 45 37 23 66 30 78 9 98 22Z"/></svg>
      <span class="update-puppet-copy"><strong>可更新！</strong></span>
    </span></span>`;
  document.body.append(button);
  const motion=button.querySelector(".update-puppet-motion");
  let pending=null,animation=null,revision=0;
  const reduced=()=>matchMedia("(prefers-reduced-motion: reduce)").matches;
  function reveal(){
    if(!pending || document.hidden)return;
    ++revision;animation?.cancel();button.hidden=false;button.disabled=false;
    button.setAttribute("aria-label","可更新，前往关于与更新");
    animation=motion.animate(reduced()?[{opacity:0},{opacity:1}]:[
      {transform:"rotate(80deg)",opacity:0,offset:0},
      {transform:"rotate(-7deg)",opacity:1,offset:.62},
      {transform:"rotate(2deg)",opacity:1,offset:.82},
      {transform:"rotate(0deg)",opacity:1,offset:1}
    ],{duration:reduced()?120:860,easing:"cubic-bezier(.22,.75,.24,1)",fill:"both"});
  }
  function clear(){
    pending=null;const current=++revision;
    if(button.hidden)return;
    button.disabled=true;animation?.cancel();
    animation=motion.animate(reduced()?[{opacity:1},{opacity:0}]:[
      {transform:"rotate(0deg)",opacity:1},
      {transform:"rotate(80deg)",opacity:0}
    ],{duration:reduced()?100:300,easing:"cubic-bezier(.4,0,.8,.3)",fill:"both"});
    animation.onfinish=()=>{if(current===revision)button.hidden=true;};
  }
  button.onclick=()=>{clear();void Promise.resolve().then(open).catch(error=>report(error.message));};
  document.addEventListener("visibilitychange",()=>{if(!document.hidden && button.hidden)reveal();});
  return {show(next){pending=next;if(document.hidden){++revision;animation?.cancel();button.hidden=true;}else reveal();},clear};
};
