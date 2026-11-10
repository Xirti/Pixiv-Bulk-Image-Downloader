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

globalThis.createAccountSettings = function ({root,save,browse,report,syncNow,clearCache}) {
  const fields=[...root.querySelectorAll("[data-setting]")],dirty=new Set();
  const status=root.querySelector("[data-settings-status]"),sync=root.querySelector("[data-sync-status]");
  const button=root.querySelector("[data-settings-save]");
  const jump=root.querySelector("[data-settings-jump]");
  const syncButton=root.querySelector("[data-settings-sync]");
  const clearButton=root.querySelector("[data-settings-clear-cache]");
  let saving=false,clearing=false,lastProgress={},saved=null,preferencesLoaded=false;
  const search=root.querySelector('[data-settings-search]'),heading=root.querySelector('[data-settings-heading]');
  const categories=[...root.querySelectorAll('[data-settings-category]')],panels=[...root.querySelectorAll('[data-settings-panel]')];
  let category=root.dataset.settingsCategory || 'general';
  function showCategory(){
    const query=search.value.trim().toLocaleLowerCase();let found=false;
    for(const panel of panels){
      let matches=false;
      for(const group of panel.querySelectorAll('.settings-group')){
        let visible=false;
        for(const row of group.querySelectorAll('.settings-item')){row.hidden=Boolean(query) && !row.textContent.toLocaleLowerCase().includes(query);visible ||= !row.hidden;}
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
    clearing=true;clearButton.disabled=true;status.textContent="正在清除账号缓存…";
    try{await clearCache();status.textContent="账号缓存已清除";}
    catch(error){status.textContent=error.message;}
    finally{clearing=false;clearButton.disabled=false;}
  };
  fields.forEach(field=>field.addEventListener("input",()=>{
    if(field.dataset.setting==='autoLoadPages' && !field.checked){
      const visible=root.querySelector('[data-setting="showLoadButton"]');visible.checked=true;edited(visible);
    }
    edited(field);syncDependencies();
  }));
  root.querySelector("[data-settings-browse]").onclick=async()=>{
    const field=root.querySelector('[data-setting="saveRoot"]');
    try{const data=await browse(field.value);if(data.cancelled)return;field.value=data.selected;edited(field);}
    catch(error){status.textContent=error.message;}
  };
  button.onclick=async()=>{
    if(saving)return;
    const changes=Object.fromEntries(fields.filter(field=>dirty.has(field.dataset.setting)).map(field=>[field.dataset.setting,valueOf(field)]));
    if(!Object.keys(changes).length){status.textContent="设置已保存";return;}
    if(changes.syncMinutes!==undefined && (!/^[0-9]+$/.test(changes.syncMinutes) || BigInt(changes.syncMinutes)<3n)){
      status.textContent="同步间隔至少为 3 分钟，请输入整数。";root.querySelector('[data-setting="syncMinutes"]').focus();return;
    }
    saving=true;button.disabled=true;jump.disabled=true;fields.forEach(field=>field.disabled=true);
    try{const value=await save(changes);dirty.clear();update(value);status.textContent="设置已保存";}
    catch(error){status.textContent=error.message;report(error.message);}
    finally{saving=false;button.disabled=!preferencesLoaded;jump.disabled=!preferencesLoaded;fields.forEach(field=>field.disabled=!preferencesLoaded);syncDependencies();}
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
  return {update,get dirty(){return dirty.size>0 || saving;},reset:()=>{dirty.clear();saved=null;preferencesLoaded=false;status.textContent="";jump.hidden=true;}};
};
