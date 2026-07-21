let items = [];
let activeArtworkId = null;
let currentPage = 1;
let pageNumbers = [1];
let firstAvailablePage = 1;
let preloadedThrough = 1;
let activeTagQuery = "猫耳";
let searchController = null;
let detailController = null;
let requestTokenPromise = null;
let viewGeneration = 0;
let lockedDeckPage = null;
let pendingNavigationPage = null;
let activeSearchContext = { kind: "tags", value: "猫耳" };
let currentDetailItem = null;
let collectionPageOffset = 0;
let batchCandidateItems = [];
const batchCandidateContextByArtwork = new Map();
const batchCandidateResultPageByArtwork = new Map();
const MAX_SELECTED_ARTWORKS = 100;
const MAX_SELECTED_PAGES = 1000;
const DOWNLOAD_CHUNK_ARTWORKS = 20;
const DOWNLOAD_CHUNK_PAGES = 200;
const DETAIL_PAGE_WINDOW = 48;
const SEARCH_KEEP_BEHIND = 6;
const selectedArtworkIds = new Set();
const selectedArtworks = new Map();
const selectedPagesByArtwork = new Map();
const selectedContextByArtwork = new Map();
const selectedResultPageByArtwork = new Map();
const archivedArtworkIds = new Set();

const $ = (selector) => document.querySelector(selector);
const grid = $("#grid");
const gallery = $("#gallery");
const searchButton = $("#searchForm button");
document.documentElement.classList.add("conservative");

const esc = (value) => String(value).replace(
  /[&<>"']/g,
  (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
);

async function getRequestToken() {
  if (!requestTokenPromise) {
    requestTokenPromise = fetch("/api/health", {
      cache: "no-store",
      headers: { "Sec-Fetch-Site": "same-origin" },
    })
      .then((response) => {
        if (!response.ok) throw new Error("本机服务未准备好");
        return response.json();
      })
      .then((data) => {
        if (data.protocolVersion !== 5 || data.applicationId !== "MOKU.PixivTagGallery") throw new Error("MOKU 后端版本过旧，请关闭当前窗口并重新启动 MOKU");
        if (!data.requestToken) throw new Error("本机请求授权未初始化");
        return data.requestToken;
      })
      .catch((error) => {
        requestTokenPromise = null;
        throw error;
      });
  }
  return requestTokenPromise;
}

async function fetchJson(url, options = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const upstream = options.signal;
  let timedOut = false;
  const relayAbort = () => controller.abort(upstream.reason);

  if (upstream) {
    if (upstream.aborted) relayAbort();
    else upstream.addEventListener("abort", relayAbort, { once: true });
  }

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const headers = new Headers(options.headers || {});
    headers.set("X-MOKU-Request-Token", await getRequestToken());
    const response = await fetch(url, { ...options, headers, signal: controller.signal });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      throw new Error("服务返回了无法解析的数据");
    }
    if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
    return data;
  } catch (error) {
    if (timedOut) throw new Error("请求超时，请检查 VPN / 系统代理后重试");
    throw error;
  } finally {
    clearTimeout(timer);
    if (upstream) upstream.removeEventListener("abort", relayAbort);
  }
}

function installImageFallbacks(root = document) {
  root.querySelectorAll("img").forEach((img) => {
    if (img.dataset.fallbackReady === "1") return;
    img.dataset.fallbackReady = "1";
    img.addEventListener("error", () => {
      img.removeAttribute("src");
      img.classList.add("image-unavailable");
      img.closest(".poster,.batch-collection,.page-select,.deck-card,figure")?.classList.add("image-unavailable");
    }, { once: true });
  });
}

function clearDetail(message = "选择一件作品查看详情") {
  document.body.classList.remove("collection-basket-open");
  $("#detail").hidden = false;
  lockedDeckPage = null;
  activeArtworkId = null;
  $("#dTitle").textContent = message;
  $("#dDesc").textContent = "";
  $("#dArtist").textContent = "—";
  $("#dSize").textContent = "—";
  $("#dBookmarks").textContent = "—";
  $("#dDate").textContent = "—";
  $("#dTags").innerHTML = "";
  $("#deck").innerHTML = "";
  $("#collectionPages").innerHTML = "";
  $("#collectionPageMore").hidden = true;
  $("#returnToBatch").hidden = true;
  $("#deckHint").textContent = "点击搜索结果后加载作品详情";
  $("#quality").innerHTML = "";
  $("#format").innerHTML = "";
  $("#qualityText").textContent = "";
  $("#formatText").textContent = "";
  $("#formatHint").textContent = "";
  $("#viewAll").hidden = true;
  $("#download").disabled = true;
  $("#batchWorkspace").hidden = true;
}

function updateSelectionBar() {
  const count = selectedArtworkIds.size;
  const pages = selectedPageCount();
  $("#selectionBar").hidden = items.length === 0 && count === 0;
  $("#selectionCount").textContent = count
    ? `采集篮 ${count}/${MAX_SELECTED_ARTWORKS} 个作品 · ${pages}/${MAX_SELECTED_PAGES} 张图片${archivedArtworkIds.size ? ` · 已归档 ${archivedArtworkIds.size}` : ""}`
    : `当前页 ${items.length} 个作品`;
  $("#clearSelection").disabled = count === 0;
}

function selectedPageCount() {
  return [...selectedArtworkIds].reduce(
    (total, id) => total + (selectedPagesByArtwork.get(id)?.size || 0),
    0,
  );
}

