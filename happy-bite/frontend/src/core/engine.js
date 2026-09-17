/* Happy Bite — domain engine. No DOM, no storage, no network. Runs under Node
   (`npm test`). Everything the kitchen needs to decide tonight lives here,
   which is why it keeps working with the server switched off. */

import { INGREDIENTS, RECIPES } from "../data/index.js";

export const DAY = 86400000;
export const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
export const ref = (id) => INGREDIENTS[id] || {};
export const isoDay = (d = new Date()) => {
  const x = new Date(d); x.setMinutes(x.getMinutes() - x.getTimezoneOffset());
  return x.toISOString().slice(0, 10);
};

/* ---------- Expiry ----------
   Two ways to know when something goes off. If the household (or KitchenBuddy,
   reading a use-by date aloud) set an explicit `expires` day, that wins. Failing
   that, an estimate from purchase date plus a shelf life — wrong in detail,
   right enough to decide what gets used first. */

export function daysLeft(item) {
  if (item.expires) {
    return Math.round((new Date(item.expires).getTime() - today().getTime()) / DAY);
  }
  const r = ref(item.id);
  if (!r.shelfLife) return 999;
  const end = new Date(item.bought).getTime() + r.shelfLife * DAY;
  return Math.round((end - today().getTime()) / DAY);
}

export const isUrgent = (item) => daysLeft(item) <= 3;

export function expiryLabel(d) {
  if (d < 0) return "Out of date";
  if (d === 0) return "Today";
  if (d === 1) return "Tomorrow";
  if (d <= 30) return d + " days";
  return "";
}

/* Items going off per day for the next `span` days. Day 0 also carries
   anything already past its date — that's the pile to deal with first. */
export function expiryHistogram(stock, span = 14) {
  const bins = Array.from({ length: span }, () => []);
  for (const a of stock) {
    const d = daysLeft(a);
    if (d < span) bins[Math.max(0, d)].push(a.id);
  }
  return bins;
}

/* ---------- Units ----------
   `perPiece` bridges "three courgettes" and "600 g". Quantity is held in
   the reference unit; only the display converts, so toggling never drifts.

   WHAT WAS WRONG, AND WHY THE KITCHEN KEPT GOING RED
   --------------------------------------------------
   The old `convert` handled exactly one pair of units — g <-> u, and only for
   an ingredient with a `perPiece` — and for every other mismatch it returned
   the number UNCHANGED. Not "I can't tell": unchanged, as a plain number, which
   every caller then compared against a quantity in a different unit.

   So a litre of milk in the fridge, held as 1 (unit "l"), was compared against
   a recipe wanting 200 (unit "ml") and came back short. Two onions, held as 2
   (unit "u") against a need for 150 g, came back short. Both showed RED — "you
   haven't got this" — for something sitting on the shelf. This is the whole of
   the "we have it and it shows in red" report, and it never involved the
   server at all.

   Now: real conversions inside mass and inside volume, `perPiece` to cross
   between pieces and mass, and NaN — honestly, loudly — when two units cannot
   be compared. NaN is deliberate: `NaN >= want` is false and `NaN < want` is
   ALSO false, so a caller that forgets to check can't accidentally call it
   short. `known()` below is the explicit check. */

const MASS = { mg: 0.001, g: 1, kg: 1000 };
const VOL = { ml: 1, cl: 10, dl: 100, l: 1000 };

export const countable = (r) => Boolean(r && r.perPiece);
export const known = (n) => typeof n === "number" && Number.isFinite(n);

export function convert(qty, from, to, r) {
  const n = Number(qty);
  if (!Number.isFinite(n)) return NaN;
  if (!from || !to || from === to) return n;
  if (MASS[from] && MASS[to]) return n * MASS[from] / MASS[to];
  if (VOL[from] && VOL[to]) return n * VOL[from] / VOL[to];
  // Water's density, used on purpose and only between ml and g. A kitchen
  // holding stock in ml against a recipe written in g is the common case and
  // the error is smaller than the error in "you don't have any".
  if (VOL[from] && MASS[to]) return n * VOL[from] / MASS[to];
  if (MASS[from] && VOL[to]) return n * MASS[from] / VOL[to];
  if (r && r.perPiece) {
    if (from === "u" && MASS[to]) return n * r.perPiece / MASS[to];
    if (MASS[from] && to === "u") return Math.max(1, Math.round(n * MASS[from] / r.perPiece));
    if (from === "u" && VOL[to]) return n * r.perPiece / VOL[to];
    if (VOL[from] && to === "u") return Math.max(1, Math.round(n * VOL[from] / r.perPiece));
  }
  return NaN;                      // two units with nothing between them
}

