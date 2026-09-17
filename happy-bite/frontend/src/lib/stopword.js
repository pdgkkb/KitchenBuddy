/* "Stop, Bob" — interrupting the chef while it's thinking or talking.

   Bob starts on an answer the moment you stop speaking, and sometimes that is
   before you said the right thing: half a question, a misheard word. Nothing
   used to listen while it was thinking, so the only way out was to wait for an
   answer to the wrong question.

   So while the chef works, a second, narrow listener runs: it hears only one
   thing, "stop". Said with the name ("stop Bob", "Bob, stop", "Bob, wait"), a
   stop word counts at the start of whatever was said, and anything after it
   is the new question: "Bob, stop — how long for the onions?" Said without the
   name, the whole utterance must be a stop phrase and nothing else, so "stop
   stirring for a sec" to someone else in the kitchen doesn't cancel anything.

   isStop(text, wakeWord)        -> null | { rest }
   watchForStop({ listen, wakeWord, onStop }) -> { cancel }
   raceStop({ work, listen, wakeWord }) -> Promise<{ stopped, value?, rest?, settled }> */

import { withoutWake } from "./wake.js";

const STOPS = [
  "stop thinking", "stop talking", "stop that", "stop it", "stop",
  "cancel that", "cancel", "wait wait", "wait", "hold on", "hang on",
  "never mind", "nevermind", "forget it", "forget that", "not that",
  "no no", "no stop", "no wait", "shut up", "quiet", "enough",
];
const FILLER = new Set(["hey", "ok", "okay", "oh", "um", "uh", "er", "please", "just", "right", "so"]);

function stripFiller(words) {
  let i = 0, j = words.length;
  while (i < j && FILLER.has(words[i])) i++;
  while (j > i && FILLER.has(words[j - 1])) j--;
  return words.slice(i, j);
}

export function isStop(text, wakeWord) {
  const { named, words } = withoutWake(wakeWord, text);
  const core = stripFiller(words);
  const said = core.join(" ");
  for (const phrase of STOPS) {
    if (said === phrase) return { rest: "" };
    if (named && said.startsWith(phrase + " ")) {
      const rest = stripFiller(core.slice(phrase.split(" ").length));
      return { rest: rest.length >= 2 ? rest.join(" ") : "" };
    }
  }
  return null;
}

/* Listen, turn after turn, until a stop is heard or `cancel()` is called.
   `listen` is one turn: ({ onText, onError }) -> { stop, abort? }. */
export function watchForStop({ listen, wakeWord, onStop }) {
  let done = false;
  let turn = null;
  const next = () => {
    if (done) return;
    turn = listen({
      onText: (text) => {
        turn = null;
        if (done) return;
        const hit = isStop(text || "", wakeWord);
        if (hit) { done = true; onStop(hit); } else next();
      },
      onError: () => { turn = null; if (!done) setTimeout(next, 600); },
    });
  };
  next();
  return {
    cancel() {
      done = true;
      try { (turn?.abort || turn?.stop)?.call(turn); } catch { /* already over */ }
      turn = null;
    },
  };
}

/* Run `work` (the chef answering) while watching for a stop.

   Resolves with { stopped: false, value } when the work finishes first, or
   { stopped: true, rest } the moment a stop is heard. The work isn't
   cancelled here — the caller aborts the request — so `settled` resolves once
   it has actually wound down, and anything that needs the chef free again
   (the new question in "Bob, stop, how long for the onions") waits on it. */
export function raceStop({ work, listen, wakeWord }) {
  let watch;
  const heard = new Promise((resolve) => {
    watch = watchForStop({ listen, wakeWord, onStop: (hit) => resolve({ stopped: true, rest: hit.rest }) });
  });
  const running = Promise.resolve().then(work);
  const settled = running.then(() => {}, () => {});
  return Promise.race([running.then((value) => ({ stopped: false, value })), heard])
    .then((out) => { watch.cancel(); return { ...out, settled }; },
          (err) => { watch.cancel(); throw err; });
}
