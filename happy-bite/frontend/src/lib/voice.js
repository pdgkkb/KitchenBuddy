/* Happy Bite — voice, both directions, two routes each.

   Speaking:  the server's voice when it has one, otherwise the browser's
              speechSynthesis (on most tablets that voice is on-device).
   Listening: the server transcribes a recording when it can, otherwise the
              browser's SpeechRecognition — which in Chrome sends the audio
              to Google. Either way the audio leaves the device, so the
              interface asks once per session and says why.

   Both listening routes need a SECURE CONTEXT. Over plain http on a LAN
   address the browser refuses silently; localhost is exempt. That's the
   single most common "the mic does nothing" cause, so it gets named. */

import * as api from "./api.js";

let current = null;

export function stopSpeaking() {
  if (current) { current.pause(); current = null; }
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
}

export async function speak(text, useServer) {
  stopSpeaking();
  const clean = String(text).replace(/[*_#`>]/g, "").trim();
  if (!clean) return;
  if (useServer) {
    try {
      const blob = await api.say(clean);
      const audio = new Audio(URL.createObjectURL(blob));
      current = audio;
      await audio.play();
      return;
    } catch { /* fall through to the browser voice */ }
  }
  if (typeof speechSynthesis === "undefined") return;
  const u = new SpeechSynthesisUtterance(clean);
  const lang = /[àâçéèêëîïôûùüÿœ]/i.test(clean) ? "fr" : "en";
  u.voice = speechSynthesis.getVoices().find(v => v.lang.startsWith(lang) && v.localService)
         || speechSynthesis.getVoices().find(v => v.lang.startsWith(lang)) || null;
  u.rate = 1.02;
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
  unsupported: "This browser can't listen. Chrome, Edge and Safari can; Firefox only with the server's voice switched on."
};

/* Starts listening. Returns { stop } — call it to finish a server
   recording early. `onText` gets the transcript once. */
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
