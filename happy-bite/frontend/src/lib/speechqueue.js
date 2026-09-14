/* Happy Bite — speaking while the sentence is still being written.

   The chef used to wait for the whole reply, then start talking. On a local
   7B that is three or four seconds of silence in which you are standing over a
   hot pan wondering whether it heard you. Almost all of that is dead time: the
   first sentence is usually finished after half a second.

   So this sits on the stream. Deltas come in, and the moment a sentence is
   complete it goes into a play queue; the rest keeps arriving while that
   sentence is being spoken. Sentences play strictly in order, one at a time,
   with no gap you'd notice.

   It is ARMED rather than wired in: `arm()` before a send, `disarm()` after.
   Chat.jsx feeds it unconditionally, and feeding a disarmed queue does nothing
   — so the ordinary chat is untouched and only the screens that want spoken
   streaming get it.

   The last thing it does is tell voice.js what it said, so the reply isn't read
   out a second time when the send resolves. */

import * as api from "./api.js";
import * as voice from "./voice.js";

/* A sentence ends at . ! ? … or a newline — but not inside "180 °C." or
   "approx. 5 min", which is why the abbreviation guard is here. */
const END = /([^.!?…\n]+[.!?…]+(?=\s|$)|[^\n]+\n)/;
const ABBREV = /(?:^|\s)(?:approx|approximately|etc|e\.g|i\.e|no|vs|min|tbsp|tsp|°\s*[CF]|[0-9])\.$/i;
const MIN_CHARS = 18;                 // below this, wait for more — "Yes." alone is choppy

let state = null;

function clean(text) {
  return String(text || "").replace(/[*_#`>]/g, "").replace(/\s+/g, " ").trim();
}

/* Play one piece of text and resolve when it has actually finished, so the
   next sentence starts exactly then. Server voice first, browser as fallback —
   same order as voice.speak, but without its stopSpeaking() at the top, which
   would cut off the sentence before. */
function play(text, useServer) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };

    const browser = () => {
      if (typeof speechSynthesis === "undefined") return finish();
      const u = new SpeechSynthesisUtterance(text);
      const lang = /[àâçéèêëîïôûùüÿœ]/i.test(text) ? "fr" : "en";
      const voices = speechSynthesis.getVoices();
      u.voice = voices.find(v => v.lang.startsWith(lang) && v.localService)
             || voices.find(v => v.lang.startsWith(lang)) || null;
      u.rate = 1.02;
      u.onend = finish;
      u.onerror = finish;
      if (state) state.utterance = u;
      speechSynthesis.speak(u);
    };

    if (!useServer) return browser();

    api.say(text).then((blob) => {
      if (!state || state.cancelled) return finish();
      const audio = new Audio(URL.createObjectURL(blob));
      state.audio = audio;
      audio.onended = finish;
      audio.onerror = finish;
      audio.play().catch(browser);
    }).catch(browser);
  });
}

async function drain() {
  if (!state || state.playing) return;
  state.playing = true;
  while (state && !state.cancelled && state.queue.length) {
    const next = state.queue.shift();
    state.said.push(next);
    await play(next, state.useServer);
  }
  if (state) state.playing = false;
  if (state && state.ended && !state.queue.length) settle();
}

function settle() {
  if (!state) return;
  const all = state.said.join(" ").trim();
  if (all) voice.markSpoken(all);
  state.resolve?.();
  state.resolve = null;
}

/* Pull every complete sentence out of the buffer. */
function harvest(force) {
  if (!state) return;
  for (;;) {
    if (force) {
      const rest = clean(state.buffer);
      state.buffer = "";
      if (rest) state.queue.push(rest);
      break;
    }
    const m = END.exec(state.buffer);
    if (!m) break;
    const sentence = state.buffer.slice(0, m.index + m[0].length);
    const rest = state.buffer.slice(m.index + m[0].length);
    if (ABBREV.test(sentence.trimEnd()) && rest.trim().length < 2) break;   // "5 min." — wait
    const text = clean(sentence);
    if (text.length < MIN_CHARS && rest.trim().length === 0) break;         // too short to say alone
    state.buffer = rest;
    if (text) state.queue.push(text);
  }
  if (state.queue.length) drain();
}

/* ------------------------------------------------------------------ api */

export function armed() {
  return !!state && !state.cancelled;
}

/* Start listening to the next stream. Returns a promise that resolves when
   everything spoken has finished playing. */
export function arm({ useServer = false } = {}) {
  cancel();
  let resolve;
  const finished = new Promise(r => { resolve = r; });
  state = {
    useServer, buffer: "", queue: [], said: [], playing: false,
    ended: false, cancelled: false, audio: null, utterance: null, resolve,
  };
  return finished;
}

export function feed(delta) {
  if (!state || state.cancelled) return;
  state.buffer += String(delta || "");
  harvest(false);
}

/* The stream is over: say whatever is left in the buffer. */
export function end() {
  if (!state || state.cancelled) return;
  state.ended = true;
  harvest(true);
  if (!state.playing && !state.queue.length) settle();
}

export function disarm() {
  end();
  const s = state;
  state = null;
  return s ? s.said.join(" ") : "";
}

export function cancel() {
  if (!state) return;
  state.cancelled = true;
  try { state.audio?.pause(); } catch { /* ignore */ }
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  state.resolve?.();
  state = null;
}

export function spokenSoFar() {
  return state ? state.said.join(" ") : "";
}
