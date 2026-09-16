/* Happy Bite — kitchen stock questions and changes, answered without a model.

   The four things people say most about what's on the shelves, each with its
   own rule for how much to say back:

     "add 200 grams of chicken", "I used half a litre of milk"
         -> change the stock, confirm with the new total
     "what type of meat do we have", "any fish?"
         -> NAMES ONLY. They asked what kinds, not how much.
     "do we have eggs"
         -> yes or no, with the name. Still no amount.
     "how much chicken do we have", "how many eggs are left"
         -> the exact amount, said the way a person says it.

   Sending these to the local model took seconds, and a small model narrates
   "I've added the chicken" without calling the tool, so nothing changed. Here
   they are instant and the change is real.

   parseStock(text, stock) -> null | { say } | { say, action }
   Anything it isn't sure about returns null and goes to the chef as before —
   an unknown ingredient, a question it can't place, a sentence with a recipe
   or the shopping list in it. */

import { INGREDIENTS } from "../data/index.js";
import { ref } from "../core/engine.js";

/* ------------------------------------------------------------------ words */

const fold = (s) => String(s || "").normalize("NFD").replace(/\p{M}/gu, "");

function clean(text) {
  return fold(String(text || "").toLowerCase())
    .replace(/(\d),(\d)/g, "$1.$2")                 // 1,5 kg
    .replace(/['’]/g, "")                            // what's -> whats
    .replace(/(\d)([a-z])/g, "$1 $2")                // 200g -> 200 g
    .replace(/[^\p{L}\p{N}.\s]/gu, " ")
    .replace(/(?<!\d)\.|\.(?!\d)/g, " ")             // full stops, not decimals
    .replace(/\s+/g, " ")
    .trim();
}

/* Plural to singular, well enough to compare "eggs" with "Eggs" and
   "tomatoes" with "Tomato". Not a linguist. */
const sing = (w) =>
  w.length <= 3 ? w
  : w.endsWith("ies") ? w.slice(0, -3) + "y"
  : /(ches|shes|xes|oes|sses)$/.test(w) ? w.slice(0, -2)
  : w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1)
  : w;

const wordsOf = (s) => clean(s).split(" ").filter(Boolean).map(sing);

const listWords = (arr) =>
  arr.length <= 1 ? (arr[0] || "") : arr.slice(0, -1).join(", ") + " and " + arr[arr.length - 1];

/* ------------------------------------------------------------- quantities */

const NUM = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, forty: 40, fifty: 50,
  half: 0.5, quarter: 0.25, couple: 2, few: 3, dozen: 12,
};

// Singular forms, because every word has been through sing().
const UNIT = {
  g: ["mass", 1], gr: ["mass", 1], gram: ["mass", 1], gramme: ["mass", 1],
  kg: ["mass", 1000], kilo: ["mass", 1000], kilogram: ["mass", 1000],
  mg: ["mass", 0.001], milligram: ["mass", 0.001],
  kgs: ["mass", 1000], lbs: ["mass", 453.6], pcs: ["count", 1],
  lb: ["mass", 453.6], pound: ["mass", 453.6], oz: ["mass", 28.35], ounce: ["mass", 28.35],
  ml: ["vol", 1], millilitre: ["vol", 1], milliliter: ["vol", 1],
  cl: ["vol", 10], centilitre: ["vol", 10], centiliter: ["vol", 10],
  l: ["vol", 1000], litre: ["vol", 1000], liter: ["vol", 1000], ltr: ["vol", 1000],
  piece: ["count", 1], pc: ["count", 1], unit: ["count", 1], item: ["count", 1], x: ["count", 1],
};
const CANON_ML = { ml: 1, cl: 10, l: 1000 };

/* The first amount in the words, and which words it used up.
   "200 grams", "half a litre", "two hundred g", "a dozen", "1.5 kg". */
