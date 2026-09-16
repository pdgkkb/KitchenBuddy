/* Happy Bite — hands-free wake word.

   The tablet listens for a short phrase ("Hey Chef") and, on hearing it, opens
   the microphone for the real command. Two layers, on purpose:

   - Wake spotting uses the browser's continuous SpeechRecognition. It only ever
     needs to recognise one phrase, so it's cheap and needs no model files.
     (In Chrome this streams audio to Google while armed — the UI must say so
     and make arming a deliberate, revocable choice.)
   - The command itself is then captured by voice.listen(), which — when the
     server runs the local Whisper model — is transcribed on your machine.

   THIS FILE NEVER TALKS TO THE SERVER. Not once. Whether Whisper and Kokoro
   are running has no bearing on whether the wake word can work; that confusion
   cost days, because VoiceAgent used to refuse to arm this without them.

   WHAT CHANGED, AND WHY IT IS ALL ABOUT SAYING WHAT HAPPENED:

   The old version reported a refused microphone and nothing else. Every other
   way this fails — the recogniser starting and immediately stopping because
   something else holds the microphone, Chrome unable to reach the service that
   does the recognising, a browser with no recogniser at all — looked identical
   from outside: silence. You said "hey chef", nothing happened, and there was
   no way to tell which of five things had gone wrong.

   So `onStatus` now reports every transition, `onHeard` reports every phrase
   the browser thought it heard whether it matched or not, and a recogniser
   that keeps dying without hearing anything says so instead of restarting for
   ever.

   Needs a SECURE CONTEXT (https or localhost), like all microphone access. */

const Rec = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

export function wakeSupported() {
  if (typeof window === "undefined") return "unsupported";
  if (!window.isSecureContext) return "insecure";
  return Rec ? "ok" : "unsupported";
}

export const WAKE_REASON = {
  insecure: "The wake word needs a secure page. Use localhost, or serve the app over https — on a plain http:// network address the browser blocks the microphone.",
  unsupported: "This browser has no speech recogniser. Chrome, Edge and Safari have one; Firefox does not.",
};

const norm = (s) => s.toLowerCase().replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();

/* Loose matching so a natural "hey, chef" or a small mishear still fires. */
function makeMatcher(phrase) {
  const want = norm(phrase);
  const head = want.split(" ")[0];                 // e.g. "hey"
  const tail = want.split(" ").slice(1).join(" "); // e.g. "chef"
  const variants = [want, want.replace(/\s+/g, ""), `${head} ${tail}`, `ok ${tail}`, `hi ${tail}`];
  return (heard) => {
    const h = norm(heard);
    return variants.some(v => v && h.includes(v)) ||
           (tail.length >= 3 && h.includes(tail) && h.length <= tail.length + 6);
  };
}

/* Arm the wake word. Returns { stop } to disarm.

   onWake()                 the phrase was heard
   onHeard(text, matched)   every transcript, matched or not — this is what
                            turns "it doesn't work" into "Chrome hears Jeff"
   onStatus(state, detail)  "starting" | "armed" | "error" | "off"
   onError(message)         a human sentence, only for things worth saying
*/
export function startWakeWord({ phrase = "hey chef", onWake, onError, onStatus, onHeard, lang } = {}) {
  const state = wakeSupported();
  if (state !== "ok") {
    onStatus?.("error", WAKE_REASON[state]);
    onError?.(WAKE_REASON[state]);
    return { stop() {} };
  }

  const matches = makeMatcher(phrase);
  let rec = null;
  let stopped = false;
  let restartTimer = null;
  let cooldown = 0;                                 // ignore repeats right after a fire
  let heardAnything = false;
  let deadStarts = 0;                               // started and died with nothing heard

  const start = () => {
    if (stopped) return;
    rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = lang || navigator.language || "en-GB";

    rec.onstart = () => onStatus?.("armed");

    rec.onresult = (e) => {
      const now = Date.now();
      heardAnything = true;
      deadStarts = 0;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0]?.transcript || "";
        const hit = matches(text);
        if (text.trim()) onHeard?.(text.trim(), hit);
        if (hit) {
          if (now < cooldown) return;
          cooldown = now + 3000;                    // don't double-trigger
          onWake?.();
          return;
        }
      }
    };

    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        stopped = true;
        const why = "The microphone was refused, so the wake word is off. Click the icon at the left of the address bar and allow it.";
        onStatus?.("error", why);
        onError?.(why);
      } else if (e.error === "network") {
        // Worth naming: Chrome does the recognising on Google's servers, so
        // this is not a bug in the app and no amount of retrying helps.
        const why = "The browser couldn't reach its speech service, so the wake word can't listen. Chrome does this recognition online.";
        onStatus?.("error", why);
        onError?.(why);
      }
      // "no-speech" and "aborted" are transient: onend restarts.
    };

    rec.onend = () => {
      if (stopped) { onStatus?.("off"); return; }
      // A recogniser that starts and dies without hearing a thing, again and
      // again, is not "flaky" — something else is holding the microphone, or
      // the tab lost it. Restarting for ever hides that; six tries is enough
      // to know.
      if (!heardAnything && ++deadStarts >= 6) {
        stopped = true;
        const why = "The wake word keeps stopping before it hears anything. Something else is using the microphone — close the other tab or app, then switch it off and on again.";
        onStatus?.("error", why);
        onError?.(why);
        return;
      }
      restartTimer = setTimeout(start, 400);        // keep it alive
    };

    try { rec.start(); onStatus?.("starting"); }
    catch { restartTimer = setTimeout(start, 600); }
  };

  /* Ask for the microphone explicitly first. Without this, a refusal arrives
     later as a bare "not-allowed" from the recogniser, long after the person
     has stopped connecting it to what they just did. */
  const begin = async () => {
    try {
      if (navigator.mediaDevices?.getUserMedia) {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach(t => t.stop());
      }
    } catch {
      stopped = true;
      const why = "The microphone was refused, so the wake word is off. Click the icon at the left of the address bar and allow it.";
      onStatus?.("error", why);
      onError?.(why);
      return;
    }
    start();
  };
  begin();

  return {
    stop() {
      stopped = true;
      clearTimeout(restartTimer);
      try { rec?.stop(); } catch { /* already stopped */ }
      onStatus?.("off");
    }
  };
}
