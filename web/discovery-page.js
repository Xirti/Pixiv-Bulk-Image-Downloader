"use strict";

globalThis.createDiscoveryPage = function ({root,request,escape:esc,allowed,signedIn,generation,changed,openArtwork,openArtist,choose,selected,canChoose,installImages,attachUgoira,report,autoPrepare,browse,viewed=()=>{},backgroundAllowed=()=>true}) {
  const grid=root.querySelector('[data-discovery-grid]'),message=root.querySelector('[data-discovery-message]'),retryButton=root.querySelector('[data-discovery-retry]');
  const next=root.querySelector('[data-discovery-next]'),previous=root.querySelector('[data-discovery-prev]');
  const update=root.querySelector('[data-discovery-update]');
  const cache=new Map(),positions=new Map(),staged=new Map(),nextReads=new Map();
  const latest=new Map(),sourceVersions=new Map(),attempts=new Map(),followingDirty=new Set();
  const popularViews=new Map(),startedSources=new Set(),pausedSources=new Set(),refreshRequired=new Map(),backgroundBatches=new Map();
  const recommendationSizes=new Map();
  const previews=createPreviewImageCache({retainUnused:true,maxImages:480,reuseDetached:true});
  const initial=()=>({kind:'recommended',scope:'all',mode:'safe',rank:'day',offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:'',choice:'default',waiting:false});
  let state=initial(),data=null,rendered,active=false,pending=false,error='',revision=0,controller=null;
  let background=new AbortController(),preparing=null,warming=null,networkQueued=false,sessionGeneration=null,preferences=null;
  const reads=createPageReads(request);
  let restoreTop=null,sorting=false,sortProgress="";
  let settingsRetry=null,updateController=null,retryLoad=null;
  let viewedData=null,followingVersion=0;
  function markViewed(){
    if(!active || document.hidden || pending || !signedIn() || sessionGeneration!==generation() || state.kind!=='following' || !data?.items.length || viewedData===data)return;
    const shown=data;viewedData=shown;
    void Promise.resolve(viewed(shown.items)).catch(problem=>{
      if(viewedData===shown)viewedData=null;
      console.warn('Unable to save the viewed artworks',problem);
    });
  }
  const sortControl=root.querySelector("[data-discovery-sort-control]");
  sortControl.innerHTML=popularitySortMarkup("data-discovery-sort");
  const sourceKey=s=>JSON.stringify([s.kind,s.scope,s.mode,s.rank]);
  const pageKey=(s,batch=s.batch)=>JSON.stringify([sourceKey(s),batch,s.offset,s.order || "default",s.popularityIndex || ""]);
  const reusableSort=(saved,source)=>saved && saved.state.batch===source?.batch && (source.kind!=='recommended' ||
    (saved.state.sortSourceSize ?? 0)>=(recommendationSizes.get(source.batch) ?? 0));
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
      const url=query(destination,parameters);
      let result;
      try{result=await reads.read(url,{signal});}
      catch(problem){
        if(signal?.aborted || problem.errorKind!=='cache_changed')throw problem;
        result=await reads.read(url,{signal});
      }
      signal?.throwIfAborted();
      if(result.cached===false && attempt)throw Object.assign(new Error('关注新作批次已更新'),{errorKind:'cache_miss'});
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
      sourceVersions.set(key,sourceVersion(key)+1);latest.delete(key);staged.delete(key);attempts.delete(key);followingDirty.add(key);backgroundBatches.delete(key);refreshRequired.delete(key);
    }
  }

  function beginSourceRefresh(key){
    refreshRequired.set(key,'');backgroundBatches.delete(key);
    sourceVersions.set(key,sourceVersion(key)+1);
    staged.delete(key);
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
    if(target.order!=="popular" && target.kind==='recommended' && result.sourceSize!=null){
      const size=Math.max(result.sourceSize,recommendationSizes.get(result.batch) ?? 0);
      recommendationSizes.delete(result.batch);recommendationSizes.set(result.batch,size);
      if(recommendationSizes.size>80)recommendationSizes.delete(recommendationSizes.keys().next().value);
    }
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
  function refreshInterval(target,result) {
    if(!result)return 60000;
    const created=result.created*1000;
    const now=Date.now();
    if(target.kind==='following')return now<created || now-created>=Number(preferences.syncMinutes)*60000 ? 60000 : null;
    if(target.kind==='ranking'){
      const window=rankingWindow(now);
      if((result.rankingWindow ?? rankingWindow(created))!==window)return 60000;
      // A dated Web response can still be from an earlier edition. Allow a
      // bounded retry period without treating App or undated results as stale.
      if(result.method!=='web' || !/^\d{8}$/.test(result.edition))return null;
      const day=window*86400000;
      const edition=new Date(day).toISOString().slice(0,10).replaceAll('-','');
      const interval=30*60000;
      return result.edition<edition && now-(day+3*3600000)<4*3600000 && now-created>=interval ? interval : null;
    }
    return japanDay(created)!==japanDay(now) ? 60000 : null;
  }
  function render(){
    preserveViewport(root,()=>{
    root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.discoveryTab===state.kind)));
    root.querySelector('[data-discovery-scope]').hidden=state.kind!=='following';root.querySelector('[data-discovery-scope]').value=state.scope;
    root.querySelector('[data-discovery-ranks]').hidden=state.kind!=='ranking';
    root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.discoveryRank===state.rank)));
    root.querySelector('[data-discovery-mode]').value=state.mode;root.querySelector('[data-discovery-mode]').hidden=state.kind==='ranking';
    message.textContent=error || (pending?'正在加载…':!signedIn()?'登录 Pixiv 后查看发现':'');
    message.classList.toggle('is-error',Boolean(error));
    retryButton.hidden=!retryLoad || pending || sorting;
    const edition=root.querySelector('[data-discovery-edition]');
    const date=data?.edition;
    edition.textContent=state.kind==='ranking' && date?`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}`:'';
    if(data?.created)edition.title=`最近检查 ${new Date(data.created*1000).toLocaleString()}`;
    update.hidden=!staged.has(sourceKey(state));
    update.disabled=pending || sorting || Boolean(settingsRetry);
    sortControl.hidden=state.kind==='following' && !settingsRetry;
    setPopularitySort(sortControl,state.choice || state.order || 'default',state.kind==='ranking'?'官方排序':'推荐顺序');
    setPopularityLoad(sortControl,true,pending || sorting,browse.buttonVisible(),Boolean(data) && !state.waiting && state.choice===state.order);
    root.querySelector('[data-discovery-sort-status]').textContent=sorting?sortProgress:'';

    if(rendered!==data){
      interaction.reset({immediate:true});previews.park(grid);rendered=data;
      grid.innerHTML=data?.items.length?renderArtworkCards(data.items,{escape:esc,selected}):
        data?'<p class="account-empty">没有符合条件的作品</p>':'';
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
  async function load({refresh=false,checkpoint='',restoreExpired=false,pageTarget=null,recoverSource=false}={}){
    const destination=pageTarget || structuredClone(state);
    if(destination.order==='popular' && !refresh){
      const local=cache.get(pageKey(destination));
      if(local?.popularityIndex===destination.popularityIndex){
        controller?.abort();revision++;state=destination;data=local;pending=false;sorting=false;error='';
        if(pageTarget)restoreTop=0;
        render();return;
      }
      return loadPopularity(destination.page,{restoreExpired,pagination:Boolean(pageTarget),recoverSource});
    }
    sorting=false;
    controller?.abort();controller=new AbortController();
    const signal=controller.signal,version=++revision,auth=generation(),key=sourceKey(destination);
    if(refresh && !checkpoint)beginSourceRefresh(key);
    const followAtStart=followingVersion;error='';retryLoad=null;
    const local=cache.get(pageKey(destination));
    if(local && !refresh){
      state=destination;data=local;pending=false;
      if(pageTarget)restoreTop=0;
      render();prepareNext();return;
    }
    pending=true;if(!refresh && !pageTarget)data=null;render();
    try{
      await readPage(destination,refresh?{...(checkpoint?{batch:checkpoint}:{refresh:'1',batch:''}),offset:'0',revision:String(version)}:{},{signal,accept(result){
        if(version!==revision || auth!==generation())return;
        data=result;state={...destination,batch:result.batch,offset:result.offset,...(refresh?{page:1,trail:[]}:{} )};
        if(pageTarget)restoreTop=0;
        if(refresh){
          refreshRequired.delete(key);restoreTop=root.scrollTop;
          if(followAtStart===followingVersion)followingDirty.delete(key);
        }
        savePage(state,result);remember(restoreTop ?? root.scrollTop);
        if(staged.get(key)?.state.batch===result.batch)staged.delete(key);
      }});
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      if(!pageTarget && problem.errorKind==='filtered' && problem.batch)refreshRequired.set(key,problem.batch);
      if(checkpoint && staleHistory(problem))refreshRequired.set(key,'');
      if(restoreExpired && staleHistory(problem))return rebuildHistory(destination,problem.errorKind==='cache_cleared');
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      if(pageTarget && staleHistory(problem)){
        const ready=staged.get(key);
        if(!ready)return load({refresh:true});
        state={...structuredClone(ready.state),choice:destination.choice,waiting:false};
        data=ready.data;staged.delete(key);refreshRequired.delete(key);restoreTop=0;
        remember(0);return;
      }
      if(pageTarget){
        error=problem.message || '这一页暂未读取';
        retryLoad=()=>load({pageTarget});
        return;
      }
      error=problem.message || '发现暂未读取';
      state.waiting=true;
      retryLoad=()=>startLoad({refresh:refresh && !refreshRequired.has(key)});
    }finally{if(version===revision && !signal.aborted){pending=false;render();if(!error)prepareNext();}}
  }
  function prepareNext(){
    if(state.waiting || state.order==='popular' || !active || document.hidden || !autoPrepare() || !data || data.nextOffset==null)return;
    const target={...state,offset:data.nextOffset},key=pageKey(target),signal=background.signal,auth=generation();
    if(cache.has(key) || nextReads.has(key))return;
    const reading=readPage(target,{background:'1'},{signal,async accept(result){
      if(auth!==generation() || nextReads.get(key)!==reading)return;
      savePage(target,result);await previews.prepare(key,result.items,signal,{warm:'account'});
    }}).catch(problem=>{
      if(!signal.aborted && auth===generation() && nextReads.get(key)===reading)
        console.warn('Discovery next page prefetch failed',problem);
    }).finally(()=>{if(nextReads.get(key)===reading)nextReads.delete(key);});
    nextReads.set(key,reading);
  }
  async function open(options={},loadOnReady=false){
    if(sessionGeneration!==generation()){clear();sessionGeneration=generation();}
    const {kind,scope,mode,rank}=options.restore || {...state,...options};
    const target={kind,scope:kind==='following'?scope:'all',mode:kind==='ranking'?'safe':mode,rank:kind==='ranking'?rank:'day'};
    settingsRetry=null;retryLoad=null;
    remember();controller?.abort();const visit=++revision;pending=true;sorting=false;active=true;
    try {
      await browse.ready();
    } catch (problem) {
      if(visit!==revision)return;
      settingsRetry=options.restore?options:{...target,refresh:options.refresh};
      if(sourceKey(state)!==sourceKey(target)){state={...initial(),...target,waiting:true};data=null;restoreTop=0;}
      pending=false;error=problem.message || '发现设置暂未读取';retryLoad=()=>open(settingsRetry,true);render();return;
    }
    if(visit!==revision)return;
    pending=false;
    if(options.restore){
      state=structuredClone(options.restore);data=cache.get(pageKey(state)) || null;restoreTop=null;
      if(state.waiting){if(manual() && !loadOnReady){render();return;}await startLoad({restoring:true});return;}
      await load({restoreExpired:Boolean(state.batch || state.popularityIndex)});return;
    }
    const key=sourceKey(target),previousKey=sourceKey(state),choice=browse.order(target.kind);
    const normal=positions.get(key),popular=popularViews.get(key);
    const saved=(choice==='popular' && reusableSort(popular,normal?.state)?popular:null) || normal;
    state=saved?structuredClone(saved.state):{...target,offset:0,page:1,trail:[],batch:'',order:'default',popularityIndex:''};
    state.choice=choice;state.waiting=manual();data=startedSources.has(key)?saved?.data || null:null;restoreTop=key===previousKey?saved?.scroll || 0:0;error='';
    if(key!==previousKey)root.scrollTop=0;
    if(!signedIn()){data=null;pending=false;render();return;}
    if(manual() && !options.refresh && !loadOnReady){render();void prepare({network:false});return;}
    await startLoad({refresh:Boolean(options.refresh) || followingDirty.has(key)});
    void prepare({network:true});
  }
  function cancelLoad(){
    controller?.abort();revision++;pending=false;sorting=false;retryLoad=null;
    if(updateController && updateController===controller){
      error='加载已中断，可再次应用更新';render();return;
    }
    state.waiting=true;
    const key=sourceKey(state);startedSources.delete(key);pausedSources.add(key);sourceVersions.set(key,sourceVersion(key)+1);
    remember();error='加载已中断，可再次开始';render();
  }
  async function startLoad({refresh=false,restoring=false}={}){
    if(pending || sorting){cancelLoad();return;}
    if(!allowed() && !restoring)return;
    if(settingsRetry)return open(settingsRetry,true);
    const key=sourceKey(state);
    const checkpoint=refresh?'':refreshRequired.get(key) || '';
    refresh ||= refreshRequired.has(key);
    state.waiting=false;error='';retryLoad=null;startedSources.add(key);pausedSources.delete(key);
    const choice=state.choice || 'default';
    if(choice==='default' || state.kind==='following'){
      if(!refresh && data && state.order==='popular'){await applySort('default');return;}
      state.order='default';state.popularityIndex='';await load({refresh,checkpoint});return;
    }
    if(!refresh && state.order==='popular' && state.popularityIndex){await load({recoverSource:true});return;}
    if(!refresh && data && state.batch){await applySort('popular');return;}
    controller=new AbortController();const signal=controller.signal,version=++revision,auth=generation(),destination=structuredClone(state);
    if(refresh && !checkpoint)beginSourceRefresh(key);
    pending=true;sortProgress='正在准备作品元数据…';render();
    try{
      // This seed is metadata only. Its default-order images never enter the DOM.
      const seed=await readPage({...destination,offset:0,batch:refresh?checkpoint:destination.batch},refresh && !checkpoint?{refresh:'1',revision:String(version)}:{},{signal});
      if(signal.aborted || version!==revision || auth!==generation())return;
      if(refresh)refreshRequired.delete(key);
      const normal={...destination,batch:seed.batch,offset:0,page:1,trail:[],order:'default',popularityIndex:'',waiting:false};
      savePage(normal,seed);positions.set(key,{state:normal,data:seed,scroll:root.scrollTop});
      state={...normal,order:'popular',choice:'popular',edition:seed.edition};pending=false;
      await loadPopularity(1);
    }catch(problem){
      if(version!==revision || signal.aborted)return;
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      if(problem.errorKind==='filtered' && problem.batch)refreshRequired.set(key,problem.batch);
      if(checkpoint && staleHistory(problem))refreshRequired.set(key,'');
      if(!refresh && destination.batch && staleHistory(problem)){
        pending=false;return startLoad({refresh:true,restoring});
      }
      error=problem.message || '作品元数据暂未读取';state.waiting=true;
      retryLoad=()=>startLoad();
    }
    finally{if(version===revision && !signal.aborted){pending=false;render();}}
  }
  function stopBackground(){
    const stopping=[preparing,warming];
    background.abort();background=new AbortController();preparing=warming=null;nextReads.clear();networkQueued=false;
    return Promise.allSettled(stopping);
  }
  function applyPrepared(target,result) {
    const key=sourceKey(target),nextState={...target,batch:result.batch,offset:result.offset};
    const known=latest.get(key);
    if(known && publicationOrder(known)>publicationOrder(result))return;
    savePage(nextState,result);
    const existing=positions.get(key);
    const record={state:nextState,data:result,scroll:0};
    if(!existing){positions.set(key,record);staged.delete(key);}
    else if(existing.state.batch!==result.batch){
      const first=cache.get(pageKey({...existing.state,offset:0}));
      // The same cards can have a different continuation or belong to a new ranking window.
      const same=first && first.edition===result.edition && first.rankingWindow===result.rankingWindow &&
        first.nextOffset===result.nextOffset && first.items.map(item=>item.id).join(',')===result.items.map(item=>item.id).join(',');
      if(same)staged.delete(key);
      else staged.set(key,record);
    }
    if(active && sourceKey(state)===key)update.hidden=!staged.has(key);
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
      }catch(problem){
        if(signal.aborted || auth!==generation())return;
        console.warn('Unable to reconcile cached following works',problem);
        if(sourceVersion(descriptor)===version && cache.get(key)===page){
          cache.delete(key);reload=replacePageReferences(page,null) || reload;
        }
      }
    }
    if(reload && ownsIdleView(viewRevision) && state.kind==='following')await load({refresh:true});
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
          if(!network || !preferences || !automatic(target))continue;
          const interval=followingDirty.has(key) ? 60000 : refreshInterval(target,result);
          if(interval===null)continue;
          const now=performance.now();
          if(now-(attempts.get(key) ?? -Infinity)<interval)continue;
          attempts.set(key,now);
          const batch=backgroundBatches.get(key);
          result=await readPage(target,{background:'1',...(batch?{batch}:{refresh:'1',batch:''}),offset:'0'},{signal});
          if(signal.aborted || auth!==generation() || version!==sourceVersion(key))continue;
          backgroundBatches.delete(key);followingDirty.delete(key);applyPrepared(target,result);
          if(!document.hidden && !warming){
            const imageKey=pageKey({...target,batch:result.batch});
            const warm=waitForMediaDecode(imageSignal=>previews.prepare(imageKey,result.items,imageSignal,{warm:'account'}),signal)
              .catch(problem=>{
                if(!signal.aborted)console.warn('Discovery thumbnail prefetch failed',problem);
              }).finally(()=>{if(warming===warm)warming=null;});
            warming=warm;
          }
        }catch(problem){
          if(signal.aborted || auth!==generation())return;
          if(version!==sourceVersion(key))continue;
          if(problem.errorKind==='filtered' && problem.batch)backgroundBatches.set(key,problem.batch);
          else if(staleHistory(problem))backgroundBatches.delete(key);
          console.warn('Discovery background preparation failed',problem);
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
    if(state.kind==='following' && ownsIdleView()){render();void load({refresh:true});}
  }
  async function loadPopularity(page,{restoreExpired=false,pagination=false,candidate=null,recoverSource=false}={}){
    controller?.abort();const operation=controller=new AbortController();
    const signal=operation.signal,version=++revision,auth=generation(),destination=structuredClone(candidate?.state || state);
    if(candidate)updateController=operation;
    sorting=true;sortProgress='正在准备作品元数据…';error='';retryLoad=null;render();
    const parameters={kind:destination.kind,batch:destination.batch,mode:destination.mode,
      index:(candidate?candidate.index:destination.popularityIndex) || '',page:String(page)};
    const progress=value=>{
      if(version!==revision || signal.aborted)return;
      if(candidate)candidate.index=value.index;
      else if(!pagination){state.popularityIndex=value.index;state.sortSourceSize=value.sourceSize;}
      sortProgress=`已读取 ${value.loaded} 件${value.counted?` · 已核对 ${value.counted} 件收藏数`:''}${value.missing?` · ${value.missing} 件缺少收藏数`:''}，正在准备…`;render();
    };
    try{
      let result;
      try{
        result=await readPopularityPage(request,parameters,{signal,progress});
      }catch(problem){
        if(!recoverSource || !parameters.index || problem.errorKind!=='cache_miss' || version!==revision || signal.aborted)throw problem;
        result=await readPopularityPage(request,{...parameters,index:'',page:'1'},{signal,progress});
        page=1;
      }
      if(version!==revision || signal.aborted || auth!==generation())return;
      if(candidate){
        const key=sourceKey(destination);
        positions.set(key,{state:candidate.state,data:candidate.data,scroll:candidate.scroll});
        if(staged.get(key)===candidate)staged.delete(key);
      }
      state={...destination,order:'popular',...(candidate?{choice:'popular',waiting:false}:{}),popularityIndex:result.index,sortSourceSize:result.sourceSize,page,
        offset:(page-1)*36,trail:Array.from({length:page-1},(_,i)=>i*36)};
      data={...result,batch:destination.batch,scope:destination.scope,edition:candidate?.data.edition || destination.edition || data?.edition || '',popularityIndex:result.index,
        offset:state.offset,nextOffset:page<result.pages?page*36:null};
      savePage(state,data);restoreTop=pagination?0:root.scrollTop;remember(restoreTop);
    }catch(problem){if(version===revision && !signal.aborted){
      if(restoreExpired && staleHistory(problem))return rebuildHistory(destination,problem.errorKind==='cache_cleared');
      if(problem.errorKind==='cache_cleared')return retireClearedView(destination,problem.message);
      if((recoverSource || pagination) && staleHistory(problem)){sorting=false;return startLoad({refresh:true});}
      if(candidate){
        if(staleHistory(problem))candidate.index='';
        error=problem.message || '新内容暂未完成排序';
        retryLoad=()=>loadPopularity(1,{candidate});
        return;
      }
      if(pagination){
        error=problem.message || '排序页暂未读取';
        retryLoad=()=>loadPopularity(page,{pagination:true});
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
      state.waiting=true;retryLoad=()=>startLoad();
    }}
    finally{
      if(updateController===operation)updateController=null;
      if(version===revision && !signal.aborted){sorting=false;render();}
    }
  }
  async function applySort(order){
    if(!allowed() || pending || state.kind==='following' || !data)return;
    remember();controller?.abort();revision++;sorting=false;
    const key=sourceKey(state),top=root.scrollTop;
    if(order==='default'){
      const normal=staged.get(key) || positions.get(key);
      if(!normal)return;
      state={...structuredClone(normal.state),choice:order,waiting:false};data=normal.data;restoreTop=top;staged.delete(key);error='';retryLoad=null;render();return;
    }
    const saved=popularViews.get(key);
    if(reusableSort(saved,state)){
      state={...structuredClone(saved.state),choice:order,waiting:false};data=saved.data;restoreTop=top;error='';retryLoad=null;render();
      if(data.popularityIndex!==state.popularityIndex)await loadPopularity(state.page,{recoverSource:true});
      return;
    }
    state={...state,order:'popular',choice:'popular',popularityIndex:'',page:1,offset:0,trail:[]};
    await loadPopularity(1,{recoverSource:true});
  }
  async function changeSort(order){
    if(!allowed() || state.kind==='following')return;
    if(pending || sorting)cancelLoad();
    state.choice=order;browse.choose(state.kind,order);
    if(manual()){state.waiting=true;error='';retryLoad=null;render();return;}
    await startLoad();
  }
  async function refresh(){
    if(pending || sorting)cancelLoad();
    await startLoad({refresh:true});
  }
  sortControl.querySelectorAll('[data-discovery-sort]').forEach(button=>button.onclick=()=>void changeSort((state.choice || state.order)==='popular'?'default':'popular'));
  sortControl.querySelector('[data-popularity-start]').onclick=()=>{
    const resuming=state.waiting && state.choice==='popular' && state.order==='popular' && state.popularityIndex;
    const refresh=!resuming && !refreshRequired.has(sourceKey(state)) && (state.kind==='ranking' || Boolean(data) && !state.waiting && state.choice===state.order);
    void startLoad({refresh});
  };
  function configure(value,loaded){
    const nextPreferences=loaded?{autoLoadPages:value.autoLoadPages,showLoadButton:value.showLoadButton,autoRanking:value.autoRanking,autoFollowing:value.autoFollowing,autoRecommended:value.autoRecommended,syncMinutes:value.syncMinutes}:null;
    if(JSON.stringify(preferences)===JSON.stringify(nextPreferences))return;
    preferences=nextPreferences;void stopBackground();
    if(active)render();
    if(loaded)void prepare({network:true});
  }
  const staleHistory=problem=>['cache_miss','cache_cleared','source_changed','ranking_changed','page_missing'].includes(problem.errorKind);
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
    error=message;retryLoad=()=>startLoad({refresh:true});
    render();
  }
  function clear(redraw=true){
    controller?.abort();revision++;settingsRetry=null;retryLoad=null;void stopBackground();reads.clear();interaction.clear();previews.clear();cache.clear();positions.clear();staged.clear();latest.clear();attempts.clear();sourceVersions.clear();followingDirty.clear();
    popularViews.clear();recommendationSizes.clear();startedSources.clear();pausedSources.clear();refreshRequired.clear();backgroundBatches.clear();viewedData=null;sorting=false;state=initial();data=null;rendered=undefined;pending=false;error='';restoreTop=0;grid.replaceChildren();if(active && redraw)render();
  }
  root.querySelectorAll('[data-discovery-tab]').forEach(button=>button.onclick=()=>void open({kind:button.dataset.discoveryTab}));
  root.querySelector('[data-discovery-scope]').onchange=e=>void open({scope:e.target.value});
  root.querySelectorAll('[data-discovery-rank]').forEach(button=>button.onclick=()=>void open({rank:button.dataset.discoveryRank}));
  root.querySelector('[data-discovery-mode]').onchange=e=>void open({mode:e.target.value});
  retryButton.onclick=()=>{if(!retryButton.hidden && allowed())void retryLoad?.();};
  update.onclick=()=>{
    if(pending || sorting || settingsRetry)return;
    const fresh=staged.get(sourceKey(state));if(!fresh)return;
    const choice=state.choice || browse.order(fresh.state.kind);
    if(choice==='popular' && fresh.state.kind!=='following'){void loadPopularity(1,{candidate:fresh});return;}
    controller?.abort();revision++;pending=false;sorting=false;error='';
    state={...structuredClone(fresh.state),choice,waiting:false};
    data=fresh.data;
    staged.delete(sourceKey(state));restoreTop=root.scrollTop;void startLoad();
  };
  next.onclick=()=>{if(next.disabled)return;remember();void load({pageTarget:{...state,offset:data.nextOffset,page:state.page+1,trail:[...state.trail,state.offset]}});};
  previous.onclick=()=>{if(previous.disabled)return;remember();const trail=state.trail.slice(),offset=trail.pop();void load({pageTarget:{...state,offset,page:state.page-1,trail}});};
  setInterval(()=>{void prepare({network:true});},30000);
  document.addEventListener('visibilitychange',markViewed);
  return {open,prepare,stopBackground,clear,configure,followingChanged,refresh,syncSelection,
    snapshot:()=>structuredClone({...state,waiting:state.waiting || pending || sorting}),
    suspend(){
      if(pending || (sorting && updateController!==controller))state.waiting=true;
      remember();active=false;controller?.abort();revision++;pending=false;sorting=false;interaction.reset({immediate:true});
    }};
};
