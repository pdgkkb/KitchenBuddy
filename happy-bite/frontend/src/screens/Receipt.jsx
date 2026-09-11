/* Receipt — photograph, check, put away.

   The parser still doesn't exist: this shows simulated output at a
   deliberately poor match rate, because an interface that only works
   when the parser is good is one nobody has tested. The real parser
   plugs in at `onFile` — one function. */

import * as E from "../core/engine.js";
import { SAMPLE_RECEIPT } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { Ring } from "../components/Charts.jsx";
import { catOf } from "./Kitchen.jsx";
import { nameOf } from "./Today.jsx";

export default function Receipt() {
  const { k, setReceipt, putAway } = useKitchen();
  const ui = useUI();
  const t = k.receipt;

  const load = () => {
    /* Remembered corrections apply before anyone looks: fix a line once,
       and it's right on every receipt after. */
    const r = structuredClone(SAMPLE_RECEIPT);
    for (const l of r.lines) {
      if (l.raw in k.corrections) {
        const id = k.corrections[l.raw];
        Object.assign(l, id ? { id, confidence: 1 } : { id: null, rejected: true, confidence: 0 });
      }
    }
    setReceipt(r);
  };

  const onFile = () => { ui.say("Reading the receipt"); setTimeout(load, 700); };

  if (!t) return (
    <div className="screen">
      <h1 className="screen-title">Receipt</h1>
      <label className="dropzone">
        <Icon name="receipt" size={48} />
        <span className="empty-note">Photograph the receipt flat, well lit, not folded.</span>
        <span className="btn btn-primary">Photograph a receipt</span>
        <input type="file" accept="image/*" capture="environment" hidden onChange={onFile} />
      </label>
      <button className="link link-block" onClick={load}>Use a sample receipt</button>
    </div>
  );

  const { confirmed, unsure, rejected } = E.sortLines(t.lines);
  const rate = E.matchRate(t.lines);
  const idx = (l) => t.lines.indexOf(l);

  return (
    <div className="screen">
      <h1 className="screen-title">{t.store}</h1>
      <section className="panel gauge">
        <Ring value={rate} size={96} stroke={10} color="var(--mint)" label={`${Math.round(rate * 100)}% matched`}>
          <span className="gauge-figure">{Math.round(rate * 100)}<small>%</small></span>
        </Ring>
        <p className="gauge-note">{confirmed.length} of {t.lines.length - rejected.length} food lines matched on their own.
          {unsure.length ? ` ${unsure.length} need a look.` : " All sorted."}</p>
      </section>

      {unsure.length > 0 && <><h2 className="group-head is-urgent">Needs checking</h2>
        <ul className="rows">{unsure.map(l => <Line key={idx(l)} l={l} i={idx(l)} kind="unsure" />)}</ul></>}
      {confirmed.length > 0 && <><h2 className="group-head">Matched</h2>
        <ul className="rows">{confirmed.map(l => <Line key={idx(l)} l={l} i={idx(l)} kind="ok" />)}</ul></>}
      {rejected.length > 0 && <><h2 className="group-head">Not food<span className="group-count">{rejected.length}</span></h2>
        <ul className="rows">{rejected.map(l => <Line key={idx(l)} l={l} i={idx(l)} kind="out" />)}</ul></>}

      <div className="row-actions" style={{ marginTop: 28 }}>
        <button className="btn btn-primary" onClick={() => { const n = putAway(); ui.say(n + " items put away"); ui.setTab("kitchen"); }}>
          Put {confirmed.length} items away
        </button>
      </div>
      <button className="link link-block" onClick={() => setReceipt(null)}>Discard this receipt</button>
    </div>
  );
}

function Line({ l, i, kind }) {
  const ui = useUI();
  const c = l.id ? catOf(l.id) : catOf("__none");
  return (
    <li>
      <button className={"row" + (kind === "out" ? " is-dim" : "")} onClick={() => ui.openSheet("verify", { index: i })}>
        <span className="row-icon" style={{ "--tint": c.tint }}><Icon name={l.id ? E.ref(l.id).category : "other"} size={30} /></span>
        <span className="row-text">
          <span className="row-name">{l.id ? nameOf(l.id) : l.raw}</span>
          <span className="row-sub">{kind === "out" ? "Marked as not food" : l.id ? l.raw : "Nothing matched"}</span>
        </span>
        <span className={"row-tag" + (kind === "unsure" ? " is-check" : "")}>
          {kind === "unsure" ? "Check" : kind === "out" ? "Skipped" : E.formatQty(l.qty, E.ref(l.id).unit)}
        </span>
      </button>
    </li>
  );
}
