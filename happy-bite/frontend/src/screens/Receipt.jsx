/* Receipt — photograph, check, put away.

   The photo goes to the server (POST /api/receipt/read): tesseract reads it,
   each line is looked up in Open Food Facts and in this household's past
   corrections, and the model decides. The sample receipt is still here, at
   its deliberately poor match rate, for trying the screen without a server.

   Two ways in. "Photograph a receipt" opens the camera (components/
   ReceiptCamera.jsx). "Import a receipt" opens a file picker that Chrome
   remembers: pick a photo from your receipts folder once and it opens there
   every time after. "Scan a receipt" elsewhere in the app arrives here with
   ui.receiptCamera set and goes straight to the camera. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as E from "../core/engine.js";
import * as api from "../lib/api.js";
import { SAMPLE_RECEIPT } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import ReceiptCamera, { cameraAvailable } from "../components/ReceiptCamera.jsx";
import { Ring } from "../components/Charts.jsx";
import { catOf } from "./Kitchen.jsx";
import { nameOf } from "./Today.jsx";

export default function Receipt() {
  const { k, setReceipt, putAway } = useKitchen();
  const ui = useUI();
  const t = k.receipt;

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [camera, setCamera] = useState(false);
  const shootInput = useRef(null);            // camera fallback: opens the camera app on a phone or tablet
  const importInput = useRef(null);           // file picker fallback for browsers without showOpenFilePicker

  /* Remembered corrections apply before anyone looks: fix a line once,
     and it's right on every receipt after. */
  const open = (receipt) => {
    const r = structuredClone(receipt);
    for (const l of r.lines) {
      if (l.raw in k.corrections) {
        const id = k.corrections[l.raw];
        Object.assign(l, id ? { id, confidence: 1 } : { id: null, rejected: true, confidence: 0 });
      }
    }
    setReceipt(r);
  };

  const read = async (photo) => {
    if (!photo) return;
    setCamera(false);
    setError(null);
    if (!ui.server.online) {
      setError("Reading a photo needs the server. Start the backend, or try the sample receipt.");
      return;
    }
    setBusy(true);
    ui.say("Reading the receipt");
    try {
      open(await api.readReceipt(photo, k.customs, k.corrections));
    } catch (err) {
      setError(err.message);
    }
    setBusy(false);
  };

  const onFile = (e) => {
    const photo = e.target.files?.[0];
    e.target.value = "";                      // the same photo can be chosen again after a failure
    read(photo);
  };

  const takePhoto = useCallback(() => {
    setError(null);
    if (cameraAvailable()) setCamera(true);
    else shootInput.current?.click();
  }, []);

  /* Chrome and Edge: a picker with an id remembers the folder it was last used
     in, so after the first receipt it opens in your receipts folder. The first
     time it starts in Pictures. Other browsers get the plain file input. */
  const importPhoto = async () => {
    setCamera(false);
    setError(null);
    if (!window.showOpenFilePicker) { importInput.current?.click(); return; }
    try {
      const [handle] = await window.showOpenFilePicker({
        id: "receipts",
        startIn: "pictures",
        multiple: false,
        types: [{ description: "Receipt photos", accept: { "image/*": [".jpg", ".jpeg", ".png", ".webp"] } }],
      });
      read(await handle.getFile());
    } catch (err) {
      if (err?.name !== "AbortError") importInput.current?.click();
    }
  };

  // "Scan a receipt" from the menu or the empty kitchen: straight to the camera.
  useEffect(() => {
    if (!ui.receiptCamera) return;
    ui.receiptCameraOpened();
    takePhoto();
  }, [ui.receiptCamera]); // eslint-disable-line

  const capture = (
    <>
      {camera && <ReceiptCamera onPhoto={read} onClose={() => setCamera(false)} onImport={importPhoto} />}
      <input ref={shootInput} type="file" accept="image/*" capture="environment" hidden onChange={onFile} />
      <input ref={importInput} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={onFile} />
    </>
  );

  if (!t) return (
    <div className="screen">
      <h1 className="screen-title">Receipt</h1>
      {busy ? (
        <div className="dropzone" aria-live="polite">
          <Icon name="receipt" size={48} />
          <span className="empty-note">Reading the receipt and looking each line up. This can take a minute.</span>
        </div>
      ) : (
        <div className="dropzone is-actions">
          <Icon name="receipt" size={48} />
          <span className="empty-note">Photograph the receipt flat, well lit, not folded.</span>
          <div className="dropzone-actions">
            <button className="btn btn-primary" onClick={takePhoto}><Icon name="camera" /> Photograph a receipt</button>
            <button className="btn btn-ghost" onClick={importPhoto}><Icon name="image" /> Import a receipt</button>
          </div>
        </div>
      )}
      {capture}
      {error && <div className="band is-warm"><b>That didn't work</b><span>{error}</span></div>}
      {!busy && <button className="link link-block" onClick={() => open(SAMPLE_RECEIPT)}>Use a sample receipt</button>}
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
      {capture}
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
