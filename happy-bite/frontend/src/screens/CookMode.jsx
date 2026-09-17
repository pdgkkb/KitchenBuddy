/* Cooking mode — the step, big, as key points, with the chef listening.

   This screen is about ONE step at a time, not a chat log. The step is the
   headline; heat and what-to-watch-for are the key points beside it. With local
   voice on, it waits for its name — "Bob" (WAKE_WORD in backend/.env) — and
   only then listens: "Bob, next", "Bob, start a timer", or "Bob" on its own, a
   beep, then the question. It answers out loud in a sentence or two — the
   answer shows as a short caption under the step, not a scrolling transcript —
   and goes back to waiting for its name.

   WHY IT WAITS FOR A NAME: it used to act on everything it heard. Talking to
   someone else in the kitchen, or the radio, moved the step on or sent a
   "question" to the model — a slow, busy GPU for something nobody asked. It
   still transcribes what it hears, locally, to find the name; everything
   without it is dropped before the model ever sees it.

   "Bob, stop chef" — stop talking (keeps listening).
   "stop timer" — cancel a running timer.
   "stop cooking" — end the session.
   The ✕ minimises to a dock you tap or reach with "go back to the cooking
   recipe".

   WHAT CHANGED, AND WHY THIS SCREEN USED TO LAG
   --------------------------------------------
   Three things, none of them visible on this screen, all of them paid for here.

   1. The microphone was built and destroyed on EVERY turn of the listening
      loop — getUserMedia, a fresh AudioContext, a fresh analyser. That is a
      few hundred milliseconds of device negotiation before anything was
      recording (which is where the first word of what you said went), and a
      browser only allows a handful of AudioContexts at once, so a long
      session eventually just stopped hearing with no error at all. lib/voice.js
      now holds one microphone for the session; this screen hands it back in
      `stopLive` and nowhere else.

   2. Every question rebuilt a system prompt containing the whole ingredient
      catalogue, sixty lines of stock, nine thousand characters of recipe JSON
      and a fresh corpus lookup — thousands of tokens the model reads before
      writing the first character of "turn it down a bit". The server now
      sends a cooking-sized prompt (see prompts.chef_system) and skips the
      corpus entirely while you are at the hob.

   3. The server has always been able to write the answers to a step's likely
      questions IN ADVANCE, while you chop — `backend/app/prefetch.py`, exposed
      at /api/cook/prefetch and /api/cook/quick — and no browser code ever
      called either endpoint. It does now: `prime` below parks the answers for
      the step you are on and the one after, and `hearNext` asks for a parked
      answer before it troubles the model. A hit costs nothing and arrives
      instantly, which the caption marks so it doesn't read as a guess. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as E from "../core/engine.js";
import { idleWindow, windowLabel } from "../core/window.js";
import { realCue, stepBrief, stepHeat, vesselToGrab } from "../core/brief.js";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import * as wake from "../lib/wake.js";
import { parseCook } from "../lib/command.js";
import { raceStop } from "../lib/stopword.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { Icon } from "../components/Icon.jsx";
import Stars from "../components/Stars.jsx";
import { useChat } from "../components/Chat.jsx";
import * as sq from "../lib/speechqueue.js";
import "../styles/method.css";

export default function CookMode() {
  const { k, finishCooking, setPref, saveRecipe } = useKitchen();
  const ui = useUI();
  const applyAction = useApplyAction();
  const { recipe, serves } = ui.cooking;
  const [i, setI] = useState(0);
  const [showIngredients, setShowIngredients] = useState(false);
  const [pictures, setPictures] = useState({});
  const [drawing, setDrawing] = useState(false);
  const [live, setLive] = useState(false);               // hands-free listening on
  const oneShotRef = useRef(false);                      // this turn came from the Talk button
  const [vstate, setVstate] = useState("");              // waiting | listening | thinking | speaking
  const [answer, setAnswer] = useState("");              // the chef's latest short reply
  const [instant, setInstant] = useState(false);         // that answer was already written
  const [typed, setTyped] = useState("");
  const step = recipe.steps[i];
  const total = recipe.steps.length;
  const last = i === total - 1;

  /* The gap in this step and the one job that fits it (core/window.js).
     Jobs already handed out are remembered for the session, so the knife is
     rinsed once, not on every step. A step keeps its job while you're on it. */
  const givenRef = useRef({});                          // step index -> key
  /* Is there any of it left once this dish has had its share? Only then is
     "put the eggs back in the fridge" a real job. */
  const hasLeftover = (id) => {
    const have = E.held(k.stock.find(a => a.id === id), E.ref(id), E.ref(id).unit);
    const need = recipe.needs.find(n => n.id === id);
    if (!have.present || !need) return false;
    return have.sure && have.qty > E.scale(need.qty, serves, recipe.serves, E.ref(id).unit);
  };
  const gapFor = (n) => {
    const given = givenRef.current;
    const done = Object.entries(given).filter(([m]) => Number(m) !== n).map(([, key]) => key);
    const w = idleWindow(recipe, n, done, { hasLeftover });
    if (w) given[n] = w.key;
    return w;
  };
  const gap = useMemo(() => gapFor(i), [recipe, i]); // eslint-disable-line react-hooks/exhaustive-deps
  /* Steps whose window has already been said out loud, so starting the timer
     doesn't say it a second time. */
  const gapSaidRef = useRef(new Set());
  const gapRef = useRef(gap); gapRef.current = gap;

  const iRef = useRef(0); iRef.current = i;
  const liveRef = useRef(false);
  const listenRef = useRef(null);
  /* Which listening session a turn belongs to. Bumped on every start and stop.

     "Bob, next step" once went from step 1 to step 3. In development React
     mounts this screen, unmounts it and mounts it again (StrictMode), which
     ran startLive twice: the first session's opening sentence was cut off by
     the second's, its promise resolved, and it carried on into its own
     listening loop. Two loops heard the same sentence and each moved the step
     on. A turn from an old session now does nothing, and a loop never starts
     while another turn is already listening. */
  const sessionRef = useRef(0);
  const useServer = ui.server.voice;
  const wakeWord = ui.server.wakeWord || wake.DEFAULT_WAKE;
  const wakeWordRef = useRef(wakeWord); wakeWordRef.current = wakeWord;
  const wakeName = wake.wakeLabel(wakeWord);

  const context = useCallback(() => ({
    mode: "cooking", recipe, step: iRef.current, serves, stock: stockForServer(k.stock),
    equipment: k.equipment || []
  }), [recipe, serves, k.stock, k.equipment]);
  /* This screen used to pass no onAction at all, so cooking mode was the one
     place the chef could not actually change anything — "I've used the last of
     the milk", mid-recipe, went nowhere. */
  const chat = useChat(context, null, applyAction);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;
  const stopChatRef = useRef(chat.stop); stopChatRef.current = chat.stop;
  const busy = chat.busy;

  useEffect(() => () => voice.stopSpeaking(), []);

  /* The page behind this screen is covered completely, but its drifting,
     blurred background kept animating on the same GPU the model and the voice
     run on. Take it out while cooking; it comes back with the dock. */
  useEffect(() => {
    document.body.classList.add("is-cooking");
    return () => document.body.classList.remove("is-cooking");
  }, []);

  const say = useCallback((text, wasInstant = false) => {
    setAnswer(text || "");
    setInstant(!!text && wasInstant);
  }, []);

  const goStep = useCallback((n) => {
    const t = Math.max(0, Math.min(total - 1, n));
    iRef.current = t; setI(t); say(""); return t;
  }, [total, say]);

  const ingredientsLine = useCallback(() => {
    const names = recipe.needs.map(n => E.ref(n.id).name.toLowerCase());
    const extras = (recipe.extras || []).map(x => x.toLowerCase());
    const all = [...names, ...extras];
    if (!all.length) return "It's a short one — no tracked ingredients.";
    const listed = all.length === 1 ? all[0] : all.slice(0, -1).join(", ") + " and " + all[all.length - 1];
    return `You'll need ${listed}.`;
  }, [recipe]);

  const finish = useCallback(() => {
    stopLive();
    if (!k.book.some(r => r.id === recipe.id)) saveRecipe(recipe);
    finishCooking(recipe, serves);
    ui.stopTimer();
    ui.stopCooking();
    ui.openSheet("review", { recipe });
  }, [k.book, recipe, serves]); // eslint-disable-line

  const speak = (text) => voice.speak(text, useServer);
  // The step said out loud: what to grab, what to do, what goes in (core/brief.js),
  // and the gap in it if it has one. The window used to be said only when a
  // timer was started, so arriving at the step, it was on the screen and
  // nobody mentioned it.
  const brief = (n) => {
    const g = gapFor(n);
    if (g) gapSaidRef.current.add(n);
    const extra = g ? `${stepHeat(recipe.steps[n]) ? "While it cooks" : "While you wait"}, ${g.say.charAt(0).toLowerCase()}${g.say.slice(1)}` : "";
    return stepBrief(recipe, n, serves, true, extra);
  };
  const heat = stepHeat(step);
  const grab = vesselToGrab(recipe, i);

  /* The timer is the moment the waiting starts, so it is the moment to hear
     what fits in the wait: "You have a 90-second window. Rinse the knife and
     the cutting board." Always spoken: with the local voice (Kokoro) when it
     runs, and the browser's own voice when it doesn't. */
  const startStepTimer = () => {
    ui.startTimer(E.stepMinutes(step), step.do.slice(0, 40));
    if (gap && !gapSaidRef.current.has(i)) { gapSaidRef.current.add(i); speak(gap.say); }
  };

  /* A step chosen by tapping — Next, Back, or a dot in the progress bar. It
     used to change the screen and say nothing, even with hands-free on: only
     "Bob, next" read the step. Now a tap reads it too when hands-free is
     listening, or when "Each step while cooking" is switched on (a setting
     that, until now, nothing read). */
  const tapStep = (n) => {
    const t = goStep(n);
    if (liveRef.current || k.prefs.readSteps) speak(brief(t));
  };

  /* One turn. `awake` is false while waiting for the name, true for the one
     turn after a bare "Bob". Every path out of a handled command calls
     hearNext() with no argument, which is back to waiting. */
  const hearNext = useCallback((awake = false) => {
    if (!liveRef.current || listenRef.current) return;
    /* A turn the cook started with the Talk button is exactly one turn: when it
       is over, the microphone closes again rather than sitting there waiting to
       hear its name. Someone who wants it listening all the time uses the mic
       button in the header, which is what "live" means. */
    if (!awake && oneShotRef.current) { oneShotRef.current = false; stopLive(); return; }
    const session = sessionRef.current;
    const current = () => session === sessionRef.current && liveRef.current;
    setVstate(awake ? "listening" : "waiting");
    listenRef.current = voice.listenVAD({
      // Waiting: a name is short, so a short pause ends the clip and nothing is
      // lost by starting another. Awake: a normal pause, and a few seconds to
      // start talking before it goes back to waiting.
      silence: awake ? 1000 : 700,
      maxWait: awake ? 7000 : 9000,
      onText: async (raw) => {
        if (session !== sessionRef.current) return;         // a turn from a session that has ended
        listenRef.current = null;
        if (!current()) return;
        let said = (raw || "").trim();
        if (!said) return hearNext();

        const rest = wake.afterWake(wakeWordRef.current, said);
        if (rest === null && !awake) return hearNext();        // not said to Bob — ignore it
        if (rest === "") {                                     // just the name: beep, then listen
          await voice.chime();
          return hearNext(true);
        }
        if (rest) said = rest;                                  // "Bob, next step" in one breath
        return handle(said);
      },
      onError: () => {
        if (session !== sessionRef.current) return;
        listenRef.current = null;
        if (current()) setTimeout(() => current() && hearNext(), 800);
      },
    });

    /* What was said to Bob: a command, or a question for the chef. Separate
       from the listening so "Bob, stop — how long for the onions?" can hand
       the new question straight back in. */
    async function handle(said) {
        const c = parseCook(said);
        if (c) {
          if (c.cmd === "stopCooking") { stopLive(); ui.stopCooking(); return; }
          if (c.cmd === "minimize")    { stopLive(); ui.minimizeCooking(); return; }
          if (c.cmd === "finish")      { finish(); return; }
          if (c.cmd === "stopChef")    { voice.stopSpeaking(); return hearNext(); }
          if (c.cmd === "stopTimer")   { ui.stopTimer(); setVstate("speaking"); await speak("Timer stopped."); return hearNext(); }
          setVstate("speaking");
          if (c.cmd === "next")        { const t = goStep(iRef.current + 1); await speak(brief(t)); }
          else if (c.cmd === "back")   { const t = goStep(iRef.current - 1); await speak(brief(t)); }
          else if (c.cmd === "goto")   {
            if (c.step > total) await speak(`There ${total === 1 ? "is only one step" : `are only ${total} steps`}.`);
            else { const t = goStep(c.step - 1); await speak(brief(t)); }
          }
          else if (c.cmd === "repeat") { await speak(brief(iRef.current)); }
          else if (c.cmd === "ingredients") { await speak(ingredientsLine()); }
          else if (c.cmd === "timer")  {
            const s = recipe.steps[iRef.current];
            const said = gapSaidRef.current.has(iRef.current);
            if (gapRef.current) gapSaidRef.current.add(iRef.current);
            const also = gapRef.current && !said ? ` ${gapRef.current.say}` : "";
            if (c.minutes) { ui.startTimer(c.minutes, s.do.slice(0, 40)); await speak(`Timer on for ${spokenMinutes(c.minutes)}.${also}`); }
            else if (E.stepMinutes(s) >= 3) { ui.startTimer(E.stepMinutes(s), s.do.slice(0, 40)); await speak(`Timer on for ${E.stepMinutes(s)} minutes.${also}`); }
            else await speak("This step is quick — you watch it rather than time it. Tell me how long if you want a timer.");
          }
          return hearNext();
        }

        // A real question. Ask the server whether it already wrote this answer
        // while we were chopping — a hit is instant and costs the model
        // nothing. A miss returns null and falls straight through, which is the
        // right way round: a two-second wait is cheap and a confidently wrong
        // parked answer, at the hob, is not.
        //
        // All of it — the parked answer, the model, the reading aloud — can be
        // cut off with "stop Bob" (lib/stopword.js). It often starts before
        // you've said the right thing.
        setVstate("thinking");
        let cut = false;
        const out = await raceStop({
          wakeWord: wakeWordRef.current,
          listen: (turn) => voice.listenVAD({ ...turn, silence: 600, maxWait: 120000, maxLen: 4000 }),
          work: async () => {
            const parked = await api.quickAnswer(recipe.id, iRef.current, said);
            if (cut || !current()) return;
            if (parked?.answer) {
              say(parked.answer, true);
              setVstate("speaking");
              await speak(parked.answer);
              return;
            }
            const reply = await sendRef.current(said);
            if (!cut) say(reply || "");
          },
        });
        if (!current()) return;
        if (out.stopped) {
          cut = true;
          stopChatRef.current();                 // the request, the reading-aloud queue, the voice
          say("");
          await out.settled;                     // the chef is free again before anything new goes in
          if (!current()) return;
          if (out.rest) return handle(out.rest);
          await voice.chime();                   // "go on" — and it listens for the right question
          return hearNext(true);
        }
        hearNext();
    }
  }, [recipe, total, useServer, goStep, ingredientsLine, finish, say]); // eslint-disable-line

  /* Talk without saying the name. In cooking mode the microphone is always
     gated on "Bob" so the radio and the conversation don't move the recipe on —
     which is right when your hands are busy and wrong when you are holding the
     tablet. Tapping this is the same as saying the name and waiting for the
     chime: one turn, no name. Nothing here needs the internet; the words are
     transcribed by the local model like every other command. */
  const talkNow = useCallback(() => {
    if (!useServer || !ui.server.chat) return;
    voice.stopSpeaking();                                  // cut the chef off mid-sentence if needed
    sq.cancel();
    try { listenRef.current?.stop(); } catch { /* it may already be finishing */ }
    listenRef.current = null;
    if (!liveRef.current) {                                // tapping it also opens the microphone
      oneShotRef.current = true;
      liveRef.current = true;
      setLive(true);
    }
    hearNext(true);
  }, [useServer, ui.server.chat, hearNext]);

  const startLive = useCallback(() => {
    if (!useServer) { ui.say("Turn on the local voice (Whisper + Kokoro) to cook hands-free."); return; }
    setPref("speakReplies", true);
    const session = ++sessionRef.current;
    liveRef.current = true; setLive(true);
    (async () => {
      setVstate("speaking");
      await speak(brief(iRef.current));
      if (session === sessionRef.current) hearNext();
    })();
  }, [useServer, hearNext, setPref, ui, recipe, total]);

  function stopLive() {
    sessionRef.current++;
    liveRef.current = false; setLive(false); setVstate("");
    try { listenRef.current?.stop(); } catch { /* */ }
    listenRef.current = null;
    sq.cancel();                    // the queue holds the sentences still to come
    voice.stopSpeaking();           // this only ever stopped the one being said
    voice.releaseMic();             // one microphone per session; this is where it goes back
  }

  useEffect(() => {
    if (useServer && ui.server.chat && !liveRef.current) startLive();
    return () => stopLive();
  }, [useServer, ui.server.chat]); // eslint-disable-line

  /* Answers written while they chop.

     Delayed by two and a half seconds, so tapping "next" three times in a row
     starts one job rather than three; and skipped entirely while the chef is
     busy, because a background job racing a foreground one on the same local
     model makes both of them slower — which is the lag this whole file is
     trying to remove, not add to.

     This step and the next: you almost always walk forwards, and the answers
     for the step you are ABOUT to reach are the ones worth having ready. */
  useEffect(() => {
    if (!ui.server.chat || busy) return;
    const t = setTimeout(() => {
      api.prefetchStep(recipe, i, serves);
      if (i + 1 < total) api.prefetchStep(recipe, i + 1, serves);
    }, 2500);
    return () => clearTimeout(t);
  }, [i, total, recipe, serves, ui.server.chat, busy]);

  const askTyped = async (e) => {
    e?.preventDefault?.();
    const q = typed.trim();
    if (!q) return;
    setTyped("");
    setVstate("thinking");
    const parked = await api.quickAnswer(recipe.id, iRef.current, q);
    if (parked?.answer) {
      say(parked.answer, true);
      setVstate(live ? "waiting" : "");
      return;
    }
    const reply = await chat.send(q);
    say(reply || "");
    setVstate(live ? "waiting" : "");
  };

  const picture = async () => {
    setDrawing(true);
    try {
      const { url } = await api.makeImage({ kind: "step", name: recipe.name, step: step.do, cue: step.cue });
      setPictures(p => ({ ...p, [i]: url }));
    } catch (e) { ui.say(e.message); }
    setDrawing(false);
  };

  const inKitchen = useMemo(() => new Map(k.stock.map(a => [a.id, a])), [k.stock]);
  const stateLabel = {
    waiting: `Say “${wakeName}” — then “next”, “repeat”, or ask me anything.`,
    listening: "Listening…", thinking: `Thinking… say “${wakeName}, stop” to cancel`, speaking: "Speaking…",
  }[vstate];
  const bgAnim = k.prefs.bgAnim !== false;
  const mood = moodFor(recipe.cuisine);

  return (
    <div className={"cook cook-solo" + (bgAnim ? " bg-on" : "")} data-mood={mood} role="dialog" aria-label={`Cooking ${recipe.name}`}>
      {bgAnim && <div className="cook-bg" aria-hidden><span /><span /><span /></div>}

      <header className="cook-head">
        <button className="icon-btn" onClick={ui.minimizeCooking} aria-label="Minimise — keep cooking in the background"><Icon name="close" /></button>
        <div className="cook-title">
          <p className="cook-name">{recipe.name}</p>
          <ol className="progress" aria-label={`Step ${i + 1} of ${total}`}>
            {recipe.steps.map((_, n) => (
              <li key={n}><button className={n < i ? "is-done" : n === i ? "is-now" : ""}
                                  onClick={() => tapStep(n)} aria-label={`Go to step ${n + 1}`}>
                {n === i && <span className="progress-emoji" aria-hidden="true">🍳</span>}
              </button></li>
            ))}
          </ol>
        </div>
        <button className={"icon-btn" + (bgAnim ? " is-on" : "")} aria-pressed={bgAnim}
                onClick={() => setPref("bgAnim", !bgAnim)}
                aria-label={bgAnim ? "Turn off the background animation" : "Turn on the background animation"}>
          <Icon name="spark" />
        </button>
        {useServer && ui.server.chat && (
          <button className={"icon-btn" + (live ? " is-on" : "")} aria-pressed={live}
                  onClick={() => (live ? stopLive() : startLive())}
                  aria-label={live ? "Pause hands-free listening" : "Listen hands-free"}>
            <Icon name={live ? "mic" : "mute"} />
          </button>
        )}
      </header>

      {live && (
        <div className={"cook-live is-" + (vstate || "idle")} role="status" aria-live="polite">
          <span className="cook-live-dot" />
          <span>{stateLabel || `Say “${wakeName}”, then “next”, “repeat”, or ask me anything. “${wakeName}, stop cooking” to end.`}</span>
        </div>
      )}

      {useServer && ui.server.chat && (
        <button className={"cook-talk is-" + (vstate === "listening" ? "listening" : live ? "live" : "idle")}
                onClick={talkNow}
                aria-label={`Talk without saying ${wakeName}`}>
          <Icon name="mic" size={22} />
          <span>{vstate === "listening" ? "Listening — say it now"
            : vstate === "thinking" ? "Thinking…"
            : `Talk without saying “${wakeName}”`}</span>
        </button>
      )}

      <div className="cook-stage">
        <div className="cook-recipe-summary">
          <span>{recipe.name}</span>
          <b>{E.minutesOf(recipe)} min total</b>
          <small><Stars recipe={recipe} size={15} /></small>
        </div>
        <button className="link cook-ing-toggle" onClick={() => setShowIngredients(!showIngredients)}>
          <Icon name="list" size={20} /> {showIngredients ? "Hide ingredients" : `Ingredients for ${serves}`}
        </button>
        {showIngredients && (
          <ul className="ings compact">
            {recipe.needs.map(n => {
              const r = E.ref(n.id);
              const want = E.scale(n.qty, serves, recipe.serves, r.unit);
              /* `held` is the three-way answer: there with an amount, there
                 with an amount we can't compare (pieces against grams), or not
                 there. Only the last one is short. The old code treated the
                 middle case as zero, which is why things sitting on the shelf
                 were drawn in red. */
              const have = E.held(inKitchen.get(n.id), r, r.unit);
              const short = !have.present || (have.sure && have.qty < want && want > 0);
              return <li key={n.id} className={"ing" + (short ? " is-short" : "")}>
                <span className="ing-qty">{want > 0 ? E.formatQty(want, r.unit) : "some"}</span>
                <span className="ing-name">{r.name}{n.prep && <small>{n.prep}</small>}</span></li>;
            })}
            {(recipe.extras || []).map(x => <li key={x} className="ing"><span className="ing-qty">·</span><span className="ing-name">{x}</span></li>)}
          </ul>
        )}

        <div className="cook-step-heading">
          <p className="cook-stepno">Step {i + 1} of {total}</p>
          <span className="cook-percent">{Math.round(((i + 1) / total) * 100)}% of the dish</span>
        </div>

        {(heat || realCue(step) || grab) && (
          <div className="kpts">
            {grab && <span className="kpt kpt-grab">Grab {grab}</span>}
            {heat && <span className="kpt kpt-heat"><Icon name="flame" size={16} /> {heat}</span>}
            {realCue(step) && <span className="kpt kpt-cue">Watch for: {realCue(step).toLowerCase()}</span>}
            {E.stepMinutes(step) >= 3 && <span className="kpt kpt-time"><Icon name="clock" size={16} /> about {E.stepMinutes(step)} min</span>}
          </div>
        )}

        {/* What to reach for, with the amount already scaled to this table.
            The step said "add the goat meat"; nobody standing at the hob wants
            to scroll back up to a list to find out how much that was. */}
        {step.uses?.length > 0 && (
          <div className="cook-now">
            <p className="cook-now-label">Add now</p>
            <ul>
              {step.uses.map(id => {
                const r = E.ref(id);
                const from = recipe.needs.find(n => n.id === id)
                          || (recipe.seasoning || []).find(s => s.id === id);
                const want = from?.qty ? E.scale(from.qty, serves, recipe.serves, r.unit) : 0;
                return <li key={id}>
                  <b>{want > 0 ? E.formatQty(want, r.unit) : ""}</b> {r.name}
                </li>;
              })}
            </ul>
          </div>
        )}

        <div className="cook-keypoints">
          <h2 className="cook-do">Do this</h2>
          <ul>{keyPoints(step.do).map((point, n) => <li key={n}>{point}</li>)}</ul>
          {step.why && <p className="cook-why">{step.why}</p>}
        </div>

        {/* Said out loud when the timer starts — that's the moment the waiting
            begins. Shown from the moment the step is, so it can be planned. */}
        {gap && (
          <div className="cook-gap" role="note">
            <p className="cook-gap-label"><Icon name="clock" size={16} /> {windowLabel(gap.seconds)} window</p>
            <p className="cook-gap-task">{gap.task}</p>
            {gap.active && <p className="cook-gap-then">Then back to the pan for a stir.</p>}
          </div>
        )}

        {pictures[i] && <img className="cook-pic" src={pictures[i]} alt={`What “${step.do}” should look like`}
                              width="512" height="512" decoding="async" />}

        {answer && (
          <div className={"cook-answer" + (instant ? " is-instant" : "")}>
            <span className="cook-answer-ic"><Icon name="chef" size={20} /></span>
            <p>{answer}
              {instant && <span className="answer-instant" title="Written while you were chopping">ready</span>}
            </p>
          </div>
        )}

        <div className="cook-tools">
          {E.stepMinutes(step) >= 3 && (
            <button className="btn btn-small btn-ghost" onClick={startStepTimer}>
              <Icon name="clock" size={20} /> {E.stepMinutes(step)} min timer
            </button>
          )}
          <button className="btn btn-small btn-ghost" onClick={() => speak(brief(i))}>
            <Icon name="speaker" size={20} /> Read aloud
          </button>
          {ui.server.images && !pictures[i] && (
            <button className="btn btn-small btn-ghost" onClick={picture} disabled={drawing}>
              <Icon name="image" size={20} /> {drawing ? "Drawing…" : "Show me"}
            </button>
          )}
        </div>

        <div className="cook-nav">
          <button className="btn btn-ghost" disabled={i === 0} onClick={() => tapStep(i - 1)}>
            <Icon name="back" /> Back
          </button>
          {last
            ? <button className="btn btn-primary" onClick={finish}>Finished</button>
            : <button className="btn btn-primary" onClick={() => tapStep(i + 1)}>Next step <Icon name="next" /></button>}
        </div>

        <button className="link cook-end" onClick={() => { stopLive(); ui.stopCooking(); }}>Stop cooking</button>
      </div>

      {ui.server.chat && (
        <form className="cook-ask" onSubmit={askTyped}>
          <input className="cook-ask-input" value={typed} placeholder="Ask about this step…"
                 enterKeyHint="send" onChange={(e) => setTyped(e.target.value)} />
          <button className="icon-btn icon-btn-lg is-mint" type="submit" aria-label="Ask" disabled={!typed.trim()}>
            <Icon name="send" />
          </button>
        </form>
      )}
    </div>
  );
}

