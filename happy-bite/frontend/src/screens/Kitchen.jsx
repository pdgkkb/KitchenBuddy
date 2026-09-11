/* Kitchen — what's in, grouped, most urgent first, with the next two
   weeks of expiry drawn above the list. */

import { useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { CATEGORIES } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { ExpiryChart } from "../components/Charts.jsx";
import { nameOf } from "./Today.jsx";

export const catOf = (id) => CATEGORIES.find(c => c.id === E.ref(id).category) || CATEGORIES.at(-1);

export default function Kitchen() {
  const { k } = useKitchen();
  const ui = useUI();
  const [cat, setCat] = useState(null);

  const items = useMemo(() => k.stock.map(a => ({ ...a, days: E.daysLeft(a) })), [k.stock]);
  const bins = useMemo(() => E.expiryHistogram(k.stock, 14), [k.stock]);

  if (!items.length) return (
    <div className="screen">
      <h1 className="screen-title">Kitchen</h1>
      <p className="empty-note">Nothing in here yet. Scan a receipt to fill it.</p>
      <button className="btn btn-primary" onClick={() => ui.setTab("receipt")}>Scan a receipt</button>
    </div>
  );

  const shown = cat ? items.filter(a => E.ref(a.id).category === cat) : items;
  const urgent = shown.filter(a => a.days <= 3).sort((a, b) => a.days - b.days);
  const groups = CATEGORIES
    .map(c => ({ c, list: shown.filter(a => E.ref(a.id).category === c.id && a.days > 3).sort((a, b) => a.days - b.days) }))
    .filter(g => g.list.length);
  const present = CATEGORIES.filter(c => items.some(a => E.ref(a.id).category === c.id));

  return (
    <div className="screen">
      <header className="screen-head">
        <h1 className="screen-title">Kitchen</h1>
        <button className="btn btn-small btn-ghost" onClick={() => ui.setTab("receipt")}>
          <Icon name="receipt" size={20} /> Scan a receipt
        </button>
      </header>

      <section className="panel">
        <h2 className="panel-title">Going off</h2>
        <ExpiryChart bins={bins} nameOf={nameOf} />
      </section>

      <div className="chips chips-scroll">
        <button className={"chip" + (!cat ? " is-on" : "")} onClick={() => setCat(null)}>All</button>
        {present.map(c => (
          <button key={c.id} className={"chip" + (cat === c.id ? " is-on" : "")} style={{ "--tint": c.tint }}
                  onClick={() => setCat(c.id)}>{c.name}</button>
        ))}
      </div>

      {urgent.length > 0 && <>
        <h2 className="group-head is-urgent">Use these first</h2>
        <ul className="rows">{urgent.map(a => <StockRow key={a.id} a={a} />)}</ul>
      </>}
      {groups.map(g => (
        <section key={g.c.id}>
          <h2 className="group-head" style={{ "--tint": g.c.tint }}>{g.c.name}<span className="group-count">{g.list.length}</span></h2>
          <ul className="rows">{g.list.map(a => <StockRow key={a.id} a={a} />)}</ul>
        </section>
      ))}
    </div>
  );
}

function StockRow({ a }) {
  const ui = useUI();
  const c = catOf(a.id);
  const shelf = E.ref(a.id).shelfLife || 1;
  return (
    <li>
      <button className="row" onClick={() => ui.openSheet("quantity", { id: a.id })}>
        <span className="row-icon" style={{ "--tint": c.tint }}><Icon name={E.ref(a.id).category} size={30} /></span>
        <span className="row-text">
          <span className="row-name">{nameOf(a.id)}</span>
          <span className="row-sub">{E.formatQty(a.qty, a.unit || E.ref(a.id).unit)}</span>
        </span>
        <span className="row-end">
          <span className={"row-tag" + (a.days <= 3 ? " is-urgent" : "")}>{E.expiryLabel(a.days)}</span>
          {a.days <= 30 && (
            <span className="freshbar" aria-hidden="true">
              <span style={{ width: `${Math.max(4, Math.min(100, (a.days / Math.min(shelf, 30)) * 100))}%`,
                             background: a.days <= 3 ? "var(--coral)" : "var(--mint)" }} />
            </span>
          )}
        </span>
      </button>
    </li>
  );
}
