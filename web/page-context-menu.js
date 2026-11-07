"use strict";

// Blank-space actions leave text editing and artwork controls to their owners.
globalThis.createPageContextMenu = function ({refresh,allowed}) {
  const menu=document.createElement("div"),button=document.createElement("button");
  menu.className="text-context-menu page-context-menu";menu.hidden=true;
  menu.setAttribute("role","menu");menu.setAttribute("aria-label","页面操作");
  button.type="button";button.textContent="刷新";button.setAttribute("role","menuitem");
  menu.append(button);document.body.append(menu);
  let previousFocus=null;
  function close(refocus=false){
    if(menu.hidden)return;
    menu.hidden=true;
    if(refocus && previousFocus?.isConnected)previousFocus.focus({preventScroll:true});
  }
  function open(x,y){
    previousFocus=document.activeElement;button.disabled=!allowed();menu.hidden=false;
    const box=menu.getBoundingClientRect();
    menu.style.left=`${Math.max(8,Math.min(x,innerWidth-box.width-8))}px`;
    menu.style.top=`${Math.max(8,Math.min(y,innerHeight-box.height-8))}px`;
    menu.focus({preventScroll:true});if(!button.disabled)button.focus({preventScroll:true});
  }
  menu.tabIndex=-1;
  button.onclick=()=>{close(true);if(allowed())void refresh();};
  document.addEventListener("contextmenu",event=>{
    close();
    if(event.defaultPrevented || !(event.target instanceof Element) || document.querySelector("dialog[open]"))return;
    if(event.target.closest("[hidden],[inert],a,button,input,textarea,select,label,[contenteditable],[role=button],svg,img,canvas,video,.gallery-card,.batch-collection,.artist-candidate,.page-select,figure"))return;
    event.preventDefault();open(event.clientX,event.clientY);
  });
  document.addEventListener("pointerdown",event=>{if(!menu.contains(event.target))close();},true);
  window.addEventListener("scroll",()=>close(),true);
  window.addEventListener("resize",()=>close());window.addEventListener("blur",()=>close());
  window.addEventListener("keydown",event=>{
    if(menu.hidden)return;
    if(["Escape","Tab","ArrowDown","ArrowUp","Home","End"].includes(event.key)){
      event.preventDefault();event.stopImmediatePropagation();
      if(event.key==="Escape" || event.key==="Tab")close(true);
      else if(!button.disabled)button.focus({preventScroll:true});
    }
  },true);
  return {close};
};
