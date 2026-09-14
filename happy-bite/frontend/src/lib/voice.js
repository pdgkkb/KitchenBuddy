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
   permission (granted once, from a button tap). */

import * as api from "./api.js";

let current = null;
let currentResolve = null;

export function stopSpeaking() {
  if (current) { try { current.pause(); } catch { /* ignore */ } current = null; }
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  if (currentResolve) { const r = currentResolve; currentResolve = null; r(); }
}

/* Returns a Promise that resolves when the audio finishes (or is stopped). */
export function speak(text, useServer) {
  stopSpeaking();
  const clean = String(text).replace(/[*_#`>]/g, "").trim();
  if (!clean) return Promise.resolve();
  return new Promise((resolve) => {
    currentResolve = resolve;
    const done = () => { if (currentResolve === resolve) { currentResolve = null; resolve(); } };

    if (useServer) {
      api.say(clean).then((blob) => {
        const audio = new Audio(URL.createObjectURL(blob));
        current = audio;
        audio.onended = done;
        audio.onerror = done;
        audio.play().catch(() => browserSpeak(clean, done));
      }).catch(() => browserSpeak(clean, done));
      return;
    }
    browserSpeak(clean, done);
  });
}

function browserSpeak(clean, done) {
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
    const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(t => MediaRecorder.isTypeSupported?.(t));
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
   Records, watches the mic level, and stops on its own after you go quiet.
   Sends the clip to the server (Whisper). onText("") means it heard nothing.
   Options: silence (ms of quiet that ends a turn), maxWait (ms to wait for you
   to start), maxLen (ms hard cap). */
export function listenVAD({ onText, onError, onStart, silence = 1000, maxWait = 9000, maxLen = 20000 }) {
  if (listenState(true) !== "ok") { onError?.(LISTEN_REASON[listenState(true)] || "Can't listen."); return { stop() {} }; }

  let stream = null, recorder = null, audioCtx = null, raf = 0;
  let stopped = false, speaking = false, started = false;
  let lastVoice = 0, startedAt = 0;
  const chunks = [];

  const cleanup = () => {
    cancelAnimationFrame(raf);
    try { audioCtx && audioCtx.close(); } catch { /* ignore */ }
    stream?.getTracks().forEach(t => t.stop());
  };

  const endRecording = () => {
    if (recorder && recorder.state === "recording") recorder.stop();
  };

  navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  }).then((s) => {
    if (stopped) { s.getTracks().forEach(t => t.stop()); return; }
    stream = s;
    onStart?.();
    const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(t => MediaRecorder.isTypeSupported?.(t));
    recorder = new MediaRecorder(s, type ? { mimeType: type } : undefined);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      cleanup();
      if (stopped && !started) return;                     // cancelled before any speech
      if (!started) { onText?.(""); return; }              // never heard anything
      try {
        const { text } = await api.hear(new Blob(chunks, { type: recorder.mimeType }));
        onText?.(text || "");
      } catch (e) { onError?.(e.message); }
    };
    recorder.start();
    startedAt = performance.now();

    // level metering
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = audioCtx.createMediaStreamSource(s);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);

    const tick = () => {
      if (stopped) return;
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
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }).catch(() => { onError?.("Microphone access was refused. Check the site's permissions."); });

  return { stop() { stopped = true; endRecording(); cleanup(); } };
}
