"use strict";

// Keep the loaded image itself; recreating <img src> repeats no-store proxy reads.
globalThis.createPreviewImageCache = function ({retainUnused=false,maxImages=Infinity,reuseDetached=false,priority=0}={}) {
  const pages=new Map();
  const decoded=new WeakMap();
  const pending=new Map(),budget=globalThis.mediaBudget;
  function forget(image){
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
    const identity=placeholder.dataset.previewArtwork || source;
    let image=images.get(identity);
    if(!image && reuseDetached){
      for(const previous of pages.values()){
        const candidate=previous.get(identity);
        if(candidate && !candidate.isConnected && candidate.complete && candidate.naturalWidth>0){
          image=candidate;previous.delete(identity);break;
        }
      }
    }
    if(image && ((image.complete && !image.naturalWidth) || image.classList.contains("image-unavailable"))){
      forget(image);images.delete(identity);image=null;
    }
    if(image){
      const previous=(image.getAttribute("src") || "").replace(/[?&]warm=1$/,"");
      if(source && !image.naturalWidth && previous!==source)image.src=source;
      // Keep runtime listeners, but route retries using the current card's data.
      for(const field of ["previewArtwork","basketArtwork","restoreArtwork","sourcePreviewImage"]){
        if(placeholder.dataset[field]===undefined)delete image.dataset[field];
        else image.dataset[field]=placeholder.dataset[field];
      }
      image.alt=placeholder.alt;image.className=placeholder.className;placeholder.replaceWith(image);
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
      const identity=placeholder.dataset.previewArtwork || source;
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
        if(image?.complete && !image.naturalWidth){forget(image);images.delete(identity);image=null;}
        if(image?.complete && image.naturalWidth>0 && decoded.get(image)===image.src){
          track(images,identity,image);continue;
        }
        if(!image){
          image=new Image();image.dataset.previewArtwork=identity;image.alt=item.title;image.decoding="async";
          image.src=item.thumb+(item.thumb.includes("?")?"&":"?")+`warm=${warm}`;images.set(identity,image);
        }
        let consumers=pending.get(image);
        if(!consumers){consumers=new Set();pending.set(image,consumers);}
        let retire;
        const stopped=new Promise(resolve=>{retire=resolve;});
        const consumer={signal,retire};consumers.add(consumer);track(images,identity,image);
        const abort=()=>{
          retire();
          if(!image.isConnected && !image.complete && [...consumers].every(reader=>reader.signal.aborted)
              && images.get(identity)===image){forget(image);images.delete(identity);}
        };
        signal.addEventListener("abort",abort,{once:true});
        const decodingSource=image.src;
        // Removing src can leave Image.decode() pending in WebView/Chromium.
        try{await Promise.race([image.decode().then(()=>decoded.set(image,decodingSource)),stopped]);}
        catch(error){
          if(!signal.aborted && images.get(identity)===image && !image.isConnected
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
