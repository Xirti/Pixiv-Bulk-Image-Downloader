"use strict";
// One modal for lists and detail stages; it never owns download selection.
globalThis.createImageViewer=function({dialog,cache,showDialog,report,loadItem,sizeChanged=()=>{}}){
  let image=dialog.querySelector("img");
  const count=dialog.querySelector("[data-stage-count]"),original=dialog.querySelector("[data-stage-original]");
  const status=dialog.querySelector("[data-stage-status]"),retry=dialog.querySelector("[data-stage-retry]");
  let current=null,revision=0,size="large",originalView=false,working=null,displayed=-1,manualSize=false,pendingSize=false;
  function resize(){
    if(!current || !dialog.open)return;
    const frame=current.item.pageImages?.[current.page];
    const ownImage=displayed===current.page;
    const width=frame?.width || (ownImage?image.naturalWidth:0) || current.item.width || 1;
    const height=frame?.height || (ownImage?image.naturalHeight:0) || current.item.height || 1;
    const fraction={small:.25,medium:1/3,large:2/3}[size];
    const scale=Math.min(Math.sqrt(innerWidth*innerHeight*fraction/(width*height)),(innerWidth-112)/width,(innerHeight-112)/height);
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
    if(!next)return;
    size=next;manualSize=true;pendingSize=true;paintSize();
  });
  function release(context){
    if(!context)return;
    for(const entry of context.previews.values())entry.image.removeAttribute("src");
    context.previews.clear();
  }
  function preview(context,page,source){
    let entry=context.previews.get(page);
    if(entry?.error){context.previews.delete(page);entry=null;}
    if(entry){context.previews.delete(page);context.previews.set(page,entry);return entry.promise;}
    if(!source)return Promise.reject(new Error("这张图片暂未提供预览，可尝试查看原图"));
    const node=new Image();node.decoding="async";
    entry={image:node,error:null,promise:null};context.previews.set(page,entry);
    node.src=source;
    entry.promise=node.decode().then(()=>node,error=>{entry.error=error;throw error;});
    // Keep the visible page and target. Idle preview nodes have a small local budget.
    for(const [index,old] of context.previews){
      if(context.previews.size<=6)break;
      if(index!==context.page && old.image!==image){context.previews.delete(index);old.image.removeAttribute("src");}
    }
    return entry.promise;
  }
  function warm(context){
    if(context!==current || !context.item.pageImages || context.item.pages<2)return;
    const next=(context.page+1)%context.item.pages,source=context.item.pageImages[next]?.regular;
    if(!source || context.previews.has(next))return;
    // The entry records background failure; an explicit visit retries it.
    void preview(context,next,source).catch(error=>{context.preloadError={page:next,error};});
  }
  async function detail(context){
    if(context.item.pageImages)return context.item;
    const request=context.detailRead || (context.detailRead=loadItem(context.item.id));
    let item;
    try{item=await request;}
    finally{if(context.detailRead===request)context.detailRead=null;}
    if(context!==current)return null;
    context.item=item;return item;
  }
  function commit(node,page){
    if(node!==image){image.replaceWith(node);image=node;}
    image.alt=`${current.item.title} 第 ${page+1} 张`;
    image.dataset.stagePage=String(page);image.classList.remove("image-unavailable");
    delete image.dataset.detailArtwork;delete image.dataset.detailPage;delete image.dataset.suspendedSrc;
    image.style.visibility="visible";displayed=page;resize();
  }
  async function paint(context=current,version=revision){
    const page=context.page;
    cache.pin(context.item,page);originalView=false;working="preview";
    image.style.visibility="hidden";displayed=-1;dialog.setAttribute("aria-busy","true");
    status.textContent=`正在加载第 ${page+1} 张…`;retry.hidden=true;
    original.textContent="查看原图";original.disabled=true;
    count.textContent=`${page+1} / ${context.item.pages}`;
    dialog.querySelectorAll("[data-stage-turn]").forEach(button=>button.disabled=context.item.pages<2);
    resize();
    try{
      const initial=page===context.initialPage?context.initialPreview:null;
      const item=initial?context.item:await detail(context);
      if(context!==current || version!==revision || !dialog.open || !item)return;
      const node=await preview(context,page,initial || item.pageImages?.[page]?.regular || (page===0?item.thumb:""));
      if(context!==current || version!==revision || !dialog.open)return;
      commit(node,page);status.textContent="";context.onPage?.(page);warm(context);
    }catch(error){
      if(context!==current || version!==revision || !dialog.open)return;
      status.textContent=`第 ${page+1} 张加载失败`;retry.hidden=false;report(error.message || "预览加载失败，请重试");
    }finally{
      if(context===current && version===revision){working=null;original.disabled=false;dialog.setAttribute("aria-busy","false");}
    }
  }
  async function viewOriginal(){
    if(!current || working || originalView)return;
    const context=current,page=context.page,version=++revision;
    working="original";original.disabled=true;retry.hidden=true;status.textContent="原图加载中…";
    try{
      const item=await detail(context);if(!item || context!==current || version!==revision || !dialog.open)return;
      let url=cache.peek(item,page);
      if(!url){
        try{url=await cache.get(item,page);}
        catch(error){
          if(error.status!==403)throw error;
          const fresh=await loadItem(item.id);
          if(context!==current || version!==revision || !dialog.open)return;
          context.item=fresh;url=await cache.get(fresh,page);
        }
      }
      if(context!==current || version!==revision || !dialog.open)return;
      const node=new Image();node.src=url;await node.decode();
      if(context!==current || version!==revision || !dialog.open)return;
      commit(node,page);originalView=true;original.textContent="已显示原图";status.textContent="";
    }catch(error){
      if(context!==current || version!==revision || !dialog.open)return;
      cache.invalidate(context.item,page);status.textContent="原图加载失败，可重试";
      report(error.message || "原图加载失败");
    }finally{if(context===current && version===revision){working=null;original.disabled=originalView;}}
  }
  function turn(direction){
    if(!current || current.item.pages<2)return;
    current.page=(current.page+direction+current.item.pages)%current.item.pages;
    void paint(current,++revision);
  }
  function open({owner=null,item,page=0,preview:source=null,onPage=null,onClose=null}){
    if(dialog.open)dialog.close();
    release(current);revision++;cache.unpin();displayed=-1;
    current={owner,item,page,initialPage:page,initialPreview:source,previews:new Map(),onPage,onClose,opener:document.activeElement};
    showDialog(dialog);paintSize();void paint();dialog.querySelector("[data-stage-close]").focus({preventScroll:true});
    // Prepare only the next preview of a multi-page work, never its original.
    if(item.pages>1 && !item.pageImages){
      const context=current,version=revision;
      void detail(context).then(ready=>{if(ready && context===current)warm(context);}).catch(error=>{
        context.detailError=error;
        if(context===current && version===revision && !working)status.textContent="后续图片准备失败，可点击箭头重试";
      });
    }
  }
  function close(){if(dialog.open)dialog.close();}
  dialog.querySelector("[data-stage-close]").onclick=close;
  original.onclick=()=>void viewOriginal();retry.onclick=()=>{if(current)void paint(current,++revision);};
  dialog.querySelectorAll("[data-stage-turn]").forEach(button=>button.onclick=()=>turn(Number(button.dataset.stageTurn)));
  dialog.addEventListener("keydown",event=>{
    if(event.key==="ArrowLeft" || event.key==="ArrowRight"){event.preventDefault();turn(event.key==="ArrowRight"?1:-1);}
  });
  dialog.addEventListener("close",()=>{
    if(dialog.open)return;
    const previous=current;current=null;revision++;working=null;displayed=-1;
    cache.unpin();retry.hidden=true;dialog.setAttribute("aria-busy","false");
    previous?.opener?.isConnected && previous.opener.focus({preventScroll:true});previous?.onClose?.();
    release(previous);image.removeAttribute("src");
    if(pendingSize){pendingSize=false;sizeChanged(size);}
  });
  window.addEventListener("resize",resize);
  document.addEventListener("visibilitychange",()=>{if(document.hidden)close();});
  return {open,close,owns:owner=>current?.owner===owner,setSize(value){if(!manualSize){size=value;paintSize();}},clear(){close();cache.clear();}};
};
