/* Shopping — fills itself from planned meals and "try this next time". */

import { useMemo } from "react";
import * as E from "../core/engine.js";
import { CATEGORIES } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { Ring } from "../components/Charts.jsx";
import { nameOf } from "./Today.jsx";

export default function Shopping() {
  const { k, toggleBought, clearShopping, togglePlanned } = useKitchen();
  const ui = useUI();
  const items = useMemo(() => E.shoppingList(k.planned, k.stock, k.diners.length || 1, k.wishlist, k.book), [k]);
  const planned = k.planned.map(id => k.book.find(r => r.id === id)).filter(Boolean);

  if (!items.length && !planned.length) return (
    <div className="screen">
      <h1 className="screen-title">Shopping</h1>
      <p className="empty-note">This fills itself from the meals you plan. Open a recipe and tap “Plan it” to start.</p>
      <button className="btn btn-ghost" onClick={() => ui.setTab("recipes")}>Browse recipes</button>
    </div>
  );

  const left = items.filter(i => !k.bought.includes(i.id)).length;
  const used = [...new Set(items.map(i => i.category))];

  return (
    <div className="screen">
      <h1 className="screen-title">Shopping</h1>
      <section className="panel gauge">
        <Ring value={items.length ? 1 - left / items.length : 1} size={96} stroke={10} color="var(--peach)"
              label={`${items.length - left} of ${items.length} picked up`}>
          <span className="gauge-figure">{left}</span>
        </Ring>
        <p className="gauge-note">{left === 1 ? "thing" : "things"} left to pick up
          {planned.length ? ` for ${planned.map(r => r.name).join(", ")}` : ""}.</p>
      </section>

      {planned.length > 0 && (
        <div className="pills">
          {planned.map(r => (
            <button key={r.id} className="pill" onClick={() => togglePlanned(r.id)} aria-label={`Unplan ${r.name}`}>
              {r.name}<Icon name="close" size={16} />
            </button>
          ))}
        </div>
      )}

      {CATEGORIES.filter(c => used.includes(c.id)).map(c => (
        <section key={c.id}>
          <h2 className="group-head" style={{ "--tint": c.tint }}>{c.name}</h2>
          <ul className="rows">
            {items.filter(i => i.category === c.id).map(i => (
              <li key={i.id}>
                <button className="row" aria-pressed={k.bought.includes(i.id)} onClick={() => toggleBought(i.id)}>
                  <span className="tick" aria-hidden="true"><Icon name="check" size={20} /></span>
                  <span className="row-text"><span className="row-name">{nameOf(i.id)}</span></span>
                  <span className="row-tag">{E.formatQty(i.qty, i.unit)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <button className="link link-block" onClick={clearShopping}>Clear the list</button>
    </div>
  );
}
