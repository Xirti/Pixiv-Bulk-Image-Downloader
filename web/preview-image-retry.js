"use strict";

globalThis.installPreviewImageRetry = function ({root,loadArtwork,allowed,report}) {
  let lastHost=null,lastAttempt=-Infinity;
  for(const name of ["click","dblclick","keydown"])root.addEventListener(name,event=>{
    if(name==="keydown" && !["Enter"," "].includes(event.key))return;
    if(event.target.closest("input,a,[data-cloud-kind],[data-select]"))return;
    const host=event.target.closest(".poster,.page-select,.deck-card,figure,.batch-collection,.source-candidate-image");
    if(!host)return;
    if(host.classList.contains("is-sensitive-masked"))return;
    const image=host.querySelector("img.image-unavailable");
    if(!image && !(lastHost===host && performance.now()-lastAttempt<650))return;
    event.preventDefault();event.stopImmediatePropagation();
    if(lastHost===host && performance.now()-lastAttempt<650)return;
    if(name==="dblclick" || !image || !allowed() || image.dataset.previewRetryPending==="1")return;
    lastHost=host;lastAttempt=performance.now();
    image.dataset.previewRetryPending="1";
    host.setAttribute("aria-busy","true");
    void (async()=>{
      try{
        let source=image.dataset.failedPreviewSrc;
        const id=image.dataset.previewArtwork || image.dataset.detailArtwork || image.dataset.basketArtwork;
        if(id && image.dataset.sourcePreviewImage===undefined && image.dataset.previewRetryAttempted==="1"){
          const item=await loadArtwork(id);
          const page=Number(image.dataset.detailPage || image.dataset.basketPage || 0);
          source=image.dataset.previewArtwork?item.thumb:item.pageImages?.[page]?.regular || item.thumb;
        }
        if(!source)throw new Error("预览地址暂不可用，请刷新当前页面");
        if(!image.isConnected)return;
        image.dataset.previewRetryAttempted="1";
        image.classList.remove("image-unavailable");host.classList.remove("image-unavailable");
        const url=new URL(source,location.origin);
        url.searchParams.set("retry",String(Date.now()));
        image.src=url.href;
      }catch(error){if(image.isConnected)report(error.message);}
      finally{delete image.dataset.previewRetryPending;host.removeAttribute("aria-busy");}
    })();
  },true);
};