function unarchivedSelectionIds() {
  return new Set([...selectedArtworkIds].filter((id) => !archivedArtworkIds.has(id)));
}

function detachSelection(ids) {
  ids.forEach((id) => archivedArtworkIds.add(id));
  updateSelectionBar();
  return ids.size;
}

function clearSelection(ids) {
  ids.forEach((id) => {
    selectedArtworkIds.delete(id);
    selectedArtworks.delete(id);
    selectedPagesByArtwork.delete(id);
    selectedContextByArtwork.delete(id);
    selectedResultPageByArtwork.delete(id);
  });
  updateSelectionBar();
  return ids.size;
}

function clearAllSelection() {
  selectedArtworkIds.clear();
  selectedArtworks.clear();
  selectedPagesByArtwork.clear();
  selectedContextByArtwork.clear();
  selectedResultPageByArtwork.clear();
  archivedArtworkIds.clear();
  batchCandidateItems = [];
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  updateSelectionBar();
}

function toggleArtworkSelection(item, checked) {
  if (checked) {
    if (!selectedArtworkIds.has(item.id) && selectedArtworkIds.size >= MAX_SELECTED_ARTWORKS) {
      $("#toast").textContent = `一次最多选择 ${MAX_SELECTED_ARTWORKS} 个作品`;
      return false;
    }
    const existingPages = selectedPagesByArtwork.get(item.id);
    const additionalPages = existingPages ? 0 : item.pages;
    if (selectedPageCount() + additionalPages > MAX_SELECTED_PAGES) {
      $("#toast").textContent = `一次最多选择 ${MAX_SELECTED_PAGES} 张图片`;
      return false;
    }
    selectedArtworkIds.add(item.id);
    selectedArtworks.set(item.id, item);
    selectedContextByArtwork.set(item.id, { ...activeSearchContext });
    selectedResultPageByArtwork.set(item.id, currentPage);
    if (!selectedPagesByArtwork.has(item.id)) {
      selectedPagesByArtwork.set(item.id, new Set(Array.from({ length: item.pages }, (_, page) => page)));
    }
  } else {
    selectedArtworkIds.delete(item.id);
    selectedArtworks.delete(item.id);
    selectedPagesByArtwork.delete(item.id);
    selectedContextByArtwork.delete(item.id);
    selectedResultPageByArtwork.delete(item.id);
    archivedArtworkIds.delete(item.id);
  }
  updateSelectionBar();
  return true;
}

function selectAllCurrentPage() {
  const additionalPages = items.reduce((total, item) => {
    const allPages = new Set(Array.from({ length: item.pages }, (_, page) => page));
    const existingPages = selectedPagesByArtwork.get(item.id) || new Set();
    return total + [...allPages].filter((page) => !existingPages.has(page)).length;
  }, 0);
  const additionalArtworks = items.filter((item) => !selectedArtworkIds.has(item.id)).length;
  const status = $("#pageSelectionStatus");
  if (selectedArtworkIds.size + additionalArtworks > MAX_SELECTED_ARTWORKS) {
    status.textContent = `无法全选：采集篮最多 ${MAX_SELECTED_ARTWORKS} 个作品`;
    return false;
  }
  if (selectedPageCount() + additionalPages > MAX_SELECTED_PAGES) {
    status.textContent = `无法全选：采集篮最多 ${MAX_SELECTED_PAGES} 张图片`;
    return false;
  }
  for (const item of items) {
    toggleArtworkSelection(item, true);
    const allPages = new Set(Array.from({ length: item.pages }, (_, page) => page));
    selectedPagesByArtwork.set(item.id, allPages);
  }
  updateSelectionBar();
  status.textContent = `已全选当前页 ${items.length} 个作品及其全部图片`;
  render();
  return true;
}

function clearAllCurrentPage() {
  for (const item of items) toggleArtworkSelection(item, false);
  $("#pageSelectionStatus").textContent = "已取消当前页全部选择";
  render();
}