/* How much of `need` this kitchen holds, in the unit the recipe asks for.
   `sure` is false when the units don't meet — the item IS there, we simply
   can't put a number on it, and "there, amount unknown" must never be drawn
   the same as "not there". */
export function held(item, r, unit) {
  if (!item) return { qty: 0, sure: true, present: false };
  const got = convert(item.qty, item.unit || r.unit || unit, unit || r.unit, r);
  return known(got)
    ? { qty: got, sure: true, present: true }
    : { qty: 0, sure: false, present: true };
}

export function step(unit, qty) {
  if (unit === "u") return 1;
  if (unit === "cl") return 10;
  if (unit === "l") return 0.25;
  if (unit === "ml") return 50;
  return qty >= 1500 ? 250 : 50;
}

export function formatQty(qty, unit) {
  const n = (x) => String(Math.round(x * 100) / 100).replace(".", ",");
  if (!known(qty)) return "some";
  if (unit === "u") return n(qty);
  if (unit === "g" && qty >= 1000) return n(qty / 1000) + " kg";
  if (unit === "ml" && qty >= 1000) return n(qty / 1000) + " l";
  if (unit === "l") return n(qty) + " l";
  return n(qty) + " " + unit;
}

/* Scaled to the table, rounded to something a person recognises.
   `base` guards a recipe saved before `serves` was always written: dividing by
   0 or undefined made every quantity NaN, and NaN quantities are another way
   an ingredient you own reads as missing. */
export function scale(qty, serves, base, unit) {
  const from = Number(base) > 0 ? Number(base) : 4;
  const to = Number(serves) > 0 ? Number(serves) : from;
  const raw = Number(qty) * (to / from);
  if (!Number.isFinite(raw)) return 0;
  if (unit === "u") return Math.max(1, Math.round(raw));
  // Litres are small numbers: rounding 0.7 l to the nearest half made it 0.5.
  if (unit === "l") return Math.round(raw * 20) / 20;
  if (raw >= 100) return Math.round(raw / 10) * 10;
  if (raw >= 10) return Math.round(raw);
  return Math.round(raw * 2) / 2;
}

/* ---------- Taste, learned from reviews ---------- */

export const EMPTY_TASTE = { liked: {}, verdicts: {}, tweaks: {} };

export function applyReview(taste, review) {
  const t = { liked: { ...taste.liked }, verdicts: { ...taste.verdicts }, tweaks: { ...taste.tweaks } };
  for (const id of review.liked || []) t.liked[id] = (t.liked[id] || 0) + 1;
  for (const id of review.disliked || []) t.liked[id] = (t.liked[id] || 0) - 1;
  t.verdicts[review.recipe] = review.verdict;
  if (review.tweaks && review.tweaks.length) t.tweaks[review.recipe] = review.tweaks;
  return t;
}

export function tasteBonus(recipe, taste) {
  const verdict = taste.verdicts[recipe.id];
  if (verdict === "no") return -1.0;                /* buried, not banned */
  let bonus = verdict === "love" ? 0.25 : 0;
  for (const n of recipe.needs) {
    const s = taste.liked[n.id] || 0;
    bonus += Math.max(-0.12, Math.min(0.12, s * 0.04));
  }
  return bonus;
}

/* ---------- Difficulty ----------
   Half a star to five. It replaced "Easy / Some work / Involved": three words
   that put porridge and a Sunday braise one notch apart.

   A recipe with a method is rated from it, not from the number it was saved
   with (which was the model's guess): how many ingredients, how many go in at
   the busiest step, how many things to wash, and how long it takes. An egg
   fried in one pan is ½, a plain omelette 1, a stir-fry with rice 2½, a
   lasagne 4. Mirrors stars_from_method in backend/app/recipes.py — change both
   together.

   `complexity` (1–3) is kept underneath because the filters, the saved book
   and the server's "understand" step all speak it. A recipe with stars has its
   complexity read off them; an old recipe without stars gets them from its
   complexity. */

