/* Who's eating, the household, one person.

   A profile is a name and a list of things they won't eat. No age, no
   weight, no goals, no conditions: that would be health data, and staying
   out of it is what keeps this app out of medical-device territory. */

import { useState } from "react";
import { CATEGORIES, INGREDIENTS } from "../data/index.js";
import { uid } from "../core/engine.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { nameOf } from "../screens/Today.jsx";

export function TableSheet() {
  const { k, toggleDiner } = useKitchen();
  const ui = useUI();
  return (
    <>
      <p className="sub-note">Portions and exclusions both follow this.</p>
      <div className="chips">
        {k.people.map(p => (
          <button key={p.id} className={"chip chip-lg" + (k.diners.includes(p.id) ? " is-on" : "")}
                  aria-pressed={k.diners.includes(p.id)} onClick={() => toggleDiner(p.id)}>{p.name}</button>
        ))}
      </div>
      <button className="btn btn-ghost" style={{ marginTop: 24 }} onClick={() => ui.openSheet("household")}>Manage household</button>
    </>
  );
}

export function HouseholdSheet() {
  const { k } = useKitchen();
  const ui = useUI();
  return (
    <>
      <ul className="rows">
        {k.people.map(p => (
          <li key={p.id}><button className="row" onClick={() => ui.openSheet("profile", { id: p.id })}>
            <span className="avatar">{p.name[0]}</span>
            <span className="row-text"><span className="row-name">{p.name}</span>
              <span className="row-sub">{p.avoids.length ? "avoids " + p.avoids.map(nameOf).join(", ").toLowerCase() : "eats everything"}</span></span>
            <span className="row-tag">Edit</span>
          </button></li>
        ))}
      </ul>
      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={() => ui.openSheet("profile", {})}>Add someone</button>
    </>
  );
}

export function ProfileSheet({ id }) {
  const { k, saveProfile, removeProfile } = useKitchen();
  const ui = useUI();
  const existing = id && k.people.find(x => x.id === id);
  const [name, setName] = useState(existing?.name || "");
  const [avoids, setAvoids] = useState(new Set(existing?.avoids || []));
  const flip = (i) => setAvoids(s => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });

  return (
    <>
      <label className="field-label" htmlFor="pname">Name</label>
      <input id="pname" className="field" value={name} placeholder="Camille" onChange={(e) => setName(e.target.value)} />
      <h3 className="sub-head">Won't eat</h3>
      {CATEGORIES.filter(c => c.id !== "other").map(c => {
        const ids = Object.keys(INGREDIENTS).filter(i => INGREDIENTS[i].category === c.id);
        if (!ids.length) return null;
        return (
          <div key={c.id}>
            <p className="option-label" style={{ "--tint": c.tint }}>{c.name}</p>
            <div className="chips">
              {ids.map(i => <button key={i} className={"chip" + (avoids.has(i) ? " is-off" : "")}
                                    aria-pressed={avoids.has(i)} onClick={() => flip(i)}>{nameOf(i)}</button>)}
            </div>
          </div>
        );
      })}
      <button className="btn btn-primary" style={{ marginTop: 24 }} onClick={() => {
        if (!name.trim()) return ui.say("It needs a name");
        saveProfile({ id: existing?.id || uid(), name: name.trim(), avoids: [...avoids] });
        ui.openSheet("household");
      }}>Save</button>
      {existing && <button className="link link-block danger" onClick={() => { removeProfile(existing.id); ui.openSheet("household"); }}>Remove from household</button>}
    </>
  );
}
