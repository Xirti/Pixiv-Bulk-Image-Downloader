"use strict";

globalThis.createSourceSearch = function ({root,settingsRoot,request,escape:esc,changed,openArtwork,preview,openExternal,allowed,installImages=()=>{}}) {
  const find=selector=>root.querySelector(selector), setting=selector=>settingsRoot.querySelector(selector);
  let file=null, imageUrl="", job=null, busy=false, sequence=0, timer=null, clickTimer=null;
  let configuration={mode:"guest",keyConfigured:false}, settingsLoaded=false;
  let maskSensitive=true;
  const revealed = createSensitiveImageReveals({root,onHide:()=>render()});
  let revealedJob = null;
  let recentlyRevealedId=null,revealedAt=-Infinity;
  let cacheEpoch=0;
  let loginTimer=null;
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
  function message(text,error=false){find("[data-source-message]").textContent=text;find("[data-source-message]").classList.toggle("is-error",error);}
  function controls(){
    find("[data-source-query]").disabled=!file || busy;
    find("[data-source-cancel]").hidden=!busy;
    find("[data-source-cancel]").disabled=!job?.id;
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
    clearTimeout(timer);clearTimeout(clickTimer);sequence++;job=null;render();
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
  function apply(data){
    job=data;busy=["queued","searching","verification"].includes(job.state);
    message(job.message,job.state==="error");render();changed();
  }
  async function poll(revision){
    if(revision!==sequence || !job?.id)return;
    try{
      const data=await request(`/api/source-search/job?id=${encodeURIComponent(job.id)}`);
      if(revision!==sequence)return;
      apply(data);
      if(busy)timer=setTimeout(()=>void poll(revision),1500);
    }catch(error){
      if(revision!==sequence)return;
      busy=false;message(`${error.message}。可点击「读取状态」重试，查询不会重复提交。`,true);controls();
    }
  }
  async function search(){
    if(!allowed() || !file || busy)return;
    const revision=++sequence;clearTimeout(timer);clearTimeout(clickTimer);job=null;busy=true;render();message("正在准备搜图副本…");
    try{
      const data=await request(`/api/source-search/query?mode=${find("[data-source-mode]").value}`,{method:"POST",headers:{"Content-Type":file.type || "application/octet-stream"},body:file},30000);
      if(revision!==sequence)return;
      apply(data);if(busy)timer=setTimeout(()=>void poll(revision),1500);
    }catch(error){if(revision===sequence){busy=false;message(error.message,true);controls();}}
  }
  function showSettings(){
    setting("[data-source-setting-mode]").value=configuration.mode;
    setting("[data-source-key-status]").textContent=configuration.keyConfigured?"已安全保存 API Key":"尚未配置 API Key，官网查询可独立登录";
    const states={"signed-in":"上次核对已登录","signed-out":"尚未登录","unknown":"尚未核对"};
    setting("[data-source-website-state]").textContent=`SauceNAO 官网会话 · ${states[configuration.websiteLogin] || states.unknown}`;
    find("[data-source-mode]").value=configuration.mode;
  }
  async function loadSettings(){
    try{
      configuration=await request("/api/source-search/settings");settingsLoaded=true;showSettings();render();
      if(configuration.error){setting("[data-source-settings-message]").textContent=configuration.error;message(configuration.error,true);}
    }
    catch(error){setting("[data-source-settings-message]").textContent=error.message;}
  }
  async function saveSettings(action){
    const status=setting("[data-source-settings-message]"), buttons=settingsRoot.querySelectorAll("button");
    buttons.forEach(button=>button.disabled=true);status.textContent="正在保存…";
    const key=setting("[data-source-key]");
    try{
      const data={action,mode:setting("[data-source-setting-mode]").value};
      if(action==="save" && key.value.trim())data.key=key.value.trim();
      configuration=await post("settings",data);key.value="";showSettings();render();
      if(action==="clear"){
        clearTimeout(timer);clearTimeout(clickTimer);sequence++;cacheEpoch++;job=null;busy=false;render();changed();
        images.clear();
      }
      status.textContent=action==="clear"?"搜图结果缓存已清除":action==="reset-guest"?"游客会话已重置":action==="remove-key"?"API Key 已移除":"搜图设置已保存";
    }catch(error){status.textContent=error.message;}
    finally{buttons.forEach(button=>button.disabled=false);}
  }
  find("[data-source-file]").addEventListener("change",event=>choose(event.target.files[0]));
  find("[data-source-query]").onclick=()=>void search();
  async function login() {
    clearTimeout(loginTimer);
    try {
      const task=await post("login",{});
      setting("[data-source-settings-message]").textContent="请在官网窗口输入账号、密码并完成验证";
      let remaining=200;
      async function check() {
        try {
          const state=await request(`/api/source-search/job?id=${encodeURIComponent(task.id)}`);
          configuration=await request("/api/source-search/settings");
          const selected=find("[data-source-mode]").value;
          showSettings();find("[data-source-mode]").value=selected;
          if (state.state === "done" || state.state === "error" || state.state === "cancelled") {
            setting("[data-source-settings-message]").textContent=state.message;
          } else if (--remaining > 0) loginTimer=setTimeout(()=>void check(),3000);
        } catch(error) {setting("[data-source-settings-message]").textContent=error.message;}
      }
      loginTimer=setTimeout(()=>void check(),1500);
    } catch(error) {setting("[data-source-settings-message]").textContent=error.message;message(error.message,true);}
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
  find("[data-source-read]").onclick=()=>{if(job?.id){clearTimeout(timer);void poll(sequence);}else message("还没有发起查询，请先选择图片。");};
  find("[data-source-cancel]").onclick=async()=>{
    if(!job?.id)return;
    try{await post("cancel",{id:job.id});clearTimeout(timer);sequence++;apply({...job,state:"cancelled",message:"已取消查询；已发出的请求可能仍计入额度"});}
    catch(error){message(error.message,true);}
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
    snapshot:()=>({job,imageUrl,name:find("[data-source-file-name]").textContent,epoch:cacheEpoch}),
    restore(state){
      clearTimeout(timer);clearTimeout(clickTimer);sequence++;
      if(imageUrl!==state?.imageUrl){file=null;find("[data-source-file]").value="";}
      job=state?.epoch===cacheEpoch?state.job:null;imageUrl=state?.imageUrl || "";
      const image=find("[data-source-input-image]");image.hidden=!imageUrl;if(imageUrl)image.src=imageUrl;else image.removeAttribute("src");
      find("[data-source-file-name]").textContent=state?.name || "选择、拖入或粘贴图片";
      busy=Boolean(job && ["queued","searching","verification"].includes(job.state));render();message(job?.message || "选择图片后，点击上传查询。");
      if(busy)void poll(sequence);
    },
    refresh(){
      renderedResults="";
      if(job?.id){clearTimeout(timer);return poll(sequence);}return loadSettings();
    },
    suspend(){clearTimeout(clickTimer);},
  };
};
