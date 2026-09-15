/* What you cook with — said once, used everywhere.

   Tapping sixteen things is a chore, so nothing here is required: the first
   time it opens, the four almost-everyone-has-them are already on and you turn
   off what you haven't got. That inverts the work — most people have no oven to
   declare, they have *one missing thing*, and this is two taps for them.

   Until this sheet is saved, `k.equipment` is null and the app says nothing
   about equipment at all. Null is not "owns nothing": telling someone their
   kitchen lacks an oven they never mentioned is the kind of confident wrongness
   that makes people close an app. */

import { useState } from "react";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { COMMON, EQUIPMENT } from "../core/equipment.js";
import "../styles/equipment.css";

export default function EquipmentSheet() {
  const { k, setEquipment } = useKitchen();
  const ui = useUI();
  const [chosen, setChosen] = useState(() => new Set(k.equipment || COMMON));
  const first = !k.equipment;

  const toggle = (id) => setChosen(s => {
    const next = new Set(s);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const save = () => {
    setEquipment([...chosen]);
    ui.closeSheet();
    ui.say(chosen.size ? "Noted — I'll work around what's missing" : "Noted");
  };

  return (
    <>
      <p className="sub-note">
        {first
          ? "The usual four are already on. Turn off anything you haven't got — that's the part I need."
          : "Tap anything that's changed."}
      </p>

      <ul className="equip-grid">
        {EQUIPMENT.map(e => {
          const on = chosen.has(e.id);
          return (
            <li key={e.id}>
              <button className={"equip" + (on ? " is-on" : "")} aria-pressed={on}
                      onClick={() => toggle(e.id)}>
                <span className="equip-tick" aria-hidden="true">{on ? "✓" : ""}</span>
                <span className="equip-text">
                  <span className="equip-name">{e.name}</span>
                  {e.note && <span className="equip-note">{e.note}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="band" style={{ marginTop: 18 }}>
        <b>What this changes</b>
        <span>Recipes that need something you haven't got get a panel offering a way round —
          and the chef is told what's in your kitchen, so "can I do this in a pan instead?"
          gets a real answer rather than a guess.</span>
      </div>

      <button className="btn btn-primary" style={{ marginTop: 20 }} onClick={save}>
        <Icon name="list" size={20} /> Save {chosen.size ? `(${chosen.size})` : ""}
      </button>
      {!first && (
        <button className="link link-block" onClick={() => setChosen(new Set(COMMON))}>
          Back to the usual four
        </button>
      )}
    </>
  );
}
