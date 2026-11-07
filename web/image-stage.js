"use strict";

// Viewing state stays here; this component never receives the selection store.
globalThis.createImageStage = function ({root, hint, viewer, installImages, report, onViewChange=()=>{}}) {
  const motion=matchMedia("(prefers-reduced-motion: reduce)");
  const animations=new Set();
  let item=null,page=0,queued=0,busy=false,revision=0;
  const owner={};
  function imageAt(index){return item.pageImages[index];}
  function sizeStack(){
    if(!item || !root.clientWidth)return;
    const image=imageAt(page),img=root.querySelector(`[data-stage-page="${page}"] img`);
    const width=image.width || img?.naturalWidth || item.width || 1;
    const height=image.height || img?.naturalHeight || item.height || 1;
    const scale=Math.min(Math.max(1,root.clientWidth-112)/width,Math.max(1,root.clientHeight-54)/height);
    const stack=root.querySelector(".image-stack");
    if(stack){stack.style.width=`${width*scale}px`;stack.style.height=`${height*scale}px`;}
  }
  function card(index,rank){
    const image=imageAt(index),button=document.createElement("button");
    button.type="button";button.className="deck-card stage-card";button.dataset.stagePage=String(index);
    button.style.zIndex=String(8-rank);
    button.style.transform=`translateY(${rank*10}px) scale(${1-rank*.045}) rotate(${rank*1.5}deg)`;
    button.tabIndex=rank===0?0:-1;button.setAttribute("aria-hidden",String(rank!==0));
    button.style.pointerEvents=rank===0?"auto":"none";
    button.setAttribute("aria-label",`放大第 ${index+1} 张`);
    const img=new Image();const source=image.regular || (index===0?item.thumb:"");if(source)img.src=source;img.alt=`${item.title} 第 ${index+1} 张`;
    img.dataset.detailArtwork=item.id;img.dataset.detailPage=String(index);img.decoding="async";
    button.append(img);button.onclick=openViewer;return button;
  }
  function paint(){
    const stack=root.querySelector(".image-stack");stack.replaceChildren();
    for(let rank=0;rank<Math.min(3,item.pages);rank++)stack.append(card((page+rank)%item.pages,rank));
    sizeStack();installImages(root);
    hint.textContent=`${page+1} / ${item.pages} · 点击主图放大`;
    root.querySelectorAll(".stage-arrow").forEach(button=>{button.disabled=item.pages<2;});
    onViewChange();
  }
  function setVisible(value){if(!value && viewer.owns(owner))viewer.close();}
  function suspend(){
    setVisible(false);
    revision++;queued=0;busy=false;
    animations.forEach(animation=>animation.cancel());animations.clear();
    if(viewer.owns(owner))viewer.close();
  }
  function reset(){suspend();item=null;page=0;root.replaceChildren();}
  function render(next,{preserve=false}={}){
    const previous=preserve && item?.id===next.id?page:0;
    revision++;queued=0;busy=false;
    animations.forEach(animation=>animation.cancel());animations.clear();
    if(viewer.owns(owner))viewer.close();
    item=next;page=Math.min(previous,next.pages-1);
    root.classList.add("image-stage");
    root.innerHTML='<button type="button" class="stage-arrow stage-prev" aria-label="上一张">←</button><div class="image-stack"></div><button type="button" class="stage-arrow stage-next" aria-label="下一张">→</button>';
    root.querySelector(".stage-prev").onclick=()=>void turn(-1);
    root.querySelector(".stage-next").onclick=()=>void turn(1);
    paint();
    return Math.min(next.pages,3);
  }
  async function turn(direction){
    if(!item || item.pages<2)return;
    queued+=direction;if(busy)return;busy=true;
    const generation=revision;
    try{
      while(queued && generation===revision){
        const step=Math.sign(queued);queued-=step;
        const next=(page+step+item.pages)%item.pages;
        if(!motion.matches && !viewer.owns(owner)){
          const stack=root.querySelector(".image-stack"),front=stack.querySelector(`[data-stage-page="${page}"]`);
          let incoming=stack.querySelector(`[data-stage-page="${next}"]`);
          if(!incoming){incoming=card(next,3);stack.append(incoming);installImages(stack);}
          const route=[
            {transform:"translate(0,0) scale(1) rotate(0)",zIndex:10,offset:0},
            {transform:"translate(36%,22%) scale(.94) rotate(13deg)",zIndex:10,offset:.48},
            {transform:"translate(36%,22%) scale(.94) rotate(13deg)",zIndex:0,offset:.52},
            {transform:"translate(0,28px) scale(.87) rotate(4deg)",zIndex:0,offset:1}
          ];
          const lift=[{transform:"translateY(10px) scale(.955) rotate(1.5deg)",zIndex:8},{transform:"translateY(0) scale(1) rotate(0)",zIndex:8}];
          const a=front.animate(step>0?route:lift.slice().reverse(),{duration:350,fill:"forwards",easing:"cubic-bezier(.4,0,.2,1)"});
          const b=incoming.animate(step>0?lift:route.slice().reverse().map((frame,i)=>({...frame,offset:[0,.48,.52,1][i]})),{duration:350,fill:"forwards",easing:"cubic-bezier(.4,0,.2,1)"});
          animations.add(a);animations.add(b);
          await Promise.all([a.finished,b.finished]);animations.delete(a);animations.delete(b);
        }
        if(generation!==revision)return;
        page=next;paint();
      }
    }catch(error){
      // Cancelling on navigation is a normal resource handoff.
      if(generation===revision){queued=0;report(error.message || "图片切换失败，请重试");paint();}
    }finally{if(generation===revision)busy=false;}
  }
  function openViewer(){
    if(!item || busy)return;
    const surface=root.closest(".workspace-page,.basket-page"),top=surface.scrollTop,left=surface.scrollLeft;
    const openedRevision=revision;
    viewer.open({owner,item,page,onPage:index=>{page=index;onViewChange();},onClose:()=>{
      if(revision!==openedRevision || !item)return;
      paint();
      surface.scrollTop=top;surface.scrollLeft=left;
    }});
  }
  root.addEventListener("keydown",event=>{
    if(event.key==="ArrowLeft" || event.key==="ArrowRight"){event.preventDefault();void turn(event.key==="ArrowRight"?1:-1);}
  });
  root.addEventListener("load",sizeStack,true);
  new ResizeObserver(sizeStack).observe(root);
  document.addEventListener("visibilitychange",()=>{if(document.hidden)suspend();});
  window.addEventListener("pagehide",suspend);
  function clear(){reset();viewer.clear();}
  function restorePage(index){
    if(!item)return;
    revision++;queued=0;busy=false;
    animations.forEach(animation=>animation.cancel());animations.clear();
    page=Math.min(Math.max(0,index),item.pages-1);paint();
  }
  return {render,reset,suspend,setVisible,clear,restorePage,state:()=>({page})};
};
