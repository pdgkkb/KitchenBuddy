/* Cooking mode — the step, big, as key points, with the chef listening.

   This screen is about ONE step at a time, not a chat log. The step is the
   headline; heat and what-to-watch-for are the key points beside it. With local
   voice on it listens the whole time: say "next"/"back"/"repeat", "start a
   timer", or ask a question and it answers out loud in a sentence or two — the
   answer shows as a short caption under the step, not a scrolling transcript.

   "stop chef" — stop talking (keeps listening).
   "stop timer" — cancel a running timer.
   "stop cooking" — end the session.
   The ✕ minimises to a dock you tap or reach with "go back to the cooking
   recipe". */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import { parseCook } from "../lib/command.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { Icon } from "../components/Icon.jsx";
import { useChat } from "../components/Chat.jsx";

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
  const [vstate, setVstate] = useState("");              // listening | thinking | speaking
  const [answer, setAnswer] = useState("");              // the chef's latest short reply
  const [typed, setTyped] = useState("");
  const step = recipe.steps[i];
  const total = recipe.steps.length;
  const last = i === total - 1;

  const iRef = useRef(0); iRef.current = i;
  const liveRef = useRef(false);
  const listenRef = useRef(null);
  const useServer = ui.server.voice;

  const context = useCallback(() => ({
    mode: "cooking", recipe, step: iRef.current, serves, stock: stockForServer(k.stock),
    equipment: k.equipment || []
  }), [recipe, serves, k.stock, k.equipment]);
  /* This screen used to pass no onAction at all, so cooking mode was the one
     place the chef could not actually change anything — "I've used the last of
     the milk", mid-recipe, went nowhere. */
  const chat = useChat(context, null, applyAction);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;

  useEffect(() => () => voice.stopSpeaking(), []);

  const goStep = useCallback((n) => {
    const t = Math.max(0, Math.min(total - 1, n));
    iRef.current = t; setI(t); setAnswer(""); return t;
  }, [total]);

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

  const hearNext = useCallback(() => {
    if (!liveRef.current) return;
    setVstate("listening");
    listenRef.current = voice.listenVAD({
      onText: async (raw) => {
        listenRef.current = null;
        if (!liveRef.current) return;
        const said = (raw || "").trim();
        if (!said) return hearNext();

        const c = parseCook(said);
        if (c) {
          if (c.cmd === "stopCooking") { stopLive(); ui.stopCooking(); return; }
          if (c.cmd === "minimize")    { stopLive(); ui.minimizeCooking(); return; }
          if (c.cmd === "finish")      { finish(); return; }
          if (c.cmd === "stopChef")    { voice.stopSpeaking(); return hearNext(); }
          if (c.cmd === "stopTimer")   { ui.stopTimer(); setVstate("speaking"); await speak("Timer stopped."); return hearNext(); }
          setVstate("speaking");
          if (c.cmd === "next")        { const t = goStep(iRef.current + 1); await speak(stepBrief(recipe.steps[t], t, total)); }
          else if (c.cmd === "back")   { const t = goStep(iRef.current - 1); await speak(stepBrief(recipe.steps[t], t, total)); }
          else if (c.cmd === "repeat") { await speak(stepBrief(recipe.steps[iRef.current], iRef.current, total)); }
          else if (c.cmd === "ingredients") { await speak(ingredientsLine()); }
          else if (c.cmd === "timer")  {
            const s = recipe.steps[iRef.current];
            if (s.minutes >= 3) { ui.startTimer(s.minutes, s.do.slice(0, 40)); await speak(`Timer on for ${s.minutes} minutes.`); }
            else await speak("This step is quick — you watch it rather than time it. Tell me how long if you want a timer.");
          }
          return hearNext();
        }

        // A real question — the chef answers out loud, shown as a short caption.
        setVstate("thinking");
        const reply = await sendRef.current(said);
        setAnswer(reply || "");
        hearNext();
      },
      onError: () => { if (liveRef.current) setTimeout(hearNext, 800); },
    });
  }, [recipe, total, useServer, goStep, ingredientsLine, finish]); // eslint-disable-line

  const startLive = useCallback(() => {
    if (!useServer) { ui.say("Turn on the local voice (Whisper + Kokoro) to cook hands-free."); return; }
    setPref("speakReplies", true);
    liveRef.current = true; setLive(true);
    (async () => { setVstate("speaking"); await speak(stepBrief(recipe.steps[iRef.current], iRef.current, total)); hearNext(); })();
  }, [useServer, hearNext, setPref, ui, recipe, total]);

  function stopLive() {
    liveRef.current = false; setLive(false); setVstate("");
    try { listenRef.current?.stop(); } catch { /* */ }
    listenRef.current = null;
    voice.stopSpeaking();
  }

  useEffect(() => {
    if (useServer && ui.server.chat && !liveRef.current) startLive();
    return () => stopLive();
  }, [useServer, ui.server.chat]); // eslint-disable-line

  const askTyped = async (e) => {
    e?.preventDefault?.();
    const q = typed.trim();
    if (!q) return;
    setTyped("");
    setVstate("thinking");
    const reply = await chat.send(q);
    setAnswer(reply || "");
    setVstate(live ? "listening" : "");
  };

  const picture = async () => {
    setDrawing(true);
    try {
      const { url } = await api.makeImage({ kind: "step", name: recipe.name, step: step.do, cue: step.cue });
      setPictures(p => ({ ...p, [i]: url }));
    } catch (e) { ui.say(e.message); }
    setDrawing(false);
  };

  const held = new Map(k.stock.map(a => [a.id, a]));
  const stateLabel = { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[vstate];
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
                                  onClick={() => goStep(n)} aria-label={`Go to step ${n + 1}`}>
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
          <span>{stateLabel || "Say “next”, “repeat”, or ask me anything. “Stop cooking” to end."}</span>
        </div>
      )}

      <div className="cook-stage">
        <div className="cook-recipe-summary">
          <span>{recipe.name}</span>
          <b>{recipe.minutes} min total</b>
          <small>{recipe.complexity === 1 ? "Easy" : recipe.complexity === 3 ? "Involved" : "Some work"}</small>
        </div>
        <button className="link cook-ing-toggle" onClick={() => setShowIngredients(!showIngredients)}>
          <Icon name="list" size={20} /> {showIngredients ? "Hide ingredients" : `Ingredients for ${serves}`}
        </button>
        {showIngredients && (
          <ul className="ings compact">
            {recipe.needs.map(n => {
              const r = E.ref(n.id);
              const want = E.scale(n.qty, serves, recipe.serves, r.unit);
              const it = held.get(n.id);
              const have = it ? E.convert(it.qty, it.unit || r.unit, r.unit, r) : 0;
              return <li key={n.id} className={"ing" + (have < want ? " is-short" : "")}>
                <span className="ing-qty">{E.formatQty(want, r.unit)}</span>
                <span className="ing-name">{r.name}{n.prep && <small>{n.prep}</small>}</span></li>;
            })}
            {(recipe.extras || []).map(x => <li key={x} className="ing"><span className="ing-qty">·</span><span className="ing-name">{x}</span></li>)}
          </ul>
        )}

        <div className="cook-step-heading">
          <p className="cook-stepno">Step {i + 1} of {total}</p>
          <span className="cook-percent">{Math.round(((i + 1) / total) * 100)}% of the dish</span>
        </div>

        {(step.heat || step.cue) && (
          <div className="kpts">
            {step.heat && <span className="kpt kpt-heat"><Icon name="flame" size={16} /> {step.heat}</span>}
            {step.cue && <span className="kpt kpt-cue">Watch for: {step.cue.toLowerCase()}</span>}
            {step.minutes >= 3 && <span className="kpt kpt-time"><Icon name="clock" size={16} /> about {step.minutes} min</span>}
          </div>
        )}

        <div className="cook-keypoints">
          <h2 className="cook-do">Do this</h2>
          <ul>{keyPoints(step.do).map((point, n) => <li key={n}>{point}</li>)}</ul>
          {step.why && <p className="cook-why">{step.why}</p>}
        </div>

        {pictures[i] && <img className="cook-pic" src={pictures[i]} alt={`What “${step.do}” should look like`} />}

        {answer && (
          <div className="cook-answer">
            <span className="cook-answer-ic"><Icon name="chef" size={20} /></span>
            <p>{answer}</p>
          </div>
        )}

        <div className="cook-tools">
          {step.minutes >= 3 && (
            <button className="btn btn-small btn-ghost" onClick={() => ui.startTimer(step.minutes, step.do.slice(0, 40))}>
              <Icon name="clock" size={20} /> {step.minutes} min timer
            </button>
          )}
          <button className="btn btn-small btn-ghost" onClick={() => speak(stepBrief(step, i, total))}>
            <Icon name="speaker" size={20} /> Read aloud
          </button>
          {ui.server.images && !pictures[i] && (
            <button className="btn btn-small btn-ghost" onClick={picture} disabled={drawing}>
              <Icon name="image" size={20} /> {drawing ? "Drawing…" : "Show me"}
            </button>
          )}
        </div>

        <div className="cook-nav">
          <button className="btn btn-ghost" disabled={i === 0} onClick={() => goStep(i - 1)}>
            <Icon name="back" /> Back
          </button>
          {last
            ? <button className="btn btn-primary" onClick={finish}>Finished</button>
            : <button className="btn btn-primary" onClick={() => goStep(i + 1)}>Next step <Icon name="next" /></button>}
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

/* One step, said like a cook — no field labels, heat woven in, then it hands the
   turn back to you: it asks, then waits for "done" (or a question). */
function stepBrief(s, idx, total, ask = true) {
  const lead = idx + 1 === total ? "Last step. " : `Step ${idx + 1}. `;
  const heat = s.heat ? `On ${s.heat} heat, ` : "";
  const body = heat ? heat + s.do.charAt(0).toLowerCase() + s.do.slice(1) : s.do;
  const cue = s.cue ? ` Look for ${s.cue.toLowerCase()}.` : "";
  const prompt = idx + 1 === total ? " And that's it — enjoy." : " Tell me when that's done.";
  return lead + body + cue + (ask ? prompt : "");
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
