/* "How was this picked?" — the ranking, opened up. Four numbers, each on
   its own scale, added together. Nothing hidden, nothing weighted in secret. */

import { SCORE_PARTS } from "../core/engine.js";
import { RangeBar } from "../components/Charts.jsx";
import { listOf } from "../screens/Today.jsx";

const sign = (v) => (v > 0 ? "+" : "") + v.toFixed(2);

export default function ScoreSheet({ pick }) {
  const p = pick.parts;
  const notes = {
    coverage: `${pick.have} of ${pick.total} ingredients are in, in the amounts this table needs.`,
    urgency: pick.urgent.length ? `Uses ${listOf(pick.urgent).toLowerCase()} before it goes off.` : "Uses nothing that's about to go off.",
    speed: `${pick.recipe.minutes} minutes. Anything under an hour earns a little.`,
    taste: p.taste === 0 ? "No reviews yet — so this part can't help. Rate what you cook and it starts to."
         : p.taste < 0 ? "You said “not again”. Buried, not banned." : "Built from what you said you liked."
  };
  return (
    <>
      <div className="score-total">
        <span className="score-num">{pick.score.toFixed(2)}</span>
        <p className="sub-note">The dish with the highest total is tonight's. Here's where this one's came from.</p>
      </div>
      {Object.entries(SCORE_PARTS).map(([key, meta]) => (
        <section key={key} className="score-part">
          <div className="score-row"><h3>{meta.label}</h3><span className="score-val">{sign(p[key])}</span></div>
          <RangeBar value={p[key]} min={meta.min} max={meta.max} shown={sign(p[key])}
                    left={meta.min === 0 ? "0" : meta.min} right={"+" + meta.max} />
          <p className="sub-note">{notes[key]}</p>
        </section>
      ))}
    </>
  );
}
