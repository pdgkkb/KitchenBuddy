/* The recipe, before you start: what you need for this table, how you
   want it done, the method at a glance — then cooking mode.

   On a wide screen it opens as a near-full-width two-column sheet: the photo,
   the details and everything you need on the LEFT, the method steps on the
   RIGHT. On a phone the two stack. "Start cooking" takes it full-screen. */

import { useState } from "react";
import * as E from "../core/engine.js";
import { realCue, stepHeat } from "../core/brief.js";
import * as api from "../lib/api.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import { DishTags, nameOf } from "../screens/Today.jsx";
import PhotoStrip from "../components/PhotoStrip.jsx";
import "../styles/method.css";
import AdaptPanel from "../components/AdaptPanel.jsx";

/* Everything about the dish EXCEPT the method: what you need, seasoning,
   options, ideas. This is the left column on a wide screen. */
/* How many people this is for. The recipe keeps the quantities it was written
   with; everything shown (ingredients, seasoning, what's short) and everything
   cooking mode says is scaled from r.serves to this number with E.scale.
   Nothing is rewritten, so going from 2 to 6 and back to 2 is exact. */
export const MAX_SERVES = 20;                      // the server accepts up to 20 in cooking mode

export function ServesStepper({ serves, setServes, written }) {
  const n = Math.max(1, Math.round(Number(serves) || 1));
  const set = (to) => setServes(Math.min(MAX_SERVES, Math.max(1, to)));
  return (
    <span className="serves-step" role="group" aria-label="Number of servings">
      <button type="button" className="serves-btn" onClick={() => set(n - 1)} disabled={n <= 1}
              aria-label="One serving fewer">−</button>
      <span className="serves-num" aria-live="polite">{n} {n === 1 ? "person" : "people"}</span>
      <button type="button" className="serves-btn" onClick={() => set(n + 1)} disabled={n >= MAX_SERVES}
              aria-label="One serving more">+</button>
      {Number(written) > 0 && Number(written) !== n && (
        <button type="button" className="serves-reset" onClick={() => set(Number(written))}>
          written for {written}
        </button>
      )}
    </span>
  );
}

