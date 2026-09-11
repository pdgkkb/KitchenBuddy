/* Happy Bite — persistence, two functions wide: read(key) / write(key, value).

   IndexedDB in the browser, falling back to memory when it's refused
   (private mode, sandboxed preview). Nothing else in the app touches
   IndexedDB, so swapping in SQLite-in-WASM later touches this file only.

   The household's data stays on this device. The server never stores it;
   it only sees what a single assistant request needs, for that request. */

const DB = "happybite";
const SHELF = "kv";
let db = null;
let inMemory = false;
const fallback = new Map();

function open() {
  return new Promise((ok, no) => {
    if (typeof indexedDB === "undefined") return no(new Error("no IndexedDB"));
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(SHELF);
    r.onsuccess = () => ok(r.result);
    r.onerror = () => no(r.error);
  });
}

async function ready() {
  if (db || inMemory) return;
  try { db = await open(); }
  catch (e) { inMemory = true; console.warn("Happy Bite: storage unavailable, session only.", e); }
}

const shelf = (mode) => db.transaction(SHELF, mode).objectStore(SHELF);

export async function read(key, fb = null) {
  await ready();
  if (inMemory) return fallback.has(key) ? fallback.get(key) : fb;
  return new Promise((ok) => {
    const r = shelf("readonly").get(key);
    r.onsuccess = () => ok(r.result === undefined ? fb : r.result);
    r.onerror = () => ok(fb);
  });
}

export async function write(key, value) {
  await ready();
  if (inMemory) { fallback.set(key, value); return value; }
  return new Promise((ok) => {
    const r = shelf("readwrite").put(value, key);
    r.onsuccess = () => ok(value);
    r.onerror = () => { console.warn("Happy Bite: write refused", key); ok(value); };
  });
}

export const isInMemory = () => inMemory;
