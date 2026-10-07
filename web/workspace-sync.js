// Own local-record synchronization, including unacknowledged edits and account changes.
globalThis.createWorkspaceSync = function ({request, readBasket, applyWorkspace, canRestore, onStatus, onScopeChanged,
  getAuthorizationGeneration = () => null}) {
  const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const fields = ["context", "resultPage", "archived"];
  const itemFields = ["source", "title", "artist", "pages", "workType", "restriction", "tags"];
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const canonical = rows => rows.map(row => ({
    id: String(row.id), pages: [...row.pages].sort((a, b) => a - b),
    context: {...row.context}, resultPage: row.resultPage || 1, archived: Boolean(row.archived),
    item: {id: String(row.id), source: row.item.source || "pixiv", title: row.item.title, artist: row.item.artist,
      pages: row.item.pages, workType: row.item.workType, restriction: row.item.restriction, tags: row.item.tags},
  }));
  let ready = false, loading = false, needsRestore = false, initialized = false;
  let scope = null, revision = 0, epoch = 0, readGeneration = 0, sequence = 0;
  let applying = false, scheduled = false, recovery = false, error = null;
  let unacknowledgedBasket = new Map();
  let tail = Promise.resolve();
  let observed = new Map();
  const edits = new Map(), controllers = new Set();
  const notify = () => onStatus({ready, loading, needsRestore, scope, revision, error});

  async function send(url, options = {}) {
    const controller = new AbortController();
    controllers.add(controller);
    try { return await request(url, {...options, signal: controller.signal}); }
    finally { controllers.delete(controller); }
  }

  function captureEdits() {
    const current = new Map(canonical(readBasket()).map(row => [row.id, row]));
    for (const id of new Set([...observed.keys(), ...current.keys()])) {
      const before = observed.get(id), after = current.get(id);
      if (same(before, after)) continue;
      const delta = edits.get(id) || {pages: new Map(), fields: new Map(), item: new Map()};
      const number = ++sequence;
      if (!after) {
        for (const page of before.pages) delta.pages.set(page, {number, checked: false});
        delta.fields.clear();
        delta.item.clear();
      } else {
        delta.seed = copy(after);
        const oldPages = new Set(before?.pages || []), newPages = new Set(after.pages);
        for (const page of new Set([...oldPages, ...newPages])) {
          if (oldPages.has(page) !== newPages.has(page)) delta.pages.set(page, {number, checked: newPages.has(page)});
        }
        for (const field of fields) {
          if (!same(before?.[field], after[field])) {
            delta.fields.set(field, {number, value: copy(after[field])});
          }
        }
        for (const field of itemFields) {
          if (!same(before?.item[field], after.item[field])) {
            const previous = delta.item.get(field);
            delta.item.set(field, {number, value: copy(after.item[field]),
              before: previous ? previous.before : copy(before?.item[field])});
          }
        }
      }
      edits.set(id, delta);
    }
    observed = current;
  }

  function mergeEdits(rows) {
    const merged = new Map(canonical(rows).map(row => [row.id, row]));
    for (const [id, delta] of edits) {
      let row = merged.get(id);
      const pages = new Set(row?.pages || []);
      for (const [page, action] of delta.pages) {
        if (action.checked) pages.add(page); else pages.delete(page);
      }
      if (!pages.size) { merged.delete(id); continue; }
      const remoteItem = row?.item;
      row = copy(row || delta.seed);
      for (const [field, action] of delta.fields) row[field] = copy(action.value);
      for (const [field, action] of delta.item) {
        // A title refresh must not roll back another window's page count (or
        // restriction). Keep conflicting remote metadata, but apply our pages.
        if (remoteItem && !same(remoteItem[field], action.before) && !same(remoteItem[field], action.value)) {
          const sentItem = unacknowledgedBasket.get(id)?.item;
          if (!sentItem || !same(remoteItem[field], sentItem[field])) continue;
          // The previous request reached disk but its reply was lost. Newer
          // local edits can build on that sent value, including after retries.
          action.before = copy(remoteItem[field]);
        }
        row.item[field] = copy(action.value);
      }
      row.pages = [...pages].filter(page => page < row.item.pages).sort((a, b) => a - b);
      if (row.pages.length) merged.set(id, row); else merged.delete(id);
    }
    return [...merged.values()];
  }

  function acknowledge(number, basket) {
    const saved = new Map(basket.map(row => [row.id, row]));
    for (const [id, delta] of edits) {
      for (const actions of [delta.pages, delta.fields, delta.item]) {
        for (const [key, action] of actions) if (action.number <= number) actions.delete(key);
      }
      // Later edits were made against this successful save, not the older
      // snapshot. Rebase them so a retry does not mistake our own metadata
      // update for a conflicting change from another window.
      for (const [field, action] of delta.item) action.before = copy(saved.get(id)?.item[field]);
      if (!delta.pages.size && !delta.fields.size && !delta.item.size) edits.delete(id);
    }
  }

  function reset() {
    epoch++;
    readGeneration++;
    controllers.forEach(controller => controller.abort());
    controllers.clear();
    edits.clear();
    observed.clear();
    unacknowledgedBasket.clear();
    ready = false;
    loading = true;
    needsRestore = true;
    initialized = false;
    scope = null;
    revision = 0;
    error = null;
    recovery = false;
    scheduled = false;
    tail = Promise.resolve();
    notify();
  }

  function fail(reason, token) {
    if (token !== epoch) return;
    error = reason;
    recovery = !reason.basketCapacity;
    ready = Boolean(reason.basketCapacity);
    initialized = true;
    notify();
  }

  async function load(token) {
    if (!canRestore()) {
      needsRestore = true;
      loading = true;
      ready = false;
      notify();
      return false;
    }
    const generation = ++readGeneration, previousScope = scope;
    needsRestore = false;
    loading = true;
    ready = false;
    notify();
    try {
      const data = await send("/api/workspace");
      if (token !== epoch || generation !== readGeneration) return false;
      const authorizationGeneration = getAuthorizationGeneration();
      if ((previousScope !== null && data.scope !== previousScope)
        || (authorizationGeneration !== null && Number.isInteger(data.authorizationGeneration)
          && data.authorizationGeneration !== authorizationGeneration)) {
        reset();
        const changedEpoch = epoch;
        const rechecked = await onScopeChanged();
        if (changedEpoch !== epoch || ready) return false;
        if (rechecked) return load(changedEpoch);
        loading = false;
        fail(new Error("暂时无法确认 Pixiv 账户，请重试"), changedEpoch);
        return false;
      }
      const basket = mergeEdits(data.basket || []);
      applying = true;
      applyWorkspace({...data, basket});
      observed = new Map(canonical(readBasket()).map(row => [row.id, row]));
      unacknowledgedBasket.clear();
      scope = data.scope;
      revision = data.revision || 0;
      initialized = true;
      ready = true;
      recovery = false;
      error = null;
      return true;
    } catch (reason) {
      if (token === epoch && generation === readGeneration) fail(reason, token);
      return false;
    } finally {
      if (token === epoch && generation === readGeneration) {
        applying = false;
        loading = false;
        notify();
      }
    }
  }

  async function save(token) {
    try {
      if (recovery && !await load(token)) return;
      while (token === epoch && ready && edits.size) {
        const number = sequence;
        const basket = canonical(readBasket());
        unacknowledgedBasket = new Map(basket.map(row => [row.id, row]));
        const data = await send("/api/workspace/basket", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({basket, revision, scope})});
        if (token !== epoch) return;
        revision = data.revision;
        acknowledge(number, basket);
        unacknowledgedBasket.clear();
        error = null;
        notify();
      }
    } catch (reason) { fail(reason, token); }
  }

  function enqueue(operation) {
    const token = epoch;
    const result = tail.then(() => token === epoch ? operation(token) : false);
    tail = result.catch(reason => fail(reason, token));
    return tail;
  }

  function scheduleSave() {
    if (scheduled || loading || !initialized || !edits.size) return tail;
    scheduled = true;
    return enqueue(async token => {
      try { await save(token); }
      finally { if (token === epoch) scheduled = false; }
    });
  }

  function restore() {
    return enqueue(async token => {
      if (await load(token) && edits.size) await save(token);
    });
  }

  return {
    get ready() { return ready; }, get loading() { return loading; }, get scope() { return scope; },
    get needsRestore() { return needsRestore; },
    get active() { return initialized || loading || needsRestore || scope !== null || edits.size > 0; },
    changed() { if (!applying) { captureEdits(); return scheduleSave(); } return tail; },
    restore,
    retry() { return recovery || !initialized ? restore() : scheduleSave(); },
    reset,
    settled() { return tail; },
  };
};
