/* What you can actually cook with.

   The mirror of backend/app/adapt.py, on the browser's side. It lives in core/
   with the rest of the kitchen logic because it must work with the server off:
   knowing that tonight's recipe wants an oven you haven't got is a fact about
   the recipe, not a question for a model. Only the *adaptation* needs the chef.

   Keep the ids here and in adapt.py in step — they cross the wire by name. */

export const EQUIPMENT = [
  { id: "hob", name: "Hob", note: "Gas, electric or induction rings" },
  { id: "pan", name: "Frying pan", note: "Skillet, sauté pan" },
  { id: "saucepan", name: "Saucepan", note: "For boiling and simmering" },
  { id: "oven", name: "Oven", note: "Baking and roasting" },
  { id: "grill", name: "Grill", note: "Overhead grill or broiler" },
  { id: "airfryer", name: "Air fryer", note: "" },
  { id: "microwave", name: "Microwave", note: "" },
  { id: "pressure", name: "Pressure cooker", note: "Instant Pot and the like" },
  { id: "slowcooker", name: "Slow cooker", note: "" },
  { id: "steamer", name: "Steamer", note: "Basket or electric" },
  { id: "bbq", name: "Barbecue", note: "" },
  { id: "wok", name: "Wok", note: "" },
  { id: "blender", name: "Blender", note: "Jug or stick" },
  { id: "processor", name: "Food processor", note: "" },
  { id: "ricecooker", name: "Rice cooker", note: "" },
  { id: "toaster", name: "Toaster", note: "" },
];

/* A sensible starting point when somebody opens the sheet for the first time.
   Almost every kitchen has these; the point of the sheet is what's missing. */
export const COMMON = ["hob", "pan", "saucepan", "oven"];

const BY_ID = Object.fromEntries(EQUIPMENT.map(e => [e.id, e]));

export const equipName = (id) => BY_ID[id]?.name || id;

/* Said in a sentence: "an oven and a grill". */
export function equipList(ids) {
  const names = (ids || []).map(id => equipName(id).toLowerCase());
  if (!names.length) return "";
  if (names.length === 1) return names[0];
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

/* Same patterns as adapt.py's WANTS, same order. */
const WANTS = [
  ["oven", /\boven|\bbake[ds]?\b|\bbaking\b|\broast(?:ed|ing)?\b|\bgratin|\b(?:180|190|200|210|220|230|240)\s*°?\s*c\b/i],
  ["grill", /\bgrill(?:ed|ing)?\b|\bbroil(?:ed|er|ing)?\b/i],
  ["airfryer", /\bair[- ]?fry(?:er|ing)?\b/i],
  ["microwave", /\bmicrowave[ds]?\b/i],
  ["pressure", /\bpressure cook|\binstant pot\b/i],
  ["slowcooker", /\bslow cook(?:er|ed|ing)?\b|\bcrock ?pot\b/i],
  ["steamer", /\bsteam(?:ed|er|ing)\b/i],
  ["bbq", /\bbarbecue|\bbbq\b|\bover coals\b/i],
  ["wok", /\bwok\b|\bstir[- ]?fry/i],
  ["blender", /\bblend(?:ed|er|ing)?\b|\bpur[ée]e[ds]?\b|\bliquidi[sz]e/i],
  ["processor", /\bfood processor\b/i],
  ["pan", /\bfrying pan\b|\bskillet\b|\bsear(?:ed|ing)?\b|\bfry\b|\bfrying\b|\bsaut[ée](?:ed|ing)?\b|\bpan[- ]fry/i],
  ["saucepan", /\bsaucepan\b|\bboil(?:ed|ing)?\b|\bsimmer(?:ed|ing)?\b|\bpot\b|\bstock ?pot\b/i],
  ["hob", /\bhob\b|\bstove ?top\b|\bmedium[- ]high heat\b|\bover (?:a )?low heat\b/i],
];

const IMPLIES = { pan: "hob", saucepan: "hob", wok: "hob" };

/* Every appliance the recipe's own words ask for. Reads `heat` too — "Oven
   200 °C" lives there and is the clearest signal a recipe gives. */
export function wantedBy(recipe) {
  const found = [];
  for (const step of recipe?.steps || []) {
    const text = [step.do, step.heat, step.cue, step.why].filter(Boolean).join(" ");
    for (const [id, rx] of WANTS) if (!found.includes(id) && rx.test(text)) found.push(id);
  }
  for (const id of [...found]) {
    const implied = IMPLIES[id];
    if (implied && !found.includes(implied)) found.push(implied);
  }
  return found;
}

/* What the recipe wants that this kitchen hasn't got.

   `owned` of null means nobody has said yet. That is not the same as owning
   nothing: we stay quiet rather than telling someone their own kitchen is
   missing an oven they never mentioned. */
export function missingFor(recipe, owned) {
  if (!owned) return [];
  return wantedBy(recipe).filter(id => !owned.includes(id));
}

/* The key an adaptation is stored under: the recipe plus the exact kit it was
   written for, so buying an air fryer invalidates yesterday's answer instead of
   quietly serving it forever. */
export const adaptKey = (recipeId, owned) => `${recipeId}::${[...(owned || [])].sort().join(",")}`;
