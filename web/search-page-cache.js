"use strict";

// Foreground pagination can adopt a prepared page or its existing request.
globalThis.createSearchPageCache = function ({cancelRequest,maxPages=8}) {
  const entries=new Map();
  const key=(query,page,filters,snapshot)=>JSON.stringify([query,snapshot,page,filters]);
  function drop(identity){
    const entry=entries.get(identity);if(!entry)return;
    entry.controller?.abort();if(entry.pending)cancelRequest(entry.id);entries.delete(identity);
  }
  function trim(){while(entries.size>maxPages)drop(entries.keys().next().value);}
  function remember(identity,data){const previous=entries.get(identity);entries.set(identity,{...previous,data,warm:false,pending:false});trim();}
  function promote(identity){
    const entry=entries.get(identity);if(!entry)return null;
    entry.warm=false;entries.delete(identity);entries.set(identity,entry);return entry;
  }
  function warm(identity,id,read,onReady){
    if(entries.has(identity))return entries.get(identity).promise || Promise.resolve(entries.get(identity).data);
    const entry={id,warm:true,pending:true,controller:new AbortController(),data:null,promise:null};entries.set(identity,entry);trim();
    entry.promise=read(entry.controller.signal).then(data=>{
      if(entry.controller.signal.aborted)return null;
      if(data.prefetchSkipped){entries.delete(identity);return null;}
      entry.data=data;entry.pending=false;onReady(data,entry.controller.signal);return data;
    }).catch(error=>{if(entries.get(identity)===entry)entries.delete(identity);throw error;});
    return entry.promise;
  }
  function cancelWarm(keep=null){for(const [identity,entry] of entries)if(identity!==keep){if(entry.warm)drop(identity);else if(!entry.pending)entry.controller?.abort();}}
  function prune(retained){for(const [identity,entry] of entries)if(!entry.warm && !retained.has(identity))drop(identity);}
  function warmKeys(){return [...entries].filter(([,entry])=>entry.warm).map(([identity])=>identity);}
  function clear(){for(const identity of entries.keys())drop(identity);}
  return {key,remember,promote,warm,cancelWarm,prune,warmKeys,cancel:drop,clear,has:identity=>entries.has(identity)};
};