async function search(tag, page = 1) {
  if (searchController) searchController.abort();
  viewGeneration += 1;
  if (detailController) detailController.abort();
  searchController = new AbortController();
  const controller = searchController;
  const cleanTag = String(tag || "").trim() || "原创";
  activeTagQuery = cleanTag;
  const mode = $("#safety").value || "safe";
  const contextMatch = cleanTag.match(/^\s*(pid|author)\s*[:：]\s*(.+)$/i);
  activeSearchContext = contextMatch
    ? { kind: contextMatch[1].toLowerCase(), value: contextMatch[2].trim() }
    : { kind: "tags", value: cleanTag };

  searchButton.disabled = true;
  searchButton.textContent = "正在寻找…";
  grid.innerHTML = '<p class="loading-state">正在连接 Pixiv，可随时继续操作页面…</p>';
  $("#pagination").innerHTML = "";
  $("#count").textContent = "正在加载当前页";

  try {
    const query = new URLSearchParams({
      tag: cleanTag,
      page: String(page),
      mode,
      workType: $("#workType").value || "all",
      includeAi: String($("#includeAi").checked),
      fuzzy: String(Boolean($("#fuzzySearch")?.checked)),
    });
    const data = await fetchJson(`/api/pixiv/search?${query}`, { signal: controller.signal }, 90000);
    if (controller !== searchController) return;

    items = Array.isArray(data.items) ? data.items : [];
    currentPage = Number(data.page) || 1;
    pageNumbers = Array.isArray(data.availablePages) ? data.availablePages : (Array.isArray(data.pageNumbers) ? data.pageNumbers : [currentPage]);
    firstAvailablePage = pageNumbers.length ? Number(pageNumbers[0]) : currentPage;
    preloadedThrough = Number(data.preloadedThrough) || currentPage;
    $("#tagTitle").textContent = data.label || (Array.isArray(data.tags) && data.tags.length ? data.tags.join(" + ") : (data.tag || cleanTag));
    const preloadStatus = data.preloadedThrough > currentPage ? ` · 已预加载至第 ${data.preloadedThrough} 页` : "";
    const historyStatus = data.budgetExhausted ? " · 本次加载达到请求预算，可继续翻页" : (data.hasMore ? " · 可继续加载更早作品" : " · 已到历史末尾");
    const fuzzyLabel = data.fuzzy ? " · 别名扩展已启用" : "";
    $("#count").textContent = `已加载 ${data.total} 件 · 第 ${currentPage} 页 · 每页 ${data.perPage || 36} 件${fuzzyLabel}${preloadStatus}${historyStatus}${data.truncatedDates?.length ? ` · ${data.truncatedDates.length} 个高密度日期受平台截断` : ""}`;
    render();
    renderPagination();
    clearDetail();
  } catch (error) {
    if (controller.signal.aborted) return;
    grid.innerHTML = `<div class="error-state"><b>加载失败</b><p>${esc(error.message || "Pixiv 搜索失败")}</p><button id="retrySearch" type="button">重试当前搜索</button></div>`;
    $("#count").textContent = "连接未完成";
    $("#retrySearch")?.addEventListener("click", () => search(cleanTag, page));
  } finally {
    if (controller === searchController) {
      searchController = null;
      searchButton.disabled = false;
      searchButton.innerHTML = "开始寻找 <span>↗</span>";
    }
  }
}

function syncResultSelectionControls() {
  grid.querySelectorAll("[data-select]").forEach((box) => {
    const item = items[Number(box.dataset.select)];
    box.checked = Boolean(item && selectedArtworkIds.has(item.id));
  });
}

function render() {
  gallery.classList.add("in");
  grid.className = "grid";
  updateSelectionBar();
  if (!items.length) {
    grid.innerHTML = '<p class="empty-state">当前页没有符合安全范围的作品。</p>';
    return;
  }

  grid.innerHTML = items.map((item, index) => {
    const image = `<img src="${item.thumb}" alt="${esc(item.title)}" loading="lazy" decoding="async">`;
    const checked = selectedArtworkIds.has(item.id) ? "checked" : "";
    return `<article class="card" tabindex="0" data-i="${index}"><label class="card-select"><input type="checkbox" data-select="${index}" ${checked}><span>选择</span></label><div class="poster">${image}${item.pages > 1 ? `<span class="series">叠图 ${item.pages}P</span>` : ""}</div><div class="meta"><div><h3>${esc(item.title)}</h3><p>${esc(item.artist)} · ${item.tags.map((tag) => `#${esc(tag)}`).join(" ")}</p></div><span>♡ ${Number(item.bookmarks || 0).toLocaleString()}</span></div></article>`;
  }).join("");
  installImageFallbacks(grid);

  grid.querySelectorAll("[data-i]").forEach((card) => {
    const open = () => {
      select(Number(card.dataset.i));
      $("#detail").scrollIntoView({ behavior: "auto" });
    };
    card.onclick = (event) => { if (!event.target.closest(".card-select")) open(); };
    card.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    };
  });
  grid.querySelectorAll("[data-select]").forEach((box) => {
    box.onchange = () => {
      const accepted = toggleArtworkSelection(items[Number(box.dataset.select)], box.checked);
      if (!accepted) box.checked = false;
    };
  });
}

function renderPagination() {
  const pagination = $("#pagination");
  pagination.innerHTML = `<button ${currentPage <= firstAvailablePage ? "disabled" : ""} data-page="${currentPage - 1}" aria-label="上一页">←</button>${pageNumbers.map((number) => number === null ? '<span class="page-gap">…</span>' : `<button class="${number === currentPage ? "active" : ""}" data-page="${number}">${number}</button>`).join("")}<button ${currentPage >= preloadedThrough ? "disabled" : ""} data-page="${currentPage + 1}" aria-label="下一页">→</button>`;
  pagination.querySelectorAll("button:not([disabled])").forEach((button) => {
    button.onclick = () => {
      navigateToPage(Number(button.dataset.page));
    };
  });
  updatePaginationDock();
}

function updatePaginationDock() {
  const dock = document.querySelector(".pagination-dock");
  const gallery = $("#gallery");
  dock.classList.toggle("is-visible", Boolean($("#pagination").children.length) && gallery.getBoundingClientRect().bottom > 0);
}

window.addEventListener("scroll", updatePaginationDock, { passive: true });

async function select(index) {
  if (detailController) detailController.abort();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  let item = items[index];
  if (!item) return;
  activeArtworkId = item.id;

  $("#toast").textContent = "";
  $("#dTitle").textContent = "正在加载作品详情…";
  $("#deck").innerHTML = '<p class="loading-state">正在读取作品信息</p>';
  $("#download").disabled = true;

  try {
    if (item.source === "pixiv" && !item.pageImages) {
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, 18000);
      if (controller !== detailController || generation !== viewGeneration) return;
      items[index] = item;
    }
    if (controller !== detailController || generation !== viewGeneration) return;
    renderDetail(item, index);
  } catch (error) {
    if (controller.signal.aborted) return;
    $("#dTitle").textContent = "作品详情加载失败";
    $("#deck").innerHTML = "";
    $("#toast").textContent = error.message || "作品详情加载失败";
  } finally {
    if (controller === detailController) detailController = null;
  }
}

