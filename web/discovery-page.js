"use strict";

globalThis.createDiscoveryPage = function ({root,request,escape:esc,allowed,signedIn,generation,changed,openArtwork,openArtist,choose,selected,canChoose,installImages,attachUgoira,report,autoPrepare,browse,viewed=()=>{},backgroundAllowed=()=>true}) {
  const grid=root.querySelector('[data-discovery-grid]'),message=root.querySelector('[data-discovery-message]');
  const next=root.querySelector('[data-discovery-next]'),previous=root.querySelector('[data-discovery-prev]');
  const update=root.querySelector('[data-discovery-update]');
  const cache=new Map(),positions=new Map(),staged=new Map(),nextReads=new Map();
  const latest=new Map(),sourceVersions=new Map(),attempts=new Map(),followingDirty=new Set();
  const popularViews=new Map(),startedSources=new Set(),pausedSources=new Set();
  const previews=createPreviewImageCache({retainUnused:true,maxImages:480,reuseDetached:true});
  const initial=()=>({kind:'recommended',scope:'all',mode:'safe',rank:'day',offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:'',choice:'default',waiting:false});
  let state=initial(),data=null,rendered,active=false,pending=false,error='',revision=0,controller=null;
  let background=new AbortController(),preparing=null,networkQueued=false,sessionGeneration=null,preferences=null;
  const reads=createPageReads(request);
  let restoreTop=null,sorting=false,sortProgress="";
  let viewedData=null,followingVersion=0;
  function markViewed(){
    if(!active || document.hidden || pending || !signedIn() || sessionGeneration!==generation() || state.kind!=='following' || !data?.items.length || viewedData===data)return;
    const shown=data;viewedData=shown;
    void Promise.resolve(viewed(shown.items)).catch(()=>{if(viewedData===shown)viewedData=null;report('新作查看状态暂未保存');});
  }
  const sortControl=root.querySelector("[data-discovery-sort-control]");
  sortControl.innerHTML=popularitySortMarkup("data-discovery-sort");
  const sourceKey=s=>JSON.stringify([s.kind,s.scope,s.mode,s.rank]);
  const pageKey=(s,batch=s.batch)=>JSON.stringify([sourceKey(s),batch,s.offset,s.order || "default",s.popularityIndex || ""]);
  const sourceVersion=key=>sourceVersions.get(key) || 0;
  const ownsIdleView=(visit=revision)=>active && visit===revision && !pending && !sorting;
  const manual=()=>state.kind!=='following' && !browse.automatic();
  const japanDay=milliseconds=>Math.floor((milliseconds+9*3600000)/86400000);
  const rankingWindow=milliseconds=>Math.floor((milliseconds-3*3600000)/86400000);
  const publicationOrder=result=>result.publicationOrder ?? result.created*1000;
  const interaction=createGalleryInteraction({grid,scrollSurface:root,allowed:()=>active && !pending && allowed(),report,
    openArtwork:index=>{const item=data?.items[index];if(item)openArtwork(item,{context:{kind:'pid',value:item.id},resultPage:state.page});},openArtist:item=>openArtist(item.userId)});
  const query=(s,extra={})=>'/api/discovery?'+new URLSearchParams({kind:s.kind,scope:s.scope,mode:s.mode,rank:s.rank,offset:s.offset,batch:s.batch,...extra});
  async function readPage(target,extra={}, {signal,accept=result=>result}={}){
    let destination=target,parameters=extra;
    for(let attempt=0;attempt<2;attempt++){
      const version=followingVersion;
      const result=await reads.read(query(destination,parameters),{signal});
      signal?.throwIfAborted();
      if(result.cached===false && attempt)throw new Error('关注新作缓存已清理，请刷新');
      if(target.kind!=='following' || version===followingVersion || result.cached===false)return accept(result);
      // Re-filter the same snapshot against the newly synchronized relations.
      destination={...target,batch:result.batch,offset:result.offset};
      parameters={cached:'1',...(extra.background?{background:extra.background}:{})};
    }
    throw new Error('关注状态仍在更新，请稍后刷新');
  }
  function invalidateFollowing(){
    followingVersion++;
    for(const key of new Set([...positions.keys(),...latest.keys(),sourceKey(state),sourceKey({...initial(),kind:'following'})])){
      if(JSON.parse(key)[0]!=='following')continue;
      sourceVersions.set(key,sourceVersion(key)+1);latest.delete(key);staged.delete(key);attempts.delete(key);followingDirty.add(key);
    }
  }

  function remember(scroll=root.scrollTop){
    if(!active || !data && !(state.order==='popular' && state.popularityIndex))return;
    const record={state:structuredClone(state),data,scroll};
    const views=state.order==='popular'?popularViews:positions;
    views.set(sourceKey(state),record);
  }
  function replacePageReferences(previous,next){
    for(const views of [positions,staged,popularViews]){
      for(const [key,record] of views){
        if(record.data!==previous)continue;
        if(next)record.data=next;
        else views.delete(key);
      }
    }
    if(data!==previous)return false;
    data=next;
    return true;
  }
  function savePage(target,result) {
    const key=pageKey(target);
    cache.delete(key);cache.set(key,result);
    const source=sourceKey(target),known=latest.get(source);
    if(target.order!=="popular" && target.offset===0 && (!known || publicationOrder(result)>=publicationOrder(known)))latest.set(source,result);
    if(cache.size>80){
      for(const old of cache.keys()){
        if(old!==pageKey(state))cache.delete(old);
        if(cache.size<=80)break;
      }
    }
  }
  function automatic(target) {
    if(!preferences)return false;
    if(active && sourceKey(target)===sourceKey(state) && (pending || sorting))return false;
    if(pausedSources.has(sourceKey(target)))return false;
    if(target.kind!=='following' && !preferences.autoLoadPages && !startedSources.has(sourceKey(target)))return false;
    return preferences[{ranking:'autoRanking',following:'autoFollowing',recommended:'autoRecommended'}[target.kind]];
  }
  function due(target,result) {
    if(!result)return true;
    const created=result.created*1000;
    const now=Date.now();
    if(target.kind==='following')return now<created || now-created>=Number(preferences.syncMinutes)*60000;
    if(target.kind==='ranking')return (result.rankingWindow ?? rankingWindow(created))!==rankingWindow(now);
    return japanDay(created)!==japanDay(now);
  }
  function render(){
    preserveViewport(root,()=>{
    root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.discoveryTab===state.kind)));
    root.querySelector('[data-discovery-scope]').hidden=state.kind!=='following';root.querySelector('[data-discovery-scope]').value=state.scope;
    root.querySelector('[data-discovery-ranks]').hidden=state.kind!=='ranking';
    root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.discoveryRank===state.rank)));
    root.querySelector('[data-discovery-mode]').value=state.mode;root.querySelector('[data-discovery-mode]').hidden=state.kind==='ranking';
    message.textContent=error || (state.waiting?'选择排序后点击开始加载':pending?'正在准备作品…':!signedIn()?'请在我的账号登录后查看发现。':data?.unavailable?`${data.unavailable} 件未在当前范围展示`:'');
    message.classList.toggle('is-error',Boolean(error));
    const edition=root.querySelector('[data-discovery-edition]');
    const date=data?.edition;
    edition.textContent=state.kind==='ranking'?(date?`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}`:'站方未注明榜次日期 · 数字为来源序位'):'';
    if(data?.created)edition.title=`最近检查 ${new Date(data.created*1000).toLocaleString()}`;
    update.hidden=!staged.has(sourceKey(state));
    update.disabled=pending || sorting;
    sortControl.hidden=state.kind==='following';
    setPopularitySort(sortControl,state.choice || state.order || 'default',state.kind==='ranking'?'官方排序':'推荐顺序');
    setPopularityLoad(sortControl,true,pending || sorting,browse.buttonVisible(),Boolean(data) && !state.waiting && state.choice===state.order);
    root.querySelector('[data-discovery-sort-status]').textContent=sorting?sortProgress:state.order==='popular' && data?.total!=null?`${state.kind==='ranking'?'完整榜次':'本次推荐'} · ${data.total} 件 · 按收藏数`:'';

    if(rendered!==data){
      interaction.reset({immediate:true});previews.park(grid);rendered=data;
      grid.innerHTML=data?.items.length?renderArtworkCards(data.items,{escape:esc,selected}):`<p class="account-empty">${pending?'正在读取作品…':signedIn()?'当前范围没有可展示的作品。':'登录后可浏览推荐、关注新作与榜单。'}</p>`;
      previews.mount(grid,pageKey(state));installImages(grid);attachUgoira(grid);interaction.mount(data?.items || []);
      grid.querySelectorAll('[data-select]').forEach(box=>box.onchange=()=>{const item=data.items[Number(box.dataset.select)];if(!choose(item,box.checked,{context:{kind:'pid',value:item.id},resultPage:state.page}))box.checked=selected(item.id);});
    }
    syncSelection();
    const sortIncomplete=state.order==='popular' && data?.popularityIndex!==state.popularityIndex;
    previous.disabled=!state.trail.length || pending || sorting || state.waiting || sortIncomplete;next.disabled=data?.nextOffset==null || pending || sorting || state.waiting || sortIncomplete;
    root.querySelector('[data-discovery-page]').textContent=`第 ${state.page} 页`;
    root.setAttribute('aria-busy',String(pending || sorting));
    },pending?null:restoreTop);
    if(!pending){restoreTop=null;markViewed();changed();}
  }
  function syncSelection(){grid.querySelectorAll('[data-select]').forEach(box=>{box.checked=selected(data.items[Number(box.dataset.select)].id);box.disabled=!canChoose();});}
  async function load(refresh=false,restoreExpired=false,pageTarget=null){
    const destination=pageTarget || structuredClone(state);
    if(destination.order==='popular' && !refresh){
      const local=cache.get(pageKey(destination));
      if(local?.popularityIndex===destination.popularityIndex){
        controller?.abort();revision++;state=destination;data=local;pending=false;sorting=false;error='';
        if(pageTarget)restoreTop=0;
        render();return;
      }
      return loadPopularity(destination.page,restoreExpired,Boolean(pageTarget));
    }
    sorting=false;
    controller?.abort();controller=new AbortController();
    const signal=controller.signal,version=++revision,auth=generation(),key=sourceKey(destination);
    if(refresh){sourceVersions.set(key,sourceVersion(key)+1);staged.delete(key);}
    const followAtStart=followingVersion;error='';
    const local=cache.get(pageKey(destination));
    if(local && !refresh){
      state=destination;data=local;pending=false;
      if(pageTarget)restoreTop=0;
      render();prepareNext();return;
    }
    pending=true;if(!refresh && !pageTarget)data=null;render();
    try{
      await readPage(destination,refresh?{refresh:'1',batch:'',offset:'0',revision:String(version)}:{},{signal,accept(result){
        if(version!==revision || auth!==generation())return;
        data=result;state={...destination,batch:result.batch,offset:result.offset,...(refresh?{page:1,trail:[]}:{} )};
        if(pageTarget)restoreTop=0;
        if(refresh){restoreTop=root.scrollTop;if(followAtStart===followingVersion)followingDirty.delete(key);}
        savePage(state,result);remember(restoreTop ?? root.scrollTop);
        if(staged.get(key)?.state.batch===result.batch)staged.delete(key);
      }});
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      if(restoreExpired && staleHistory(problem))return rebuildHistory(destination,problem.errorKind==='cache_cleared');
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      if(pageTarget && !staleHistory(problem)){
        error=`${problem.message || '发现读取失败'} · 可再次翻页重试`;
        return;
      }
      error=problem.message || '发现读取失败，请右键刷新重试';
      state.waiting=true;
    }finally{if(version===revision && !signal.aborted){pending=false;render();if(!error)prepareNext();}}
  }
  function prepareNext(){
    if(state.waiting || state.order==='popular' || !active || document.hidden || !autoPrepare() || !data || data.nextOffset==null)return;
    const target={...state,offset:data.nextOffset},key=pageKey(target),signal=background.signal,auth=generation();
    if(cache.has(key) || nextReads.has(key))return;
    const reading=readPage(target,{background:'1'},{signal,async accept(result){
      if(auth!==generation() || nextReads.get(key)!==reading)return;
      savePage(target,result);await previews.prepare(key,result.items,signal,{warm:'account'});
    }}).catch(problem=>{if(!signal.aborted && auth===generation())report(`下一页暂未准备好：${problem.message}`);}).finally(()=>{if(nextReads.get(key)===reading)nextReads.delete(key);});
    nextReads.set(key,reading);
  }
  async function open(options={}){
    if(sessionGeneration!==generation()){clear();sessionGeneration=generation();}
    remember();controller?.abort();const visit=++revision;pending=true;sorting=false;active=true;
    try {
      await browse.ready();
    } catch (problem) {
      if(visit!==revision)return;
      pending=false;error=problem.message || '发现设置读取失败，请重试';render();return;
    }
    if(visit!==revision)return;
    pending=false;
    if(options.restore){
      state=structuredClone(options.restore);data=cache.get(pageKey(state)) || null;restoreTop=null;
      if(state.waiting){if(manual()){render();return;}await startLoad({restoring:true});return;}
      await load(false,Boolean(state.batch || state.popularityIndex));return;
    }
    const target={...state,...options};
    if(target.kind!=='following')target.scope='all';
    if(target.kind!=='ranking')target.rank='day';else target.mode='safe';
    const key=sourceKey(target),previousKey=sourceKey(state),choice=browse.order(target.kind);
    const normal=positions.get(key),popular=popularViews.get(key);
    const saved=(choice==='popular' && popular?.state.batch===normal?.state.batch?popular:null) || normal;
    state=saved?structuredClone(saved.state):{...target,offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:''};
    state.choice=choice;state.waiting=manual();data=startedSources.has(key)?saved?.data || null:null;restoreTop=key===previousKey?saved?.scroll || 0:0;error='';
    if(key!==previousKey)root.scrollTop=0;
    if(!signedIn()){data=null;pending=false;render();return;}
    if(manual() && !options.refresh){render();void prepare({network:false});return;}
    await startLoad({refresh:Boolean(options.refresh) || followingDirty.has(key)});
    void prepare({network:true});
  }
  function cancelLoad(){
    controller?.abort();revision++;pending=false;sorting=false;state.waiting=true;
    const key=sourceKey(state);startedSources.delete(key);pausedSources.add(key);sourceVersions.set(key,sourceVersion(key)+1);
    remember();error='加载已中断，可再次开始';render();
  }
  async function startLoad({refresh=false,restoring=false}={}){
    if(pending || sorting){cancelLoad();return;}
    if(!allowed() && !restoring)return;
    state.waiting=false;error='';startedSources.add(sourceKey(state));pausedSources.delete(sourceKey(state));
    const choice=state.choice || 'default';
    if(choice==='default' || state.kind==='following'){
      if(!refresh && data && state.order==='popular'){await applySort('default');return;}
      state.order='default';state.popularityIndex='';await load(refresh);return;
    }
    if(!refresh && state.order==='popular' && state.popularityIndex){await load();return;}
    if(!refresh && data && state.batch){await applySort('popular');return;}
    controller=new AbortController();const signal=controller.signal,version=++revision,auth=generation(),destination=structuredClone(state);
    const key=sourceKey(destination);
    if(refresh){sourceVersions.set(key,sourceVersion(key)+1);staged.delete(key);}
    pending=true;sortProgress='正在准备作品元数据…';render();
    try{
      // This seed is metadata only. Its default-order images never enter the DOM.
      const seed=await readPage({...destination,offset:0,batch:refresh?'':destination.batch},refresh?{refresh:'1',revision:String(version)}:{},{signal});
      if(signal.aborted || version!==revision || auth!==generation())return;
      const normal={...destination,batch:seed.batch,offset:0,page:1,trail:[],order:'default',popularityIndex:'',waiting:false};
      savePage(normal,seed);positions.set(key,{state:normal,data:seed,scroll:root.scrollTop});
      state={...normal,order:'popular',choice:'popular',edition:seed.edition};pending=false;
      await loadPopularity(1);
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      error=problem.message || '元数据准备失败，请重试';state.waiting=true;
    }
    finally{if(version===revision && !signal.aborted){pending=false;render();}}
  }
  function stopBackground(){
    const stopping=preparing;background.abort();background=new AbortController();preparing=null;nextReads.clear();networkQueued=false;
    return stopping || Promise.resolve();
  }
  function applyPrepared(target,result) {
    const key=sourceKey(target),nextState={...target,batch:result.batch,offset:result.offset};
    const known=latest.get(key);
    if(known && publicationOrder(known)>publicationOrder(result))return;
    savePage(nextState,result);
    const existing=positions.get(key);
    const record={state:nextState,data:result,scroll:0};
    if(!existing){positions.set(key,record);staged.delete(key);return;}
    if(existing.state.batch===result.batch)return;
    const first=cache.get(pageKey({...existing.state,offset:0}));
    // An App ranking may keep the same first page while its old cursor expires.
    const windowChanged=result.rankingWindow!=null && existing.data.rankingWindow!==result.rankingWindow;
    const same=!windowChanged && first && first.edition===result.edition && first.items.map(item=>item.id).join(',')===result.items.map(item=>item.id).join(',');
    if(same)staged.delete(key);
    else staged.set(key,record);
  }
  async function reconcileFollowing(){
    const signal=background.signal,auth=generation(),viewRevision=revision;let reload=false;
    for(const [key,page] of [...cache]){
      if(page.kind!=='following')continue;
      const [descriptor]=JSON.parse(key);
      const [kind,scope,mode,rank]=JSON.parse(descriptor);
      const target={kind,scope,mode,rank,batch:page.batch,offset:page.offset};
      const version=sourceVersion(descriptor);
      try{
        const result=await readPage(target,{cached:'1',background:'1'},{signal});
        if(signal.aborted || auth!==generation())return;
        if(sourceVersion(descriptor)!==version || cache.get(key)!==page)continue;
        if(result.cached===false){
          cache.delete(key);
          if(replacePageReferences(page,null) && ownsIdleView(viewRevision)){reload=true;render();}
          continue;
        }
        const same=page.items.map(item=>item.id).join(',')===result.items.map(item=>item.id).join(',');
        if(same)continue;
        savePage(target,result);
        if(replacePageReferences(page,result) && ownsIdleView(viewRevision))render();
      }catch(problem){if(signal.aborted || auth!==generation())return;report(`关注新作状态核对失败：${problem.message}`);return;}
    }
    if(reload && ownsIdleView(viewRevision) && state.kind==='following')await load(true);
  }
  async function prepare({network=false,page=null,completed=null}={}){
    if(!signedIn())return;
    if(page){if(page.kind==='follow' && completed){invalidateFollowing();await reconcileFollowing();}return;}
    if(network && (!preferences || !backgroundAllowed()))return;
    const auth=generation();
    if(sessionGeneration!==auth){clear();sessionGeneration=auth;}
    if(preparing){if(network)networkQueued=true;return preparing;}
    const signal=background.signal,viewRevision=revision;
    const task=(async()=>{
      const targets=new Map();
      const rankingTargets=['day','week','month','week_rookie','week_original'].map(rank=>({...initial(),kind:'ranking',rank}));
      const visitedTargets=Array.from(positions.values(),record=>({...record.state,offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:''}));
      const defaultTargets=[initial(),{...initial(),kind:'following'},...rankingTargets];
      for(const target of [...defaultTargets,...visitedTargets])targets.set(sourceKey(target),target);
      for(const [key,target] of targets){
        if(signal.aborted || auth!==generation())return;
        if(network && !automatic(target))continue;
        const version=sourceVersion(key);
        try{
          let result=latest.get(key);
          if(!result){
            result=await readPage(target,{background:'1',cached:'1',batch:'',offset:'0'},{signal});
            if(signal.aborted || auth!==generation() || version!==sourceVersion(key))continue;
            if(result.cached===false)result=null;
            else applyPrepared(target,result);
          }
          if(!network || !preferences || !automatic(target) || !due(target,result) && !followingDirty.has(key))continue;
          const now=performance.now();
          if(now-(attempts.get(key) ?? -Infinity)<60000)continue;
          attempts.set(key,now);
          result=await readPage(target,{background:'1',refresh:'1',batch:'',offset:'0'},{signal});
          if(signal.aborted || auth!==generation() || version!==sourceVersion(key))continue;
          followingDirty.delete(key);applyPrepared(target,result);
          if(!document.hidden)await previews.prepare(pageKey({...target,batch:result.batch}),result.items,signal,{warm:'account'});
        }catch(problem){
          if(signal.aborted || auth!==generation())return;
          if(version!==sourceVersion(key))continue;
          if(ownsIdleView(viewRevision) && sourceKey(state)===key && !error){error=problem.message;render();}
        }
      }
      if(signal.aborted || auth!==generation() || !active)return;
      if(ownsIdleView(viewRevision)){
        const saved=positions.get(sourceKey(state));
        // Preparing a source can seed the idle view, but cannot move its page or batch.
        if(!data && !error && !state.waiting && saved && state.page===saved.state.page &&
            pageKey(state,state.batch || saved.state.batch)===pageKey(saved.state)){
          state={...state,batch:saved.state.batch};
          data=saved.data;
        }
        render();
      }else{
        update.hidden=!staged.has(sourceKey(state));
      }
    })().finally(()=>{if(preparing!==task)return;preparing=null;if(networkQueued && !signal.aborted){networkQueued=false;void prepare({network:true});}});
    preparing=task;return task;
  }
  function followingChanged(id,relationship){
    invalidateFollowing();
    for(const [key,page] of cache){
      if(page.kind!=='following')continue;
      const filtered=page.items.filter(item=>item.userId!==id || relationship.active && (page.scope==='all' || relationship.privacy===page.scope));
      if(filtered.length===page.items.length)continue;
      const updated={...page,items:filtered};cache.set(key,updated);
      replacePageReferences(page,updated);
    }
    if(state.kind==='following' && ownsIdleView()){render();void load(true);}
  }
  async function loadPopularity(page,restoreExpired=false,pagination=false){
    controller?.abort();controller=new AbortController();
    const signal=controller.signal,version=++revision,auth=generation(),destination=structuredClone(state);
    sorting=true;sortProgress='正在准备作品元数据…';error='';render();
    try{
      const result=await readPopularityPage(request,{kind:destination.kind,batch:destination.batch,mode:destination.mode,
        index:destination.popularityIndex || '',page:String(page)},{signal,progress:value=>{
          if(version!==revision || signal.aborted)return;
          if(!pagination)state.popularityIndex=value.index;
          sortProgress=`已读取 ${value.loaded} 件${value.counted?` · 已核对 ${value.counted} 件收藏数`:''}，正在准备…`;render();
        }});
      if(version!==revision || signal.aborted || auth!==generation())return;
      state={...destination,order:'popular',popularityIndex:result.index,page,offset:(page-1)*36,trail:Array.from({length:page-1},(_,i)=>i*36)};
      data={...result,batch:destination.batch,scope:destination.scope,edition:destination.edition || data?.edition || '',popularityIndex:result.index,
        offset:state.offset,nextOffset:page<result.pages?page*36:null};
      savePage(state,data);restoreTop=pagination?0:root.scrollTop;remember(restoreTop);
    }catch(problem){if(version===revision && !signal.aborted){
      if(restoreExpired && staleHistory(problem))return rebuildHistory(destination,problem.errorKind==='cache_cleared');
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      if(pagination && !staleHistory(problem)){
        error=`${problem.message || '排序读取失败'} · 可再次翻页重试`;
        return;
      }
      error=`${problem.message} · 可重新开启排序重试`;
      const expired=['cache_miss','source_changed','ranking_changed'].includes(problem.errorKind);
      if(expired){popularViews.delete(sourceKey(state));state.popularityIndex='';}
      if(data && data.popularityIndex!==state.popularityIndex){
        const key=sourceKey(state),normal=positions.get(key);
        if(!expired)popularViews.set(key,{state:structuredClone(state),data,scroll:root.scrollTop});
        if(normal){
          if(!state.popularityIndex)state={...structuredClone(normal.state),choice:destination.choice};
          data=normal.data;
        }
      }
      state.waiting=true;
    }}
    finally{if(version===revision && !signal.aborted){sorting=false;render();}}
  }
  async function applySort(order){
    if(!allowed() || pending || state.kind==='following' || !data)return;
    remember();controller?.abort();revision++;sorting=false;
    const key=sourceKey(state),top=root.scrollTop;
    if(order==='default'){
      const normal=staged.get(key) || positions.get(key);
      if(!normal)return;
      state={...structuredClone(normal.state),choice:order,waiting:false};data=normal.data;restoreTop=top;staged.delete(key);error='';render();return;
    }
    const saved=popularViews.get(key);
    if(saved && saved.state.batch===state.batch){
      state={...structuredClone(saved.state),choice:order,waiting:false};data=saved.data;restoreTop=top;error='';render();
      if(data.popularityIndex!==state.popularityIndex)await loadPopularity(state.page);
      return;
    }
    state={...state,order:'popular',choice:'popular',popularityIndex:'',page:1,offset:0,trail:[]};
    await loadPopularity(1);
  }
  async function changeSort(order){
    if(!allowed() || state.kind==='following')return;
    if(pending || sorting)cancelLoad();
    state.choice=order;browse.choose(state.kind,order);
    if(manual()){state.waiting=true;error='';render();return;}
    await startLoad();
  }
  async function refresh(){
    if(pending || sorting)cancelLoad();
    await startLoad({refresh:true});
  }
  sortControl.querySelectorAll('[data-discovery-sort]').forEach(button=>button.onclick=()=>void changeSort((state.choice || state.order)==='popular'?'default':'popular'));
  sortControl.querySelector('[data-popularity-start]').onclick=()=>{
    const resuming=state.waiting && state.choice==='popular' && state.order==='popular' && state.popularityIndex;
    const refresh=!resuming && (state.kind==='ranking' || Boolean(data) && !state.waiting && state.choice===state.order);
    void startLoad({refresh});
  };
  function configure(value,loaded){
    const nextPreferences=loaded?{autoLoadPages:value.autoLoadPages,showLoadButton:value.showLoadButton,autoRanking:value.autoRanking,autoFollowing:value.autoFollowing,autoRecommended:value.autoRecommended,syncMinutes:value.syncMinutes}:null;
    if(JSON.stringify(preferences)===JSON.stringify(nextPreferences))return;
    preferences=nextPreferences;void stopBackground();
    if(active)render();
    if(loaded)void prepare({network:true});
  }
  const staleHistory=problem=>['cache_miss','cache_cleared','source_changed','ranking_changed'].includes(problem.errorKind);
  function rebuildHistory(destination,cleared){
    if(cleared)clear(false);
    else{
      const source=sourceKey(destination);
      for(const pages of [cache,nextReads])for(const key of pages.keys())if(JSON.parse(key)[0]===source)pages.delete(key);
      for(const pages of [latest,positions,staged,popularViews])pages.delete(source);
      sourceVersions.set(source,sourceVersion(source)+1);
    }
    state={...destination,offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:'',waiting:false};
    data=null;pending=false;sorting=false;restoreTop=0;
    return startLoad({restoring:true});
  }
  function retireClearedView(destination,message){
    clear(false);
    state={...destination,offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:'',waiting:true};
    error=message;
    render();
  }
  function clear(redraw=true){
    controller?.abort();revision++;void stopBackground();reads.clear();interaction.clear();previews.clear();cache.clear();positions.clear();staged.clear();latest.clear();attempts.clear();sourceVersions.clear();followingDirty.clear();
    popularViews.clear();startedSources.clear();pausedSources.clear();viewedData=null;sorting=false;state=initial();data=null;rendered=undefined;pending=false;error='';restoreTop=0;grid.replaceChildren();if(active && redraw)render();
  }
  root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.onclick=()=>void open({kind:button.dataset.discoveryTab}));
  root.querySelector('[data-discovery-scope]').onchange=e=>void open({scope:e.target.value});
  root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.onclick=()=>void open({rank:button.dataset.discoveryRank}));
  root.querySelector('[data-discovery-mode]').onchange=e=>void open({mode:e.target.value});
  update.onclick=()=>{
    if(pending || sorting)return;
    const fresh=staged.get(sourceKey(state));if(!fresh)return;
    controller?.abort();revision++;pending=false;sorting=false;error='';
    state={...structuredClone(fresh.state),choice:browse.order(fresh.state.kind),waiting:false};
    data=fresh.data;
    staged.delete(sourceKey(state));restoreTop=root.scrollTop;void startLoad();
  };
  next.onclick=()=>{if(next.disabled)return;remember();void load(false,false,{...state,offset:data.nextOffset,page:state.page+1,trail:[...state.trail,state.offset]});};
  previous.onclick=()=>{if(previous.disabled)return;remember();const trail=state.trail.slice(),offset=trail.pop();void load(false,false,{...state,offset,page:state.page-1,trail});};
  setInterval(()=>{void prepare({network:true});},30000);
  document.addEventListener('visibilitychange',markViewed);
  return {open,prepare,stopBackground,clear,configure,followingChanged,refresh,syncSelection,snapshot:()=>structuredClone({...state,waiting:state.waiting || pending || sorting}),suspend(){if(pending || sorting)state.waiting=true;remember();active=false;controller?.abort();revision++;pending=false;sorting=false;interaction.reset({immediate:true});}};
};
