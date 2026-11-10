let items = [];
const ARTWORK_DETAIL_TIMEOUT_MS = 60000;
let activeArtworkId = null;
let currentPage = 1;
let pageNumbers = [1];
let firstAvailablePage = 1;
let preloadedThrough = 1;
let searchHasMore = false;
let searchOrderingPending = false;
let activeTagQuery = "猫耳";
let searchController = null;
let activeSearchRequestId = null;
let activeSearchPageKey = null;
let activeSearchSnapshotId = null;
let searchRequestSequence = 0;
let searchGeneration = 0;
let requestPolicyMode = "safe";
let requestModeEditing = false;
const REQUEST_MODES = ["safe", "normal", "fast"];
const REQUEST_MODE_LABELS = ["安全", "普通", "激进"];
let requestBlockedKind = "";
let requestPolicyWaiting = false;
let lastPageInteraction = performance.now();
let requestPolicyRevision = 0;
let requestPolicyReadSequence = 0;
let requestPolicyRefresh = null;
let requestCooldownUntil = 0;
let prefetchTimer = null;
let prefetchController = null;
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
let basketDownloadSnapshot = null;
let basketReturnMode = "summary";
let basketArtworkOffset = 0;
let viewerPageOffset = 0;
let paginationDockFrame = null;
let searchPending = false;
let pageRefreshPending = false;
let navigationRevision = 0;
let pageRefreshController = null;
let resultSelectionEnabled = false;
let singleDownloadPending = false;
let detailDownloadProgress = null;
let batchDownloadProgress = null;
let activeWorkspace = null;
let accountOrigin = false;
let contextReturns = {};
let workspaceReturn = null;
let resumableBatchTask = null;
let resumableSingleTask = null;
let activeDownload = null;
let lastKnownLoggedIn = null;
let lastKnownAuthorizationGeneration = null;
let downloadAuthorizationRevision = 0;
let authStatusGeneration = 0;
let appConnection = null;
let loginMethod = "app";
let authActionBusy = false;
let appConnectionTimer = null;
let appConnectionRevision = 0;
let appConnectionBusy = false;
const pendingTasks = new Map();
let pendingTasksMarkup = null;
let savedRecentSearches = [];
let recentSearchWrites=Promise.resolve();
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
const selection = createSelectionStore({maxPages: MAX_SELECTED_PAGES, onChange: () => workspaceSync.changed()});
const galleryPreviewCache = createPreviewImageCache({reuseDetached:true});
const basketPreviewCache = createPreviewImageCache({retainUnused:true,maxImages:240,priority:2});
const searchPageCache = createSearchPageCache({cancelRequest:notifySearchCancellation});
const detailRefreshes = new Map();
const detailRefreshAttempts = new Map();
const staleBasketPreviewIds = new Set();
const downloadedPages = new Map();
let basketThumbnailObserver = null;
let basketCardSizeObserver = null;
const basketThumbnailControllers = new Set();
let basketThumbnailQueue = [];
let basketThumbnailWindow = 0;
let detailImageIdleTimer = null;
let pageHistory = null;
let navigationRecordFrame = null, navigationRecordTimer = null;
let searchSurface = "home";
let searchLoadedAt = 0;
let detailLoadedAt = 0;

const $ = (selector) => document.querySelector(selector);
const windowControls = $("#windowControls");
const openDialogs = [];
let windowStateTimer = null;
let windowActionRevision = 0;

function showAppDialog(modal) {
  if (modal.open) return;
  modal.showModal();
  openDialogs.push(modal);
}

function syncWindowState(state) {
  const button = $("#windowMaximize");
  button.dataset.maximized = String(state.maximized);
  const label = state.maximized ? "还原窗口" : "最大化窗口";
  button.setAttribute("aria-label", label);
  button.title = label;
}

async function performWindowAction(action) {
  const revision = ++windowActionRevision;
  try {
    const state = await window.pywebview.api.window_action(action);
    if (!state.ok) throw new Error(state.error || "窗口操作失败，请重试");
    if (revision === windowActionRevision && action !== "close") syncWindowState(state);
  } catch (error) {
    if (revision === windowActionRevision) announceToast(error.message);
  }
}

function bindWindowControls() {
  if (!window.pywebview?.api?.window_action) return;
  document.documentElement.classList.add("desktop-window");
  windowControls.hidden = false;
  void performWindowAction("state");
  schedulePaginationDockUpdate();
}

windowControls.querySelectorAll("[data-window-action]").forEach(button => {
  button.onclick = () => {
    const modal = openDialogs.at(-1);
    if (button.dataset.windowAction === "close" && modal?.open) modal.close();
    else void performWindowAction(button.dataset.windowAction);
  };
});
document.querySelectorAll(".app-header, .viewer-bar, .workspace-bar").forEach(header => {
  const draggable = event => !windowControls.hidden
    && !event.target.closest("button, a, input, select, textarea, label, [contenteditable]");
  header.addEventListener("mousedown", event => {
    if (event.button === 0 && event.detail === 1 && draggable(event)) {
      event.preventDefault();
      void performWindowAction("drag");
    }
  });
  header.addEventListener("dblclick", event => {
    if (event.button === 0 && draggable(event)) void performWindowAction("toggle-maximize");
  });
});
window.addEventListener("pywebviewready", bindWindowControls);
window.addEventListener("resize", () => {
  clearTimeout(windowStateTimer);
  windowStateTimer = setTimeout(() => {
    if (!windowControls.hidden) void performWindowAction("state");
  }, 100);
});
const grid = $("#grid");
const gallery = $("#gallery");
const searchButton = $("#searchSubmit");
const cancelSearchButton = $("#cancelSearch");
document.documentElement.classList.add("conservative");

const searchInput = $("#tag");
const searchKind = $("#searchKind");
const searchHelp = $("#searchHelp");
const tagSearchHelp = searchHelp.textContent;
const searchPrefixPattern = /^\s*(pid|uid|author)\s*[:：]\s*/i;
const searchPlaceholders = {
  tags: "输入标签，多个标签用 ; 分隔",
  pid: "输入作品 ID",
  uid: "输入画师 ID",
  author: "输入画师名称",
};

function syncSearchKind() {
  const match = searchInput.value.match(searchPrefixPattern);
  const kind = match ? match[1].toLowerCase() : "tags";
  if (searchKind.value !== kind) {
    searchKind.value = kind;
    searchKind.dispatchEvent(new Event("input", {bubbles: true}));
  }
  syncHomeSortControl();
  searchInput.placeholder = searchPlaceholders[kind];
  searchHelp.textContent = kind === "tags" ? tagSearchHelp
    : kind === "author" ? "按画师名称寻找作品；也可以直接输入 author:画师名。"
    : `填写 Pixiv 的纯数字${kind === "pid" ? "作品" : "画师"} ID；也可以直接粘贴带 ${kind}: 前缀的内容。`;
}

searchKind.onchange = () => {
  const match = searchInput.value.match(searchPrefixPattern);
  const text = match ? searchInput.value.slice(match[0].length) : searchInput.value;
  const prefix = searchKind.value === "tags" ? "" : `${searchKind.value}:`;
  searchInput.value = prefix + text;
  syncSearchKind();
  searchInput.focus();
  searchInput.setSelectionRange(prefix.length, searchInput.value.length);
};
searchInput.addEventListener("input", event => { if (!event.isComposing) syncSearchKind(); });
searchInput.addEventListener("compositionend", syncSearchKind);
syncSearchKind();

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
  if (!["1", "7", "30"].includes(preset)) return { startDate: "", endDate: "" };
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

function readRecentSearches() {
  return savedRecentSearches.slice(0, 8);
}
function saveRecentChange(change){
  const scope=workspaceSync.scope,authorization=lastKnownAuthorizationGeneration;
  const pending=recentSearchWrites.catch(()=>{}).then(()=>{
    if(scope!==workspaceSync.scope || authorization!==lastKnownAuthorizationGeneration)throw new Error('账号状态已改变，请重试');
    return fetchJson('/api/workspace/recent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...change,scope})});
  });
  recentSearchWrites=pending;return pending;
}

function rememberSearch(tag, filters) {
  const {order,popularityIndex,...criteria}=filters;
  const row = {tag, filters: criteria};
  const key = JSON.stringify(row);
  const rows = [row, ...readRecentSearches().filter(old => JSON.stringify(old) !== key)].slice(0, 8);
  savedRecentSearches = rows;
  if (workspaceSync.ready) {
    void saveRecentChange({search:row})
      .catch(() => { $("#recentSearches").title = "最近搜索暂未保存，重开窗口后可能丢失"; });
  }
  renderRecentSearches();
}

function restoreSearch(row) {
  if (contextNavigationLocked()) return;
  const filters = row.filters;
  $("#tag").value = row.tag;
  syncSearchKind();
  $("#safety").value = lastKnownLoggedIn === true ? filters.mode : "safe";
  $("#workType").value = filters.workType;
  $("#includeAi").checked = Boolean(filters.includeAi);
  $("#fuzzySearch").checked = Boolean(filters.fuzzy);
  $("#datePreset").value = filters.startDate ? "custom" : "all";
  $("#startDate").value = filters.startDate || "";
  $("#endDate").value = filters.endDate || "";
  syncDateInputs();
  $("#searchForm").requestSubmit();
}

function renderRecentSearches() {
  const holder = $("#recentSearches");
  if (!holder) return;
  const rows = readRecentSearches();
  holder.hidden = !rows.length;
  holder.innerHTML = '<span class="recent-search-heading"><span>最近搜索</span><button type="button" class="recent-search-clear" data-recent-clear title="清空最近搜索" aria-label="清空最近搜索"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7m4-7v7"/></svg></button></span>' + rows.map((row, index) => `<span class="recent-search-chip"><button type="button" data-recent-search="${index}" title="恢复这次搜索的标签和筛选条件"><span class="recent-search-label">${esc(row.tag)}</span></button><button type="button" class="recent-search-remove" data-recent-remove="${index}" aria-label="删除最近搜索：${esc(row.tag)}" title="删除">×</button></span>`).join("");
  holder.querySelector('[data-recent-clear]').onclick=async()=>{
    if(!workspaceSync.ready){announceToast('最近搜索尚未就绪，请稍后再试');return;}
    const scope=workspaceSync.scope,authorization=lastKnownAuthorizationGeneration,button=holder.querySelector('[data-recent-clear]');
    button.disabled=true;
    try{
      await saveRecentChange({action:'clear'});
      if(scope!==workspaceSync.scope || authorization!==lastKnownAuthorizationGeneration)return;
      savedRecentSearches=savedRecentSearches.filter(row=>!rows.includes(row));renderRecentSearches();
    }catch(error){button.disabled=false;announceToast(`清空最近搜索失败：${error.message}`);}
  };
  holder.querySelectorAll("[data-recent-search]").forEach(button => {
    button.onclick = () => restoreSearch(rows[Number(button.dataset.recentSearch)]);
  });
  holder.querySelectorAll("[data-recent-remove]").forEach(button => {
    button.onclick = async event => {
      event.stopPropagation();
      if (!workspaceSync.ready) { announceToast("最近搜索尚未就绪，请稍后再试"); return; }
      const row = rows[Number(button.dataset.recentRemove)], scope = workspaceSync.scope;
      const authorization = lastKnownAuthorizationGeneration;
      button.disabled = true;
      try {
        await saveRecentChange({search:row,action:'remove'});
        if (scope !== workspaceSync.scope || authorization !== lastKnownAuthorizationGeneration) return;
        savedRecentSearches = savedRecentSearches.filter(old => old!==row);
        renderRecentSearches();
      } catch (error) { button.disabled = false; announceToast(`删除最近搜索失败：${error.message}`); }
    };
  });
}

const esc = (value) => String(value).replace(
  /[&<>"']/g,
  (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char],
);
const historyView = createDownloadHistoryView({
  fetchJson: (url, options) => fetchJson(url, options), showDialog: showAppDialog,
});

const workspaceSync = createWorkspaceSync({
  request: (url, options) => fetchJson(url, options),
  getAuthorizationGeneration: () => lastKnownAuthorizationGeneration,
  readBasket: () => selection.snapshot(),
  canRestore: () => !selection.locked && !singleDownloadPending,
  applyWorkspace: data => {
    restoreBasketView(data.basket);
    savedRecentSearches = data.recent || [];
    renderRecentSearches();
    pendingTasks.clear();
    for (const saved of data.tasks || []) {
      const task = {revision: 0, ...saved, persisted: true, authorizationRevision: downloadAuthorizationRevision};
      pendingTasks.set(task.id, task);
    }
    updateSelectionBar();
    renderPendingTasks();
    if (selectedPageCount() > MAX_SELECTED_PAGES) {
      showTaskDock("采集篮选择已保留", `当前 ${selectedPageCount()} 张，请减少至 ${MAX_SELECTED_PAGES} 张以内再保存或批量下载。`, 8000);
    } else if (pendingTasks.size) showTaskDock("有未完成的下载", `${pendingTasks.size} 项任务已暂停，可在下载页继续。`, 8000);
  },
  onStatus: state => {
    $("#workspaceNotice").hidden = !state.error;
    $("#workspaceNoticeText").textContent = state.error
      ? (state.error.basketCapacity ? `选择尚未保存：${state.error.message}` : `本机记录暂未同步：${state.error.message}。当前选择已保留。`) : "";
    $("#workspaceRetry").disabled = state.loading || selection.locked || singleDownloadPending;
    syncSearchScopedControls();
    if (state.ready && !state.loading) scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
  },
  onScopeChanged: () => {
    downloadAuthorizationRevision++;
    handleAuthorizationLoss("Pixiv 账户状态已变更");
    return syncAuthStatus();
  },
});

$("#workspaceRetry").onclick = () => workspaceSync.retry();

function restoreWorkspace() { return workspaceSync.restore(); }

function restoreBasketView(rows) {
  // Disk records contain durable metadata, not preview URLs. Keep previews
  // already loaded by this account. Selection-only restores keep the deck;
  // changed metadata must also reach the visible fields and page controls.
  const previewItems = new Map([...items, ...batchCandidateItems, ...selection.snapshot().map(row => row.item),
    basketDownloadSnapshot?.item, currentDetailItem, basketDetailItem].filter(Boolean).map(item => [String(item.id), item]));
  const restored = rows.map(row => ({...row, item: {...previewItems.get(String(row.id)), ...row.item}}));
  if (!selection.restore(restored, {allowOverflow: true}).accepted) throw new Error("采集篮记录不完整");
  const records = new Map(selection.snapshot().map(row => [row.id, row]));
  const changedDetailIds = new Set();
  const latestItem = item => item ? records.get(String(item.id))?.item || item : null;
  for (const row of records.values()) {
    const preview = previewItems.get(row.id);
    if (["title", "artist", "pages", "workType", "restriction", "tags"].some(
      field => JSON.stringify(preview?.[field]) !== JSON.stringify(row.item[field]),
    )) changedDetailIds.add(row.id);
    if (!preview?.pageImages || preview.pages !== row.item.pages || preview.workType !== row.item.workType
      || preview.restriction !== row.item.restriction) staleBasketPreviewIds.add(row.id);
  }
  currentDetailItem = latestItem(currentDetailItem);
  basketDetailItem = latestItem(basketDetailItem);
  const changed = item => item && changedDetailIds.has(String(item.id));
  if (basketDownloadSnapshot) {
    basketDownloadSnapshot.item = latestItem(basketDownloadSnapshot.item);
    basketDownloadSnapshot.detailChanged ||= Boolean(changed(basketDownloadSnapshot.item));
  }
  syncResultSelectionControls();
  const syncPages = (root, item) => {
    root.querySelectorAll("[data-collection-page]").forEach(box => {
      box.checked = selection.isPageSelected(item?.id, Number(box.dataset.collectionPage));
    });
    if (item) bindCollectionPageInputs(root, item);
  };
  syncPages($("#collectionPages"), basketDownloadSnapshot?.item || currentDetailItem);
  if (!basketPageOpen()) {
    if (changed(currentDetailItem)) refreshDetailControls(currentDetailItem, false);
  } else {
    const candidates = new Map(batchCandidateItems.map(item => [String(item.id), item]));
    for (const row of records.values()) {
      candidates.set(row.id, row.item);
      batchCandidateContextByArtwork.set(row.id, {...row.context});
      batchCandidateResultPageByArtwork.set(row.id, row.resultPage);
    }
    batchCandidateItems = [...candidates.values()];
    if (basketReturnMode === "picker") openBasketArtworkPicker();
    else {
      if (changed(basketDetailItem)) refreshDetailControls(basketDetailItem, true);
      else syncPages($("#basketPages"), basketDetailItem);
      if (basketDetailItem && records.has(String(basketDetailItem.id))) {
        currentDetailContext = {...records.get(String(basketDetailItem.id)).context};
      }
      syncBasketHeader();
      syncBasketArtworkMeta();
    }
  }
  const visibleDetail = basketPageOpen() ? (basketReturnMode === "detail" ? basketDetailItem : null)
    : ((activeWorkspace === "download" || !$("#allViewer").hidden) ? currentDetailItem : null);
  if (changed(visibleDetail) && visibleDetail.source === "pixiv" && staleBasketPreviewIds.has(String(visibleDetail.id))) {
    detailRefreshAttempts.delete(String(visibleDetail.id));
    void refreshArtworkPreview(visibleDetail.id);
  }
}

async function restoreWorkspaceWhenUnlocked() {
  if (workspaceSync.needsRestore && !selection.locked && !singleDownloadPending) await workspaceSync.restore();
}

function renderPendingTasks() {
  const holder = $("#pendingDownloads");
  if (!holder) return;
  const visibleTasks = new Map();
  if (activeDownload) visibleTasks.set(activeDownload.task.id, activeDownload.task);
  for (const [id, task] of pendingTasks) if (!visibleTasks.has(id)) visibleTasks.set(id, task);
  holder.hidden = !visibleTasks.size;
  $("#pendingDownloadSummary").textContent=activeDownload
    ? `下载中 · ${activeDownload.task.savedCount} / ${activeDownload.task.savedCount+activeDownload.task.remainingChunks.reduce((sum,chunk)=>sum+chunk.pageCount,0)} 张`
    : `未完成任务 · ${visibleTasks.size} 项`;
  const locked = contextNavigationLocked() || Boolean(activeDownload) || selection.locked || singleDownloadPending;
  const markup = [...visibleTasks.values()].map(task => {
    const options = task.kind === "single" ? task.taskOptions.body : task.taskOptions;
    const title = task.kind === "single" ? `作品 ${options.id}` : "采集篮下载";
    const running = activeDownload?.task.id === task.id;
    return `<div class="pending-task"><div><b>${esc(title)} · ${running ? "下载中 · " : ""}剩余 ${task.remainingChunks.length} 批</b><small>${options.quality === "regular" ? "标准预览" : "原图"} · ${esc(options.ugoiraFormat === "source" ? "源格式" : (options.ugoiraFormat || "source").toUpperCase())} · ${esc(options.saveRoot || "默认下载目录")}</small></div>${task.conversionFailure ? `<button type="button" data-adjust-task="${esc(task.id)}" ${locked ? "disabled" : ""}>调整参数</button>` : ""}${running ? `<button type="button" data-pause-task="${esc(task.id)}" ${activeDownload.pauseRequested ? "disabled" : ""}>${activeDownload.pauseRequested ? "正在暂停…" : "暂停"}</button>` : `<button type="button" data-resume-task="${esc(task.id)}" ${locked || !task.remainingChunks.length ? "disabled" : ""}>继续</button>`}<button type="button" data-remove-task="${esc(task.id)}" ${locked ? "disabled" : ""}>移除</button></div>`;
  }).join("");
  if (markup === pendingTasksMarkup) return;
  pendingTasksMarkup = markup;
  $("#pendingDownloadList").innerHTML = markup;
  $("#pendingDownloadList").querySelectorAll("[data-resume-task]").forEach(button => {
    button.onclick = () => resumeSavedTask(pendingTasks.get(button.dataset.resumeTask));
  });
  $("#pendingDownloadList").querySelectorAll("[data-adjust-task]").forEach(button => {
    button.onclick = () => adjustSavedTask(pendingTasks.get(button.dataset.adjustTask));
  });
  $("#pendingDownloadList").querySelectorAll("[data-pause-task]").forEach(button => {
    button.onclick = () => pauseDownloadTask(button.dataset.pauseTask);
  });
  $("#pendingDownloadList").querySelectorAll("[data-remove-task]").forEach(button => {
    button.onclick = async () => {
      if (contextNavigationLocked()) return;
      try { await deleteSavedTask(button.dataset.removeTask); }
      catch (error) { $("#pendingDownloadStatus").textContent = error.message; }
    };
  });
}

async function pauseDownloadTask(id) {
  const running = activeDownload;
  if (!running || running.task.id !== id || running.pauseInFlight) return;
  running.pauseRequested = true;
  if (running.cooldownUntil) { renderPendingTasks(); return; }
  running.pauseInFlight = true;
  renderPendingTasks();
  try {
    // The pause can race the initial POST. Retry until its owner is registered
    // or the local operation ends; never abort the response that confirms the
    // batch was rolled back or successfully published.
    while (activeDownload === running && running.chunk) {
      const requestId = running.chunk.requestId;
      const result = await fetchJson("/api/pixiv/download/cancel", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({requestId}),
      }, 8000);
      if (result.cancelled) { running.pauseSentForId = requestId; return; }
      await new Promise(resolve => setTimeout(resolve, 300));
    }
  } catch (error) {
    if (activeDownload === running) {
      running.pauseRequested = false;
      announceToast(`暂停请求失败：${error.message}。可再次点击暂停。`);
    }
  } finally { running.pauseInFlight = false; renderPendingTasks(); }
}

function forgetTaskRetry(id) {
  if (resumableSingleTask?.id === id) resumableSingleTask = null;
  if (resumableBatchTask?.id === id) resumableBatchTask = null;
}

async function writeTaskState(path, payload, timeout = 12000) {
  try {
    return await fetchJson(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}, timeout);
  } catch (error) {
    if (error.taskConflict) {
      forgetTaskRetry(payload.task?.id ?? payload.id);
      await workspaceSync.restore();
    }
    throw error;
  }
}

async function saveDownloadTask(task, expectedTask = null, completedRequestId = null) {
  if (task.transient || (!workspaceSync.ready && !task.persisted)) return;
  if (!task.remainingChunks.length) { await deleteSavedTask(task.id); return; }
  const authorization = downloadAuthorizationRevision;
  const scope = workspaceSync.scope;
  const payload=completedRequestId ? {scope,checkpoint:{id:task.id,revision:task.revision,requestId:completedRequestId,
    progress:{savedCount:task.savedCount,fileCount:task.fileCount,skippedCount:task.skippedCount,firstSaved:task.firstSaved}}}
    : {task,scope,updateOnly:Boolean(task.persisted),...(expectedTask?{expectedTask}:{})};
  const result = await writeTaskState("/api/workspace/task", payload);
  if (authorization !== downloadAuthorizationRevision || scope !== workspaceSync.scope) throw new Error("Pixiv 账户状态已变更，请重新读取任务");
  task.revision = result.task.revision;
  pendingTasks.set(task.id, task);
  renderPendingTasks();
}

