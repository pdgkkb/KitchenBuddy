import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCook } from "./command.js";

test("moving to a step by number moves the screen, however it's said", () => {
  for (const [said, n] of [["go to step 2", 2], ["Go back to step two.", 2], ["step 3", 3],
                           ["skip to step four please", 4], ["the first step", 1], ["step to", 2]])
    assert.deepEqual(parseCook(said), { cmd: "goto", step: n }, said);
});

test("a question about a step is still a question", () => {
  assert.equal(parseCook("how long is step 2"), null);
  assert.equal(parseCook("what do I do in step 2"), null);
});

test("next and back still mean next and back", () => {
  assert.deepEqual(parseCook("go to the next step"), { cmd: "next" });
  assert.deepEqual(parseCook("last step"), { cmd: "back" });
});
