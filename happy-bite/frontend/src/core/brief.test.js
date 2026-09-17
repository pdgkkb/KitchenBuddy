import { test } from "node:test";
import assert from "node:assert/strict";
import { stepBrief, stepHeat, vesselToGrab, stepIngredients } from "./brief.js";

/* The skewers that came out of the assistant: marinate before the marinade,
   a heat on mixing, and a step that only says "the chicken cubes". */
const skewers = {
  serves: 2,
  needs: [
    { id: "chicken", qty: 400, prep: "cubed" },
    { id: "oliveoil", qty: 2, flexible: true },
    { id: "garlic", qty: 2 },
  ],
  seasoning: [{ id: "salt", qty: 2 }, { id: "oregano", qty: 1 }],
  steps: [
    { do: "Marinate the chicken cubes", minutes: 10, heat: "Medium", uses: ["chicken"] },
    { do: "Mix the marinade ingredients", minutes: 2, heat: "Low", uses: ["oliveoil", "garlic", "salt", "oregano"] },
    { do: "Thread the chicken cubes onto skewers", minutes: 2, heat: "Low", uses: ["chicken"] },
    { do: "Grill the skewers, turning once", minutes: 6, heat: "Medium-high" },
  ],
};

test("a spoken step says what to grab and what goes in, with amounts for the table", () => {
  assert.equal(stepBrief(skewers, 0, 2),
    "Step 1. Grab a bowl. Marinate the chicken cubes. You'll need 400 grams of chicken breast. Tell me when that's done.");
  assert.equal(stepBrief(skewers, 1, 2),
    "Step 2. Mix the marinade ingredients. You'll need a splash of olive oil and 2 cloves of garlic, plus salt and oregano. Tell me when that's done.");
});

test("amounts are scaled to the people eating", () => {
  assert.match(stepBrief(skewers, 0, 4), /800 grams of chicken breast/);
});

test("an ingredient already used isn't read out again", () => {
  assert.deepEqual(stepIngredients(skewers, 2), []);
});

test("no heat on a step that never touches the hob", () => {
  assert.equal(stepHeat(skewers.steps[1]), "");
  assert.equal(stepHeat(skewers.steps[3]), "Medium-high");
  assert.doesNotMatch(stepBrief(skewers, 1, 2), /heat/);
});

test("grab a new container only when the step needs a different one", () => {
  assert.equal(vesselToGrab(skewers, 0), "a bowl");
  assert.equal(vesselToGrab(skewers, 1), "");                 // still the bowl
  const eggs = { steps: [{ do: "Whisk the eggs" }, { do: "Pour the whisked eggs into the pan and scramble" }, { do: "Fry the rice" }] };
  assert.equal(vesselToGrab(eggs, 1), "");                    // the step names the pan itself
  assert.equal(vesselToGrab(eggs, 2), "");                    // already at the pan
});

test("without `uses`, the ingredients a step names are the ones read out", () => {
  const r = { ...skewers, steps: [{ do: "Toss the chicken with the garlic and olive oil" }] };
  assert.deepEqual(stepIngredients(r, 0).map(x => x.id).sort(), ["chicken", "garlic", "oliveoil"]);
});

test("the last step closes", () => {
  assert.match(stepBrief(skewers, 3, 2), /^Last step\. On medium-high heat, grill the skewers, turning once\. .*And that's it — enjoy\.$/);
});

test("the gap in a step is said before the turn is handed back", () => {
  const said = stepBrief(skewers, 3, 2, true, "While it cooks, you have a 90-second window. Rinse the bowl and put it away.");
  assert.match(said, /you have a 90-second window\. Rinse the bowl and put it away\. And that's it — enjoy\.$/);
});

test("a step that already says its heat isn't told it twice", () => {
  const r = { steps: [{ do: "Heat a non-stick pan over medium heat and add the olive oil", heat: "Medium" }] };
  assert.match(stepBrief(r, 0, 2), /^Last step\. Heat a non-stick pan over medium heat/);
});
