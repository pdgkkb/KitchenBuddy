/* The gap in a step, and the one small job that fits in it.

   "My rice is frying for four minutes. You have a 90-second window: rinse the
   knife and the cutting board."

   A step with a clock on it is mostly waiting, and waiting at the hob is where
   the washing-up piles up. This finds the gap and names ONE job for it — the
   job that makes the end of dinner shorter, not a list.

   No model. It is read off the recipe the cook is already holding: what has
   been cut so far, what comes next, what came out of the fridge. So it is
   instant, it works with the server off, and it never invents a chore for a
   knife that was never used.

   Two kinds of gap:
   - Frying, searing, stir-frying: the pan needs a stir every minute or two, so
     the window is 90 seconds whatever the step's total — you come back, stir,
     and there is another one.
   - Simmering, boiling, baking, resting: the whole step less a minute to get
     back to the pan, up to ten minutes. */

import { ref, stepMinutes } from "./engine.js";
import { stepHeat } from "./brief.js";

const ACTIVE = /\b(fry|fries|frying|fried|stir|saut|sear|scrambl|toss|toast|brown|carameli[sz]|wilt)/i;
// Waiting with no heat on: the only gaps a step without a heat can have.
const RESTING = /\b(rest|marinat|soak|chill|stand|sit|leave|prove|cool)/i;
// Prep worth doing ahead. "to serve" isn't a job.
const PREP_JOB = /(chop|slic|dic|cut|minc|grat|crush|peel|halv|juic|drain|beat|whisk|trim|shred|quarter|rinse|wash)/i;
// "In a frying pan" names a pan; it doesn't mean the step is frying.
const withoutPans = (text) => String(text || "").replace(/\bfrying pan\b/gi, "pan");
const CUT = /\b(chop|slice|dice|cut|mince|grate|peel|trim|halve|shred|crush|julienne|slic|dic)/i;
const BOWL = /\b(whisk|beat|crack|mix|combine)/i;
const FRIDGE = new Set(["dairy", "meat", "seafood"]);

const STIR_GAP = 90;
const MAX_GAP = 600;

/* idleWindow(recipe, i, done, { hasLeftover }) -> null | { seconds, active, key, task, say }
   `done` is the keys of jobs already handed out this session, so the same
   chore isn't suggested on every step. `hasLeftover(id)` says whether any of
   an ingredient is left after this recipe — without it, nothing is sent back
   to the fridge, because "put the cod back" after all of it went in the pan
   is a job that doesn't exist.

   A gap needs something to wait FOR: a heat under the step, or resting,
   marinating, soaking. "Take the cooked rice out and break it up" is three
   minutes of work, not three minutes of waiting, whatever "cooked" says. */
export function idleWindow(recipe, i, done = [], opts = {}) {
  const steps = recipe?.steps || [];
  const step = steps[i];
  const minutes = stepMinutes(step);
  if (!step || !(minutes >= 3)) return null;
  const text = withoutPans(step.do);
  const heated = !!stepHeat(step);
  const active = heated && ACTIVE.test(text);
  if (!heated && !RESTING.test(text)) return null;

  const seconds = active
    ? STIR_GAP
    : Math.min(MAX_GAP, Math.floor((minutes * 60 - 60) / 30) * 30);
  if (seconds < 60) return null;

  const job = jobs(recipe, i, seconds, opts).find(j => !done.includes(j.key));
  if (!job) return null;
  return { seconds, active, key: job.key, task: job.text, say: `You have a ${windowLabel(seconds)} window. ${job.text}` };
}

export function windowLabel(seconds) {
  if (seconds < 120) return `${seconds}-second`;
  return `${Math.floor(seconds / 60)}-minute`;
}

/* In order of how much they shorten the evening. */
function jobs(recipe, i, seconds, { hasLeftover } = {}) {
  const steps = recipe.steps || [];
  const needs = recipe.needs || [];
  const name = (id) => (ref(id).name || String(id).replace(/^c_/, "").replace(/_/g, " ")).toLowerCase();
  const sofar = steps.slice(0, i + 1);
  const usedSoFar = new Set(sofar.flatMap(s => s.uses || []));
  const saidSoFar = sofar.map(s => s.do || "").join(" ").toLowerCase();
  // "Chop the spinach and beat the eggs" readied both without adding either.
  const named = (id) => name(id).split(/\s+/).filter(w => w.length > 2)
    .some(w => saidSoFar.includes(w.replace(/e?s$/, "")));
  const out = [];

  // Get ahead: whatever the next step adds that still needs cutting.
  const next = steps[i + 1];
  if (next && seconds >= STIR_GAP) {
    for (const id of next.uses || []) {
      const need = needs.find(n => n.id === id);
      if (need?.prep && PREP_JOB.test(need.prep) && !usedSoFar.has(id) && !named(id)) {
        out.push({ key: `prep:${id}`, text: `Get the ${name(id)} ${need.prep.toLowerCase()} for the next step.` });
        break;
      }
    }
  }

  // Clean up after what has already been done.
  const cut = sofar.some(s => CUT.test(s.do || ""))
    || needs.some(n => usedSoFar.has(n.id) && CUT.test(n.prep || ""));
  if (cut) out.push({ key: "knife", text: "Rinse the knife and the cutting board." });
  if (sofar.some(s => BOWL.test(s.do || ""))) out.push({ key: "bowl", text: "Rinse the bowl and put it away." });

  const chilled = [...usedSoFar].filter(id => FRIDGE.has(ref(id).category) && hasLeftover?.(id));
  if (chilled.length) {
    const names = chilled.slice(0, 2).map(name);
    out.push({ key: `fridge:${chilled.join(",")}`, text: `Put the ${names.join(" and ")} back in the fridge.` });
  }

  if (i >= steps.length - 2) out.push({ key: "plates", text: "Set out the plates and forks." });
  out.push({ key: "counter", text: "Wipe down the counter." });
  return out;
}
