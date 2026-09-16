"""Answers written before the question is asked.

Standing at the hob, the questions are not a surprise. On "add the courgettes
in one layer, leave them alone" you are going to ask one of: how long, how hot,
can I stir it, what if they're sticking, can I use something else. A 7B model
on a laptop needs a few seconds per answer, and those seconds are the whole
difference between a chef and a search box.

So while you are reading a step, the server asks the model — once — for the
handful of questions that step invites and their answers, and parks them. When
you actually speak, `quick()` tries to match what you said against that list
and, on a hit, the reply is instant and costs nothing.

Matching is deliberately blunt: normalised token overlap plus difflib. A fuzzy
matcher that reaches too far would answer the wrong question confidently, which
is much worse than a two-second wait, so the thresholds are set high and a miss
simply falls through to the real model.

BACKGROUND WORK MUST NEVER SLOW DOWN A QUESTION
-----------------------------------------------
On a local model this is not a detail. One step's answers are ~15 seconds of
GPU on gemma-4-e2b; the browser asks for two steps at a time; and LM Studio
runs requests side by side, so a question asked meanwhile shared the GPU with
two background jobs and came back several times slower. That was a large part
of "cooking mode lags". Now:

  * background jobs run one at a time (a single slot);
  * `foreground()` wraps every request someone is waiting on: while one is in
    flight no background job starts, and any job already running is CANCELLED
    — the HTTP request is dropped and the server stops generating;
  * a cancelled step is simply not parked; the browser asks again when the
    person is idle, and it starts over then.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from difflib import SequenceMatcher

TTL = 45 * 60              # a parked step is stale after this
MAX_STEPS = 48             # cache ceiling, LRU by last touch
JACCARD = 0.62             # token overlap needed to call it the same question
RATIO = 0.78               # or a straight string similarity this high

# Words that carry no intent. "long", "much" and "hot" are deliberately NOT in
# here: in a kitchen they are the whole question.
STOP = {
    "a", "an", "the", "is", "it", "this", "that", "to", "do", "i", "you", "we",
    "should", "can", "could", "how", "what", "when", "does", "my", "of", "for",
    "and", "or", "be", "am", "are", "in", "on", "at", "with", "now", "ok",
    "okay", "please", "chef", "much", "many",
}

SYSTEM = """You are a chef standing beside someone who is cooking, and you are
about to be interrupted. Write the questions this exact step invites and the
answer to each.

Rules:
- Questions in the words a tired person actually uses out loud, not documentation
  headings. "how long do I leave them", not "duration of the saute phase".
- Answers of one or two spoken sentences. No lists, no markdown, no preamble.
- Ground every answer in THIS recipe and THIS step: real times, real heat, the
  quantities given. If the step has a cue, say what the cue looks like.
- Cover the obvious ground: how long, how hot, how do I know it's ready, what if
  it looks wrong, can I swap or leave out an ingredient, can I walk away.
- Never invent an ingredient that isn't in the recipe."""


def schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "description": f"{n} likely questions with their answers.",
                "items": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "What they might ask, spoken."},
                        "a": {"type": "string", "description": "The answer, 1-2 spoken sentences."},
                    },
                    "required": ["q", "a"],
                },
            }
        },
        "required": ["pairs"],
    }


def _stem(word: str) -> str:
    """Blunt on purpose. Both sides go through it, so it only has to be
    consistent, not linguistically right: "longer" and "long" must land on the
    same token, and it doesn't matter what that token is."""
    for suffix in ("ing", "est", "er", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    bits = re.findall(r"[a-z0-9']+", str(text).lower())
    return {_stem(b) for b in bits if b not in STOP and len(b) > 1}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(text).lower()))


def _score(said: str, question: str) -> float:
    """How much this parked question looks like what was actually said.

    Normalised so that 1.0 is the bar: anything under it falls through to the
    real model, which is the right way round — a two-second wait is cheap and a
    confidently wrong answer, at the hob, is not.
    """
    a, b = _words(said), _words(question)
    if not a or not b:
        return 0.0
    jac = len(a & b) / len(a | b)
    ratio = SequenceMatcher(None, _norm(said), _norm(question)).ratio()
    best = max(jac / JACCARD, ratio / RATIO)

    # A very short question is mostly stop-words, so overlap scores it badly:
    # "how long?" against "how long do I leave them" is one word in five. But a
    # short question whose every content word appears in a parked one IS that
    # question — the rest of the sentence is the part they didn't bother saying.
    if len(a) <= 3 and a <= b:
        best = max(best, 1.0 + len(a) / 100)
    return best


