let items = [];
let activeArtworkId = null;
let currentPage = 1;
let pageNumbers = [1];
let firstAvailablePage = 1;
let preloadedThrough = 1;
let searchHasMore = false;
let activeTagQuery = "猫耳";
let searchController = null;
let activeSearchRequestId = null;
let searchRequestSequence = 0;
let searchGeneration = 0;
let prefetchTimer = null;
let prefetchController = null;
let prefetchRequestId = null;
let detailController = null;
let requestToken = null;
let animationFormats = {gif: true, mp4: null};
let requestTokenPromise = null;
let requestTokenController = null;
let viewGeneration = 0;
let pendingNavigationPage = null;
let activeSearchContext = { kind: "tags", value: "猫耳" };
let activeSearchFilters = { mode: "safe", workType: "all", includeAi: false, fuzzy: false, startDate: "", endDate: "" };
let currentDetailItem = null;
let currentDetailContext = null;
let collectionPageOffset = 0;
let batchCandidateItems = [];
let basketDetailItem = null;
let basketReturnMode = "summary";
let basketArtworkOffset = 0;
let viewerPageOffset = 0;
let paginationDockFrame = null;
let searchPending = false;
let resultSelectionEnabled = false;
let singleDownloadPending = false;
let resumableBatchTask = null;
let resumableSingleTask = null;
let lastKnownLoggedIn = null;
let lastKnownAuthorizationGeneration = null;
let downloadAuthorizationRevision = 0;
let authStatusGeneration = 0;
const batchCandidateContextByArtwork = new Map();
const batchCandidateResultPageByArtwork = new Map();
const MAX_SELECTED_PAGES = 1000;
const DOWNLOAD_CHUNK_ARTWORKS = 20;
const DOWNLOAD_CHUNK_PAGES = 200;
const DETAIL_PAGE_WINDOW = 48;
const DETAIL_REFRESH_COOLDOWN_MS = 30000;
const BASKET_ARTWORK_WINDOW = 120;
const VIEWER_PAGE_WINDOW = 80;
const SEARCH_KEEP_BEHIND = 6;
const selection = createSelectionStore({maxPages: MAX_SELECTED_PAGES});
const detailRefreshes = new Map();
const detailRefreshAttempts = new Map();
const staleBasketPreviewIds = new Set();

const $ = (selector) => document.querySelector(selector);
const grid = $("#grid");
const gallery = $("#gallery");
const searchButton = $("#searchSubmit");
const cancelSearchButton = $("#cancelSearch");
document.documentElement.classList.add("conservative");

function readSearchFilters() {
  return {
    mode: $("#safety").value || "safe",
    workType: $("#workType").value || "all",
    includeAi: Boolean($("#includeAi").checked),
    fuzzy: Boolean($("#fuzzySearch")?.checked),
    ...publicationDateBounds($("#datePreset").value, $("#startDate").value, $("#endDate").value),
  };
}