function renderDetail(item, index) {
  lockedDeckPage = null;
  currentDetailItem = item;
  collectionPageOffset = 0;
  $("#dTitle").textContent = item.title;
  $("#dDesc").textContent = item.description;
  $("#dArtist").textContent = item.artist;
  $("#dSize").textContent = `${item.width} × ${item.height} px`;
  $("#dBookmarks").textContent = Number(item.bookmarks || 0).toLocaleString();
  $("#dDate").textContent = item.date;
  $("#dTags").innerHTML = item.tags.map((tag) => `<span>#${esc(tag)}</span>`).join("");
  const chosenPages = selectedPagesByArtwork.get(item.id) || new Set();
  renderCollectionPageWindow(item, chosenPages);

  const visible = Math.min(item.pages, 4);
  const middle = (visible - 1) / 2;
  $("#deck").innerHTML = Array.from({ length: visible }, (_, page) => {
    const delta = page - middle;
    const angle = delta * 3.2;
    const lift = Math.abs(delta) * 4;
    const fallback = `/api/image/${(currentPage - 1) * 12 + index}/${page}?size=preview`;
    const src = item.pageImages?.[page]?.regular || item.thumb || fallback;
    return `<button class="deck-card" data-page="${page}" style="--i:${page};--angle:${angle}deg;--lift:${lift}px" aria-label="第 ${page + 1} 张" aria-pressed="false"><img src="${src}" alt="${esc(item.title)} 第 ${page + 1} 张" loading="lazy" decoding="async"><span>${page + 1} / ${item.pages}</span></button>`;
  }).join("");
  installImageFallbacks($("#deck"));

  $("#deck").querySelectorAll(".deck-card").forEach((card) => {
    card.onmouseenter = () => previewDeckCard(card);
    card.onmouseleave = () => { if (lockedDeckPage === null) resetDeckFan(); };
    card.onclick = () => toggleDeckCard(card);
  });
  $("#viewAll").hidden = item.pages <= visible;
  $("#deckHint").textContent = item.pages > visible ? `预览前 ${visible} 张，共 ${item.pages} 张；点击一张固定，再点一次取消` : "轻移鼠标预览；点击一张固定，再点一次取消";
  $("#quality").innerHTML = item.qualities.map((quality) => `<option value="${quality.id}">${esc(quality.label)} · ${quality.width} × ${quality.height}</option>`).join("");
  $("#format").innerHTML = item.formats.map((format) => `<option value="${format.id}">${esc(format.label)}</option>`).join("");
  $("#download").disabled = false;
  updateFormatHint();
}

function renderCollectionPageWindow(item = currentDetailItem, chosenPages = selectedPagesByArtwork.get(item?.id)) {
  if (!item) return;
  const pages = item.pageImages || [];
  const start = collectionPageOffset;
  const end = Math.min(pages.length, start + DETAIL_PAGE_WINDOW);
  $("#collectionPages").innerHTML = pages.slice(start, end).map((page, localIndex) => {
    const pageNo = start + localIndex;
    return `<label class="page-select"><input type="checkbox" data-collection-page="${pageNo}" ${chosenPages?.has(pageNo) ? "checked" : ""}><img src="${page.regular}" alt="${esc(item.title)} 第 ${pageNo + 1} 张" loading="lazy" decoding="async"><span>${pageNo + 1}</span></label>`;
  }).join("");
  installImageFallbacks($("#collectionPages"));
  $("#collectionPages").querySelectorAll("[data-collection-page]").forEach((box) => {
    box.onchange = () => {
      let set = selectedPagesByArtwork.get(item.id);
      const page = Number(box.dataset.collectionPage);
      if (box.checked) {
        if (!selectedArtworkIds.has(item.id) && selectedArtworkIds.size >= MAX_SELECTED_ARTWORKS) {
          box.checked = false;
          $("#toast").textContent = `一次最多选择 ${MAX_SELECTED_ARTWORKS} 个作品`;
          return;
        }
        if (!set) {
          set = new Set();
          selectedPagesByArtwork.set(item.id, set);
        }
        if (!set.has(page) && selectedPageCount() >= MAX_SELECTED_PAGES) {
          box.checked = false;
          $("#toast").textContent = `一次最多选择 ${MAX_SELECTED_PAGES} 张图片`;
          return;
        }
        set.add(page);
      } else if (set) {
        set.delete(page);
      }
      if (set?.size) {
        selectedArtworks.set(item.id, item);
        selectedArtworkIds.add(item.id);
        if (!selectedResultPageByArtwork.has(item.id)) {
          selectedResultPageByArtwork.set(item.id, batchCandidateResultPageByArtwork.get(item.id) || currentPage);
        }
        if (!selectedContextByArtwork.has(item.id)) {
          selectedContextByArtwork.set(item.id, batchCandidateContextByArtwork.get(item.id) || { ...activeSearchContext });
        }
      } else {
        selectedPagesByArtwork.delete(item.id);
        selectedArtworks.delete(item.id);
        selectedArtworkIds.delete(item.id);
        selectedContextByArtwork.delete(item.id);
        selectedResultPageByArtwork.delete(item.id);
        archivedArtworkIds.delete(item.id);
      }
      updateSelectionBar();
      syncResultSelectionControls();
    };
  });
  const more = $("#collectionPageMore");
  more.hidden = pages.length <= DETAIL_PAGE_WINDOW;
  more.textContent = `${start + 1}–${Math.max(start + 1, end)} / ${pages.length} · 显示后 ${DETAIL_PAGE_WINDOW} 张`;
}

