"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { deflateRawSync } = require("node:zlib");
require("../web/ugoira-preview.js");

function archive(names, compressed = false) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const name of names) {
    const bytes = Buffer.from(name);
    const payload = compressed ? deflateRawSync(Buffer.from([1])) : Buffer.from([1]);
    const local = Buffer.alloc(30 + bytes.length + payload.length);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt32LE(0xa505df1b, 14); // CRC32 of the one-byte frame fixture.
    local.writeUInt16LE(bytes.length, 26);
    bytes.copy(local, 30);
    payload.copy(local, 30 + bytes.length);
    const entry = Buffer.alloc(46 + bytes.length);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(compressed ? 8 : 0, 10);
    entry.writeUInt32LE(0xa505df1b, 16);
    entry.writeUInt32LE(payload.length, 20);
    entry.writeUInt32LE(1, 24);
    entry.writeUInt16LE(bytes.length, 28);
    entry.writeUInt32LE(offset, 42);
    bytes.copy(entry, 46);
    locals.push(local);
    central.push(entry);
    offset += local.length;
  }
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(names.length, 10);
  end.writeUInt32LE(offset, 16);
  const zip = Buffer.concat([...locals, ...central, end]);
  return zip.buffer.slice(zip.byteOffset, zip.byteOffset + zip.byteLength);
}

function setup({ count = 2, fetchBytes, decode, hoverDelay = 0 } = {}) {
  const names = Array.from({ length: count }, (_, i) => `${i}.jpg`);
  const bitmaps = [];
  const errors = [];
  const options = [];
  const requests = [];
  const cancelled = [];
  const host = {
    isConnected: true, dataset: {}, clientWidth: 200, clientHeight: 300, appended: 0, attached: 0,
    appendChild() { this.appended += 1; this.attached += 1; },
  };
  globalThis.window = { devicePixelRatio: 1 };
  globalThis.document = {
    createElement: () => ({
      remove() { host.attached -= 1; },
      getContext: () => ({ clearRect() {}, drawImage: (bitmap) => assert.equal(bitmap.closed, 0) }),
    }),
  };
  const bitmap = (width = 480, height = 240) => {
    const value = { width, height, closed: 0, close() { this.closed += 1; } };
    bitmaps.push(value);
    return value;
  };
  globalThis.createImageBitmap = async (blob, resize) => {
    options.push(resize);
    return decode ? decode(bitmap) : bitmap();
  };
  const preview = createUgoiraPreview({
    hoverDelay,
    cancelRequest: id => cancelled.push(id),
    fetchJson: async (url) => { requests.push(url); return { width: 4096, height: 2048, frames: names.map(file => ({ file, delay: 60000 })) }; },
    fetchBytes: fetchBytes || (async () => archive(names)),
    onError: message => errors.push(message),
  });
  return { preview, host, bitmaps, errors, options, requests, cancelled, zip: archive(names) };
}

test("brief hover cancels the dwell without requesting or decoding an archive", async () => {
  let requests = 0;
  const state = setup({hoverDelay: 180, fetchBytes: async () => { requests += 1; return archive(["0.jpg"]); }});
  const pending = state.preview.start(state.host, "1");
  state.preview.stop();
  await pending;
  assert.equal(state.requests.length, 0);
  assert.equal(requests, 0);
  assert.equal(state.bitmaps.length, 0);
  assert.equal(state.host.appended, 0);
  assert.deepEqual(state.errors, []);
});

test("sustained hover starts loading only after the dwell completes", async () => {
  const state = setup({hoverDelay: 180});
  const originalTimer = globalThis.setTimeout;
  let finishDwell;
  globalThis.setTimeout = (callback, delay) => {
    if (delay === 180) finishDwell = callback;
    return 0;
  };
  try {
    const pending = state.preview.start(state.host, "1");
    assert.equal(state.requests.length, 0);
    assert.equal(state.host.appended, 0);
    finishDwell();
    await pending;
    assert.equal(state.requests.length, 1);
    assert.equal(state.host.appended, 1);
  } finally {
    state.preview.clear();
    globalThis.setTimeout = originalTimer;
  }
});

test("a cached animation plays immediately without a second hover dwell", async () => {
  const state = setup({count: 1, hoverDelay: 180});
  const nativeSetTimeout = globalThis.setTimeout;
  let finishDwell, dwells = 0;
  globalThis.setTimeout = (callback, delay) => {
    if (delay === 180) { dwells += 1; finishDwell = callback; }
    return 0;
  };
  try {
    const first = state.preview.start(state.host, "1");
    finishDwell();
    await first;
    state.preview.stop(state.host);
    const second = state.preview.start(state.host, "1");
    assert.equal(state.host.attached, 1, "the cached preview waited for the hover delay");
    assert.equal(dwells, 1);
    await second;
    assert.equal(state.requests.length, 1);
  } finally {
    state.preview.clear();
    globalThis.setTimeout = nativeSetTimeout;
  }
});

