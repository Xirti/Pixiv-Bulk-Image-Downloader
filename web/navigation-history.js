"use strict";

// Page navigation owns a bounded timeline, not selection or download state.
globalThis.createNavigationHistory = function ({capture,restore,changed,allowed,validate=()=>{},prune=()=>{},maxEntries=30}) {
  let entries=[],cursor=-1,restoring=false,revision=0,activeRestore=null;
  function status(){return {back:cursor>0,forward:cursor>=0 && cursor<entries.length-1,restoring};}
  const cancelled=()=>Object.assign(new Error("页面读取已取消"),{name:"AbortError"});
  async function restoreEntry(state,generation){
    const controller=new AbortController(),entry={controller};
    activeRestore=entry;
    const scope={
      signal:controller.signal,
      check(){
        if(controller.signal.aborted || generation!==revision || activeRestore!==entry)throw cancelled();
        validate(state);
      },
      async read(run){
        scope.check();
        const value=await run(scope.signal);
        scope.check();
        return value;
      },
      afterPaint(paint){
        scope.check();
        return new Promise((resolve,reject)=>{
          const abort=()=>{cancelAnimationFrame(frame);reject(cancelled());};
          const frame=requestAnimationFrame(()=>{
            controller.signal.removeEventListener("abort",abort);
            try{scope.check();resolve(paint());}catch(error){reject(error);}
          });
          controller.signal.addEventListener("abort",abort,{once:true});
        });
      },
    };
    try{scope.check();await restore(state,scope);scope.check();}
    finally{
      controller.abort();
      if(activeRestore===entry)activeRestore=null;
    }
  }
  function record(state){
    if(restoring)return;
    if(cursor>=0 && entries[cursor].key===state.key)entries[cursor]=state;
    else{
      entries.splice(cursor+1);entries.push(state);
      if(entries.length>maxEntries)entries.shift();
      cursor=entries.length-1;
    }
    prune(entries,cursor);changed(status());
  }
  async function go(direction){
    const target=cursor+direction;
    if(restoring || !allowed() || target<0 || target>=entries.length)return false;
    cancelRestore();
    const generation=revision;
    const previous=capture();entries[cursor]=previous;
    prune(entries,cursor);
    const state=entries[target];restoring=true;changed(status());
    try{
      await restoreEntry(state,generation);
      if(generation!==revision)return false;
      const restored=capture();cursor=target;entries[cursor]=restored;prune(entries,cursor);return true;
    }catch(error){
      if(generation!==revision)return false;
      // Restore the outgoing page if the target failed after clearing the UI.
      try{
        await restoreEntry(previous,generation);
        if(generation===revision)entries[cursor]=capture();
      }catch(recoveryError){
        if(generation!==revision)return false;
        throw new AggregateError([error,recoveryError],`${error.message}；原页面恢复失败：${recoveryError.message}`);
      }
      throw error;
    }finally{
      if(generation===revision){restoring=false;changed(status());}
    }
  }
  function clear(){revision++;activeRestore?.controller.abort();activeRestore=null;entries=[];cursor=-1;restoring=false;changed(status());}
  function cancelRestore(){if(restoring || activeRestore){revision++;activeRestore?.controller.abort();activeRestore=null;restoring=false;changed(status());}}
  function restoreSnapshot(state){cancelRestore();return restoreEntry(state,revision);}
  function find(predicate){for(let index=cursor;index>=0;index--)if(predicate(entries[index]))return entries[index];return null;}
  function goTo(key){for(let index=cursor-1;index>=0;index--)if(entries[index].key===key)return go(index-cursor);return Promise.resolve(false);}
  return {record,go,goTo,find,clear,cancelRestore,restore:restoreSnapshot,status,previous:()=>cursor>0?entries[cursor-1]:null};
};
