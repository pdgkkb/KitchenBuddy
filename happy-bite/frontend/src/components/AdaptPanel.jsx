/* Happy Bite — cooking this dish with the kit you've actually got.

   A recipe that says "Oven 200 °C" is useless in a kitchen with no oven, and
   the app already knows both halves of that: the recipe's own words say what it
   wants, and the household has said what it owns. So the panel appears by
   itself, before anyone has to be disappointed.

   Three things it refuses to do:

     * It never rewrites the recipe. The author's version stays the authority;
       this sits beside it, clearly marked as the way round.
     * It never pretends. "No, not with these" is a first-class answer with its
       own colour, because a confident adaptation that ruins dinner is far worse
       than being told to plan something else.
     * It never asks the model twice for the same question. An answer is kept
       against the recipe AND the exact equipment it was written for, so buying
       an air fryer invalidates it instead of quietly serving yesterday's. */

import { useState } from "react";
import * as api from "../lib/api.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "./Icon.jsx";
import { adaptKey, equipList, equipName, missingFor, wantedBy } from "../core/equipment.js";
import "../styles/equipment.css";

/* Appliances worth asking about. Nobody needs prompting about owning a saucepan;
   an oven, an air fryer or a pressure cooker changes what's cookable. */
const NOTABLE = ["oven", "grill", "airfryer", "microwave", "pressure", "slowcooker", "steamer", "bbq"];

const TONE = { yes: "is-yes", partly: "is-partly", no: "is-no" };
const LEAD = { yes: "Yes — here's how", partly: "Partly", no: "Not with these" };

export default function AdaptPanel({ recipe }) {
  const { k, saveAdaptation, forgetAdaptation } = useKitchen();
  const ui = useUI();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState(false);
  const [question, setQuestion] = useState("");

  const owned = k.equipment;                       // null = never said
  const key = adaptKey(recipe.id, owned);
  const saved = k.adaptations?.[key];
  const missing = missingFor(recipe, owned);
  const wants = wantedBy(recipe);

  /* Nobody has said what they cook with. Ask once, quietly, and only where the
     recipe leans on something worth knowing about. */
  if (!owned) {
    if (!wants.some(id => NOTABLE.includes(id))) return null;
    return (
      <div className="adapt adapt-ask-first">
        <p className="adapt-first-text">
          This one wants {equipList(wants.filter(id => NOTABLE.includes(id)))}.
          Tell me what you cook with and I'll flag the ones you can't do — and work out a way round.
        </p>
        <button className="btn btn-small btn-ghost"
                onClick={() => ui.openSheet("equipment", { recipe: recipe.id })}>
          <Icon name="list" size={20} /> What I cook with
        </button>
      </div>
    );
  }

  const ask = async (extra) => {
    if (!ui.server.chat) {
      setError("The chef is off. Start the server and I'll work out the way round.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const { adaptation } = await api.adaptRecipe({
        recipe, equipment: owned, serves: k.diners.length || recipe.serves,
        question: extra || "", custom: k.customs,
      });
      saveAdaptation(key, adaptation);
      setAsking(false);
      setQuestion("");
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  };

  /* Everything it needs is here and nothing has been asked: stay out of the way,
     but leave the door open for "could I do this in the air fryer instead?". */
  if (!missing.length && !saved && !asking) {
    return (
      <p className="adapt-quiet">
        <Icon name="list" size={16} /> You've got everything this needs.{" "}
        <button className="link" onClick={() => setAsking(true)}>Cook it another way?</button>
      </p>
    );
  }

  return (
    <div className="adapt">
      {missing.length > 0 && !saved && (
        <div className="adapt-head">
          <p className="adapt-need">
            <Icon name="flame" size={18} />
            This recipe wants {equipList(missing)} — not on your list.
          </p>
          <p className="adapt-sub">
            I can work out whether it's doable with {equipList(owned.slice(0, 4))}
            {owned.length > 4 ? " and the rest" : ""}, and say so plainly if it isn't.
          </p>
          <button className="btn btn-small btn-primary" onClick={() => ask("")} disabled={busy}>
            {busy ? "Working it out…" : `Cook it without the ${equipName(missing[0]).toLowerCase()}`}
          </button>
        </div>
      )}

      {saved && <Adaptation a={saved} recipe={recipe} busy={busy}
                            onAgain={() => setAsking(true)}
                            onForget={() => { forgetAdaptation(key); setError(""); }} />}

      {asking && (
        <form className="adapt-ask" onSubmit={(e) => { e.preventDefault(); ask(question.trim()); }}>
          <label className="field-label" htmlFor="adapt-q">What have you got in mind?</label>
          <input id="adapt-q" className="field" value={question} disabled={busy}
                 placeholder="I've got a pan and a microwave but no oven — can I still do this?"
                 onChange={(e) => setQuestion(e.target.value)} />
          <div className="row-actions" style={{ marginTop: 12 }}>
            <button type="button" className="btn btn-small btn-ghost" onClick={() => setAsking(false)}>Cancel</button>
            <button type="submit" className="btn btn-small btn-primary" disabled={busy}>
              {busy ? "Working it out…" : "Ask the chef"}
            </button>
          </div>
        </form>
      )}

      {error && <p className="adapt-error">{error}</p>}

      <button className="link adapt-edit" onClick={() => ui.openSheet("equipment", { recipe: recipe.id })}>
        Change what I cook with
      </button>
    </div>
  );
}

function Adaptation({ a, recipe, busy, onAgain, onForget }) {
  const steps = recipe.steps || [];
  return (
    <div className={"adapt-result " + (TONE[a.possible] || "is-partly")}>
      <p className="adapt-verdict">
        <span className="adapt-lead">{LEAD[a.possible] || "Maybe"}</span>
        {a.verdict}
      </p>

      {(a.using?.length > 0 || a.insteadOf?.length > 0) && (
        <p className="adapt-swap">
          {a.using?.length > 0 && <>Using {equipList(a.using)}</>}
          {a.insteadOf?.length > 0 && <> instead of {equipList(a.insteadOf)}</>}.
        </p>
      )}

      {a.changes?.length > 0 && (
        <>
          <h4 className="adapt-head-steps">What changes</h4>
          <ol className="adapt-steps">
            {a.changes.map(c => (
              <li key={c.step} className="adapt-step">
                <span className="adapt-stepno">{c.step}</span>
                <div>
                  {steps[c.step - 1] && (
                    <p className="adapt-was">Instead of: {steps[c.step - 1].do}</p>
                  )}
                  <p className="adapt-do">{c.do}</p>
                  {(c.heat || c.cue || c.minutes >= 3) && (
                    <p className="adapt-kpts">
                      {c.heat && <span className="kpt kpt-heat"><Icon name="flame" size={16} /> {c.heat}</span>}
                      {c.minutes >= 3 && <span className="kpt kpt-time"><Icon name="clock" size={16} /> about {c.minutes} min</span>}
                      {c.cue && <span className="kpt kpt-cue">Watch for: {c.cue.toLowerCase()}</span>}
                    </p>
                  )}
                  {c.why && <p className="adapt-why">{c.why}</p>}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}

      {a.watch?.length > 0 && (
        <div className="adapt-watch">
          <h4 className="adapt-head-steps">Honestly, it won't be identical</h4>
          <ul>{a.watch.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      <div className="row-actions adapt-actions">
        <button className="btn btn-small btn-ghost" onClick={onAgain} disabled={busy}>Ask something else</button>
        <button className="link" onClick={onForget}>Throw this away</button>
      </div>
    </div>
  );
}
