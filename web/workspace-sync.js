// Own local-record synchronization, including unacknowledged edits and account changes.
globalThis.createWorkspaceSync = function ({request, readBasket, applyWorkspace, canRestore, onStatus, onScopeChanged, onRecent = () => {},
  getAuthorizationGeneration = () => null}) {
  const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const fields = ["context", "resultPage", "archived"];
  const itemFields = ["source", "title", "artist", "pages", "workType", "restriction", "loginOnly", "tags"];
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const canonical = rows => rows.map(row => ({
    id: String(row.id), pages: [...row.pages].sort((a, b) => a - b),
    context: {...row.context}, resultPage: row.resultPage || 1, archived: Boolean(row.archived),
    item: {id: String(row.id), source: row.item.source || "pixiv", title: row.item.title, artist: row.item.artist,
      pages: row.item.pages, workType: row.item.workType, restriction: row.item.restriction,
      loginOnly: row.item.loginOnly === true || undefined, tags: row.item.tags},
  }));
  let ready = false, loading = false, needsRestore = false, initialized = false;
  let scope = null, revision = 0, epoch = 0, readGeneration = 0, sequence = 0;
  let applying = false, scheduled = false, recovery = false, error = null;
  let pendingBasket = null, recentError = null;
  let recentSequence = 0, recentWriter;
  const pendingRecent = [];
  let tail = Promise.resolve();
  let observed = new Map(), pageChecks = new Map();
  const edits = new Map(), controllers = new Set();
  const notify = () => onStatus({ready, loading, needsRestore, scope, revision, error: error || recentError, basketError:error, recentError});

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
          continue;
        }
        row.item[field] = copy(action.value);
      }
      if ([...pages].some(page => page >= row.item.pages)) {
        throw new Error("作品页数发生冲突，当前选择已保留。请打开作品核验详情后重试保存。");
      }
      row.pages = [...pages].sort((a, b) => a - b);
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
    pageChecks.clear();
    pendingBasket = null;
    recentError = null;
    pendingRecent.length = 0;
    recentWriter = null;
    recentSequence = 0;
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
      if ((previousScope !== null && data.scope !== previousScope && data.priorScope !== previousScope)
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
      if (pendingBasket !== null) {
        const original = pendingBasket;
        let committedRevision;
        try {
          committedRevision = await writeBasket(original, data.scope);
        } catch (reason) {
          if (!reason.basketConflict) throw reason;
        }
        if (token !== epoch || generation !== readGeneration) return false;
        pendingBasket = null;
        if (committedRevision !== undefined) acknowledge(original.number, original.basket);
        if (committedRevision > data.revision ||
            (committedRevision === undefined && original.revision === data.revision)) {
          return load(token);
        }
      }
      const basket = mergeEdits(data.basket || []);
      applying = true;
      pageChecks = new Map(Object.entries(data.basketPageChecks || {}));
      applyWorkspace({...data, basket});
      onRecent(data.recent || []);
      observed = new Map(canonical(readBasket()).map(row => [row.id, row]));
      if(previousScope !== null && previousScope === data.priorScope){
        for(const {body} of pendingRecent)if(body.scope === previousScope)body.scope = data.scope;
      }
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

  async function writeBasket({basket, revision, requestId}, owner) {
    const data = await send("/api/workspace/basket", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({basket, revision, requestId, scope: owner})});
    return data.revision;
  }

  async function save(token) {
    try {
      if (recovery && !await load(token)) return;
      while (token === epoch && ready && edits.size) {
        const number = sequence;
        const basket = canonical(readBasket());
        const requestId = [Date.now().toString(36), number.toString(36),
          Math.random().toString(36).slice(2).padEnd(14, "0")].join("-");
        pendingBasket = {basket, revision, number, requestId};
        const committedRevision = await writeBasket(pendingBasket, scope);
        if (token !== epoch) return;
        revision = committedRevision;
        acknowledge(number, basket);
        pendingBasket = null;
        error = null;
        notify();
      }
    } catch (reason) {
      if (token === epoch && (reason.basketCapacity || reason.basketConflict || reason.status === 400)) {
        pendingBasket = null;
      }
      fail(reason, token);
    }
  }

  function enqueue(operation, reportFailure = false) {
    const token = epoch;
    const result = tail.then(() => token === epoch ? operation(token) : false);
    tail = result.catch(reason => {
      if (reportFailure && token === epoch) { recentError = reason; notify(); }
      else fail(reason, token);
    });
    return reportFailure ? result : tail;
  }

  function changeRecent(change) {
    const authorization = getAuthorizationGeneration(), owner = scope;
    return enqueue(async token => {
      if (authorization !== getAuthorizationGeneration() || (owner !== null && scope !== owner))
        throw new Error("最近搜索尚未就绪或账号已改变，请重试");
      recentWriter ||= globalThis.crypto?.randomUUID?.() || [Date.now().toString(36), Math.random().toString(36).slice(2).padEnd(14,"0")].join("-");
      pendingRecent.push({authorization, body:{...copy(change), scope, writer:recentWriter, sequence:++recentSequence}});
      await flushRecent(token);
    }, true);
  }

  async function flushRecent(token) {
    while (token === epoch && pendingRecent.length) {
      const {authorization, body} = pendingRecent[0];
      if (authorization !== getAuthorizationGeneration()) throw new Error("最近搜索的账号已改变，请重新读取");
      if (scope === null) throw new Error("最近搜索尚未就绪，请重新读取");
      body.scope ??= scope;
      if (body.scope !== scope) throw new Error("最近搜索的账号已改变，请重新读取");
      const data = await send("/api/workspace/recent", {method: "POST",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (token !== epoch || authorization !== getAuthorizationGeneration()) return;
      pendingRecent.shift();
      onRecent(data.recent);
    }
    if (token === epoch) { recentError = null; notify(); }
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

  function retry() {
    const basket = recovery || !initialized ? restore() : scheduleSave();
    if (!recentError && !pendingRecent.length) return basket;
    return enqueue(async token => { if (ready) await flushRecent(token); }, true).catch(()=>{});
  }

  return {
    get ready() { return ready; }, get loading() { return loading; }, get scope() { return scope; },
    get needsRestore() { return needsRestore; },
    get active() { return initialized || loading || needsRestore || scope !== null || edits.size > 0; },
    changed() { if (!applying) { captureEdits(); return scheduleSave(); } return tail; },
    pageCheck: id => pageChecks.get(String(id)),
    confirmPages(id, check, pages) {
      return enqueue(async token => {
        if (!ready || pageChecks.get(id) !== check || !observed.has(id)) return;
        await send("/api/workspace/basket/confirm-pages", {method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify({scope, id, token:check.token, pages})});
        if (token !== epoch) return;
        // Removing the work while its detail was being confirmed also removes its disputed pages.
        const removed = !observed.has(id) && edits.get(id);
        if (removed) for (const page of check.pages) removed.pages.set(page, {number: sequence, checked:false});
        if (await load(token) && edits.size) await save(token);
      });
    },
    restore, changeRecent, retry,
    reset,
    settled() { return tail; },
    async waitForSave({retryOnce=false,requireRead=true}={}) {
      let current;
      let retried=false;
      do {
        current = tail;
        await current;
        if (current===tail && retryOnce && !retried && ((edits.size && !error?.basketCapacity) || pendingRecent.length)) {
          retried=true;
          await retry();
        }
      } while (current !== tail);
      const unsaved=[];
      if (recentError || pendingRecent.length) unsaved.push("最近搜索");
      if (edits.size || pendingBasket !== null) unsaved.push("采集篮");
      if (unsaved.length || requireRead && error) {
        // Strict callers also use the original cause to report or test a failed save.
        if (requireRead) throw error || recentError || new Error("本机记录尚未保存");
        throw new Error(`未能确认${unsaved.join("和")}已保存到本机。`, {cause:error || recentError});
      }
    },
  };
};