async function deleteSavedTask(id) {
  if (!workspaceSync.ready) return;
  const revision = pendingTasks.get(id)?.revision ?? 0;
  const result = await writeTaskState("/api/workspace/task/delete", {id, scope: workspaceSync.scope, revision});
  pendingTasks.delete(id);
  forgetTaskRetry(id);
  renderPendingTasks();
  if (result.pauseWarning) announceToast(result.pauseWarning);
}

function savedTaskRequest(task, chunk) {
  return task.kind === "single"
    ? {endpoint: task.taskOptions.endpoint, body: {...task.taskOptions.body, pages: chunk.groups[0].pages}, timeout: 1800000}
    : {endpoint: "/api/pixiv/batch-download", body: {...task.taskOptions, groups: chunk.groups, context: chunk.context}, timeout: 1800000};
}

function downloadTaskCheckpoint(task) {
  return JSON.stringify([task.kind, task.revision || 0, task.chunkPolicy || "", task.taskOptions, task.completedBatches || 0, task.totalBatches,
    task.savedCount || 0, task.fileCount || 0, task.skippedCount || 0, task.firstSaved || "",
    task.conversionFailure?.artworkId || "", task.conversionFailure?.code || "",
    task.remainingChunks.map(chunk => [chunk.requestId, chunk.pageCount, chunk.context.kind, chunk.context.value,
      chunk.groups.map(group => [group.id, group.pages])])]);
}

// Change only the failed/remaining work. Completed files and counters stay put.
function adjustSavedTask(task) {
  if (!task?.conversionFailure || contextNavigationLocked() || activeDownload || selection.locked || singleDownloadPending || !workspaceSync.ready) return;
  let modal = $("#adjustTaskDialog");
  if (!modal) {
    modal = document.createElement("dialog");
    modal.id = "adjustTaskDialog";
    modal.className = "capacity-dialog";
    modal.innerHTML = `<h2>调整剩余任务</h2><p>已完成的文件保留，仅修改剩余批次的清晰度和动图格式。</p>
      <label>清晰度<select id="adjustTaskQuality"><option value="original">原图（原始分辨率）</option><option value="regular">标准（缩小预览；动图 600px）</option></select></label>
      <label>动图格式<select id="adjustTaskFormat"></select></label><p id="adjustTaskStatus" role="status"></p>
      <div class="dialog-actions"><button id="adjustTaskConfirm" type="button">应用并继续</button><button id="adjustTaskCancel" type="button">取消</button></div>`;
    document.body.append(modal);
  }
  const authorization = downloadAuthorizationRevision;
  const expectedTask = JSON.parse(JSON.stringify(task));
  const expectedCheckpoint = downloadTaskCheckpoint(task);
  const options = task.kind === "single" ? task.taskOptions.body : task.taskOptions;
  $("#adjustTaskQuality").value = options.quality;
  $("#adjustTaskFormat").innerHTML = downloadFormatOptions([
    {id:"source", label:"原始帧 ZIP + JSON"}, {id:"gif", label:"GIF 动图"}, {id:"mp4", label:"MP4 视频"},
  ]);
  $("#adjustTaskFormat").value = options.ugoiraFormat;
  $("#adjustTaskStatus").textContent = `作品 ${task.conversionFailure.artworkId} 转换未完成。`;
  $("#adjustTaskCancel").onclick = () => modal.close();
  $("#adjustTaskConfirm").disabled = false;
  $("#adjustTaskConfirm").onclick = async () => {
    if (contextNavigationLocked() || authorization !== downloadAuthorizationRevision) {
      $("#adjustTaskStatus").textContent = "账户或任务状态已变更，请重新打开任务。";
      return;
    }
    const quality = $("#adjustTaskQuality").value;
    const format = $("#adjustTaskFormat").value;
    if (quality === options.quality && format === options.ugoiraFormat) {
      $("#adjustTaskStatus").textContent = "请选择新的清晰度或格式，或取消后使用原参数继续。";
      return;
    }
    $("#adjustTaskConfirm").disabled = true;
    $("#adjustTaskCancel").disabled = true;
    modal.oncancel = event => event.preventDefault();
    setBasketSelectionLocked(true);
    let saveError = null;
    try {
      const updated = {...task, taskOptions: JSON.parse(JSON.stringify(task.taskOptions)), signature: "",
        remainingChunks: task.remainingChunks.map(chunk => ({...chunk, requestId: nextDownloadRequestId()}))};
      const changedOptions = updated.kind === "single" ? updated.taskOptions.body : updated.taskOptions;
      changedOptions.quality = quality;
      changedOptions.ugoiraFormat = format;
      delete updated.conversionFailure;
      // Save first: a failed state write leaves the old task and request IDs intact.
      await saveDownloadTask(updated, expectedTask);
      if (authorization !== downloadAuthorizationRevision) throw new Error("账户已变更，请重新读取任务");
      Object.assign(task, updated);
      delete task.conversionFailure;
      pendingTasks.set(task.id, task);
      modal.close();
    } catch (error) {
      saveError = error;
    } finally {
      setBasketSelectionLocked(false);
      $("#adjustTaskConfirm").disabled = false;
      $("#adjustTaskCancel").disabled = false;
      modal.oncancel = null;
      renderPendingTasks();
    }
    if (saveError) {
      // A disconnected response can hide a committed edit. Re-read through the
      // workspace owner, preserving any local basket edits and account checks.
      await workspaceSync.restore();
      if (authorization !== downloadAuthorizationRevision) { modal.close(); return; }
      if (!workspaceSync.ready) {
        $("#adjustTaskStatus").textContent = "任务状态读取失败，请关闭此窗口并重试本机记录同步。";
        return;
      }
      const latest = pendingTasks.get(task.id);
      if (!latest || downloadTaskCheckpoint(latest) !== expectedCheckpoint) {
        modal.close();
        announceToast(latest ? "任务状态已重新读取，请从任务列表继续或调整。" : "任务已移除，列表已更新。");
        return;
      }
      $("#adjustTaskStatus").textContent = saveError.message || "任务参数保存失败，请重试";
      return;
    }
    await resumeSavedTask(task);
  };
  showAppDialog(modal);
}

async function rememberConversionFailure(task, error) {
  if (!error.conversionFailure || task.authorizationRevision !== downloadAuthorizationRevision) return;
  task.conversionFailure = error.conversionFailure;
  try { await saveDownloadTask(task); }
  catch { task.stateWarning = true; }
}

async function resumeSavedTask(task) {
  if (!task || contextNavigationLocked() || activeDownload || selection.locked || singleDownloadPending || !workspaceSync.ready) return;
  task.authorizationRevision = downloadAuthorizationRevision;
  setBasketSelectionLocked(true);
  $("#pendingDownloadStatus").textContent = "正在继续保存的任务…";
  try {
    await executeDownloadTask(task, chunk => savedTaskRequest(task, chunk), () => {
      showTaskDock("继续下载", `第 ${task.completedBatches + 1}/${task.totalBatches} 批 · 已保存 ${savedDownloadPages(task)} 页`, 0);
    });
    const summary = `任务完成，已保存 ${savedDownloadPages(task)} 页${downloadCompletionWarnings(task)}`;
    $("#pendingDownloadStatus").textContent=summary;
    showTaskDock("下载完成", summary);
  } catch (error) {
    await rememberConversionFailure(task, error);
    $("#pendingDownloadStatus").textContent = error.paused ? "任务已暂停，已完成批次保留，点击继续后下载。" : error.conversionFailure
      ? `已暂停：${error.message}。作品 ${error.conversionFailure.artworkId} 转换未完成，可调整参数后继续剩余批次。`
      : `已暂停：${error.message}。再次继续会重试失败的这一批。`;
    showTaskDock("下载已暂停", error.message, 8000);
  } finally { setBasketSelectionLocked(false); renderPendingTasks(); }
}

function announceToast(message) {
  // The basket page covers #detail, so shared flows report in both places.
  $("#toast").textContent = message;
  $("#basketToast").textContent = message;
}

let taskDockTimer = null;
function showTaskDock(title, text, holdMs = 3200) {
  $("#taskDockTitle").textContent = title;
  $("#taskDockText").textContent = text;
  $("#taskDockText").title = text;
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

function responseError(payload, status) {
  const data = payload !== null && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const error = new Error(data.error || `请求失败（HTTP ${status}）`);
  error.status=status;
  error.errorKind = data.errorKind;
  error.retryAfterSeconds = data.retryAfterSeconds;
  if (["verification_required", "authentication_required"].includes(data.errorKind)) {
    requestPolicyReadSequence++;
    requestBlockedKind = data.errorKind;
    requestPolicyWaiting = true;
    cancelSearchPrefetch();
  } else if (data.errorKind === "upstream_rate_limit") {
    requestPolicyReadSequence++;
    requestPolicyWaiting = true;
    requestCooldownUntil = Math.max(requestCooldownUntil, Date.now() + data.retryAfterSeconds * 1000);
    cancelSearchPrefetch();
  }
  error.requestIdConflict = Boolean(data.requestIdConflict);
  error.basketCapacity = Boolean(data.basketCapacity);
  error.taskCapacity = Boolean(data.taskCapacity);
  error.taskConflict = Boolean(data.taskConflict);
  error.conversionFailure = data.conversionFailure || null;
  error.cloudState=data.state || null;error.official=Boolean(data.official);
  error.ownTaskCount = Number(data.ownTaskCount) || 0;
  error.otherTaskCount = Number(data.otherTaskCount) || 0;
  return error;
}

async function fetchJson(url, options = {}, timeoutMs = 12000) {
  options.signal?.throwIfAborted();
  const controller = new AbortController();
  // Detail reads use the same bounded cancellation registry as search reads.
  const detailRequestId = /^\/api\/pixiv\/artwork\/\d+(?:\?|$)/.test(url) ? nextSearchRequestId() : null;
  if (detailRequestId) {
    url += `${url.includes("?") ? "&" : "?"}requestId=${encodeURIComponent(detailRequestId)}`;
    cancelSearchPrefetch();
    controller.signal.addEventListener("abort", () => { void notifySearchCancellation(detailRequestId); }, {once: true});
  }
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
    if (!response.ok) throw responseError(data, response.status);
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

function cancelActiveSearch(keepPage=null) {
  cancelSearchPrefetch(keepPage);
  if (!searchController) return false;
  const requestId = activeSearchRequestId;
  searchController.abort();
  if(activeSearchPageKey && activeSearchPageKey!==keepPage)searchPageCache.cancel(activeSearchPageKey);
  else if(!activeSearchPageKey)void notifySearchCancellation(requestId);
  return true;
}

function cancelSearchPrefetch(keepPage=null) {
  clearTimeout(prefetchTimer);
  prefetchTimer = null;
  searchPageCache.cancelWarm(keepPage);
  prefetchController = null;
}

function currentPageNeedsMoreResults() {
  return activeSearchContext.kind !== "pid" && searchHasMore && items.length < 36;
}

function searchPrefetchPaused() {
  return !resultSelectionEnabled || searchPending || pageRefreshPending || detailController || detailRefreshes.size
    || selection.locked || singleDownloadPending || workspaceSync.loading || activeDownload
    || requestBlockedKind || activeWorkspace || basketPageOpen() || document.hidden
    || Date.now() < requestCooldownUntil;
}

function scheduleSearchPrefetch(tag, page, filters, generation, ahead = 1, intent = false) {
  clearTimeout(prefetchTimer);
  prefetchTimer = null;
  if (filters.order === "popular" || searchPrefetchPaused() || prefetchController || !searchHasMore || currentPageNeedsMoreResults() || activeSearchContext.kind === "pid") return;
  const snapshotId = activeSearchSnapshotId;
  const identity=searchPageCache.key(tag,page+1,filters,snapshotId);
  if(searchPageCache.has(identity))return;
  prefetchTimer = setTimeout(async () => {
    prefetchTimer = null;
    if (searchPrefetchPaused() || generation !== searchGeneration || currentPage !== page) return;
    if (!intent && performance.now() - lastPageInteraction < 1800) {
      scheduleSearchPrefetch(tag, page, filters, generation, ahead);
      return;
    }
    const marker = {};
    const requestId = nextSearchRequestId();
    prefetchController = marker;
    const query = new URLSearchParams({
      tag, page: String(page+1), mode: filters.mode, workType: filters.workType,
      includeAi: String(filters.includeAi), fuzzy: String(filters.fuzzy), requestId, warm: "1",
      startDate: filters.startDate || "", endDate: filters.endDate || "",
      snapshotId,
    });
    try {
      const data = await searchPageCache.warm(identity,requestId,
        signal=>fetchJson(`/api/pixiv/search?${query}`,{signal},90000),
        (ready,signal)=>{void galleryPreviewCache.prepare(identity,ready.items,signal);});
      if (!data || generation !== searchGeneration || currentPage !== page) return;
      if (!Array.isArray(data.availablePages)) return;
      pageNumbers = data.availablePages;
      firstAvailablePage = Number(pageNumbers[0]) || page;
      preloadedThrough = Math.max(Number(data.preloadedThrough) || page,Number(data.page) || page);
      searchHasMore = Boolean(data.hasMore);
      // Metadata only: keep the current DOM, selection and image capabilities.
      renderPagination();
      renderCacheStatus();
    } catch (error) {
      // Warm-up failures leave usable foreground results; explicit paging retries.
      if(prefetchController===marker)$("#cacheStatus").textContent="下一页暂未准备完成，翻页时重试。";
    } finally {
      if (prefetchController === marker) {
        prefetchController = null;
      }
    }
  }, intent ? 120 : 2000 + Math.random() * 500);
}

for (const eventName of ["pointerover", "focusin"]) {
  $("#pagination").addEventListener(eventName, event => {
    const button = event.target.closest("button[data-page]");
    if (button && !button.disabled && Number(button.dataset.page) === currentPage + 1) {
      scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration, 1, true);
    }
  });
}

for (const eventName of ["pointermove", "pointerdown", "scroll", "keydown"]) {
  document.addEventListener(eventName, () => { lastPageInteraction = performance.now(); }, {passive: true, capture: true});
}

function abortDetailRefreshes() {
  detailRefreshes.forEach((entry) => entry.controller.abort());
  detailRefreshes.clear();
  detailRefreshAttempts.clear();
  stopBasketThumbnails();
}

function invalidateDetailView() {
  viewGeneration += 1;
  galleryInteraction.reset({immediate:true});
  normalDetailView.suspend();
  abortDetailRefreshes();
  if (detailController) detailController.abort();
  detailController = null;
}

function rememberArtworkDetail(item) {
  if (String(activeArtworkId) === String(item.id)) detailLoadedAt = Date.now();
  const artworkId = String(item.id);
  const itemIndex = items.findIndex((row) => String(row.id) === artworkId);
  if (itemIndex >= 0) items[itemIndex] = item;
  const candidateIndex = batchCandidateItems.findIndex((row) => String(row.id) === artworkId);
  if (candidateIndex >= 0) batchCandidateItems[candidateIndex] = item;
  selection.remember(item);
  if (String(basketDetailItem?.id || "") === artworkId) basketDetailItem = item;
  if (String(basketDownloadSnapshot?.item?.id || "") === artworkId) {
    basketDownloadSnapshot.item = item;
    basketDownloadSnapshot.detailChanged = true;
  }
  staleBasketPreviewIds.delete(artworkId);
}

function refreshDetailControls(item, inBasket = basketPageOpen()) {
  if (inBasket && String(basketDetailItem?.id) === String(item.id)) {
    const visible = basketDetailView.render(item, undefined, {preserve: true});
    renderCollectionWindowInto(item, $("#basketPages"), $("#basketPageMore"));
    $("#basketViewAll").hidden = item.pages <= visible;
    syncBasketArtworkMeta();
  } else if (!inBasket) {
    const visible = normalDetailView.render(item, undefined, {preserve: true});
    renderCollectionPageWindow(item);
    $("#viewAll").hidden = item.pages <= visible;
    renderSingleDownloadOptions(item, true);
    updateFormatHint();
  }
  if (!$("#allViewer").hidden) renderViewerWindow(item);
  $("#download").textContent = downloadButtonLabel(item);
  updateSelectionBar();
  syncResultSelectionControls();
  syncPreviewResources();
}

async function refreshArtworkPreview(rawArtworkId) {
  const artworkId = String(rawArtworkId || "");
  if (!/^\d+$/.test(artworkId) || String(activeArtworkId || "") !== artworkId) return false;
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
      const fresh = await fetchJson(`/api/pixiv/artwork/${artworkId}`, { signal: controller.signal }, ARTWORK_DETAIL_TIMEOUT_MS);
      if (
        controller.signal.aborted
        || generation !== viewGeneration
        || String(activeArtworkId || "") !== artworkId
        || String(fresh?.id || "") !== artworkId
        || !Array.isArray(fresh.pageImages)
      ) return false;

      rememberArtworkDetail(fresh);
      currentDetailItem = fresh;

      refreshDetailControls(fresh);

      document.querySelectorAll("[data-detail-artwork]").forEach((img) => {
        if (String(img.dataset.detailArtwork || "") !== artworkId) return;
        const page = Number(img.dataset.detailPage);
        const image = fresh.pageImages?.[page];
        const source = image?.regular || (page === 0 ? fresh.thumb : "");
        if (!source) return;
        delete img.dataset.detailRefreshAttemptedUrl;
        img.classList.remove("image-unavailable");
        img.closest(".page-select,.deck-card,figure")?.classList.remove("image-unavailable");
        if (img.hasAttribute("data-suspended-src")) img.dataset.suspendedSrc = source;
        else img.setAttribute("src", source);
      });
      announceToast("预览授权已刷新");
      return true;
    } catch (error) {
      if (!controller.signal.aborted && generation === viewGeneration) {
        announceToast(error.message || "预览刷新失败");
      }
      return false;
    } finally {
      if (detailRefreshes.get(artworkId)?.promise === task) {
        detailRefreshes.delete(artworkId);
        scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
      }
    }
  })();
  detailRefreshes.set(artworkId, { controller, promise: task });
  return task;
}

function installImageFallbacks(root = document) {
  root.querySelectorAll("img").forEach((img) => {
    if (img.dataset.fallbackReady === "1") return;
    img.dataset.fallbackReady = "1";
    img.addEventListener("load",()=>{
      img.classList.remove("image-unavailable");
      img.closest(".poster,.batch-collection,.page-select,.deck-card,figure,.source-candidate-image")?.classList.remove("image-unavailable");
      delete img.dataset.failedPreviewSrc;
      delete img.dataset.previewRetryAttempted;
    });
    img.addEventListener("error", async () => {
      if (!img.isConnected || img.hasAttribute("data-suspended-src")) return;
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
      img.dataset.failedPreviewSrc=failedSource;
      img.removeAttribute("src");
      img.classList.add("image-unavailable");
      if (basketArtworkId) staleBasketPreviewIds.add(String(basketArtworkId));
      img.closest(".poster,.batch-collection,.page-select,.deck-card,figure,.source-candidate-image")?.classList.add("image-unavailable");
    });
  });
}

installPreviewImageRetry({root:document,allowed:()=>!contextNavigationLocked(),report:announceToast,
  loadArtwork:id=>fetchJson(`/api/pixiv/artwork/${id}`,{},ARTWORK_DETAIL_TIMEOUT_MS)});

async function fetchBytes(url, { signal, cache } = {}, timeoutMs = 30000) {
  signal?.throwIfAborted();
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
    const response = await fetch(url, { headers, signal: controller.signal, cache });
    if (!response.ok) {
      let data = {};
      try { data = await response.json(); } catch { /* Non-JSON errors retain the status fallback. */ }
      throw responseError(data, response.status);
    }
    return await response.arrayBuffer();
  } catch (error) {
    if (timedOut) throw new Error("图片加载超时，请检查网络后重试");
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
  syncPreviewResources();
  if (document.hidden) { ugoiraPreview.stop(); cancelSearchPrefetch(); }
  else void syncAuthStatus().then(() => scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration));
});
window.addEventListener("pagehide", () => {
  ugoiraPreview.clear();
  cancelSearchPrefetch();
  stopBasketThumbnails();
  clearTimeout(detailImageIdleTimer);
  detailImageIdleTimer = null;
});

function attachUgoiraHoverTargets(root) {
  root.querySelectorAll("[data-ugoira-preview]").forEach((host) => {
    if (host.dataset.ugoiraHoverReady === "1") return;
    host.dataset.ugoiraHoverReady = "1";
    host.addEventListener("mouseenter", () => {if(!host.classList.contains("is-sensitive-masked"))ugoiraPreview.start(host, host.dataset.ugoiraPreview);});
    host.addEventListener("mouseleave", () => ugoiraPreview.stop(host));
  });
}

function syncSearchScopedControls() {
  renderPendingTasks();
  syncSearchSortControl();
  const pageBusy=searchPending || pageRefreshPending;
  const resultActionsDisabled = selection.locked || pageBusy || workspaceSync.loading || !resultSelectionEnabled || items.length === 0;
  $("#selectAllPage").disabled = resultActionsDisabled;
  $("#clearPageSelection").disabled = resultActionsDisabled;
  const selectionActionsDisabled = selection.locked || pageBusy || workspaceSync.loading;
  $("#clearSelection").disabled = selectionActionsDisabled || selection.size === 0;
  $("#openBatch").disabled = selectionActionsDisabled || selection.size === 0;
  $("#batchDownload").disabled = selection.locked || pageBusy || singleDownloadPending || workspaceSync.loading || selectedPageCount() === 0;
  $("#workspaceRetry").disabled = workspaceSync.loading || pageRefreshPending || selection.locked || singleDownloadPending;
  $("#basketClear").disabled = contextNavigationLocked() || selection.locked || singleDownloadPending || batchCandidateItems.length === 0;
  $("#basketDownload").disabled = contextNavigationLocked() || selection.locked || singleDownloadPending || selectedPageCount() === 0;
  searchButton.disabled = selection.locked || pageBusy || singleDownloadPending || workspaceSync.loading;
  const detailReady = Boolean(
    currentDetailItem
    && activeArtworkId !== null
    && String(currentDetailItem.id) === String(activeArtworkId),
  );
  $("#download").disabled = selection.locked || pageBusy || singleDownloadPending || workspaceSync.loading || !detailReady;
  if (detailReady && !singleDownloadPending) {
    $("#download").disabled ||= currentDownloadPages(currentDetailItem).length === 0;
    $("#download").textContent = downloadButtonLabel(currentDetailItem);
  }
  document.querySelectorAll("[data-select],[data-batch-select],[data-collection-page]").forEach(box=>{box.disabled=selection.locked || singleDownloadPending || workspaceSync.loading;});
  syncNavigationAvailability();
  renderDetailDownloadProgress();
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
  $("#viewAll").hidden = true;
  $("#download").disabled = true;
  syncSearchScopedControls();
}