export const STAR_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];

const WASH = [
  /\b(frying pan|skillet|griddle)\b|(?<!sauce)\bpan\b/i,
  /\b(saucepan|pot|casserole|dutch oven|stockpot)\b/i,
  /\bwok\b/i,
  /(?<!serving )(?<!warm )\bbowl\b/i,
  /\b(baking|roasting|oven|ovenproof|gratin) (dish|tray|tin|sheet|pan)\b|\bbaking paper\b|\b(loaf|cake|pie|muffin) tin\b/i,
  /\b(chop|dice|slice|mince|cube|halve|quarter|shred|julienne|peel|trim|cut)\w*/i,
  /\b(grate|grated|grater|zest)\b/i,
  /\b(drain|colander|sieve|sift|strain)\w*/i,
  /\b(blend|blender|food processor|whizz|puree|purée)\w*/i,
  /\b(mixer|stand mixer|electric whisk)\b/i,
  /\b(rolling pin|roll out|roll it out)\b/i,
  /\bsteamer\b/i,
];
const ANOTHER = /\b(another|second|separate|clean|large|small) (frying pan|pan|saucepan|pot|bowl)\b/gi;
const clamp01 = x => Math.min(1, Math.max(0, x));

export function starsFromMethod(recipe) {
  const needs = recipe.needs || [], seasoning = recipe.seasoning || [], steps = recipe.steps || [];
  const salt = new Set(seasoning.map(x => x.id));
  const n = needs.length + (recipe.extras || []).length + 0.5 * seasoning.length;
  const busiest = Math.max(1, ...steps.map(st => (st.uses || []).reduce((t, u) => t + (salt.has(u) ? 0.5 : 1), 0)));
  const text = steps.map(st => `${st.do || ""} ${st.heat || ""}`).join(" ");
  const wash = WASH.filter(re => re.test(text)).length + (text.match(ANOTHER) || []).length;
  const minutes = Number(recipe.minutes) || steps.reduce((t, st) => t + (Number(st.minutes) || 0), 0) || 15;
  const score = 0.30 * clamp01((n - 1) / 14)
              + 0.20 * clamp01((busiest - 1) / 5)
              + 0.25 * clamp01((Math.max(1, wash) - 1) / 6)
              + 0.25 * clamp01(Math.log(Math.max(minutes, 5) / 5) / Math.log(36));
  return Math.min(5, Math.max(0.5, Math.round((0.5 + 4.5 * score) * 2) / 2));
}

export function starsOf(recipe) {
  if ((recipe?.steps || []).length >= 2) return starsFromMethod(recipe);
  const s = Number(recipe?.stars);
  if (Number.isFinite(s) && s > 0) return Math.min(5, Math.max(0.5, Math.round(s * 2) / 2));
  return { 1: 1, 2: 2.5, 3: 4 }[recipe?.complexity] || 2;
}

export function complexityOf(recipe) {
  if (!recipe?.stars && !(recipe?.steps || []).length && [1, 2, 3].includes(recipe?.complexity)) return recipe.complexity;
  const s = starsOf(recipe);
  return s <= 1.5 ? 1 : s <= 3 ? 2 : 3;
}

/* The most stars each complexity filter lets through. */
export const STARS_FOR_COMPLEXITY = { 1: 1.5, 2: 3, 3: 5 };

/* ---------- Time ----------
   The minutes a step's own words give it, and a recipe's honest total. The
   same rules as the server's recipes.text_minutes, for recipes saved before it
   existed: "Roast for 12 minutes on the first side" with a `minutes` of 2 was
   shown as "watch closely", had no timer, and made a 40-minute tray bake read
   15 min. */

const NUMBER_WORDS = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, "forty-five": 45, forty: 40, sixty: 60 };
const TIME_IN_TEXT = /\b(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty-five|forty|sixty)(?:\s*(?:-|to|or)\s*(\d+|[a-z]+))?\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b(\s+(?:per|a|on each|each)\s+side)?/gi;
const numberOf = (w) => /^\d+(\.\d+)?$/.test(w) ? Number(w) : NUMBER_WORDS[String(w).toLowerCase()];

