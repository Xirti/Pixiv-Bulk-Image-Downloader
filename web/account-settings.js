"use strict";

// Both account-page and settings actions display the same synchronization state.
globalThis.accountSyncPresentation = function (value,progress,loaded,clearing=false) {
  const rows=[progress.bookmark,progress.follow].filter(Boolean);
  const busy=Boolean(progress.manual || progress.preparing || progress.requesting || !progress.error && value.autoSync && rows.some(row=>row.running));
  const complete=rows.length===2 && rows.every(row=>row.completed);
  const status=busy?"正在同步…":progress.error?`同步失败：${progress.error}`:complete?
    `最近同步：${new Date(Math.min(...rows.map(row=>row.completed))*1000).toLocaleString()}`:
    value.autoSync?"等待首次同步":"自动同步已关闭";
  return {busy,disabled:!loaded || clearing || Boolean(progress.clearing) || busy,status};
};

globalThis.createPixivDisplaySettings = function ({root,request,session,reconcile,login}) {
  const fields=[...root.querySelectorAll('[data-pixiv-setting]')];
  const message=root.querySelector('[data-pixiv-display-status]');
  const refresh=root.querySelector('[data-pixiv-display-refresh]');
  const loginButton=root.querySelector('[data-pixiv-display-login]');
  const ageHint=root.querySelector('[data-pixiv-age-hint]');
  let sessionKey='',state=null,operation=null,error='',notice='';
  const readable=()=>session().logged && session().method==='web';
  function paint(){
    const connected=readable(),busy=Boolean(operation);
    for(const field of fields){
      const key=field.dataset.pixivSetting;
      field.disabled=!connected || !state || busy || key==='ageRestriction' && !state.canChangeAge;
      if(field.type==='checkbox')field.checked=Boolean(state?.[key]);
      else field.value=state?String(state[key]):'';
    }
    refresh.hidden=!connected;refresh.disabled=busy;
    loginButton.hidden=connected;
    ageHint.hidden=!connected || !state || state.canChangeAge;
    message.textContent=!connected?'此项需网页登录，请在登录管理中选择网页登录。':
      busy?notice:error || notice || (state?'修改后自动保存到 Pixiv。':'正在读取…');
  }
  function run(change=null,refreshState=false){
    if(!readable()){paint();return Promise.resolve();}
    if(operation)return operation;
    const startKey=sessionKey;
    const auth=session().generation;
    error='';notice=change?'正在保存…':'正在读取…';
    let refreshAgain=false;
    const task=(async()=>{
      try{
        const result=await request('/api/settings/pixiv'+(refreshState?'?refresh=1':''),change?{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({...change,authorizationGeneration:auth})
        }:{});
        if(sessionKey!==startKey)return;
        await reconcile(result);
        if(sessionKey!==startKey || !readable())return;
        if(session().generation!==result.authorizationGeneration || session().browsingGeneration!==result.browsingGeneration){
          refreshAgain=true;return;
        }
        state=result.settings;notice=change?'已保存到 Pixiv。':'';
      }catch(problem){
        if(sessionKey!==startKey)return;
        await reconcile(null);
        if(sessionKey===startKey && readable()){error=problem.message;notice='';}
      }finally{
        if(operation===task){
          operation=null;paint();
          if(readable() && !error && (refreshAgain || !state && sessionKey!==startKey))void run(null,true);
        }
      }
    })();
    operation=task;paint();return task;
  }
  fields.forEach(field=>field.addEventListener('change',()=>{
    const value=field.type==='checkbox'?field.checked:Number(field.value);
    void run({key:field.dataset.pixivSetting,value});
  }));
  refresh.onclick=()=>run(null,true);
  loginButton.onclick=login;
  return {
    load:()=>state?Promise.resolve():run(),
    setSession(){
      const current=session(),next=`${current.method}:${current.logged}:${current.generation}`;
      if(next===sessionKey)return;
      sessionKey=next;state=null;error='';notice='';paint();
      if(readable() && !operation)void run();
    }
  };
};

