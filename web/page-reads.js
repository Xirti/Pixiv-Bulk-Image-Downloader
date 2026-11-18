"use strict";

// One GET per content query. Each reader owns only its subscription, not its peers.
globalThis.createPageReads=function(request){
  const pending=new Map();
  function read(url,{signal}={}){
    if(signal?.aborted)return Promise.reject(signal.reason);
    const parsed=new URL(url,location.origin);
    const background=parsed.searchParams.get("background")==="1";
    parsed.searchParams.delete("background");parsed.searchParams.sort();
    const key=parsed.pathname+parsed.search;
    let entry=pending.get(key);
    if(!entry){
      const controller=new AbortController();
      entry={controller,readers:new Set(),url,background,started:false,promote:null};pending.set(key,entry);
      entry.promise=Promise.resolve().then(()=>{
        controller.signal.throwIfAborted();
        entry.started=true;
        return request(entry.url,{
          signal:controller.signal,
          onPromote:callback=>{entry.promote=callback;},
        });
      });
      const finished=()=>{if(pending.get(key)===entry)pending.delete(key);};
      entry.promise.then(finished,finished);
    }else if(!background && entry.background){
      entry.background=false;
      if(entry.started)entry.promote?.();
      else entry.url=url;
    }
    return new Promise((resolve,reject)=>{
      const finish=(result,value)=>{
        if(!entry.readers.delete(cancel))return;
        signal?.removeEventListener("abort",cancel);result(value);
      };
      const cancel=()=>{
        finish(reject,signal?.reason || new DOMException("页面读取已取消","AbortError"));
        if(!entry.readers.size){
          if(pending.get(key)===entry)pending.delete(key);
          entry.controller.abort();
        }
      };
      entry.readers.add(cancel);signal?.addEventListener("abort",cancel,{once:true});
      entry.promise.then(value=>finish(resolve,value),error=>finish(reject,error));
    });
  }
  function clear(){
    for(const entry of [...pending.values()])for(const cancel of [...entry.readers])cancel();
    pending.clear();
  }
  return {read,clear};
};
