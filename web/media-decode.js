"use strict";

// Native decoders may settle after their caller has already timed out or left.
globalThis.waitForMediaDecode = function (decode, signal, {timeout=30000,discard}={}) {
  return new Promise((resolve,reject)=>{
    signal.throwIfAborted();
    const operation=new AbortController();
    const cleanup=()=>{
      clearTimeout(timer);
      signal.removeEventListener("abort",abort);
    };
    const interrupt=error=>{
      operation.abort(error);
      cleanup();
      reject(error);
    };
    const abort=()=>interrupt(signal.reason);
    const timer=setTimeout(()=>interrupt(new Error("图片解码超时，可重试")),timeout);
    signal.addEventListener("abort",abort,{once:true});
    Promise.resolve().then(()=>{
      operation.signal.throwIfAborted();
      return decode(operation.signal);
    }).then(value=>{
      if(operation.signal.aborted){discard?.(value);return;}
      cleanup();
      resolve(value);
    },error=>{
      if(operation.signal.aborted)return;
      cleanup();
      reject(error);
    });
  });
};
