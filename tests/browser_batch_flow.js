(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (predicate, timeout = 120000, label = "condition") => {
    const started = performance.now();
    let last;
    while (performance.now() - started < timeout) {
      try {
        last = predicate();
        if (last) return last;
      } catch (error) {
        last = String(error);
      }
      await wait(150);
    }
    throw new Error(`timeout waiting for ${label}; last=${String(last)}`);
  };
  const submit = async (tag, workType = "all", includeAi = false) => {
    document.querySelector("#tag").value = tag;
    document.querySelector("#workType").value = workType;
    document.querySelector("#includeAi").checked = includeAi;
    document.querySelector("#searchForm").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await waitFor(() => !document.querySelector("#searchForm button").disabled && !document.querySelector("#grid .loading-state"), 120000, `search ${tag}`);
  };
  const openDetail = async index => {
    const card = document.querySelectorAll("#grid .card")[index];
    if (!card) throw new Error(`missing card ${index}`);
    card.click();
    await waitFor(() => document.querySelector("#download") && !document.querySelector("#download").disabled && document.querySelectorAll("#collectionPages [data-collection-page]").length > 0, 120000, `detail ${index}`);
  };
  const detailSnapshot = label => ({
    label,
    title: document.querySelector("#dTitle")?.textContent,
    activeId: window.activeArtworkId || null,
    pageCount: document.querySelectorAll("#collectionPages [data-collection-page]").length,
    checkedPages: [...document.querySelectorAll("#collectionPages [data-collection-page]")].map((box, i) => box.checked ? i : null).filter(i => i !== null),
    selectionCount: document.querySelector("#selectionCount")?.textContent,
  });
  const chooseOnly = pages => {
    [...document.querySelectorAll("#collectionPages [data-collection-page]")].forEach((box, i) => {
      const wanted = pages.includes(i);
      if (box.checked !== wanted) {
        box.checked = wanted;
        box.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  };

  await waitFor(() => document.readyState === "complete" && document.querySelector("#searchForm"), 20000, "initial UI");
  const initial = {
    title: document.title,
    ready: document.readyState,
    mode: document.querySelector("#mode")?.textContent,
    standalone: matchMedia("(display-mode: standalone)").matches,
    count: document.querySelector("#count")?.textContent,
  };

  await submit("猫耳", "all", false);
  const firstSearch = { count: document.querySelector("#count")?.textContent, cards: document.querySelectorAll("#grid .card").length };
  let multiIndex = -1;
  let firstProbe = null;
  for (let i = 0; i < Math.min(12, document.querySelectorAll("#grid .card").length); i += 1) {
    await openDetail(i);
    firstProbe = detailSnapshot(`first-probe-${i}`);
    if (firstProbe.pageCount > 1) {
      multiIndex = i;
      break;
    }
  }
  if (multiIndex < 0) throw new Error("no multi-page work in first 12 details");
  const firstBefore = detailSnapshot("first-before");
  chooseOnly(firstBefore.pageCount > 1 ? [0, 1] : [0]);
  const firstAfter = detailSnapshot("first-after");
  const firstTitle = firstAfter.title;

  await submit("初音ミク", "all", false);
  const secondSearch = { count: document.querySelector("#count")?.textContent, cards: document.querySelectorAll("#grid .card").length, firstSelectionStill: document.querySelector("#selectionCount")?.textContent };
  let secondIndex = -1;
  let secondProbe = null;
  for (let i = 0; i < Math.min(12, document.querySelectorAll("#grid .card").length); i += 1) {
    await openDetail(i);
    secondProbe = detailSnapshot(`second-probe-${i}`);
    if (secondProbe.pageCount > 1) {
      secondIndex = i;
      break;
    }
  }
  if (secondIndex < 0) throw new Error("no multi-page work in second first 12 details");
  const secondBefore = detailSnapshot("second-before");
  chooseOnly([0]);
  const secondAfter = detailSnapshot("second-after");
  const secondTitle = secondAfter.title;

  document.querySelector("#openBatch").click();
  await waitFor(() => !document.querySelector("#batchWorkspace").hidden && document.querySelectorAll("#batchCollections .batch-collection").length >= 2, 20000, "batch workspace");
  const batchInitial = {
    summary: document.querySelector("#batchSummary")?.textContent,
    rows: [...document.querySelectorAll("#batchCollections .batch-collection")].map(row => row.textContent.trim()),
  };

  const firstRow = [...document.querySelectorAll("#batchCollections .batch-collection")].find(row => row.textContent.includes(firstTitle));
  if (!firstRow) throw new Error("first selected collection missing after cross-search return");
  firstRow.click();
  await waitFor(() => !document.querySelector("#returnToBatch").hidden && document.querySelectorAll("#collectionPages [data-collection-page]").length > 0, 30000, "open first from batch");
  const firstReopened = detailSnapshot("first-reopened");
  document.querySelector("#returnToBatch").click();
  await waitFor(() => !document.querySelector("#batchWorkspace").hidden, 20000, "return batch 1");

  const secondRow = [...document.querySelectorAll("#batchCollections .batch-collection")].find(row => row.textContent.includes(secondTitle));
  if (!secondRow) throw new Error("second selected collection missing from batch");
  secondRow.click();
  await waitFor(() => !document.querySelector("#returnToBatch").hidden && document.querySelector("#dTitle")?.textContent === secondTitle, 30000, "open second from batch");
  const secondReopened = detailSnapshot("second-reopened");
  document.querySelector("#returnToBatch").click();
  await waitFor(() => !document.querySelector("#batchWorkspace").hidden, 20000, "return batch 2");

  const beforeDownloadRows = [...document.querySelectorAll("#batchCollections .batch-collection")].map(row => row.textContent.trim());
  document.querySelector("#batchDownload").click();
  await waitFor(() => !document.querySelector("#batchDownload").disabled && /已保存|批量下载失败/.test(document.querySelector("#toast")?.textContent || ""), 360000, "batch download");
  const toast = document.querySelector("#toast")?.textContent;
  if (!/^已保存\s+\d+\s+张图片/.test(toast || "")) throw new Error(`batch failed: ${toast}`);

  return {
    initial,
    firstSearch,
    firstBefore,
    firstAfter,
    secondSearch,
    secondBefore,
    secondAfter,
    batchInitial,
    firstReopened,
    secondReopened,
    beforeDownloadRows,
    toast,
    finalSelection: document.querySelector("#selectionCount")?.textContent,
  };
})()
