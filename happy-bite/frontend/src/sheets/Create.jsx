/* New recipes are invented by the assistant or read from a link. Nothing is
   saved until a person has read it and named it.

   The waiting states are the change here. A local Qwen takes ten to thirty
   seconds to write a recipe; the panel used to respond to that by greying out
   a button, which is indistinguishable from a crash. Now the panel is replaced
   by a narration of the phases, and a failure leaves that narration on screen
   so the error has somewhere to sit. */

import { useEffect, useRef, useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import PhotoStrip from "../components/PhotoStrip.jsx";
import Stars from "../components/Stars.jsx";
import Waiting, { METHOD_STAGES, LINK_STAGES } from "../components/Waiting.jsx";
import { RecipeDetails, RecipeMethod, PhotoButton } from "./RecipeSheet.jsx";
import "../styles/draft.css";

export function CreateSheet({ options: initialOptions = null, autoGenerate = false, initialBrief = "", maxMinutes = null,
                              anyIngredients: initialAny = false }) {
  const { k } = useKitchen();
  const ui = useUI();
  const [brief, setBrief] = useState(initialBrief);
  // Off: built from what's in the kitchen. On: any dish, the rest gets bought.
  const [anyIngredients, setAnyIngredients] = useState(initialAny);
  const [options, setOptions] = useState(initialOptions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  /* Which of the two requests is running ("ideas" or "method"), and the phase
     the server last reported. Null means it isn't narrating and Waiting should
     fall back to its own pacing. */
  const [phase, setPhase] = useState("ideas");
  const [stage, setStage] = useState(null);
  const [chosen, setChosen] = useState("");
  const on = ui.server.recipes;
  const soon = k.stock.filter(a => E.daysLeft(a) <= 3).map(a => E.ref(a.id).name.toLowerCase());

  /* TWO REQUESTS, ONE RECIPE.
     ----------------------------------------------------------------------
     LM Studio showed two "Generating" rows for one recipe, the same prompt,
     the same token count, started together. The machine wrote the recipe
     twice, at half the speed, and threw one away.

     main.jsx renders the app inside <StrictMode>, and in development React 18
     deliberately mounts, runs effects, cleans up and runs them AGAIN on the
     same component, to flush out effects that aren't safe to repeat. This one
     wasn't. Its `!busy` guard reads the `busy` captured when the closure was
     made — false both times — so setBusy in between changes nothing it can
     see, and make() fires twice.

     A ref is the guard that works, because it survives that second mount
     while state does not. */
  const startedRef = useRef(false);
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    if (autoGenerate && !initialOptions && !error) make();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* One dish, one call. This used to ask the model for two ideas, show them,
     and then ask again for the full method of whichever you picked — two round
     trips, each re-sending the whole prompt from scratch. On a local model that
     is the single most expensive thing the app does, and choosing between two
     one-line descriptions was never worth it: you can always ask for another. */
  const make = async () => {
    // Nothing starts a second generation while one is running — not a double
    // mount, not a double tap, not Retry pressed twice. A minute of this
    // machine's attention is too expensive to spend twice on the same dish.
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setBusy(true);
    setError(null);
    setPhase("method");
    setChosen("");
    setStage(null);
    if (!on) {
      setBusy(false);
      setError("The assistant is switched off. Start the server to create a new recipe.");
      return;
    }
    try {
      const { recipe } = await api.generateRecipeStream({
        options: false,
        brief: brief || "Something good for tonight", stock: stockForServer(k.stock),
        serves: k.diners.length || 4, custom: k.customs, anyIngredients,
        // Only when the brief is still the one it came with. Edited, the
        // server reads the time out of the new words instead.
        ...(maxMinutes && brief === initialBrief ? { maxMinutes } : {})
      }, setStage);
      setBusy(false);
      inFlightRef.current = false;
      ui.openSheet("draft", { draft: recipe });
    } catch (e) {
      setBusy(false);
      inFlightRef.current = false;
      setError(e.message);
    }
  };

  const choose = async (idea) => {
    setOptions(null);
    setBusy(true);
    setPhase("method");
    setStage(null);
    setChosen(idea.name);
    try {
      /* The dish is already decided, so this wait says so. The pictures are not
         waited for at all: the recipe opens the moment it lands and PhotoStrip
         asks for them afterwards, on the server's own background worker. */
      const { recipe } = await api.generateRecipeStream({
        options: false,
        brief: `Write the full recipe for this chosen idea: ${idea.name}. ${idea.description}`,
        stock: stockForServer(k.stock), serves: k.diners.length || 4, custom: k.customs, anyIngredients
      }, setStage);
      setBusy(false);
      ui.openSheet("draft", { draft: recipe });
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  };

  if (busy || error) return (
    <Waiting
      stages={METHOD_STAGES}
      stage={stage}
      error={error}
      title={phase === "method" && chosen ? `Writing ${chosen}` : "Working on it"}
      note={ui.server.model ? `${ui.server.model} on this Mac` : "The assistant"}
      onRetry={() => { setError(null); make(); }}
      onCancel={() => { setBusy(false); setError(null); }}
    />
  );

  if (options) return <RecipeOptions options={options} onChoose={choose}
    onBack={() => setOptions(null)} />;

  return (
    <>
      <p className="sub-note">{on
        ? (anyIngredients
          ? "The assistant writes whatever suits the request. Anything you haven't got goes on the shopping list."
          : "The assistant writes this from what's in your kitchen, using what goes off first.")
        : "The assistant is switched off. Start the server to create a new recipe."}</p>
      {!on && <div className="band is-warm">
        <b>{ui.server.online ? "Chef model unavailable" : "Backend not reachable"}</b>
        <span>{ui.server.online ? "Check the assistant settings or model configuration." : "Start the backend on port 8000, then check again."}</span>
        <button className="btn btn-small btn-ghost" onClick={ui.refreshServer}>Check again</button>
      </div>}
      {on && (
        <div className="chips" role="group" aria-label="Ingredients" style={{ marginBottom: 16 }}>
          <button className={`chip chip-sm${anyIngredients ? "" : " is-on"}`} aria-pressed={!anyIngredients}
                  onClick={() => setAnyIngredients(false)}>From my kitchen</button>
          <button className={`chip chip-sm${anyIngredients ? " is-on" : ""}`} aria-pressed={anyIngredients}
                  onClick={() => setAnyIngredients(true)}>Any ingredients</button>
        </div>
      )}
      <label className="field-label" htmlFor="brief">What do you want?</label>
      <textarea id="brief" className="field field-area" rows={3} value={brief} onChange={(e) => setBrief(e.target.value)}
                placeholder={on ? (soon.length ? `Something warm with the ${soon[0]} before it goes` : "Something comforting, 30 minutes") : "Name it, or leave blank"} />
      {on && (
        <div className="chips" style={{ marginTop: 12 }}>
          {["15 minutes", "One pan", "Kids will eat it", "Take our time", "Bold and spicy"].map(s => (
            <button key={s} className="chip chip-sm" onClick={() => setBrief(b => (b ? b + ", " : "") + s.toLowerCase())}>{s}</button>
          ))}
        </div>
      )}
      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={make} disabled={!on}>
        Write it
      </button>
    </>
  );
}

function RecipeOptions({ options, onChoose, onBack }) {
  return (
    <div className="recipe-options">
      <p className="sub-note">Pick your dish. Each one is a fresh recipe made for this request.</p>
      <ol className="option-cards">
        {options.map((recipe, index) => (
          <li key={recipe.id}>
            <button className="option-card" onClick={() => onChoose(recipe)}>
              <span className="option-number">{index + 1}</span>
              <span className="option-card-text">
                <strong>{recipe.name}</strong>
                <span>{recipe.description || `${recipe.cuisine} · ${recipe.minutes} minutes`}</span>
                <small>{recipe.needs.slice(0, 4).map(n => E.ref(n.id).name).join(" · ")}</small>
                <small>Difficulty <Stars recipe={recipe} size={15} /></small>
              </span>
              <Icon name="next" size={22} />
            </button>
          </li>
        ))}
      </ol>
      <button className="link link-block" onClick={onBack}>Change the request</button>
    </div>
  );
}

export function LinkSheet() {
  const { k } = useKitchen();
  const ui = useUI();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const read = async () => {
    setBusy(true); setError(null);
    try {
      const { recipe, method } = await api.importLink({ url: url.trim(), custom: k.customs });
      ui.openSheet("draft", { draft: recipe, method });
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  if (!ui.server.online) return (
    <p className="sub-note">Reading a website needs the server running — the browser can't fetch other sites on its own.
      Start it, then try again.</p>
  );

  if (busy) return (
    <Waiting
      stages={LINK_STAGES}
      note="Reading the page on the server"
      onCancel={() => setBusy(false)}
    />
  );

  return (
    <>
      <p className="sub-note">Paste a recipe from any cooking site. It's read, matched to your kitchen and{ui.server.recipes
        ? " given heat and cues for each step. The author's recipe stays the authority."
        : " kept as written — with the assistant on, it would also add heat and cues."}</p>
      <label className="field-label" htmlFor="url">Link</label>
      <div className="askbar">
        <input id="url" className="askbar-input" type="url" inputMode="url" value={url} placeholder="https://…"
               onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && url && read()} />
        {navigator.clipboard?.readText && (
          <button className="btn btn-small btn-ghost" onClick={async () => { try { setUrl(await navigator.clipboard.readText()); } catch { /* clipboard refused */ } }}>Paste</button>
        )}
      </div>
      {error && <div className="band is-warm"><b>That didn't work</b><span>{error}</span></div>}
      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={read} disabled={!/^https?:\/\//.test(url.trim())}>
        Read it
      </button>
      <p className="sub-note" style={{ marginTop: 16 }}>Tip: you can also paste a link straight into the chef's chat and cook it together.</p>
    </>
  );
}

/* The recipe you were just handed.

   Two changes, and the first is the one that matters. This panel used to hold
   the only copy of a recipe that had just cost a local model a full minute of
   the machine's attention — "Nothing is saved until a person has read it and
   named it", said the file's own header, which sounds careful and in practice
   is a shredder: a tap on the scrim outside the panel, the Escape key, or a
   second thought about the name threw the whole thing away with no warning and
   no way back. It is saved the moment it appears now. The name and the photo
   edit the saved copy, and the footer offers to throw it away — a deliberate
   act, rather than the default outcome of touching the wrong pixel.

   The second is the shape. A recipe has two halves you read differently: what
   you need, which you scan and check against the kitchen, and what you do,
   which you follow one line at a time. In one narrow column they interrupt
   each other. Side by side on a wide screen — the same arrangement the saved
   recipe already uses — they don't. See frontend/src/styles/draft.css. */
export function DraftSheet({ draft, method }) {
  const { k, saveRecipe, updateRecipe, deleteRecipe } = useKitchen();
  const ui = useUI();
  const [name, setName] = useState(draft.name);
  const [photo, setPhoto] = useState(draft.photo);
  const [choices, setChoices] = useState({});
  const [picked, setServes] = useState(null);      // null: the household's diners, as before
  const recipe = { ...draft, name, photo };
  const serves = picked ?? (k.diners.length || draft.serves);

  // Into the book on arrival, exactly as written.
  useEffect(() => { saveRecipe({ ...draft }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* Renaming edits the saved copy rather than a local one, so there is never a
     draft and a saved version disagreeing. Debounced so a rename is one write
     rather than one per keystroke; an empty box is never written, because a
     recipe with no name is unfindable in the book. */
  useEffect(() => {
    if (!name.trim()) return;
    const h = setTimeout(() => updateRecipe(draft.id, { name: name.trim(), photo }), 400);
    return () => clearTimeout(h);
  }, [name, photo]); // eslint-disable-line react-hooks/exhaustive-deps

  const note = draft.origin === "assistant" ? "Written by the assistant. Read it before you trust it — it can be confidently wrong about timings."
    : method === "plain" ? "Read from the page as written. The assistant is off, so no heat or cues were added."
    : "Read from the page and adapted to your kitchen's ingredients.";

  const throwAway = () => {
    deleteRecipe(draft.id);
    ui.closeSheet();
    ui.say("Thrown away");
  };

  return (
    <div className="draft">
      <div className="draft-hero">
        <DishImage recipe={recipe} className="draft-photo">
          <PhotoButton recipe={recipe} onDone={setPhoto} />
        </DishImage>
        <div className="draft-hero-text">
          {/* The name is the first thing most people want to change, so it is
              the heading itself rather than a labelled box further down. */}
          <input className="draft-name" value={name} aria-label="Name of this recipe"
                 placeholder="Name this dish" onChange={(e) => setName(e.target.value)} />
          <p className="draft-meta">
            {E.minutesOf(draft) > 0 && <span className="draft-pill"><Icon name="clock" size={15} /> {E.minutesOf(draft)} min</span>}
            <span className="draft-pill"><Icon name="people" size={15} /> Serves {serves}</span>
            <span className="draft-pill"><Stars recipe={draft} size={15} /></span>
            {draft.cuisine && <span className="draft-pill">{draft.cuisine}</span>}
            {draft.steps?.length > 0 && <span className="draft-pill"><Icon name="list" size={15} /> {draft.steps.length} steps</span>}
          </p>
        </div>
      </div>

      <div className="draft-saved">
        <Icon name="check" size={18} />
        <span>Saved to your recipes<small>{draft.origin === "link"
          ? ` — from ${draft.source?.site || "the page"}`
          : " — rename it above, or throw it away at the bottom"}</small></span>
      </div>
      <p className="draft-warn">{note}</p>

      <div className="draft-cols">
        <div className="draft-left">
          <PhotoStrip recipe={recipe} auto onPrimary={setPhoto} />
          <RecipeDetails recipe={recipe} serves={serves} setServes={setServes} choices={choices} setChoices={setChoices} />
        </div>
        <div className="draft-right">
          <RecipeMethod recipe={recipe} />
        </div>
      </div>

      <div className="draft-foot">
        <button className="btn btn-primary" onClick={() => ui.startCooking({ ...recipe, choices }, serves)}>Start cooking</button>
        <button className="btn btn-ghost" onClick={() => { ui.closeSheet(); ui.setTab("recipes"); }}>Done — it's in the book</button>
        <span className="draft-spacer" />
        {draft.origin !== "link" && <button className="link" onClick={() => ui.openSheet("create")}>
          <Icon name="spark" size={18} /> Try another</button>}
        <button className="link danger" onClick={throwAway}><Icon name="trash" size={18} /> Throw it away</button>
      </div>
    </div>
  );
}