/* The pulsating dock shown while cooking is minimised. Tap to come back. */
export function CookDock() {
  const ui = useUI();
  if (!ui.cooking) return null;
  const name = ui.cooking.recipe.name;
  return (
    <div className="cook-dock">
      <button className="cook-dock-btn" onClick={ui.resumeCooking} aria-label={`Back to cooking ${name}`}>
        <span className="cook-dock-pulse" aria-hidden />
        <Icon name="chef" size={22} />
        <span className="cook-dock-text">Cooking <b>{name}</b> · tap to resume</span>
      </button>
      <button className="cook-dock-x" onClick={ui.stopCooking} aria-label="Stop cooking"><Icon name="close" size={18} /></button>
    </div>
  );
}

function spokenMinutes(m) {
  if (m < 1) return `${Math.round(m * 60)} seconds`;
  if (m >= 60 && m % 60 === 0) return m === 60 ? "an hour" : `${m / 60} hours`;
  return m === 1 ? "one minute" : `${m} minutes`;
}

function keyPoints(text) {
  return String(text || "").split(/(?<=[.!?])\s+/).map(s => s.trim()).filter(Boolean);
}

function moodFor(cuisine) {
  const c = (cuisine || "").toLowerCase();
  if (/ital/.test(c)) return "italian";
  if (/french|france/.test(c)) return "french";
  if (/span|tapas|paella/.test(c)) return "spanish";
  if (/mexic|tex/.test(c)) return "mexican";
  if (/indian|curry|tikka|masala/.test(c)) return "indian";
  if (/chin|thai|japan|korea|viet|asian|ramen|noodle/.test(c)) return "asian";
  return "default";
}