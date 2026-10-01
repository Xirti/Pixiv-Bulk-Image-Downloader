"use strict";

// Owns every request, decoded frame and playback timer for hover previews.
globalThis.createUgoiraPreview = function ({ fetchJson, fetchBytes, onError, cancelRequest = () => {}, hoverDelay = 0 }) {
  const cache = new Map();
  const maxBytes = 96 * 1024 * 1024;
  let cachedBytes = 0;
  let active = null;
  let decompressionFormat;

  function parseZipEntries(buffer) {
    const view = new DataView(buffer);
    let eocd = -1;
    for (let index = view.byteLength - 22; index >= 0; index -= 1) {
      if (view.getUint32(index, true) === 0x06054b50) { eocd = index; break; }
    }
    if (eocd < 0) throw new Error("动图数据已损坏");
    const count = view.getUint16(eocd + 10, true);
    const decoder = new TextDecoder();
    let offset = view.getUint32(eocd + 16, true);
    const entries = new Map();
    for (let index = 0; index < count; index += 1) {
      if (view.getUint32(offset, true) !== 0x02014b50) throw new Error("动图数据已损坏");
      const method = view.getUint16(offset + 10, true);
      const compressedSize = view.getUint32(offset + 20, true);
      const rawSize = view.getUint32(offset + 24, true);
      if (rawSize > 16 * 1024 * 1024 || compressedSize > 16 * 1024 * 1024) {
        throw new Error("动图单帧过大，无法预览");
      }
      const nameLength = view.getUint16(offset + 28, true);
      const extraLength = view.getUint16(offset + 30, true);
      const commentLength = view.getUint16(offset + 32, true);
      const localOffset = view.getUint32(offset + 42, true);
      const crc32 = view.getUint32(offset + 16, true);
      entries.set(decoder.decode(new Uint8Array(buffer, offset + 46, nameLength)), { method, compressedSize, rawSize, crc32, localOffset });
      offset += 46 + nameLength + extraLength + commentLength;
    }
    return entries;
  }

  async function inflateZipEntry(buffer, entry) {
    const view = new DataView(buffer);
    if (view.getUint32(entry.localOffset, true) !== 0x04034b50) throw new Error("动图数据已损坏");
    const nameLength = view.getUint16(entry.localOffset + 26, true);
    const extraLength = view.getUint16(entry.localOffset + 28, true);
    const start = entry.localOffset + 30 + nameLength + extraLength;
    const compressed = buffer.slice(start, start + entry.compressedSize);
    if (entry.method === 0) return compressed;
    if (entry.method === 8) {
      if (typeof DecompressionStream !== "function") throw new Error("当前 WebView 不支持动图预览");
      let inflater;
      if (decompressionFormat !== "gzip") {
        try {
          inflater = new DecompressionStream("deflate-raw");
          decompressionFormat = "deflate-raw";
        } catch { decompressionFormat = "gzip"; }
      }
      let payload = compressed;
      if (decompressionFormat === "gzip") {
        // Older WebView2 hosts support gzip but not raw DEFLATE. Both wrap
        // the same compressed bytes; ZIP already supplies gzip's CRC and size.
        const wrapped = new Uint8Array(compressed.byteLength + 18);
        wrapped.set([0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 255]);
        wrapped.set(new Uint8Array(compressed), 10);
        const trailer = new DataView(wrapped.buffer);
        trailer.setUint32(wrapped.byteLength - 8, entry.crc32, true);
        trailer.setUint32(wrapped.byteLength - 4, entry.rawSize, true);
        payload = wrapped;
        inflater = new DecompressionStream("gzip");
      }
      const stream = new Blob([payload]).stream().pipeThrough(inflater);
      const reader = stream.getReader();
      const chunks = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 16 * 1024 * 1024) throw new Error("动图单帧过大，无法预览");
          chunks.push(value);
        }
        return await new Blob(chunks).arrayBuffer();
      } finally {
        await reader.cancel();
        reader.releaseLock();
      }
    }
    throw new Error("动图数据使用了不支持的压缩方式");
  }

  function release(entry) {
    entry.frames.forEach(({ bitmap }) => bitmap.close());
  }

  function evictOldest() {
    const [id, entry] = cache.entries().next().value;
    cache.delete(id);
    cachedBytes -= entry.bytes;
    release(entry);
  }

  async function load(id, signal, showFrames, requestId) {
    if (cache.has(id)) {
      const entry = cache.get(id);
      cache.delete(id);
      cache.set(id, entry);
      showFrames(entry);
      return entry.frames;
    }
    const entry = { frames: [], bytes: 0, complete: false };
    try {
      signal.throwIfAborted();
      const query = `requestId=${encodeURIComponent(requestId)}`;
      const meta = await fetchJson(`/api/pixiv/ugoira/${id}?${query}`, { signal });
      signal.throwIfAborted();
      if (!Array.isArray(meta.frames) || !meta.frames.length || meta.frames.length > 1500) {
        throw new Error("动图帧数超出预览范围，仍可下载原始 ZIP");
      }
      const buffer = await fetchBytes(`/api/pixiv/ugoira/${id}?mode=zip&${query}`, { signal });
      signal.throwIfAborted();
      const entries = parseZipEntries(buffer);
      const width = Number(meta.width);
      const height = Number(meta.height);
      const scale = Math.min(
        1, 480 / width, 480 / height,
        Math.sqrt(maxBytes / (meta.frames.length * width * height * 4)),
      );
      const resize = width > 0 && height > 0 && Number.isFinite(scale)
        ? { resizeWidth: Math.max(1, Math.floor(width * scale)), resizeHeight: Math.max(1, Math.floor(height * scale)) }
        : {};
      for (const frame of meta.frames) {
        signal.throwIfAborted();
        const zipped = entries.get(frame.file);
        if (!zipped) throw new Error("动图缺少图片帧");
        const raw = await inflateZipEntry(buffer, zipped);
        signal.throwIfAborted();
        const bitmap = await createImageBitmap(new Blob([raw], { type: meta.mime || "image/jpeg" }), resize);
        if (signal.aborted) {
          bitmap.close();
          signal.throwIfAborted();
        }
        entry.frames.push({ bitmap, delay: Math.max(16, Number(frame.delay) || 100) });
        entry.bytes += bitmap.width * bitmap.height * 4;
        if (entry.bytes > maxBytes) throw new Error("动图超过预览内存上限，仍可下载原始 ZIP");
        while (cache.size && cachedBytes + entry.bytes > maxBytes) evictOldest();
        showFrames(entry);
      }
      signal.throwIfAborted();
      entry.complete = true;
      showFrames(entry);
      while (cache.size >= 6) evictOldest();
      cache.set(id, entry);
      cachedBytes += entry.bytes;
      return entry.frames;
    } catch (error) {
      release(entry);
      throw error;
    }
  }

  function stop(host) {
    if (host) delete host.dataset.ugoiraState;
    if (!active || (host && active.host !== host)) return;
    const player = active;
    active = null;
    player.controller.abort();
    if (player.loading) {
      Promise.resolve().then(() => cancelRequest(player.requestId)).catch(() => {});
    }
    clearTimeout(player.timer);
    player.canvas?.remove();
    delete player.host.dataset.ugoiraState;
  }

  async function start(host, id) {
    stop();
    const requestId = `ugoira_${globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(36).slice(2)}`}`;
    const player = { host, requestId, loading: false, controller: new AbortController(), canvas: null, timer: null };
    active = player;
    try {
      if (hoverDelay > 0 && !cache.has(id)) {
        await new Promise((resolve) => {
          const finish = () => {
            clearTimeout(player.timer);
            player.controller.signal.removeEventListener("abort", finish);
            resolve();
          };
          player.controller.signal.addEventListener("abort", finish, {once: true});
          player.timer = setTimeout(finish, hoverDelay);
        });
        if (active !== player) return;
      }
      host.dataset.ugoiraState = "loading";
      player.loading = !cache.has(id);
      const showFrames = (entry) => {
        if (active !== player) return;
        if (host.isConnected === false) { stop(); return; }
        if (player.canvas) {
          if (player.waiting) { player.waiting = false; player.draw(); }
          return;
        }
        const canvas = document.createElement("canvas");
        canvas.className = "ugoira-preview";
        player.canvas = canvas;
        const ratio = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
        canvas.width = Math.max(1, Math.round(host.clientWidth * ratio));
        canvas.height = Math.max(1, Math.round(host.clientHeight * ratio));
        const context = canvas.getContext("2d");
        if (!context) throw new Error("无法创建动图预览画布");
        host.appendChild(canvas);
        host.dataset.ugoiraState = "playing";
        let index = 0;
        const draw = () => {
          if (active !== player) return;
          if (host.isConnected === false) { stop(); return; }
          if (index >= entry.frames.length) {
            if (entry.complete) index = 0;
            else { player.waiting = true; return; }
          }
          const { bitmap, delay } = entry.frames[index];
          const scale = Math.max(canvas.width / bitmap.width, canvas.height / bitmap.height);
          const width = bitmap.width * scale;
          const height = bitmap.height * scale;
          context.clearRect(0, 0, canvas.width, canvas.height);
          context.drawImage(bitmap, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
          player.timer = setTimeout(() => { index += 1; draw(); }, delay);
        };
        player.draw = draw;
        draw();
      };
      await load(id, player.controller.signal, showFrames, requestId);
      player.loading = false;
    } catch (error) {
      if (active === player) {
        stop();
        host.dataset.ugoiraState = "error";
        onError(error.message || "动图预览加载失败");
      }
    }
  }

  function clear() {
    stop();
    cache.forEach(release);
    cache.clear();
    cachedBytes = 0;
  }

  return { start, stop, clear };
};