function publicationDateBounds(preset, startDate = "", endDate = "", now = new Date()) {
  if (preset === "custom") return { startDate, endDate };
  if (preset !== "7" && preset !== "30") return { startDate: "", endDate: "" };
  const day = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const part = (type) => day.find((row) => row.type === type).value;
  const end = `${part("year")}-${part("month")}-${part("day")}`;
  const start = new Date(`${end}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - Number(preset) + 1);
  return { startDate: start.toISOString().slice(0, 10), endDate: end };
}

function syncDateInputs() {
  const custom = $("#datePreset").value === "custom";
  $("#customDates").hidden = !custom;
  for (const input of [$("#startDate"), $("#endDate")]) {
    input.disabled = !custom;
    input.required = custom;
  }
  const start = $("#startDate").value;
  const end = $("#endDate").value;
  $("#endDate").setCustomValidity(custom && start && end && start > end ? "结束日期不能早于开始日期" : "");
}

$("#datePreset").onchange = syncDateInputs;
$("#startDate").oninput = syncDateInputs;
$("#endDate").oninput = syncDateInputs;
syncDateInputs();

const esc = (value) => String(value).replace(
  /[&<>"']/g,
  (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
);

function announceToast(message) {
  // The basket page covers #detail, so shared flows report in both places.
  $("#toast").textContent = message;
  $("#basketToast").textContent = message;
}

let taskDockTimer = null;
function showTaskDock(title, text, holdMs = 3200) {
  $("#taskDockTitle").textContent = title;
  $("#taskDockText").textContent = text;
  $("#taskDock").classList.add("is-visible");
  if (taskDockTimer) clearTimeout(taskDockTimer);
  taskDockTimer = holdMs > 0
    ? setTimeout(() => {
        taskDockTimer = null;
        $("#taskDock").classList.remove("is-visible");
      }, holdMs)
    : null;
}

async function getRequestToken() {
  if (requestToken) return requestToken;
  if (!requestTokenPromise) {
    const controller = new AbortController();
    requestTokenController = controller;
    const timer = setTimeout(() => controller.abort(), 5000);
    let pending;
    pending = fetch("/api/health", {
      cache: "no-store",
      headers: { "Sec-Fetch-Site": "same-origin" },
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("本机服务未准备好");
        return response.json();
      })
      .then((data) => {
        if (data.protocolVersion !== 5 || data.applicationId !== "MOKU.PixivTagGallery") throw new Error("MOKU 后端版本过旧，请关闭当前窗口并重新启动 MOKU");
        if (!data.requestToken) throw new Error("本机请求授权未初始化");
        requestToken = data.requestToken;
        if (data.animationFormats) animationFormats = data.animationFormats;
        return requestToken;
      })
      .catch((error) => {
        if (controller.signal.aborted) throw new Error("本机服务连接超时或已取消");
        throw error;
      })
      .finally(() => {
        clearTimeout(timer);
        if (requestTokenController === controller) requestTokenController = null;
        if (!requestToken && requestTokenPromise === pending) requestTokenPromise = null;
      });
    requestTokenPromise = pending;
  }
  return requestTokenPromise;
}

function waitForPromiseOrAbort(promise, signal) {
  if (!signal) return Promise.resolve(promise);
  if (signal.aborted) return Promise.reject(signal.reason || new Error("请求已取消"));
  return new Promise((resolve, reject) => {
    const finish = (callback) => (value) => {
      signal.removeEventListener("abort", abort);
      callback(value);
    };
    const abort = () => reject(signal.reason || new Error("请求已取消"));
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve(promise).then(finish(resolve), finish(reject));
  });
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
    headers.set(
      "X-MOKU-Request-Token",
      await waitForPromiseOrAbort(getRequestToken(), controller.signal),
    );
    const response = await fetch(url, { ...options, headers, signal: controller.signal });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      throw new Error("服务返回了无法解析的数据");
    }
    if (!response.ok) {
      const error = new Error(data.error || `请求失败（HTTP ${response.status}）`);
      error.requestIdConflict = Boolean(data.requestIdConflict);
      throw error;
    }
    return data;
  } catch (error) {
    if (timedOut) {
      const timeout = new Error("请求超时，请检查 VPN / 系统代理后重试");
      timeout.timedOut = true;
      throw timeout;
    }
    throw error;
  } finally {
    clearTimeout(timer);
    if (upstream) upstream.removeEventListener("abort", relayAbort);
  }
}

function nextSearchRequestId() {
  searchRequestSequence += 1;
  return `${Date.now().toString(36)}-${searchRequestSequence.toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

async function notifySearchCancellation(requestId, endpoint = "/api/pixiv/search/cancel") {
  if (!requestId || !requestToken) return;
  try {
    await fetch(endpoint, {
      method: "POST",
      cache: "no-store",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-MOKU-Request-Token": requestToken,
      },
      body: JSON.stringify({ requestId }),
    });
  } catch {
    // The local service may already be closing; the browser request is still aborted below.
  }
}

function cancelActiveSearch() {
  cancelSearchPrefetch();
  if (!searchController) return false;
  const requestId = activeSearchRequestId;
  searchController.abort();
  void notifySearchCancellation(requestId);
  return true;
}

function cancelSearchPrefetch() {
  clearTimeout(prefetchTimer);
  prefetchTimer = null;
  if (prefetchController) {
    prefetchController.abort();
    void notifySearchCancellation(prefetchRequestId);
  }
  prefetchController = null;
  prefetchRequestId = null;
}

function currentPageNeedsMoreResults() {
  return activeSearchContext.kind !== "pid" && searchHasMore && items.length < 36;
}

function scheduleSearchPrefetch(tag, page, filters, generation) {
  if (!searchHasMore || currentPageNeedsMoreResults() || preloadedThrough >= page + 3 || activeSearchContext.kind === "pid") return;
  prefetchTimer = setTimeout(async () => {
    prefetchTimer = null;
    if (document.hidden || generation !== searchGeneration || currentPage !== page) return;
    const controller = new AbortController();
    const requestId = nextSearchRequestId();
    prefetchController = controller;
    prefetchRequestId = requestId;
    const query = new URLSearchParams({
      tag, page: String(page), mode: filters.mode, workType: filters.workType,
      includeAi: String(filters.includeAi), fuzzy: String(filters.fuzzy), requestId, prefetch: "true",
      startDate: filters.startDate || "", endDate: filters.endDate || "",
    });
    try {
      const data = await fetchJson(`/api/pixiv/search?${query}`, {signal: controller.signal}, 60000);
      if (controller.signal.aborted || generation !== searchGeneration || currentPage !== page) return;
      if (!Array.isArray(data.availablePages)) return;
      pageNumbers = data.availablePages;
      firstAvailablePage = Number(pageNumbers[0]) || page;
      preloadedThrough = Number(data.preloadedThrough) || page;
      searchHasMore = Boolean(data.hasMore);
      // Metadata only: keep the current DOM, selection and image capabilities.
      renderPagination();
      renderCacheStatus();
    } catch {
      // A failed warm-up must not replace usable foreground results.
      if (!controller.signal.aborted) void notifySearchCancellation(requestId);
    } finally {
      if (prefetchController === controller) {
        prefetchController = null;
        prefetchRequestId = null;
      }
    }
  }, 650);
}

function abortDetailRefreshes() {
  detailRefreshes.forEach((entry) => entry.controller.abort());
  detailRefreshes.clear();
  detailRefreshAttempts.clear();
}

function invalidateDetailView() {
  viewGeneration += 1;
  abortDetailRefreshes();
  if (detailController) detailController.abort();
  detailController = null;
}

function rememberArtworkDetail(item) {
  const artworkId = String(item.id);
  const itemIndex = items.findIndex((row) => String(row.id) === artworkId);
  if (itemIndex >= 0) items[itemIndex] = item;
  const candidateIndex = batchCandidateItems.findIndex((row) => String(row.id) === artworkId);
  if (candidateIndex >= 0) batchCandidateItems[candidateIndex] = item;
  selection.remember(item);
  if (String(basketDetailItem?.id || "") === artworkId) basketDetailItem = item;
  staleBasketPreviewIds.delete(artworkId);
}

async function refreshArtworkPreview(rawArtworkId) {
  const artworkId = String(rawArtworkId || "");
  if (!/^\d+$/.test(artworkId)) return false;
  const existing = detailRefreshes.get(artworkId);
  if (existing) return existing.promise;
  const now = Date.now();
  const lastAttempt = detailRefreshAttempts.get(artworkId) || 0;
  if (now - lastAttempt < DETAIL_REFRESH_COOLDOWN_MS) return false;
  detailRefreshAttempts.set(artworkId, now);
  const controller = new AbortController();
  const generation = viewGeneration;
  let task;
  task = (async () => {
    try {
      const fresh = await fetchJson(`/api/pixiv/artwork/${artworkId}`, { signal: controller.signal }, 18000);
      if (
        controller.signal.aborted
        || generation !== viewGeneration
        || String(activeArtworkId || "") !== artworkId
        || String(fresh?.id || "") !== artworkId
        || !Array.isArray(fresh.pageImages)
      ) return false;

      rememberArtworkDetail(fresh);
      currentDetailItem = fresh;

      document.querySelectorAll("[data-detail-artwork]").forEach((img) => {
        if (String(img.dataset.detailArtwork || "") !== artworkId) return;
        const page = Number(img.dataset.detailPage);
        const source = fresh.pageImages?.[page]?.regular || (page === 0 ? fresh.thumb : "");
        if (!source) return;
        delete img.dataset.detailRefreshAttemptedUrl;
        img.classList.remove("image-unavailable");
        img.closest(".page-select,.deck-card,figure")?.classList.remove("image-unavailable");
        img.setAttribute("src", source);
      });
      announceToast("预览授权已刷新");
      return true;
    } catch (error) {
      if (!controller.signal.aborted && generation === viewGeneration) {
        announceToast(error.message || "预览刷新失败");
      }
      return false;
    } finally {
      if (detailRefreshes.get(artworkId)?.promise === task) detailRefreshes.delete(artworkId);
    }
  })();
  detailRefreshes.set(artworkId, { controller, promise: task });
  return task;
}

function installImageFallbacks(root = document) {
  root.querySelectorAll("img").forEach((img) => {
    if (img.dataset.fallbackReady === "1") return;
    img.dataset.fallbackReady = "1";
    img.addEventListener("error", async () => {
      const artworkId = img.dataset.detailArtwork;
      const basketArtworkId = img.dataset.basketArtwork;
      const failedUrl = img.currentSrc || img.getAttribute?.("src") || img.src || "";
      const failedSource = img.getAttribute?.("src") || img.src || "";
      const generation = viewGeneration;
      if (artworkId && failedUrl && img.dataset.detailRefreshAttemptedUrl !== failedUrl) {
        img.dataset.detailRefreshAttemptedUrl = failedUrl;
        if (await refreshArtworkPreview(artworkId)) return;
      }
      if (
        generation !== viewGeneration
        || img.isConnected === false
        || (img.getAttribute?.("src") || img.src || "") !== failedSource
      ) return;
      img.removeAttribute("src");
      img.classList.add("image-unavailable");
      if (basketArtworkId) staleBasketPreviewIds.add(String(basketArtworkId));
      img.closest(".poster,.batch-collection,.page-select,.deck-card,figure")?.classList.add("image-unavailable");
    });
  });
}

async function fetchBytes(url, { signal } = {}, timeoutMs = 30000) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(signal.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const headers = new Headers();
    headers.set("X-MOKU-Request-Token", await waitForPromiseOrAbort(getRequestToken(), controller.signal));
    controller.signal.throwIfAborted();
    const response = await fetch(url, { headers, signal: controller.signal });
    if (!response.ok) {
      let data;
      try { data = await response.json(); } catch { /* Non-JSON errors retain the status fallback. */ }
      throw new Error(data?.error || `请求失败（HTTP ${response.status}）`);
    }
    return await response.arrayBuffer();
  } catch (error) {
    if (timedOut) throw new Error("动图加载超时，请检查网络后移开鼠标重试");
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

const ugoiraPreview = createUgoiraPreview({
  hoverDelay: 180,
  fetchJson: (url, options) => fetchJson(url, options, 30000),
  fetchBytes: (...args) => fetchBytes(...args),
  cancelRequest: requestId => notifySearchCancellation(requestId, "/api/pixiv/ugoira/cancel"),
  onError: (message) => announceToast(message),
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) { ugoiraPreview.stop(); cancelSearchPrefetch(); }
  else void syncAuthStatus();
});
window.addEventListener("pagehide", () => { ugoiraPreview.clear(); cancelSearchPrefetch(); });

function attachUgoiraHoverTargets(root) {
  root.querySelectorAll("[data-ugoira-preview]").forEach((host) => {
    if (host.dataset.ugoiraHoverReady === "1") return;
    host.dataset.ugoiraHoverReady = "1";
    host.addEventListener("mouseenter", () => ugoiraPreview.start(host, host.dataset.ugoiraPreview));
    host.addEventListener("mouseleave", () => ugoiraPreview.stop(host));
  });
}

function syncSearchScopedControls() {
  const resultActionsDisabled = selection.locked || searchPending || !resultSelectionEnabled || items.length === 0;
  $("#selectAllPage").disabled = resultActionsDisabled;
  $("#clearPageSelection").disabled = resultActionsDisabled;
  const selectionActionsDisabled = selection.locked || searchPending;
  $("#clearSelection").disabled = selectionActionsDisabled || selection.size === 0;
  $("#openBatch").disabled = selectionActionsDisabled || selection.size === 0;
  $("#batchDownload").disabled = selection.locked || searchPending || singleDownloadPending || selectedPageCount() === 0;
  searchButton.disabled = selection.locked || searchPending || singleDownloadPending;
  const detailReady = Boolean(
    currentDetailItem
    && activeArtworkId !== null
    && String(currentDetailItem.id) === String(activeArtworkId),
  );
  $("#download").disabled = selection.locked || searchPending || singleDownloadPending || !detailReady;
  if (detailReady && !singleDownloadPending) {
    $("#download").disabled ||= currentDownloadPages(currentDetailItem).length === 0;
    $("#download").textContent = downloadButtonLabel(currentDetailItem);
  }
}

function clearDetail(message = "选择一件作品查看详情") {
  closeBasketPage();
  document.body.classList.remove("batch-mode");
  normalDetailView.reset();
  activeArtworkId = null;
  currentDetailItem = null;
  currentDetailContext = null;
  $("#dTitle").textContent = message;
  $("#dWorkType").hidden = true;
  $("#dDesc").textContent = "";
  $("#dArtist").textContent = "—";
  $("#dSize").textContent = "—";
  $("#dBookmarks").textContent = "—";
  $("#dDate").textContent = "—";
  $("#dTags").innerHTML = "";
  $("#deck").innerHTML = "";
  $("#collectionPages").innerHTML = "";
  $("#collectionPageMore").hidden = true;
  $("#deckHint").textContent = "点击搜索结果后加载作品详情";
  $("#quality").innerHTML = "";
  $("#format").innerHTML = "";
  $("#qualityText").textContent = "";
  $("#formatText").textContent = "";
  $("#formatHint").textContent = "";
  $("#viewAll").hidden = true;
  $("#download").disabled = true;
  syncSearchScopedControls();
}

function discardRestrictedSelections() {
  const restrictedIds = new Set();
  const rememberRestricted = (item) => {
    if (item?.restriction === "r18" && item.id !== undefined) restrictedIds.add(item.id);
  };
  items.forEach(rememberRestricted);
  batchCandidateItems.forEach(rememberRestricted);
  selection.snapshot().forEach(({item}) => rememberRestricted(item));
  rememberRestricted(currentDetailItem);
  selection.revoke(restrictedIds);
  restrictedIds.forEach((id) => staleBasketPreviewIds.delete(id));
  batchCandidateItems = [];
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  staleBasketPreviewIds.clear();
}

function handleAuthorizationLoss(reason = "Pixiv 已断开") {
  resumableSingleTask = null;
  resumableBatchTask = null;
  ugoiraPreview.clear();
  invalidateDetailView();
  cancelActiveSearch();
  searchController = null;
  activeSearchRequestId = null;
  searchPending = false;
  resultSelectionEnabled = false;
  $("#safety").value = "safe";
  activeSearchFilters = { ...activeSearchFilters, mode: "safe" };
  closeAllViewer();
  discardRestrictedSelections();
  items = [];
  currentPage = 1;
  pageNumbers = [1];
  firstAvailablePage = 1;
  preloadedThrough = 1;
  searchHasMore = false;
  $("#pagination").innerHTML = "";
  $("#count").textContent = `${reason}，请重新搜索`;
  $("#tagTitle").textContent = "等待搜索";
  grid.innerHTML = `<div class="empty-state"><b>${esc(reason)}</b><p>受限预览已清理，请重新搜索。</p></div>`;
  clearDetail(`${reason}，请重新搜索`);
  updateSelectionBar();
  searchButton.disabled = selection.locked;
  searchButton.innerHTML = "开始寻找 <span>↗</span>";
  cancelSearchButton.disabled = false;
  cancelSearchButton.hidden = true;
  syncSearchScopedControls();
}

function updateSelectionBar() {
  const count = selection.size;
  const pages = selectedPageCount();
  $("#selectionBar").hidden = items.length === 0 && count === 0;
  $("#selectionCount").textContent = count
    ? `采集篮 ${count} 个作品 · ${pages}/${MAX_SELECTED_PAGES} 张图片${selection.archivedCount ? ` · 已归档 ${selection.archivedCount}` : ""}`
    : `当前页 ${items.length} 个作品`;
  $("#clearSelection").disabled = count === 0;
  if (document.body.classList.contains("batch-mode")) updateBatchDetailSummary();
  renderCacheStatus();
  updateFloatingChrome();
  syncSearchScopedControls();
}

function selectedPageCount() {
  return selection.pageCount;
}

function showSelectionLimitDialog(attemptedPages = 1) {
  const remaining = Math.max(0, MAX_SELECTED_PAGES - selectedPageCount());
  $("#selectionLimitText").textContent = `采集篮最多保存 ${MAX_SELECTED_PAGES} 张图片的下载选择。当前还可加入 ${remaining} 张，本次尝试新增 ${attemptedPages} 张。请先取消部分图片。`;
  const dialog = $("#selectionLimitDialog");
  if (dialog?.showModal && !dialog.open) dialog.showModal();
}

function renderCacheStatus() {
  const status = $("#cacheStatus");
  if (!status) return;
  const retainedStart = Math.max(firstAvailablePage, currentPage - SEARCH_KEEP_BEHIND);
  status.textContent = `搜索缓存：当前第 ${currentPage} 页，数据预加载至第 ${preloadedThrough} 页，保留第 ${retainedStart}–${currentPage} 页；缩略图仅加载当前打开页。采集篮缓存：${selection.size} 个作品、${selectedPageCount()}/${MAX_SELECTED_PAGES} 张已选；只保留元数据和下载链接，不缓存图片二进制。`;
}

function unarchivedSelectionIds() {
  return new Set(selection.snapshot().filter(row => !row.archived).map(row => row.id));
}

function detachSelection(ids) {
  const result = selection.archive(ids);
  if (result.accepted) updateSelectionBar();
  return result.count || 0;
}

function clearSelection(ids) {
  const result = selection.remove(ids);
  if (result.accepted) updateSelectionBar();
  return result.count || 0;
}

function clearAllSelection() {
  if (!selection.clear().accepted) return false;
  batchCandidateItems = [];
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  updateSelectionBar();
  return true;
}

function reportSelectionRejection(result) {
  if (result.reason === "capacity") showSelectionLimitDialog(result.additional);
  else announceToast(result.reason === "locked"
    ? "下载任务进行中，本次任务已锁定当前勾选。"
    : "作品页数异常，已阻止加入采集篮");
}

function toggleArtworkSelection(item, checked, origin = {context: activeSearchContext, resultPage: currentPage}) {
  const result = checked ? selection.choose([item], origin) : selection.remove([item.id]);
  if (!result.accepted) { reportSelectionRejection(result); return false; }
  updateSelectionBar();
  return true;
}

function selectAllCurrentPage() {
  if (selection.locked || searchPending || !resultSelectionEnabled) return false;
  const result = selection.choose(items, {context: activeSearchContext, resultPage: currentPage}, {fill: true});
  if (!result.accepted) {
    $("#pageSelectionStatus").textContent = result.reason === "capacity"
      ? `无法全选：采集篮最多 ${MAX_SELECTED_PAGES} 张图片`
      : "无法全选：搜索结果包含异常页数";
    reportSelectionRejection(result);
    return false;
  }
  updateSelectionBar();
  $("#pageSelectionStatus").textContent = `已全选当前页 ${items.length} 个作品及其全部图片`;
  render();
  return true;
}

function clearAllCurrentPage() {
  if (selection.locked || searchPending || !resultSelectionEnabled) return;
  clearSelection(items.map(item => item.id));
  $("#pageSelectionStatus").textContent = "已取消当前页全部选择";
  render();
}

async function search(tag, page = 1, filters = readSearchFilters()) {
  if (selection.locked || singleDownloadPending) return;
  ugoiraPreview.stop();
  const previousView = searchController?._mokuRestoreView || {
    count: $("#count").textContent,
    hadCommittedResults: resultSelectionEnabled,
  };
  cancelActiveSearch();
  invalidateDetailView();
  const generation = ++searchGeneration;
  searchController = new AbortController();
  const controller = searchController;
  controller._mokuRestoreView = previousView;
  const requestId = nextSearchRequestId();
  activeSearchRequestId = requestId;
  const cleanTag = String(tag || "").trim() || "原创";
  const requestedFilters = {
    mode: filters?.mode || "safe",
    workType: filters?.workType || "all",
    includeAi: Boolean(filters?.includeAi),
    fuzzy: Boolean(filters?.fuzzy),
    startDate: filters?.startDate || "",
    endDate: filters?.endDate || "",
  };
  const contextMatch = cleanTag.match(/^\s*(pid|uid|author)\s*[:：]\s*(.+)$/i);
  const requestedContext = contextMatch
    ? { kind: contextMatch[1].toLowerCase(), value: contextMatch[2].trim() }
    : { kind: "tags", value: cleanTag };

  searchPending = true;
  resultSelectionEnabled = false;
  searchButton.disabled = true;
  searchButton.textContent = "正在寻找…";
  cancelSearchButton.hidden = false;
  cancelSearchButton.disabled = false;
  $("#download").disabled = true;
  syncSearchScopedControls();
  grid.innerHTML = '<p class="loading-state">正在连接 Pixiv，可随时继续操作页面…</p>';
  $("#pagination").innerHTML = "";
  $("#count").textContent = "正在加载当前页";

  try {
    const query = new URLSearchParams({
      tag: cleanTag,
      page: String(page),
      mode: requestedFilters.mode,
      workType: requestedFilters.workType,
      includeAi: String(requestedFilters.includeAi),
      fuzzy: String(requestedFilters.fuzzy),
      startDate: requestedFilters.startDate,
      endDate: requestedFilters.endDate,
      requestId,
    });
    const data = await fetchJson(`/api/pixiv/search?${query}`, { signal: controller.signal }, 90000);
    if (controller !== searchController) return;

    activeTagQuery = cleanTag;
    activeSearchContext = requestedContext;
    activeSearchFilters = requestedFilters;
    items = Array.isArray(data.items) ? data.items : [];
    reconcileSelectedArtworkPreviews(items);
    currentPage = Number(data.page) || 1;
    pageNumbers = Array.isArray(data.availablePages) ? data.availablePages : (Array.isArray(data.pageNumbers) ? data.pageNumbers : [currentPage]);
    firstAvailablePage = pageNumbers.length ? Number(pageNumbers[0]) : currentPage;
    preloadedThrough = Number(data.preloadedThrough) || currentPage;
    searchHasMore = Boolean(data.hasMore);
    $("#tagTitle").textContent = data.label || (Array.isArray(data.tags) && data.tags.length ? data.tags.join(" + ") : (data.tag || cleanTag));
    const preloadStatus = data.preloadedThrough > currentPage ? ` · 已预加载至第 ${data.preloadedThrough} 页` : "";
    const historyStatus = currentPageNeedsMoreResults()
      ? " · 当前页尚未满，可继续加载本页"
      : (data.budgetExhausted ? " · 本次加载达到请求预算，可继续翻页" : (data.hasMore ? " · 可继续加载更早作品" : " · 已到历史末尾"));
    const fuzzyLabel = data.fuzzy ? " · 别名扩展已启用" : "";
    const dateLabel = requestedFilters.startDate ? ` · ${requestedFilters.startDate} 至 ${requestedFilters.endDate}（日本时间）` : "";
    $("#count").textContent = `已加载 ${data.total} 件 · 第 ${currentPage} 页 · 每页 ${data.perPage || 36} 件${dateLabel}${fuzzyLabel}${preloadStatus}${historyStatus}${data.truncatedDates?.length ? ` · ${data.truncatedDates.length} 个高密度日期受平台截断` : ""}`;
    resultSelectionEnabled = true;
    render();
    renderPagination();
    clearDetail();
    scheduleSearchPrefetch(cleanTag, currentPage, requestedFilters, generation);
  } catch (error) {
    if (controller.signal.aborted) {
      if (controller === searchController) {
        resultSelectionEnabled = previousView.hadCommittedResults;
        if (previousView.hadCommittedResults) {
          render();
          renderPagination();
          $("#count").textContent = previousView.count;
        } else {
          grid.innerHTML = '<div class="empty-state"><b>搜索已取消</b><p>调整条件后可以重新开始寻找。</p></div>';
          $("#pagination").innerHTML = "";
          $("#count").textContent = "搜索已取消";
        }
      }
      return;
    }
    grid.innerHTML = `<div class="error-state"><b>加载失败</b><p>${esc(error.message || "Pixiv 搜索失败")}</p><button id="retrySearch" type="button">重试当前搜索</button></div>`;
    $("#count").textContent = "连接未完成";
    $("#retrySearch")?.addEventListener("click", () => search(cleanTag, page, requestedFilters));
  } finally {
    if (controller === searchController) {
      searchController = null;
      activeSearchRequestId = null;
      searchPending = false;
      searchButton.disabled = false;
      searchButton.innerHTML = "开始寻找 <span>↗</span>";
      cancelSearchButton.disabled = false;
      cancelSearchButton.hidden = true;
      syncSearchScopedControls();
    }
  }
}

function syncResultSelectionControls() {
  grid.querySelectorAll("[data-select]").forEach((box) => {
    const item = items[Number(box.dataset.select)];
    box.checked = Boolean(item && selection.has(item.id));
  });
}

function reconcileSelectedArtworkPreviews(freshItems) {
  const freshById = new Map(freshItems.map((item) => [String(item.id), item]));
  const mergePreview = (existing) => {
    const fresh = freshById.get(String(existing.id));
    if (!fresh) return existing;
    const merged = { ...existing, ...fresh };
    if (Array.isArray(existing.pageImages) && existing.pageImages.length) {
      merged.pageImages = existing.pageImages;
      merged.thumb = fresh.thumb || existing.thumb;
    }
    return merged;
  };
  selection.snapshot().forEach(({item}) => {
    const merged = mergePreview(item);
    if (merged !== item) selection.remember(merged);
  });
  batchCandidateItems = batchCandidateItems.map(mergePreview);
}

function render() {
  gallery.classList.add("in");
  grid.className = "grid";
  updateSelectionBar();
  syncSearchScopedControls();
  if (!items.length) {
    grid.innerHTML = currentPageNeedsMoreResults()
      ? '<p class="empty-state">本次尚未找到符合条件的作品，可点击“继续加载本页”再查一段。</p>'
      : '<p class="empty-state">当前页没有符合安全范围的作品。</p>';
    return;
  }

  grid.innerHTML = items.map((item, index) => {
    const image = `<img src="${item.thumb}" alt="${esc(item.title)}" loading="lazy" decoding="async">`;
    const checked = selection.has(item.id) ? "checked" : "";
    const badges = `${item.workType === "ugoira" ? '<span class="series type-ugoira">动图</span>' : ""}${item.pages > 1 ? `<span class="series">叠图 ${item.pages}P</span>` : ""}`;
    const ugoiraHover = item.workType === "ugoira" ? ` data-ugoira-preview="${esc(String(item.id))}"` : "";
    return `<article class="card" tabindex="0" data-i="${index}"><label class="card-select"><input type="checkbox" data-select="${index}" ${checked}><span>选择</span></label><div class="poster"${ugoiraHover}>${image}${badges}</div><div class="meta"><div><h3>${esc(item.title)}</h3><p>${esc(item.artist)} · ${item.tags.map((tag) => `#${esc(tag)}`).join(" ")}</p></div><span>♡ ${Number(item.bookmarks || 0).toLocaleString()}</span></div></article>`;
  }).join("");
  installImageFallbacks(grid);
  attachUgoiraHoverTargets(grid);

  grid.querySelectorAll("[data-i]").forEach((card) => {
    const open = () => {
      select(Number(card.dataset.i));
      $("#detail").scrollIntoView({ behavior: "auto" });
    };
    card.onclick = (event) => { if (!event.target.closest(".card-select")) open(); };
    card.onkeydown = (event) => {
      if (event.target.closest(".card-select")) return;
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
  const canLoadNext = currentPage < preloadedThrough || searchHasMore;
  const incomplete = currentPageNeedsMoreResults();
  const nextPage = incomplete ? currentPage : currentPage + 1;
  const nextLabel = incomplete ? "继续加载当前页" : (currentPage < preloadedThrough ? "下一页" : "继续加载下一页");
  // Cache residency is not a navigation boundary: evicted pages can be reloaded.
  const lastPage = Math.max(currentPage, preloadedThrough, ...pageNumbers.filter(Number.isInteger));
  const visiblePages = new Set([1, lastPage]);
  for (let number = Math.max(1, currentPage - 2); number <= Math.min(lastPage, currentPage + 2); number += 1) visiblePages.add(number);
  const navigationPages = [...visiblePages].sort((a, b) => a - b).flatMap((number, index, pages) => (
    index && number > pages[index - 1] + 1 ? [null, number] : [number]
  ));
  pagination.innerHTML = `<button ${currentPage <= 1 ? "disabled" : ""} data-page="${currentPage - 1}" aria-label="上一页">←</button>${navigationPages.map((number) => number === null ? '<span class="page-gap">…</span>' : `<button class="${number === currentPage ? "active" : ""}" ${incomplete && number > currentPage ? "disabled" : ""} data-page="${number}">${number}</button>`).join("")}<button ${canLoadNext ? "" : "disabled"} data-page="${nextPage}" aria-label="${nextLabel}">${incomplete ? "继续加载本页" : "→"}</button>`;
  pagination.querySelectorAll("button:not([disabled])").forEach((button) => {
    button.onclick = () => {
      navigateToPage(Number(button.dataset.page));
    };
  });
  updatePaginationDock();
  updateFloatingChrome();
}

function updatePaginationDock() {
  const dock = document.querySelector(".pagination-dock");
  const hasPagination = Boolean($("#pagination").children.length) || selection.size > 0;
  const overlayOpen = !$("#allViewer").hidden || basketPageOpen();
  const galleryRect = $("#gallery").getBoundingClientRect();
  const dockTop = window.innerHeight - (dock.offsetHeight || 0);
  const dockOverGallery = galleryRect.top < dockTop && galleryRect.bottom > dockTop;
  dock.classList.toggle("is-visible", hasPagination && !overlayOpen && dockOverGallery);
  const rail = document.querySelector('.page-rail');
  rail.hidden = overlayOpen;
  const sections = ['home', 'gallery', 'detail'];
  const marker = window.innerHeight * .4;
  const active = sections.reduce((current, id) => $(`#${id}`).getBoundingClientRect().top <= marker ? id : current, 'home');
  rail.querySelectorAll('a').forEach(link => {
    if (link.hash === `#${active}`) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  });
}

function activeScrollSurface() {
  if (!$("#allViewer").hidden) return $("#allViewer");
  if (basketPageOpen()) return $("#basketPage");
  return window;
}

function updateFloatingChrome() {
  const surface = activeScrollSurface();
  const overlayOpen = surface !== window;
  const scrollOffset = surface === window ? window.scrollY : surface.scrollTop;
  $("#backTop").classList.toggle("is-visible", scrollOffset > 600);
  const entry = $("#basketEntry");
  const count = selection.size;
  entry.classList.remove("is-visible");
  document.querySelector('.page-rail').hidden = overlayOpen;
  entry.textContent = `采集篮 ${count} 作品 · ${selectedPageCount()} 张`;
  entry.disabled = selection.locked || searchPending;
}

function schedulePaginationDockUpdate() {
  if (paginationDockFrame !== null) return;
  const run = () => {
    paginationDockFrame = null;
    updatePaginationDock();
    updateFloatingChrome();
  };
  paginationDockFrame = 1;
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  else setTimeout(run, 16);
}

window.addEventListener("scroll", schedulePaginationDockUpdate, { passive: true });
window.addEventListener("resize", schedulePaginationDockUpdate, { passive: true });
document.querySelectorAll('.page-rail a').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  document.querySelector(link.hash).scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}));
$("#basketPage").addEventListener("scroll", schedulePaginationDockUpdate, { passive: true });
$("#allViewer").addEventListener("scroll", schedulePaginationDockUpdate, { passive: true });

async function select(index) {
  let item = items[index];
  if (!item) return;
  clearDetail("正在加载作品详情…");
  closeAllViewer();
  invalidateDetailView();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  const detailContext = { ...activeSearchContext };
  activeArtworkId = item.id;

  announceToast("");
  $("#dTitle").textContent = "正在加载作品详情…";
  $("#deck").innerHTML = '<p class="loading-state">正在读取作品信息</p>';
  $("#download").disabled = true;

  try {
    if (item.source === "pixiv" && !item.pageImages) {
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, 18000);
      if (controller !== detailController || generation !== viewGeneration) return;
      rememberArtworkDetail(item);
    }
    if (controller !== detailController || generation !== viewGeneration) return;
    renderDetail(item, index, detailContext);
  } catch (error) {
    if (controller.signal.aborted) return;
    $("#dTitle").textContent = "作品详情加载失败";
    $("#deck").innerHTML = "";
    announceToast(error.message || "作品详情加载失败");
  } finally {
    if (controller === detailController) detailController = null;
  }
}

function downloadButtonLabel(item) {
  const count = currentDownloadPages(item).length;
  if (!count) return "选择图片后下载";
  if (item?.workType === "ugoira") {
    const format = $("#format").value;
    return `下载动图 ${format === "gif" ? "GIF" : format === "mp4" ? "MP4" : "ZIP"} ↓`;
  }
  return item.pages === 1 ? "下载本图 ↓" : `下载已选 ${count} 张 ↓`;
}

function currentDownloadPages(item) {
  if (!item) return [];
  if (item.pages === 1) return [0];
  return [...(selection.get(item.id)?.pages || [])].sort((a, b) => a - b);
}

function detailView(prefix, deck, hint) {
  const names = ["Title", "WorkType", "Desc", "Artist", "Size", "Bookmarks", "Date", "Tags"];
  return createArtworkDetailView({
    deck: $(deck), hint: $(hint),
    fields: Object.fromEntries(names.map(name => [name, $(`#${prefix}${name}`)])),
    escape: esc, installImages: installImageFallbacks,
  });
}
const normalDetailView = detailView("d", "#deck", "#deckHint");
const basketDetailView = detailView("basketDetail", "#basketDetailDeck", "#basketDetailDeckHint");

function renderDetail(item, index, detailContext = activeSearchContext) {
  normalDetailView.reset();
  currentDetailItem = item;
  currentDetailContext = { ...detailContext };
  collectionPageOffset = 0;
  const visible = normalDetailView.render(item, page => `/api/image/${(currentPage - 1) * 12 + index}/${page}?size=preview`);
  renderCollectionPageWindow(item);
  $("#viewAll").hidden = item.pages <= visible;
  $("#quality").innerHTML = item.qualities.map((quality) => `<option value="${quality.id}">${esc(quality.label)}${quality.width > 0 && quality.height > 0 ? ` · ${quality.width} × ${quality.height}` : ""}</option>`).join("");
  $("#format").innerHTML = downloadFormatOptions(item.formats);
  $("#download").textContent = downloadButtonLabel(item);
  syncSearchScopedControls();
  updateFormatHint();
}

function syncBasketArtworkMeta() {
  if (!basketDetailItem) return;
  const selectedPages = selection.get(basketDetailItem.id)?.pages;
  $("#batchSummary").textContent = `已选 ${selectedPages?.size || 0}/${basketDetailItem.pages} 张`;
  $("#basketModeHint").textContent = `${basketDetailItem.artist} · 在作品详情中逐张勾选`;
}

function detailSelectionOrigin(item) {
  const inBasket = basketPageOpen();
  return {
    context: (inBasket && batchCandidateContextByArtwork.get(item.id)) || currentDetailContext || activeSearchContext,
    resultPage: (inBasket && batchCandidateResultPageByArtwork.get(item.id)) || currentPage,
  };
}

function bindCollectionPageInputs(pagesRoot, item) {
  pagesRoot.querySelectorAll("[data-collection-page]").forEach((box) => {
    box.onchange = () => {
      const page = Number(box.dataset.collectionPage);
      const result = selection.setPage(item, page, box.checked, detailSelectionOrigin(item));
      if (!result.accepted) {
        box.checked = Boolean(selection.get(item.id)?.pages.has(page));
        if (result.reason !== "locked") reportSelectionRejection(result);
        return;
      }
      updateSelectionBar();
      syncResultSelectionControls();
      if (basketPageOpen()) {
        syncBasketHeader();
        syncBasketArtworkMeta();
      }
    };
  });
}

function renderCollectionWindowInto(item, pagesRoot, moreButton) {
  const pages = item.pageImages || [];
  const chosenPages = selection.get(item.id)?.pages;
  const start = collectionPageOffset;
  const end = Math.min(pages.length, start + DETAIL_PAGE_WINDOW);
  const ugoiraHover = item.workType === "ugoira" ? ` data-ugoira-preview="${esc(String(item.id))}"` : "";
  pagesRoot.innerHTML = pages.slice(start, end).map((page, localIndex) => {
    const pageNo = start + localIndex;
    return `<label class="page-select"${ugoiraHover}><input type="checkbox" data-collection-page="${pageNo}" ${chosenPages?.has(pageNo) ? "checked" : ""} ${selection.locked ? "disabled" : ""}><img src="${esc(page.regular)}" data-detail-artwork="${esc(item.id)}" data-detail-page="${pageNo}" alt="${esc(item.title)} 第 ${pageNo + 1} 张" loading="lazy" decoding="async"><span>${pageNo + 1}</span></label>`;
  }).join("");
  installImageFallbacks(pagesRoot);
  attachUgoiraHoverTargets(pagesRoot);
  bindCollectionPageInputs(pagesRoot, item);
  moreButton.hidden = pages.length <= DETAIL_PAGE_WINDOW;
  moreButton.textContent = `${start + 1}–${Math.max(start + 1, end)} / ${pages.length} · 显示后 ${DETAIL_PAGE_WINDOW} 张`;
}

function renderCollectionPageWindow(item = currentDetailItem) {
  if (!item) return;
  renderCollectionWindowInto(item, $("#collectionPages"), $("#collectionPageMore"));
}

function renderViewerWindow(item) {
  const pages = Array.isArray(item?.pageImages) ? item.pageImages : [];
  const lastStart = Math.max(0, Math.floor(Math.max(0, pages.length - 1) / VIEWER_PAGE_WINDOW) * VIEWER_PAGE_WINDOW);
  const start = Math.min(Math.max(0, viewerPageOffset), lastStart);
  const end = Math.min(pages.length, start + VIEWER_PAGE_WINDOW);
  viewerPageOffset = start;
  const navigation = pages.length > VIEWER_PAGE_WINDOW
    ? `<div class="pagination viewer-window-pagination" style="grid-column:1/-1" aria-label="连续查看分页"><button type="button" data-viewer-window="${Math.max(0, start - VIEWER_PAGE_WINDOW)}" ${start === 0 ? "disabled" : ""}>← 前 ${VIEWER_PAGE_WINDOW} 张</button><span>${start + 1}–${end} / ${pages.length}</span><button type="button" data-viewer-window="${Math.min(lastStart, start + VIEWER_PAGE_WINDOW)}" ${end >= pages.length ? "disabled" : ""}>后 ${VIEWER_PAGE_WINDOW} 张 →</button></div>`
    : "";
  $("#viewerGrid").innerHTML = `${navigation}${item.pageImages.slice(start, end).map((page, localIndex) => {
    const pageNo = start + localIndex;
    return `<figure><img src="${esc(page.regular)}" data-detail-artwork="${esc(item.id)}" data-detail-page="${pageNo}" alt="${esc(item.title)} 第 ${pageNo + 1} 张" loading="lazy" decoding="async"><span>${String(pageNo + 1).padStart(2, "0")} / ${String(pages.length).padStart(2, "0")}</span></figure>`;
  }).join("")}`;
  installImageFallbacks($("#viewerGrid"));
  $("#viewerGrid").querySelectorAll("[data-viewer-window]:not([disabled])").forEach((button) => {
    button.onclick = () => {
      viewerPageOffset = Number(button.dataset.viewerWindow) || 0;
      const latestItem = String(currentDetailItem?.id || "") === String(item.id)
        ? currentDetailItem
        : selection.get(item.id)?.item || items.find((row) => String(row.id) === String(item.id)) || item;
      renderViewerWindow(latestItem);
      $("#allViewer").scrollTop = 0;
    };
  });
  $("#viewerCount").textContent = pages.length > VIEWER_PAGE_WINDOW
    ? `${pages.length} 张 · 当前 ${start + 1}–${end}`
    : `${pages.length} 张 · 连续浏览`;
}

function openAllViewer() {
  const activeDetail = currentDetailItem
    && activeArtworkId !== null
    && String(currentDetailItem.id) === String(activeArtworkId)
    ? currentDetailItem
    : null;
  const item = activeDetail || selection.get(activeArtworkId)?.item || items.find((row) => row.id === activeArtworkId);
  if (!item?.pageImages) return;
  viewerPageOffset = 0;
  $("#viewerTitle").textContent = item.title;
  $("#viewerArtist").textContent = item.artist;
  renderViewerWindow(item);
  $("#allViewer").hidden = false;
  document.body.classList.add("viewer-open");
  $("#allViewer").scrollTop = 0;
  schedulePaginationDockUpdate();
}

function closeAllViewer() {
  viewerPageOffset = 0;
  $("#allViewer").hidden = true;
  document.body.classList.remove("viewer-open");
  $("#viewerGrid").innerHTML = "";
  $("#viewerTitle").textContent = "";
  $("#viewerArtist").textContent = "";
  $("#viewerCount").textContent = "";
  schedulePaginationDockUpdate();
}

$("#viewAll").onclick = openAllViewer;
$("#closeViewer").onclick = closeAllViewer;
function advanceCollectionWindow(item, rerender) {
  const pageCount = (item?.pageImages || []).length;
  collectionPageOffset = collectionPageOffset + DETAIL_PAGE_WINDOW >= pageCount
    ? 0
    : collectionPageOffset + DETAIL_PAGE_WINDOW;
  rerender();
}

$("#collectionPageMore").onclick = () => advanceCollectionWindow(currentDetailItem, () => renderCollectionPageWindow());
$("#basketPageMore").onclick = () => {
  if (!basketDetailItem) return;
  advanceCollectionWindow(basketDetailItem, () => renderBasketArtworkDetail(basketDetailItem));
};
$("#basketViewAll").onclick = openAllViewer;
$("#backTop").onclick = () => activeScrollSurface().scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? "auto" : "smooth" });
function openBatchHub() {
  if (selection.locked || searchPending) return;
  if (!selection.size) {
    $("#pageSelectionStatus").textContent = "请先勾选至少一个作品";
    return;
  }
  showBatchDetail();
}
$("#basketEntry").onclick = openBatchHub;

