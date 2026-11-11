"use strict";

function mokuPreviewBounds(scrollSurface) {
  const chrome=parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--chrome-height")) || 48;
  const header=scrollSurface===window?null:scrollSurface.querySelector(":scope > .account-top, :scope > .workspace-bar, :scope > .viewer-bar");
  const rail=document.querySelector(".page-rail").getBoundingClientRect();
  const dock=document.querySelector(".pagination-dock.is-visible");
  return {left:rail.right+12,top:Math.max(chrome,header?header.getBoundingClientRect().bottom:0)+12,
    right:document.documentElement.clientWidth-12,bottom:(dock?dock.getBoundingClientRect().top:innerHeight)-12};
}

// Expanding reuses the already decoded thumbnail. It owns no network requests.
globalThis.createGalleryInteraction = function ({grid, openArtwork, openArtist, allowed, report, scrollSurface=window}) {
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const clicks = createCardInteraction({interval:()=>650, single:card=>{toggle(card);scheduleCheck();}, double:open});
  const prepared = new Map();
  let rows=[],cards=[],active=null,base=null,cleanupTimer=null,leaveTimer=null,checkFrame=null,pointer=null;

  function cancelLeave() {
    clearTimeout(leaveTimer);
    leaveTimer=null;
  }
  function setMotion(duration, easing) {
    grid.style.setProperty("--gallery-motion",`${duration}ms`);
    grid.style.setProperty("--gallery-ease",easing);
  }
  function clearLayout() {
    grid.classList.remove("gallery-deformed");
    for(const property of ["grid-auto-rows","padding-bottom","--gallery-motion","--gallery-ease"])grid.style.removeProperty(property);
    cards.forEach(card=>{
      for(const property of ["width","height","transform","transition-delay","transition"])card.style.removeProperty(property);
    });
    prepared.forEach(({image})=>{
      image.classList.remove("gallery-preview");
      for(const property of ["width","height","transform","transition-delay","transition"])image.style.removeProperty(property);
    });
    prepared.clear();base=null;cleanupTimer=null;
  }
  function reset({immediate=false,slow=false}={}) {
    clicks.cancel();cancelLeave();clearTimeout(cleanupTimer);
    // Collapse around the currently visible content, not the old expanded row.
    // Rebase the document scroll, then preserve the starting screen boxes with FLIP.
    if(base && !immediate){
      const rect=grid.getBoundingClientRect(),boxes=cards.map(card=>card.getBoundingClientRect());
      const visible=boxes.map((box,index)=>({box,index})).filter(({box})=>box.bottom>0 && box.top<innerHeight && box.right>0 && box.left<document.documentElement.clientWidth);
      const anchor=!slow && active ? {box:boxes[active.index],index:active.index}
        : visible.sort((a,b)=>Math.abs(a.box.top+(a.box.height-54)/2+1-innerHeight/2)-Math.abs(b.box.top+(b.box.height-54)/2+1-innerHeight/2))[0];
      if(anchor){
        const center=anchor.box.top+(anchor.box.height-54)/2+1;
        const delta=rect.top+base.centers[anchor.index].y-center;
        const images=[...prepared.values()].map(({image})=>({image,transform:getComputedStyle(image).transform}));
        cards.forEach((card,index)=>{
          card.style.transition="none";
          Object.assign(card.style,{width:`${boxes[index].width}px`,height:`${boxes[index].height}px`});
        });
        images.forEach(({image,transform})=>{
          image.style.transition="none";image.style.transform=transform;
        });
        const top=Math.min(Math.max(0,(scrollSurface===window?window.scrollY:scrollSurface.scrollTop)+delta),Math.max(0,base.documentHeight-(scrollSurface===window?innerHeight:scrollSurface.clientHeight)));
        scrollSurface.scrollTo({top,left:0,behavior:"instant"});
        const moved=grid.getBoundingClientRect();
        cards.forEach((card,index)=>{
          const col=index%base.columns,row=Math.floor(index/base.columns);
          card.style.transform=`translate3d(${boxes[index].left-moved.left-col*(base.width+14)}px,${boxes[index].top-moved.top-row*(base.height+14)}px,0)`;
        });
        grid.getBoundingClientRect();
        cards.forEach(card=>card.style.removeProperty("transition"));
        prepared.forEach(({image})=>image.style.removeProperty("transition"));
      }
    }
    if(active){
      active.card.classList.remove("gallery-expanded");
      active.card.querySelector(".poster").setAttribute("aria-expanded","false");
      active=null;
    }
    if(!base)return;
    setMotion(slow?800:240,slow?"cubic-bezier(.45,0,.2,1)":"cubic-bezier(.3,0,.2,1)");
    grid.style.paddingBottom="0px";
    cards.forEach(card=>{
      if(!card.style.width)return;
      Object.assign(card.style,{width:`${base.width}px`,height:`${base.height}px`,transform:"translate3d(0,0,0)",transitionDelay:"0ms"});
      const preview=prepared.get(card);
      if(preview){
        const scale=Math.max((base.width-2)/preview.width,(base.height-54)/preview.height);
        preview.image.style.transform=`translate(-50%,-50%) scale(${scale*preview.width/320})`;
        preview.image.style.transitionDelay="0ms";
      }
    });
    if(immediate || motion.matches)clearLayout();
    else cleanupTimer=setTimeout(clearLayout,(slow?800:240)+40);
  }
  function open(card) {
    if(!allowed())return;
    const index=cards.indexOf(card);
    reset({immediate:true});openArtwork(index);
  }
  function toggle(card) {
    if(!allowed())return;
    if(active?.card===card){reset();return;}
    const image=card.querySelector(".poster img");
    if(!image.complete || !image.naturalWidth || !image.naturalHeight){
      report("预览图尚未加载完成，请稍后再展开");return;
    }
    clicks.cancel();cancelLeave();clearTimeout(cleanupTimer);cleanupTimer=null;
    if(active){
      active.card.classList.remove("gallery-expanded");
      active.card.querySelector(".poster").setAttribute("aria-expanded","false");
    }
    const index=cards.indexOf(card);
    active={card,index};
    setMotion(260,"cubic-bezier(.3,0,.2,1)");
    const rect=grid.getBoundingClientRect();
    if(!base){
      const first=cards[0].getBoundingClientRect();
      const centers=cards.map(target=>{
        const poster=target.querySelector(".poster").getBoundingClientRect();
        return {x:poster.left+poster.width/2-rect.left,y:poster.top+poster.height/2-rect.top};
      });
      base={width:first.width,height:first.height,columns:getComputedStyle(grid).gridTemplateColumns.split(" ").length,gridWidth:rect.width,centers,documentHeight:scrollSurface===window?document.documentElement.scrollHeight:scrollSurface.scrollHeight};
      // CSS cannot interpolate auto -> px. Commit numeric, unchanged start boxes first.
      cards.forEach(target=>Object.assign(target.style,{width:`${base.width}px`,height:`${base.height}px`,transform:"translate3d(0,0,0)",transitionDelay:"0ms"}));
      grid.style.gridAutoRows=`${base.height}px`;
      grid.classList.add("gallery-deformed");
    }
    if(!prepared.has(card)){
      const current=card.querySelector(".poster").getBoundingClientRect();
      const initialScale=Math.max(current.width/image.naturalWidth,current.height/image.naturalHeight);
      image.style.transition="none";
      image.classList.add("gallery-preview");
      image.style.width="320px";image.style.height=`${320*image.naturalHeight/image.naturalWidth}px`;
      image.style.transform=`translate(-50%,-50%) scale(${initialScale*image.naturalWidth/320})`;
      prepared.set(card,{image,width:image.naturalWidth,height:image.naturalHeight});
      // One starting-frame flush; the decoded node and URL stay unchanged.
      image.getBoundingClientRect();
      image.style.removeProperty("transition");
    }
    card.classList.add("gallery-expanded");
    card.querySelector(".poster").setAttribute("aria-expanded","true");
    arrange();
  }
  function arrange(){
    const {card,index}=active,item=rows[index],image=card.querySelector(".poster img");
    const {width,height,columns}=base,gap=14,row=Math.floor(index/columns),col=index%columns;
    const rect=grid.getBoundingClientRect(),totalRows=Math.ceil(cards.length/columns),center=base.centers[index];
    const screen=mokuPreviewBounds(scrollSurface);
    const bounds={left:screen.left-rect.left,right:screen.right-rect.left,top:screen.top-rect.top,bottom:screen.bottom-rect.top};
    const frameWidth=item.width>0 && item.height>0?item.width:image.naturalWidth;
    const frameHeight=item.width>0 && item.height>0?item.height:image.naturalHeight;
    const layout=mokuLayout({columns,rows:totalRows,count:cards.length,cellWidth:width,cellHeight:height,gap,focusCol:col,focusRow:row,
      centerX:center.x,centerY:center.y,imageRatio:frameWidth/frameHeight,bounds});
    grid.style.paddingBottom=`${Math.max(0,layout.bottom-(totalRows*height+(totalRows-1)*gap))}px`;
    cards.forEach((target,i)=>{
      const r=Math.floor(i/columns),c=i%columns,box=layout.cards[i];
      const w=box.width,h=box.height;
      const x=box.left-c*(width+gap),y=box.top-r*(height+gap);
      // Shared progress preserves the gaps between interpolated row/column tracks.
      const delay=0;
      Object.assign(target.style,{width:`${w}px`,height:`${h}px`,transform:`translate3d(${x}px,${y}px,0)`,transitionDelay:`${delay}ms`});
      const preview=prepared.get(target);
      if(preview){
        // Ordinary focus contains the full image; only aspect-limited long images crop.
        const fit=(target===card && !layout.crop?Math.min:Math.max)((w-2)/preview.width,(h-54)/preview.height);
        preview.image.style.transform=`translate(-50%,-50%) scale(${fit*preview.width/320})`;
        preview.image.style.transitionDelay=`${delay}ms`;
      }
    });
  }
  function beginReturn() {
    if(!active || clicks.waiting() || leaveTimer!==null)return;
    const owner=active;
    leaveTimer=setTimeout(()=>{leaveTimer=null;if(active===owner)reset({slow:true});},500);
  }
  function checkDistance() {
    checkFrame=null;
    if(!active || clicks.waiting())return;
    const rect=active.card.getBoundingClientRect();
    const offscreen=rect.bottom < -180 || rect.top > innerHeight+180;
    if(offscreen){beginReturn();return;}
    if(!pointer)return;
    const dx=Math.max(rect.left-pointer.x,0,pointer.x-rect.right);
    const dy=Math.max(rect.top-pointer.y,0,pointer.y-rect.bottom);
    const distance=Math.hypot(dx,dy);
    if(distance>180)beginReturn();
    else if(distance<120)cancelLeave();
  }
  function scheduleCheck() {
    if(active && checkFrame===null)checkFrame=requestAnimationFrame(checkDistance);
  }
  function mount(items) {
    reset({immediate:true});rows=items;cards=[...grid.querySelectorAll(".gallery-card")];
    cards.forEach((card,index)=>{
      const poster=card.querySelector(".poster");
      poster.onclick=event=>{
        if(!allowed())return;
        cancelLeave();
        if(event.detail===0){
          clicks.cancel();
          const rect=poster.getBoundingClientRect();pointer={x:rect.left+rect.width/2,y:rect.top+rect.height/2};
          toggle(card);scheduleCheck();
        }else{
          pointer={x:event.clientX,y:event.clientY};clicks.click(card,pointer);
        }
      };
      poster.ondblclick=event=>event.preventDefault();
      poster.onkeydown=event=>{
        if(event.key==="Enter"){event.preventDefault();open(card);}
        if(event.key==="Escape"){event.stopPropagation();reset();}
      };
      card.querySelector(".gallery-author").onclick=event=>{
        event.preventDefault();event.stopPropagation();
        if(allowed() && !mokuHasSelectedText(event.currentTarget)){reset({immediate:true});openArtist(rows[index]);}
      };
    });
  }
  grid.addEventListener("click",event=>{if(!event.target.closest(".poster")){clicks.cancel();scheduleCheck();}});
  grid.addEventListener("moku-card-action",()=>{clicks.cancel();scheduleCheck();});
  window.addEventListener("pointermove",event=>{
    if(event.pointerType==="touch")return;
    pointer={x:event.clientX,y:event.clientY};scheduleCheck();
  },{passive:true});
  window.addEventListener("pointerout",event=>{if(!event.relatedTarget){clicks.cancel();beginReturn();}});
  window.addEventListener("blur",()=>{clicks.cancel();beginReturn();});
  scrollSurface.addEventListener("scroll",()=>{clicks.cancel();scheduleCheck();},{passive:true});
  window.addEventListener("resize",()=>{
    if(base && Math.abs(grid.getBoundingClientRect().width-base.gridWidth)<1){if(active)arrange();scheduleCheck();return;}
    reset({immediate:true});
  });
  window.addEventListener("pagehide",clear);
  document.addEventListener("visibilitychange",()=>{if(document.hidden)reset({immediate:true});});
  motion.addEventListener("change",()=>reset({immediate:true}));
  function clear(){reset({immediate:true});rows=[];cards=[];}
  return {mount,reset,clear};
};
