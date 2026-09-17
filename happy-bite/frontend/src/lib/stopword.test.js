import { test } from "node:test";
import assert from "node:assert/strict";
import { isStop, raceStop, watchForStop } from "./stopword.js";

test("stop, said to Bob, however it's said", () => {
  for (const said of ["stop Bob", "Bob, stop.", "Bob stop thinking", "hey Bob, wait", "Bobby, cancel that", "okay stop bob"])
    assert.deepEqual(isStop(said, "bob"), { rest: "" }, said);
});

test("a bare stop phrase counts on its own", () => {
  for (const said of ["Stop.", "wait", "never mind", "hold on"])
    assert.deepEqual(isStop(said, "bob"), { rest: "" }, said);
});

test("Bob, stop — and the new question after it", () => {
  assert.deepEqual(isStop("Bob, stop, how long for the onions?", "bob"), { rest: "how long for the onions" });
});

test("stop inside an ordinary sentence cancels nothing", () => {
  for (const said of ["stop stirring for a second", "wait for the oil to shimmer", "I want to stop at step three", "the pan is hot", ""])
    assert.equal(isStop(said, "bob"), null, said);
});

test("the watch keeps listening until it hears stop, then stops listening", () => {
  const heard = ["the pan is hot", "", "Bob, stop"];
  let turns = 0, stopped = null;
  const listen = ({ onText }) => { const t = heard[turns++]; queueMicrotask(() => onText(t)); return { abort() {} }; };
  return new Promise((resolve) => {
    watchForStop({ listen, wakeWord: "bob", onStop: (hit) => { stopped = hit; resolve(); } });
  }).then(() => {
    assert.equal(turns, 3);
    assert.deepEqual(stopped, { rest: "" });
  });
});

test("cancelling the watch throws the current recording away", () => {
  let aborted = false;
  const w = watchForStop({ listen: () => ({ abort() { aborted = true; }, stop() {} }), wakeWord: "bob", onStop() {} });
  w.cancel();
  assert.ok(aborted);
});

test("raceStop: the answer wins when nobody says stop", async () => {
  const listen = () => ({ abort() {} });                         // hears nothing
  const out = await raceStop({ work: async () => "fry it", listen, wakeWord: "bob" });
  assert.equal(out.stopped, false);
  assert.equal(out.value, "fry it");
});

test("raceStop: 'Bob, stop' wins while the chef is still thinking", async () => {
  let finish;
  const work = () => new Promise((r) => { finish = r; });
  const listen = ({ onText }) => { queueMicrotask(() => onText("Bob, stop, how long for the onions")); return { abort() {} }; };
  const out = await raceStop({ work, listen, wakeWord: "bob" });
  assert.equal(out.stopped, true);
  assert.equal(out.rest, "how long for the onions");
  finish("late answer");
  await out.settled;
});