test("stopping cancels an in-flight archive and prevents decoding", async () => {
  let resolve, signal;
  const state = setup({ fetchBytes: (_url, options) => {
    signal = options.signal;
    return new Promise(done => { resolve = done; });
  } });
  const pending = state.preview.start(state.host, "1");
  await Promise.resolve();
  state.preview.stop();
  assert.equal(signal.aborted, true);
  resolve(state.zip);
  await pending;
  assert.equal(state.bitmaps.length, 0);
  assert.deepEqual(state.errors, []);
});

test("leaving an in-flight preview also cancels its matching backend request", async () => {
  let finish, zipUrl;
  const state = setup({fetchBytes: url => {
    zipUrl = url;
    return new Promise(resolve => { finish = () => resolve(state.zip); });
  }});
  const pending = state.preview.start(state.host, "1");
  for (let i = 0; i < 10 && !finish; i += 1) await Promise.resolve();
  assert.ok(finish);
  state.preview.stop(state.host);
  finish();
  await pending;
  assert.equal(state.cancelled.length, 1, "leaving stopped only the browser, not its backend request");
  const id = new URL(zipUrl, "http://localhost").searchParams.get("requestId");
  assert.equal(state.cancelled[0], id);
  assert.equal(new URL(state.requests[0], "http://localhost").searchParams.get("requestId"), id);
  assert.match(id, /^[A-Za-z0-9_-]{16,128}$/);
  assert.deepEqual(state.errors, []);
});

test("a partial decode failure closes every completed bitmap", async () => {
  let count = 0;
  const state = setup({ decode: bitmap => {
    if (++count === 2) throw new Error("broken frame");
    return bitmap();
  } });
  await state.preview.start(state.host, "1");
  assert.equal(state.bitmaps[0].closed, 1);
  assert.deepEqual(state.errors, ["broken frame"]);
  state.preview.clear();
  assert.equal(state.bitmaps[0].closed, 1);
});

test("leaving a failed preview clears its hover-only error state", async () => {
  const state = setup({fetchBytes: async () => { throw new Error("offline"); }});
  await state.preview.start(state.host, "1");
  assert.equal(state.host.dataset.ugoiraState, "error");
  state.preview.stop(state.host);
  assert.equal(state.host.dataset.ugoiraState, undefined);
  assert.deepEqual(state.errors, ["offline"]);
});

test("a host detached on the final frame does not retain an aborted cache entry", async () => {
  const state = setup({count: 1});
  state.host.isConnected = false;
  try {
    await state.preview.start(state.host, "1");
    assert.equal(state.bitmaps[0].closed, 1, "the cancelled last frame remained cached");
    assert.equal(state.host.attached, 0);
    state.host.isConnected = true;
    await state.preview.start(state.host, "1");
    assert.equal(state.requests.length, 2, "a cancelled decode was reused instead of reloaded");
    assert.equal(state.host.attached, 1);
    assert.deepEqual(state.errors, []);
  } finally { state.preview.clear(); }
});

test("the first frame is visible while later frames are still decoding", async () => {
  let count = 0, finish;
  const state = setup({decode: bitmap => ++count === 1
    ? bitmap()
    : new Promise(resolve => { finish = () => resolve(bitmap()); })});
  try {
    const pending = state.preview.start(state.host, "1");
    for (let i = 0; i < 20 && !finish; i += 1) await Promise.resolve();
    assert.ok(finish, "the second frame was not reached");
    assert.equal(state.host.appended, 1, "preview stayed invisible until every frame was decoded");
    assert.equal(state.host.dataset.ugoiraState, "playing");
    finish();
    await pending;
  } finally { state.preview.clear(); }
});

test("playback waits for an undecoded frame instead of looping a partial animation", async () => {
  let count = 0, finish;
  const state = setup({count: 3, decode: bitmap => ++count < 3
    ? bitmap() : new Promise(resolve => { finish = () => resolve(bitmap()); })});
  const nativeSetTimeout = globalThis.setTimeout;
  const timers = [], drawn = [];
  globalThis.setTimeout = callback => { timers.push(callback); return 0; };
  globalThis.document.createElement = () => ({
    remove() { state.host.attached -= 1; },
    getContext: () => ({clearRect() {}, drawImage(bitmap) {
      assert.equal(bitmap.closed, 0);
      drawn.push(state.bitmaps.indexOf(bitmap));
    }}),
  });
  try {
    const pending = state.preview.start(state.host, "1");
    for (let i = 0; i < 30 && !finish; i += 1) await Promise.resolve();
    assert.ok(finish);
    timers.shift()();
    timers.shift()();
    assert.deepEqual(drawn, [0, 1], "an incomplete prefix was replayed as a full loop");
    finish();
    await pending;
    assert.deepEqual(drawn, [0, 1, 2]);
    timers.shift()();
    assert.deepEqual(drawn, [0, 1, 2, 0]);
  } finally {
    state.preview.clear();
    globalThis.setTimeout = nativeSetTimeout;
  }
});