class Prefetcher:
    """One cache of parked answers, keyed by (recipe id, step index)."""

    def __init__(self, llm, count: int = 4, enabled: bool = True):
        self.llm = llm
        self.count = max(2, min(8, int(count or 4)))
        self.enabled = bool(enabled)
        self.cache: dict[tuple[str, int], dict] = {}
        self._busy: set[tuple[str, int]] = set()
        self._tasks: set[asyncio.Task] = set()
        self._slot = asyncio.Semaphore(1)          # one background job at a time
        self._foreground = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @asynccontextmanager
    async def foreground(self):
        """Someone is waiting on the model: get background work out of the way."""
        self._foreground += 1
        self._idle.clear()
        for task in list(self._tasks):
            task.cancel()
        try:
            yield
        finally:
            self._foreground -= 1
            if self._foreground == 0:
                self._idle.set()

    # ---------------------------------------------------------------- write

    def ensure(self, recipe: dict, step: int, serves: int | None = None) -> bool:
        """Start filling this step's answers if they aren't already there."""
        if not self.enabled or not self.llm:
            return False
        key = self._key(recipe, step)
        if key is None or key in self._busy:
            return False
        got = self.cache.get(key)
        if got and time.time() - got["at"] < TTL:
            got["touched"] = time.time()
            return False
        self._busy.add(key)
        task = asyncio.create_task(self._fill(key, recipe, step, serves))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def prime(self, recipe: dict, step: int, serves: int | None = None) -> None:
        """This step plus the next one — you almost always walk forwards."""
        self.ensure(recipe, step, serves)
        if step + 1 < len(recipe.get("steps") or []):
            self.ensure(recipe, step + 1, serves)

    async def _fill(self, key, recipe: dict, step: int, serves: int | None) -> None:
        try:
            await self._idle.wait()                # never start while a question is out
            async with self._slot:
                await self._idle.wait()
                await self._write(key, recipe, step, serves)
        except asyncio.CancelledError:
            pass                                   # a question came in; asked again later
        except Exception as e:  # noqa: BLE001 — a miss just means the slow path
            print("prefetch failed:", type(e).__name__, e)
        finally:
            self._busy.discard(key)

    async def _write(self, key, recipe: dict, step: int, serves: int | None) -> None:
        steps = recipe.get("steps") or []
        if not (0 <= step < len(steps)):
            return
        s = steps[step]
        body = {
            "recipe": recipe.get("name"),
            "cuisine": recipe.get("cuisine"),
            "cooking_for": serves or recipe.get("serves"),
            "step_number": step + 1,
            "of": len(steps),
            "step": s.get("do"),
            "heat": s.get("heat"),
            "watch_for": s.get("cue"),
            "why": s.get("why"),
            "about_minutes": s.get("minutes"),
            "all_steps": [x.get("do") for x in steps],
        }
        user = (f"Write the {self.count} most likely questions and answers for step "
                f"{step + 1}.\n\n" + json.dumps(body, ensure_ascii=False)[:6000])
        out = await self.llm.json(SYSTEM, user, schema(self.count), 700)
        pairs = []
        for p in (out or {}).get("pairs") or []:
            q = str(p.get("q") or "").strip()[:200]
            a = str(p.get("a") or "").strip()[:600]
            if q and a:
                pairs.append({"q": q, "a": a})
        if pairs:
            self.cache[key] = {"pairs": pairs[:self.count], "at": time.time(),
                               "touched": time.time()}
            self._evict()

    def _evict(self) -> None:
        if len(self.cache) <= MAX_STEPS:
            return
        old = sorted(self.cache.items(), key=lambda kv: kv[1].get("touched", 0))
        for k, _ in old[: len(self.cache) - MAX_STEPS]:
            self.cache.pop(k, None)

    # ---------------------------------------------------------------- read

    def quick(self, recipe_id: str, step: int, said: str) -> dict | None:
        """The parked answer for what they just said, or None to ask for real."""
        key = (str(recipe_id or ""), int(step or 0))
        got = self.cache.get(key)
        if not got or time.time() - got["at"] > TTL:
            return None
        got["touched"] = time.time()
        ranked = sorted(((_score(said, p["q"]), p) for p in got["pairs"]),
                        key=lambda t: t[0], reverse=True)
        if not ranked or ranked[0][0] < 1.0:
            return None
        # Two parked questions equally close means we don't actually know which
        # one was asked. Say nothing and let the model read the sentence.
        if len(ranked) > 1 and ranked[1][0] >= 1.0 and ranked[0][0] - ranked[1][0] < 0.08:
            return None
        score, best = ranked[0]
        return {"answer": best["a"], "matched": best["q"],
                "confidence": round(min(score, 2.0), 2)}

    def ready(self, recipe_id: str, step: int) -> int:
        got = self.cache.get((str(recipe_id or ""), int(step or 0)))
        return len(got["pairs"]) if got else 0

    @staticmethod
    def _key(recipe: dict, step: int):
        rid = str(recipe.get("id") or recipe.get("name") or "").strip()
        if not rid:
            return None
        return (rid, int(step))
