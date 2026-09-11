/* New recipes: invented by the assistant, read from a link, or — with the
   server off — assembled from a template and labelled as one. Nothing is
   saved until a person has read it and named it. */

import { useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import { draftRecipe } from "../core/templates.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import { RecipeView, PhotoButton } from "./RecipeSheet.jsx";

export function CreateSheet() {
  const { k } = useKitchen();
  const ui = useUI();
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const on = ui.server.recipes;
  const soon = k.stock.filter(a => E.daysLeft(a) <= 3).map(a => E.ref(a.id).name.toLowerCase());

  const make = async () => {
    setBusy(true);
    if (on) {
      try {
        const { recipe } = await api.generateRecipe({
          brief: brief || "Something good for tonight", stock: stockForServer(k.stock),
          serves: k.diners.length || 4, custom: k.customs
        });
        setBusy(false);
        return ui.openSheet("draft", { draft: recipe });
      } catch (e) { ui.say(e.message + " Built one from a template instead."); }
    }
    const local = draftRecipe(brief, k.stock);
    setBusy(false);
    local ? ui.openSheet("draft", { draft: local }) : ui.say("Not enough in the kitchen to build anything");
  };

  return (
    <>
      <p className="sub-note">{on
        ? "The assistant writes this from what's in your kitchen, using what goes off first."
        : "The server's assistant is off, so this builds a plain dish from a small set of templates. It won't be clever, but it works offline."}</p>
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
      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={make} disabled={busy}>
        {busy ? "Writing it…" : on ? "Write it" : "Build one"}
      </button>
    </>
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
          <button className="btn btn-small btn-ghost" onClick={async () => { try { setUrl(await navigator.clipboard.readText()); } catch {} }}>Paste</button>
        )}
      </div>
      {error && <div className="band is-warm"><b>That didn't work</b><span>{error}</span></div>}
      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={read} disabled={busy || !/^https?:\/\//.test(url.trim())}>
        {busy ? "Reading the page…" : "Read it"}
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
    : draft.origin === "link" ? (method === "plain" ? "Read from the page as written. The assistant is off, so no heat or cues were added." : "Read from the page and adapted to your kitchen's ingredients.")
    : "Built from a template. Plain by design.";

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
