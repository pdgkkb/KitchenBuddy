/* Happy Bite — hands-free wake word.

   The tablet listens for a short phrase ("Hey Chef") and, on hearing it,
   opens the microphone for the real command. Two layers, on purpose:

   - Wake spotting uses the browser's continuous SpeechRecognition. It only
     ever needs to recognise one phrase, so it's cheap and needs no model
     files. (In Chrome this streams audio to Google while armed — the UI must
     say so and make arming a deliberate, revocable choice.)
   - The command itself is then captured by voice.listen(), which — when the
     server runs the local Whisper model (SPEECH_PROVIDER=local) — is
     transcribed on your machine. So the always-on part is tiny and the
     accurate part stays local.

   A fully on-device wake word (openWakeWord via onnxruntime-web, no cloud even
   while armed) is the recommended upgrade; see docs/VOICE.md. This module is
   the version that runs today.

   Needs a SECURE CONTEXT (https or localhost), like all mic access. */

const Rec = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

export function wakeSupported() {
  if (typeof window === "undefined") return "unsupported";
  if (!window.isSecureContext) return "insecure";
  return Rec ? "ok" : "unsupported";
}

const norm = (s) => s.toLowerCase().replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();

/* Loose matching so a natural "hey, chef" or a small mishear still fires. */
function makeMatcher(phrase) {
  const want = norm(phrase);
  const head = want.split(" ")[0];                 // e.g. "hey"
  const tail = want.split(" ").slice(1).join(" "); // e.g. "chef"
  const variants = [want, want.replace(/\s+/g, ""), `${head} ${tail}`, `ok ${tail}`, `hi ${tail}`];
  return (heard) => {
    const h = norm(heard);
    return variants.some(v => v && h.includes(v)) || (tail.length >= 3 && h.includes(tail) && h.length <= tail.length + 6);
  };
}

/* Arm the wake word. Returns { stop } to disarm. `onWake` fires each time the
   phrase is heard; the caller then starts a normal listen for the command. */
export function startWakeWord({ phrase = "hey chef", onWake, onError, onStatus, lang }) {
  const state = wakeSupported();
  if (state !== "ok") {
    onError?.(state === "insecure"
      ? "Hands-free needs a secure connection (https or localhost)."
      : "This browser can't run the wake word.");
    return { stop() {} };
  }

  const matches = makeMatcher(phrase);
  let rec = null;
  let stopped = false;
  let restartTimer = null;
  let cooldown = 0;                                 // ignore repeats right after a fire

  const start = () => {
    if (stopped) return;
    rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = lang || navigator.language || "en-GB";
    rec.onresult = (e) => {
      const now = Date.now();
      if (now < cooldown) return;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0]?.transcript || "";
        if (matches(text)) {
          cooldown = now + 3000;                    // don't double-trigger
          onWake?.();
          return;
        }
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        onError?.("Microphone access was refused, so hands-free is off.");
        stopped = true;
      }
      // "no-speech" / "aborted" / "network" are transient: onend restarts.
    };
    rec.onend = () => {
      if (!stopped) restartTimer = setTimeout(start, 400);   // keep it alive
    };
    try { rec.start(); onStatus?.("armed"); }
    catch { restartTimer = setTimeout(start, 600); }
  };

  start();

  return {
    stop() {
      stopped = true;
      clearTimeout(restartTimer);
      try { rec?.stop(); } catch { /* already stopped */ }
      onStatus?.("off");
    }
  };
}