export function RecipeDetails({ recipe: r, serves, setServes, choices, setChoices }) {
  const { k, addWish } = useKitchen();
  const ui = useUI();
  const held = new Map(k.stock.map(a => [a.id, a]));
  const strength = choices.__strength || "As written";
  const mult = strength === "Gentle" ? 0.6 : strength === "Bold" ? 1.5 : 1;
  const ideas = E.seasoningIdeas(r, k.stock);
  const learned = k.taste.tweaks[r.id];

  return (
    <>
      {learned?.length > 0 && (
        <div className="band is-warm"><b>Last time you said</b><span>{learned.join(", ")}</span></div>
      )}

      {/* Said out loud rather than quietly repaired. A method that mentions an
          ingredient the recipe doesn't contain is the model losing the thread,
          and the cook should find that out here, not with the pan already hot. */}
      {r.adjusted?.length > 0 && (
        <div className="band is-warm">
          <b>Quantities brought down</b>
          <span>The assistant asked for more than anyone could eat, so these were
            trimmed: {r.adjusted.join("; ")}. Treat them as a starting point.</span>
        </div>
      )}

      {/* The method used these and the list had no amount for them, so the
          assistant was asked for just the amounts (backend recipes.add_amounts). */}
      {r.added?.length > 0 && (
        <div className="band">
          <b>Added to the ingredients</b>
          <span>The method uses these, so they were added with an amount: {r.added.join("; ")}.</span>
        </div>
      )}

      {/* Sent back once with this list and still wrong. The server's words,
          because they are the specific ones: "step 2 says 255 minutes". */}
      {r.problems?.length > 0 && (
        <div className="band is-warm">
          <b>Check this one before you start</b>
          <span>The assistant was asked to fix it and didn't manage: {r.problems.join("; ")}.</span>
        </div>
      )}

      {!r.problems?.length && stillWrong(r).length > 0 && (
        <div className="band is-warm">
          <b>Read the method twice</b>
          <span>It mentions something this recipe doesn't contain — {stillWrong(r).join(", ")}.
            The assistant lost the thread; trust the ingredients above.</span>
        </div>
      )}

      <div className="serves-head">
        <h3 className="sub-head">What you need for{setServes ? "" : ` ${serves}`}</h3>
        {setServes && <ServesStepper serves={serves} setServes={setServes} written={r.serves} />}
      </div>
      <ul className="ings">
        {r.needs.map(n => {
          const rr = E.ref(n.id);
          const want = E.scale(n.qty, serves, r.serves, rr.unit);
          const it = held.get(n.id);
          const have = it ? E.convert(it.qty, it.unit || rr.unit, rr.unit, rr) : 0;
          const short = have < want && !n.flexible;
          return (
            <li key={n.id} className={"ing" + (short ? " is-short" : "")}>
              {/* A quantity of zero means the recipe named the ingredient and
                  never said how much. Printing "0 g" would be a lie with a
                  decimal point in it. */}
              <span className="ing-qty">{want > 0 ? E.formatQty(want, rr.unit) : "some"}</span>
              {/* The shelf it comes from, drawn. At a glance a list of twelve
                  lines becomes four groups, which is how a cook reads it. */}
              <Icon name={rr.category} size={20} className="ing-ic" />
              <span className="ing-name">{rr.name}{n.prep && <small>{n.prep}</small>}</span>
              {short && <span className="ing-flag">need {E.formatQty(want - have, rr.unit)}</span>}
            </li>
          );
        })}
      </ul>
      {r.extras?.length > 0 && <>
        <p className="sub-note" style={{ marginTop: 14 }}>Also, as the recipe wrote them — not tracked in your kitchen:</p>
        <ul className="ings">{r.extras.map(x => <li key={x} className="ing"><span className="ing-name">{x}</span></li>)}</ul>
      </>}

      {(r.seasoning || []).length > 0 && <>
        <h3 className="sub-head">Seasoning</h3>
        <div className="chips">
          {["Gentle", "As written", "Bold"].map(v => (
            <button key={v} className={"chip" + (v === strength ? " is-on" : "")}
                    onClick={() => setChoices({ ...choices, __strength: v })}>{v}</button>
          ))}
        </div>
        <ul className="ings">
          {r.seasoning.filter(x => held.has(x.id) || x.essential).map(x => {
            const rr = E.ref(x.id);
            const qty = x.qty ? E.scale(x.qty * mult, serves, r.serves, rr.unit) : 0;
            // "1 g" of salt is a pinch, and nobody weighs a pinch.
            const amount = x.toTaste || !x.qty ? "to taste"
              : rr.unit === "g" && qty <= 1.5 ? "a pinch"
              : E.formatQty(qty, rr.unit);
            return <li key={x.id} className="ing"><span className="ing-qty">{amount}</span>
              <span className="ing-name">{rr.name}</span></li>;
          })}
        </ul>
      </>}

      {ideas.length > 0 && (
        <div className="ideas">
          <p className="ideas-head">Next time, try</p>
          <ul>
            {ideas.map(i => (
              <li key={i.id}><span className="idea-name">{i.name}</span><span className="idea-origin">{i.origin}</span>
                <button className="chip chip-sm" onClick={() => { addWish(i.id); ui.say(i.name + " added to shopping"); }}>Add to shopping</button></li>
            ))}
          </ul>
        </div>
      )}

      {/* In RecipeDetails rather than the sheet, so the draft of a brand-new
          recipe gets it too — that is exactly when you find out it wants an
          oven you haven't got. */}
      <AdaptPanel recipe={r} />

      {r.needs.filter(n => n.doneness && E.ref(n.id).doneness).map(n => {
        const opts = E.ref(n.id).doneness;
        const chosen = choices[n.id] || opts[Math.floor(opts.length / 2)];
        return (
          <div key={n.id} className="option-block">
            <p className="option-label">How do you want the {nameOf(n.id).toLowerCase()}?</p>
            <div className="chips">
              {opts.map(o => <button key={o} className={"chip" + (o === chosen ? " is-on" : "")}
                                     onClick={() => setChoices({ ...choices, [n.id]: o })}>{o}</button>)}
            </div>
          </div>
        );
      })}
    </>
  );
}

