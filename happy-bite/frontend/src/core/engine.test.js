/* `npm test` — the engine under Node, no browser. */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as E from "./engine.js";
import { parse } from "./intent.js";
import { STARTING_STOCK } from "../data/index.js";
// The app's book starts empty (data/index.js), so the engine is tested against
// a few small recipes made up for the tests (test-recipes.js).
import { RECIPES } from "./test-recipes.js";

const t0 = E.today().getTime();
const stock = STARTING_STOCK.map(a => ({ id: a.id, qty: a.qty, unit: E.ref(a.id).unit,
  bought: new Date(t0 + a.bought * E.DAY).toISOString().slice(0, 10) }));
const people = [{ id: "a", name: "A", avoids: [] }, { id: "b", name: "B", avoids: ["pork", "beef"] }];
const ctx = { stock, people, diners: ["a", "b"], serves: 2, taste: E.EMPTY_TASTE, filters: {}, recipes: RECIPES };

test("proposals are cookable and respect exclusions", () => {
  const p = E.propose(ctx);
  assert.ok(p.main && p.main.cookable);
  for (const a of [p.main, ...p.alternates]) {
    assert.ok(!a.recipe.needs.some(n => ["pork", "beef"].includes(n.id)));
  }
});

test("score parts add up to the score", () => {
  const a = E.propose(ctx).main;
  const sum = Object.values(a.parts).reduce((x, y) => x + y, 0);
  assert.ok(Math.abs(sum - a.score) < 1e-9);
});

test("surprise never draws an excluded dish", () => {
  for (let i = 0; i < 300; i++) {
    const s = E.surprise(ctx);
    assert.ok(!s.recipe.needs.some(n => ["pork", "beef"].includes(n.id)));
  }
});

test("scaling rounds to kitchen numbers", () => {
  assert.deepEqual([1, 3, 6].map(n => E.scale(600, n, 4, "g")), [150, 450, 900]);
  assert.equal(E.scale(2, 3, 4, "u"), 2);
});

test("the dashboard summary counts what's there", () => {
  const s = E.kitchenSummary({ stock, book: RECIPES, bought: [], shopping: [], history: [{ date: E.isoDay() }],
                               diners: ["a"], people, taste: E.EMPTY_TASTE });
  assert.equal(s.items, stock.length);
  assert.equal(s.fresh + s.useSoon + s.gone, stock.length);
  assert.equal(s.week.length, 7);
  assert.equal(s.cookedThisWeek, 1);
});

test("free text says what it understood", () => {
  const r = parse("something quick and vegetarian with courgettes");
  assert.equal(r.filters.maxMinutes, 20);
  assert.equal(r.filters.mustUse, "courgette");
  assert.ok(r.avoid.includes("chicken"));
  assert.ok(parse("make me a sandwich").blank);
});

test("difficulty is half a star to five, and old recipes get stars from their complexity", () => {
  assert.equal(E.starsOf({ stars: 3.5 }), 3.5);
  assert.equal(E.starsOf({ stars: 3.3 }), 3.5);
  assert.equal(E.starsOf({ stars: 9 }), 5);
  assert.equal(E.starsOf({ complexity: 1 }), 1);
  assert.equal(E.starsOf({}), 2);
  assert.equal(E.complexityOf({ stars: 0.5 }), 1);
  assert.equal(E.complexityOf({ stars: 3.5 }), 3);
  assert.equal(E.complexityOf({ complexity: 2 }), 2);
});

test("a recipe with a method is rated from its ingredients, busiest step, washing-up and time", () => {
  const st = (d, minutes, uses = []) => ({ do: d, minutes, uses });
  const egg = { stars: 3, needs: [{ id: "egg" }], seasoning: [], minutes: 5,
    steps: [st("Heat the frying pan and crack in the egg", 4, ["egg"]), st("Slide onto a plate and eat", 1)] };
  const omelette = { needs: [{ id: "egg" }, { id: "butter" }], seasoning: [{ id: "salt" }, { id: "pepper" }], minutes: 8,
    steps: [st("In a bowl, whisk the eggs with the salt and pepper", 2, ["egg", "salt", "pepper"]),
            st("In a frying pan, melt the butter", 1, ["butter"]),
            st("Pour in the eggs and cook until just set", 4), st("Fold and slide onto a plate", 1)] };
  const stirfry = { needs: "chicken pepper onion garlic rice springonion".split(" ").map(id => ({ id })),
    seasoning: "soy oil ginger".split(" ").map(id => ({ id })), minutes: 25,
    steps: [st("In a saucepan, boil the rice", 12, ["rice"]),
            st("Slice the chicken, pepper and onion; chop the garlic", 5),
            st("In a wok, fry the chicken in the oil", 4, ["chicken", "oil"]),
            st("Add the pepper, onion, garlic and ginger and stir-fry", 3, ["pepper", "onion", "garlic", "ginger"]),
            st("Add the soy and spring onion, serve over the rice", 1, ["soy", "springonion"])] };
  assert.equal(E.starsOf(egg), 0.5);          // the method wins over a saved guess
  assert.equal(E.starsOf(omelette), 1);
  assert.equal(E.starsOf(stirfry), 2.5);
});