function discardPreviousAccountSelections() {
  cancelGalleryPopularity();galleryIntent=null;galleryLinkedEntry=false;galleryDefaultViews.clear();galleryPopularViews.clear();galleryPopularityJobs.clear();
  clearTimeout(appConnectionTimer);
  appConnectionRevision++;
  appConnectionBusy=false;
  syncAppConnection(null);
  searchPageCache.clear();
  galleryInteraction.clear();
  galleryPreviewCache.clear();
  basketPreviewCache.clear();
  artistPage.clear();
  accountView.clear();
  discoveryView?.clear();
  accountOrigin=false;
  contextReturns={};
  detailDownloadProgress = null;
  batchDownloadProgress = null;
  normalDetailView.clear();
  // A public-looking work can still contain this account's private choices.
  selection.revoke(selection.snapshot().map(row => row.id));
  batchCandidateItems = [];
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  staleBasketPreviewIds.clear();
}

function handleAuthorizationLoss(reason = "Pixiv 已断开") {
  resumableSingleTask = null;
  resumableBatchTask = null;
  ugoiraPreview.clear();
  pendingTasks.clear();
  savedRecentSearches = [];
  renderRecentSearches();
  renderPendingTasks();
  invalidateDetailView();
  cancelActiveSearch();
  searchController = null;
  activeSearchRequestId = null;
  searchPending = false;
  resultSelectionEnabled = false;
  $("#safety").value = "safe";
  activeSearchFilters = { ...activeSearchFilters, mode: "safe" };
  closeAllViewer();
  discardPreviousAccountSelections();
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
  searchButton.textContent = "开始寻找";
  cancelSearchButton.disabled = false;
  cancelSearchButton.hidden = true;
  syncSearchScopedControls();
  searchLoadedAt = 0;
  pageHistory?.clear();
  queueNavigationRecord();
}

function updateSelectionBar() {
  const count = selection.size;
  const pages = selectedPageCount();
  $("#selectionBar").hidden = items.length === 0 && count === 0;
  $("#selectionCount").textContent = count
    ? `采集篮 ${count} 个作品 · ${pages}/${MAX_SELECTED_PAGES} 张图片`
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
  if (dialog?.showModal && !dialog.open) showAppDialog(dialog);
}