export function textMinutes(text) {
  let total = 0;
  for (const [, low, high, unit, perSide] of String(text || "").matchAll(TIME_IN_TEXT)) {
    const n = numberOf(high || low);
    if (n === undefined) continue;
    const u = unit.toLowerCase();
    total += (u.startsWith("h") ? n * 60 : u.startsWith("s") ? n / 60 : n) * (perSide ? 2 : 1);
  }
  const second = String(text || "").match(/minutes? on the first side,?\s+(?:and\s+)?(\w+)\s+on the (?:second|other)/i);
  if (second) total += numberOf(second[1]) || 0;
  return total;
}

export const stepMinutes = (step) => Math.round(Math.max(Number(step?.minutes) || 0, textMinutes(step?.do)));

const OVEN_STEP = /\b(roast|bake|baking|oven)/i;
const OVEN_ON = /\b(preheat|turn the oven on|heat the oven|oven on to|switch the oven on)/i;

export function minutesOf(recipe) {
  const steps = recipe?.steps || [];
  let summed = steps.reduce((t, s) => t + stepMinutes(s), 0);
  if (summed && steps.some(s => OVEN_STEP.test(s.do || "") || /oven/i.test(s.heat || "")) && !steps.some(s => OVEN_ON.test(s.do || ""))) summed += 10;
  const stated = Number(recipe?.minutes) || 0;
  if (!summed) return stated;
  return stated >= summed && stated <= summed + 15 ? stated : summed;
}

/* ---------- Portions ----------
   The server's portion rule (backend recipes.PORTION_G), for recipes saved
   before it existed: 800 g of chicken and 800 g of potatoes "for 2". Per
   person, by shelf: the most that is still a plate of food, and the ordinary
   portion an amount over it comes down to. */

const PORTION_G = {
  meat: [250, 150], seafood: [250, 150], produce: [250, 175], frozen: [250, 150],
  pantry: [150, 90], bakery: [200, 100], dairy: [250, 100],
};

export function sanePortions(recipe) {
  const heads = Math.max(1, Number(recipe?.serves) || 4);
  const adjusted = [];
  const needs = (recipe?.needs || []).map(n => {
    const r = ref(n.id);
    const limit = r.unit === "g" && !/\b(oil|vinegar)\b/i.test(r.name || "") && PORTION_G[r.category];
    if (!limit || !(n.qty > limit[0] * heads)) return n;
    const qty = limit[1] * heads;
    adjusted.push(`${r.name}: ${n.qty} -> ${qty} g`);
    return { ...n, qty, flexible: true };
  });
  if (!adjusted.length) return recipe;
  return { ...recipe, needs, adjusted: [...(recipe.adjusted || []), ...adjusted] };
}

/* ---------- What you've cooked ----------
   Every finished cook is one entry: { date, recipe, at, name, serves, stars,
   verdict }. Entries from before the name was kept have only { date, recipe },
   so everything else is looked up in the book, and a recipe since thrown away
   still shows as something you cooked rather than vanishing. */

export function cookedHistory(history, book) {
  const byId = new Map((book || []).map(r => [r.id, r]));
  return [...(history || [])]
    .map((h, i) => ({ ...h, order: h.at || i, recipeNow: byId.get(h.recipe) || null }))
    .sort((a, b) => (b.date || "").localeCompare(a.date || "") || b.order - a.order)
    .map(h => ({
      ...h,
      name: h.name || h.recipeNow?.name || "A recipe no longer in your book",
      stars: h.stars ?? (h.recipeNow ? starsOf(h.recipeNow) : null),
    }));
}

