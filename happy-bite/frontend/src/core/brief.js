/* A step, said the way a cook beside you would say it.

   "Marinate the chicken cubes." With what? In what? The step text a model
   writes is a heading, and read aloud it sends you back to the screen with
   wet hands. So the spoken step is built from the whole recipe, not just the
   line: what to grab if it's a new bowl or pan, the step itself, and what goes
   in, with amounts scaled to the table.

     "Step 1. Grab a bowl. Marinate the chicken cubes. You'll need 400 grams
      of chicken breast, a splash of olive oil and 2 cloves of garlic, plus salt and
      oregano. Tell me when that's done."

   No model: read off the recipe, so it's instant and it can't invent an
   ingredient. Runs under Node (`npm test`). */

import { ref, scale } from "./engine.js";

/* A heat on "Mix the marinade" is a form field the model filled in, and "On
   low heat, mix the marinade" is nonsense said out loud. But "Add the
   tomatoes" on medium is a real hob step that just doesn't say "fry" — so a
   heat is only dropped from a step that is plainly off the hob (washing,
   mixing, chopping, threading, blending) and says nothing about cooking.
   The same rule as the server's. */
const HOB = /\b(fr(y|ies|ied|ying)|boil|simmer|saut|sear|heat|bak|roast|grill|toast|brown|melt|steam|poach|cook|pan|wok|oven|skillet|reduc|warm|scrambl|blanch|char|carameli|crisp|wilt|stir|broil|hob|flame)/i;
const OFF_HOB = /\b(bowl|wash|rinse|pat\b|marinat|mix\b|mixing|whisk|beat\b|combine|thread|skewer|chop|slice|dice|cut\b|mince|grate|peel|blend|mash|knead)/i;

export function stepHeat(step) {
  if (!step?.heat) return "";
  const text = `${step.do || ""} ${step.heat}`;
  return OFF_HOB.test(step.do || "") && !HOB.test(text) ? "" : step.heat;
}

/* "no cue", "n/a", "none needed": what a model writes into a field it has
   nothing for. Printed as "until no cue", which is how it appeared on screen. */
const EMPTY_CUE = /^(n\/?a|none( needed)?|no cue|not needed|nothing( specific)?|null|-|—)\.?$/i;
export function realCue(step) {
  const cue = String(step?.cue || "").trim();
  return cue && !EMPTY_CUE.test(cue) ? cue : "";
}

/* "On medium-high heat, …" — but "In the oven at 200 °C, …". */
function heatPhrase(heat) {
  const oven = heat.match(/oven\s*(.*)/i);
  if (oven) return oven[1] ? `In the oven at ${oven[1].trim()}, ` : "In the oven, ";
  return `On ${heat.toLowerCase()} heat, `;
}

/* What a step happens in. A step that names its container ("in a bowl",
   "heat a pan") has said it; otherwise the verb decides, cooking verbs first,
   so "pour the whisked eggs into the pan" is a pan and not a bowl. */
const NAMED = [
  ["pot", /\b(pot|saucepan|pan of (salted )?water)\b/i],
  ["blender", /\b(blender|food processor)\b/i],
  ["bowl", /\bbowls?\b/i], ["pan", /\b(pan|wok|skillet|frying pan)\b/i],
  ["tray", /\b(tray|baking dish|roasting tin|oven dish)\b/i],
];
const BY_VERB = [
  ["pot", /\b(boil|simmer|blanch|poach|bring .*\b(up|to the boil))/i],
  ["pan", /\b(fr(y|ies|ied|ying)|saut|sear|scrambl|stir-?fr|toast|brown|carameli|soften|sweat)|\bin (plenty of |a little |a splash of )?oil\b/i],
  ["tray", /\b(bak|roast)/i],
  ["blender", /\bblend/i],
  ["bowl", /\b(marinat|whisk|beat\b|mix\b|mixing|combine|coat|mash|knead)/i],
];
const SAY = { bowl: "a bowl", pan: "a frying pan", pot: "a pot", tray: "a baking tray", blender: "a blender or food processor" };

export function stepVessel(step) {
  const text = step?.do || "";
  for (const [key, re] of NAMED) if (re.test(text)) return { key, named: true };
  for (const [key, re] of BY_VERB) {
    // A step with a hob heat is in a pan or a pot, whatever its verb says:
    // "toss the pasta through the sauce" on medium is not a bowl.
    if ((key === "bowl" || key === "blender") && stepHeat(step)) continue;
    if (re.test(text)) return { key, named: false };
  }
  return null;
}

/* "Grab a bowl" — only when this step needs something the step before it
   wasn't already using, and the step doesn't say so itself. */
export function vesselToGrab(recipe, i) {
  const steps = recipe?.steps || [];
  const here = stepVessel(steps[i]);
  if (!here || here.named) return "";
  // Still at the hob from the step before: the curry that started in a pan
  // is simmered in the same pan, not in a pot fetched halfway through.
  const hob = (st) => stepHeat(st) && !/oven/i.test(stepHeat(st));
  if (here.key !== "tray" && hob(steps[i]) && i > 0 && hob(steps[i - 1])) return "";
  for (let j = i - 1; j >= 0; j--) {
    const before = stepVessel(steps[j]);
    if (before) return before.key === here.key ? "" : SAY[here.key];
  }
  return SAY[here.key];
}