function renderCacheStatus() {
  const status = $("#cacheStatus");
  if (!status) return;
  status.textContent = `第 ${currentPage} 页 · 采集篮 ${selection.size} 个作品、${selectedPageCount()}/${MAX_SELECTED_PAGES} 张已选。旧页可以返回重新加载。`;
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

async function search(tag, page = 1, filters = readSearchFilters(), snapshotId = null, {manualRefresh=false,continuePage=false,returnTarget=undefined,order=filters.order || "default",preferenceKind=null}={}) {
  cancelGalleryPopularity();
  if (selection.locked || singleDownloadPending || workspaceSync.loading || (pageRefreshPending && !manualRefresh)) return;
  checkpointNavigation();
  const previousReturns={...contextReturns};
  const preparedKey=!manualRefresh && !continuePage && snapshotId?searchPageCache.key(String(tag || "").trim(),page,filters,snapshotId):null;
  const prepared=preparedKey?searchPageCache.promote(preparedKey):null;
  ugoiraPreview.stop();
  const previousView = searchController?._mokuRestoreView || {
    count: $("#count").textContent,
    hadCommittedResults: resultSelectionEnabled,
    artist: artistPage.snapshot(),
    contextReturns:previousReturns,
    source:capturePageState().source,
  };
  if(returnTarget!==undefined)contextReturns.gallery=returnTarget;
  cancelActiveSearch(preparedKey);
  activeSearchPageKey=prepared?preparedKey:null;
  invalidateDetailView();
  const generation = ++searchGeneration;
  searchController = new AbortController();
  const controller = searchController;
  controller._mokuRestoreView = previousView;
  const requestId = nextSearchRequestId();
  const requestedSnapshotId = snapshotId || nextSearchRequestId();
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
  let resultTag=cleanTag,resultContext=requestedContext;
  galleryPreferenceKind=preferenceKind || requestedContext.kind;galleryDraftOrder=order;
  if(order==="popular")requestedFilters.order="popular";
  const keepGrid = previousView.hadCommittedResults && cleanTag === activeTagQuery
    && JSON.stringify(requestedFilters) === JSON.stringify(activeSearchFilters);
  let committed = false;

  searchPending = true;
  galleryStopRequested=false;
  resultSelectionEnabled = false;
  searchButton.disabled = true;
  searchButton.textContent = "正在寻找…";
  cancelSearchButton.hidden = false;
  cancelSearchButton.disabled = false;
  $("#download").disabled = true;
  syncSearchScopedControls();
  grid.setAttribute("aria-busy", "true");
  if (!keepGrid) {
    galleryInteraction.clear();
    grid.innerHTML = '<p class="loading-state">正在连接 Pixiv，可随时继续操作页面…</p>';
    $("#pagination").innerHTML = "";
  }
  $("#count").textContent = `正在加载第 ${page} 页${keepGrid ? " · 当前仍显示上一页" : ""}`;

  try {
    if(requestedContext.kind==="author"){
      artistPage.showName(requestedContext.value);
      const candidateRequestId=nextSearchRequestId();activeSearchRequestId=candidateRequestId;
      const candidates=await fetchJson(`/api/pixiv/artists?${new URLSearchParams({name:requestedContext.value,page:String(page),requestId:candidateRequestId,...(manualRefresh?{refresh:"1"}:{})})}`,{signal:controller.signal},60000);
      if(controller!==searchController)return;
      if(candidates.candidates.length!==1 || candidates.hasMore){
        activeTagQuery=cleanTag;activeSearchContext=requestedContext;activeSearchFilters=requestedFilters;
        activeSearchSnapshotId=requestedSnapshotId;searchLoadedAt=Date.now();searchSurface="gallery";
        items=[];currentPage=page;pageNumbers=[page];firstAvailablePage=page;preloadedThrough=page;searchHasMore=false;searchOrderingPending=false;
        artistPage.showName(requestedContext.value,candidates);grid.innerHTML="";$("#pagination").innerHTML="";
        $("#tagTitle").textContent=requestedContext.value;$("#count").textContent="";
        rememberSearch(cleanTag,requestedFilters);clearDetail();committed=true;return true;
      }
      resultTag=`uid:${candidates.candidates[0].userId}`;
      resultContext={kind:"uid",value:candidates.candidates[0].userId};
    }
    activeSearchRequestId=requestId;
    if(resultContext.kind==="uid")artistPage.showProfile(resultContext.value,manualRefresh,manualRefresh);
    else artistPage.clear();
    artistPage.setLoading(galleryLinkedEntry,galleryLinkedEntry,browseOptions.buttonVisible());
    if(order==="popular" && resultContext.kind!=="pid"){
      activeTagQuery=resultTag;activeSearchContext=resultContext;activeSearchFilters=requestedFilters;
      activeSearchSnapshotId=requestedSnapshotId;searchSurface="gallery";items=[];
      $("#tagTitle").textContent=resultContext.value;rememberSearch(cleanTag,requestedFilters);
      committed=await loadGalleryPopularity(1,{refresh:manualRefresh,signal:controller.signal});
      controller.signal.throwIfAborted();
      if(!committed)throw new Error($("#searchSortStatus").textContent || artistPage.snapshot().sortMessage || "排序准备失败，请重试");
      return committed;
    }
    const query = new URLSearchParams({
      tag: resultTag,
      page: String(page),
      mode: requestedFilters.mode,
      workType: requestedFilters.workType,
      includeAi: String(requestedFilters.includeAi),
      fuzzy: String(requestedFilters.fuzzy),
      startDate: requestedFilters.startDate,
      endDate: requestedFilters.endDate,
      requestId,
      snapshotId: requestedSnapshotId,
    });
    let data=prepared?.data;
    if(!data && prepared?.pending){activeSearchRequestId=prepared.id;data=await waitForPromiseOrAbort(prepared.promise,controller.signal);}
    if(!data){activeSearchRequestId=requestId;data=await fetchJson(`/api/pixiv/search?${query}`, { signal: controller.signal }, 90000);}
    if (controller !== searchController) return;

    rememberSearch(cleanTag, requestedFilters);
    activeTagQuery = resultTag;
    searchLoadedAt = Date.now();
    searchSurface = "gallery";
    activeSearchContext = resultContext;
    activeSearchFilters = requestedFilters;
    activeSearchSnapshotId = data.snapshotId || requestedSnapshotId;
    searchPageCache.remember(searchPageCache.key(resultTag,page,requestedFilters,activeSearchSnapshotId),data);
    items = Array.isArray(data.items) ? data.items : [];
    reconcileSelectedArtworkPreviews(items);
    currentPage = Number(data.page) || 1;
    pageNumbers = Array.isArray(data.availablePages) ? data.availablePages : (Array.isArray(data.pageNumbers) ? data.pageNumbers : [currentPage]);
    firstAvailablePage = pageNumbers.length ? Number(pageNumbers[0]) : currentPage;
    preloadedThrough = Number(data.preloadedThrough) || currentPage;
    searchHasMore = Boolean(data.hasMore);
    searchOrderingPending = Boolean(data.orderingPending);
    $("#tagTitle").textContent = data.label || (Array.isArray(data.tags) && data.tags.length ? data.tags.join(" + ") : (data.tag || cleanTag));
    const preloadStatus = data.preloadedThrough > currentPage ? ` · 已预加载至第 ${data.preloadedThrough} 页` : "";
    const historyStatus = currentPageNeedsMoreResults()
      ? (searchOrderingPending ? " · 正在确认日期排序，可继续加载本页" : " · 当前页尚未满，可继续加载本页")
      : (data.budgetExhausted ? " · 本次结果尚未加载完，可继续翻页" : (data.hasMore ? " · 可继续加载更早作品" : " · 已到最后一页"));
    const fuzzyLabel = data.fuzzy ? " · 别名扩展已启用" : "";
    const dateLabel = requestedFilters.startDate ? ` · ${requestedFilters.startDate} 至 ${requestedFilters.endDate}（日本时间）` : "";
    $("#count").textContent = `已加载 ${data.total} 件 · 第 ${currentPage} 页 · 每页 ${data.perPage || 36} 件${dateLabel}${fuzzyLabel}${preloadStatus}${historyStatus}${data.truncatedDates?.length ? ` · ${data.truncatedDates.length} 个高密度日期受平台截断` : ""}`;
    resultSelectionEnabled = true;
    setGallerySort(order);
    render();
    renderPagination();
    clearDetail();
    void refreshDownloadedIndicators();
    committed = true;
  } catch (error) {
    if (controller.signal.aborted) {
      if (controller === searchController) {
        artistPage.restore(previousView.artist);
        contextReturns={...previousView.contextReturns};
        restoreInterruptedSearch(previousView);
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
    if (controller !== searchController) return;
    if(requestedContext.kind==="author"){
      activeTagQuery=cleanTag;activeSearchContext=requestedContext;activeSearchFilters=requestedFilters;
      activeSearchSnapshotId=requestedSnapshotId;searchLoadedAt=Date.now();searchSurface="gallery";
      items=[];currentPage=page;pageNumbers=[page];firstAvailablePage=page;preloadedThrough=page;searchHasMore=false;searchOrderingPending=false;
      artistPage.showName(requestedContext.value,{query:requestedContext.value,page,candidates:[],hasMore:false,error:`${error.message || "作者搜索失败"}。也可输入 UID 直接打开作者页。`});
      grid.innerHTML="";$("#pagination").innerHTML="";$("#count").textContent="";$("#tagTitle").textContent=requestedContext.value;
    }else if (keepGrid) {
      restoreInterruptedSearch(previousView);
      renderPagination();
      $("#count").textContent = `第 ${page} 页加载失败：${error.message || "连接未完成"} · 仍显示第 ${currentPage} 页，可再次翻页重试`;
    } else {
      grid.innerHTML = `<div class="error-state"><b>加载失败</b><p>${esc(error.message || "Pixiv 搜索失败")}</p><button id="retrySearch" type="button">重试当前搜索</button></div>`;
      $("#count").textContent = "连接未完成";
    }
    $("#retrySearch")?.addEventListener("click", () => search(cleanTag, page, requestedFilters, requestedSnapshotId,{order,preferenceKind:galleryPreferenceKind}));
  } finally {
    if (controller === searchController) {
      searchController = null;
      activeSearchRequestId = null;
      activeSearchPageKey = null;
      searchPending = false;
      grid.setAttribute("aria-busy", "false");
      searchButton.disabled = false;
      searchButton.textContent = "开始寻找";
      cancelSearchButton.disabled = false;
      cancelSearchButton.hidden = true;
      syncSearchScopedControls();
      if(committed && resultSelectionEnabled)rememberGalleryView();
      if (committed && resultSelectionEnabled) scheduleSearchPrefetch(resultTag, currentPage, requestedFilters, generation);
      queueNavigationRecord();
    }
  }
  return committed;
}

function syncResultSelectionControls() {
  accountView.syncSelection();
  discoveryView?.syncSelection();
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
    // Result pages are summaries, possibly from an older search snapshot.
    // Only another full detail may replace the authoritative page count/type.
    if (!Array.isArray(fresh.pageImages)) {
      return {...fresh, ...existing, thumb: fresh.thumb || existing.thumb,
        bookmarks: existing.bookmarks ?? fresh.bookmarks ?? null,
        restriction: fresh.restriction === "r18" ? "r18" : existing.restriction ?? fresh.restriction};
    }
    const merged = { ...existing, ...fresh };
    if (fresh.bookmarks == null) merged.bookmarks = existing.bookmarks ?? null;
    return merged;
  };
  const updated = [];
  selection.snapshot().forEach(({item}) => {
    const merged = mergePreview(item);
    if (merged !== item) updated.push(merged);
  });
  selection.rememberMany(updated);
  batchCandidateItems = batchCandidateItems.map(mergePreview);
  freshItems.forEach((item, index) => {
    const detail = selection.get(item.id)?.item;
    if (detail?.pageImages?.length) freshItems[index] = detail;
  });
}

let settingsView=null;
let discoveryView=null;
let downloadDefaultsTouched=false;
let initialDownloadDefaultsApplied=false;
let sensitiveMaskScope="both";
let recentlyRevealedPoster=null,searchMaskRevealedAt=-Infinity;
const searchImageReveals=createSensitiveImageReveals({root:grid,onHide:id=>{
  const card=grid.querySelector(`[data-sensitive-image="${id}"]`);
  if(!card)return;
  const poster=card.querySelector(".poster");
  if(card.classList.contains("gallery-expanded") || card.classList.contains("adaptive-expanded"))galleryInteraction.reset({slow:true});
  ugoiraPreview.stop(poster);
  poster.classList.add("is-sensitive-masked");
  poster.querySelector(".sensitive-image-mask").hidden=false;
}});
function applySensitiveMask(scope) {
  if(scope===sensitiveMaskScope)return;
  sensitiveMaskScope=scope;recentlyRevealedPoster=null;searchImageReveals.clear();
  galleryInteraction.reset({immediate:true});ugoiraPreview.stop();
  grid.querySelectorAll(".gallery-card").forEach((card,index)=>{
    const item=items[index],poster=card.querySelector(".poster");
    const masked=["search","both"].includes(scope) && item.restriction!=="safe";
    poster.classList.toggle("is-sensitive-masked",masked);
    let overlay=poster.querySelector(".sensitive-image-mask");
    if(masked && !overlay){poster.insertAdjacentHTML("beforeend",sensitiveImageMaskMarkup(item.restriction==="r18"?"敏感内容":"分级待确认"));overlay=poster.querySelector(".sensitive-image-mask");}
    if(overlay)overlay.hidden=!masked;
  });
  sourceSearchView.setMaskSensitive(["source","both"].includes(scope));
}
for(const name of ["click","dblclick","keydown"])grid.addEventListener(name,event=>{
  const poster=event.target.closest(".poster");
  if(!poster || name==="keydown" && !["Enter"," "].includes(event.key))return;
  if(!poster.classList.contains("is-sensitive-masked") && (poster!==recentlyRevealedPoster || performance.now()-searchMaskRevealedAt>650))return;
  event.preventDefault();event.stopImmediatePropagation();
  if(!poster.classList.contains("is-sensitive-masked"))return;
  poster.classList.remove("is-sensitive-masked");
  poster.querySelector(".sensitive-image-mask").hidden=true;
  searchImageReveals.reveal(poster.closest("[data-sensitive-image]").dataset.sensitiveImage);
  recentlyRevealedPoster=poster;searchMaskRevealedAt=performance.now();
},true);
function applyDownloadDefaults(){
  const prefs=cloudControls.getPreferences();
  $("#saveRoot").value=prefs.saveRoot;$("#createFolder").checked=prefs.createFolder;downloadDefaultsTouched=false;
}
const browseOptions=createBrowseOptions({ready:async()=>{if(lastKnownLoggedIn===null)await syncAuthStatus();await cloudControls.ready();},save:patch=>cloudControls.savePreferences(patch),report:announceToast});
const cloudControls=createCloudControls({
  request:fetchJson,session:()=>({logged:lastKnownLoggedIn===true,generation:lastKnownAuthorizationGeneration,method:loginMethod}),
  login:()=>{void openAccount("home");},report:message=>showTaskDock("Pixiv",message,4500),
  changed:(kind,id,state,previous)=>{
    accountView.cloudChanged(kind,id,state,previous);
    if(kind==="follow")discoveryView?.followingChanged(id,state);
  },
  cachedTags:()=>accountView.cachedTags(),cleared:()=>{accountView.clearCache();discoveryView?.clear();},
  prepareAccount:options=>Promise.all([accountView.prepare(options),discoveryView?.prepare(options)]),
  checkUpdates:options=>accountView.checkUpdates(options),
  clearStarted:()=>Promise.all([accountView.stopBackground(),discoveryView?.stopBackground()]),
  preferencesChanged:(prefs,sync,loaded)=>{
    browseOptions.configure(prefs,loaded);syncHomeSortControl();syncSearchSortControl();
    settingsView?.update(prefs,sync,loaded);
    const syncAction=accountSyncPresentation(prefs,sync,loaded);
    const syncButton=$("#accountSync");
    syncButton.disabled=lastKnownLoggedIn!==true || syncAction.disabled;
    syncButton.textContent=syncAction.busy?"正在同步…":"同步账号状态";
    $("#accountSyncStatus").textContent=lastKnownLoggedIn===true?syncAction.status:"登录后可同步账号状态";
    accountView.configure(prefs,loaded);
    discoveryView?.configure(prefs,loaded);
    if(loaded){mediaBudget.configure(prefs.mediaMemory);applyPreviewLayout(prefs.previewLayout);applyPreviewAction(prefs.previewAction);imageViewer.setSize(prefs.previewSize);applySensitiveMask(prefs.sensitiveMask);}
    if(loaded && !initialDownloadDefaultsApplied){initialDownloadDefaultsApplied=true;if(!downloadDefaultsTouched)applyDownloadDefaults();}
  },
  official:async id=>{
    if(!window.pywebview?.api?.pixiv_official_author)return {ok:false,error:"请在 MOKU 桌面窗口中打开官网"};
    return window.pywebview.api.pixiv_official_author(id);
  },
});
settingsView=createAccountSettings({root:$("[data-account-settings]"),save:patch=>cloudControls.savePreferences(patch),
  syncNow:()=>cloudControls.refresh(),clearCache:()=>cloudControls.clearCache(),
  browse:initial=>fetchJson("/api/system/select-folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initial})},60000),
  report:announceToast});
$("#accountSync").onclick=()=>cloudControls.refresh();
document.querySelectorAll("[data-open-project-notice]").forEach(button=>{
  button.onclick=()=>showAppDialog($("#projectNotice"));
});
document.querySelectorAll("a[data-project-external]").forEach(link=>{
  link.onclick=event=>{
    if(event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;
    const api=window.pywebview?.api;if(!api?.open_source_link)return;
    event.preventDefault();
    void api.open_source_link(link.href).then(result=>{if(!result.ok)showTaskDock("项目链接",result.error || "链接打开失败",5000);})
      .catch(()=>showTaskDock("项目链接","链接打开失败，可右键复制链接",5000));
  };
});
createAppUpdates({root:$("[data-account-settings]"),request:fetchJson,report:announceToast,openUpdates:async()=>{
  const category=$('[data-settings-category="updates"]');category.click();
  await openAccount("settings");
  if(activeWorkspace==="account"){$("#accountPage").scrollTop=0;category.focus({preventScroll:true});queueNavigationRecord();}
},beforeRestart:async()=>{
  if(activeDownload || singleDownloadPending || selection.locked)throw Error("请等待下载任务完成或中断后再更新");
  if(settingsView.dirty)throw Error("请先保存设置改动");
  if(cloudControls.busy)throw Error("请等待收藏或关注操作完成");
  await workspaceSync.settled();await recentSearchWrites;await cloudControls.settled();
  if(cloudControls.busy)throw Error("请等待收藏或关注操作完成");
  if(workspaceSync.active && !workspaceSync.ready)throw Error("本机记录尚未保存，请重试保存后更新");
}});
for(const id of ["saveRoot","createFolder"])$("#"+id).addEventListener("input",()=>{downloadDefaultsTouched=true;});
$("#openRequestSettings").onclick=()=>{helpDialog.close();void openAccount("settings");};

const artistPage = createArtistPage({
  root:$("#artistPanel"),fetchJson:(url,options)=>fetchJson(url,options,60000),nextRequestId:nextSearchRequestId,
  cancelRequest:id=>notifySearchCancellation(id),openArtist:openArtistPage,
  openArtwork:item=>{void select(-1,item);openDownloadPage();},
  onSort:order=>void changeGallerySort(order),onLoad:()=>void startGalleryLoad({refresh:galleryLoadComplete()}),
  changed:queueNavigationRecord,allowed:()=>!contextNavigationLocked(),escape:esc,
});

const galleryDefaultViews=new Map(),galleryPopularViews=new Map(),galleryPopularityJobs=new Map();
let galleryPopularityController=null;
let galleryIntent=null,galleryLinkedEntry=false,galleryDraftOrder="default",galleryPreferenceKind="tags",galleryStopRequested=false;
$("#homeSortControl").innerHTML=popularitySortMarkup("data-home-sort");
$("#homeSortControl").dataset.ready="1";
$("#homeSortControl").querySelector("[data-home-sort]").onclick=()=>{
  const kind=searchKind.value;
  browseOptions.choose(kind,browseOptions.order(kind)==="popular"?"default":"popular");syncHomeSortControl();
};

$("#searchSortControl").innerHTML=popularitySortMarkup("data-search-sort")+'<span id="searchSortStatus" class="popularity-status" role="status"></span>';
$("#searchSortControl").dataset.ready="1";
$("#searchSortControl").querySelectorAll("[data-search-sort]").forEach(button=>button.onclick=()=>void changeGallerySort(galleryDraftOrder==="popular"?"default":"popular"));
$("#searchSortControl").querySelector("[data-popularity-start]").onclick=()=>void startGalleryLoad({refresh:galleryLoadComplete()});
function syncHomeSortControl(){
  const root=$("#homeSortControl");if(root.dataset.ready!=="1")return;
  setPopularitySort(root,browseOptions.order(searchKind.value));setPopularityLoad(root,false);
}

function galleryLoadComplete(){
  return resultSelectionEnabled && !galleryIntent && galleryDraftOrder===(activeSearchFilters.order || 'default');
}
function syncSearchSortControl(message){
  const root=$("#searchSortControl");
  if(root.dataset.ready!=="1")return;
  root.hidden=activeSearchContext.kind==="uid" || (!resultSelectionEnabled && !galleryIntent && !searchPending) || searchSurface!=="gallery";
  const order=galleryDraftOrder;
  setPopularitySort(root,order);
  const busy=galleryLinkedEntry && !galleryStopRequested && Boolean(searchPending || galleryPopularityController);
  setPopularityLoad(root,galleryLinkedEntry,busy,browseOptions.buttonVisible(),galleryLoadComplete());artistPage.setLoading(galleryLinkedEntry,busy,browseOptions.buttonVisible(),galleryLoadComplete());
  if(activeSearchContext.kind==="uid"){
    const snapshot=artistPage.snapshot();if(snapshot.sort!==order)artistPage.setSort(order,snapshot.sortMessage);
  }
  if(message!==undefined)$("#searchSortStatus").textContent=message;
}
function setGallerySort(order,message=""){
  galleryDraftOrder=order;
  if(activeSearchContext.kind==="uid")artistPage.setSort(order,message);
  else{
    setPopularitySort($("#searchSortControl"),order);
    $("#searchSortStatus").textContent=message;
  }
  syncSearchSortControl();
}

function gallerySortKey(){
  return galleryViewKey(activeTagQuery,activeSearchFilters);
}
function galleryViewKey(query,options){
  const {order,popularityIndex,...filters}=options;
  return JSON.stringify([lastKnownAuthorizationGeneration,query,filters]);
}
function rememberGalleryView(){
  const key=gallerySortKey(),saved=capturePageState();saved.source.intent=null;
  const views=activeSearchFilters.order==="popular"?galleryPopularViews:galleryDefaultViews;
  views.delete(key);views.set(key,saved);
  while(views.size>8)views.delete(views.keys().next().value);
}
function cancelGalleryPopularity(){
  if(!galleryPopularityController)return;
  galleryPopularityController.abort();galleryPopularityController=null;
  setGallerySort(activeSearchFilters.order==="popular"?"popular":"default");
}
async function changeGallerySort(order){
  if(contextNavigationLocked())return;
  galleryDraftOrder=order;browseOptions.choose(galleryPreferenceKind,order);
  if(galleryIntent || galleryLinkedEntry && !browseOptions.automatic() || activeSearchContext.kind==="author"){
    galleryStopRequested=true;cancelGalleryPopularity();cancelActiveSearch();
    if(galleryIntent)galleryIntent.order=order;
    galleryDraftOrder=order;setGallerySort(order,"已选择，点击开始加载");return;
  }
  if(activeSearchContext.kind==="pid"){
    activeSearchFilters={...activeSearchFilters,order};setGallerySort(order);queueNavigationRecord();return;
  }
  if(!resultSelectionEnabled)return;
  const key=gallerySortKey();
  preserveViewport(window,preparePageNavigation);checkpointNavigation();
  if(order==="default"){
    if(activeSearchFilters.order!=="popular"){setGallerySort("default");return;}
    galleryPopularViews.set(key,capturePageState());
    const saved=galleryDefaultViews.get(key);
    if(saved){restoreSortedView(saved);queueNavigationRecord();}
    else{const {order,popularityIndex,...filters}=activeSearchFilters;await search(activeTagQuery,1,filters,null,{preferenceKind:galleryPreferenceKind});}
    return;
  }
  if(activeSearchFilters.order!=="popular"){
    const saved=capturePageState();saved.artist.sort="default";saved.artist.sortMessage="";
    galleryDefaultViews.set(key,saved);
    while(galleryDefaultViews.size>8){const first=galleryDefaultViews.keys().next().value;galleryDefaultViews.delete(first);galleryPopularViews.delete(first);galleryPopularityJobs.delete(first);}
  }
  const saved=galleryPopularViews.get(key);
  if(saved && !galleryPopularityJobs.has(key)){restoreSortedView(saved);queueNavigationRecord();return;}
  await loadGalleryPopularity(1);
}
function restoreSortedView(saved){
  preserveViewport(window,()=>{artistPage.restore(saved.artist);restoreResultPage(saved.source);});
  void refreshDownloadedIndicators();
}
async function loadGalleryPopularity(page,{refresh=false,signal=null}={}){
  cancelGalleryPopularity();cancelSearchPrefetch();
  const controller=new AbortController();galleryPopularityController=controller;
  galleryStopRequested=false;
  const abort=()=>controller.abort();signal?.throwIfAborted();signal?.addEventListener("abort",abort,{once:true});
  const account=lastKnownAuthorizationGeneration,uid=activeSearchContext.value,filters={...activeSearchFilters};
  const author=activeSearchContext.kind==="uid",key=gallerySortKey();
  if(refresh)galleryPopularityJobs.set(key,{index:"",refresh:true});
  const queued=galleryPopularityJobs.get(key);
  setGallerySort("popular",author?"正在准备作者作品元数据…":"正在准备搜索候选池 · 最多 500 件…");
  try{
    const result=await readPopularityPage(requestAccount,{kind:author?"author":"search",uid:author?uid:"",query:author?"":activeTagQuery,fuzzy:String(Boolean(filters.fuzzy)),mode:filters.mode,workType:filters.workType,
      includeAi:String(filters.includeAi),startDate:filters.startDate || "",endDate:filters.endDate || "",page:String(page),
      index:queued?queued.index:filters.popularityIndex || "",refresh:queued?.refresh?"1":"0"},{signal:controller.signal,
      progress:state=>{if(galleryPopularityController===controller){
        galleryPopularityJobs.set(key,{index:state.index,refresh:false});
        setGallerySort("popular",state.ready?`${author?"全部可见作品":"本次搜索候选池"} · ${state.total} 件`:`已读取 ${state.loaded} 件${state.counted?` · 已核对 ${state.counted} 件收藏数`:""}，正在准备…`);
      }}});
    if(galleryPopularityController!==controller || account!==lastKnownAuthorizationGeneration)return false;
    galleryPopularityJobs.delete(key);
    searchGeneration++;
    items=result.items;currentPage=result.page;pageNumbers=[1,result.pages];firstAvailablePage=1;preloadedThrough=result.pages;
    searchHasMore=false;searchOrderingPending=false;resultSelectionEnabled=true;
    activeSearchFilters={...filters,order:"popular",popularityIndex:result.index};activeSearchSnapshotId=`pop-${result.index}`;activeSearchPageKey=null;
    searchLoadedAt=Date.now();reconcileSelectedArtworkPreviews(items);
    $("#count").textContent=`按收藏数排序 · ${author?"全部可见作品":"本次搜索候选池（最多 500 件）"} ${result.total} 件 · 第 ${currentPage}/${result.pages} 页`;
    render();renderPagination();setGallerySort("popular",`${author?"全部可见作品":"本次搜索候选池"} · ${result.total} 件`);
    void refreshDownloadedIndicators();if(!searchPending)rememberGalleryView();queueNavigationRecord();return true;
  }catch(error){
    if(!controller.signal.aborted && galleryPopularityController===controller)setGallerySort(activeSearchFilters.order==="popular"?"popular":"default",`${error.message} · 点击滑块右侧重试`);
    return false;
  }finally{signal?.removeEventListener("abort",abort);if(galleryPopularityController===controller){galleryPopularityController=null;syncSearchSortControl();}}
}

async function openLinkedSearch(tag,returnTarget,options={}){
  const revision=navigationRevision;
  try{
    await browseOptions.ready();if(revision!==navigationRevision)return;
    const match=tag.match(/^\s*(pid|uid|author)\s*[:：]\s*(.+)$/i);
    const context=match?{kind:match[1].toLowerCase(),value:match[2].trim()}:{kind:"tags",value:tag};
    const filters=readSearchFilters(),order=options.order || browseOptions.order(context.kind);
    galleryPreferenceKind=context.kind;galleryDraftOrder=order;galleryLinkedEntry=true;
    galleryIntent={tag,page:options.page || 1,filters,returnTarget,order,preferenceKind:context.kind};
    activeTagQuery=tag;activeSearchContext=context;activeSearchFilters={...filters};activeSearchSnapshotId=null;
    items=[];currentPage=1;pageNumbers=[1];preloadedThrough=1;firstAvailablePage=1;searchHasMore=false;searchOrderingPending=false;resultSelectionEnabled=false;
    contextReturns.gallery=returnTarget;searchSurface="gallery";
    if(context.kind==="uid")artistPage.prepareProfile(context.value,order,!browseOptions.automatic());
    else if(context.kind==="author")artistPage.showName(context.value,{loading:false,paused:true});
    else artistPage.clear();
    $("#tag").value=tag;syncSearchKind();$("#tagTitle").textContent=context.value;$("#count").textContent="选择排序后点击开始加载";
    render();$("#pagination").innerHTML="";scrollToResults();setGallerySort(order);queueNavigationRecord();
    if(browseOptions.automatic() || options.start)await startGalleryLoad();
  }catch(error){if(revision===navigationRevision)announceToast(error.message);}
}
async function startGalleryLoad({restoring=false,refresh=false}={}){
  if(searchPending || galleryPopularityController){
    const wanted=galleryDraftOrder;galleryStopRequested=true;
    cancelGalleryPopularity();cancelActiveSearch();galleryDraftOrder=wanted;
    syncSearchSortControl();return;
  }
  if(contextNavigationLocked() && !restoring)return;
  const intent=galleryIntent || {tag:activeTagQuery,page:1,filters:{...activeSearchFilters},returnTarget:contextReturns.gallery,preferenceKind:galleryPreferenceKind};
  const revision=navigationRevision,order=galleryDraftOrder;
  const key=galleryViewKey(intent.tag,intent.filters),saved=(order==="popular"?galleryPopularViews:galleryDefaultViews).get(key);
  if(!refresh && saved && !galleryPopularityJobs.has(key)){
    galleryIntent=null;
    restoreSortedView({...saved,source:{...saved.source,intent:null,linked:galleryLinkedEntry,draft:order,preferenceKind:galleryPreferenceKind}});
    syncSearchSortControl();queueNavigationRecord();return;
  }
  const committed=await search(intent.tag,refresh?1:intent.page,intent.filters,null,{manualRefresh:refresh,returnTarget:intent.returnTarget,order,preferenceKind:intent.preferenceKind});
  if(revision===navigationRevision && committed)galleryIntent=null;
  syncSearchSortControl();queueNavigationRecord();
}
function openArtistPage(userId,options={}){
  preparePageNavigation();checkpointNavigation();
  const source=capturePageState();
  if(source.artist?.kind==="candidates" && !options.order)options={...options,order:galleryDraftOrder};
  const parent=source.view==="discovery" || source.view==="gallery" && source.artist?.kind!=="profile"?source
    : source.view==="detail" && contextReturns.detail?pageHistory?.find(page=>page.key===contextReturns.detail.key && (page.view==="discovery" || page.view==="gallery" && page.artist?.kind!=="profile")):null;
  const tag=userId?`uid:${userId}`:`author:${options.name}`;
  void openLinkedSearch(tag,parent?{key:parent.key,label:parent.view==="discovery"?"发现":"搜索结果"}:null,options);
}

document.addEventListener("click",event=>{
  const link=event.target.closest("[data-artist-id],[data-tag-name]");
  if(!link)return;
  event.preventDefault();
  if(contextNavigationLocked() || mokuHasSelectedText(link))return;
  if(link.dataset.artistId)openArtistPage(link.dataset.artistId);
  else{preparePageNavigation();checkpointNavigation();const parent=capturePageState();void openLinkedSearch(link.dataset.tagName,{key:parent.key});}
});

const galleryInteraction = createGalleryInteraction({
  grid,
  allowed: () => !contextNavigationLocked(),
  openArtwork: index => { void select(index); openDownloadPage(); },
  openArtist: item => {
    if (!item.userId) return;
    openArtistPage(item.userId);
  },
  report: message => showTaskDock("预览加载失败", message, 4000),
});

async function requestAccount(url,options={}) {
  if(options.method && options.method!=="GET")return fetchJson(url,options,60000);
  const id=nextSearchRequestId(),cancel=()=>notifySearchCancellation(id);
  options.signal?.throwIfAborted();options.signal?.addEventListener("abort",cancel,{once:true});
  try{return await fetchJson(`${url}${url.includes("?")?"&":"?"}requestId=${encodeURIComponent(id)}`,options,url.startsWith("/api/popularity?")?90000:60000);}
  finally{options.signal?.removeEventListener("abort",cancel);}
}
const downloadStatistics=createDownloadStatistics({root:$("#downloadStatistics"),request:fetchJson});
const accountView=createAccountPage({root:$("#accountPage"),request:requestAccount,escape:esc,statistics:downloadStatistics,
  allowed:()=>!contextNavigationLocked(),changed:queueNavigationRecord,
  selected:id=>selection.has(id),canChoose:()=>!selection.locked && !singleDownloadPending && !workspaceSync.loading,choose:(item,checked,origin)=>toggleArtworkSelection(item,checked,origin),
  openUpdates:scope=>openDiscovery({kind:"following",scope},true),
  openArtist:id=>{accountOrigin=true;openArtistPage(id);},
  openArtwork:(item,origin)=>{accountOrigin=true;void select(-1,item,origin.context);openDownloadPage();},
  installImages:installImageFallbacks,attachUgoira:attachUgoiraHoverTargets,
  report:message=>showTaskDock("账户",message,4000),mode:()=>readSearchFilters().mode,
  signedIn:()=>lastKnownLoggedIn===true,connectionChanged:syncAppConnection,settingsReady:()=>cloudControls.ready(),
  autoPrepare:()=>cloudControls.getPreferences().autoSync,
  getWork:id=>[currentDetailItem,basketDetailItem,...items,...selection.snapshot()].find(item=>item?.id===id) || null,
  getAuthor:id=>{
    const profile=artistPage.snapshot().profile;
    if(profile?.userId===id)return {id,name:profile.name,avatar:profile.avatar,bio:profile.description,bioLoaded:true,isNew:false};
    const work=[currentDetailItem,basketDetailItem,...items,...selection.snapshot()].find(item=>item?.userId===id);
    return work?{id,name:work.artist,avatar:"",bio:"",bioLoaded:false,isNew:false}:null;
  },
});

discoveryView=createDiscoveryPage({root:$("#discoveryPage"),request:requestAccount,escape:esc,
  viewed:rows=>accountView.seenWorks(rows),
  allowed:()=>!contextNavigationLocked(),signedIn:()=>lastKnownLoggedIn===true,generation:()=>lastKnownAuthorizationGeneration,
  changed:()=>{queueNavigationRecord();void cloudControls.hydrate();},
  openArtwork:(item,origin)=>{accountOrigin=false;void select(-1,item,origin.context);openDownloadPage();},
  openArtist:id=>{accountOrigin=false;openArtistPage(id);},
  choose:toggleArtworkSelection,selected:id=>selection.has(id),canChoose:()=>!selection.locked && !singleDownloadPending && !workspaceSync.loading,
  installImages:installImageFallbacks,attachUgoira:attachUgoiraHoverTargets,
  report:message=>showTaskDock("发现",message,4000),browse:browseOptions,autoPrepare:()=>cloudControls.getPreferences().autoSync});

async function openDiscovery(options={},linked=false){
  preparePageNavigation();checkpointNavigation();
  const parent=linked?capturePageState():null,revision=navigationRevision;
  closeAllViewer();closeBasketPage();accountOrigin=false;contextReturns=parent?{discovery:{key:parent.key}}:{};
  setWorkspace("discovery");
  if(lastKnownLoggedIn===null){
    await syncAuthStatus();
    if(revision!==navigationRevision || activeWorkspace!=="discovery")return;
  }
  await Promise.all([discoveryView.open(options),syncAuthStatus()]);
  if(revision===navigationRevision && activeWorkspace==="discovery")queueNavigationRecord();
  return revision===navigationRevision && activeWorkspace==="discovery" && !discoveryView.snapshot().waiting;
}

async function openAccount(view) {
  preparePageNavigation();
  const revision=navigationRevision;
  checkpointNavigation();closeAllViewer();closeBasketPage();accountOrigin=true;
  contextReturns={};
  setWorkspace("account");
  if(lastKnownLoggedIn===null){
    await syncAuthStatus();
    if(revision!==navigationRevision || activeWorkspace!=="account")return;
  }
  const loading=accountView.open(view?{view}:undefined);
  await Promise.all([loading,syncAuthStatus()]);
  if(revision===navigationRevision && activeWorkspace==="account")queueNavigationRecord();
}

const sourceSearchView=createSourceSearch({root:$("#reversePage"),settingsRoot:$("[data-source-settings]"),
  installImages:installImageFallbacks,
  request:fetchJson,escape:esc,changed:queueNavigationRecord,allowed:()=>!contextNavigationLocked(),report:announceToast,
  openArtwork:async id=>{
    if(contextNavigationLocked())return;
    preparePageNavigation();checkpointNavigation();const parent=capturePageState();accountOrigin=false;closeAllViewer();
    await openLinkedSearch(`pid:${id}`,parent,{start:true});
  },
  openExternal:(url,event)=>{
    const api=window.pywebview?.api;if(!api?.open_source_link)return;
    event.preventDefault();
    void api.open_source_link(url).then(result=>{if(!result.ok)announceToast(result.error);}).catch(()=>announceToast("来源链接打开失败，可右键复制链接"));
  },
  preview:(row,thumb)=>imageViewer.open({item:{id:row.id,source:"saucenao",title:row.title || row.indexName || "来源预览",pages:1,width:0,height:0,thumb:row.thumb,
    pageImages:[{regular:row.thumb,original:"",width:0,height:0}]},preview:thumb,allowOriginal:false}),
});
$("#sourceSearchEntry").onclick=async()=>{
  if(contextNavigationLocked())return;
  preparePageNavigation();checkpointNavigation();
  const parent=capturePageState();
  closeAllViewer();closeBasketPage();accountOrigin=false;contextReturns={reverse:{key:parent.key}};
  workspaceReturn=null;setWorkspace("reverse");await sourceSearchView.open();queueNavigationRecord();
};

function render() {
  return preserveViewport(window,()=>{
  syncSearchSortControl();
  searchImageReveals.clear();
  galleryInteraction.reset({immediate:true});
  galleryPreviewCache.park(grid);
  gallery.classList.add("in");
  grid.className = "grid";
  updateSelectionBar();
  if(galleryIntent && !items.length){
    galleryInteraction.clear();grid.innerHTML='<p class="empty-state">选好排序后，点击开始加载。</p>';return;
  }
  if (!items.length) {
    galleryInteraction.clear();
    grid.innerHTML = currentPageNeedsMoreResults()
      ? (searchOrderingPending
        ? '<p class="empty-state">已读取作品，正在确认日期排序；可点击“继续加载本页”继续确认有序结果。</p>'
        : '<p class="empty-state">本次尚未找到符合条件的作品，可点击“继续加载本页”再查一段。</p>')
      : '<p class="empty-state">当前页没有符合安全范围的作品。</p>';
    return;
  }

  recentlyRevealedPoster=null;searchMaskRevealedAt=-Infinity;
  grid.innerHTML = renderArtworkCards(items,{escape:esc,selected:id=>selection.has(id),maskSensitive:["search","both"].includes(sensitiveMaskScope)});
  galleryPreviewCache.mount(grid,JSON.stringify([activeTagQuery,activeSearchSnapshotId,currentPage,activeSearchFilters]));
  installImageFallbacks(grid);
  attachUgoiraHoverTargets(grid);

  galleryInteraction.mount(items);
  grid.querySelectorAll("[data-select]").forEach((box) => {
    box.onchange = () => {
      const accepted = toggleArtworkSelection(items[Number(box.dataset.select)], box.checked);
      if (!accepted) box.checked = selection.has(items[Number(box.dataset.select)].id);
    };
  });
  });
}

async function refreshDownloadedIndicators() {
  const generation = searchGeneration;
  const ids = items.map(item => String(item.id)).filter(id => /^\d+$/.test(id));
  const visibleIds = new Set(ids);
  for (const id of downloadedPages.keys()) if (!visibleIds.has(id)) downloadedPages.delete(id);
  if (!ids.length) return;
  try {
    const data = await fetchJson(`/api/library/catalog?${new URLSearchParams({ids: ids.join(",")})}`);
    if (generation !== searchGeneration) return;
    for (const id of ids) downloadedPages.set(id, data.pages?.[id] || []);
    grid.querySelectorAll("[data-downloaded]").forEach(badge => {
      const count = downloadedPages.get(badge.dataset.downloaded)?.length || 0;
      badge.hidden = !count;
      badge.textContent = `已下载 ${count}P`;
    });
  } catch { /* Download status is optional and must not hide search results. */ }
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
  const overlayOpen = activeScrollSurface() !== window;
  const galleryRect = $("#gallery").getBoundingClientRect();
  const dockTop = window.innerHeight - (dock.offsetHeight || 0);
  const dockOverGallery = galleryRect.top < dockTop && galleryRect.bottom > dockTop;
  dock.classList.toggle("is-visible", hasPagination && !overlayOpen && dockOverGallery);
  const rail = document.querySelector('.page-rail');
  const railHidden = false;
  if (rail.hidden !== railHidden) rail.hidden = railHidden;
  const sections = ['home', 'gallery'];
  const marker = window.innerHeight * .4;
  const active = libraryWorkspaceOpen() ? activeWorkspace
    : basketPageOpen() ? "basket" : accountOrigin ? "account" : activeWorkspace === "download" ? "detail"
    : sections.reduce((current, id) => $(`#${id}`).getBoundingClientRect().top <= marker ? id : current, 'home');
  rail.querySelectorAll('a, button').forEach(link => {
    if (link.hash === `#${active}` || link.dataset.view === active) {
      if (link.getAttribute('aria-current') !== 'location') link.setAttribute('aria-current', 'location');
    } else if (link.hasAttribute('aria-current')) link.removeAttribute('aria-current');
  });
}

function activeScrollSurface() {
  if (!$("#allViewer").hidden) return $("#allViewer");
  if (libraryWorkspaceOpen()) return $(`#${activeWorkspace}Page`);
  if (basketPageOpen()) return $("#basketPage");
  if (activeWorkspace === "download") return $("#downloadPage");
  return window;
}

function updateFloatingChrome() {
  const surface = activeScrollSurface();
  const overlayOpen = surface !== window;
  const scrollOffset = surface === window ? window.scrollY : surface.scrollTop;
  const header = document.querySelector('.app-header');
  header.hidden = false;
  const chromeHeight = header.offsetHeight;
  const chromeValue = `${chromeHeight}px`;
  if (document.documentElement.style.getPropertyValue("--chrome-height") !== chromeValue) {
    document.documentElement.style.setProperty("--chrome-height", chromeValue);
  }
  $("#backTop").classList.toggle("is-visible", scrollOffset > 600);
  const entry = $("#basketEntry");
  const count = selection.size;
  if (entry.classList.contains("is-visible")) entry.classList.remove("is-visible");
  const rail = document.querySelector('.page-rail');
  const railHidden = false;
  if (rail.hidden !== railHidden) rail.hidden = railHidden;
  if ($("main").inert !== overlayOpen) $("main").inert = overlayOpen;
  if ($("footer").inert !== overlayOpen) $("footer").inert = overlayOpen;
  for (const id of ["downloadPage", "basketPage", "historyPage", "accountPage", "discoveryPage", "reversePage"]) {
    const page = $(`#${id}`), inert = overlayOpen && page !== surface;
    if (page.inert !== inert) page.inert = inert;
  }
  const label = `采集篮 ${count} 作品 · ${selectedPageCount()} 张`;
  if (entry.textContent !== label) entry.textContent = label;
  const disabled = selection.locked || searchPending;
  if (entry.disabled !== disabled) entry.disabled = disabled;
  queueNavigationRecord(true);
}

// Keep page state intact while releasing media owned by inactive views.
function syncPreviewResources({releaseIdle = false} = {}) {
  const surface = activeScrollSurface();
  const normalVisible = !document.hidden && surface === $("#downloadPage");
  const basketVisible = !document.hidden && surface === $("#basketPage");
  normalDetailView.setVisible(normalVisible && !document.body.classList.contains("batch-mode"));
  basketDetailView.setVisible(basketVisible && basketReturnMode==="detail");
  if (basketVisible && basketReturnMode === "picker") {
    if (!basketThumbnailObserver) loadBasketThumbnails();
  } else if (basketThumbnailObserver || basketThumbnailQueue.length || basketThumbnailControllers.size) {
    stopBasketThumbnails();
  }
  let needsRelease = false;
  for (const [selector, visible] of [
    ["#deck, #collectionPages", normalVisible],
    ["#basketDetailDeck, #basketPages", basketVisible && basketReturnMode === "detail"],
  ]) {
    document.querySelectorAll(selector).forEach(root => {
      if (visible) {
        const suspended = root.querySelectorAll("img[data-suspended-src]");
        if (suspended.length) installImageFallbacks(root);
        suspended.forEach(img => {
          const source = img.dataset.suspendedSrc;
          delete img.dataset.suspendedSrc;
          img.src = source;
        });
      } else {
        root.querySelectorAll("img[src]").forEach(img => {
          if (releaseIdle) {
            // Retire the old request owner so its queued errors cannot affect
            // the image restored on the next visit.
            const suspended = img.cloneNode(false);
            suspended.dataset.suspendedSrc = img.getAttribute("src");
            suspended.removeAttribute("src");
            delete suspended.dataset.fallbackReady;
            img.replaceWith(suspended);
            img.removeAttribute("src");
          } else needsRelease = true;
        });
      }
    });
  }
  clearTimeout(detailImageIdleTimer);
  detailImageIdleTimer = null;
  if (needsRelease) {
    detailImageIdleTimer = setTimeout(() => {
      detailImageIdleTimer = null;
      syncPreviewResources({releaseIdle: true});
    }, 30000);
  }
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
  navigatePrimary(link.hash);
}));
document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); navigatePrimary('#home'); });
for (const id of ["basketPage", "allViewer", "downloadPage", "historyPage", "accountPage", "discoveryPage", "reversePage"]) {
  $(`#${id}`).addEventListener("scroll", schedulePaginationDockUpdate, { passive: true });
}