function basketPageOpen() {
  return !$("#basketPage").hidden;
}

function openBasketPage() {
  $("#basketPage").hidden = false;
  document.body.classList.add("basket-page-open");
  updateFloatingChrome();
}

function updateBatchDetailSummary() {
  $("#batchDetailSummary").textContent = `${selection.size} 个作品 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张已选`;
}

function showBatchDetail() {
  const quality = $("#quality").value;
  clearDetail();
  closeAllViewer();
  invalidateDetailView();
  document.body.classList.add("batch-mode");
  renderBatchDownloadOptions(quality);
  updateBatchDetailSummary();
  syncSearchScopedControls();
  $("#detail").scrollIntoView({ behavior: "auto" });
}

function closeBasketPage() {
  const wasOpen = !$("#basketPage").hidden;
  $("#basketPage").hidden = true;
  document.body.classList.remove("basket-page-open");
  basketReturnMode = "summary";
  basketDetailItem = null;
  basketDetailView.reset();
  ugoiraPreview.stop();
  // Restricted previews must not linger in the hidden basket DOM.
  $("#batchCollections").innerHTML = "";
  $("#basketArtworkDetail").hidden = true;
  $("#basketDetailDeck").innerHTML = "";
  $("#basketPages").innerHTML = "";
  $("#basketPageMore").hidden = true;
  $("#basketViewAll").hidden = true;
  if (!wasOpen) return;
  invalidateDetailView();
  updateFloatingChrome();
}

