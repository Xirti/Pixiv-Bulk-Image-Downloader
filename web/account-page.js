"use strict";

globalThis.createAccountPage = function ({root,request,escape:esc,allowed,changed,openArtist,openArtwork,
    choose,selected,canChoose=()=>true,installImages,attachUgoira,report,mode,signedIn,generation=()=>null,connectionChanged,settingsReady=()=>Promise.resolve(),autoPrepare=()=>true,getWork=()=>null,getAuthor=()=>null,openUpdates=()=>{},statistics=null}) {
  const header=root.querySelector("[data-account-profile]"),tools=root.querySelector("[data-account-tools]");
  const peopleRoot=root.querySelector("[data-account-people]"),grid=root.querySelector("[data-account-grid]");
  const message=root.querySelector("[data-account-message]"),pagination=root.querySelector("[data-account-pagination]");
  const ownPanel=root.querySelector("[data-account-own-works]"),ownGrid=root.querySelector("[data-account-own-grid]");
  const ownCache=createPreviewImageCache({retainUnused:true,maxImages:144});
  const cache=new Map(),positions=new Map(),worksCache=createPreviewImageCache({retainUnused:true,maxImages:480}),avatars=createPreviewImageCache({retainUnused:true,maxImages:240});
  const empty=()=>({view:"home",scope:"public",tag:"",offset:0,page:1,trail:[],scroll:0});
  let state=empty(),profile=null,tags=null,data=null,error="",pending=false,revision=0,controller=null,active=false;
  let own=null,ownMode=null;
  const scopes={following:"public",bookmarks:"public"};
  const labels={home:"资料",following:"关注",followers:"粉丝",bookmarks:"收藏",settings:"设置"};
  let renderedHeader,renderedOwn,renderedPeople,renderedGrid,ownKey="",peopleKey="",gridKey="";
  let backgroundRevision=0,backgroundController=new AbortController(),backgroundQueue=Promise.resolve();
  const reads=createPageReads(request),nextReads=new Map();
  let updatePreferences={detect:true,dots:true},updates={public:{},private:{}},checkingUpdates=null;
  const acknowledged=new Map();
  const pendingUpdateScopes=new Set();
  let relationVersion=0;
  let scrollFrame=null,restoringScroll=false,restoreTop=null;
  function remember(){if(active && !restoringScroll){state.scroll=root.scrollTop;positions.set(state.view,structuredClone(state));}}
  root.addEventListener("scroll",remember,{passive:true});
  const pageKey=()=>JSON.stringify([state.view,state.scope,state.tag,state.offset,mode()]);
  function cachePage(key,value){
    cache.delete(key);cache.set(key,value);
    const current=pageKey();
    while(cache.size>128){const old=[...cache.keys()].find(identity=>identity!==current);cache.delete(old);}
  }
  async function read(view,params={},options={}){
    let current=params;
    for(let attempt=0;attempt<2;attempt++){
      const version=relationVersion;
      const value=await reads.read(`/api/account/${view}?${new URLSearchParams(current)}`,options);
      if(version===relationVersion)return value;
      // The response predates a confirmed relationship change. Read the
      // repaired server cache; only this overlapping read needs replacement.
      current={...params};delete current.refresh;
    }
    throw new Error("账号列表仍在更新，请稍后重试");
  }
  function stopBackground(){
    backgroundRevision++;backgroundController.abort();backgroundController=new AbortController();nextReads.clear();
    // Callers are stopping work; its original caller owns any reported failure.
    return Promise.allSettled([backgroundQueue]);
  }
  function prepare({network=false,refresh=false,page=null,completed=null}={}){
    const version=backgroundRevision,signal=backgroundController.signal,contentMode=mode();
    const run=async()=>{
      if(version!==backgroundRevision || signal.aborted || !signedIn())return;
      const valid=()=>version===backgroundRevision && !signal.aborted && signedIn() && contentMode===mode();
      const params={background:"1",...(!network?{cached:"1"}:{}),...(refresh?{refresh:"1"}:{})};
      if(!page || !profile){
        const value=await read("profile",params,{signal});
        if(!valid())return;
        if(value.cached===false || !value.connected)return;
        profile=value;connectionChanged(profile);
      }
      if(completed){
        const view=page.kind==="bookmark"?"bookmarks":"following";
        for(const key of cache.keys()){
          const [type,scope,tag,offset]=JSON.parse(key);
          if(type===view && (tag || !completed[scope]?.includes(offset)))cache.delete(key);
        }
      }
      const images=[];
      const failures=[];
      if(!page && !network && updatePreferences.detect)await checkUpdates({network:false});
      if(!page || refresh){
        const entries=[{id:`self:${profile.uid}`,thumb:profile.avatar,title:profile.name},{id:`cover:${profile.uid}`,thumb:profile.background,title:""}];
        images.push(()=>avatars.prepare("account-self",entries,signal,{warm:"account"}));
      }
      const pages=page?[{view:page.kind==="bookmark"?"bookmarks":"following",scope:page.scope,offset:page.offset}]:[
        {view:"bookmarks",scope:"public",offset:0},{view:"bookmarks",scope:"private",offset:0},
        {view:"following",scope:"public",offset:0},{view:"following",scope:"private",offset:0},
        ...(profile.method==="app"?[{view:"followers",scope:"public",offset:0}]:[])];
      for(const target of pages){
        if(!valid())return;
        const relation=["bookmarks","following"].includes(target.view);
        let value;
        try{value=await read(target.view,{scope:target.scope,offset:String(target.offset),tag:"",mode:contentMode,
          background:"1",...(relation || !network?{cached:"1"}:{}),...(!relation && refresh?{refresh:"1"}:{})},{signal});}
        catch(problem){if(signal.aborted)throw problem;failures.push(`${labels[target.view]}：${problem.message}`);continue;}
        if(!valid())return;
        if(value.cached===false)continue;
        cachePage(JSON.stringify([target.view,target.scope,"",target.offset,contentMode]),value);
        if(target.offset===0){
          if(value.items)images.push(()=>worksCache.prepare("account-works",value.items,signal,{warm:"account"}));
          if(value.people)images.push(()=>avatars.prepare("account-people",value.people.map(person=>({id:`avatar:${person.id}`,thumb:person.avatar,title:person.name})),signal,{warm:"account"}));
        }
      }
      if(!page){
        for(const view of ["tags",...(profile.workCount===0?[]:["works"])]){
          let value;
          try{value=await read(view,{...params,mode:contentMode},{signal});}
          catch(problem){if(signal.aborted)throw problem;failures.push(`${view==="tags"?"收藏标签":"本人作品"}：${problem.message}`);continue;}
          if(!valid())return;
          if(value.cached===false)continue;
          if(view==="tags")tags=value;
          else{own=value;ownMode=contentMode;images.push(()=>ownCache.prepare("account-own-works",value.items,signal,{warm:"account"}));}
        }
        if(profile.workCount===0){own={items:[]};ownMode=contentMode;}
      }
      // Adopt a synchronized visible page while preserving its scroll position.
      if(valid() && active && !pending){
        if(state.view==="home")render();
        else if(state.view!=="settings" && (page || refresh || !data)){
          const available=cache.get(pageKey());if(available && available!==data){data=available;render();}
        }
      }
      if(!document.hidden && (network || page))for(const warm of images){if(!valid())return;await warm();}
      if(failures.length)throw new Error(failures.join("；"));
    };
    // A failed task is reported to its caller without poisoning subsequent work.
    const queued=backgroundQueue.catch(()=>{}).then(run);
    backgroundQueue=queued;
    return queued;
  }
  function prepareNext(){
    if(!active || !autoPrepare() || !data || data.nextOffset==null || !["following","followers","bookmarks"].includes(state.view))return;
    const target={...state,offset:data.nextOffset},contentMode=mode(),signal=backgroundController.signal,version=backgroundRevision;
    const key=JSON.stringify([target.view,target.scope,target.tag,target.offset,contentMode]);
    if(cache.has(key) || nextReads.has(key))return;
    const reading=read(target.view,{scope:target.scope,offset:String(target.offset),tag:target.tag,mode:contentMode,background:"1"},{signal}).then(async value=>{
      if(version!==backgroundRevision || signal.aborted || contentMode!==mode())return;
      cachePage(key,value);
      if(value.items)await worksCache.prepare("account-works",value.items,signal,{warm:"account"});
      if(value.people)await avatars.prepare("account-people",value.people.map(person=>({id:`avatar:${person.id}`,thumb:person.avatar,title:person.name})),signal,{warm:"account"});
    }).catch(problem=>{if(!signal.aborted && version===backgroundRevision)report(`下一页暂未准备好：${problem.message}`);}).finally(()=>{if(nextReads.get(key)===reading)nextReads.delete(key);});
    nextReads.set(key,reading);
  }
  const artworkContext=item=>({context:{kind:"pid",value:item.id},resultPage:state.page});
  function syncUpdateDots(){
    const enabled=updatePreferences.detect && updatePreferences.dots;
    const dot=tools.querySelector('[data-account-updates] .update-dot');
    if(dot)dot.hidden=!enabled || !Object.values(updates[state.scope]).some(row=>row.isNew);
    peopleRoot.querySelectorAll('[data-person]').forEach(card=>{
      const person=data?.people?.[Number(card.dataset.person)];if(!person)return;
      const marker=updates[state.scope][person.id] || person;
      card.querySelector('.update-dot').hidden=!enabled || !marker.isNew;
    });
  }
  function checkUpdates({network=true,continuing=false}={}){
    if(!updatePreferences.detect || !signedIn())return Promise.resolve({hasMore:false});
    if(checkingUpdates)return checkingUpdates;
    const version=backgroundRevision,signal=backgroundController.signal;
    let task;task=(async()=>{
      let hasMore=false,combined=false;
      for(const scope of ['public','private']){
        const refresh=network && !combined && (!continuing || pendingUpdateScopes.has(scope));
        const result=await read('updates',{scope,background:'1',...(refresh?{refresh:'1'}:{cached:'1'})},{signal});
        if(version!==backgroundRevision || signal.aborted)return {hasMore:false};
        if(result.cached===false)continue;
        if(result.connected===false)return {hasMore:false};
        combined=Boolean(result.combined);
        if(refresh){if(result.hasMore)pendingUpdateScopes.add(scope);else pendingUpdateScopes.delete(scope);}
        hasMore ||= result.hasMore;
        for(const [id,marker] of Object.entries(result.authors)){
          if(updates[scope][id]?.latestObserved>marker.latestObserved)continue;
          updates[scope][id]={...marker,isNew:marker.isNew && marker.latestObserved>(acknowledged.get(id) || '')};
        }
        for(const id of Object.keys(updates[scope]))if(!result.authors[id])delete updates[scope][id];
      }
      syncUpdateDots();return {hasMore};
    })().finally(()=>{if(checkingUpdates===task)checkingUpdates=null;});
    checkingUpdates=task;return task;
  }
  async function acknowledge(authors){
    const version=backgroundRevision,auth=generation();
    const entries=Object.entries(authors);
    for(let offset=0;offset<entries.length;offset+=500){
      const part=Object.fromEntries(entries.slice(offset,offset+500));
      if(version!==backgroundRevision || auth!==generation())return;
      await request('/api/account/seen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({authors:part,authorizationGeneration:auth})});
      if(version!==backgroundRevision || auth!==generation())return;
      for(const [id,stamp] of Object.entries(part)){
        acknowledged.set(id,[acknowledged.get(id) || '',stamp].sort().at(-1));
        for(const scope of ['public','private'])if(updates[scope][id]?.latestObserved<=stamp)updates[scope][id].isNew=false;
        for(const value of cache.values())for(const person of value.people || [])if(person.id===id && person.latestObserved<=stamp)person.isNew=false;
      }
    }
    syncUpdateDots();
  }
  function seenWorks(items){
    if(!updatePreferences.detect)return Promise.resolve();
    const dates={};
    for(const item of items){
      if(!item.sortDate)continue;
      const stamp=new Date(item.sortDate).toISOString().slice(0,19)+'+00:00';
      if(stamp>(acknowledged.get(item.userId) || '') && stamp>(dates[item.userId] || ''))dates[item.userId]=stamp;
    }
    return acknowledge(dates);
  }
  function artist(person){
    if(!allowed())return;
    const stamp=updates[state.scope][person.id]?.latestObserved || person.latestObserved;
    if(stamp)void acknowledge({[person.id]:stamp}).catch(()=>report("作者查看状态暂未保存"));
    openArtist(person.id);
  }
  const interaction=createGalleryInteraction({grid,scrollSurface:root,allowed:()=>active && allowed() && !pending,
    openArtwork:index=>{const item=data?.items?.[index];if(item)openArtwork(item,artworkContext(item));},
    openArtist:item=>openArtist(item.userId),report});
  const ownContext=()=>({context:{kind:"uid",value:profile.uid},resultPage:1});
  const ownInteraction=createGalleryInteraction({grid:ownGrid,scrollSurface:root,allowed:()=>active && allowed() && !pending,
    openArtwork:index=>{const item=own?.items?.[index];if(item)openArtwork(item,ownContext());},
    openArtist:item=>openArtist(item.userId),report});

  function syncSelection(){
    grid.querySelectorAll("[data-select]").forEach(box=>{const item=data?.items?.[Number(box.dataset.select)];if(item)box.checked=selected(item.id);box.disabled=!canChoose();});
    ownGrid.querySelectorAll("[data-select]").forEach(box=>{const item=own?.items?.[Number(box.dataset.select)];if(item)box.checked=selected(item.id);box.disabled=!canChoose();});
  }
  function render(){
    preserveViewport(root,()=>{
    root.querySelectorAll("[data-account-tab]").forEach(button=>button.setAttribute("aria-selected",String(button.dataset.accountTab===state.view)));
    header.hidden=state.view!=="home";
    statistics?.setVisible(active && state.view==="home");
    root.querySelector("[data-account-settings]").hidden=state.view!=="settings";
    message.hidden=state.view==="settings";
    root.querySelector("[data-account-management]").hidden=state.view!=="home";
    root.classList.toggle("account-overview",state.view==="home");
    message.textContent=error || (pending?"正在读取……":!profile?.connected && state.view!=="home"?"请在「资料」中完成 Pixiv 登录与账号连接。":data?.updatesError || "");
    message.classList.toggle("is-error",Boolean(error));
    root.setAttribute("aria-busy",String(pending));
    const p=profile;
    if(renderedHeader!==p){
    renderedHeader=p;avatars.park(header);
    header.innerHTML=p?.connected?`${p.background?`<img class="account-cover" data-gallery-src="${esc(p.background)}" data-preview-artwork="cover:${esc(p.uid)}" alt="">`:""}<div class="account-identity">${p.avatar?`<img class="account-avatar" data-gallery-src="${esc(p.avatar)}" data-preview-artwork="self:${esc(p.uid)}" alt="${esc(p.name)}">`:'<span class="account-avatar account-avatar-empty" aria-hidden="true">人</span>'}<h2>${esc(p.name)}</h2><p class="account-uid">UID <span>${esc(p.uid)}</span>${p.account?` · ${esc(p.account)}`:""}</p>${p.bio?`<p class="account-bio">${esc(p.bio)}</p>`:""}${p.region?`<p class="account-location">${esc(p.region)}</p>`:""}</div><div class="account-stats"><button type="button" data-shortcut="following"><strong>${p.following ?? "查看"}</strong><span>关注</span></button><button type="button" data-shortcut="followers"><strong class="account-stat-label">查看</strong><span>粉丝</span></button><button type="button" data-shortcut="bookmarks"><strong>${p.publicBookmarks ?? "查看"}</strong><span>公开收藏</span></button></div>`:
      `<div class="account-connect"><span class="account-avatar account-avatar-empty" aria-hidden="true">人</span><h2>${pending?"正在读取账号":"我的 Pixiv 账号"}</h2><p>登录后浏览关注的作者、粉丝与云端收藏。</p></div>`;
    avatars.mount(header,"account-self");installImages(header);
    header.querySelectorAll("[data-shortcut]").forEach(button=>button.onclick=()=>navigate(button.dataset.shortcut));
    }
    ownPanel.hidden=state.view!=="home" || !p?.connected || !(own?.items?.length || p.workCount>0 || own?.error);
    const ownMessage=root.querySelector("[data-account-own-message]");
    ownMessage.textContent=own?.error || (!own?.items?.length && p?.workCount>0?"当前范围暂无可展示的作品预览。":"");
    ownMessage.hidden=!ownMessage.textContent;
    const nextOwnKey=JSON.stringify([p?.uid,ownMode]);
    if(!ownPanel.hidden && (renderedOwn!==own?.items || ownKey!==nextOwnKey)){
      renderedOwn=own?.items;ownKey=nextOwnKey;ownInteraction.reset({immediate:true});ownCache.park(ownGrid);
      ownGrid.innerHTML=renderArtworkCards(own?.items || [],{escape:esc,selected});
      ownCache.mount(ownGrid,"account-own-works");
      installImages(ownGrid);attachUgoira(ownGrid);ownInteraction.mount(own?.items || []);
      ownGrid.querySelectorAll("[data-select]").forEach(box=>box.onchange=()=>{const item=own.items[Number(box.dataset.select)];if(!choose(item,box.checked,ownContext()))box.checked=selected(item.id);});
    }
    let toolbar="";
    if(state.view==="following" || state.view==="bookmarks"){
      toolbar=`<div class="account-privacy" role="group" aria-label="公开范围"><button type="button" data-scope="public" aria-pressed="${state.scope==="public"}">公开</button><button type="button" data-scope="private" aria-pressed="${state.scope==="private"}">非公开</button></div>`;
      if(state.view==="bookmarks")toolbar+=`<label class="account-tag-filter">分类<select aria-label="收藏分类"><option value="">全部收藏</option>${(tags?.[state.scope] || []).map(tag=>`<option value="${esc(tag.name)}" ${tag.name===state.tag?"selected":""}>${esc(tag.name)} · ${tag.count}</option>`).join("")}</select></label>`;
    }
    if(state.view==="following")toolbar+='<button type="button" class="account-updates-link" data-account-updates>查看新作<span class="update-dot" hidden aria-hidden="true"></span></button>';
    const total=data?.total;
    if(state.view!=="home")toolbar+=`<span class="account-total">${Number.isInteger(total)?`${total} ${state.view==="bookmarks"?"件作品":"位"+labels[state.view]}`:""}</span>`;
    const privacy=tools.querySelector(".account-privacy");
    if(privacy && ["following","bookmarks"].includes(state.view)){
      const next=document.createElement("template");next.innerHTML=toolbar;
      next.content.querySelector(".account-privacy").remove();
      tools.querySelectorAll(":scope > :not(.account-privacy)").forEach(node=>node.remove());
      tools.append(next.content);
      privacy.querySelectorAll("[data-scope]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.scope===state.scope)));
    }else tools.innerHTML=toolbar;
    tools.hidden=["home","settings"].includes(state.view);
    tools.querySelectorAll("[data-scope]").forEach(button=>button.onclick=()=>navigate(state.view,{scope:button.dataset.scope,tag:""}));
    tools.querySelector("select")?.addEventListener("change",event=>navigate("bookmarks",{tag:event.target.value}));
    tools.querySelector("[data-account-updates]")?.addEventListener("click",async()=>{
      const version=backgroundRevision;
      const seen=Object.fromEntries(Object.entries(updates[state.scope]).filter(([,marker])=>marker.isNew).map(([id,marker])=>[id,marker.latestObserved]));
      const opened=await openUpdates(state.scope);if(opened===false || version!==backgroundRevision)return;
      try{await acknowledge(seen);}catch{report("新作查看状态暂未保存");}
    });

    peopleRoot.hidden=!p?.connected || !["following","followers"].includes(state.view);
    grid.hidden=!p?.connected || state.view!=="bookmarks";
    if(!peopleRoot.hidden && (renderedPeople!==data?.people || peopleKey!==pageKey())){
    renderedPeople=data?.people;peopleKey=pageKey();avatars.park(peopleRoot);
    peopleRoot.innerHTML=(data?.people || []).map((person,index)=>`<article class="account-person" data-person="${index}"><a class="account-person-avatar" href="#artist/${esc(person.id)}" aria-label="${esc(person.name)}"><span class="update-dot" hidden aria-hidden="true"></span>${person.avatar?`<img data-gallery-src="${esc(person.avatar)}" data-preview-artwork="avatar:${esc(person.id)}" alt="" loading="lazy" decoding="async">`:'<span aria-hidden="true">人</span>'}</a><div class="account-person-info"><a class="account-person-name" href="#artist/${esc(person.id)}">${esc(person.name)}</a><span class="account-person-uid">UID ${esc(person.id)}${state.view==="followers"&&person.following?' · 互相关注':""}</span><p class="account-person-bio">${person.bioLoaded?esc(person.bio || "暂未填写简介"):'<button type="button" data-person-bio>查看简介</button>'}</p>${person.latestObserved?`<span class="account-person-update" title="已读取范围内的最新作品">最新观察 · ${esc(person.latestObserved.slice(0,10))}</span>`:""}</div></article>`).join("");
    if(!peopleRoot.hidden && !pending && !error && !(data?.people?.length))peopleRoot.innerHTML=`<p class="account-empty">${state.view==="followers"?"目前还没有粉丝。":"这个范围内还没有关注的作者。"}</p>`;
    avatars.mount(peopleRoot,"account-people");installImages(peopleRoot);
    peopleRoot.querySelectorAll("[data-person]").forEach(row=>{
      const person=data.people[Number(row.dataset.person)];
      row.querySelectorAll("a").forEach(link=>link.onclick=event=>{event.preventDefault();if(!mokuHasSelectedText(link))artist(person);});
      row.querySelector("[data-person-bio]")?.addEventListener("click",async event=>{
        const button=event.currentTarget;button.disabled=true;button.textContent="读取中…";
        try{const value=await request(`/api/pixiv/artist/${person.id}`);person.bio=value.description || value.bio || "";person.bioLoaded=true;
          row.querySelector(".account-person-bio").textContent=person.bio || "暂未填写简介";
        }catch{button.disabled=false;button.textContent="读取失败，重试";}
      });
    });
    }
    if(state.view==="bookmarks" && p?.connected && (renderedGrid!==data?.items || gridKey!==pageKey())){
      renderedGrid=data?.items;gridKey=pageKey();interaction.reset({immediate:true});worksCache.park(grid);
      grid.innerHTML=renderArtworkCards(data?.items || [],{escape:esc,selected});
      if(!pending && !error && !data?.items?.length)grid.innerHTML='<p class="account-empty">这个范围内没有可显示的收藏作品。</p>';
      worksCache.mount(grid,"account-works");installImages(grid);attachUgoira(grid);interaction.mount(data?.items || []);
      grid.querySelectorAll("[data-select]").forEach(box=>box.onchange=()=>{const item=data.items[Number(box.dataset.select)];if(!choose(item,box.checked,artworkContext(item)))box.checked=selected(item.id);});
    }
    pagination.hidden=["home","settings"].includes(state.view) || !p?.connected;
    pagination.innerHTML=`<button type="button" data-account-prev ${!state.trail.length || pending?"disabled":""}>← 上一页</button><span>第 ${state.page} 页</span><button type="button" data-account-next ${data?.nextOffset==null || pending?"disabled":""}>下一页 →</button>${data?.unavailable?`<small>${data.unavailable} 件因当前安全范围或作品状态未显示</small>`:""}`;
    pagination.querySelector("[data-account-prev]")?.addEventListener("click",()=>{const trail=state.trail.slice(),offset=trail.pop();navigate(state.view,{offset,page:state.page-1,trail});});
    pagination.querySelector("[data-account-next]")?.addEventListener("click",()=>navigate(state.view,{offset:data.nextOffset,page:state.page+1,trail:[...state.trail,state.offset]}));
    },active?restoreTop:null);
    if(active){
      if(!pending)restoreTop=null;
      state.scroll=root.scrollTop;
      restoringScroll=true;
      cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(()=>{restoringScroll=false;scrollFrame=null;});
    }
    syncSelection();syncUpdateDots();changed();
  }

  async function load(refresh=false){
    controller?.abort();controller=new AbortController();const signal=controller.signal,version=++revision,key=pageKey();
    error="";data=cache.get(key) || null;
    if(data)cachePage(key,data);
    if(!refresh && profile?.connected && (state.view==="home"?own && ownMode===mode():state.view!=="settings" && data && (state.view!=="bookmarks" || tags))){
      pending=false;render();prepareNext();return;
    }
    pending=!data;
    if(ownMode!==mode())own=null;
    render();
    try{
      if(state.view==="settings"){await settingsReady();return;}
      if(!signedIn()){profile=null;data=null;connectionChanged(null);return;}
      if(!profile || refresh){
        const value=await read("profile",refresh?{refresh:"1"}:{},{signal});
        if(version!==revision)return;
        profile=value;connectionChanged(profile);
      }
      if(version!==revision)return;
      if(!profile.connected){data=null;return;}
      if(state.view==="home"){
        if(profile.workCount===0){own={items:[]};ownMode=mode();}
        else if(!own || refresh || ownMode!==mode()){
          render();const requestedMode=mode();
          try{
            const value=await read("works",{mode:requestedMode,...(refresh?{refresh:"1"}:{})},{signal});
            if(version!==revision)return;own=value;ownMode=requestedMode;
          }catch(problem){
            if(version!==revision || signal.aborted)return;
            own={items:[],error:problem.message || "本人作品暂未读取，可右键刷新重试"};ownMode=requestedMode;
          }
        }
      }
      if(state.view!=="home" && (!data || refresh)){
        const query=new URLSearchParams({scope:state.scope,offset:String(state.offset),tag:state.tag,mode:mode(),...(refresh?{refresh:"1"}:{})});
        const value=await read(state.view,Object.fromEntries(query),{signal});
        if(version!==revision)return;data=value;cachePage(key,value);
        pending=false;render();
      }
      if(state.view==="bookmarks" && (!tags || refresh))tags=await read("tags",refresh?{refresh:"1"}:{},{signal});
      if(version!==revision)return;
      if(profile.authorization?.error)error=profile.authorization.error;
    }catch(problem){if(version!==revision || signal.aborted)return;error=problem.message || "账号读取失败，请刷新重试";}
    finally{if(version===revision){pending=false;render();prepareNext();}}
  }
  async function navigate(view,options={}){
    if(!allowed())return;
    remember();
    const same=view===state.view;
    const saved=!same && !Object.keys(options).length?positions.get(view):null;
    state=saved?structuredClone(saved):{view,scope:options.scope || (same?state.scope:scopes[view] || "public"),tag:options.tag ?? (same?state.tag:""),
      offset:options.offset ?? 0,page:options.page ?? 1,trail:options.trail || [],scroll:0};
    restoreTop=state.scroll;
    if(scopes[view]!==undefined)scopes[view]=state.scope;
    await load();
  }
  function clear(keepView=false){
    void stopBackground();
    controller?.abort();reads.clear();revision++;profile=null;tags=null;data=null;own=null;ownMode=null;
    state=keepView?{...state,offset:0,page:1,trail:[],scroll:0}:empty();restoreTop=0;positions.clear();
    updates={public:{},private:{}};acknowledged.clear();pendingUpdateScopes.clear();checkingUpdates=null;
    interaction.clear();ownInteraction.clear();
    cache.clear();worksCache.clear();ownCache.clear();avatars.clear();
    for(const node of [header,ownGrid,peopleRoot,grid])node.innerHTML="";
    renderedHeader=undefined;renderedOwn=renderedPeople=renderedGrid=null;ownKey=peopleKey=gridKey="";
    error="";pending=false;render();
  }
  root.querySelectorAll("[data-account-tab]").forEach(button=>button.onclick=()=>navigate(button.dataset.accountTab));
  root.querySelector("[data-account-own-all]").onclick=()=>{if(allowed() && profile?.uid)openArtist(profile.uid);};
  return {open:async snapshot=>{remember();active=true;if(snapshot)state={...empty(),...structuredClone(snapshot)};restoreTop=state.scroll;await load();},
    snapshot:()=>{remember();return structuredClone(state);},refresh:()=>{remember();return Promise.all([load(true),state.view==="home"?statistics?.refresh():null]);},syncSelection,
    cachedTags:()=>tags,prepare,stopBackground,checkUpdates,seenWorks,
    configure(value,loaded){if(!loaded)return;updatePreferences={detect:value.autoFollowing,dots:value.updateDots};syncUpdateDots();},
    cloudChanged:(kind,id,value,previous)=>{
      relationVersion++;
      if(kind==='follow')for(const scope of ['public','private'])if(!value.active || value.privacy && value.privacy!==scope)delete updates[scope][id];
      const view=kind==="bookmark"?"bookmarks":"following",field=kind==="bookmark"?"items":"people";
      const existing=[...cache.values()].flatMap(page=>page[field] || []).find(row=>row.id===id);
      const record=existing || (kind==="bookmark"?getWork(id):getAuthor(id));
      if(kind==="follow")for(const [key,page] of cache)if(JSON.parse(key)[0]==="followers"){
        for(const person of page.people || [])if(person.id===id)person.following=value.active;
      }
      for(const [key,page] of cache){
        const [type,scope,tag,offset,contentMode]=JSON.parse(key);
        if(type!==view || !page[field])continue;
        const rows=page[field],old=rows.length;
        const membership=relation=>{
          if(!relation?.known)return null;
          if(!relation.active || relation.privacy && relation.privacy!==scope)return false;
          if(!relation.privacy && !rows.some(row=>row.id===id))return null;
          if(kind==="bookmark"){
            if(tag){if(relation.tags===null)return null;if(!relation.tags.includes(tag))return false;}
            if(contentMode!=="all"){
              if(!record)return null;
              if((contentMode==="r18")!==(record.restriction==="r18"))return false;
            }
          }
          return true;
        };
        const previousMember=membership(previous),nextMember=membership(value);
        const fits=nextMember===true || nextMember===null && rows.some(row=>row.id===id);
        const next=rows.filter(row=>row.id!==id || fits);
        if(fits && !offset && record && !next.some(row=>row.id===id))next.unshift({...record,...(kind==="follow"?{following:true}:{})});
        if(next.length!==old || next.some((row,index)=>row!==rows[index])){
          page[field]=next;
        }
        const delta=previousMember!==null && nextMember!==null?Number(nextMember)-Number(previousMember):next.length-old;
        if(delta && Number.isInteger(page.total))page.total=Math.max(0,page.total+delta);
      }
      if(profile && previous?.known && (kind==="follow" || !value.active || value.privacy)){
        const delta=kind==="follow"?Number(value.active)-Number(previous.active):
          Number(value.active && value.privacy==="public")-Number(previous.active && previous.privacy==="public");
        const count=kind==="follow"?"following":"publicBookmarks";
        if(Number.isInteger(profile[count]))profile[count]=Math.max(0,profile[count]+delta);
        if(delta)renderedHeader=undefined;
      }
      if(kind==="bookmark" && tags && previous?.known &&
          (!previous.active || previous.privacy && previous.tags!==null) && (!value.active || value.privacy && value.tags!==null)){
        for(const scope of ["public","private"]){
          const oldTags=previous.active && previous.privacy===scope?previous.tags || []:[];
          const newTags=value.active && value.privacy===scope?value.tags || []:[];
          for(const name of new Set([...oldTags,...newTags])){
            const delta=Number(newTags.includes(name))-Number(oldTags.includes(name));
            const row=tags[scope]?.find(row=>row.name===name);
            if(row)row.count=Math.max(0,row.count+delta);
            else if(delta>0)(tags[scope] ||= []).push({name,count:delta});
          }
          tags[scope]=(tags[scope] || []).filter(row=>row.count>0);
        }
      }
      if(active)render();
    },
    invalidateConnection:()=>{profile=null;},
    suspend:()=>{remember();active=false;statistics?.setVisible(false);controller?.abort();revision++;pending=false;interaction.reset({immediate:true});ownInteraction.reset({immediate:true});},
    clear,clearCache:()=>clear(true),
    label:()=>"我的账号"};
};
