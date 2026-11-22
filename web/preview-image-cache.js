"use strict";

// Keep the loaded image itself; recreating <img src> repeats no-store proxy reads.
globalThis.createPreviewImageCache = function ({retainUnused=false,maxImages=Infinity,reuseDetached=false,priority=0,
    promoteRead=globalThis.promotePixivRead,cancelRead=globalThis.notifySearchCancellation,requestId=globalThis.nextSearchRequestId}={}) {
  const pages=new Map();
  const decoded=new WeakMap();
  const pending=new Map(),budget=globalThis.mediaBudget;
  const warmReads=new WeakMap();
  function finishWarm(image,cancel=false){
    const reading=warmReads.get(image);
    if(!reading)return;
    warmReads.delete(image);
    image.removeEventListener("load",reading.loaded);
    image.removeEventListener("error",reading.failed);
    reading.controller.abort();
    if(cancel)void cancelRead?.(reading.id);
  }
  function fallbackWarm(image,reading,cancel){
    if(warmReads.get(image)!==reading)return false;
    const replace=reading.foreground && image.getAttribute("src")===reading.url && !(image.complete && image.naturalWidth);
    finishWarm(image,cancel && replace);
    if(replace)image.src=reading.foreground;
    return Boolean(replace);
  }
  function warmSource(image,source,warm){
    const url=source+(source.includes("?")?"&":"?")+`warm=${warm}`;
    if(!source.startsWith("/api/pixiv/image?") || !promoteRead || !requestId)return url;
    const id=requestId();
    const reading={id,url:url+`&requestId=${encodeURIComponent(id)}`,controller:new AbortController(),foreground:null};
    reading.loaded=()=>finishWarm(image);
    reading.failed=event=>{if(fallbackWarm(image,reading,false))event.stopImmediatePropagation();};
    image.addEventListener("load",reading.loaded);
    image.addEventListener("error",reading.failed);
    image.fetchPriority="low";
    warmReads.set(image,reading);
    return reading.url;
  }
  const cachedSource=image=>imageSourceKey((image.getAttribute("src") || image.dataset.suspendedSrc || "")
    .replace(/[?&]warm=(?:1|account)$/,""));
  function forget(image){
    finishWarm(image,true);
    for(const consumer of pending.get(image) || [])consumer.retire();
    budget?.forget(image);image.removeAttribute("src");image.remove();
  }
  function track(images,identity,image){budget?.track(image,()=>{if(images.get(identity)===image)images.delete(identity);},
    {priority,busy:()=>pending.has(image)});}
  function drop(key){
    for(const image of pages.get(key).values())forget(image);
    pages.delete(key);
  }
  function park(root){for(const image of root.querySelectorAll("img"))image.remove();budget?.schedule();}
  function trim(){
    for(const [key,images] of pages)if(!images.size)pages.delete(key);
    if(maxImages===Infinity)return;
    let count=[...pages.values()].reduce((total,images)=>total+images.size,0);
    if(count<=maxImages)return;
    const candidates=[...pages.values()].flatMap(images=>[...images].filter(([,image])=>!image.isConnected && !pending.has(image)).map(([source,image])=>({images,source,image})))
      .sort((a,b)=>Number(a.image.complete && a.image.naturalWidth>0)-Number(b.image.complete && b.image.naturalWidth>0));
    for(const {images,source,image} of candidates){
      if(count<=maxImages)break;
      forget(image);images.delete(source);count--;
    }
    for(const [key,images] of pages)if(!images.size)pages.delete(key);
  }
  function place(images,placeholder,source){
    const version=imageSourceKey(source),identity=placeholder.dataset.previewArtwork || version;
    let image=images.get(identity);
    if(!image && reuseDetached){
      for(const previous of pages.values()){
        const candidate=previous.get(identity);
        if(candidate && !candidate.isConnected && candidate.complete && candidate.naturalWidth>0 &&
            (!source || cachedSource(candidate)===version)){
          image=candidate;previous.delete(identity);break;
        }
      }
    }
    if(image && image!==placeholder && ((image.complete && !image.naturalWidth) || image.classList.contains("image-unavailable"))){
      forget(image);images.delete(identity);image=null;
    }
    if(image){
      const sameSource=cachedSource(image)===version;
      const reading=warmReads.get(image);
      const previous=(image.getAttribute("src") || "").replace(/[?&]warm=1$/,"");
      if(source && sameSource && reading && !(image.complete && image.naturalWidth)){
        const first=!reading.foreground;
        reading.foreground=source;
        image.fetchPriority="high";
        if(first)void promoteRead(reading.id,reading.controller.signal).catch(()=>fallbackWarm(image,reading,true));
      }else if(source && (!sameSource || !image.naturalWidth && previous!==source)){
        finishWarm(image,true);
        image.src=source;
      }
      // Keep runtime listeners, but route retries using the current card's data.
      for(const field of ["previewArtwork","basketArtwork","restoreArtwork","sourcePreviewImage"]){
        if(placeholder.dataset[field]===undefined)delete image.dataset[field];
        else image.dataset[field]=placeholder.dataset[field];
      }
      image.alt=placeholder.alt;image.className=placeholder.className;
      if(image!==placeholder)placeholder.replaceWith(image);
    }
    else{if(!source)return placeholder;image=placeholder;image.src=source;}
    images.delete(identity);images.set(identity,image);
    track(images,identity,image);
    return image;
  }
  function mount(root,key){
    // Detached images must not retain their former cards, handlers or layout.
    for(const images of pages.values())for(const image of images.values())if(!image.isConnected)image.remove();
    let images=pages.get(key);
    if(!images){images=new Map();pages.set(key,images);}
    const used=new Set();
    for(const placeholder of root.querySelectorAll("img[data-gallery-src]")){
      const source=placeholder.dataset.gallerySrc;
      const identity=placeholder.dataset.previewArtwork || imageSourceKey(source);
      placeholder.removeAttribute("data-gallery-src");
      used.add(identity);
      place(images,placeholder,source);
    }
    if(!retainUnused)for(const [source,image] of images)if(!used.has(source)){forget(image);images.delete(source);}
    trim();
  }
  function assign(key,placeholder,source){
    let images=pages.get(key);
    if(!images){images=new Map();pages.set(key,images);}
    const image=place(images,placeholder,source);trim();return image;
  }
  function prune(retained){for(const key of pages.keys())if(!retained.has(key))drop(key);}
  function clear(){for(const key of pages.keys())drop(key);}
  async function prepare(key,items,signal,{limit=12,warm="1"}={}){
    let images=pages.get(key);if(!images){images=new Map();pages.set(key,images);}
    let cursor=0;
    async function worker(){
      while(cursor<Math.min(limit,items.length) && !signal.aborted && pages.get(key)===images){
        const item=items[cursor++],identity=String(item.id);
        if(!item.thumb)continue;
        let image=images.get(identity);
        if(image && cachedSource(image)!==imageSourceKey(item.thumb)){
          if(image.isConnected)continue;
          forget(image);images.delete(identity);image=null;
        }
        if(image?.complete && !image.naturalWidth){forget(image);images.delete(identity);image=null;}
        if(image?.complete && image.naturalWidth>0 && decoded.get(image)===image.src){
          track(images,identity,image);continue;
        }
        if(!image){
          image=new Image();image.dataset.previewArtwork=identity;image.alt=item.title;image.decoding="async";
          image.src=warmSource(image,item.thumb,warm);images.set(identity,image);
        }
        let consumers=pending.get(image);
        if(!consumers){consumers=new Set();pending.set(image,consumers);}
        let retire;
        const stopped=new Promise(resolve=>{retire=resolve;});
        const consumer={signal,retire};consumers.add(consumer);track(images,identity,image);
        const decodingSource=image.src;
        const abort=()=>{
          retire();
          if(image.src===decodingSource && !image.isConnected && !image.complete && [...consumers].every(reader=>reader.signal.aborted)
              && images.get(identity)===image){forget(image);images.delete(identity);}
        };
        signal.addEventListener("abort",abort,{once:true});
        // Removing src can leave Image.decode() pending in WebView/Chromium.
        try{await Promise.race([image.decode().then(()=>{if(image.src===decodingSource)decoded.set(image,decodingSource);}),stopped]);}
        catch(error){
          if(!signal.aborted && image.src===decodingSource && images.get(identity)===image && !image.isConnected
              && ![...consumers].some(reader=>reader!==consumer && !reader.signal.aborted)){
            forget(image);images.delete(identity);
          }
        }
        finally{
          consumers.delete(consumer);if(!consumers.size)pending.delete(image);
          signal.removeEventListener("abort",abort);budget?.schedule();
        }
      }
    }
    await Promise.all([worker(),worker()]);
    trim();
  }
  return {park,mount,assign,prune,clear,prepare};
};