function showBasketPane(mode) {
  ugoiraPreview.stop();
  basketReturnMode = mode;
  $("#batchCollections").hidden = mode !== "picker";
  $("#basketArtworkDetail").hidden = mode !== "detail";
}

function ensureDownloadOptionDefaults() {
  if (!$("#quality").options.length) {
    $("#quality").innerHTML = '<option value="regular">预览清晰度</option><option value="original">原图</option>';
  }
  if (!$("#format").options.length) {
    $("#format").innerHTML = '<option value="source">保留源格式</option>';
  }
}

function downloadFormatOptions(formats) {
  return formats.map(format => {
    const unavailable = format.id === "mp4" && animationFormats.mp4 === false;
    const label = unavailable ? "MP4 视频（未检测到 FFmpeg）" : format.label;
    return `<option value="${format.id}" ${unavailable ? "disabled" : ""}>${esc(label)}</option>`;
  }).join("");
}

function renderBatchDownloadOptions(quality = $("#quality").value, format = $("#format").value) {
  $("#quality").innerHTML = '<option value="regular">标准清晰度</option><option value="original">原始清晰度</option>';
  $("#quality").value = quality === "original" ? "original" : "regular";
  $("#format").innerHTML = downloadFormatOptions([
    {id: "source", label: "保留源格式（动图为 ZIP + JSON）"},
    {id: "gif", label: "动图转 GIF，静态图保留源格式"},
    {id: "mp4", label: "动图转 MP4（需本机 FFmpeg）"},
  ]);
  $("#format").value = ["gif", "mp4"].includes(format) && (format !== "mp4" || animationFormats.mp4 !== false)
    ? format : "source";
  updateFormatHint();
}

