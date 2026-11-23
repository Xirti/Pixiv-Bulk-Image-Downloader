"use strict";

// Search work owns its requests, restore point and sorting/prefetch lifetimes.
globalThis.createSearchLifecycle = function ({cancelRequest, pages}) {
  let active = null;
  let sorting = null;
  let warming = null;
  let warmTimer = null;

  function cancelPrefetch(keepPage = null) {
    clearTimeout(warmTimer);
    warmTimer = null;
    pages.cancelWarm(keepPage);
    warming = null;
  }

  function prefetch({enabled, delay, ready, run}) {
    clearTimeout(warmTimer);
    warmTimer = null;
    if (!enabled || warming) return;
    const scheduled = setTimeout(async () => {
      if (warmTimer !== scheduled) return;
      warmTimer = null;
      if (!ready()) return;
      const task = {isCurrent: () => warming === task};
      warming = task;
      try {
        await run(task);
      } finally {
        if (warming === task) warming = null;
      }
    }, delay);
    warmTimer = scheduled;
  }

  function cancelSort() {
    if (!sorting) return false;
    const previous = sorting;
    sorting = null;
    previous.cancel();
    return true;
  }

  function beginSort(parentSignal) {
    cancelSort();
    parentSignal?.throwIfAborted();
    const controller = new AbortController();
    const abort = () => controller.abort(parentSignal.reason);
    parentSignal?.addEventListener("abort", abort, {once: true});
    const task = {
      signal: controller.signal,
      isCurrent: () => sorting?.task === task,
      finish() {
        parentSignal?.removeEventListener("abort", abort);
        if (!task.isCurrent()) return false;
        sorting = null;
        return true;
      },
    };
    sorting = {task, cancel() {
      parentSignal?.removeEventListener("abort", abort);
      controller.abort();
    }};
    return task;
  }

  function cancel(keepPage = null) {
    cancelPrefetch(keepPage);
    if (!active) return false;
    active.controller.abort();
    if (active.pageKey && active.pageKey !== keepPage) pages.cancel(active.pageKey);
    else if (!active.pageKey) void cancelRequest(active.requestId);
    return true;
  }

  function begin(previous, {requestId, pageKey = null}) {
    const restore = active?.task.previous || previous;
    cancel(pageKey);
    const controller = new AbortController();
    const entry = {controller, requestId, pageKey, task: null};
    const task = {
      previous: restore,
      signal: controller.signal,
      async read(id, read, adoptedPage = null) {
        controller.signal.throwIfAborted();
        entry.requestId = id;
        entry.pageKey = adoptedPage;
        const result = await read(controller.signal);
        controller.signal.throwIfAborted();
        return result;
      },
    };
    entry.task = task;
    active = entry;
    return task;
  }

  function finish(task) {
    if (active?.task !== task) return false;
    active = null;
    return true;
  }

  function reset() {
    const previous = active?.task.previous;
    cancelSort();
    cancel();
    active = null;
    return previous;
  }

  return {
    begin, cancel, finish, reset, beginSort, cancelSort, prefetch, cancelPrefetch,
    get active() { return active?.task || null; },
    get paging() { return active?.task.page != null ? active.task : sorting?.task; },
    get sorting() { return Boolean(sorting); },
  };
};