function findAmount(words) {
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    let q = /^\d+(\.\d+)?$/.test(w) ? parseFloat(w) : NUM[w];
    if (q === undefined) continue;
    let j = i + 1;
    if (words[j] === "hundred") { q *= 100; j++; }
    if (words[j] === "and" && words[j + 1] === "a" && words[j + 2] === "half") { q += 0.5; j += 3; }
    if (w === "half" && (words[j] === "a" || words[j] === "an")) j++;       // half a litre
    if (words[j] === "dozen") { q *= 12; j++; }
    const unit = UNIT[words[j]];
    if (unit) j++;
    // A bare "a"/"an" is only an amount when something follows it that isn't
    // another number ("a chicken breast"), never "a few" on its own.
    if ((w === "a" || w === "an") && !unit && NUM[words[j]] !== undefined) continue;
    if (words[j] === "of") j++;
    return { qty: q, family: unit ? unit[0] : null, factor: unit ? unit[1] : 1, from: i, to: j };
  }
  return null;
}

/* What they said, in the ingredient's own unit — or null when it can't be
   known ("add two milk": two what?). Densities ignored, like the server's
   actions.py: a gram of milk is a millilitre, for stock-keeping. */
function toCanonical(amount, info) {
  const canon = info.unit || "g";
  const { qty, family, factor } = amount;
  if (canon === "u") {
    if (!family || family === "count") return qty;
    return info.perPiece ? Math.max(1, Math.round((qty * factor) / info.perPiece)) : null;
  }
  if (!family || family === "count") {
    return canon === "g" && info.perPiece ? qty * info.perPiece : null;   // "2 chicken breasts"
  }
  if (canon === "g") return round(qty * factor, 1);
  if (CANON_ML[canon]) return round((qty * factor) / CANON_ML[canon], 3);
  return null;
}

const round = (x, d) => Math.round(x * 10 ** d) / 10 ** d;
const n = (x) => String(round(x, 2));

/* "450 grams of chicken breast", "1.5 litres of milk", "6 eggs". */
function spoken(qty, unit, name) {
  const lower = name.toLowerCase();
  if (unit === "u") {
    if (qty === 1) return `1 ${lower.split(" ").map((w, i, a) => i === a.length - 1 ? sing(w) : w).join(" ")}`;
    return `${n(qty)} ${/s$/.test(lower) ? lower : lower + "s"}`;
  }
  if (unit === "g") return qty >= 1000 ? `${n(qty / 1000)} kilos of ${lower}` : `${n(qty)} gram${qty === 1 ? "" : "s"} of ${lower}`;
  const ml = qty * (CANON_ML[unit] || 1);
  if (ml >= 1000) return `${n(ml / 1000)} litre${ml === 1000 ? "" : "s"} of ${lower}`;
  return `${n(ml)} millilitres of ${lower}`;
}

/* ------------------------------------------------------------ ingredients */

// "chicken" means the meat, not chicken stock — unless they said stock.
const NOT_THE_THING = new Set(["broth", "stock", "bouillon", "cube", "powder", "seasoning", "sauce",
  "gravy", "flavor", "flavour", "soup", "cracker", "crumb", "bun", "bean", "fat", "dripping"]);

const nameWords = new Map();
const namesOf = (id) => {
  let w = nameWords.get(id);
  if (!w) { w = wordsOf(ref(id).name || id); nameWords.set(id, w); }
  return w;
};

function matches(id, want) {
  const have = namesOf(id);
  if (!want.every(w => have.includes(w))) return false;
  return !have.some(w => NOT_THE_THING.has(w) && !want.includes(w));
}

/* The one ingredient they mean. Their own shelves first; then the name said
   exactly; then the household's and the hand-written catalogue before the
   corpus's 3,000; then the shortest name. Adjectives the catalogue doesn't
   know ("fresh chicken") are dropped from the front until something fits. */
