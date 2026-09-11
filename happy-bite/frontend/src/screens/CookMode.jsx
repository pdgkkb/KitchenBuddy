/* Cooking mode — one step at a time, big, with the chef beside it.

   The steps work with the server off: heat, cue, why and timer are all in
   the recipe. The chef is the extra — ask how it should look, what to swap,
   whether it's done — and it knows which step you're on. */

import { useCallback, useEffect, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { useChat, ChatThread, Composer } from "../components/Chat.jsx";

export default function CookMode() {
  const { k, finishCooking, setPref, saveRecipe } = useKitchen();
  const ui = useUI();
  const { recipe, serves } = ui.cooking;
  const [i, setI] = useState(0);
  const [showIngredients, setShowIngredients] = useState(false);
  const [pictures, setPictures] = useState({});
  const [drawing, setDrawing] = useState(false);
  const [panel, setPanel] = useState("step");            // narrow screens: step | chat
  const step = recipe.steps[i];
  const last = i === recipe.steps.length - 1;

  const context = useCallback(() => ({
    mode: "cooking", recipe, step: i, serves, stock: stockForServer(k.stock)
  }), [recipe, i, serves, k.stock]);

  const chat = useChat(context,
    `I'm with you for the whole ${recipe.name.toLowerCase()}. Ask me anything — how it should look, what to swap, whether it's done.`);

  useEffect(() => { if (k.prefs.readSteps) voice.speak(stepSpeech(step), false); }, [i]); // eslint-disable-line
  useEffect(() => () => voice.stopSpeaking(), []);

  const suggestions = useMemo(() => [
    step.cue ? `What does “${step.cue.toLowerCase()}” look like?` : "How do I know this step is done?",
    "What can I use instead?",
    step.heat ? "My hob runs hot — what should I set?" : "Can I do this ahead?",
    "Is it safe to eat?"
  ], [step]);

  const picture = async () => {
    setDrawing(true);
    try {
      const { url } = await api.makeImage({ kind: "step", name: recipe.name, step: step.do, cue: step.cue });
      setPictures(p => ({ ...p, [i]: url }));
    } catch (e) { ui.say(e.message); }
    setDrawing(false);
  };

  const finish = () => {
    if (!k.book.some(r => r.id === recipe.id)) saveRecipe(recipe);
    finishCooking(recipe, serves);
    ui.stopTimer();
    ui.stopCooking();
    ui.openSheet("review", { recipe });
  };

  const held = new Map(k.stock.map(a => [a.id, a]));

  return (
    <div className="cook" role="dialog" aria-label={`Cooking ${recipe.name}`}>
      <header className="cook-head">
        <button className="icon-btn" onClick={ui.stopCooking} aria-label="Leave cooking mode"><Icon name="close" /></button>
        <div className="cook-title">
          <p className="cook-name">{recipe.name}</p>
          <ol className="progress" aria-label={`Step ${i + 1} of ${recipe.steps.length}`}>
            {recipe.steps.map((_, n) => (
              <li key={n}><button className={n < i ? "is-done" : n === i ? "is-now" : ""}
                                  onClick={() => setI(n)} aria-label={`Go to step ${n + 1}`} /></li>
            ))}
          </ol>
        </div>
        <button className={"icon-btn" + (k.prefs.readSteps ? " is-on" : "")} aria-pressed={k.prefs.readSteps}
                onClick={() => setPref("readSteps", !k.prefs.readSteps)}
                aria-label={k.prefs.readSteps ? "Stop reading steps aloud" : "Read steps aloud"}>
          <Icon name={k.prefs.readSteps ? "speaker" : "mute"} />
        </button>
      </header>

      <div className="cook-switch" role="tablist">
        <button role="tab" aria-selected={panel === "step"} onClick={() => setPanel("step")}>Step {i + 1}</button>
        <button role="tab" aria-selected={panel === "chat"} onClick={() => setPanel("chat")}>
          Ask the chef{chat.busy ? "…" : ""}
        </button>
      </div>

      <div className={"cook-body show-" + panel}>
        <section className="cook-step">
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

          {(step.heat || step.cue) && (
            <p className="step-heat">
              {step.heat && <span className="heat-tag"><Icon name="flame" size={18} />{step.heat}</span>}
              {step.cue && <span className="heat-cue">until {step.cue.toLowerCase()}</span>}
            </p>
          )}
          <p className="cook-do">{step.do}</p>
          {step.why && <p className="cook-why">{step.why}</p>}

          {pictures[i] && <img className="cook-pic" src={pictures[i]} alt={`What “${step.do}” should look like`} />}

          <div className="cook-tools">
            {step.minutes > 0 && (
              <button className="btn btn-small btn-ghost" onClick={() => ui.startTimer(step.minutes, step.do.slice(0, 40))}>
                <Icon name="clock" size={20} /> {step.minutes} min timer
              </button>
            )}
            <button className="btn btn-small btn-ghost" onClick={() => voice.speak(stepSpeech(step), false)}>
              <Icon name="speaker" size={20} /> Read aloud
            </button>
            {ui.server.images && !pictures[i] && (
              <button className="btn btn-small btn-ghost" onClick={picture} disabled={drawing}>
                <Icon name="image" size={20} /> {drawing ? "Drawing…" : "Show me"}
              </button>
            )}
          </div>

          <div className="cook-nav">
            <button className="btn btn-ghost" disabled={i === 0} onClick={() => setI(i - 1)}>
              <Icon name="back" /> Back
            </button>
            {last
              ? <button className="btn btn-primary" onClick={finish}>Finished</button>
              : <button className="btn btn-primary" onClick={() => setI(i + 1)}>Next step <Icon name="next" /></button>}
          </div>
        </section>

        <aside className="cook-chat">
          {ui.server.chat ? (
            <>
              <ChatThread messages={chat.messages}
                          onSaveRecipe={(r) => { saveRecipe(r); ui.say("Saved to your recipes"); }}
                          onCookRecipe={(r) => { saveRecipe(r); ui.startCooking(r, serves); setI(0); }} />
              <Composer onSend={chat.send} busy={chat.busy} onStop={chat.stop}
                        placeholder="Ask about this step" suggestions={chat.messages.length < 4 ? suggestions : []} />
            </>
          ) : (
            <div className="offline-note">
              <Icon name="chef" size={36} />
              <p><b>The chef is off.</b> Start the server with a key to ask questions while you cook.
                Steps, timers and reading aloud keep working without it.</p>
              <button className="btn btn-small btn-ghost" onClick={ui.refreshServer}>Check again</button>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

const stepSpeech = (s) =>
  [s.heat && `Heat: ${s.heat}.`, s.do, s.cue && `Until ${s.cue.toLowerCase()}.`, s.why].filter(Boolean).join(" ");
