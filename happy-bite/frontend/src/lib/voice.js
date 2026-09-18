/* Happy Bite — voice.

   Speaking:  the server's voice (local Kokoro) when it has one, otherwise the
              browser's speechSynthesis. speak() resolves when playback ENDS, so
              a conversation loop can wait before listening again.
   Listening: two ways —
     • listen()      one-shot: MediaRecorder -> server Whisper (or the browser
                     recogniser as a fallback). Used by the tap-to-talk mic.
     • listenVAD()   hands-free: records and auto-stops when you go quiet
                     (voice-activity detection), then transcribes with Whisper.
                     This is what the hands-free conversation uses — no wake
                     recogniser, so none of the "aborted" flakiness.

   All of it needs a SECURE CONTEXT (https or localhost) and microphone
   permission (granted once, from a button tap).

   WHY COOKING MODE FELT SLOW, AND WHAT THIS FILE HAD TO DO WITH IT
   ---------------------------------------------------------------
   Cooking mode listens in a loop: listen, transcribe, answer, listen again.
   Every single turn used to build the whole audio stack from scratch —
   getUserMedia, a new AudioContext, a new analyser, a new MediaRecorder — and
   tear it down again at the end. Two things follow from that, and both were
   reported as "the app lags in cook mode":

     1. getUserMedia is not free. On macOS it is tens to hundreds of
        milliseconds of device negotiation, EVERY TURN, before the microphone
        is even open — a gap in which you have started talking and nothing is
        recording. That is also why the first word of an answer went missing.
     2. A browser allows only a handful of AudioContexts at once (Chrome
        stops at six). close() is asynchronous, so a long session opened them
        faster than they went away; when the limit was reached the audio graph
        stopped starting and hands-free just went deaf, with no error.

   So the microphone is now opened ONCE per hands-free session and held: one
   stream, one AudioContext, one analyser, reused by every turn. Only the
   MediaRecorder — which is cheap — is per turn. `releaseMic()` closes it when
   the session ends.

   The level meter moved off requestAnimationFrame too. rAF fires 60 times a
   second, on the same thread as React and the animated background, to read a
   number that changes meaningfully about 25 times a second; and the browser
   throttles or stops it entirely when the tab is backgrounded, which froze
   voice-activity detection mid-turn on a tablet that dimmed. A 40 ms interval
   does the same job for a fraction of the work and keeps running. */

import * as api from "./api.js";

let current = null;
let currentResolve = null;

/* Which request is allowed to make a sound.
   ------------------------------------------------------------------------
   This is the echo, and the voice that keeps talking after "stop cooking".

   `speak()` with the server voice does `api.say(text).then(blob => play it)`.
   Between the request and the reply there is a second or two of network and
   Kokoro, and NOTHING in the old code could reach into that gap. stopSpeaking()
   paused `current` — but `current` was still null, because the audio element
   does not exist yet. When the blob finally arrived it built a fresh Audio and
   played it, cheerfully, over whatever was happening by then.

   Two consequences, both reported: press stop and the chef finishes its
   sentence a second later, and ask two things quickly (or let React mount the
   screen twice, which it does in development) and both replies play at once —
   an echo.

   So every request takes a ticket. stopSpeaking() invalidates every ticket
   issued so far, and a reply holding a stale one is dropped on arrival. */
let generation = 0;

/* What the sentence-by-sentence queue has already read out, so the same text
   isn't spoken a second time when the stream finishes. */
let spokenMark = "";

const flat = (t) => String(t || "").replace(/[*_#`>]/g, "").replace(/\s+/g, " ").trim().toLowerCase();

export function markSpoken(text) {
  spokenMark = flat(text);
}

/* Consumed on use: a deliberate repeat still speaks. */
export function isEcho(text) {
  if (!spokenMark) return false;
  const said = flat(text);
  if (!said) return false;
  const nearly = (a, b) => a.startsWith(b.slice(0, Math.max(12, Math.floor(b.length * 0.9))));
  if (said === spokenMark || nearly(spokenMark, said) || nearly(said, spokenMark)) {
    spokenMark = "";
    return true;
  }
  return false;
}

export function stopSpeaking() {
  generation += 1;                       // every ticket issued so far is void
  if (current) { try { current.pause(); } catch { /* ignore */ } current = null; }
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  if (currentResolve) { const r = currentResolve; currentResolve = null; r(); }
}

/* Two short rising notes: "I heard my name, go on." A spoken "yes?" would be
   friendlier and a second late — Kokoro has to write it first — and the first
   word of the command goes into that second. Resolves when the sound is over,
   so the microphone isn't recording its own beep. One AudioContext, kept:
   browsers allow only a few. */
let chimeCtx = null;
export function chime() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return Promise.resolve();
    chimeCtx = chimeCtx || new AC();
    const ctx = chimeCtx;
    if (ctx.state === "suspended") ctx.resume();
    const t = ctx.currentTime + 0.02;
    [[660, 0], [880, 0.11]].forEach(([freq, at]) => {
      const osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, t + at);
      gain.gain.exponentialRampToValueAtTime(0.25, t + at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.1);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t + at);
      osc.stop(t + at + 0.11);
    });
    return new Promise(resolve => setTimeout(resolve, 300));
  } catch {
    return Promise.resolve();
  }
}

