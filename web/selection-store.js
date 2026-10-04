"use strict";

// Own each choice as one record. Callers receive snapshots, never writable Sets.
globalThis.createSelectionStore = function ({maxPages = 1000, onChange = () => {}} = {}) {
  if (!Number.isSafeInteger(maxPages) || maxPages < 1) throw new Error("Invalid selection limit");
  const records = new Map();
  let pageCount = 0;
  let locked = false;
  const idOf = (item) => item?.id == null ? "" : String(item.id);
  const countOf = (item) => Number.isSafeInteger(Number(item?.pages)) && Number(item.pages) > 0 ? Number(item.pages) : null;
  const reject = (reason, additional = 0) => ({accepted: false, reason, additional});
  const snapshot = (row) => row ? {...row, pages: new Set(row.pages), context: {...row.context}} : null;

  function commit(id, item, pages, origin) {
    const previous = records.get(id);
    pageCount += pages.size - (previous?.pages.size || 0);
    if (!pages.size) { records.delete(id); return; }
    records.set(id, {
      id, item, pages,
      context: {...(previous?.context || origin?.context || {kind: "tags", value: "原创"})},
      resultPage: previous?.resultPage || origin?.resultPage || 1,
      archived: previous?.archived || false,
    });
  }

  function remove(ids, forced = false) {
    if (locked && !forced) return reject("locked");
    let count = 0;
    for (const raw of ids) {
      const id = String(raw), row = records.get(id);
      if (!row) continue;
      pageCount -= row.pages.size;
      records.delete(id);
      count += 1;
    }
    if (count) onChange();
    return {accepted: true, count};
  }

  return {
    get size() { return records.size; },
    get pageCount() { return pageCount; },
    get locked() { return locked; },
    get archivedCount() { return [...records.values()].filter(row => row.archived).length; },
    setLocked(value) { locked = Boolean(value); },
    has(id) { return records.has(String(id)); },
    get(id) { return snapshot(records.get(String(id))); },
    snapshot() { return [...records.values()].map(snapshot); },
    choose(items, origin, {fill = false} = {}) {
      if (locked) return reject("locked");
      const plan = new Map();
      for (const item of items) {
        const id = idOf(item), count = countOf(item);
        if (!id || count === null) return reject("pages");
        plan.set(id, {item, count});
      }
      let additional = 0;
      for (const [id, {count}] of plan) {
        const old = records.get(id)?.pages.size || 0;
        additional += (fill || !old ? count : old) - old;
      }
      if (pageCount + additional > maxPages) return reject("capacity", additional);
      for (const [id, {item, count}] of plan) {
        const old = records.get(id);
        const pages = !fill && old ? old.pages : new Set(Array.from({length: count}, (_, page) => page));
        commit(id, item, pages, origin);
      }
      if (plan.size) onChange();
      return {accepted: true, count: plan.size};
    },
    setPage(item, page, checked, origin) {
      if (locked) return reject("locked");
      const id = idOf(item), count = countOf(item);
      if (!id || count === null || !Number.isInteger(page) || page < 0 || page >= count) return reject("pages");
      const previous = records.get(id);
      if (checked && !previous?.pages.has(page) && pageCount >= maxPages) return reject("capacity", 1);
      const pages = new Set(previous?.pages);
      if (checked) pages.add(page); else pages.delete(page);
      commit(id, item, pages, origin);
      onChange();
      return {accepted: true};
    },
    remove(ids) { return remove(ids); },
    // Revocation must clear old-account choices even while a download is locked.
    revoke(ids) { return remove(ids, true); },
    clear() { return remove([...records.keys()]); },
    archive(ids) {
      if (locked) return reject("locked");
      let count = 0;
      for (const id of ids) {
        const row = records.get(String(id));
        if (row && !row.archived) { row.archived = true; count += 1; }
      }
      if (count) onChange();
      return {accepted: true, count};
    },
    remember(item) {
      const id = idOf(item), row = records.get(id), count = countOf(item);
      if (!row || count === null) return false;
      commit(id, item, new Set([...row.pages].filter(page => page < count)), row);
      onChange();
      return true;
    },
    restore(rows, {allowOverflow = false} = {}) {
      if (locked || !Array.isArray(rows)) return reject("locked");
      const plan = new Map();
      let total = 0;
      for (const row of rows) {
        const id = idOf(row.item), count = countOf(row.item);
        const pages = new Set(row.pages);
        if (!id || count === null || plan.has(id) || !pages.size || [...pages].some(page => !Number.isInteger(page) || page < 0 || page >= count)) return reject("pages");
        total += pages.size;
        plan.set(id, {...row, id, pages, context: {...row.context}});
      }
      if (total > maxPages && !allowOverflow) return reject("capacity");
      records.clear();
      for (const [id, row] of plan) records.set(id, row);
      pageCount = total;
      onChange();
      return {accepted: true};
    },
  };
};
