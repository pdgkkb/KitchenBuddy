/* Sheets that change the kitchen: filters, a quantity, a receipt line. */

import { useDeferredValue, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { CATEGORIES, COMPLEXITY, CUISINES, INGREDIENTS, MEAL_TYPES } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { catOf } from "../screens/Kitchen.jsx";
import { nameOf } from "../screens/Today.jsx";

/* Settings, not questions: set once, here, and Tonight still shows one dish. */
export function AdjustSheet() {
  const { k, setFilter, clearFilters } = useKitchen();
  const ui = useUI();
  const f = k.filters;
  const Row = ({ label, children }) => <><p className="option-label">{label}</p><div className="chips">{children}</div></>;
  const Chip = ({ on, onClick, children }) => <button className={"chip" + (on ? " is-on" : "")} aria-pressed={on} onClick={onClick}>{children}</button>;
  return (
    <>
      <Row label="Meal">
        <Chip on={!f.mealType} onClick={() => setFilter("mealType", null)}>Any</Chip>
        {MEAL_TYPES.map(m => <Chip key={m.id} on={f.mealType === m.id} onClick={() => setFilter("mealType", m.id)}>{m.name}</Chip>)}
      </Row>
      <Row label="Most time you'll spend">
        {[15, 20, 30, 45, null].map(t => <Chip key={String(t)} on={(f.maxMinutes || null) === t}
          onClick={() => setFilter("maxMinutes", t)}>{t ? t + " min" : "No limit"}</Chip>)}
      </Row>
      <Row label="Effort">
        {COMPLEXITY.map(c => <Chip key={c.level} on={(f.maxComplexity || 3) === c.level}
          onClick={() => setFilter("maxComplexity", c.level === 3 ? null : c.level)}>
          {c.level === 3 ? "Any" : `Up to ${E.STARS_FOR_COMPLEXITY[c.level]} ★`}</Chip>)}
      </Row>
      <Row label="Kitchen">
        <Chip on={!f.cuisine} onClick={() => setFilter("cuisine", null)}>Anywhere</Chip>
        {CUISINES.map(c => <Chip key={c} on={f.cuisine === c} onClick={() => setFilter("cuisine", c)}>{c}</Chip>)}
      </Row>
      <button className="btn btn-primary" style={{ marginTop: 24 }} onClick={ui.closeSheet}>Done</button>
      <button className="link link-block" onClick={() => { clearFilters(); ui.closeSheet(); }}>Clear all filters</button>
    </>
  );
}

/* Weight or count. Quantity is held in the reference unit and only the
   display converts, so switching g → pieces → g never drifts the stock. */
export function QuantitySheet({ id }) {
  const { k, setStock } = useKitchen();
  const ui = useUI();
  const item = k.stock.find(x => x.id === id);
  const r = E.ref(id);
  const [unit, setUnit] = useState(item?.unit || r.unit);
  const [canon, setCanon] = useState(item ? E.convert(item.qty, item.unit || r.unit, r.unit, r) : 0);
  if (!item) return null;
  const start = E.convert(item.qty, item.unit || r.unit, r.unit, r);
  const shown = E.convert(canon, r.unit, unit, r);

  const nudge = (dir) => {
    const st = E.step(unit, shown);
    const next = Math.max(0, Math.round((shown + st * dir) / st) * st);
    setCanon(E.convert(Math.round(next * 100) / 100, unit, r.unit, r));
  };

  return (
    <>
      <div className="stepper">
        <button className="step-btn" onClick={() => nudge(-1)} aria-label="Less">−</button>
        <div className="step-value" aria-live="polite">{shown > 0 ? E.formatQty(shown, unit) : "All gone"}</div>
        <button className="step-btn" onClick={() => nudge(1)} aria-label="More">+</button>
      </div>
      {E.countable(r) && (
        <button className="btn btn-ghost" style={{ marginTop: 16 }} onClick={() => setUnit(unit === "u" ? "g" : "u")}>
          {unit === "u" ? "Count in grams" : "Count in pieces"}
        </button>
      )}
      <div className="chips" style={{ marginTop: 20 }}>
        <button className="chip" onClick={() => setCanon(0)}>All gone</button>
        <button className="chip" onClick={() => setCanon(Math.round(start * 50) / 100)}>About half</button>
        <button className="chip" onClick={() => setCanon(start)}>As it was</button>
      </div>
      <button className="btn btn-primary" style={{ marginTop: 24 }} onClick={() => {
        setStock(id, shown, unit); ui.closeSheet(); if (shown <= 0) ui.say("Taken out of the kitchen");
      }}>Save</button>
    </>
  );
}

const SHOWN = 60;
const collator = new Intl.Collator();
let sorted = { size: -1, ids: [] };
function sortedIds() {
  const all = Object.keys(INGREDIENTS);           // INGREDIENTS grows: re-sort only when it has
  if (all.length !== sorted.size) sorted = { size: all.length, ids: all.sort((a, b) => collator.compare(nameOf(a), nameOf(b))) };
  return sorted.ids;
}

export function VerifySheet({ index }) {
  const { k, resolveLine, createProduct } = useKitchen();
  const ui = useUI();
  const line = k.receipt?.lines[index];
  const [q, setQ] = useState("");
  const [cat, setCat] = useState(null);
  const [newName, setNewName] = useState("");
  const deferredQ = useDeferredValue(q);
  // ~3,000 products: sorted once, filtered on a deferred query, and only the
  // first SHOWN drawn — a list nobody scrolls to the end of isn't worth 3,000 rows.
  const matches = useMemo(() => {
    const query = deferredQ.trim().toLowerCase();
    return sortedIds().filter(i => (!cat || INGREDIENTS[i].category === cat)
      && (!query || nameOf(i).toLowerCase().includes(query)));
  }, [deferredQ, cat]);
  const ids = matches.slice(0, SHOWN);
  if (!line) return null;
  const pick = (id) => { resolveLine(index, id); ui.closeSheet(); };

  return (
    <>
      <p className="raw-label">{line.raw}</p>
      {line.product && <p className="sub-note">Closest product found: {line.product}</p>}
      <input className="field" type="search" placeholder="Search products" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="chips chips-scroll" style={{ marginTop: 14 }}>
        <button className={"chip" + (!cat ? " is-on" : "")} onClick={() => setCat(null)}>All</button>
        {CATEGORIES.filter(c => c.id !== "other").map(c => (
          <button key={c.id} className={"chip" + (cat === c.id ? " is-on" : "")} style={{ "--tint": c.tint }} onClick={() => setCat(c.id)}>{c.name}</button>
        ))}
      </div>
      {matches.length > SHOWN && <p className="sub-note">Showing {SHOWN} of {matches.length} — type to narrow it down.</p>}
      <ul className="rows short-list">
        {ids.map(i => (
          <li key={i}><button className="row" onClick={() => pick(i)}>
            <span className="row-icon" style={{ "--tint": catOf(i).tint }}><Icon name={INGREDIENTS[i].category} size={28} /></span>
            <span className="row-text"><span className="row-name">{nameOf(i)}</span></span>
            <span className="row-tag">{catOf(i).name}</span>
          </button></li>
        ))}
      </ul>
      <h3 className="sub-head">Not in the list?</h3>
      <div className="panel">
        <input className="field" placeholder="Name it yourself" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <div className="chips" style={{ marginTop: 12 }}>
          {CATEGORIES.filter(c => c.id !== "other").map(c => (
            <button key={c.id} className="chip" style={{ "--tint": c.tint }} onClick={() => {
              if (!newName.trim()) return ui.say("Type a name first");
              pick(createProduct(newName.trim(), c.id)); ui.say(newName.trim() + " added");
            }}>{c.name}</button>
          ))}
        </div>
        <p className="sub-note">Pick a category to save it. It'll be recognised on every receipt after this one.</p>
      </div>
      <button className="btn btn-ghost" style={{ marginTop: 20 }} onClick={() => { resolveLine(index, null, true); ui.closeSheet(); ui.say("Marked as not food"); }}>
        This isn't food
      </button>
    </>
  );
}