/* Returns a Promise that resolves when the audio finishes (or is stopped). */
export function speak(text, useServer) {
  const clean = String(text).replace(/[*_#`>]/g, "").trim();
  if (clean && isEcho(clean)) return Promise.resolve();   // the stream already said it
  stopSpeaking();
  if (!clean) return Promise.resolve();
  const ticket = generation;
  return new Promise((resolve) => {
    currentResolve = resolve;
    const done = () => { if (currentResolve === resolve) { currentResolve = null; resolve(); } };

    if (useServer) {
      api.say(clean).then((blob) => {
        // Back from the server. Is anyone still listening for this one?
        if (ticket !== generation) return done();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        current = audio;
        // The object URL used to leak one blob per sentence for the whole
        // session. A cooking session is hundreds of sentences of WAV.
        const end = () => { try { URL.revokeObjectURL(url); } catch { /* ignore */ } done(); };
        audio.onended = end;
        audio.onerror = end;
        audio.play().catch(() => browserSpeak(clean, end, ticket));
      }).catch(() => browserSpeak(clean, done, ticket));
      return;
    }
    browserSpeak(clean, done, ticket);
  });
}

function browserSpeak(clean, done, ticket) {
  if (ticket !== undefined && ticket !== generation) return done();
  if (typeof speechSynthesis === "undefined") return done();
  const u = new SpeechSynthesisUtterance(clean);
  const lang = /[àâçéèêëîïôûùüÿœ]/i.test(clean) ? "fr" : "en";
  u.voice = speechSynthesis.getVoices().find(v => v.lang.startsWith(lang) && v.localService)
         || speechSynthesis.getVoices().find(v => v.lang.startsWith(lang)) || null;
  u.rate = 1.02;
  u.onend = done;
  u.onerror = done;
  speechSynthesis.speak(u);
}

export function listenState(useServer) {
  if (typeof window === "undefined") return "unsupported";
  if (!window.isSecureContext) return "insecure";
  if (useServer) return navigator.mediaDevices?.getUserMedia && window.MediaRecorder ? "ok" : "unsupported";
  return (window.SpeechRecognition || window.webkitSpeechRecognition) ? "ok" : "unsupported";
}

export const LISTEN_REASON = {
  insecure: "Listening needs a secure connection. Over plain http on a network address the browser blocks the microphone — use localhost, or serve the app over https.",
  unsupported: "This browser can't listen. Chrome, Edge and Safari can."
};

/* ---- One microphone, held for the whole hands-free session ---- */

let mic = null;          // { stream, ctx, analyser, buf, mime }
let micOpening = null;   // so two turns starting at once don't open two mics

const pickMime = () =>
  ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
    .find(t => window.MediaRecorder?.isTypeSupported?.(t)) || "";

const micAlive = () =>
  !!mic && mic.stream.getTracks().some(t => t.readyState === "live")
        && mic.ctx.state !== "closed";

async function openMic() {
  if (micAlive()) return mic;
  if (micOpening) return micOpening;
  releaseMic();
  micOpening = (async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") { try { await ctx.resume(); } catch { /* ignore */ } }
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    mic = { stream, ctx, analyser, buf: new Uint8Array(analyser.fftSize), mime: pickMime() };
    return mic;
  })();
  try { return await micOpening; }
  finally { micOpening = null; }
}

/* Close the microphone. Call this when hands-free ends — not between turns.
   While it is open the browser shows its recording indicator, which is the
   honest signal that the kitchen is listening. */
export function releaseMic() {
  const m = mic;
  mic = null;
  if (!m) return;
  try { m.stream.getTracks().forEach(t => t.stop()); } catch { /* ignore */ }
  try { m.ctx.close(); } catch { /* ignore */ }
}

export function micIsOpen() { return micAlive(); }

/* One-shot listen (tap-to-talk). */
export function listen({ useServer, onText, onError, onEnd }) {
  const state = listenState(useServer);
  if (state !== "ok") { onError(LISTEN_REASON[state]); onEnd?.(); return { stop() {} }; }
  return useServer ? record({ onText, onError, onEnd }) : recognise({ onText, onError, onEnd });
}

function recognise({ onText, onError, onEnd }) {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new Rec();
  rec.lang = navigator.language || "en-GB";
  rec.interimResults = false;
  rec.onresult = (e) => onText(e.results[0][0].transcript);
  rec.onerror = (e) => onError(
    e.error === "not-allowed" ? "Microphone access was refused. Check the site's permissions."
    : e.error === "no-speech" ? "Didn't hear anything."
    : e.error === "network" ? "Speech recognition couldn't reach its service."
    : "Listening failed (" + e.error + ").");
  rec.onend = () => onEnd?.();
  try { rec.start(); } catch { onError("Listening wouldn't start."); onEnd?.(); }
  return { stop: () => rec.stop() };
}

function record({ onText, onError, onEnd }) {
  let recorder = null, stream = null, cap = null, stopped = false;
  const finish = () => { clearTimeout(cap); stream?.getTracks().forEach(t => t.stop()); };

  navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => {
    if (stopped) { s.getTracks().forEach(t => t.stop()); onEnd?.(); return; }
    stream = s;
    const type = pickMime();
    recorder = new MediaRecorder(s, type ? { mimeType: type } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      finish();
      try {
        const { text } = await api.hear(new Blob(chunks, { type: recorder.mimeType }));
        text ? onText(text) : onError("Didn't hear anything.");
      } catch (e) { onError(e.message); }
      onEnd?.();
    };
    recorder.start();
    cap = setTimeout(() => recorder.state === "recording" && recorder.stop(), 15000);
  }).catch(() => { onError("Microphone access was refused. Check the site's permissions."); onEnd?.(); });

  return { stop() { stopped = true; if (recorder?.state === "recording") recorder.stop(); } };
}