function contextNavigationLocked() {
  return Boolean(pageHistory?.status().restoring);
}

function syncNavigationAvailability() {
  document.querySelector('.page-rail').querySelectorAll('a, button').forEach(link => {
    if(!["pageBack","pageForward"].includes(link.id))link.setAttribute('aria-disabled', 'false');
  });
  syncPageHistoryControls();
}

function libraryWorkspaceOpen() {
  return ["history", "account", "discovery", "reverse"].includes(activeWorkspace);
}

function pruneNavigationCache(entries,cursor) {
  const focus=entries[cursor].source,retained=new Set(),now=Date.now();
  const order=entries.map((_,index)=>index).sort((a,b)=>Math.abs(a-cursor)-Math.abs(b-cursor));
  for(const index of order){
    const state=entries[index],source=state.source;
    if(source.items===null || (!source.ready && state.artist?.kind!=="candidates"))continue;
    const key=JSON.stringify([source.query,source.snapshot,source.page,source.filters]);
    const far=source.snapshot && source.snapshot===focus.snapshot && source.query===focus.query && Math.abs(source.page-focus.page)>SEARCH_KEEP_BEHIND;
    if(index!==cursor && (far || (!retained.has(key) && (now-state.cachedAt>30*60*1000 || retained.size>=8)))){
      // Keep navigation identity and position, release only the cached payload.
      source.items=null;state.artist=null;
      if(state.normal)state.normal={...state.normal,item:null};
    }else retained.add(key);
  }
  searchPageCache.prune(retained);
  galleryPreviewCache.prune(new Set([...retained,...searchPageCache.warmKeys()]));
}

function preparePageNavigation() {
  cancelGalleryPopularity();
  navigationRevision++;
  pageHistory?.cancelRestore();
  pageRefreshController?.abort();pageRefreshController=null;pageRefreshPending=false;
  if(searchPending){
    const previous=searchController?._mokuRestoreView;
    cancelActiveSearch();searchController=null;searchPending=false;searchGeneration++;
    activeSearchRequestId=null;activeSearchPageKey=null;
    if(previous){
      restoreInterruptedSearch(previous);
      artistPage.restore(previous.artist);contextReturns={...previous.contextReturns};
      render();renderPagination();$("#count").textContent=previous.count;
    }
    grid.setAttribute("aria-busy","false");
    searchButton.textContent="开始寻找";cancelSearchButton.hidden=true;
  }
  checkpointNavigation();
  invalidateDetailView();
  syncSearchScopedControls();
}

function capturePageState() {
  const baseView = libraryWorkspaceOpen() ? activeWorkspace : basketPageOpen() ? "basket"
    : activeWorkspace === "download" ? (document.body.classList.contains("batch-mode") ? "batch" : "detail") : searchSurface;
  const view = $("#allViewer").hidden ? baseView : "viewer";
  const normal = basketPageOpen() ? basketDownloadSnapshot : {item:currentDetailItem,context:currentDetailContext,artworkId:activeArtworkId,pageOffset:collectionPageOffset};
  const form = Object.fromEntries(["tag","safety","workType","datePreset","startDate","endDate"].map(id=>[id,$(`#${id}`).value]));
  form.includeAi=$("#includeAi").checked;form.fuzzySearch=$("#fuzzySearch").checked;
  const sourceKey=JSON.stringify([activeTagQuery,currentPage,activeSearchFilters]);
  const identity = view === "reverse" ? "reverse" : view === "discovery" ? JSON.stringify(discoveryView.snapshot()) : view === "account" ? "account" : view === "home" || view === "gallery" ? `${accountOrigin}:${sourceKey}`
    : view === "basket" ? `${basketReturnMode}:${basketDetailItem?.id || ""}`
    : view === "detail" || view === "viewer" ? `${accountOrigin}:${baseView}:${activeArtworkId || ""}:${sourceKey}` : "";
  return {
    key:JSON.stringify([view,identity]),view,baseView,cachedAt:Date.now(),authorization:lastKnownAuthorizationGeneration,
    source:{query:activeTagQuery,page:currentPage,filters:{...activeSearchFilters},context:{...activeSearchContext},snapshot:activeSearchSnapshotId,
      items:items.slice(),pages:pageNumbers.slice(),first:firstAvailablePage,through:preloadedThrough,hasMore:searchHasMore,
      ordering:searchOrderingPending,ready:resultSelectionEnabled,label:$("#tagTitle").textContent,count:$("#count").textContent,loadedAt:searchLoadedAt,sortMessage:$("#searchSortStatus")?.textContent || "",intent:galleryIntent?structuredClone(galleryIntent):null,linked:galleryLinkedEntry,draft:galleryDraftOrder,preferenceKind:galleryPreferenceKind},
    form,searchSurface,account:accountView.snapshot(),discovery:discoveryView.snapshot(),reverse:sourceSearchView.snapshot(),accountOrigin,contextReturns:{...contextReturns},batch:document.body.classList.contains("batch-mode"),workspaceReturn,artist:{...artistPage.snapshot(),...(galleryIntent && searchPending?{paused:true}:{})},
    normal:normal?.item ? {item:normal.item,context:normal.context,page:normal.stagePage ?? normalDetailView.state().page,offset:normal.pageOffset,loadedAt:detailLoadedAt} : null,
    normalId:normal?.artworkId || null,
    basket:basketPageOpen() ? {mode:basketReturnMode,id:basketDetailItem?.id || null,offset:collectionPageOffset,artworkOffset:basketArtworkOffset,
      originWorkspace:basketDownloadSnapshot?.workspace || null,stagePage:basketDetailView.state().page} : null,
    quality:$("#quality").value,format:$("#format").value,viewerOffset:viewerPageOffset,
    download:{saveRoot:$("#saveRoot").value,createFolder:$("#createFolder").checked,groupArtworks:$("#groupArtworks").checked,skipExisting:$("#skipExisting").checked},
    scroll:{window:{left:window.scrollX,top:window.scrollY},download:$("#downloadPage").scrollTop,basket:$("#basketPage").scrollTop,
      history:$("#historyPage").scrollTop,account:$("#accountPage").scrollTop,discovery:$("#discoveryPage").scrollTop,reverse:$("#reversePage").scrollTop,viewer:$("#allViewer").scrollTop},
  };
}

function checkpointNavigation() {
  if(pageHistory && !pageRefreshPending && !pageHistory.status().restoring)pageHistory.record(capturePageState());
}
function queueNavigationRecord(deferred=false) {
  clearTimeout(navigationRecordTimer);navigationRecordTimer=null;
  if(deferred){navigationRecordTimer=setTimeout(()=>{navigationRecordTimer=null;queueNavigationRecord();},160);return;}
  if(!pageHistory || pageRefreshPending || pageHistory.status().restoring || navigationRecordFrame!==null)return;
  navigationRecordFrame=requestAnimationFrame(()=>{
    navigationRecordFrame=null;
    if(!searchPending && !pageRefreshPending)pageHistory.record(capturePageState());
  });
}
function syncPageHistoryControls() {
  const state=pageHistory?.status() || {back:false,forward:false,restoring:false};
  const locked=state.restoring;
  const finishedRestore=!locked && document.body.classList.contains("navigation-restoring");
  document.body.classList.toggle("navigation-restoring",state.restoring);
  if(finishedRestore)queueMicrotask(syncSearchScopedControls);
  for(const [id,available] of [["pageBack",state.back],["pageForward",state.forward]]){
    const button=$(`#${id}`);button.disabled=locked || !available;button.setAttribute("aria-disabled",String(button.disabled));
  }
  const currentKey=capturePageState().key;
  document.querySelectorAll("[data-context-back]").forEach(button=>{
    const target=contextReturns[button.dataset.returnSurface],parent=target && target.key!==currentKey && pageHistory?.find(page=>page.key===target.key);
    button.hidden=!parent;
    if(!parent)return;
    button.textContent=`← 返回${target.label || navigationPageLabel(parent,{baseView:basketPageOpen()?"basket":activeWorkspace})}`;button.disabled=locked;
    button.setAttribute("aria-label",button.textContent);
  });
}

function navigationPageLabel(page,current) {
  if(page.view==="home")return "首页";
  if(page.view==="gallery")return page.artist?.kind==="profile"?"作者主页":page.artist?.kind==="candidates"?"作者选择":"搜索结果";
  if(page.view==="basket")return page.basket?.mode==="detail"?"采集篮详情":"采集篮";
  if(page.view==="account")return "我的账号";
  if(page.view==="discovery")return "发现";
  if(page.view==="reverse")return "以图找图";
  if(page.view==="history")return "下载历史";
  if(page.view==="viewer")return "图片浏览";
  if(page.baseView==="batch" || current.baseView==="basket")return "下载页";
  return "作品详情";
}
function assertNavigationAccount(state) {
  if(state.authorization!==null && state.authorization!==lastKnownAuthorizationGeneration)throw new Error("账号已变更，请重新打开页面");
}
function restoreGalleryChoice(source){
  galleryIntent=source.intent || null;galleryLinkedEntry=Boolean(source.linked);galleryDraftOrder=source.draft || source.filters.order || "default";galleryPreferenceKind=source.preferenceKind || source.context.kind;
}
function restoreInterruptedSearch(previous){
  const source=galleryLinkedEntry?{...previous.source,intent:galleryIntent,draft:galleryDraftOrder,linked:true,preferenceKind:galleryPreferenceKind}:previous.source;
  restoreResultPage(source);
}
function restoreResultPage(source){
  const retainCards=source.ready && resultSelectionEnabled && items.length>0 && source.items!==null
    && source.query===activeTagQuery && source.snapshot===activeSearchSnapshotId && source.page===currentPage
    && JSON.stringify(source.filters)===JSON.stringify(activeSearchFilters)
    && source.items.length===items.length && source.items.every((item,index)=>item===items[index]);
  restoreGalleryChoice(source);
  activeTagQuery=source.query;activeSearchContext={...source.context};activeSearchFilters={...source.filters};activeSearchSnapshotId=source.snapshot;
  items=source.items?.slice() || [];currentPage=source.page;pageNumbers=source.pages.slice();firstAvailablePage=source.first;preloadedThrough=source.through;
  searchHasMore=source.hasMore;searchOrderingPending=source.ordering;resultSelectionEnabled=source.ready && source.items!==null;searchLoadedAt=source.loadedAt;
  reconcileSelectedArtworkPreviews(items);$("#tagTitle").textContent=source.label;$("#count").textContent=source.count;
  if(retainCards){syncResultSelectionControls();updateSelectionBar();void cloudControls.hydrate();}else render();
  renderPagination();syncSearchSortControl(source.sortMessage || "");
  if(!resultSelectionEnabled && !galleryIntent)grid.innerHTML='<div class="empty-state"><b>准备就绪</b><p>输入标签后点击“开始寻找”。</p></div>';
}
async function restorePageState(state) {
  const revision=navigationRevision;
  const current=()=>{
    if(revision!==navigationRevision)throw Object.assign(new Error("页面读取已取消"),{name:"AbortError"});
    assertNavigationAccount(state);
  };
  current();
  closeAllViewer();closeBasketPage();invalidateDetailView();clearDetail();
  setWorkspace(["account","discovery","history","reverse"].includes(state.baseView)?state.baseView:null);
  searchSurface=state.searchSurface;accountOrigin=Boolean(state.accountOrigin);
  artistPage.restore(state.artist);
  for(const id of ["tag","safety","workType","datePreset","startDate","endDate"])$(`#${id}`).value=state.form[id];
  $("#includeAi").checked=state.form.includeAi;$("#fuzzySearch").checked=state.form.fuzzySearch;
  syncSearchKind();syncDateInputs();
  const source=state.source;
  if(source.items===null && state.baseView==="gallery")restoreGalleryChoice(source);
  if(source.items===null && state.baseView==="gallery" && source.filters.order==="popular" && !source.intent){
    activeTagQuery=source.query;activeSearchContext={...source.context};activeSearchFilters={...source.filters};
    if(!await loadGalleryPopularity(source.page))throw new Error("原排序页面读取失败，请重新准备人气排序");
    current();
  }else if(source.items===null && state.baseView==="gallery" && !source.intent){
    if(!await search(source.query,source.page,source.filters,source.snapshot,{preferenceKind:source.preferenceKind}))throw new Error("原页面缓存已清理，读取失败，请重试");
  }else{
    restoreResultPage(source);
  }
  current();
  searchSurface=state.searchSurface;
  const wantsNormal=state.baseView==="detail" || state.baseView==="batch" || state.workspaceReturn==="download" || state.basket;
  if(wantsNormal && state.normal){
    const item=state.normal.item || await fetchJson(`/api/pixiv/artwork/${state.normalId}`,{},ARTWORK_DETAIL_TIMEOUT_MS);
    current();
    if(!state.normal.item)rememberArtworkDetail(item);
    activeArtworkId=item.id;renderDetail(item,items.findIndex(row=>row.id===item.id),state.normal.context);
    detailLoadedAt=state.normal.item?state.normal.loadedAt:Date.now();collectionPageOffset=state.normal.offset;renderCollectionPageWindow(item);
    normalDetailView.restorePage(state.normal.page);
  }else if(state.baseView==="detail" && state.normalId){
    const index=items.findIndex(row=>String(row.id)===String(state.normalId));
    if(index<0)throw new Error("原作品不在可恢复列表中，请重新打开");
    await select(index,null,null,{restoring:true});current();
  }
  if(state.batch){document.body.classList.add("batch-mode");renderBatchDownloadOptions(state.quality,state.format);updateBatchDetailSummary();}
  if(state.baseView==="detail" || state.baseView==="batch" || state.workspaceReturn==="download" || state.basket?.originWorkspace==="download")openDownloadPage();
  if(state.basket){
    if(selection.size){
      openSelectionBasket({restoring:true});
      basketArtworkOffset=state.basket.artworkOffset;openBasketArtworkPicker();
      if(state.basket.mode==="detail" && state.basket.id && selection.has(state.basket.id)){
        await openBatchCollection(state.basket.id,{restoring:true});current();collectionPageOffset=state.basket.offset;
        basketDetailView.restorePage(state.basket.stagePage || 0);
        if(basketDetailItem)renderCollectionWindowInto(basketDetailItem,$("#basketPages"),$("#basketPageMore"));
      }
    }else{
      basketReturnMode="picker";$("#batchCollections").innerHTML='<p class="empty-state">采集篮为空</p>';
      $("#batchCollections").hidden=false;$("#basketArtworkDetail").hidden=true;$("#basketTitle").textContent="采集篮";openBasketPage();
    }
  }
  if(state.baseView==="history")openLibrary(state.baseView,{restoring:true});
  if(state.baseView==="account"){await accountView.open({...state.account,scroll:state.scroll.account});current();}
  if(state.baseView==="discovery"){setWorkspace("discovery");await discoveryView.open({restore:state.discovery});current();}
  if(state.baseView==="reverse")sourceSearchView.restore(state.reverse);
  workspaceReturn=state.workspaceReturn;
  contextReturns={...state.contextReturns};
  if(state.view==="viewer"){
    openAllViewer({restoring:true});viewerPageOffset=state.viewerOffset;
    if(currentDetailItem)renderViewerWindow(currentDetailItem);
  }
  for(const [id,value] of [["quality",state.quality],["format",state.format]])if([...$(`#${id}`).options].some(option=>option.value===value))$(`#${id}`).value=value;
  if(state.download){
    $("#saveRoot").value=state.download.saveRoot;
    for(const id of ["createFolder","groupArtworks","skipExisting"])$("#"+id).checked=state.download[id];
    downloadDefaultsTouched=true;
  }
  updateFormatHint();syncSearchScopedControls();syncPreviewResources();
  if(state.baseView==="gallery" && galleryIntent && browseOptions.automatic()){await startGalleryLoad({restoring:true});current();}
  await restorePageScroll(state,revision);
}

async function restorePageScroll(state,revision=navigationRevision) {
  await new Promise(resolve=>requestAnimationFrame(resolve));
  if(revision!==navigationRevision)throw Object.assign(new Error("页面读取已取消"),{name:"AbortError"});
  assertNavigationAccount(state);
  const surface=activeScrollSurface();
  if(surface===window)window.scrollTo({left:state.scroll.window.left,top:state.scroll.window.top,behavior:"instant"});
  for(const [id,value] of [["downloadPage",state.scroll.download],["basketPage",state.scroll.basket],["historyPage",state.scroll.history],["accountPage",state.scroll.account || 0],["discoveryPage",state.scroll.discovery || 0],["reversePage",state.scroll.reverse || 0],["allViewer",state.scroll.viewer]])if(surface===$(`#${id}`))surface.scrollTop=value;
  updateFloatingChrome();updatePaginationDock();
}

async function refreshCurrentPage() {
  if(contextNavigationLocked() || pageRefreshPending || detailController || selection.locked || singleDownloadPending)return;
  checkpointNavigation();
  const state=capturePageState(),surface=activeScrollSurface();
  if(surface===$("#discoveryPage")){await discoveryView.refresh();return;}
  if(surface===window && galleryIntent){await startGalleryLoad();return;}
  if(surface===window && searchSurface==="gallery" && ["uid","tags"].includes(activeSearchContext.kind) && activeSearchFilters.order==="popular"){galleryPopularViews.delete(gallerySortKey());await loadGalleryPopularity(1,{refresh:true});return;}
  if(surface===$("#reversePage")){await sourceSearchView.refresh();return;}
  const revision=navigationRevision,controller=new AbortController();pageRefreshController=controller;
  const current=()=>{
    if(controller.signal.aborted || revision!==navigationRevision)throw Object.assign(new Error("页面读取已取消"),{name:"AbortError"});
    assertNavigationAccount(state);
  };
  pageRefreshPending=true;syncSearchScopedControls();
  try{
    if(surface===$("#accountPage"))await accountView.refresh();
    else if(surface===$("#historyPage"))await historyView.refresh();
    else if(surface===window && (resultSelectionEnabled || (state.artist && state.artist.kind!=="none"))){
      const refreshed=await search(activeTagQuery,currentPage,activeSearchFilters,null,{manualRefresh:true,preferenceKind:galleryPreferenceKind});
      current();
      if(!refreshed){await restorePageState(state);throw new Error("未能读取新内容，原页面已保留");}
    }else if(surface===$("#basketPage") || !currentDetailItem || document.body.classList.contains("batch-mode")){
      const failed=new Set([...$("#batchCollections").querySelectorAll("img.image-unavailable[data-basket-artwork]")].map(image=>image.dataset.basketArtwork));
      await workspaceSync.settled();current();await workspaceSync.restore();current();
      if(!workspaceSync.ready)throw new Error("本机记录未能同步，当前选择已保留");
      if(surface===$("#basketPage")){
        const rows=selection.snapshot();
        batchCandidateItems=rows.map(row=>failed.has(row.id)?{...row.item,thumb:""}:row.item);
        batchCandidateContextByArtwork.clear();batchCandidateResultPageByArtwork.clear();
        for(const row of rows){
          batchCandidateContextByArtwork.set(row.id,row.context);batchCandidateResultPageByArtwork.set(row.id,row.resultPage);
          if(failed.has(row.id)){staleBasketPreviewIds.add(row.id);selection.remember({...row.item,thumb:""});}
        }
        if(state.basket?.mode==="detail" && selection.has(state.basket.id)){
          const fresh=await fetchJson(`/api/pixiv/artwork/${state.basket.id}?refresh=1`,{signal:controller.signal},ARTWORK_DETAIL_TIMEOUT_MS);
          current();rememberArtworkDetail(fresh);
          currentDetailItem=fresh;activeArtworkId=fresh.id;collectionPageOffset=state.basket.offset;
          renderBasketArtworkDetail(fresh);
          basketDetailView.restorePage(state.basket.stagePage || 0);
        }else{basketArtworkOffset=state.basket?.artworkOffset || 0;openBasketArtworkPicker();}
      }else if(document.body.classList.contains("batch-mode"))updateBatchDetailSummary();
      else if(surface===window){await syncAuthStatus();renderRecentSearches();}
    }else{
      const item=currentDetailItem;
      const fresh=await fetchJson(`/api/pixiv/artwork/${item.id}?refresh=1`,{signal:controller.signal},ARTWORK_DETAIL_TIMEOUT_MS);
      current();rememberArtworkDetail(fresh);currentDetailItem=fresh;
      refreshDetailControls(fresh,basketPageOpen());
      if(!basketPageOpen())normalDetailView.restorePage(state.normal?.page || 0);
    }
    current();await restorePageScroll(state,revision);
  }catch(error){if(!controller.signal.aborted && revision===navigationRevision)showTaskDock("刷新失败",error.message || "请稍后重试",5000);}
  finally{if(pageRefreshController===controller){pageRefreshController=null;pageRefreshPending=false;syncSearchScopedControls();queueNavigationRecord();}}
}

