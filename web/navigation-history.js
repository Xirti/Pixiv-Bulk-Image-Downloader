"use strict";

globalThis.waitForNavigationFrame = function (signal) {
  signal.throwIfAborted();
  return new Promise((resolve,reject)=>{
    const clean=()=>{
      cancelAnimationFrame(frame);
      clearTimeout(timer);
      signal.removeEventListener("abort",abort);
    };
    const finish=()=>{clean();resolve();};
    const abort=()=>{clean();reject(signal.reason);};
    // Layout reads can complete even when the renderer stops producing frames.
    const frame=requestAnimationFrame(finish);
    const timer=setTimeout(finish,100);
    signal.addEventListener("abort",abort,{once:true});
  });
};

// Page navigation owns a bounded timeline, not selection or download state.
globalThis.createNavigationHistory = function ({capture,restore,changed,allowed,canCapture=()=>true,beforeNavigate=()=>{},validate=()=>{},prune=()=>{},maxEntries=30}) {
  let entries=[],cursor=-1,restoring=false,revision=0,activeRestore=null;
  function status(){return {back:cursor>0,forward:cursor>=0 && cursor<entries.length-1,restoring};}
  const canRecord=()=>!restoring && activeRestore===null && canCapture();
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
      async afterPaint(paint){
        scope.check();
        await waitForNavigationFrame(scope.signal);
        scope.check();
        return paint();
      },
    };
    try{scope.check();await restore(state,scope);scope.check();}
    finally{
      controller.abort();
      if(activeRestore===entry)activeRestore=null;
    }
  }
  function record(state){
    if(!canRecord())return;
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
    const captureOutgoing=canRecord();
    cancelRestore();
    beforeNavigate();
    const generation=revision;
    const previous=captureOutgoing?capture():entries[cursor];entries[cursor]=previous;
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
  return {record,canRecord,go,goTo,find,clear,cancelRestore,restore:restoreSnapshot,status,current:()=>entries[cursor] || null,previous:()=>cursor>0?entries[cursor-1]:null};
};
