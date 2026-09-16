import { test } from "node:test";
import assert from "node:assert/strict";
import { idleWindow, windowLabel } from "./window.js";

/* The demo dinner: spinach and egg fried rice, three steps, one pan. */
const friedRice = {
  needs: [
    { id: "egg", qty: 3, prep: "beaten" },
    { id: "spinach", qty: 150, prep: "roughly chopped" },
    { id: "rice", qty: 300 },
  ],
  steps: [
    { do: "Chop the spinach and beat the eggs.", minutes: 3, uses: [] },
    { do: "Fry the rice in a hot pan, stirring now and then.", minutes: 4, heat: "High", uses: ["rice"] },
    { do: "Push the rice aside, scramble the eggs, then stir in the spinach.", minutes: 3, heat: "Medium-high", uses: ["egg", "spinach"] },
  ],
};

test("frying rice for four minutes gives a 90-second window to rinse the knife and board", () => {
  const w = idleWindow(friedRice, 1);
  assert.equal(w.seconds, 90);
  assert.equal(w.say, "You have a 90-second window. Rinse the knife and the cutting board.");
});

test("a job already handed out is not handed out again", () => {
  const w = idleWindow(friedRice, 1, ["knife"]);
  assert.notEqual(w.key, "knife");
});

test("prep for the next step comes first when it hasn't been done", () => {
  const r = { ...friedRice, steps: [
    { do: "Fry the rice in a hot pan.", minutes: 4, heat: "High", uses: ["rice"] },
    friedRice.steps[2],
  ] };
  assert.equal(idleWindow(r, 0).task, "Get the eggs beaten for the next step.");
});

test("a simmer gives the whole step less a minute", () => {
  const r = { needs: [], steps: [{ do: "Simmer the sauce.", minutes: 8, heat: "Low" }, { do: "Serve.", minutes: 1 }] };
  const w = idleWindow(r, 0);
  assert.equal(w.seconds, 420);
  assert.match(w.say, /^You have a 7-minute window\./);
});

test("no window on a short step or one that isn't cooking", () => {
  assert.equal(idleWindow(friedRice, 0), null);                          // chopping is the work itself
  assert.equal(idleWindow({ steps: [{ do: "Fry the onion.", minutes: 2 }] }, 0), null);
});

test("window labels", () => {
  assert.equal(windowLabel(90), "90-second");
  assert.equal(windowLabel(180), "3-minute");
});