createPageContextMenu({refresh:refreshCurrentPage,allowed:()=>!contextNavigationLocked() && !pageHistory?.status().restoring && !detailController});

function setWorkspace(view) {
  if(view!=="reverse")sourceSearchView.suspend();
  if(view!=="discovery")discoveryView?.suspend();
  galleryInteraction.reset({immediate:true});
  if(view!=="account")accountView.suspend();
  if (view !== "download") normalDetailView.suspend();
  if (activeWorkspace === "history" && view !== "history") historyView.close();
  activeWorkspace = view;
  if(view)artistPage.suspend();
  else if(!searchPending && !pageHistory?.status().restoring)artistPage.resume();
  for (const name of ["download", "history", "account", "discovery", "reverse"]) $(`#${name}Page`).hidden = name !== view;
  ugoiraPreview.stop();
  if (view) cancelSearchPrefetch();
  else scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
  updatePaginationDock();
  updateFloatingChrome();
  syncPreviewResources();
}

function openDownloadPage() {
  workspaceReturn = null;
  setWorkspace("download");
}

function navigatePrimary(hash) {
  preparePageNavigation();
  checkpointNavigation();
  contextReturns={};
  accountOrigin=false;
  if (hash === "#home" || hash === "#gallery") searchSurface = hash.slice(1);
  const fromBasket = basketPageOpen();
  closeAllViewer();
  closeBasketPage();
  if (hash === "#detail") {
    if (fromBasket || (selection.size && !currentDetailItem && !document.body.classList.contains("batch-mode"))) showBatchDetail({fromSidebar:true});
    else openDownloadPage();
  } else {
    workspaceReturn = null;
    setWorkspace(null);
    if (hash === "#home") window.scrollTo({top: 0, behavior: "instant"});
    else $(hash).scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    if(hash==="#gallery" && galleryIntent && browseOptions.automatic())void startGalleryLoad();
  }
}

function openLibrary(view,{restoring=false}={}) {
  if(view==="favorites"){void openAccount("bookmarks");return;}
  if(!restoring)preparePageNavigation();
  checkpointNavigation();
  accountOrigin=false;
  contextReturns={};
  if (!libraryWorkspaceOpen()) workspaceReturn = activeWorkspace;
  closeAllViewer();
  setWorkspace(view);
  if (view === "history") historyView.open();
}

function closeLibrary() {
  if(pageRefreshPending)return;
  checkpointNavigation();
  const target = workspaceReturn;
  workspaceReturn = null;
  setWorkspace(target);
}

$("#navHistory").onclick = () => openLibrary("history");
$("#navAccount").onclick=()=>openAccount();
$("#navDiscovery").onclick=()=>openDiscovery();
$("#navBasket").onclick = () => {
  if (!selection.size) {
    showTaskDock("采集篮为空", "先在预览页勾选作品，再来选择图片。", 3000);
    return;
  }
  preparePageNavigation();
  if (libraryWorkspaceOpen()) closeLibrary();
  closeAllViewer();
  openSelectionBasket({fromSidebar:true});
};
$("#historyBack").onclick = closeLibrary;
$("#downloadBack").onclick = () => navigatePrimary("#gallery");

