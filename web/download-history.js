/* Download history is loaded on entry, never during application startup. */
function createDownloadHistoryView({ fetchJson }) {
  const get = (id) => document.querySelector(`#${id}`);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  let page = 1;
  let query = "";
  let generation = 0;
  let controller = null;
  let clearing = false;

  async function load(target = page) {
    controller?.abort();
    controller = new AbortController();
    const current = ++generation;
    get("historyStatus").textContent = "正在读取本机记录…";
    get("historyClear").disabled = true;
    get("historyPagination").innerHTML = "";
    try {
      const params = new URLSearchParams({ page: target, query });
      const data = await fetchJson(`/api/library/history?${params}`, { signal: controller.signal });
      if (current !== generation) return;
      page = data.page;
      get("historyStatus").textContent = `${data.total} 条${query ? "匹配" : ""}记录 · 最多保留最近 ${data.limit.toLocaleString()} 条作品记录`;
      get("historyClear").disabled = clearing || !data.total;
      get("historyList").innerHTML = data.items.map((row, index) => {
        const time = new Date(row.completedAt).toLocaleString();
        const format = row.format === "source" ? (row.workType === "ugoira" ? "帧 ZIP + JSON" : "源格式") : row.format.toUpperCase();
        const pages = row.pages.map((number) => `P${number + 1}`).join("、");
        return `<article class="history-record"><h3>${escape(row.title || "未命名作品")}</h3>
          <p>${escape(row.artist || "未知画师")} · ID ${escape(row.artworkId)}</p>
          <p>${escape(time)} · ${row.quality === "original" ? "原始清晰度" : "标准清晰度"} · ${escape(format)} · ${row.files.length} 个文件</p>
          <p class="history-pages" title="${escape(pages)}">${escape(pages)}</p>
          <details><summary>保存路径</summary><textarea class="history-paths" readonly aria-label="保存路径">${escape(row.files.join("\n"))}</textarea></details>
          <button type="button" data-copy-history="${index}">复制路径</button></article>`;
      }).join("") || `<p class="empty-state">${query ? "没有匹配记录。" : "还没有下载记录。启用此功能后，成功保存的作品会记录在这里；旧文件不会自动扫描。"}</p>`;
      get("historyList").querySelectorAll("[data-copy-history]").forEach((button) => {
        button.onclick = async () => {
          try {
            await navigator.clipboard.writeText(data.items[Number(button.dataset.copyHistory)].files.join("\n"));
            if (current === generation) get("historyStatus").textContent = "路径已复制";
          } catch {
            if (current === generation) get("historyStatus").textContent = "复制失败，请展开保存路径后手动复制。";
          }
        };
      });
      if (data.pages > 1) {
        get("historyPagination").innerHTML = `<button type="button" data-history-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><span>${page} / ${data.pages}</span><button type="button" data-history-page="${page + 1}" ${page >= data.pages ? "disabled" : ""}>下一页</button>`;
        get("historyPagination").querySelectorAll("[data-history-page]").forEach((button) => {
          button.onclick = () => { load(Number(button.dataset.historyPage)); get("historyPage").scrollTop = 0; };
        });
      }
    } catch (error) {
      if (current !== generation) return;
      get("historyStatus").textContent = error.message || "无法读取下载历史";
      get("historyList").innerHTML = '<p class="error-state">读取失败，可点击刷新重试。</p>';
    }
  }

  get("historySearch").onsubmit = (event) => {
    event.preventDefault();
    query = get("historyQuery").value.trim();
    load(1);
  };
  get("historyRefresh").onclick = () => load();
  get("historyClear").onclick = () => get("historyClearDialog").showModal();
  get("historyClearCancel").onclick = () => get("historyClearDialog").close();
  get("historyClearConfirm").onclick = async () => {
    if (clearing) return;
    clearing = true;
    get("historyClearDialog").close();
    get("historyClear").disabled = true;
    try {
      await fetchJson("/api/library/history/clear", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }),
      });
      if (!get("historyPage").hidden) await load(1);
    } catch (error) {
      get("historyStatus").textContent = error.message || "清空失败";
    } finally {
      clearing = false;
      if (!get("historyPage").hidden) get("historyClear").disabled = false;
    }
  };
  return {
    open: () => load(),
    close: () => { generation += 1; controller?.abort(); controller = null; },
    refresh: () => { if (!get("historyPage").hidden) load(); },
  };
}
