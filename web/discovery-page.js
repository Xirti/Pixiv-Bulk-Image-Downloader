"use strict";
globalThis.createDiscoveryPage=function({root,request,escape:esc,allowed,signedIn,generation,changed,openArtwork,openArtist,choose,selected,canChoose,installImages,attachUgoira,report,autoPrepare}){
  const grid=root.querySelector('[data-discovery-grid]'),message=root.querySelector('[data-discovery-message]');
  const next=root.querySelector('[data-discovery-next]'),previous=root.querySelector('[data-discovery-prev]');
  const cache=new Map(),positions=new Map(),staged=new Map(),nextReads=new Map(),previews=createPreviewImageCache({retainUnused:true,maxImages:480});
  const initial=()=>({kind:'recommended',scope:'all',mode:'safe',rank:'day',offset:0,page:1,trail:[],batch:''});
  let state=initial(),data=null,rendered,active=false,pending=false,error='',revision=0,controller=null;
  let background=new AbortController(),preparing=null,networkQueued=false,lastPrepare=0,sessionGeneration=null;
  const reads=createPageReads(request);
  let restoreTop=null;
  const sourceKey=s=>JSON.stringify([s.kind,s.scope,s.mode,s.rank]);
  const pageKey=(s,batch=s.batch)=>JSON.stringify([sourceKey(s),batch,s.offset]);
  const interaction=createGalleryInteraction({grid,scrollSurface:root,allowed:()=>active && !pending && allowed(),report,
    openArtwork:index=>{const item=data?.items[index];if(item)openArtwork(item,{context:{kind:'pid',value:item.id},resultPage:state.page});},openArtist:item=>openArtist(item.userId)});
  const query=(s,extra={})=>'/api/discovery?'+new URLSearchParams({kind:s.kind,scope:s.scope,mode:s.mode,rank:s.rank,offset:s.offset,batch:s.batch,...extra});
  function remember(){if(active && data)positions.set(sourceKey(state),{state:structuredClone(state),data,scroll:root.scrollTop});}
  function render(){
    root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.discoveryTab===state.kind)));
    root.querySelector('[data-discovery-scope]').hidden=state.kind!=='following';root.querySelector('[data-discovery-scope]').value=state.scope;
    root.querySelector('[data-discovery-ranks]').hidden=state.kind!=='ranking';
    root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.discoveryRank===state.rank)));
    root.querySelector('[data-discovery-mode]').value=state.mode;root.querySelector('[data-discovery-mode]').hidden=state.kind==='ranking';
    message.textContent=error || (pending?'正在准备作品…':!signedIn()?'请在我的账号登录后查看发现。':data?.unavailable?`${data.unavailable} 件未在当前范围展示`:'');
    message.classList.toggle('is-error',Boolean(error));
    const edition=root.querySelector('[data-discovery-edition]');
    edition.textContent=state.kind==='ranking'?(data?.edition?`${data.edition.slice(0,4)}-${data.edition.slice(4,6)}-${data.edition.slice(6,8)}`:'站方未注明榜次日期 · 数字为来源序位'):'';
    root.querySelector('[data-discovery-update]').hidden=!staged.has(sourceKey(state));
    if(rendered!==data){
      interaction.reset({immediate:true});previews.park(grid);rendered=data;
      grid.innerHTML=data?.items.length?renderArtworkCards(data.items,{escape:esc,selected}):`<p class="account-empty">${pending?'正在读取作品…':signedIn()?'当前范围没有可展示的作品。':'登录后可浏览推荐、关注新作与榜单。'}</p>`;
      previews.mount(grid,pageKey(state));installImages(grid);attachUgoira(grid);interaction.mount(data?.items || []);
      grid.querySelectorAll('[data-select]').forEach(box=>box.onchange=()=>{const item=data.items[Number(box.dataset.select)];if(!choose(item,box.checked,{context:{kind:'pid',value:item.id},resultPage:state.page}))box.checked=selected(item.id);});
    }
    syncSelection();
    previous.disabled=!state.trail.length || pending;next.disabled=data?.nextOffset==null || pending;
    root.querySelector('[data-discovery-page]').textContent=`第 ${state.page} 页`;
    root.setAttribute('aria-busy',String(pending));
    if(!pending){if(restoreTop!==null){root.scrollTop=restoreTop;restoreTop=null;}changed();}
  }
  function syncSelection(){grid.querySelectorAll('[data-select]').forEach(box=>{box.checked=selected(data.items[Number(box.dataset.select)].id);box.disabled=!canChoose();});}
  async function load(refresh=false){
    controller?.abort();controller=new AbortController();const signal=controller.signal,version=++revision,auth=generation();
    const old=data;error='';
    const local=cache.get(pageKey(state));
    if(local && !refresh){data=local;pending=false;render();prepareNext();return;}
    pending=true;if(!refresh)data=null;render();
    try{
      const result=await reads.read(query(state,refresh?{refresh:'1',batch:'',offset:'0'}:{}),{signal});
      if(version!==revision || signal.aborted || auth!==generation())return;
      data=result;state={...state,batch:result.batch,offset:result.offset,...(refresh?{page:1,trail:[]}:{} )};
      if(refresh)restoreTop=0;
      cache.set(pageKey(state),result);positions.set(sourceKey(state),{state:structuredClone(state),data,scroll:root.scrollTop});staged.delete(sourceKey(state));
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      error=problem.message || '发现读取失败，请右键刷新重试';if(refresh)data=old;
    }finally{if(version===revision && !signal.aborted){pending=false;render();prepareNext();}}
  }
  function prepareNext(){
    if(!active || !autoPrepare() || !data || data.nextOffset==null)return;
    const target={...state,offset:data.nextOffset},key=pageKey(target),signal=background.signal,auth=generation();
    if(cache.has(key) || nextReads.has(key))return;
    const reading=reads.read(query(target,{background:'1'}),{signal}).then(async result=>{
      if(signal.aborted || auth!==generation())return;
      cache.set(key,result);await previews.prepare(key,result.items,signal,{warm:'account'});
    }).catch(problem=>{if(!signal.aborted && auth===generation())report(`下一页暂未准备好：${problem.message}`);}).finally(()=>{if(nextReads.get(key)===reading)nextReads.delete(key);});
    nextReads.set(key,reading);
  }
  async function open(options={}){
    if(sessionGeneration!==generation()){clear();sessionGeneration=generation();}
    remember();active=true;
    if(options.restore){state=structuredClone(options.restore);data=cache.get(pageKey(state)) || null;restoreTop=null;await load();return;}
    const target={...state,...options};
    if(target.kind!=='following')target.scope='all';
    if(target.kind!=='ranking')target.rank='day';else target.mode='safe';
    const saved=positions.get(sourceKey(target));
    state=saved?structuredClone(saved.state):{...target,offset:0,page:1,trail:[],batch:''};
    data=saved?.data || null;restoreTop=saved?.scroll || 0;
    if(!signedIn()){data=null;error='';pending=false;render();return;}
    await load(Boolean(options.refresh));
  }
  function stopBackground(){const stopping=preparing;background.abort();background=new AbortController();preparing=null;nextReads.clear();networkQueued=false;return stopping || Promise.resolve();}
  async function prepare({network=false,page=null}={}){
    if(page || !signedIn())return;
    const auth=generation();
    if(sessionGeneration!==auth){clear();sessionGeneration=auth;}
    if(preparing){if(network)networkQueued=true;return preparing;}
    if(network && (!autoPrepare() || Date.now()-lastPrepare<180000))return;
    if(network)lastPrepare=Date.now();
    const signal=background.signal;
    const task=(async()=>{
      const targets=[initial(),{...initial(),kind:'following'},...['day','week','month','week_rookie','week_original'].map(rank=>({...initial(),kind:'ranking',rank}))];
      for(const target of targets){
        if(signal.aborted || auth!==generation())return;
        const key=sourceKey(target),existing=positions.get(key);
        try{
          const result=await reads.read(query(target,{background:'1',...(network?target.kind!=='ranking' && existing?{refresh:'1'}:{}:{cached:'1'})}),{signal});
          if(result.cached===false || signal.aborted || auth!==generation())continue;
          const nextState={...target,batch:result.batch};cache.set(pageKey(nextState),result);
          if(!existing)positions.set(key,{state:nextState,data:result,scroll:0});
          else if(existing.state.batch!==result.batch && (existing.data.edition!==result.edition || existing.data.items.map(item=>item.id).join(',')!==result.items.map(item=>item.id).join(',')))staged.set(key,{state:nextState,data:result,scroll:0});
          await previews.prepare(pageKey(nextState),result.items,signal,{warm:'account'});
        }catch(problem){if(signal.aborted || auth!==generation())return;if(active && sourceKey(state)===key && !data){error=problem.message;render();}}
      }
      if(active){if(!data){const saved=positions.get(sourceKey(state));if(saved){state=structuredClone(saved.state);data=saved.data;}}render();}
    })().finally(()=>{if(preparing!==task)return;preparing=null;if(networkQueued && !signal.aborted){networkQueued=false;void prepare({network:true});}});
    preparing=task;return task;
  }
  function clear(){controller?.abort();revision++;void stopBackground();reads.clear();interaction.clear();previews.clear();cache.clear();positions.clear();staged.clear();state=initial();data=null;rendered=undefined;pending=false;error='';restoreTop=0;lastPrepare=0;grid.replaceChildren();if(active)render();}
  root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.onclick=()=>void open({kind:button.dataset.discoveryTab}));
  root.querySelector('[data-discovery-scope]').onchange=e=>void open({scope:e.target.value});
  root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.onclick=()=>void open({rank:button.dataset.discoveryRank}));
  root.querySelector('[data-discovery-mode]').onchange=e=>void open({mode:e.target.value});
  root.querySelector('[data-discovery-update]').onclick=()=>{const fresh=staged.get(sourceKey(state));if(!fresh)return;state=fresh.state;data=fresh.data;staged.delete(sourceKey(state));restoreTop=0;render();remember();prepareNext();};
  next.onclick=()=>{if(data?.nextOffset==null)return;remember();state={...state,offset:data.nextOffset,page:state.page+1,trail:[...state.trail,state.offset]};root.scrollTop=0;void load();};
  previous.onclick=()=>{if(!state.trail.length)return;remember();const trail=state.trail.slice(),offset=trail.pop();state={...state,offset,page:state.page-1,trail};root.scrollTop=0;void load();};
  return {open,prepare,stopBackground,clear,refresh:()=>load(true),syncSelection,snapshot:()=>structuredClone(state),suspend(){remember();active=false;controller?.abort();revision++;pending=false;interaction.reset({immediate:true});}};
};
