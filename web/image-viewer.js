"use strict";
// One modal for lists and detail stages; it never owns download selection.
globalThis.createImageViewer=function({dialog,cache,showDialog,report,loadItem,sizeChanged=()=>{}}){
  let image=dialog.querySelector("img");
  const count=dialog.querySelector("[data-stage-count]"),original=dialog.querySelector("[data-stage-original]");
  const status=dialog.querySelector("[data-stage-status]"),retry=dialog.querySelector("[data-stage-retry]");
  let current=null,revision=0,size="large",originalView=false,busy=false,displayed=-1;
  let preferredSize=size,sizeRevision=0,sizeDraft=null;
  function resize(){
    if(!current || !dialog.open)return;
    const frame=current.item.pageImages?.[current.page];
    const ownImage=displayed===current.page;
    const width=frame?.width || (ownImage?image.naturalWidth:0) || current.item.width || 1;
    const height=frame?.height || (ownImage?image.naturalHeight:0) || current.item.height || 1;
    const fraction={small:.25,medium:1/3,large:2/3}[size];
    const gutter=Math.min(100,Math.max(56,innerWidth*.07));
    const scale=Math.min(Math.sqrt(innerWidth*innerHeight*fraction/(width*height)),(innerWidth-gutter*2)/width,(innerHeight-144)/height);
    dialog.style.width=width*scale+"px";dialog.style.height=height*scale+"px";
  }
  function paintSize(){
    const sizes=["small","medium","large"],index=sizes.indexOf(size);
    dialog.querySelector("[data-stage-size-label]").textContent=["小","中","大"][index];
    dialog.querySelectorAll("[data-stage-size]").forEach(button=>{button.disabled=index+Number(button.dataset.stageSize)<0 || index+Number(button.dataset.stageSize)>2;});
    resize();
  }
  dialog.querySelectorAll("[data-stage-size]").forEach(button=>button.onclick=()=>{
    const sizes=["small","medium","large"],next=sizes[sizes.indexOf(size)+Number(button.dataset.stageSize)];
    if(!current || !next)return;
    current.manualSize=true;size=next;sizeDraft={value:next,saving:false};paintSize();
  });
  function setSize(value){
    preferredSize=value;sizeRevision++;
    if(!current?.manualSize && !sizeDraft){size=value;paintSize();}
  }
  async function saveSize(){
    const draft=sizeDraft;
    if(!draft || draft.saving)return;
    draft.saving=true;
    const before=sizeRevision;
    try{await sizeChanged(draft.value);}
    catch{draft.saving=false;return;}
    if(before===sizeRevision)preferredSize=draft.value;
    if(sizeDraft!==draft)return;
    sizeDraft=null;
    if(!current?.manualSize){size=preferredSize;paintSize();}
  }
  function clearPreviews(context,keep=null){
    for(const entry of context.previews.values()){
      entry.controller.abort();
      if(entry.image!==keep)entry.image.removeAttribute("src");
    }
    context.previews.clear();
  }
  function release(context){
    if(!context)return;
    context.controller.abort();
    context.originalController?.abort();
    clearPreviews(context);
  }
  function preview(context,page,source){
    let entry=context.previews.get(page);
    if(entry?.failed){context.previews.delete(page);entry=null;}
    if(entry){context.previews.delete(page);context.previews.set(page,entry);return entry.promise;}
    if(!source)return Promise.reject(new Error("这张图片暂未提供预览，可尝试查看原图"));
    const node=new Image();node.decoding="async";
    const controller=new AbortController();
    entry={image:node,controller,failed:false,promise:null};context.previews.set(page,entry);
    node.src=source;
    entry.promise=waitForMediaDecode(()=>node.decode(),controller.signal).then(()=>node,error=>{
      entry.failed=true;node.removeAttribute("src");throw error;
    });
    // Keep the visible page and target. Idle preview nodes have a small local budget.
    for(const [index,old] of context.previews){
      if(context.previews.size<=6)break;
      if(index!==context.page && old.image!==image){
        context.previews.delete(index);old.controller.abort();old.image.removeAttribute("src");
      }
    }
    return entry.promise;
  }
  function warm(context){
    if(context!==current || !context.item.pageImages || context.item.pages<2)return;
    const next=(context.page+1)%context.item.pages,source=context.item.pageImages[next]?.regular;
    if(!source || context.previews.has(next))return;
    // The entry records background failure; an explicit visit retries it.
    void preview(context,next,source).catch(()=>{});
  }
  function syncPages(context){
    count.textContent=`${context.page+1} / ${context.item.pages}`;
    dialog.querySelectorAll("[data-stage-turn]").forEach(button=>button.disabled=context.item.pages<2);
  }
  function adoptItem(context,item){
    context.item=item;
    const page=Math.min(context.page,item.pages-1);
    if(page!==context.page){context.page=page;void paint(context,++revision);return false;}
    syncPages(context);resize();return true;
  }
  async function detail(context,refresh=false){
    if(context.item.pageImages && !refresh && !context.detailRead)return context.item;
    if(!context.detailRead || refresh){
      context.detailVersion=(context.detailVersion || 0)+1;
      context.detailRead=loadItem(context.item.id,{signal:context.controller.signal,...(refresh?{refresh:true}:{})});
    }
    const request=context.detailRead,version=context.detailVersion;
    let item;
    try{item=await request;}
    finally{if(context.detailRead===request)context.detailRead=null;}
    if(context!==current || version!==context.detailVersion)return null;
    if(refresh){clearPreviews(context,image);context.initialPreview=null;}
    return adoptItem(context,item)?item:null;
  }
  function commit(node,page){
    if(node!==image){image.replaceWith(node);image=node;}
    image.alt=`${current.item.title} 第 ${page+1} 张`;
    image.dataset.stagePage=String(page);image.classList.remove("image-unavailable");
    delete image.dataset.detailArtwork;delete image.dataset.detailPage;delete image.dataset.suspendedSrc;
    image.style.visibility="visible";displayed=page;resize();
  }
  async function paint(context=current,version=revision,refresh=false){
    const page=context.page;
    context.originalController?.abort();
    original.hidden=!context.allowOriginal;
    cache.pin(context.item,page);originalView=false;busy=true;
    image.style.visibility="hidden";displayed=-1;dialog.setAttribute("aria-busy","true");
    status.textContent=`正在加载第 ${page+1} 张…`;retry.hidden=true;
    original.textContent="查看原图";original.disabled=true;
    syncPages(context);
    resize();
    try{
      const initial=!refresh && !context.detailRead && page===context.initialPage?context.initialPreview:null;
      const item=initial?context.item:await detail(context,refresh);
      if(context!==current || version!==revision || !dialog.open || !item)return;
      const node=await preview(context,page,initial || item.pageImages?.[page]?.regular || (page===0?item.thumb:""));
      if(context!==current || version!==revision || !dialog.open)return;
      commit(node,page);status.textContent="";context.onPage?.(page);warm(context);
    }catch(error){
      if(context!==current || version!==revision || !dialog.open)return;
      status.textContent=`第 ${page+1} 张加载失败`;retry.hidden=false;report(error.message || "预览加载失败，请重试");
    }finally{
      if(context===current && version===revision){busy=false;original.disabled=false;dialog.setAttribute("aria-busy","false");}
    }
  }
  async function viewOriginal(){
    if(!current || !current.allowOriginal || busy || originalView)return;
    const context=current,page=context.page,version=++revision;
    const controller=new AbortController();
    context.originalController=controller;
    let node;
    busy=true;original.disabled=true;retry.hidden=true;status.textContent="原图加载中…";
    dialog.setAttribute("aria-busy","true");
    try{
      const item=await detail(context);if(!item || context!==current || version!==revision || !dialog.open)return;
      let url=cache.peek(item,page);
      if(!url){
        try{url=await cache.get(item,page);}
        catch(error){
          if(error.status!==403)throw error;
          const fresh=await detail(context,true);
          if(!fresh || context!==current || version!==revision || !dialog.open)return;
          url=await cache.get(fresh,page);
        }
      }
      if(context!==current || version!==revision || !dialog.open)return;
      node=new Image();node.src=url;
      await waitForMediaDecode(()=>node.decode(),controller.signal);
      if(context!==current || version!==revision || !dialog.open)return;
      commit(node,page);originalView=true;original.textContent="已显示原图";status.textContent="";
    }catch(error){
      if(context!==current || version!==revision || !dialog.open)return;
      cache.invalidate(context.item,page);
      retry.hidden=displayed===page;
      status.textContent=retry.hidden?"原图加载失败，可重试":"原图加载失败，可重试预览或原图";
      report(error.message || "原图加载失败");
    }finally{
      if(node && node!==image)node.removeAttribute("src");
      if(context.originalController===controller)context.originalController=null;
      if(context===current && version===revision){
        busy=false;original.disabled=originalView;dialog.setAttribute("aria-busy","false");
      }
    }
  }
  function turn(direction){
    if(!current || current.item.pages<2)return;
    current.page=(current.page+direction+current.item.pages)%current.item.pages;
    void paint(current,++revision);
  }
  function open({owner=null,item,page=0,preview:source=null,onPage=null,onClose=null,allowOriginal=true}){
    close();
    revision++;
    current={owner,item,page,initialPage:page,initialPreview:source,previews:new Map(),controller:new AbortController(),onPage,onClose,allowOriginal,opener:document.activeElement};
    size=sizeDraft?.value || preferredSize;
    showDialog(dialog);paintSize();void paint();dialog.querySelector("[data-stage-close]").focus({preventScroll:true});
    // Prepare only the next preview of a multi-page work, never its original.
    if(item.pages>1 && !item.pageImages){
      const context=current,version=revision;
      void detail(context).then(ready=>{if(ready && context===current)warm(context);}).catch(()=>{
        if(context===current && version===revision && !busy)status.textContent="后续图片准备失败，可点击箭头重试";
      });
    }
  }
  function finishClose(){
    const previous=current;
    if(!previous)return;
    current=null;revision++;busy=false;displayed=-1;
    cache.unpin();retry.hidden=true;dialog.setAttribute("aria-busy","false");
    release(previous);image.removeAttribute("src");void saveSize();
    previous.opener?.isConnected && previous.opener.focus({preventScroll:true});previous.onClose?.();
  }
  function close(){
    if(dialog.open)dialog.close();
    finishClose();
  }
  dialog.querySelector("[data-stage-close]").onclick=close;
  original.onclick=()=>void viewOriginal();retry.onclick=()=>{if(current)void paint(current,++revision,current.item.source==="pixiv");};
  dialog.querySelectorAll("[data-stage-turn]").forEach(button=>button.onclick=()=>turn(Number(button.dataset.stageTurn)));
  dialog.addEventListener("keydown",event=>{
    if(event.key==="ArrowLeft" || event.key==="ArrowRight"){event.preventDefault();turn(event.key==="ArrowRight"?1:-1);}
  });
  dialog.addEventListener("close",()=>{if(!dialog.open)finishClose();});
  window.addEventListener("resize",resize);
  document.addEventListener("visibilitychange",()=>{if(document.hidden)close();});
  return {open,close,owns:owner=>current?.owner===owner,setSize,clear(){close();cache.clear();}};
};
