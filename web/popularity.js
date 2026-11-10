"use strict";

// The server advances a bounded metadata checkpoint on each read.
globalThis.readPopularityPage = async function (request, parameters, {signal,progress=()=>{}}={}) {
  let index=parameters.index || "";
  while(true){
    signal?.throwIfAborted();
    const query=new URLSearchParams({...parameters,index,...(index?{refresh:"0"}:{})});
    const result=await request(`/api/popularity?${query}`,{signal});
    signal?.throwIfAborted();
    index=result.index;progress(result);
    if(result.ready)return result;
  }
};

globalThis.popularitySortMarkup = function (attribute, value="default") {
  return `<div class="popularity-control"><button type="button" class="popularity-start" data-popularity-start hidden>开始加载</button><svg class="popularity-leader" viewBox="0 0 32 16" aria-hidden="true"><path d="M0 2h9l13 12h10"/></svg><div class="popularity-choice"><button type="button" class="popularity-sort" data-order="${value}" ${attribute} role="switch" aria-checked="${value==="popular"}" aria-label="按收藏数排序" title="切换排序方式"></button><span class="popularity-caption" data-popularity-caption>${value==="popular"?"按收藏数":"默认顺序"}</span></div></div>`;
};

globalThis.setPopularityLoad = function (root,manual,busy=false,visible=true,loaded=false) {
  const caption=root.querySelector("[data-popularity-caption]"),start=root.querySelector("[data-popularity-start]");
  if(!start)return;
  caption.hidden=false;start.hidden=!visible || !manual && !busy;start.disabled=false;
  start.setAttribute("aria-pressed",String(busy));
  const label=busy?"中断加载":loaded?"重新加载":"开始加载";if(start.textContent!==label)start.textContent=label;
};

globalThis.setPopularitySort = function (root, value, defaultLabel="默认顺序") {
  const control=root.querySelector(".popularity-sort");
  if(!control)return;
  control.dataset.order=value;
  control.setAttribute("aria-checked",String(value==="popular"));
  const caption=root.querySelector("[data-popularity-caption]");
  const label=value==="popular"?"按收藏数":defaultLabel;if(caption.textContent!==label)caption.textContent=label;
};
