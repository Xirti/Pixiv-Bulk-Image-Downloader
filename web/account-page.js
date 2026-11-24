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
  const reads=createPageReads(request),nextReads=new Map(),bioReads=new Map();
  let updatePreferences={detect:true,dots:true},updates={public:{},private:{}},checkingUpdates=null;
  let updateController=new AbortController();
  const acknowledged=new Map();
  const pendingUpdateScopes=new Set();
  const completedScans=new Map();
  const obsoletePage=(scan,scope,tag,offset)=>Boolean(tag) || !scan[scope]?.includes(offset);
  const retiredByScan=(before,latest,{scope,tag,offset})=>
    offset>0 && latest && latest!==before && obsoletePage(latest,scope,tag,Number(offset));
  let relationVersion=0;
  let scrollFrame=null,restoringScroll=false,restoreTop=null;
  function remember(){
    if(!active)return;
    if(!restoringScroll)state.scroll=root.scrollTop;
    positions.set(state.view,structuredClone(state));
  }
  const pageKey=()=>JSON.stringify([state.view,state.scope,state.tag,state.offset,mode()]);
  function cachePage(key,value){
    cache.delete(key);cache.set(key,value);
    const current=pageKey();
    while(cache.size>128){const old=[...cache.keys()].find(identity=>identity!==current);cache.delete(old);}
  }
  async function read(view,params={}, {signal,accept=value=>value}={}){
    let current=params;
    for(let attempt=0;attempt<2;attempt++){
      const version=relationVersion,scan=completedScans.get(view);
      const value=await reads.read(`/api/account/${view}?${new URLSearchParams(current)}`,{signal});
      signal?.throwIfAborted();
      const latest=completedScans.get(view);
      if(retiredByScan(scan,latest,params)){
        throw Object.assign(new Error("账号分页已更新，请返回第一页"),{errorKind:"page_retired"});
      }
      if(version===relationVersion && scan===latest)return accept(value);
      // The response predates a confirmed relationship change. Read the
      // repaired server cache; only this overlapping read needs replacement.
      current={...params};delete current.refresh;
    }
    throw new Error("账号列表仍在更新，请稍后重试");
  }
  const pendingImages=new Map();
  let warming=null;
  function queueImages(key,decode,signal){
    pendingImages.set(key,decode);
    if(warming)return;
    warming=(async()=>{
      try{
        while(!signal.aborted && pendingImages.size){
          const [key,prepare]=pendingImages.entries().next().value;
          pendingImages.delete(key);
          try{await waitForMediaDecode(prepare,signal);}
          catch(problem){if(!signal.aborted)report(`账号缩略图暂未准备好：${problem.message}`);}
        }
      }finally{if(!signal.aborted)warming=null;}
    })();
  }
  function stopBackground(){
    const pendingUpdates=checkingUpdates?.task;
    stopUpdates();
    backgroundRevision++;backgroundController.abort();backgroundController=new AbortController();nextReads.clear();
    // Callers are stopping work; its original caller owns any reported failure.
    const images=warming;warming=null;pendingImages.clear();
    return Promise.allSettled([backgroundQueue,pendingUpdates,images]);
  }
  function prepare({network=false,refresh=false,page=null,completed=null}={}){
    const version=backgroundRevision,signal=backgroundController.signal,contentMode=mode();
    const run=async()=>{
      if(version!==backgroundRevision || signal.aborted || !signedIn())return;
      const currentSession=()=>version===backgroundRevision && !signal.aborted && signedIn() && contentMode===mode();
      const valid=()=>currentSession() && profile?.connected;
      const params={background:"1",...(!network?{cached:"1"}:{}),...(refresh?{refresh:"1"}:{})};
      if(!page || !profile){
        const before=profile;
        const value=await read("profile",params,{signal,accept(result){
          if(!currentSession() || result.cached===false || !result.connected)return null;
          if(profile===before){profile=result;connectionChanged(profile);}
          return result;
        }});
        if(!value)return;
      }
      if(!valid())return;
      let retired=null;
      if(completed){
        const view=page.kind==="bookmark"?"bookmarks":"following";
        completedScans.set(view,completed);
        const obsolete=(scope,tag,offset)=>obsoletePage(completed,scope,tag,offset);
        for(const pages of [cache,nextReads]){
          for(const key of pages.keys()){
            const [type,scope,tag,offset]=JSON.parse(key);
            if(type===view && obsolete(scope,tag,offset))pages.delete(key);
          }
        }
        const saved=positions.get(view);
        if(saved && obsolete(saved.scope,saved.tag,saved.offset))positions.delete(view);
        if(state.view===view && obsolete(state.scope,state.tag,state.offset))retired={revision,data};
      }
      const images=[];
      const failures=[];
      if(!page && !network && updatePreferences.detect)await checkUpdates({network:false});
      if(!valid())return;
      if(!page || refresh){
        const entries=[{id:`self:${profile.uid}`,thumb:profile.avatar,title:profile.name},{id:`cover:${profile.uid}`,thumb:profile.background,title:""}];
        images.push(["account-self",signal=>avatars.prepare("account-self",entries,signal,{warm:"account"})]);
      }
      const pages=page?[{view:page.kind==="bookmark"?"bookmarks":"following",scope:page.scope,offset:page.offset}]:[
        {view:"bookmarks",scope:"public",offset:0},{view:"bookmarks",scope:"private",offset:0},
        {view:"following",scope:"public",offset:0},{view:"following",scope:"private",offset:0},
        ...(profile.method==="app"?[{view:"followers",scope:"public",offset:0}]:[])];
      for(const target of pages){
        if(!valid())return;
        const relation=["bookmarks","following"].includes(target.view);
        const key=JSON.stringify([target.view,target.scope,"",target.offset,contentMode]),before=cache.get(key);
        let value;
        try{value=await read(target.view,{scope:target.scope,offset:String(target.offset),tag:"",mode:contentMode,
          background:"1",...(relation || !network?{cached:"1"}:{}),...(!relation && refresh?{refresh:"1"}:{})},{signal,accept(result){
            if(!valid() || result.cached===false || cache.get(key)!==before)return null;
            cachePage(key,result);
            return result;
          }});}
        catch(problem){if(signal.aborted)throw problem;failures.push(`${labels[target.view]}：${problem.message}`);continue;}
        if(!valid())return;
        if(value && target.offset===0){
          if(value.items)images.push(["account-works",signal=>worksCache.prepare("account-works",value.items,signal,{warm:"account"})]);
          if(value.people)images.push(["account-people",signal=>avatars.prepare("account-people",value.people.map(person=>({id:`avatar:${person.id}`,thumb:person.avatar,title:person.name})),signal,{warm:"account"})]);
        }
      }
      if(!page){
        if(!valid())return;
        const catalog=profile;
        for(const view of ["tags",...(catalog.workCount===0?[]:["works"])]){
          const before=view==="tags"?tags:own;
          let value;
          try{value=await read(view,{...params,mode:contentMode},{signal,accept(result){
            if(!valid() || result.cached===false)return null;
            if(view==="tags"){
              if(tags===before)tags=result;
            }else if(own===before){own=result;ownMode=contentMode;return result;}
            return null;
          }});}
          catch(problem){if(signal.aborted)throw problem;failures.push(`${view==="tags"?"收藏标签":"本人作品"}：${problem.message}`);continue;}
          if(!valid())return;
          if(value)images.push(["account-own-works",signal=>ownCache.prepare("account-own-works",value.items,signal,{warm:"account"})]);
        }
        if(profile===catalog && catalog.workCount===0){own={items:[]};ownMode=contentMode;}
      }
      // Adopt a synchronized visible page while preserving its scroll position.
      if(valid() && active && !controller){
        if(retired && revision===retired.revision && data===retired.data && !cache.has(pageKey())){
          await returnToFirstPage();
        }else if(state.view==="home")render();
        else if(state.view!=="settings" && (page || refresh || !data)){
          const available=cache.get(pageKey());if(available && available!==data){data=available;render();}
        }
      }
      if(valid() && !document.hidden && (network || page))for(const [key,decode] of images)
        queueImages(key,media=>valid()?decode(media):undefined,signal);
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
    const visit=revision;
    const key=JSON.stringify([target.view,target.scope,target.tag,target.offset,contentMode]);
    if(cache.has(key) || nextReads.has(key))return;
    const reading=read(target.view,{scope:target.scope,offset:String(target.offset),tag:target.tag,mode:contentMode,background:"1"},{signal,async accept(value){
      if(version!==backgroundRevision || signal.aborted || contentMode!==mode() || nextReads.get(key)!==reading || cache.has(key))return;
      cachePage(key,value);
      if(value.items)queueImages("account-works",media=>worksCache.prepare("account-works",value.items,media,{warm:"account"}),signal);
      if(value.people)queueImages("account-people",media=>avatars.prepare("account-people",value.people.map(person=>({id:`avatar:${person.id}`,thumb:person.avatar,title:person.name})),media,{warm:"account"}),signal);
    }}).catch(problem=>{
      if(!signal.aborted && version===backgroundRevision && nextReads.get(key)===reading &&
          active && visit===revision && !document.hidden && !["page_retired","page_rebased"].includes(problem.errorKind))
        report(`下一页暂未准备好：${problem.message}`);
    }).finally(()=>{if(nextReads.get(key)===reading)nextReads.delete(key);});
    nextReads.set(key,reading);
  }
  const artworkContext=item=>({context:{kind:"pid",value:item.id},resultPage:state.page});
  function syncPeopleStatus(){
    const enabled=updatePreferences.detect && updatePreferences.dots;
    const dot=tools.querySelector('[data-account-updates] .update-dot');
    if(dot)dot.hidden=!enabled || !Object.values(updates[state.scope]).some(row=>row.isNew);
    peopleRoot.querySelectorAll('[data-person]').forEach(card=>{
      const person=data?.people?.[Number(card.dataset.person)];if(!person)return;
      const observed=updates[state.scope][person.id];
      const marker=observed && observed.latestObserved>=(person.latestObserved || '') ? observed : person;
      const sameObservation=observed && observed.latestObserved===person.latestObserved;
      const unread=marker.isNew && (!sameObservation || person.isNew) && marker.latestObserved>(acknowledged.get(person.id) || '');
      card.querySelector('.update-dot').hidden=!enabled || !unread;
      const date=card.querySelector('.account-person-update');
      date.hidden=!marker.latestObserved;
      date.textContent=marker.latestObserved?`最新观察 · ${marker.latestObserved.slice(0,10)}`:'';
      card.querySelector('[data-person-following]').hidden=state.view!=="followers" || !person.following;
    });
  }
  function stopUpdates(){
    updateController.abort();
    updateController=new AbortController();
    checkingUpdates=null;
  }
  function checkUpdates({network=true,continuing=false}={}){
    if(!updatePreferences.detect || !signedIn())return Promise.resolve({hasMore:false});
    const signal=updateController.signal,auth=generation();
    const valid=()=>!signal.aborted && updatePreferences.detect && signedIn() && generation()===auth;
    if(checkingUpdates){
      const pending=checkingUpdates;
      if(!network || pending.network && (continuing || !pending.continuing))return pending.task;
      return pending.task.then(()=>valid()?checkUpdates({network,continuing}):{hasMore:false});
    }
    let task;task=(async()=>{
      let combined=profile?.method==="web";
      const failures=[];
      for(const scope of ['public','private']){
        if(!valid())return {hasMore:false};
        const usesCursor=scope==='public' || !combined;
        const refresh=network && usesCursor && (!continuing || pendingUpdateScopes.has(scope));
        let result;
        try{
          result=await read('updates',{scope,background:'1',...(refresh?{refresh:'1'}:{cached:'1'})},{signal,accept(value){
            if(!valid() || value.cached===false || value.connected===false)return value;
            combined ||= Boolean(value.combined);
            if(usesCursor){if(value.hasMore)pendingUpdateScopes.add(scope);else pendingUpdateScopes.delete(scope);}
            for(const [id,marker] of Object.entries(value.authors)){
              if(updates[scope][id]?.latestObserved>marker.latestObserved)continue;
              updates[scope][id]={...marker,isNew:marker.isNew && marker.latestObserved>(acknowledged.get(id) || '')};
            }
            for(const id of Object.keys(updates[scope]))if(!value.authors[id])delete updates[scope][id];
            syncPeopleStatus();
            return value;
          }});
        }catch(problem){
          if(!valid())return {hasMore:false};
          if(refresh)pendingUpdateScopes.add(scope);
          failures.push(`${scope==='public'?'公开':'私密'}范围：${problem.message}`);
          continue;
        }
        if(!valid())return {hasMore:false};
        if(result.connected===false)return {hasMore:false};
      }
      if(failures.length)report(`新作检查未完成：${failures.join('；')}`);
      return {hasMore:pendingUpdateScopes.size>0};
    })().finally(()=>{if(checkingUpdates?.task===task)checkingUpdates=null;});
    checkingUpdates={task,network,continuing};return task;
  }
  async function acknowledge(authors,proofs){
    const version=backgroundRevision,auth=generation();
    const entries=Object.entries(authors);
    for(let offset=0;offset<entries.length;offset+=250){
      const part=Object.fromEntries(entries.slice(offset,offset+250));
      if(version!==backgroundRevision || auth!==generation())return;
      const saved=await request('/api/account/seen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({authors:part,proofs:Object.fromEntries(Object.keys(part).map(id=>[id,proofs[id]])),authorizationGeneration:auth})});
      if(version!==backgroundRevision || auth!==generation())return;
      const accepted=new Map(Object.entries(saved.accepted || {}));
      if(!accepted.size)continue;
      for(const [id,stamp] of accepted){
        if(stamp>(acknowledged.get(id) || ''))acknowledged.set(id,stamp);
        for(const scope of ['public','private'])if(updates[scope][id]?.latestObserved<=stamp)updates[scope][id].isNew=false;
      }
      for(const value of cache.values())for(const person of value.people || []){
        const stamp=accepted.get(person.id);
        if(stamp!==undefined && person.latestObserved<=stamp)person.isNew=false;
      }
      syncPeopleStatus();
    }
  }
  function seenWorks(items){
    if(!updatePreferences.detect)return Promise.resolve();
    const dates={},proofs={};
    for(const item of items){
      if(!item.sortDate)continue;
      const stamp=new Date(item.sortDate).toISOString().slice(0,19)+'+00:00';
      if(stamp>(acknowledged.get(item.userId) || '') && stamp>(dates[item.userId] || '')){
        dates[item.userId]=stamp;proofs[item.userId]=item.viewProof;
      }
    }
    return acknowledge(dates,proofs);
  }
  function artist(person){
    if(!allowed())return;
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
      `<div class="account-connect"><span class="account-avatar account-avatar-empty" aria-hidden="true">人</span><h2>${pending?"正在读取账号":"我的 Pixiv 账号"}</h2></div>`;
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
      try{await openUpdates(state.scope);}catch(problem){report(`新作暂未打开：${problem.message}`);}
    });

    peopleRoot.hidden=!p?.connected || !["following","followers"].includes(state.view);
    grid.hidden=!p?.connected || state.view!=="bookmarks";
    globalThis.mediaBudget?.schedule();
    if(!peopleRoot.hidden && (renderedPeople!==data?.people || peopleKey!==pageKey())){
    renderedPeople=data?.people;peopleKey=pageKey();avatars.park(peopleRoot);
    peopleRoot.innerHTML=(data?.people || []).map((person,index)=>`<article class="account-person" data-person="${index}"><a class="account-person-avatar" href="#artist/${esc(person.id)}" aria-label="${esc(person.name)}"><span class="update-dot" hidden aria-hidden="true"></span>${person.avatar?`<img data-gallery-src="${esc(person.avatar)}" data-preview-artwork="avatar:${esc(person.id)}" alt="" loading="lazy" decoding="async">`:'<span aria-hidden="true">人</span>'}</a><div class="account-person-info"><a class="account-person-name" href="#artist/${esc(person.id)}">${esc(person.name)}</a><span class="account-person-uid">UID ${esc(person.id)}<span data-person-following ${state.view==="followers"&&person.following?"":"hidden"}> · 互相关注</span></span><p class="account-person-bio">${person.bioLoaded?esc(person.bio || "暂未填写简介"):'<button type="button" data-person-bio>查看简介</button>'}</p><span class="account-person-update" title="已读取范围内的最新作品" hidden></span></div></article>`).join("");
    if(!peopleRoot.hidden && !pending && !error && !(data?.people?.length))peopleRoot.innerHTML=`<p class="account-empty">${state.view==="followers"?"目前还没有粉丝。":"这个范围内还没有关注的作者。"}</p>`;
    avatars.mount(peopleRoot,"account-people");installImages(peopleRoot);
    peopleRoot.querySelectorAll("[data-person]").forEach(row=>{
      const person=data.people[Number(row.dataset.person)];
      row.querySelectorAll("a").forEach(link=>link.onclick=event=>{event.preventDefault();if(!mokuHasSelectedText(link))artist(person);});
      const button=row.querySelector("[data-person-bio]");
      if(!button)return;
      if(bioReads.has(person.id)){button.disabled=true;button.textContent="读取中…";}
      button.addEventListener("click",async ()=>{
        const id=person.id;
        if(bioReads.has(id))return;
        const reading=new AbortController(),auth=generation();
        bioReads.set(id,reading);button.disabled=true;button.textContent="读取中…";
        let value;
        try{value=await request(`/api/pixiv/artist/${id}`,{signal:reading.signal});}
        catch(error){if(!reading.signal.aborted)console.warn("Account biography could not be read",error);}
        if(bioReads.get(id)!==reading)return;
        bioReads.delete(id);
        if(auth!==generation())return;
        const bio=value?{bio:value.description || value.bio || "",bioLoaded:true}:null;
        if(bio){
          Object.assign(person,bio);
          for(const page of cache.values())for(const entry of page.people || [])if(entry.id===id)Object.assign(entry,bio);
        }
        for(const current of peopleRoot.querySelectorAll("[data-person]")){
          const entry=renderedPeople[Number(current.dataset.person)];
          if(entry.id!==id)continue;
          if(bio){
            Object.assign(entry,bio);
            current.querySelector(".account-person-bio").textContent=bio.bio || "暂未填写简介";
          }else{
            const retry=current.querySelector("[data-person-bio]");
            if(retry){retry.disabled=false;retry.textContent="读取失败，重试";}
          }
        }
      });
    });
    }
    if(state.view==="bookmarks" && p?.connected && (renderedGrid!==data?.items || gridKey!==pageKey())){
      renderedGrid=data?.items;gridKey=pageKey();interaction.reset({immediate:true});worksCache.park(grid);
      grid.innerHTML=renderArtworkCards(data?.items || [],{escape:esc,selected});
      if(!pending && !error && !data?.items?.length){
        const empty = data?.unavailable ? "没有符合条件的收藏" : "暂无收藏";
        grid.innerHTML=`<p class="account-empty">${empty}</p>`;
      }
      worksCache.mount(grid,"account-works");installImages(grid);attachUgoira(grid);interaction.mount(data?.items || []);
      grid.querySelectorAll("[data-select]").forEach(box=>box.onchange=()=>{const item=data.items[Number(box.dataset.select)];if(!choose(item,box.checked,artworkContext(item)))box.checked=selected(item.id);});
    }
    pagination.hidden=["home","settings"].includes(state.view) || !p?.connected;
    pagination.innerHTML=`<button type="button" data-account-prev ${!state.trail.length || pending?"disabled":""}>← 上一页</button><span>第 ${state.page} 页</span><button type="button" data-account-next ${data?.nextOffset==null || pending?"disabled":""}>下一页 →</button>`;
    pagination.querySelector("[data-account-prev]")?.addEventListener("click",()=>{const trail=state.trail.slice(),offset=trail.pop();navigate(state.view,{offset,page:state.page-1,trail});});
    pagination.querySelector("[data-account-next]")?.addEventListener("click",()=>navigate(state.view,{offset:data.nextOffset,page:state.page+1,trail:[...state.trail,state.offset]}));
    },active?restoreTop:null);
    if(active){
      if(!pending)restoreTop=null;
      state.scroll=root.scrollTop;
      restoringScroll=true;
      cancelAnimationFrame(scrollFrame);scrollFrame=requestAnimationFrame(()=>{restoringScroll=false;scrollFrame=null;});
    }
    syncSelection();syncPeopleStatus();changed();
  }

  async function load(refresh=false,refreshPage=refresh){
    controller?.abort();controller=new AbortController();const signal=controller.signal,version=++revision,key=pageKey();
    const scan=completedScans.get(state.view);
    const pageRetired=()=>retiredByScan(scan,completedScans.get(state.view),state);
    error="";data=cache.get(key) || null;
    if(data)cachePage(key,data);
    if(!refreshPage && profile?.connected && (state.view==="home"?own && ownMode===mode():state.view!=="settings" && data && (state.view!=="bookmarks" || tags))){
      controller=null;pending=false;render();prepareNext();return;
    }
    pending=!data;
    if(ownMode!==mode())own=null;
    render();
    try{
      if(state.view==="settings"){await settingsReady();return;}
      if(!signedIn()){profile=null;data=null;connectionChanged(null);return;}
      if(!profile || refresh){
        await read("profile",refresh?{refresh:"1"}:{},{signal,accept(value){
          if(version!==revision)return;
          profile=value;connectionChanged(profile);
        }});
      }
      if(version!==revision)return;
      if(!profile.connected){data=null;return;}
      if(pageRetired())return returnToFirstPage();
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
      if(state.view!=="home" && (!data || refreshPage)){
        const query=new URLSearchParams({scope:state.scope,offset:String(state.offset),tag:state.tag,mode:mode(),...(refreshPage?{refresh:"1"}:{})});
        await read(state.view,Object.fromEntries(query),{signal,accept(value){
          if(version!==revision)return;
          data=value;cachePage(key,value);pending=false;render();
        }});
        if(version!==revision)return;
      }
      if(state.view==="bookmarks" && (!tags || refresh)){
        await read("tags",refresh?{refresh:"1"}:{},{signal,accept(value){if(version===revision)tags=value;}});
      }
      if(version!==revision)return;
      if(pageRetired())return returnToFirstPage();
      if(profile.authorization?.error)error=profile.authorization.error;
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      if(state.offset>0 && problem.errorKind==="page_rebased"){
        for(const pages of [cache,nextReads])for(const key of pages.keys()){
          const [view,scope,tag,,contentMode]=JSON.parse(key);
          if(view===state.view && scope===state.scope && tag===state.tag && contentMode===mode())pages.delete(key);
        }
        return returnToFirstPage(true);
      }
      if(state.offset>0 && problem.errorKind==="page_retired")return returnToFirstPage();
      error=problem.message || "账号读取失败，请刷新重试";
    }
    finally{if(version===revision){controller=null;pending=false;render();prepareNext();}}
  }
  function returnToFirstPage(refresh=false){
    state={...state,offset:0,page:1,trail:[],scroll:0};restoreTop=0;
    return load(false,refresh);
  }
  async function navigate(view,options={}){
    if(!allowed())return;
    remember();
    const same=view===state.view;
    const saved=!same && !Object.keys(options).length?positions.get(view):null;
    state=saved?{...structuredClone(saved),scroll:0}:{view,scope:options.scope || (same?state.scope:scopes[view] || "public"),tag:options.tag ?? (same?state.tag:""),
      offset:options.offset ?? 0,page:options.page ?? 1,trail:options.trail || [],scroll:0};
    restoreTop=state.scroll;
    if(scopes[view]!==undefined)scopes[view]=state.scope;
    await load();
  }
  function clear(keepView=false,keepScroll=false){
    if(keepScroll)remember();
    void stopBackground();
    for(const reading of bioReads.values())reading.abort();
    bioReads.clear();
    controller?.abort();controller=null;reads.clear();revision++;profile=null;tags=null;data=null;own=null;ownMode=null;
    state=keepView?{...state,offset:0,page:1,trail:[],scroll:keepScroll?state.scroll:0}:empty();restoreTop=state.scroll;positions.clear();
    updates={public:{},private:{}};acknowledged.clear();pendingUpdateScopes.clear();completedScans.clear();
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
    configure(value,loaded){
      if(!loaded)return;
      if(updatePreferences.detect && !value.autoFollowing)stopUpdates();
      updatePreferences={detect:value.autoFollowing,dots:value.updateDots};syncPeopleStatus();
    },
    captureCounts:()=>new Set([profile,tags,data,...cache.values()]),
    cloudChanged:(kind,id,value,previous,beforeCounts)=>{
      relationVersion++;
      const canAdjust=value=>beforeCounts===undefined || beforeCounts?.has(value);
      let rereadPage=false,rereadProfile=false,rereadTags=false;
      if(kind==='follow')for(const scope of ['public','private'])if(!value.active || value.privacy && value.privacy!==scope)delete updates[scope][id];
      const view=kind==="bookmark"?"bookmarks":"following",field=kind==="bookmark"?"items":"people";
      const web=profile?.method==="web";
      if(web)for(const key of nextReads.keys())if(JSON.parse(key)[0]===view)nextReads.delete(key);
      let existing;
      for(const page of cache.values()){
        existing=page[field]?.find(row=>row.id===id);
        if(existing)break;
      }
      const record=existing || (kind==="bookmark"?getWork(id):getAuthor(id));
      if(kind==="follow")for(const [key,page] of cache)if(JSON.parse(key)[0]==="followers"){
        for(const person of page.people || [])if(person.id===id)person.following=value.active;
      }
      const visibleKey=pageKey(),pages=new Map(cache);
      if(data && !pages.has(visibleKey))pages.set(visibleKey,data);
      for(const [key,page] of pages){
        const [type,scope,tag,offset,contentMode]=JSON.parse(key);
        if(type!==view || !page[field])continue;
        const rows=page[field],present=rows.some(row=>row.id===id);
        const membership=(relation,visible=true)=>{
          if(!relation?.known)return null;
          if(!relation.active || relation.privacy && relation.privacy!==scope)return false;
          if(!relation.privacy && !present)return null;
          if(kind==="bookmark"){
            if(tag){if(relation.tags===null)return null;if(!relation.tags.includes(tag))return false;}
            if(visible && contentMode!=="all"){
              if(!record)return null;
              if(record.restriction!==contentMode)return false;
            }
          }
          return true;
        };
        const previousMember=membership(previous),nextMember=membership(value);
        const fits=nextMember===true || nextMember===null && present;
        if(present && !fits)page[field]=rows.filter(row=>row.id!==id);
        else if(!present && fits && !offset && record)page[field]=[{...record,...(kind==="follow"?{following:true}:{})},...rows];
        const delta=previousMember!==null && nextMember!==null?Number(nextMember)-Number(previousMember):page[field].length-rows.length;
        if(delta && Number.isInteger(page.total)){
          if(canAdjust(page))page.total=Math.max(0,page.total+delta);
          else if(kind==="follow" && !offset && page.nextOffset==null && page.total===rows.length && (!fits || present || record)){
            page.total=page[field].length;
          }else{
            page.total=null;cache.delete(key);
            rereadPage ||= page===data;
          }
        }
        if(web && (delta || page[field]!==rows || membership(previous,false)!==membership(value,false)))cache.delete(key);
      }
      if(profile && previous?.known && (kind==="follow" || !value.active || value.privacy)){
        const delta=kind==="follow"?Number(value.active)-Number(previous.active):
          Number(value.active && value.privacy==="public")-Number(previous.active && previous.privacy==="public");
        const count=kind==="follow"?"following":"publicBookmarks";
        if(delta && Number.isInteger(profile[count])){
          if(canAdjust(profile))profile[count]=Math.max(0,profile[count]+delta);
          else {profile={...profile,[count]:null};rereadProfile=true;}
        }
        if(delta)renderedHeader=undefined;
      }
      if(kind==="bookmark" && tags && previous?.known &&
          (!previous.active || previous.privacy && previous.tags!==null) && (!value.active || value.privacy && value.tags!==null)){
        for(const scope of ["public","private"]){
          const oldTags=previous.active && previous.privacy===scope?previous.tags || []:[];
          const newTags=value.active && value.privacy===scope?value.tags || []:[];
          if(!canAdjust(tags) && (oldTags.some(name=>!newTags.includes(name)) || newTags.some(name=>!oldTags.includes(name)))){
            tags=null;rereadTags=true;break;
          }
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
      if(active && rereadPage && !controller)void load();
      const session=generation(),background=backgroundRevision;
      const reread=(view,adopt)=>void read(view,{},{accept(result){
        if(generation()!==session || backgroundRevision!==background)return;
        adopt(result);if(active)render();
      }}).catch(problem=>{
        if(generation()===session && backgroundRevision===background)report(`账号计数暂未核对：${problem.message}`);
      });
      if(rereadProfile){
        const placeholder=profile;
        reread("profile",result=>{if(profile===placeholder){profile=result;connectionChanged(result);}});
      }
      if(rereadTags)reread("tags",result=>{if(!tags)tags=result;});
    },
    invalidateConnection:()=>{profile=null;},
    suspend:()=>{remember();active=false;statistics?.setVisible(false);controller?.abort();controller=null;revision++;pending=false;interaction.reset({immediate:true});ownInteraction.reset({immediate:true});},
    clear,clearCache:()=>clear(true),clearContent:()=>clear(true,true),
    label:()=>"我的账号"};
};