globalThis.createAccountSettings = function ({root,save,browse,report,syncNow,clearCache}) {
  const fields=[...root.querySelectorAll("[data-setting]")],dirty=new Set();
  const status=root.querySelector("[data-settings-status]"),sync=root.querySelector("[data-sync-status]");
  const button=root.querySelector("[data-settings-save]");
  const jump=root.querySelector("[data-settings-jump]");
  const syncButton=root.querySelector("[data-settings-sync]");
  const clearButton=root.querySelector("[data-settings-clear-cache]");
  let saving=false,clearing=false,lastProgress={},saved=null,preferencesLoaded=false;
  let revision=0;
  const search=root.querySelector('[data-settings-search]'),heading=root.querySelector('[data-settings-heading]');
  const categories=[...root.querySelectorAll('[data-settings-category]')],panels=[...root.querySelectorAll('[data-settings-panel]')];
  let category=root.dataset.settingsCategory || 'general';
  function showCategory(){
    const query=search.value.trim().toLocaleLowerCase();let found=false;
    for(const panel of panels){
      let matches=false;
      for(const group of panel.querySelectorAll('.settings-group')){
        let visible=false;
        const support=[...group.querySelectorAll('[data-settings-support]')];
        const supportMatches=Boolean(query) && support.some(row=>row.textContent.toLocaleLowerCase().includes(query));
        for(const row of group.querySelectorAll('.settings-item:not([data-settings-support])')){row.hidden=Boolean(query) && !supportMatches && !row.textContent.toLocaleLowerCase().includes(query);visible ||= !row.hidden;}
        support.forEach(row=>row.hidden=!visible);
        group.hidden=!visible;matches ||= visible;
      }
      panel.hidden=query?!matches:panel.dataset.settingsPanel!==category;
      found ||= !panel.hidden && matches;
    }
    categories.forEach(button=>button.setAttribute('aria-current',!query && button.dataset.settingsCategory===category?'page':'false'));
    heading.textContent=query?'搜索结果':categories.find(button=>button.dataset.settingsCategory===category).textContent;
    root.querySelector('[data-settings-empty]').hidden=found;
  }
  categories.forEach(button=>button.onclick=()=>{
    category=button.dataset.settingsCategory;root.dataset.settingsCategory=category;search.value='';showCategory();
    const page=root.closest('.workspace-page');page.scrollTop=Math.min(page.scrollTop,root.offsetTop);
  });
  search.addEventListener('input',showCategory);showCategory();
  function syncDependencies(){
    const automatic=root.querySelector('[data-setting="autoLoadPages"]'),visible=root.querySelector('[data-setting="showLoadButton"]');
    visible.disabled=automatic.disabled || !automatic.checked;
  }
  const valueOf=field=>field.type==="checkbox"?field.checked:field.value.trim();
  function edited(field){
    if(saved && valueOf(field)===saved[field.dataset.setting])dirty.delete(field.dataset.setting);
    else dirty.add(field.dataset.setting);
    status.textContent=dirty.size?"有未保存的设置":"";jump.hidden=dirty.size===0;
  }
  jump.onclick=()=>{
    const page=root.closest(".workspace-page"),footer=button.closest(".settings-footer");
    const bottom=footer.getBoundingClientRect().bottom-page.getBoundingClientRect().bottom;
    page.scrollTo({top:page.scrollTop+bottom+88,behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"});
    button.focus({preventScroll:true});
  };
  syncButton.onclick=()=>syncNow();
  clearButton.onclick=async()=>{
    if(clearing)return;
    const current=revision;
    clearing=true;clearButton.disabled=true;status.textContent="正在清除账号缓存…";
    try{
      await clearCache();
      if(current===revision)status.textContent="账号缓存已清除";
    }catch(error){
      if(current===revision)status.textContent=error.message;
    }
    finally{
      if(current===revision){
        clearing=false;
        update(saved || {},null,preferencesLoaded);
      }
    }
  };
  fields.forEach(field=>field.addEventListener("input",()=>{
    if(field.dataset.setting==='autoLoadPages' && !field.checked){
      const visible=root.querySelector('[data-setting="showLoadButton"]');visible.checked=true;edited(visible);
    }
    edited(field);syncDependencies();
  }));
  root.querySelector("[data-settings-browse]").onclick=async()=>{
    const field=root.querySelector('[data-setting="saveRoot"]');
    const current=revision;
    try{
      const data=await browse(field.value);
      if(current!==revision || data.cancelled)return;
      field.value=data.selected;
      edited(field);
    }catch(error){
      if(current===revision)status.textContent=error.message;
    }
  };
  button.onclick=async()=>{
    if(saving)return;
    const changes=Object.fromEntries(fields.filter(field=>dirty.has(field.dataset.setting)).map(field=>[field.dataset.setting,valueOf(field)]));
    if(!Object.keys(changes).length){status.textContent="设置已保存";return;}
    if(changes.syncMinutes!==undefined && (!/^[0-9]+$/.test(changes.syncMinutes) || BigInt(changes.syncMinutes)<3n)){
      status.textContent="同步间隔至少为 3 分钟，请输入整数。";root.querySelector('[data-setting="syncMinutes"]').focus();return;
    }
    const current=revision;
    saving=true;button.disabled=true;jump.disabled=true;fields.forEach(field=>field.disabled=true);
    try{
      const value=await save(changes);
      if(current!==revision)return;
      dirty.clear();
      update(value);
      status.textContent="设置已保存";
    }catch(error){
      if(current===revision){
        status.textContent=error.message;
        report(error.message);
      }
    }finally{
      if(current===revision){
        saving=false;
        button.disabled=!preferencesLoaded;
        jump.disabled=!preferencesLoaded;
        fields.forEach(field=>field.disabled=!preferencesLoaded);
        syncDependencies();
      }
    }
  };
  function update(value,progress=null,loaded=true){
    preferencesLoaded=loaded;
    if(progress!==null)lastProgress=progress;
    progress=lastProgress;
    button.disabled=!loaded || saving;
    jump.disabled=!loaded || saving;
    clearButton.disabled=!loaded || clearing;
    fields.forEach(field=>field.disabled=!loaded || saving);
    if(loaded){
      saved=value;
      for(const field of fields){
        const key=field.dataset.setting,next=value[key];
        if(dirty.has(key)){if(valueOf(field)===next)dirty.delete(key);else continue;}
        if(field.type==="checkbox")field.checked=next;
        else if(field.value!==String(next))field.value=next;
      }
    }
    jump.hidden=dirty.size===0;
    syncDependencies();
    const action=accountSyncPresentation(value,progress,loaded,clearing);
    syncButton.disabled=action.disabled;
    if(sync.textContent!==action.status)sync.textContent=action.status;
  }
  return {
    update,
    get dirty(){return dirty.size>0 || saving;},
    reset:()=>{
      revision++;
      dirty.clear();
      saving=false;
      clearing=false;
      saved=null;
      lastProgress={};
      preferencesLoaded=false;
      status.textContent="";
      jump.hidden=true;
      button.disabled=true;
      jump.disabled=true;
      clearButton.disabled=true;
      fields.forEach(field=>field.disabled=true);
      syncDependencies();
    },
  };
};