function readDownloadOptions() {
  return {
    quality: $("#quality").value || "regular",
    ugoiraFormat: ["gif", "mp4"].includes($("#format").value) ? $("#format").value : "source",
    saveRoot: $("#saveRoot").value.trim(),
    createFolder: $("#createFolder").checked,
    groupArtworks: Boolean($("#groupArtworks")?.checked),
  };
}

function syncBasketHeader() {
  const chosen = batchCandidateItems;
  if (!chosen.length) {
    $("#batchSummary").textContent = `${selectedPageCount()}/${MAX_SELECTED_PAGES} 张图片已选`;
    return;
  }
  const selectedCount = chosen.filter((item) => selection.get(item.id)?.pages?.size).length;
  $("#batchSummary").textContent = `${selectedCount}/${chosen.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
}

function openSelectionBasket() {
  if (selection.locked || searchPending) return;
  const chosen = selection.snapshot().map(row => row.item).filter((item) => selection.get(item.id)?.pages?.size);
  if (!chosen.length) {
    $("#pageSelectionStatus").textContent = "请先勾选至少一个作品";
    return;
  }
  invalidateDetailView();
  batchCandidateItems = chosen;
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  for (const item of chosen) {
    batchCandidateContextByArtwork.set(item.id, selection.get(item.id)?.context || { ...activeSearchContext });
    batchCandidateResultPageByArtwork.set(item.id, selection.get(item.id)?.resultPage || currentPage);
  }
  basketArtworkOffset = 0;
  ensureDownloadOptionDefaults();
  openBasketArtworkPicker();
  openBasketPage();
  $("#basketPage").scrollTop = 0;
}

function applyBasketArtworkSelection(item, box) {
  const accepted = toggleArtworkSelection(item, box.checked, detailSelectionOrigin(item));
  if (!accepted) {
    box.checked = Boolean(selection.get(item.id)?.pages?.size);
    return false;
  }
  const selectedPages = selection.get(item.id)?.pages;
  const selected = Boolean(selectedPages?.size);
  box.checked = selected;
  const card = box.closest(".batch-collection");
  card?.classList.toggle("is-selected", selected);
  const label = card?.querySelector(".batch-card-select");
  label?.setAttribute("aria-label", `${selected ? "取消选择" : "选择"} ${item.title}`);
  const selectionLabel = card?.querySelector(".batch-card-copy small");
  if (selectionLabel) selectionLabel.textContent = `${item.artist} · 已选 ${selectedPages?.size || 0}/${item.pages} 张`;

  const selectedCount = batchCandidateItems.filter((candidate) => selection.get(candidate.id)?.pages?.size).length;
  $("#batchSummary").textContent = `${selectedCount}/${batchCandidateItems.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
  syncResultSelectionControls();
  return true;
}

function openBasketArtworkPicker() {
  const chosen = batchCandidateItems;
  if (!chosen.length) return;
  const lastStart = Math.max(0, Math.floor(Math.max(0, chosen.length - 1) / BASKET_ARTWORK_WINDOW) * BASKET_ARTWORK_WINDOW);
  const start = Math.min(Math.max(0, basketArtworkOffset), lastStart);
  const end = Math.min(chosen.length, start + BASKET_ARTWORK_WINDOW);
  basketArtworkOffset = start;
  ensureDownloadOptionDefaults();
  showBasketPane("picker");
  const selectedCount = chosen.filter((item) => selection.get(item.id)?.pages?.size).length;
  $("#basketTitle").textContent = "采集篮 · 选择要下载的作品";
  $("#basketModeHint").textContent = "点击作品进入详情并逐张选择图片；返回回到第三页下载选项。";
  $("#batchSummary").textContent = `${selectedCount}/${chosen.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
  const navigation = chosen.length > BASKET_ARTWORK_WINDOW
    ? `<div class="pagination basket-window-pagination" style="grid-column:1/-1" aria-label="采集篮作品分页"><button type="button" data-basket-window="${Math.max(0, start - BASKET_ARTWORK_WINDOW)}" ${start === 0 ? "disabled" : ""}>← 前 ${BASKET_ARTWORK_WINDOW} 件</button><span>${start + 1}–${end} / ${chosen.length}</span><button type="button" data-basket-window="${Math.min(lastStart, start + BASKET_ARTWORK_WINDOW)}" ${end >= chosen.length ? "disabled" : ""}>后 ${BASKET_ARTWORK_WINDOW} 件 →</button></div>`
    : "";
  $("#batchCollections").innerHTML = `${navigation}${chosen.slice(start, end).map((item) => {
    const selectedPages = selection.get(item.id)?.pages;
    const selected = Boolean(selectedPages?.size);
    const selectedPagesLabel = `${selectedPages?.size || 0}/${item.pages} 张`;
    const ugoiraHover = item.workType === "ugoira" ? ` data-ugoira-preview="${esc(String(item.id))}"` : "";
    return `<article class="batch-collection ${selected ? "is-selected" : ""}" data-batch-artwork="${esc(item.id)}"><label class="batch-card-select" aria-label="${selected ? "取消选择" : "选择"} ${esc(item.title)}"><input type="checkbox" data-batch-select="${esc(item.id)}" ${selected ? "checked" : ""}><span aria-hidden="true">✓</span></label><button class="batch-card-open" type="button" data-open-collection="${esc(item.id)}" aria-label="打开 ${esc(item.title)} 的作品详情并选择图片"><span class="batch-card-cover"${ugoiraHover}><img src="${esc(item.thumb)}" data-basket-artwork="${esc(item.id)}" alt="${esc(item.title)}" loading="lazy" decoding="async"><span class="batch-page-count" aria-hidden="true">${item.pages}P</span></span><span class="batch-card-copy"><b>${esc(item.title)}</b><small>${esc(item.artist)} · 已选 ${selectedPagesLabel}</small></span></button></article>`;
  }).join("")}`;
  installImageFallbacks($("#batchCollections"));
  attachUgoiraHoverTargets($("#batchCollections"));
  $("#batchCollections").querySelectorAll("[data-basket-window]:not([disabled])").forEach((button) => {
    button.onclick = () => {
      basketArtworkOffset = Number(button.dataset.basketWindow) || 0;
      openBasketArtworkPicker();
      $("#basketPage").scrollTop = 0;
    };
  });
  $("#batchCollections").querySelectorAll("[data-batch-select]").forEach((box) => {
    box.onchange = () => {
      const item = batchCandidateItems.find((candidate) => candidate.id === box.dataset.batchSelect);
      if (!item) return;
      applyBasketArtworkSelection(item, box);
    };
  });
  $("#batchCollections").querySelectorAll("[data-open-collection]").forEach((button) => {
    button.onclick = () => openBatchCollection(button.dataset.openCollection);
  });
}

function selectedGroups() {
  return selection.snapshot().map(row => ({
    id: row.id,
    pages: [...row.pages].sort((a, b) => a - b),
    context: row.context,
  }));
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

function prepareDownloadTask(chunks, taskOptions, previousTask) {
  const signature = JSON.stringify({ chunks, taskOptions });
  if (
    previousTask?.signature === signature
    && previousTask.remainingChunks.length
  ) {
    return previousTask;
  }
  return {
    signature,
    authorizationRevision: downloadAuthorizationRevision,
    remainingChunks: chunks.map((chunk) => ({ ...chunk, requestId: nextDownloadRequestId() })),
    savedCount: 0,
    fileCount: 0,
    firstSaved: "",
    cleanupPending: false,
    completedBatches: 0,
    totalBatches: chunks.length,
  };
}

function nextDownloadRequestId() {
  return globalThis.crypto?.randomUUID?.() || `download-${nextSearchRequestId()}`;
}

async function fetchDownloadResult(request, chunk, task) {
  const body = JSON.stringify({ ...request.body, requestId: chunk.requestId });
  let timeoutRetries = 0;
  while (true) {
    if (task.authorizationRevision !== downloadAuthorizationRevision) {
      throw new Error("Pixiv 账户状态已变更，下载已中断，请重新发起");
    }
    let data;
    try {
      data = await fetchJson(request.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      }, request.timeout);
    } catch (error) {
      // A slow local save may outlive the response. Reconnect once using the
      // same identity; the backend returns pending or the completed result.
      if (error.timedOut && timeoutRetries++ === 0) continue;
      if (error.requestIdConflict) chunk.requestId = nextDownloadRequestId();
      throw error;
    }
    if (!data.pending) return data;
    const delay = Math.max(250, Math.min(3000, Number(data.retryAfterMs) || 1000));
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
}

async function executeDownloadTask(task, requestForChunk, reportProgress) {
  const chunks = task.remainingChunks;
  for (let index = 0; index < chunks.length; index += 1) {
    const chunk = chunks[index];
    reportProgress();
    const request = requestForChunk(chunk);
    const data = await fetchDownloadResult(request, chunk, task);
    task.savedCount += data.pages ?? chunk.pageCount;
    task.fileCount += data.saved?.length || 0;
    task.firstSaved ||= data.saved?.[0] || "";
    task.cleanupPending ||= Boolean(data.cleanupPending);
    task.completedBatches += 1;
    // Advance only after publication succeeds; failures retain this chunk.
    task.remainingChunks = chunks.slice(index + 1);
  }
}

function setDownloadButtonState(button, text, disabled) {
  button.disabled = disabled;
  button.textContent = text;
}

function setBasketSelectionLocked(locked) {
  selection.setLocked(locked);
  const controls = document.querySelectorAll("[data-select],[data-batch-select],[data-collection-page],[data-open-collection],#selectAllPage,#clearPageSelection,#clearSelection,#basketBack,#basketEntry,#openBasketPicker,#searchForm input,#searchSubmit,#searchForm select,#pagination button");
  controls.forEach((control) => {
    if (locked) {
      control.dataset.basketLockDisabled = String(control.disabled);
      control.disabled = true;
    } else {
      control.disabled = control.dataset.basketLockDisabled === "true";
      delete control.dataset.basketLockDisabled;
    }
  });
  if (!locked) {
    $("#clearSelection").disabled = selection.size === 0;
    $("#basketEntry").disabled = selection.locked || searchPending;
  }
  updateFloatingChrome();
  syncSearchScopedControls();
}

function downloadPayload(item, sourceIndex) {
  if (item.source === "pixiv") {
    const downloadContext = currentDetailContext || activeSearchContext;
    return {
      endpoint: "/api/pixiv/download",
      body: { id: item.id, pages: currentDownloadPages(item), ...readDownloadOptions(), context: downloadContext },
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
    for (let offset = 0; offset < group.pages.length; offset += DOWNLOAD_CHUNK_PAGES) {
      const part = { ...group, pages: group.pages.slice(offset, offset + DOWNLOAD_CHUNK_PAGES) };
      if (current.length && (
        pageCount + part.pages.length > DOWNLOAD_CHUNK_PAGES
        || current.length >= DOWNLOAD_CHUNK_ARTWORKS
      )) {
        chunks.push({ groups: current, pageCount });
        current = [];
        pageCount = 0;
      }
      current.push(part);
      pageCount += part.pages.length;
    }
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
    (id) => (selection.get(id)?.resultPage || currentPage) < oldestRetainedPage,
  );
}

function navigateToPage(page) {
  if (selection.locked) return;
  if (page > currentPage && currentPageNeedsMoreResults()) page = currentPage;
  if (selectionWouldBeEvicted(page)) {
    openCapacityDialog(page);
    return;
  }
  search(activeTagQuery, page, { ...activeSearchFilters });
  scrollToResults();
}

function archiveAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  const detached = detachSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    announceToast(`已将当前 ${detached} 个作品放入采集篮；继续翻页不会下载原图`);
    search(activeTagQuery, page, { ...activeSearchFilters });
    scrollToResults();
  }
}

function clearAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  clearSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    search(activeTagQuery, page, { ...activeSearchFilters });
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

function renderBasketArtworkDetail(item) {
  basketDetailItem = item;
  basketDetailView.reset();
  showBasketPane("detail");
  $("#basketTitle").textContent = "采集篮 · 作品详情";
  const visible = basketDetailView.render(item);
  renderCollectionWindowInto(item, $("#basketPages"), $("#basketPageMore"));
  $("#basketViewAll").hidden = item.pages <= visible;
  syncBasketHeader();
  syncBasketArtworkMeta();
}

async function openBatchCollection(id) {
  let item = selection.get(id)?.item || batchCandidateItems.find((candidate) => candidate.id === id);
  if (!item) return;
  invalidateDetailView();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  try {
    if (item.source === "pixiv" && (!item.pageImages || staleBasketPreviewIds.has(String(item.id)))) {
      $("#basketTitle").textContent = "正在加载合集详情…";
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, 18000);
      if (controller !== detailController || generation !== viewGeneration) return;
      rememberArtworkDetail(item);
    }
    if (controller !== detailController || generation !== viewGeneration) return;
    activeArtworkId = item.id;
    currentDetailItem = item;
    currentDetailContext = {
      ...(batchCandidateContextByArtwork.get(item.id)
        || selection.get(item.id)?.context
        || activeSearchContext),
    };
    collectionPageOffset = 0;
    renderBasketArtworkDetail(item);
    $("#basketPage").scrollTop = 0;
  } catch (error) {
    if (controller.signal.aborted) return;
    announceToast(error.message || "作品详情加载失败");
  } finally {
    if (controller === detailController) detailController = null;
  }
}

$("#openBasketPicker").onclick = openSelectionBasket;
$("#basketBack").onclick = () => {
  if (selection.locked) return;
  invalidateDetailView();
  if (basketReturnMode === "detail") {
    basketDetailItem = null;
    basketDetailView.reset();
    $("#basketDetailDeck").innerHTML = "";
    $("#basketPages").innerHTML = "";
    openBasketArtworkPicker();
    $("#basketPage").scrollTop = 0;
  }
  else showBatchDetail();
};
$("#selectAllPage").onclick = selectAllCurrentPage;
$("#clearPageSelection").onclick = clearAllCurrentPage;
$("#clearSelection").onclick = () => {
  if (selection.locked || searchPending) return;
  invalidateDetailView();
  clearAllSelection();
  clearDetail();
  render();
};
$("#openBatch").onclick = openSelectionBasket;

$("#batchDownload").onclick = async () => {
  if (selection.locked || searchPending || singleDownloadPending) return;
  const groups = selectedGroups();
  if (!groups.length) {
    announceToast("请至少选择一张图片");
    return;
  }
  let plannedChunks;
  try {
    plannedChunks = planContextDownloadChunks(groups);
  } catch (error) {
    announceToast(error.message);
    return;
  }
  const button = $("#batchDownload");
  const taskOptions = readDownloadOptions();
  const task = prepareDownloadTask(plannedChunks, taskOptions, resumableBatchTask);
  setDownloadButtonState(button, "准备保存…", true);
  setBasketSelectionLocked(true);
  announceToast("本次任务已锁定当前勾选；完成前不能修改采集篮。");
  showTaskDock("批量下载", "任务已锁定，正在准备保存…", 0);
  try {
    await executeDownloadTask(task, (chunk) => ({
      endpoint: "/api/pixiv/batch-download",
      body: { groups: chunk.groups, ...taskOptions, context: chunk.context },
      timeout: 300000,
    }), () => {
      setDownloadButtonState(button, `正在保存第 ${task.completedBatches + 1}/${task.totalBatches} 批…`, true);
      showTaskDock("批量下载", `第 ${task.completedBatches + 1}/${task.totalBatches} 批 · 已保存 ${task.savedCount} 张`, 0);
    });
    resumableBatchTask = null;
    announceToast(`已保存 ${task.savedCount} 张图片，共 ${task.totalBatches} 批${task.cleanupPending ? "；临时文件清理未完成，请检查日志" : ""}`);
    showTaskDock("批量下载完成", `已保存 ${task.savedCount} 张图片，共 ${task.totalBatches} 批`);
  } catch (error) {
    resumableBatchTask = task.authorizationRevision === downloadAuthorizationRevision ? task : null;
    const prefix = task.savedCount ? `已保存 ${task.savedCount} 张；后续` : "批量下载";
    const retryHint = resumableBatchTask
      ? `再次点击只继续剩余 ${task.remainingChunks.length} 批。`
      : "账户状态已变更，请重新确认后发起下载。";
    announceToast(`${prefix}失败：${error.message}。${retryHint}`);
    showTaskDock("批量下载中断", `${prefix}失败：${error.message}；${retryHint}`, 8000);
  } finally {
    setDownloadButtonState(button, "下载已勾选图片", false);
    setBasketSelectionLocked(false);
  }
};

addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (document.querySelector("dialog[open]")) return;
  if (!$("#allViewer").hidden) {
    closeAllViewer();
    return;
  }
  if (basketPageOpen() && !selection.locked) $("#basketBack").click();
});

function updateFormatHint() {
  const quality = $("#quality").selectedOptions[0]?.textContent || "";
  const format = $("#format").selectedOptions[0]?.textContent || "";
  $("#qualityText").textContent = quality;
  $("#formatText").textContent = format;
  const animation = $("#format").value === "gif"
    ? "动图转为 GIF；色彩会量化，帧时长按 10ms 精度保存，保留透明背景。单帧小于 10ms 时请选 MP4 或原始帧 ZIP。"
    : $("#format").value === "mp4"
      ? "动图转为 H.264 MP4；需本机 FFmpeg，透明区域以白底合成。"
      : "动图保存官方帧 ZIP 和帧延迟 JSON。";
  if (document.body.classList.contains("batch-mode")) {
    $("#formatHint").textContent = `静态图片保留源格式；${animation}`;
  } else if (currentDetailItem?.workType === "ugoira") {
    $("#formatHint").textContent = `${animation} ${quality}；超大文件或超出转换资源预算时，请选择标准清晰度或原始帧 ZIP。`;
    if (!singleDownloadPending) $("#download").textContent = downloadButtonLabel(currentDetailItem);
  } else {
    $("#formatHint").textContent = quality ? `将按 ${quality}，${format} 保存。源格式不可转换时会保留原扩展名。` : "";
  }
}

$("#quality").onchange = updateFormatHint;
$("#format").onchange = updateFormatHint;
$("#searchForm").onsubmit = (event) => {
  event.preventDefault();
  search($("#tag").value, 1, readSearchFilters());
  scrollToResults();
};

cancelSearchButton.onclick = () => {
  if (!searchController) return;
  cancelSearchButton.disabled = true;
  $("#count").textContent = "正在停止搜索…";
  cancelActiveSearch();
};

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
    if (data.error) throw new Error(data.error);
    if (data.selected) {
      $("#saveRoot").value = data.selected;
      announceToast(`保存位置：${data.selected}`);
    } else if (data.cancelled) {
      announceToast("已取消目录选择");
    }
  } catch (error) {
    announceToast(error.message || "目录选择失败");
  } finally {
    button.disabled = false;
    button.textContent = "浏览…";
  }
};

$("#download").onclick = async () => {
  const item = currentDetailItem && String(currentDetailItem.id) === String(activeArtworkId)
    ? currentDetailItem
    : selection.get(activeArtworkId)?.item || items.find((row) => row.id === activeArtworkId);
  if (!item || selection.locked || searchPending || singleDownloadPending) return;
  if (!currentDownloadPages(item).length) {
    announceToast("请至少勾选一张图片");
    return;
  }
  const sourceIndex = Math.max(0, items.findIndex((row) => row.id === item.id));
  const request = downloadPayload(item, sourceIndex);
  const chunks = item.source === "pixiv"
    ? planDownloadChunks([{ id: item.id, pages: request.body.pages }])
    : [{ pageCount: item.pages }];
  const task = prepareDownloadTask(chunks, { endpoint: request.endpoint, body: request.body }, resumableSingleTask);
  const button = $("#download");
  singleDownloadPending = true;
  setDownloadButtonState(button, "正在保存…", true);
  showTaskDock("正在保存", "正在保存当前作品…", 0);
  syncSearchScopedControls();
  try {
    await executeDownloadTask(task, (chunk) => ({
      ...request,
      body: item.source === "pixiv" ? { ...request.body, pages: chunk.groups[0].pages } : request.body,
    }), () => {
      setDownloadButtonState(button, `正在保存第 ${task.completedBatches + 1}/${task.totalBatches} 批…`, true);
      showTaskDock("正在保存", `第 ${task.completedBatches + 1}/${task.totalBatches} 批 · 已保存 ${task.savedCount} 页`, 0);
    });
    resumableSingleTask = null;
    const summary = `已保存 ${task.savedCount} 页，${task.fileCount} 个文件`;
    announceToast(`${summary}：${task.firstSaved}${task.cleanupPending ? "；临时文件清理未完成，请检查日志" : ""}`);
    showTaskDock("保存完成", summary);
  } catch (error) {
    resumableSingleTask = task.authorizationRevision === downloadAuthorizationRevision ? task : null;
    const retryHint = resumableSingleTask
      ? `再次点击只继续剩余 ${task.remainingChunks.length} 批。`
      : "账户状态已变更，请重新确认后发起下载。";
    announceToast(`已保存 ${task.savedCount} 页；保存失败：${error.message || "未知错误"}。${retryHint}`);
    showTaskDock("保存失败", error.message || "未知错误", 8000);
  } finally {
    singleDownloadPending = false;
    button.textContent = downloadButtonLabel(currentDetailItem);
    syncSearchScopedControls();
  }
};

async function syncAuthStatus() {
  const generation = ++authStatusGeneration;
  try {
    const data = await fetchJson("/api/status", {}, 8000);
    if (generation !== authStatusGeneration) return;
    const logged = Boolean(data.loggedIn);
    const authorizationLost = lastKnownLoggedIn === true && !logged;
    const accountGeneration = Number.isInteger(data.authorizationGeneration) ? data.authorizationGeneration : null;
    const accountChanged = lastKnownAuthorizationGeneration !== null
      && accountGeneration !== null && accountGeneration !== lastKnownAuthorizationGeneration;
    if (accountChanged || (lastKnownLoggedIn !== null && lastKnownLoggedIn !== logged)) {
      downloadAuthorizationRevision += 1;
      resumableSingleTask = null;
      resumableBatchTask = null;
    }
    lastKnownLoggedIn = logged;
    lastKnownAuthorizationGeneration = accountGeneration;
    $("#mode").textContent = logged ? "PIXIV AUTHORIZED" : "PIXIV PUBLIC";
    $("#loginBtn").textContent = logged ? "Pixiv 已连接" : "登录 Pixiv";
    $("#authStateTitle").textContent = logged ? "当前：已连接" : "当前：未连接";
    $("#authStateText").textContent = logged ? "Pixiv 会话已保存在本机。" : "将在 MOKU 应用内打开 Pixiv 官方登录页面。";
    $("#authAction").textContent = logged ? "退出 Pixiv 账户" : "打开应用内登录窗口";
    $("#safety").querySelectorAll('option[value="r18"],option[value="all"]').forEach((option) => { option.disabled = !logged; });
    if (authorizationLost) handleAuthorizationLoss();
    else if (accountChanged) handleAuthorizationLoss("Pixiv 账户状态已变更");
  } catch (error) {
    if (generation !== authStatusGeneration) return;
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
  let actionError = "";
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
  } catch (error) {
    actionError = error.message || "授权失败";
  } finally {
    await syncAuthStatus();
    if (actionError) $("#authStateText").textContent = actionError;
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
syncSearchScopedControls();

if ("requestIdleCallback" in window) {
  requestIdleCallback(() => syncAuthStatus(), { timeout: 2000 });
} else {
  setTimeout(() => syncAuthStatus(), 500);
}
