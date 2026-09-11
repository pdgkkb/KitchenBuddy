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
   An estimate from purchase date plus a shelf life — not a printed use-by
   date. Wrong in detail, right enough to decide what gets used first. */

export function daysLeft(item) {
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
   the reference unit; only the display converts, so toggling never drifts. */

export const countable = (r) => Boolean(r && r.perPiece);

export function convert(qty, from, to, r) {
  if (from === to || !countable(r)) return qty;
  if (from === "g" && to === "u") return Math.max(1, Math.round(qty / r.perPiece));
  if (from === "u" && to === "g") return Math.round(qty * r.perPiece);
  return qty;
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
  if (unit === "u") return n(qty);
  if (unit === "g" && qty >= 1000) return n(qty / 1000) + " kg";
  if (unit === "ml" && qty >= 1000) return n(qty / 1000) + " l";
  if (unit === "l") return n(qty) + " l";
  return n(qty) + " " + unit;
}

/* Scaled to the table, rounded to something a person recognises. */
export function scale(qty, serves, base, unit) {
  const raw = qty * (serves / base);
  if (unit === "u") return Math.max(1, Math.round(raw));
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

/* ---------- Filters and scoring ---------- */

export function exclusionsFor(diners, people) {
  return new Set(people.filter(p => diners.includes(p.id)).flatMap(p => p.avoids));
}

export function passesFilters(recipe, f) {
  if (f.mealType && !recipe.types.includes(f.mealType)) return false;
  if (f.maxMinutes && recipe.minutes > f.maxMinutes) return false;
  if (f.maxComplexity && recipe.complexity > f.maxComplexity) return false;
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

  const held = new Map(stock.map(a => [a.id, a]));
  let required = 0, met = 0, have = 0;
  const missing = [], urgent = [], missingSeasoning = [];

  for (const n of recipe.needs) {
    const r = ref(n.id);
    const item = held.get(n.id);
    const want = scale(n.qty, serves, recipe.serves, r.unit);
    const got = item ? convert(item.qty, item.unit || r.unit, r.unit, r) : 0;
    const weight = n.flexible ? 0.2 : 1;
    required += weight;
    if (got >= want) {
      met += weight; have += 1;
      if (item && isUrgent(item)) urgent.push(n.id);
    } else if (!n.flexible) {
      missing.push({ id: n.id, qty: want - got });
    }
  }

  for (const s of recipe.seasoning || []) {
    if (held.has(s.id)) continue;
    if (s.essential) missing.push({ id: s.id, qty: s.qty });
    else missingSeasoning.push(s.id);
  }

  const parts = {
    coverage: required ? met / required : 0,
    urgency: Math.min(urgent.length, 3) * 0.12,
    speed: Math.max(0, 60 - recipe.minutes) / 60 * 0.10,
    taste: tasteBonus(recipe, taste)
  };
  const score = parts.coverage + parts.urgency + parts.speed + parts.taste;

  return {
    recipe, score, parts, coverage: parts.coverage, missing, urgent, missingSeasoning,
    have, total: recipe.needs.length, cookable: missing.length === 0
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
  const held = new Map(stock.map(a => [a.id, a]));
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
    const item = held.get(id);
    const have = item ? convert(item.qty, item.unit || r.unit, r.unit, r) : 0;
    const short = qty - have;
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
  const held = new Set(stock.map(a => a.id));
  return (recipe.seasoning || [])
    .filter(s => !s.essential && !held.has(s.id) && ref(s.id).name)
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
