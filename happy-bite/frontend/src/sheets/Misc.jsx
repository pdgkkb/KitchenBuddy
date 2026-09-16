/* The review loop, and the assistant settings. */

import { useState } from "react";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { nameOf } from "../screens/Today.jsx";
import * as wake from "../lib/wake.js";

const TWEAKS = ["Needs more salt", "Too salty", "Needs more spice", "Too spicy", "Too rich", "Too dry",
  "Needs more sauce", "Bland", "Cook it longer", "Cook it less", "Bigger portions", "Smaller portions",
  "Texture was off", "Took longer than it said"];

/* All taps, no typing. Somebody who has just eaten will tap four things;
   they won't write a paragraph. Four taps is enough to change next week. */
export function ReviewSheet({ recipe: r }) {
  const { saveReview } = useKitchen();
  const ui = useUI();
  const [verdict, setVerdict] = useState(null);
  const [marks, setMarks] = useState({});       // id -> 1 liked, -1 disliked
  const [tweaks, setTweaks] = useState(new Set());
  const parts = [...r.needs.map(n => n.id), ...(r.seasoning || []).filter(x => x.essential).map(x => x.id)];

  const cycle = (id) => setMarks(m => ({ ...m, [id]: m[id] === 1 ? -1 : m[id] === -1 ? 0 : 1 }));

  return (
    <>
      <div className="verdicts">
        {[["love", "Loved it"], ["fine", "It was fine"], ["no", "Not again"]].map(([v, label]) => (
          <button key={v} className={"verdict" + (verdict === v ? " is-on" : "")} aria-pressed={verdict === v}
                  onClick={() => setVerdict(v)}>{label}</button>
        ))}
      </div>
      <h3 className="sub-head">What worked?</h3>
      <p className="sub-note">Tap what you enjoyed; tap again for what you didn't. This decides what comes up next week.</p>
      <div className="chips">
        {parts.map(id => <button key={id} className={"chip" + (marks[id] === 1 ? " is-on" : marks[id] === -1 ? " is-off" : "")}
                                 onClick={() => cycle(id)}>{nameOf(id)}</button>)}
      </div>
      <h3 className="sub-head">Anything off?</h3>
      <div className="chips">
        {TWEAKS.map(t => <button key={t} className={"chip" + (tweaks.has(t) ? " is-on" : "")}
          onClick={() => setTweaks(s => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; })}>{t}</button>)}
      </div>
      <button className="btn btn-primary" style={{ marginTop: 24 }} onClick={() => {
        if (!verdict) return ui.say("Pick one of the three first");
        saveReview({ recipe: r.id, verdict, tweaks: [...tweaks],
          liked: Object.keys(marks).filter(id => marks[id] === 1),
          disliked: Object.keys(marks).filter(id => marks[id] === -1) });
        ui.closeSheet();
        ui.say(verdict === "no" ? "Noted — it'll come up less" : "Noted — that'll shape what comes next");
      }}>Save</button>
      <button className="link link-block" onClick={ui.closeSheet}>Skip</button>
    </>
  );
}

export function ServerSheet() {
  const { k, setPref } = useKitchen();
  const ui = useUI();
  const s = ui.server;
  const phrase = wake.wakeLabel(s.wakeWord);

  /* What the wake word can do HERE, worked out before it is switched on.
     "It doesn't work" was, four times running, one of these three sentences
     that nothing on screen ever said. */
  const canWake = wake.wakeSupported();

  const Line = ({ on, label, detail }) => (
    <li className="status-line">
      <span className={"status-dot" + (on ? " is-on" : "")} aria-hidden="true" />
      <span className="row-text"><span className="row-name">{label}</span><span className="row-sub">{detail}</span></span>
      <span className="row-tag">{on ? "On" : "Off"}</span>
    </li>
  );
  return (
    <>
      <div className="band"><b>What stays on this device</b>
        <span>Your stock, household, reviews and recipes live in this browser. The server stores none of it.
          Only a question to the chef — with the recipe and a stock list, never names — leaves the device, and only when you ask.</span></div>
      <ul className="rows">
        <Line on={s.online} label="Server" detail={s.online ? "Reachable" : "Not reachable — everything below is off, the kitchen still works"} />
        <Line on={s.chat} label="The chef" detail={s.chat ? (s.local ? "A model on your own network" : s.model) : "Needs a key in backend/.env"} />
        <Line on={s.images} label="Photos of dishes and steps" detail={s.images ? "On request only" : "Needs an image key"} />
        <Line on={s.voice} label="Server voice" detail={s.voice ? "Listening and speaking through the server" : "Using the browser's own voice"} />
      </ul>
      <h3 className="sub-head">Reading aloud</h3>
      <div className="chips">
        <button className={"chip" + (k.prefs.speakReplies ? " is-on" : "")} aria-pressed={k.prefs.speakReplies}
                onClick={() => setPref("speakReplies", !k.prefs.speakReplies)}><Icon name="chef" size={20} /> The chef's replies</button>
        <button className={"chip" + (k.prefs.readSteps ? " is-on" : "")} aria-pressed={k.prefs.readSteps}
                onClick={() => setPref("readSteps", !k.prefs.readSteps)}><Icon name="list" size={20} /> Each step while cooking</button>
      </div>

      <h3 className="sub-head">Hands-free</h3>
      <p className="sub-note">With this on, the app listens for “{phrase}” whenever you aren't already talking to it, so you
        can start with flour on your hands. It keeps the microphone open to do that, and in Chrome the browser sends what it
        hears to Google to recognise it. It's on unless you switch it off here, or by holding the mic button down. Say
        “{phrase}” and what you want in one go — “{phrase}, I'm drained, give me 15 minutes” — or “{phrase}”, wait for
        the chime, then talk.</p>
      <div className="chips">
        <button className={"chip" + (k.prefs.handsFree !== false ? " is-on" : "")} aria-pressed={k.prefs.handsFree !== false}
                disabled={canWake !== "ok"}
                onClick={() => setPref("handsFree", k.prefs.handsFree === false)}>
          <Icon name="mic" size={20} /> Listen for the wake word
        </button>
      </div>
      {canWake !== "ok" && (
        <div className="band is-warm" style={{ marginTop: 12 }}>
          <b>The wake word can't run in this browser</b>
          <span>{wake.WAKE_REASON[canWake]}</span>
        </div>
      )}
      {canWake === "ok" && k.prefs.handsFree !== false && (
        <p className="sub-note" style={{ marginTop: 10 }}>
          It's on. Close this and say “{phrase}” — the mic button shows a ring while it's listening for you, and says
          underneath if something stops it.
        </p>
      )}

      <button className="btn btn-ghost" style={{ marginTop: 24 }} onClick={ui.refreshServer}>Check the server again</button>
    </>
  );
}