function resolve(want, stock) {
  for (let start = 0; start < want.length; start++) {
    const w = want.slice(start);
    const inStock = (stock || []).filter(s => s.qty > 0 && matches(s.id, w));
    if (inStock.length === 1) return { id: inStock[0].id };
    if (inStock.length > 1) {
      const exact = inStock.find(s => namesOf(s.id).length === w.length);
      return exact ? { id: exact.id } : { ask: inStock.map(s => ref(s.id).name || s.id) };
    }
    const found = Object.keys(INGREDIENTS).filter(id => matches(id, w));
    if (found.length) {
      const rank = (id) => [namesOf(id).length === w.length ? 0 : 1, id.startsWith("c_") ? 1 : 0, namesOf(id).length];
      found.sort((a, b) => {
        const x = rank(a), y = rank(b);
        return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
      });
      return { id: found[0] };
    }
  }
  return null;
}

/* Kinds of food, for "what meat do we have". Category ids from shared/catalog.json;
   a name pattern where a kind is narrower than its shelf. */
const KINDS = {
  meat: { cats: ["meat"] }, poultry: { cats: ["meat"], name: /chicken|turkey|duck|quail|poussin/ },
  protein: { cats: ["meat", "seafood"] },
  fish: { cats: ["seafood"] }, seafood: { cats: ["seafood"] },
  veg: { cats: ["produce"] }, veggie: { cats: ["produce"] }, vegetable: { cats: ["produce"] },
  veggy: { cats: ["produce"] }, fruit: { cats: ["produce"] }, produce: { cats: ["produce"] },
  dairy: { cats: ["dairy"] },
  cheese: { cats: ["dairy"], name: /chees|parmesan|mozzarella|feta|cheddar|brie|halloumi|ricotta|mascarpone|goat|gruyere|comte|emmental|camembert|roquefort|pecorino/ },
  spice: { cats: ["seasoning"] }, herb: { cats: ["seasoning"] }, seasoning: { cats: ["seasoning"] },
  frozen: { cats: ["frozen"] }, bread: { cats: ["bakery"] }, bakery: { cats: ["bakery"] },
  drink: { cats: ["drinks"] }, beverage: { cats: ["drinks"] },
  staple: { cats: ["pantry"] },
};

function ofKind(kind, stock) {
  return (stock || []).filter(s => {
    if (!(s.qty > 0)) return false;
    const r = ref(s.id);
    if (!kind.cats.includes(r.category)) return false;
    const words = namesOf(s.id);
    if (words.some(w => NOT_THE_THING.has(w))) return false;
    return !kind.name || kind.name.test((r.name || "").toLowerCase());
  });
}

/* ----------------------------------------------------------- the grammar */

// Stripped from the front: politeness, and the "I"/"we" before a verb.
const LEADS = new Set(["hey", "chef", "ok", "okay", "so", "and", "also", "please", "just", "can", "could",
  "would", "you", "will", "i", "we", "ive", "weve", "id", "wed", "right", "now", "then"]);

// Words in a question that aren't what it's about.
const SCAFFOLD = new Set(["what", "whats", "which", "how", "much", "many", "do", "doe", "did", "we", "i", "you",
  "u", "have", "ha", "had", "got", "gotten", "is", "are", "there", "thi", "that", "theres", "any", "some",
  "the", "a", "an", "of", "in", "on", "at", "left", "still", "remaining", "currently", "right", "now",
  "kitchen", "fridge", "freezer", "pantry", "cupboard", "stock", "inventory", "type", "kind", "sort",
  "variety", "varietie", "quantity", "amount", "tell", "me", "list", "please", "can", "could", "would",
  "chef", "hey", "ok", "okay", "so", "and", "all", "our", "my", "available", "home", "exactly", "total",
  "whatre", "ive", "weve", "dont", "to", "for", "cook", "with", "should", "know"]);

