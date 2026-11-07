"use strict";

// Keep the loaded image itself; recreating <img src> repeats no-store proxy reads.
globalThis.createPreviewImageCache = function ({retainUnused=false,maxImages=Infinity}={}) {
  const pages=new Map();
  function drop(key){
    for(const image of pages.get(key).values())image.remove();
    pages.delete(key);
  }
  function park(root){for(const image of root.querySelectorAll("img"))image.remove();}
  function trim(){
    if(maxImages===Infinity)return;
    let count=[...pages.values()].reduce((total,images)=>total+images.size,0);
    if(count<=maxImages)return;
    const candidates=[...pages.values()].flatMap(images=>[...images].filter(([,image])=>!image.isConnected).map(([source,image])=>({images,source,image})))
      .sort((a,b)=>Number(a.image.complete && a.image.naturalWidth>0)-Number(b.image.complete && b.image.naturalWidth>0));
    for(const {images,source,image} of candidates){
      if(count<=maxImages)break;
      image.remove();images.delete(source);count--;
    }
  }
  function place(images,placeholder,source){
    const identity=placeholder.dataset.previewArtwork || source;
    let image=images.get(identity);
    if(image && ((image.complete && !image.naturalWidth) || image.classList.contains("image-unavailable"))){
      image.remove();images.delete(identity);image=null;
    }
    if(image){
      const previous=(image.getAttribute("src") || "").replace(/[?&]warm=1$/,"");
      if(source && !image.naturalWidth && previous!==source)image.src=source;
      image.alt=placeholder.alt;image.className=placeholder.className;placeholder.replaceWith(image);
    }
    else{if(!source)return placeholder;image=placeholder;image.src=source;}
    images.delete(identity);images.set(identity,image);
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
    if(!retainUnused)for(const [source,image] of images)if(!used.has(source)){image.remove();images.delete(source);}
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
      while(cursor<Math.min(limit,items.length) && !signal.aborted){
        const item=items[cursor++],identity=String(item.id);
        if(!item.thumb)continue;
        let image=images.get(identity);
        if(image?.complete && !image.naturalWidth){images.delete(identity);image=null;}
        if(!image){
          image=new Image();image.dataset.previewArtwork=identity;image.alt=item.title;image.decoding="async";
          image.src=item.thumb+(item.thumb.includes("?")?"&":"?")+`warm=${warm}`;images.set(identity,image);
        }
        const abort=()=>{if(!image.isConnected && !image.complete){image.removeAttribute("src");if(images.get(identity)===image)images.delete(identity);}};
        signal.addEventListener("abort",abort,{once:true});
        try{await image.decode();}
        catch(error){if(!signal.aborted && images.get(identity)===image && !image.isConnected)images.delete(identity);}
        finally{signal.removeEventListener("abort",abort);}
      }
    }
    await Promise.all([worker(),worker()]);
    trim();
  }
  return {park,mount,assign,prune,clear,prepare};
};
