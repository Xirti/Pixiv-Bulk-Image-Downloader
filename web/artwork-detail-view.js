"use strict";

// One renderer/interaction owner; the two entry points supply only their DOM.
globalThis.createArtworkDetailView = function ({deck, hint, fields, escape, installImages, stage = null}) {
  let current=null;
  const actions=document.createElement("div");actions.className="cloud-detail-actions";
  fields.Tags.after(actions);
  function reset(){current=null;actions.replaceChildren();stage.reset();}
  function render(item, fallback = () => "", {preserve = false} = {}) {
    current=item;actions.innerHTML=cloudActionButton("bookmark",item.id,true)+(item.userId?cloudActionButton("follow",item.userId,true):"");
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
    return stage.render(item, {preserve});
  }
  return {render, reset, snapshot:()=>({item:current,stagePage:stage.state().page}), suspend: () => stage?.suspend(), setVisible: value => stage?.setVisible(value), clear: () => {current=null;actions.replaceChildren();stage.clear();},
    restorePage:index=>stage?.restorePage(index), state:()=>stage?.state() || {page:0}};
};
