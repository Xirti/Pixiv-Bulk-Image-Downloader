"use strict";

// Text selection and navigation are separate actions on every author link.
globalThis.mokuHasSelectedText = function (element) {
  const selection=window.getSelection();
  return selection.rangeCount>0 && !selection.isCollapsed && selection.getRangeAt(0).intersectsNode(element);
};

globalThis.createArtistPage = function ({root,fetchJson,nextRequestId,cancelRequest,openArtist,openArtwork,onSort,onLoad,changed,allowed,escape:esc}) {
  const empty=()=>({kind:"none",userId:null,profile:null,overview:null,profileError:"",overviewError:"",query:"",page:1,candidates:[],hasMore:false,visible:6,sort:"default",sortMessage:"",paused:false,manualLoad:false,busy:false});
  let state=empty(),controller=null,revision=0,requests=new Set(),renderedKey="",renderPending=false,hydrating=null;
  const images=createPreviewImageCache({maxImages:128,reuseDetached:true});
  function suspend(){revision++;controller?.abort();controller=null;for(const id of requests)cancelRequest(id);requests.clear();}
  function begin(){suspend();controller=new AbortController();return {generation:revision,signal:controller.signal};}
  async function request(path,signal){
    const id=nextRequestId();requests.add(id);
    try{return await fetchJson(`${path}${path.includes("?")?"&":"?"}requestId=${encodeURIComponent(id)}`,{signal});}
    finally{requests.delete(id);}
  }
  function avatar(profile){return profile.avatar?`<img class="artist-avatar" data-gallery-src="${esc(profile.avatar)}" alt="${esc(profile.name)} 的头像">`:'<span class="artist-avatar artist-avatar-empty" aria-hidden="true">✦</span>';}
  function render(){
    const key=`${state.kind}:${state.userId || state.query}:${state.page}`;
    if(key===renderedKey && mokuHasSelectedText(root)){renderPending=true;changed();return;}
    renderedKey=key;renderPending=false;
    images.park(root);
    root.hidden=state.kind==="none";
    const results=root.closest(".results");
    results.classList.toggle("artist-mode",state.kind==="profile");
    results.classList.toggle("artist-candidates",state.kind==="candidates");
    if(root.hidden){root.replaceChildren();changed();return;}
    if(state.kind==="candidates"){
      root.innerHTML=`<div class="artist-candidate-heading"><p class="eyebrow">FIND THE ARTIST</p><h2>选择你要找的作者</h2><p>${esc(state.query)} · 根据头像、UID 和简介确认</p></div><div class="artist-candidate-grid">${state.candidates.slice(0,state.visible).map(profile=>`<article class="artist-candidate">${avatar(profile)}<div><a class="artist-name-link" href="#artist/${esc(profile.userId)}" data-artist-id="${esc(profile.userId)}">${esc(profile.name)}</a><span class="artist-uid">UID ${esc(profile.userId)}</span></div><p>${esc(profile.description || profile.descriptionStatus || "简介加载中…")}</p></article>`).join("")}</div>${!state.candidates.length?'<p class="artist-inline-status">'+(state.loading?'正在寻找作者…':state.error?esc(state.error):'没有找到同名作者，可试试 UID。')+'</p>':""}${state.error?'<button class="artist-more" data-artist-name-retry type="button">重试作者搜索</button>':""}${state.visible<state.candidates.length?'<button class="artist-more" data-artist-more type="button">显示更多作者</button>':""}${state.hasMore?'<button class="artist-more" data-artist-next type="button">下一页候选 →</button>':""}`;
      root.querySelector("[data-artist-name-retry]")?.addEventListener("click",()=>openArtist(null,{name:state.query,page:state.page}));
      root.querySelector("[data-artist-more]")?.addEventListener("click",()=>{state.visible+=6;render();void hydrateCandidates();});
      root.querySelector("[data-artist-next]")?.addEventListener("click",()=>openArtist(null,{name:state.query,page:state.page+1}));
    }else{
      const profile=state.profile || {name:`Pixiv 用户 ${state.userId}`,userId:state.userId,avatar:"",description:"",background:"",region:"",webpage:""};
      const overview=state.overview;
      root.innerHTML=`${profile.background?`<div class="artist-cover"><img data-gallery-src="${esc(profile.background)}" alt=""></div>`:""}<div class="artist-identity">${avatar(profile)}<p class="eyebrow">ARTIST</p><h1>${esc(profile.name)}</h1><div class="artist-cloud-actions">${cloudActionButton("follow",profile.userId,true)}</div><p class="artist-uid">UID <span>${esc(profile.userId)}</span>${profile.region?` · ${esc(profile.region)}`:""}</p>${overview?`<div class="artist-stats"><span><b>${overview.counts.illusts}</b> 插画／动图</span><span><b>${overview.counts.manga}</b> 漫画</span></div>`:""}${profile.description?`<details class="artist-biography"><summary>${esc(profile.description.split(/\r?\n/).find(line=>line.trim()) || "简介")}</summary><p>${esc(profile.description)}</p></details>`:'<p class="artist-inline-status">'+(state.profileError?esc(state.profileError):state.profile?'暂无简介':state.paused?'点击开始加载作者资料与作品':'正在读取作者资料…')+'</p>'}${profile.webpage?`<p class="artist-website">${esc(profile.webpage)}</p>`:""}${state.profileError?'<button class="artist-more" data-artist-retry type="button">重试资料</button>':""}</div>${overview?.featured.length?`<section class="artist-featured"><h2>作者精选 <small>${overview.featured.length}</small></h2><div class="artist-featured-grid">${overview.featured.map((item,index)=>`<button type="button" class="artist-featured-card" data-featured="${index}"><img data-gallery-src="${esc(item.thumb)}" alt="${esc(item.title)}"><span>${esc(item.title)}</span></button>`).join("")}</div></section>`:""}${state.overviewError?`<p class="artist-inline-status">${esc(state.overviewError)} <button type="button" class="artist-more" data-artist-retry>重试</button></p>`:""}<div class="artist-works-bar"><h2>作品</h2>${popularitySortMarkup("data-artist-sort",state.sort)}<span data-artist-sort-status class="popularity-status" role="status">${esc(state.sortMessage || "")}</span></div>`;
      root.querySelectorAll("[data-featured]").forEach(button=>{button.onclick=()=>{if(allowed())openArtwork(state.overview.featured[Number(button.dataset.featured)]);};});
      root.querySelectorAll("[data-artist-sort]").forEach(button=>button.onclick=()=>{if(allowed())onSort(state.sort==="popular"?"default":"popular");});
      root.querySelectorAll("[data-artist-retry]").forEach(button=>{button.onclick=()=>showProfile(state.userId,true);});
    }
    root.querySelectorAll("[data-artist-id]").forEach(link=>{link.onclick=event=>{
      event.preventDefault();event.stopPropagation();if(allowed() && !mokuHasSelectedText(link))openArtist(link.dataset.artistId);
    };});
    images.mount(root,key);
    root.querySelectorAll("img").forEach(image=>{image.onerror=()=>{image.classList.add("artist-image-unavailable");};});
    setPopularityLoad(root,state.manualLoad,state.busy,state.loadButtonVisible!==false,Boolean(state.reloadReady));
    const start=root.querySelector("[data-popularity-start]");if(start)start.onclick=()=>{if(state.busy || allowed())onLoad();};
    changed();
  }
  async function hydrateCandidates(){
    const owner=controller,generation=revision;
    if(hydrating===generation)return;
    hydrating=generation;
    try{
      for(let index=0;index<Math.min(state.visible,state.candidates.length);index++){
        if(generation!==revision || !owner || owner.signal.aborted)return;
        const profile=state.candidates[index];
        if(profile.description || profile.descriptionStatus)continue;
        try{
          const fresh=await request(`/api/pixiv/artist/${profile.userId}`,owner.signal);
          if(generation!==revision)return;
          Object.assign(profile,fresh,{descriptionStatus:fresh.description?"":"暂无简介"});render();
        }catch(error){
          if(generation!==revision || owner.signal.aborted)return;
          profile.descriptionStatus="简介暂未加载";render();
        }
      }
    }finally{if(hydrating===generation)hydrating=null;}
  }
  function showName(query,data=null){
    begin();state={...empty(),kind:"candidates",query,loading:!data,...(data || {})};render();
    if(data && !state.paused)void hydrateCandidates();
  }
  function showProfile(userId,force=false,refresh=false){
    const preserve=state.kind==="profile" && state.userId===userId;
    if(!force && preserve && (state.profile && state.overview || requests.size>0 && controller && !controller.signal.aborted))return;
    const {generation,signal}=begin();state=preserve?{...state,profileError:"",overviewError:"",paused:false}:{...empty(),kind:"profile",userId};render();
    for(const kind of ["profile","overview"]){
      if(!force && state[kind])continue;
      request(`/api/pixiv/artist/${userId}${kind==="overview"?"/overview":""}${refresh?"?refresh=1":""}`,signal).then(data=>{
        if(generation!==revision)return;state[kind]=data;render();
      }).catch(error=>{
        if(generation!==revision || signal.aborted)return;
        state[kind+"Error"]=kind==="profile"?"作者资料读取失败，作品仍可浏览":"作品统计与精选暂未加载";render();
      });
    }
  }
  function clear(){suspend();images.clear();state=empty();render();}
  function prepareProfile(userId,sort,manualLoad){
    suspend();state={...empty(),kind:"profile",userId,sort,manualLoad,paused:true,busy:false};render();
  }
  function setLoading(manual,busy=false,visible=true,loaded=false){state.manualLoad=manual;state.busy=busy;state.loadButtonVisible=visible;state.reloadReady=loaded;setPopularityLoad(root,manual,busy,visible,loaded);}
  function setSort(value,message="") {
    state.sort=value;state.sortMessage=message;
    setPopularitySort(root,value);
    const status=root.querySelector("[data-artist-sort-status]");if(status)status.textContent=message;
    changed();
  }
  function setWorks(){setSort("default");}
  function restore(snapshot){suspend();state=snapshot?structuredClone(snapshot):empty();if(!["default","popular"].includes(state.sort))state.sort="default";render();resume();}
  function resume(){if(state.paused)return;if(state.kind==="profile" && (!state.profile || !state.overview))showProfile(state.userId);else if(state.kind==="candidates"){begin();void hydrateCandidates();}}
  document.addEventListener("selectionchange",()=>{if(renderPending && !mokuHasSelectedText(root))render();});
  return {showName,showProfile,prepareProfile,clear,suspend,resume,setWorks,setSort,setLoading,snapshot:()=>structuredClone(state),restore};
};
