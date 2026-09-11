/* Happy Bite — "something quick and vegetarian" → filters, offline.

   A keyword matcher, not a language model. It covers how people actually
   phrase this out loud — short, two or three constraints — and fails on
   anything indirect ("we had pasta yesterday"). With the server's assistant
   on, /api/understand handles those; this is the path that always works.

   It always reports what it understood, so a wrong reading is visible. */

import { INGREDIENTS, CUISINES } from "../data/index.js";

const TIME = [
  { rx: /\b(quick|fast|hurry|rush|no time|15|fifteen)\b/i, filters: { maxMinutes: 20 }, said: "under 20 minutes" },
  { rx: /\b(30|thirty|half an hour)\b/i, filters: { maxMinutes: 30 }, said: "under 30 minutes" },
  { rx: /\b(slow|long|take my time|weekend)\b/i, filters: { maxComplexity: 3 }, said: "time isn't a problem" }
];
const EFFORT = [
  { rx: /\b(easy|simple|lazy|effortless|can'?t be bothered)\b/i, filters: { maxComplexity: 1 }, said: "nothing fiddly" },
  { rx: /\b(proper|project|impress|fancy|ambitious)\b/i, filters: { maxComplexity: 3 }, said: "happy to put work in" }
];
const MEAL = [
  { rx: /\b(breakfast|morning)\b/i, filters: { mealType: "breakfast" }, said: "breakfast" },
  { rx: /\blunch\b/i, filters: { mealType: "lunch" }, said: "lunch" },
  { rx: /\b(dinner|tonight|evening|supper)\b/i, filters: { mealType: "dinner" }, said: "dinner" },
  { rx: /\b(snack|nibble|something small)\b/i, filters: { mealType: "snack" }, said: "a snack" }
];
const MEAT = ["chicken", "beef", "pork", "cod"];
const DIETARY = [
  { rx: /\b(vegetarian|veggie|no meat|meat.?free)\b/i, avoid: MEAT, said: "no meat or fish" },
  { rx: /\b(vegan|no dairy|dairy.?free)\b/i,
    avoid: [...MEAT, "egg", "milk", "cream", "goat", "parmesan", "butter", "yoghurt"], said: "no animal products" },
  { rx: /\b(no fish|without fish)\b/i, avoid: ["cod"], said: "no fish" },
  { rx: /\bno pork\b/i, avoid: ["pork"], said: "no pork" }
];

const findCuisine = (text) =>
  CUISINES.find(c => new RegExp("\\b" + c + "\\b", "i").test(text)) || null;

function findIngredient(text) {
  const t = text.toLowerCase();
  let best = null;
  for (const [id, r] of Object.entries(INGREDIENTS)) {
    const word = r.name.toLowerCase().replace(/[^a-z ]/g, "").split(" ")[0];
    if (word.length < 4) continue;
    const stem = word.replace(/e?s$/, "");
    if (new RegExp("\\b" + stem).test(t) && (!best || stem.length > best.stem.length)) best = { id, stem };
  }
  return best ? best.id : null;
}

export function parse(text) {
  const filters = {};
  const avoid = [];
  const understood = [];
  for (const set of [TIME, EFFORT, MEAL]) {
    const rule = set.find(r => r.rx.test(text));
    if (rule) { Object.assign(filters, rule.filters); understood.push(rule.said); }
  }
  for (const rule of DIETARY) {
    if (rule.rx.test(text)) { avoid.push(...rule.avoid); understood.push(rule.said); }
  }
  const cuisine = findCuisine(text);
  if (cuisine) { filters.cuisine = cuisine; understood.push(cuisine + " food"); }
  const ingredient = findIngredient(text);
  if (ingredient) {
    filters.mustUse = ingredient;
    understood.push("with " + INGREDIENTS[ingredient].name.toLowerCase());
  }
  return { filters, avoid: [...new Set(avoid)], understood, blank: understood.length === 0, source: "local" };
}