test("three choices: the best three first, then a reroll that deals a different hand", () => {
  const c = { ...ctx, people: [], diners: [], recipes: RECIPES };
  const first = E.drawChoices(c);
  assert.equal(first.length, 3);
  assert.equal(new Set(first.map(n => n.recipe.id)).size, 3);
  let seed = 0.37;
  const random = () => (seed = (seed * 9301 + 49297) % 233280 / 233280);
  const again = E.drawChoices(c, { reroll: true, avoid: first.map(n => n.recipe.id), random });
  assert.equal(again.length, 3);
  assert.ok(again.every(n => !first.some(f => f.recipe.id === n.recipe.id)));
});

test("a step's own words set its minutes, and the total is honest", () => {
  assert.equal(E.textMinutes("Roast for 12 minutes on the first side"), 12);
  assert.equal(E.textMinutes("four minutes on the first side, three on the second"), 7);
  assert.equal(E.textMinutes("cook 3-4 minutes each side"), 8);
  assert.equal(E.stepMinutes({ do: "Roast for another 12 minutes", minutes: 2 }), 12);
  const trayBake = { minutes: 15, steps: [
    { do: "In a bowl, toss the chicken with the oil", minutes: 2 },
    { do: "Roast for 12 minutes on the first side", minutes: 2 },
    { do: "Flip the chicken", minutes: 1 },
    { do: "Roast for another 12 minutes", minutes: 2 },
  ] };
  assert.equal(E.minutesOf(trayBake), 37);                       // 27 of steps + 10 for an oven nobody turned on
  assert.equal(E.minutesOf({ minutes: 25, steps: [{ do: "Boil", minutes: 18 }, { do: "Toss", minutes: 2 }] }), 25);
});

test("cooking history: newest first, old entries named from the book, and the numbers add up", () => {
  const now = new Date("2026-09-16T19:00:00");
  const d = (n) => E.isoDay(new Date(now.getTime() - n * E.DAY));
  const history = [
    { date: d(20), recipe: "gone" },
    { date: d(2), recipe: "pasta-tomato" },
    { date: d(1), recipe: "fried-rice", at: 1, name: "Vegetable fried rice", serves: 2, stars: 1.5, verdict: "love" },
    { date: d(0), recipe: "fried-rice", at: 2, name: "Vegetable fried rice", serves: 3, stars: 1.5 },
  ];
  const list = E.cookedHistory(history, RECIPES);
  assert.deepEqual(list.map(h => h.date), [d(0), d(1), d(2), d(20)]);
  assert.equal(list[2].name, "Tomato and parmesan pasta");
  assert.equal(list[2].stars, 1.5);          // read off its method: six ingredients, 25 minutes
  assert.equal(list[3].name, "A recipe no longer in your book");
  const s = E.historyStats(history, now);
  assert.equal(s.total, 4);
  assert.equal(s.thisWeek, 3);
  assert.equal(s.streak, 3);
  assert.deepEqual(s.favourite, { recipe: "fried-rice", count: 2, name: "Vegetable fried rice" });
});

test("portions for two: 800 g of chicken comes down to an ordinary plate, sane amounts are left alone", () => {
  const r = { serves: 2, needs: [{ id: "chicken", qty: 800 }, { id: "potato", qty: 350 }, { id: "oliveoil", qty: 4 }] };
  const fixed = E.sanePortions(r);
  assert.deepEqual(fixed.needs.map(n => n.qty), [300, 350, 4]);
  assert.equal(fixed.adjusted.length, 1);
  assert.equal(E.sanePortions(fixed), fixed);                 // nothing left to fix the second time
  for (const seed of RECIPES) assert.equal(E.sanePortions(seed), seed, seed.id);
});