function previewDeckCard(card) {
  if (lockedDeckPage !== null) return;
  resetDeckFan();
  card.classList.add("deck-preview");
}

function toggleDeckCard(card) {
  const page = Number(card.dataset.page);
  if (lockedDeckPage !== null && lockedDeckPage !== page) return;
  if (lockedDeckPage === page) {
    lockedDeckPage = null;
    resetDeckFan();
    $("#deckHint").textContent = "已取消固定；轻移鼠标预览，点击一张可再次固定";
    return;
  }
  lockedDeckPage = page;
  const cards = [...$("#deck").querySelectorAll(".deck-card")];
  cards.forEach((row) => {
    const selected = Number(row.dataset.page) === page;
    row.classList.toggle("deck-locked", selected);
    row.classList.toggle("deck-inert", !selected);
    row.setAttribute("aria-pressed", String(selected));
  });
  $("#deckHint").textContent = `已固定第 ${page + 1} 张；其他牌保持不动，再点当前牌取消`;
}

function resetDeckFan() {
  $("#deck").querySelectorAll(".deck-card").forEach((card) => {
    card.classList.remove("deck-preview", "deck-locked", "deck-inert");
    card.setAttribute("aria-pressed", "false");
  });
}

function openAllViewer() {
  const item = selectedArtworks.get(activeArtworkId) || items.find((row) => row.id === activeArtworkId);
  if (!item?.pageImages) return;
  $("#viewerTitle").textContent = item.title;
  $("#viewerArtist").textContent = item.artist;
  $("#viewerCount").textContent = `${item.pages} 张 · 连续浏览`;
  $("#viewerGrid").innerHTML = item.pageImages.map((page, index) => `<figure><img src="${page.regular}" alt="${esc(item.title)} 第 ${index + 1} 张" loading="lazy" decoding="async"><span>${String(index + 1).padStart(2, "0")} / ${String(item.pages).padStart(2, "0")}</span></figure>`).join("");
  installImageFallbacks($("#viewerGrid"));
  $("#allViewer").hidden = false;
  document.body.classList.add("viewer-open");
  $("#allViewer").scrollTop = 0;
}

function closeAllViewer() {
  $("#allViewer").hidden = true;
  document.body.classList.remove("viewer-open");
  $("#viewerGrid").innerHTML = "";
}

$("#viewAll").onclick = openAllViewer;
$("#closeViewer").onclick = closeAllViewer;
$("#collectionPageMore").onclick = () => {
  if (!currentDetailItem) return;
  const pageCount = (currentDetailItem.pageImages || []).length;
  collectionPageOffset = collectionPageOffset + DETAIL_PAGE_WINDOW >= pageCount
    ? 0
    : collectionPageOffset + DETAIL_PAGE_WINDOW;
  renderCollectionPageWindow();
};

function renderBatchWorkspace() {
  const chosen = batchCandidateItems.length ? batchCandidateItems : [...selectedArtworks.values()];
  if (!chosen.length) return;
  batchCandidateItems = chosen;
  const selectedCount = chosen.filter((item) => selectedArtworkIds.has(item.id)).length;
  document.body.classList.add("collection-basket-open");
  $("#detail").hidden = false;
  $("#batchWorkspace").hidden = false;
  $("#deck").innerHTML = "";
  $("#collectionPages").innerHTML = "";
  $("#collectionPageMore").hidden = true;
  $("#viewAll").hidden = true;
  $("#returnToBatch").hidden = true;
  $("#dTitle").textContent = "打包详情";
  $("#dDesc").textContent = `${selectedCount}/${chosen.length} 个作品已勾选`;
  $("#dArtist").textContent = "—";
  $("#dSize").textContent = "—";
  $("#dBookmarks").textContent = "—";
  $("#dDate").textContent = "—";
  $("#dTags").innerHTML = "";
  if (!$("#quality").options.length) {
    $("#quality").innerHTML = '<option value="regular">预览清晰度</option><option value="original">原图</option>';
  }
  if (!$("#format").options.length) {
    $("#format").innerHTML = '<option value="source">保留源格式</option>';
  }
  $("#batchSummary").textContent = `${selectedCount} 个作品 · ${selectedPageCount()} 张图片`;
  $("#batchDownload").disabled = selectedPageCount() === 0;
  $("#batchCollections").innerHTML = chosen.map((item) => {
    const selectedPages = selectedPagesByArtwork.get(item.id);
    const selected = selectedArtworkIds.has(item.id) && Boolean(selectedPages?.size);
    const selectedPagesLabel = `${selectedPages?.size || 0}/${item.pages} 张`;
    const pageCount = item.pages > 1 ? `<span class="batch-page-count">${item.pages}P</span>` : "";
    return `<article class="batch-collection ${selected ? "is-selected" : ""}" data-batch-artwork="${esc(item.id)}"><label class="batch-card-select" aria-label="${selected ? "取消选择" : "选择"} ${esc(item.title)}"><input type="checkbox" data-batch-select="${esc(item.id)}" ${selected ? "checked" : ""}><span aria-hidden="true">✓</span></label><button class="batch-card-open" type="button" data-open-collection="${esc(item.id)}" aria-label="打开 ${esc(item.title)}${item.pages > 1 ? `，共 ${item.pages} 张` : ""}"><span class="batch-card-cover"><img src="${item.thumb}" alt="${esc(item.title)}" loading="lazy" decoding="async">${pageCount}</span><span class="batch-card-copy"><b>${esc(item.title)}</b><small>${esc(item.artist)} · 已选 ${selectedPagesLabel}</small></span></button></article>`;
  }).join("");
  installImageFallbacks($("#batchCollections"));
  $("#batchCollections").querySelectorAll("[data-batch-select]").forEach((box) => {
    box.onchange = () => {
      const item = batchCandidateItems.find((candidate) => candidate.id === box.dataset.batchSelect);
      if (!item) return;
      const accepted = toggleArtworkSelection(item, box.checked);
      if (!accepted) {
        box.checked = false;
        return;
      }
      if (box.checked) {
        selectedContextByArtwork.set(item.id, batchCandidateContextByArtwork.get(item.id) || { ...activeSearchContext });
        selectedResultPageByArtwork.set(item.id, batchCandidateResultPageByArtwork.get(item.id) || currentPage);
      }
      syncResultSelectionControls();
      renderBatchWorkspace();
    };
  });
  $("#batchCollections").querySelectorAll("[data-open-collection]").forEach((button) => {
    button.onclick = () => openBatchCollection(button.dataset.openCollection);
  });
}

