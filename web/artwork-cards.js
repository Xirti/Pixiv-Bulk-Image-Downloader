"use strict";

globalThis.cloudActionButton = function(kind,id,full=false){
  const mark=kind==="bookmark"?'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20S3 14.5 3 8.5a4.5 4.5 0 0 1 9-1 4.5 4.5 0 0 1 9 1C21 14.5 12 20 12 20Z"/></svg>':'<span data-follow-mark aria-hidden="true">＋</span>';
  return `<button type="button" class="cloud-toggle${full?" cloud-full":""}" data-cloud-kind="${kind}" data-cloud-id="${id}" aria-label="${kind==="bookmark"?"收藏":"关注"}" aria-pressed="false">${mark}${full?`<span data-cloud-label>${kind==="bookmark"?"收藏":"关注"}</span>`:""}</button>`;
};

// Search and account collections keep exactly the same card markup.
globalThis.sensitiveImageMaskMarkup = function (label) {
  return `<span class="sensitive-image-mask"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M9.4 4.5A12 12 0 0 1 12 4c5.4 0 9 8 9 8a18 18 0 0 1-3 4.3M6.3 6.3A22 22 0 0 0 3 12s3.6 8 9 8a10 10 0 0 0 4.2-1M10 10a2.8 2.8 0 0 0 4 4"/></svg><span>${label}</span><small>点击显示图片</small></span>`;
};

globalThis.renderArtworkCards = function (rows, {escape, selected, maskSensitive=false}) {
  return rows.map((item,index)=>{
    const image=`<img data-gallery-src="${escape(item.thumb)}" data-preview-artwork="${escape(item.id)}" alt="${escape(item.title)}" loading="lazy" decoding="async">`;
    const count=item.pages>1?`<span class="gallery-page-count" title="共 ${item.pages} 张">${item.pages}</span>`:"";
    const rank=item.rank?`<span class="feed-position" title="${item.rankKind==="official"?"站方名次":"榜单来源序位，非站方名次字段"}">${item.rankKind==="official"?"#":"序位 "}${item.rank}</span>`:"";
    const masked=maskSensitive && item.restriction!=="safe";
    const mask=masked?sensitiveImageMaskMarkup(item.restriction==="r18"?"敏感内容":"分级待确认"):"";
    return `<article class="card gallery-card" data-i="${index}" data-sensitive-image="${escape(item.id)}"><label class="card-select"><input type="checkbox" data-select="${index}" ${selected(item.id)?"checked":""} aria-label="勾选 ${escape(item.title)}"></label><button class="poster${masked?" is-sensitive-masked":""}" type="button" aria-expanded="false" aria-label="${escape(item.title)}：单击展开或恢复，双击查看详情；空格展开，Enter 查看详情"${item.workType==="ugoira"?` data-ugoira-preview="${escape(item.id)}"`:""}>${image}${count}${mask}</button><div class="meta"><h3 title="${escape(item.title)}">${rank}${escape(item.title)}</h3>${cloudActionButton("bookmark",item.id)}<a class="gallery-author" href="#artist/${escape(item.userId || "")}" title="${escape(item.artist)}">${escape(item.artist)}</a>${item.userId?cloudActionButton("follow",item.userId):""}</div></article>`;
  }).join("");
};
