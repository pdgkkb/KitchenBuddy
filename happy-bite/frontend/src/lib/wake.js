/* Happy Bite — hands-free wake word.

   The tablet listens for a short phrase ("Bob") and, on hearing it, opens
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

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();

export const DEFAULT_WAKE = "bob";

/* What a recogniser writes instead of the wake word. A one-syllable name is
   the hard case: "Bob" comes back as "Bobby", "Bop" or "Bab" often enough that
   matching the spelling alone makes it feel deaf. Deliberately NOT every word
   one letter away — "boy" and "box" start sentences too. */
const SOUNDS_LIKE = {
  bob: ["bob", "bobb", "bobby", "bobs", "bop", "bopp", "bab"],
  chef: ["chef", "shef", "jeff"],
};
const GREETING = new Set(["hey", "hi", "ok", "okay", "oh", "yo"]);
// What may come before the name when it's said TO it: "um, okay Bob".
const FILLER = new Set([...GREETING, "um", "uh", "er", "so", "and", "right", "well", "alright"]);

/* Where the wake word is in `heard`: the index of the first word after it,
   or -1. `first` means only filler words may come before it.

   A greeting in the phrase ("hey chef") is optional when it is said — "ok
   chef" and "chef" both count — but a bare "chef" only counts in a short
   utterance, or "the chef said" would wake it. */
function findWake(phrase, words, first = false) {
  const all = norm(phrase || DEFAULT_WAKE).split(" ").filter(Boolean);
  const greeted = all.length > 1 && GREETING.has(all[0]);
  const want = (greeted ? all.slice(1) : all).map(w => new Set(SOUNDS_LIKE[w] || [w]));
  for (let i = 0; i < words.length; i++) {
    if (first && i > 0 && !FILLER.has(words[i - 1])) break;
    if (i + want.length > words.length) break;
    if (!want.every((set, k) => set.has(words[i + k]))) continue;
    if (greeted && !(i > 0 && GREETING.has(words[i - 1])) && words.length > want.length + 2) continue;
    return i + want.length;
  }
  return -1;
}

/* The words of `heard` with the wake word taken out, wherever it was, and
   whether it was there. "stop Bob" and "Bob, stop" both come back as
   { named: true, words: ["stop"] }. */
export function withoutWake(phrase, heard) {
  const words = norm(heard).split(" ").filter(Boolean);
  const end = findWake(phrase, words);
  if (end < 0) return { named: false, words };
  const all = norm(phrase || DEFAULT_WAKE).split(" ").filter(Boolean);
  const len = all.length > 1 && GREETING.has(all[0]) ? all.length - 1 : all.length;
  let start = end - len;
  if (start > 0 && GREETING.has(words[start - 1])) start--;
  return { named: true, words: [...words.slice(0, start), ...words.slice(end)] };
}

/* "bob" -> "Bob", for putting on the screen. */
export const wakeLabel = (phrase) =>
  norm(phrase || DEFAULT_WAKE).replace(/\b[a-z]/g, c => c.toUpperCase());

/* What was said AFTER the wake word, as it was said — or null when the wake
   word wasn't at the start.

     "Bob, how long for the onions?"  -> "how long for the onions?"
     "Bob."                           -> ""
     "tell Bob the pan is hot"        -> null

   Only "hey", "ok", "um" and the like may come before it: someone who says the
   name in the middle of a sentence is talking about Bob, not to him. The
   always-on recogniser passes `anywhere`, because its transcript can start
   with whatever was said in the room before the name. */
export function afterWake(phrase, heard, anywhere = false) {
  const raw = String(heard || "").trim().split(/\s+/).filter(Boolean);
  const words = [], owner = [];                    // each word, and the raw token it came from
  raw.forEach((token, r) => norm(token).split(" ").filter(Boolean).forEach(w => { words.push(w); owner.push(r); }));
  const end = findWake(phrase, words, !anywhere);
  if (end < 0) return null;
  const from = end < words.length ? owner[end] : raw.length;
  return raw.slice(from).join(" ").replace(/^[\s,.;:!?-]+/, "");
}

/* Loose matching for the always-on recogniser: anywhere in what it heard. */
function makeMatcher(phrase) {
  return (heard) => findWake(phrase, norm(heard).split(" ").filter(Boolean)) >= 0;
}

/* Arm the wake word. Returns { stop } to disarm.

   onWake(rest)             the phrase was heard; `rest` is what was said after
                            it in the same breath ("" for the name alone)
   onHeard(text, matched)   every transcript, matched or not — this is what
                            turns "it doesn't work" into "Chrome hears Jeff"
   onStatus(state, detail)  "starting" | "armed" | "error" | "off"
   onError(message)         a human sentence, only for things worth saying
*/
export function startWakeWord({ phrase = DEFAULT_WAKE, onWake, onError, onStatus, onHeard, lang } = {}) {
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
  let pending = null;                               // heard the name, waiting for the sentence

  /* The name shows up in an INTERIM result, before the sentence is over.
     Firing then would open the command microphone halfway through "Bob, I'm
     drained, give me…" and lose the words that matter. So wait for the final
     result, which carries the whole sentence; every interim result that still
     has the name in it pushes the wait back, so it only runs out once they
     have stopped talking. */
  const WAIT_FOR_FINAL = 1500;
  const fire = (rest) => {
    clearTimeout(pending); pending = null;
    cooldown = Date.now() + 3000;                   // don't double-trigger
    onWake?.(rest);
  };

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
        if (!hit || now < cooldown) continue;
        if (e.results[i].isFinal) return fire(afterWake(phrase, text, true) || "");
        clearTimeout(pending);
        pending = setTimeout(() => fire(""), WAIT_FOR_FINAL);
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
      clearTimeout(pending);
      try { rec?.stop(); } catch { /* already stopped */ }
      onStatus?.("off");
    }
  };
}