function openCurrentPageBatch() {
  if (!items.length) return;
  if (!selectAllCurrentPage()) return;
  viewGeneration += 1;
  if (detailController) detailController.abort();
  detailController = null;
  batchCandidateItems = [...selectedArtworks.values()];
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  for (const item of batchCandidateItems) {
    batchCandidateContextByArtwork.set(item.id, selectedContextByArtwork.get(item.id) || { ...activeSearchContext });
    batchCandidateResultPageByArtwork.set(item.id, selectedResultPageByArtwork.get(item.id) || currentPage);
  }
  renderBatchWorkspace();
  $("#detail").scrollIntoView({ behavior: "auto" });
}

function selectedGroups() {
  return [...selectedArtworks.keys()]
    .map((id) => ({
      id,
      pages: [...(selectedPagesByArtwork.get(id) || [])].sort((a, b) => a - b),
      context: selectedContextByArtwork.get(id) || activeSearchContext,
    }))
    .filter((group) => group.pages.length);
}

function contextKey(context) {
  return `${context?.kind || "tags"}\u0000${context?.value || ""}`;
}

function planContextDownloadChunks(groups) {
  const buckets = new Map();
  for (const group of groups) {
    const key = contextKey(group.context);
    if (!buckets.has(key)) buckets.set(key, { context: group.context, groups: [] });
    buckets.get(key).groups.push({ id: group.id, pages: group.pages });
  }
  return [...buckets.values()].flatMap((bucket) =>
    planDownloadChunks(bucket.groups).map((chunk) => ({ ...chunk, context: bucket.context }))
  );
}

function setDownloadButtonState(button, text, disabled) {
  button.disabled = disabled;
  button.textContent = text;
}

function downloadPayload(item, sourceIndex) {
  if (item.source === "pixiv") {
    return {
      endpoint: "/api/pixiv/download",
      body: { id: item.id, quality: $("#quality").value, saveRoot: $("#saveRoot").value.trim(), createFolder: $("#createFolder").checked, context: activeSearchContext },
      timeout: 120000,
    };
  }
  return {
    endpoint: "/api/download",
    body: { index: (currentPage - 1) * 12 + sourceIndex, pages: item.pages, quality: $("#quality").value, format: $("#format").value, tag: $("#tagTitle").textContent },
    timeout: 120000,
  };
}

function scrollToResults() {
  $("#gallery").scrollIntoView({ behavior: "auto" });
}

function planDownloadChunks(groups) {
  const chunks = [];
  let current = [];
  let pageCount = 0;
  for (const group of groups) {
    if (group.pages.length > DOWNLOAD_CHUNK_PAGES) {
      throw new Error(`作品 ${group.id} 单独超过 ${DOWNLOAD_CHUNK_PAGES} 张，无法安全分块`);
    }
    if (current.length && (
      pageCount + group.pages.length > DOWNLOAD_CHUNK_PAGES
      || current.length >= DOWNLOAD_CHUNK_ARTWORKS
    )) {
      chunks.push({ groups: current, pageCount });
      current = [];
      pageCount = 0;
    }
    current.push(group);
    pageCount += group.pages.length;
  }
  if (current.length) chunks.push({ groups: current, pageCount });
  return chunks;
}

function openCapacityDialog(targetPage) {
  pendingNavigationPage = targetPage;
  const dialog = $("#capacityDialog");
  if (dialog?.showModal) dialog.showModal();
}

function selectionWouldBeEvicted(targetPage) {
  const oldestRetainedPage = Math.max(1, targetPage - SEARCH_KEEP_BEHIND);
  return [...unarchivedSelectionIds()].some(
    (id) => (selectedResultPageByArtwork.get(id) || currentPage) < oldestRetainedPage,
  );
}

function navigateToPage(page) {
  if (selectionWouldBeEvicted(page)) {
    openCapacityDialog(page);
    return;
  }
  search(activeTagQuery, page);
  scrollToResults();
}

function archiveAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  const detached = detachSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    $("#toast").textContent = `已将当前 ${detached} 个作品放入采集篮；继续翻页不会下载原图`;
    search(activeTagQuery, page);
    scrollToResults();
  }
}

function clearAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  clearSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    search(activeTagQuery, page);
    scrollToResults();
  }
}