const nameOf = (id) => (ref(id).name || String(id).replace(/^c_/, "").replace(/_/g, " ")).toLowerCase();
const wordsOf = (id) => nameOf(id).split(/\s+/).filter(w => w.length > 2).map(w => w.replace(/e?s$/, ""));
const mentions = (text, id) => wordsOf(id).some(w => new RegExp(`\\b${w}`, "i").test(text));

/* What goes in at this step, the first time it's needed. The step's own
   `uses` when the recipe has them; otherwise the ingredients the step names.
   Something already used in an earlier step isn't read out again. */
export function stepIngredients(recipe, i) {
  const steps = recipe?.steps || [];
  const all = [...(recipe?.needs || []).map(n => ({ ...n, seasoning: false })),
               ...(recipe?.seasoning || []).map(s => ({ ...s, seasoning: true }))];
  const at = (n) => steps[n]?.uses?.length
    ? all.filter(x => steps[n].uses.includes(x.id))
    : all.filter(x => mentions(steps[n]?.do || "", x.id));
  const earlier = new Set();
  for (let n = 0; n < i; n++) at(n).forEach(x => earlier.add(x.id));
  return at(i).filter(x => !earlier.has(x.id));
}

const POUR = /\b(oil|vinegar)\b/i;

/* "1 onions", "2 garlic": the catalogue names things in the plural and
   counts garlic in heads it calls cloves. Said out loud, both are wrong. */
function singular(name) {
  const words = name.split(" ");
  const last = words.pop();
  const one = /oes$/.test(last) ? last.slice(0, -2)
    : /ies$/.test(last) ? last.slice(0, -3) + "y"
    : /(ch|sh|ss|x)es$/.test(last) ? last.slice(0, -2)
    : /[^s]s$/.test(last) ? last.slice(0, -1) : last;
  return [...words, one].join(" ");
}

function counted(n, name) {
  if (/^garlic$/.test(name)) return `${n} ${n === 1 ? "clove" : "cloves"} of garlic`;
  return `${n} ${n === 1 ? singular(name) : name}`;
}

const round = (x, step) => Math.max(step, Math.round(x / step) * step);

function spokenAmount(item, recipe, serves) {
  const name = nameOf(item.id);
  const unit = ref(item.id).unit || "g";
  if (!item.qty) return name;
  const from = Number(recipe.serves) > 0 ? Number(recipe.serves) : 4;
  const raw = Number(item.qty) * ((Number(serves) > 0 ? Number(serves) : from) / from);
  if (unit === "u") return counted(Math.max(1, Math.round(raw)), name);
  if (unit === "g" || unit === "kg") {
    const g = unit === "kg" ? raw * 1000 : scale(raw, 1, 1, "g");
    return g >= 1000 ? `${Math.round(g / 100) / 10} kilos of ${name}` : `${Math.round(g)} grams of ${name}`;
  }
  // Liquids in millilitres: nobody pours "2 centilitres" or "0.7 litres".
  const ml = unit === "l" ? raw * 1000 : unit === "cl" ? raw * 10 : raw;
  if (POUR.test(name) && ml <= 30) return `a splash of ${name}`;
  if (ml >= 1000) return `${Math.round(ml / 100) / 10} litres of ${name}`;
  return `${round(ml, ml >= 100 ? 50 : 10)} millilitres of ${name}`;
}

const list = (a) => a.length <= 1 ? (a[0] || "") : a.slice(0, -1).join(", ") + " and " + a[a.length - 1];

export function needsLine(recipe, i, serves) {
  const items = stepIngredients(recipe, i);
  const main = items.filter(x => !x.seasoning).map(x => spokenAmount(x, recipe, serves));
  const season = items.filter(x => x.seasoning).map(x => nameOf(x.id));
  if (!main.length && !season.length) return "";
  if (!main.length) return `Season with ${list(season)}.`;
  return `You'll need ${list(main)}${season.length ? `, plus ${list(season)}` : ""}.`;
}

/* `extra` is said after the step and before handing the turn back — where
   the gap in a timed step goes: "…While it cooks, you have a 90-second
   window. Rinse the bowl and put it away. Tell me when that's done." */
export function stepBrief(recipe, idx, serves, ask = true, extra = "") {
  const steps = recipe?.steps || [];
  const s = steps[idx];
  if (!s) return "";
  const total = steps.length;
  const lead = idx + 1 === total ? "Last step." : `Step ${idx + 1}.`;
  const grab = vesselToGrab(recipe, idx);
  const heat = stepHeat(s);
  const said = String(s.do || "").trim().replace(/[.!]*$/, ".");
  // "Turn the oven on to 200 °C" and "heat a pan over medium heat" already
  // say it; don't say it twice.
  const saysItself = (/oven/i.test(heat) && /\boven\b/i.test(said))
    || (heat && said.toLowerCase().includes(`${heat.toLowerCase()} heat`));
  const body = heat && !saysItself ? `${heatPhrase(heat)}${said.charAt(0).toLowerCase()}${said.slice(1)}` : said;
  const cue = realCue(s) ? `Look for ${realCue(s).toLowerCase().replace(/[.!]*$/, "")}.` : "";
  const prompt = !ask ? "" : idx + 1 === total ? "And that's it — enjoy." : "Tell me when that's done.";
  return [lead, grab && `Grab ${grab}.`, body, needsLine(recipe, idx, serves), cue, extra, prompt]
    .filter(Boolean).join(" ");
}