/* The method — the right column on a wide screen. */
export function RecipeMethod({ recipe: r }) {
  return (
    <>
      <h3 className="sub-head">Method</h3>
      <ol className="steps">
        {r.steps.map((st, i) => (
          <li key={i} className="step">
            <span className="step-no">{i + 1}</span>
            <div>
              {/* "Watch closely" is for a short step at the stove, not for
                  tossing chicken in a bowl. */}
              <p className="step-label">Step {i + 1}{E.stepMinutes(st) >= 3 ? ` · ${E.stepMinutes(st)} min`
                : stepHeat(st) ? " · watch closely" : ""}</p>
              {(stepHeat(st) || realCue(st)) && <p className="step-heat">
                {stepHeat(st) && <span className="heat-tag"><Icon name="flame" size={16} />{stepHeat(st)}</span>}
                {realCue(st) && <span className="heat-cue">until {realCue(st).toLowerCase()}</span>}</p>}
              <p className="step-do">{st.do}</p>
              {st.uses?.length > 0 && (
                <p className="now-in">
                  <span className="now-in-label">In now</span>
                  {st.uses.map(id => <span key={id} className="now-in-item">{nameOf(id)}</span>)}
                </p>
              )}
              {st.why && <p className="step-why">{st.why}</p>}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

/* Both, one after another — used where a single column is wanted (the draft
   sheet). The two-column recipe sheet places the halves side by side itself. */
export function RecipeView(props) {
  return <><RecipeDetails {...props} /><RecipeMethod recipe={props.recipe} /></>;
}

export function PhotoButton({ recipe, onDone }) {
  const ui = useUI();
  const [busy, setBusy] = useState(false);
  if (!ui.server.images) return null;
  const go = async () => {
    setBusy(true);
    try {
      const { url } = await api.makeImage({ kind: "dish", name: recipe.name, cuisine: recipe.cuisine, description: recipe.description });
      onDone(url);
    } catch (e) { ui.say(e.message); }
    setBusy(false);
  };
  return (
    <button className="photo-btn" onClick={go} disabled={busy}>
      <Icon name="image" size={20} /> {busy ? "Making a photo…" : recipe.photo ? "New photo" : "Make a photo"}
    </button>
  );
}

export default function RecipeSheet({ id }) {
  const { k, togglePlanned, deleteRecipe, setPhoto } = useKitchen();
  const ui = useUI();
  const r = k.book.find(x => x.id === id);
  const [choices, setChoices] = useState({});
  const [picked, setServes] = useState(null);      // null: the household's diners, as before
  if (!r) return null;
  const serves = picked ?? (k.diners.length || r.serves);
  const planned = k.planned.includes(r.id);

  return (
    <div className="sheet recipe-sheet">
      <div className="sheet-scrim" onClick={ui.closeSheet} />
      <section className="sheet-panel is-bleed is-recipe-wide" role="dialog" aria-modal="true" aria-label={r.name}>
        <div className="recipe-cols">
          <div className="recipe-left">
            <DishImage recipe={r} className="dish-photo dish-bleed">
              <button className="icon-btn on-photo close-photo" onClick={ui.closeSheet} aria-label="Close"><Icon name="close" /></button>
              <div className="dish-overlay">
                <h2 className="dish-name">{r.name}</h2>
                <DishTags recipe={r} />
              </div>
              <PhotoButton recipe={r} onDone={(url) => setPhoto(r.id, url)} />
            </DishImage>
            <div className="sheet-body">
              {r.description && <p className="lead">{r.description}</p>}
              {r.source && <p className="sub-note">From <a href={r.source.url} target="_blank" rel="noreferrer">{r.source.site}</a>. Adapted for your kitchen; the original is the authority.</p>}
              <PhotoStrip recipe={r} />
              <RecipeDetails recipe={r} serves={serves} setServes={setServes} choices={choices} setChoices={setChoices} />
            </div>
          </div>
          <div className="recipe-right">
            <div className="sheet-body">
              <RecipeMethod recipe={r} />
            </div>
          </div>
        </div>
        <footer className="sheet-foot">
          <button className="btn btn-ghost" onClick={() => { togglePlanned(r.id); ui.say(planned ? "Taken off the plan" : "Planned — missing items are on the shopping list"); }}>
            {planned ? "Planned" : "Plan it"}
          </button>
          <button className="btn btn-primary" onClick={() => ui.startCooking({ ...r, choices }, serves)}>Start cooking</button>
        </footer>
        {r.origin && <button className="link link-block danger" onClick={() => { deleteRecipe(r.id); ui.closeSheet(); ui.say("Recipe deleted"); }}>
          <Icon name="trash" size={18} /> Delete this recipe</button>}
      </section>
    </div>
  );
}

/* The "mentions something this recipe doesn't contain" lines saved with a
   recipe, minus the ones the server's check got wrong before it was fixed:
   "step 1 mentions chicken" on a recipe made of chicken breast, "mentions
   marinade". Recipes written since are checked properly on the server; this
   keeps older ones in the book from crying wolf. */
const NOT_FOODS = new Set(["ingredient", "marinade", "mixture", "sauce", "dressing", "filling", "garnish",
  "season", "serve", "water", "salt", "pepper", "stock", "whole", "savory", "olive", "sprinkle"]);
const undoubled = (w) => w.replace(/(.)\1+/g, "$1");
function stillWrong(r) {
  const words = new Set();
  [...(r.needs || []), ...(r.seasoning || [])].forEach(n =>
    String(E.ref(n.id).name || n.id).toLowerCase().split(/[^a-z]+/).filter(Boolean)
      .forEach(w => { words.add(w); words.add(w.replace(/e?s$/, "")); words.add(undoubled(w)); }));
  (r.extras || []).forEach(x => String(x).toLowerCase().split(/[^a-z]+/).forEach(w => words.add(w)));
  return (r.contradictions || []).filter(line => {
    const w = (line.match(/mentions (\w+)/) || [])[1] || "";
    const bare = w.replace(/e?s$/, "");
    return !NOT_FOODS.has(bare) && !NOT_FOODS.has(w) && !words.has(w) && !words.has(bare)
      && !words.has(undoubled(w)) && !words.has(undoubled(bare));
  });
}
