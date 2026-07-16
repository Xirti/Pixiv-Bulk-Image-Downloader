(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, timeout=120000, label='condition') => { const s=performance.now(); let last; while(performance.now()-s<timeout){try{last=fn();if(last)return last;}catch(e){last=String(e)}await wait(150)}throw new Error(`timeout ${label}: ${last}`); };
  const search = async (tag='猫耳') => {
    document.querySelector('#tag').value=tag;
    document.querySelector('#workType').value='all';
    document.querySelector('#includeAi').checked=false;
    for(let attempt=1;attempt<=3;attempt++){
      document.querySelector('#searchForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
      await waitFor(()=>!document.querySelector('#searchForm button').disabled&&!document.querySelector('#grid .loading-state'),120000,`search ${attempt}`);
      if(document.querySelectorAll('#grid .card').length) return attempt;
      await wait(1500);
    }
    throw new Error(`search failed: ${document.querySelector('#grid')?.textContent}`);
  };
  await waitFor(()=>document.readyState==='complete'&&document.querySelector('#searchForm'),20000,'initial');
  const attempt=await search('猫耳');
  const cards=[...document.querySelectorAll('#grid .card')];
  if(cards.length<2) throw new Error('need two cards');
  const selections=[];
  for(const i of [0,1]){
    const box=cards[i].querySelector('[data-select]');
    box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));
    selections.push({i,title:cards[i].querySelector('h3')?.textContent,series:cards[i].querySelector('.series')?.textContent||null});
  }
  document.querySelector('#openBatch').click();
  await waitFor(()=>!document.querySelector('#batchWorkspace').hidden&&document.querySelectorAll('#batchCollections .batch-collection').length===2,20000,'batch');
  const batchRows=[...document.querySelectorAll('#batchCollections .batch-collection')].map(row=>row.textContent.trim());
  const observed={};
  const originalFetch=window.fetch;
  window.fetch=async (input,init)=>{
    const url=String(input);
    if(url.includes('/api/pixiv/batch-download')){
      observed.url=url;observed.body=JSON.parse(init?.body||'{}');
      return new Response(JSON.stringify({ok:true,saved:['probe-a','probe-b'],artworks:2,pages:observed.body.groups.reduce((n,g)=>n+g.pages.length,0)}),{status:200,headers:{'Content-Type':'application/json'}});
    }
    return originalFetch(input,init);
  };
  document.querySelector('#batchDownload').click();
  await waitFor(()=>!document.querySelector('#batchDownload').disabled&&/已保存/.test(document.querySelector('#toast')?.textContent||''),30000,'captured batch');
  window.fetch=originalFetch;
  return {attempt,count:document.querySelector('#count')?.textContent,cards:cards.length,selections,selectionCount:document.querySelector('#selectionCount')?.textContent,batchRows,capturedPayload:observed.body,toast:document.querySelector('#toast')?.textContent};
})()