function cancelCapacityDecision() {
  pendingNavigationPage = null;
  $("#capacityDialog")?.close();
}

$("#archiveAndContinue").onclick = archiveAndContinue;
$("#clearAndContinue").onclick = clearAndContinue;
$("#cancelCapacity").onclick = cancelCapacityDecision;

async function openBatchCollection(id) {
  if (detailController) detailController.abort();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  let item = selectedArtworks.get(id) || batchCandidateItems.find((candidate) => candidate.id === id);
  if (!item) return;
  try {
    if (item.source === "pixiv" && !item.pageImages) {
      $("#dTitle").textContent = "正在加载合集详情…";
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, 18000);
      if (controller !== detailController || generation !== viewGeneration) return;
      const candidateIndex = batchCandidateItems.findIndex((candidate) => candidate.id === item.id);
      if (candidateIndex >= 0) batchCandidateItems[candidateIndex] = item;
      if (selectedArtworkIds.has(item.id)) selectedArtworks.set(item.id, item);
    }
    if (controller !== detailController || generation !== viewGeneration) return;
    $("#detail").hidden = false;
    $("#batchWorkspace").hidden = true;
    $("#returnToBatch").hidden = false;
    activeArtworkId = item.id;
    renderDetail(item, items.findIndex((row) => row.id === id));
    $("#detail").scrollIntoView({ behavior: "auto" });
  } catch (error) {
    if (controller.signal.aborted) return;
    $("#toast").textContent = error.message || "作品详情加载失败";
  } finally {
    if (controller === detailController) detailController = null;
  }
}

$("#returnToBatch").onclick = () => {
  viewGeneration += 1;
  if (detailController) detailController.abort();
  detailController = null;
  renderBatchWorkspace();
};
$("#selectAllPage").onclick = selectAllCurrentPage;
$("#clearPageSelection").onclick = clearAllCurrentPage;
$("#clearSelection").onclick = () => { clearAllSelection(); document.body.classList.remove("collection-basket-open"); render(); };
$("#openBatch").onclick = openCurrentPageBatch;

$("#batchDownload").onclick = async () => {
  const groups = selectedGroups();
  if (!groups.length) {
    $("#toast").textContent = "请至少选择一张图片";
    return;
  }
  let chunks;
  try {
    chunks = planContextDownloadChunks(groups);
  } catch (error) {
    $("#toast").textContent = error.message;
    return;
  }
  const button = $("#batchDownload");
  setDownloadButtonState(button, "准备保存…", true);
  let savedCount = 0;
  try {
    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      setDownloadButtonState(button, `正在保存第 ${index + 1}/${chunks.length} 批…`, true);
      const data = await fetchJson("/api/pixiv/batch-download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          groups: chunk.groups,
          quality: $("#quality").value || "regular",
          saveRoot: $("#saveRoot").value.trim(),
          createFolder: $("#createFolder").checked,
          groupArtworks: Boolean($("#groupArtworks")?.checked),
          context: chunk.context,
        }),
      }, 300000);
      savedCount += Array.isArray(data.saved) ? data.saved.length : chunk.pageCount;
    }
    $("#toast").textContent = `已保存 ${savedCount} 张图片，共 ${chunks.length} 批`;
  } catch (error) {
    const prefix = savedCount ? `已保存 ${savedCount} 张；后续` : "批量下载";
    $("#toast").textContent = `${prefix}失败：${error.message}`;
  } finally {
    setDownloadButtonState(button, "下载已勾选图片", false);
  }
};

addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#allViewer").hidden) closeAllViewer();
});

function updateFormatHint() {
  const quality = $("#quality").selectedOptions[0]?.textContent || "";
  const format = $("#format").selectedOptions[0]?.textContent || "";
  $("#qualityText").textContent = quality;
  $("#formatText").textContent = format;
  $("#formatHint").textContent = quality ? `将按 ${quality}，${format} 保存。源格式不可转换时会保留原扩展名。` : "";
}

$("#quality").onchange = updateFormatHint;
$("#format").onchange = updateFormatHint;
$("#searchForm").onsubmit = (event) => {
  event.preventDefault();
  search($("#tag").value, 1);
  scrollToResults();
};

$("#fuzzySearch")?.addEventListener("change", () => {
  if ($("#tag").value.trim()) search($("#tag").value, 1);
});

$("#browseFolder").onclick = async () => {
  const button = $("#browseFolder");
  button.disabled = true;
  button.textContent = "等待选择…";
  try {
    let data;
    if (window.pywebview?.api?.select_folder) {
      data = await window.pywebview.api.select_folder();
    } else {
      data = await fetchJson("/api/system/select-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial: $("#saveRoot").value }),
      }, 300000);
    }
    if (data.selected) {
      $("#saveRoot").value = data.selected;
      $("#toast").textContent = `保存位置：${data.selected}`;
    } else if (data.cancelled) {
      $("#toast").textContent = "已取消目录选择";
    }
  } catch (error) {
    $("#toast").textContent = error.message || "目录选择失败";
  } finally {
    button.disabled = false;
    button.textContent = "浏览…";
  }
};

