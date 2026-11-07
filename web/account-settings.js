"use strict";

globalThis.createAccountSettings = function ({root,save,browse,report,syncNow,clearCache}) {
  const fields=[...root.querySelectorAll("[data-setting]")],dirty=new Set();
  const status=root.querySelector("[data-settings-status]"),sync=root.querySelector("[data-sync-status]");
  const button=root.querySelector("[data-settings-save]");
  const jump=root.querySelector("[data-settings-jump]");
  const syncButton=root.querySelector("[data-settings-sync]");
  const clearButton=root.querySelector("[data-settings-clear-cache]");
  let saving=false,clearing=false,lastProgress={},saved=null;
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
  fields.forEach(field=>field.addEventListener("input",()=>edited(field)));
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
    finally{saving=false;button.disabled=false;jump.disabled=false;fields.forEach(field=>field.disabled=false);}
  };
  function update(value,progress=null,loaded=true){
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
    const rows=[progress.bookmark,progress.follow].filter(Boolean),running=rows.some(row=>row.running);
    const complete=rows.length===2 && rows.every(row=>row.completed);
    syncButton.disabled=!loaded || clearing || Boolean(progress.manual) || Boolean(progress.preparing) || value.autoSync && running;
    const label=progress.error?`同步暂未完成：${progress.error}`:progress.preparing || progress.manual || value.autoSync && running?"正在后台准备账号资料与列表…":complete?
      `${value.autoSync?"":"自动同步已关闭 · "}最近完整同步：${new Date(Math.min(...rows.map(row=>row.completed))*1000).toLocaleString()}`:
      value.autoSync?"尚未完成首次同步":"自动同步已关闭，可点击立即同步或右键刷新。";
    if(sync.textContent!==label)sync.textContent=label;
  }
  return {update,reset:()=>{dirty.clear();saved=null;status.textContent="";jump.hidden=true;}};
};