async function select(index,suppliedItem=null,suppliedContext=null,{restoring=false}={}) {
  let item = suppliedItem || items[index];
  if (!item) return;
  if(!restoring)preparePageNavigation();
  checkpointNavigation();
  contextReturns.detail={key:capturePageState().key};
  clearDetail("正在加载作品详情…");
  closeAllViewer();
  invalidateDetailView();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  const detailContext = { ...(suppliedContext || activeSearchContext) };
  activeArtworkId = item.id;

  announceToast("");
  $("#dTitle").textContent = "正在加载作品详情…";
  $("#deck").innerHTML = '<p class="loading-state">正在读取作品信息</p>';
  $("#download").disabled = true;

  try {
    if (item.source === "pixiv" && (!item.pageImages || staleBasketPreviewIds.has(String(item.id)))) {
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, ARTWORK_DETAIL_TIMEOUT_MS);
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
    if (controller === detailController) {
      detailController = null;
      scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
    }
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
  return [...(selection.get(item.id)?.pages || [])].filter(page => page >= 0 && page < item.pages).sort((a, b) => a - b);
}

const detailHdCache=createDetailImageCache({fetchBytes:(url,options)=>fetchBytes(url,{...options,cache:"no-store"},60000)});
let previewSizeWrite=Promise.resolve();
const imageViewer=createImageViewer({dialog:$("#imageViewer"),cache:detailHdCache,showDialog:showAppDialog,report:announceToast,
  loadItem:(id,options)=>fetchJson(`/api/pixiv/artwork/${id}`,options,ARTWORK_DETAIL_TIMEOUT_MS),
  sizeChanged:value=>{previewSizeWrite=previewSizeWrite.then(()=>cloudControls.savePreferences({previewSize:value})).catch(error=>announceToast(error.message));}});
globalThis.openListPreview=(item,image)=>{
  if(!item || !image?.naturalWidth){announceToast("预览图尚未加载完成，请稍后再试");return;}
  imageViewer.open({item,preview:image.currentSrc || image.src});
};
function detailView(prefix, deck, hint) {
  const names = ["Title", "WorkType", "Desc", "Artist", "Size", "Bookmarks", "Date", "Tags"];
  return createArtworkDetailView({
    deck: $(deck), hint: $(hint),
    noticeBefore:prefix==="d"?$("#toast"):null,
    isDownloaded:item=>prefix==="d" && detailDownloadProgress?.status==="success" && String(detailDownloadProgress.artworkId)===String(item.id),
    fields: Object.fromEntries(names.map(name => [name, $(`#${prefix}${name}`)])),
    escape: esc, installImages: installImageFallbacks,
    openOfficial:(id,event)=>{
      const api=window.pywebview?.api;
      if(!api?.pixiv_open_artwork)return;
      event.preventDefault();
      void api.pixiv_open_artwork(id).then(result=>{if(!result.ok)announceToast(result.error || "官网链接打开失败");})
        .catch(()=>announceToast("官网链接打开失败，可右键复制链接"));
    },
    stage: createImageStage({
      root: $(deck), hint: $(hint), viewer:imageViewer,
      installImages: installImageFallbacks, report: announceToast,
      onViewChange: queueNavigationRecord,
    }),
  });
}
const normalDetailView = detailView("d", "#deck", "#deckHint");
const basketDetailView = detailView("basketDetail", "#basketDetailDeck", "#basketDetailDeckHint");

function renderSingleDownloadOptions(item, preserve = false) {
  if(!preserve)applyDownloadDefaults();
  const quality = preserve ? $("#quality").value : null;
  const format = preserve ? $("#format").value : null;
  const qualityOptions = item.qualities.map((option) => `<option value="${option.id}">${esc(downloadQualityLabel(option.id, item) || option.label)}${option.width > 0 && option.height > 0 ? ` · ${option.width} × ${option.height}` : ""}</option>`).join("");
  const formatOptions = downloadFormatOptions(item.formats);
  if ($("#quality").innerHTML !== qualityOptions) $("#quality").innerHTML = qualityOptions;
  if ($("#format").innerHTML !== formatOptions) $("#format").innerHTML = formatOptions;
  const defaultQuality = item.qualities.find(option => option.id === "original")?.id || item.qualities[0]?.id || "original";
  $("#quality").value = item.qualities.some(option => option.id === quality) ? quality : defaultQuality;
  const formats = item.formats.filter(option => option.id !== "mp4" || animationFormats.mp4 !== false);
  $("#format").value = formats.some(option => option.id === format) ? format : formats[0]?.id || "source";
}

function renderDetail(item, index, detailContext = activeSearchContext) {
  detailLoadedAt = Date.now();
  normalDetailView.reset();
  selection.remember(item);
  currentDetailItem = item;
  currentDetailContext = { ...detailContext };
  collectionPageOffset = 0;
  const visible = normalDetailView.render(item);
  renderCollectionPageWindow(item);
  $("#viewAll").hidden = item.pages <= visible;
  renderSingleDownloadOptions(item);
  $("#download").textContent = downloadButtonLabel(item);
  syncSearchScopedControls();
  updateFormatHint();
  syncPreviewResources();
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
      const result = selection.setPage(String(item.id), page, box.checked, detailSelectionOrigin(item));
      if (!result.accepted) {
        box.checked = selection.isPageSelected(item.id, page);
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
  const pages = (item.pageImages || []).slice(0, item.pages);
  const chosenPages = selection.get(item.id)?.pages;
  const lastStart = Math.max(0, Math.floor(Math.max(0, pages.length - 1) / DETAIL_PAGE_WINDOW) * DETAIL_PAGE_WINDOW);
  const start = Math.min(Math.max(0, collectionPageOffset), lastStart);
  collectionPageOffset = start;
  const end = Math.min(pages.length, start + DETAIL_PAGE_WINDOW);
  const ugoiraHover = item.workType === "ugoira" ? ` data-ugoira-preview="${esc(String(item.id))}"` : "";
  pagesRoot.innerHTML = pages.slice(start, end).map((page, localIndex) => {
    const pageNo = start + localIndex;
    return `<label class="page-select"${ugoiraHover}><input type="checkbox" data-collection-page="${pageNo}" ${chosenPages?.has(pageNo) ? "checked" : ""} ${selection.locked ? "disabled" : ""}><img src="${esc(page.regular || (pageNo===0?item.thumb:"") || "")}" data-detail-artwork="${esc(item.id)}" data-detail-page="${pageNo}" alt="${esc(item.title)} 第 ${pageNo + 1} 张" loading="lazy" decoding="async"><span>${pageNo + 1}</span></label>`;
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
  $("#viewerTitle").textContent = item?.title || "";
  $("#viewerArtist").textContent = item?.artist || "";
  const pages = Array.isArray(item?.pageImages) ? item.pageImages.slice(0, item.pages) : [];
  const lastStart = Math.max(0, Math.floor(Math.max(0, pages.length - 1) / VIEWER_PAGE_WINDOW) * VIEWER_PAGE_WINDOW);
  const start = Math.min(Math.max(0, viewerPageOffset), lastStart);
  const end = Math.min(pages.length, start + VIEWER_PAGE_WINDOW);
  viewerPageOffset = start;
  const navigation = pages.length > VIEWER_PAGE_WINDOW
    ? `<div class="pagination viewer-window-pagination" style="grid-column:1/-1" aria-label="连续查看分页"><button type="button" data-viewer-window="${Math.max(0, start - VIEWER_PAGE_WINDOW)}" ${start === 0 ? "disabled" : ""}>← 前 ${VIEWER_PAGE_WINDOW} 张</button><span>${start + 1}–${end} / ${pages.length}</span><button type="button" data-viewer-window="${Math.min(lastStart, start + VIEWER_PAGE_WINDOW)}" ${end >= pages.length ? "disabled" : ""}>后 ${VIEWER_PAGE_WINDOW} 张 →</button></div>`
    : "";
  $("#viewerGrid").innerHTML = `${navigation}${pages.slice(start, end).map((page, localIndex) => {
    const pageNo = start + localIndex;
    // Page dimensions come from normalized detail metadata; reserve space before lazy loading.
    const size = page.width > 0 && page.height > 0 ? page : item;
    const width = size.width > 0 && size.height > 0 ? size.width : 1;
    const height = size.width > 0 && size.height > 0 ? size.height : 1;
    return `<figure><img src="${esc(page.regular || (pageNo===0?item.thumb:"") || "")}" width="${esc(width)}" height="${esc(height)}" data-detail-artwork="${esc(item.id)}" data-detail-page="${pageNo}" alt="${esc(item.title)} 第 ${pageNo + 1} 张" loading="lazy" decoding="async"><span>${String(pageNo + 1).padStart(2, "0")} / ${String(pages.length).padStart(2, "0")}</span></figure>`;
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

function openAllViewer({restoring=false}={}) {
  if(!restoring)preparePageNavigation();
  checkpointNavigation();
  contextReturns.viewer={key:capturePageState().key};
  const activeDetail = currentDetailItem
    && activeArtworkId !== null
    && String(currentDetailItem.id) === String(activeArtworkId)
    ? currentDetailItem
    : null;
  const item = activeDetail || selection.get(activeArtworkId)?.item || items.find((row) => row.id === activeArtworkId);
  if (!item?.pageImages) return;
  viewerPageOffset = 0;
  renderViewerWindow(item);
  $("#allViewer").hidden = false;
  ugoiraPreview.stop();
  document.body.classList.add("viewer-open");
  $("#allViewer").scrollTop = 0;
  syncPreviewResources();
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
  syncPreviewResources();
  schedulePaginationDockUpdate();
}

$("#viewAll").onclick = openAllViewer;
$("#closeViewer").onclick = () => { if(pageRefreshPending)return;checkpointNavigation(); closeAllViewer(); };
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
  if (contextNavigationLocked()) return;
  if (!selection.size) {
    $("#pageSelectionStatus").textContent = "请先勾选至少一个作品";
    return;
  }
  showBatchDetail();
}
$("#basketEntry").onclick = openBatchHub;
$("#downloadReturnBasket").onclick = () => openSelectionBasket();

function basketPageOpen() {
  return !$("#basketPage").hidden;
}

function openBasketPage() {
  cancelSearchPrefetch();
  if (!basketPageOpen()) {
    // Basket detail uses the active preview identity; keep the normal download
    // view's identity alongside its untouched DOM until the basket is closed.
    basketDownloadSnapshot = {
      ...normalDetailView.snapshot(), context: currentDetailContext,
      artworkId: activeArtworkId, pageOffset: collectionPageOffset,
      workspace:activeWorkspace,
    };
  }
  $("#basketPage").hidden = false;
  document.body.classList.add("basket-page-open");
  updatePaginationDock();
  updateFloatingChrome();
  syncSearchScopedControls();
  syncPreviewResources();
}

function updateBatchDetailSummary() {
  $("#batchDetailSummary").textContent = `${selection.size} 个作品 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张已选`;
  $("#batchArtworkCount").textContent=String(selection.size);
  $("#batchPageCount").textContent=String(selectedPageCount());
  renderDetailDownloadProgress();
}

function showBatchDetail({fromSidebar=false}={}) {
  preparePageNavigation();
  checkpointNavigation();
  contextReturns.detail=fromSidebar?null:{key:capturePageState().key};
  const quality = $("#quality").value;
  const format = $("#format").value;
  clearDetail();
  closeAllViewer();
  invalidateDetailView();
  document.body.classList.add("batch-mode");
  applyDownloadDefaults();
  renderBatchDownloadOptions(quality, format);
  updateBatchDetailSummary();
  syncSearchScopedControls();
  openDownloadPage();
}

function closeBasketPage() {
  const wasOpen = !$("#basketPage").hidden;
  $("#basketPage").hidden = true;
  document.body.classList.remove("basket-page-open");
  basketReturnMode = "summary";
  basketDetailItem = null;
  basketDetailView.reset();
  ugoiraPreview.stop();
  stopBasketThumbnails();
  basketCardSizeObserver?.disconnect();
  basketPreviewCache.park($("#batchCollections"));
  // Restricted previews must not linger in the hidden basket DOM.
  $("#batchCollections").innerHTML = "";
  $("#basketArtworkDetail").hidden = true;
  $("#basketDetailDeck").innerHTML = "";
  $("#basketPages").innerHTML = "";
  $("#basketPageMore").hidden = true;
  $("#basketViewAll").hidden = true;
  if (!wasOpen) return;
  if (basketDownloadSnapshot) {
    currentDetailItem = basketDownloadSnapshot.item;
    currentDetailContext = basketDownloadSnapshot.context;
    activeArtworkId = basketDownloadSnapshot.artworkId;
    collectionPageOffset = basketDownloadSnapshot.pageOffset;
    if (currentDetailItem) {
      selection.remember(currentDetailItem);
      if (basketDownloadSnapshot.detailChanged) refreshDetailControls(currentDetailItem, false);
    }
    basketDownloadSnapshot = null;
  }
  $("#collectionPages").querySelectorAll("[data-collection-page]").forEach(box => {
    box.checked = selection.isPageSelected(currentDetailItem?.id, Number(box.dataset.collectionPage));
  });
  invalidateDetailView();
  syncSearchScopedControls();
  updateFloatingChrome();
  syncPreviewResources();
  scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
}

function showBasketPane(mode) {
  ugoiraPreview.stop();
  basketReturnMode = mode;
  $("#basketDownload").hidden=mode==="detail";
  $("#batchCollections").hidden = mode !== "picker";
  $("#basketArtworkDetail").hidden = mode !== "detail";
  syncPreviewResources();
}

function ensureDownloadOptionDefaults() {
  if (!$("#quality").options.length) {
    renderBatchQualityOptions();
  }
  if (!$("#format").options.length) {
    $("#format").innerHTML = '<option value="source">保留源格式</option>';
  }
}

function downloadQualityLabel(id, item = null) {
  if (id === "original") return "原图（原始分辨率）";
  if (id === "regular") return item?.workType === "ugoira" ? "标准（600px 帧包）" : "标准（Pixiv 缩小预览）";
  return "";
}

function renderBatchQualityOptions(quality = "original") {
  $("#quality").innerHTML = '<option value="original">原图（原始分辨率）</option><option value="regular">标准（缩小预览；动图 600px）</option>';
  $("#quality").value = quality === "regular" ? "regular" : "original";
}

function downloadFormatOptions(formats) {
  return formats.map(format => {
    const unavailable = format.id === "mp4" && animationFormats.mp4 === false;
    const label = unavailable ? "MP4 视频（未检测到 FFmpeg）" : format.label;
    return `<option value="${format.id}" ${unavailable ? "disabled" : ""}>${esc(label)}</option>`;
  }).join("");
}

function renderBatchDownloadOptions(quality = $("#quality").value, format = $("#format").value) {
  renderBatchQualityOptions(quality);
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
    quality: $("#quality").value || "original",
    ugoiraFormat: ["gif", "mp4"].includes($("#format").value) ? $("#format").value : "source",
    saveRoot: $("#saveRoot").value.trim(),
    createFolder: $("#createFolder").checked,
    groupArtworks: Boolean($("#groupArtworks")?.checked),
    skipExisting: $("#skipExisting")?.checked ?? true,
  };
}

function syncBasketHeader() {
  const chosen = batchCandidateItems;
  if (!chosen.length) {
    $("#batchSummary").textContent = `${selectedPageCount()}/${MAX_SELECTED_PAGES} 张图片已选`;
    return;
  }
  const selectedCount = chosen.filter((item) => selection.has(item.id)).length;
  $("#batchSummary").textContent = `${selectedCount}/${chosen.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
}

function openSelectionBasket({fromSidebar=false,restoring=false}={}) {
  if(!restoring)preparePageNavigation();
  const rows = selection.snapshot();
  if (!rows.length) {
    $("#pageSelectionStatus").textContent = "请先勾选至少一个作品";
    return;
  }
  checkpointNavigation();
  contextReturns.basket=fromSidebar?null:{key:capturePageState().key};
  invalidateDetailView();
  batchCandidateItems = rows.map(row => row.item);
  batchCandidateContextByArtwork.clear();
  batchCandidateResultPageByArtwork.clear();
  for (const row of rows) {
    batchCandidateContextByArtwork.set(row.id, row.context);
    batchCandidateResultPageByArtwork.set(row.id, row.resultPage);
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
    box.checked = selection.has(item.id);
    return false;
  }
  const selectedPages = selection.pageCountFor(item.id);
  const selected = selectedPages > 0;
  box.checked = selected;
  const card = box.closest(".batch-collection");
  card?.classList.toggle("is-selected", selected);
  const label = card?.querySelector(".batch-card-select");
  label?.setAttribute("aria-label", `${selected ? "取消选择" : "选择"} ${item.title}`);
  const selectionLabel = card?.querySelector(".batch-card-copy small");
  if (selectionLabel) {
    selectionLabel.textContent = `${item.artist} · 已选 ${selectedPages}/${item.pages} 张`;
    selectionLabel.title = selectionLabel.textContent;
  }

  const selectedCount = batchCandidateItems.filter((candidate) => selection.has(candidate.id)).length;
  $("#batchSummary").textContent = `${selectedCount}/${batchCandidateItems.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
  syncResultSelectionControls();
  return true;
}

function openBasketArtworkPicker() {
  const chosen = batchCandidateItems;
  basketPreviewCache.park($("#batchCollections"));
  basketCardSizeObserver?.disconnect();
  if (!chosen.length) {
    showBasketPane("picker");
    $("#basketTitle").textContent = "采集篮";
    $("#basketModeHint").textContent = "在预览页勾选作品后，可来这里挑选图片。";
    syncBasketHeader();
    $("#batchCollections").innerHTML = '<p class="empty-state basket-empty">采集篮为空</p>';
    syncSearchScopedControls();
    return;
  }
  const lastStart = Math.max(0, Math.floor(Math.max(0, chosen.length - 1) / BASKET_ARTWORK_WINDOW) * BASKET_ARTWORK_WINDOW);
  const start = Math.min(Math.max(0, basketArtworkOffset), lastStart);
  const end = Math.min(chosen.length, start + BASKET_ARTWORK_WINDOW);
  basketArtworkOffset = start;
  ensureDownloadOptionDefaults();
  showBasketPane("picker");
  const selectedCount = chosen.filter((item) => selection.has(item.id)).length;
  $("#basketTitle").textContent = "采集篮 · 选择要下载的作品";
  $("#basketModeHint").textContent = "点击作品挑选图片；选好后点下载，进入下载设置。";
  $("#batchSummary").textContent = `${selectedCount}/${chosen.length} 个作品已勾选 · ${selectedPageCount()}/${MAX_SELECTED_PAGES} 张`;
  const navigation = chosen.length > BASKET_ARTWORK_WINDOW
    ? `<div class="pagination basket-window-pagination" style="grid-column:1/-1" aria-label="采集篮作品分页"><button type="button" data-basket-window="${Math.max(0, start - BASKET_ARTWORK_WINDOW)}" ${start === 0 ? "disabled" : ""}>← 前 ${BASKET_ARTWORK_WINDOW} 件</button><span>${start + 1}–${end} / ${chosen.length}</span><button type="button" data-basket-window="${Math.min(lastStart, start + BASKET_ARTWORK_WINDOW)}" ${end >= chosen.length ? "disabled" : ""}>后 ${BASKET_ARTWORK_WINDOW} 件 →</button></div>`
    : "";
  $("#batchCollections").innerHTML = `${navigation}${chosen.slice(start, end).map((item) => {
    const selectedPages = selection.pageCountFor(item.id);
    const selected = selectedPages > 0;
    const selectedPagesLabel = `${selectedPages}/${item.pages} 张`;
    const ugoiraHover = item.workType === "ugoira" ? ` data-ugoira-preview="${esc(String(item.id))}"` : "";
    return `<article class="batch-collection ${selected ? "is-selected" : ""}" data-batch-artwork="${esc(item.id)}"><label class="batch-card-select" aria-label="${selected ? "取消选择" : "选择"} ${esc(item.title)}"><input type="checkbox" data-batch-select="${esc(item.id)}" ${selected ? "checked" : ""}><span aria-hidden="true">✓</span></label><button class="batch-card-open" type="button" data-open-collection="${esc(item.id)}" aria-label="打开 ${esc(item.title)} 的作品详情并选择图片"><span class="batch-card-cover"${ugoiraHover}><img data-gallery-src="${esc(item.thumb || "")}" data-preview-artwork="${esc(item.id)}" ${item.thumb ? "" : `data-restore-artwork="${esc(item.id)}"`} data-basket-artwork="${esc(item.id)}" alt="${esc(item.title)}" loading="lazy" decoding="async"><span class="batch-page-count" aria-hidden="true">${item.pages}P</span></span><span class="batch-card-copy"><b title="${esc(item.title)}">${esc(item.title)}</b><small title="${esc(item.artist)} · 已选 ${selectedPagesLabel}">${esc(item.artist)} · 已选 ${selectedPagesLabel}</small></span></button></article>`;
  }).join("")}`;
  basketPreviewCache.mount($("#batchCollections"),"basket");
  // Derive height from width: measuring the stretched grid row's height would
  // feed the placeholder back into itself after a resize.
  basketCardSizeObserver ??= new ResizeObserver(([entry]) => {
    if (entry.contentRect.width <= 0) return;
    const holder = $("#batchCollections");
    const height = `calc(${entry.contentRect.width * 1.25}px + var(--basket-card-copy-height))`;
    if (holder.style.getPropertyValue("--basket-card-height") !== height) {
      holder.style.setProperty("--basket-card-height", height);
    }
  });
  basketCardSizeObserver.observe($("#batchCollections .batch-collection"));
  installImageFallbacks($("#batchCollections"));
  loadBasketThumbnails();
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

function stopBasketThumbnails() {
  basketThumbnailWindow++;
  basketThumbnailObserver?.disconnect();
  basketThumbnailObserver = null;
  basketThumbnailQueue = [];
  basketThumbnailControllers.forEach(controller => controller.abort());
}

function pumpBasketThumbnails() {
  if (document.hidden || activeScrollSurface() !== $("#basketPage") || basketReturnMode !== "picker") return;
  const generation = basketThumbnailWindow;
  while (basketThumbnailControllers.size < 3 && basketThumbnailQueue.length) {
      const image = basketThumbnailQueue.shift();
      const id = image.dataset.restoreArtwork;
      const controller = new AbortController();
      basketThumbnailControllers.add(controller);
      fetchJson(`/api/pixiv/artwork/${id}`, {signal: controller.signal}, ARTWORK_DETAIL_TIMEOUT_MS).then(fresh => {
        if (controller.signal.aborted || generation !== basketThumbnailWindow || activeScrollSurface() !== $("#basketPage") || basketReturnMode !== "picker" || !batchCandidateItems.some(item => String(item.id) === id) || String(fresh.id) !== id) return;
        rememberArtworkDetail(fresh);
        const preview=basketPreviewCache.assign("basket",image,fresh.thumb || "");
        preview.classList.remove("image-unavailable");
        preview.closest(".batch-collection")?.classList.remove("image-unavailable");
        delete preview.dataset.restoreArtwork;
        const card = preview.closest(".batch-collection");
        if (card) {
          const pages = selection.pageCountFor(id);
          card.classList.toggle("is-selected", pages > 0);
          card.querySelector("[data-batch-select]").checked = pages > 0;
          card.querySelector(".batch-card-select").setAttribute("aria-label", `${pages ? "取消选择" : "选择"} ${fresh.title}`);
          card.querySelector(".batch-card-open").setAttribute("aria-label", `打开 ${fresh.title} 的作品详情并选择图片`);
          card.querySelector(".batch-card-copy b").textContent = fresh.title;
          card.querySelector(".batch-card-copy small").textContent = `${fresh.artist} · 已选 ${pages}/${fresh.pages} 张`;
          card.querySelector(".batch-card-copy small").title = card.querySelector(".batch-card-copy small").textContent;
          card.querySelector(".batch-card-copy b").title = fresh.title;
          card.querySelector(".batch-page-count").textContent = `${fresh.pages}P`;
          preview.alt = fresh.title;
        }
        updateSelectionBar();
        syncResultSelectionControls();
        syncBasketHeader();
      }).catch(() => {
        if (generation === basketThumbnailWindow) image.alt = "预览暂未加载，点击作品重试";
      }).finally(() => {
        basketThumbnailControllers.delete(controller);
        pumpBasketThumbnails();
      });
  }
}

function loadBasketThumbnails() {
  stopBasketThumbnails();
  if (document.hidden || activeScrollSurface() !== $("#basketPage") || basketReturnMode !== "picker") return;
  if (typeof IntersectionObserver !== "function") return;
  const generation = basketThumbnailWindow;
  basketThumbnailObserver = new IntersectionObserver(entries => {
    if (generation !== basketThumbnailWindow) return;
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      basketThumbnailObserver.unobserve(entry.target);
      basketThumbnailQueue.push(entry.target);
    }
    pumpBasketThumbnails();
  }, {root: $("#basketPage"), rootMargin: "100px"});
  $("#batchCollections").querySelectorAll("[data-restore-artwork]").forEach(image => basketThumbnailObserver.observe(image));
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
  return [...buckets.values()].flatMap(bucket => {
    const chunks = [];
    for (const group of bucket.groups) {
      if ((selection.get(group.id)?.item.workType || "ugoira") === "ugoira") {
        chunks.push(...planDownloadChunks([group]));
      } else for(const page of group.pages)chunks.push({groups:[{id:group.id,pages:[page]}],pageCount:1});
    }
    return chunks.map(chunk => ({...chunk, context: bucket.context}));
  });
}

function groupsForProgress(chunks) {
  const groups=new Map();
  for(const chunk of chunks)for(const group of chunk.groups){
    if(!groups.has(group.id))groups.set(group.id,new Set());
    for(const page of group.pages)groups.get(group.id).add(page);
  }
  return [...groups].sort(([a],[b])=>String(a).localeCompare(String(b))).map(([id,pages])=>({id,pages:[...pages].sort((a,b)=>a-b)}));
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
    id: nextDownloadRequestId(),
    kind: taskOptions.endpoint ? "single" : "batch",
    chunkPolicy: "animation-artwork-v1",
    taskOptions: JSON.parse(JSON.stringify(taskOptions)),
    signature,
    selectionKey:taskOptions.endpoint?null:JSON.stringify(groupsForProgress(chunks)),
    authorizationRevision: downloadAuthorizationRevision,
    remainingChunks: chunks.map((chunk) => ({ ...chunk, context: chunk.context || {kind: "tags", value: "原创"}, requestId: nextDownloadRequestId() })),
    savedCount: 0,
    fileCount: 0,
    firstSaved: "",
    cleanupPending: false,
    historyWarning: false,
    catalogWarning: false,
    skippedCount: 0,
    completedBatches: 0,
    totalBatches: chunks.length,
  };
}

function nextDownloadRequestId() {
  return globalThis.crypto?.randomUUID?.() || `download-${nextSearchRequestId()}`;
}

async function waitForDownloadCooldown(task, seconds) {
  const running = activeDownload;
  if (!running || running.task !== task) throw new Error("下载状态已更新，请重新读取任务");
  running.cooldownUntil = performance.now() + seconds * 1000;
  try {
    while (performance.now() < running.cooldownUntil) {
      if (running.pauseRequested) throw Object.assign(new Error("下载已暂停"), {paused: true});
      if (task.authorizationRevision !== downloadAuthorizationRevision) throw new Error("Pixiv 账户状态已变更，请重新确认下载");
      if (requestBlockedKind) throw new Error("Pixiv 要求完成登录或页面验证，请处理后继续任务");
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  } finally { running.cooldownUntil = 0; }
}

async function fetchDownloadResult(request, chunk, task) {
  const body = JSON.stringify({ ...request.body, requestId: chunk.requestId,
    ...(task.persisted ? {queueId: task.id, scope: workspaceSync.scope} : {}) });
  let timeoutRetries = 0;
  let cooldownRetries = 0;
  let observeOnly = false;
  while (true) {
    if (task.authorizationRevision !== downloadAuthorizationRevision) {
      throw new Error("Pixiv 账户状态已变更，下载已中断，请重新发起");
    }
    let data;
    try {
      data = await fetchJson(request.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(observeOnly ? {"X-MOKU-Download-Probe": "1"} : {}) },
        body,
      }, request.timeout);
    } catch (error) {
      // A slow local save may outlive the response. Reconnect once using the
      // same identity; the backend returns pending or the completed result.
      if (error.errorKind === "upstream_rate_limit" && cooldownRetries++ < 2) {
        await waitForDownloadCooldown(task, error.retryAfterSeconds);
        if (activeDownload?.pauseRequested) throw Object.assign(new Error("下载已暂停"), {paused: true});
        continue;
      }
      if (error.timedOut && timeoutRetries++ === 0) {
        if (activeDownload?.pauseRequested) observeOnly = true;
        continue;
      }
      if (error.requestIdConflict) chunk.requestId = nextDownloadRequestId();
      throw error;
    }
    if (data.inactive) {
      const paused = Boolean(activeDownload?.pauseRequested);
      throw Object.assign(new Error(paused ? "下载已暂停，已完成批次保留" : "上一下载尝试已结束，请继续重试此批"), {paused});
    }
    if (!data.pending) return data;
    // Once another owner is active, observe its receipt without starting a
    // replacement operation if it fails or finishes cancellation.
    observeOnly = true;
    const delay = Math.max(250, Math.min(3000, Number(data.retryAfterMs) || 1000));
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
}

function chooseDirectDownload(error) {
  const dialog = $("#taskCapacityDialog");
  $("#taskCapacityText").textContent = `未完成任务已满：当前账号 ${error.ownTaskCount} 项，其他账号 ${error.otherTaskCount} 项。可先移除旧任务，或只下载这一次；直接下载不会保留重启恢复记录。`;
  return new Promise(resolve => {
    const finish = direct => {
      dialog.oncancel = dialog.onclose = null;
      dialog.close();
      resolve(direct);
    };
    $("#taskCapacityDirect").onclick = () => finish(true);
    $("#taskCapacityCancel").onclick = () => finish(false);
    $("#taskCapacityManage").onclick = () => {
      finish(false);
      setTimeout(() => {
        navigatePrimary("#detail");
        $("#pendingDownloads").hidden=false;
        $("#pendingDownloads").open=true;
        $("#pendingDownloadSummary").focus({preventScroll:true});
        $("#pendingDownloadStatus").textContent = error.ownTaskCount
          ? "移除不再需要的未完成任务后，可重新下载。"
          : "当前账号没有可清理的任务。可切回原账号清理，或重新下载时选择本次直接下载。";
      }, 0);
    };
    dialog.oncancel = dialog.onclose = () => finish(false);
    showAppDialog(dialog);
  });
}

async function executeDownloadTask(task, requestForChunk, reportProgress) {
  const running = {task, chunk: null, pauseRequested: false, pauseInFlight: false, checking: false, startedAt: performance.now()};
  activeDownload = running;
  updateDetailDownloadProgress(task,"running");
  cancelSearchPrefetch();
  const paused = () => Object.assign(new Error("下载已暂停，已完成批次保留"), {paused: true});
  const updateStatus = () => {
    const elapsed = Math.floor((performance.now() - running.startedAt) / 1000);
    $("#pendingDownloadStatus").textContent = running.pauseRequested
      ? "正在暂停…"
      : running.cooldownUntil ? `正在等待 Pixiv 限频冷却 · 剩余 ${Math.max(0, Math.ceil((running.cooldownUntil - performance.now()) / 1000))} 秒 · 可点击暂停`
      : running.checking ? `正在核对旧任务进度 · 已用 ${elapsed} 秒…`
      : `已处理 ${task.savedCount} 张 · ${elapsed} 秒`;
  };
  updateStatus();
  renderPendingTasks();
  const timer = setInterval(updateStatus, 1000);
  try {
    if (task.persisted && !task.chunkPolicy) {
      running.checking = true;
      updateStatus();
      while (true) {
        const state = await writeTaskState("/api/workspace/task/migrate", {id: task.id, scope: workspaceSync.scope}, 30000);
        if (task.authorizationRevision !== downloadAuthorizationRevision) throw new Error("Pixiv 账户状态已变更，请重新确认下载");
        if (state.pending) {
          running.chunk = {requestId: state.requestId};
          if (running.pauseRequested && !running.pauseInFlight && running.pauseSentForId !== state.requestId) void pauseDownloadTask(task.id);
          await new Promise(resolve => setTimeout(resolve, 500));
          continue;
        }
        if (state.removed) { pendingTasks.delete(task.id); throw new Error("任务已移除，列表已更新"); }
        Object.assign(task, state.task);
        updateDetailDownloadProgress(task,"running");
        if (task.remainingChunks.length) pendingTasks.set(task.id, task);
        else pendingTasks.delete(task.id);
        running.checking = false;
        running.chunk = null;
        renderPendingTasks();
        if (running.pauseRequested && task.remainingChunks.length) throw paused();
        break;
      }
    }
    delete task.conversionFailure;
    if (!task.transient && (workspaceSync.ready || task.persisted)) {
      try {
        await saveDownloadTask(task);
        task.persisted = true;
      } catch (error) {
        if (!error.taskCapacity || task.persisted || !await chooseDirectDownload(error)) throw error;
        if (task.authorizationRevision !== downloadAuthorizationRevision) throw new Error("Pixiv 账户状态已变更，请重新确认下载");
        task.transient = true;
        task.persisted = false;
      }
    }
    const chunks = task.remainingChunks;
    for (let index = 0; index < chunks.length; index += 1) {
      if (running.pauseRequested) throw paused();
      const chunk = chunks[index];
      running.chunk = chunk;
      running.startedAt = performance.now();
      updateStatus();
      reportProgress();
      const request = requestForChunk(chunk);
      let data;
      try { data = await fetchDownloadResult(request, chunk, task); }
      catch (error) {
        if (running.pauseRequested) throw paused();
        throw error;
      }
      if (task.authorizationRevision !== downloadAuthorizationRevision) throw new Error("Pixiv 账户状态已变更，请重新确认下载");
      task.savedCount += data.pages ?? chunk.pageCount;
      task.fileCount += data.saved?.length || 0;
      task.firstSaved ||= data.saved?.[0] || "";
      task.cleanupPending ||= Boolean(data.cleanupPending);
      task.historyWarning ||= Boolean(data.historyWarning);
      task.catalogWarning ||= Boolean(data.catalogWarning);
      task.stateWarning ||= Boolean(data.recoveryWarning);
      task.skippedCount += data.skippedPages || 0;
      historyView.refresh();
      dispatchEvent(new Event("moku-download-history-changed"));
      task.completedBatches += 1;
      // Advance only after publication succeeds; failures retain this chunk.
      task.remainingChunks = chunks.slice(index + 1);
      updateDetailDownloadProgress(task,"running");
      if (task.persisted) {
        if (task.remainingChunks.length) await saveDownloadTask(task,null,chunk.requestId);
        else {
          try { await saveDownloadTask(task); }
          catch { task.stateWarning = true; }
        }
      }
    }
    void refreshDownloadedIndicators();
    $("#pendingDownloadStatus").textContent = "任务已完成。";
    updateDetailDownloadProgress(task,"success");
  } catch (error) {
    updateDetailDownloadProgress(task,error.paused?"paused":"error");
    $("#pendingDownloadStatus").textContent = error.paused
      ? "任务已暂停，已完成批次保留，点击继续后下载。"
      : `已暂停：${error.message}。再次继续会重试失败的这一批。`;
    throw error;
  } finally {
    clearInterval(timer);
    activeDownload = null;
    renderPendingTasks();
  }
}

function downloadCompletionWarnings(task) {
  return `${task.skippedCount ? `；另有 ${task.skippedCount} 页已存在，已跳过` : ""}${task.cleanupPending ? "；临时文件清理未完成，请检查日志" : ""}${task.historyWarning ? "；下载历史记录失败，文件已保存" : ""}${task.catalogWarning ? "；目录记录失败，下次可能无法跳过这些图片" : ""}${task.stateWarning ? "；任务记录未更新，文件已保存" : ""}`;
}

function savedDownloadPages(task) {
  // savedCount is the persisted processed-page cursor, including skipped pages.
  return Math.max(0, task.savedCount - task.skippedCount);
}

function updateDetailDownloadProgress(task,status) {
  const progress={taskId:task.id,status,done:task.savedCount,
    total:task.savedCount+task.remainingChunks.reduce((sum,chunk)=>sum+chunk.pageCount,0)};
  if(task.kind==="single")detailDownloadProgress={...progress,artworkId:task.taskOptions.body.id};
  else batchDownloadProgress={...progress,selectionKey:task.selectionKey || null};
  renderDetailDownloadProgress();
}

function renderDetailDownloadProgress() {
  const batch=document.body.classList.contains("batch-mode");
  const state=batch
    ? (batchDownloadProgress && (batchDownloadProgress.selectionKey===JSON.stringify(groupsForProgress([{groups:selectedGroups()}])))
      ? batchDownloadProgress:{status:"idle",done:0,total:selectedPageCount()})
    : currentDetailItem && String(detailDownloadProgress?.artworkId)===String(currentDetailItem.id)
      ? detailDownloadProgress : {status:"idle",done:0,total:currentDetailItem?currentDownloadPages(currentDetailItem).length:0};
  $("#detailDownloadProgress").setAttribute("aria-label",batch?"采集篮下载进度":"本作品下载进度");
  $("#detailDownloadProgress").dataset.state=state.status;
  $("#downloadProgressResult").textContent=state.status==="success"?"✅":state.status==="error"?"❌":"";
  $("#downloadProgressTrack").setAttribute("aria-valuemax",String(state.total));
  $("#downloadProgressTrack").setAttribute("aria-valuenow",String(state.done));
  $("#downloadProgressTrack").setAttribute("aria-valuetext",`${state.done} / ${state.total}${state.status==="success"?" · 完成":state.status==="error"?" · 失败":state.status==="paused"?" · 已暂停":""}`);
  $("#downloadProgressFill").style.width=`${state.total?state.done/state.total*100:0}%`;
  $("#downloadProgressCount").textContent=`${state.done} / ${state.total}`;
  normalDetailView.refreshDownloadStatus();
  $("#batchDownloadPrompt").hidden=!batch || state.status!=="success";
}

function setDownloadButtonState(button, text, disabled) {
  button.disabled = disabled;
  button.textContent = text;
}

function setBasketSelectionLocked(locked) {
  selection.setLocked(locked);
  const controls = document.querySelectorAll("[data-select],[data-batch-select],[data-collection-page],#selectAllPage,#clearPageSelection,#clearSelection,#searchForm input,#searchSubmit,#searchForm select,#pagination button");
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
  updatePaginationDock();
  if (!locked) {
    void restoreWorkspaceWhenUnlocked();
    scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
  }
}

function downloadPayload(item) {
  const downloadContext = currentDetailContext || activeSearchContext;
  return {
    endpoint: "/api/pixiv/download",
    body: { id: item.id, pages: currentDownloadPages(item), ...readDownloadOptions(), context: downloadContext },
    timeout: 1800000,
  };
}

function scrollToResults() {
  if (!selection.locked && !singleDownloadPending) {
    closeAllViewer();
    closeBasketPage();
    setWorkspace(null);
  }
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
  if (dialog?.showModal) showAppDialog(dialog);
}

function selectionWouldBeEvicted(targetPage) {
  const oldestRetainedPage = Math.max(1, targetPage - SEARCH_KEEP_BEHIND);
  return [...unarchivedSelectionIds()].some(
    (id) => (selection.get(id)?.resultPage || currentPage) < oldestRetainedPage,
  );
}

function navigateToPage(page) {
  if (selection.locked) return;
  if(activeSearchFilters.order==="popular"){checkpointNavigation();void loadGalleryPopularity(page);scrollToResults();return;}
  if (page > currentPage && currentPageNeedsMoreResults()) page = currentPage;
  if (selectionWouldBeEvicted(page)) {
    openCapacityDialog(page);
    return;
  }
  search(activeTagQuery, page, { ...activeSearchFilters }, activeSearchSnapshotId, {continuePage:page===currentPage && currentPageNeedsMoreResults(),preferenceKind:galleryPreferenceKind});
  scrollToResults();
}

function archiveAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  const detached = detachSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    announceToast(`已将当前 ${detached} 个作品放入采集篮；继续翻页不会下载原图`);
    search(activeTagQuery, page, { ...activeSearchFilters }, activeSearchSnapshotId, {continuePage:page===currentPage && currentPageNeedsMoreResults(),preferenceKind:galleryPreferenceKind});
    scrollToResults();
  }
}

function clearAndContinue() {
  const page = pendingNavigationPage;
  pendingNavigationPage = null;
  clearSelection(unarchivedSelectionIds());
  $("#capacityDialog")?.close();
  if (page !== null) {
    search(activeTagQuery, page, { ...activeSearchFilters }, activeSearchSnapshotId, {continuePage:page===currentPage && currentPageNeedsMoreResults(),preferenceKind:galleryPreferenceKind});
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
  selection.remember(item);
  basketDetailView.reset();
  showBasketPane("detail");
  $("#basketTitle").textContent = "采集篮 · 作品详情";
  const visible = basketDetailView.render(item);
  renderCollectionWindowInto(item, $("#basketPages"), $("#basketPageMore"));
  $("#basketViewAll").hidden = item.pages <= visible;
  syncBasketHeader();
  syncBasketArtworkMeta();
  syncPreviewResources();
}

async function openBatchCollection(id,{restoring=false}={}) {
  if(!restoring)preparePageNavigation();
  let item = selection.get(id)?.item || batchCandidateItems.find((candidate) => candidate.id === id);
  if (!item) return;
  checkpointNavigation();
  contextReturns.basket={key:capturePageState().key};
  invalidateDetailView();
  detailController = new AbortController();
  const controller = detailController;
  const generation = viewGeneration;
  try {
    if (item.source === "pixiv" && (!item.pageImages || staleBasketPreviewIds.has(String(item.id)))) {
      $("#basketTitle").textContent = "正在加载合集详情…";
      item = await fetchJson(`/api/pixiv/artwork/${item.id}`, { signal: controller.signal }, ARTWORK_DETAIL_TIMEOUT_MS);
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
    queueNavigationRecord();
    $("#basketPage").scrollTop = 0;
  } catch (error) {
    if (controller.signal.aborted) return;
    announceToast(error.message || "作品详情加载失败");
  } finally {
    if (controller === detailController) detailController = null;
  }
}

$("#openBasketPicker").onclick = openSelectionBasket;
$("#basketDownload").onclick = () => {
  if (contextNavigationLocked() || selectedPageCount() === 0) return;
  showBatchDetail();
};
$("#basketClear").onclick = () => {
  if (contextNavigationLocked() || selection.locked || singleDownloadPending || !batchCandidateItems.length) return;
  showAppDialog($("#basketClearDialog"));
};
$("#basketClearCancel").onclick = () => $("#basketClearDialog").close();
$("#basketClearConfirm").onclick = () => {
  $("#basketClearDialog").close();
  if (contextNavigationLocked() || !basketPageOpen()) return;
  invalidateDetailView();
  if (!clearAllSelection()) return;
  basketDetailItem = null;
  basketDetailView.reset();
  $("#basketDetailDeck").innerHTML = "";
  $("#basketPages").innerHTML = "";
  $("#basketToast").textContent = "已清空采集篮";
  openBasketArtworkPicker();
  syncResultSelectionControls();
  $("#basketPage").scrollTop = 0;
};
$("#basketBack").onclick = () => {
  if (selection.locked || pageRefreshPending) return;
  checkpointNavigation();
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
  queueNavigationRecord();
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
  if (selectedPageCount() > MAX_SELECTED_PAGES) {
    announceToast(`采集篮已超过 ${MAX_SELECTED_PAGES} 张，请取消部分选择后下载`);
    return;
  }
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
  announceToast("");
  showTaskDock("批量下载", "任务已锁定，正在准备保存…", 0);
  try {
    await executeDownloadTask(task, (chunk) => ({
      endpoint: "/api/pixiv/batch-download",
      body: { groups: chunk.groups, ...taskOptions, context: chunk.context },
      timeout: 1800000,
    }), () => {
      setDownloadButtonState(button, "正在保存…", true);
      showTaskDock("批量下载", `已处理 ${task.savedCount} / ${task.savedCount+task.remainingChunks.reduce((sum,chunk)=>sum+chunk.pageCount,0)} 张`, 0);
    });
    resumableBatchTask = null;
    const summary = `已保存 ${savedDownloadPages(task)} 张图片${downloadCompletionWarnings(task)}`;
    $("#pendingDownloadStatus").textContent=summary;
    showTaskDock("批量下载完成", summary, task.historyWarning ? 8000 : 3200);
  } catch (error) {
    await rememberConversionFailure(task, error);
    resumableBatchTask = !error.taskConflict && task.authorizationRevision === downloadAuthorizationRevision ? task : null;
    const prefix = task.savedCount ? `已保存 ${savedDownloadPages(task)} 页；后续` : "批量下载";
    const retryHint = resumableBatchTask
      ? `再次点击只继续剩余 ${task.remainingChunks.length} 批。`
      : "账户状态已变更，请重新确认后发起下载。";
    const message = error.paused ? `下载已暂停，已保存 ${savedDownloadPages(task)} 页。${retryHint}` : `${prefix}失败：${error.message}。${retryHint}`;
    $("#pendingDownloadStatus").textContent=message;
    showTaskDock(error.paused ? "下载已暂停" : "批量下载中断", message, 8000);
  } finally {
    setDownloadButtonState(button, "下载已勾选图片", false);
    setBasketSelectionLocked(false);
  }
};

addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (document.querySelector("dialog[open]")) return;
  if (!$("#allViewer").hidden) {
    checkpointNavigation();
    closeAllViewer();
    return;
  }
  if (libraryWorkspaceOpen()) { closeLibrary(); return; }
  if (contextNavigationLocked()) return;
  if (basketPageOpen()) $("#basketBack").click();
  else if (activeWorkspace === "download") navigatePrimary("#gallery");
});

function updateFormatHint() {
  const quality = $("#quality").selectedOptions[0]?.textContent || "";
  const format = $("#format").selectedOptions[0]?.textContent || "";
  $("#qualityText").textContent = $("#quality").value === "regular"
    ? "标准：使用 Pixiv 缩小预览；动图为 600px 帧包，文件较小。"
    : quality ? "原图：使用原始分辨率，不缩小图片。" : "";
  $("#formatText").textContent = format;
  if (currentDetailItem?.workType === "ugoira" && !singleDownloadPending) $("#download").textContent = downloadButtonLabel(currentDetailItem);
}

$("#quality").onchange = updateFormatHint;
$("#format").onchange = updateFormatHint;
$("#searchForm").onsubmit = async (event) => {
  event.preventDefault();preparePageNavigation();checkpointNavigation();accountOrigin=false;
  const revision=navigationRevision,tag=$("#tag").value,filters=readSearchFilters(),kind=searchKind.value;
  scrollToResults();
  try{
    await browseOptions.ready();if(revision!==navigationRevision)return;
    galleryLinkedEntry=false;galleryIntent=null;
    await search(tag,1,filters,null,{returnTarget:null,order:browseOptions.order(kind),preferenceKind:kind});
  }catch(error){if(revision===navigationRevision)announceToast(error.message);}
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
      downloadDefaultsTouched=true;
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
  const request = downloadPayload(item);
  const chunks = request.body.pages.flatMap(page=>planDownloadChunks([{id:item.id,pages:[page]}]));
  const task = prepareDownloadTask(chunks, { endpoint: request.endpoint, body: request.body }, resumableSingleTask);
  const button = $("#download");
  singleDownloadPending = true;
  announceToast("");
  setDownloadButtonState(button, "正在保存…", true);
  showTaskDock("正在保存", "正在保存当前作品…", 0);
  syncSearchScopedControls();
  try {
    await executeDownloadTask(task, (chunk) => ({
      ...request,
      body: { ...request.body, pages: chunk.groups[0].pages },
    }), () => {
      setDownloadButtonState(button, "正在保存…", true);
      showTaskDock("正在保存", `第 ${task.completedBatches + 1}/${task.totalBatches} 批 · 已保存 ${savedDownloadPages(task)} 页`, 0);
    });
    resumableSingleTask = null;
    const summary = `已保存 ${savedDownloadPages(task)} 页，${task.fileCount} 个文件`;
    $("#pendingDownloadStatus").textContent=`${summary}${task.firstSaved ? `：${task.firstSaved}` : ""}${downloadCompletionWarnings(task)}`;
    showTaskDock("保存完成", `${summary}${downloadCompletionWarnings(task)}`, task.historyWarning ? 8000 : 3200);
  } catch (error) {
    await rememberConversionFailure(task, error);
    resumableSingleTask = !error.taskConflict && task.authorizationRevision === downloadAuthorizationRevision ? task : null;
    const retryHint = resumableSingleTask
      ? `再次点击只继续剩余 ${task.remainingChunks.length} 批。`
      : "账户状态已变更，请重新确认后发起下载。";
    $("#pendingDownloadStatus").textContent=error.paused ? `下载已暂停，已保存 ${savedDownloadPages(task)} 页。${retryHint}` : `已保存 ${savedDownloadPages(task)} 页；保存失败：${error.message || "未知错误"}。${retryHint}`;
    showTaskDock(error.paused ? "下载已暂停" : "保存失败", error.message || "未知错误", 8000);
  } finally {
    singleDownloadPending = false;
    renderPendingTasks();
    button.textContent = downloadButtonLabel(currentDetailItem);
    syncSearchScopedControls();
    void restoreWorkspaceWhenUnlocked();
  }
};

async function syncAuthStatus() {
  const generation = ++authStatusGeneration;
  const policyRevision = requestPolicyRevision;
  const policySequence = $("#requestMode").disabled ? null : ++requestPolicyReadSequence;
  try {
    const data = await fetchJson("/api/status", {}, 8000);
    if (generation !== authStatusGeneration) return;
    if (data.requestPolicy && policySequence === requestPolicyReadSequence && policyRevision === requestPolicyRevision && !$("#requestMode").disabled) applyRequestPolicy(data.requestPolicy);
    const logged = Boolean(data.loggedIn);
    const authorizationLost = lastKnownLoggedIn === true && !logged;
    const accountGeneration = Number.isInteger(data.authorizationGeneration) ? data.authorizationGeneration : null;
    const accountChanged = lastKnownAuthorizationGeneration !== null
      && accountGeneration !== null && accountGeneration !== lastKnownAuthorizationGeneration;
    const reloadWorkspace = workspaceSync.active
      && (accountChanged || (lastKnownLoggedIn !== null && lastKnownLoggedIn !== logged));
    if (reloadWorkspace) workspaceSync.reset();
    if (accountChanged || (lastKnownLoggedIn !== null && lastKnownLoggedIn !== logged)) {
      downloadAuthorizationRevision += 1;
      resumableSingleTask = null;
      resumableBatchTask = null;
    }
    lastKnownLoggedIn = logged;
    lastKnownAuthorizationGeneration = accountGeneration;
    $("#mode").textContent = data.authMethod==="app"?(logged?"PIXIV · APP":"PIXIV · 待授权"):(logged?"PIXIV · WEB":"PIXIV PUBLIC");
    loginMethod=data.authMethod || "app";
    if(accountChanged || authorizationLost)settingsView.reset();
    cloudControls.setSession();
    syncAppConnection(appConnection);
    $("#safety").querySelectorAll('option[value="r18"],option[value="all"]').forEach((option) => { option.disabled = !logged; });
    if (authorizationLost) handleAuthorizationLoss();
    else if (accountChanged) handleAuthorizationLoss("Pixiv 账户状态已变更");
    if (reloadWorkspace) {
      syncSearchScopedControls();
      await restoreWorkspaceWhenUnlocked();
    }
    return true;
  } catch (error) {
    if (generation !== authStatusGeneration) return;
    $("#mode").textContent = "PIXIV OFFLINE";
    $("#authStateText").textContent = error.message || "暂时无法读取本机会话状态";
    return false;
  }
}

function applyRequestPolicy(state) {
  const previousMode = requestPolicyMode;
  const wasWaiting = requestPolicyWaiting;
  requestPolicyMode = state.mode;
  requestBlockedKind = state.blockedKind || "";
  requestPolicyWaiting = Boolean(requestBlockedKind || state.cooldownSeconds);
  $("#requestResume").hidden = !requestBlockedKind;
  requestCooldownUntil = Date.now() + state.cooldownSeconds * 1000;
  if (!requestModeEditing) {
    $("#requestMode").value = REQUEST_MODES.indexOf(state.mode);
    $("#requestMode").setAttribute("aria-valuetext", REQUEST_MODE_LABELS[REQUEST_MODES.indexOf(state.mode)]);
  }
  $("#requestMode").disabled = false;
  $("#requestPolicyStatus").textContent = requestBlockedKind
    ? "请完成 Pixiv 登录或验证，再继续请求"
    : state.cooldownSeconds
    ? `请求冷却 · 剩余 ${state.cooldownSeconds} 秒`
    : `${({safe: "安全", normal: "普通", fast: "激进"})[state.mode]} · ${state.downloadWidth || 1} 路下载${state.queued?` · ${state.queued} 项排队`:""}`;
  if (state.mode === "safe" || state.cooldownSeconds || requestBlockedKind) cancelSearchPrefetch();
  else if (previousMode === "safe" || wasWaiting) scheduleSearchPrefetch(activeTagQuery, currentPage, activeSearchFilters, searchGeneration);
}

async function changeRequestPolicy(update) {
  const revision = ++requestPolicyRevision;
  const sequence = ++requestPolicyReadSequence;
  $("#requestMode").disabled = true;
  $("#requestResume").disabled = true;
  try {
    const state = await fetchJson("/api/network/policy", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(update),
    });
    if (revision === requestPolicyRevision && sequence === requestPolicyReadSequence) applyRequestPolicy(state);
  } catch (error) {
    if (revision === requestPolicyRevision) {
      $("#requestMode").value = REQUEST_MODES.indexOf(requestPolicyMode);
      $("#requestMode").setAttribute("aria-valuetext", REQUEST_MODE_LABELS[REQUEST_MODES.indexOf(requestPolicyMode)]);
      $("#requestPolicyStatus").textContent = error.message;
    }
  } finally {
    if (revision === requestPolicyRevision) {
      $("#requestMode").disabled = false;
      $("#requestResume").disabled = false;
      if (sequence !== requestPolicyReadSequence) void refreshRequestPolicy();
    }
  }
}

$("#requestResume").onclick = () => changeRequestPolicy({action: "resume"});
$("#requestMode").onpointerdown = () => { requestModeEditing = true; };
$("#requestMode").onblur = $("#requestMode").onpointercancel = () => { requestModeEditing = false; };
$("#requestMode").oninput = () => {
  requestModeEditing = true;
  $("#requestMode").setAttribute("aria-valuetext", REQUEST_MODE_LABELS[Number($("#requestMode").value)]);
};
$("#requestMode").onchange = () => {
  requestModeEditing = false;
  return changeRequestPolicy({mode: REQUEST_MODES[Number($("#requestMode").value)]});
};

function refreshRequestPolicy() {
  if (requestPolicyRefresh) return requestPolicyRefresh;
  if ($("#requestMode").disabled) return Promise.resolve();
  const revision = requestPolicyRevision;
  const sequence = ++requestPolicyReadSequence;
  requestPolicyRefresh = (async () => {
    try {
      const state = await fetchJson("/api/network/policy", {}, 5000);
      if (sequence === requestPolicyReadSequence && revision === requestPolicyRevision && !$("#requestMode").disabled) applyRequestPolicy(state);
    } catch (error) {
      if (sequence === requestPolicyReadSequence && revision === requestPolicyRevision && !$("#requestMode").disabled) $("#requestPolicyStatus").textContent = error.message;
    } finally {
      requestPolicyRefresh = null;
    }
  })();
  return requestPolicyRefresh;
}

const helpDialog = $("#helpDialog");
$("#helpBtn").onclick = () => {
  showAppDialog(helpDialog);
  void refreshRequestPolicy();
};
const guideTabs = [...helpDialog.querySelectorAll("[data-guide-panel]")];
function selectGuidePanel(tab) {
  for (const button of guideTabs) {
    const selected = button === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(`#${button.dataset.guidePanel}`).hidden = !selected;
  }
}
for (const tab of guideTabs) {
  tab.onclick = () => selectGuidePanel(tab);
  tab.onkeydown = event => {
    const index = guideTabs.indexOf(tab);
    const next = event.key === "ArrowRight" ? (index + 1) % guideTabs.length
      : event.key === "ArrowLeft" ? (index + guideTabs.length - 1) % guideTabs.length
      : event.key === "Home" ? 0 : event.key === "End" ? guideTabs.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    selectGuidePanel(guideTabs[next]);
    guideTabs[next].focus();
  };
}
helpDialog.addEventListener("close", () => {
  void fetchJson("/api/ui/guide", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({seen: true})})
    .catch(error => announceToast(error.message));
});
const initialPolicyRevision = requestPolicyRevision;
const initialPolicySequence = ++requestPolicyReadSequence;
void fetchJson("/api/network/policy").then(state => {
  if (initialPolicySequence === requestPolicyReadSequence && initialPolicyRevision === requestPolicyRevision) applyRequestPolicy(state);
}).catch(error => {
  if (initialPolicySequence === requestPolicyReadSequence && initialPolicyRevision === requestPolicyRevision) $("#requestPolicyStatus").textContent = error.message;
}).finally(() => {
  if (initialPolicyRevision === requestPolicyRevision) $("#requestMode").disabled = false;
});
setInterval(() => {
  if ((!helpDialog.open && !(activeWorkspace==="account" && accountView.snapshot().view==="settings") && !requestPolicyWaiting) || document.hidden || $("#requestMode").disabled) return;
  void refreshRequestPolicy();
}, 2000);
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
      tls: "TLS 握手或连接中断",
      certificate: "证书校验失败",
      tls_protocol: "TLS 协议或代理端口异常",
      dns: "域名解析失败",
      reset: "连接被重置或传输中断",
      proxy: "代理隧道连接失败",
      http: "HTTP 响应异常",
      unavailable: "无法连接",
    };
    const formatCheck = (label, row) => {
      if (row?.ok) return `${label}：可用${Number.isFinite(row.ms) ? `（${row.ms} ms）` : ""}`;
      if (row?.errorKind === "waiting" || row?.errorKind === "paused") return `${label}：暂缓检测（${row.message}）`;
      return `${label}：不可用（${errorLabels[row?.errorKind] || "无法连接"}${row?.errorCode ? ` · ${row.errorCode}` : ""}）`;
    };
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

function syncAppConnection(value) {
  appConnection=value;
  const app=loginMethod==="app",waiting=app && Boolean(value?.authorization?.pending);
  $("#authMethod").value=loginMethod;
  $("#authMethod").disabled=authActionBusy || appConnectionBusy;
  $("#rememberLoginRow").hidden=app;
  $("#authStateTitle").textContent=waiting?"等待 Pixiv 授权":lastKnownLoggedIn?"已登录 · "+(app?"App 授权":"网页登录"):"尚未登录";
  $("#authStateText").textContent=app && value?.authorization?.error || (waiting?"请在系统浏览器完成本次授权。":lastKnownLoggedIn?"当前方式已连接，可以继续浏览。":app?"在系统浏览器完成一次 App 授权即可使用。":"在应用内的 Pixiv 官方窗口完成登录。");
  $("#authAction").textContent=appConnectionBusy?"正在打开授权网页…":authActionBusy?"正在处理…":waiting?"重新打开授权网页":lastKnownLoggedIn?(app?"重新授权":"重新登录"):"登录 Pixiv";
  $("#authAction").disabled=authActionBusy || appConnectionBusy;
  $("#authDisconnect").hidden=!lastKnownLoggedIn;
  $("#authDisconnect").disabled=authActionBusy || appConnectionBusy;
}
async function beginAppLogin(){
  const revision=++appConnectionRevision;
  clearTimeout(appConnectionTimer);
  appConnectionBusy=true;syncAppConnection(appConnection);
  try{
    if(!window.pywebview?.api?.pixiv_account_login)throw Error("请在 MOKU 桌面程序中登录");
    const result=await window.pywebview.api.pixiv_account_login();
    if(revision!==appConnectionRevision)return;
    if(!result.ok)throw Error(result.error || "授权暂不可用");
    appConnectionBusy=false;
    syncAppConnection({...appConnection,authorization:{pending:true,error:""}});
    async function poll(){
      try{
        const value=await fetchJson("/api/account/authorization",{},8000);
        if(revision!==appConnectionRevision)return;
        syncAppConnection(value);
        if(value.authorization?.pending){appConnectionTimer=setTimeout(poll,2000);return;}
        await syncAuthStatus();
        if(revision!==appConnectionRevision)return;
        syncAppConnection(value);
        accountView.invalidateConnection();
        if(activeWorkspace==="account")await accountView.refresh();
      }catch(error){
        if(revision!==appConnectionRevision)return;
        appConnectionBusy=false;syncAppConnection(appConnection);
        $("#authStateText").textContent=error.message || "授权状态读取失败，可右键刷新重试";
      }
    }
    appConnectionTimer=setTimeout(poll,1000);
  }catch(error){
    if(revision!==appConnectionRevision)return;
    appConnectionBusy=false;syncAppConnection(appConnection);
    $("#authStateText").textContent=error.message || "授权暂不可用";
  }
}
$("#authAction").onclick = async () => {
  if(loginMethod==="app"){await beginAppLogin();return;}
  authActionBusy=true;syncAppConnection(appConnection);
  let actionError="";
  try {
    if (!window.pywebview?.api?.pixiv_login)throw Error("请在 MOKU 桌面版中登录。");
    const data=await window.pywebview.api.pixiv_login(Boolean($("#rememberLogin").checked));
    if(!data.ok)throw Error(data.error || "登录失败");
  }catch(error){actionError=error.message || "登录失败";}
  finally{
    authActionBusy=false;
    await syncAuthStatus();
    if(activeWorkspace==="account")await accountView.refresh();
    syncAppConnection(appConnection);
    if(actionError)$("#authStateText").textContent=actionError;
  }
};
$("#authDisconnect").onclick=async()=>{
  authActionBusy=true;syncAppConnection(appConnection);
  let error="";
  try{
    if(!window.pywebview?.api?.pixiv_logout)throw Error("请在 MOKU 桌面版中退出登录。");
    const result=await window.pywebview.api.pixiv_logout();if(!result.ok)throw Error(result.error || "退出失败");
  }catch(problem){error=problem.message;}
  finally{
    authActionBusy=false;await syncAuthStatus();
    if(activeWorkspace==="account")await accountView.refresh();
    syncAppConnection(appConnection);if(error)$("#authStateText").textContent=error;
  }
};
$("#authMethod").onchange=async event=>{
  if(contextNavigationLocked() || selection.locked || singleDownloadPending || activeDownload){event.target.value=loginMethod;showTaskDock("暂不能切换登录方式","请等待当前下载完成。",4000);return;}
  const method=event.target.value;
  authActionBusy=true;syncAppConnection(appConnection);
  let failure="";
  try{
    // syncAppConnection keeps the control at the current method while saving.
    await fetchJson("/api/auth/method",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({method})});
    await syncAuthStatus();
    if(activeWorkspace==="account")await accountView.refresh();
  }catch(error){failure=error.message || "登录方式未能切换";}
  finally{authActionBusy=false;syncAppConnection(appConnection);if(failure)$("#authStateText").textContent=failure;}
};
document.querySelectorAll(".dialog-close").forEach((button) => {
  button.onclick = () => button.closest("dialog")?.close();
});
document.addEventListener("close", event => {
  const index = openDialogs.indexOf(event.target);
  if (index === -1) return;
  openDialogs.splice(index, 1);
}, true);
document.querySelectorAll("dialog").forEach((modal) => {
  modal.onclick = (event) => {
    if (event.target !== modal) return;
    const bounds = modal.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom) modal.close();
  };
});