export function historyStats(history, now = new Date()) {
  const entries = history || [];
  const day = (offset) => isoDay(new Date(now.getTime() - offset * DAY));
  const days = new Set(entries.map(h => h.date));
  const weekStart = day(6), monthStart = isoDay(now).slice(0, 7);
  // Days in a row with something cooked, ending today — or yesterday, so an
  // evening that hasn't cooked yet doesn't break it.
  let streak = 0;
  for (let i = days.has(day(0)) ? 0 : 1; days.has(day(i)); i++) streak++;
  const counts = new Map();
  for (const h of entries) counts.set(h.recipe, (counts.get(h.recipe) || 0) + 1);
  const [favId, favCount] = [...counts].sort((a, b) => b[1] - a[1])[0] || [null, 0];
  return {
    total: entries.length,
    thisWeek: entries.filter(h => h.date >= weekStart).length,
    thisMonth: entries.filter(h => (h.date || "").startsWith(monthStart)).length,
    streak,
    favourite: favCount > 1 ? { recipe: favId, count: favCount,
                                name: [...entries].reverse().find(h => h.recipe === favId && h.name)?.name || null } : null,
  };
}

/* ---------- Filters and scoring ---------- */

export function exclusionsFor(diners, people) {
  return new Set(people.filter(p => diners.includes(p.id)).flatMap(p => p.avoids));
}

export function passesFilters(recipe, f) {
  if (f.mealType && !recipe.types.includes(f.mealType)) return false;
  if (f.maxMinutes && minutesOf(recipe) > f.maxMinutes) return false;
  if (f.maxComplexity && complexityOf(recipe) > f.maxComplexity) return false;
  if (f.cuisine && recipe.cuisine !== f.cuisine) return false;
  if (f.mustUse && !recipe.needs.some(n => n.id === f.mustUse)) return false;
  return true;
}

/* The score is four numbers added up. `parts` keeps them apart so the
   interface can show exactly how a dish was picked — a ranking nobody can
   inspect is one nobody trusts. */
export const SCORE_PARTS = {
  coverage: { label: "Already in the kitchen", min: 0, max: 1 },
  urgency:  { label: "Uses what goes off first", min: 0, max: 0.36 },
  speed:    { label: "Quick to make", min: 0, max: 0.10 },
  taste:    { label: "What you said last time", min: -1, max: 0.61 }
};

export function assess(recipe, stock, excluded, taste, serves) {
  for (const n of recipe.needs) if (excluded.has(n.id)) return null;
  for (const s of recipe.seasoning || []) if (excluded.has(s.id)) return null;

  const stockMap = new Map(stock.map(a => [a.id, a]));
  let required = 0, met = 0, inStock = 0;
  const missing = [], urgent = [], missingSeasoning = [], unsure = [];

  for (const n of recipe.needs) {
    const r = ref(n.id);
    const item = stockMap.get(n.id);
    const want = scale(n.qty, serves, recipe.serves, r.unit);
    const have = held(item, r, r.unit);
    const weight = n.flexible ? 0.2 : 1;
    required += weight;

    /* Three outcomes, not two. The middle one is new and is the fix:
       the item is on the shelf but its unit and the recipe's unit have
       nothing between them, so we know they HAVE it and not how much. That
       is not a reason to send them shopping. */
    if (have.present && !have.sure) {
      met += weight; inStock += 1;
      unsure.push(n.id);
      if (item && isUrgent(item)) urgent.push(n.id);
    } else if (have.qty >= want || want <= 0) {
      met += weight; inStock += 1;
      if (item && isUrgent(item)) urgent.push(n.id);
    } else if (!n.flexible) {
      missing.push({ id: n.id, qty: want - have.qty });
    }
  }

  for (const s of recipe.seasoning || []) {
    if (stockMap.has(s.id)) { inStock += 1; continue; }
    if (s.essential) missing.push({ id: s.id, qty: s.qty });
    else missingSeasoning.push(s.id);
  }

  /* ---------- The little ring, and why it said 1/1 ----------
     `total` used to be `recipe.needs.length`, which is not the number of
     ingredients in the recipe — it is the number of ingredients that happened
     to match a catalogue id. An assistant-written dish puts the rest in
     `seasoning` and in `extras` (the ones with no id at all, written out as a
     shopper would), and the recipe sheet prints all three. So a five-line
     recipe with one matched id drew "1/1" beside a list of five things, which
     reads as a bug because it is one.

     Now the ring counts every line the sheet shows, and "have" is everything
     that is NOT standing between you and cooking it — so a dish marked "Ready
     to cook" always draws a full ring, and one short of two things draws
     4 of 6. The two numbers can no longer disagree with the label beside them. */
  const listed = recipe.needs.length
               + (recipe.seasoning || []).length
               + (recipe.extras || []).length;
  const total = Math.max(listed, 1);

  const parts = {
    coverage: required ? met / required : 0,
    urgency: Math.min(urgent.length, 3) * 0.12,
    speed: Math.max(0, 60 - recipe.minutes) / 60 * 0.10,
    taste: tasteBonus(recipe, taste)
  };
  const score = parts.coverage + parts.urgency + parts.speed + parts.taste;

  return {
    recipe, score, parts, coverage: parts.coverage, missing, urgent, missingSeasoning,
    unsure,                                   // present, amount not comparable
    inStock,                                  // actually counted on the shelf
    have: Math.max(0, total - missing.length),
    total,
    cookable: missing.length === 0
  };
}

