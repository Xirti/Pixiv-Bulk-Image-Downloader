(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, timeout, label) => { const s=performance.now(); while(performance.now()-s<timeout){let v;try{v=fn();if(v)return v;}catch{}await wait(150);}throw new Error(`timeout ${label}`); };
  document.querySelector('#tag').value='猫耳';
  document.querySelector('#workType').value='all';
  document.querySelector('#includeAi').checked=false;
  document.querySelector('#searchForm').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  await waitFor(()=>!document.querySelector('#searchForm button').disabled&&!document.querySelector('#grid .loading-state'),120000,'search');
  const cards=[...document.querySelectorAll('#grid .card')];
  const summaries=cards.map((card,i)=>({i,text:card.textContent.trim().slice(0,160),series:card.querySelector('.series')?.textContent||null,title:card.querySelector('h3')?.textContent||null}));
  const details=[];
  for(let i=0;i<Math.min(cards.length,36);i++){
    await window.select(i);
    await waitFor(()=>document.querySelector('#dTitle')?.textContent!=='正在加载作品详情…'&&!document.querySelector('#download').disabled,120000,`detail ${i}`);
    details.push({i,title:document.querySelector('#dTitle')?.textContent,pages:document.querySelectorAll('#collectionPages [data-collection-page]').length,toast:document.querySelector('#toast')?.textContent});
    if(details[details.length-1].pages>1) break;
  }
  return {count:document.querySelector('#count')?.textContent,cards:cards.length,summaries,details};
})()
