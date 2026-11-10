"use strict";

globalThis.createDownloadStatistics = function ({root, request}) {
  let active=false, data=null, dirty=true, year=null, controller=null, revision=0, updatedAt=0,refreshTimer=null,historyRevision=0;
  const esc=value=>String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  const number=value=>Number(value).toLocaleString();
  const status=root.querySelector("[data-statistics-status]"),body=root.querySelector("[data-statistics-body]");
  function render(){
    const total=data.totals, max=Math.max(1,...data.calendar.map(day=>day.pages));
    const leading=new Date(`${data.calendar[0].day}T00:00:00Z`).getUTCDay();
    let lastMonth=-1;
    const cells=data.calendar.map(day=>{
      const value=day.pages,level=value?Math.max(1,Math.ceil(value/max*4)):0;
      return `<span class="download-heat-cell" data-level="${level}" data-statistics-day="${day.day}" data-statistics-count="${value}" aria-label="${day.day}：${number(value)} 张"></span>`;
    }).join("");
    const weeks=Math.ceil((leading+data.calendar.length)/7);
    const months=Array.from({length:weeks},(_,index)=>{
      const date=new Date(`${data.calendar[0].day}T00:00:00Z`);
      date.setUTCDate(date.getUTCDate()+Math.max(0,index*7-leading));
      const month=date.getUTCMonth();
      const label=month!==lastMonth?`${month+1}月`:"";lastMonth=month;
      return `<span>${label}</span>`;
    }).join("");
    const trendMax=Math.max(1,...data.trend.map(day=>day.pages));
    const trendTotal=data.trend.reduce((sum,day)=>sum+day.pages,0);
    const typeNames={illustration:"插画",illust:"插画",manga:"漫画",ugoira:"动图"};
    body.innerHTML=`<div class="download-stat-totals">${[[total.pages,"下载图片"],[total.files,"保存文件"],[total.artworks,"不同作品"],[total.activeDays,"活跃天数"]].map(([value,label])=>`<div><strong>${number(value)}</strong><span>${label}</span></div>`).join("")}</div>
      <section class="download-stat-card"><header><h4>下载日历</h4><select data-statistics-year aria-label="统计年份"><option value="" ${data.year===null?"selected":""}>近一年</option>${data.years.map(value=>`<option value="${value}" ${value===data.year?"selected":""}>${value} 年</option>`).join("")}</select></header>
      <div class="download-heat-scroll"><div class="download-heat-grid" style="--heat-weeks:${weeks}" aria-label="${data.year || '近一年'} 每日下载图片数"><span class="download-heat-spacer" style="grid-row:span ${leading || 1}" ${leading?"":"hidden"}></span>${cells}</div><div class="download-heat-months" style="--heat-weeks:${weeks}">${months}</div></div>
      <footer><span>${number(data.calendar.reduce((sum,day)=>sum+day.pages,0))} 张 · ${data.year?`${data.year} 年`:"近一年"}</span><span class="download-heat-key">少 ${[0,1,2,3,4].map(level=>`<i class="download-heat-cell" data-level="${level}"></i>`).join("")} 多</span></footer></section>
      <div class="download-stat-columns"><section class="download-stat-card"><header><h4>近 30 天</h4><span>${number(trendTotal)} 张</span></header><div class="download-trend" role="img" aria-label="近 30 天共下载 ${number(trendTotal)} 张图片">${data.trend.map(day=>`<span title="${day.day} · ${number(day.pages)} 张"><i style="height:${day.pages?Math.max(3,day.pages/trendMax*100):0}%"></i></span>`).join("")}</div><footer><span>${data.trend[0].day.slice(5)}</span><span>${data.trend.at(-1).day.slice(5)}</span></footer></section>
      <section class="download-stat-card"><header><h4>常下载画师</h4><span>图片数</span></header><div class="download-artists">${data.artists.map(row=>`<div class="download-artist-row"><span title="${esc(row.name)}">${esc(row.name)}</span><span class="download-stat-bar"><i style="width:${row.pages/Math.max(1,data.artists[0].pages)*100}%"></i></span><b>${number(row.pages)}</b></div>`).join("") || '<p class="download-stat-empty">下载后会在这里留下记录</p>'}</div></section></div>
      <div class="download-stat-types">${data.types.map(row=>`<span>${esc(typeNames[row.type] || "其他")} <b>${number(row.pages)}</b></span>`).join("")}</div>
      <details class="download-stat-method"><summary>本机记录 · 统计口径</summary><p>按 UTC 日期汇总成功保存的图片页和文件；重复下载再次计入，已存在而跳过的图片不计入。作品按 ID 去重，画师按历史署名归类。</p><p>依据当前保留的 ${number(total.records)} 条记录，最多 ${number(data.limit)} 条。删除、清空或自动淘汰历史后，统计随之变化，不代表终身总量；文件数量也不代表当前磁盘占用。</p></details>`;
    body.querySelector("[data-statistics-year]").onchange=event=>{year=event.target.value?Number(event.target.value):null;void load(true);};
  }
  async function load(force=false){
    if(!active)return;
    if(refreshTimer!==null){clearTimeout(refreshTimer);refreshTimer=null;}
    if(controller && !force)return;
    if(!force && !dirty && data && data.year===year && Date.now()-updatedAt<60000)return;
    controller?.abort();const current=++revision,changes=historyRevision,abort=new AbortController();controller=abort;
    root.setAttribute("aria-busy","true");status.textContent=data?"正在更新…":"正在读取本机记录…";
    try{
      const value=await request(`/api/library/statistics${year?`?year=${year}`:""}`,{signal:abort.signal});
      if(current!==revision || !active)return;
      data=value;year=value.year;dirty=changes!==historyRevision;updatedAt=Date.now();render();status.textContent=data.firstDay?`${data.firstDay} — ${data.lastDay}`:"还没有下载记录";
    }catch(error){
      if(current!==revision || abort.signal.aborted)return;
      status.textContent=error.message || "统计读取失败，可右键刷新重试";
    }finally{if(current===revision){controller=null;root.setAttribute("aria-busy","false");}}
  }
  const tooltip=document.createElement("div");tooltip.className="download-stat-tooltip";tooltip.hidden=true;tooltip.setAttribute("role","tooltip");root.append(tooltip);
  root.addEventListener("pointerover",event=>{
    const cell=event.target.closest("[data-statistics-day]");
    if(!cell){tooltip.hidden=true;return;}
    const bounds=cell.getBoundingClientRect();
    tooltip.textContent=`${cell.dataset.statisticsDay} · ${number(cell.dataset.statisticsCount)} 张`;
    tooltip.style.left=`${Math.max(100,Math.min(innerWidth-100,bounds.left+bounds.width/2))}px`;
    tooltip.style.top=`${Math.max(8,bounds.top-36)}px`;tooltip.hidden=false;
  });
  root.addEventListener("pointerleave",()=>{tooltip.hidden=true;});
  document.addEventListener("scroll",()=>{tooltip.hidden=true;},true);
  function invalidate(){
    dirty=true;historyRevision++;
    if(!active)return;
    if(refreshTimer!==null)clearTimeout(refreshTimer);
    refreshTimer=setTimeout(()=>{refreshTimer=null;void load(true);},200);
  }
  addEventListener("moku-download-history-changed",invalidate);
  return {setVisible(value){
    const entering=value && !active;
    const hidden=!value;
    if(root.hidden!==hidden)root.hidden=hidden;
    if(hidden){tooltip.hidden=true;if(refreshTimer!==null){clearTimeout(refreshTimer);refreshTimer=null;}}
    if(!value && active){revision++;controller?.abort();controller=null;root.setAttribute("aria-busy","false");}
    active=value;if(entering)void load();
  },refresh:()=>load(true)};
};
