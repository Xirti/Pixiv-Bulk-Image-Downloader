(async () => {
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  const waitFor=async(fn,timeout=20000,label='condition')=>{const s=performance.now();while(performance.now()-s<timeout){try{const v=fn();if(v)return v}catch{}await wait(100)}throw new Error(`timeout ${label}`)};
  const fakeItems=[
    {id:'111111111',source:'pixiv',title:'回归合集 A',artist:'QA',tags:['回归'],pages:3,width:1000,height:800,bookmarks:1,date:'2026-07-13',description:'',thumb:'/api/image/1/0?size=preview',qualities:[{id:'regular',label:'常规',width:1000,height:800}],formats:[{id:'source',label:'源格式'}]},
    {id:'222222222',source:'pixiv',title:'回归合集 B',artist:'QA',tags:['回归'],pages:2,width:1000,height:800,bookmarks:2,date:'2026-07-13',description:'',thumb:'/api/image/2/0?size=preview',qualities:[{id:'regular',label:'常规',width:1000,height:800}],formats:[{id:'source',label:'源格式'}]}
  ];
  window.items=fakeItems;
  items=fakeItems;
  render();
  [...document.querySelectorAll('#grid [data-select]')].forEach(box=>{box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}))});
  document.querySelector('#openBatch').click();
  await waitFor(()=>!document.querySelector('#batchWorkspace').hidden&&document.querySelectorAll('#batchCollections .batch-collection').length===2,20000,'batch');
  const rows=[...document.querySelectorAll('#batchCollections .batch-collection')].map(x=>x.textContent.trim());
  const observed={};const real=window.fetch;
  window.fetch=async(input,init)=>{if(String(input).includes('/api/pixiv/batch-download')){observed.body=JSON.parse(init.body);return new Response(JSON.stringify({ok:true,saved:['a','b','c','d','e'],artworks:2,pages:5}),{status:200,headers:{'Content-Type':'application/json'}})}return real(input,init)};
  document.querySelector('#batchDownload').click();
  await waitFor(()=>!document.querySelector('#batchDownload').disabled&&/已保存/.test(document.querySelector('#toast').textContent),30000,'download');
  window.fetch=real;
  return {rows,selection:document.querySelector('#selectionCount').textContent,payload:observed.body,toast:document.querySelector('#toast').textContent};
})()
