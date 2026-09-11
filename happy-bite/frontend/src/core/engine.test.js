/* `npm test` — the engine under Node, no browser. */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as E from "./engine.js";
import { parse } from "./intent.js";
import { RECIPES, STARTING_STOCK } from "../data/index.js";

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
