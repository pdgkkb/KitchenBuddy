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

/* Server-sent events over a POST, read by hand — EventSource can't POST. Shared
   by the chat and by recipe writing, which is the only reason the recipe wait
   can narrate itself honestly rather than guessing on a stopwatch. */
export async function readEvents(res, onEvent) {
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

/* The same request as generateRecipe, but the server says where it has got to.
   `onStage` is called with the key of each phase as it is actually reached —
   see STAGES_IDEAS / STAGES_METHOD in backend/app/api.py. Resolves with the
   same body the plain endpoint returns, so callers can fall back to it.

   One of the keys is OPTIONAL: "template" only arrives when the corpus turned
   out to have a recipe this kitchen can already cook, in which case "method"
   never arrives at all. Anything drawing a list of stages has to treat the two
   as alternatives rather than as a sequence. */
export async function generateRecipeStream(body, onStage, signal) {
  const res = await call("/api/recipes/generate/stream", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal
  }, 300000);
  let result = null;
  let failed = null;
  await readEvents(res, (ev) => {
    if (ev.type === "stage") onStage?.(ev.key);
    else if (ev.type === "result") result = ev;
    else if (ev.type === "error") failed = ev.message;
  });
  if (failed) throw new ApiError(failed, 502);
  if (!result) throw new ApiError("The assistant stopped without an answer.", 502);
  return result;
}

export const adaptRecipe = (body) => post("/api/recipes/adapt", body, 180000);
export const importLink = (body) => post("/api/recipes/import", body, 60000);
export const understand = (text) => post("/api/understand", { text }, 20000);
export const makeImage = (body) => post("/api/images", body, 120000);

export const makeRecipeImages = (recipe) => post("/api/recipes/images", {
  id: recipe.id, name: recipe.name, cuisine: recipe.cuisine,
  description: recipe.description, count: 1
});
export const recipeImages = (id) => call(`/api/recipes/images/${encodeURIComponent(id)}`, {}, 10000).then(r => r.json());
export const clearRecipeImages = (id) => call(`/api/recipes/images/${encodeURIComponent(id)}`, { method: "DELETE" }, 10000).then(r => r.json());

/* ---- Cooking: answers written while you chop ----------------------------

   backend/app/prefetch.py has been complete since it was written, main.py has
   always constructed it and api_extra.py has always exposed it — and nothing
   in the browser ever called either endpoint, so every question at the hob
   took the full model round trip while the machine sat idle between steps.
   These two functions are the missing wire.

   Both fail SILENTLY, on purpose. A prefetch that doesn't happen is a slower
   answer, never an error worth putting on a screen in front of someone
   holding a hot pan. */

export const prefetchStep = (recipe, step, serves) =>
  post("/api/cook/prefetch", { recipe, step, serves }, 8000).catch(() => null);

/* The parked answer for what they just said, or null to ask the model.
   204 means "nothing parked that matches" — which is the common case and
   must not read as a failure. */
export async function quickAnswer(recipeId, step, question) {
  try {
    const res = await call("/api/cook/quick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipeId, step, question })
    }, 6000);
    return res.status === 204 ? null : await res.json();
  } catch { return null; }
}

export async function say(text) {
  const res = await call("/api/speech/say", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text })
  }, 30000);
  return res.blob();
}

/* A photo of a till receipt -> {store, lines}. The household's own products
   and hand corrections go with it: the server matches lines against them. */
export async function readReceipt(photo, custom, corrections) {
  const form = new FormData();
  form.append("photo", photo, photo.name || "receipt.jpg");
  form.append("custom", JSON.stringify(custom || {}));
  form.append("corrections", JSON.stringify(corrections || {}));
  // OCR, a product lookup per line and a model pass: a minute on a laptop is normal.
  return (await call("/api/receipt/read", { method: "POST", body: form }, 180000)).json();
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
  await readEvents(res, onEvent);
}