/* Happy Bite — photograph a receipt from wherever you are.

   It was buried on its own tab, which is the wrong place: the moment you think
   about a receipt is when you get home with the bags and the shopping list is
   still open. So the button lives on the Shopping screen too.

   One input, two ways in. `capture="environment"` opens the rear camera on a
   phone; on a laptop the same input is an ordinary file picker, so an emailed
   PDF screenshot or a photo already in your library works. The second button
   drops the capture hint for people who want the library on a phone as well.

   The photo goes to the server, which OCRs it and maps the lines to the
   catalogue, then it lands in the receipt screen that already exists for
   checking and correcting. Nothing is put away without you looking at it. */

import { useRef, useState } from "react";
import * as api from "../lib/api.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "./Icon.jsx";
import "../styles/happy-extra.css";

export default function ReceiptButton({ compact = false }) {
  const { k, setReceipt } = useKitchen();
  const ui = useUI();
  const [busy, setBusy] = useState(false);
  const library = useRef(null);

  const send = async (file) => {
    if (!file) return;
    if (!ui.server.chat) {
      ui.say("Reading a receipt needs the assistant — open the Receipt tab to enter it by hand.");
      return;
    }
    setBusy(true);
    try {
      const receipt = await api.readReceipt(file, k.customs);
      /* Corrections you've already made are applied before you see it, exactly
         as the sample path does — fix a line once, it stays fixed. */
      for (const l of receipt.lines) {
        if (l.raw in k.corrections) {
          const id = k.corrections[l.raw];
          Object.assign(l, id ? { id, confidence: 1, rejected: false }
                              : { id: null, rejected: true, confidence: 0 });
        }
      }
      setReceipt(receipt);
      ui.setTab("receipt");
      ui.say(`${receipt.lines.length} lines read`);
    } catch (e) {
      ui.say(e.message || "That receipt couldn't be read.");
    }
    setBusy(false);
  };

  const pick = (e) => { send(e.target.files?.[0]); e.target.value = ""; };

  return (
    <div className={"receipt-cta" + (compact ? " is-compact" : "")}>
      <label className={"btn " + (compact ? "btn-small btn-ghost" : "btn-primary")}>
        <Icon name="receipt" size={compact ? 18 : 20} />
        {busy ? "Reading the receipt…" : "Photograph a receipt"}
        <input type="file" accept="image/*" capture="environment" hidden
               disabled={busy} onChange={pick} />
      </label>
      <button type="button" className="link" disabled={busy}
              onClick={() => library.current?.click()}>
        or choose an image
      </button>
      <input ref={library} type="file" accept="image/*" hidden onChange={pick} />
    </div>
  );
}