$("#download").onclick = async () => {
  const item = selectedArtworks.get(activeArtworkId) || items.find((row) => row.id === activeArtworkId);
  if (!item) return;
  const sourceIndex = Math.max(0, items.findIndex((row) => row.id === item.id));
  const button = $("#download");
  setDownloadButtonState(button, "正在保存…", true);
  try {
    const request = downloadPayload(item, sourceIndex);
    const data = await fetchJson(request.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request.body),
    }, request.timeout);
    $("#toast").textContent = `已保存 ${data.saved.length} 张：${data.saved[0]}`;
  } catch (error) {
    $("#toast").textContent = `保存失败：${error.message || "未知错误"}`;
  } finally {
    setDownloadButtonState(button, "下载本作品 ↓", false);
  }
};

async function syncAuthStatus() {
  try {
    const data = await fetchJson("/api/status", {}, 8000);
    const logged = Boolean(data.loggedIn);
    $("#mode").textContent = logged ? "PIXIV AUTHORIZED" : "PIXIV PUBLIC";
    $("#loginBtn").textContent = logged ? "Pixiv 已连接" : "登录 Pixiv";
    $("#authStateTitle").textContent = logged ? "当前：已连接" : "当前：未连接";
    $("#authStateText").textContent = logged ? "Pixiv 会话已保存在本机。" : "将在 MOKU 应用内打开 Pixiv 官方登录页面。";
    $("#authAction").textContent = logged ? "退出 Pixiv 账户" : "打开应用内登录窗口";
    $("#safety").querySelectorAll('option[value="r18"],option[value="all"]').forEach((option) => { option.disabled = !logged; });
  } catch (error) {
    $("#mode").textContent = "PIXIV OFFLINE";
    $("#authStateText").textContent = error.message || "暂时无法读取本机会话状态";
  }
}

const helpDialog = $("#helpDialog");
$("#helpBtn").onclick = () => helpDialog.showModal();
$("#networkCheck").onclick = async () => {
  const button = $("#networkCheck");
  button.disabled = true;
  button.textContent = "正在匿名检测…";
  $("#networkHeadline").textContent = "正在检查当前网络";
  $("#networkRoute").textContent = "当前路线：读取中";
  $("#networkGuidance").textContent = "正在分别测试 Pixiv 主站和图片线路，不会发送登录 Cookie。";
  $("#pixivCheck").textContent = "Pixiv 主站：检测中";
  $("#cdnCheck").textContent = "图片线路：检测中";
  try {
    const data = await fetchJson("/api/network/diagnose", {}, 20000);
    const summary = data.summary || {};
    $("#networkHeadline").textContent = summary.headline || "检测完成";
    $("#networkRoute").textContent = `当前路线：${summary.routeLabel || "未知"}`;
    $("#networkGuidance").textContent = summary.guidance || "请根据分项结果检查网络。";
    const checks = new Map((data.checks || []).map((row) => [row.name, row]));
    const errorLabels = {
      timeout: "连接超时",
      refused: "连接被拒绝",
      tls: "证书或 TLS 错误",
      http: "HTTP 响应异常",
      unavailable: "无法连接",
    };
    const formatCheck = (label, row) => row?.ok
      ? `${label}：可用${Number.isFinite(row.ms) ? `（${row.ms} ms）` : ""}`
      : `${label}：不可用（${errorLabels[row?.errorKind] || "无法连接"}）`;
    $("#pixivCheck").textContent = formatCheck("Pixiv 主站", checks.get("pixiv"));
    $("#cdnCheck").textContent = formatCheck("图片线路", checks.get("cdn"));
  } catch (error) {
    $("#networkHeadline").textContent = "网络检测未完成";
    $("#networkRoute").textContent = "当前路线：未知";
    $("#networkGuidance").textContent = error.message || "MOKU 暂时无法完成匿名网络检测。";
    $("#pixivCheck").textContent = "Pixiv 主站：未完成";
    $("#cdnCheck").textContent = "图片线路：未完成";
  } finally {
    button.disabled = false;
    button.textContent = "重新检测网络";
  }
};

const dialog = $("#loginDialog");
$("#loginBtn").onclick = async () => {
  dialog.showModal();
  await syncAuthStatus();
};
$("#authAction").onclick = async () => {
  const logged = $("#authAction").textContent.includes("退出");
  $("#authAction").disabled = true;
  $("#authStateText").textContent = logged ? "正在退出…" : "请在 MOKU 桌面登录窗口完成登录、验证码或 2FA；应用会实时监控状态…";
  try {
    const remember = Boolean($("#rememberLogin").checked);
    let data;
    if (!window.pywebview?.api?.pixiv_login) {
      throw new Error("账户授权只在 MOKU 桌面版提供，请启动 MOKU.exe 后登录。");
    }
    data = logged
      ? await window.pywebview.api.pixiv_logout()
      : await window.pywebview.api.pixiv_login(remember);
    if (!data.ok) throw new Error(data.error || "授权失败");
    await syncAuthStatus();
  } catch (error) {
    $("#authStateText").textContent = error.message || "授权失败";
  } finally {
    $("#authAction").disabled = false;
  }
};
document.querySelectorAll(".dialog-close").forEach((button) => {
  button.onclick = () => button.closest("dialog")?.close();
});
document.querySelectorAll("dialog").forEach((modal) => {
  modal.onclick = (event) => {
    if (event.target === modal) modal.close();
  };
});

clearDetail();
grid.innerHTML = '<div class="empty-state"><b>准备就绪</b><p>输入标签后点击“开始寻找”。首屏不再自动连接 Pixiv。</p></div>';
$("#count").textContent = "等待搜索";

if ("requestIdleCallback" in window) {
  requestIdleCallback(() => syncAuthStatus(), { timeout: 2000 });
} else {
  setTimeout(() => syncAuthStatus(), 500);
}
