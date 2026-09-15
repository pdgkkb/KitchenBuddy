#!/usr/bin/env python3
"""Happy Bite — what is the model ACTUALLY doing?

Every diagnosis so far has been a guess dressed up as a conclusion, because
nobody has measured anything. This measures. It talks straight to your model
server — the app and its backend are not involved — and answers four questions
with numbers:

    which model is really loaded
    how fast does it write
    is it reasoning before it answers
    how long would a real recipe take, and does it finish

Nothing to install: standard library only. Run it from anywhere:

    python3 check_model.py

It reads backend/.env if it can find it, so it asks YOUR server about YOUR
model. Options:

    python3 check_model.py --url http://localhost:1234/v1 --model some-model
    python3 check_model.py --budget 3000      the app's own token budget
    python3 check_model.py --wait 900         give up after this many seconds

It only ever sends two short prompts. It writes nothing, changes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

G, R, Y, D, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = O = ""

TINY = "Reply with the single word: ready."

# Close to what the app actually asks for, and deliberately NOT simplified:
# a prompt that is easier than the real one would give a reassuring number
# that means nothing.
RECIPE_SYS = (
    "You are a careful home cook. Answer with JSON only, no prose, no markdown "
    "fence. Shape: {\"name\": str, \"minutes\": int, \"serves\": int, "
    "\"needs\": [{\"name\": str, \"qty\": number, \"unit\": str}], "
    "\"steps\": [{\"do\": str, \"minutes\": int, \"heat\": str, \"cue\": str, \"why\": str}]}"
)
RECIPE_USER = (
    "Write one dinner recipe for 4 people using what is in the kitchen: "
    "chicken thighs 600 g (2 days left), courgettes 3 (3 days left), "
    "rice 400 g, onions 2, garlic 1 bulb, lemons 2, butter 200 g, "
    "olive oil, salt, pepper, thyme, cream 200 ml. "
    "Eight to ten steps, each with heat, a cue and one line of why. "
    "Something good for tonight."
)


def find_env() -> Path | None:
    here = Path.cwd()
    for base in (here, here.parent, here / "happy-bite", here.parent / "happy-bite"):
        p = base / "backend" / ".env"
        if p.is_file():
            return p
    return None


def read_env(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def post(url: str, body: dict, key: str, wait: float) -> tuple[dict | None, str | None, float]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key or 'none'}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=wait) as r:
            return json.loads(r.read().decode()), None, time.time() - t0
    except urllib.error.HTTPError as e:
        detail = (e.read().decode(errors="replace") or "").strip().replace("\n", " ")[:300]
        return None, f"HTTP {e.code}: {detail or 'no detail'}", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}", time.time() - t0


def get(url: str, key: str, wait: float) -> tuple[dict | None, str | None]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key or 'none'}"})
    try:
        with urllib.request.urlopen(req, timeout=wait) as r:
            return json.loads(r.read().decode()), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def counts(resp: dict) -> tuple[int, int, str | None]:
    usage = resp.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    out = int(usage.get("completion_tokens") or 0)
    choice = (resp.get("choices") or [{}])[0]
    return prompt, out, choice.get("finish_reason")


def message(resp: dict) -> tuple[str, str]:
    msg = ((resp.get("choices") or [{}])[0]).get("message") or {}
    text = msg.get("content") or ""
    thought = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return text, str(thought or "")


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--url"); ap.add_argument("--model"); ap.add_argument("--key")
    ap.add_argument("--budget", type=int, default=None)
    ap.add_argument("--wait", type=float, default=900.0)
    a = ap.parse_args()

    url, model, key, budget = a.url, a.model, a.key, a.budget
    env_path = find_env()
    if env_path:
        env = read_env(env_path)
        url = url or env.get("OPENAI_BASE_URL")
        model = model or env.get("LLM_MODEL")
        key = key or env.get("OPENAI_API_KEY")
        budget = budget or int(env.get("LLM_JSON_TOKENS") or 0) or None
        print(f"{D}Read {env_path}{O}")
    url = (url or "http://localhost:1234/v1").rstrip("/")
    budget = budget or 3000

    print(f"\nAsking {url} about {model or '(whatever is loaded)'}")
    print(f"{D}Two prompts, nothing written, nothing changed. Up to {a.wait/60:.0f} min.{O}\n")

    notes: list[str] = []

    # ---------------------------------------------------------- 1. what is loaded
    print("1. What is loaded")
    listing, err = get(f"{url}/models", key, 15)
    if err:
        print(f"  {R}x{O} {url} didn't answer — {err}")
        print(f"\n  The model server isn't running, or isn't on that address.")
        print(f"  Start LM Studio (or Ollama), load the model, then run this again.")
        return 2
    ids = [m.get("id") for m in (listing.get("data") or []) if m.get("id")]
    for i in ids:
        print(f"  {G}+{O} {i}" + (f"  {D}<- the app asks for this one{O}" if i == model else ""))
    if model and model not in ids:
        print(f"  {R}x{O} the app asks for {model!r}, which this server does not list.")
        notes.append(f"LLM_MODEL in backend/.env is {model!r}; the server offers " +
                     (", ".join(repr(i) for i in ids) or "nothing") + ".")
    if not model:
        model = ids[0] if ids else ""

    # ------------------------------------------------------------- 2. how fast
    print("\n2. How fast, from cold")
    body = {"model": model, "messages": [{"role": "user", "content": TINY}],
            "max_tokens": 16, "stream": False}
    resp, err, secs = post(f"{url}/chat/completions", body, key, a.wait)
    if err:
        print(f"  {R}x{O} refused after {secs:.0f}s — {err}")
        return 2
    _, out, _ = counts(resp)
    print(f"  {G}+{O} answered in {secs:.1f}s ({out} tokens)")
    if secs > 60:
        notes.append(f"Even a one-word answer took {secs:.0f}s. That is the model loading into "
                     "memory, or the machine swapping — not the recipe being hard.")

    # ------------------------------------------------- 3. a real recipe, timed
    print(f"\n3. A real recipe, budget {budget} tokens")
    print(f"   {D}this is the slow one — leave it{O}")
    body = {"model": model,
            "messages": [{"role": "system", "content": RECIPE_SYS},
                         {"role": "user", "content": RECIPE_USER}],
            "max_tokens": budget, "temperature": 0.7, "top_p": 0.8, "stream": False}
    resp, err, secs = post(f"{url}/chat/completions", body, key, a.wait)
    if err:
        print(f"  {R}x{O} nothing came back after {secs:.0f}s — {err}")
        timed_out = "timed out" in err.lower() or "timeout" in err.lower()
        if not timed_out:
            return 2
        # This is exactly where the app gives up, and giving up here too would
        # leave the same question unanswered. Ask again for a SHORT reply: too
        # short to finish a recipe, long enough to measure the rate and to see
        # whether the model starts by thinking. A refusal to finish is not a
        # refusal to speak.
        print(f"\n  It ran {secs/60:.0f} minutes without finishing — the app's own failure.")
        print(f"  {D}asking again, 300 tokens only, just to watch it start{O}")
        body["max_tokens"] = 300
        resp, err2, secs2 = post(f"{url}/chat/completions", body, key, min(a.wait, 300))
        if err2 or not resp:
            print(f"  {R}x{O} that failed too — {err2}")
            print("\n  The server takes the request and never produces anything. Reload the")
            print("  model in LM Studio; if step 2 was also slow, the Mac is out of memory.")
            return 2
        text, thought = message(resp)
        _, out_n, finish = counts(resp)
        rate = out_n / secs2 if secs2 > 0 else 0
        reasoned = bool(thought) or "</think>" in text.lower() or "<think>" in text.lower()
        print(f"  {G}+{O} 300 tokens in {secs2:.0f}s — {rate:.1f} tokens/s")
        print(f"\n4. What that means\n")
        if reasoned:
            print(f"  {R}REASONING IS ON{O}, and at {rate:.1f} tokens/s it never reaches the recipe")
            print(f"  inside ten minutes. The first {budget} tokens go to deliberation.\n")
            print("  In LM Studio: the loaded model's settings, the Jinja prompt template.")
            print("  Put this on the first line of the template:\n")
            print(f"      {Y}{{%- set enable_thinking = false %}}{O}\n")
            print("  Reload the model, run this again. That line is what the app has been")
            print("  trying to send as chat_template_kwargs, which LM Studio won't accept")
            print("  over the API.")
        else:
            print(f"  Reasoning looks {G}off{O}, so the model is simply too slow here:")
            print(f"  {rate:.1f} tokens/s means a 700-token recipe needs {700 / rate if rate else 0:.0f}s,")
            print(f"  and the {budget}-token budget needs {budget / rate if rate else 0:.0f}s.")
            print("  Free memory on the Mac, or use a smaller model or quant.")
        print(f"\n{D}Paste everything above into the chat and I can act on it.{O}")
        return 1

    text, thought = message(resp)
    prompt_n, out_n, finish = counts(resp)
    rate = out_n / secs if secs > 0 else 0
    print(f"  {G}+{O} came back in {secs:.0f}s — {out_n} tokens at {rate:.1f}/s"
          f" (prompt was {prompt_n})")
    print(f"  {D}finish_reason: {finish}{O}")

    # ------------------------------------------------------------ 4. the verdict
    print("\n4. What that means\n")

    closing = "</think>" in text.lower()
    opening = "<think>" in text.lower()
    reasoned = bool(thought) or closing or opening
    think_chars = len(thought) if thought else (
        text.lower().index("</think>") if closing else 0)

    if reasoned:
        share = ""
        if think_chars and len(text) + think_chars:
            share = f" — about {100 * think_chars / (think_chars + len(text)):.0f}% of the reply"
        print(f"  {R}REASONING IS ON.{O} The model thought for {think_chars} characters"
              f"{share}.")
        print("  At this speed that is roughly "
              f"{think_chars / 4 / rate if rate else 0:.0f}s spent before the recipe starts.")
        print("  This is the whole problem, and no token budget fixes it.\n")
        print("  In LM Studio: the loaded model's settings, the Jinja prompt template.")
        print("  Put this on the first line of the template:\n")
        print(f"      {Y}{{%- set enable_thinking = false %}}{O}\n")
        print("  Then reload the model and run this script again. The line above is")
        print("  what the app has been trying to send as chat_template_kwargs, which")
        print("  LM Studio does not accept over the API.")
    else:
        print(f"  {G}Reasoning is off.{O} The reply is answer, not deliberation.")

    if finish == "length":
        print(f"\n  {R}It was cut off{O} — it used all {budget} tokens and was still writing.")
        if reasoned:
            print("  It spent them thinking. Turn reasoning off; the budget is not the issue.")
        else:
            print(f"  Raise LLM_JSON_TOKENS in backend/.env above {budget}.")
    else:
        ok = False
        try:
            clean = re.sub(r"```(?:json)?", "", text)
            if closing:
                clean = clean[clean.lower().index("</think>") + 8:]
            s, e = clean.find("{"), clean.rfind("}")
            json.loads(clean[s:e + 1])
            ok = True
        except Exception:  # noqa: BLE001
            pass
        print(f"\n  {(G + 'The JSON parses.' + O) if ok else (R + 'The reply is not valid JSON.' + O)}")

    print(f"\n  A recipe needs roughly 700 tokens of JSON. At {rate:.1f} tokens/s that is"
          f" {700 / rate if rate else 0:.0f}s of writing.")
    if rate and rate < 8:
        print(f"  {Y}That rate is low for a 9B model.{O} If step 2 was also slow, the Mac is")
        print("  swapping: close what else is holding memory, or use a smaller quant.")

    if notes:
        print("\n  Also:")
        for n in notes:
            print(f"   - {n}")

    print(f"\n{D}Paste everything above into the chat and I can act on it.{O}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())