const ADD = ["add", "put", "bought", "got", "picked up", "pick up", "found", "stock up on", "restock", "pop", "place"];
const TAKE = ["remove", "take out", "took out", "take away", "used", "used up", "ate", "eaten", "finished",
  "threw away", "throw away", "threw out", "throw out", "binned", "spilled", "spilt", "delete", "subtract"];
const GONE = /^(?:(?:are|were|is|am|im) )?(?:out of|ran out of|run out of|no more|finished all(?: of)? the|finished the|used up all(?: of)? the|used all(?: of)? the|used up the|have no|dont have any)\b/;

const DEST = new Set(["to", "into", "in", "the", "my", "our", "kitchen", "fridge", "freezer", "pantry",
  "cupboard", "stock", "inventory", "some", "more", "another", "of", "please", "now", "for", "me",
  "thanks", "back", "away", "up", "out", "all", "it", "them", "just", "today"]);

function startsWith(t, phrases) {
  for (const p of phrases) if (t === p || t.startsWith(p + " ")) return p;
  return null;
}

function stripLeads(t) {
  const w = t.split(" ");
  while (w.length > 1 && LEADS.has(w[0])) w.shift();
  return w.join(" ");
}

const QUESTION = /^(what|whats|which|how|do|does|did|have|has|is|are|any|got any|tell|should|shall|can|could|would|when|where|why)\b/;
const SOFT_LEADS = /^((hey|chef|ok|okay|so|and|also|please|right|um|uh) )+/;

export function parseStock(text, stock) {
  const raw = String(text || "");
  const t = clean(raw);
  if (!t || t.split(" ").length > 16) return null;
  // Other screens' business: the shopping list, recipes, timers.
  if (/\b(shopping|list|recipe|recipes|timer|reminder|basket|cart)\b/.test(t)) return null;

  // "I have 6 eggs" is news, "have we got eggs" is a question: decided with
  // only the hellos stripped, before "I"/"we" go too.
  const soft = t.replace(SOFT_LEADS, "");
  const asked = raw.includes("?") || QUESTION.test(soft);
  const polite = /^(can|could|would|will) you\b/.test(soft);

  if (!asked || polite) {
    const change = parseChange(stripLeads(t), stock);
    if (change) return change;
  }
  return asked && !polite ? parseQuestion(soft, stock) : null;
}

function parseChange(body, stock) {
  // "we're out of eggs", "finished the milk"
  const gone = body.match(GONE);
  if (gone) {
    const want = body.slice(gone[0].length).split(" ").filter(w => w && !DEST.has(w)).map(sing);
    if (!want.length) return null;
    const hit = resolve(want, stock);
    if (!hit) return null;
    if (hit.ask) return { say: `Which one — ${listWords(hit.ask.map(x => x.toLowerCase()))}?` };
    const name = (ref(hit.id).name || hit.id).toLowerCase();
    if (!(stock || []).some(s => s.id === hit.id && s.qty > 0)) return { say: `There's no ${name} in the kitchen already.` };
    return { action: { kind: "set_stock", id: hit.id, qty: 0 }, say: `Okay, ${name} is off the list of what's in the kitchen.` };
  }

  const verb = startsWith(body, ADD) ? "add" : startsWith(body, TAKE) ? "take" : null;
  if (!verb) return null;
  const said = startsWith(body, verb === "add" ? ADD : TAKE);
  const words = body.slice(said.length).trim().split(" ").filter(Boolean).map(sing);
  const amount = findAmount(words);
  const rest = amount ? [...words.slice(0, amount.from), ...words.slice(amount.to)] : words;
  const want = rest.filter(w => !DEST.has(w));
  if (!want.length) return null;

  const hit = resolve(want, stock);
  if (!hit) return null;                          // not something we know — the chef can create it
  if (hit.ask) return { say: `Which one — ${listWords(hit.ask.map(x => x.toLowerCase()))}?` };

  const info = ref(hit.id);
  const name = (info.name || hit.id).toLowerCase();
  const canon = info.unit || "g";
  const held = (stock || []).find(s => s.id === hit.id && s.qty > 0);
  const unitWord = { g: "grams", ml: "millilitres", cl: "centilitres", l: "litres", u: "" }[canon];

  if (!amount) {
    if (verb === "take" && held) {
      return { action: { kind: "set_stock", id: hit.id, qty: 0 }, say: `Okay, ${name} is off the list of what's in the kitchen.` };
    }
    return { say: canon === "u" ? `How many ${name}?` : `How much ${name} — in ${unitWord}?` };
  }
  const qty = toCanonical(amount, info);
  if (qty === null || !(qty > 0)) {
    return { say: canon === "u" ? `How many ${name} is that?` : `How much ${name} is that, in ${unitWord}?` };
  }

  if (verb === "add") {
    const total = (held ? held.qty : 0) + qty;
    const now = held ? ` You've got ${spoken(total, canon, info.name || hit.id)} now.` : "";
    return {
      action: { kind: "add_stock", id: hit.id, qty, unit: canon },
      say: `Added ${spoken(qty, canon, info.name || hit.id)}.${now}`,
    };
  }

  if (!held) return { say: `There's no ${name} in the kitchen to take out.` };
  const left = round(Math.max(0, held.qty - qty), 3);
  return {
    action: { kind: "adjust_stock", id: hit.id, delta: -Math.min(qty, held.qty) },
    say: left > 0 ? `Done — ${spoken(left, canon, info.name || hit.id)} left.` : `That's the last of the ${name}.`,
  };
}

