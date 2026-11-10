"use strict";

// One renderer/interaction owner; the two entry points supply only their DOM.
globalThis.createArtworkDetailView = function ({deck, hint, fields, escape, installImages, stage = null, openOfficial = null, noticeBefore = null, isDownloaded = () => false}) {
  let current=null;
  const actions=document.createElement("div");actions.className="cloud-detail-actions";
  fields.Tags.after(actions);
  const notice=document.createElement("p"),message=document.createElement("span");
  notice.className="artwork-encouragement";
  notice.classList.toggle("single-only",fields.Tags.classList.contains("single-only"));
  notice.hidden=true;
  if(noticeBefore)noticeBefore.before(notice);else actions.after(notice);
  function refreshDownloadStatus(){
    message.textContent=current && isDownloaded(current)?"👍 下载成功，快去给画师点赞评论吧！":"👍 喜欢的话就去给画师点赞评论吧！";
  }
  let foldFrame=null;
  function measureFolds(){
    if(foldFrame!==null)return;
    foldFrame=requestAnimationFrame(()=>{
      foldFrame=null;
      const overflow=folds.map(fold=>fold.wrapper.classList.contains('is-open') || fold.field.clientWidth>0 && fold.field.scrollHeight>fold.field.clientHeight+1);
      folds.forEach((fold,index)=>{fold.wrapper.classList.toggle('is-truncated',overflow[index]);fold.button.hidden=!overflow[index];});
    });
  }
  const folds=[['Desc','简介'],['Tags','标签']].map(([name,label])=>{
    const field=fields[name],wrapper=document.createElement('div'),button=document.createElement('button');
    wrapper.className=`detail-fold detail-fold-${name.toLowerCase()}`;
    wrapper.classList.toggle('single-only',field.classList.contains('single-only'));
    button.type='button';button.className='detail-fold-toggle';button.hidden=true;
    button.setAttribute('aria-controls',field.id);button.setAttribute('aria-expanded','false');button.setAttribute('aria-label',`展开${label}`);
    button.innerHTML='<svg viewBox="0 0 18 12" aria-hidden="true"><path d="m3 3 6 6 6-6"/></svg>';
    field.before(wrapper);wrapper.append(field,button);
    button.onclick=()=>{
      const expanded=wrapper.classList.toggle('is-open');
      button.setAttribute('aria-expanded',String(expanded));button.setAttribute('aria-label',`${expanded?'收起':'展开'}${label}`);
    };
    new ResizeObserver(measureFolds).observe(field);
    return {field,wrapper,button,label};
  });
  function reset(){current=null;actions.replaceChildren();notice.hidden=true;notice.replaceChildren();stage.reset();}
  function render(item, fallback = () => "", {preserve = false} = {}) {
    const keepFolds=preserve && current?.id===item.id;
    folds.forEach(fold=>{
      if(!keepFolds){fold.wrapper.classList.remove('is-open');fold.button.setAttribute('aria-expanded','false');fold.button.setAttribute('aria-label',`展开${fold.label}`);}
      fold.wrapper.hidden=fold.field===fields.Desc?!item.description:!item.tags?.length;
    });
    current=item;actions.innerHTML=cloudActionButton("bookmark",item.id,true)+(item.userId?cloudActionButton("follow",item.userId,true):"");
    const official=document.createElement("a");official.className="artwork-source-link";
    official.href=`https://www.pixiv.net/artworks/${item.id}`;official.target="_blank";official.rel="noopener noreferrer";
    official.dataset.copyLink="";official.textContent=official.href;official.title=official.href;
    official.onclick=event=>{if(!event.button && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey)openOfficial?.(item.id,event);};
    notice.replaceChildren(message,official);notice.hidden=false;refreshDownloadStatus();
    fields.Title.textContent = item.title;
    fields.WorkType.hidden = item.workType !== "ugoira";
    fields.Desc.textContent = item.description;
    fields.Artist.textContent = item.artist;
    if(item.userId){
      const link=document.createElement("a");link.href=`#artist/${item.userId}`;link.dataset.artistId=item.userId;
      link.className="text-link";link.textContent=item.artist;fields.Artist.replaceChildren(link);
    }
    fields.Size.textContent = `${item.width} × ${item.height} px`;
    fields.Bookmarks.textContent = item.bookmarks == null ? "暂未获取" : Number(item.bookmarks).toLocaleString();
    fields.Date.textContent = item.date;
    fields.Tags.innerHTML = (item.tags || []).map(tag => `<a href="#tag/${encodeURIComponent(tag)}" data-tag-name="${escape(tag)}">#${escape(tag)}</a>`).join("");
    measureFolds();
    return stage.render(item, {preserve});
  }
  return {render, reset, refreshDownloadStatus, snapshot:()=>({item:current,stagePage:stage.state().page}), suspend: () => stage?.suspend(), setVisible: value => stage?.setVisible(value), clear: () => {current=null;actions.replaceChildren();notice.hidden=true;notice.replaceChildren();stage.clear();},
    restorePage:index=>stage?.restorePage(index), state:()=>stage?.state() || {page:0}};
};
