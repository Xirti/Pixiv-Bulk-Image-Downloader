"use strict";
// Explicit original-image requests only. The shared viewer pins its visible entry.
globalThis.createDetailImageCache=function({fetchBytes,maxBytes=96*1024*1024,maxImages=6,ttl=300000}){
  const entries=new Map();let bytes=0,pinned=null;
  const key=(item,page)=>`${item.id}:${page}`;
  function drop(entry){
    if(entries.get(entry.key)!==entry)return;
    entries.delete(entry.key);
    clearTimeout(entry.timer);entry.controller.abort();
    if(entry.url){URL.revokeObjectURL(entry.url);bytes-=entry.size;}
  }
  function trim(size){
    if(size>maxBytes)throw new Error("原图超出预览缓存容量，可通过下载保存查看");
    let ready=0;
    for(const entry of entries.values())if(entry.url)ready++;
    while(bytes+size>maxBytes || ready>=maxImages){
      let old;
      for(const entry of entries.values())if(entry.url && entry.key!==pinned){old=entry;break;}
      if(!old)throw new Error("原图缓存空间不足，请关闭预览后重试");
      drop(old);ready--;
    }
  }
  function peek(item,page){
    const entry=entries.get(key(item,page));
    if(entry?.url && performance.now()<entry.expires)return entry.url;
    return null;
  }
  function pin(item,page){
    const id=key(item,page);
    if(pinned===id)return;
    unpin();pinned=id;
  }
  function unpin(){
    const previous=entries.get(pinned);pinned=null;
    if(previous && (!previous.url || performance.now()>=previous.expires))drop(previous);
  }
  function get(item,page){
    const id=key(item,page);let entry=entries.get(id);
    if(entry?.url && performance.now()>=entry.expires){drop(entry);entry=null;}
    if(entry){entries.delete(id);entries.set(id,entry);return entry.promise;}
    const source=item.pageImages[page].original;
    if(!source)return Promise.reject(new Error("这张图片暂未提供原图"));
    entry={key:id,controller:new AbortController(),url:null,size:0,timer:null,expires:0};
    entries.set(id,entry);
    entry.promise=(async()=>{
      try{
        const data=await fetchBytes(source,{signal:entry.controller.signal});
        entry.controller.signal.throwIfAborted();trim(data.byteLength);
        entry.url=URL.createObjectURL(new Blob([data]));entry.size=data.byteLength;bytes+=entry.size;
        entry.expires=performance.now()+ttl;
        entry.timer=setTimeout(()=>{if(pinned!==id)drop(entry);},ttl);
        return entry.url;
      }catch(error){drop(entry);throw error;}
    })();
    return entry.promise;
  }
  function clear(){pinned=null;for(const entry of [...entries.values()])drop(entry);}
  window.addEventListener("pagehide",clear);
  return {get,peek,pin,unpin,clear,invalidate(item,page){const entry=entries.get(key(item,page));if(entry)drop(entry);}};
};