pageHistory=createNavigationHistory({capture:capturePageState,restore:restorePageState,allowed:()=>true,changed:syncPageHistoryControls,prune:pruneNavigationCache});
document.querySelectorAll("[data-context-back]").forEach(button=>{
  button.onclick=()=>{
    if(contextNavigationLocked())return;
    const target=contextReturns[button.dataset.returnSurface];
    preparePageNavigation();
    if(target)void pageHistory.goTo(target.key).catch(error=>showTaskDock("返回失败",error.message,5000));
  };
});
function navigateHistory(direction) {
  if (contextNavigationLocked()) return;
  preparePageNavigation();
  void pageHistory.go(direction).catch(error=>showTaskDock("页面跳转失败",error.message,4000));
}
$("#pageBack").onclick=()=>navigateHistory(-1);
$("#pageForward").onclick=()=>navigateHistory(1);
installHistoryInput({navigate:navigateHistory,canNavigate:()=>!contextNavigationLocked()});

clearDetail();
renderRecentSearches();
grid.innerHTML = '<div class="empty-state"><b>准备就绪</b><p>输入标签后点击“开始寻找”。登录后会在后台准备账号数据。</p></div>';
$("#count").textContent = "等待搜索";
syncSearchScopedControls();

checkpointNavigation();
if ("requestIdleCallback" in window) {
  requestIdleCallback(async () => { await syncAuthStatus(); await restoreWorkspace(); }, { timeout: 2000 });
} else {
  setTimeout(async () => { await syncAuthStatus(); await restoreWorkspace(); }, 500);
}

bindWindowControls();
void sourceSearchView.loadSettings();
void fetchJson("/api/ui/guide").then(state => {
  if (!state.seen) showAppDialog(helpDialog);
}).catch(error => {
  showAppDialog(helpDialog);
  announceToast(error.message);
});
