(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const waitFor = async (predicate, timeout = 60000, label = "condition") => {
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
  const setSelect = (selector, value) => {
    const el = document.querySelector(selector);
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const setCheckbox = (selector, checked) => {
    const el = document.querySelector(selector);
    el.checked = checked;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const submitSearch = async (tag, workType, includeAi) => {
    document.querySelector("#tag").value = tag;
    setSelect("#workType", workType);
    setCheckbox("#includeAi", includeAi);
    document.querySelector("#searchForm").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await waitFor(() => !document.querySelector("#searchForm button").disabled && !document.querySelector("#grid .loading-state"), 120000, `${workType}/${includeAi} search`);
    const cards = [...document.querySelectorAll("#grid .card")];
    return {
      requested: { tag, workType, includeAi },
      countText: document.querySelector("#count")?.textContent,
      cards: cards.length,
      visibleTitles: cards.slice(0, 5).map(card => card.querySelector("h3")?.textContent),
      error: document.querySelector("#grid .error-state")?.textContent || null,
    };
  };

  await waitFor(() => document.readyState === "complete" && document.querySelector("#searchForm"), 20000, "initial UI");
  const initial = {
    title: document.title,
    readyState: document.readyState,
    publicMode: document.querySelector("#mode")?.textContent,
    count: document.querySelector("#count")?.textContent,
    windowOuter: [outerWidth, outerHeight],
    windowInner: [innerWidth, innerHeight],
    standalone: matchMedia("(display-mode: standalone)").matches,
  };

  const filters = [];
  filters.push(await submitSearch("猫耳", "illustration", false));
  filters[filters.length - 1].domWorkType = document.querySelector("#workType").value;
  filters[filters.length - 1].domIncludeAi = document.querySelector("#includeAi").checked;

  filters.push(await submitSearch("猫耳", "manga", false));
  filters[filters.length - 1].domWorkType = document.querySelector("#workType").value;
  filters[filters.length - 1].domIncludeAi = document.querySelector("#includeAi").checked;

  filters.push(await submitSearch("猫耳", "ugoira", false));
  filters[filters.length - 1].domWorkType = document.querySelector("#workType").value;
  filters[filters.length - 1].domIncludeAi = document.querySelector("#includeAi").checked;

  filters.push(await submitSearch("猫耳", "all", true));
  filters[filters.length - 1].domWorkType = document.querySelector("#workType").value;
  filters[filters.length - 1].domIncludeAi = document.querySelector("#includeAi").checked;

  return {
    initial,
    filters,
    final: {
      count: document.querySelector("#count")?.textContent,
      cards: document.querySelectorAll("#grid .card").length,
      galleryVisible: getComputedStyle(document.querySelector("#grid")).visibility,
      selectionCount: document.querySelector("#selectionCount")?.textContent,
    },
  };
})()
