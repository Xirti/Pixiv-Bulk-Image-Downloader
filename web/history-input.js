"use strict";

globalThis.installHistoryInput = function ({navigate,canNavigate}) {
  let lastInput = {direction:0,source:"",time:-Infinity};
  function handle(direction,source) {
    const now = performance.now();
    if (direction === lastInput.direction && source !== lastInput.source && now-lastInput.time < 220) return;
    lastInput = {direction,source,time:now};
    const focused = document.activeElement;
    const textInput = focused?.tagName === "TEXTAREA" || (focused?.tagName === "INPUT" && ["text","search","url","email","password","tel","number"].includes(focused.type));
    if (focused && (focused.isContentEditable || textInput) && !focused.disabled && !focused.readOnly) {
      document.execCommand(direction < 0 ? "undo" : "redo");
      return;
    }
    if (!document.querySelector("dialog[open]") && canNavigate()) navigate(direction);
  }
  for (const type of ["mousedown","mouseup","auxclick"]) {
    document.addEventListener(type,event=>{
      if (event.button !== 3 && event.button !== 4) return;
      event.preventDefault();event.stopPropagation();
      if (type === "mouseup") handle(event.button === 3 ? -1 : 1,"mouse");
    },true);
  }
  document.addEventListener("keydown",event=>{
    let direction = 0;
    if (event.key === "BrowserBack") direction = -1;
    else if (event.key === "BrowserForward") direction = 1;
    else if (event.altKey && !event.ctrlKey && !event.metaKey && event.key === "ArrowLeft") direction = -1;
    else if (event.altKey && !event.ctrlKey && !event.metaKey && event.key === "ArrowRight") direction = 1;
    if (!direction) return;
    event.preventDefault();event.stopPropagation();
    if (!event.repeat) handle(direction,"key");
  },true);
  window.chrome?.webview?.addEventListener("message",event=>{
    const data = event.data;
    if (data?.type === "moku-history" && (data.direction === -1 || data.direction === 1)) handle(data.direction,"native");
  });
};
