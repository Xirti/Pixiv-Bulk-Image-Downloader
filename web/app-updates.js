"use strict";

globalThis.createAppUpdates=function({root,request,beforeRestart,report,openUpdates}){
  const notice=createUpdateNotice({open:openUpdates,report});
  const status=root.querySelector("[data-update-status]"),detail=root.querySelector("[data-update-detail]");
  const check=root.querySelector("[data-update-check]"),download=root.querySelector("[data-update-download]");
  const restart=root.querySelector("[data-update-restart]"),cancel=root.querySelector("[data-update-cancel]");
  const channel=root.querySelector("[data-update-channel]"),automatic=root.querySelector("[data-update-automatic]");
  const progress=root.querySelector("[data-update-progress]");
  let state=null,reading=false,acting=false,timer=null,sequence=0,announced=null;
  const busy=()=>state && ["checking","downloading","verifying","restarting"].includes(state.phase);
  function render(){
    const phase=state.phase,release=state.release;
    check.disabled=acting || busy();channel.disabled=acting || busy();automatic.disabled=acting || busy();
    channel.value=state.options.channel;automatic.checked=state.options.automatic;
    download.hidden=phase!=="available";download.disabled=acting;
    restart.hidden=phase!=="ready";restart.disabled=acting || !state.installable;
    cancel.hidden=!["downloading","verifying"].includes(phase);cancel.disabled=acting;
    progress.hidden=!["downloading","verifying"].includes(phase);progress.max=release?.size || 1;progress.value=state.received;
    const labels={idle:"尚未检查更新",checking:"正在检查…",current:"当前没有更新版本",available:`可更新至 v${release?.version}`,
      downloading:`正在下载 · ${Math.min(100,Math.floor(state.received/(release?.size || 1)*100))}%`,verifying:"正在校验更新包…",ready:"更新已准备好",restarting:"正在重启更新…",error:"检查更新失败"};
    status.textContent=labels[phase];
    const bits=[`当前版本 v${state.current}`];
    if(release)bits.push(`${(release.size/1024/1024).toFixed(1)} MB`);
    if(release?.published)bits.push(`发布于 ${new Date(release.published).toLocaleDateString()}`);
    if(state.error)bits.push(state.error);
    if(!state.installable && phase==="ready")bits.push("源码运行请使用便携版完成更新");
    detail.textContent=bits.join(" · ");
    if(phase==="available" && !state.error){
      const completed=`${state.checked}:${release.version}`;
      if(completed!==announced){announced=completed;notice.show(release.version);}
    }
    if(!release)notice.clear();
    if(timer){clearTimeout(timer);timer=null;}
    if(busy() && phase!=="restarting")timer=setTimeout(()=>void read(),750);
  }
  async function read(){
    if(reading || acting)return;reading=true;const revision=++sequence;
    try{const value=await request("/api/updates");if(revision===sequence){state=value;render();}}
    catch(error){status.textContent=error.message;if(state && busy())timer=setTimeout(()=>void read(),3000);}
    finally{reading=false;}
  }
  async function act(action,extra={}){
    if(acting)return;acting=true;++sequence;if(state)render();
    try{state=await request("/api/updates",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...extra})});}
    catch(error){report(error.message);}
    finally{acting=false;if(state)render();}
  }
  check.onclick=()=>void act("check");download.onclick=()=>void act("download");cancel.onclick=()=>void act("cancel");
  channel.onchange=()=>void act("configure",{options:{channel:channel.value}});
  automatic.onchange=()=>void act("configure",{options:{automatic:automatic.checked}});
  restart.onclick=async()=>{
    if(acting)return;acting=true;render();
    try{
      await beforeRestart();
      if(!window.pywebview?.api?.restart_update)throw Error("请在 MOKU 桌面窗口中重启更新");
      document.body.inert=true;
      status.textContent="正在准备重启…";
      const result=await window.pywebview.api.restart_update();
      if(!result.ok)throw Error(result.error || "无法重启更新");
      state.phase="restarting";
    }catch(error){report(error.message);}
    finally{acting=false;if(state.phase!=="restarting")document.body.inert=false;render();}
  };
  // Daily checks belong to the backend. This timer observes local state only,
  // including checks completed while the user browses another page.
  setInterval(()=>{if(state?.options.automatic && !busy())void read();},15000);
  root.querySelector('[data-settings-category="updates"]').addEventListener("click",()=>void read());
  new MutationObserver(()=>{if(!root.hidden)void read();}).observe(root,{attributes:true,attributeFilter:["hidden"]});
  void read();
  return {read};
};
