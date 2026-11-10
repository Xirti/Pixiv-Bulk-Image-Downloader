"use strict";

// One relationship owner for every card/detail. It never touches download selection or images.
globalThis.createCloudControls = function ({request,session,login,report,changed,preferencesChanged,official,cachedTags=()=>null,cleared=()=>{},prepareAccount=()=>Promise.resolve(),clearStarted=()=>Promise.resolve(),checkUpdates=()=>Promise.resolve({})}) {
  const defaults={bookmarkDefault:"ask",followDefault:"ask",bookmarkLast:"private",followLast:"private",autoSync:true,syncMinutes:"10",autoRanking:true,autoFollowing:true,autoRecommended:true,saveRoot:"",createFolder:true,mediaMemory:"normal",previewLayout:"adaptive",previewAction:"expand",previewSize:"large",sensitiveMask:"both",autoLoadPages:false,orderTags:"default",orderAuthor:"default",orderUid:"default",orderPid:"default",orderRecommended:"default",orderRanking:"default",showLoadButton:true,updateDots:true};
  let prefs={...defaults},states={bookmark:new Map(),follow:new Map()},syncState={},owner=null;
  let epoch=0,sessionKey="",settingsPromise=null,prefsLoaded=false,hydration=null,hydrationAgain=false,hydratedKey="",timer=null,syncError="";
  let popup=null,anchor=null,popupVersion=0,readVersion=0,syncBusy=false,lastSyncAttempt=0,syncTurn=0;
  let autoNotBefore=0,syncRequest=null;
  let clearing=false,accountPrepared=false,auxiliaryBusy=false,auxiliaryCompleted=0,auxiliaryError="";
  let preferenceWrites=Promise.resolve();
  let updatesBusy=false,lastUpdateCheck=0,updatesRemaining=false;
  const busy=new Set(),manual=new Set(),restart=new Set();
  const painted=new WeakMap();
  const post=(path,data,timeout=90000)=>request(path,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...data,authorizationGeneration:session().generation})},timeout);
  const key=(kind,id)=>`${kind}:${id}`;
  const get=(kind,id)=>states[kind].get(id) || {known:false,active:null,privacy:null,tags:null};
  const sameEpoch=(revision,generation)=>revision===epoch && generation===session().generation;

  function paint(){
    const logged=session().logged;
    document.querySelectorAll("[data-cloud-kind][data-cloud-id]").forEach(button=>{
      const kind=button.dataset.cloudKind,id=button.dataset.cloudId,state=get(kind,id),pending=busy.has(key(kind,id));
      const self=kind==="follow" && id===owner;
      const signature=JSON.stringify([kind,id,self,pending,logged,state.known,state.active,state.privacy]);
      if(painted.get(button)===signature)return;
      button.hidden=self;
      button.disabled=pending;
      button.classList.toggle("is-active",state.active===true);
      button.classList.toggle("is-unknown",logged && !state.known);
      button.classList.toggle("is-pending",pending);
      button.setAttribute("aria-pressed",!state.known && logged?"mixed":state.active===true?"true":"false");
      button.setAttribute("aria-busy",String(pending));
      const name=kind==="bookmark"?"收藏":"关注",privacy=state.privacy==="private"?"私密":state.privacy==="public"?"公开":"";
      button.title=pending?"正在提交云端…":state.known?`${state.active?`已${privacy}${name}，点击管理`:`${name}（云端）`}`:`管理${name}（提交时核对）`;
      button.setAttribute("aria-label",button.title);
      const text=button.querySelector("[data-cloud-label]");
      if(text){const value=pending?"处理中…":state.active?`已${name}`:name;if(text.textContent!==value)text.textContent=value;}
      const mark=button.querySelector("[data-follow-mark]");
      if(mark && mark.textContent!==(state.active?"√":"＋"))mark.textContent=state.active?"√":"＋";
      painted.set(button,signature);
    });
  }
  function apply(kind,id,state){states[kind].set(id,state);paint();}
  function close(){popupVersion++;if(popup){popup.remove();popup=null;}anchor=null;}
  function position(){
    if(!popup || !anchor)return;
    if(!anchor.isConnected || !anchor.getClientRects().length){close();return;}
    const rect=anchor.getBoundingClientRect(),box=popup.getBoundingClientRect();
    popup.style.left=`${Math.max(8,Math.min(innerWidth-box.width-8,rect.right-box.width))}px`;
    popup.style.top=`${Math.max(8,Math.min(innerHeight-box.height-8,rect.bottom+8))}px`;
  }
  function notifySettings(){preferencesChanged({...prefs},{...structuredClone(syncState),error:syncError || auxiliaryError,manual:manual.size>0,preparing:auxiliaryBusy,prepared:auxiliaryCompleted,clearing,requesting:syncBusy},prefsLoaded);}
  async function prepareAccountData(options){
    const revision=epoch,generation=session().generation;
    if(auxiliaryBusy || clearing || !session().logged)return;
    auxiliaryBusy=true;notifySettings();
    try{
      await prepareAccount(options);
      if(!sameEpoch(revision,generation))return;
      accountPrepared=true;auxiliaryCompleted=Date.now()/1000;auxiliaryError="";
    }catch(error){if(sameEpoch(revision,generation)){accountPrepared=true;auxiliaryError=`账号页面准备未完成：${error.message}`;auxiliaryCompleted=Date.now()/1000;}}
    finally{if(sameEpoch(revision,generation)){auxiliaryBusy=false;notifySettings();}}
  }

  async function loadPreferences(){
    const revision=epoch;
    const data=await request("/api/settings",{},20000);
    if(revision!==epoch || data.authorizationGeneration!==session().generation)return;
    prefs=data.settings;prefsLoaded=true;notifySettings();
  }
  function ready(){
    if(prefsLoaded)return Promise.resolve();
    if(!settingsPromise){let pending;pending=loadPreferences().finally(()=>{if(settingsPromise===pending)settingsPromise=null;});settingsPromise=pending;}
    return settingsPromise;
  }
  function savePreferences(patch){
    const revision=epoch,generation=session().generation;
    const pending=preferenceWrites.catch(()=>{}).then(async()=>{
      if(!sameEpoch(revision,generation))throw new Error("账号已改变，请重新打开设置");
      const data=await post("/api/settings",{settings:patch},20000);
      if(!sameEpoch(revision,generation))throw new Error("账号已改变，请重新打开设置");
      const wasAutomatic=prefs.autoSync;
      prefs=data.settings;notifySettings();lastSyncAttempt=0;
      if(wasAutomatic && !prefs.autoSync)await clearStarted();
      if(!wasAutomatic && prefs.autoSync){accountPrepared=false;auxiliaryCompleted=0;void tick();}
      return {...prefs};
    });
    preferenceWrites=pending;return pending;
  }
  function visibleTargets(){
    const result={bookmark:new Set(),follow:new Set()};
    document.querySelectorAll("[data-cloud-kind][data-cloud-id]").forEach(button=>{
      const box=button.getBoundingClientRect();
      if(box.width===0 || box.height===0 || box.bottom < -500 || box.top > innerHeight+500)return;
      result[button.dataset.cloudKind].add(button.dataset.cloudId);
    });
    return {works:[...result.bookmark].slice(0,100),authors:[...result.follow].slice(0,100)};
  }
  async function hydrate(force=false){
    paint();
    if(!session().logged)return;
    if(hydration){hydrationAgain ||= force;return hydration;}
    const {works,authors}=visibleTargets();
    const queryKey=JSON.stringify([works,authors]);
    if(!force && queryKey===hydratedKey)return;
    const revision=epoch,generation=session().generation;
    hydration=(async()=>{
      try{
        const data=await request(`/api/cloud/state?${new URLSearchParams({works:works.join(","),authors:authors.join(",")})}`,{},30000);
        if(!sameEpoch(revision,generation) || data.authorizationGeneration!==generation)return;
        hydratedKey=queryKey;owner=data.owner;syncState=data.sync;
        for(const kind of ["bookmark","follow"])for(const [id,state] of Object.entries(data.states[kind])){
          const old=get(kind,id);
          if(!old.touched || state.touched>=old.touched)states[kind].set(id,state);
        }
        paint();notifySettings();
      }catch(error){
        if(revision===epoch){hydratedKey="";syncError=error.message;lastSyncAttempt=Date.now();notifySettings();}
      }
    })().finally(()=>{hydration=null;if(hydrationAgain){hydrationAgain=false;void hydrate(true);}});
    return hydration;
  }
  function schedule(){clearTimeout(timer);timer=setTimeout(()=>{void hydrate();position();},100);}

  async function read(kind,id){
    const revision=epoch,generation=session().generation;
    const data=await post("/api/cloud/read",{kind,id});
    if(!sameEpoch(revision,generation))throw new Error("账号已改变，请重新操作");
    apply(kind,id,data.state);return data.state;
  }
  async function mutate(kind,id,state,active,privacy,tags,version){
    const revision=epoch,generation=session().generation,targetKey=key(kind,id);
    busy.add(targetKey);paint();
    try{
      const data=await post("/api/cloud/write",{kind,id,active,privacy,tags,expected:state.stamp || null,
        requestId:crypto.randomUUID().replaceAll("-","")},120000);
      if(!sameEpoch(revision,generation))return;
      apply(kind,id,data.state);
      if(!data.confirmed)throw new Error("结果仍待确认，请刷新核对");
      if(active)prefs[kind+"Last"]=privacy;
      changed(kind,id,data.state,state);notifySettings();
      if(version===popupVersion)close();
      report(active?`${kind==="bookmark"?"收藏":"关注"}已保存`:`已取消${kind==="bookmark"?"收藏":"关注"}`);
    }catch(error){
      if(!sameEpoch(revision,generation))return;
      if(error.cloudState){
        apply(kind,id,error.cloudState);changed(kind,id,error.cloudState,state);
        if(error.errorKind==="conflict" && popup && version===popupVersion){
          const button=anchor,privacyChoice=popup.querySelector('input[name="cloudPrivacy"]:checked')?.value;
          const tagsInput=popup.querySelector('[data-cloud-tags]'),editedTags=tagsInput?.dataset.edited==="true"?tagsInput.value:null;
          editor(button,kind,id,error.cloudState);version=popupVersion;
          if(privacyChoice)popup.querySelector(`input[value="${privacyChoice}"]`).checked=true;
          if(editedTags!==null){const input=popup.querySelector('[data-cloud-tags]');input.value=editedTags;input.dataset.edited="true";}
        }
      }
      if(error.official && kind==="follow"){
        if(!popup || version!==popupVersion){
          const target=[...document.querySelectorAll('[data-cloud-kind="follow"][data-cloud-id]')].find(button=>button.dataset.cloudId===id && button.getClientRects().length);
          if(target){editor(target,kind,id,error.cloudState || state);version=popupVersion;}
        }
        showOfficial(kind,id,version,error.message);
      }else if(popup && version===popupVersion){
        popup.querySelector("[data-cloud-error]").textContent=error.message;
      }else report(error.message);
    }finally{
      if(revision===epoch){busy.delete(targetKey);paint();
        if(popup && version===popupVersion)popup.querySelectorAll("button,input").forEach(control=>control.disabled=false);}
    }
  }
  function shell(button,title){
    close();anchor=button;
    popup=document.createElement("div");popup.className="cloud-popover";popup.setAttribute("role","dialog");
    popup.setAttribute("aria-label",title);popup.tabIndex=-1;
    const parent=button.closest("dialog[open]") || document.body;parent.append(popup);
    return popupVersion;
  }
  function showOfficial(kind,id,version,message){
    if(!popup || version!==popupVersion){report(message);return;}
    popup.querySelector("[data-cloud-error]").textContent=message;
    if(popup.querySelector("[data-cloud-official]"))return;
    const button=document.createElement("button");button.type="button";button.dataset.cloudOfficial="";button.textContent="前往官网完成";
    button.onclick=async()=>{
      const revision=epoch;
      button.disabled=true;
      try{
        const result=await official(id);
        if(revision!==epoch)return;
        if(!result.ok)throw new Error(result.error || "官网窗口暂不可用");
        const state=await read(kind,id);changed(kind,id,state);close();refresh();report("官网状态已重新核对");
      }catch(error){if(revision===epoch && popup)popup.querySelector("[data-cloud-error]").textContent=error.message;}
      finally{button.disabled=false;}
    };
    popup.append(button);position();
  }
  function editor(button,kind,id,state){
    const name=kind==="bookmark"?"收藏":"关注",version=shell(button,`管理${name}`);
    const privacy=state.active?state.privacy:(prefs[kind+"Default"]==="ask"?prefs[kind+"Last"]:prefs[kind+"Default"]);
    popup.innerHTML=`<strong>${state.active?`管理${name}`:`添加${name}`}</strong><fieldset class="cloud-privacy"><legend>可见范围</legend><label><input type="radio" name="cloudPrivacy" value="private" ${privacy==="private"?"checked":""}>私密</label><label><input type="radio" name="cloudPrivacy" value="public" ${privacy==="public"?"checked":""}>公开</label></fieldset>${kind==="bookmark"?'<label class="cloud-tags-label">收藏标签<input data-cloud-tags placeholder="用分号分隔，可留空" autocomplete="off"></label><div class="cloud-tag-suggestions" aria-label="已有收藏标签"></div>':""}<p class="cloud-error" data-cloud-error role="status"></p><div class="cloud-popover-actions"><button type="button" data-cloud-save>${state.active?"保存修改":`确认${name}`}</button>${state.active?`<button type="button" data-cloud-remove>取消${name}</button>`:""}</div>`;
    const input=popup.querySelector("[data-cloud-tags]");if(input){input.value=(state.tags || []).join("; ");input.oninput=()=>{input.dataset.edited="true";};}
    if(state.active && !state.privacy)popup.querySelector("[data-cloud-error]").textContent="隐私尚未确认，请选择本次希望使用的范围。";
    if(kind==="bookmark" && state.active && state.tags===null)popup.querySelector("[data-cloud-error]").textContent="标签尚未缓存，未编辑时会保留原标签；提交时核对。";
    if(!state.known)popup.querySelector("[data-cloud-error]").textContent="本地尚无完整状态，提交时核对。";
    const submit=async(active)=>{
      const selected=popup.querySelector('input[name="cloudPrivacy"]:checked');
      if(active && !selected){popup.querySelector("[data-cloud-error]").textContent="请选择公开或私密";return;}
      const tags=kind==="bookmark"?(state.active && state.tags===null && input.dataset.edited!=="true"?null:input.value.split(/[;；,，\n]/).map(t=>t.trim()).filter(Boolean)):[];
      popup.querySelectorAll("button,input").forEach(control=>control.disabled=true);
      await mutate(kind,id,state,active,selected?.value || "private",tags,version);
    };
    popup.querySelector("[data-cloud-save]").onclick=()=>submit(true);
    popup.querySelector("[data-cloud-remove]")?.addEventListener("click",()=>submit(false));
    position();popup.focus({preventScroll:true});
    if(kind==="bookmark"){
        const data=cachedTags() || {};
        const names=[...new Set([...(data.public || []),...(data.private || [])].map(t=>t.name))].slice(0,12);
        const area=popup.querySelector(".cloud-tag-suggestions");
        for(const name of names){const chip=document.createElement("button");chip.type="button";chip.textContent=name;
          chip.onclick=()=>{const tags=input.value.split(/[;；,，\n]/).map(t=>t.trim()).filter(Boolean);if(!tags.includes(name))input.value=[...tags,name].join("; ");input.dataset.edited="true";};area.append(chip);}
        position();
    }
  }
  function open(button,force){
    const {cloudKind:kind,cloudId:id}=button.dataset,targetKey=key(kind,id);
    if(busy.has(targetKey))return;
    if(!session().logged){login();report("请先登录 Pixiv，再进行云端操作");return;}
    close();
    const state=get(kind,id);
    if(!force && !state.active && prefsLoaded && prefs[kind+"Default"]!=="ask"){
      void mutate(kind,id,state,true,prefs[kind+"Default"],[],popupVersion);
    }else editor(button,kind,id,state);
  }

  async function tick(){
    if(clearing || !session().logged || !prefsLoaded || settingsPromise)return;
    if(prefs.autoFollowing && Date.now()>=autoNotBefore && !updatesBusy && Date.now()-lastUpdateCheck>=(updatesRemaining?30000:Number(prefs.syncMinutes)*60000)){
      updatesBusy=true;lastUpdateCheck=Date.now();const revision=epoch,generation=session().generation;
      void checkUpdates({continuing:updatesRemaining}).then(result=>{if(sameEpoch(revision,generation))updatesRemaining=Boolean(result.hasMore);})
        .catch(error=>{if(sameEpoch(revision,generation))report(`新作检查未完成：${error.message}`);})
        .finally(()=>{if(sameEpoch(revision,generation))updatesBusy=false;});
    }
    if(syncBusy || !manual.size && Date.now()-lastSyncAttempt<1000)return;
    if(prefs.autoSync && Date.now()>=autoNotBefore && (!accountPrepared || (Date.now()/1000-auxiliaryCompleted)/60>=Number(prefs.syncMinutes))){
      void prepareAccountData({network:true,refresh:accountPrepared});
    }
    const candidates=["bookmark","follow"],now=Date.now();
    const due=candidates.map((_,offset)=>candidates[(syncTurn+offset)%2]).find(candidate=>manual.has(candidate) || prefs.autoSync && now>=autoNotBefore &&
      (syncState[candidate]?.running || !syncState[candidate]?.completed || (now-syncState[candidate].completed*1000)/60000>=Number(prefs.syncMinutes)));
    if(!due)return;
    syncBusy=true;lastSyncAttempt=now;const revision=epoch,generation=session().generation;
    try{
      syncRequest=post("/api/cloud/sync",{kind:due,force:restart.has(due)});
      const data=await syncRequest;
      if(!sameEpoch(revision,generation))return;
      restart.delete(due);syncState[due]=data.sync;syncError="";syncTurn=due==="bookmark"?1:0;
      const preparePage=prefs.autoSync || manual.has(due);
      if(!data.sync.running)manual.delete(due);
      if(preparePage)await prepareAccount({page:data.sync.page,completed:data.sync.running?null:data.sync.pageOffsets});
      if(!sameEpoch(revision,generation))return;
      await hydrate(true);notifySettings();
    }catch(error){
      if(revision===epoch){syncError=error.message;lastSyncAttempt=Date.now()+55000;manual.clear();restart.clear();notifySettings();}
    }finally{syncBusy=false;syncRequest=null;if(sameEpoch(revision,generation)){notifySettings();setTimeout(()=>{void tick();},1100);}}
  }
  async function clearCache(){
    if(!session().logged){login();throw new Error("请先登录后清除当前账号缓存");}
    const generation=session().generation;
    clearing=true;
    epoch++;readVersion++;close();manual.clear();restart.clear();busy.clear();
    autoNotBefore=Date.now()+Number(prefs.syncMinutes)*60000;
    try{
      await clearStarted();
      if(syncRequest)await Promise.allSettled([syncRequest]);
      if(generation!==session().generation)throw new Error("账号已改变，请重新操作");
      await post("/api/cloud/clear-cache",{},30000);
      if(generation!==session().generation)throw new Error("账号已改变，请重新操作");
      states={bookmark:new Map(),follow:new Map()};syncState={};hydratedKey="";syncError="";auxiliaryError="";
      lastUpdateCheck=0;updatesRemaining=false;
      auxiliaryBusy=false;accountPrepared=false;auxiliaryCompleted=0;
      cleared();paint();notifySettings();report("当前账号缓存已清除");
    }finally{clearing=false;}
  }
  function refresh(){
    if(clearing || manual.size || syncBusy)return;
    if(!session().logged){login();report("请先登录 Pixiv，再同步云端状态");return;}
    for(const kind of ["bookmark","follow"]){manual.add(kind);restart.add(kind);}
    hydratedKey="";lastSyncAttempt=0;syncError="";notifySettings();void hydrate(true);void tick();
    void prepareAccountData({network:true,refresh:true});
  }
  function setSession(){
    const value=session(),next=JSON.stringify([value.logged,value.generation,value.method]);
    if(next===sessionKey)return;
    sessionKey=next;epoch++;readVersion++;close();busy.clear();manual.clear();restart.clear();
    updatesBusy=false;lastUpdateCheck=0;updatesRemaining=false;
    void clearStarted();
    auxiliaryBusy=false;accountPrepared=false;auxiliaryCompleted=0;auxiliaryError="";
    states={bookmark:new Map(),follow:new Map()};syncState={};owner=null;hydratedKey="";lastSyncAttempt=0;autoNotBefore=0;
    prefs={...defaults};prefsLoaded=false;syncError="";notifySettings();settingsPromise=null;
    const revision=epoch;
    void ready().then(async()=>{
      if(revision!==epoch)return;
      await hydrate(true);
      if(revision!==epoch || !session().logged)return;
      await prepareAccountData({network:false});
      if(revision!==epoch)return;
      if(prefs.autoSync)void prepareAccountData({network:true,refresh:true});
      void tick();
    }).catch(error=>report(error.message));paint();
  }
  document.addEventListener("click",event=>{
    const button=event.target.closest("[data-cloud-kind][data-cloud-id]");
    if(button){button.dispatchEvent(new Event("moku-card-action",{bubbles:true}));event.preventDefault();event.stopImmediatePropagation();void open(button,false);}
    else if(popup && !popup.contains(event.target)){readVersion++;close();}
  },true);
  document.addEventListener("dblclick",event=>{if(event.target.closest("[data-cloud-kind]")){event.preventDefault();event.stopImmediatePropagation();}},true);
  document.addEventListener("contextmenu",event=>{
    const button=event.target.closest("[data-cloud-kind][data-cloud-id]");
    if(button){button.dispatchEvent(new Event("moku-card-action",{bubbles:true}));event.preventDefault();event.stopImmediatePropagation();void open(button,true);}
  },true);
  document.addEventListener("keydown",event=>{if(event.key==="Escape" && popup){event.preventDefault();event.stopPropagation();readVersion++;close();}});
  document.addEventListener("scroll",schedule,true);window.addEventListener("resize",schedule);
  new MutationObserver(records=>{
    const selector="[data-cloud-kind][data-cloud-id]";
    if(records.some(record=>[...record.addedNodes,...record.removedNodes].some(node=>
      node.nodeType===1 && (node.matches(selector) || node.querySelector(selector)))))schedule();
  }).observe(document.body,{subtree:true,childList:true});
  setInterval(()=>{void tick();},5000);
  return {get busy(){return busy.size>0;},settled:()=>preferenceWrites,setSession,hydrate,refresh,clearCache,savePreferences,ready,getPreferences:()=>({...prefs}),getSync:()=>structuredClone(syncState)};
};
