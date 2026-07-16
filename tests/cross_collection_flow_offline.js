(async () => {
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const waitFor=async(fn,timeout=30000,label='condition')=>{const s=performance.now();let last;while(performance.now()-s<timeout){try{last=fn();if(last)return last}catch(e){last=String(e)}await wait(100)}throw new Error(`timeout ${label}: ${last}`)};
  const page=(id,n)=>({width:1200,height:900,regular:`/api/image/${id}/0?size=preview`,original:`/api/image/${id}/0?size=original`});
  const detail=(id,title,count)=>({id:String(id),source:'pixiv',title,artist:'QA',tags:['回归'],pages:count,width:1200,height:900,bookmarks:1,date:'2026-07-13',description:'跨合集状态回归',thumb:`/api/image/${id}/0?size=preview`,qualities:[{id:'regular',label:'常规',width:1200,height:900}],formats:[{id:'source',label:'源格式'}],pageImages:Array.from({length:count},(_,i)=>page(id+i,i))});
  const a=detail(101,'跨合集 A',4),b=detail(202,'跨合集 B',3);
  const realFetch=window.fetch;
  window.fetch=async(input,init)=>{const u=String(input);if(u.includes('/api/pixiv/artwork/101'))return new Response(JSON.stringify(a),{status:200,headers:{'Content-Type':'application/json'}});if(u.includes('/api/pixiv/artwork/202'))return new Response(JSON.stringify(b),{status:200,headers:{'Content-Type':'application/json'}});return realFetch(input,init)};
  items=[{...a,pageImages:undefined},{...b,pageImages:undefined}];render();
  const boxes=[...document.querySelectorAll('#grid [data-select]')];boxes.forEach(x=>{x.checked=true;x.dispatchEvent(new Event('change',{bubbles:true}))});
  document.querySelector('#openBatch').click();await waitFor(()=>document.querySelectorAll('#batchCollections .batch-collection').length===2,10000,'batch');
  document.querySelector('[data-open-collection="101"]').click();await waitFor(()=>document.querySelectorAll('#collectionPages [data-collection-page]').length===4,20000,'A detail');
  const aBoxes=[...document.querySelectorAll('#collectionPages [data-collection-page]')];aBoxes.forEach((x,i)=>{const wanted=[1,3].includes(i);if(x.checked!==wanted){x.checked=wanted;x.dispatchEvent(new Event('change',{bubbles:true}))}});
  const aChosen=aBoxes.map((x,i)=>x.checked?i:null).filter(x=>x!==null);
  document.querySelector('#returnToBatch').click();await waitFor(()=>!document.querySelector('#batchWorkspace').hidden,10000,'return A');
  document.querySelector('[data-open-collection="202"]').click();await waitFor(()=>document.querySelectorAll('#collectionPages [data-collection-page]').length===3,20000,'B detail');
  const bBoxes=[...document.querySelectorAll('#collectionPages [data-collection-page]')];bBoxes.forEach((x,i)=>{const wanted=i===0;if(x.checked!==wanted){x.checked=wanted;x.dispatchEvent(new Event('change',{bubbles:true}))}});
  document.querySelector('#returnToBatch').click();await waitFor(()=>!document.querySelector('#batchWorkspace').hidden,10000,'return B');
  document.querySelector('[data-open-collection="101"]').click();await waitFor(()=>document.querySelectorAll('#collectionPages [data-collection-page]').length===4,10000,'reopen A');
  const restoredA=[...document.querySelectorAll('#collectionPages [data-collection-page]')].map((x,i)=>x.checked?i:null).filter(x=>x!==null);
  document.querySelector('#returnToBatch').click();await waitFor(()=>!document.querySelector('#batchWorkspace').hidden,10000,'final batch');
  const rows=[...document.querySelectorAll('#batchCollections .batch-collection')].map(x=>x.textContent.trim());
  const captured={};window.fetch=async(input,init)=>{if(String(input).includes('/api/pixiv/batch-download')){captured.body=JSON.parse(init.body);return new Response(JSON.stringify({ok:true,saved:['a','b','c'],artworks:2,pages:3}),{status:200,headers:{'Content-Type':'application/json'}})}return realFetch(input,init)};
  document.querySelector('#batchDownload').click();await waitFor(()=>/已保存/.test(document.querySelector('#toast').textContent),20000,'download');window.fetch=realFetch;
  return {aChosen,restoredA,bChosen:[0],rows,payload:captured.body,toast:document.querySelector('#toast').textContent};
})()
