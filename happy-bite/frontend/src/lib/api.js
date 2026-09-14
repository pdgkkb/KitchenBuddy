/* Happy Bite — the server, from the browser's side.

   Every call here is optional. When the server is off, `status()` says so
  and the interface falls back for non-model features to the browser's own
  voice. Nothing in the kitchen core imports this file. */

const BASE = import.meta.env?.VITE_API_BASE || "";

export const OFF = { online: false, chat: false, recipes: false, images: false, voice: false, model: null, local: false };

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function call(path, init = {}, timeout = 90000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const res = await fetch(BASE + path, { ...init, signal: init.signal || ctl.signal });
    if (!res.ok) {
      let msg = `The server answered ${res.status}.`;
      try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : msg; } catch {}
      throw new ApiError(msg, res.status);
    }
    return res;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(e.name === "AbortError" ? "The server took too long." : "The server can't be reached.", 0);
  } finally {
    clearTimeout(timer);
  }
}

const post = (path, body, timeout) => call(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
}, timeout).then(r => r.json());

export async function status() {
  try {
    const s = await (await call("/api/status", {}, 3000)).json();
    return { ...s, online: true };
  } catch { return OFF; }
}

export const generateRecipe = (body) => post("/api/recipes/generate", body, 120000);
export const importLink = (body) => post("/api/recipes/import", body, 60000);
export const understand = (text) => post("/api/understand", { text }, 20000);
export const makeImage = (body) => post("/api/images", body, 120000);

export const makeRecipeImages = (recipe) => post("/api/recipes/images", {
  id: recipe.id, name: recipe.name, cuisine: recipe.cuisine,
  description: recipe.description, count: 1
});
export const recipeImages = (id) => call(`/api/recipes/images/${encodeURIComponent(id)}`, {}, 10000).then(r => r.json());
export const clearRecipeImages = (id) => call(`/api/recipes/images/${encodeURIComponent(id)}`, { method: "DELETE" }, 10000).then(r => r.json());

export async function say(text) {
  const res = await call("/api/speech/say", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text })
  }, 30000);
  return res.blob();
}

export async function hear(blob) {
  const form = new FormData();
  form.append("audio", blob, blob.type.includes("mp4") ? "speech.mp4" : "speech.webm");
  return (await call("/api/speech/hear", { method: "POST", body: form }, 30000)).json();
}

/* The chat streams as server-sent events over a POST, so EventSource is
   no use; the body is read by hand. `onEvent` gets each parsed event:
   {type: "delta"|"attachment"|"status"|"error"|"done", ...}. */
export async function chat(body, onEvent, signal) {
  const res = await call("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal
  }, 180000);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try { onEvent(JSON.parse(line.slice(6))); } catch { /* a torn line; skip */ }
        }
      }
    }
  }
}
