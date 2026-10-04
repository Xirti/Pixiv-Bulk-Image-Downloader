/* Download history is loaded on entry, never during application startup. */
function createDownloadHistoryView({ fetchJson }) {
  const get = (id) => document.querySelector(`#${id}`);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const selected = new Set();
  let page = 1, query = "", generation = 0;
  let controller = null, pendingDelete = null;
  let loading = false, deleting = false;
  let total = 0, rows = [];

  function syncControls() {
    const busy = loading || deleting;
    get("historyClear").disabled = busy || !total;
    get("historyDeleteSelected").disabled = busy || !selected.size;
    get("historySelectedCount").textContent = selected.size ? `已选 ${selected.size} 条` : "";
    const all = get("historySelectAll");
    const count = rows.filter(row => selected.has(row.id)).length;
    all.disabled = busy || !rows.length;
    all.checked = rows.length > 0 && count === rows.length;
    all.indeterminate = count > 0 && count < rows.length;
    get("historyRefresh").disabled = deleting;
    get("historyList").querySelectorAll("[data-select-history], [data-delete-history]").forEach(button => { button.disabled = busy; });
  }

  async function load(target = page) {
    if (deleting) return;
    controller?.abort();
    controller = new AbortController();
    const current = ++generation;
    loading = true;
    rows = [];
    get("historyStatus").textContent = "正在读取本机记录…";
    get("historyList").innerHTML = "";
    get("historyPagination").innerHTML = "";
    syncControls();
    try {
      const params = new URLSearchParams({ page: target, query });
      const data = await fetchJson(`/api/library/history?${params}`, { signal: controller.signal });
      if (current !== generation) return;
      page = data.page;
      total = data.total;
      rows = data.items;
      get("historyStatus").textContent = `${total} 条${query ? "匹配" : ""}记录`;
      get("historyList").innerHTML = rows.map((row, index) => {
        const time = new Date(row.completedAt).toLocaleString(undefined, {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false});
        const format = row.format === "source" ? (row.workType === "ugoira" ? "帧 ZIP + JSON" : "源格式") : row.format.toUpperCase();
        const work = `${row.title || "未命名作品"} · ${row.artist || "未知画师"} · ID ${row.artworkId}`;
        const info = `${time} · ${row.quality === "original" ? "原图" : "标准预览"} · ${format} · ${row.files.length} 个文件`;
        return `<article class="history-record"><input type="checkbox" data-select-history="${row.id}" ${selected.has(row.id) ? "checked" : ""} aria-label="选择 ${escape(row.title || "未命名作品")}">
          <h3 title="${escape(work)}">${escape(work)}</h3><p class="history-info">${escape(info)}</p>
          <span class="history-paths" title="${escape(row.files.join("\n"))}">${escape(row.files[0] || "")}${row.files.length > 1 ? ` · +${row.files.length - 1}` : ""}</span>
          <div class="history-record-actions"><button type="button" data-copy-history="${index}" title="复制全部保存路径">复制</button><button type="button" data-delete-history="${row.id}" title="只删除这条记录，不删除文件">删除</button></div></article>`;
      }).join("") || `<p class="empty-state">${query ? "没有匹配记录。" : "还没有下载记录。之后成功下载的作品会显示在这里。"}</p>`;
      get("historyList").querySelectorAll("[data-select-history]").forEach(box => {
        box.onchange = () => {
          if (loading || deleting) return;
          const id = Number(box.dataset.selectHistory);
          box.checked ? selected.add(id) : selected.delete(id);
          syncControls();
        };
      });
      get("historyList").querySelectorAll("[data-delete-history]").forEach(button => {
        button.onclick = () => confirmDelete([Number(button.dataset.deleteHistory)]);
      });
      get("historyList").querySelectorAll("[data-copy-history]").forEach(button => {
        button.onclick = async () => {
          try {
            await navigator.clipboard.writeText(data.items[Number(button.dataset.copyHistory)].files.join("\n"));
            if (current === generation) get("historyStatus").textContent = "路径已复制";
          } catch {
            if (current === generation) get("historyStatus").textContent = "复制失败，请重试。";
          }
        };
      });
      if (data.pages > 1) {
        get("historyPagination").innerHTML = `<button type="button" data-history-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><span>${page} / ${data.pages}</span><button type="button" data-history-page="${page + 1}" ${page >= data.pages ? "disabled" : ""}>下一页</button>`;
        get("historyPagination").querySelectorAll("[data-history-page]").forEach(button => {
          button.onclick = () => { load(Number(button.dataset.historyPage)); get("historyPage").scrollTop = 0; };
        });
      }
    } catch (error) {
      if (current !== generation) return;
      get("historyStatus").textContent = error.message || "无法读取下载历史";
      get("historyList").innerHTML = '<p class="error-state">读取失败，可点击刷新重试。</p>';
    } finally {
      if (current === generation) { loading = false; syncControls(); }
    }
  }

  function confirmDelete(ids) {
    if (loading || deleting || (ids !== null && !ids.length)) return;
    pendingDelete = { ids };
    get("historyDeleteTitle").textContent = ids === null ? "清空下载历史？" : `删除 ${ids.length} 条记录？`;
    get("historyDeleteText").textContent = "只删除记录，不会删除已下载的文件。删除后无法恢复。";
    get("historyClearConfirm").textContent = ids === null ? "清空记录" : "删除记录";
    get("historyClearDialog").showModal();
  }

  get("historySelectAll").onchange = () => {
    if (loading || deleting) return;
    const checked = get("historySelectAll").checked;
    rows.forEach(row => { checked ? selected.add(row.id) : selected.delete(row.id); });
    get("historyList").querySelectorAll("[data-select-history]").forEach(box => { box.checked = checked; });
    syncControls();
  };
  get("historySearch").onsubmit = (event) => {
    event.preventDefault();
    if (deleting) return;
    const next = get("historyQuery").value.trim();
    if (next !== query) selected.clear();
    query = next;
    load(1);
  };
  get("historyRefresh").onclick = () => load();
  get("historyClear").onclick = () => confirmDelete(null);
  get("historyDeleteSelected").onclick = () => confirmDelete([...selected]);
  get("historyClearCancel").onclick = () => { pendingDelete = null; get("historyClearDialog").close(); };
  get("historyClearDialog").addEventListener("cancel", () => { pendingDelete = null; });
  get("historyClearConfirm").onclick = async () => {
    if (deleting || !pendingDelete) return;
    const { ids } = pendingDelete;
    pendingDelete = null;
    deleting = true;
    get("historyClearDialog").close();
    controller?.abort();
    const current = ++generation;
    loading = false;
    syncControls();
    try {
      await fetchJson(`/api/library/history/${ids === null ? "clear" : "delete"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ids === null ? { confirm: true } : { confirm: true, ids }),
      });
      if (ids === null) selected.clear();
      else ids.forEach(id => selected.delete(id));
      deleting = false;
      if (!get("historyPage").hidden) await load(ids === null ? 1 : page);
    } catch (error) {
      // Re-entry while a mutation is pending skips load(). Refresh its old DOM
      // on failure too, so cleared selections and the error remain consistent.
      if (current !== generation && !get("historyPage").hidden) {
        deleting = false;
        await load();
      }
      if (!get("historyPage").hidden) get("historyStatus").textContent = error.message || "删除失败，请重试";
    } finally {
      deleting = false;
      syncControls();
    }
  };
  return {
    open: () => load(),
    close: () => { generation += 1; controller?.abort(); controller = null; selected.clear(); loading = false; },
    refresh: () => { if (!get("historyPage").hidden) load(); },
  };
}
