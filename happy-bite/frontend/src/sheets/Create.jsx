/* New recipes are invented by the assistant or read from a link. Nothing is
   saved until a person has read it and named it.

   The waiting states are the change here. A local Qwen takes ten to thirty
   seconds to write a recipe; the panel used to respond to that by greying out
   a button, which is indistinguishable from a crash. Now the panel is replaced
   by a narration of the phases, and a failure leaves that narration on screen
   so the error has somewhere to sit. */

import { useEffect, useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import PhotoStrip from "../components/PhotoStrip.jsx";
import Waiting, { RECIPE_STAGES, LINK_STAGES } from "../components/Waiting.jsx";
import { RecipeView, PhotoButton } from "./RecipeSheet.jsx";

export function CreateSheet({ options: initialOptions = null, autoGenerate = false, initialBrief = "" }) {
  const { k } = useKitchen();
  const ui = useUI();
  const [brief, setBrief] = useState(initialBrief);
  const [options, setOptions] = useState(initialOptions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const on = ui.server.recipes;
  const soon = k.stock.filter(a => E.daysLeft(a) <= 3).map(a => E.ref(a.id).name.toLowerCase());

  useEffect(() => {
    if (autoGenerate && !initialOptions && !busy && !error) make();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const make = async () => {
    setBusy(true);
    setError(null);
    if (!on) {
      setBusy(false);
      setError("The assistant is switched off. Start the server to create a new recipe.");
      return;
    }
    try {
      const { recipes } = await api.generateRecipe({
        brief: brief || "Something good for tonight", stock: stockForServer(k.stock),
        serves: k.diners.length || 4, custom: k.customs
      });
      setBusy(false);
      setOptions(recipes);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  };

  const choose = async (idea) => {
    setOptions(null);
    setBusy(true);
    try {
      const { recipe } = await api.generateRecipe({
        options: false,
        brief: `Write the full recipe for this chosen idea: ${idea.name}. ${idea.description}`,
        stock: stockForServer(k.stock), serves: k.diners.length || 4, custom: k.customs
      });
      setBusy(false);
      ui.openSheet("draft", { draft: recipe });
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  };

  if (busy || error) return (
    <Waiting
      stages={RECIPE_STAGES}
      error={error}
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
        ? "The assistant writes this from what's in your kitchen, using what goes off first."
        : "The assistant is switched off. Start the server to create a new recipe."}</p>
      {!on && <div className="band is-warm">
        <b>{ui.server.online ? "Chef model unavailable" : "Backend not reachable"}</b>
        <span>{ui.server.online ? "Check the assistant settings or model configuration." : "Start the backend on port 8000, then check again."}</span>
        <button className="btn btn-small btn-ghost" onClick={ui.refreshServer}>Check again</button>
      </div>}
      <label className="field-label" htmlFor="brief">What do you want?</label>
      <textarea id="brief" className="field field-area" rows={3} value={brief} onChange={(e) => setBrief(e.target.value)}
                placeholder={on ? (soon.length ? `Something warm with the ${soon[0]} before it goes` : "Something comforting, 30 minutes") : "Name it, or leave blank"} />
      {on && (
        <div className="chips" style={{ marginTop: 12 }}>
          {["Impress guests", "Kids will eat it", "One pan", "Bold and spicy"].map(s => (
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
                <small>Difficulty: {recipe.complexity === 1 ? "Easy" : recipe.complexity === 3 ? "Involved" : "Some work"}</small>
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

export function DraftSheet({ draft, method }) {
  const { k, saveRecipe } = useKitchen();
  const ui = useUI();
  const [name, setName] = useState(draft.name);
  const [photo, setPhoto] = useState(draft.photo);
  const [choices, setChoices] = useState({});
  const recipe = { ...draft, name, photo };
  const serves = k.diners.length || draft.serves;

  const note = draft.origin === "assistant" ? "Written by the assistant. Read it before you trust it — it can be confidently wrong about timings."
    : method === "plain" ? "Read from the page as written. The assistant is off, so no heat or cues were added."
    : "Read from the page and adapted to your kitchen's ingredients.";

  const keep = (thenCook) => {
    if (!name.trim()) return ui.say("Give it a name first");
    saveRecipe(recipe);
    if (thenCook) ui.startCooking(recipe, serves);
    else { ui.closeSheet(); ui.setTab("recipes"); ui.say("Saved to your recipes"); }
  };

  return (
    <>
      <DishImage recipe={recipe} className="dish-photo dish-draft">
        <PhotoButton recipe={recipe} onDone={setPhoto} />
      </DishImage>
      {/* Not auto-started: the draft isn't saved yet, so pictures are made only
          when the button above asks for them. */}
      <PhotoStrip recipe={recipe} auto onPrimary={setPhoto} />
      <div className="band"><b>{draft.origin === "link" ? `From ${draft.source?.site}` : "Before you save it"}</b><span>{note}</span></div>
      <label className="field-label" htmlFor="rname">Call it</label>
      <input id="rname" className="field" value={name} onChange={(e) => setName(e.target.value)} />
      <RecipeView recipe={recipe} serves={serves} choices={choices} setChoices={setChoices} />
      <div className="row-actions" style={{ marginTop: 24 }}>
        <button className="btn btn-ghost" onClick={() => keep(false)}>Save it</button>
        <button className="btn btn-primary" onClick={() => keep(true)}>Save and cook</button>
      </div>
      {draft.origin !== "link" && <button className="link link-block" onClick={() => ui.openSheet("create")}>
        <Icon name="spark" size={18} /> Try another</button>}
    </>
  );
}