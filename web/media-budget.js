"use strict";

// Shared retention budget for decoded list images. Visible DOM and in-flight
// preparation are protected; originals and animation frames have separate caps.
globalThis.mediaBudget=(()=>{
  const entries=new Map();let maximum=256*1024*1024,scheduled=false,clock=0;
  const estimate=image=>Math.max(1,image.naturalWidth || 540)*Math.max(1,image.naturalHeight || 540)*4;
  function trim(){
    scheduled=false;
    let bytes=0;for(const [image,entry] of entries){entry.bytes=estimate(image);bytes+=entry.bytes;}
    if(bytes<=maximum)return;
    const cold=[...entries].filter(([image,entry])=>!image.isConnected && !entry.busy())
      .sort((a,b)=>a[1].priority-b[1].priority || a[1].touched-b[1].touched);
    for(const [image,entry] of cold){
      if(bytes<=maximum)break;
      entries.delete(image);image.removeEventListener("load",schedule);entry.drop();
      image.removeAttribute("src");image.remove();bytes-=entry.bytes;
    }
  }
  function schedule(){if(!scheduled){scheduled=true;queueMicrotask(trim);}}
  function forget(image){if(entries.delete(image))image.removeEventListener("load",schedule);}
  function track(image,drop,{priority=0,busy=()=>false}={}){
    if(!entries.has(image))image.addEventListener("load",schedule);
    entries.set(image,{drop,priority,busy,touched:++clock,bytes:estimate(image)});schedule();
  }
  return {track,forget,schedule,configure(mode){maximum=(mode==="low"?128:256)*1024*1024;schedule();},
    snapshot(){return {images:entries.size,estimatedBytes:[...entries.keys()].reduce((n,image)=>n+estimate(image),0),budgetBytes:maximum};}};
})();