test("clearing during decode closes the late bitmap without attaching a canvas", async () => {
  let finish;
  const state = setup({ decode: bitmap => new Promise(resolve => { finish = () => resolve(bitmap()); }) });
  const pending = state.preview.start(state.host, "1");
  for (let i = 0; i < 10 && !finish; i += 1) await Promise.resolve();
  assert.ok(finish);
  state.preview.clear();
  finish();
  await pending;
  assert.equal(state.bitmaps[0].closed, 1);
  assert.equal(state.host.appended, 0);
  assert.deepEqual(state.errors, []);
});

test("cache reuse, resized decoding and clear share one resource owner", async () => {
  const state = setup();
  try {
    await state.preview.start(state.host, "1");
    state.preview.stop();
    await state.preview.start(state.host, "1");
    assert.equal(state.bitmaps.length, 2);
    assert.deepEqual(state.options[0], { resizeWidth: 480, resizeHeight: 240 });
    state.preview.clear();
    assert.ok(state.bitmaps.every(bitmap => bitmap.closed === 1));
    await state.preview.start(state.host, "1");
    assert.equal(state.bitmaps.length, 4);
  } finally { state.preview.clear(); }
  assert.ok(state.bitmaps.every(bitmap => bitmap.closed === 1));
});

test("cache eviction closes old frames but never active frames", async () => {
  const state = setup({ count: 1 });
  try {
    for (let i = 0; i < 7; i += 1) await state.preview.start(state.host, String(i));
    assert.equal(state.bitmaps[0].closed, 1);
    assert.equal(state.bitmaps[6].closed, 0);
  } finally { state.preview.clear(); }
  assert.ok(state.bitmaps.every(bitmap => bitmap.closed === 1));
});

test("decoded memory budget rejects oversized previews and frees their frames", async () => {
  const state = setup({ decode: bitmap => bitmap(4096, 4096) });
  await state.preview.start(state.host, "1");
  assert.equal(state.errors.length, 1);
  assert.match(state.errors[0], /内存上限/);
  assert.ok(state.bitmaps.every(bitmap => bitmap.closed === 1));
  assert.equal(state.host.attached, 0, "a partially loaded oversized preview was left visible");
});

test("deflated frames are decompressed before decoding", async () => {
  const state = setup({ fetchBytes: async () => archive(["0.jpg", "1.jpg"], true) });
  try {
    await state.preview.start(state.host, "1");
    assert.deepEqual(state.errors, []);
    assert.equal(state.bitmaps.length, 2);
  } finally { state.preview.clear(); }
});

test("hosts without raw-deflate decode the same ZIP through a gzip wrapper", async () => {
  const nativeDecompressionStream = globalThis.DecompressionStream;
  const formats = [];
  globalThis.DecompressionStream = class {
    constructor(format) {
      formats.push(format);
      if (format === "deflate-raw") throw new TypeError("unsupported format");
      return new nativeDecompressionStream(format);
    }
  };
  const urls = [];
  const state = setup({fetchBytes: async (url) => {
    urls.push(url);
    return archive(["0.jpg", "1.jpg"], true);
  }});
  try {
    await state.preview.start(state.host, "1");
    assert.deepEqual(state.errors, [], "the older WebView could not decode a valid compressed ZIP");
    assert.equal(state.host.attached, 1);
    assert.equal(urls.length, 1);
    assert.deepEqual(formats, ["deflate-raw", "gzip", "gzip"]);
  } finally {
    state.preview.clear();
    globalThis.DecompressionStream = nativeDecompressionStream;
  }
});

test("transparent animation frames do not retain pixels from earlier frames", async () => {
  const state = setup();
  let pixel = "transparent";
  let advance;
  const nativeSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = callback => { advance = callback; return 0; };
  globalThis.document.createElement = () => ({
    remove() {},
    getContext: () => ({
      clearRect() { pixel = "transparent"; },
      drawImage(bitmap) {
        if (bitmap === state.bitmaps[0]) pixel = "red";
        // The second bitmap is transparent and leaves existing pixels untouched.
      },
    }),
  });
  try {
    await state.preview.start(state.host, "1");
    assert.equal(pixel, "red");
    advance();
    assert.equal(pixel, "transparent");
  } finally {
    state.preview.clear();
    globalThis.setTimeout = nativeSetTimeout;
  }
});