/* One proposal, two alternates, never more. Cookable only by default;
   `allowIncomplete` is the labelled second pass for an otherwise empty screen. */
export function propose(ctx) {
  const excluded = exclusionsFor(ctx.diners, ctx.people);
  const book = ctx.recipes || RECIPES;
  const pool = book.filter(r => passesFilters(r, ctx.filters || {}));
  const rated = pool
    .map(r => assess(r, ctx.stock, excluded, ctx.taste, ctx.serves))
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);
  const ready = rated.filter(r => r.cookable);
  const use = ctx.allowIncomplete ? rated : ready;
  return {
    main: use[0] || null, alternates: use.slice(1, 3),
    considered: pool.length, cookableCount: ready.length,
    nearMisses: rated.filter(r => !r.cookable).length
  };
}

/* Three dishes to choose between, the way a game offers three upgrades.

   The first draw is the best three by score — the proposal and its two
   alternates. A reroll draws three at random from everything cookable,
   leaving out the ones just shown so it never deals the same hand twice;
   `random` is injectable so the tests can pin it. Fewer than three cookable?
   The near misses fill the gaps, flagged by `cookable: false`. */
export function drawChoices(ctx, { reroll = false, avoid = [], random = Math.random } = {}) {
  const excluded = exclusionsFor(ctx.diners, ctx.people);
  const book = ctx.recipes || RECIPES;
  const rated = book
    .filter(r => passesFilters(r, ctx.filters || {}))
    .map(r => assess(r, ctx.stock, excluded, ctx.taste, ctx.serves))
    .filter(Boolean)
    .sort((a, b) => (b.cookable - a.cookable) || (b.score - a.score));
  if (!reroll) return rated.slice(0, 3);

  const fresh = rated.filter(n => !avoid.includes(n.recipe.id));
  const pool = fresh.length >= 3 ? fresh : [...fresh, ...rated.filter(n => avoid.includes(n.recipe.id))];
  const ready = pool.filter(n => n.cookable);
  const deck = ready.length >= 3 ? ready : pool;
  const hand = [];
  const left = [...deck];
  while (hand.length < 3 && left.length) hand.push(left.splice(Math.floor(random() * left.length), 1)[0]);
  if (hand.length < 3) hand.push(...pool.filter(n => !hand.includes(n)).slice(0, 3 - hand.length));
  return hand;
}

/* Ignores score, urgency and taste. Keeps exclusions: a dish somebody at
   the table won't eat is a failed dinner, not a spontaneous one. */
export function surprise(ctx, avoid) {
  const excluded = exclusionsFor(ctx.diners, ctx.people);
  const book = ctx.recipes || RECIPES;
  const scored = book
    .filter(r => passesFilters(r, ctx.filters || {}))
    .map(r => assess(r, ctx.stock, excluded, ctx.taste, ctx.serves))
    .filter(Boolean);
  let candidates = scored.filter(n => n.cookable).map(n => n.recipe);
  if (!candidates.length) candidates = scored.map(n => n.recipe);
  if (!candidates.length) return null;
  if (candidates.length > 1 && avoid) {
    const others = candidates.filter(r => r.id !== avoid);
    if (others.length) candidates = others;
  }
  const pick = candidates[Math.floor(Math.random() * candidates.length)];
  return assess(pick, ctx.stock, excluded, ctx.taste, ctx.serves);
}

