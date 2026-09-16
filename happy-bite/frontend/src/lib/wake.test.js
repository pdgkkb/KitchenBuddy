import { test } from "node:test";
import assert from "node:assert/strict";
import { afterWake, wakeLabel } from "./wake.js";

test("what was said after Bob, as it was said", () => {
  assert.equal(afterWake("bob", "Bob, how long for the onions?"), "how long for the onions?");
  assert.equal(afterWake("bob", "Bob next step"), "next step");
  assert.equal(afterWake("bob", "OK Bob. Start a timer for 5 minutes"), "Start a timer for 5 minutes");
  assert.equal(afterWake("bob", "Hey, Bobby, repeat"), "repeat");         // a common mishearing
});

test("just the name wakes it with nothing to do yet", () => {
  assert.equal(afterWake("bob", "Bob."), "");
  assert.equal(afterWake("bob", "bop"), "");
});

test("anything not said to Bob is ignored", () => {
  for (const heard of ["next step", "tell Bob the pan is hot", "can you pass me the salt", "boy that smells good", "Thank you.", ""])
    assert.equal(afterWake("bob", heard), null, heard);
});

test("a two-word wake phrase still works, greeting optional", () => {
  assert.equal(afterWake("hey chef", "hey chef next"), "next");
  assert.equal(afterWake("hey chef", "ok chef, repeat"), "repeat");
  assert.equal(afterWake("hey chef", "the chef said the onions go in now"), null);
  assert.equal(wakeLabel("bob"), "Bob");
});

test("the always-on recogniser finds the request after the name, wherever the name is", () => {
  assert.equal(afterWake("bob", "so anyway Bob I'm drained give me 15 minutes", true), "I'm drained give me 15 minutes");
  assert.equal(afterWake("bob", "right Bob", true), "");
  assert.equal(afterWake("bob", "so anyway Bob I'm drained", false), null);  // cooking mode stays strict
});
