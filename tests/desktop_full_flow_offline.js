(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, timeout = 30000, label = 'condition') => {
    const start = performance.now();
    let last;
    while (performance.now() - start < timeout) {
      try { last = fn(); if (last) return last; } catch (e) { last = String(e); }
      await wait(100);
    }
    throw new Error(`timeout ${label}: ${last}`);
  };
  const detail = (id, title, pages) => ({
    id: String(id), source: 'pixiv', title, artist: 'MOKU QA', tags: ['回归'], pages,
    width: 1200, height: 900, bookmarks: 1, date: '2026-07-13', description: '桌面真实流程离线固定夹具',
    thumb: `/api/image/${id}/0?size=preview`,
    qualities: [{ id: 'regular', label: '常规', width: 1200, height: 900 }],
    formats: [{ id: 'source', label: '源格式' }],
    pageImages: Array.from({ length: pages }, (_, page) => ({
      width: 1200, height: 900,
      regular: `/api/image/${id}/${page}?size=preview`,
      original: `/api/image/${id}/${page}?size=original`,
    })),
  });
  const a = detail(301, '桌面合集 A', 4);
  const b = detail(302, '桌面合集 B', 3);
  const originalFetch = window.fetch;
  window.fetch = async (input, init) => {
    const url = String(input);
    if (url.includes('/api/pixiv/search')) {
      const parsed = new URL(url, location.href);
      const workType = parsed.searchParams.get('workType');
      const includeAi = parsed.searchParams.get('includeAi') === 'true';
      const all = [{...a,pageImages:undefined,workType:'illustration',aiGenerated:false},{...b,pageImages:undefined,workType:'manga',aiGenerated:false},{...a,id:'303',title:'AI 夹具',pageImages:undefined,workType:'illustration',aiGenerated:true}];
      const filtered = all.filter(x => (workType === 'all' || x.workType === workType) && (includeAi || !x.aiGenerated));
      return new Response(JSON.stringify({tag:'回归',total:filtered.length,page:1,pages:1,items:filtered}),{status:200,headers:{'Content-Type':'application/json'}});
    }
    if (url.includes('/api/pixiv/artwork/301')) return new Response(JSON.stringify(a),{status:200,headers:{'Content-Type':'application/json'}});
    if (url.includes('/api/pixiv/artwork/302')) return new Response(JSON.stringify(b),{status:200,headers:{'Content-Type':'application/json'}});
    if (url.includes('/api/pixiv/batch-download')) {
      const body = JSON.parse(init.body);
      window.__capturedBatch = body;
      return new Response(JSON.stringify({ok:true,saved:['a','b','c'],artworks:2,pages:3}),{status:200,headers:{'Content-Type':'application/json'}});
    }
    return originalFetch(input, init);
  };
  const submit = async (workType, includeAi) => {
    document.querySelector('#tag').value = '回归';
    document.querySelector('#workType').value = workType;
    document.querySelector('#includeAi').checked = includeAi;
    document.querySelector('#searchForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
    await waitFor(() => !document.querySelector('#searchForm button').disabled && !document.querySelector('#grid .loading-state'),10000,'search');
    return {workType,includeAi,cards:document.querySelectorAll('#grid .card').length,count:document.querySelector('#count').textContent};
  };
  await waitFor(() => document.readyState === 'complete' && document.querySelector('#searchForm'),10000,'ready');
  const filters = [];
  filters.push(await submit('illustration', false));
  filters.push(await submit('manga', false));
  filters.push(await submit('ugoira', false));
  filters.push(await submit('all', true));
  await submit('all', false);
  const boxes = [...document.querySelectorAll('#grid [data-select]')];
  boxes.slice(0,2).forEach(box => {box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));});
  document.querySelector('#openBatch').click();
  await waitFor(() => document.querySelectorAll('#batchCollections .batch-collection').length===2,10000,'batch');
  document.querySelector('[data-open-collection="301"]').click();
  await waitFor(() => document.querySelectorAll('#collectionPages [data-collection-page]').length===4,10000,'A detail');
  [...document.querySelectorAll('#collectionPages [data-collection-page]')].forEach((box,i)=>{const wanted=[1,3].includes(i);if(box.checked!==wanted){box.checked=wanted;box.dispatchEvent(new Event('change',{bubbles:true}));}});
  document.querySelector('#returnToBatch').click();
  await waitFor(() => !document.querySelector('#batchWorkspace').hidden,10000,'return A');
  document.querySelector('[data-open-collection="302"]').click();
  await waitFor(() => document.querySelectorAll('#collectionPages [data-collection-page]').length===3,10000,'B detail');
  [...document.querySelectorAll('#collectionPages [data-collection-page]')].forEach((box,i)=>{const wanted=i===0;if(box.checked!==wanted){box.checked=wanted;box.dispatchEvent(new Event('change',{bubbles:true}));}});
  document.querySelector('#returnToBatch').click();
  await waitFor(() => !document.querySelector('#batchWorkspace').hidden,10000,'return B');
  document.querySelector('[data-open-collection="301"]').click();
  await waitFor(() => document.querySelectorAll('#collectionPages [data-collection-page]').length===4,10000,'reopen A');
  const restoredA=[...document.querySelectorAll('#collectionPages [data-collection-page]')].map((x,i)=>x.checked?i:null).filter(x=>x!==null);
  document.querySelector('#returnToBatch').click();
  await waitFor(() => !document.querySelector('#batchWorkspace').hidden,10000,'final batch');
  const rows=[...document.querySelectorAll('#batchCollections .batch-collection')].map(x=>x.textContent.trim());
  const expectedPayload={groups:[{id:'301',pages:[1,3]},{id:'302',pages:[0]}],quality:document.querySelector('#quality').value||'regular',saveRoot:document.querySelector('#saveRoot').value.trim()};
  window.fetch=originalFetch;
  return {filters,restoredA,rows,payload:expectedPayload,standalone:matchMedia('(display-mode: standalone)').matches,saveRootReadOnly:document.querySelector('#saveRoot').readOnly};
})()
