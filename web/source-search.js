"use strict";

globalThis.createSourceSearch = function ({root,settingsRoot,request,escape:esc,changed,openArtwork,preview,openExternal,allowed,installImages=()=>{}}) {
  const find=selector=>root.querySelector(selector), setting=selector=>settingsRoot.querySelector(selector);
  let file=null, imageUrl="", job=null, busy=false, sequence=0, timer=null, clickTimer=null, polling=null;
  let lastReceipt=null, viewReceipt=null;
  let configuration={mode:"guest",keyConfigured:false}, settingsLoaded=false;
  let settingsVersion=0;
  let settingsRead=null,settingsWrite=null;
  let queryModeChosen=false,settingsModeChosen=false;
  let maskSensitive=true;
  const revealed = createSensitiveImageReveals({root,onHide:()=>render()});
  let revealedJob = null;
  let recentlyRevealedId=null,revealedAt=-Infinity;
  let cacheEpoch=0;
  let loginPending=false;
  const images=createPreviewImageCache({retainUnused:true,maxImages:128,reuseDetached:true});
  let renderedResults="";

  function candidateMarkup(row,index){
    const masked=Boolean(row.thumb) && maskSensitive && row.restriction!=="safe" && !revealed.has(row.id);
    const title=row.title || row.indexName || "匹配来源";
    const image=row.thumb?`<img data-gallery-src="${esc(row.thumb)}" data-preview-artwork="${esc(row.id)}" data-source-preview-image alt="${esc(title)}" loading="lazy" decoding="async">`:'<span class="source-no-preview">无预览</span>';
    const pixiv=row.pixivIds.map(id=>`<a class="source-pixiv-link" href="https://www.pixiv.net/artworks/${id}" target="_blank" rel="noopener noreferrer" data-copy-link data-source-pixiv="${id}" title="PID ${id}">Pixiv <span>PID ${id}</span><span aria-hidden="true">↗</span></a>`).join("");
    const links=row.links.map((link,linkIndex)=>{
      if(link.pixivId)return '';
      const caption=link.label && link.label!==link.site && link.label.length<=22 && !/^[0-9]+$/.test(link.label)?`${link.site} · ${link.label}`:link.site;
      return `<a class="source-origin-link" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer" data-copy-link data-source-link="${index}:${linkIndex}" title="${esc(link.label?link.label+' · '+link.url:link.url)}">${esc(caption)} <span aria-hidden="true">↗</span></a>`;
    }).join("");
    const details=row.details.length?`<details class="source-record-details"><summary><span>${esc(row.details.join(" · "))}</span><svg viewBox="0 0 18 12" aria-hidden="true"><path d="m3 3 6 6 6-6"/></svg></summary><p>${row.details.map(esc).join("<br>")}</p></details>`:"";
    return `<div class="source-candidate-visual${masked?' is-masked':''}"><button type="button" class="source-candidate-image" data-source-preview="${index}" aria-label="预览 ${esc(title)}"${masked || !row.thumb?' disabled':''}>${image}</button><button type="button" class="source-mask-reveal" data-source-reveal="${index}"${masked?'':' hidden'}>${sensitiveImageMaskMarkup(row.sensitive?'敏感内容':'分级待确认')}</button></div><div class="source-candidate-copy"><div class="source-record-heading"><h3 title="${esc(title)}">${esc(title)}</h3><span class="source-score" aria-label="相似度 ${row.similarity.toFixed(2)}%">${row.similarity.toFixed(2)}%</span></div>${row.artist?`<span class="source-record-author">${esc(row.artist)}</span>`:''}${row.indexName?`<small class="source-record-index">${esc(row.indexName)}</small>`:''}${details}<div class="source-links">${pixiv}${links || (!pixiv?'<small>未提供来源链接</small>':'')}</div></div>`;
  }
  function paintMasks(){
    for(const [index,row] of (job?.candidates || []).entries()){
      const card=root.querySelector(`[data-source-candidate="${index}"]`);
      if(!card)continue;
      const masked=Boolean(row.thumb) && maskSensitive && row.restriction!=="safe" && !revealed.has(row.id);
      card.querySelector('.source-candidate-visual').classList.toggle('is-masked',masked);
      card.querySelector('[data-source-preview]').disabled=masked || !row.thumb;
      card.querySelector('[data-source-reveal]').hidden=!masked;
    }
  }
  const urls=[];
  const post=(path,data)=>request(`/api/source-search/${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
  const receiptPath=id=>`/api/source-search/receipt?requestId=${encodeURIComponent(id)}`;
  function message(text,error=false){find("[data-source-message]").textContent=text;find("[data-source-message]").classList.toggle("is-error",error);}
  function controls(){
    find("[data-source-query]").disabled=!file || busy || Boolean(lastReceipt?.pending);
    find("[data-source-read]").disabled=Boolean(viewReceipt?.pending);
    const canCancel=busy && job?.id || viewReceipt?.pending || viewReceipt?.unconfirmed;
    find("[data-source-cancel]").hidden=!canCancel;
    find("[data-source-cancel]").disabled=!canCancel;
    find("[data-source-file]").disabled=busy;
    find("[data-source-mode]").disabled=busy;
    find("[data-source-browser-fallback]").hidden=!job?.browserFallback;
    find("[data-source-browser-fallback]").disabled=busy;
    root.setAttribute("aria-busy",String(busy));
  }
  function choose(selected){
    if(busy || !selected)return;
    if(selected.size>12*1024*1024){message("请选择不超过 12 MiB 的图片",true);return;}
    if(!/\.(?:jpe?g|png|webp|gif|bmp)$/i.test(selected.name) && !/^image\/(?:jpeg|png|webp|gif|bmp|x-ms-bmp)$/.test(selected.type)){
      message("支持 JPG、PNG、WebP、GIF 和 BMP 图片",true);return;
    }
    file=selected;imageUrl=URL.createObjectURL(selected);urls.push(imageUrl);
    while(urls.length>8)URL.revokeObjectURL(urls.shift());
    const image=find("[data-source-input-image]");image.src=imageUrl;image.hidden=false;
    find("[data-source-file-name]").textContent=selected.name;
    clearTimeout(timer);clearTimeout(clickTimer);sequence++;viewReceipt=null;job=null;render();
    message("图片已就绪，点击「上传并查找」开始。");changed();
  }
  function render(){
    if (revealedJob !== job?.id) { revealed.clear(); revealedJob = job?.id; }
    const results=job?.candidates || [];
    const signature = JSON.stringify([job?.id,results,cacheEpoch]);
    if (signature !== renderedResults) {
      renderedResults = signature;
      images.park(find("[data-source-results]"));
      find("[data-source-results]").innerHTML = results.map((row,index) => `<article class="source-candidate" data-source-candidate="${index}" data-sensitive-image="${esc(row.id)}">${candidateMarkup(row,index)}</article>`).join("");
      images.mount(find("[data-source-results]"),job?.id || "empty");
      installImages(find("[data-source-results]"));
    }
    paintMasks();
    const quota=job?.quota || {}, parts=[];
    if(quota.short_remaining>=0)parts.push(`短期剩余 ${quota.short_remaining}${quota.short_limit>0?` / ${quota.short_limit}`:""}`);
    if(quota.long_remaining>=0)parts.push(`当日剩余 ${quota.long_remaining}${quota.long_limit>0?` / ${quota.long_limit}`:""}`);
    find("[data-source-quota]").textContent=[job?.cached?"近期缓存":"",...parts].filter(Boolean).join(" · ");
    find("[data-source-result-count]").textContent=job?.state==="done"?`共 ${results.length} 条来源 · ${results.filter(row=>row.pixivIds.length).length} 条含 Pixiv`:"";
    controls();
  }
  async function readCacheEpoch(signal){
    const {cacheEpoch:epoch}=await request("/api/source-search/cache-state",{signal});
    if(!Number.isSafeInteger(epoch) || epoch<0)throw new Error("搜图缓存状态不可读");
    return epoch;
  }

  function observeCacheEpoch(epoch){
    if(!(epoch>cacheEpoch))return;
    cacheEpoch=epoch;
    if(lastReceipt?.result?.resultEpoch<epoch){
      if(viewReceipt===lastReceipt)viewReceipt=null;
      lastReceipt=null;
    }
    if(job?.resultEpoch<epoch){
      clearTimeout(timer);clearTimeout(clickTimer);timer=clickTimer=null;job=null;
      busy=Boolean(viewReceipt?.pending);
    }
    images.clear();
  }

  function apply(data){
    observeCacheEpoch(data.cacheEpoch);
    if(data.resultEpoch<cacheEpoch){
      if(viewReceipt?.result===data){
        if(lastReceipt===viewReceipt)lastReceipt=null;
        viewReceipt=null;
      }
      if(!job)busy=Boolean(viewReceipt?.pending);
      if(!job)message("搜图缓存已清除，请重新查询");
      render();changed();
      return;
    }
    if(viewReceipt){
      viewReceipt.result=data;
      viewReceipt.unconfirmed=false;
      viewReceipt.error="";
    }
    job=data;busy=["queued","searching","verification"].includes(job.state);
    message(job.message,job.state==="error");render();changed();
  }
  function poll(revision){
    const id=job?.id;
    const path=id?`/api/source-search/job?id=${encodeURIComponent(id)}`
      : viewReceipt?.unconfirmed?receiptPath(viewReceipt.id):null;
    if(revision!==sequence || !path || viewReceipt?.pending)return Promise.resolve();
    if(polling?.revision===revision)return polling.promise;
    clearTimeout(timer);timer=null;
    const owner={revision,promise:null};polling=owner;
    owner.promise=request(path).then(data=>{
      if(revision!==sequence)return;
      apply(data);
      if(busy)timer=setTimeout(()=>void poll(revision),1500);
    }).catch(error=>{
      if(revision!==sequence)return;
      if(error.status===404 && id && job?.id===id){
        if(lastReceipt?.result?.id===id)lastReceipt=null;
        if(viewReceipt?.result?.id===id)viewReceipt=null;
        job=null;busy=false;
        message(error.message,true);render();changed();
        return;
      }
      message(`${error.message}。可点击「读取状态」重试，查询不会重复提交。`,true);controls();
    }).finally(()=>{if(polling===owner)polling=null;});
    return owner.promise;
  }
  async function search(){
    if(!allowed() || !file || busy || lastReceipt?.pending)return;
    const mode=find("[data-source-mode]").value;
    const id=viewReceipt?.unconfirmed && viewReceipt.mode===mode?viewReceipt.id:crypto.randomUUID();
    const receipt={id,mode,pending:true,result:null,error:"",unconfirmed:false};
    lastReceipt=viewReceipt=receipt;
    sequence++;clearTimeout(timer);clearTimeout(clickTimer);job=null;busy=true;render();message("正在准备搜图副本…");
    try{
      receipt.result=await request(`/api/source-search/query?mode=${encodeURIComponent(mode)}&requestId=${id}`,{
        method:"POST",headers:{"Content-Type":file.type || "application/octet-stream"},body:file
      },30000);
    }catch(error){
      receipt.error=error.message;
      receipt.unconfirmed=!error.status || error.status>=500;
      if(receipt.unconfirmed){
        try{receipt.result=await request(receiptPath(id));}
        catch(lookupError){if(lookupError.status!==404)receipt.error=lookupError.message;}
        if(!receipt.result)receipt.error+="。可点击「读取状态」确认这次查询。";
      }
    }finally{
      receipt.pending=false;
      if(viewReceipt!==receipt){
        controls();
      }else if(receipt.result){
        const revision=sequence;
        apply(receipt.result);
        if(busy)timer=setTimeout(()=>void poll(revision),1500);
      }else{
        busy=false;
        message(receipt.error,true);
        controls();
        changed();
      }
    }
  }
  function showSettings({applyMode=false}={}){
    if(!settingsModeChosen)setting("[data-source-setting-mode]").value=configuration.mode;
    setting("[data-source-key-status]").textContent=configuration.keyConfigured?"已安全保存 API Key":"尚未配置 API Key，官网查询可独立登录";
    const states={"signed-in":"上次核对已登录","signed-out":"尚未登录","unknown":"尚未核对"};
    setting("[data-source-website-state]").textContent=`SauceNAO 官网会话 · ${states[configuration.websiteLogin] || states.unknown}`;
    if(applyMode)find("[data-source-mode]").value=configuration.mode;
  }
  function loadSettings({refresh=false}={}){
    if(settingsWrite){
      const readAfterWrite=()=>loadSettings({refresh});
      return settingsWrite.then(readAfterWrite,readAfterWrite);
    }
    if(!refresh && settingsRead?.version===settingsVersion){
      return settingsRead.promise;
    }
    const reading={version:++settingsVersion,promise:null};
    settingsRead=reading;
    reading.promise=request("/api/source-search/settings").then(value=>{
      if(reading.version!==settingsVersion)return false;
      const applyMode=!settingsLoaded && !queryModeChosen;
      configuration=value;
      observeCacheEpoch(value.cacheEpoch);
      settingsLoaded=true;
      showSettings({applyMode});
      render();
      if(configuration.error){
        setting("[data-source-settings-message]").textContent=configuration.error;
        message(configuration.error,true);
      }
      return true;
    }).catch(error=>{
      if(reading.version===settingsVersion){
        setting("[data-source-settings-message]").textContent=error.message;
      }
      return false;
    }).finally(()=>{
      if(settingsRead===reading)settingsRead=null;
    });
    return reading.promise;
  }
  async function clearCache(data){
    const before=await readCacheEpoch();
    try{
      return await post("settings",data);
    }catch(error){
      if(error.status && error.status<500)throw error;
      let after;
      try{after=await readCacheEpoch();}
      catch(recovery){throw new AggregateError([error,recovery],error.message+"；无法核对搜图缓存状态："+recovery.message);}
      if(after<=before)throw error;
      return {cacheEpoch:after};
    }
  }

  async function saveSettings(action){
    if(settingsWrite)return;
    settingsVersion++;
    const status=setting("[data-source-settings-message]"), controls=settingsRoot.querySelectorAll("button,input,select");
    controls.forEach(control=>control.disabled=true);status.textContent="正在保存…";
    const key=setting("[data-source-key]");
    try{
      const data={action,mode:setting("[data-source-setting-mode]").value};
      if(action==="save" && key.value.trim())data.key=key.value.trim();
      settingsWrite=action==="clear"?clearCache(data):post("settings",data);
      const saved=await settingsWrite;
      observeCacheEpoch(saved.cacheEpoch);
      if(action!=="clear"){
        configuration=saved;
        settingsLoaded=true;
        queryModeChosen=false;
        settingsModeChosen=false;
        key.value="";
        showSettings({applyMode:true});
      }
      render();changed();
      status.textContent=action==="clear"?"搜图结果缓存已清除":action==="reset-guest"?"游客会话已重置":action==="remove-key"?"API Key 已移除":"搜图设置已保存";
    }catch(error){status.textContent=error.message;}
    finally{
      settingsWrite=null;
      controls.forEach(control=>control.disabled=false);
    }
  }
  find("[data-source-file]").addEventListener("change",event=>choose(event.target.files[0]));
  find("[data-source-query]").onclick=()=>void search();
  find("[data-source-mode]").addEventListener("change",()=>{queryModeChosen=true;});
  setting("[data-source-setting-mode]").addEventListener("change",()=>{settingsModeChosen=true;});
  async function login() {
    if(loginPending)return;
    loginPending=true;
    try {
      const task=await post("login",{});
      setting("[data-source-settings-message]").textContent="请在官网窗口输入账号、密码并完成验证";
      for(let remaining=200;remaining>0;remaining--){
        await new Promise(resolve=>setTimeout(resolve,remaining===200?1500:3000));
        const state=await request(`/api/source-search/job?id=${encodeURIComponent(task.id)}`);
        const adopted=await loadSettings({refresh:true});
        if(state.state === "done" || state.state === "error" || state.state === "cancelled"){
          if(adopted)setting("[data-source-settings-message]").textContent=state.message;
          return;
        }
      }
    } catch(error) {setting("[data-source-settings-message]").textContent=error.message;message(error.message,true);}
    finally{loginPending=false;}
  }
  setting("[data-source-login]").onclick=()=>void login();
  find("[data-source-login]").onclick=()=>void login();
  find("[data-source-browser-fallback]").onclick=async()=>{
    if (!job?.id || busy) return;
    const revision = ++sequence;
    clearTimeout(timer);busy=true;controls();
    try {
      const data = await post("continue",{id:job.id});
      if (sequence !== revision) return;
      apply(data);if(busy)timer=setTimeout(()=>void poll(revision),1500);
    } catch(error) {
      if(sequence === revision){busy=false;message(error.message,true);controls();}
    }
  };
  find("[data-source-manual-open]").onclick=()=>{
    if(!allowed())return;
    const value=find("[data-source-manual-id]").value.trim();
    let id=/^[1-9][0-9]{0,29}$/.test(value)?value:"";
    if(!id){
      try{
        const url=new URL(value),match=url.pathname.match(/^\/(?:[a-z]{2}\/)?artworks\/([1-9][0-9]{0,29})\/?$/);
        if(url.protocol==="https:" && ["www.pixiv.net","pixiv.net"].includes(url.hostname) && !url.username && !url.password){
          id=match?.[1] || (url.pathname==="/member_illust.php"?url.searchParams.get("illust_id") || "":"");
        }
      }catch{message("请输入 Pixiv 作品链接或数字 PID",true);return;}
    }
    if(!/^[1-9][0-9]{0,29}$/.test(id)){message("请输入 Pixiv 作品链接或数字 PID",true);return;}
    void openArtwork(id);
  };
  find("[data-source-read]").onclick=()=>{
    if(job?.id || viewReceipt?.unconfirmed){clearTimeout(timer);void poll(sequence);}
    else if(!viewReceipt?.pending)message("还没有发起查询，请先选择图片。");
  };
  find("[data-source-cancel]").onclick=async()=>{
    const revision=sequence,id=job?.id,receipt=viewReceipt;
    if(!id && !receipt?.pending && !receipt?.unconfirmed)return;
    const ownsView=()=>revision===sequence && (id?job?.id===id:viewReceipt===receipt);
    try{
      const result=await post("cancel",id?{id}:{requestId:receipt.id});
      if(!ownsView())return;
      clearTimeout(timer);sequence++;
      if(!id){
        if(lastReceipt===receipt)lastReceipt=null;
        viewReceipt=null;
      }
      apply(result.job || {...job,state:"cancelled",message:"已取消查询；已发出的请求可能仍计入额度"});
    }catch(error){if(ownsView())message(error.message,true);}
  };
  root.addEventListener("dragover",event=>{if(event.dataTransfer.types.includes("Files")){event.preventDefault();root.classList.add("is-dragging");}});
  root.addEventListener("dragleave",event=>{if(!root.contains(event.relatedTarget))root.classList.remove("is-dragging");});
  root.addEventListener("drop",event=>{event.preventDefault();root.classList.remove("is-dragging");choose(event.dataTransfer.files[0]);});
  document.addEventListener("paste",event=>{
    if(root.hidden || document.querySelector("dialog[open]") || event.target.closest("input,textarea,[contenteditable=true]"))return;
    const image=[...event.clipboardData.items].find(item=>item.type.startsWith("image/"));
    if(image){event.preventDefault();choose(image.getAsFile());}
  });
  root.addEventListener("click",event=>{
    const reveal=event.target.closest("[data-source-reveal]");
    if(reveal){clearTimeout(clickTimer);const row=job?.candidates[Number(reveal.dataset.sourceReveal)];if(row){revealed.reveal(row.id);recentlyRevealedId=row.id;revealedAt=performance.now();render();}return;}
    const pixiv=event.target.closest('[data-source-pixiv]');
    const link=event.target.closest('[data-source-link]');
    if(pixiv || link){
      if(event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;
      if(event.detail>1){event.preventDefault();return;}
      if(!allowed()){event.preventDefault();return;}
      clearTimeout(clickTimer);
      if(pixiv){event.preventDefault();void openArtwork(pixiv.dataset.sourcePixiv);}
      else {const [i,k]=link.dataset.sourceLink.split(':').map(Number);openExternal(job.candidates[i].links[k].url,event);}
      return;
    }
    const target=event.target.closest('[data-source-preview]');if(!target || target.disabled || !allowed())return;
    const row=job?.candidates[Number(target.dataset.sourcePreview)];if(!row)return;
    if(row.id===recentlyRevealedId && performance.now()-revealedAt<650)return;
    clearTimeout(clickTimer);
    const thumbnail=target.querySelector('img')?.currentSrc || row.thumb;
    clickTimer=setTimeout(()=>{if(!root.hidden && allowed())preview(row,thumbnail);},650);
  });
  root.addEventListener('dblclick',event=>{
    const target=event.target.closest('[data-source-preview]');if(!target || target.disabled || !allowed())return;
    const row=job?.candidates[Number(target.dataset.sourcePreview)];
    if(!row?.pixivIds.length || row.id===recentlyRevealedId && performance.now()-revealedAt<650)return;
    clearTimeout(clickTimer);void openArtwork(row.pixivIds[0]);
  });
  settingsRoot.querySelectorAll("[data-source-config-action]").forEach(button=>button.onclick=()=>void saveSettings(button.dataset.sourceConfigAction));
  controls();
  return {
    setMaskSensitive(enabled){if(maskSensitive===enabled)return;maskSensitive=enabled;revealed.clear();clearTimeout(clickTimer);render();},
    async open(){if(!settingsLoaded)await loadSettings();controls();},loadSettings,
    snapshot:()=>({job,imageUrl,name:find("[data-source-file-name]").textContent,epoch:cacheEpoch,submission:viewReceipt?.id ?? null}),
    async restore(state,{signal}={}){
      clearTimeout(timer);clearTimeout(clickTimer);const revision=++sequence;
      if(state?.job || state?.submission){
        const epoch=await readCacheEpoch(signal);
        signal?.throwIfAborted();
        if(revision!==sequence)return;
        observeCacheEpoch(epoch);
      }
      const savedImage=state?.imageUrl || "",expired=Boolean(savedImage) && !urls.includes(savedImage);
      if(imageUrl!==savedImage || expired){file=null;find("[data-source-file]").value="";}
      viewReceipt=lastReceipt && state?.epoch===cacheEpoch && state.submission===lastReceipt.id?lastReceipt:null;
      job=viewReceipt?.result || (state?.epoch===cacheEpoch?state.job:null);imageUrl=expired?"":savedImage;
      const image=find("[data-source-input-image]");image.hidden=!imageUrl;if(imageUrl)image.src=imageUrl;else image.removeAttribute("src");
      find("[data-source-file-name]").textContent=state?.name || "选择、拖入或粘贴图片";
      busy=Boolean(viewReceipt?.pending || job && ["queued","searching","verification"].includes(job.state));render();
      const status=job?.message || (viewReceipt?.pending?"正在准备搜图副本…":viewReceipt?.error);
      message(expired?[status,"图片预览已释放，重新查询请选择图片。"].filter(Boolean).join(" · ")
        : status || "选择图片后，点击上传查询。",Boolean(viewReceipt?.error || job?.state==="error"));
      if(busy && job?.id)void poll(sequence);
    },
    refresh(){
      renderedResults="";
      if(job?.id || viewReceipt?.unconfirmed){clearTimeout(timer);return poll(sequence);}return loadSettings();
    },
    suspend(){clearTimeout(clickTimer);},
  };
};