function parseQuestion(t, stock) {
  const words = t.split(" ");
  const howMuch = /\b(how much|how many|what quantity|what amount|quantity of|amount of)\b/.test(t);
  const possession = /\b(have|got|left|there|remaining|any|in (the |my |our )?(kitchen|fridge|freezer|pantry|cupboard|stock))\b/.test(t);
  if (!possession) return null;

  const subject = words.map(sing).filter(w => !SCAFFOLD.has(w) && !UNIT[w] && NUM[w] === undefined && !/^\d/.test(w));
  if (!subject.length) return null;               // "what's in the kitchen" — command.js reads the shelf

  // A kind of food: "what type of meat", "any fish", "how much cheese".
  const kindWord = subject.find(w => KINDS[w]);
  if (kindWord && subject.length <= 2) {
    const items = ofKind(KINDS[kindWord], stock);
    const label = kindWord === "veg" || kindWord === "veggie" ? "veg" : kindWord;
    if (!items.length) return { say: `There's no ${label} in the kitchen right now.` };
    if (howMuch) {
      return { say: `You've got ${listWords(items.map(s => spoken(s.qty, s.unit || ref(s.id).unit || "g", ref(s.id).name || s.id)))}.` };
    }
    return { say: `You've got ${listWords(items.map(s => (ref(s.id).name || s.id).toLowerCase()))}.` };
  }

  // One ingredient: "how much chicken", "do we have eggs".
  const inStock = (stock || []).filter(s => s.qty > 0 && matches(s.id, subject));
  if (inStock.length) {
    if (howMuch) {
      return { say: `You've got ${listWords(inStock.map(s => spoken(s.qty, s.unit || ref(s.id).unit || "g", ref(s.id).name || s.id)))}.` };
    }
    return { say: `Yes — you've got ${listWords(inStock.map(s => (ref(s.id).name || s.id).toLowerCase()))}.` };
  }
  // Not on the shelves. Only answer "no" when every word they said names a
  // food we know — "do we have enough rice for 4" is a question for the chef,
  // not "no, there's no enough rice".
  if (!Object.keys(INGREDIENTS).some(id => matches(id, subject))) return null;
  const said = subject.join(" ");
  return { say: howMuch ? `You haven't got any ${said}.` : `No, there's no ${said} in the kitchen.` };
}
