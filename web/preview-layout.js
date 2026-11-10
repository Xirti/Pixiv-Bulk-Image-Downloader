"use strict";
// Both layouts share cards, decoded nodes, selection and source callbacks.
(() => {
  const regularFactory=globalThis.createGalleryInteraction;
  let mode="adaptive";
  let action="expand";
  const owners=new Set();
  document.documentElement.dataset.previewLayout=mode;
  globalThis.applyPreviewLayout=value=>{
    if(value===mode)return;
    owners.forEach(owner=>owner.change(value));
    mode=value;document.documentElement.dataset.previewLayout=value;
  };
  globalThis.getPreviewLayout=()=>mode;
  globalThis.applyPreviewAction=value=>{
    if(value===action)return;
    action=value;owners.forEach(owner=>owner.change(mode));
  };

  function adaptive(options){
    const {grid,scrollSurface=window}=options;
    let rows=[],cards=[],base=null,active=-1,leaving=null,pointer=null,lastWidth=0,resizeFrame=null,checkFrame=null;
    const clicks=createCardInteraction({interval:()=>650,single:card=>toggle(cards.indexOf(card)),double:card=>open(cards.indexOf(card))});
    function clearTimers(){clearTimeout(leaving);leaving=null;clicks.cancel();cancelAnimationFrame(checkFrame);checkFrame=null;}
    function apply(boxes,height,duration=0){
      grid.style.height=height+"px";grid.style.setProperty("--adaptive-motion",duration+"ms");
      cards.forEach((card,i)=>{const b=boxes[i];Object.assign(card.style,{left:"0px",top:"0px",width:b.width+"px",height:b.height+"px",transform:`translate3d(${b.x}px,${b.y}px,0)`});});
    }
    function measure(){
      if(!grid.classList.contains("adaptive-preview") || grid.clientWidth<=0)return;
      clearTimers();active=-1;cards.forEach(card=>{card.classList.remove("adaptive-expanded");card.querySelector(".poster")?.setAttribute("aria-expanded","false");});
      lastWidth=grid.clientWidth;
      const chrome=parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--chrome-height")) || 48;
      base=computeAdaptiveLayout(rows,lastWidth,{maxHeight:Math.max(120,Math.min(380,innerHeight-chrome-70))});
      apply(base.boxes,base.height);base.documentHeight=scrollSurface===window?document.documentElement.scrollHeight:scrollSurface.scrollHeight;
    }
    function reset({immediate=false,slow=false}={}){
      clearTimers();
      if(active<0 || !base)return;
      cards[active].classList.remove("adaptive-expanded");cards[active].querySelector(".poster")?.setAttribute("aria-expanded","false");
      if(!immediate){
        const old=cards.map(card=>card.getBoundingClientRect());
        const visible=old.map((box,i)=>({box,i})).filter(({box})=>box.bottom>60 && box.top<innerHeight);
        const anchor=slow?visible.sort((a,b)=>Math.abs(a.box.top+a.box.height/2-innerHeight/2)-Math.abs(b.box.top+b.box.height/2-innerHeight/2))[0]:{box:old[active],i:active};
        if(anchor){
          const r=grid.getBoundingClientRect(),b=base.boxes[anchor.i],delta=r.top+b.y+b.height/2-anchor.box.top-anchor.box.height/2;
          const current=scrollSurface===window?window.scrollY:scrollSurface.scrollTop;
          const top=Math.max(0,Math.min(current+delta,base.documentHeight-(scrollSurface===window?innerHeight:scrollSurface.clientHeight)));
          scrollSurface.scrollTo({top,left:0,behavior:"instant"});
          const moved=grid.getBoundingClientRect();grid.style.setProperty("--adaptive-motion","0ms");
          cards.forEach((card,i)=>Object.assign(card.style,{width:old[i].width+"px",height:old[i].height+"px",transform:`translate3d(${old[i].left-moved.left}px,${old[i].top-moved.top}px,0)`}));
          grid.getBoundingClientRect();
        }
      }
      active=-1;apply(base.boxes,base.height,immediate?0:slow?800:240);
    }
    function open(index){if(options.allowed() && rows[index]){reset({immediate:true});options.openArtwork(index);}}
    function toggle(index){
      if(!options.allowed() || !base || index<0)return;
      if(active===index){reset();return;}
      const image=cards[index].querySelector(".poster img");
      if(!image?.naturalWidth){options.report("预览图尚未加载完成，请稍后再试");return;}
      clearTimers();
      if(active>=0){cards[active].classList.remove("adaptive-expanded");cards[active].querySelector(".poster").setAttribute("aria-expanded","false");}
      active=index;
      const r=grid.getBoundingClientRect(),b=base.boxes[index],ratio=b.width/b.height;
      const bounds=mokuPreviewBounds(scrollSurface);
      const {width:w,height:h}=mokuPreviewSize({width:b.width,height:b.height,ratio,maxWidth:bounds.right-bounds.left,maxHeight:bounds.bottom-bounds.top});
      const cx=Math.min(bounds.right-w/2,Math.max(bounds.left+w/2,r.left+b.x+b.width/2));
      const cy=Math.min(bounds.bottom-h/2,Math.max(bounds.top+h/2,r.top+b.y+b.height/2));
      const focus={x:cx-r.left-w/2,y:cy-r.top-h/2,width:w,height:h};
      const boxes=base.boxes.map((box,i)=>{
        if(i===index)return focus;
        const next={...box};
        if(box.y<b.y-.5)next.y+=Math.min(0,focus.y-b.y);
        else if(box.y>b.y+.5)next.y+=Math.max(0,focus.y+focus.height-b.y-b.height);
        else if(box.x<b.x)next.x+=Math.min(0,focus.x-b.x);
        else next.x+=Math.max(0,focus.x+focus.width-b.x-b.width);
        return next;
      });
      cards[index].classList.add("adaptive-expanded");cards[index].querySelector(".poster").setAttribute("aria-expanded","true");
      apply(boxes,Math.max(base.height,...boxes.map(box=>box.y+box.height)),260);
    }
    function check(){
      checkFrame=null;
      if(active<0 || clicks.waiting())return;
      const r=cards[active].getBoundingClientRect(),distance=pointer?Math.hypot(Math.max(r.left-pointer.x,0,pointer.x-r.right),Math.max(r.top-pointer.y,0,pointer.y-r.bottom)):0;
      if(r.bottom< -180 || r.top>innerHeight+180 || distance>180){if(leaving===null)leaving=setTimeout(()=>{leaving=null;reset({slow:true});},500);}
      else if(distance<120){clearTimeout(leaving);leaving=null;}
    }
    function scheduleCheck(){if(active>=0 && checkFrame===null)checkFrame=requestAnimationFrame(check);}
    function mount(value){
      clear();rows=value;cards=[...grid.querySelectorAll(".gallery-card")];if(!cards.length)return;grid.classList.add("adaptive-preview");
      cards.forEach((card,index)=>{
        const poster=card.querySelector(".poster");
        poster.onclick=e=>{
          if(!options.allowed())return;
          if(e.detail===0){const r=poster.getBoundingClientRect();pointer={x:r.left+r.width/2,y:r.top+r.height/2};toggle(index);}
          else{pointer={x:e.clientX,y:e.clientY};clicks.click(card,pointer);}
        };
        poster.ondblclick=e=>e.preventDefault();
        poster.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();open(index);}else if(e.key==="Escape"){e.stopPropagation();reset();}};
        card.querySelector(".gallery-author").onclick=e=>{e.preventDefault();e.stopPropagation();if(options.allowed() && !mokuHasSelectedText(e.currentTarget)){reset({immediate:true});options.openArtist(rows[index]);}};
      });
      measure();
    }
    function clear(){
      reset({immediate:true});clearTimers();grid.classList.remove("adaptive-preview");grid.style.removeProperty("height");grid.style.removeProperty("--adaptive-motion");
      cards.forEach(card=>{for(const field of ["width","height","transform","left","top"])card.style.removeProperty(field);card.classList.remove("adaptive-expanded");});base=null;lastWidth=0;rows=[];cards=[];
    }
    new ResizeObserver(()=>{if(grid.classList.contains("adaptive-preview") && grid.clientWidth>0 && Math.abs(grid.clientWidth-lastWidth)>.5 && resizeFrame===null)resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;measure();});}).observe(grid);
    window.addEventListener("resize",()=>{if(grid.classList.contains("adaptive-preview") && resizeFrame===null)resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;measure();});});
    grid.addEventListener("click",e=>{if(!e.target.closest(".poster"))clicks.cancel();});grid.addEventListener("moku-card-action",()=>clicks.cancel());
    window.addEventListener("pointermove",e=>{if(e.pointerType!=="touch"){pointer={x:e.clientX,y:e.clientY};scheduleCheck();}},{passive:true});
    scrollSurface.addEventListener("scroll",()=>{clicks.cancel();scheduleCheck();},{passive:true});
    window.addEventListener("blur",()=>{clicks.cancel();if(active>=0 && leaving===null)leaving=setTimeout(()=>reset({slow:true}),500);});
    return {mount,reset,clear};
  }
  globalThis.createGalleryInteraction=options=>{
    const regular=regularFactory(options),natural=adaptive(options);
    let rows=[],current=mode;
    const popupClicks=createCardInteraction({interval:()=>650,single:card=>{
      if(!options.allowed())return;
      const index=[...options.grid.querySelectorAll(".gallery-card")].indexOf(card);
      globalThis.openListPreview(rows[index],card.querySelector(".poster img"));
    },double:card=>{
      if(options.allowed())options.openArtwork([...options.grid.querySelectorAll(".gallery-card")].indexOf(card));
    }});
    function mount(){
      (current==="adaptive"?natural:regular).mount(rows);
      if(action!=="popup")return;
      options.grid.querySelectorAll(".gallery-card").forEach(card=>{
        const poster=card.querySelector(".poster");
        poster.onclick=e=>{
          if(!options.allowed())return;
          if(e.detail===0){popupClicks.cancel();globalThis.openListPreview(rows[[...options.grid.querySelectorAll(".gallery-card")].indexOf(card)],poster.querySelector("img"));}
          else popupClicks.click(card);
        };
        const keydown=poster.onkeydown;
        poster.onkeydown=e=>{if(e.key==="Escape" || e.key==="Enter")popupClicks.cancel();keydown(e);};
      });
    }
    options.grid.addEventListener("moku-card-action",()=>popupClicks.cancel());
    options.grid.addEventListener("click",e=>{if(!e.target.closest(".poster"))popupClicks.cancel();});
    (options.scrollSurface || window).addEventListener("scroll",()=>popupClicks.cancel(),{passive:true});
    const owner={change(value){preserveViewport(options.scrollSurface || window,()=>{popupClicks.cancel();(current==="adaptive"?natural:regular).clear();current=value;mount();});}};
    owners.add(owner);
    return {mount(value){popupClicks.cancel();rows=value;mount();},reset(value){popupClicks.cancel();(current==="adaptive"?natural:regular).reset(value);},clear(){popupClicks.cancel();regular.clear();natural.clear();rows=[];}};
  };
})();