/* ---------- Shopping ---------- */

export function shoppingList(plannedIds, stock, serves, extras = [], book = RECIPES) {
  const stockMap = new Map(stock.map(a => [a.id, a]));
  const want = new Map();
  for (const id of plannedIds) {
    const r = book.find(x => x.id === id);
    if (!r) continue;
    for (const n of r.needs) {
      if (n.flexible) continue;
      const u = ref(n.id).unit;
      want.set(n.id, (want.get(n.id) || 0) + scale(n.qty, serves, r.serves, u));
    }
    for (const s of r.seasoning || []) {
      if (!s.essential) continue;
      want.set(s.id, Math.max(want.get(s.id) || 0, s.qty));
    }
  }
  for (const id of extras) if (!want.has(id)) want.set(id, ref(id).unit === "u" ? 1 : 50);

  const out = [];
  for (const [id, qty] of want) {
    const r = ref(id);
    if (!r.name) continue;
    const have = held(stockMap.get(id), r, r.unit);
    // Same rule as the kitchen: if it is on the shelf and we can't compare the
    // amount, don't put it on the list. Buying a second jar of something you
    // already have is the cheaper mistake, but it is still the wrong answer.
    if (have.present && !have.sure) continue;
    const short = qty - have.qty;
    if (short > 0) out.push({ id, qty: short, unit: r.unit, category: r.category });
  }
  return out.sort((a, b) =>
    a.category.localeCompare(b.category) || ref(a.id).name.localeCompare(ref(b.id).name));
}

/* ---------- Receipt ---------- */

export const THRESHOLD = 0.75;

export function sortLines(lines) {
  return {
    confirmed: lines.filter(l => l.id && !l.rejected && l.confidence >= THRESHOLD),
    unsure:    lines.filter(l => !l.rejected && (!l.id || l.confidence < THRESHOLD)),
    rejected:  lines.filter(l => l.rejected)
  };
}

export function matchRate(lines) {
  const scored = lines.filter(l => !l.rejected);
  if (!scored.length) return 0;
  return scored.filter(l => l.id && l.confidence >= THRESHOLD).length / scored.length;
}

/* ---------- Seasoning ideas: only from the dish on screen ---------- */

export function seasoningIdeas(recipe, stock) {
  const inKitchen = new Set(stock.map(a => a.id));
  return (recipe.seasoning || [])
    .filter(s => !s.essential && !inKitchen.has(s.id) && ref(s.id).name)
    .map(s => ({ id: s.id, name: ref(s.id).name, origin: ref(s.id).origin }));
}

/* ---------- The kitchen at a glance ----------
   Everything the Today dashboard draws, from data the app already holds.
   Still no health data: a profile is a name and what they won't eat. */

export function kitchenSummary({ stock, book, bought, shopping, history, diners, people, taste }) {
  const days = stock.map(daysLeft);
  const excluded = exclusionsFor(diners, people);
  const serves = diners.length || 1;
  const rated = book.map(r => assess(r, stock, excluded, taste, serves)).filter(Boolean);

  const week = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today().getTime() - i * DAY);
    const key = isoDay(d);
    week.push({ key, date: d, cooked: history.filter(h => h.date === key).length });
  }

  return {
    items: stock.length,
    fresh: days.filter(d => d > 3).length,
    useSoon: days.filter(d => d >= 0 && d <= 3).length,
    gone: days.filter(d => d < 0).length,
    ready: rated.filter(r => r.cookable).length,
    bookSize: rated.length,
    shoppingLeft: shopping.filter(i => !bought.includes(i.id)).length,
    shoppingTotal: shopping.length,
    week,
    cookedThisWeek: week.reduce((n, d) => n + d.cooked, 0),
    soon: expiryHistogram(stock, 7).map(b => b.length)
  };
}

/* ---------- Odds and ends ---------- */

const TINTS = ["#34C99A", "#3AA7E0", "#F0A04B", "#9C7BE0", "#2BB5B0", "#E07BA8"];
export function tintFor(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return TINTS[h % TINTS.length];
}

export const uid = () => "id" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);