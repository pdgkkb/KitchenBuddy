/* Kitchen — everything you have, sorted by what goes off first.

   The useful signal on a stock row is time, not a photo: each line shows how
   long it has left and taps through to a quantity sheet to adjust it or use
   it up. `catOf` lives here because the receipt and the kitchen sheets colour
   their rows the same way, from the ingredient's category.

   (Rebuilt to match how the rest of the app uses this screen: a default
   export for the Kitchen tab, and a named `catOf` export.) */

import { useDeferredValue, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { CATEGORIES } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { ExpiryChart } from "../components/Charts.jsx";
import { nameOf } from "./Today.jsx";

const CAT = Object.fromEntries(CATEGORIES.map(c => [c.id, c]));
const OTHER = CAT.other || { id: "other", name: "Other", tint: "var(--cat-other)" };

/* An ingredient id -> its category record {id, name, tint}. Safe on unknown
   or missing ids (the receipt passes "__none" for an unmatched line). */
export const catOf = (id) => CAT[E.ref(id).category] || OTHER;

export default function Kitchen() {
  const { k } = useKitchen();
  const ui = useUI();
  const [q, setQ] = useState("");

  const deferredQ = useDeferredValue(q);
  const rows = useMemo(() => {
    const query = deferredQ.trim().toLowerCase();
    return k.stock
      .filter(a => E.ref(a.id).name)
      .map(a => ({ a, d: E.daysLeft(a), cat: E.ref(a.id).category || "other" }))
      .filter(x => !query || nameOf(x.a.id).toLowerCase().includes(query))
      .sort((x, y) => x.d - y.d);
  }, [k.stock, deferredQ]);

  const bins = useMemo(() => E.expiryHistogram(k.stock, 14), [k.stock]);
  const useSoon = k.stock.filter(a => E.daysLeft(a) <= 3).length;

  if (!k.stock.length) return (
    <div className="screen">
      <h1 className="screen-title">Kitchen</h1>
      <p className="empty-note">Nothing in the kitchen yet. Scan a receipt to fill it, or tell the chef what you bought
        (“I bought 2 litres of milk and a dozen eggs”).</p>
      <button className="btn btn-ghost" onClick={() => ui.scanReceipt()}>Scan a receipt</button>
    </div>
  );

  const cats = CATEGORIES.filter(c => rows.some(x => x.cat === c.id));

  return (
    <div className="screen">
      <h1 className="screen-title">Kitchen</h1>
      <p className="sub-note">{k.stock.length} {k.stock.length === 1 ? "thing" : "things"} in the kitchen
        {useSoon ? `, ${useSoon} to use soon` : ""}.</p>

      <section className="panel">
        <ExpiryChart bins={bins} nameOf={nameOf} />
      </section>

      <button className="btn btn-small btn-ghost" style={{ marginBottom: 14 }}
              onClick={() => ui.openSheet("equipment")}>
        <Icon name="list" size={20} />
        {k.equipment ? `What I cook with (${k.equipment.length})` : "What I cook with"}
      </button>

      <input className="field" type="search" placeholder="Search the kitchen" value={q}
             onChange={(e) => setQ(e.target.value)} />

      {cats.map(c => (
        <section key={c.id}>
          <h2 className="group-head" style={{ "--tint": c.tint }}>{c.name}</h2>
          <ul className="rows">
            {rows.filter(x => x.cat === c.id).map(({ a, d }) => {
              const urgent = d <= 3;
              const label = E.expiryLabel(d);
              return (
                <li key={a.id}>
                  <button className={"row" + (urgent ? " is-urgent" : "")}
                          onClick={() => ui.openSheet("quantity", { id: a.id })}>
                    <span className="row-icon" style={{ "--tint": catOf(a.id).tint }}>
                      <Icon name={c.id} size={28} />
                    </span>
                    <span className="row-text">
                      <span className="row-name">{nameOf(a.id)}</span>
                      <span className="row-sub">{label || "Keeps a while"}</span>
                    </span>
                    <span className="row-tag">{E.formatQty(a.qty, a.unit || E.ref(a.id).unit)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {!rows.length && <p className="empty-note">Nothing matches that.</p>}
    </div>
  );
}
