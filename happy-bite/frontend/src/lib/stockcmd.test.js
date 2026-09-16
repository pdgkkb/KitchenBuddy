import { test } from "node:test";
import assert from "node:assert/strict";
import { parseStock } from "./stockcmd.js";

const STOCK = [
  { id: "chicken", qty: 450, unit: "g" },
  { id: "c_bacon", qty: 200, unit: "g" },
  { id: "c_chicken_stock", qty: 500, unit: "g" },
  { id: "egg", qty: 6, unit: "u" },
  { id: "milk", qty: 1.5, unit: "l" },
  { id: "cod", qty: 260, unit: "g" },
];

test("adding with an amount changes the stock and gives the new total", () => {
  const r = parseStock("add 200 grams of chicken in the kitchen", STOCK);
  assert.deepEqual(r.action, { kind: "add_stock", id: "chicken", qty: 200, unit: "g" });
  assert.equal(r.say, "Added 200 grams of chicken breast. You've got 650 grams of chicken breast now.");
});

test("amounts in other units are converted to the ingredient's own", () => {
  assert.equal(parseStock("put half a litre of milk in the fridge", STOCK).action.qty, 0.5);
  assert.equal(parseStock("add 1.5kg chicken", STOCK).action.qty, 1500);
  assert.equal(parseStock("I bought a dozen eggs", STOCK).action.qty, 12);
  assert.equal(parseStock("can you add 2 chicken breasts", STOCK).action.qty, 250);   // perPiece 125
});

test("adding without an amount asks for one", () => {
  const r = parseStock("add chicken to the kitchen", STOCK);
  assert.equal(r.action, undefined);
  assert.match(r.say, /How much chicken breast/);
});

test("using some takes it away", () => {
  const r = parseStock("I used 100 ml of milk", STOCK);
  assert.deepEqual(r.action, { kind: "adjust_stock", id: "milk", delta: -0.1 });
  assert.match(r.say, /1\.4 litres of semi-skimmed milk left/);
  assert.deepEqual(parseStock("we're out of eggs", STOCK).action, { kind: "set_stock", id: "egg", qty: 0 });
});

test("what type of meat: names only, no amounts, no chicken stock", () => {
  const r = parseStock("what type of meat do we have", STOCK);
  assert.equal(r.say, "You've got chicken breast and bacon.");
  assert.doesNotMatch(r.say, /\d/);
  assert.equal(parseStock("any fish?", STOCK).say, "You've got cod fillet.");
  assert.equal(parseStock("what kind of cheese have we got", STOCK).say, "There's no cheese in the kitchen right now.");
});

test("do we have X: yes or no, no amount", () => {
  assert.equal(parseStock("do we have eggs", STOCK).say, "Yes — you've got eggs.");
  assert.equal(parseStock("have we got any rice?", STOCK).say, "No, there's no rice in the kitchen.");
});

test("how much / how many: the exact amount", () => {
  assert.equal(parseStock("how much chicken do we have", STOCK).say, "You've got 450 grams of chicken breast.");
  assert.equal(parseStock("how many eggs are left", STOCK).say, "You've got 6 eggs.");
  assert.equal(parseStock("how much meat do we have", STOCK).say,
    "You've got 450 grams of chicken breast and 200 grams of bacon.");
  assert.equal(parseStock("how much milk is there", STOCK).say, "You've got 1.5 litres of semi-skimmed milk.");
});

test("everything else is left for the chef", () => {
  for (const text of [
    "what's in the kitchen",                     // command.js reads the shelf
    "do we have enough rice for 4",
    "should I add the chicken now?",
    "how much chicken do I need for this",
    "add eggs to the shopping list",
    "add a recipe",
    "what can I cook with chicken",
    "I have a question",
    "add 200 g of unicorn",
  ]) assert.equal(parseStock(text, STOCK), null, text);
});