/* ---- Hands-free listen with voice-activity detection ----
   Records on the microphone already open, watches the level, and stops on its
   own after you go quiet. Sends the clip to the server (Whisper/Parakeet).
   onText("") means it heard nothing.
   Options: silence (ms of quiet that ends a turn), maxWait (ms to wait for you
   to start), maxLen (ms hard cap). */

const METER_MS = 40;               // 25 reads a second is plenty to hear a pause

export function listenVAD({ onText, onError, onStart, silence = 1000, maxWait = 9000, maxLen = 20000 }) {
  const state = listenState(true);
  // Both methods, always. A caller that cancels a turn calls abort(); when
  // this early return handed back an object with only stop() on it, that call
  // threw, the turn was never cancelled, and the caller's catch swallowed it.
  if (state !== "ok") { onError?.(LISTEN_REASON[state] || "Can't listen."); return { stop() {}, abort() {} }; }

  let recorder = null, meter = 0;
  let stopped = false, speaking = false, started = false, aborted = false;
  let lastVoice = 0, startedAt = 0;
  const chunks = [];

  const stopMeter = () => { clearInterval(meter); meter = 0; };
  const endRecording = () => {
    stopMeter();
    if (recorder && recorder.state === "recording") recorder.stop();
  };

  openMic().then((m) => {
    if (stopped) return;
    onStart?.();
    recorder = new MediaRecorder(m.stream, m.mime ? { mimeType: m.mime } : undefined);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stopMeter();
      // The stream and the AudioContext stay open on purpose — the next turn
      // reuses them. releaseMic() is the session's job, not this turn's.
      if (aborted) return;                                  // thrown away, see abort()
      if (stopped && !started) return;                     // cancelled before any speech
      if (!started) { onText?.(""); return; }              // never heard anything
      try {
        const { text } = await api.hear(new Blob(chunks, { type: recorder.mimeType }));
        onText?.(text || "");
      } catch (e) { onError?.(e.message); }
    };
    recorder.start();
    startedAt = performance.now();

    const { analyser, buf } = m;
    meter = setInterval(() => {
      if (stopped) return stopMeter();
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / buf.length);
      const now = performance.now();

      if (rms > 0.035) {                    // speaking
        if (!started) started = true;
        speaking = true;
        lastVoice = now;
      } else if (speaking && now - lastVoice > silence) {
        return endRecording();              // a clear pause after speech -> done
      }
      if (!started && now - startedAt > maxWait) return endRecording();   // gave up waiting
      if (now - startedAt > maxLen) return endRecording();                // hard cap
    }, METER_MS);
  }).catch(() => {
    onError?.("Microphone access was refused. Check the site's permissions.");
  });

  // stop() ends the recording and still transcribes what was said; abort()
  // throws it away. A turn cancelled because its moment has passed — the
  // "stop Bob" watch once the answer has arrived — must not spend a
  // transcription on audio nobody will read.
  return {
    stop() { stopped = true; endRecording(); },
    abort() { stopped = true; aborted = true; chunks.length = 0; endRecording(); },
  };
}