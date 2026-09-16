/* Recipes — the one screen where a long list is right. Browsing on a
   Sunday is the opposite of deciding at 7pm, so what you can't make is
   dimmed rather than hidden: here, seeing what you'd need is the point. */

import { useDeferredValue, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { MEAL_TYPES } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import { Ring } from "../components/Charts.jsx";
import { effortName, listOf } from "./Today.jsx";

export default function Recipes() {
  const { k } = useKitchen();
  const ui = useUI();
  const [q, setQ] = useState("");
  const [type, setType] = useState(null);
  const [mine, setMine] = useState(false);

  const deferredQ = useDeferredValue(q);
  const rated = useMemo(() => {
    const query = deferredQ.trim().toLowerCase();
    return k.book
      .map(r => ({ r, a: E.assess(r, k.stock, new Set(), k.taste, k.diners.length || r.serves) }))
      .filter(x => x.a)
      .filter(x => !query || x.r.name.toLowerCase().includes(query) || x.r.cuisine.toLowerCase().includes(query))
      .filter(x => !type || x.r.types.includes(type))
      .filter(x => !mine || x.r.origin)
      .sort((a, b) => (b.a.cookable - a.a.cookable) || a.r.name.localeCompare(b.r.name));
  }, [k, deferredQ, type, mine]);

  const ready = rated.filter(x => x.a.cookable).length;

  return (
    <div className="screen">
      <h1 className="screen-title">Recipes</h1>

      <div className="make-row">
        <button className="make" onClick={() => ui.openSheet("create")}>
          <Icon name="spark" size={28} /><span><b>Create one</b><small>from what's in the kitchen</small></span>
        </button>
        <button className="make" onClick={() => ui.openSheet("link")}>
          <Icon name="link" size={28} /><span><b>Add from a link</b><small>any cooking website</small></span>
        </button>
      </div>

      <input className="field" type="search" placeholder="Search recipes" value={q} onChange={(e) => setQ(e.target.value)} />

      <div className="chips chips-scroll" style={{ marginTop: 14 }}>
        <button className={"chip" + (!type && !mine ? " is-on" : "")} onClick={() => { setType(null); setMine(false); }}>All</button>
        <button className={"chip" + (mine ? " is-on" : "")} onClick={() => setMine(!mine)}>Yours</button>
        {MEAL_TYPES.map(m => (
          <button key={m.id} className={"chip" + (type === m.id ? " is-on" : "")} onClick={() => setType(type === m.id ? null : m.id)}>{m.name}</button>
        ))}
      </div>

      <p className="sub-note">{ready} of {rated.length} can be cooked with what's in the kitchen.</p>

      <ul className="cards">
        {rated.map(({ r, a }) => (
          <li key={r.id}>
            <button className={"rcard" + (a.cookable ? "" : " is-dim")} onClick={() => ui.openSheet("recipe", { id: r.id })}>
              <DishImage recipe={r} className="rcard-img" />
              <span className="rcard-body">
                <span className="rcard-name">{r.name}</span>
                <span className="rcard-sub">{r.minutes} min, {effortName(r.complexity).toLowerCase()}, {r.cuisine}</span>
                <span className={"rcard-need" + (a.cookable ? " is-ready" : "")}>
                  {a.cookable ? "Ready to cook" : "Need " + listOf(a.missing.map(m => m.id))}
                </span>
              </span>
              <Ring value={a.have / Math.max(1, a.total)} size={46} stroke={5}
                    color={a.cookable ? "var(--mint)" : "var(--sky)"} label={`${a.have} of ${a.total} in stock`}>
                <span className="mini-frac">{a.have}/{a.total}</span>
              </Ring>
            </button>
          </li>
        ))}
      </ul>
      {!rated.length && <p className="empty-note">Nothing matches that.</p>}
    </div>
  );
}
