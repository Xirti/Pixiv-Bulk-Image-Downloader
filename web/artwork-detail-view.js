"use strict";

// One renderer/interaction owner; the two entry points supply only their DOM.
globalThis.createArtworkDetailView = function ({deck, hint, fields, escape, installImages}) {
  let lockedPage = null;
  const cards = () => [...deck.querySelectorAll(".deck-card")];
  function reset() {
    lockedPage = null;
    cards().forEach(card => {
      card.classList.remove("deck-preview", "deck-locked", "deck-inert");
      card.setAttribute("aria-pressed", "false");
    });
  }
  function toggle(card) {
    const page = Number(card.dataset.page);
    if (lockedPage !== null && lockedPage !== page) return;
    if (lockedPage === page) {
      reset();
      hint.textContent = "已取消固定；轻移鼠标预览，点击一张可再次固定";
      return;
    }
    lockedPage = page;
    cards().forEach(row => {
      const selected = Number(row.dataset.page) === page;
      row.classList.remove("deck-preview");
      row.classList.toggle("deck-locked", selected);
      row.classList.toggle("deck-inert", !selected);
      row.setAttribute("aria-pressed", String(selected));
    });
    hint.textContent = `已固定第 ${page + 1} 张；其他牌保持不动，再点当前牌取消`;
  }
  function render(item, fallback = () => "") {
    reset();
    fields.Title.textContent = item.title;
    fields.WorkType.hidden = item.workType !== "ugoira";
    fields.Desc.textContent = item.description;
    fields.Artist.textContent = item.artist;
    fields.Size.textContent = `${item.width} × ${item.height} px`;
    fields.Bookmarks.textContent = item.bookmarks == null ? "暂未获取" : Number(item.bookmarks).toLocaleString();
    fields.Date.textContent = item.date;
    fields.Tags.innerHTML = (item.tags || []).map(tag => `<span>#${escape(tag)}</span>`).join("");
    const visible = Math.min(item.pages, 4), middle = (visible - 1) / 2;
    deck.innerHTML = Array.from({length: visible}, (_, page) => {
      const delta = page - middle;
      const src = item.pageImages?.[page]?.regular || item.thumb || fallback(page);
      return `<button class="deck-card" data-page="${page}" style="--i:${page};--angle:${delta * 3.2}deg;--lift:${Math.abs(delta) * 4}px" aria-label="第 ${page + 1} 张" aria-pressed="false"><img src="${escape(src)}" data-detail-artwork="${escape(item.id)}" data-detail-page="${page}" alt="${escape(item.title)} 第 ${page + 1} 张" loading="lazy" decoding="async"><span>${page + 1} / ${item.pages}</span></button>`;
    }).join("");
    installImages(deck);
    cards().forEach(card => {
      card.onmouseenter = () => {
        if (lockedPage !== null) return;
        reset();
        card.classList.add("deck-preview");
      };
      card.onmouseleave = () => { if (lockedPage === null) reset(); };
      card.onclick = () => toggle(card);
    });
    hint.textContent = item.pages > visible
      ? `预览前 ${visible} 张，共 ${item.pages} 张；点击一张固定，再点一次取消`
      : "轻移鼠标预览；点击一张固定，再点一次取消";
    return visible;
  }
  return {render, reset};
};
