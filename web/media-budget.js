"use strict";

// Hidden cards retain their DOM while their image source can be suspended.
globalThis.mediaBudget=(()=>{
  const entries=new Map();let maximum=256*1024*1024,scheduled=false,clock=0;
  const hidden=image=>image.closest("[hidden], [inert]")!==null;
  const estimate=image=>image.getAttribute("src")?Math.max(1,image.naturalWidth || 540)*Math.max(1,image.naturalHeight || 540)*4:0;
  function resume(image,entry){
    const source=entry.suspended;
    entry.suspended=null;delete image.dataset.suspendedSrc;
    if(!image.getAttribute("src"))image.src=source;
    entry.touched=++clock;
  }
  function trim(){
    scheduled=false;
    let bytes=0;
    for(const [image,entry] of entries){
      if(entry.suspended && (image.getAttribute("src") || image.isConnected && !hidden(image)))resume(image,entry);
      entry.bytes=estimate(image);bytes+=entry.bytes;
    }
    if(bytes<=maximum)return;
    const cold=[...entries].filter(([image,entry])=>entry.bytes && !entry.busy() && (!image.isConnected || hidden(image)))
      .sort((a,b)=>a[1].priority-b[1].priority || a[1].touched-b[1].touched);
    for(const [image,entry] of cold){
      if(bytes<=maximum)break;
      if(image.isConnected){
        entry.suspended=image.getAttribute("src");
        image.dataset.suspendedSrc=entry.suspended;
        image.removeAttribute("src");
      }else{
        forget(image);entry.drop();
        image.removeAttribute("src");image.remove();
      }
      bytes-=entry.bytes;
    }
  }
  function schedule(){if(!scheduled){scheduled=true;queueMicrotask(trim);}}
  function forget(image){
    const entry=entries.get(image);
    if(!entry)return;
    if(entry.suspended)delete image.dataset.suspendedSrc;
    entries.delete(image);image.removeEventListener("load",schedule);
  }
  function track(image,drop,{priority=0,busy=()=>false}={}){
    const previous=entries.get(image);
    if(!previous)image.addEventListener("load",schedule);
    entries.set(image,{drop,priority,busy,touched:++clock,suspended:previous?.suspended || null});schedule();
  }
  return {track,forget,schedule,configure(mode){maximum=(mode==="low"?128:256)*1024*1024;schedule();},
    snapshot(){return {images:[...entries.values()].filter(entry=>!entry.suspended).length,
      estimatedBytes:[...entries.keys()].reduce((n,image)=>n+estimate(image),0),budgetBytes:maximum};}};
})();
